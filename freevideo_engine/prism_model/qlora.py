# FreeVideo addition for the vendored Prism model (MIT); see NOTICE.
"""The Light tier's unmerged distill LoRA in INT8, next to the base W8A8 GEMM.

The student pass adds ``(y @ down^T) @ up^T (+ diff_b)`` to every base QLinear of a
video block (rank 256). sampling.lean_dit_block_lora runs that branch in bf16 on a
second, unquantized LayerNorm pass (``y``); on GeForce cards bf16 tensor throughput
is a quarter of INT8, so the branch costs far more than its ~8.5% of the FLOPs.

Here both halves run on the INT8 W8A8 GEMM (qlinear.w8a8_gemm) and reuse the base
GEMM's already quantized, Hadamard-rotated input:

  down  t = xq @ Dq^T * sx * sd  with  Dq = INT8(rotate_k(down / act_mult))  -- the
        same transform as the base weight (x W^T = (x * act_mult) H (rotate_k(W /
        act_mult))^T), so ``xq`` (and its offset, if asymmetric) is used as is; the
        downs of linears that share one quantized input (q/k/v) run as one GEMM.
  up    out += [gate *] (INT8(t) @ Uq^T * st * su + diff_b): per-token INT8 t (no
        rotation), per-channel INT8 up, as a second W8A8 GEMM (K = rank) whose
        epilogue adds into the base output or the gated residual in place.
        (A variant with the up as a second K loop inside the base GEMM, two int32
        accumulators, measured no faster on RTX 4070 / H200: its tile has to shrink
        to 128x128.)

No extra LayerNorm pass and no bf16 GEMM remain. The INT8 LoRA weights are made on
the GPU at the start of every student block call (~9 ms per block on an RTX 4070):
streamed blocks re-point their buffers into shared slots (``.data`` rebinding keeps
the tensor's version counter), so a cache keyed on the buffers could serve the other
expert's LoRA.

Switch: FREEVIDEO_PRISM_LORA_INT8 (default on; 0 restores the bf16 LoRA branch).
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

import torch

from . import qlinear


def enabled() -> bool:
    return os.environ.get('FREEVIDEO_PRISM_LORA_INT8', '1') == '1'


class QLoRA:
    """INT8 down/up of one LoRA (or of a group of LoRAs sharing one quantized input:
    their downs concatenated, ``slices`` gives each member's columns of t)."""
    __slots__ = ('dq', 'ds', 'dsum', 'ups', 'slices')

    def __init__(self, dq, ds, dsum, ups, slices):
        self.dq, self.ds, self.dsum, self.ups, self.slices = dq, ds, dsum, ups, slices


def supported(lin) -> bool:
    return (getattr(lin, 'lora_down', None) is not None and getattr(lin, '_is_prism_qlinear', False)
            and lin.mode == 'w8a8_int8' and lin.lora_down.device.type == 'cuda')


def _int8_rows(w):
    """Per-row symmetric INT8 (round to nearest, absmax scale) in a few fused ops."""
    s = w.abs().amax(1).clamp_min_(1e-12).div_(qlinear.INT8_MAX)
    return torch.round(w / s[:, None]).clamp_(-qlinear.INT8_MAX, qlinear.INT8_MAX).to(torch.int8), s


@torch.no_grad()
def _down_weight(lins):
    """rotate_k(down / act_mult) of the group's downs, concatenated: the base weight's
    input transform applied to down (members share act_mult / rotation)."""
    base = lins[0]
    d = torch.cat([lin.lora_down for lin in lins]) if len(lins) > 1 else base.lora_down
    d = d.float()
    if base.act_mult is not None:
        d = d / base.act_mult[None, :]
    if base.rot_block:
        b = base.rot_block
        h = base._had.get(('qlora', d.device))
        if h is None:
            h = base._had[('qlora', d.device)] = qlinear.hadamard(b, d.device, torch.float32) / b ** 0.5
        d = (d.view(-1, d.shape[1] // b, b) @ h).view(d.shape)
    return d


@torch.no_grad()
def prepare(lins: Sequence) -> QLoRA:
    """QLoRA for linears that share one quantized input (same act_mult / rotation /
    asymmetry as lins[0])."""
    lins = tuple(lins)
    base = lins[0]
    for lin in lins[1:]:
        if (lin.rot_block != base.rot_block or lin.act_asym != base.act_asym
                or (lin.act_mult is None) != (base.act_mult is None)
                or (lin.act_mult is not None and lin.act_mult is not base.act_mult
                    and not torch.equal(lin.act_mult, base.act_mult))):
            raise ValueError('LoRA group members must share one activation quantization spec')
    dq, ds = _int8_rows(_down_weight(lins))
    dsum = dq.float().sum(1) if base.act_asym else None
    ups, slices, c0 = [], [], 0
    for lin in lins:
        uq, us = _int8_rows(lin.lora_up.float())
        diff_b = getattr(lin, 'lora_diff_b', None)
        ups.append((uq, us, None if diff_b is None else diff_b.contiguous()))
        r = lin.lora_down.shape[0]
        slices.append((c0, c0 + r))
        c0 += r
    return QLoRA(dq, ds, dsum, tuple(ups), tuple(slices))


def down(q: QLoRA, xq, sx, ox=None, dtype=torch.bfloat16):
    """t [M, sum of ranks] for the quantized input of the group's first linear."""
    return qlinear.w8a8_gemm(xq, sx, q.dq, q.ds, None, ox=ox, wsum=q.dsum, out_dtype=dtype)


def up_add(q: QLoRA, t, i: int, out, gate=None, act: str = 'none'):
    """out (+)= [gate *] (t_i @ up_i^T + diff_b_i), in place (out [M, N], contiguous rows).

    gate None: out += lora. gate [N] / row table: out += gate * lora.
    act='gelu_tanh_post': out = GELU(out + lora) (the ffn.0 input of the student)."""
    c0, c1 = q.slices[i]
    uq, us, diff_b = q.ups[i]
    tq, st, _ = qlinear.quantize_activation(t[:, c0:c1])
    gdiv = 1
    if gate is not None:
        gate, gdiv = qlinear.row_table(gate, t.shape[0], uq.shape[0])
    return qlinear.w8a8_gemm(tq, st, uq, us, diff_b, residual=out, gate=gate, gate_div=gdiv, out=out, act=act)


def lora_into(q: QLoRA, xq, sx, ox, outs, gates=None, rows: Optional[slice] = None, act: str = 'none'):
    """Add the LoRA of each member of ``q`` (one shared quantized input xq) to its
    output ``outs[i]`` in place (``gates[i]`` optional; ``act`` as in up_add)."""
    if rows is not None:
        xq, sx = xq[rows], sx[rows]
        ox = None if ox is None else ox[rows]
    t = down(q, xq, sx, ox, dtype=outs[0].dtype)
    for i, out in enumerate(outs):
        up_add(q, t, i, out, None if gates is None else gates[i], act)
    del t

