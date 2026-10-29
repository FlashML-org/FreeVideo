"""Sequential module offloading with bounded pinned-host and CUDA buffers."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from contextlib import contextmanager, nullcontext
import json
import os
import re
import threading
import time
import weakref

import torch


def cpu_weights(layer):
    """Frozen parameters and CPU buffers (including official FP8 weights/scales).

    Precomputed modulation tables already resident on CUDA are left in place.
    Only immutable buffers may be included in an offloaded layer.
    """
    for name, value in layer.named_parameters():
        if value.device.type != 'cpu' or value.requires_grad:
            raise ValueError('Offloaded parameters must be frozen CPU tensors')
        yield name, value
    for name, value in layer.named_buffers():
        if value.device.type == 'cpu':
            if value.requires_grad:
                raise ValueError('Offloaded buffers must be frozen')
            yield name, value


def _pin_reservation(sizes):
    """Charge the host allocator's configured rounding, not only tensor bytes."""
    settings = os.environ.get('PYTORCH_ALLOC_CONF', os.environ.get('PYTORCH_CUDA_ALLOC_CONF', ''))
    threshold = float('inf')
    for option in ('pinned_max_round_threshold_mb', 'pinned_max_cached_size_mb'):
        match = re.search(r'(?:^|,)\s*' + option + r':\s*(\d+)(?:,|$)', settings)
        if match:
            threshold = min(threshold, int(match[1]) * 2**20)
    return sum((1 << (size - 1).bit_length()) if 0 < size <= threshold else size for size in sizes)


