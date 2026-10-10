"""Native MPS model lifecycle around the shared VDN model, sampler and assets.

The CUDA engine remains its own entry point. This explicit reference path uses
BF16 projections, native SDPA and one streamed layer; it does not use CUDA kernel
profiles or declare the installed application ready.
"""
import gc
from contextlib import contextmanager
import json
import math
from pathlib import Path
import os
import time


def upscale_latents(video, sampling, base, backend, checkpoint=None):
    """The same learned upscale/crop for resident and process-separated stages."""
    import torch
    from .latent_upscale import upscale
    from .two_pass import UPSCALER, crop_latents
    print(json.dumps(dict(event='latent_upscale', completed_steps=sampling['base_steps'],
                          total=sampling['total_steps'])), flush=True)
    checkpoint = checkpoint or Path(base).parent / 'latent_upscaler' / Path(UPSCALER['file']).name
    tick = time.monotonic()
    with torch.no_grad():
        backend.empty_cache()
        lifted, stats = upscale(video.to('mps'), checkpoint,
            sampling['upscale_target']['width'], sampling['upscale_target']['height'], memory_saving=True)
        lifted = crop_latents(lifted, sampling).cpu()
        backend.synchronize()
        backend.empty_cache()
    return lifted, dict(stats, stage_seconds=time.monotonic() - tick, crop=dict(sampling['crop']))


def load_conditioning(path, device, *, task, canvas, target=None):
    """Validate H3 inputs; resize keyframes and retain complete reference rows."""
    import torch
    from .conditioning import to_cache
    from .keyframes import first_pass_conditions, validate_conditioning
    from .media_conditioning import describe
    value = torch.load(path, map_location='cpu', weights_only=True)
    anchors = tuple(value.get('keyframe_anchors', ()))
    inferred = {(): 't2va', ('first',): 'i2va', ('last',): 'l2va', ('first', 'last'): 'fl2va'}.get(anchors)
    actual_task = value.get('task', inferred)
    reference = str(actual_task).startswith('ref2va')
    if (not reference and (actual_task != task or inferred != task)
            or not anchors and value.get('condition_latents')):
        raise ValueError('Saved conditioning does not match the native request task')
    prompt = value['prompt_embeds']
    if not isinstance(prompt, torch.Tensor) or prompt.ndim != 2:
        raise ValueError('Expected saved H3 layer-50 prompt embeddings [L,5120]')
    checked = to_cache([[prompt.unsqueeze(0), {'minimax_token_tags': value['text_token_tags']}]], task=actual_task)
    value.update(checked, task=actual_task)
    source = target or canvas
    info = describe(value, source['width'], source['height'], source['frames'])
    if info['task'] != task:
        raise ValueError('Saved conditioning does not match the native request task')
    if reference:
        references = [{name: (item.to(device=device, dtype=torch.float32)
                       if isinstance(item, torch.Tensor) else item) for name, item in row.items()}
                      for row in value['references']]
        return checked['prompt_embeds'].to(device), checked['text_token_tags'], references
    conditions = (anchors, value['condition_latents']) if anchors else None
    if conditions:
        if (source['width'], source['height']) != (canvas['width'], canvas['height']):
            # Latents are still on the host. Use the shared deterministic input
            # resize before uploading; the original full-size anchors stay saved.
            conditions = first_pass_conditions(conditions, source, canvas)
        validate_conditioning(checked['prompt_embeds'], checked['text_token_tags'], conditions,
                              task, canvas['width'], canvas['height'])
        conditions = (conditions[0], [latent.to(device, torch.float32) for latent in conditions[1]])
    return checked['prompt_embeds'].to(device), checked['text_token_tags'], conditions


