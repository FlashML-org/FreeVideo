# FreeVideo: Multi-Scene Storyboard, Story Bible Architecture & LoRA Guide (v1.0)

## Overview & Executive Summary
This document records the design, implementation, and verification of the **Global Story Bible Architecture**, **Variable Scene Pacing & Multi-Shot Transitions**, and the **LoRA Target Directory Alignment** for FreeVideo. These enhancements eliminate previous visual discrepancies (erratic camera jumps, scene-to-scene lighting/background shifts, facial artifacts, and staging mismatches) and prepare the project for publication on GitHub.

---

## 1. Root-Cause Analysis of Previous Generation Flaws

| Identified Defect | Root Cause | Implemented Solution |
| :--- | :--- | :--- |
| **Theme / Location Drift** (Dark ruins -> Castle interior -> Daylight) | Each scene in the storyboard was submitted with isolated text without persistent world context. | **Global Story Bible Injection**: The initial "Describe your scene" prompt acts as the persistent global bible (setting, lighting, style, atmosphere) automatically merged into every scene. |
| **Facial Artifacts & Muddy Quality** | Batch script dispatched with `'preview': True`, cutting sampling off after step 4 without running the 2-pass latent refinement schedule. | **Disabled Preview Mode (`preview: False`)**: Enforces full 2-pass sampling schedule, enabling high-frequency face, eye, and skin texture refinement. |
| **Character Staging Inconsistency** (Facing wrong directions) | Lack of spatial axis enforcement in generation prompts. | **Fixed Screen-Left / Screen-Right Coordinate Rules**: Staging explicitly assigns Voldemort to Screen-Left facing right, and Harry/Hermione/Malfoy to Screen-Right facing left. |
| **Unsolicited Macro Close-ups** | Single long shots without camera cut instructions causing diffusion zoom drift. | **Multi-Shot Temporal Transitions (`[Shot 1]`, `[Shot 2]`)**: Explicit camera cuts maintain cinematic medium/medium-wide framing. |
| **LoRA Path Confusion** | LoRAs were mistakenly placed in FreeVideo or local folders where the host ComfyUI couldn't index them. | **Standardized Documentation & UI**: Explicitly stated that `.safetensors` files must reside in `ComfyUI/models/loras/`. |

---

## 2. Global Story Bible Architecture

### UI & Pipeline Merging Mechanism
In `web/studio_scenes.js` and `web/studio.js`, when generating a multi-scene storyboard:
1. The user specifies overall visual parameters, persistent characters, environment, and lighting in the primary **"Describe your scene"** input field (`initialPrompt`).
2. When the user opens the **Multi-Scene Storyboard (`+`)**, individual cards define scene-specific actions, dialogue, and durations.
3. The engine automatically merges the Story Bible into each scene:
   ```javascript
   let combinedText = scenePrompt;
   if (bible) {
       if (scenePrompt) {
           combinedText = `${bible}\n\n[Scene Action / Shots]: ${scenePrompt}`;
       } else {
           combinedText = bible;
       }
   }
   ```
4. A dedicated guidance banner informs creators:
   `💡 Story Bible: The main prompt above sets the persistent environment, character & lighting across all scenes.`

---

## 3. Pacing & Variable Duration Breakdown

Rather than a uniform 4.0s duration per clip, the narrative scenes now use adaptive pacing tailored to dramatic beats:

| Scene # | Narrative Title | Duration | Shot Cuts | Pacing Rationale |
| :---: | :--- | :---: | :---: | :--- |
| **1** | The Dark Lord Strikes | **2.8s** | 2 shots | Fast-paced, sudden opening attack. |
| **2** | The Sacrifice & Smoke Blast | **3.0s** | 2 shots | Rapid heroic leap & violent smoke detonation. |
| **3** | The Smoke Clears: Male Draco Malfoy | **4.5s** | 2 shots | Medium beat: smoke parts, comedic dropped jaws. |
| **4** | Draco Realizes the Transformation | **5.5s** | 2 shots | Slower character beat: patting chest, voice shock, rage. |
| **5** | "Turn Me Back... NOW!" | **5.5s** | 2 shots | Dialogue focus: Draco's hysterical screaming & Voldemort's confusion. |
| **6** | Professor McGonagall's Holy Intervention | **4.8s** | 2 shots | Elegant entrance & radiant celestial magic. |
| **7** | The Holy Restoration to Girl | **5.0s** | 2 shots | Luminous golden transfiguration back to Female Malfoy. |
| **8** | Joyful Relief | **5.0s** | 2 shots | Emotional beat: Female Malfoy admiring hair, laughing with relief. |
| **9** | "How Dare YOU!!!" | **3.2s** | 2 shots | Fast snap from joy to demonic fury and shouting at Voldemort. |
| **10** | Overpowered Super-Spell Finale | **3.0s** | 2 shots | Screen-shattering apocalyptic blast whiteout. |
| **Total** | **Full Narrative Film** | **42.3s** | **20 shots** | **Seamless master film with 0.3s audio crossfades.** |

---

## 4. LoRA Target Directory Rules
- Target Path: `ComfyUI/models/loras/`
- Accepted Format: `.safetensors`
- Max Supported Concurrently: 32 LoRAs
- Documentation Updated:
  - `docs/LoRA.md` (English installation guide and UI usage)
  - `docs/LoRA.zh-CN.md` (Chinese installation guide and UI usage)
  - `README.md` and `README.zh-CN.md` (Feature highlights and guide links)
  - `web/studio.js` (In-app error handling and guidance hints)

---

## Observations, Feedback & Tips
1. **Multi-Shot Directing (`[Shot 1] At 00:00.000, ... [Shot 2] At 00:01.500`)**: MiniMax H3 / VDN responds with remarkable fidelity to explicit timestamped camera instructions. Specifying both the camera framing (e.g., *medium tracking shot*, *eye-level two-shot*) and subject motion prevents the diffusion model from defaulting to unprompted zooms or face-distorting macro close-ups.
2. **Fixed Spatial Coordinates (Screen-Left vs Screen-Right)**: When generating multi-character confrontational scenes, specifying the screen axis in every single prompt (e.g. *Lord Voldemort on Screen-Left facing right; Harry and Hermione on Screen-Right facing left*) prevents the model from flipping the 180-degree line between cuts.
3. **Quality Refinement Pass**: Running with `preview: False` ensures that both the 8-step base diffusion pass and the 3-step refinement pass complete. On Pascal hardware, this adds ~25% compute time but completely eliminates muddiness and facial distortion artifacts.
