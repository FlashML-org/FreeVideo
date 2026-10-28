# 社区 LoRA

[English](LoRA.md) · 中文

FreeVideo 支持 MiniMax H3 社区 LoRA。每组对照使用相同的提示词和 seed。

## LoRA 放置路径（安装方法）

> [!IMPORTANT]
> 社区 LoRA 模型文件（`.safetensors`）必须放置在 **ComfyUI** 安装根目录下的 **`models/loras/`** 文件夹中：
> ```
> ComfyUI/
> └── models/
>     └── loras/
>         ├── vh5tape-vhs-lora-minimax-h3.safetensors
>         └── my_custom_h3_lora.safetensors
> ```
> **请勿将 LoRA 文件放在 FreeVideo 自身文件夹中。** FreeVideo 会直接读取 ComfyUI 主程序的 `/models/loras` 目录。

### 如何在 FreeVideo Studio 中使用 LoRA：
1. 将 `.safetensors` 格式的 LoRA 放入 `ComfyUI/models/loras/` 文件夹。
2. 打开 FreeVideo 工作台，找到 **LoRAs** 控制区域。
3. 点击 **+ 添加 (Add)** 按钮。
4. 从下拉菜单选择已检测到的 LoRA，并拖动调节权重强度（0.1 ~ 2.0）。
5. 支持最多叠加并排序 32 个 LoRA。


## [VHS 录像带](https://huggingface.co/KennethFal/vh5tape-vhs-lora-minimax-h3)

| 无 LoRA | 有 LoRA |
| :---: | :---: |
| https://github.com/user-attachments/assets/c32440e6-6fba-4685-bd47-3cdac3b5c63e | https://github.com/user-attachments/assets/83989e7a-0f4f-4e58-80f3-705d4fc3e392 |

## [手绘线稿动画](https://huggingface.co/Inner-Reflections/MiniMax-H3-Looping-Sketch-Anime)

| 无 LoRA | 有 LoRA |
| :---: | :---: |
| https://github.com/user-attachments/assets/60982172-eb0e-4f30-a19d-7ffcbe5ff920 | https://github.com/user-attachments/assets/1bab08eb-8c1f-4ee7-a25c-960ac40a201b |

## [Male POV 第一人称](https://civitai.com/models/2966380/minimax-h3-male-pov)

| 无 LoRA | 有 LoRA |
| :---: | :---: |
| https://github.com/user-attachments/assets/163924ad-dd11-4b08-9b91-62c823b6c0a7 | https://github.com/user-attachments/assets/17d3374b-c3b3-409e-8da5-6a1415bb4acb |
