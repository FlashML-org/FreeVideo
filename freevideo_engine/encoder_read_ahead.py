"""Read the text encoder's checkpoint while it is copied to the device.

A request whose encoder is not resident builds the model on the CPU, then copies
the checkpoint to the GPU through page faults. One Windows RTX 5090 request
copied the 15.7 GB checkpoint in 28.7 s (0.55 GB/s), while the same request
read its decoder ahead at 1.29 GB/s; on a Windows RTX 5060 Ti the faults read
1.18 GB/s against 1.78 GB/s for large sequential reads. Reading the file in
order during the copy lets the copy find most of its pages in the OS cache.

The reader runs only during the copy. Started before the native library
import, it delayed the import's own small reads (that RTX 5060 Ti, file not
cached: import 4.0 -> 7.5 s). Started at the model's construction, a file
already in the OS cache turned every read into a CPU copy that took a core
from the threads building the model (Linux, eight cores: 0.6 -> 2.5 s). It
stops when the copy returns. Like the decoder read-ahead it keeps no private
copy, CUDA allocation or pinned memory, only reclaimable OS cache, and only
when live RAM covers the whole file beside the system reserve.
"""
import os
from pathlib import Path
import threading
import time

from .encoder_prewarm import CHUNK, GiB, available_memory

# Live RAM is sampled once per this many bytes, not per chunk. On Linux a sample
# parses /proc/meminfo and the cgroup files under the GIL (0.48 ms), while a
# chunk already in the OS cache reads in about 2 ms.
MEMORY_CHECK_BYTES = 256 * 2**20



class EncoderReadAhead:
    def __init__(self, path, *, ram_budget_bytes=None):
        self.path = Path(path)
        self.ram_budget_bytes = ram_budget_bytes
        self.cancelled = threading.Event()
        self.thread = None
        self.closed = None
        self.result = dict(state='not-started', read_bytes=0, elapsed_seconds=0.,
                           gpu_allocation_bytes=0, private_buffer_bytes=CHUNK,
                           scope='Encoder checkpoint pages; reclaimable OS cache, no GPU residency')

    def start(self):
        if self.thread is not None:
            return self
        if os.environ.get('FREEVIDEO_ENCODER_READ_AHEAD', 'auto').lower() in ('off', '0', 'false'):
            self.result.update(state='skipped', reason='Disabled by FREEVIDEO_ENCODER_READ_AHEAD')
            return self
        self.thread = threading.Thread(target=self._run, name='FreeVideo encoder read-ahead', daemon=True)
        self.thread.start()
        return self

    def _run(self):
        started = time.monotonic()
        try:
            stamp = self.path.stat()
            total = stamp.st_size
            available, complete = available_memory()
            allowance = max(0, available - 2 * GiB)
            if self.ram_budget_bytes is not None:
                from .ram import ProcessMemory
                from .system import memory_sample
                used = memory_sample(ProcessMemory().sample(os.getpid()))
                complete = complete and type(used) is int and used >= 0
                allowance = min(allowance, max(0, int(self.ram_budget_bytes) - (used or 0) - 2 * GiB))
            self.result.update(total_bytes=total, allowance_bytes=allowance)
            if not complete or not 0 < total <= allowance:
                self.result.update(state='skipped', reason='Live RAM cannot retain the encoder checkpoint')
                return
            self.result.update(state='reading', reason='The device copy overlaps encoder reads')
            scratch = bytearray(CHUNK)
            checked = 0  # The allowance above sampled live RAM at offset 0.
            with self.path.open('rb', buffering=0) as file:
                remaining = total
                while remaining:
                    pressure = False
                    if self.result['read_bytes'] - checked >= MEMORY_CHECK_BYTES:
                        checked = self.result['read_bytes']
                        available, complete = available_memory()
                        pressure = not complete or available < 2 * GiB + MEMORY_CHECK_BYTES
                    if self.cancelled.is_set() or pressure:
                        self.result.update(state='stopped', reason='The device copy finished, or live RAM pressure')
                        return
                    count = file.readinto(memoryview(scratch)[:min(CHUNK, remaining)])
                    if not count:
                        raise ValueError('Encoder checkpoint truncated during read-ahead')
                    remaining -= count
                    self.result['read_bytes'] += count
            current = self.path.stat()
            if (current.st_size, current.st_mtime_ns) != (stamp.st_size, stamp.st_mtime_ns):
                raise ValueError('Encoder checkpoint changed during read-ahead')
            self.result.update(state='ready', reason='Checkpoint read into the OS cache; the loader reuses it')
        except Exception as error:
            # Speculative I/O never replaces the native loader's own reads and validation.
            self.result.update(state='failed', reason=type(error).__name__ + ': ' + str(error))
        finally:
            self.result['elapsed_seconds'] = time.monotonic() - started

    def close(self):
        """Stop reading and return what was read; later calls return the same result."""
        if self.closed is None:
            self.cancelled.set()
            if self.thread is not None:
                self.thread.join(timeout=1.)
            self.closed = dict(self.result, reader_still_stopping=bool(self.thread and self.thread.is_alive()))
        return self.closed
