"""Read the encoder's streamed layers a few layers ahead of the forward pass.

When live RAM cannot hold the 15.7 GB checkpoint, neither whole-file read-ahead
runs and every layer the device does not keep is copied from the read-only
mapping through page faults, one small read at a time. A 16 GB Windows RTX 4060
Ti spent 127.6 s computing a prompt that takes 1-3 s on the device, at 0.11 GB/s,
while the same drive read the video model's weights at 0.24-0.72 GB/s in large
blocks a minute later.

The forward pass visits the layers in order and touches each one once, so the
next layers' bytes are known. This reader keeps a small number of layers in
front of the pass: it reads their byte ranges in large blocks (the faults then
find the pages in the OS cache) and gives the pages of finished layers back, so
the encoder never holds more than a few hundred MB beyond what it is using.
It keeps no private copy, CUDA allocation or pinned memory.

One handle reading a layer front to back leaves most of a fast drive idle, as
it does for the video model's streamed layers, so each layer is read in large
pieces on a few handles at once, with the same reader count and piece layout
(FREEVIDEO_READ_THREADS=1 reads on one handle). The bytes the encoder later
uses are unchanged; only the order the drive is asked for them in differs.
"""
import ctypes
import json
import mmap
import os
import re
import struct
import threading
import time
from pathlib import Path

from .encoder_prewarm import CHUNK
from .hardware import cgroup_memory
from .system import system_memory
from .tensor_io import read_threads

LEAD_LAYERS = 3
# The window holds a few layers and gives finished ones back, so it needs no
# room beyond what the pass would take for itself; only a nearly empty machine
# (live RAM below this) pauses it.
PRESSURE_FLOOR_BYTES = 32 * 2**20
# How long one layer may wait for room before the reader stands down for good.
STALL_LIMIT_SECONDS = 20.
LAYER_NAME = re.compile(r'(?:^|\.)layers\.(\d+)\.')
EMBED_NAME = re.compile(r'(?:^|\.)embed_tokens\.')


def available_memory():
    """Live RAM the window may count on: clean file cache is returned on demand, so the cgroup's
    reclaimable headroom is used, not the raw remainder under the cap (which cache always fills)."""
    available = system_memory()['available_bytes']
    group = cgroup_memory()
    reclaimable = group.get('reclaimable_available_bytes')
    if reclaimable is not None:
        available = min(available, reclaimable)
    return available, group.get('complete', True)


