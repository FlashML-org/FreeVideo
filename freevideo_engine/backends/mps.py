"""Apple MPS services with one shared physical-memory budget.

Model loading/streaming are integrated separately. Unsupported services fail
explicitly; model operations never route to CUDA or implicit CPU fallback.
"""
import math
import os

from .base import BackendCapabilities, DeviceBackend


def allocator_ceiling(budget, recommended, total, available, owned, reserve, explicit=None):
    values = (budget, recommended, total, available, owned, reserve)
    if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in values):
        raise ValueError('MPS admission requires finite nonnegative byte counts')
    if not budget or not recommended or not total:
        raise ValueError('MPS admission requires positive budget and capacity')
    if explicit is not None and (type(explicit) is not int or not 0 < explicit <= total):
        raise ValueError('MPS allocator limit must be positive and within unified physical RAM')
    bounds = dict(planning_budget_bytes=int(budget), recommended_working_set_bytes=int(recommended),
        physical_capacity_after_reserve_bytes=max(0, int(total - reserve)),
        live_pool_capacity_bytes=max(0, int(min(total, available) + owned - reserve)))
    if explicit is not None:
        bounds['explicit_allocator_limit_bytes'] = explicit
    return dict(allocator_limit_bytes=min(bounds.values()), bounds=bounds,
                owned_driver_bytes=int(owned), growth_reserve_bytes=int(reserve))


