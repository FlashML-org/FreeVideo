"""Selected CUDA attention operators; failures propagate without substitution."""
import importlib.metadata


class CUDAAttentionKernels:
    def __init__(self, global_backend, window_backend, *, query_chunk=0, window_varlen=False):
        import os
        import torch
        self.rocm_backend = os.environ.get('FREEVIDEO_ROCM_ATTENTION', 'aotriton') if torch.version.hip else None
        if self.rocm_backend not in (None, 'aotriton', 'triton-window'):
            raise ValueError('FREEVIDEO_ROCM_ATTENTION must be aotriton or triton-window')
        if self.rocm_backend == 'triton-window':
            arch = torch.cuda.get_device_properties(torch.cuda.current_device()).gcnArchName.split(':')[0]
            if arch != 'gfx1201':
                raise ValueError('Triton H3 attention is validated only on gfx1201')
        choices = ('cudnn', 'torch-flash', 'sage2', 'fa2', 'fa4')
        if global_backend not in choices or window_backend not in choices:
            raise ValueError('Unknown CUDA attention backend')
        if 'fa4' in (global_backend, window_backend):
            from ..fa4_guard import check_selected
            check_selected()
        if window_backend in ('fa2', 'fa4') and query_chunk:
            raise ValueError('Packed varlen windows do not support a query-chunk override')
        if window_varlen and window_backend not in ('sage2', 'fa2', 'fa4'):
            raise ValueError('Packed varlen windows require a sage2, fa2 or fa4 window backend')
        if 'sage2' in (global_backend, window_backend):
            if not importlib.metadata.version('sageattention').startswith('2.'):
                raise ImportError('The sage2 route requires SageAttention 2.x; the tested version is 2.2.0.')
            from ..triton_compat import activate
            activate()
            from sageattention import sageattn
            self.attention = sageattn
            if window_varlen and window_backend == 'sage2':
                from sageattention import sageattn_varlen
                self.attention_varlen = sageattn_varlen
        if 'fa2' in (global_backend, window_backend):
            if not importlib.metadata.version('flash-attn').startswith('2.'):
                raise ImportError('The fa2 route requires flash-attn 2.x.')
            from flash_attn.flash_attn_interface import flash_attn_varlen_func

    def batched(self, leg, q, k, v, scale, *, window=False):
        if leg == 'torch-flash' and self.rocm_backend == 'triton-window' and window:
            from ..rocm_attention import attention
            return attention(q, k, v, scale)
        if leg == 'sage2':
            return self.attention(q, k, v, tensor_layout='NHD', is_causal=False, sm_scale=scale)
        if leg == 'fa2':
            from flash_attn.flash_attn_interface import flash_attn_func
            return flash_attn_func(q, k, v, softmax_scale=scale, causal=False)
        if leg == 'fa4':
            from flash_attn.cute import flash_attn_func
            result = flash_attn_func(q, k, v, softmax_scale=scale, causal=False)
            return result[0] if isinstance(result, tuple) else result
        if leg not in ('cudnn', 'torch-flash'):
            raise ValueError('Unknown CUDA attention backend: ' + str(leg))
        import torch.nn.functional as F
        from torch.nn.attention import SDPBackend, sdpa_kernel
        backend = SDPBackend.CUDNN_ATTENTION if leg == 'cudnn' else SDPBackend.FLASH_ATTENTION
        with sdpa_kernel(backend):
            result = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2),
                                                    v.transpose(1, 2), scale=scale)
        return result.transpose(1, 2)

    def varlen(self, leg, q, k, v, plan, scale, *, smooth_k=True):
        if leg == 'sage2':
            return self.attention_varlen(q, k, v,
                cu_seqlens_q=plan.cu_q, cu_seqlens_k=plan.cu_k,
                max_seqlen_q=plan.max_q, max_seqlen_k=plan.max_k,
                sm_scale=scale, is_causal=False, smooth_k=smooth_k)
        if leg == 'fa2':
            from flash_attn.flash_attn_interface import flash_attn_varlen_func
        elif leg == 'fa4':
            from flash_attn.cute import flash_attn_varlen_func
        else:
            raise ValueError('Packed varlen requires sage2, fa2 or fa4')
        result = flash_attn_varlen_func(q, k, v,
            cu_seqlens_q=plan.cu_q, cu_seqlens_k=plan.cu_k,
            max_seqlen_q=plan.max_q, max_seqlen_k=plan.max_k,
            softmax_scale=scale, causal=False)
        return result[0] if isinstance(result, tuple) else result

    def decomposed(self, q, k, v, layout, bounds, scale, anchor_frames):
        from src.models.softmax_attention.decomposed import window_softmax_decomposed
        return window_softmax_decomposed(q, k, v, layout, bounds, scale, anchor_frames)
