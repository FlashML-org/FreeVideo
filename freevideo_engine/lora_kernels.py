"""LoRA up projection and addition without a second full output matrix."""
import triton
import triton.language as tl


@triton.jit
def _up_add(Z, B, Y, M: tl.constexpr, N: tl.constexpr, R: tl.constexpr,
            SCALE: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr, BR: tl.constexpr):
    m = tl.program_id(0) * BM + tl.arange(0, BM)
    n = tl.program_id(1) * BN + tl.arange(0, BN)
    r = tl.arange(0, BR)
    z = tl.load(Z + m[:, None] * R + r[None, :],
                (m[:, None] < M) & (r[None, :] < R), other=0.)
    b = tl.load(B + n[None, :] * R + r[:, None],
                (n[None, :] < N) & (r[:, None] < R), other=0.)
    delta = tl.dot(z, b).to(Y.dtype.element_ty).to(tl.float32)
    old = tl.load(Y + m[:, None] * N + n[None, :],
                  (m[:, None] < M) & (n[None, :] < N), other=0.).to(tl.float32)
    tl.store(Y + m[:, None] * N + n[None, :], old + SCALE * delta,
             (m[:, None] < M) & (n[None, :] < N))


def up_add(z, b, output, scale):
    m, n = output.shape
    _up_add[(triton.cdiv(m, 32), triton.cdiv(n, 128))](
        z, b, output, m, n, z.shape[1], scale, 32, 128,
        max(32, triton.next_power_of_2(z.shape[1])), num_warps=4)
    return output
