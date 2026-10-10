"""Reopen immutable safetensor layer views only while filling an offload slot."""
from contextlib import contextmanager
from pathlib import Path
import threading


_TORCH_DTYPES = {'BOOL': 'bool', 'U8': 'uint8', 'I8': 'int8', 'I16': 'int16',
                 'I32': 'int32', 'I64': 'int64', 'F16': 'float16', 'BF16': 'bfloat16',
                 'F32': 'float32', 'F64': 'float64', 'F8_E4M3': 'float8_e4m3fn',
                 'F8_E5M2': 'float8_e5m2'}


_IDENTITY_FIELDS = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


def _file_identity(value):
    return tuple(getattr(value, name) for name in _IDENTITY_FIELDS)


class CheckpointChangedError(ValueError):
    def __init__(self, path, source, expected, observed):
        self.checkpoint_change = dict(source=source, fields=[name for name, before, after
            in zip(_IDENTITY_FIELDS, expected, observed) if before != after])
        super().__init__('Checkpoint changed during streamed inference: ' + str(path))


def _open_safetensors(path, *, framework, device='cpu'):
    """Open a streamed shard without reserving the whole file on Windows.

    The mmap backend is excellent on Linux, where clean mapped pages are
    reclaimable and do not consume commit.  Windows charges the section views
    against the system commit even when their pages are clean.  Reopening a
    multi-hundred-MiB shard for every transformer block can therefore consume
    the pagefile while the process's working set stays small.  safetensors
    0.8's ``pread`` backend reads only the requested tensor bytes and avoids
    that commit reservation.  Runtime dependencies pin this version, so do not
    silently fall back to mmap on Windows.
    """
    from safetensors import safe_open
    from .system import windows
    kwargs = {'framework': framework, 'device': device}
    if windows():
        kwargs['backend'] = 'pread'
    return safe_open(str(path), **kwargs)


def interleaved_layer_order(count):
    """Spread any affordable prefix of pinned layers across the computation.

    A long cached prefix fills the bounded read-ahead window and then leaves
    the disk idle. Interleaving retained layers gives the reader useful work
    throughout a step without increasing its buffers or changing layer order.
    Longest-gap bisection also spreads partial allocations when live RAM or
    the platform's pinning limit is lower than the plan.
    """
    import heapq
    if type(count) is not int or count < 0:
        raise ValueError('Layer count must be a non-negative integer')
    pending = [(-count, 0, count)] if count else []
    order = []
    while pending:
        _, start, end = heapq.heappop(pending)
        middle = (start + end) // 2
        order.append(middle)
        for lo, hi in ((start, middle), (middle + 1, end)):
            if lo < hi:
                heapq.heappush(pending, (lo - hi, lo, hi))
    return order


def release_file_pages(paths):
    """Advise away consumed clean pages; never remove or rewrite a weight file."""
    import os
    advised, errors = 0, 0
    if not hasattr(os, 'posix_fadvise'):
        return advised, errors
    for path in paths:
        try:
            with path.open('rb', buffering=0) as file:
                os.posix_fadvise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                advised += path.stat().st_size
        except OSError:
            # Advisory cache management is optional. The normal tensor reader
            # still validates the immutable file before and after every use.
            errors += 1
    return advised, errors


def read_ahead_budget():
    """Bound clean file-cache lookahead by live RAM, without a pinned copy."""
    from .system import system_memory, windows
    from .hardware import cgroup_memory
    memory = system_memory()
    available = memory['available_bytes']
    # Windows reports physical and commit headroom separately.  File-cache
    # read-ahead is deliberately pageable, but it still must not run when the
    # pagefile/commit is already the tighter bound; otherwise a speculative
    # read can recreate the same pressure that streamed pread was introduced
    # to avoid.
    # Commit is a meaningful second capacity on Windows: section views and
    # pageable staging can consume the pagefile even when physical RAM looks
    # available.  Linux's MemAvailable already accounts for reclaimable file
    # cache; do not import the Windows commit ceiling into its read-ahead
    # budget.
    if windows():
        commit = memory.get('commit_available_bytes')
        if type(commit) is int and commit >= 0:
            available = min(available, commit)
    group = cgroup_memory()
    if not group.get('complete'):
        return 0
    limited = group.get('reclaimable_available_bytes')
    if limited is not None:
        available = min(available, limited)
    # Current-layer views, a small read buffer and changing application usage
    # also need space. These are clean reclaimable pages, not retained tensors.
    return max(0, min(1536 * 2**20, available - 512 * 2**20))


