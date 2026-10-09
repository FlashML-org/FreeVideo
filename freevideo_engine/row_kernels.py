"""Compile the row-wise fused block kernels once for every row count.

The fused pointwise kernels of a transformer block -- the RMSNorm + AdaLN
affine before each sub-layer, the gated residual add after it, the SwiGLU
activation, QK-norm + rope and the softmax gate -- work row by row. Upstream
compiles them with dynamic=False, so dynamo keeps one graph per row count, and
the row count is the request's video tokens plus the prompt's text tokens (and
an FF tile's remainder). Every new prompt length therefore compiles these
kernels again, and so does every new canvas. In a fresh process each of
those is an FX graph cache miss and a full compile:

  RTX 5060 Ti, Windows, 768x1344x56 two-pass, same prompt     154.3 s
  the same request with prompts of two other lengths          163.9, 167 s

Twelve graphs missed there. On Linux the same request with a new prompt
recompiled _pre_ref and _post_into_branch three times each and _qk_prep_body
and _swiglu_ref twice each, and nothing else; a new canvas missed those ten and
seven linear-attention graphs, which depend on the frame layout and stay
static here. A resident ComfyUI session keeps the graphs on one code object,
and from dynamo's recompile limit (eight) on a new length runs eager: _pre_ref
at 17478 rows took 2.76 ms a call instead of 0.26.

Only the row dimension is marked dynamic, on an alias of each row-indexed
argument so the caller's tensor carries no mark into any other compiled
function. Everything else -- hidden size, heads, head dimension, tables --
stays specialised as upstream compiles it. A row-wise kernel does the same
arithmetic on every row whatever their number; inductor's reduction over the
static hidden dimension is unchanged. Against the per-row-count graphs, on an
RTX PRO 6000 at 4400 to 73043 rows, all five kernels were bitwise identical
and ran at the same speed (pre 1.080 ms against 1.080 at 73043 rows, post
1.581 against 1.582). Complete two-pass requests produced the same latents
as before at 768x1344x56 and 1344x768x243 with int8, and at 768x1344x56 with
FP8 and with an online LoRA, and the same steady step times. A row count of
0 or 1 still specialises, as dynamo always does.
"""
import torch

_ROWS = '_freevideo_dynamic_rows'
# (residual, gate, indices, branch) of a gated residual add.
POST_ROWS = ((0, -2), (2, 0), (3, -2))


def dynamic_rows(compiled, rows):
    """Call `compiled` with the token dimension of some arguments dynamic.

    `rows` pairs an argument's position with its token dimension: the residual
    stream is [batch, tokens, hidden], so its tokens are dimension -2, while
    the AdaLN indices are [tokens]. Marking the batch dimension instead does
    nothing -- a size-1 dimension always specialises -- and the token symbol
    of the indices is then forced equal to the stream's fixed count.
    """
    mark = torch._dynamo.maybe_mark_dynamic

    def call(*args):
        args = list(args)
        for index, dim in rows:
            alias = args[index].view(args[index].shape)
            mark(alias, dim % alias.dim())
            args[index] = alias
        return compiled(*args)
    setattr(call, _ROWS, True)
    call.__wrapped__ = compiled
    return call


def install():
    """Put dynamic-row compiles of upstream's fused kernels in upstream's caches.

    Upstream looks each kernel up by name and compiles it on first use, so an
    entry placed here before the first call is the one every block uses.
    Idempotent; an entry from an earlier engine in this process is replaced
    once, which compiles the dynamic graph once.
    """
    from src.models.ops import fused_block
    from src.models.softmax_attention import kernels
    entries = (
        # (hidden, weight, eps, scale, shift, indices)
        (fused_block._CACHE, 'pre', fused_block._pre_ref, ((0, -2), (5, 0))),
        # (residual, gate, indices, branch)
        (fused_block._CACHE, 'post', fused_block._post_ref, POST_ROWS),
        # (projected [tokens, 2 * width])
        (fused_block._CACHE, 'swiglu', fused_block._swiglu_ref, ((0, 0),)),
        # (q or k [tokens, heads, head_dim], weight, eps, cos [tokens, rotary], sin)
        (kernels._QK_PREP_CACHE, 'fn', kernels._qk_prep_body, ((0, 0), (3, 0), (4, 0))),
        # (softmax output [tokens, heads, head_dim], gate [tokens, heads])
        (kernels._SOFTMAX_GATE_CACHE, 'fn', kernels._softmax_gate_body, ((0, 0), (1, 0))),
    )
    for cache, name, body, rows in entries:
        if not getattr(cache.get(name), _ROWS, False):
            cache[name] = dynamic_rows(torch.compile(body, dynamic=False), rows)
