# Block-sparse attention (BSA) Triton kernels and IVPQ dynamic block shapes
# from Prism (MIT, Tencent), vendored from Prism-fast 3910631
# (hymm/models/modules/block_sparse_attention). The training-free bias
# rectification variants are not vendored; see ../NOTICE.
from .bsa_interface import flash_attn_bsa_3d, flash_attn_bsa_cross  # noqa: F401
