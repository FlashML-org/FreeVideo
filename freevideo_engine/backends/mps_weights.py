"""Bounded storage decoding and MPS layer residency.

Hydrate one matrix from original storage with an explicit CPU or Metal decoder.
Metal never receives float8 Torch tensors. No whole-model BF16 disk copy, locked
host planes, CUDA streams or implicit CPU operator fallback are involved.
"""
import json
import math
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import ExitStack
from pathlib import Path

import torch

from ..tensor_io import open_tensors

_DTYPES = {'F8_E4M3': torch.float8_e4m3fn, 'BF16': torch.bfloat16, 'F16': torch.float16,
           'F32': torch.float32, 'F64': torch.float64, 'I64': torch.int64, 'I32': torch.int32,
           'I16': torch.int16, 'I8': torch.int8, 'U8': torch.uint8, 'BOOL': torch.bool}


class HostTensors:
    """One safetensors file read whole into host memory; tensors are views of it.

The data section is read in parallel chunks: one synchronous read per tensor
left the SSD at a fraction of its bandwidth. File reads release the GIL, so a
background read overlaps the sampling thread's work. load_group binds device
copies (or CPU clones), never these views.
"""
    def __init__(self, path, *, pool=None, chunk=8 << 20, max_bytes=512 << 20):
        if (type(chunk) is not int or chunk <= 0 or
                type(max_bytes) is not int or max_bytes <= 0):
            raise ValueError('Prefetch needs positive chunk and buffer limits')
        # Keep safetensors' shape, offset, overlap and payload validation. Reading
        # ahead changes I/O scheduling, not which model files are accepted.
        with open_tensors(path) as checked:
            validated = {key: (checked.get_slice(key).get_shape(), checked.get_slice(key).get_dtype())
                         for key in checked.keys()}
        with open(path, 'rb', buffering=0) as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                raise ValueError('Truncated prefetched tensor header')
            length = struct.unpack('<Q', prefix)[0]
            if not 2 <= length <= 16 << 20:
                raise ValueError('Prefetched tensor header exceeds its bounded read')
            header = json.loads(handle.read(length))
            size = os.fstat(handle.fileno()).st_size
        header.pop('__metadata__', None)
        if set(header) != set(validated):
            raise ValueError('Prepared tensor header changed during prefetch')
        for key, info in header.items():
            if info['dtype'] not in _DTYPES:
                raise ValueError('Unsupported prepared tensor dtype: ' + key)
            if (info['shape'], info['dtype']) != validated[key]:
                raise ValueError('Prepared tensor metadata changed during prefetch: ' + key)
        self.header, self.start = header, 8 + length
        if not 0 <= size - self.start <= max_bytes:
            raise ValueError('Prepared tensor file exceeds the prefetch buffer limit')
        self.buffer = torch.empty(size - self.start, dtype=torch.uint8)
        view = memoryview(self.buffer.numpy())
        ranges = [(begin, min(begin + chunk, len(view))) for begin in range(0, len(view), chunk)]

        def read(bounds):
            begin, end = bounds
            with open(path, 'rb', buffering=0) as source:
                source.seek(self.start + begin)
                while begin < end:
                    count = source.readinto(view[begin:end])
                    if not count:
                        raise OSError('Prepared tensor file ended early: ' + str(path))
                    begin += count
        if pool is None or len(ranges) < 2:
            for bounds in ranges:
                read(bounds)
        else:
            futures = [pool.submit(read, bounds) for bounds in ranges]
            try:
                for future in futures:
                    future.result()
            finally:
                # A failed chunk must finish/cancel its siblings before a retry
                # allocates another host buffer on the same reader pool.
                for future in futures:
                    future.cancel()
                wait(futures)

    def keys(self):
        return list(self.header)

    def get_tensor(self, key):
        info = self.header[key]
        begin, end = info['data_offsets']
        dtype = _DTYPES[info['dtype']]
        raw = self.buffer[begin:end]
        if begin % dtype.itemsize:      # Element views need aligned storage offsets.
            raw = raw.clone()
        return raw.view(dtype).reshape(info['shape'])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def attach_lora(module, index, cache, manifest):
    """Bind shared eager LoRA forwards with unloaded, metadata-only factors.

    The returned sidecar joins the layer's existing residency files. Factors
    follow the same load/release hooks as the base weights on every sampling
    step; attaching adapters does not retain a second model in unified memory.
    """
    from ..adaln_assets import asset_path, tensor_header
    from ..lora_online import attach_block
    online = manifest.get('online_lora', {})
    if not online:
        return None
    if online.get('version') != 1:
        raise ValueError('Unsupported native LoRA manifest version')
    spec = online.get('blocks', {}).get(str(index))
    if spec is None:
        return None
    path = asset_path(cache, spec['file'])
    records = [row for row in manifest['groups'] if row['file'] == spec['file']]
    if len(records) != 1 or path.stat().st_size != records[0]['bytes']:
        raise ValueError('Incomplete native LoRA factor group')
    header = tensor_header(path)
    prefix = '' if index == 'root' else f'transformer_blocks.{index}.'
    allowed = 'token_refiner.refiner_blocks.' if index == 'root' else prefix
    dtypes = {'BF16': torch.bfloat16, 'F32': torch.float32}
    values = {}
    for name, row in header.items():
        if row['dtype'] not in dtypes:
            raise ValueError('Native LoRA factors require BF16 or FP32 storage')
        values[name] = torch.empty(row['shape'], dtype=dtypes[row['dtype']], device='meta')
    expected = set()
    for name, patches in spec['modules'].items():
        if not name.startswith(allowed) or not patches:
            raise ValueError('Native LoRA target is outside its streamed layer')
        layer = module.get_submodule(name.removeprefix(prefix))
        if not isinstance(layer, torch.nn.Linear) or hasattr(layer, '_freevideo_lora'):
            raise ValueError('Native LoRA requires an unpatched linear projection')
        for number, patch in enumerate(patches):
            scale, rank = patch['scale'], patch['rank']
            if (type(scale) not in (int, float) or not math.isfinite(scale)
                    or type(rank) is not int or rank <= 0):
                raise ValueError('Invalid native LoRA scale or rank')
            key = name + '._freevideo_lora.' + str(number)
            expected.update((key + '.a', key + '.b'))
            if key + '.a' not in values or key + '.b' not in values:
                raise ValueError('Native LoRA factor pair is incomplete')
            a, b = values[key + '.a'], values[key + '.b']
            dtype = manifest['linears'][name]['input_dtype']
            if (a.shape != (rank, layer.in_features) or b.shape != (layer.out_features, rank)
                    or a.dtype != b.dtype or str(a.dtype).removeprefix('torch.') != dtype):
                raise ValueError('Native LoRA factor shape or dtype differs from its projection')
    if set(values) != expected:
        raise ValueError('Native LoRA factor group includes unbound tensors')
    # attach_block installs the same two eager matmuls used by the shared
    # implementation. Its CUDA-only fused branch is never selected on MPS.
    attach_block(module, index, Path(cache), manifest, lambda _: values, device='meta')
    return path


