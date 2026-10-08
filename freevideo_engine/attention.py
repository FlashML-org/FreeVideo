"""Official decomposed attention or portable cuDNN/Sage window calls."""
import types
from collections import defaultdict

import torch

from .backends import get_backend
from .paths import add_vdn
add_vdn()
from src.models.softmax_attention.decomposed import _plan


ALIASES = {'original': 'cudnn/fa4', 'dense': 'cudnn/cudnn',
           'sage2': 'sage2/sage2', 'sage2-window': 'cudnn/sage2',
           'fa2-window': 'cudnn/fa2', 'sdpa': 'torch-flash/torch-flash'}
BACKENDS = ('cudnn', 'torch-flash', 'sage2', 'fa2', 'fa4')


def split_backend(backend, allowed=BACKENDS):
    parts = ALIASES.get(backend, backend).split('/')
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2 or any(p not in allowed for p in parts):
        raise ValueError('Attention must be a backend or global/window pair: ' + ', '.join(allowed))
    return tuple(parts)


class WindowAttention:
    def __init__(self, backend, query_chunk=0, window_batch=1, window_varlen=False,
                 varlen_smooth_k=True, *, device_backend=None):
        if window_batch < 1:
            raise ValueError('Window batch must be positive')
        if window_varlen and query_chunk:
            raise ValueError('Packed varlen windows do not support a query-chunk override')
        self.device_backend = device_backend if device_backend is not None else get_backend()
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
        names = ((*BACKENDS, 'fa2_varlen', 'fa4_varlen', 'sage2_varlen')
                 if self.device_backend.capabilities.name == 'cuda'
                 else self.device_backend.capabilities.attention_candidates)
        self.backend_calls = {part + '_' + name: 0 for part in ('global', 'window') for name in names}
        if torch.version.hip:
            self.backend_calls.update({'global_rocm-triton': 0, 'window_rocm-triton': 0})
        self.select_backend(backend)

    def batched(self, q, k, v, scale, *, window=False):
        leg = self.window_backend if window else self.global_backend
        self.calls += 1
        accelerated_window = (window and leg == 'torch-flash'
                              and getattr(self.kernels, 'rocm_backend', None) == 'triton-window')
        actual = 'rocm-triton' if accelerated_window else leg
        self.backend_calls[('window_' if window else 'global_') + actual] += 1
        if accelerated_window:
            return self.kernels.batched(leg, q, k, v, scale, window=True)
        return self.kernels.batched(leg, q, k, v, scale)

    def dense(self, q, k, v, scale, *, window=False):
        return self.batched(q.unsqueeze(0), k.unsqueeze(0), v.unsqueeze(0), scale, window=window)[0]

    def select_backend(self, backend):
        global_backend, window_backend = split_backend(backend,
            allowed=self.device_backend.capabilities.attention_candidates)
        kernels = self.device_backend.attention_kernels(global_backend, window_backend,
            query_chunk=self.query_chunk, window_varlen=self.window_varlen)
        self.global_backend, self.window_backend = global_backend, window_backend
        self.kernels = kernels
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
            return self.kernels.decomposed(q, k, v, layout, bounds, scale, anchor_frames)
        plan = self.prepare(layout, bounds, q.device, anchor_frames)
        out = torch.empty_like(q)
        if len(plan.dense_q):
            chunk = self.query_chunk or len(plan.dense_q)
            for start in range(0, len(plan.dense_q), chunk):
                rows = plan.dense_q[start:start + chunk]
                out[rows] = self.dense(q[rows], k, v, scale)
        if plan.has_windows:
            if ((self.window_varlen and self.window_backend == 'sage2')
                    or self.window_backend in ('fa2', 'fa4')):
                out[plan.win_q] = self.kernels.varlen(
                    self.window_backend, q[plan.win_q], k[plan.kv_gather], v[plan.kv_gather],
                    plan, scale, smooth_k=self.varlen_smooth_k)
                self.backend_calls['window_' + self.window_backend + '_varlen'] += 1
            elif self.window_batch > 1:
                if self.batches is None:
                    self.batches = self._window_batches(plan)
                indexed = (layout.tokens_per_frame >= 1008
                           and self.window_backend == 'torch-flash'
                           and getattr(self.kernels, 'rocm_backend', None) == 'triton-window'
                           and q.ndim == 3 and q.shape == k.shape == v.shape
                           and q.shape[-1] == 128
                           and q.dtype == k.dtype == v.dtype == torch.bfloat16)
                for rows, keys in self.batches:
                    if indexed:
                        from .rocm_attention import attention_indexed
                        out[rows] = attention_indexed(q, k, v, rows, keys, scale)
                        self.calls += 1
                        self.backend_calls['window_rocm-triton'] += 1
                        continue
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
