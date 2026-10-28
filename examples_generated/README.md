# FreeVideo Showcase & Compatibility Examples

This directory contains benchmark video generations, prompts, and settings demonstrating the capabilities of the FreeVideo standalone engine and MiniMax H3 multimodal generation pipeline on NVIDIA Pascal hardware (GTX 1070 Ti 8GB).

---

## Verified 10-Second Benchmark Generations

| Directory | Scene Title | Resolution | Verified Video Asset |
| :--- | :--- | :---: | :--- |
| `01_pet_cat_sunlight/` | 🐱 Ginger Tabby Cat in Sunlight | 448×256 & 320×256 | [Watch 10s Video](https://github.com/user-attachments/assets/fb680c8f-b74d-4206-b18a-8564a0dde121) |
| `02_human_cyberpunk_detective/` | 🕵️ Cyberpunk Detective Investigation | 448×256 | [Watch 10s Video](https://github.com/user-attachments/assets/8c4f4e99-46b7-4c92-b01c-0b833d346b5b) |
| `03_mythical_phoenix_rebirth/` | 🦅 Phoenix Solar Rebirth | 448×256 | [Watch 10s Video](https://github.com/user-attachments/assets/0b7b5786-a585-4ac2-a6c5-f13b0663ad95) |
| `04_giant_colossus_forest/` | 🗿 Ancient Moss Colossus | 448×256 | [Watch 10s Video](https://github.com/user-attachments/assets/be3f6923-ab2c-48e3-bf04-be5ccaf97368) |
| `05_alien_bioluminescent_entity/` | 🐙 Deep Cavern Bioluminescent Alien | 448×256 | [Watch 10s Video](https://github.com/user-attachments/assets/c5b14b6d-9130-4c64-ac84-e93b970503b4) |
| `06_zombie_apocalypse_encounter/` | 🧟 Zombie Apocalypse Encounter | 448×256 | [Watch 10s Video](https://github.com/user-attachments/assets/13c9697c-87e3-4627-8e63-ac87d7405e97) |
| `07_multiscene_harry_and_fem_malfoy/` | 🎬 Multi-Scene Narrative: Harry & Female Malfoy | 448×256 | [Watch 10s Video](https://github.com/user-attachments/assets/89f8d692-65ea-47e4-a82d-b55683362441) |

---

## Directory Layout
Each benchmark subfolder contains:
- `*_10s.mp4`: Full 10-second multimodal video generation (H.264 + AAC audio).
- `*_10s_prompt_and_settings.txt`: Exact positive prompt, negative prompt, seeds, CFG guidance, steps, and resolution used.
- `poster.jpg`: High-definition video keyframe poster.
- `preview.gif`: Lightweight animated browser preview.

---

## Autonomous Multi-Tier Generation
To run multi-tier generation autonomously:
```bat
Run_MultiTier_Overnight.bat
```
Execution logs and benchmarks are automatically recorded to `_project/artifacts/generation_runs_report_v1.0.md`.