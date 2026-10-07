# Prism (preview)

English · [中文](Prism.zh-CN.md)

> **Read first:** Prism is an early-access preview and **may occasionally have audio problems**, so choose it with care. Its current accelerated version may be slower and lower in quality than MiniMax H3, whose ecosystem is mature. **We recommend MiniMax H3.** Before trying Prism, [watch the comparison](https://freevideo-community.pages.dev/prism/) first.

## What it is

[Prism](https://huggingface.co/FrancisRing/Prism) is an open joint video and audio generation model from Fudan University, Tencent Hunyuan and Zhejiang University, released by Tencent under the MIT license. It builds on [MOVA](https://huggingface.co/OpenMOSS-Team/MOVA-360p) (OpenMOSS) and the [Wan2.2](https://github.com/Wan-Video/Wan2.2) video model, both Apache-2.0. Prism animates a first frame into a video with matching sound: speech, sound effects and music.

FreeVideo runs Prism locally as a preview, next to MiniMax H3. MiniMax H3 remains the recommended model and covers every input; Prism is offered for image-to-video with audio at 720p. The preview is still being optimized, and its settings and speed may change between releases.

## Installation and selection

Prism is optional. Nothing is downloaded for it unless you choose it.

- **Windows and the launcher:** on the **Models** step, check **Prism · Preview** and keep **MiniMax H3 · Recommended** checked. The card shows the download size. Then continue as usual.
- **Existing ComfyUI:** install the node from the Prism branch (`git clone -b prism-beta https://github.com/FlashML-org/FreeVideo.git` in `ComfyUI/custom_nodes`), open FreeVideo **Settings**, check **Prism** under **Models** (keep **MiniMax H3** checked), then click **Detect & review** and **Install / repair**.
- **Linux:** `git clone -b prism-beta https://github.com/FlashML-org/FreeVideo.git && cd FreeVideo`, then `./setup.sh --video-models h3,prism` installs both; `--video-models prism` installs Prism alone on a new installation, and an installed MiniMax H3 is always kept. Without the option, an existing installation keeps its models and a new one installs MiniMax H3 only.

**Updating a clone:** the `prism-beta` branch is replaced whenever the preview changes, so `git pull` cannot update it. Run `git fetch origin prism-beta && git reset --hard origin/prism-beta` in the clone instead (this discards your own edits there), then restart ComfyUI or rerun `./setup.sh`.

**Updates:** the Prism test build of FreeVideo.exe is experimental. It updates itself through the usual update notice from the rolling [`prism-preview` prerelease](https://github.com/FlashML-org/FreeVideo/releases/tag/prism-preview), which is refreshed whenever the test build changes, and never from the regular releases, so updating keeps Prism.

> **Using the first test build?** It follows the regular releases, which do not include Prism, so the update it offers removes Prism. Download [FreeVideo.exe](https://github.com/FlashML-org/FreeVideo/releases/download/prism-preview/FreeVideo.exe) once more from the [Prism page](https://freevideo-community.pages.dev/prism/) to get Prism updates instead.

Prism needs an NVIDIA RTX 30 series or newer GPU with at least 12 GiB of VRAM and 20 GB of RAM; 16 GiB and 32 GB are recommended. Every card downloads the same INT8 weights. **Original** additionally uses the original bf16 weights, an optional download; without it, Original runs on the INT8 weights and says so. Prism is not available on Mac, and 8 GiB cards cannot yet hold a 720p, 8.5-second video.

| | Download | Disk |
| --- | --- | --- |
| Prism, INT8 (all levels) | 46.6 GiB | 46.6 GiB |
| Original bf16 weights for the Original level (optional) | 60.8 GiB | 60.8 GiB |

To generate, choose **Prism** under **Model** in the creative workspace (or set **Model** to **Prism (preview)** on the FreeVideo node), add a first frame and click **Generate video**.

## Supported inputs

| Input | MiniMax H3 | Prism (preview) |
| --- | --- | --- |
| Text prompt | Yes | Yes |
| First frame | Optional | Required on **Light**, **Standard** and **High**; optional (experimental) on **Max** and **Original** |
| Last frame | Yes | No |
| Image, video and audio references | Experimental | No |
| LoRAs | Yes | No |

The official Prism setting is **1280 × 720 for 205 frames (8.5 seconds) at 24 fps**, and FreeVideo starts there when you switch to Prism. Other sizes and lengths can be set and are marked experimental. Lengths round up to the next 4n + 1 frames (8.5 s becomes 205 frames, 8.54 s), and sizes use multiples of 16 pixels.

Describe the picture, the motion and the sound in the prompt, in English or Chinese. Mark what should be heard with tags, as in Prism's own examples:

- `<speech>…</speech>`: the exact words a person says. Chinese prompts with Chinese speech produce Mandarin.
- `<sfx>…</sfx>`: sound effects and ambience, for example `<sfx>waves and seagulls</sfx>`.
- `<music>…</music>`: music, for example `<music>a string quartet playing in a stone church</music>`.
- `<text>…</text>`: text that appears on screen.

Without a first frame (text-to-video) Prism starts from an empty frame. This is outside what Prism was trained for, so it is experimental and only offered on the undistilled levels. It is offered on **Max** and **Original**, where the picture is usable; the sound is often faint, and quiet ambience such as wind or birdsong can come out almost silent (−55 LUFS in our tests, against about −16 with a first frame). The distilled levels (**Light**, **Standard**, **High**) give a blurred, grey picture without a first frame. For text-only prompts, MiniMax H3 is the better choice.

## Quality levels

Prism has its own five levels. **Light**, the fastest, is the default. Each samples once at the target size; higher levels take longer and come closer to the official Prism result. **Original** is the official recipe.

| Level | Sampling | Audio | Text-to-video | Time on one H200 (speed-up vs Original) |
| --- | --- | --- | --- | --- |
| Light (default) | 8 distilled steps, CFG 2 on the first step | the distilled pass's own features; 4 audio sub-steps per step, CFG 5 | No | 4.3 min (14.6×) |
| Standard | 8 distilled steps, CFG 2 on the first step | original-model features from layer 10 (layers 10–29); 4 audio sub-steps, CFG 5 | No | 6.2 min (10.3×) |
| High | 8 distilled steps, CFG 2 on the first step | original-model features on all 30 audio-linked layers; 4 audio sub-steps, CFG 5 | No | 6.8 min (9.3×) |
| Max | 20 steps, original weights, CFG 5 every step | joint | Experimental | 15.6 min (4.1×) |
| Original | 50 steps, original bf16 weights and exact attention (official) | joint | Experimental | 63 min (1×) |

Times are sampling and decoding of one 8.5-second 720p video, without model loading.

How close each level comes to the official result: the same prompt, first frame and seed, compared with Prism's own multi-GPU sampler. SSIM measures the picture (1 = identical); the log-mel distance measures the sound (lower is closer). Original on one GPU differs from the multi-GPU sampler only by rounding order (PSNR 36 dB on case 1); with INT8 weights and quantized attention it would be 31 dB, so Original keeps the original precision.

| Level | SSIM, case 1 / case 2 | Log-mel distance, case 1 / case 2 | Speech error rate, case 2 (Whisper) |
| --- | --- | --- | --- |
| Light | 0.833 / 0.795 | 1.05 / 2.13 | 0.09 |
| Standard | 0.833 / 0.795 | 0.98 / 2.14 | 0.09 |
| High | 0.833 / 0.795 | 0.93 / 2.11 | 0.09 |
| Max | 0.879 / 0.852 | 1.32 / 1.16 | 0.01 |
| Original | 0.960 / 0.934 | 0.60 / 0.46 | 0.03 |

The three distilled levels produce the same picture and differ only in how the sound is guided. The official sampler itself scores a speech error rate of 0.01 on case 2. The levels are defined in `freevideo_engine/prism_tiers.json`. Two-pass acceleration applies to MiniMax H3 only: for Prism, sampling a half-size draft first and refining it at 720p was 1.8× faster but visibly softer and lost speech, and at 640 × 352 the model breaks down.

## Acceleration

Prism's video model is two Wan2.2-A14B experts (14.3 B parameters each, one for high noise and one for low noise) joined to a 1.4 B audio model by a 2.7 B bridge: 32.7 B parameters in all, 18.4 B active per step. The official sampler runs 50 steps with guidance on every step (100 model passes) and takes about 76 minutes on one H200. What FreeVideo changes, with the speed-up each part gives on an H200 at 720p:

- **Few-step distillation (Light, Standard and High):** the LightX2V Wan2.2 image-to-video distillation (Apache-2.0) is applied as a rank-256 LoRA on top of the original weights: 8 steps instead of 50, and guidance only on the first step, so 9 model passes instead of 100.
- **Audio guidance (Light, Standard and High):** the distillation was trained for video alone; used directly, Prism's sound became faint and broken. At each step FreeVideo therefore caches what the audio attends to in the video (FP8, about 17 GB, in VRAM or RAM) and moves the audio 4 small steps with guidance (CFG 5) against that cache. Light fills the cache from the distilled pass itself, at no extra cost. High runs the original weights over the 30 layers that feed the audio to fill it (about 40% of High's time); Standard starts that pass at layer 10 and reuses the distilled features below it. The speech error rate on case 2 goes from 0.19 with the distillation alone to 0.09 on High; the video is unchanged. Cheaper teachers (sparser, lower resolution, every other step) made the audio audibly worse and were dropped.
- **Quantized weights:** linear layers run as INT8 on every GPU, with Triton matrix kernels, per-token activation scales and a 128-wide Hadamard rotation. Weight memory is halved; on an H200 a pass takes 9% less time. INT8 was chosen over FP8 because it stays closer to the original (31.0 against 29.3 dB on the official recipe) and because GeForce cards run FP8 with 32-bit accumulation at half the INT8 rate.
- **Faster sparse attention:** Prism skips 75% of the attention blocks and picks the rest from the content. FreeVideo computes the kept blocks with a quantized kernel in the style of SageAttention (INT8 Q·K, FP8 P·V) instead of the bf16 original: 413 ms instead of 894 ms per attention call at 720p (2.2×). Block selection, the padded tail and the rotary embedding are fused or skipped (block selection 44 → 28 ms, rotary embedding 52 → 4 ms per layer). The kernel is tuned once per GPU on first use.
- **Video and audio decoding:** a re-implemented Wan VAE decoder (same weights, 63 dB from the original output) decodes the 205 frames in 4.5 s instead of 16.4 s; the first-frame encoding takes 0.8 s instead of 5.8 s. The audio teacher's small steps run as one CUDA graph that reads the FP8 cache directly (121 ms instead of 194 ms each).
- **Bounded memory:** the high-noise and low-noise experts load one at a time. Layers that do not fit in VRAM stream from RAM or disk with prefetch, using the same placement policy as MiniMax H3, sized from the memory free at that moment. At 12 GiB the text encoder streams to the GPU one block at a time, the video model's residual stream waits in host memory during attention, and attention is processed one head at a time.
- **Separate encoding:** the text encoder and the first-frame encoding run in their own process and release their memory before the video model loads.

Altogether, one 8.5-second 720p video on an H200 takes about 4.3 minutes of sampling and decoding on **Light** and 6.8 on **High**, against 76 minutes for the official sampler (18× and 11×). The undistilled levels keep the official sampling and gain only from the kernels.

## Generation time and memory

1280 × 720, 205 frames (8.5 s), first frame and prompt, measured with FreeVideo on an H200 that was limited to the VRAM and RAM of smaller machines. The limits reproduce how memory is placed and streamed on those machines, not their compute speed; on a consumer card each step takes longer (see the estimate below). Times include model loading and decoding.

| VRAM | RAM | Level | Time | Peak VRAM | Peak RAM |
| --- | --- | --- | --- | --- | --- |
| 24 GiB | 64 GB | High | 8.2 min | 25.4 GB | 29.8 GiB |
| 16 GiB | 32 GB | High | 8.3 min | 16.9 GB | 29.1 GiB |
| 16 GiB | 24 GB | High | 8.4 min | 17.0 GB | 21.7 GiB |
| 12 GiB | 32 GB | High | 8.8 min | 12.7 GB | 29.2 GiB |
| 12 GiB | 20 GB | High | 10.7 min | 12.7 GB | 17.7 GiB |
| 12 GiB | 32 GB | Max | about 19 min | 12.6 GB | — |
| 16 GiB | 32 GB | Original (bf16) | about 70 min | 16.1 GB | 27.8 GiB |

Max and Original were measured for 4 steps and extended to their full step count. Below 32 GB of RAM, and on Windows at 32 GB too (Windows lets the GPU pin only about half of the RAM), part of the audio cache is kept in a file next to the output and read back each step; about 2 GB/s from an NVMe SSD keeps up. The same seed gives the same video at every limit above, except 12 GiB with 20 GB of RAM.

On a real RTX 4070 (12 GB, Windows), a whole Light video took 28 minutes: about 2.9 minutes per step (5.6 for the first, which runs two passes with guidance), half a minute to encode and under a minute to decode, within the card's 11 GB dedicated budget and without shared GPU memory. A whole High video took 50 minutes there (about 5 minutes per step: the audio teacher's base-weight pass adds 2).

The first video after installation also tunes the attention kernels for the GPU and prepares caches, once. The tuning usually takes 1 to 2 minutes (72 s on the RTX 4070) and stops after 3 at most.

## Licenses

Prism weights and code are MIT (Tencent). MOVA, Wan2.2 and the LightX2V distillation models are Apache-2.0. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). MiniMax H3 keeps its own license.
