"""Official decomposed attention or portable cuDNN/Sage window calls."""
import types
import importlib.metadata
from collections import defaultdict

import torch
import torch.nn.functional as F
from torch.nn.attention import SDPBackend, sdpa_kernel

from .paths import add_vdn
add_vdn()
from src.models.softmax_attention.decomposed import _plan, window_softmax_decomposed


ALIASES = {'original': 'cudnn/fa4', 'dense': 'cudnn/cudnn',
           'sage2': 'sage2/sage2', 'sage2-window': 'cudnn/sage2',
           'fa2-window': 'cudnn/fa2', 'sdpa': 'torch-flash/torch-flash'}
BACKENDS = ('cudnn', 'torch-flash', 'sage2', 'fa2', 'fa4')


def split_backend(backend):
    parts = ALIASES.get(backend, backend).split('/')
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2 or any(p not in BACKENDS for p in parts):
        raise ValueError('Attention must be a backend or global/window pair: ' + ', '.join(BACKENDS))
    return tuple(parts)


class WindowAttention:
    def __init__(self, backend, query_chunk=0, window_batch=1, window_varlen=False,
                 varlen_smooth_k=True):
        if window_batch < 1:
            raise ValueError('Window batch must be positive')
        if window_varlen and query_chunk:
            raise ValueError('Packed varlen windows do not support a query-chunk override')
        self.backend = backend
        self.query_chunk = query_chunk
        self.window_batch = window_batch
        # One packed call replaces the per-window Python loop. Sage2 smooths
        # keys over the whole packed batch, while the loop smooths each window
        # separately, so this is a reported numerical change, never implied.
        self.window_varlen = window_varlen
        self.varlen_smooth_k = varlen_smooth_k
        self.current_plan = None
        self.offsets = None
        self.batches = None
        self.calls = 0
        self.window_calls = 0
        self.backend_calls = {part + '_' + name: 0 for part in ('global', 'window')
                              for name in (*BACKENDS, 'fa2_varlen', 'fa4_varlen', 'sage2_varlen')}
        self.select_backend(backend)

    def batched(self, q, k, v, scale, *, window=False):
        leg = self.window_backend if window else self.global_backend
        self.calls += 1
        self.backend_calls[('window_' if window else 'global_') + leg] += 1
        if leg == 'sage2':
            return self.attention(q, k, v, tensor_layout='NHD', is_causal=False, sm_scale=scale)
        if leg == 'fa2':
            from flash_attn.flash_attn_interface import flash_attn_func
            return flash_attn_func(q, k, v, softmax_scale=scale, causal=False)
        if leg == 'fa4':
            from flash_attn.cute import flash_attn_func
            result = flash_attn_func(q, k, v, softmax_scale=scale, causal=False)
            return result[0] if isinstance(result, tuple) else result
        backend = SDPBackend.CUDNN_ATTENTION if leg == 'cudnn' else SDPBackend.FLASH_ATTENTION
        with sdpa_kernel(backend):
            result = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2),
                                                    v.transpose(1, 2), scale=scale)
        return result.transpose(1, 2)

    def dense(self, q, k, v, scale, *, window=False):
        return self.batched(q.unsqueeze(0), k.unsqueeze(0), v.unsqueeze(0), scale, window=window)[0]

    def select_backend(self, backend):
        self.global_backend, self.window_backend = split_backend(backend)
        if 'fa4' in (self.global_backend, self.window_backend):
            from .fa4_guard import check_selected
            check_selected()
        if self.window_backend in ('fa2', 'fa4') and self.query_chunk:
            raise ValueError('Packed varlen windows do not support a query-chunk override')
        if self.window_varlen and self.window_backend not in ('sage2', 'fa2', 'fa4'):
            raise ValueError('Packed varlen windows require a sage2, fa2 or fa4 window backend')
        if 'sage2' in (self.global_backend, self.window_backend):
            if not importlib.metadata.version('sageattention').startswith('2.'):
                raise ImportError('The sage2 route requires SageAttention 2.x; the tested version is 2.2.0.')
            from .triton_compat import activate
            activate()
            from sageattention import sageattn
            self.attention = sageattn
            if self.window_varlen and self.window_backend == 'sage2':
                from sageattention import sageattn_varlen
                self.attention_varlen = sageattn_varlen
        if 'fa2' in (self.global_backend, self.window_backend):
            if not importlib.metadata.version('flash-attn').startswith('2.'):
                raise ImportError('The fa2 route requires flash-attn 2.x.')
            from flash_attn.flash_attn_interface import flash_attn_varlen_func
        self.backend = backend

    def _window_batches(self, plan):
        # Windows may share shapes but never keys/softmax normalization. The
        # batch dimension preserves each exact mask, including anchor columns.
        # Cache only indices; activations are gathered one bounded batch at a time.
        groups = defaultdict(list)
        q_offsets, k_offsets = self.offsets
        for qa, qb, ka, kb in zip(q_offsets, q_offsets[1:], k_offsets, k_offsets[1:]):
            chunk = self.query_chunk or qb - qa
            for start in range(qa, qb, chunk):
                end = min(start + chunk, qb)
                groups[end - start, kb - ka].append((start, end, ka, kb))
        batches = []
        for windows in groups.values():
            for start in range(0, len(windows), self.window_batch):
                selected = windows[start:start + self.window_batch]
                rows = torch.stack([plan.win_q[qa:qb] for qa, qb, _, _ in selected])
                keys = torch.stack([plan.kv_gather[ka:kb] for _, _, ka, kb in selected])
                batches.append((rows, keys))
        return batches

    def prepare(self, layout, bounds, device, anchor_frames='none', *, batches=False):
        """Create shared indices on the caller stream before parallel readers."""
        plan = _plan(layout, bounds, anchor_frames, device)
        if plan is not self.current_plan:
            self.offsets = (plan.cu_q.tolist(), plan.cu_k.tolist()) if plan.has_windows else ([], [])
            self.current_plan = plan
            self.batches = None
        if (batches and plan.has_windows and self.window_batch > 1 and not self.window_varlen
                and self.window_backend not in ('fa2', 'fa4') and self.batches is None):
            self.batches = self._window_batches(plan)
        return plan

    def __call__(self, q, k, v, layout, bounds, scale, anchor_frames='none'):
        if (self.global_backend, self.window_backend) == ('cudnn', 'fa4'):
            plan = _plan(layout, bounds, anchor_frames, q.device)
            self.backend_calls['global_cudnn'] += bool(len(plan.dense_q))
            self.backend_calls['window_fa4_varlen'] += bool(plan.has_windows)
            self.window_calls += 1
            return window_softmax_decomposed(q, k, v, layout, bounds, scale, anchor_frames)
        plan = self.prepare(layout, bounds, q.device, anchor_frames)
        out = torch.empty_like(q)
        if len(plan.dense_q):
            chunk = self.query_chunk or len(plan.dense_q)
            for start in range(0, len(plan.dense_q), chunk):
                rows = plan.dense_q[start:start + chunk]
                out[rows] = self.dense(q[rows], k, v, scale)
        if plan.has_windows:
            if self.window_varlen and self.window_backend == 'sage2':
                # Every window in one kernel: no Python loop, no per-window
                # gather temporaries. Keys are smoothed across the packed batch.
                out[plan.win_q] = self.attention_varlen(
                    q[plan.win_q], k[plan.kv_gather], v[plan.kv_gather],
                    cu_seqlens_q=plan.cu_q, cu_seqlens_k=plan.cu_k,
                    max_seqlen_q=plan.max_q, max_seqlen_k=plan.max_k,
                    sm_scale=scale, is_causal=False, smooth_k=self.varlen_smooth_k)
                self.backend_calls['window_sage2_varlen'] += 1
            elif self.window_backend in ('fa2', 'fa4'):
                if self.window_backend == 'fa2':
                    from flash_attn.flash_attn_interface import flash_attn_varlen_func
                else:
                    from flash_attn.cute import flash_attn_varlen_func
                result = flash_attn_varlen_func(q[plan.win_q], k[plan.kv_gather], v[plan.kv_gather],
                                               cu_seqlens_q=plan.cu_q, cu_seqlens_k=plan.cu_k,
                                               max_seqlen_q=plan.max_q, max_seqlen_k=plan.max_k,
                                               softmax_scale=scale, causal=False)
                out[plan.win_q] = result[0] if isinstance(result, tuple) else result
                self.backend_calls['window_' + self.window_backend + '_varlen'] += 1
            elif self.window_batch > 1:
                if self.batches is None:
                    self.batches = self._window_batches(plan)
                for rows, keys in self.batches:
                    # NHD with an explicit batch dimension. Do not concatenate
                    # sequence lengths: that would mix unrelated attention masks.
                    qw, kw, vw = q[rows], k[keys], v[keys]
                    result = self.batched(qw, kw, vw, scale, window=True)
                    out[rows] = result
                    del qw, kw, vw, result
                self.window_calls += 1
                return out
            else:
                q_offsets, k_offsets = self.offsets
                for qa, qb, ka, kb in zip(q_offsets, q_offsets[1:], k_offsets, k_offsets[1:]):
                    keys = plan.kv_gather[ka:kb]
                    kw, vw = k[keys], v[keys]
                    chunk = self.query_chunk or qb - qa
                    for start in range(qa, qb, chunk):
                        rows = plan.win_q[start:min(start + chunk, qb)]
                        out[rows] = self.dense(q[rows], kw, vw, scale, window=True)
                    del kw, vw
        self.window_calls += 1
        return out

    def install(self, transformer):
        from src.models.hybrid_transform import iter_hybrids
        policy = self

        def window(attn, q, k, v, layout, bounds, scale, inference):
            return policy(q, k, v, layout, bounds, scale, attn.anchor_frames)

        count = 0
        for attn in iter_hybrids(transformer):
            attn._window_softmax = types.MethodType(window, attn)
            count += 1
        return count
