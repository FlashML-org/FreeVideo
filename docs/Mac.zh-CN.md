# Mac（实验性）

[English](Mac.md) · 中文

FreeVideo 可在 Apple 芯片 Mac 上本地运行 MiniMax H3，需要 macOS 14 及以上和 Xcode 命令行工具。Mac 版本目前为实验性质，仍在持续优化。

## 安装

1. [下载 FreeVideo-Mac-arm64.dmg](https://github.com/FlashML-org/FreeVideo/releases/download/macos-preview/FreeVideo-Mac-arm64.dmg)，将 FreeVideo 拖入「应用程序」。
2. 打开 FreeVideo。预览版首次打开需要确认一次，见下方[首次打开](#首次打开)。
3. 选择安装位置，可添加已有的模型目录，然后点击 **安装并启动**。Mac 运行环境和缺失的模型会自动下载。

## 首次打开

Mac 预览版暂未进行 Apple 公证，因此首次打开时 macOS 会拦截，并提示无法验证开发者。放行前请先确认文件来自官方：

1. 只从 [FreeVideo 的 Release 页面](https://github.com/FlashML-org/FreeVideo/releases/tag/macos-preview)下载。
2. 在「终端」中运行 `shasum -a 256 ~/Downloads/FreeVideo-Mac-arm64.dmg`，输出应与 Release 中 SHA256SUMS.txt 里的值一致。
3. 打开 FreeVideo，关闭系统提示后，在 **系统设置 → 隐私与安全** 中找到 FreeVideo，点击 **仍要打开** 并按提示确认。

这只放行 FreeVideo，不会关闭 Gatekeeper，也不会改动其他安全设置。如果系统提示文件已损坏或包含恶意软件，请不要打开，并向我们反馈。完成公证后将不再需要这一步。

## 特性

- 通过 Metal 在 GPU 上计算，采样和视频解码中的注意力使用 MLX 融合内核。
- 每个阶段开始前，根据当前可用的统一内存确定注意力头分组、分块大小和常驻的 Transformer 层数。
- Mac 下载 ConvRot int8 权重。M5 及更新的芯片在 GPU 的 Metal 4 张量单元上以 int8 计算大矩阵；更早的芯片在每层加载时于 GPU 上解码为 BF16 计算。
- 未常驻内存的层从磁盘流式加载并提前预取。
- 支持文本、首帧输入和二次采样。

## 生成时间

在 M5、24GB 统一内存、可用内存约 14GB 的情况下：

| 分辨率 | 时长 | 生成时间 |
| --- | --- | --- |
| 1344 × 768 | 10 秒 | 约 42 分钟 |
| 960 × 544 | 10 秒 | 约 22 分钟 |
| 512 × 512 | 1.6 秒 | 约 3 分钟 |

以上时间使用了 M5 的 int8 张量单元。M5 之前的 Mac 以 BF16 运行同一套权重，耗时更长。Mac 版本仍在持续优化，更大的统一内存和更新的芯片都会进一步缩短生成时间。
