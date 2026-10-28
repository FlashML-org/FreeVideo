<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/assets/freevideo.svg">
    <img alt="FreeVideo-Pascal" src="web/assets/freevideo-light.svg" width="60%">
  </picture>
  <h1>FreeVideo-Pascal</h1>
  <p><b>独立单次概念验证（Proof of Concept）：在 NVIDIA Pascal 显卡（GTX 1060 – 1080 Ti）上本地运行 MiniMax H3 视频生成</b></p>
  <p>
    <a href="https://github.com/SrCodeGorilla/FreeVideo-Pascal/releases/latest"><img src="https://img.shields.io/github/v/release/SrCodeGorilla/FreeVideo-Pascal?style=for-the-badge&logo=github&color=blue" alt="最新发布版本"></a>
    <a href="https://ko-fi.com/SrCodeGorilla"><img src="https://img.shields.io/badge/Ko--fi-请作者喝杯咖啡-F16061?style=for-the-badge&logo=ko-fi&logoColor=white" alt="Ko-fi"></a>
    <a href="https://buymeacoffee.com/SrCodeGorilla"><img src="https://img.shields.io/badge/Buy_Me_a_Coffee-支持创作者-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee"></a>
  </p>
  <p><a href="README.md">English</a> · 中文</p>
</div>

