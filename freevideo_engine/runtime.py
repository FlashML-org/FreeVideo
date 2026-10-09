"""VDN T2VA/FL2VA engine with file-backed weights and bounded layer offloading."""
import gc
import hashlib
import json
import os
import sys
from pathlib import Path
import time
import types

import torch

from .adaln import CachedModulation, TableCache, ScheduleCursor, schedule_embeddings, schedule_timesteps
from .attention import WindowAttention
from .backends import get_backend
from .weights import skeleton
from .paths import base_path, checkpoint_path
from .torch_compat import empty_host_cache


def _load_safetensors(path):
    """Materialize a shard without retaining a file mapping.

    ``safetensors.torch.load_file`` uses a memory map by default.  That is
    useful for Linux page-cache reuse, but on Windows the section view also
    reserves system commit for the whole shard.  During model preparation we
    only need the tensors, so read them through the platform-aware streamed
    opener and close the handle before returning the dictionary.  Linux keeps
    its mmap path. Windows reads each shard on a few threads without a
    mapping (``tensor_io.ParallelReads``), twice as fast as safetensors'
    ``pread`` backend on the RTX 5060 Ti machine.
    """
    from .system import windows
    if windows():
        from .tensor_io import ParallelReads
        reads = ParallelReads(path)
        try:
            if not reads.skipped:
                return reads.tensors(sorted(reads.entries, key=lambda name: reads.entries[name][2]))
        finally:
            reads.close()
    from .streamed_weights import _open_safetensors
    with _open_safetensors(path, framework='pt', device='cpu') as handle:
        return {name: handle.get_tensor(name) for name in handle.keys()}