def live_pin_capacity(*, headroom_bytes=None, nonlocal_reserve_bytes=None, commit_reserve_bytes=0):
    """Bytes that page-locking may take now, beyond the working headroom."""
    # Charge free physical memory only. Effective availability credits this
    # tree's reclaimable mapped pages, which is right for measuring pressure
    # but wrong for deciding how much to lock: locked pages cannot be
    # reclaimed, so space that only exists after reclaim cannot back them.
    # Crediting them raised the cap to about 15 GiB and cudaHostAlloc failed
    # with cudaErrorMemoryAllocation during load. On Windows, pinned pages
    # also consume system commit, so physical headroom alone can be wildly
    # optimistic when the pagefile is nearly full. Cap against both counters;
    # Linux reports no separate commit budget here and remains unchanged.
    #
    # The planned budget came from availability measured before text encoding
    # ran, and a plan made at 22.70 GiB free pinned 10.88 GiB and left 1.73 GiB,
    # under the emergency floor, because encoding had taken the host to
    # 2.77 GiB. Cap against live availability so whole groups drop out and keep
    # their mapped storage instead of failing the request.
    from .system import system_memory, HOST_WEIGHT_HEADROOM
    if headroom_bytes is None:
        headroom_bytes = HOST_WEIGHT_HEADROOM
    if type(headroom_bytes) is not int or headroom_bytes < 2 * 2**30:
        raise ValueError('Pinning must preserve at least 2 GiB of host working space')
    memory = system_memory()
    available = memory.get('physical_available_bytes') or memory['available_bytes']
    commit_available = memory.get('commit_available_bytes')
    if commit_available is not None:
        # WDDM charges this process's GPU allocations to system commit as they
        # are made, so allocator room the request has not reserved yet cannot
        # back locked pages either. Pinning against commit alone left an 8 GiB
        # card's request with under 1 GiB of commit once sampling reserved its
        # workspace, and live pressure then switched off host read-ahead and
        # the step cache for the rest of the request. The caller passes that
        # unreserved allocator room; physical RAM is not charged for it.
        available = min(available, commit_available - commit_reserve_bytes)
    cap = available - headroom_bytes
    from .system import windows
    if windows():
        # Free RAM does not bound locked pages on Windows: the WDDM non-local
        # budget does. A 3.5 GiB working allowance on a 27.98 GiB machine pinned
        # past it on the second request, and every retry failed the same way.
        # The reserve covers locked buffers allocated after the weights; the
        # caller adds its request's CPU attention readouts to the default.
        from .windows_gpu_memory import nonlocal_pin_capacity, NONLOCAL_PIN_RESERVE
        reserve = NONLOCAL_PIN_RESERVE if nonlocal_reserve_bytes is None else nonlocal_reserve_bytes
        capacity = nonlocal_pin_capacity(reserve)
        if capacity is None and memory.get('total_bytes'):
            # Without a DXGI reading, assume the Windows default of half of RAM.
            try:
                pinned = torch.cuda.memory.host_memory_stats()['allocated_bytes.current']
            except (RuntimeError, KeyError, AttributeError):
                pinned = 0
            capacity = max(0, memory['total_bytes'] // 2 - pinned - reserve)
        if capacity is not None:
            cap = min(cap, capacity)
    return cap


@torch.no_grad()
def pin_layer_weights(layers, max_bytes=None, *, headroom_bytes=None, nonlocal_reserve_bytes=None,
                      commit_reserve_bytes=0, progress=None):
    """Pack immutable CPU parameters into persistent pinned planes, one per dtype.

    This optional mode trades pageable/mapped storage for locked host residency.
    Rebinding preserves parameter values and permits direct asynchronous H2D.
    """
    if type(commit_reserve_bytes) is not int or commit_reserve_bytes < 0:
        raise ValueError('Commit reserve must be a nonnegative byte count')
    layers = list(layers)
    layer_groups = []
    for layer in layers:
        by_dtype = {}
        for name, parameter in cpu_weights(layer):
            if not isinstance(parameter, torch.nn.Parameter) and parameter._base is not None:
                # safetensors buffers are views. Reassigning their .data alone
                # keeps the old view base (and mapped FP8 file) alive alongside
                # the pinned copy. Detach the registered buffer first; this
                # shares the bytes without retaining view metadata.
                parent, _, leaf = name.rpartition('.')
                owner = layer.get_submodule(parent) if parent else layer
                parameter = parameter.detach()
                setattr(owner, leaf, parameter)
            by_dtype.setdefault(parameter.dtype, []).append(parameter)
        layer_groups.append(list(by_dtype.items()))

    cap = live_pin_capacity(headroom_bytes=headroom_bytes, nonlocal_reserve_bytes=nonlocal_reserve_bytes,
                            commit_reserve_bytes=commit_reserve_bytes)
    max_bytes = cap if max_bytes is None else min(max_bytes, cap)

    groups, sizes, reserved = [], [], 0
    for entries in layer_groups:
        current_sizes = [sum(p.numel() * p.element_size() for p in parameters) for _, parameters in entries]
        current_reserve = _pin_reservation(current_sizes)
        if reserved + current_reserve > max_bytes:
            continue
        groups.extend(entries)
        sizes.extend(current_sizes)
        reserved += current_reserve
    required = sum(sizes)
    # Charge the container's non-reclaimable working set, not clean file cache:
    # mmap-backed weights are replaced group by group while pinning, and their
    # clean pages can be reclaimed. Leave a separate 2 GiB process allowance.
    from pathlib import Path
    cgroup = Path('/sys/fs/cgroup')
    if (cgroup / 'memory.max').is_file():
        limit = (cgroup / 'memory.max').read_text(encoding='utf-8').strip()
        if limit != 'max':
            stat = {key: int(value) for key, value in (line.split() for line in (cgroup / 'memory.stat').read_text(encoding='utf-8').splitlines())}
            clean_file = max(0, stat.get('file', 0) - sum(stat.get(k, 0) for k in ('shmem', 'file_dirty', 'file_writeback', 'unevictable')))
            available_cgroup = int(limit) - int((cgroup / 'memory.current').read_text(encoding='utf-8')) + clean_file
            if available_cgroup < reserved + 2 * 2**30:
                raise MemoryError('Pinned weights exceed cgroup working-set allowance; select a smaller pinned budget')
    pinned = 0
    for index, (dtype, parameters) in enumerate(groups):
        try:
            plane = torch.empty(sum(p.numel() for p in parameters), dtype=dtype, pin_memory=True)
        except (RuntimeError, MemoryError) as error:
            # Pinning is an optimization, so a host allocator that stops short
            # must cost transfers, not the request. Locked host memory has a
            # platform ceiling independent of free RAM: this machine refused
            # every attempt past 12.60 GiB with 25.1, 10.9 and 9.9 GiB free,
            # while the plan asked for 15.05 GiB because the budget scales with
            # available RAM. Whatever is not pinned keeps its mapped storage.
            print(json.dumps({'event': 'host_pinning_truncated', 'pinned_bytes': pinned,
                              'planned_bytes': required, 'groups_pinned': index,
                              'groups_planned': len(groups),
                              'reason': str(error).splitlines()[0][:200]}), flush=True)
            return pinned
        offset = 0
        for parameter in parameters:
            view = plane[offset:offset + parameter.numel()].view(parameter.shape)
            view.copy_(parameter)
            offset += parameter.numel()
            parameter.data = view
        pinned += sum(p.numel() * p.element_size() for p in parameters)
        if progress is not None:
            progress(pinned, required)
    return pinned


@dataclass
class Slot:
    host: dict
    device: dict
    copy_done: object = None
    compute_done: object = None


@dataclass
class _HostRead:
    """One layer being prepared ahead in a staging block."""

    slot: Slot
    future: object
    from_drive: bool  # Not held in RAM as a view when it was queued.
    lane: str = None  # 'drive' or 'ram' once the two are read ahead separately.


# Live RAM that must stay free beside the one prepared block host read-ahead
# holds. It used to borrow the 8 GiB weight-cache allowance, which no 16 GiB
# machine reaches while sampling: a 16 GiB RTX 3060 Laptop reread all 21.6 GB
# of weights every step (about 80 s) with reads and compute never overlapping.
# 3 GiB still kept 32 GiB machines busy with other applications (2-3.5 GiB
# available while sampling) from overlapping. The read-ahead adds one block of
# locked pages, and the reads happen either way. With 41 GiB held by another
# process on a 56 GiB RTX 3090 machine (about 2 GiB available while sampling),
# 1344x768x124 went from 15.5-15.9 to 10.7-10.9 s per first-pass step and
# 29.8-30.0 to 26.0-26.1 per second-pass step at 1 GiB, twice each (327-332 s
# to 283-294 s for the request). The runtime check in _queue_host returns the
# extra block under live pressure; read-ahead then shares the staging buffer.
HOST_PREFETCH_HEADROOM = 1 * 2**30


# A read from the drive that keeps the inference thread waiting this long means
# the drive, not the GPU, sets the pace of those layers. From then on the next
# layer that comes from the drive is read into the extra block as soon as that
# block is free, past the layers RAM already holds, which keep going through the
# staging buffer. Until a read is that slow, read-ahead stays one layer deep on
# alternating blocks, as before.
HOST_FILE_READ_WAIT = 0.2


def _host_prefetch_fits(extra_bytes):
    """Speculative staging must fit both live physical RAM and Windows commit."""
    from .system import system_memory
    try:
        memory = system_memory()
        available = memory.get('physical_available_bytes')
        if available is None:
            available = memory['available_bytes']
        commit = memory.get('commit_available_bytes')
        if commit is not None:
            available = min(available, commit)
        return available >= HOST_PREFETCH_HEADROOM + extra_bytes
    except (OSError, ValueError, TypeError, KeyError):
        return False


@dataclass
class _StreamedLayout:
    """Metadata for reusing an unloaded layer, without owning any weight storage."""

    source: object
    index: int
    entries: tuple

    def shapes(self, source, index, weights):
        if self.source() is not source or source is None or self.index != index:
            raise ValueError('Unloaded streamed layers require their original checkpoint source and order')
        if len(weights) != len(self.entries):
            raise ValueError('Unloaded streamed layer tensors changed; reload the model')
        shapes = {}
        for (name, parameter), (reference, saved_name, dtype, count, shape) in zip(weights, self.entries):
            if (reference() is not parameter or name != saved_name or parameter.dtype != dtype
                    or parameter.numel() != 0 or tuple(parameter.shape) != (0,)):
                raise ValueError('Unloaded streamed layer tensors changed; reload the model')
            shapes[name] = count, shape
        return shapes


@torch.no_grad()
def initialize_streamed_layer(layer, source, index):
    """Bind an unmaterialized layer to a checkpoint without reading its weights."""
    tensors = ([(name, value, True) for name, value in layer.named_parameters()]
               + [(name, value, False) for name, value in layer.named_buffers()])
    if source is None or any(not value.is_meta for _, value, _ in tensors):
        raise ValueError('Streamed initialization requires a checkpoint and only meta tensors')
    entries = []
    for name, value, parameter in tensors:
        count, shape, dtype = value.numel(), tuple(value.shape), value.dtype
        empty = torch.empty(0, dtype=dtype, device='cpu')
        if parameter:
            empty = torch.nn.Parameter(empty, requires_grad=False)
        parent, _, leaf = name.rpartition('.')
        owner = layer.get_submodule(parent) if parent else layer
        setattr(owner, leaf, empty)
        entries.append((weakref.ref(empty), name, dtype, count, shape))
    layer._freevideo_streamed_layout = _StreamedLayout(weakref.ref(source), index, tuple(entries))


@torch.no_grad()
def unload_streamed_layer(layer, source, index):
    """Keep a newly loaded layer's layout without retaining its checkpoint bytes.

    Safetensors buffers are tensor views, including with the pread reader.
    Emptying their .data does not clear _base: it would retain the entire CPU
    tensor until sampling constructs an offloader. Detach registered buffers
    first, then record the new identities used to restore the exact layout.
    """
    entries = []
    for name, parameter in cpu_weights(layer):
        if not isinstance(parameter, torch.nn.Parameter) and parameter._base is not None:
            parent, _, leaf = name.rpartition('.')
            owner = layer.get_submodule(parent) if parent else layer
            parameter = parameter.detach()
            setattr(owner, leaf, parameter)
        entries.append((weakref.ref(parameter), name, parameter.dtype,
                        parameter.numel(), tuple(parameter.shape)))
        parameter.data = torch.empty(0, dtype=parameter.dtype)
    layer._freevideo_streamed_layout = _StreamedLayout(weakref.ref(source), index, tuple(entries))


@torch.no_grad()
def prepare_streamed_layer(layer, source, index, *, pin_budget_bytes, headroom_bytes, nonlocal_reserve_bytes=None,
                           commit_reserve_bytes=0):
    """Retain one affordable pinned layer or release it before loading the next.

    Return logical pinned bytes and the charged allocator reservation. A partial
    host-allocation failure keeps no half-pinned layer: the streaming offloader
    would discard it anyway, and it must not consume the next layer's budget.
    """
    sizes = {}
    for _, value in cpu_weights(layer):
        sizes[value.dtype] = sizes.get(value.dtype, 0) + value.numel() * value.element_size()
    reserved = _pin_reservation(sizes.values())
    pinned = 0
    if 0 < reserved <= pin_budget_bytes:
        pinned = pin_layer_weights([layer], max_bytes=pin_budget_bytes, headroom_bytes=headroom_bytes,
                                   nonlocal_reserve_bytes=nonlocal_reserve_bytes,
                                   commit_reserve_bytes=commit_reserve_bytes)
        if pinned == sum(sizes.values()) and all(value.is_pinned() for _, value in cpu_weights(layer)):
            return pinned, reserved
    unload_streamed_layer(layer, source, index)
    if pinned:
        from .torch_compat import empty_host_cache
        empty_host_cache(torch)
    return 0, 0


def loading_retains(source, index, *, pin_budget_bytes, **pin_options):
    """Whether preparing a streamed layer may keep what loading reads: a pin or a view.

    The checks of prepare_streamed_layer (planned budget, then the live cap of
    pin_layer_weights, with pin_options) and of SafetensorLayers.adopt, made on
    checkpoint header sizes, which equal the bound tensors, before the layer is
    read. pin_layer_weights measures with the layer already read into private
    memory, so the live cap here is charged its size as well.
    """
    sizes = source.layer_bytes(index)
    size = sum(sizes.values())
    reservation = _pin_reservation(sizes.values())
    if 0 < reservation <= pin_budget_bytes and reservation <= live_pin_capacity(**pin_options) - size:
        return True
    from .streamed_weights import host_view_fits
    return source.host_views is not None and host_view_fits(size, source.host_view_reserve)


class LayerOffloader:
    """Keep CPU weights and stream a sequential layer list through one or two slots.

    CUDA events protect both host-buffer reuse and device-buffer overwrite. No
    parameter is changed numerically; hooks rebind its storage only while it runs.
    This requires inference without autograd and one request at a time.

    Streamed layers retain zero-storage placeholders after close. Reuse them with
    the same checkpoint source and layer order; only in-memory sources are restored.
    """

    def __init__(self, layers, device='cuda', prefetch=True, manage_hooks=True, weight_source=None,
                 host_prefetch=None):
        if torch.is_grad_enabled():
            raise RuntimeError('LayerOffloader requires gradients disabled')
        self.layers = list(layers)
        self.device = torch.device(device)
        self.prefetch = prefetch
        self.weight_source = weight_source
        self.cpu = []
        self.layout = []
        sizes = {}
        for index, layer in enumerate(self.layers):
            weights, layout, offsets = {}, [], {}
            parameters = list(cpu_weights(layer))
            saved = getattr(layer, '_freevideo_streamed_layout', None)
            shapes = saved.shapes(weight_source, index, parameters) if saved is not None else {}
            for name, parameter in parameters:
                if weight_source is not None and not isinstance(parameter, torch.nn.Parameter) and parameter._base is not None:
                    parent, _, leaf = name.rpartition('.')
                    owner = layer.get_submodule(parent) if parent else layer
                    parameter = parameter.detach()
                    setattr(owner, leaf, parameter)
                dtype = parameter.dtype
                offset = offsets.get(dtype, 0)
                count, shape = shapes.get(name, (parameter.numel(), parameter.shape))
                weights[name] = parameter.detach()
                layout.append((parameter, name, dtype, offset, count, shape))
                offsets[dtype] = offset + count
            for dtype, count in offsets.items():
                sizes[dtype] = max(sizes.get(dtype, 0), count)
            self.cpu.append(weights)
            self.layout.append(layout)
            del parameters
        self.pinned_layers = [all(value.is_pinned() for value in weights.values()) for weights in self.cpu]
        if weight_source is not None:
            # Keep module objects and their metadata, but no full-checkpoint
            # tensor views. The pre-hook restores exact shapes before a layer
            # executes. Offloaded placeholders own zero bytes between calls.
            for index, (layer, pinned, weights, layout) in enumerate(zip(
                    self.layers, self.pinned_layers, self.cpu, self.layout)):
                if pinned:
                    continue  # Keep a bounded pinned subset; reopen only the rest.
                layer._freevideo_streamed_layout = _StreamedLayout(weakref.ref(weight_source), index, tuple(
                    (weakref.ref(parameter), name, dtype, count, shape)
                    for parameter, name, dtype, _, count, shape in layout))
                for parameter, name, dtype, *_ in layout:
                    empty = torch.empty(0, dtype=dtype)
                    parameter.data = empty
                    weights[name] = empty
        self.pinned_sources = all(self.pinned_layers)
        # Staging buffers are an optimization over pageable copies, so a host
        # allocator that refuses them must cost transfer speed, not the
        # request. Weight pinning had already taken 14.51 GiB of locked memory
        # and left nothing here, failing load with cudaErrorMemoryAllocation.
        self.pinned_staging = True

        def staging(count, dtype):
            if self.pinned_sources:
                return None
            try:
                return torch.empty(count, dtype=dtype, pin_memory=True)
            except (RuntimeError, MemoryError):
                self.pinned_staging = False
                return torch.empty(count, dtype=dtype)

        self.slots = [Slot(
            host={} if self.pinned_sources else {dtype: staging(count, dtype) for dtype, count in sizes.items()},
            device={dtype: torch.empty(count, dtype=dtype, device=self.device) for dtype, count in sizes.items()},
        ) for _ in range(2 if prefetch else 1)]
        self.copy_stream = torch.cuda.Stream(device=self.device)
        self.executor = ThreadPoolExecutor(max_workers=1) if prefetch else None
        self.pending = {}
        self.transfers = []
        self.host_buffer_wait_seconds = 0.
        self.prefetch_wait_seconds = 0.
        self.direct_read_layers = 0
        self.direct_read_bytes = 0
        self.source_layers = 0
        self.reader_layers = 0
        # The weight source outlives this pass; report only this pass's view hits.
        self.host_view_hits_start = getattr(weight_source, 'host_view_hits', 0)
        self.host_view_hit_bytes_start = getattr(weight_source, 'host_view_hit_bytes', 0)
        self.direct_reads_start = dict(getattr(weight_source, 'read_stats', None) or {})
        self.hooks = []
        self.expected = 0
        self.closed = False
        self.cached = {}
        self.cache_candidates = set()
        self.cache_budget_bytes = 0
        self.cache_hits = 0
        self.read_ahead_before_cache = None
        self.read_ahead = (weight_source.read_ahead(self.layout, self.pinned_layers)
                           if weight_source is not None else None)
        # A second CUDA slot is not affordable on every card. With enough RAM,
        # prepare one future layer on the CPU while the current layer computes;
        # upload it only at the next normal boundary through the SAME GPU slot.
        # Keep the existing full prefetch path when two device slots are funded.
        from .system import windows
        requested = windows() if host_prefetch is None else host_prefetch
        self.host_prefetch = False
        # Memory pressure no longer switches read-ahead off (see the shared
        # reason below); reports keep this field for earlier versions.
        self.host_prefetch_disabled_reason = None
        self.host_prefetch_shared_reason = None
        self.host_slots = []
        self.host_executor = None
        self.host_pending = {}
        self.host_cursor = 0
        self.host_prefetch_reads = 0
        self.host_prefetch_wait_seconds = 0.
        self.host_memory_checked = 0.
        # Reads of drive-backed layers past the next streamed layer: their own
        # thread, and the extra block while it is not being handed back.
        self.host_file_ahead = bool(getattr(weight_source, 'reads_delay_compute', False))
        self.host_file_executor = None
        self.host_file_ahead_reads = 0
        self.host_extra_returning = False
        self.host_counters = threading.Lock()
        if (requested and not prefetch and sum(not p for p in self.pinned_layers) > 1
                and getattr(weight_source, 'direct_read', False)
                and getattr(weight_source, 'intermediate_dtype', None) is None
                and self.pinned_staging):
            # Without room for a second block of locked pages, read ahead into
            # the staging buffer every streamed layer already passes through:
            # the next read starts once the current layer's upload completes,
            # while that layer computes. Reading on the inference thread instead
            # waits until it has queued the current layer, and with a full
            # launch queue the GPU then idles through most of the read. With 18
            # of 50 layers reread from the file every step, each read about as
            # long as a layer's compute, 1344x768x362 steps went from 41.1 to
            # 32.0 s at head groups of 2 and from 34.5 to 27.6 s at 4 on an RTX
            # PRO 6000 (latents identical); the second block saves 0.4 s more.
            self.host_slots = [Slot(host=self.slots[0].host, device={})]
            extra = _pin_reservation([plane.numel() * plane.element_size()
                                      for plane in self.slots[0].host.values()])
            if _host_prefetch_fits(extra):
                host = {}
                try:
                    for dtype, count in sizes.items():
                        host[dtype] = torch.empty(count, dtype=dtype, pin_memory=True)
                except (RuntimeError, MemoryError):
                    host.clear()
                    self.host_prefetch_shared_reason = 'host_allocation_refused'
                    from .torch_compat import empty_host_cache
                    empty_host_cache(torch)
                else:
                    self.host_slots.append(Slot(host=host, device={}))
            else:
                self.host_prefetch_shared_reason = 'host_headroom'
            self.host_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='freevideo-host-stage')
            self.host_prefetch = True
        for index, layer in enumerate(self.layers if manage_hooks else []):
            self.hooks.append(layer.register_forward_pre_hook(
                lambda module, args, index=index: self._before(index)))
            self.hooks.append(layer.register_forward_hook(
                lambda module, args, result, index=index: self._after(index), always_call=True))

    @property
    def at_boundary(self):
        """No layer pass is open, so the next pass can start; False after a failure inside one."""
        return not (self.expected or self.pending or self.host_pending)

    def end_pass_early(self):
        """Finish a successful partial traversal after its last forward hook.

        Indices belong to this offloaded subset, not the transformer's blocks.
        Block zero may be resident, so an already idle traversal is valid too.
        Keep admitted weight caches and live reader pools for the next step.
        """
        if self.closed:
            raise RuntimeError('Cannot finish a closed offloader')
        # _stage may still be submitting copies or filling an admitted cache.
        # Join producers before waiting on the final events they recorded.
        for future in self.pending.values():
            future.result()
        for read in self.host_pending.values():
            if not read.future.cancel():
                read.future.result()
        # A skipped prefetch has copy_done but no new compute_done. Likewise,
        # the first block's compute may still read its slot after Python returns.
        # Never reset/reuse either plane until both operations have completed.
        for slot in [*self.slots, *self.cached.values(), *self.host_slots]:
            for event in (slot.copy_done, slot.compute_done):
                if event is not None:
                    event.synchronize()
        if self.read_ahead is not None:
            self.read_ahead.end_pass_early()
        self.pending.clear()
        self.host_pending.clear()
        self.host_cursor = 0
        self.expected = 0

    def cache_between_steps(self, max_bytes, *, max_layers=None, allow_growth=True):
        """Retain affordable device planes until this sampling pass closes.

        Admit only at a completed step boundary, after measuring real workspace.
        Prefer disk-backed layers: caching them also removes repeated host reads.
        Loading remains incremental through the existing staging buffers.
        With no later reuse, retain only already-filled caches: filling a new
        plane on the final forward just spends memory before discarding it.
        """
        if self.expected or self.pending or self.host_pending or self.closed:
            raise RuntimeError('Pass cache admission requires an idle step boundary')
        remaining = self.cache_budget_bytes = max(0, int(max_bytes))
        candidates = set()
        # Disk reads cost most, then copies from RAM-resident views, then pinned H2D.
        views = getattr(self.weight_source, 'host_views', None) or {}
        for index in sorted(range(len(self.layers)), key=lambda i: (self.pinned_layers[i], i in views)):
            if not allow_growth and index not in self.cached:
                continue
            if max_layers is not None and len(candidates) >= max_layers:
                break
            size = sum(count * parameter.element_size() for parameter, _, _, _, count, _ in self.layout[index])
            if 0 < size <= remaining:
                candidates.add(index)
                remaining -= size
        for index in set(self.cached) - candidates:
            slot = self.cached.pop(index)
            for event in (slot.compute_done, slot.copy_done):
                if event is not None:
                    event.synchronize()
            slot.device.clear()
        changed = candidates != self.cache_candidates
        self.cache_candidates = candidates
        if changed and self.read_ahead is not None:
            self.read_ahead.close()
            self.read_ahead_before_cache = self.read_ahead.stats()
            self.read_ahead = self.weight_source.read_ahead(self.layout,
                [pinned or index in self.cache_candidates for index, pinned in enumerate(self.pinned_layers)])

    def cached_bytes(self):
        return sum(t.numel() * t.element_size() for slot in self.cached.values() for t in slot.device.values())

    @contextmanager
    def layer(self, index):
        """Keep one layer resident while a caller evaluates several independent tiles."""
        if self.hooks:
            raise RuntimeError('Explicit layer contexts require manage_hooks=False')
        self._before(index)
        try:
            yield self.layers[index]
        finally:
            self._after(index)

    def _fill_host(self, index, slot):
        # CPU writes must wait for the previous H2D read of this host plane.
        # They need not wait for GPU computation using the separate device plane.
        with torch.cuda.device(self.device):
            waited = 0.
            if slot.copy_done is not None:
                tick = time.monotonic()
                slot.copy_done.synchronize()
                waited = time.monotonic() - tick
            started = time.monotonic()
            if self.read_ahead is not None:
                self.read_ahead.before(index)
            direct = self.weight_source.stage_into(index, self.layout[index], slot.host)
            counts = {}
            if not direct:
                self.reader_layers += 1
                with self.weight_source.layer(index, self.layout[index]) as weights:
                    for _, name, dtype, offset, count, _ in self.layout[index]:
                        slot.host[dtype][offset:offset + count].copy_(weights[name].reshape(-1))
            read = 0
            for _, _, dtype, offset, count, _ in self.layout[index]:
                counts[dtype] = offset + count
                if direct:
                    read += count * slot.host[dtype].element_size()
            # The two read-ahead threads and the inference thread all fill.
            with self.host_counters:
                self.host_buffer_wait_seconds += waited
                self.direct_read_bytes += read
                self.direct_read_layers += int(bool(direct))
            if self.read_ahead is not None:
                self.read_ahead.consumed(index)
            return slot, counts, time.monotonic() - started

    def _stop_host_prefetch(self):
        for read in self.host_pending.values():
            read.future.cancel()
        for executor in (self.host_executor, self.host_file_executor):
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        self.host_executor = self.host_file_executor = None
        for slot in self.host_slots:
            if slot.copy_done is not None:
                slot.copy_done.synchronize()
        self.host_pending.clear()
        self.host_slots.clear()
        self.host_prefetch = False

    def _free_host_slot(self):
        """A staging block no queued read is filling, in the order blocks alternate."""
        for _ in self.host_slots:
            slot = self.host_slots[self.host_cursor % len(self.host_slots)]
            self.host_cursor += 1
            if all(slot is not read.slot for read in self.host_pending.values()):
                return slot
        raise RuntimeError('Every staging block is held by a queued read')

    def _host_views(self):
        return getattr(self.weight_source, 'host_views', None) or {}

    def _submit_host(self, index, slot, lane=None):
        executor = self.host_executor
        if lane == 'drive':
            if self.host_file_executor is None:
                self.host_file_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='freevideo-host-read')
            executor = self.host_file_executor
        self.host_pending[index] = _HostRead(slot, executor.submit(self._fill_host, index, slot),
                                             index not in self._host_views(), lane)
        self.host_prefetch_reads += 1

    def _queue_host(self, start):
        if not self.host_prefetch:
            return
        now = time.monotonic()
        if len(self.host_slots) > 1 and now - self.host_memory_checked >= 1.:
            self.host_memory_checked = now
            if not _host_prefetch_fits(0):
                # Return only the extra block of locked pages. Reading ahead
                # through the shared staging buffer holds no more memory than
                # reading on demand does.
                self.host_prefetch_shared_reason = 'live_host_pressure'
                self.host_extra_returning = True
        if self.host_extra_returning and all(read.slot is not self.host_slots[-1] for read in self.host_pending.values()):
            # A read already queued into the block is used first.
            self.host_extra_returning = False
            extra = self.host_slots.pop()
            if extra.copy_done is not None:
                extra.copy_done.synchronize()
            del extra
            self.host_cursor = 0
            from .torch_compat import empty_host_cache
            empty_host_cache(torch)
        ahead = [index for index in range(start, len(self.layers))
                 if not self.pinned_layers[index] and index not in self.cached]
        if not ahead:
            return
        if not (self.host_file_ahead and len(self.host_slots) > 1 and not self.host_extra_returning
                and self.read_ahead is None):
            if not self.host_pending:
                self._submit_host(ahead[0], self._free_host_slot())
            return
        # Layers RAM holds are copied in a fraction of a layer's compute, so
        # the staging buffer alone keeps them one layer ahead. The extra block
        # goes to the drive: the next layer that has to be read from it starts
        # as soon as the block's last upload is done, however many RAM-backed
        # layers run in between, and on a thread of its own so that those
        # layers do not queue behind the read. One drive read runs at a time.
        # With 10 of 50 layers read from the drive every step, spread through
        # the model by loading, and each read taking 3.3 times a second-pass
        # layer's compute, 1344x768x243 went from 26.4-27.6 to 18.2-20.1 s per
        # second-pass step (18.2 s without the reads) and from 16.4 to 12.5 s
        # per first-pass step, where the ten reads alone take 12 s, on an RTX
        # PRO 6000 with identical output. One layer ahead, each of those reads
        # overlapped a single layer's compute and the GPU waited for the rest.
        views = self._host_views()
        shared, extra = self.host_slots[0], self.host_slots[-1]
        near = ahead[0]
        target = next((index for index in ahead if index not in views), None)
        if (target is not None and target not in self.host_pending
                and not any(read.lane == 'drive' for read in self.host_pending.values())):
            if target == near and not self.host_pending:
                self._submit_host(target, self._free_host_slot(), 'drive')
            elif all(read.slot is not extra for read in self.host_pending.values()):
                self._submit_host(target, extra, 'drive')
                self.host_file_ahead_reads += int(target != near)
        if (near in views and near not in self.host_pending
                and all(read.slot is not shared and read.lane == 'drive' for read in self.host_pending.values())):
            self._submit_host(near, shared, 'ram')

    def _stage(self, index):
        slot = self.slots[index % len(self.slots)]
        with torch.cuda.device(self.device):
            if index in self.cached:
                if self.read_ahead is not None:
                    self.read_ahead.consumed(index)
                self.cache_hits += 1
                cached = self.cached[index]
                return cached, cached.copy_done
            # A previous H2D transfer must finish before the CPU writes this slot.
            pinned = self.pinned_layers[index]
            if not pinned and self.weight_source is not None:
                self.source_layers += 1
            host_slot = slot
            staged = self.host_prefetch and not pinned
            if slot.copy_done is not None and not pinned and not staged:
                wait_started = time.monotonic()
                slot.copy_done.synchronize()
                self.host_buffer_wait_seconds += time.monotonic() - wait_started
            started = time.monotonic()
            if self.read_ahead is not None and not staged:
                self.read_ahead.before(index)
            counts = {}
            direct = (not staged and self.weight_source is not None and not pinned
                      and self.weight_source.stage_into(index, self.layout[index], slot.host))
            if staged:
                if index in self.host_pending:
                    read = self.host_pending.pop(index)
                    tick = time.monotonic()
                    host_slot, counts, host_seconds = read.future.result()
                    waited = time.monotonic() - tick
                    self.host_prefetch_wait_seconds += waited
                    if read.from_drive and waited > HOST_FILE_READ_WAIT and not self.host_file_ahead:
                        self.host_file_ahead = True
                        if hasattr(self.weight_source, 'reads_delay_compute'):
                            # The next pass of this request starts with it on.
                            self.weight_source.reads_delay_compute = True
                else:
                    host_slot, counts, host_seconds = self._fill_host(index, self._free_host_slot())
            elif direct:
                self.direct_read_layers += 1
                for _, _, dtype, offset, count, _ in self.layout[index]:
                    counts[dtype] = offset + count
                    self.direct_read_bytes += count * slot.host[dtype].element_size()
            else:
                if self.weight_source is not None and not pinned:
                    self.reader_layers += 1
                source = (self.weight_source.layer(index, self.layout[index]) if self.weight_source is not None and not pinned
                          else nullcontext(self.cpu[index]))
                with source as weights:
                    for _, name, dtype, offset, count, _ in self.layout[index]:
                        if not pinned:
                            slot.host[dtype][offset:offset + count].copy_(weights[name].reshape(-1))
                        counts[dtype] = offset + count
            if self.read_ahead is not None and not staged:
                self.read_ahead.consumed(index)
            if not staged:
                host_seconds = time.monotonic() - started
            begin, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            with torch.cuda.stream(self.copy_stream):
                destination = slot
                if index in self.cache_candidates:
                    # Allocate on the copying stream so reused allocator blocks
                    # cannot race outstanding work from another CUDA stream.
                    planes = {}
                    try:
                        for dtype, count in counts.items():
                            planes[dtype] = torch.empty(count, dtype=dtype, device=self.device)
                    except torch.cuda.OutOfMemoryError:
                        planes.clear()
                        self.cache_candidates.discard(index)
                    else:
                        destination = Slot(host={}, device=planes)
                        self.cached[index] = destination
                # Forward kernels may still be reading the device slot from two
                # layers ago even though Python already left that forward call.
                if destination.compute_done is not None:
                    self.copy_stream.wait_event(destination.compute_done)
                begin.record()
                if pinned:
                    for _, name, dtype, offset, count, _ in self.layout[index]:
                        destination.device[dtype][offset:offset + count].copy_(self.cpu[index][name].reshape(-1), non_blocking=True)
                else:
                    for dtype, count in counts.items():
                        destination.device[dtype][:count].copy_(host_slot.host[dtype][:count], non_blocking=True)
                end.record()
            slot.copy_done = end
            host_slot.copy_done = end
            destination.copy_done = end
            self.transfers.append((index, host_seconds, begin, end,
                                   sum(count * slot.device[dtype].element_size() for dtype, count in counts.items())))
            return destination, end

    def _before(self, index):
        if index != self.expected:
            raise RuntimeError(f'Expected layer {self.expected}, got {index}; concurrent/reordered execution is unsupported')
        if index in self.pending:
            wait_started = time.monotonic()
            slot, done = self.pending.pop(index).result()
            self.prefetch_wait_seconds += time.monotonic() - wait_started
        else:
            slot, done = self._stage(index)
        torch.cuda.current_stream(self.device).wait_event(done)
        for parameter, _, dtype, offset, count, shape in self.layout[index]:
            parameter.data = slot.device[dtype][offset:offset + count].view(shape)
        if self.prefetch and index + 1 < len(self.layers):
            self.pending[index + 1] = self.executor.submit(self._stage, index + 1)
        self._queue_host(index + 1)

    def _after(self, index):
        slot = self.cached.get(index, self.slots[index % len(self.slots)])
        done = torch.cuda.Event()
        done.record(torch.cuda.current_stream(self.device))
        slot.compute_done = done
        for parameter, name, *_ in self.layout[index]:
            parameter.data = self.cpu[index][name]
        self.expected = (index + 1) % len(self.layers)

    def drive_reads(self):
        """Layers this pass has read from the weight files so far; None if unknown.

        Pinned groups, cached device copies and adopted views are in memory.
        A direct-read source counts each layer it reads from its files; a
        layer staged through its tensor reader is one more. A source that
        keeps no such count has every staged layer taken as a read. Pageable
        tensors without a source may be mapped file pages, which the system
        reads again unseen.
        """
        if self.weight_source is None:
            return 0 if self.pinned_sources else None
        counted = getattr(self.weight_source, 'read_stats', None)
        if counted is None:
            return self.source_layers
        return self.reader_layers + sum(counted[name] - self.direct_reads_start.get(name, 0)
                                        for name in ('kept_layers', 'passed_layers'))

    def drive_layers(self):
        """Offloaded layers neither pinned nor held as a RAM view; None if unknown.

        Each use of such a layer reads its file unless a device copy is
        cached, and a cached copy can be released between steps.
        """
        if self.weight_source is None:
            return 0 if self.pinned_sources else None
        views = getattr(self.weight_source, 'host_views', None) or {}
        return sum(not pinned and index not in views for index, pinned in enumerate(self.pinned_layers))

    def stats(self):
        self.copy_stream.synchronize()
        return {'prefetch': self.prefetch, 'slots': len(self.slots),
                'streamed_checkpoint_layers': self.weight_source is not None,
                'streamed_layer_count': sum(not pinned for pinned in self.pinned_layers) if self.weight_source is not None else 0,
                'pinned_model_sources': self.pinned_sources,
                'pinned_layer_count': sum(self.pinned_layers),
                'pinned_layer_indices': [index for index, pinned in enumerate(self.pinned_layers) if pinned],
                'cuda_buffer_bytes': sum(t.numel() * t.element_size() for s in self.slots for t in s.device.values()),
                'pass_cache_budget_bytes': self.cache_budget_bytes,
                'pass_cache_bytes': sum(t.numel() * t.element_size() for s in self.cached.values() for t in s.device.values()),
                'pass_cache_layer_count': len(self.cached),
                'pass_cache_layer_indices': sorted(self.cached), 'pass_cache_hits': self.cache_hits,
                'pinned_buffer_bytes': sum(t.numel() * t.element_size()
                    for s in [*self.slots, *self.host_slots[1:]] for t in s.host.values() if t.is_pinned()),
                'pageable_buffer_bytes': sum(t.numel() * t.element_size() for s in self.slots for t in s.host.values() if not t.is_pinned()),
                'transfers': len(self.transfers),
                'h2d_bytes': sum(row[4] for row in self.transfers),
                'host_stage_seconds': sum(row[1] for row in self.transfers),
                'host_buffer_wait_seconds': self.host_buffer_wait_seconds,
                'prefetch_wait_seconds': self.prefetch_wait_seconds,
                'h2d_seconds': sum(row[2].elapsed_time(row[3]) for row in self.transfers) / 1000,
                'direct_read_layers': self.direct_read_layers,
                'direct_read_bytes': self.direct_read_bytes,
                'host_view_layers': len(getattr(self.weight_source, 'host_views', None) or {}),
                'host_view_bytes': getattr(self.weight_source, 'host_view_bytes', 0),
                'host_view_hits': getattr(self.weight_source, 'host_view_hits', 0) - self.host_view_hits_start,
                'host_view_hit_bytes': getattr(self.weight_source, 'host_view_hit_bytes', 0) - self.host_view_hit_bytes_start,
                'host_view_released': getattr(self.weight_source, 'host_view_released', 0),
                # Converted layers kept in RAM for the clips still to come.
                'converted_layer_count': len(getattr(self.weight_source, 'converted', None) or {}),
                'converted_bytes': getattr(self.weight_source, 'converted_bytes', 0),
                'converted_hits': getattr(self.weight_source, 'converted_hits', 0),
                'converted_refused': getattr(self.weight_source, 'converted_refused', 0),
                # Direct reads kept in the file cache between steps and those passing through.
                'cache_kept_layer_count': len(getattr(self.weight_source, 'cache_kept', None) or {}),
                'cache_kept_bytes': getattr(self.weight_source, 'cache_kept_bytes', 0),
                **{'direct_' + name: value - self.direct_reads_start.get(name, 0)
                   for name, value in (getattr(self.weight_source, 'read_stats', None) or {}).items()},
                'host_prefetch': self.host_prefetch,
                'host_prefetch_reads': self.host_prefetch_reads,
                'host_prefetch_wait_seconds': self.host_prefetch_wait_seconds,
                'host_prefetch_buffer_bytes': sum(t.numel() * t.element_size()
                    for slot in self.host_slots[1:] for t in slot.host.values()),
                # Drive-backed layers read ahead past RAM-backed ones.
                'host_prefetch_file_ahead': self.host_file_ahead,
                'host_prefetch_file_ahead_reads': self.host_file_ahead_reads,
                'host_prefetch_disabled_reason': self.host_prefetch_disabled_reason,
                'host_prefetch_shared_reason': self.host_prefetch_shared_reason,
                'disk_read_ahead': self.read_ahead.stats() if self.read_ahead is not None else {'enabled': False},
                'disk_read_ahead_before_cache': self.read_ahead_before_cache,
                'timing_note': 'Host staging, buffer waits and H2D may overlap compute. Prefetch wait can include '
                               'buffer waiting and host staging; do not add these counters to wall time.'}

    def close(self):
        if self.closed:
            return
        if self.executor is not None:
            self.executor.shutdown(wait=True)
        self._stop_host_prefetch()
        if self.read_ahead is not None:
            self.read_ahead.close()
        self.copy_stream.synchronize()
        for slot in [*self.slots, *self.cached.values()]:
            if slot.compute_done is not None:
                slot.compute_done.synchronize()
        for handle in self.hooks:
            handle.remove()
        for index, entries in enumerate(self.layout):
            # Streamed CPU entries are zero-storage placeholders. Reopening all
            # checkpoint mappings here defeats streaming and can exhaust Windows
            # commit after the final step, before the caller can save its latents.
            # Retained layout metadata reconstructs the next request's slots.
            for parameter, name, *_ in entries:
                parameter.data = self.cpu[index][name]
        self.pending.clear()
        self.cached.clear()
        self.cache_candidates.clear()
        self.slots.clear()
        release = getattr(self.weight_source, 'release_converted', None)
        if release is not None:
            release()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
