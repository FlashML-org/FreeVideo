# Third-party notices

FreeVideo code uses [Apache-2.0](LICENSE). Its dependencies and models retain their own licenses.

| Component | License |
| --- | --- |
| [Three.js](https://threejs.org/) r160, © 2010–2023 three.js authors (bundled whitebox renderer) | [MIT](web/vendor/three/LICENSE) |
| [Depth Anything V2 Small](https://github.com/DepthAnything/Depth-Anything-V2/tree/a561b849ebae10a6f5ef49e26c83cbbcd36c71bf), © 2024 Depth Anything authors; DINOv2 © Meta Platforms, Inc. and affiliates. Small-only DPT/DINOv2 inference and preprocessing are adapted and bundled; Small weights are an optional download pinned at `03876f8651c73a60fe4c2c48294e09fcb6838fcf`. | [Apache-2.0 (code and Small weights)](freevideo_engine/licenses/Depth-Anything-V2-Apache-2.0.txt) |
| Qt, PySide6 and Shiboken6 | LGPL-3.0; [component notices](freevideo_engine/launcher/licenses/NOTICE.txt) |
| PyInstaller bootloader | GPL-2.0 with the bootloader exception |
| CPython / psutil | PSF / BSD-3-Clause |
| [QuickJS Python binding](https://github.com/PetterS/quickjs) 1.19.4 (author: Petter Strandmark) and QuickJS (© 2016–2021 Fabrice Bellard, © 2017–2021 Charlie Gordon) | [MIT notices](freevideo_engine/licenses/quickjs-MIT.txt) |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI), including the H3 text encoder code | GPL-3.0 |
| [VDN-H3](https://github.com/OpenVDN/vdn-minimax-h3), [Diffusers](https://github.com/huggingface/diffusers), [SageAttention](https://github.com/thu-ml/SageAttention) | Apache-2.0 |
| [VDN-H3 and H3 model weights](https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE) | MiniMax H3 Community License |
| [MiniMax H3 latent upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler) | [MIT](freevideo_engine/licenses/latent-upscaler-MIT.txt) |
| [FreeToken](https://github.com/FlashML-org/FreeToken) prompt VLM operators, [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) optional weights | [Apache-2.0](freevideo_engine/licenses/FreeToken-Apache-2.0.txt) |
| [Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) Turbo image generation models, including Qwen3-VL-8B and VAE (optional download; [notice](freevideo_engine/image_gen/licenses/NOTICE.txt) installed with the models), © 2026 Hangzhou Tongyi Laboratory Technology Co., Ltd. All Rights Reserved. | [Qwen RESEARCH LICENSE AGREEMENT](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/LICENSE) |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 1.13.8 speech runtime, built by FreeVideo without text-to-speech, ALSA and PortAudio (modified build; `__init__.py` edited; source snapshot 11afbd00 kept by FreeVideo; optional download for automatic subtitles, [build note](freevideo_engine/licenses/sherpa-onnx-build-NOTE.txt)). Includes statically linked kaldi-native-fbank, kaldi-decoder, kaldifst, OpenFst, simple-sentencepiece (Apache-2.0), KissFFT (BSD-3-Clause), pybind11 (BSD-3-Clause), nlohmann/json (MIT), hclust-cpp and Darts-clone (BSD-2-Clause), ThreadPool (Zlib). The Windows build also contains msvcp140.dll and msvcp140_1.dll (Microsoft Visual C++ 2022 runtime 14.44.35211, © Microsoft Corporation), unmodified and redistributed as permitted by Microsoft for its redistributable files. | Apache-2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) 1.28.2 (CPU), © Microsoft Corporation; its third-party notices are shipped inside the sherpa-onnx wheel | MIT |
| [Eigen](https://gitlab.com/libeigen/eigen) 5.0.1, compiled into sherpa-onnx and ONNX Runtime; source: https://gitlab.com/libeigen/eigen/-/tree/5.0.1 | MPL-2.0 |
| [Silero VAD](https://github.com/snakers4/silero-vad) v5.0 voice-activity model, © 2020-present Silero Team (optional download; license file downloaded with the model) | [MIT](freevideo_engine/licenses/Silero-VAD-MIT.txt) |
| [NVIDIA NeMo stt_en_conformer_ctc_small](https://huggingface.co/nvidia/stt_en_conformer_ctc_small), © NVIDIA, exported to ONNX and quantized to int8 by the sherpa-onnx project, changed from the original; not endorsed by NVIDIA (optional download for English subtitles; [notice](freevideo_engine/licenses/NeMo-stt_en_conformer_ctc_small-NOTICE.txt)) | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) |
| [OpenAI Whisper tiny](https://github.com/openai/whisper), © 2022 OpenAI, exported to ONNX and quantized to int8 by the sherpa-onnx project (optional download for spoken-language detection) | [MIT](freevideo_engine/licenses/Whisper-MIT.txt) |
| [SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) by FunASR / FunAudioLLM (Alibaba Group), converted to ONNX and quantized to int8 by the sherpa-onnx project, used unchanged under its original name (optional download for multilingual subtitles; [notice](freevideo_engine/licenses/SenseVoice-NOTICE.txt)) | [FunASR Model Open Source License Agreement v1.1](freevideo_engine/licenses/FunASR-MODEL_LICENSE-v1.1.txt) |
| [Lucide](https://lucide.dev) icons (image editor: eyedropper, hand, crop) | [ISC](freevideo_engine/licenses/Lucide-ISC.txt) |
| [Noto Sans CJK SC and Noto Sans Mono CJK SC](https://github.com/notofonts/noto-cjk) 2.004, © 2014-2021 Adobe: subsets renamed FreeVideo Sans SC and FreeVideo Mono SC (image editor text, editor text and subtitles / 剪辑视图的文字和字幕, share card, Linux launcher) | [SIL OFL 1.1](freevideo_engine/licenses/Noto-CJK-Sans-OFL.txt) |
| [Noto Serif CJK SC](https://github.com/notofonts/noto-cjk) 2.003, © 2017-2024 Adobe: subset renamed FreeVideo Serif SC (image editor text, editor text and subtitles / 剪辑视图的文字和字幕) | [SIL OFL 1.1](freevideo_engine/licenses/Noto-CJK-Serif-OFL.txt) |
| [LXGW WenKai](https://github.com/lxgw/LxgwWenKai) v1.522, © 2021-2026 LXGW, © 2020 The Klee Project Authors: subset renamed FreeVideo Kai SC (image editor text, editor text and subtitles / 剪辑视图的文字和字幕) | [SIL OFL 1.1](freevideo_engine/licenses/LXGW-WenKai-OFL.txt) |

Original launcher license texts are included in Windows packages. Other runtime dependencies are listed in [constraints](constraints), with their license information in the installed packages.

The fonts in `web/fonts/` are built by `scripts/build_web_fonts.py` from the pinned sources above; each file keeps its original copyright notice and licence in its name table. `FreeVideoBox.woff2` is FreeVideo's own.

`decode_stream.py`, `lora_cache.py` and `reference_sampler.py` include adaptations of upstream code; their source headers retain the attribution.

`prompt_vlm/core.py` adapts FreeToken's merged MLP/QKV and bounded vision operators from revision
`555efd89447232a555e05d187256b76a51c1aaa0`. Its header describes the changes and integration boundary.
