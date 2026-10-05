<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/assets/freevideo.svg">
    <img alt="FreeVideo" src="web/assets/freevideo-light.svg" width="65%">
  </picture>
</div>

<p align="center">
| <a href="https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo.exe"><b>Windows 下载</b></a> | <a href="https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Mac-arm64.dmg"><b>macOS 下载</b></a> | <a href="https://discord.gg/MsA277cJzZ"><b>Discord</b></a> | <a href="https://freevideo-community.pages.dev/qq"><b>QQ 群</b></a> | <a href="https://freevideo-community.pages.dev/wechat"><b>微信群</b></a> |
</p>

<p align="center"><a href="README.md">English</a> · 中文</p>

FreeVideo 由 [Video DeltaNet（VDN）](https://openvdn.github.io/) 驱动，让 MiniMax H3 能够在消费级显卡上本地运行，最低只需 8GB 显存和 16GB 内存，并会根据硬件配置自动选择合适的加速路径。

https://github.com/user-attachments/assets/ecda7d0d-7fbe-4e0c-8c29-8f3315bafc15

## 简介

FreeVideo 是面向消费级显卡的 MiniMax H3 本地推理引擎，基于 [OpenVDN](https://github.com/OpenVDN) 的 8 步模型 [VDN-H3](https://huggingface.co/OpenVDN/vdn-minimax-h3) 和 [Video DeltaNet](https://openvdn.github.io/) 的混合注意力。

它统一调度显存、内存与磁盘，并根据硬件条件调整权重放置、计算精度和注意力内核。FreeVideo 以 ComfyUI 插件形式提供，配备 Windows 启动器，也支持 Linux 命令行。主要特性包括：

- **硬件自适应**：针对不同显卡架构选择 FP8 计算路径（原生 FP8，或 FP8 存储配合 BF16 计算），并自动探测可用的注意力内核，无需手动配置。
- **低显存推理**：通过权重流式加载、异步预取与分块计算降低峰值显存，最低只需 8GB 显存和 16GB 内存。
- **多模态输入**：支持文本、首帧、尾帧，以及图像、视频、音频参考输入。
- **社区 LoRA**：支持在工作流中使用 MiniMax H3 社区 LoRA。[查看效果对比](docs/LoRA.zh-CN.md)。
- **ComfyUI 集成**：在 ComfyUI 中提供专门的创作面板，支持二次采样和批量生成，并可浏览历史作品；需要更精细的控制时，可切换到节点视图，接入 LoRA 或自定义工作流。
- **一键部署**：Windows 和 Mac 启动器自动完成 ComfyUI、运行环境与模型的部署，可复用已有模型，并支持离线安装。

## 开始使用

### Windows

1. [下载 FreeVideo.exe](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo.exe) 并运行。
2. 选择已有的 ComfyUI 目录或安装新的 ComfyUI。可添加已有的模型目录以复用文件，缺失的模型会自动下载。
3. 点击 **安装并启动**，浏览器中会打开带有 FreeVideo 创作面板的 ComfyUI。

<div align="center">
  <img alt="FreeVideo 创作面板" src="https://github.com/user-attachments/assets/f4719079-bb48-4836-9c64-2473477cd1a0" width="92%">
</div>

**离线安装：** 从[夸克网盘](https://pan.quark.cn/s/c51235b84618)下载离线包，将 ZIP 文件直接拖入启动器，无需解压。需要「公用模型」包和对应显卡的模型包（30/40 系或 50 系）；全新安装 ComfyUI 时还需要「运行环境」包。

### macOS（Apple 芯片预览版）

1. [下载 FreeVideo-Mac-arm64.dmg](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Mac-arm64.dmg)，打开后将 **FreeVideo.app** 拖入 **应用程序**。
2. 打开 FreeVideo，选择已有的 ComfyUI 目录或安装新的 ComfyUI。可添加已有的模型目录以复用文件，缺失的运行环境和模型会自动下载。
3. 点击 **安装并启动**，浏览器中会打开带有 FreeVideo 创作面板的 ComfyUI。

预览版已在 24GB 统一内存的 M5 Mac 上测试，生成耗时与内存说明见 [Mac 说明](docs/Mac.zh-CN.md)。

Mac 预览版暂未进行 Apple 公证，首次打开时 macOS 会拦截。请只从 Release 页面下载，按 [Mac 说明](docs/Mac.zh-CN.md#首次打开)核对文件后再放行。这只对 FreeVideo 生效，不影响其他安全设置。

### 已有 ComfyUI

以自定义节点方式安装：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/FlashML-org/FreeVideo.git
```

重启 ComfyUI，打开 **工作流 → 浏览模板 → FreeVideo → FreeVideo-All-in-One**，然后在 FreeVideo 的 **设置** 中完成安装。

### Linux

安装：

```bash
git clone https://github.com/FlashML-org/FreeVideo.git && cd FreeVideo
./setup.sh
```

从提示词文件生成视频：

```bash
./freevideo generate --prompt-file prompt.txt --out video.mp4
```

### 更多文档

- [FreeVideo Adaptive Execution Planner](docs/zh-CN/execution-planning.md)

### 问题反馈

问题和建议请提交至 [GitHub Issues](https://github.com/FlashML-org/FreeVideo/issues)，也可以在 [Discord](https://discord.gg/MsA277cJzZ)、[QQ 群](https://freevideo-community.pages.dev/qq)或[微信群](https://freevideo-community.pages.dev/wechat)中交流。

## 引用

FreeVideo 基于 VDN-H3。如在研究中使用 FreeVideo，请引用 [Video DeltaNet 论文](https://arxiv.org/abs/2609.20744)：

```bibtex
@article{xi2026videodeltanet,
  title={Video DeltaNet: A Video-Native Hybrid Attention for Livestream Video Generation},
  author={Xi, Haocheng and Xie, Yiming and Zhao, Hexu and Zhang, Yiwen and Liu, Michael and Creavin, Thomas and Keutzer, Kurt and Li, Xiuyu and Lv, Zhaoyang and Xu, Chenfeng and Feng, Haiwen},
  journal={arXiv preprint arXiv:2609.20744},
  year={2026}
}
```

## 团队

### 项目团队

[Bowen Xue](https://github.com/KBRASK) · [Shuo Yang](https://github.com/andy-yang-1) · [Haocheng Xi](https://github.com/haochengxi) · [Xiaoze Fan](https://github.com/jason-fxz) · [Chenfeng Xu](https://github.com/chenfengxu714)

### 特别感谢

特别感谢 [**AIwood爱屋研究室**](https://space.bilibili.com/503934057) 和 [**T8star-Aix**](https://space.bilibili.com/385085361) 参与项目测试并提供宝贵反馈。

*按参与时间先后排序。*

## 致谢

感谢 [OpenVDN](https://github.com/OpenVDN) 开源 [Video DeltaNet / VDN-H3](https://github.com/OpenVDN/vdn-minimax-h3) 的模型权重、训练代码与推理实现。

感谢 Impossible Research 提供计算资源。

同时感谢 [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) 提供基础模型，以及以下项目：
[ComfyUI](https://github.com/Comfy-Org/ComfyUI)、
[Diffusers](https://github.com/huggingface/diffusers)、
[SageAttention](https://github.com/thu-ml/SageAttention)、
[MiniMax H3 潜空间放大模型](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler)、
[ComfyUI 版 H3 文本编码器](https://huggingface.co/t8star/Vdn-Minimax-H3-Comfy)和
[Qt for Python](https://doc.qt.io/qtforpython-6/)。

## 许可证

代码采用 [Apache License 2.0](LICENSE) 授权。模型权重适用 [MiniMax H3 社区许可证](https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE)，该许可证对使用地区和用途有所限制。