def layer_ranges(path):
    """File byte ranges of the language model's embedding (key -1) and each layer, merged and sorted."""
    with open(path, 'rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError('Incomplete safetensors header')
        header_size = struct.unpack('<Q', prefix)[0]
        if not 2 <= header_size <= min(100_000_000, size - 8):
            raise ValueError('Invalid safetensors header size')
        header = json.loads(stream.read(header_size))
    base = 8 + header_size
    found = {}
    for name, info in header.items():
        if name == '__metadata__' or name.startswith('visual.') or '.visual.' in name:
            continue
        match = LAYER_NAME.search(name)
        key = int(match.group(1)) if match else -1 if EMBED_NAME.search(name) else None
        if key is None:
            continue
        begin, end = info['data_offsets']
        if end > begin:
            found.setdefault(key, []).append((base + begin, base + end))
    merged = {}
    for key, spans in found.items():
        spans.sort()
        out = [list(spans[0])]
        for begin, end in spans[1:]:
            if begin <= out[-1][1]:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([begin, end])
        merged[key] = [tuple(span) for span in out]
    return merged


def _mapping_base(path):
    """Address of the checkpoint mapping's first byte, from the loader's own record, or None."""
    from .encoder_checkpoint import MAPPINGS, READ_ONLY_RANGES
    live = [reference() for reference in MAPPINGS.get(os.path.realpath(path), [])]
    live = [mapping for mapping in live if mapping is not None and not mapping.closed]
    if len(live) != 1:
        return None, None
    for reference, begin, end in READ_ONLY_RANGES:
        if reference() is live[0]:
            return live[0], begin
    return live[0], None


class LayerWindow:
    def __init__(self, path, modules, *, lead=LEAD_LAYERS, release=True):
        """`modules` maps a layer key (-1 embedding, else layer index) to the module whose forward touches it."""
        self.path = Path(path)
        self.modules = modules
        self.lead = max(1, int(lead))
        self.threads = read_threads()
        self.local = threading.local()
        self.streams = []       # one read handle per pool thread, closed when the window ends
        self.release = release
        self.cond = threading.Condition()
        self.cancelled = False
        self.thread = None
        self.handles = []
        self.plan = []          # [(key, ranges, bytes)] in execution order
        self.reached = -1       # plan position the pass has entered
        self.next = 0           # plan position the reader will read next
        self.released = 0       # plan positions already given back, this sweep
        self.closed = None
        self.mapping = None
        self.address = None
        self.result = dict(state='not-started', layers=0, read_bytes=0, released_bytes=0, stalls=0,
                           sweeps=0, elapsed_seconds=0., lead_layers=self.lead, read_threads=self.threads,
                           scope='Streamed encoder layers; reclaimable OS cache, no GPU residency')

    def start(self):
        if self.thread is not None:
            return self
        ranges = layer_ranges(self.path)
        keys = [key for key in sorted(ranges) if key in self.modules]
        self.plan = [(key, ranges[key], sum(end - begin for begin, end in ranges[key])) for key in keys]
        if not self.plan:
            self.result.update(state='skipped', reason='No streamed layers')
            return self
        self.stamp = self.path.stat()
        self.mapping, self.address = _mapping_base(self.path)
        position = {key: index for index, (key, _, _) in enumerate(self.plan)}
        for key, module in self.modules.items():
            if key in position:
                self.handles.append(module.register_forward_pre_hook(self._entered(position[key])))
        self.result.update(state='reading', layers=len(self.plan),
                           planned_bytes=sum(size for _, _, size in self.plan),
                           reason='The forward pass is read a few layers ahead of its page faults')
        self.thread = threading.Thread(target=self._run, name='FreeVideo encoder window', daemon=True)
        self.thread.start()
        return self

    def _entered(self, position):
        def hook(module, args, kwargs=None):
            with self.cond:
                if position <= self.reached:
                    # The pass started again (a retry): read from here once more.
                    self.result['sweeps'] += 1
                    self.next = position
                    self.released = position
                self.reached = position
                self.cond.notify_all()
        return hook

    def _read_parallel(self, pool, spans):
        """Read one layer's ranges in CHUNK pieces on the pool's handles; return the bytes read.

        Neighbouring pieces go to different handles, so together they still
        move through the layer from front to back. Each pool thread keeps its
        own handle and scratch buffer for the whole window: the bytes are only
        brought into the OS cache. A failure on one handle stops the others
        at their next piece."""
        pieces = []
        for begin, end in spans:
            while begin < end:
                length = min(CHUNK, end - begin)
                pieces.append((begin, length))
                begin += length
        threads = min(self.threads, len(pieces))
        failed = threading.Event()

        def read(group):
            total = 0
            local = self.local
            stream = getattr(local, 'stream', None)
            if stream is None:
                stream = local.stream = self.path.open('rb', buffering=0)
                local.scratch = bytearray(CHUNK)
                with self.cond:
                    self.streams.append(stream)
            scratch = local.scratch
            try:
                for position, length in group:
                    if self.cancelled or failed.is_set():
                        break
                    stream.seek(position)
                    done = 0
                    while done < length:
                        count = stream.readinto(memoryview(scratch)[:length - done])
                        if not count:
                            raise ValueError('Encoder checkpoint truncated during read window')
                        done += count
                    total += length
            except BaseException:
                failed.set()    # the other handles stop before their next piece
                raise
            return total

        futures = [pool.submit(read, pieces[first::threads]) for first in range(threads)]
        total, failure = 0, None
        for future in futures:
            try:
                total += future.result()
            except BaseException as error:
                failed.set()
                failure = failure or error
        if failure is not None:
            raise failure
        return total

    def _run(self):
        started = time.monotonic()
        scratch = None
        pool = None
        try:
            if self.threads > 1:
                from concurrent.futures import ThreadPoolExecutor
                pool = ThreadPoolExecutor(self.threads, thread_name_prefix='freevideo-encoder-window')
            else:
                scratch = bytearray(CHUNK)
            with self.path.open('rb', buffering=0) as file:
                while True:
                    with self.cond:
                        while not self.cancelled and (self.next >= len(self.plan)
                                                      or self.next > self.reached + self.lead):
                            self.cond.wait(0.1)
                            self._give_back()
                        if self.cancelled:
                            self.result['state'] = 'stopped'
                            self.result['reason'] = 'The encoder finished'
                            return
                        position = self.next
                    key, spans, size = self.plan[position]
                    if not self._room(size):
                        self.result.update(state='stopped', reason='Live RAM stayed too low to read ahead')
                        return
                    if pool is None:
                        for begin, end in spans:
                            file.seek(begin)
                            remaining = end - begin
                            while remaining:
                                if self.cancelled:
                                    break
                                count = file.readinto(memoryview(scratch)[:min(CHUNK, remaining)])
                                if not count:
                                    raise ValueError('Encoder checkpoint truncated during read window')
                                remaining -= count
                                self.result['read_bytes'] += count
                    else:
                        self.result['read_bytes'] += self._read_parallel(pool, spans)
                    with self.cond:
                        if self.next == position:
                            self.next = position + 1
                        self._give_back()
        except Exception as error:
            # Speculative I/O never replaces the loader's own reads and validation.
            self.result.update(state='failed', reason=type(error).__name__ + ': ' + str(error))
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
            with self.cond:
                streams, self.streams = self.streams, []
            for stream in streams:
                try:
                    stream.close()
                except OSError:
                    pass
            self.result['elapsed_seconds'] = time.monotonic() - started

    def _room(self, size):
        waited = time.monotonic()
        while not self.cancelled:
            available, complete = available_memory()
            if not complete or available >= PRESSURE_FLOOR_BYTES:
                return True
            self.result['stalls'] += 1
            if time.monotonic() - waited > STALL_LIMIT_SECONDS:
                return False
            time.sleep(0.02)
        return True

    def _give_back(self):
        """Release the pages of layers the pass has finished with (called holding the lock)."""
        if not self.release:
            return
        while self.released < self.reached - 1 and self.released < len(self.plan):
            for begin, end in self.plan[self.released][1]:
                self.result['released_bytes'] += release_range(self.mapping, self.address, begin, end)
            self.released += 1

    def close(self):
        """Stop reading and remove the hooks; return what was done. Later calls return the same result."""
        if self.closed is None:
            with self.cond:
                self.cancelled = True
                self.cond.notify_all()
            for handle in self.handles:
                handle.remove()
            self.handles.clear()
            if self.thread is not None:
                self.thread.join(timeout=2.)
            if self.result['state'] == 'reading':
                self.result['state'] = 'stopped'
            self.closed = dict(self.result, reader_still_stopping=bool(self.thread and self.thread.is_alive()))
        return self.closed


def release_range(mapping, address, begin, end):
    """Give the pages of [begin, end) of the checkpoint back; clean pages of a read-only file refault on use.

    Linux/macOS advise the mapping away. Windows removes the pages from the
    working set, which is where mapped pages stay until the system trims them.
    Returns the bytes advised, 0 when nothing could be done."""
    page = mmap.PAGESIZE
    first = -(-begin // page) * page
    last = end // page * page
    if last <= first:
        return 0
    try:
        if os.name == 'nt':
            if address is None:
                return 0
            ctypes.windll.kernel32.VirtualUnlock(ctypes.c_void_p(address + first), ctypes.c_size_t(last - first))
            return last - first
        if mapping is None or not hasattr(mapping, 'madvise') or not hasattr(mmap, 'MADV_DONTNEED'):
            return 0
        mapping.madvise(mmap.MADV_DONTNEED, first, last - first)
        return last - first
    except (OSError, ValueError, AttributeError):
        return 0


def layer_modules(model, owns_mapped):
    """{key: module} for the language model's embedding and layers whose weights still sit in the mapping."""
    from .encoder_lowmem import language_model
    llama = language_model(model)
    found = {}
    embed = getattr(llama, 'embed_tokens', None)
    if embed is not None and any(owns_mapped(p) for p in embed.parameters()):
        found[-1] = embed
    for index, layer in enumerate(llama.layers):
        if any(owns_mapped(p) for p in layer.parameters()):
            found[index] = layer
    return found
