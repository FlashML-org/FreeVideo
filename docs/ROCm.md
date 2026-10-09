# Radeon AI PRO R9700 on Linux

The ROCm runtime path is validated on R9700 (`gfx1201`, 32 GB). It uses native
FP8 model projections, BF16 window attention, FP32 audio decoding, and the
original 8-step base plus 3-step refinement schedule.

## Measured generation time

1344×768, 243 frames at 24 fps (10.125 s), seed 2026090901. The first pass is
672×384, followed by learned latent upscale and three refinement steps.

| Native-worker stage | Mean seconds |
|---|---:|
| Model load | 2.97 |
| Base, 8 steps | 101.26 |
| Latent upscale | 3.54 |
| Refinement, 3 steps | 169.20 |
| Video/audio decode, encode and artifact save | 56.41 |
| Other worker overhead | 1.31 |
| **Complete worker** | **334.69** |

Two fresh-process runs took 335.455 s and 333.918 s with shared disk caches and
cached text conditioning; text encoding is outside this timer. Their video/audio
latents, RGB frames, audio and audio-decoder inputs were bitwise identical.
These are descriptive measurements of the validated BF16 attention / native
FP8 FFN composition, not a steady-state performance guarantee. Machine-readable
settings and timings are in [the benchmark receipt](../benchmarks/rocm/r9700-1344x768-8plus3.json).

After porting that composition onto upstream `a2cf304`, the first complete run
took 357.470 s including JIT compilation, and a new-process run with warmed disk
caches took **336.892 s**. Both ported runs were bitwise identical to the
archived 334.69 s composition for all five output artifacts above. The warm run
is 0.66% above the archived mean; this is a single repeat, not a performance
regression or speedup significance test.

The [published Windows RTX 5090 result](https://github.com/FlashML-org/FreeVideo/blob/7c3536a999e23ba0e6404b085cbdf42718d435d5/docs/zh-CN/execution-planning.md#端到端)
is 122 s at 1344×768 for a requested 10-second two-pass video. That table does
not specify step counts or text-encoding timing. Its host has 64 GB RAM;
the R9700 request used an approximately 451 GiB RAM budget. This is a published
reference, rather than a matched hardware comparison.

R9700 is slower in these results. Its
[640 GB/s peak memory bandwidth](https://www.amd.com/en/products/graphics/workstations/radeon-ai-pro/ai-9000-series/amd-radeon-ai-pro-r9700.html)
is 35.7% of the
[RTX 5090's 1792 GB/s](https://www.nvidia.com/en-us/geforce/graphics-cards/compare/):
a **2.8× bandwidth gap, approximately 3×**. This is a substantial hardware
constraint for bandwidth-sensitive operations. Peak bandwidth alone does not
prove the cause of the complete runtime gap; matrix throughput and kernel
implementation also matter.

## Run in an existing ROCm environment

The tested stack is PyTorch 2.12.0+rocm10.0.0, HIP 7.15.26333, Triton 3.8.0
and Python 3.12. Keep the ROCm Torch/Triton packages when installing the other
[runtime dependencies](../scripts/amd/requirements-runtime.txt). Use an existing
FreeVideo runtime with the H3 model and prepared FP8 weights.

```bash
export FREEVIDEO_ROCM_SPATIAL_CONV=triton
export FREEVIDEO_ROCM_ATTENTION=triton-window
export FREEVIDEO_ROCM_AUDIO_CONV=native
export FREEVIDEO_ROCM_VIDEO_BLAS=cublaslt
export FREEVIDEO_ROCM_FFN=tail-preserving
./freevideo generate --prompt-file prompt.txt --out video.mp4
```

`triton-window` retains BF16 Q/K/V and FP32 softmax, using indexed loads for
large windows. `tail-preserving` coalesces full FFN rows to 8192 while retaining
the reference 2048-row tail and global FP8 activation scales. Smaller chunks
and recomputation use the standard bounded implementation. Effective FFN
chunks are recorded in the sampling metrics. `cublaslt` is PyTorch's alias
for hipBLASLt on ROCm and applies only to video decoding.

## Optional container and cached-conditioning benchmark

Pull the pinned image once:

```bash
docker pull rocm/pytorch@sha256:55bf8baa2a513b1c05bd256119fbc57a6ca64170e6cf5fe519b1cdd0c458cfd9
export FV_STORAGE_ROOT=/path/to/runtime-and-model-storage
export FV_RENDER_DEVICE=/dev/dri/renderD128  # Set this to your R9700 render node.
scripts/amd/run_rocm_fast.sh -m freevideo_engine.cli --help
```

The storage layout uses `models/vdn` and `experiments/freevideo-r9700` for
runtime, caches and dependencies; the checkout is mounted read-only at
`/workspace`, and storage at `/data`. The GPU UUID is read from the chosen
render node; `FV_GPU_UUID` and `FV_ROCM_IMAGE` can override detection/image.

To measure a saved worker request with cached conditioning, use new output
directories and update the request's model/conditioning paths for your storage:

```bash
scripts/amd/run_rocm_fast.sh /workspace/scripts/amd/benchmark.py \
  --request /data/request.json --out /data/outputs/r9700-run-00
```

The default environment variables retain standard ROCm operators. The fast
route is opt-in and checked for `gfx1201`. The INT8 QK / FP8 PV attention
research route is outside this change.
