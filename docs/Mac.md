# Mac (experimental)

English · [中文](Mac.zh-CN.md)

FreeVideo runs MiniMax H3 locally on Apple silicon Macs with macOS 14 or later and the Xcode Command Line Tools. Mac support is experimental and still being optimized.

## Installation

1. [Download FreeVideo-Mac-arm64.dmg](https://github.com/FlashML-org/FreeVideo/releases/download/macos-preview/FreeVideo-Mac-arm64.dmg) and drag FreeVideo into Applications.
2. Open FreeVideo. If macOS blocks it, click **Open Anyway** in **System Settings → Privacy & Security**.
3. Choose an install location, optionally add existing model folders, and click **Install & launch**. The Mac environment and missing models are downloaded automatically.

## Features

- Computation runs on the GPU through Metal; attention in sampling and video decoding uses MLX's fused kernels.
- Before each stage, attention head groups, chunk sizes and the number of resident transformer layers are set from the unified memory available at that moment.
- The remaining layers stream from disk with read-ahead, and FP8 weights are decoded on the GPU.
- Text prompts, first-frame input and two-pass sampling are supported.

## Generation time

On an M5 with 24 GB of unified memory and about 14 GB available:

| Resolution | Length | Time |
| --- | --- | --- |
| 1344 × 768 | 10 s | about 46 min |
| 960 × 544 | 10 s | about 25 min |
| 512 × 512 | 1.6 s | about 3 min |

Mac support is still being optimized; more unified memory and newer chips shorten generation further.
