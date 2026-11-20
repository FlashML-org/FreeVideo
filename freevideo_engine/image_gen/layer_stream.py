"""Layer-by-layer weight streaming from safetensors files, and one layer read shared by two branches.

``DiskLayerStream`` keeps no host copy of the layers it serves: each layer is read from its file just ahead of
use into a small ring of host buffers (pinned on a CUDA device), copied to the device for the layer's forward,
and dropped afterwards. Host memory stays at the ring plus the page cache, whatever the model's size.

``run_layers_paired`` applies every layer to two streams (the positive and the negative branch of a guided
step) so the layer is read once for both; the result is the same as running the branches one after the other.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import torch

READ_AHEAD = 3
READ_THREADS = 4


class SafetensorsIndex:
    """Byte offsets of tensors in a set of safetensors files, read from the headers only."""

    def __init__(self, folder, weight_map):
        self.folder, self.weight_map, self.headers = folder, weight_map, {}

    def locate(self, key):
        path = str(self.folder / self.weight_map[key])
        if path not in self.headers:
            with open(path, 'rb') as handle:
                size = int.from_bytes(handle.read(8), 'little')
                self.headers[path] = (json.loads(handle.read(size)), 8 + size)
        header, base = self.headers[path]
        begin, end = header[key]['data_offsets']
        return path, base + begin, end - begin


def read_into(path, offset, length, buffer):
    """Fill ``buffer`` (a contiguous uint8 tensor view) with ``length`` bytes at ``offset``."""
    view = memoryview(buffer.numpy()).cast('B')
    if hasattr(os, 'preadv'):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            got = 0
            while got < length:
                count = os.preadv(descriptor, [view[got:length]], offset + got)
                if count <= 0:
                    raise EOFError('short read from %s' % path)
                got += count
        finally:
            os.close(descriptor)
        return length
    # Platforms without preadv (Windows): each call opens its own unbuffered binary handle, so the reader
    # threads never share a file position, and the bytes read are the same.
    with open(path, 'rb', buffering=0) as handle:
        handle.seek(offset)
        got = 0
        while got < length:
            count = handle.readinto(view[got:length])
            if not count:
                raise EOFError('short read from %s' % path)
            got += count
    return length


class DiskLayerStream:
    """Serve ``layers`` (modules with the same parameter shapes) from ``index``; ``key(i, name)`` names a tensor."""

    def __init__(self, layers, index, key, device, ahead=READ_AHEAD, threads=READ_THREADS, keep=()):
        self.layers, self.index, self.key, self.device = list(layers), index, key, torch.device(device)
        self.ahead, self.bytes_read = ahead, 0
        self.pin_failed = False
        # Layers in ``keep`` are read once and stay on the card; the others are streamed each time they run.
        self.keep = {i for i in keep if 0 <= i < len(self.layers)}
        self.order = [i for i in range(len(self.layers)) if i not in self.keep]
        self.pos = {i: p for p, i in enumerate(self.order)}
        self.pool = ThreadPoolExecutor(threads)
        self.ready = {}
        self.pinned = self._wants_pinned()
        # Layers of one kind share a ring of buffers; a model that mixes kinds (attention and linear-attention
        # layers) gets one ring per kind, so a layer is only ever read into a buffer of its own shape.
        self.kinds = []
        self.kind_params = {}
        for i, layer in enumerate(self.layers):
            if i in self.keep:
                self.kinds.append(None)
                continue
            params = dict(layer.named_parameters())
            kind = tuple((n, tuple(p.shape), p.dtype) for n, p in params.items())
            self.kinds.append(kind)
            self.kind_params.setdefault(kind, params)
        try:
            self._build_ring()
        except (RuntimeError, MemoryError):
            if not self.pinned:
                raise
            # The system would not lock that many pages (Windows limits locked memory and counts it against the
            # commit limit): serve the same layers from ordinary host buffers. The bytes read and the result are
            # the same; only the copy to the card is no longer asynchronous.
            self.slots, self.slot_kind, self.free = [], [], {}
            release = getattr(torch._C, '_host_emptyCache', None)
            if release is not None:
                release()  # give back the locked pages the failed ring already held
            self.pinned = False
            self.pin_failed = True
            self._build_ring()
        self.events = {}
        self.handles = []
        for i, layer in enumerate(self.layers):
            if i in self.keep:
                self._load_kept(i)
                continue
            for module in layer.modules():
                for name, param in list(module.named_parameters(recurse=False)):
                    setattr(module, name, torch.nn.Parameter(
                        torch.empty(0, device=self.device, dtype=param.dtype), requires_grad=False))
            self.handles.append(layer.register_forward_pre_hook(self._before(i)))
            self.handles.append(layer.register_forward_hook(self._after(i)))

    def _wants_pinned(self):
        return self.device.type == 'cuda'

    def _build_ring(self):
        self.slots = []
        self.slot_kind = []
        self.free = {}
        for kind, params in self.kind_params.items():
            self.free[kind] = []
            for _ in range(self.ahead + 3):
                self.free[kind].append(len(self.slots))
                self.slots.append({n: torch.empty(p.shape, dtype=p.dtype, pin_memory=self.pinned)
                                   for n, p in params.items()})
                self.slot_kind.append(kind)

    def _load_kept(self, i):
        """Read a kept layer's weights straight onto the card; it never leaves."""
        layer = self.layers[i]
        for module_name, module in layer.named_modules():
            for name, param in list(module.named_parameters(recurse=False)):
                full = name if not module_name else module_name + '.' + name
                path, offset, length = self.index.locate(self.key(i, full))
                host = torch.empty(param.shape, dtype=param.dtype)
                if length != host.numel() * host.element_size():
                    raise ValueError('%s in %s holds %d bytes, the model expects %d; wrong dtype or shape'
                                     % (self.key(i, full), path, length, host.numel() * host.element_size()))
                read_into(path, offset, length, host.view(torch.uint8).reshape(-1))
                self.bytes_read += length
                setattr(module, name, torch.nn.Parameter(host.to(self.device), requires_grad=False))

    def _read(self, i, slot):
        out = self.slots[slot]
        for name, param in self.layers[i].named_parameters():
            path, offset, length = self.index.locate(self.key(i, name))
            want = out[name].numel() * out[name].element_size()
            if length != want:
                raise ValueError('%s in %s holds %d bytes, the model expects %d (%s %s); wrong dtype or shape'
                                 % (self.key(i, name), path, length, want, out[name].dtype, tuple(out[name].shape)))
            read_into(path, offset, length, out[name].view(torch.uint8).reshape(-1))
            self.bytes_read += length
        return slot

    def _take_slot(self, i):
        slot = self.free[self.kinds[i]].pop(0)
        event = self.events.pop(slot, None)
        if event is not None:
            event.synchronize()
        return slot

    def _kick(self, j):
        if j not in self.keep and 0 <= j < len(self.layers) and j not in self.ready:
            slot = self._take_slot(j)
            self.ready[j] = (slot, self.pool.submit(self._read, j, slot))

    def _before(self, i):
        def hook(module, args, kwargs=None):
            window = set(self.order[self.pos[i]:self.pos[i] + self.ahead + 1])
            for j in [j for j in self.ready if j not in window]:
                slot, future = self.ready.pop(j)
                future.result()
                self.free[self.kinds[j]].append(slot)
            start = self.pos[i]
            for j in self.order[start:start + self.ahead + 1]:
                if j == i or self.free[self.kinds[j]]:
                    self._kick(j)
            slot, future = self.ready.pop(i)
            future.result()
            current = torch.cuda.current_stream(self.device) if self.pinned else None
            for name, param in module.named_parameters():
                tensor = self.slots[slot][name].to(self.device, non_blocking=self.pinned)
                if self.pinned:
                    tensor.record_stream(current)
                param.data = tensor
            if self.pinned:
                event = torch.cuda.Event()
                event.record(current)
                self.events[slot] = event
            self.free[self.kinds[i]].append(slot)
        return hook

    def _after(self, i):
        def hook(module, args, output):
            for name, param in module.named_parameters():
                param.data = torch.empty(0, device=self.device, dtype=param.dtype)
        return hook

    def close(self):
        for handle in self.handles:
            handle.remove()
        self.handles = []
        for slot, future in list(self.ready.values()):
            future.exception()  # wait for the read; a failed read-ahead was already reported when its layer ran
        self.ready.clear()
        self.pool.shutdown()


def run_layers_paired(layers, first, second, *, skip_second=(), call_hooks=True):
    """Apply each layer to both streams; ``first``/``second`` are (x, *extra) tuples passed to ``layer(x, *extra)``.

    A layer index in ``skip_second`` is applied to the first stream only. With ``call_hooks`` the layer's forward
    hooks run once per layer around the pair, which is what makes a streamed layer read once for both.
    Returns the two final streams.
    """
    x1, extra1 = first[0], first[1:]
    x2, extra2 = second[0], second[1:]
    skip = set(skip_second)
    for i, layer in enumerate(layers):
        if call_hooks:
            for hook in list(layer._forward_pre_hooks.values()):
                hook(layer, (x1,))
        x1 = layer.forward(x1, *extra1)
        if i not in skip:
            x2 = layer.forward(x2, *extra2)
        if call_hooks:
            for hook in list(layer._forward_hooks.values()):
                hook(layer, (x1,), x1)
    return x1, x2
