<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/assets/freevideo.svg">
    <img alt="FreeVideo" src="web/assets/freevideo-light.svg" width="65%">
  </picture>
</div>

<p align="center">
| <a href="https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo.exe"><b>Windows 下载</b></a> | <a href="https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Mac-arm64.dmg"><b>macOS 下载</b></a> | <a href="https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Linux-x86_64.AppImage"><b>Linux 下载</b></a> | <a href="https://freevideo-community.pages.dev/#gallery"><b>作品展示</b></a> | <a href="https://discord.gg/MsA277cJzZ"><b>Discord</b></a> | <a href="https://freevideo-community.pages.dev/qq"><b>QQ 群</b></a> | <a href="https://freevideo-community.pages.dev/wechat"><b>微信群</b></a> |
</p>

<p align="center"><a href="README.md">English</a> · 中文</p>

FreeVideo 由 [Video DeltaNet（VDN）](https://openvdn.github.io/) 驱动，让 MiniMax H3 能够在消费级显卡上本地运行，最低只需 8GB 显存和 16GB 内存，并会根据硬件配置自动选择合适的加速路径。

https://github.com/user-attachments/assets/ecda7d0d-7fbe-4e0c-8c29-8f3315bafc15

<p align="center"><a href="https://freevideo-community.pages.dev/#gallery">在作品展示中查看更多视频和四档质量对比 →</a></p>

## 更新动态

- **2026-10-07** · **[v0.3.2](https://github.com/FlashML-org/FreeVideo/releases/tag/v0.3.2)：先出预览。** 先看半分辨率预览，满意的再继续二采。
- **2026-10-07** · **[v0.3.0](https://github.com/FlashML-org/FreeVideo/releases/tag/v0.3.0)：更快的 int8 模型。** GeForce 和 RTX 30 系显卡改用 int8，RTX 3090 生成 5 秒视频快约 2.3 倍。
- **2026-10-06** · **[作品展示](https://freevideo-community.pages.dev/#gallery)上线。** 观看 FreeVideo 生成的 20 秒视频，以及四档质量的并排对比。
- **2026-10-06** · **[v0.2.3](https://github.com/FlashML-org/FreeVideo/releases/tag/v0.2.3)：视频自带工作流。** 把 FreeVideo 生成的视频拖到 ComfyUI 画布上，即可还原提示词、种子和全部参数，之前的视频也能补上。
- **2026-10-05** · **[v0.2.0](https://github.com/FlashML-org/FreeVideo/releases/tag/v0.2.0)：四档质量。** 每次生成可选择轻量、标准、精细或极致，档位越高，生成质量越高，但耗时更长；生成结果可导出为带生成耗时和显卡信息的分享图片或视频。
- **2026-10-05** · **[v0.1.2](https://github.com/FlashML-org/FreeVideo/releases/tag/v0.1.2)：支持 Mac。** Apple 芯片 Mac 也能本地生成 MiniMax H3 视频（预览版）。
- **2026-10-03** · **支持社区 LoRA。** 在工作流中直接使用 MiniMax H3 LoRA，参见[示例](docs/LoRA.zh-CN.md)。
- **2026-10-02** · **FreeVideo 开源。** 最低 8GB 显存，即可在消费级显卡上运行 MiniMax H3。

## 简介

FreeVideo 是面向消费级显卡的 MiniMax H3 本地推理引擎，基于 [OpenVDN](https://github.com/OpenVDN) 的 8 步模型 [VDN-H3](https://huggingface.co/OpenVDN/vdn-minimax-h3) 和 [Video DeltaNet](https://openvdn.github.io/) 的混合注意力。

它统一调度显存、内存与磁盘，并根据硬件条件调整权重放置、计算精度和注意力内核。FreeVideo 以 ComfyUI 插件形式提供，配备 Windows、macOS 和 Linux 启动器，并支持在远程 GPU 服务器上以服务器模式运行。主要特性包括：

- **硬件自适应**：针对不同显卡选择权重格式（GeForce 和 RTX 30 系用 int8，专业卡和数据中心卡用 FP8），并自动探测可用的注意力内核，无需手动配置。
- **低显存推理**：通过权重流式加载、异步预取与分块计算降低峰值显存，最低只需 8GB 显存和 16GB 内存。
- **多模态输入**：支持文本、首帧、尾帧，以及图像、视频、音频参考输入。
- **社区 LoRA**：支持在工作流中使用 MiniMax H3 社区 LoRA。[查看效果对比](docs/LoRA.zh-CN.md)。
- **ComfyUI 集成**：在 ComfyUI 中提供专门的创作面板，支持二次采样和批量生成，并可浏览历史作品；需要更精细的控制时，可切换到节点视图，接入 LoRA 或自定义工作流。
- **一键部署**：Windows、Mac 和 Linux 启动器自动完成 ComfyUI、运行环境与模型的部署，可复用已有模型，并支持离线安装。Linux 上也可以在终端运行 `./setup.sh` 一次装好。

## 开始使用

### Windows

1. [下载 FreeVideo.exe](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo.exe) 并运行。
2. 选择已有的 ComfyUI 目录或安装新的 ComfyUI。可添加已有的模型目录以复用文件，缺失的模型会自动下载。
3. 点击 **安装并启动**，浏览器中会打开带有 FreeVideo 创作面板的 ComfyUI。

<div align="center">
  <img alt="FreeVideo 创作面板" src="https://github.com/user-attachments/assets/f4719079-bb48-4836-9c64-2473477cd1a0" width="92%">
</div>

**离线安装：** 从[夸克网盘](https://pan.quark.cn/s/c51235b84618)下载离线包，将 ZIP 文件直接拖入启动器，无需解压。完全离线安装需要「视频模型」「文本编码器」「视频与音频解码器」「采样缓存」四个模型包，全新安装 ComfyUI 时还需要「运行环境」包。也可以只导入「运行环境」包，再在模型来源中选择**自动下载**（v0.3.12 及以上）。「音频参考缓存」为可选包，仅在参考素材带有音频时使用。

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

**桌面：** [下载 FreeVideo-Linux-x86_64.AppImage](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Linux-x86_64.AppImage)，加上可执行权限后打开，之后的步骤与 Windows 相同。

**终端或服务器：**

```bash
git clone https://github.com/FlashML-org/FreeVideo.git && cd FreeVideo
./setup.sh
```

安装程序会装好 FreeVideo、ComfyUI 和模型，然后在浏览器中打开 FreeVideo。之后输入 `freevideo` 即可再次打开。在服务器上，可以通过 SSH 从自己的电脑访问，或运行 `freevideo server --listen 0.0.0.0` 获取局域网访问链接。详见 [Linux 说明](docs/Linux.zh-CN.md)。

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