class LayerReadAhead:
    """One cancellable reader warms a byte-bounded window of immutable files.

    Only file cache retains the data. The reader owns one 4 MiB scratch buffer,
    never a tensor, a pinned allocation or a GPU slot. Submission advances as
    the inference thread consumes layers; it cannot race through the model.
    """
    def __init__(self, plans, check, layer_count, budget=read_ahead_budget):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event, Lock
        self.plans = sorted((index, tuple(paths), sum(path.stat().st_size for path in paths))
                            for index, paths in plans.items())
        self.check = check
        self.paths_by_index = {index: paths for index, paths, _ in self.plans}
        self.layer_count = layer_count
        self.budget = budget
        self.cursor = 0
        self.pending = {}
        self.pending_bytes = 0
        self.stop = Event()
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='freevideo-read-ahead')
        self.closed = False
        self.failed = None
        self.last_read_finished = None
        self.counters = dict(read_bytes=0, read_seconds=0., wait_seconds=0.,
                             reader_idle_seconds=0.,
                             files_read=0, peak_window_bytes=0, skipped_layers=0,
                             discard_advised_bytes=0, discard_errors=0)
        self._fill()

    def _read(self, paths):
        import time
        started = time.monotonic()
        with self.lock:
            if self.last_read_finished is not None:
                self.counters['reader_idle_seconds'] += max(0., started - self.last_read_finished)
        scratch = bytearray(4 * 2**20)
        read_bytes = 0
        files = 0
        try:
            for path in paths:
                if self.stop.is_set():
                    break
                self.check(path)
                complete = False
                with path.open('rb', buffering=0) as file:
                    while not self.stop.is_set():
                        count = file.readinto(scratch)
                        if not count:
                            complete = True
                            break
                        read_bytes += count
                self.check(path)
                files += int(complete)
        finally:
            with self.lock:
                self.counters['read_bytes'] += read_bytes
                self.counters['read_seconds'] += time.monotonic() - started
                self.counters['files_read'] += files
                self.last_read_finished = time.monotonic()

    def _fill(self):
        if self.closed or self.failed:
            return
        maximum = max(0, int(self.budget()))
        while self.cursor < len(self.plans):
            index, paths, size = self.plans[self.cursor]
            if size > maximum:
                self.cursor += 1
                self.counters['skipped_layers'] += 1
                continue
            if self.pending_bytes + size > maximum:
                break
            self.pending[index] = (size, self.executor.submit(self._read, paths))
            self.pending_bytes += size
            self.counters['peak_window_bytes'] = max(self.counters['peak_window_bytes'], self.pending_bytes)
            self.cursor += 1

    def before(self, index):
        import time
        self._fill()
        item = self.pending.get(index)
        if item is None:
            return
        started = time.monotonic()
        try:
            item[1].result()
        except Exception as error:
            # A failed optimization must not replace the real tensor reader's
            # validation/error. Stop speculative reads; the normal path retries
            # this immutable file and still checks its identity and contents.
            self.failed = self.failed or type(error).__name__ + ': ' + str(error)
            self.stop.set()
            for _, future in self.pending.values():
                future.cancel()
        finally:
            self.counters['wait_seconds'] += time.monotonic() - started

    def consumed(self, index):
        item = self.pending.pop(index, None)
        if item is not None:
            self.pending_bytes -= item[0]
            if not self.failed:
                # mmap copies mark consumed pages active. Under a tight cgroup
                # Linux otherwise evicts the unread speculative pages first,
                # so both threads end up fetching the same weights from disk.
                advised, errors = release_file_pages(self.paths_by_index[index])
                self.counters['discard_advised_bytes'] += advised
                self.counters['discard_errors'] += errors
        if index == self.layer_count - 1:
            if self.pending:
                raise RuntimeError('Read-ahead layers were consumed out of order')
            self.cursor = 0
        self._fill()

    def stats(self):
        with self.lock:
            return dict(self.counters, enabled=True, failure=self.failed,
                        scratch_buffer_bytes=4 * 2**20,
                        read_scope='Logical CPU reads, including file-cache hits; not physical disk I/O',
                        scope='Reclaimable file cache; one reader, no extra pinned RAM or GPU slot')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        for _, future in self.pending.values():
            future.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.pending.clear()
        self.pending_bytes = 0


