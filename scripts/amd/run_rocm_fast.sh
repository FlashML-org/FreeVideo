#!/usr/bin/env bash
# Validated gfx1201 BF16 attention and native FP8 FFN; see docs/ROCm.md.
set -euo pipefail
fv_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export FV_ROCM_SPATIAL_CONV="${FV_ROCM_SPATIAL_CONV:-triton}"
export FV_ROCM_ATTENTION="${FV_ROCM_ATTENTION:-triton-window}"
export FV_ROCM_VIDEO_BLAS="${FV_ROCM_VIDEO_BLAS:-cublaslt}"
export FV_ROCM_AUDIO_CONV="${FV_ROCM_AUDIO_CONV:-native}"
export FV_ROCM_FFN="${FV_ROCM_FFN:-tail-preserving}"
exec bash "$fv_script_dir/run_rocm.sh" "$@"
