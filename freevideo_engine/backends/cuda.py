"""CUDA services. Existing kernels and transfer scheduling remain authoritative."""
from .base import BackendCapabilities, DeviceBackend


class CUDABackend(DeviceBackend):
    device = 'cuda'
    capabilities = BackendCapabilities(
        name='cuda', memory_model='dedicated',
        linear_policies=('native-fp8', 'bf16-weight-only'),
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
        from pathlib import Path
        torch = self.torch
        return dict(cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(),
                    device_backend='cuda',
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
            from src.models.ops.fp8_linear import per_tensor_gemm
            from ..fp8 import install_cached_linears
            expected = 'per_tensor' if per_tensor_gemm() else 'rowwise'
            if linear_compute == 'native-fp8' and self.torch.cuda.get_device_capability() < (8, 9):
                raise ValueError('Native FP8 GEMM requires Ada or newer; choose bf16-weight-only on Ampere')
            if linear_compute == 'native-fp8' and manifest['scale_granularity'] != expected:
                raise ValueError('FP8 cache scale granularity does not match this GPU; prepare a separate cache')
            install_cached_linears(model, manifest['linears'], weight_only=linear_compute == 'bf16-weight-only')
            if linear_compute == 'native-fp8':
                from ..fp8_gemm import install
                actual_fp8_gemm = install(model, manifest['scale_granularity'], requested=fp8_gemm)
        elif precision != 'bf16':
            raise ValueError('Unknown prepared weight precision')
        return actual_fp8_gemm

    def install_chunked_ff(self, module, chunk, *, recompute=False):
        from ..fp8_ops import install_chunked_ff
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
