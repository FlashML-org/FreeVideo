"""Request-aware model residency inside the isolated interactive GPU worker."""
import gc
import json
from pathlib import Path
import sys
import time

import torch
from .torch_compat import empty_host_cache, host_cache_release_supported

GiB = 1 << 30
# Where a preview's sampling peak is kept, apart from its complete request's.
PREVIEW_STAGE = 'engine_preview'


def key(value):
    from .adaln_assets import digest
    return digest(value)


def file_identity(path):
    from .storage import fingerprint
    path = Path(path).resolve()
    return dict(path=str(path), stamp=fingerprint(path))


def gpu_bytes(model):
    if hasattr(model, 'transformer'):
        model = model.transformer
    elif hasattr(model, 'cond_stage_model'):
        model = model.cond_stage_model
    if model is None:
        return 0
    storages = {}
    for tensor in list(model.parameters()) + list(model.buffers()):
        if tensor.device.type == 'cuda':
            try:
                storage = tensor.untyped_storage()
                storages[storage.data_ptr()] = storage.nbytes()
            except (NotImplementedError, RuntimeError):
                # Native quantized tensor wrappers expose their packed tensors.
                for value in vars(tensor).values():
                    if isinstance(value, torch.Tensor) and value.device.type == 'cuda':
                        storage = value.untyped_storage()
                        storages[storage.data_ptr()] = storage.nbytes()
    return sum(storages.values())


def pinned_cpu_storages(model, *, committed=False):
    """Owned, physically backed weights which eviction can actually release.

    Mapped/pageable weights are excluded: Linux already credits clean file
    cache, and Windows commit is not resident RAM. Count storage once even
    when several tensor views (or model entries) share a pinned plane.
    ``committed`` also leaves out registered read-only checkpoint maps: they
    are resident while pinned, but file-backed, so releasing them returns no
    commit.
    """
    if hasattr(model, 'transformer'):
        model = model.transformer
    elif hasattr(model, 'cond_stage_model'):
        model = model.cond_stage_model
    if model is None:
        return {}
    from .encoder_checkpoint import owns_readonly_tensor
    storages = {}
    for tensor in list(model.parameters()) + list(model.buffers()):
        if tensor.device.type != 'cpu':
            continue
        try:
            if tensor.is_pinned() and not (committed and owns_readonly_tensor(tensor)):
                storage = tensor.untyped_storage()
                storages[storage.data_ptr()] = storage.nbytes()
        except (NotImplementedError, RuntimeError):
            # Unknown wrapper ownership earns no speculative RAM allowance.
            continue
    return storages


def is_parked(model):
    """Whether an engine handed its GPU weights to host memory with park()."""
    return type(getattr(model, 'parked', None)) is list


def parked_bytes(model):
    """Host bytes of weights an engine moved off the GPU with park()."""
    if not is_parked(model):
        return 0
    records = model.parked
    total = 0
    for module, kind, name in records:
        tensor = getattr(module, kind).get(name)
        if tensor is not None:
            total += tensor.numel() * tensor.element_size()
    return total