> [!IMPORTANT]
> **一次性概念验证项目 — 本项目非持续维护分支**
> 本仓库是一个独立的、**已完成的一次性概念验证项目（One-off Proof of Concept）**，后续**不会**作为持续开发的软件维护。其诞生仅为了在技术上证明：在 8GB 显存预算的老旧 Pascal 显卡上，本地跑通 MiniMax H3 多模态视频生成是完全可行的。
>
> 本项目**不是**官方 FreeVideo。如需适配新一代显卡（RTX 30 系、40 系、Blackwell）以及获取官方最新动态，请访问上游官方仓库：**[FlashML-org/FreeVideo](https://github.com/FlashML-org/FreeVideo)**。

---

## ☕ 创作者自愿赞赏（请作者喝杯咖啡）

如果 FreeVideo-Pascal 帮您省下了购买新显卡的数千元预算，让您使用现有的老旧老机器成功开启了本地 AI 视频创作之旅，欢迎请作者喝杯咖啡支持：

<p align="center">
  <a href="https://ko-fi.com/SrCodeGorilla"><b>☕ 通过 Ko-fi 支持 (ko-fi.com/SrCodeGorilla)</b></a> &nbsp;•&nbsp;
  <a href="https://buymeacoffee.com/SrCodeGorilla"><b>🍕 通过 Buy Me a Coffee 支持 (buymeacoffee.com/SrCodeGorilla)</b></a>
</p>

> [!NOTE]
> **完全自愿原则：** 赞赏纯属自愿。请务必**仅在**本工具确实对您有所帮助、且您已在自己的 Pascal 显卡上成功生成视频之后，再考虑给予赞赏！

---

## 🌟 验证成果展示（GTX 1070 Ti 8GB 实测通过）

为了验证 MiniMax H3 多模态视频生成在无需昂贵硬件升级的前提下可在老旧 Pascal 显卡上完全稳定运行，以下经典场景均已完成全流程端到端实测验证：

| 场景分类 | 角色与分镜主题 | 网页播放试看 | 完整提示词（多模态） | 生成参数与设置 |
| :--- | :--- | :---: | :--- | :--- |
| 🐱 **宠物与动物** | 阳光下的金渐层猫咪 | [<img src="examples_generated/01_pet_cat_sunlight/poster.jpg" width="130" alt="猫咪"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/fb680c8f-b74d-4206-b18a-8564a0dde121) | <details><summary><b>🐱 查看猫咪分镜完整提示词</b></summary><br><b>视觉画面：</b> 阳光明媚的客厅地面微距镜头，一只可爱活泼的金渐层幼猫在毛绒地毯上翻滚，琥珀色眼睛闪烁好奇光芒，小爪子在空中抓向晨光与悬浮光斑。<br><br><b>环境音效：</b> 柔和有节奏的呼噜声、地毯纤维轻微摩擦声、轻柔呼吸与窗外鸟鸣。<br><br><b>配乐风格：</b> 温暖欢快的原声指弹吉他，搭配低音大提琴与钟琴点缀。</details> | • <b>分辨率：</b> 448×320<br>• <b>目标时长：</b> 10.0秒<br>• <b>精炼模式：</b> Two-Pass 双轮迭代 (8+3步)<br>• <b>随机种子：</b> <code>4213067137</code> |
| 🕵️ **人类与赛博朋克** | 赛博朋克侦探调查 | [<img src="examples_generated/02_human_cyberpunk_detective/poster.jpg" width="130" alt="侦探"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/8c4f4e99-46b7-4c92-b01c-0b833d346b5b) | <details><summary><b>🕵️ 查看侦探分镜完整提示词</b></summary><br><b>视觉画面：</b> 新宿霓虹小巷暴雨中疲惫的生化侦探中近景，暗色风衣镶嵌青色光纤缝线，呼出白色寒雾，左眼蓝宝石机械义眼微型光圈机械旋转缩放对焦。<br><br><b>环境音效：</b> 沥青路面密集暴雨声、高压霓虹灯电流嗡鸣、远方悬浮飞车引擎声、义眼微型齿轮对焦卡嗒声。<br><br><b>配乐风格：</b> 沉浸式模拟合成器波浪氛围音，搭配慢速低音脉冲。</details> | • <b>分辨率：</b> 448×320<br>• <b>目标时长：</b> 10.0秒<br>• <b>精炼模式：</b> Two-Pass 双轮迭代 (8+3步)<br>• <b>随机种子：</b> <code>8326374438</code> |
| 🦅 **神话幻想生物** | 太阳不死鸟浴火重生 | [<img src="examples_generated/03_mythical_phoenix_rebirth/poster.jpg" width="130" alt="不死鸟"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/0b7b5786-a585-4ac2-a6c5-f13b0663ad95) | <details><summary><b>🦅 查看不死鸟分镜完整提示词</b></summary><br><b>视觉画面：</b> 远古火山黄昏史诗全景。黑曜石余烬巢穴深处，展翼金红神话不死鸟展开炽热羽翼，金色火星与星光粒子旋涡升腾，镜头螺旋盘旋仰拍极速升空。<br><br><b>环境音效：</b> 火山低频轰鸣、烈焰巨翼呼啸破风声、余烬碎裂噼啪声与清澈高亢凤鸣。<br><br><b>配乐风格：</b> 恢弘管弦乐渐强高潮，宏大弦乐群、圆号与空灵唱诗合唱。</details> | • <b>分辨率：</b> 448×320<br>• <b>目标时长：</b> 10.0秒<br>• <b>精炼模式：</b> Two-Pass 双轮迭代 (8+3步)<br>• <b>随机种子：</b> <code>919078551</code> |
| 🗿 **巨型奇观生物** | 迷雾红木林远古巨神石像 | [<img src="examples_generated/04_giant_colossus_forest/poster.jpg" width="130" alt="巨石像"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/be3f6923-ab2c-48e3-bf04-be5ccaf97368) | <details><summary><b>🗿 查看巨神像分镜完整提示词</b></summary><br><b>视觉画面：</b> 红木森林地面震撼极低仰角。布满数百年苔藓与青藤的远古巨石像泰坦踏碎晨雾跨步穿行，沉重每一步微震镜头并惊起飞鸟，晨光倾泻穿透树冠。<br><br><b>环境音效：</b> 次低音地震级地表震颤、树枝断裂碎裂声、回荡鸟鸣与远古岩石摩擦沉闷声。<br><br><b>配乐风格：</b> 史诗打击乐、沉重太鼓、肃穆大提琴与深沉铜管。</details> | • <b>分辨率：</b> 448×320<br>• <b>目标时长：</b> 10.0秒<br>• <b>精炼模式：</b> Two-Pass 双轮迭代 (8+3步)<br>• <b>随机种子：</b> <code>5507082314</code> |
| 🐙 **异形与外星生命** | 深邃地底发光水母异形 | [<img src="examples_generated/05_alien_bioluminescent_entity/poster.jpg" width="130" alt="异形"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/c5b14b6d-9130-4c64-ac84-e93b970503b4) | <details><summary><b>🐙 查看异形分镜完整提示词</b></summary><br><b>视觉画面：</b> 悬浮紫青双色紫晶的地底异星巨型洞穴。半透明深渊未知生命体挥舞飘逸丝带状鱼鳍与发光触须无重力滑行，珠光皮肤波浪式脉动青蓝荧光。<br><br><b>环境音效：</b> 水下空灵晶体风铃回响、异星空间共鸣、微风光晕闪烁与生物发光低频脉冲。<br><br><b>配乐风格：</b> 空灵外太空氛围音乐、模块化合成器钟声与温暖深沉次低音。</details> | • <b>分辨率：</b> 448×320<br>• <b>目标时长：</b> 10.0秒<br>• <b>精炼模式：</b> Two-Pass 双轮迭代 (8+3步)<br>• <b>随机种子：</b> <code>7154134444</code> |
| 🧟 **末世与丧尸遭遇** | 末日废弃公路尸潮逼近 | [<img src="examples_generated/06_zombie_apocalypse_encounter/poster.jpg" width="130" alt="丧尸"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/13c9697c-87e3-4627-8e63-ac87d7405e97) | <details><summary><b>🧟 查看丧尸分镜完整提示词</b></summary><br><b>视觉画面：</b> 破败高架桥厚重寒雾中的紧张恐怖镜头。生锈侧翻车辆碎片横陈，衣衫褴褛空洞眼神的丧尸在迷雾中蹒跚前行，幸存者颤抖手电筒光束投射拉长诡异阴影。<br><br><b>环境音效：</b> 摩天废墟掠过的凄厉风声、湿漉路面拖拽靴底摩擦声、粗重喘息与手电筒电闸咔嗒声。<br><br><b>配乐风格：</b> 黑暗紧张弦乐、不协调大提琴刮奏与心跳般沉闷次重低音。</details> | • <b>分辨率：</b> 448×320<br>• <b>目标时长：</b> 10.0秒<br>• <b>精炼模式：</b> Two-Pass 双轮迭代 (8+3步)<br>• <b>随机种子：</b> <code>6386587537</code> |
| 🎬 **多分镜连贯叙事** | 哈利与女版马尔福对决 | [<img src="examples_generated/07_multiscene_harry_and_fem_malfoy/poster.jpg" width="130" alt="哈利与女版马尔福"><br>▶️ **播放 10秒视频**](https://github.com/user-attachments/assets/89f8d692-65ea-47e4-a82d-b55683362441) | <details><summary><b>🎬 查看连贯多分镜完整提示词（3镜头故事链）</b></summary><br><b>镜头1 (0-3秒)：</b> 暴风雨中霍格沃茨哥特式占星塔。冷月雷电照亮湿漉石地板，斯莱特林银发莱拉·马尔福倚靠石栏颤抖，哈利身着深色大衣立于拱门旁。<br><b>镜头2 (3-7秒)：</b> 情感中特写，女版马尔福贵族伪装彻底破碎，眼含泪水颤声绝望倾诉告白。<br><b>镜头3 (7-10秒)：</b> 情绪失控双膝瘫软，哈利箭步上前接住入怀，镜头完成动人慢速360度环绕，云层破晓月光柔和定格。<br><br><b>环境音效：</b> 石顶暴雨击打声、拱廊山风、滚滚闷雷、长袍沙沙声。<br><br><b>配乐风格：</b> 哀伤深情的独奏钢琴，高潮处转为温柔悠扬的共鸣和弦。</details> | • <b>分辨率：</b> 512×288<br>• <b>目标时长：</b> 10.0秒 (3分镜长镜头)<br>• <b>精炼模式：</b> Multi-Shot (8+3步)<br>• <b>随机种子：</b> <code>1944993644</code> |

### 🎥 网页原生播放器在线试看

<details open>
<summary><b>🎬 分镜 7：多分镜连贯叙事 — 哈利与女版马尔福（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/89f8d692-65ea-47e4-a82d-b55683362441

</details>

<details open>
<summary><b>🐱 分镜 1：阳光下的金渐层猫咪（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/fb680c8f-b74d-4206-b18a-8564a0dde121

</details>

<details>
<summary><b>🕵️ 分镜 2：赛博朋克生化侦探调查（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/8c4f4e99-46b7-4c92-b01c-0b833d346b5b

</details>

<details>
<summary><b>🦅 分镜 3：太阳不死鸟浴火重生（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/0b7b5786-a585-4ac2-a6c5-f13b0663ad95

</details>

<details>
<summary><b>🗿 分镜 4：迷雾红木林远古巨神石像（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/be3f6923-ab2c-48e3-bf04-be5ccaf97368

</details>

<details>
<summary><b>🐙 分镜 5：深邃地底发光水母异形（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/c5b14b6d-9130-4c64-ac84-e93b970503b4

</details>

<details>
<summary><b>🧟 分镜 6：末日废弃公路尸潮逼近（10秒）</b> <i>[原生交互播放器]</i></summary>

https://github.com/user-attachments/assets/13c9697c-87e3-4627-8e63-ac87d7405e97

</details>

---

## ✉️ 致 FreeVideo 原作者团队与开源社区的公开信

**尊敬的薛博文（Bowen Xue）、FlashML 团队以及开源视频 AI 社区的朋友们：**

首先，谨向你们致以最诚挚的敬意与感谢！你们通过 [FreeVideo](https://github.com/FlashML-org/FreeVideo) 与 [Video DeltaNet (VDN-H3)](https://openvdn.github.io/) 所做出的突破性工作，让 MiniMax H3 这一顶级多模态视频生成模型能够在消费级个人电脑上本地运行，为全球开源社区开启了全新的创作纪元。

### 我们希望解决的现实痛点
在如今的硬件市场环境下，动辄花费数千元甚至上万元升级至新一代显卡（RTX 30 系、40 系及更高端硬件），对于全球大量学生、独立创作者和预算有限的开发者而言是一笔沉重的经济负担。全球仍有数以百万计的创作者依赖稳定耐用的 NVIDIA Pascal 显卡（GTX 1060、1070、1070 Ti、1080、1080 Ti）。由于官方上游强制要求具备原生 BF16 计算能力的 SM80+ 架构与 FlashAttention 内核，这些老显卡在官方版本中被完全拒之门外。

### 本次概念验证所证明的结论
我们发起 **FreeVideo-Pascal**，作为一项纯粹独立的、非商业性质的一次性概念验证（PoC），只为了探索一个最纯粹的技术命题：
> *一张来自 2016 年、仅有 8GB 显存的 Pascal 架构显卡，到底能不能完整跑通当今最前沿的 MiniMax H3 多模态视频生成？*

实测得出的答案是：**完全可行**。通过实现：
1. **动态 FP16 精度回退保护**：在 SM80 以下架构上将强制 BF16 替换为 IEEE FP16 运算与 FP32 累加，彻底杜绝指令崩溃；
2. **PyTorch SDPA 注意力路由机制**：在无 SageAttention2/FlashAttention 支持时自动无缝降级至通用内存优化算法；
3. **更精细的显存切片卸载**：确保整个去噪与 VAE 解码峰值显存严格控制在 8 GB 以内；
4. **多分镜故事基准（Story Bible）与运镜连贯性**：让长篇多镜头叙事在角色面容、服装光影与空间站位上保持高度一致。

### 对官方上游的真诚邀请
本项目是**一个已完成的、一次性概念验证工程**。我们无意进行商业化，无意分裂生态，后续也无需持续维护分支。

由于很多知名开源仓库对 AI 辅助编写的 PR 存在顾虑，我们特意以独立公开 PoC 的形式完整开源呈现。**我们真诚欢迎薛博文以及 FlashML 官方团队随时查阅、审视并任意挑选（Cherry-pick）、合并本仓库中任何针对老旧显卡的兼容回退代码或分镜基准设计至官方 FreeVideo 中。**

如果让每个人用手中已有的旧电脑实现自由创作同样契合你们的初心，这些代码与经验完全属于你们。

致以最崇高的敬意与感谢，  
**SrCodeGorilla 与开源 Pascal 社区**  
[https://github.com/SrCodeGorilla/FreeVideo-Pascal](https://github.com/SrCodeGorilla/FreeVideo-Pascal)

---

## 💡 项目初衷、社区使命与开源协作理念

### 为什么打造 FreeVideo-Pascal？
官方上游 FreeVideo 项目专为具备原生 BF16 硬件算力的新一代 GPU（计算能力 SM80+：RTX 30 系、40 系、Blackwell）设计。在老旧 Pascal 架构（GTX 1060~1080 Ti）上，官方安装程序会强制中止，或在运行时因缺失 BF16 指令与 FlashAttention 内核而崩溃。

在目前的硬件市场环境下，显卡升级成本对于大量个人创作者、学生和独立开发者而言极其高昂。**FreeVideo-Pascal 的诞生正是为了打破这种硬件壁垒。**

### 独立概念验证与对上游的开源回馈
- **纯粹的社区探索**：本项目为个人与社区独立开发，无任何企业背景、商业利益或竞品考量。
- **一次性技术展示**：本仓库**不会**作为持续维护的长期分支，我们将其作为已完成的独立成果开源交付给社区。
- **开源回馈与上游协作**：官方 FreeVideo 的主创团队完全可以根据需要，随时自由检视、借鉴、挑选或直接将本分支中的兼容补丁与新功能吸收回官方正式版本中。
- **赋能老旧硬件创作者**：让拥有老机器、无力承担显卡升级成本的广大创作者，同样能够享受到最前沿开源视频生成模型带来的创作乐趣。


---

## 🛠️ Pascal 硬件兼容重构文件清单

为使 MiniMax H3 跑通 Pascal，重构了以下核心底层模块：

| 重构源码文件 | Pascal 核心适配改造内容 |
| :--- | :--- |
| `freevideo_engine/hardware.py` | 将 CUDA 计算能力准入门槛从 SM80（`sm_80`）下调至 SM60+（`sm_60`, `sm_61`），解除原生 BF16 硬件检测中止。 |
| `freevideo_engine/attention.py` | 实现了注意力自动降级调度，当检测到算力低于 SM80 时，无缝切换至 PyTorch 原生高效 SDPA（`torch-sdpa`），彻底免除 SageAttention2/FlashAttention 依赖。 |
| `freevideo_engine/backends/cuda_attention.py` | 针对 SM80 以下禁用 Triton 融合注意力算子，QKV 张量全部安全回退至 PyTorch 算子流。 |
| `freevideo_engine/weights.py` & `freevideo_engine/weight_only.py` | 增加 FP8 weight-only 张量的 PyTorch 反量化与矩阵乘法备选路径，避免触发硬件 Tensor Core 报错。 |
| `freevideo_engine/int8_ops.py` | 针对 ConvRot int8 线性层提供 PyTorch 运算回退，无需 Windows/Pascal 下的 Triton JIT 编译。 |
| `freevideo_engine/environments.py` & `freevideo_engine/torch_downloads.json` | 锁定 PyTorch 环境依赖至 `cu126`（针对 `sm_61` 预编译），防止引入不兼容的 `cu130` 安装包。 |
| `freevideo_engine/generate.py` | 强制在 SM80 以下启用 FP16 激活与 FP32 累加；大 Batch 下采用 eager Newton-Schulz DeltaNet 矩阵求逆以保护显存。 |
| `freevideo_engine/vae_tiles.py` | 采用时空分块 VAE 解码与激进的显存卸载策略，严格将峰值显存锁定在 8GB 阈值之内。 |

---

## ✨ 新增功能特性与相关文件清单

| 新增特性 | 涉及主要文件 | 功能描述 |
| :--- | :--- | :--- |
| **多分镜故事板与故事基准（Story Bible）** | `web/studio_scenes.js`, `web/studio.js`, `freevideo_engine/chain_plan.py` | 支持多段连贯叙事与自由分镜时长调节。自动将主输入框作为全局“故事基准”，统一控制跨镜头角色、光影、环境与站位。 |
| **故事板分镜审核卡与连续生成流** | `web/studio_review.js`, `web/studio_chain.js`, `freevideo_engine/comfy_chain.py` | 浏览器内每段生成后弹出审核卡（**接受**、**重试**或**修改下段提示词**），带给创作者全权导演控制力。 |
| **自适应硬件性能剖析器与动态倒计时** | `scripts/adaptive_profiler.py`, `scripts/generate_malfoid_story.py` | 依据实际渲染基准动态估算耗时，赋予 2.5 倍超时安全冗余，并向终端输出实时 ETA 倒计时。 |
| **社区 LoRA 宿主路径自动挂载** | `web/studio.js`, `docs/LoRA.md`, `docs/LoRA.zh-CN.md` | 规范 LoRA 检索路径至 ComfyUI 根目录下的 `models/loras/`，支持面板多 LoRA 叠加与权重调节。 |
| **连贯长剧本批处理与音频平滑混流器** | `scripts/generate_malfoid_story.py`, `scripts/malfoid_story_specs.py`, `malfoid.bat` | 组织多镜头剧本调度，强制关闭预览启用全采样以消除人脸伪影，并通过 FFmpeg 执行 0.3 秒交叉淡入淡出合成全片。 |
| **全自动模型校验与多源下载工具** | `Full_Downloads.bat`, `scripts/download_models.py` | 基于 HTTP HEAD 快速校验 Hugging Face 模型节点有效性，并为受限地区提供镜像访问指引。 |
| **一键全量安装脚本与智能崩溃拦截** | `Full_Install.bat`, `scripts/build_windows.py` | 一键安装并注入智能暂停拦截机制，避免控制台报错闪退导致调试受阻。 |

---

## 🤖 AI 协作开发透明度与统计数据

秉持开源领域对技术过程的完全坦诚，本项目使用 **Antigravity IDE** 进行结对编程：

- **开发工具**：Google DeepMind Antigravity IDE
- **主要协作大模型**：
  - **Gemini 3.8 Flash**：负责多层级管线设计、脚本编写、快速工具编排与全局文档整合。
  - **Claude Opus 5.5**：负责底层张量反量化方案分析、算子精度降级与显存边界推演。
- **开发周期**：约两周持续测试与迭代（2026年10月）。
- **累计 Token 消耗估算**：约 380 万 Tokens（涵盖长上下文分析、代码差异对比与测试追踪）。
- **等效 API 工具成本**：约 22.00 美元（仅作技术统计参考）。

---

## Pascal 显卡快速上手（GTX 1060 – 1080 Ti）

### Windows 安装与运行步骤：
1. 将本仓库克隆至您的 ComfyUI 插件目录：
   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/SrCodeGorilla/FreeVideo-Pascal.git
   ```
2. 运行模型与环境校验工具：
   ```cmd
   Full_Downloads.bat
   ```
3. 启动 ComfyUI，即可在浏览器中进入专为 Pascal 显卡优化的 FreeVideo 面板开始创作！

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
git clone https://github.com/SrCodeGorilla/FreeVideo-Pascal.git
```

重启 ComfyUI，打开 **工作流 → 浏览模板 → FreeVideo → FreeVideo-All-in-One**，然后在 FreeVideo 的 **设置** 中完成安装。

### Linux

**桌面：** [下载 FreeVideo-Linux-x86_64.AppImage](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Linux-x86_64.AppImage)，加上可执行权限后打开，之后的步骤与 Windows 相同。

**终端或服务器：**

```bash
git clone https://github.com/SrCodeGorilla/FreeVideo-Pascal.git && cd FreeVideo-Pascal
./setup.sh
```

安装程序会装好 FreeVideo、ComfyUI 和模型，然后在浏览器中打开 FreeVideo。之后输入 `freevideo` 即可再次打开。在服务器上，可以通过 SSH 从自己的电脑访问，或运行 `freevideo server --listen 0.0.0.0` 获取局域网访问链接。详见 [Linux 说明](docs/Linux.zh-CN.md)。

### 更多文档

- [FreeVideo Adaptive Execution Planner](docs/zh-CN/execution-planning.md)
- [社区实测](docs/zh-CN/community-results.md)

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
