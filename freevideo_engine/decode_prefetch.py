"""Overlap the final sampling steps with bounded decoder checkpoint reads.

No Torch imports, CUDA allocations, model objects, pinned copies or encoder
weights. Only reclaimable OS file cache retains the read data. Native decoding
 still validates and loads the original checkpoint after sampling releases VRAM.
The sampler starts this reader before its last two steps; the reader can still
stop immediately when the live RAM/Commit allowance is insufficient. When the
allowance covers only part of the decoder, that part is read and the loader
reads the rest itself.
"""
import json
import os
from pathlib import Path
import threading
import time

from .encoder_prewarm import CHUNK, GiB, available_memory


def decoder_ranges(base):
    plans = []
    for path in sorted((Path(base) / 'vae').glob('*.safetensors')):
        size = path.stat().st_size
        with path.open('rb') as file:
            length = int.from_bytes(file.read(8), 'little')
            if not 2 <= length <= min(16 * 2**20, size - 8):
                raise ValueError('Invalid VAE safetensors header')
            header = json.loads(file.read(length))
        spans = []
        for name, tensor in header.items():
            if not name.startswith(('decoder.', 'post_quant_conv.')):
                continue
            lo, hi = tensor['data_offsets']
            if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= size - length - 8:
                raise ValueError('Invalid VAE tensor range')
            spans.append((lo + length + 8, hi + length + 8))
        merged = []
        for lo, hi in sorted(spans):
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
            else:
                merged.append((lo, hi))
        if merged:
            plans.append((path, tuple(merged), (size, path.stat().st_mtime_ns)))
    return plans


class DecoderReadAhead:
    def __init__(self, base, *, ram_budget_bytes=None):
        self.base = base
        self.ram_budget_bytes = ram_budget_bytes
        self.cancelled = threading.Event()
        self.thread = None
        self.result = dict(state='not-started', read_bytes=0, elapsed_seconds=0.,
                           gpu_allocation_bytes=0, private_buffer_bytes=CHUNK,
                           scope='Decoder-only checkpoint pages; reclaimable OS cache, no GPU residency')

    def start(self):
        if self.thread is not None:
            return
        if os.environ.get('FREEVIDEO_DECODE_READ_AHEAD', 'auto').lower() in ('off', '0', 'false'):
            self.result.update(state='skipped', reason='Disabled by FREEVIDEO_DECODE_READ_AHEAD')
            return
        self.thread = threading.Thread(target=self._run, name='FreeVideo decoder read-ahead', daemon=True)
        self.thread.start()

    def _run(self):
        started = time.monotonic()
        try:
            plans = decoder_ranges(self.base)
            total = sum(hi - lo for _, spans, _ in plans for lo, hi in spans)
            available, complete = available_memory()
            allowance = max(0, available - 2 * GiB)
            if self.ram_budget_bytes is not None:
                from .ram import ProcessMemory
                from .system import memory_sample
                sample = ProcessMemory().sample(os.getpid())
                used = memory_sample(sample)
                complete = complete and type(used) is int and used >= 0
                allowance = min(allowance, max(0, self.ram_budget_bytes - (used or 0) - 2 * GiB))
            # The allowance bounds how much the cache may hold for the decoder,
            # not whether reading is worth it. Short of the whole decoder, read
            # the part that fits: every page read here is one the loader does
            # not wait for after sampling. Pinning a few more transformer layers
            # can leave the allowance just under the decoder's size, and
            # skipping the whole read then costs the entire cold load.
            target = min(total, allowance)
            self.result.update(total_bytes=total, allowance_bytes=allowance, target_bytes=target)
            if not complete or total <= 0 or target < min(total, CHUNK):
                self.result.update(state='skipped', reason='Live RAM cannot retain decoder pages alongside sampling')
                return
            self.result.update(state='reading', reason='Final denoising steps can overlap decoder disk reads')
            scratch = bytearray(CHUNK)
            for path, spans, stamp in plans:
                if self.result['read_bytes'] >= target:
                    break
                with path.open('rb', buffering=0) as file:
                    for lo, hi in spans:
                        if self.result['read_bytes'] >= target:
                            break
                        file.seek(lo)
                        remaining = min(hi - lo, target - self.result['read_bytes'])
                        while remaining:
                            available, complete = available_memory()
                            if self.cancelled.is_set() or not complete or available < 2 * GiB + CHUNK:
                                self.result.update(state='stopped', reason='Sampling finished, cancellation, or live RAM pressure')
                                return
                            count = file.readinto(memoryview(scratch)[:min(CHUNK, remaining)])
                            if not count:
                                raise ValueError('VAE checkpoint truncated during read-ahead')
                            remaining -= count
                            self.result['read_bytes'] += count
                current = path.stat()
                if (current.st_size, current.st_mtime_ns) != stamp:
                    raise ValueError('VAE checkpoint changed during read-ahead')
            if self.result['read_bytes'] < total:
                self.result.update(state='partial', reason='Decoder pages within the live RAM allowance read while sampling; the loader reads the rest')
            else:
                self.result.update(state='ready', reason='Decoder pages read while sampling; normal loader reuses surviving OS cache')
        except Exception as error:
            # Speculative I/O must not replace the normal loader's validation.
            self.result.update(state='failed', reason=type(error).__name__ + ': ' + str(error))
        finally:
            self.result['elapsed_seconds'] = time.monotonic() - started

    def close(self):
        self.cancelled.set()
        if self.thread is not None:
            self.thread.join(timeout=1.)
        result = dict(self.result)
        result['reader_still_stopping'] = bool(self.thread and self.thread.is_alive())
        return result