_READ_PIECE = 8 << 20
_reader = None
_reader_lock = threading.Lock()


def _reader_pool():
    """Threads shared by every streamed source's direct reads."""
    global _reader
    with _reader_lock:
        if _reader is None:
            from concurrent.futures import ThreadPoolExecutor
            from .tensor_io import read_threads
            _reader = ThreadPoolExecutor(read_threads(), thread_name_prefix='freevideo-stream-read')
        return _reader


# Streamed layers kept in RAM as read-only file views must leave this much
# physical memory free. A 27.98 GiB Windows machine started sampling at
# 7.31 GiB available and ran the refine pass at 6.98 GiB: 0.35 GiB of later
# growth, plus a 2 GiB floor for the rest of the system.
HOST_VIEW_RESERVE = int(2.5 * 2**30)
# Below this much available memory, views are handed back one layer per use,
# so a request that grows (a larger refine pass, readouts, another program)
# trades them for disk reads instead of pushing Windows toward its floor.
HOST_VIEW_SHED = int(1.5 * 2**30)


def host_view_fits(size, reserve=HOST_VIEW_RESERVE):
    """Whether one more layer may stay in RAM as a read-only file view.

    Read-only views charge no commit (a 412 MiB block mapped and touched moved
    Windows commit by 1 MiB; a private copy moved it by 415 MiB), and Windows
    can drop their clean pages without writing the pagefile. Only physical
    memory bounds them.
    """
    from .system import system_memory
    memory = system_memory()
    available = memory.get('physical_available_bytes') or memory['available_bytes']
    return available - size >= reserve


