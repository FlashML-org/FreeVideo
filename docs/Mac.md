# Mac (experimental)

English · [中文](Mac.zh-CN.md)

FreeVideo runs MiniMax H3 locally on Apple silicon Macs with macOS 14 or later and the Xcode Command Line Tools. Mac support is experimental and still being optimized.

## Installation

1. [Download FreeVideo-Mac-arm64.dmg](https://github.com/FlashML-org/FreeVideo/releases/download/macos-preview/FreeVideo-Mac-arm64.dmg) and drag FreeVideo into Applications.
2. Open FreeVideo. The preview needs your approval the first time; see [First open](#first-open).
3. Choose an install location, optionally add existing model folders, and click **Install & launch**. The Mac environment and missing models are downloaded automatically.

## First open

The Mac preview isn't notarized by Apple yet, so macOS blocks it the first time and says it can't verify the developer. Before approving it, make sure the file is the official one:

1. Download it only from the [FreeVideo releases page](https://github.com/FlashML-org/FreeVideo/releases/tag/macos-preview).
2. In Terminal, run `shasum -a 256 ~/Downloads/FreeVideo-Mac-arm64.dmg`. The result should match the value in SHA256SUMS.txt on the release.
3. Open FreeVideo and dismiss the alert. Then, in **System Settings → Privacy & Security**, find FreeVideo, click **Open Anyway** and confirm.

This approves FreeVideo only. It doesn't turn off Gatekeeper or change your other security settings. If macOS says the file is damaged or contains malware, don't open it, and let us know. Once FreeVideo is notarized, this step goes away.

## Features

- Computation runs on the GPU through Metal; attention in sampling and video decoding uses MLX's fused kernels.
- Before each stage, attention head groups, chunk sizes and the number of resident transformer layers are set from the unified memory available at that moment.
- Macs download ConvRot int8 weights. On Apple M5 and newer, the large projections run as int8 on the GPU's Metal 4 tensor units; earlier Macs decode them to BF16 on the GPU as each layer loads.
- Layers that are not kept in memory stream from disk with read-ahead.
- Text prompts, first-frame input and two-pass sampling are supported.

## Generation time

On an M5 with 24 GB of unified memory and about 14 GB available:

| Resolution | Length | Time |
| --- | --- | --- |
| 1344 × 768 | 10 s | about 42 min |
| 960 × 544 | 10 s | about 22 min |
| 512 × 512 | 1.6 s | about 3 min |

These times use the M5's int8 tensor units. Macs before the M5 run the same weights in BF16 and take longer. Mac support is still being optimized; more unified memory and newer chips shorten generation further.