def conditioning_tokens(task, canvas, conditions):
    """Packed reference rows from already validated conditioning, on CPU or MPS.

    Arbitrary references keep their original latent geometry in both passes;
    keyframes follow the current pass's resized canvas. Use the same accounting
    for lifetime admission and the actual per-pass compute plan.
    """
    if not conditions:
        return {}
    if task.startswith('ref2va'):
        return dict(reference_video_tokens=sum(math.prod(row['latent'].shape[2:]) // 4
                    for row in conditions if row['kind'] in ('image', 'video')),
                    reference_audio_tokens=sum(row['audio_latent'].shape[0]
                    for row in conditions if row.get('audio_latent') is not None))
    return dict(reference_video_tokens=len(conditions[0]) * (canvas['width'] // 32)
                * (canvas['height'] // 32))


class Engine:
    def __init__(self, cache, *, base, checkpoint, steps=8, budget_bytes=4 * 2**30,
                 reserve_bytes=2 * 2**30, query_chunk=128, ff_chunk=256, projection_chunk=256,
                 weight_decoder='cpu', budget_ceiling_bytes=None, attention_chunk=None, task='t2va', canvas=None,
                 head_chunk=4, window_batch=1, attention_impl='torch', fast_kernels=False,
                 resident_bytes=0, attention_batch_bytes=1 << 29, plan_compute=False, memory_retry=False):
        attention_chunk = projection_chunk if attention_chunk is None else attention_chunk
        if type(attention_chunk) is not int or attention_chunk < 1:
            raise ValueError('MPS attention row chunk must be a positive integer')
        for name, value in (('head_chunk', head_chunk), ('window_batch', window_batch)):
            if type(value) is not int or value < 1:
                raise ValueError('MPS ' + name + ' must be a positive integer')
        if attention_impl not in ('torch', 'mlx'):
            raise ValueError('Native window attention must use torch or mlx')
        if type(resident_bytes) is not int or resident_bytes < 0:
            raise ValueError('MPS retained layer bytes must be a nonnegative integer')
        if type(plan_compute) is not bool or type(memory_retry) is not bool:
            raise ValueError('MPS compute planning must be explicitly enabled or disabled')
        # A stage retried after running out of unified memory plans its smallest,
        # grouped-projection partitions whatever the fresh allowance suggests.
        self.plan_compute, self.memory_retry, self.compute = plan_compute, memory_retry, None
        self._ff_originals = []
        self._block_originals = []
        self.block_policy = None
        self.fused_modulation = bool(fast_kernels)
        self.modulation_policy = None
        self.resident_bytes = resident_bytes
        self._reuse_sampling_weights = False
        self._shared_residency = None
        self._shared_residency_final = None
        self.kernels = {}
        self.kernel_stats = {}
        from .media_request import TASKS
        if task not in TASKS:
            raise ValueError('Unsupported native conditioning task')
        if task != 't2va' and canvas is None:
            raise ValueError('Native media generation requires its full target canvas')
        self.task, self.canvas = task, dict(canvas) if canvas is not None else None
        from .macos_vdn import activate
        self.overlay = activate()
        import torch
        from .backends import get_backend
        from .backends.mps_weights import load_group, attach_lora
        from .backends.mps_linear import install as install_linear
        from .weights import skeleton
        from .adaln import CachedModulation, ScheduleCursor, TableCache, schedule_embeddings, schedule_timesteps
        from .adaln_assets import SLIM_FORMATS, asset_path, validate_catalog, restore_projections
        from .attention import WindowAttention
        from .packing import install_streamed_forward
        self.backend = get_backend('mps')
        if not self.backend.is_available():
            raise RuntimeError('Native MPS is unavailable')
        if type(steps) is not int or steps < 1:
            raise ValueError('Sampling steps must be positive')
        if budget_ceiling_bytes is not None and (type(budget_ceiling_bytes) is not int or budget_ceiling_bytes < 1):
            raise ValueError('MPS budget ceiling must be a positive integer')
        self.policy = self.backend.configure_budget(budget_bytes, reserve_bytes=reserve_bytes)
        self.budget_ceiling_bytes = budget_ceiling_bytes
        self.reserve_bytes = reserve_bytes
        self.allocator_limits = [self.policy['effective_allocator_limit_bytes']]
        self.steps, self.cache = steps, Path(cache).resolve()
        self.base = Path(base).resolve()
        self.model = None
        self.closed = False
        self.residency = None
        self.specs = []
        self.manifest = json.loads((self.cache / 'manifest.json').read_text())
        manifest = self.manifest
        precision = manifest.get('precision')
        int8_compute = False
        if precision == 'int8':
            from .backends import mps_int8
            # Apple M5 and newer run int8 products; earlier Macs read the same
            # ConvRot weights as BF16 (better than the FP8 export, no int8 speedup).
            int8_compute = mps_int8.available()
        self.linear_compute = 'int8' if int8_compute else {'fp8': 'bf16-weight-only', 'int8': 'bf16-weight-only'}.get(
            precision, 'bf16')
        self.backend.prepare_linears(None, manifest, self.linear_compute, 'auto')
        groups = {row['group']: row for row in manifest['groups']}
        if len(groups) != len(manifest['groups']):
            raise ValueError('Duplicate prepared model group')
        def path(name):
            row = groups[name]
            value = asset_path(self.cache, row['file'])
            if value.stat().st_size != row['bytes']:
                raise ValueError('Incomplete prepared model group: ' + name)
            return value
        self.linears = manifest.get('linears', {})
        self.weight_decoder_name = weight_decoder
        if weight_decoder == 'metal':
            from .backends.mps_fp8 import decode_weight
            self.weight_decoder = decode_weight
        elif weight_decoder == 'cpu':
            self.weight_decoder = None
        else:
            raise ValueError('Unknown MPS weight decoder')
        started = time.monotonic()
        try:
            with torch.no_grad():
                model = self.model = skeleton(self.base, Path(checkpoint))
                count = len(model.transformer_blocks)
                validate_catalog(manifest, count)
                if precision == 'int8':
                    rotation = manifest.get('rotation')
                    if rotation is not None and rotation != dict(rotation, kind='convrot', group=256):
                        raise ValueError('Unknown int8 weight rotation')
                    int8_linears = {name: entry for name, entry in manifest['linears'].items()
                                    if entry.get('storage') == 'int8'}
                    if any(entry.get('convrot_group') != (256 if rotation is not None else None)
                           for entry in int8_linears.values()):
                        raise ValueError('Int8 manifest rotation differs between its Linears')
                    int8_stats = {}
                    int8_info = (mps_int8.install(model, stats=int8_stats, storage='int8', int8_linears=int8_linears)
                                 if int8_compute else dict(implementation='mps-int8-dequantized-bf16'))
                root = path('root')
                root_lora = attach_lora(model, 'root', self.cache, manifest)
                load_group(model, root, self.linears,
                    exclude=('transformer_blocks.', 'token_refiner.refiner_blocks.', 'rope.'),
                    decoder=self.weight_decoder)
                model.rope.to('mps')
                times = schedule_timesteps(steps, device='mps', task=task)
                self.cursor = ScheduleCursor(times)
                tables = TableCache(self.cache, manifest['source_id'], steps, manifest=manifest,
                    timesteps=times, channels=model.config.hidden_size, device='cpu', task=task)
                embeddings = None
                delivery_checked = False
                for index, layer in enumerate(model.token_refiner.refiner_blocks):
                    files = [root, root_lora] if root_lora is not None else root
                    self.specs.append((layer, files, f'token_refiner.refiner_blocks.{index}.', ()))
                for index, layer in enumerate(model.transformer_blocks):
                    self._block_originals.append((layer, layer.forward))
                    table = tables.load(index, steps)
                    if (table is None and not delivery_checked and manifest.get('format') in SLIM_FORMATS
                            and f'adaln/{index:02d}' not in groups):
                        from .macos_reference import prepare_missing
                        delivery_checked = True
                        if prepare_missing(self.cache, tables.identity, count):
                            table = tables.load(index, steps)
                            if table is None:
                                raise RuntimeError('Downloaded reference constants are incomplete; files retained')
                    if table is None:
                        if manifest.get('format') in SLIM_FORMATS and f'adaln/{index:02d}' not in groups:
                            # Audio references add time zero; arbitrary step
                            # counts can also need constants absent from a slim
                            # package. Restore the original, pinned projections
                            # through the existing shared path only on a miss.
                            # The package manifest and published tables stay
                            # immutable; verified computed tables are reusable.
                            groups.update({row['group']: row for row in restore_projections(self.cache, manifest)})
                        load_group(layer.adaln_proj, path(f'adaln/{index:02d}'), self.linears,
                                   prefix=f'transformer_blocks.{index}.adaln_proj.', decoder=self.weight_decoder)
                        if embeddings is None:
                            _, embeddings = schedule_embeddings(model, steps, device='mps', task=task)
                        table = [layer.adaln_proj(value) for value in embeddings]
                        layer.adaln_proj = CachedModulation(table, self.cursor)
                        tables.save(index, layer.adaln_proj, producer_device='mps')
                    else:
                        layer.adaln_proj = CachedModulation(table, self.cursor).to('mps')
                    factors = attach_lora(layer, index, self.cache, manifest)
                    files = [path(f'blocks/{index:02d}'), factors] if factors is not None else path(f'blocks/{index:02d}')
                    self.specs.append((layer, files,
                        f'transformer_blocks.{index}.', ('adaln_proj.',)))
                    self._ff_originals.append((layer.ff, layer.ff.forward))
                    self.backend.install_chunked_ff(layer.ff, ff_chunk)
                self.schedule_hook = model.register_forward_pre_hook(self.cursor.before, with_kwargs=True)
                if attention_impl == 'mlx':
                    from .backends.mps_mlx_attention import MLXWindowAttention
                    self.attention = MLXWindowAttention(batch_bytes=attention_batch_bytes)
                else:
                    self.attention = WindowAttention('mps', query_chunk=query_chunk, window_batch=window_batch,
                                                     device_backend=self.backend)
                self.attention.install(model)
                # Keep FP32 input/output packing independent from BF16 attention
                # workspace tuning. Changing the output-head GEMM batch changed
                # rounding and amplified differences across sampling steps.
                install_streamed_forward(model, projection_chunk)
                self.linear_policy = install_linear(model, head_chunk=head_chunk, gate_chunk=attention_chunk)
                qk_prepare = None
                if fast_kernels:
                    from functools import partial
                    from .backends import mps_delta, mps_features, mps_qk
                    self.kernel_stats = dict(delta_rule={}, features={}, qk={})
                    self.kernels = dict(
                        delta_rule=mps_delta.install(model, stats=self.kernel_stats['delta_rule']),
                        features=mps_features.install(model, stats=self.kernel_stats['features']),
                        qk='mps-fused-qk-norm-rope-v1')
                    qk_prepare = partial(mps_qk.prepare_qk, stats=self.kernel_stats['qk'])
                self.qk_prepare = qk_prepare
                if precision == 'int8':
                    # Fast kernels above rebuild these reports; keep the int8 receipt.
                    self.kernel_stats['int8'] = int8_stats
                    self.kernels['int8_linears'] = int8_info['implementation']
                self._bind_attention(head_chunk=head_chunk, row_chunk=attention_chunk, ff_chunk=ff_chunk)
                self.kernels['attention'] = attention_impl
                model.eval().requires_grad_(False)
                self.backend.synchronize()
            self.load_seconds = time.monotonic() - started
        except BaseException:
            self.close()
            raise

    def _bind_attention(self, *, head_chunk, row_chunk, ff_chunk, bounded=False):
        """Bind one phase's attention and owned-buffer policy as a unit.

        Planning enables the bounded candidate only where the smallest original
        partition does not fit, or for a memory retry (macos_compute.plan). Validate
        the block contract before changing attention, and restore the captured
        upstream block methods when returning to the reference path.
        """
        if type(bounded) is not bool:
            raise ValueError('MPS bounded buffers must be explicitly enabled or disabled')
        if bounded:
            from .backends import mps_blocks, mps_grouped_qkv
            mps_blocks.validate(self.model, row_chunk=row_chunk, ff_chunk=ff_chunk)
            hybrid = mps_grouped_qkv.install(self.model, self.attention,
                head_chunk=head_chunk, row_chunk=row_chunk, qk_prepare=self.qk_prepare)
            blocks = mps_blocks.install(self.model, row_chunk=row_chunk, ff_chunk=ff_chunk)
        else:
            from .backends.mps_attention import install_hybrid
            hybrid = install_hybrid(self.model, self.attention,
                head_chunk=head_chunk, row_chunk=row_chunk, qk_prepare=self.qk_prepare)
            if (getattr(self, 'block_policy', None) is not None
                    or getattr(self, 'modulation_policy', None) is not None):
                for module, original in self._block_originals:
                    module.forward = original
            blocks = None
        self.modulation_policy = None
        if getattr(self, 'fused_modulation', False) and not bounded:
            from .backends import mps_modulation
            self.modulation_policy = mps_modulation.install(self.model)
            self.kernel_stats['modulation'] = self.modulation_policy
            self.kernels['modulation'] = mps_modulation.IMPLEMENTATION
        elif hasattr(self, 'kernel_stats'):
            self.kernel_stats.pop('modulation', None)
            self.kernels.pop('modulation', None)
        self.hybrid_policy, self.block_policy = hybrid, blocks

    def _block_metrics(self, previous=None):
        policy = getattr(self, 'block_policy', None)
        if policy is None:
            return None
        result = dict(policy)
        for name in ('block_calls', 'post_chunks', 'modulation_chunks', 'ff_chunks'):
            result[name] -= (previous or {}).get(name, 0)
        return result

    @contextmanager
    def _sampling_weights(self, specs, options):
        """Own the offloader for one sample, or for this generate() invocation."""
        if not getattr(self, '_reuse_sampling_weights', False):
            with self.backend.make_offloader(specs, linears=self.linears,
                    decoder=self.weight_decoder, **options) as residency:
                yield residency
            return
        residency = self._shared_residency
        if residency is None:
            residency = self.backend.make_offloader(specs, linears=self.linears,
                decoder=self.weight_decoder, **options)
            # __enter__ cleans a partial load itself. Publish only a successful
            # entry so close() never sees a half-constructed shared context.
            residency.__enter__()
            self._shared_residency = residency
        else:
            if list(specs) != residency.specs:
                raise RuntimeError('Shared sampling weights must keep the same model and checkpoints')
            residency.reconfigure(resident_bytes=getattr(self, 'resident_bytes', 0),
                                  working_reserve_bytes=self.reserve_bytes)
        try:
            yield residency
        except BaseException:
            self._release_shared_weights()
            raise

    def _release_shared_weights(self):
        residency = getattr(self, '_shared_residency', None)
        if residency is not None:
            self._shared_residency_final = residency.stats()
            # Clear only after cleanup succeeds. A failed fence/release must be
            # visible to Engine.close() rather than silently losing ownership.
            residency.close()
            self._shared_residency = None

    @staticmethod
    def _residency_metrics(residency, previous=None):
        result = residency.stats()
        if previous is None:
            return result
        counters = ('layer_loads', 'hydrated_weight_bytes', 'load_seconds',
                    'retained_layer_hits', 'retained_layer_evictions', 'deferred_cached_forwards')
        result = dict(result)
        for key in counters:
            if key in result:
                result[key] -= previous.get(key, 0)
        if 'prefetch' in result:
            result['prefetch'] = dict(result['prefetch'])
            for key in ('hits', 'misses', 'wait_seconds', 'read_seconds'):
                result['prefetch'][key] -= previous.get('prefetch', {}).get(key, 0)
        result.update(counter_scope='Current sampling phase', peak_scope='Shared sampling lifetime')
        return result

    def _plan_compute(self, canvas, prompt_rows):
        """Rebind this pass's partitions against the enforced cap and actual rows.

        This runs before the offloader is entered or any sampling forward. Keep
        the original FF methods so repeated passes never nest chunk wrappers.
        Explicit diagnostic/reference partitions opt out of planning.
        """
        if not getattr(self, 'plan_compute', False):
            return
        from .macos_compute import plan, sequence_tokens
        from .backends.mps_linear import install as install_linear
        rows = sequence_tokens(canvas, prompt_rows)
        computed = plan(self.policy['effective_allocator_limit_bytes'], rows['total'],
                        frames=canvas['latent_frames'], layers=len(self.model.transformer_blocks),
                        allow_bounded=True, force_bounded=getattr(self, 'memory_retry', False))
        computed.update(tokens=rows['total'], packed_rows=rows, frames=canvas['latent_frames'],
                        admission='enforced allocator cap, after conditioning; before sampling')
        for module, original in self._ff_originals:
            module.forward = original
            self.backend.install_chunked_ff(module, computed['ff_chunk'])
        self.linear_policy = install_linear(self.model, head_chunk=computed['head_chunk'],
                                            gate_chunk=computed['attention_chunk'])
        self._bind_attention(head_chunk=computed['head_chunk'], row_chunk=computed['attention_chunk'],
                             ff_chunk=computed['ff_chunk'], bounded=computed.get('bounded_buffers', False))
        self.attention.window_batch = computed['window_batch']
        if hasattr(self.attention, 'batch_bytes'):
            self.attention.batch_bytes = computed['attention_batch_bytes']
        self.resident_bytes = computed['resident_bytes']
        self.compute = computed
        if getattr(self, '_shared_residency', None) is not None:
            self._shared_residency.reconfigure(resident_bytes=self.resident_bytes,
                                               working_reserve_bytes=self.reserve_bytes)

    def _refine_prompt(self, prompt):
        """Release one-use prompt weights before the repeating sampling cycle.

        The refined tensor lives for this sample call. Keeping its refiner
        weights consumes the layer cache without another forward, and including
        them in the prefetch cycle reads them again after the last video block.
        """
        specs = [row for row in self.specs if row[2].startswith('token_refiner.refiner_blocks.')]
        try:
            with self.backend.make_offloader(specs, linears=self.linears,
                                             decoder=self.weight_decoder) as residency:
                self.residency = residency
                text = self.model.context_embedder(prompt.unsqueeze(0))
                self.model._freevideo_refined_text = self.model.token_refiner(text)
                return residency.stats()
        finally:
            self.residency = None

    def sample(self, *args, **kwargs):
        from .refine_schedule import modulation
        with modulation(self, kwargs.get('refine_schedule')) as prepared:
            result = self._sample(*args, **kwargs)
            if prepared is not None:
                result[2]['refinement_schedule_preparation'] = prepared
            return result

    def _sample(self, conditioning, seed, *, width, height, frames, progress_offset=0, progress_total=None,
               initial_latents=None, refine_steps=None, refine_schedule=None):
        import torch
        from .geometry import geometry, sampler_for_canvas
        from .sampling_progress import SamplingProgress
        from .system import system_memory
        from src.inference.render import generate_latents
        if self.closed:
            raise RuntimeError('MPS engine is closed')
        shared = getattr(self, '_shared_residency', None)
        previous_residency = shared.stats() if shared is not None else None
        if self.budget_ceiling_bytes is not None:
            # Recheck at a phase boundary, before loading the next set of layers.
            # Per-layer allocator changes react to transient release accounting
            # and can shrink the limit below the next layer's working set.
            self.policy = self.backend.configure_budget(self.budget_ceiling_bytes,
                                                        reserve_bytes=self.reserve_bytes)
            self.allocator_limits.append(self.policy['effective_allocator_limit_bytes'])
        if shared is not None:
            # Return retained weights under newly observed pressure before even
            # uploading conditioning or loading the one-use prompt refiner.
            shared.reconfigure(resident_bytes=self.resident_bytes,
                               working_reserve_bytes=self.reserve_bytes)
        refining = initial_latents is not None
        if refining != (refine_steps is not None):
            raise ValueError('Refinement requires both initial latents and tail steps')
        if refining:
            from .refine import generate_latents, validate_tail
            if refine_schedule is None:
                validate_tail(self.steps, refine_steps)
        elif self.task.startswith('ref2va'):
            from .reference_sampler import generate_latents
        active_steps = refine_steps if refining else self.steps
        canvas = geometry(width, height, frames=frames)
        sampler = sampler_for_canvas(generate_latents, width, height)
        progress = SamplingProgress(active_steps, len(self.model.transformer_blocks),
                                    offset=progress_offset, total=progress_total)
        class StepTimes(list):
            def append(owner, duration):
                super().append(duration)
                progress.complete(duration)
        durations = StepTimes()
        previous_calls = dict(self.attention.backend_calls)
        previous_kernel_calls = {name: dict(counts) for name, counts in
                                 getattr(self, 'kernel_stats', {}).items()}
        def host_guard(module, inputs):
            if system_memory()['available_bytes'] < 2**30:
                raise MemoryError('MPS generation reached the 1 GiB physical RAM emergency floor')
        guards = [module.register_forward_pre_hook(host_guard, prepend=True) for module, _, _, _ in self.specs]
        try:
            with torch.no_grad():
                prompt, tags, conditions = load_conditioning(conditioning, 'mps', task=self.task,
                    canvas=canvas, target=self.canvas)
                canvas.update(conditioning_tokens(self.task, canvas, conditions))
                self._plan_compute(canvas, prompt.shape[0])
                previous_block_calls = self._block_metrics()
                self.cursor.reset(self.steps - refine_steps if refining and refine_schedule is None else 0)
                progress.start()
                resident = getattr(self, 'resident_bytes', 0)
                retention = (dict(resident_bytes=resident, working_reserve_bytes=self.reserve_bytes)
                             if resident else {})
                if getattr(self, 'kernels', {}).get('delta_rule'):
                    from .macos_compute import PREFETCH_BYTES
                    # Fast kernels also stream without a per-layer sync or cache
                    # release, reading the next streamed layer during this one.
                    retention.update(synchronize_layers=False, release_cache_every_layer=False,
                                     prefetch=True, prefetch_bytes=PREFETCH_BYTES)
                text_residency = self._refine_prompt(prompt)
                sampling_specs = [row for row in self.specs
                                  if not row[2].startswith('token_refiner.refiner_blocks.')]
                with self._sampling_weights(sampling_specs, retention) as residency, \
                        progress.layers(self.model.transformer_blocks):
                    self.residency = residency
                    video, audio = sampler(self.model, prompt, tags, canvas['frames'], self.steps,
                                          seed, 'mps', step_seconds=durations,
                                          **(dict(conditions=conditions) if conditions else {}),
                                          **(dict(initial_latents=initial_latents, refine_steps=refine_steps, refine_schedule=refine_schedule)
                                             if refining else {}))
                    self.backend.synchronize()
                    if not bool(video.isfinite().all()) or not bool(audio.isfinite().all()):
                        raise ValueError('MPS sampling produced nonfinite video or audio latents')
                    return video.cpu(), audio.cpu(), dict(device_backend='mps', canvas=canvas,
                        steps=active_steps, seed=seed, task=self.task, linear_compute=self.linear_compute,
                        refinement=(dict(base_steps=self.steps, steps=refine_steps,
                            start_index=1 if refine_schedule else self.steps - refine_steps, restart_seed=seed,
                            schedule=refine_schedule or 'original-tail') if refining else None),
                        weight_decoder=self.weight_decoder_name,
                        linear_attention=self.linear_policy,
                        hybrid_attention=self.hybrid_policy,
                        block_buffers=self._block_metrics(previous_block_calls),
                        kernels=dict(getattr(self, 'kernels', {})),
                        compute=getattr(self, 'compute', None),
                        kernel_calls={name: {key: count - previous_kernel_calls.get(name, {}).get(key, 0)
                                             for key, count in counts.items()}
                                      for name, counts in getattr(self, 'kernel_stats', {}).items()},
                        attention_stats=dict(getattr(self.attention, 'stats', {})),
                        allocator_limits=dict(min_bytes=min(self.allocator_limits),
                                              max_bytes=max(self.allocator_limits)),
                        attention_calls={name: value - previous_calls[name]
                                         for name, value in self.attention.backend_calls.items()},
                        step_seconds=list(durations),
                        load_seconds=self.load_seconds, residency=self._residency_metrics(residency, previous_residency),
                        text_refiner_residency=text_residency,
                        residency_scope='Repeated video blocks; one-use prompt weights reported separately',
                        memory=self.backend.memory_stats())
        finally:
            for handle in guards:
                handle.remove()
            if self.model is not None:
                self.model.__dict__.pop('_freevideo_refined_text', None)
            self.residency = None
            self.backend.empty_cache()

    def generate(self, conditioning, seed, *, width, height, frames, two_pass=True, refine_steps=3,
                 upscaler_checkpoint=None, first_pass_saved=None, reuse_weights=False,
                 refinement_resident_bytes=None, phase_changed=None):
        """Optional cross-pass weight lifetime, isolated to this one request.

        Product admission remains separate. Retention obeys each sample's live
        allocator decision and the existing per-layer pressure checks. The model
        is released on completion, failure or cancellation, including upscaling.
        """
        if type(reuse_weights) is not bool:
            raise ValueError('Cross-pass weight reuse must be explicitly enabled or disabled')
        if refinement_resident_bytes is not None and (not reuse_weights or
                type(refinement_resident_bytes) is not int or refinement_resident_bytes < 0):
            raise ValueError('Refinement weight allowance requires shared sampling and nonnegative bytes')
        if phase_changed is not None and not callable(phase_changed):
            raise ValueError('Sampling phase callback must be callable')
        options = dict(width=width, height=height, frames=frames, two_pass=two_pass,
                       refine_steps=refine_steps, upscaler_checkpoint=upscaler_checkpoint,
                       first_pass_saved=first_pass_saved, phase_changed=phase_changed,
                       refinement_resident_bytes=refinement_resident_bytes)
        if getattr(self, '_reuse_sampling_weights', False) or getattr(self, '_shared_residency', None) is not None:
            raise RuntimeError('Native sampling weight lifetimes cannot overlap')
        if not reuse_weights:
            return self._generate(conditioning, seed, **options)
        self._reuse_sampling_weights = True
        self._shared_residency = None
        self._shared_residency_final = None
        try:
            video, audio, metrics = self._generate(conditioning, seed, **options)
        finally:
            self._reuse_sampling_weights = False
            self._release_shared_weights()
        metrics['shared_sampling_residency'] = self._shared_residency_final
        return video, audio, metrics

    def _generate(self, conditioning, seed, *, width, height, frames, two_pass=True, refine_steps=3,
                  upscaler_checkpoint=None, first_pass_saved=None,
                  refinement_resident_bytes=None, phase_changed=None):
        """Use the shared canvas/schedule, including the original audio clock.

        Single-pass behavior remains explicit; memory pressure never disables
        refinement or reduces geometry. First-pass latents can be retained by
        the worker before the upscaler runs, including on a later failure.
        """
        import torch
        from .geometry import geometry
        from .two_pass import plan
        canvas = geometry(width, height, frames=frames)
        if self.canvas is not None and any(canvas[k] != self.canvas[k] for k in ('width', 'height', 'frames')):
            raise ValueError('Native generation differs from its loaded target canvas')
        sampling = plan(canvas, enabled=two_pass, task=self.task,
                        base_steps=self.steps, refine_steps=refine_steps)
        if phase_changed is not None:
            phase_changed('first-pass')
        video, audio, first = self.sample(conditioning, seed, **sampling['first'],
                                         progress_total=sampling['total_steps'])
        metrics = dict(sampling_plan=sampling, sampling_passes=[first])
        if not sampling['enabled']:
            return video, audio, metrics
        if first_pass_saved is not None:
            first_pass_saved(video, audio, metrics)
        if phase_changed is not None:
            phase_changed('upscale')
        shared = getattr(self, '_shared_residency', None)
        if shared is not None and refinement_resident_bytes is not None:
            # The larger second-pass workspace may retain fewer layers. Return
            # those weights before loading the upscaler, keeping eligible ones.
            shared.reconfigure(resident_bytes=min(shared.resident_limit, refinement_resident_bytes),
                               working_reserve_bytes=self.reserve_bytes)
        lifted, metrics['latent_upscale'] = upscale_latents(video, sampling, self.base,
                                                           self.backend, upscaler_checkpoint)
        if phase_changed is not None:
            phase_changed('refinement')
        result, sound, second = self.sample(conditioning, seed + sampling['restart_seed_offset'],
            **sampling['second'], initial_latents=(lifted, audio), refine_steps=refine_steps,
            refine_schedule=sampling.get('refine_schedule'),
            progress_offset=self.steps, progress_total=sampling['total_steps'])
        if not torch.equal(sound, audio):
            raise ValueError('MPS refinement changed first-pass audio')
        metrics['sampling_passes'].append(second)
        return result, sound, metrics

    def close(self):
        shared = getattr(self, '_shared_residency', None)
        self._release_shared_weights()
        if self.residency is not None and self.residency is not shared:
            self.residency.close()
        self.residency = None
        if getattr(self, 'schedule_hook', None) is not None:
            self.schedule_hook.remove()
            self.schedule_hook = None
        self.specs.clear()
        self._ff_originals.clear()
        self._block_originals.clear()
        self.block_policy = None
        self.model = None
        self.attention = None
        self.closed = True
        gc.collect()
        self.backend.empty_cache()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