class MPSBackend(DeviceBackend):
    device = 'mps'
    capabilities = BackendCapabilities(name='mps', memory_model='unified',
        linear_policies=('bf16', 'bf16-weight-only'), attention_candidates=('mps',),
        pinned_host_weights=False, streamed_weights=True)

    def __init__(self, *, torch_module=None):
        if os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK', '0') not in ('', '0'):
            raise ValueError('MPS CPU fallback must be disabled; unsupported operators must fail explicitly')
        self._torch = torch_module
        self._limit = None
        self._reserve = 0

    @property
    def torch(self):
        if self._torch is None:
            import torch
            self._torch = torch
        return self._torch

    def is_available(self):
        return self.torch.backends.mps.is_available()

    def synchronize(self):
        return self.torch.mps.synchronize()

    def empty_cache(self):
        return self.torch.mps.empty_cache()

    def memory_stats(self):
        from ..macos_memory import memory_status
        return dict(memory_model='unified', host=memory_status(),
            current_allocated_bytes=self.torch.mps.current_allocated_memory(),
            driver_allocated_bytes=self.torch.mps.driver_allocated_memory(),
            recommended_working_set_bytes=self.torch.mps.recommended_max_memory(),
            allocator_limit_bytes=self._limit, peak_allocated_bytes=None,
            peak_driver_bytes=None, peak_measurement='not exposed by the MPS API')

    def memory_info(self):
        row = self.memory_stats()
        domain = min(row['host']['total_bytes'], row['recommended_working_set_bytes'])
        if self._limit is not None:
            domain = min(domain, self._limit)
        free = max(0, min(domain - row['driver_allocated_bytes'],
                          row['host']['available_bytes'] - self._reserve))
        return free, domain

    def max_memory_allocated(self):
        return None  # Current usage is not a peak.

    def max_memory_reserved(self):
        return None

    def reset_peak_memory_stats(self):
        return None

    def host_memory_stats(self):
        return {'supported': False, 'memory_model': 'unified', 'allocated_bytes.current': None}

    def configure_budget(self, budget_bytes, allocator_limit_bytes=None, *,
                         reserve_bytes=0, capacity_trial=False):
        row = self.memory_stats()
        decision = allocator_ceiling(budget_bytes, row['recommended_working_set_bytes'],
            row['host']['total_bytes'], row['host']['available_bytes'], row['driver_allocated_bytes'],
            reserve_bytes, allocator_limit_bytes)
        limit = decision['allocator_limit_bytes']
        if limit <= 0:
            raise MemoryError('No unified-memory allowance remains for MPS after the system reserve')
        # Never pass zero: MPS interprets fraction=0 as UNLIMITED allocation.
        self.torch.mps.set_per_process_memory_fraction(float(limit / row['recommended_working_set_bytes']))
        self._limit, self._reserve = limit, reserve_bytes
        return dict(enforced=True, memory_model='unified', admission=decision,
            effective_allocator_limit_bytes=limit, capacity_trial_limit_enforced=bool(capacity_trial),
            scope='MPS allocator only; CPU allocations and other applications share physical RAM and need a host guard.')

    def arithmetic_identity(self):
        import hashlib
        from importlib.metadata import PackageNotFoundError, version
        import os
        from pathlib import Path
        import platform
        from ..macos_memory import chip_name
        packages = {}
        for name in ('mlx', 'mlx-metal'):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = None
        return dict(device_backend='mps', gpu=chip_name(), macos=platform.mac_ver()[0],
            torch=str(self.torch.__version__),
            packages=packages,
            mps_fast_math=os.environ.get('PYTORCH_MPS_FAST_MATH', '0'),
            mps_prefer_metal=os.environ.get('PYTORCH_MPS_PREFER_METAL', '0'),
            mps_cpu_fallback=os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK', '0'),
            deterministic=self.torch.are_deterministic_algorithms_enabled(),
            backend_source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ('__init__.py', 'base.py', 'mps.py', 'mps_attention.py', 'mps_weights.py',
                             'mps_fp8.py', 'mps_nvfp4.py', 'mps_linear.py', 'mps_delta.py',
                             'mps_features.py', 'mps_qk.py', 'mps_mlx_attention.py', 'mps_vae.py',
                             'mps_blocks.py', 'mps_grouped_qkv.py', 'mps_vae_encode.py', 'mps_modulation.py',
                             'mps_int8.py')},
            runtime_source_sha256={name: hashlib.sha256((Path(__file__).parent.parent / name).read_bytes()).hexdigest()
                for name in ('macos_runtime.py', 'macos_vdn.py', 'macos_decode.py', 'macos_encoder.py',
                             'macos_compute.py', 'macos_stages.py', 'macos_decode_tiles.py',
                             'latent_upscale.py')})

    def attention_kernels(self, global_backend, window_backend, *, query_chunk=0, window_varlen=False):
        from .mps_attention import MPSAttentionKernels
        return MPSAttentionKernels(global_backend, window_backend,
                                  query_chunk=query_chunk, window_varlen=window_varlen)

    def prepare_linears(self, model, manifest, linear_compute, fp8_gemm):
        if fp8_gemm != 'auto':
            raise ValueError('MPS BF16 execution does not select a CUDA FP8 GEMM')
        precision = manifest.get('precision')
        if precision == 'fp8' and linear_compute == 'bf16-weight-only':
            if manifest.get('scale_granularity') not in ('rowwise', 'per_tensor'):
                raise ValueError('Unknown FP8 storage scale granularity')
            return None  # The MPS weight reader decodes each matrix before binding.
        if precision == 'int8' and linear_compute in ('int8', 'bf16-weight-only'):
            if manifest.get('scale_granularity') != 'rowwise':
                raise ValueError('Int8 storage requires per-row scales')
            # Int8 products keep eligible weights int8; every other matrix, and
            # every matrix on GPUs without int8 TensorOps, is dequantized when read.
            return None
        if linear_compute != 'bf16' or precision != 'bf16':
            raise ValueError('MPS requires BF16 execution or explicit BF16 weight-only decoding')
        return None

    def install_chunked_ff(self, module, chunk, *, recompute=False):
        if type(chunk) is not int or chunk < 1 or recompute:
            raise ValueError('MPS reference feed-forward requires a positive chunk without FP8 recompute')
        import types
        torch = self.torch
        original = module.forward
        def forward(owner, hidden, *args, **kwargs):
            if torch.is_grad_enabled():
                raise RuntimeError('Chunked MPS feed-forward requires inference')
            output = torch.empty_like(hidden)
            for start in range(0, hidden.shape[-2], chunk):
                output[..., start:start + chunk, :] = original(hidden[..., start:start + chunk, :], *args, **kwargs)
            return output
        module.forward = types.MethodType(forward, module)
        module._freevideo_mps_ff_chunk = chunk

    def make_offloader(self, layers, **options):
        from .mps_weights import LayerResidency
        return LayerResidency(layers, backend=self, **options)

    def pin_layer_weights(self, *args, **kwargs):
        raise NotImplementedError('CUDA pinned host weights do not apply to MPS unified memory')

    def prepare_streamed_layer(self, *args, **kwargs):
        raise NotImplementedError('MPS model streaming is not integrated yet')

    def unload_streamed_layer(self, *args, **kwargs):
        raise NotImplementedError('MPS model streaming is not integrated yet')
