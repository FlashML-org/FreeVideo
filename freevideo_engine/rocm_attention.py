"""BF16 inference attention with RDNA4 WMMA and FP32 online softmax.

Uses the standard Flash Attention recurrence, also described in Triton's
fused-attention tutorial. Windows and anchors remain owned by WindowAttention.
https://triton-lang.org/main/getting-started/tutorials/06-fused-attention.html
"""
import torch
import triton
import triton.language as tl


@triton.jit
def _flash(Q, K, V, OUT, NQ: tl.constexpr, NK: tl.constexpr,
           HEADS: tl.constexpr, D: tl.constexpr,
           Q0: tl.constexpr, Q1: tl.constexpr, Q2: tl.constexpr, Q3: tl.constexpr,
           K0: tl.constexpr, K1: tl.constexpr, K2: tl.constexpr, K3: tl.constexpr,
           V0: tl.constexpr, V1: tl.constexpr, V2: tl.constexpr, V3: tl.constexpr,
           SCALE: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr):
    rows = tl.program_id(0) * BM + tl.arange(0, BM)
    batch_head = tl.program_id(1)
    batch, head = batch_head // HEADS, batch_head % HEADS
    dimensions = tl.arange(0, D)
    columns = tl.arange(0, BN)
    query = tl.load(Q + batch * Q0 + rows[:, None] * Q1 + head * Q2
                    + dimensions[None, :] * Q3, rows[:, None] < NQ, other=0.)
    maximum = tl.full((BM,), float('-inf'), tl.float32)
    denominator = tl.full((BM,), 0., tl.float32)
    accumulator = tl.full((BM, D), 0., tl.float32)
    for start in range(tl.cdiv(NK, BN)):
        keys = start * BN + columns
        key = tl.load(K + batch * K0 + keys[None, :] * K1 + head * K2
                      + dimensions[:, None] * K3, keys[None, :] < NK, other=0.)
        scores = tl.dot(query, key) * (SCALE * 1.4426950408889634)
        scores = tl.where(keys[None, :] < NK, scores, float('-inf'))
        next_maximum = tl.maximum(maximum, tl.max(scores, 1))
        correction = tl.exp2(maximum - next_maximum)
        probabilities = tl.exp2(scores - next_maximum[:, None])
        denominator = denominator * correction + tl.sum(probabilities, 1)
        accumulator = accumulator * correction[:, None]
        value = tl.load(V + batch * V0 + keys[:, None] * V1 + head * V2
                        + dimensions[None, :] * V3, keys[:, None] < NK, other=0.)
        accumulator = tl.dot(probabilities.to(query.dtype), value, accumulator)
        maximum = next_maximum
    result = accumulator / denominator[:, None]
    tl.store(OUT + ((batch * NQ + rows[:, None]) * HEADS + head) * D
             + dimensions[None, :], result, rows[:, None] < NQ)


def attention(query, key, value, scale):
    """Non-causal NHD batches; every supplied key participates in softmax.

    Q/K/V stay BF16; no INT8/FP8 attention quantization or key selection.
    Reduction order differs from AOTriton, so model quality must be measured.
    """
    if (query.ndim != 4 or key.ndim != 4 or value.shape != key.shape
            or query.shape[0] != key.shape[0] or query.shape[2:] != key.shape[2:]
            or query.shape[-1] != 128 or not query.shape[1] or not key.shape[1]
            or not query.is_cuda or key.device != query.device or value.device != query.device
            or any(t.dtype != torch.bfloat16 for t in (query, key, value))):
        raise ValueError('RDNA4 attention requires nonempty BF16 NHD batches with head dimension 128')
    batch, rows, heads, dimension = query.shape
    block, warps = (128, 8) if rows >= 2048 else (64, 4)
    output = torch.empty_like(query, memory_format=torch.contiguous_format)
    _flash[(triton.cdiv(rows, block), batch * heads)](
        query, key, value, output, rows, key.shape[1], heads, dimension,
        *query.stride(), *key.stride(), *value.stride(), float(scale),
        BM=block, BN=64, num_warps=warps, num_stages=1,
        matrix_instr_nonkdim=16)
    return output


@triton.jit
def _indexed_flash(Q, K, V, QI, KI, OUT, QS: tl.constexpr, KS: tl.constexpr, NQ: tl.constexpr, NK: tl.constexpr,
           HEADS: tl.constexpr, D: tl.constexpr,
           Q0: tl.constexpr, Q1: tl.constexpr, Q2: tl.constexpr, Q3: tl.constexpr,
           K0: tl.constexpr, K1: tl.constexpr, K2: tl.constexpr, K3: tl.constexpr,
           V0: tl.constexpr, V1: tl.constexpr, V2: tl.constexpr, V3: tl.constexpr,
           SCALE: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr):
    rows = tl.program_id(0) * BM + tl.arange(0, BM)
    batch_head = tl.program_id(1)
    batch, head = batch_head // HEADS, batch_head % HEADS
    dimensions = tl.arange(0, D)
    columns = tl.arange(0, BN)
    query_indices = tl.load(QI + batch * QS + rows, rows < NQ, other=0)
    query = tl.load(Q + query_indices[:, None] * Q1 + head * Q2
                    + dimensions[None, :] * Q3, rows[:, None] < NQ, other=0.)
    maximum = tl.full((BM,), float('-inf'), tl.float32)
    denominator = tl.full((BM,), 0., tl.float32)
    accumulator = tl.full((BM, D), 0., tl.float32)
    for start in range(tl.cdiv(NK, BN)):
        keys = start * BN + columns
        key_indices = tl.load(KI + batch * KS + keys, keys < NK, other=0)
        key = tl.load(K + key_indices[None, :] * K1 + head * K2
                      + dimensions[:, None] * K3, keys[None, :] < NK, other=0.)
        scores = tl.dot(query, key) * (SCALE * 1.4426950408889634)
        scores = tl.where(keys[None, :] < NK, scores, float('-inf'))
        next_maximum = tl.maximum(maximum, tl.max(scores, 1))
        correction = tl.exp2(maximum - next_maximum)
        probabilities = tl.exp2(scores - next_maximum[:, None])
        denominator = denominator * correction + tl.sum(probabilities, 1)
        accumulator = accumulator * correction[:, None]
        value = tl.load(V + key_indices[:, None] * V1 + head * V2
                        + dimensions[None, :] * V3, keys[:, None] < NK, other=0.)
        accumulator = tl.dot(probabilities.to(query.dtype), value, accumulator)
        maximum = next_maximum
    result = accumulator / denominator[:, None]
    tl.store(OUT + ((batch * NQ + rows[:, None]) * HEADS + head) * D
             + dimensions[None, :], result, rows[:, None] < NQ)


def attention_indexed(q,k,v,rows,keys,scale):
    batch,nq=rows.shape;nk=keys.shape[1];heads,dimension=q.shape[1:]
    assert q.ndim==3 and k.shape==v.shape and q.shape==k.shape and dimension==128
    assert rows.stride(1)==keys.stride(1)==1 and q.dtype==k.dtype==v.dtype==torch.bfloat16
    block,warps=(128,8) if nq>=2048 else (64,4)
    output=torch.empty((batch,nq,heads,dimension),device=q.device,dtype=q.dtype)
    compiled=_indexed_flash[(triton.cdiv(nq,block),batch*heads)](q,k,v,rows,keys,output,rows.stride(0),keys.stride(0),nq,nk,heads,dimension,0,*q.stride(),0,*k.stride(),0,*v.stride(),float(scale),BM=block,BN=64,num_warps=warps,num_stages=1,matrix_instr_nonkdim=16)
    return output
