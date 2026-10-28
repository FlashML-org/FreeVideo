# FreeVideo Multi-Tier Showcase & Generation Execution Report (v1.0)

This report details the execution, configurations, prompt structures, and results of reference video generation runs produced using FreeVideo standalone and the MiniMax H3 multimodal pipeline on local NVIDIA Pascal hardware (GTX 1070 Ti, 8GB VRAM).

---

## 1. Completed Base Showcase Suite (Initial Runs)

| # | Category | Identifier | Target File | Resolution | Duration | Seed | File Size | Status |
| :-: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **01** | Pets | `01_pet_cat_sunlight` | `01_pet_cat_sunlight.mp4` | 448×320 | 2.5s | `421001` | 227.3 KB | **✓ SUCCESS** |
| **02** | Humans | `02_human_cyberpunk_detective` | `02_human_cyberpunk_detective.mp4` | 448×320 | 2.5s | `832044` | 226.0 KB | **✓ SUCCESS** |
| **03** | Mythical Creatures | `03_mythical_phoenix_rebirth` | `03_mythical_phoenix_rebirth.mp4` | 448×320 | 2.5s | `991203` | 482.2 KB | **✓ SUCCESS** |
| **04** | Giant Creatures | `04_giant_colossus_forest` | `04_giant_colossus_forest.mp4` | 448×320 | 2.5s | `550312` | 316.1 KB | **✓ SUCCESS** |
| **05** | Aliens | `05_alien_bioluminescent_entity` | `05_alien_bioluminescent_entity.mp4` | 448×320 | 2.5s | `714902` | 339.7 KB | **✓ SUCCESS** |
| **06** | Zombies | `06_zombie_apocalypse_encounter` | `06_zombie_apocalypse_encounter.mp4` | 448×320 | 2.5s | `638201` | 152.1 KB | **✓ SUCCESS** |
| **07** | Multi-Scene Storyboard | `07_multiscene_harry_and_fem_malfoy` | `07_multiscene_harry_and_fem_malfoy.mp4` | 512×288 | 10.0s | `19980502` | 883.4 KB | **✓ SUCCESS** |

*All outputs are located in: `D:\AI\FreeVideo\examples_generated/<example_id>/` with sidecar `.txt` files containing full multimodal prompts, soundscapes, and parameter breakdowns.*

---

## 2. Multi-Tier Extended Suite (5s, 10s, 15s)

The multi-tier runner (`scripts/generate_multitier_examples.py`) renders 21 additional variations across 3 distinct duration tiers with unique pseudorandom seeds:

### Target Tier Architecture
* **Tier 5s (5.0 seconds):** Fast cinematic vignettes demonstrating motion continuity with concise timing.
* **Tier 10s (10.0 seconds):** Standard narrative length demonstrating complex multi-camera transitions and environmental audio progression.
* **Tier 15s (15.0 seconds):** Extended epic format showcasing prolonged character stability, dialogue delivery, and orchestral crescendos.