class SafetensorLayers:
    def __init__(self, paths, prefixes, *, intermediate_dtype=None, direct_read=None, host_views=False,
                 host_view_reserve=HOST_VIEW_RESERVE):
        from .system import windows
        self.direct_read = windows() if direct_read is None else direct_read
        self.prefixes = tuple(prefixes)
        self.intermediate_dtype = intermediate_dtype
        self.files = {}
        self.handle_files = {}
        self.keys = {}
        self.byte_ranges = {}
        # Layers that pinning could not hold but RAM can: read-only views of
        # their checkpoint bytes, adopted right after the first direct read.
        self.host_views = {} if host_views and self.direct_read else None
        self.host_view_reserve = host_view_reserve
        self.host_view_bytes = 0
        self.host_view_hits = 0
        self.host_view_hit_bytes = 0
        self.host_view_released = 0
        for path in sorted(set(Path(p).resolve() for p in paths)):
            self.files[path] = self.identity(path)
            with _open_safetensors(path, framework='np') as handle:
                # safe_open validates the file layout first. Retain only its
                # header offsets, never tensor storage or a file mapping.
                if self.direct_read:
                    import json
                    import os
                    import struct
                    with path.open('rb') as stream:
                        # Windows stat().ctime can be creation time while
                        # fstat().ctime is NTFS ChangeTime. Keep each API's
                        # baseline instead of comparing these different clocks.
                        opened = _file_identity(os.fstat(stream.fileno()))
                        if opened[:4] != self.files[path][:4]:
                            raise CheckpointChangedError(path, 'handle', self.files[path][:4], opened[:4])
                        self.handle_files[path] = opened
                        length = struct.unpack('<Q', stream.read(8))[0]
                        if not 2 <= length <= min(100_000_000, self.files[path][2] - 8):
                            raise ValueError('Invalid safetensors header size')
                        header = json.loads(stream.read(length))
                    self.check(path)
                for key in handle.keys():
                    if key in self.keys:
                        raise ValueError('Duplicate checkpoint tensor: ' + key)
                    self.keys[key] = path
                    if self.direct_read:
                        spec = header[key]
                        tensor = handle.get_slice(key)
                        if spec['shape'] != tensor.get_shape() or spec['dtype'] != tensor.get_dtype():
                            raise ValueError('Checkpoint header changed: ' + key)
                        start, end = spec['data_offsets']
                        self.byte_ranges[key] = (8 + length + start, end - start,
                                                spec['dtype'], tuple(spec['shape']))
            self.check(path)

    @staticmethod
    def identity(path):
        return _file_identity(path.stat())

    def check(self, path):
        observed = self.identity(path)
        if observed != self.files[path]:
            raise CheckpointChangedError(path, 'path', self.files[path], observed)

    def stage_into(self, index, layout, planes):
        """Read matching tensor bytes directly into the existing CPU slot.

        Windows pread otherwise creates a whole layer of temporary tensors
        before copying it into this slot. FileIO.readinto uses the destination
        storage itself, so neither that allocation nor its CPU copy is needed.
        Dtype conversion (including VAE intermediate rounding) keeps the
        established tensor reader. Validate the entire plan before any write.
        """
        if not self.direct_read or self.intermediate_dtype is not None:
            return False
        import math
        import sys
        import torch
        if sys.byteorder != 'little':
            return False
        prefix = self.prefixes[index]
        reads = {}
        for _, name, dtype, offset, count, shape in layout:
            key = prefix + name
            start, size, stored, stored_shape = self.byte_ranges[key]
            if stored_shape != tuple(shape) or count != math.prod(shape):
                raise ValueError('Checkpoint layer shape changed: ' + key)
            if getattr(torch, _TORCH_DTYPES.get(stored, ''), None) != dtype:
                return False
            plane = planes[dtype]
            if (plane.device.type != 'cpu' or plane.dtype != dtype or not plane.is_contiguous()
                    or offset < 0 or count < 0 or offset + count > plane.numel()
                    or size != count * plane.element_size()):
                raise ValueError('Invalid streamed destination: ' + key)
            reads.setdefault(self.keys[key], []).append((start, size, dtype, offset, name))
        for path in reads:
            self.check(path)
        entry = self.host_views.get(index) if self.host_views is not None else None
        if entry is not None and any(name not in entry[0] or entry[0][name].numel() != size
                                     for entries in reads.values() for _, size, _, _, name in entries):
            # Views are kept by tensor name. One that cannot serve this layout
            # is dropped rather than copied to a guessed plane offset.
            self._release(index)
            entry = None
        if entry is not None:
            views, _, size = entry
            # torch copies release the GIL, so the inference thread keeps launching.
            try:
                for entries in reads.values():
                    for _, length, dtype, offset, name in entries:
                        begin = offset * planes[dtype].element_size()
                        planes[dtype].detach().view(torch.uint8)[begin:begin + length].copy_(views[name])
            finally:
                for path in reads:
                    self.check(path)
            self.host_view_hits += 1
            self.host_view_hit_bytes += size
            del entry, views
            if not host_view_fits(0, HOST_VIEW_SHED):
                self._release(index)
            return True
        # numpy exposes a writable byte view of CPU/pinned storage without
        # copying, including BF16/FP8 planes that numpy cannot represent directly.
        buffers = {dtype: memoryview(plane.detach().view(torch.uint8).numpy()).cast('B')
                   for dtype, plane in planes.items()}
        try:
            pieces = []
            for path, entries in reads.items():
                for start, size, dtype, offset, _ in sorted(entries, key=lambda row: row[0]):
                    begin = offset * planes[dtype].element_size()
                    target = buffers[dtype][begin:begin + size]
                    pieces += [(path, start + at, target[at:at + _READ_PIECE])
                               for at in range(0, size, _READ_PIECE)]
            self._read_pieces(pieces)
            if self.host_views is not None:
                size = sum(row[1] for entries in reads.values() for row in entries)
                if host_view_fits(size, self.host_view_reserve):
                    self._adopt(index, reads, size)
        finally:
            for path in reads:
                self.check(path)
        return True

    def _read_pieces(self, pieces):
        """Read (path, position, destination) pieces on a few handles at once.

        One handle reading a streamed layer tensor by tensor leaves most of a
        fast drive idle: 28 int8 layers (12.1 GB) read from an NVMe drive with
        a cold cache at 2.66 GB/s that way and at 3.4-4.4 GB/s as 8 MiB pieces
        on four handles. With 34 such layers read every step, 960x544 steps
        took 4.4 s instead of 6.0-6.2 s, bit-identical (Linux, RTX PRO 6000).
        Loading already reads its blocks like this (tensor_io.ParallelReads).
        Each piece fills exactly its bytes of the slot, and every handle is
        checked against the file it opened. FREEVIDEO_READ_THREADS=1 reads on
        the calling thread alone.
        """
        import os
        from contextlib import ExitStack
        from .tensor_io import read_threads
        threads = min(read_threads(), len(pieces))
        # A reader still running when this call leaves would write into a slot
        # its caller may reuse. After a failure here a reader writes nothing
        # more, and the call returns only once none is reading.
        state = dict(stop=False, active=0)
        idle = threading.Condition()

        def read(group):
            with idle:
                if state['stop']:
                    return
                state['active'] += 1
            try:
                streams = {}
                with ExitStack() as opened:
                    for path, position, target in group:
                        if state['stop']:
                            return
                        stream = streams.get(path)
                        if stream is None:
                            stream = streams[path] = opened.enter_context(path.open('rb', buffering=0))
                            observed = _file_identity(os.fstat(stream.fileno()))
                            if observed != self.handle_files[path]:
                                raise CheckpointChangedError(path, 'handle', self.handle_files[path], observed)
                        stream.seek(position)
                        done = 0
                        while done < len(target):
                            count = stream.readinto(target[done:])
                            if not count:
                                raise ValueError('Incomplete streamed checkpoint: ' + str(path))
                            done += count
            finally:
                with idle:
                    state['active'] -= 1
                    idle.notify_all()

        if threads < 2:
            read(pieces)
            return
        # Neighbouring pieces go to different handles, so together the
        # readers still move through each file from front to back.
        from concurrent.futures import wait
        futures = []
        try:
            for first in range(threads):
                futures.append(_reader_pool().submit(read, pieces[first::threads]))
            wait(futures)
        except BaseException:
            with idle:
                state['stop'] = True
                idle.wait_for(lambda: not state['active'])
            raise
        for future in futures:
            future.result()

    def layer_bytes(self, index):
        """Checkpoint bytes of one layer by stored dtype, from the headers alone."""
        prefix = self.prefixes[index]
        sizes = {}
        for key, (_, size, stored, _) in self.byte_ranges.items():
            if key.startswith(prefix):
                sizes[stored] = sizes.get(stored, 0) + size
        return sizes

    def placeholders(self, index, path):
        """Tensors with one file's stored dtypes and shapes for a layer, read from nothing.

        Model loading binds a streamed layer only to record its layout, then
        drops the values. When the layer will be neither pinned nor kept as a
        view, reading it first is wasted: the first step reads it again. With
        the RAM of a 16 GB machine, 31 of 45 offloaded blocks (13.4 GB) were
        such layers; on the RTX 5060 Ti test machine loading took 7.1 s instead
        of 11.3 s, with identical output, and slower drives save far more.
        Each placeholder is one element expanded to the checkpoint shape.
        Return None for a dtype torch lacks.
        """
        import torch
        prefix = self.prefixes[index]
        path = Path(path).resolve()
        tensors = {}
        for key, owner in self.keys.items():
            if owner != path or not key.startswith(prefix):
                continue
            _, _, stored, shape = self.byte_ranges[key]
            dtype = getattr(torch, _TORCH_DTYPES.get(stored, ''), None)
            if dtype is None:
                return None
            tensors[key] = (torch.empty((1,) * len(shape), dtype=dtype).expand(shape) if all(shape)
                            else torch.empty(shape, dtype=dtype))
        return tensors

    def adopt(self, index):
        """Keep a layer that loading just read as views, so its first pass reads no disk.

        Pinned groups admitted after it consume the RAM that cached these
        pages: a 768p request after a 20 s one read its 16 GiB of unpinned
        layers again during the first step. Admission and shedding are the
        same as for a layer adopted after its first direct read.
        """
        if self.host_views is None or index in self.host_views:
            return False
        prefix = self.prefixes[index]
        reads = {}
        for key, path in self.keys.items():
            if key.startswith(prefix):
                start, size, _, _ = self.byte_ranges[key]
                reads.setdefault(path, []).append((start, size, None, None, key[len(prefix):]))
        size = sum(row[1] for entries in reads.values() for row in entries)
        if not reads or not host_view_fits(size, self.host_view_reserve):
            return False
        for path in reads:
            self.check(path)
        self._adopt(index, reads, size)
        return index in self.host_views

    def _adopt(self, index, reads, size):
        """Keep the layer just read resident as read-only views of its checkpoint."""
        import mmap
        import os
        import warnings
        import torch
        views, mappings = {}, []
        for path, entries in reads.items():
            with path.open('rb', buffering=0) as stream:
                observed = _file_identity(os.fstat(stream.fileno()))
                if observed != self.handle_files[path]:
                    raise CheckpointChangedError(path, 'handle', self.handle_files[path], observed)
                try:
                    mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
                except OSError:
                    # Address space or mapping refused: this layer keeps its direct reads.
                    for opened in mappings:
                        opened.close()
                    return
            mappings.append(mapping)
            with warnings.catch_warnings():
                # Read-only checkpoint bytes; these tensors are only ever copy sources.
                warnings.simplefilter('ignore', UserWarning)
                data = torch.frombuffer(mapping, dtype=torch.uint8)
            for start, length, _, _, name in entries:
                views[name] = data[start:start + length]
        # The direct read just left these pages in the file cache. Touch every
        # page now, so they join this process's working set before the next
        # streamed reads recycle the standby list.
        for source in views.values():
            source[::mmap.PAGESIZE].sum()
        self.host_views[index] = (views, mappings, size)
        self.host_view_bytes += size

    def _release(self, index):
        views, mappings, size = self.host_views.pop(index)
        del views
        for mapping in mappings:
            try:
                mapping.close()
            except BufferError:
                pass  # A tensor view is still referenced; the mapping closes when it is collected.
        self.host_view_bytes -= size
        self.host_view_released += 1

    def release_host_views(self):
        for index in list(self.host_views or ()):
            self._release(index)

    def read_ahead(self, layouts, pinned):
        from .system import windows
        # Windows uses the commit-safe safetensors ``pread`` backend.  A
        # second file-cache reader would warm pageable copies while WDDM/Commit
        # is already the tight resource and can make the next transfer slower.
        # Linux keeps this bounded reader because clean file-cache pages are
        # reclaimable and MemAvailable accounts for them.
        if windows():
            return None
        plans = {}
        for index, layout in enumerate(layouts):
            if pinned[index]:
                continue
            prefix = self.prefixes[index]
            paths = {self.keys[prefix + name] for _, name, *_ in layout}
            # Prepared transformer groups each own a file. Do not pull an
            # entire multi-layer VAE/refiner shard into RAM to warm one layer.
            if any(not key.startswith(prefix) for key, path in self.keys.items() if path in paths):
                continue
            plans[index] = sorted(paths)
        if not plans:
            return None
        # Pinned copies and zero-sized streamed placeholders already replaced
        # these mapped sources. Their old active cache pages must not crowd out
        # the next layers. No inference tensor or file content is discarded.
        for path in self.files:
            self.check(path)
        advised, errors = release_file_pages(self.files)
        reader = LayerReadAhead(plans, self.check, len(layouts))
        reader.counters['discard_advised_bytes'] += advised
        reader.counters['discard_errors'] += errors
        return reader

    @contextmanager
    def layer(self, index, layout):
        from contextlib import ExitStack
        prefix = self.prefixes[index]
        tensors = {}
        paths = {self.keys[prefix + name] for _, name, *_ in layout}
        for path in paths:
            self.check(path)
        try:
            with ExitStack() as stack:
                opened = {path: stack.enter_context(_open_safetensors(path, framework='pt', device='cpu'))
                          for path in paths}
                for _, name, dtype, _, count, shape in layout:
                    key = prefix + name
                    tensor = opened[self.keys[key]].get_tensor(key)
                    if tuple(tensor.shape) != tuple(shape) or tensor.numel() != count:
                        raise ValueError('Checkpoint layer shape changed: ' + key)
                    # Prepared VAE Linear values follow the original loader's
                    # FP32 rounding before FP16 autocast, even for FP64 shards.
                    if self.intermediate_dtype is not None:
                        tensor = tensor.to(dtype=self.intermediate_dtype)
                    tensors[name] = tensor.to(dtype=dtype)
                yield tensors
        finally:
            tensors.clear()
            for path in paths:
                self.check(path)
