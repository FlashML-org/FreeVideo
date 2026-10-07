# Third-party notices

FreeVideo code uses [Apache-2.0](LICENSE). Its dependencies and models retain their own licenses.

| Component | License |
| --- | --- |
| Qt, PySide6 and Shiboken6 | LGPL-3.0; [component notices](freevideo_engine/launcher/licenses/NOTICE.txt) |
| PyInstaller bootloader | GPL-2.0 with the bootloader exception |
| CPython / psutil | PSF / BSD-3-Clause |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI), including the H3 text encoder code | GPL-3.0 |
| [VDN-H3](https://github.com/OpenVDN/vdn-minimax-h3), [Diffusers](https://github.com/huggingface/diffusers), [SageAttention](https://github.com/thu-ml/SageAttention) | Apache-2.0 |
| [VDN-H3 and H3 model weights](https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE) | MiniMax H3 Community License |
| [MiniMax H3 latent upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler) | [MIT](freevideo_engine/licenses/latent-upscaler-MIT.txt) |
| [FreeToken](https://github.com/FlashML-org/FreeToken) prompt VLM operators, [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) optional weights | [Apache-2.0](freevideo_engine/licenses/FreeToken-Apache-2.0.txt) |
| [Prism](https://huggingface.co/FrancisRing/Prism) code and weights, Copyright (C) 2026 Tencent (optional Prism preview) | MIT |
| [MOVA](https://huggingface.co/OpenMOSS-Team/MOVA-360p) code and weights (OpenMOSS), as modified by Tencent in Prism | Apache-2.0 |
| [Wan2.2](https://github.com/Wan-Video/Wan2.2) model architecture and weights (Wan Team, Alibaba) | Apache-2.0 |
| [LightX2V Wan2.2 distill models](https://huggingface.co/lightx2v/Wan2.2-Distill-Models), stored with the prepared Prism weights as a rank-256 LoRA | Apache-2.0 |
| [Descript Audio Codec](https://github.com/descriptinc/descript-audio-codec) decoder, as adapted by MOVA | MIT |

Original launcher license texts are included in Windows packages. Other runtime dependencies are listed in [constraints](constraints), with their license information in the installed packages.

`decode_stream.py`, `lora_cache.py` and `reference_sampler.py` include adaptations of upstream code; their source headers retain the attribution.

`prompt_vlm/core.py` adapts FreeToken's merged MLP/QKV and bounded vision operators from revision
`555efd89447232a555e05d187256b76a51c1aaa0`. Its header describes the changes and integration boundary.

The Prism preview code in `freevideo_engine/prism_model/` is adapted from Prism (MIT, Tencent), MOVA (Apache-2.0, OpenMOSS) and Wan (Apache-2.0); its source headers retain the attribution. Its quantized block-sparse attention follows the approach of [SageAttention](https://github.com/thu-ml/SageAttention) (Apache-2.0). The prepared Prism weights are a derivative of the MIT and Apache-2.0 weights above and keep their licenses.
<!-- TODO(prism): confirm against freevideo_engine/prism_model/NOTICE once the engine is final. -->