def release_encoder(clip, manager):
    """Discard our mapped encoder without a full GPU-to-CPU copy.

    Native unpatching still releases pins, hooks and loaded-model registrations.
    Once the bank drops its references, unused tensors can die where they are;
    any other owner keeps valid tensors. Future encoding uses the mmap loader.
    """
    from .encoder_checkpoint import load_clip_model_patcher
    def owned(patcher):
        factory = getattr(patcher, 'cached_patcher_init', None)
        return isinstance(factory, (tuple, list)) and bool(factory) and factory[0] is load_clip_model_patcher
    patcher = getattr(clip, 'patcher', None)
    # A native unpatch copies every weight to the CPU first: 13 GiB for the video text encoder,
    # enough to exceed a machine's memory in the moment it was meant to make room. The Mac
    # releases its encoder its own way (macos_encoder) and keeps the native path here.
    targets = [patcher] if owned(patcher) and sys.platform != 'darwin' else []
    if targets:
        for loaded in manager.current_loaded_models:
            candidate = loaded.model
            if candidate is not None and candidate.model is patcher.model:
                if not owned(candidate):
                    targets = []  # An unknown shared owner keeps native offload semantics.
                    break
                if all(candidate is not target for target in targets):
                    targets.append(candidate)
    saved = []
    try:
        for target in targets:
            native = target.unpatch_model
            saved.append((target, 'unpatch_model' in vars(target), vars(target).get('unpatch_model')))
            def discard(device_to=None, unpatch_weights=True, *, _native=native):
                # None is native unpatch_model's supported "do not move" mode.
                return _native(device_to=None, unpatch_weights=unpatch_weights)
            target.unpatch_model = discard
        manager.unload_all_models()
        # Unloading leaves the native streaming buffers allocated; see
        # encoder_memory.release_cast_buffers. The caller empties the cache.
        reset = getattr(manager, 'reset_cast_buffers', None)
        if callable(reset):
            reset()
    finally:
        for target, existed, original in reversed(saved):
            if existed:
                target.unpatch_model = original
            else:
                del target.unpatch_model
    return bool(targets)


