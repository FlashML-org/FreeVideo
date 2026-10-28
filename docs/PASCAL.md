# FreeVideo-Pascal

Community fork of [FlashML-org/FreeVideo](https://github.com/FlashML-org/FreeVideo) for **NVIDIA Pascal** GPUs. Not an official FlashVideo/FlashML release. No installer or GitHub Release has been published yet.

## Supported hardware (target)

| GPU | Compute | Status |
| --- | --- | --- |
| GTX 1060 / 1070 / **1070 Ti** / 1080 / 1080 Ti | 6.1 (1060 also 6.1; 1080 Ti 6.1) | T2V 1 s / 2 s, I2V first, I2V first+last e2e on a GTX 1070 Ti 8 GB |
| GTX 1060 6 GB (6.1) | 6.1 | Same kernels; tighter VRAM |
| Maxwell (GTX 900, SM52) | 5.2 | Still refused |

Volta (7.0) and Turing (7.5) are admitted by the same SM60+ floor but are not the focus of this fork.

## What already changed vs upstream

- `Hardware.cuda_compatibility()` admits **SM60+**. The SM80 / native-BF16 installer stop is gone.
- Linear math on Pascal uses **PyTorch dequant + matmul** (FP8 weight-only and ConvRot int8). Triton is not required.
- Kernel probes and the generate path use **FP16** activations below SM80 (FP32 accumulate). Residual / norms / adaLN / softmax stay FP32 where the engine already did.
- Attention auto-select prefers **PyTorch SDPA memory-efficient** (`torch-sdpa`). Sage2 / Flash / FA4 are not used.
- LoRA `up_add`, ConvRot int8, FP8 weight-only, and the text-encoder int8 decode have PyTorch fallbacks below SM80.
- `torch.compile` fused blocks and VDN inference kernels stay **off** on Pascal / Volta / Turing. DeltaNet uses the eager inverse (Newton–Schulz when the batch is large). 8 GB Windows plans no longer request `residual_offload` (that path needs fused kernels). Comfy/studio defaults on this fork are 448×320 / 2 s. Duration can be 1.625 s; 5 s is only the default **part length** when splitting a long video. Landscape / portrait / square chips list Pascal W×H. **Keep this preview** finishes after the current sampling step and decodes a video with audio. **Extend / next part** needs a full two-pass T2V (Preview first off); a Preview or Draft clip has no continuation tail. Videos made in parts force Preview first off.
- Installer pins are **cu126** (`sm_61`). Never install a cu130 torch on this fork.Measured on GTX 1070 Ti (2026-10-13): weight-only RMSE 0.0002 (limit 0.01); int8 rotation 0.007 (limit 0.03); int8 matmul exact.

## Proof-of-Concept Generations (Verified Runs)

The following benchmark generations demonstrate end-to-end functionality, prompt comprehension, visual fidelity, and spatial stability on Pascal (GTX 1070 Ti 8GB):

| Category | Archetype / Scene | Tiers Tested | Canvas Size | Key Visual Elements | Status |
| :--- | :--- | :---: | :---: | :--- | :---: |
| **Domestic Animal** | Ginger Tabby Cat in Sunlight | 1s, 2s, 5s, 10s | 448×256 & 320×256 | Tabby cat stretching on oak floor, playing piano keys with paws; realistic fur lighting and acoustic piano sync. | **VERIFIED** |
| **Human / Cyberpunk** | Cyberpunk Detective | 2s, 5s, 10s | 448×256 | Trenchcoated cyborg under torrential neon rain in Neo-Shinjuku; optical lens zoom analyzing floating holographic evidence. | **VERIFIED** |
| **Mythical Creature** | Phoenix Solar Rebirth | 2s, 5s, 10s | 448×256 | Golden-crested firebird igniting amidst ancient temple ruins, rising reborn with incandescent feathers and celestial sparks. | **VERIFIED** |
| **Giant Creature** | Ancient Moss Colossus | 2s, 5s, 10s | 448×256 | Giant moss-covered stone titan with glowing cyan rune eyes striding through misty redwood forest canopy. | **VERIFIED** |
| **Alien Entity** | Deep Cavern Bioluminescent | 2s, 5s, 10s | 448×256 | Translucent deep-abyss entity gliding through crystal caverns with pulsing cyan-magenta chromatophores. | **VERIFIED** |
| **Undead Horror** | Zombie Apocalypse Encounter | 2s, 5s, 10s | 448×256 | Decaying undead horde emerging through swirling dusk fog on an overgrown, abandoned highway. | **VERIFIED** |
| **Multi-Scene Story** | Female Malfoy: Battle of Hogwarts | 10 scenes (42.3s total) | 448×256 | 10-beat narrative film with variable pacing (2.8s–5.5s), multi-shot cuts (`[Shot 1]`, `[Shot 2]`), Global Story Bible, 2-pass latent refinement, and 0.3s audio crossfades. | **VERIFIED** |

---

## Purpose & Community Mission Statement

### The Pascal Compatibility Gap
Official upstream [FreeVideo](https://github.com/FlashML-org/FreeVideo) explicitly mandates modern NVIDIA GPUs featuring native BF16 arithmetic (Compute Capability SM80+: Ampere RTX 30-series, Ada Lovelace RTX 40-series, and Blackwell). Legacy Pascal hardware (GTX 1060, 1070, 1070 Ti, 1080, 1080 Ti; Compute 6.0 / 6.1) is hard-coded to halt during pre-flight environment checks or fails due to unsupported native BF16 instructions and missing FlashAttention/SageAttention2 kernels.

### Community Proof of Concept
**FreeVideo-Pascal** is an independent, non-commercial community project created solely as a **Proof of Concept (PoC)** to demonstrate that MiniMax H3 and Video DeltaNet (VDN) inference can successfully run on Pascal GPUs within an 8 GB VRAM budget.

- **Independent Community Initiative**: This repository is completely detached from, unaffiliated with, and not endorsed by FlashML, MiniMax, or any commercial vendor. It operates with zero financial incentives, sponsorship, donations, or monetization.
- **Empowering Existing Hardware**: In today's hardware market, upgrading GPUs is economically prohibitive for many indie developers, researchers, students, and hobbyists. This project exists to empower creators to generate local AI video on the hardware they already own.
- **Upstream Collaboration & AI Contribution Policy**: This codebase was developed collaboratively with AI assistance. Recognizing that many major open-source repositories maintain policies against accepting direct AI-generated pull requests, this fork serves as an open testbed. The original creators and maintainers of upstream FreeVideo are warmly invited to review, inspect, adapt, or integrate any of the compatibility patches or features into the official codebase if they wish.

---

## Architectural Refactoring Breakdown (Pascal GPU Support)

The following core engine components were refactored to enable Pascal execution:

| Source File | Nature of Changes / Pascal Adaptation |
| :--- | :--- |
| `freevideo_engine/hardware.py` | Lowers the CUDA compute compatibility floor from SM80 (`sm_80`) down to SM60+ (`sm_60`, `sm_61`). Removes the hardcoded native-BF16 blocker and adjusts capability reporting. |
| `freevideo_engine/attention.py` | Implements an automatic kernel selection fallback that routes attention through PyTorch SDPA memory-efficient (`torch-sdpa`) whenever compute capability is below SM80, eliminating dependencies on SageAttention2 and FlashAttention. |
| `freevideo_engine/backends/cuda_attention.py` | Disables fused Triton attention kernels for pre-SM80 cards and routes QKV computation through eager PyTorch operations. |
| `freevideo_engine/weights.py` & `freevideo_engine/weight_only.py` | Adds PyTorch-native dequantization + matmul fallbacks for FP8 weight-only tensors, avoiding unsupported hardware tensor cores. |
| `freevideo_engine/int8_ops.py` | Implements PyTorch fallbacks for ConvRot int8 linear layers without requiring Triton JIT compilation on Windows/Pascal. |
| `freevideo_engine/environments.py` & `freevideo_engine/torch_downloads.json` | Reconfigures PyTorch installation pins to `cu126` (`sm_61` binary wheels), preventing incompatible `cu130` torch installations. |
| `freevideo_engine/generate.py` | Enforces FP16 activation pipelines with FP32 accumulation under SM80; enables eager Newton-Schulz DeltaNet inversion for memory safety. |
| `freevideo_engine/vae_tiles.py` | Implements chunked spatial/temporal tiled VAE decoding with aggressive VRAM offloading to keep peak memory strictly under 8 GB. |

---

## New Features Added & Associated Files

Beyond backward compatibility, several high-impact creative workflow features were engineered:

| Feature | Primary Files Involved | Description |
| :--- | :--- | :--- |
| **Multi-Scene Storyboard & Story Bible** | `web/studio_scenes.js`, `web/studio.js`, `freevideo_engine/chain_plan.py` | Enables chaining multiple narrative clips with variable durations (1.6s–60s). Automatically merges the primary prompt as a **Global Story Bible** across all scenes to ensure consistent environment, lighting, character anchors, and spatial staging. |
| **Storyboard Review Card & Continuation Flow** | `web/studio_review.js`, `web/studio_chain.js`, `freevideo_engine/comfy_chain.py` | Interactive review modal after each scene render, allowing creators to **Accept**, **Retry**, or **Edit Next Prompt** before continuing the narrative chain. |
| **Adaptive Hardware Profiler & Dynamic ETA** | `scripts/adaptive_profiler.py`, `scripts/generate_malfoid_story.py` | Empirically profiles the host GPU (tracking base seconds per video second), dynamically adjusts render timeouts with a 2.5x safety margin, and calculates real-time ETA countdowns. |
| **Community LoRA Host Path Indexing** | `web/studio.js`, `docs/LoRA.md`, `docs/LoRA.zh-CN.md` | Standardizes LoRA loading directly from the host `ComfyUI/models/loras/` directory with interactive stack reordering and weight sliders. |
| **Narrative Story Orchestrator & Crossfader** | `scripts/generate_malfoid_story.py`, `scripts/malfoid_story_specs.py`, `malfoid.bat` | Coordinates multi-shot narrative pipelines with full 2-pass quality refinement (`preview: False`) and automatic 0.3s audio crossfade stitching via FFmpeg. |
| **Automated Model Verification & Downloader** | `Full_Downloads.bat`, `scripts/download_models.py` | Validates Hugging Face model endpoints via HTTP HEAD and provides mirror alternatives (`hf-mirror.com`) for restricted regions. |
| **Full Standalone Installer & Smart Pause** | `Full_Install.bat`, `scripts/build_windows.py` | One-click installation script with unified dependency alignment and non-silent error catching. |

---

## AI Development Transparency & Metadata

In the spirit of complete technical transparency, this project was pair-programmed using **Antigravity IDE**:

- **Development Environment**: Antigravity IDE
- **Primary AI Models**:
  - **Gemini 3.8 Flash**: Multi-tier pipeline specifications, rapid tool execution, batch orchestration, and structural documentation.
  - **Claude Opus 5.5**: Mathematical kernel fallback analysis, PyTorch dequantization design, and VRAM budget verification.
- **Development Timeframe**: ~2 weeks (October 2026).
- **Estimated Token Volume**: ~3.8 Million tokens across planning, code edits, compiler verification, and test logs.
- **Estimated Tool & API Cost**: ~$22.00 USD (equivalent compute cost; provided purely for statistical transparency).

---

## Completed Verification Milestones

1. **One end-to-end 1s T2V with audio on 1070 Ti**: Done 2026-10-14 (320×256, 39 frames, AAC).
2. **I2V First-Frame & First+Last Frame**: Done 2026-10-14 (448×256 and 320×256).
3. **Text-Encoder + VAE on 8 GB**: Encoder ~270s; VAE video 66s + audio 11s via eager tiled decode.
4. **PyTorch cu126 Wheel Configuration**: Fully verified cp312 pins for Windows.
5. **Local Windows Launcher Build**: Verified standalone launcher (`FreeVideo.exe`).

## License
Apache 2.0, identical to upstream FreeVideo. Model weights remain licensed under the MiniMax H3 Community License.

## Autopilot (Local LLM / LM Studio)
Autopilot submits FreeVideo jobs (prompt, size, duration, references, cast). It does not author ComfyUI workflow JSON.

### Recommended Models for 8 GB Pascal
| Model | GGUF | Notes |
| :--- | :---: | :--- |
| **Qwen2.5-7B-Instruct** | `Q4_K_M` (~4.4 GB) | Recommended for tool calling and JSON formatting. |
| **Gemma 3 4B Instruct** | `Q4_K_M` (~3.2 GB) | Efficient for visual analysis and prompts. |

### Soundtrack Polish (Automatic)
Every new decode executes an automatic CPU-side audio cleanup before MP4 muxing: DC/rumble high-pass (~80 Hz), 50/60 Hz mains hum notch filter, vocal presence EQ, and normalization toward −20 dBFS (−1 dB ceiling). Set `FREEVIDEO_AUDIO_POLISH=0` to preserve raw VAE decode.

### Spoken Dialog Estimation
The Studio **Spoken dialog estimate** calculates timing at ~2.4 words/second plus a staging beat. `<d>...</d>` tags in prompts are automatically measured.
