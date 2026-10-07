# Prism model code (vendored)

Inference-only code for FreeVideo's Prism (preview) backend: the MOVA dual-tower
video+audio DiT as fine-tuned by Prism, with the training-free speedups of the
Prism single-GPU research branch (Prism-fast `3910631`, kernels synced to `0befcb7`). Licenses: see `NOTICE`.

| File | Origin | FreeVideo changes |
| --- | --- | --- |
| `wan_video_dit.py` | `hymm/models/modules/wan_video_dit.py` | no SP / FSDP / training paths or guidance variants; FA2 or SDPA dense attention; lazy `torch.compile`; in-place RoPE option |
| `wan_audio_dit.py`, `interactionv2.py`, `mova.py` | `hymm/models/modules/` | inference only, no SP |
| `dac_vae.py` | `hymm/models/modules/dac_vae.py` | decoder of the continuous DAC only (no audiotools) |
| `flow_match_pair.py` | `hymm/diffusion/schedulers/` | no diffusers config mixins |
| `block_sparse_attention/` | `hymm/models/modules/block_sparse_attention/` | bias rectification not vendored; SP average is a no-op |
| `qlinear.py`, `qblock.py`, `sage_bsa.py`, `rope.py`, `sage_tune.py` | `hymm/fast/` (0befcb7) | import paths; no sequence parallel; bias rectification hooks removed |
| `qlinear.py` (FreeVideo) | | `act='gelu_tanh_post'`: GELU after the residual add in the W8A8 epilogue (INT8 LoRA up into `ffn.0`) |
| `sage_bsa_f16acc.py` | Prism-fast `hymm/fast/sage_bsa_f16acc.py` (same file) | FP8 PV with FP16 accumulation (`pv='fp8f16'`, SageAttention2++ style) for RTX 40/50; routed by `install()` only when `PRISM_SAGE_F16ACC` is set (`1`: any supported GPU, `auto`: SM89/SM12x with Triton >= 3.7) |
| `qlora.py` | FreeVideo | the student's distill LoRA on the INT8 GEMM (down on the base GEMM's quantized input, INT8 up in the residual epilogue); `FREEVIDEO_PRISM_LORA_INT8=1`, default off |
| `ivpq_fast.py` | `hymm/fast/ivpq_fast.py` (0befcb7) | import paths; deterministic selection by default (Triton tile means with a fixed order, block scores one head at a time), so the head chunk does not change the latents; `PRISM_BSA_DETERMINISTIC=0` restores the research path |
| `audio_graph.py` | `hymm/fast/audio_graph.py` (0befcb7) | import paths; `KVTable.bind_one` for a cache filled layer by layer; the timed split-KV launch choice is kept on disk (`PRISM_AUDIO_TUNE_CACHE`) |
| `fast_vae.py` | the official-VAE speedups of `hymm/fast/vae.py` (0befcb7) | that section only |
| `sampling.py` | `hymm/fast/pipeline.py`, `audio_regen.py`, `mova_pipeline.py` | per-unit execution for streaming; lean low-VRAM path (bit-identical INT8 latents); student = base + unmerged distill LoRA (bf16 branch, or INT8 via `qlora` with `FREEVIDEO_PRISM_LORA_INT8=1`); audio teacher (base-pass v2a K/V cache on GPU or pinned host, audio-only sub-steps); tiled Wan VAE decode for any diffusers; plain bf16 block path for bf16 weights |

Nothing here imports deepspeed, yunchang, audiotools or a kernel hub. Kernels
are Triton (INT8/FP8 GEMMs, Sage-quantized block-sparse attention, fused norms).
