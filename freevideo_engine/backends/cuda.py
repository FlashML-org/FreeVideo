"""CUDA services. Existing kernels and transfer scheduling remain authoritative."""
from .base import BackendCapabilities, DeviceBackend


class CUDABackend(DeviceBackend):
    device = 'cuda'
    capabilities = BackendCapabilities(
        name='cuda', memory_model='dedicated',
        linear_policies=('native-fp8', 'bf16-weight-only', 'int8'),
        attention_candidates=('cudnn', 'torch-flash', 'sage2', 'fa2', 'fa4'),
        pinned_host_weights=True, streamed_weights=True)

    def __init__(self, *, torch_module=None):
        self._torch = torch_module

    @property
    def torch(self):
        if self._torch is None:
            import torch
            self._torch = torch
        return self._torch

    def is_available(self):
        return self.torch.cuda.is_available()

    def synchronize(self):
        return self.torch.cuda.synchronize()

    def empty_cache(self):
        return self.torch.cuda.empty_cache()

    def memory_info(self):
        return self.torch.cuda.mem_get_info()

    def max_memory_allocated(self):
        return self.torch.cuda.max_memory_allocated()

    def max_memory_reserved(self):
        return self.torch.cuda.max_memory_reserved()

    def reset_peak_memory_stats(self):
        return self.torch.cuda.reset_peak_memory_stats()

    def host_memory_stats(self):
        return self.torch.cuda.memory.host_memory_stats()

    def configure_budget(self, budget_bytes, allocator_limit_bytes=None, *,
                         reserve_bytes=0, capacity_trial=False):
        from ..gpu_budget import configure
        return configure(self.torch, budget_bytes, allocator_limit_bytes,
                         reserve_bytes=reserve_bytes, capacity_trial=capacity_trial)

    def arithmetic_identity(self):
        import hashlib
        import os
        from pathlib import Path
        torch = self.torch
        return dict(cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(),
                    hip=torch.version.hip,
                    gcn_arch=getattr(torch.cuda.get_device_properties(0), 'gcnArchName', ''),
                    device_backend='rocm' if torch.version.hip else 'cuda',
                    rocm_kernel_environment={name: os.environ.get(name, default) for name, default in
                        (('FREEVIDEO_ROCM_SPATIAL_CONV', 'miopen'), ('FREEVIDEO_ROCM_ATTENTION', 'aotriton'),
                         ('FREEVIDEO_ROCM_FFN', 'default'))}
                        if torch.version.hip else None,
                    rocm_source_sha256=({name: hashlib.sha256(Path(__file__).parents[1].joinpath(name).read_bytes()).hexdigest()
                                         for name in ('rocm_ffn.py', 'rocm_attention.py', 'rocm_spatial.py', 'rocm_compat.py')}
                                        if torch.version.hip else None),
                    rocm_blas_environment={name: os.environ.get(name) for name in
                        ('TORCH_BLAS_PREFER_HIPBLASLT', 'ROCBLAS_USE_HIPBLASLT')} if torch.version.hip else None,
                    backend_source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                           for name in ('__init__.py', 'base.py', 'cuda.py', 'cuda_attention.py')},
                    tf32=torch.backends.cuda.matmul.allow_tf32,
                    bf16_reduced_reduction=torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction,
                    deterministic=torch.are_deterministic_algorithms_enabled())

    def attention_kernels(self, global_backend, window_backend, *,
                          query_chunk=0, window_varlen=False):
        from .cuda_attention import CUDAAttentionKernels
        return CUDAAttentionKernels(global_backend, window_backend,
                                    query_chunk=query_chunk, window_varlen=window_varlen)

    def prepare_linears(self, model, manifest, linear_compute, fp8_gemm):
        if linear_compute not in self.capabilities.linear_policies:
            raise ValueError('Unknown linear compute policy')
        precision = manifest.get('precision', 'bf16')
        actual_fp8_gemm = None
        if precision == 'fp8':
            from src.models.ops import fp8_linear
            # On ROCm select the verified scale policy from the real architecture,
            # never from HIP's CUDA capability compatibility value.
            if self.torch.version.hip:
                arch = getattr(self.torch.cuda.get_device_properties(0), 'gcnArchName', '').split(':')[0]
                if linear_compute == 'native-fp8' and arch != 'gfx1201':
                    raise ValueError('ROCm native FP8 is currently validated only on gfx1201')
                fp8_linear._PER_TENSOR = True
            per_tensor_gemm = fp8_linear.per_tensor_gemm
            from ..fp8 import install_cached_linears
            expected = 'per_tensor' if per_tensor_gemm() else 'rowwise'
            if linear_compute == 'native-fp8' and not self.torch.version.hip and self.torch.cuda.get_device_capability() < (8, 9):
                raise ValueError('Native FP8 GEMM requires Ada or newer; choose bf16-weight-only on Ampere')
            if linear_compute == 'native-fp8' and manifest['scale_granularity'] != expected:
                raise ValueError('FP8 cache scale granularity does not match this GPU; prepare a separate cache')
            install_cached_linears(model, manifest['linears'], weight_only=linear_compute == 'bf16-weight-only')
            if linear_compute == 'native-fp8':
                from ..fp8_gemm import install
                actual_fp8_gemm = install(model, manifest['scale_granularity'], requested=fp8_gemm)
        elif precision == 'int8':
            if linear_compute != 'int8':
                raise ValueError('Int8 weights on CUDA run with the int8 linear policy')
            from ..int8_ops import install_cached_linears as install_int8_linears
            install_int8_linears(model, manifest['linears'], manifest.get('rotation'))
        elif precision != 'bf16':
            raise ValueError('Unknown prepared weight precision')
        return actual_fp8_gemm

    def install_chunked_ff(self, module, chunk, *, recompute=False):
        from ..fp8_ops import install_chunked_ff
        if self.torch.version.hip:
            import os
            selected = os.environ.get('FREEVIDEO_ROCM_FFN', 'default')
            if selected not in ('default', 'tail-preserving'):
                raise ValueError('FREEVIDEO_ROCM_FFN must be default or tail-preserving')
            if selected == 'tail-preserving' and chunk == 2048 and not recompute:
                arch = self.torch.cuda.get_device_properties(0).gcnArchName.split(':')[0]
                if arch != 'gfx1201':
                    raise ValueError('Tail-preserving FP8 FFN is validated only on gfx1201')
                from ..rocm_ffn import install_tail_preserving_ff
                return install_tail_preserving_ff(module)
        return install_chunked_ff(module, chunk, recompute=recompute)

    def make_offloader(self, layers, **options):
        from ..offload import LayerOffloader
        return LayerOffloader(layers, device=self.device, **options)

    def pin_layer_weights(self, layers, **options):
        from ..offload import pin_layer_weights
        return pin_layer_weights(layers, **options)

    def prepare_streamed_layer(self, layer, source, index, **options):
        from ..offload import prepare_streamed_layer
        return prepare_streamed_layer(layer, source, index, **options)

    def unload_streamed_layer(self, layer, source, index):
        from ..offload import unload_streamed_layer
        return unload_streamed_layer(layer, source, index)