def decode_weight(weight, scale, dtype=torch.bfloat16):
    if weight.device.type != 'cpu' or scale.device.type != 'cpu':
        raise ValueError('MPS storage decoding requires CPU source tensors')
    if weight.dtype != torch.float8_e4m3fn or weight.ndim != 2:
        raise ValueError('Expected a two-dimensional E4M3 weight')
    if scale.dtype != torch.float32 or scale.numel() not in (1, weight.shape[0]):
        raise ValueError('Expected FP32 scalar or per-output-channel scales')
    if dtype != torch.bfloat16:
        raise ValueError('MPS weight-only execution requires explicit BF16 weights')
    # This matches W8A16's E4M3 -> FP32, scale, then BF16 rounding order.
    # A full FF matrix would otherwise create hundreds of MiB of FP32 scratch.
    result = torch.empty(weight.shape, dtype=dtype, device='cpu')
    rows = max(1, (16 * 2**20) // max(1, weight.shape[1] * 4))
    scales = scale.reshape(-1, 1)
    for begin in range(0, weight.shape[0], rows):
        end = begin + rows
        block = weight[begin:end].float()
        block.mul_(scales if scales.numel() == 1 else scales[begin:end])
        result[begin:end].copy_(block)
        del block
    return result


def _bind(module, name, value):
    parent, _, leaf = name.rpartition('.')
    owner = module.get_submodule(parent) if parent else module
    parameter = leaf in owner._parameters
    if not parameter and leaf not in owner._buffers:
        raise ValueError('Unexpected prepared weight: ' + name)
    expected = getattr(owner, leaf)
    # Hybrid modules are constructed in FP32 on meta; checkpoint assignment
    # supplies their actual BF16/FP32 dtype, just as the shared CUDA loader does.
    # The explicit VAE compute cache additionally binds FP16 Linear parameters.
    # Int8 weights bind only where int8 storage was installed (an int8 placeholder).
    floating = value.dtype in (torch.float16, torch.bfloat16, torch.float32)
    if (expected is None or expected.shape != value.shape
            or not (floating or value.dtype == expected.dtype == torch.int8)):
        raise ValueError('Prepared weight shape/dtype changed: ' + name)
    setattr(owner, leaf, torch.nn.Parameter(value, requires_grad=False) if parameter else value)


@torch.no_grad()
def load_group(module, path, linears, *, prefix='', exclude=(), device='mps', decoder=None,
               linear_dtype=None, handles=None):
    """Bind only this module's tensors, one CPU matrix at a time.

    handles optionally supplies already-read files (HostTensors), one per path."""
    expected = {name for name, _ in list(module.named_parameters()) + list(module.named_buffers())
                if not any(name.startswith(skip) for skip in exclude)}
    loaded, total = set(), 0
    paths = [path] if isinstance(path, (str, Path)) else list(path)
    if handles is not None and len(handles) != len(paths):
        raise ValueError('Prefetched tensors need one handle per shard')
    # Views of a prefetched file must not outlive it as bound CPU parameters.
    own_copy = handles is not None and torch.device(device).type == 'cpu'
    with ExitStack() as stack:
        sources = {}
        for position, item in enumerate(paths):
            handle = (handles[position] if handles is not None
                      else stack.enter_context(open_tensors(item)))
            for key in handle.keys():
                if key.startswith(prefix):
                    if key in sources:
                        raise ValueError('Duplicate prepared tensor across shards: ' + key)
                    sources[key] = handle
        keys = set(sources)
        for key in sorted(keys):
            if not key.startswith(prefix):
                continue
            name = key[len(prefix):]
            source = sources[key]
            if any(name.startswith(skip) for skip in exclude):
                continue
            if key.endswith('.weight_scale'):
                # Int8 storage keeps its row scales beside an int8 weight when
                # the Linear runs int8 products; FP8 scales are consumed below.
                if name not in expected or key.removesuffix('.weight_scale') + '.weight_int8' not in keys:
                    continue
                value = source.get_tensor(key).reshape(-1).float()
            elif key.endswith('.weight_int8'):
                base = key.removesuffix('.weight_int8')
                entry = linears.get(base)
                if entry is None or entry.get('storage') != 'int8' or base + '.weight_scale' not in keys:
                    raise ValueError('Missing int8 weight metadata: ' + key)
                value = source.get_tensor(key)
                if list(value.shape) != entry['weight_shape'] or value.dtype != torch.int8:
                    raise ValueError('Int8 storage shape differs from its manifest: ' + key)
                name = name.removesuffix('weight_int8') + 'weight'
                if name.removesuffix('weight') + 'weight_scale' not in expected:
                    # Linears without int8 products get the dequantized BF16 matrix,
                    # rotated back when its rows were stored in the ConvRot basis.
                    # Macs without int8 TensorOps take every matrix this way, on the GPU.
                    scale = sources[base + '.weight_scale'].get_tensor(base + '.weight_scale').reshape(-1).float()
                    if entry.get('convrot_group') is not None and torch.device(device).type == 'mps':
                        from .mps_int8 import dequantize_convrot
                        value = dequantize_convrot(value.to(device), scale.to(device))
                    else:
                        value = value.float() * scale[:, None]
                        if entry.get('convrot_group') is not None:
                            from .mps_int8 import convrot_rotate
                            value = convrot_rotate(value, entry['convrot_group'])
                        value = value.to(torch.bfloat16)
                    del scale
            elif key.endswith('.weight_fp8'):
                base = key.removesuffix('.weight_fp8')
                entry = linears.get(base)
                if entry is None or entry['input_dtype'] != 'bfloat16' or base + '.weight_scale' not in keys:
                    raise ValueError('Missing FP8 weight metadata: ' + key)
                weight = source.get_tensor(key)
                scale = sources[base + '.weight_scale'].get_tensor(base + '.weight_scale')
                if list(weight.shape) != entry['weight_shape'] or list(scale.shape) != entry['scale_shape']:
                    raise ValueError('FP8 storage shape differs from its manifest: ' + key)
                value = (decoder or decode_weight)(weight, scale)
                del weight, scale
                name = name.removesuffix('weight_fp8') + 'weight'
            else:
                if name == 'original.bias':
                    name = 'bias'
                elif '.original.bias' in name:
                    name = name.replace('.original.bias', '.bias')
                value = source.get_tensor(key)
            if name not in expected or name in loaded:
                raise ValueError('Unexpected or duplicate prepared weight: ' + key)
            if linear_dtype is not None:
                parent, _, leaf = name.rpartition('.')
                owner = module.get_submodule(parent) if parent else module
                if isinstance(owner, torch.nn.Linear) and leaf in owner._parameters:
                    # The VAE reference loads FP32 then autocasts each Linear
                    # to FP16. Cache that cast; keep norms/scales in FP32.
                    value = value.float().to(linear_dtype)
            total += value.numel() * value.element_size()
            if own_copy and value.device.type == 'cpu':
                value = value.clone()
            _bind(module, name, value.to(device))
            loaded.add(name)
            del value
    if loaded != expected:
        raise ValueError('Incomplete prepared group: ' + ', '.join(sorted(expected - loaded)))
    return total


@torch.no_grad()
def release_group(module, *, exclude=()):
    for name, value in list(module.named_parameters()) + list(module.named_buffers()):
        if not any(name.startswith(skip) for skip in exclude):
            _bind(module, name, torch.empty(value.shape, dtype=value.dtype, device='meta'))


class LayerResidency:
    """One streamed layer, optionally retaining a bounded fixed subset.

Specs are (module, file, prefix, excluded names). All nonexcluded parameters
must start as meta tensors. Progress observes actual completed layer forwards.
Retention defaults to zero and requires an explicit working-memory reserve.
Every retained layer is released when the owning context exits.
"""
    def __init__(self, specs, linears, backend, progress=None, decoder=None, *,
                 resident_bytes=0, working_reserve_bytes=0, linear_dtype=None, preload=False,
                 synchronize_layers=True, release_cache_every_layer=True, cache_floor_bytes=2 * 2**30,
                 prefetch=False, prefetch_threads=8, prefetch_bytes=512 << 20):
        if (type(resident_bytes) is not int or resident_bytes < 0
                or type(working_reserve_bytes) is not int or working_reserve_bytes < 0
                or resident_bytes and not working_reserve_bytes):
            raise ValueError('MPS retained weights need a nonnegative cap and positive working reserve')
        if linear_dtype is not None and (linear_dtype != torch.float16 or linears):
            raise ValueError('MPS Linear compute caching requires FP16 and original VAE storage')
        if type(preload) is not bool or preload and linears:
            raise ValueError('MPS preloading requires original VAE storage')
        if (type(synchronize_layers) is not bool or type(release_cache_every_layer) is not bool
                or type(cache_floor_bytes) is not int or cache_floor_bytes < 0):
            raise ValueError('MPS streaming needs boolean fence/cache options and a nonnegative cache floor')
        if type(prefetch) is not bool or prefetch and (preload or linear_dtype is not None
                                                       or type(prefetch_threads) is not int
                                                       or prefetch_threads < 1
                                                       or type(prefetch_bytes) is not int
                                                       or prefetch_bytes <= 0):
            raise ValueError('MPS layer prefetching applies to sequential transformer streaming')
        self.specs, self.linears, self.backend = list(specs), linears, backend
        self.progress = progress
        self.decoder = decoder
        self.linear_dtype = linear_dtype
        self.preload = preload
        self.preloaded_layers = 0
        self.handles = []
        self.active = None
        self.loads = self.bytes = self.peak_layer_bytes = 0
        self.load_seconds = 0.
        self.resident_limit, self.working_reserve = resident_bytes, working_reserve_bytes
        self.synchronize_layers, self.release_cache_every_layer = synchronize_layers, release_cache_every_layer
        self.cache_floor = cache_floor_bytes
        self.retained = {}
        self.retained_bytes = self.peak_retained_bytes = self.cache_hits = self.evictions = 0
        self.deferred_cached_forwards = 0
        self.active_bytes = 0
        self.pending_work = False
        # Sampling visits layers in a fixed cycle. While one layer computes, a
        # reader thread fetches the next streamed layer's file into host memory
        # (one file, about 0.43 GB for H3); the sampling thread then only
        # copies and decodes. On an M5 the synchronous read was 0.15 s per
        # layer, longer than a small request's whole layer forward.
        self.prefetch, self.prefetch_threads = prefetch, prefetch_threads
        self.prefetch_bytes = prefetch_bytes
        self._prefetcher = self._readers = self._pending = None
        self.prefetch_hits = self.prefetch_misses = 0
        self.prefetch_wait_seconds = self.read_seconds = 0.

    def _read(self, index):
        _, path, _, _ = self.specs[index]
        tick = time.monotonic()
        paths = [path] if isinstance(path, (str, Path)) else list(path)
        # Include every shard and LoRA sidecar in the one-layer host allowance.
        # Oversized layers retain the original bounded per-tensor reader.
        sizes = [Path(item).stat().st_size for item in paths]
        if sum(sizes) > self.prefetch_bytes:
            raise ValueError('Prepared layer exceeds the prefetch buffer limit')
        handles = [HostTensors(item, pool=self._readers, max_bytes=size)
                   for item, size in zip(paths, sizes)]
        self.read_seconds += time.monotonic() - tick
        return handles

    def _take(self, index):
        pending, self._pending = self._pending, None
        if pending is not None:
            pending_index, future = pending
            tick = time.monotonic()
            try:
                handles = future.result()
            except Exception:
                handles = None          # Retried by the direct read below.
            self.prefetch_wait_seconds += time.monotonic() - tick
            if pending_index == index and handles is not None:
                self.prefetch_hits += 1
                return handles
            del handles
        self.prefetch_misses += 1
        if self.backend.memory_info()[0] < self.prefetch_bytes + self.working_reserve:
            return None
        try:
            return self._read(index)
        except ValueError:
            return None                 # Bounded reader handles larger layers or other dtypes.

    def _schedule_after(self, index):
        count = len(self.specs)
        following = next(((index + step) % count for step in range(1, count)
                          if (index + step) % count not in self.retained), None)
        if following is not None and self._prefetcher is not None:
            if self.backend.memory_info()[0] >= self.prefetch_bytes + self.working_reserve:
                self._pending = (following, self._prefetcher.submit(self._read, following))

    def _load(self, index):
        module, path, prefix, exclude = self.specs[index]
        tick = time.monotonic()
        self.pending_work = True
        handles = self._take(index) if self._prefetcher is not None else None
        size = load_group(module, path, self.linears, prefix=prefix, exclude=exclude,
                          device=self.backend.device, decoder=self.decoder, linear_dtype=self.linear_dtype,
                          handles=handles)
        del handles
        if self._prefetcher is not None:
            # Queue the next read only after this layer's host copy is released:
            # at most one prefetched layer is resident in host memory.
            self._schedule_after(index)
        self.load_seconds += time.monotonic() - tick
        self.loads += 1
        self.bytes += size
        self.peak_layer_bytes = max(self.peak_layer_bytes, size)
        return size

    def _synchronize(self):
        self.backend.synchronize()
        self.pending_work = False

    def _preload(self):
        # Allocate retained weights together before temporary decoder workspaces
        # exist. Keep that fixed set, reclaiming under pressure without refilling
        # it during this decode. The next request makes a fresh live decision.
        for index, (module, _, _, exclude) in enumerate(self.specs):
            size = 0
            for name, value in list(module.named_parameters()) + list(module.named_buffers()):
                if any(name.startswith(skip) for skip in exclude):
                    continue
                parent, _, leaf = name.rpartition('.')
                owner = module.get_submodule(parent) if parent else module
                cached = self.linear_dtype is not None and isinstance(owner, torch.nn.Linear) and leaf in owner._parameters
                size += value.numel() * (2 if cached else value.element_size())
            margin = max(self.working_reserve // 4, 2 * size)
            if (self.retained_bytes + size > self.resident_limit or
                    self.backend.memory_info()[0] < self.working_reserve + margin + size):
                break
            self.active = index  # close() also releases a partially loaded layer.
            loaded = self._load(index)
            self._synchronize()
            if loaded != size:
                raise ValueError('Preloaded MPS weight size differs from its original VAE metadata')
            self.retained[index] = loaded
            self.retained_bytes += loaded
            self.peak_retained_bytes = max(self.peak_retained_bytes, self.retained_bytes)
            self.preloaded_layers += 1
            self.active = None
            self.backend.empty_cache()

    def _release(self, index):
        module, _, _, exclude = self.specs[index]
        release_group(module, exclude=exclude)
        self.retained_bytes -= self.retained.pop(index, 0)

    def _reclaim(self):
        # Keep a fixed subset across the sequential layer cycle. An LRU cache
        # smaller than that cycle would evict every layer before its next use.
        # memory_info already subtracts the system reserve and allocator use.
        if self.retained and self.backend.memory_info()[0] < self.working_reserve:
            # Completed temporary allocations can still occupy driver heaps.
            # Return those before deciding that useful weights must be evicted.
            self._synchronize()
            self.backend.empty_cache()
        while self.retained and self.backend.memory_info()[0] < self.working_reserve:
            self._release(next(reversed(self.retained)))
            self.evictions += 1
            self.backend.empty_cache()

    def reconfigure(self, *, resident_bytes, working_reserve_bytes):
        """Resize retained weights at a completed sampling-phase boundary.

        Hooks and eligible tensors remain bound. Reduced capacity fences queued
        work before dropping any weights; subsequent forwards can stream them
        through the ordinary reader. This never changes precision or operators.
        """
        if (type(resident_bytes) is not int or resident_bytes < 0
                or type(working_reserve_bytes) is not int or working_reserve_bytes < 0
                or resident_bytes and not working_reserve_bytes):
            raise ValueError('Retained weights require a nonnegative limit and positive working reserve')
        if self.active is not None:
            raise RuntimeError('Cannot reconfigure weights during a layer forward')
        self.resident_limit, self.working_reserve = resident_bytes, working_reserve_bytes
        if self.retained_bytes > resident_bytes:
            self._synchronize()
            while self.retained_bytes > resident_bytes:
                self._release(next(reversed(self.retained)))
                self.evictions += 1
            self.backend.empty_cache()
        self._reclaim()

    def __enter__(self):
        try:
            for index, (module, path, prefix, exclude) in enumerate(self.specs):
                if any(not value.is_meta for name, value in module.named_parameters()
                       if not any(name.startswith(skip) for skip in exclude)):
                    raise ValueError('MPS streamed parameters must start unloaded')
                def before(layer, args, index=index):
                    if self.active is not None:
                        raise RuntimeError('MPS streamed layers cannot overlap')
                    if self.resident_limit:
                        self._reclaim()
                    self.active = index
                    self.pending_work = True
                    if index in self.retained:
                        self.active_bytes = self.retained[index]
                        self.cache_hits += 1
                        return
                    self.active_bytes = self._load(index)
                def after(layer, args, output, index=index):
                    if self.active != index:
                        return
                    # Preloaded weights remain alive on the same MPS stream.
                    # With spare working space and no completion callback,
                    # queue their next use without a per-layer host barrier.
                    # Reclamation, failed forwards and close() still fence
                    # before releasing weights; ordinary streaming is unchanged.
                    if (output is not None and self.preload and index in self.retained
                            and self.progress is None
                            and self.backend.memory_info()[0] >= self.working_reserve):
                        self.deferred_cached_forwards += 1
                        self.active = None
                        self.active_bytes = 0
                        return
                    keep = False
                    try:
                        # One MPS queue orders a release before any reuse, so
                        # streaming needs no host barrier here; syncing kept the
                        # next layer's read from overlapping this layer's compute.
                        # The default keeps the measured fence for retention
                        # decisions; fast sampling decides from the unfenced live
                        # reading, which the admission margin below absorbs.
                        if self.synchronize_layers or output is None or self.progress is not None:
                            self._synchronize()
                        cached = index in self.retained
                        additional = 0 if cached else self.active_bytes
                        if self.resident_limit and self.synchronize_layers:
                            self.backend.empty_cache()
                        # Admit new weights with extra space between the fill
                        # and eviction thresholds. Without that gap, ordinary
                        # layer workspace fluctuations refill and evict the
                        # same subset throughout every decoder tile.
                        margin = 0 if cached else max(self.working_reserve // 4, 2 * self.active_bytes)
                        if (output is not None and self.resident_limit
                                and (cached or not self.preload)
                                and self.retained_bytes + additional <= self.resident_limit
                                and self.backend.memory_info()[0] >= self.working_reserve + margin):
                            self.retained[index] = self.active_bytes
                            self.retained_bytes += additional
                            self.peak_retained_bytes = max(self.peak_retained_bytes, self.retained_bytes)
                            keep = True
                    finally:
                        if not keep:
                            if index in self.retained and output is not None:
                                self.evictions += 1
                            self._release(index)
                        self.active = None
                        self.active_bytes = 0
                        # Returning cached buffers costs a reallocation per layer;
                        # streaming does it only when the live allowance is short.
                        if not keep and (self.release_cache_every_layer
                                         or self.backend.memory_info()[0] < self.cache_floor):
                            self.backend.empty_cache()
                    if output is not None and self.progress is not None:
                        self.progress(index)
                self.handles.extend((module.register_forward_pre_hook(before),
                    module.register_forward_hook(after, always_call=True)))
            if self.preload and self.resident_limit:
                self._preload()
            if self.prefetch:
                self._readers = ThreadPoolExecutor(self.prefetch_threads, thread_name_prefix='freevideo-read')
                self._prefetcher = ThreadPoolExecutor(1, thread_name_prefix='freevideo-prefetch')
                self._schedule_after(-1)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        for handle in self.handles:
            handle.remove()
        self.handles.clear()
        # The read after the last layer is never used; wait for it, then drop it.
        self._pending = None
        for pool in (self._prefetcher, self._readers):
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        self._prefetcher = self._readers = None
        remaining = set(self.retained)
        if self.active is not None:
            remaining.add(self.active)
        if remaining or self.pending_work:
            try:
                self._synchronize()
            finally:
                for index in sorted(remaining):
                    self._release(index)
                self.active = None
                self.active_bytes = 0
                self.backend.empty_cache()

    def __exit__(self, *args):
        self.close()

    def stats(self):
        value = dict(layer_loads=self.loads, hydrated_weight_bytes=self.bytes,
                    largest_layer_bytes=self.peak_layer_bytes, load_seconds=self.load_seconds,
                    load_seconds_scope='Host read/submit time; device completion is included in each sampling step',
                    policy='one synchronous MPS layer; original storage decoded per tensor',
                    fp8_storage=bool(self.linears),
                    fp8_decode_device='mps' if self.decoder is not None else 'cpu',
                    retains_bf16_disk_copy=False,
                    layer_synchronization=('per-layer' if self.synchronize_layers else
                                           'completion callbacks, errors, reclamation and context exit'),
                    cache_release_policy=('per streamed layer' if self.release_cache_every_layer else
                                          'low headroom and context exit'))
        if not self.synchronize_layers:
            value['policy'] = 'one queued MPS layer; original storage decoded per tensor'
        if self.resident_limit or self.peak_retained_bytes or self.cache_hits or self.evictions:
            value.update(policy=('bounded retained MPS layers with ' +
                                ('synchronous' if self.synchronize_layers else 'queued') +
                                ' streaming and live headroom reclamation'),
                resident_limit_bytes=self.resident_limit, working_reserve_bytes=self.working_reserve,
                retained_weight_bytes=self.retained_bytes, peak_retained_weight_bytes=self.peak_retained_bytes,
                retained_layer_hits=self.cache_hits, retained_layer_evictions=self.evictions)
        if self.linear_dtype is not None:
            value['linear_compute_cache'] = 'FP16 Linear weights; original norm/scale storage'
        if self.prefetch:
            value.update(prefetch=dict(hits=self.prefetch_hits, misses=self.prefetch_misses,
                                       wait_seconds=self.prefetch_wait_seconds, read_seconds=self.read_seconds,
                                       threads=self.prefetch_threads,
                                       buffer_limit_bytes=self.prefetch_bytes,
                                       scope='Next streamed layer read on a host thread; one layer in flight'))
        if self.preload:
            value.update(preloaded_layers=self.preloaded_layers,
                         deferred_cached_forwards=self.deferred_cached_forwards,
                         retention_admission='fixed before first forward; pressure may only shrink it')
        return value
