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
| [Lucide](https://lucide.dev) icons (image editor: eyedropper, hand, crop) | [ISC](freevideo_engine/licenses/Lucide-ISC.txt) |
| [Noto Sans CJK SC and Noto Sans Mono CJK SC](https://github.com/notofonts/noto-cjk) 2.004, © 2014-2021 Adobe: subsets renamed FreeVideo Sans SC and FreeVideo Mono SC (image editor text, share card, Linux launcher) | [SIL OFL 1.1](freevideo_engine/licenses/Noto-CJK-Sans-OFL.txt) |
| [Noto Serif CJK SC](https://github.com/notofonts/noto-cjk) 2.003, © 2017-2024 Adobe: subset renamed FreeVideo Serif SC (image editor text) | [SIL OFL 1.1](freevideo_engine/licenses/Noto-CJK-Serif-OFL.txt) |
| [LXGW WenKai](https://github.com/lxgw/LxgwWenKai) v1.522, © 2021-2026 LXGW, © 2020 The Klee Project Authors: subset renamed FreeVideo Kai SC (image editor text) | [SIL OFL 1.1](freevideo_engine/licenses/LXGW-WenKai-OFL.txt) |

Original launcher license texts are included in Windows packages. Other runtime dependencies are listed in [constraints](constraints), with their license information in the installed packages.

The fonts in `web/fonts/` are built by `scripts/build_web_fonts.py` from the pinned sources above; each file keeps its original copyright notice and licence in its name table. `FreeVideoBox.woff2` is FreeVideo's own.

`decode_stream.py`, `lora_cache.py` and `reference_sampler.py` include adaptations of upstream code; their source headers retain the attribution.

`prompt_vlm/core.py` adapts FreeToken's merged MLP/QKV and bounded vision operators from revision
`555efd89447232a555e05d187256b76a51c1aaa0`. Its header describes the changes and integration boundary.