| Timestamp | Example ID | Category | Duration | Resolution | Seed | Size | Status |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| 2026-10-26 07:02 | `01_pet_cat_sunlight_5s` | Pets | 5.0s | 448x320 | `4213062072` | 0.39 MB | **SUCCESS** |
| 2026-10-26 07:41 | `02_human_cyberpunk_detective_5s` | Humans | 5.0s | 448x320 | `8326369373` | 0.27 MB | **SUCCESS** |
| 2026-10-26 08:03 | `03_mythical_phoenix_rebirth_5s` | Mythical Creatures | 5.0s | 448x320 | `919073486` | 0.66 MB | **SUCCESS** |
| 2026-10-26 08:43 | `04_giant_colossus_forest_5s` | Giant Creatures | 5.0s | 448x320 | `5507077249` | 0.44 MB | **SUCCESS** |
| 2026-10-26 09:05 | `05_alien_bioluminescent_entity_5s` | Aliens | 5.0s | 448x320 | `7154129379` | 0.56 MB | **SUCCESS** |
| 2026-10-26 09:45 | `06_zombie_apocalypse_encounter_5s` | Zombies | 5.0s | 448x320 | `6386582472` | 0.20 MB | **SUCCESS** |
| 2026-10-26 10:10 | `07_multiscene_harry_and_fem_malfoy_5s` | Multi-Scene Storyboard | 5.0s | 512x288 | `1944988579` | 0.35 MB | **SUCCESS** |
| 2026-10-26 10:48 | `01_pet_cat_sunlight_10s` | Pets | 10.0s | 448x320 | `4213067137` | 0.68 MB | **SUCCESS** |
| 2026-10-26 11:26 | `02_human_cyberpunk_detective_10s` | Humans | 10.0s | 448x320 | `8326374438` | 0.50 MB | **SUCCESS** |
| 2026-10-26 12:05 | `03_mythical_phoenix_rebirth_10s` | Mythical Creatures | 10.0s | 448x320 | `919078551` | 1.20 MB | **SUCCESS** |
| 2026-10-26 12:43 | `04_giant_colossus_forest_10s` | Giant Creatures | 10.0s | 448x320 | `5507082314` | 0.82 MB | **SUCCESS** |
| 2026-10-26 13:21 | `05_alien_bioluminescent_entity_10s` | Aliens | 10.0s | 448x320 | `7154134444` | 1.20 MB | **SUCCESS** |
| 2026-10-26 13:59 | `06_zombie_apocalypse_encounter_10s` | Zombies | 10.0s | 448x320 | `6386587537` | 0.36 MB | **SUCCESS** |
| 2026-10-26 14:39 | `07_multiscene_harry_and_fem_malfoy_10s` | Multi-Scene Storyboard | 10.0s | 512x288 | `1944993644` | 0.82 MB | **SUCCESS** |
| 2026-10-26 15:11 | `01_pet_cat_sunlight_15s` | Pets | 15.0s | 448x320 | `4213072202` | 0 MB | **FAILED** |
| 2026-10-26 15:42 | `02_human_cyberpunk_detective_15s` | Humans | 15.0s | 448x320 | `8326379503` | 0 MB | **FAILED** |
| 2026-10-26 20:56 | `scene01_voldemort_attack` | Malfoid Battle Story Scene | 4.0s | 448x256 | `20261001` | 0.23 MB | **SUCCESS** |
| 2026-10-26 21:16 | `scene02_harry_and_fem_sacrifice` | Malfoid Battle Story Scene | 4.0s | 448x256 | `20261002` | 0.39 MB | **SUCCESS** |
| 2026-10-26 21:52 | `scene03_smoke_clears_male_draco` | Malfoid Battle Story Scene | 4.0s | 448x256 | `20261003` | 0.24 MB | **SUCCESS** |
| 2026-10-26 22:11 | `scene04_draco_identity_crisis` | Malfoid Battle Story Scene | 3.5s | 448x256 | `20261004` | 0.18 MB | **SUCCESS** |
| 2026-10-26 22:47 | `scene05_turn_me_back_now` | Malfoid Battle Story Scene | 4.0s | 448x256 | `20261005` | 0.36 MB | **SUCCESS** |
| 2026-10-26 23:21 | `scene06_mcgonagall_intervention` | Malfoid Battle Story Scene | 4.0s | 448x256 | `20261006` | 0.24 MB | **SUCCESS** |
| 2026-10-26 23:43 | `scene07_fem_malfoy_returns` | Malfoid Battle Story Scene | 4.0s | 448x256 | `20261007` | 0.35 MB | **SUCCESS** |
| 2026-10-27 00:03 | `scene08_fem_malfoy_relief` | Malfoid Battle Story Scene | 3.5s | 448x256 | `20261008` | 0.26 MB | **SUCCESS** |
| 2026-10-27 00:19 | `scene09_how_dare_you` | Malfoid Battle Story Scene | 3.5s | 448x256 | `20261009` | 0.23 MB | **SUCCESS** |
| 2026-10-27 00:43 | `scene10_overpowered_finisher` | Malfoid Battle Story Scene | 4.5s | 448x256 | `20261010` | 0.57 MB | **SUCCESS** |
| 2026-10-27 00:43 | `malfoid_battle_story_master_39s` | Full Narrative Master Film | 39.0s | 448x256 (Crossfade Stitched) | `20261001-20261010` | 4.15 MB | **SUCCESS** |