class Engine:
    @torch.no_grad()
    def __init__(self, cache, *, base=None, checkpoint=None,
                 attention='dense', prefetch=True, adaln_cache=False,
                 inference_kernels=False, query_chunk=0, ff_chunk=0, head_chunk=0,
                 offload_refiner=False, attention_cpu_outputs=False, preload_host=False,
                 pin_host_weights=False, adaln_disk_cache=False, projection_chunk=0,
                 grouped_attention_outputs=False, fp8_ff_recompute=False,
                 resident_blocks=0, pin_host_gb=0., cache_refined_text=False, steps=8,
                 window_batch=1, linear_compute='native-fp8', input_cache_dir=None,
                 fp8_gemm='auto', window_varlen=False, varlen_smooth_k=True,
                 task='t2va', stream_weights=False, canvas=None, head_parallelism=1,
                 residual_offload=False, device_backend=None, gpu_allocation_bytes=None):
        self.device_backend = device_backend if device_backend is not None else get_backend()
        if gpu_allocation_bytes is not None and (type(gpu_allocation_bytes) is not int or gpu_allocation_bytes < 0):
            raise ValueError('GPU allocation limit must be a nonnegative byte count')
        self.gpu_allocation_bytes = gpu_allocation_bytes
        if self.device_backend.capabilities.name != 'cuda':
            raise NotImplementedError('Engine model execution currently requires the CUDA backend')
        from .media_request import TASKS
        if task not in TASKS:
            raise ValueError('Unsupported Engine task: ' + str(task))
        self.task = task
        if type(residual_offload) is not bool:
            raise ValueError('Residual offload must be boolean')
        if residual_offload and (not projection_chunk or not inference_kernels):
            raise ValueError('Residual offload requires bounded packing and fused inference blocks')
        self.canvas = dict(canvas) if canvas is not None else None
        from .system import weight_cache_headroom, residual_host_headroom
        self.weight_cache_headroom_bytes = weight_cache_headroom(
            streamed=stream_weights, cpu_outputs=attention_cpu_outputs, canvas=self.canvas)
        if residual_offload:
            # The new pageable residual must not compete with retained weights.
            # A reference packed residual is ~0.75 GiB; reserve 1 GiB and scale
            # with video/reference rows. The live RAM guard remains in charge.
            self.weight_cache_headroom_bytes += residual_host_headroom(self.canvas)
        base = base_path() if base is None else Path(base)
        checkpoint = checkpoint_path() if checkpoint is None else Path(checkpoint)
        if linear_compute not in ('native-fp8', 'bf16-weight-only', 'int8'):
            raise ValueError('Unknown linear compute policy')
        if min(query_chunk, ff_chunk, head_chunk, projection_chunk) < 0:
            raise ValueError('Chunk sizes cannot be negative; zero disables chunking')
        if window_batch < 1:
            raise ValueError('Window batch must be positive')
        if type(head_parallelism) is not int or head_parallelism not in (1, 2, 4):
            raise ValueError('Head parallelism must be 1, 2 or 4')
        if head_parallelism > 1 and (resident_blocks != 50 or attention_cpu_outputs or grouped_attention_outputs or not head_chunk):
            raise ValueError('Parallel head groups require all 50 blocks resident and GPU head outputs')
        if attention_cpu_outputs and not head_chunk:
            raise ValueError('CPU attention outputs require head chunking')
        if grouped_attention_outputs and not attention_cpu_outputs:
            raise ValueError('Grouped readouts require CPU attention outputs')
        if pin_host_weights and preload_host:
            raise ValueError('Pinned weights already include host preloading; select one storage mode')
        if pin_host_gb < 0 or resident_blocks < 0:
            raise ValueError('Residency and host cache budgets cannot be negative')
        if pin_host_gb and (pin_host_weights or preload_host):
            raise ValueError('Bounded pinning is a separate storage mode')
        if stream_weights and (pin_host_weights or preload_host):
            raise ValueError('Streamed weights allow a bounded pinned subset, but not host preload or whole-model pinning')
        if cache_refined_text and (not offload_refiner or not projection_chunk):
            raise ValueError('Text refinement caching requires refiner offloading and streamed packing')
        self.base = Path(base)
        self.steps = steps
        self.prefetch = prefetch
        self.cache = Path(cache)
        self.input_cache_dir = input_cache_dir
        self.closed = False
        self.offloader = None
        self.stream_weight_source = None
        self.stream_refiner_count = 0
        self.cache_refined_text = cache_refined_text
        self.refiner_released = False
        started = time.perf_counter()
        manifest = json.loads((self.cache / 'manifest.json').read_text(encoding='utf-8'))
        online_lora = manifest.get('online_lora', {})
        self.online_lora_root = online_lora.get('blocks', {}).get('root', {}).get('file')
        if online_lora:
            if not head_chunk or not inference_kernels:
                raise ValueError('Online LoRA requires bounded attention; select the automatic generation profile')
            ff_chunk = ff_chunk or 2048
        lora_paths = [self.cache / spec['file'] for spec in online_lora.get('blocks', {}).values()]
        from .adaln_assets import SLIM_FORMATS, validate_catalog, restore_projections
        portable_adaln = manifest.get('format') in SLIM_FORMATS
        if portable_adaln:
            # In the slim format these constants are model parameters, not a
            # disposable optimization cache. The effective mode is reported.
            adaln_cache = True
        for group in manifest['groups']:
            if (self.cache / group['file']).stat().st_size != group['bytes']:
                raise ValueError('Incomplete prepared weights')
        self.attention = WindowAttention(attention, query_chunk, window_batch,
                                         window_varlen=window_varlen,
                                         varlen_smooth_k=varlen_smooth_k, device_backend=self.device_backend)
        model = skeleton(self.base, Path(checkpoint))
        validate_catalog(manifest, len(model.transformer_blocks))
        # Locked buffers allocated after the weights must also fit the Windows
        # non-local budget: CPU attention readouts grow with this request's rows.
        from .windows_gpu_memory import NONLOCAL_PIN_RESERVE, nonlocal_readout_bytes
        readouts = (nonlocal_readout_bytes(self.canvas, model.config.num_attention_heads * model.config.attention_head_dim)
                    if attention_cpu_outputs else 0)
        self.nonlocal_reserve_bytes = NONLOCAL_PIN_RESERVE + readouts
        # RAM views fill during the first pass; leave what the refine pass will
        # still allocate. A 20 s request that let views take that room drove
        # physical availability to 0.01 GiB when its full-size readouts arrived.
        from .streamed_weights import HOST_VIEW_RESERVE
        self.host_view_reserve_bytes = HOST_VIEW_RESERVE + readouts + (
            residual_host_headroom(self.canvas) if residual_offload else 0)
        if resident_blocks > len(model.transformer_blocks):
            raise ValueError('More resident blocks requested than the model contains')
        # Build the streamed source before binding transformer blocks.  The
        # previous order loaded every non-resident block into CPU tensors and
        # only converted them to zero-storage placeholders when the first
        # LayerOffloader was created at sampling time.  On Windows that made
        # model preparation retain the whole checkpoint's CPU working set (and
        # its pagefile commit) even though stream_weights was selected. On
        # Windows, select the bounded pinned subset one block at a time; a
        # load-all pass would consume the free RAM used to admit that cache.
        # Discard unpinned blocks immediately and restore them on demand.
        streamed_source = None
        from .system import windows
        incremental_pinning = stream_weights and bool(pin_host_gb) and windows()
        prepared_pins = dict(logical=0, reserved=0, seconds=0., views=0, unread=0, unread_bytes=0)
        streamed_refiner_count = (len(model.token_refiner.refiner_blocks)
                                  if stream_weights and offload_refiner and not cache_refined_text else 0)
        if stream_weights and not adaln_cache:
            # SafetensorLayers indexes every streamed shard up front.  Slim
            # caches may restore their AdaLN projections lazily, so ensure
            # those files exist before constructing that index.
            if any(not (self.cache / f'adaln/{index:02d}.safetensors').is_file()
                   for index in range(resident_blocks, len(model.transformer_blocks))):
                restore_projections(self.cache, manifest)
        if stream_weights and (not pin_host_gb or incremental_pinning):
            from .streamed_weights import SafetensorLayers
            streamed_prefixes = ([f'token_refiner.refiner_blocks.{index}.'
                                  for index in range(streamed_refiner_count)]
                                 + [f'transformer_blocks.{index}.'
                                    for index in range(resident_blocks, len(model.transformer_blocks))])
            streamed_paths = ([self.cache / 'root.safetensors'] if streamed_refiner_count else [])
            streamed_paths += [self.cache / f'blocks/{index:02d}.safetensors'
                               for index in range(resident_blocks, len(model.transformer_blocks))]
            if not adaln_cache:
                streamed_paths += [self.cache / f'adaln/{index:02d}.safetensors'
                                   for index in range(resident_blocks, len(model.transformer_blocks))]
            if streamed_prefixes:
                streamed_source = SafetensorLayers(streamed_paths + lora_paths, streamed_prefixes, host_views=True,
                                                   host_view_reserve=self.host_view_reserve_bytes)
                self.stream_weight_source = streamed_source
                # Only source-backed placeholders may remain empty between
                # refinement requests; pinned groups keep their own storage.
                self.stream_refiner_count = streamed_refiner_count
        def unread_weights(index):
            """Layout-only tensors for a streamed block that loading cannot keep.

            A block is read here only for a pinned group or a view. Otherwise
            its values are dropped, and where RAM is that short the file cache
            cannot hold them until the first step either.
            """
            if streamed_source is None or not streamed_source.direct_read or index < resident_blocks:
                return None
            from .offload import loading_retains
            layer = streamed_refiner_count + index - resident_blocks
            budget = max(0, int(pin_host_gb * 1e9) - prepared_pins['reserved']) if incremental_pinning else 0
            if loading_retains(streamed_source, layer, pin_budget_bytes=budget,
                               headroom_bytes=self.weight_cache_headroom_bytes,
                               nonlocal_reserve_bytes=self.nonlocal_reserve_bytes,
                               commit_reserve_bytes=self.gpu_commit_reserve()):
                return None
            tensors = streamed_source.placeholders(layer, self.cache / f'blocks/{index:02d}.safetensors')
            if tensors is not None:
                prepared_pins['unread'] += 1
                prepared_pins['unread_bytes'] += sum(streamed_source.layer_bytes(layer).values())
            return tensors
        def prepare_streamed(block, index, unread=False):
            logical = 0
            if unread:
                # Placeholder values: never pin or view them. The first direct
                # read in sampling adopts a view if RAM allows by then.
                self.device_backend.unload_streamed_layer(block, streamed_source, index)
                return
            if incremental_pinning:
                tick = time.perf_counter()
                logical, reserved = self.device_backend.prepare_streamed_layer(block, streamed_source, index,
                    pin_budget_bytes=max(0, int(pin_host_gb * 1e9) - prepared_pins['reserved']),
                    headroom_bytes=self.weight_cache_headroom_bytes,
                    nonlocal_reserve_bytes=self.nonlocal_reserve_bytes,
                    commit_reserve_bytes=self.gpu_commit_reserve())
                prepared_pins['logical'] += logical
                prepared_pins['reserved'] += reserved
                prepared_pins['seconds'] += time.perf_counter() - tick
            else:
                self.device_backend.unload_streamed_layer(block, streamed_source, index)
            if not logical:
                # Its bytes are still in the file cache; later pins would evict them.
                prepared_pins['views'] += int(streamed_source.adopt(index))
        precision = manifest.get('precision', 'bf16')
        actual_fp8_gemm = self.device_backend.prepare_linears(model, manifest, linear_compute, fp8_gemm)
        root_weights = _load_safetensors(self.cache / 'root.safetensors')
        root_weights = {name: value if offload_refiner and name.startswith('token_refiner.refiner_blocks.') else value.to(self.device_backend.device)
                        for name, value in root_weights.items()}
        result = model.load_state_dict(root_weights, strict=False, assign=True)
        if result.unexpected_keys or any(not key.startswith('transformer_blocks.') for key in result.missing_keys):
            raise ValueError('Unexpected root weight keys')
        del root_weights
        if online_lora:
            from .lora_online import attach_block
            attach_block(model, 'root', self.cache, manifest, _load_safetensors,
                         device='cpu' if offload_refiner else self.device_backend.device)
        if streamed_source is not None and streamed_refiner_count:
            # Refiner weights live in root.safetensors and otherwise remain a
            # second large CPU allocation until LayerOffloader is constructed.
            # Apply the same metadata-only representation used for transformer
            # blocks immediately after binding the root state.
            for index, block in enumerate(model.token_refiner.refiner_blocks):
                prepare_streamed(block, index)
        model.rope.to(self.device_backend.device)
        self.cursor = None
        self.schedule_hook = None
        embeddings = None
        if adaln_cache:
            self.cursor = ScheduleCursor(schedule_timesteps(steps, task=task))
        table_cache = (TableCache(self.cache, manifest['source_id'], steps, task=task, manifest=manifest,
                                 timesteps=self.cursor.timesteps, channels=model.config.hidden_size, device='cpu')
                       if adaln_cache and (adaln_disk_cache or portable_adaln) else None)
        table_cache_hits = 0
        adaln_prepare_seconds = 0.
        self.resident_weight_bytes = 0
        block_bind_seconds = 0.
        upload_submit_seconds = 0.
        for index, block in enumerate(model.transformer_blocks):
            prefix = f'transformer_blocks.{index}.'
            adaln_started = time.perf_counter()
            table = table_cache.load(index, steps) if table_cache is not None else None
            cache_hit = table is not None
            if cache_hit:
                block.adaln_proj = CachedModulation(table, self.cursor).to(self.device_backend.device, non_blocking=True)
                table_cache_hits += 1
                del table
            else:
                if not (self.cache / f'adaln/{index:02d}.safetensors').is_file():
                    restore_projections(self.cache, manifest)
                adaln = {name.removeprefix(prefix): value for name, value in
                         _load_safetensors(self.cache / f'adaln/{index:02d}.safetensors').items()}
            if adaln_cache and not cache_hit:
                if embeddings is None:
                    _, embeddings = schedule_embeddings(model, steps, task=task)
                block.load_state_dict({name: value.to(self.device_backend.device) for name, value in adaln.items()}, strict=False, assign=True)
                table = [block.adaln_proj(embedding) for embedding in embeddings]
                block.adaln_proj = CachedModulation(table, self.cursor)
                if table_cache is not None:
                    table_cache.save(index, block.adaln_proj)
                del table, adaln
            adaln_prepare_seconds += time.perf_counter() - adaln_started
            bind_started = time.perf_counter()
            weights = unread_weights(index)
            unread = weights is not None
            if not unread:
                weights = _load_safetensors(self.cache / f'blocks/{index:02d}.safetensors')
            weights = {name.removeprefix(prefix): value for name, value in weights.items()}
            if not adaln_cache:
                weights.update(adaln)
            result = block.load_state_dict(weights, strict=False, assign=True)
            if result.unexpected_keys or any(not key.startswith('adaln_proj.step_') for key in result.missing_keys):
                raise ValueError(f'Incomplete block {index}: {result}')
            del weights
            if online_lora:
                from .lora_online import attach_block
                attach_block(block, index, self.cache, manifest, _load_safetensors)
            if not adaln_cache:
                # ``weights.update(adaln)`` leaves the source dictionary alive
                # until the next iteration. Drop it before replacing this
                # block's storage with a streamed placeholder.
                del adaln
            block_bind_seconds += time.perf_counter() - bind_started
            if index < resident_blocks:
                # Bind one immutable block, enqueue its copies, then prepare
                # the next. Do not retain all 50 CPU mappings before starting
                # the upload. Default-stream ordering protects sampling and
                # the table copies without another GPU weight buffer.
                from .offload import cpu_weights
                self.resident_weight_bytes += sum(value.numel() * value.element_size() for _, value in cpu_weights(block))
                upload_started = time.perf_counter()
                block.to(self.device_backend.device, non_blocking=True)
                upload_submit_seconds += time.perf_counter() - upload_started
            elif streamed_source is not None:
                # Keep only the bounded pinned subset or metadata. Immutable
                # unpinned bytes are reopened by SafetensorLayers on demand,
                # rather than accumulating until the first sampling step.
                prepare_streamed(block, streamed_refiner_count + index - resident_blocks, unread)
            if (index + 1) % 5 == 0:
                print(json.dumps({'event': 'prepared_blocks', 'blocks': index + 1,
                                  'adaln_cache': adaln_cache, 'adaln_reused_blocks': table_cache_hits,
                                  'resident_uploaded_blocks': min(index + 1, resident_blocks),
                                  'phase': 'Load cached blocks and upload resident weights',
                                  'seconds': time.perf_counter() - started}), flush=True)
        if adaln_cache:
            del embeddings
            self.schedule_hook = model.register_forward_pre_hook(self.cursor.before, with_kwargs=True)
        if any(p.is_meta for p in list(model.parameters()) + list(model.buffers())):
            raise RuntimeError('Some transformer parameters are still on meta')
        model.eval().requires_grad_(False)
        from src.models.hybrid_transform import set_inference_mode, set_softmax_backend
        # Engine supplies every window call, including the official cuDNN/FA4
        # pair. Avoid upstream auto importing an unselected optional FA4 package.
        set_softmax_backend(model, 'flex')
        if inference_kernels:
            from .row_kernels import install as install_row_kernels
            install_row_kernels()
        set_inference_mode(model, inference_kernels)
        self.attention.install(model)
        if head_chunk:
            if self.attention is None or not inference_kernels:
                raise ValueError('Head chunks require a window policy and inference kernels')
            from .head_chunk import install_head_chunks
            install_head_chunks(model, self.attention, head_chunk, cpu_outputs=attention_cpu_outputs,
                                projection_chunk=projection_chunk or 1024, grouped_outputs=grouped_attention_outputs,
                                parallelism=head_parallelism)
        if ff_chunk:
            for block in model.transformer_blocks:
                if precision == 'fp8' and linear_compute == 'native-fp8':
                    if not inference_kernels:
                        raise ValueError('FP8 FF chunks require the official inference kernels')
                    self.device_backend.install_chunked_ff(block.ff, ff_chunk, recompute=fp8_ff_recompute)
                    continue
                from .blocks import chunked_ff
                original = block.ff.forward
                block.ff._freevideo_unchunked_forward = original
                block.ff.forward = types.MethodType(chunked_ff(original, ff_chunk), block.ff)
        if projection_chunk:
            from .packing import install_streamed_forward
            install_streamed_forward(model, projection_chunk, residual_offload=residual_offload)
            if inference_kernels:
                from .blocks import install_bounded_blocks
                install_bounded_blocks(model, residual_offload=residual_offload)
        self.transformer = model
        print(json.dumps({'event': 'model_load_phase', 'phase': 'Preparing offloaded weight storage'}), flush=True)
        self.offload_layers = (list(model.token_refiner.refiner_blocks) if offload_refiner and not cache_refined_text else []) + list(model.transformer_blocks)[resident_blocks:]
        if stream_weights and self.stream_weight_source is None:
            from .streamed_weights import SafetensorLayers
            prefixes = ([f'token_refiner.refiner_blocks.{index}.' for index in range(len(model.token_refiner.refiner_blocks))]
                        if offload_refiner and not cache_refined_text else [])
            paths = [self.cache / 'root.safetensors'] if prefixes else []
            prefixes += [f'transformer_blocks.{index}.' for index in range(resident_blocks, len(model.transformer_blocks))]
            paths += [self.cache / f'blocks/{index:02d}.safetensors' for index in range(resident_blocks, len(model.transformer_blocks))]
            if not adaln_cache:
                paths += [self.cache / f'adaln/{index:02d}.safetensors' for index in range(resident_blocks, len(model.transformer_blocks))]
            self.stream_weight_source = SafetensorLayers(paths + lora_paths, prefixes, host_views=True,
                                                         host_view_reserve=self.host_view_reserve_bytes)
        self.host_preload_seconds = 0.
        self.pinned_model_bytes = prepared_pins['logical']
        self.pinned_host_allocated_bytes = 0
        pin_seconds = prepared_pins['seconds']
        pin_order = 'sequential'
        if pin_host_weights or pin_host_gb:
            host_headroom = self.weight_cache_headroom_bytes
            if not incremental_pinning:
                pin_start = time.perf_counter()
                pin_layers = self.offload_layers
                if stream_weights and attention_cpu_outputs and cache_refined_text and not windows():
                    from .streamed_weights import interleaved_layer_order
                    pin_layers = [self.offload_layers[index] for index in interleaved_layer_order(len(self.offload_layers))]
                    pin_order = 'interleaved'
                self.pinned_model_bytes = self.device_backend.pin_layer_weights(pin_layers, max_bytes=int(pin_host_gb * 1e9) if pin_host_gb else None,
                                                           headroom_bytes=host_headroom,
                                                           nonlocal_reserve_bytes=self.nonlocal_reserve_bytes,
                                                           commit_reserve_bytes=self.gpu_commit_reserve())
                pin_seconds = time.perf_counter() - pin_start
            self.pinned_host_allocated_bytes = self.device_backend.host_memory_stats()['allocated_bytes.current']
            print(json.dumps({'event': 'host_weights_pinned', 'seconds': pin_seconds,
                              'logical_bytes': self.pinned_model_bytes,
                              'working_headroom_bytes': host_headroom,
                              'nonlocal_reserve_bytes': self.nonlocal_reserve_bytes,
                              'gpu_commit_reserve_bytes': self.gpu_commit_reserve(),
                              'pin_order': pin_order,
                              'host_allocator_bytes': self.pinned_host_allocated_bytes}), flush=True)
        if preload_host:
            preload_start = time.perf_counter()
            buffer = bytearray(8 * 1024 * 1024)
            for group in manifest['groups']:
                if group['group'].startswith('adaln/') and adaln_cache:
                    continue
                with (self.cache / group['file']).open('rb', buffering=0) as stream:
                    while stream.readinto(buffer):
                        pass
            del buffer
            self.host_preload_seconds = time.perf_counter() - preload_start
            print(json.dumps({'event': 'host_weights_read', 'seconds': self.host_preload_seconds}), flush=True)
        self.device_backend.synchronize()
        self.device_backend.empty_cache()
        self.load_seconds = time.perf_counter() - started
        self.load_breakdown = {'adaln_prepare_seconds': adaln_prepare_seconds, 'pin_weights_seconds': pin_seconds,
                               'block_bind_seconds': block_bind_seconds,
                               'resident_upload_submit_seconds': upload_submit_seconds,
                               'resident_upload_overlaps_block_preparation': True,
                               'weight_cache_headroom_bytes': self.weight_cache_headroom_bytes,
                               'nonlocal_reserve_bytes': self.nonlocal_reserve_bytes,
                               'host_view_reserve_bytes': self.host_view_reserve_bytes,
                               'weight_cache_pin_order': pin_order,
                               'host_pinning_during_load': incremental_pinning,
                               'host_views_adopted_during_load': prepared_pins['views'],
                               'streamed_blocks_unread_during_load': prepared_pins['unread'],
                               'streamed_bytes_unread_during_load': prepared_pins['unread_bytes'],
                               'host_preload_seconds': self.host_preload_seconds,
                               'remaining_load_seconds': self.load_seconds - adaln_prepare_seconds - pin_seconds - self.host_preload_seconds,
                               'scope': 'Host wall time within model load; AdaLN includes table reads or computation and writes.'}
        fa4_module = sys.modules.get('flash_attn.cute.flash_fwd')
        fa4_path = Path(fa4_module.__file__).resolve() if fa4_module is not None else None
        self.config = {'device_backend': self.device_backend.capabilities.name, 'task': task, 'attention': attention, 'prefetch': prefetch, 'adaln_cache': adaln_cache,
                       'adaln_mode': ('portable-model-asset' if table_cache is not None and table_cache.asset else
                                      'optional-model-asset' if table_cache is not None and table_cache.optional_loaded else
                                      'local-precompute' if adaln_cache else 'original-projections'),
                       'adaln_optional_blocks': len(table_cache.optional_loaded) if table_cache is not None else 0,
                       'adaln_downloaded_files': len(table_cache.optional_downloaded) if table_cache is not None else 0,
                       'adaln_downloaded_bytes': table_cache.optional_download_bytes if table_cache is not None else 0,
                       'adaln_table_identity': table_cache.identity if table_cache is not None else None,
                       'adaln_table_producer': table_cache.producer if table_cache is not None else None,
                       'fa4_dependency': ({'file': str(fa4_path), 'sha256': hashlib.sha256(fa4_path.read_bytes()).hexdigest()}
                                          if fa4_path is not None else None),
                       'precision': precision, 'fp8_linears': len(manifest.get('linears', {})),
                       'linear_compute': linear_compute,
                       'online_lora': online_lora.get('summary'),
                       'fp8_gemm': actual_fp8_gemm,
                       'fp8_scale_granularity': manifest.get('scale_granularity'),
                       'fp8_ff_recompute': fp8_ff_recompute,
                       'resident_blocks': resident_blocks, 'resident_weight_bytes': self.resident_weight_bytes,
                       'stream_weights': stream_weights,
                       'cache_refined_text': cache_refined_text,
                       'pin_host_gb': pin_host_gb,
                       'inference_kernels': inference_kernels, 'query_chunk': query_chunk,
                       'window_batch': window_batch,
                       'ff_chunk': ff_chunk, 'head_chunk': head_chunk, 'head_parallelism': head_parallelism,
                       'offload_refiner': offload_refiner,
                       'attention_cpu_outputs': attention_cpu_outputs,
                       'grouped_attention_outputs': grouped_attention_outputs,
                       'preload_host': preload_host,
                       'pin_host_weights': pin_host_weights,
                       'pinned_model_bytes': self.pinned_model_bytes,
                       'pinned_host_allocated_bytes': self.pinned_host_allocated_bytes,
                       'allocator_config': os.environ.get('PYTORCH_ALLOC_CONF', os.environ.get('PYTORCH_CUDA_ALLOC_CONF', 'default')),
                       'adaln_disk_cache': adaln_disk_cache, 'adaln_table_cache_hits': table_cache_hits,
                       'projection_chunk': projection_chunk,
                       'reuse_block_outputs': bool(projection_chunk and inference_kernels),
                       'residual_offload': residual_offload,
                       'window_varlen': window_varlen,
                       'varlen_smooth_k': varlen_smooth_k if window_varlen else None,
                       'steps': steps, 'source_id': manifest['source_id']}

    def gpu_commit_reserve(self):
        """Allocator room this request may still reserve, which WDDM will charge to commit."""
        from .system import windows
        if self.gpu_allocation_bytes is None or not windows():
            return 0
        return max(0, self.gpu_allocation_bytes - torch.cuda.memory_reserved())

    @torch.no_grad()
    def sample(self, *args, compute_options=None, **kwargs):
        from .pass_configuration import configuration
        from .refine_schedule import modulation
        with configuration(self, compute_options), modulation(self, kwargs.get('refine_schedule')) as prepared:
            result = self._sample(*args, **kwargs)
            if prepared is not None:
                result[2]['refinement_schedule_preparation'] = prepared
            return result

    @torch.no_grad()
    def _sample(self, conditioning, seed, frames=243, width=1344, height=768, *, final_step_callback=None,
               step_callback=None, sampling_complete_callback=None, initial_latents=None, refine_steps=None, refine_schedule=None,
               allow_smaller_canvas=False, progress_offset=0, progress_total=None,
               pass_cache_budget_bytes=None, gpu_reserve_bytes=0, pass_resident_blocks=None,
               budget_refresh=None):
        from src.inference.render import load_prompt, generate_latents
        from .geometry import geometry, sampler_for_canvas
        refining = initial_latents is not None
        if refining != (refine_steps is not None):
            raise ValueError('Refinement requires both initial latents and a tail step count')
        if refining:
            from .refine import generate_latents, validate_tail
            if refine_schedule is None:
                validate_tail(self.steps, refine_steps)
        active_steps = refine_steps if refining else self.steps
        canvas = geometry(width, height, frames=frames)
        if self.canvas is not None and any(canvas[key] != self.canvas[key] for key in ('width', 'height', 'frames')):
            smaller = (allow_smaller_canvas and not refining and frames == self.canvas['frames']
                       and width <= self.canvas['width'] and height <= self.canvas['height'])
            if not smaller:
                raise ValueError('This engine was loaded for a different request geometry; reload its memory placement')
        if self.canvas is not None:
            for name in ('reference_video_tokens', 'reference_audio_tokens'):
                if name in self.canvas:
                    canvas[name] = self.canvas[name]
        frames = canvas['frames']
        if self.task.startswith('ref2va') and not refining:
            from .reference_sampler import generate_latents
        generate_latents = sampler_for_canvas(generate_latents, width, height)
        if refining:
            from functools import partial
            generate_latents = partial(generate_latents, initial_latents=initial_latents, refine_steps=refine_steps, refine_schedule=refine_schedule)
        if self.closed:
            raise RuntimeError('Engine is closed')
        from .sampling_progress import SamplingProgress
        progress = SamplingProgress(active_steps, len(self.transformer.transformer_blocks),
                                    offset=progress_offset, total=progress_total)
        progress.start()
        if self.task.startswith('ref2va'):
            from .media_conditioning import describe
            value = torch.load(conditioning, map_location='cpu', weights_only=True)
            info = describe(value, width, height, frames)
            if info['task'] != self.task:
                raise ValueError('Reference modalities changed the precomputed schedule')
            conditions = value['references']
            prompt, tags = value['prompt_embeds'].to(self.device_backend.device, torch.bfloat16), value['text_token_tags']
        else:
            prompt, tags, conditions = load_prompt(str(conditioning), self.device_backend.device)
            from .keyframes import validate_conditioning
            source_canvas = self.canvas if allow_smaller_canvas and self.canvas is not None else canvas
            validate_conditioning(prompt, tags, conditions, self.task,
                                  source_canvas['width'], source_canvas['height'])
            if conditions and (width, height) != (source_canvas['width'], source_canvas['height']):
                from .keyframes import first_pass_conditions
                conditions = first_pass_conditions(conditions, source_canvas, canvas)
                validate_conditioning(prompt, tags, conditions, self.task, width, height)
            if conditions:
                canvas['reference_video_tokens'] = len(conditions[0]) * (width // 32) * (height // 32)
        if self.cursor is not None:
            self.cursor.reset(self.steps - refine_steps if refining and refine_schedule is None else 0)
        # DecoderReadAhead is CPU/file-cache only, so start it while the last
        # two denoising steps are still running. Starting at the final step
        # left slow SSDs with too little overlap to hide a cold VAE load;
        # beginning earlier is still bounded by DecoderReadAhead's live RAM
        # check and never allocates CUDA or pinned memory. Keep the trigger
        # timing in the sample receipt: it lets the report distinguish a
        # reader that had enough sampling time to finish from one that was
        # merely started before decode. The one-step case keeps the immediate
        # callback below.
        prefetch_step = max(1, active_steps - 2)
        prefetch_timing = dict(trigger_step=None, steps_remaining=None,
                               estimated_remaining_seconds=None,
                               trigger_elapsed_seconds=None)

        def trigger_prefetch():
            if final_step_callback is None or prefetch_timing['trigger_step'] is not None:
                return
            completed = len(step_seconds)
            durations = list(step_seconds)
            average = (sum(durations) / len(durations)) if durations else None
            remaining = max(0, active_steps - completed)
            prefetch_timing.update(
                trigger_step=completed,
                steps_remaining=remaining,
                estimated_remaining_seconds=(average * remaining
                                             if average is not None else None),
                trigger_elapsed_seconds=time.perf_counter() - started)
            final_step_callback()

        class StepTimes(list):
            def append(self, value):
                super().append(value)
                cached = offloader.cached_bytes() if budget_refresh is not None else 0
                peak = owner.device_backend.max_memory_reserved()
                if budget_refresh is not None:
                    # Cache tensors are idle here; no forward hook or transfer
                    # owns them. Drop optional residency before lowering a cap.
                    # CUDA's peak is cumulative across this pass. Remember the
                    # largest cache too, so releasing a cache does not turn its
                    # historical peak into fictional activation workspace.
                    cache_peak = max(pass_cache.get('peak_cache_bytes', 0), cached)
                    pass_cache['peak_cache_bytes'] = cache_peak
                    base_peak = max(pass_cache.get('workspace_peak_bytes', 0), peak - cache_peak)
                    pass_cache['workspace_peak_bytes'] = base_peak
                    live_budget = budget_refresh('sample', lambda limit: offloader.cache_between_steps(
                        min(cached, max(0, limit - base_peak - 512 * 2**20)), allow_growth=False),
                        workspace=base_peak)
                    if len(self) < active_steps:
                        from .two_pass import pass_cache_allowance
                        from .system import system_memory
                        cached = offloader.cached_bytes()
                        free, _ = owner.device_backend.memory_info()
                        commit = system_memory().get('commit_available_bytes')
                        from .two_pass import reusable_cache_bytes
                        held = reusable_cache_bytes(cached, owner.device_backend.memory_reserved(), base_peak)
                        # Other applications' memory counts once the device is
                        # full: growing into it made Windows cut this process's
                        # budget below its own workspace. Unread: no extra bound.
                        room = getattr(budget_refresh, 'device_headroom', None)
                        headroom = room() if callable(room) else None
                        if type(headroom) is not int:
                            headroom = None
                        allowance = pass_cache_allowance(live_budget, base_peak, free + held,
                            gpu_reserve_bytes, None if commit is None else commit + held,
                            device_free=None if headroom is None else headroom + held)
                        pass_cache.update(gpu_budget_bytes=live_budget, measured_peak_reserved_bytes=peak,
                            live_free_bytes=free, commit_available_bytes=commit, admitted_bytes=allowance,
                            device_headroom_bytes=headroom)
                        # New planes fill during the next step and only save a
                        # transfer on a subsequent step. The two-step refine
                        # pass therefore cannot benefit from a late fill.
                        offloader.cache_between_steps(allowance, allow_growth=len(self) + 1 < active_steps)
                elif (len(self) == 1 and pass_cache_budget_bytes is not None and allow_smaller_canvas
                        and owner.canvas is not None and canvas['video_tokens'] < owner.canvas['video_tokens']):
                    from .two_pass import pass_cache_allowance
                    from .system import system_memory
                    free, _ = owner.device_backend.memory_info()
                    peak = owner.device_backend.max_memory_reserved()
                    commit = system_memory().get('commit_available_bytes')
                    allowance = pass_cache_allowance(pass_cache_budget_bytes, peak, free, gpu_reserve_bytes, commit)
                    pass_cache.update(measured_peak_reserved_bytes=peak, live_free_bytes=free,
                                      commit_available_bytes=commit, admitted_bytes=allowance)
                    target = (max(0, pass_resident_blocks - owner.config['resident_blocks'])
                              if pass_resident_blocks is not None else None)
                    offloader.cache_between_steps(allowance, max_layers=target,
                        allow_growth=len(self) + 1 < active_steps)
                if owner.config.get('residual_offload'):
                    residual_steps.append(dict(owner.transformer._freevideo_residual_stats))
                progress.complete(value)
                if step_callback is not None:
                    step_callback(value)
                if active_steps > 1 and len(self) == prefetch_step:
                    trigger_prefetch()

        owner = self
        step_seconds = StepTimes()
        pass_cache = dict(gpu_budget_bytes=pass_cache_budget_bytes, admitted_bytes=0,
                          requested_resident_blocks=pass_resident_blocks)
        residual_steps = []
        attention_before = dict(self.attention.backend_calls) if self.attention is not None else None
        gemm_before = None
        if self.config.get('fp8_gemm'):
            from .fp8_gemm import execution_counts
            gemm_before = execution_counts()
        parallel_before = {name: getattr(self.attention, name, 0)
                           for name in ('parallel_head_calls', 'parallel_head_warmups')}
        self.device_backend.reset_peak_memory_stats()
        self.device_backend.synchronize()
        started = time.perf_counter()
        refinement = None
        if self.cache_refined_text:
            refinement = self._prepare_refined_text(prompt)
        if active_steps == 1:
            trigger_prefetch()
        checkpoint_seconds = 0.
        try:
            with progress.layers(self.transformer.transformer_blocks), self.device_backend.make_offloader(
                    self.offload_layers, prefetch=self.prefetch, weight_source=self.stream_weight_source) as offloader:
                latents, audio = generate_latents(self.transformer, prompt, tags, frames,
                                                self.steps, seed, self.device_backend.device, step_seconds=step_seconds,
                                                conditions=conditions)
                stats = offloader.stats()
                # StepTimes.append closes over the live offloader. Retaining
                # that list subclass in a receipt also retains its pinned
                # weights through VAE loading. Reports own plain numbers only.
                sampled = {'sample_seconds': time.perf_counter() - started, 'step_seconds': list(step_seconds),
                           'offload': stats, 'residual_offload_steps': residual_steps,
                           'pass_cache_admission': pass_cache,
                           'compute_configuration': {key: self.config.get(key) for key in
                               ('head_chunk', 'window_batch', 'ff_chunk', 'projection_chunk', 'prefetch',
                                'attention_cpu_outputs', 'grouped_attention_outputs', 'residual_offload',
                                'fp8_ff_recompute', 'resident_blocks', 'stream_weights', 'pin_host_gb')},
                           'decoder_prefetch': dict(prefetch_timing),
                           'geometry': canvas, 'conditioning_shape': list(prompt.shape),
                           'attention_backend_calls': ({key: count - attention_before[key]
                                for key, count in self.attention.backend_calls.items()} if attention_before is not None else None),
                           'fp8_kernel_calls': ({key: count - gemm_before[key]
                                for key, count in execution_counts().items()} if gemm_before is not None else None),
                           'head_execution': dict(requested_parallelism=self.config.get('head_parallelism', 1),
                               **{name: getattr(self.attention, name, 0) - count
                                  for name, count in parallel_before.items()}),
                           'text_refinement': refinement,
                           'torch_peak_allocated_bytes': self.device_backend.max_memory_allocated(),
                           'torch_peak_reserved_bytes': self.device_backend.max_memory_reserved()}
                if refining:
                    sampled['refinement'] = dict(base_steps=self.steps, steps=refine_steps,
                        start_index=1 if refine_schedule else self.steps - refine_steps, restart_seed=seed,
                        schedule=refine_schedule or 'original-tail',
                        audio_policy='first_pass_preserved_with_audio_clock_conditioning')
                # The final latents must be durable before exiting the weight
                # offloader: its cleanup can fail after all NFE have completed.
                if sampling_complete_callback is not None:
                    tick = time.perf_counter()
                    sampling_complete_callback(latents, audio, sampled)
                    checkpoint_seconds = time.perf_counter() - tick
        finally:
            self.transformer._freevideo_refined_text = None
        self.device_backend.synchronize()
        elapsed = time.perf_counter() - started - checkpoint_seconds
        self.device_backend.empty_cache()
        sampled.update(sample_seconds=elapsed, torch_peak_allocated_bytes=self.device_backend.max_memory_allocated(),
                       torch_peak_reserved_bytes=self.device_backend.max_memory_reserved())
        return latents, audio, sampled

    @torch.no_grad()
    def _prepare_refined_text(self, prompt):
        """The upstream text refiner has no timestep or video input.

        Compute it once for this request and discard its 0.77 GB of weights.
        Restore only those tensors from the immutable cache for a later prompt.
        The cached tensor is cleared even when sampling fails.
        """
        from .fp8 import input_dtype
        started = time.perf_counter()
        refiner = self.transformer.token_refiner.refiner_blocks
        cache, cache_key, note = None, None, None
        if self.input_cache_dir:
            from .input_cache import InputCache, digest
            from .storage import fingerprint
            try:
                implementation = Path(sys.modules[type(self.transformer).__module__].__file__)
                identity = dict(manifest=digest(self.cache / 'manifest.json'), weights=fingerprint(self.cache / 'root.safetensors'),
                    engine=digest(Path(__file__)), upstream=digest(implementation), torch=str(torch.__version__),
                    arithmetic={k: self.config[k] for k in ('attention', 'linear_compute', 'fp8_gemm', 'inference_kernels', 'head_chunk', 'projection_chunk')},
                    **self.device_backend.arithmetic_identity())
                cache = InputCache(identity, root=self.input_cache_dir, kind='refined-text')
                cpu = prompt.detach().to('cpu').contiguous()
                cache_key = str(cpu.dtype) + str(list(cpu.shape)) + hashlib.sha256(cpu.view(torch.uint8).numpy().tobytes()).hexdigest()
                del cpu
                found = cache.lookup(cache_key)
                if found:
                    path, entry = found
                    text = torch.load(path, map_location=self.device_backend.device, weights_only=True)
                    expected_shape = [1, prompt.shape[-2], self.transformer.context_embedder.out_features]
                    if (isinstance(text, torch.Tensor) and list(text.shape) == expected_shape
                            and str(text.dtype) == entry['metrics'].get('dtype') and bool(torch.isfinite(text).all())):
                        if not self.stream_refiner_count:
                            refiner.to('meta')
                        self.refiner_released = True
                        self.transformer._freevideo_refined_text = text
                        return dict(seconds=time.perf_counter() - started, shape=list(text.shape), cache_hit=True,
                                    refiner_weights_released=True, cache_receipt=str(cache.receipt(cache_key)))
                    del text
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                note = str(error)
        if self.refiner_released and not self.stream_refiner_count:
            prefix = 'token_refiner.refiner_blocks.'
            values = {name.removeprefix(prefix): tensor for name, tensor in
                      _load_safetensors(self.cache / 'root.safetensors').items() if name.startswith(prefix)}
            if self.online_lora_root:
                values.update({name.removeprefix(prefix): tensor for name, tensor in
                               _load_safetensors(self.cache / self.online_lora_root).items()})
            refiner.load_state_dict(values, strict=True, assign=True)
            del values
        # load_prompt returns [tokens, width]; generate_latents adds the batch
        # dimension before the original transformer call.
        prompt = prompt.unsqueeze(0) if prompt.ndim == 2 else prompt
        if prompt.ndim != 3 or prompt.shape[0] != 1:
            raise ValueError('Expected one text prompt for refinement')
        text = self.transformer.context_embedder(prompt.to(input_dtype(self.transformer.context_embedder)))
        with self.device_backend.make_offloader(refiner, prefetch=False,
                            weight_source=self.stream_weight_source if self.stream_refiner_count else None) as loader:
            text = self.transformer.token_refiner(text)
            stats = loader.stats()
        del loader
        self.device_backend.synchronize()
        if not self.stream_refiner_count:
            refiner.to('meta')
        self.refiner_released = True
        self.transformer._freevideo_refined_text = text
        saved = False
        if cache is not None and cache_key:
            try:
                import uuid
                cache.root.mkdir(parents=True, exist_ok=True)
                temporary = cache.root / ('refined-' + uuid.uuid4().hex + '.partial')
                torch.save(text.detach().cpu(), temporary)
                saved = cache.put(cache_key, temporary, dict(success=True, shape=list(text.shape), dtype=str(text.dtype)), move=True)
                note = cache.note or note
            except (OSError, ValueError) as error:
                note = str(error)
        gc.collect()
        self.device_backend.empty_cache()
        empty_host_cache(torch)
        return {'seconds': time.perf_counter() - started, 'shape': list(text.shape),
                'refiner_weights_released': True, 'offload': stats, 'cache_hit': False, 'cache_saved': saved, 'cache_note': note}

    def close(self):
        if self.closed:
            return
        if self.schedule_hook is not None:
            self.schedule_hook.remove()
        self.transformer = None
        self.offload_layers = []
        if self.stream_weight_source is not None:
            self.stream_weight_source.release_host_views()
        self.stream_weight_source = None
        self.attention = None
        self.closed = True
        gc.collect()
        self.device_backend.empty_cache()
        empty_host_cache(torch)
