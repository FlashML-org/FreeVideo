"""Bounded CPU tensor reads for model preparation, without persistent file maps."""
import json
import os
from pathlib import Path
import threading

_DTYPES = {'BOOL': 'bool', 'U8': 'uint8', 'I8': 'int8', 'I16': 'int16', 'I32': 'int32', 'I64': 'int64',
           'F16': 'float16', 'BF16': 'bfloat16', 'F32': 'float32', 'F64': 'float64',
           'F8_E4M3': 'float8_e4m3fn', 'F8_E5M2': 'float8_e5m2'}
_PIECE = 8 << 20


def read_threads():
    """Readers per shard. FREEVIDEO_READ_THREADS=1 reads each tensor in one call."""
    value = os.environ.get('FREEVIDEO_READ_THREADS', '')
    return max(1, int(value)) if value.isdigit() else 4


class ParallelReads:
    """Whole tensors of one safetensors file, read in pieces on a few threads.

    On the Windows RTX 5060 Ti machine, safetensors' pread backend read the
    prepared blocks at 1.04 GB/s, and at 1.05 GB/s with two files at once;
    the disk delivers 2.5 GB/s. FileIO.readinto releases the GIL, and with
    one handle per thread these readers reached 2.1 GB/s. Each tensor owns
    exactly its bytes, as with pread, and every handle closes with the
    reader. Entries of a dtype listed nowhere here are left to safetensors.
    """

    def __init__(self, path, threads=None):
        self.path = Path(path)
        size = self.path.stat().st_size
        with self.path.open('rb', buffering=0) as stream:
            length = int.from_bytes(stream.read(8), 'little')
            if not 2 <= length <= min(100_000_000, size - 8):
                raise ValueError('Invalid safetensors header size: ' + str(self.path))
            header = json.loads(stream.read(length))
        header.pop('__metadata__', None)
        self.base = 8 + length
        self.entries, self.skipped = {}, []
        import torch
        for name, entry in header.items():
            start, end = entry['data_offsets']
            if not 0 <= start <= end <= size - self.base:
                raise ValueError('Invalid safetensors entry %s in %s' % (name, self.path))
            dtype = _DTYPES.get(entry['dtype'])
            if dtype is None:
                self.skipped.append(name)
                continue
            count = 1
            for extent in entry['shape']:
                count *= extent
            if count * getattr(torch, dtype).itemsize != end - start:
                raise ValueError('Invalid safetensors entry %s in %s' % (name, self.path))
            self.entries[name] = (dtype, tuple(entry['shape']), start, end)
        self.threads = read_threads() if threads is None else threads
        self.pool = None
        self.local = threading.local()
        self.lock = threading.Lock()
        self.streams = []

    def _stream(self):
        stream = getattr(self.local, 'stream', None)
        if stream is None:
            stream = open(self.path, 'rb', buffering=0)
            with self.lock:
                self.streams.append(stream)
            self.local.stream = stream
        return stream

    def _read(self, piece):
        position, view = piece
        stream = self._stream()
        stream.seek(position)
        done = 0
        while done < len(view):
            count = stream.readinto(view[done:])
            if not count:
                raise ValueError('Truncated safetensors payload: ' + str(self.path))
            done += count

    def tensors(self, names):
        """Read several whole tensors; all their pieces share the thread pool."""
        import torch
        results, pieces = {}, []
        for name in names:
            dtype, shape, start, end = self.entries[name]
            raw = torch.empty(end - start, dtype=torch.uint8)
            view = memoryview(raw.numpy())
            pieces += [(self.base + start + offset, view[offset:offset + _PIECE])
                       for offset in range(0, end - start, _PIECE)]
            results[name] = raw.view(getattr(torch, dtype)).reshape(shape)
        if self.threads > 1 and len(pieces) > 1:
            if self.pool is None:
                from concurrent.futures import ThreadPoolExecutor
                self.pool = ThreadPoolExecutor(self.threads, thread_name_prefix='freevideo-read')
            list(self.pool.map(self._read, pieces))
        else:
            for piece in pieces:
                self._read(piece)
        return results

    def tensor(self, name):
        return self.tensors([name])[name]

    def close(self):
        if self.pool is not None:
            self.pool.shutdown()
            self.pool = None
        with self.lock:
            streams, self.streams = self.streams, []
        for stream in streams:
            stream.close()


class _Tensors:
    """A pread handle whose whole-tensor reads use ParallelReads."""

    def __init__(self, handle, path):
        self._handle = handle
        self._reads = ParallelReads(path)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._handle.__exit__(*exc)
        finally:
            self._reads.close()

    def get_tensor(self, name):
        if name not in self._reads.entries:
            return self._handle.get_tensor(name)
        return self._reads.tensor(name)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def open_tensors(path, *, framework='pt'):
    from safetensors import safe_open
    # The default Torch mmap is copy-on-write. On Windows its commit charge is
    # the whole shard, and a tiny returned tensor can keep that mapping alive.
    # pread owns only the requested tensor's bytes, including BF16/FP8 tensors.
    # Do not silently fall back to mmap on an older manually installed runtime.
    try:
        handle = safe_open(path, framework=framework, device='cpu', backend='pread')
    except TypeError as error:
        raise RuntimeError('Bounded model preparation requires safetensors >= 0.8.0. '
                           'Rerun setup to update the managed environment.') from error
    return _Tensors(handle, path) if framework == 'pt' else handle