class ModelBank:
    def __init__(self):
        self.entries = {}
        self.peaks = {}
        self.gpu_budget = None
        self.gpu_reserve = 0
        self.ram_budget = None
        self.decisions = []
        from .streamed_weights import ConvertedStore
        # Converted decoder layers streamed from the checkpoint; rebuilt on demand.
        self.converted_store = ConvertedStore()
        # Set by a request that can read the whole device: bytes no process
        # holds, and the growth reserve that request keeps out of them. The
        # planning budget already excludes that reserve; the device reading
        # does not.
        self.device_free = None
        self.device_reserve = 0

    def configure(self, gpu_budget, ram_budget=None, *, gpu_reserve=0):
        self.gpu_budget = int(gpu_budget)
        self.gpu_reserve = int(gpu_reserve)
        self.ram_budget = ram_budget
        self.decisions = []

    def take(self, role, identity):
        entry = self.entries.get(role)
        if entry and entry['identity'] != identity:
            self.drop(role, 'Model, LoRA, geometry or execution settings changed')
            entry = None
        if entry:
            entry['used'] = time.monotonic()
            self.decisions.append(dict(action='reuse', role=role, gpu_bytes=gpu_bytes(entry['model'])))
            return entry['model']

    def put(self, role, identity, model):
        self.entries[role] = dict(identity=identity, model=model, used=time.monotonic())

    def observe_peak(self, role, identity, peak):
        item = (role, identity)
        self.peaks.pop(item, None)
        self.peaks[item] = max(0, int(peak))
        while len(self.peaks) > 128:
            del self.peaks[next(iter(self.peaks))]

    def drop(self, role, reason):
        entry = self.entries.pop(role, None)
        if entry is None:
            return
        model = entry['model']
        before = torch.cuda.memory_allocated()
        encoder_discarded = False
        if role == 'encoder':
            import comfy.model_management
            encoder_discarded = release_encoder(model, comfy.model_management)
        elif role == 'engine':
            model.close()
        else:
            model.to('meta')
        del model, entry
        gc.collect()
        torch.cuda.empty_cache()
        host_cache_released = empty_host_cache(torch)
        self.decisions.append(dict(action='evict', role=role, reason=reason,
                                   encoder_discarded_without_cpu_copy=encoder_discarded,
                                   host_cache_released=host_cache_released,
                                   released_gpu_bytes=max(0, before - torch.cuda.memory_allocated())))

    def park(self, role, reason, ram_need=2*GiB):
        """Release only an idle engine's GPU weights and keep its host side.

        Only when live RAM also covers the parked copy beside the next stage's
        own need; otherwise the caller releases the whole model as before.
        """
        entry = self.entries.get(role)
        model = entry['model'] if entry else None
        if role != 'engine' or not callable(getattr(model, 'park', None)) or is_parked(model):
            return False
        held = gpu_bytes(model)
        def declined(why):
            self.decisions.append(dict(action='keep_declined', role=role, reason=why, gpu_bytes=held))
            return False
        if not self.ram_fits(ram_need + held):
            return declined('Host memory does not cover the parked weights beside the next stage')
        started = time.perf_counter()
        before = torch.cuda.memory_allocated()
        moved = model.park()
        if moved is None:
            return declined('Weights share storage; a per-tensor copy would duplicate it')
        torch.cuda.empty_cache()
        released = max(0, before - torch.cuda.memory_allocated())
        if released < .9 * moved:
            # Something outside the registered tensors still holds these
            # weights, so parking would keep two copies. The caller releases
            # the model as before; copying back first could itself not fit.
            return declined('GPU weights are still referenced elsewhere')
        self.decisions.append(dict(action='park', role=role, reason=reason, parked_bytes=moved,
                                   released_gpu_bytes=released, seconds=time.perf_counter() - started))
        return True

    def clear(self, reason='Request failed or session configuration changed'):
        self.converted_store.clear()
        for role in list(self.entries):
            self.drop(role, reason)
        gc.collect()
        if torch.cuda.is_initialized():
            torch.cuda.empty_cache()

    def available(self):
        free, _ = torch.cuda.mem_get_info()
        allocated = torch.cuda.memory_allocated()
        return max(0, min(free + torch.cuda.memory_reserved() - allocated - self.gpu_reserve,
                          self.gpu_budget - allocated))

    def ram_fits(self, need=2*GiB):
        from .ram import ProcessMemory
        from .system import inference_memory_sample, inference_headroom
        import os
        sample = ProcessMemory().sample(os.getpid())
        used = inference_memory_sample(sample)
        return (used is not None and inference_headroom(sample) >= need + 2*GiB and
                (self.ram_budget is None or used + need <= self.ram_budget))

    def settle(self, pinned=0):
        """End of a request: keep the engine and encoder only if the next cold encoder still fits.

        The RAM guard counts the whole session, so retained engine weights
        plus the next encoder's start (about 8 GiB) must stay inside the
        budget, and so must the host weights a fresh plan would pin beyond those
        the engine already holds: measured, a retained copy left no room to
        pin and sampling took 95 s instead of 38 s. Otherwise the engine goes
        as it did with one worker per request.
        """
        # A retained engine's pinned weights are reused by the next request
        # rather than pinned again, so they count once, in the session's use.
        held = sum(pinned_cpu_storages(self.entries['engine']['model']).values()) if 'engine' in self.entries else 0
        need = 8*GiB + max(0, int(pinned) - held)
        if not self.ram_fits(need):
            self.converted_store.clear()
        for role in ('engine', 'encoder'):
            if role in self.entries and not self.ram_fits(need):
                self.drop(role, 'Retained weights leave no host room for the next request')
                if role == 'engine':
                    need = 8*GiB + int(pinned)
        gc.collect()
        try:
            import ctypes
            ctypes.CDLL('libc.so.6').malloc_trim(0)
        except (OSError, AttributeError):
            pass

    def input_vae_room(self, model, canvas, *, audio=False):
        # Input encoding has its own live workspace; it cannot assume an output
        # decoder's memory footprint. Charge actual retained CPU model tensors
        # before upload plus a canvas-scaled starting workspace estimate.
        weights = sum(t.numel()*t.element_size() for t in list(model.parameters()) + list(model.buffers()))
        area = canvas.get('width', 1344)*canvas.get('height', 768)/(1344*768)
        workspace = 2*GiB if audio else max(2*GiB, 6*GiB*area)
        # The same stage also grows this whole process's host memory: the
        # video encoder's checkpoint pages and the decoded reference frames
        # rose by more than 13 GiB at 960x544 (about 26 GiB per full 1344x768
        # canvas) beside an idle engine's pinned weights. That pushed a
        # 25.6 GiB budget through its RAM guard and failed the continued
        # segment. The GPU estimate says nothing about host memory, so an idle
        # model's host copy is released unless the live budget covers the
        # stage as well. Plentiful hosts keep it for reuse.
        host = weights + (2*GiB if audio else max(8*GiB, 26*GiB*area))
        self.make_room('input_audio_vae' if audio else 'input_video_vae', weights + workspace, ram_need=host)

    def device_ceiling(self):
        """What this process's allocator can hold before the device itself is full.

        Its own pool plus what no process holds. Under Windows neither CUDA's
        free memory nor the allocator budget counts other applications until
        the device is already full. None when the device cannot be read.
        """
        read = self.device_free
        if not callable(read):
            return None
        try:
            free = read()
        except (OSError, RuntimeError, AttributeError, ValueError):
            return None
        if type(free) is not int or free < 0:
            return None
        return free + torch.cuda.memory_reserved()

    def make_room(self, role, peak, *, model=None, ram_need=2*GiB, device=False):
        retained = gpu_bytes(model) if model is not None else 0
        extra = max(0, int(peak) - retained)
        # Read once: releasing a model moves its bytes from this pool to the
        # device's free memory and leaves their sum where it was.
        ceiling = self.device_ceiling() if device else None
        reserve = max(self.gpu_reserve, self.device_reserve)
        budgeted = self.available
        def available():
            room = budgeted()
            if ceiling is not None:
                room = min(room, max(0, ceiling - torch.cuda.memory_allocated() - reserve))
            return room
        before = available()
        if not self.ram_fits(ram_need):
            self.converted_store.clear()
        # Once conditioning is ready the current video cannot use the encoder
        # again. Reclaim its speculative weights before a VAE needed later in
        # this very request, regardless of how recently preloading touched it.
        def eviction_order(item):
            return (0 if item == 'encoder' and role != 'encoder' else 1, self.entries[item]['used'])
        for other in sorted(self.entries, key=eviction_order):
            if available() >= extra and self.ram_fits(ram_need):
                break
            if other == role:
                continue
            if is_parked(self.entries[other]['model']):
                # A parked engine holds no GPU memory; only RAM releases it.
                if self.ram_fits(ram_need):
                    continue
            elif self.available() < extra and self.park(
                    other, 'Next %s stage needs GPU memory; host copy kept for the next request' % role, ram_need):
                continue
            self.drop(other, 'Next %s stage needs memory; cached weights are reclaimable' % role)
        self.decisions.append(dict(action='admit', role=role, estimated_peak_bytes=int(peak),
            reused_model_bytes=retained, additional_bytes=extra, available_before_bytes=before,
            available_after_bytes=available(), budget_bytes=self.gpu_budget,
            reason='Keep idle models when live resources cover the next stage; estimates do not certify capacity'))
        if ceiling is not None:
            self.decisions[-1]['device_ceiling_bytes'] = ceiling

    def engine_on_gpu(self):
        entry = self.entries.get('engine')
        return entry is not None and not is_parked(entry['model'])

    def engine_key(self, request):
        return key(dict(cache=file_identity(Path(request['cache'])/'manifest.json'),
                        options={k: v for k, v in request['engine_options'].items() if k != 'pin_host_gb'},
                        geometry=request.get('geometry'),
                        inputs=request.get('input_cache_dir')))

    def engine(self, request, factory):
        identity = self.engine_key(request)
        found = self.take('engine', identity)
        options, canvas = request['engine_options'], request.get('geometry', {})
        # A larger host-cache allowance does not change weights or arithmetic.
        # Keep the existing affordable cache rather than rereading every block
        # just to pin one more layer. A lower cap must still release the old
        # placement; never silently retain pins beyond a reduced RAM budget.
        # The allowance is a ceiling the load may not have reached, and free
        # RAM moves it between requests: compare what the cache really pinned.
        config = getattr(found, 'config', {})
        retained_pin_gb = config.get('pin_host_gb', 0.)
        requested_pin_gb = options.get('pin_host_gb', 0.)
        pinned = config.get('pinned_host_allocated_bytes') or config.get('pinned_model_bytes')
        held_pin_gb = min(retained_pin_gb, pinned / 1e9) if type(pinned) is int and pinned > 0 else retained_pin_gb
        if found is not None and held_pin_gb > requested_pin_gb:
            self.drop('engine', 'Host weight cache allowance decreased')
            found = None
        hit = found is not None
        rows = canvas.get('video_tokens', 72576) + canvas.get('reference_video_tokens', 0) + canvas.get('reference_audio_tokens', 0)
        if canvas.get('early_reference') is not None:
            from .two_pass import placement_tokens
            rows = placement_tokens(canvas)
        from .policy import BLOCK_BYTES, activation_bytes, windows_gpu_output_workspace
        from .system import windows
        # Use the same measured token/head envelopes as placement. The old
        # 10 GiB * tokens / 107856 estimate admitted a 13.19 GiB idle encoder
        # beside a 5090 request whose sampling actually reserved 18.71 GiB.
        # This only evicts idle models; it never lowers the requested compute.
        head = max(4, options.get('head_chunk', 8) or 16)
        workspace = max(5*GiB, 10*GiB*rows/107856, activation_bytes(head, rows))
        source = 'policy_token_head_workspace'
        if windows() and not options.get('attention_cpu_outputs', False):
            # The Windows envelope includes allocator slack and the FF stash.
            # Smaller compatibility chunks do not remove that stash; wider
            # groups still pay their measured incremental activation cost.
            workspace = max(workspace, windows_gpu_output_workspace(rows,
                prefetch=options.get('prefetch', False)) +
                max(0, activation_bytes(head, rows) - activation_bytes(8, rows)))
            source = 'windows_gpu_output_workspace'
        estimate = int(options.get('resident_blocks', 0)*BLOCK_BYTES + workspace)
        # A preview samples the first pass alone, under the key of the request
        # it previews. Its peak is evidence for another preview only: an
        # upscale of it runs the second pass, whose workspace is about twice
        # as large, and an idle audio decoder admitted on the preview's figure
        # stayed on the card through that pass. A complete request's peak
        # covers a preview too.
        preview = request.get('preview') is True
        observed = self.peaks.get((PREVIEW_STAGE if preview else 'engine', identity))
        if observed is None and preview:
            observed = self.peaks.get(('engine', identity))
        peak = estimate if observed is None else observed
        # Sampling is the long stage: an idle model stays through it only where
        # the device still holds both beside every other application.
        self.make_room('engine', peak, model=found, device=True, ram_need=8*GiB)
        self.decisions[-1]['evidence'] = dict(source=source if observed is None else 'complete_local_stage_peak',
            cold_estimate_bytes=estimate, observed_peak_bytes=observed, video_and_reference_tokens=rows,
            head_chunk=options.get('head_chunk', 8), window_batch=options.get('window_batch', 1),
            requested_pin_host_gb=requested_pin_gb,
            retained_pin_host_gb=retained_pin_gb if hit else None)
        if found is not None and is_parked(found):
            started = time.perf_counter()
            moved = found.unpark()
            self.decisions.append(dict(action='unpark', role='engine', moved_bytes=moved,
                                       seconds=time.perf_counter() - started))
        if found is None:
            found = factory()
            self.put('engine', identity, found)
        return found, hit

    def encoder_room(self, clip, tokens, fallback_peak):
        """Admit a reused native encoder with this input's workspace estimate."""
        estimator = getattr(clip.cond_stage_model, 'memory_estimation_function', None)
        peak, source = fallback_peak, 'encoder_checkpoint_estimate'
        workspace = None
        if callable(estimator):
            workspace = estimator(tokens, device=clip.patcher.load_device)
            source = 'native_encoder_weights_and_token_workspace'
        else:
            # The pinned H3 encoder has no native estimator. Native Windows
            # short-text runs reserve < 1.5 GiB beyond its packed model size;
            # keep 2 GiB for these inputs. Vision entries and longer/unknown
            # layouts retain the original conservative checkpoint estimate.
            sequences = tokens.get('qwen3vl_32b') if isinstance(tokens, dict) else None
            if (isinstance(tokens, dict) and set(tokens) == {'qwen3vl_32b'}
                    and isinstance(sequences, (list, tuple)) and len(sequences) == 1
                    and isinstance(sequences[0], (list, tuple)) and 0 < len(sequences[0]) <= 512
                    and all(isinstance(token, (list, tuple)) and token and type(token[0]) is int
                            for token in sequences[0])):
                workspace, source = 2*GiB, 'short_text_encoder_workspace'
        import math
        weights = clip.patcher.model_size()
        if (type(workspace) in (int, float) and math.isfinite(workspace) and workspace >= 0
                and type(weights) in (int, float) and math.isfinite(weights) and weights > 0):
            # Retained packed tensors and native model_size differ for
            # quantized wrappers. Charge the larger plus forward workspace.
            peak = max(weights, gpu_bytes(clip)) + max(2*GiB, workspace)
        else:
            source = 'encoder_checkpoint_estimate'
        # Measured: a reused encoder grew the whole session's guarded working
        # set by 4 GiB beside 21.5 GiB of retained engine weights, passing a
        # 25 GiB budget that a 2 GiB admission had accepted. Charge 4 GiB so
        # the retained host copy is parked first on tight machines.
        self.make_room('encoder', min(self.gpu_budget, peak), model=clip, ram_need=4*GiB)
        self.decisions[-1]['evidence'] = dict(source=source, cold_estimate_bytes=int(fallback_peak),
                                            requested_peak_bytes=int(peak))

    def decoder_room(self, options, canvas):
        if options.get('offload'):
            # The streamed decoder installs offload hooks on a different model;
            # its workspace cannot credit a cached full-GPU decoder it won't use.
            self.drop('video_vae', 'The requested streamed decoder cannot reuse the full-GPU VAE')
            from .policy import decoder_block_bytes
            need = (options.get('resident_blocks', 0)
                    * decoder_block_bytes(linear_compute_cache=bool(options.get('linear_compute_cache')))
                    + 4*GiB)
        else:
            need = 15*GiB + max(0, (canvas.get('video_tokens', 72576)-72576)*30000)
        need = self.peaks.get(('decode', key(dict(options=options, canvas=canvas))), need)
        self.make_room('video_vae', min(self.gpu_budget, need),
                       model=self.entries.get('video_vae', {}).get('model'), ram_need=2*GiB)

    def snapshot(self):
        if not torch.cuda.is_initialized():
            return dict(reclaimable_gpu_bytes=0, reclaimable_ram_bytes=0, gpu_uuid=None, models=[])
        uuid = str(getattr(torch.cuda.get_device_properties(0), 'uuid', ''))
        if uuid and not uuid.startswith(('GPU-', 'MIG-')):
            uuid = 'GPU-' + uuid
        host, committed = {}, {}
        for entry in self.entries.values():
            host.update(pinned_cpu_storages(entry['model']))
            committed.update(pinned_cpu_storages(entry['model'], committed=True))
        # Parked engine weights: private host copies, freed with the model.
        parked = sum(parked_bytes(entry['model']) for entry in self.entries.values())
        memory, gpu_capacity = None, None
        from .system import windows
        if windows():
            from .ram import ProcessMemory
            import os
            # Use the same Windows physical/mapped/commit accounting as the
            # active-request guard, not raw GlobalMemoryStatusEx availability.
            memory = ProcessMemory().sample(os.getpid())
            from .gpu_budget import owned_pool_capacity
            gpu_capacity = owned_pool_capacity(torch)
        can_release_host = host_cache_release_supported(torch)
        return dict(reclaimable_gpu_bytes=torch.cuda.memory_reserved(),
                    reclaimable_ram_bytes=(sum(host.values()) if can_release_host else 0) + parked,
                    reclaimable_commit_bytes=(sum(committed.values()) if can_release_host else 0) + parked,
                    retained_pinned_ram_bytes=sum(host.values()), parked_ram_bytes=parked,
                    host_cache_release_supported=can_release_host,
                    memory=memory, gpu_capacity=gpu_capacity,
                    ram_credit_scope='Owned pinned model storage and parked engine weights; deduplicated, reclaimable on eviction. No mapped-page or swap credit; commit only for private storage.',
                    gpu_uuid=uuid, models=[dict(role=role, gpu_bytes=gpu_bytes(entry['model']))
                                           for role, entry in self.entries.items()], decisions=self.decisions)
