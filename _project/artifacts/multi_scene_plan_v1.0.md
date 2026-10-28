# Multi-Scene Prompts with Review Gate — Plan v1.0

## Goal
Write several scene prompts up front (**+ Add scene**). Each scene continues the previous clip
from its saved continuation record. The run pauses only to ask: *"Is this the clip you want to
continue from?"* → **Continue**, **Redo**, **Edit & redo**, or **Stop**.

![Wireframe](C:/Users/Administrator/.gemini/antigravity-ide/brain/2eb6f7ff-19fa-4858-8117-365f09d39a25/multi_scene_wireframe_1792990165070.jpg)

## What already exists (reused, not rewritten)
| Piece | File | Already does |
|---|---|---|
| Chain records | [chain_store.py](file:///d:/AI/FreeVideo/freevideo_engine/chain_store.py#L50-L123) | `blocks` payload with **per-block `text`, `frames`, `seed`, `after`** |
| Scheduler | [chain_runner.py](file:///d:/AI/FreeVideo/freevideo_engine/chain_runner.py#L94-L101) | Runs block N only after block `after` is `done`; queues via ComfyUI |
| Continuation | [comfy_chain.py](file:///d:/AI/FreeVideo/freevideo_engine/comfy_chain.py#L46-L74) | Fills `continue_from` with the previous block's MP4; forces two-pass, no preview |
| Join + seam audio | `chain_join.py`, `chain_leads` | Joins parts into one video, repairs audio seams |
| UI client | [studio_chain.js](file:///d:/AI/FreeVideo/web/studio_chain.js) | Submit / poll / cancel / resume a chain |

So "use the data already loaded" is already covered: each block continues from the previous
block's saved continuation tail (latents + audio), not from re-encoding the video.

## What is missing
1. **UI:** only one prompt; the chain splits a long *duration* into identical-prompt parts.
2. **Review gate:** the runner goes straight from block N to N+1 with no pause.
3. **Redo a single block** without restarting the chain.

## Proposed changes

### Backend — review gate
- **`chain_store.validate`**: accept optional `review: true` on the chain (default `false` → current behaviour unchanged).
- **New block state `review`**: when a block finishes and `review` is on and more blocks remain,
  the block goes to `review`, chain state → `awaiting_review` (new, *not* in `ACTIVE`, so the queue
  is freed and other jobs can run while you watch).
- **`_current()`**: a block whose `after` is in `review` is not runnable.
- **New routes** (new file `chain_review.py`, keeps `chain_runner.py` from growing — it is already 705 lines, over the 500 limit):
  - `POST /freevideo/chain/{id}/approve` → block `review` → `done`, chain re-queued.
  - `POST /freevideo/chain/{id}/redo` `{seed?, text?}` → block reset to `pending` (new seed by default), re-queued.
  - Stop = existing `/cancel`; joining what was approved so far = existing join on cancel.
- Edits to later scenes' prompts while waiting: `POST /freevideo/chain/{id}/blocks/{block}` `{text}` for `pending` blocks only.

### Frontend — scene list
- New module `web/studio_scenes.js` (keeps `studio.js` from growing further; it is 1237 lines).
- Scene cards: prompt, duration (snapped to `5 + 17n` frames), optional seed. **+ Add scene**, remove, drag to reorder.
- Toggle **"Ask me after each scene"** (on by default).
- Builds a `blocks` payload: `b1` (no `after`, or `after` = a saved work for "extend existing"), `b2.after = 'b1'`, …
- Review card: inline player of the block MP4 + 4 buttons. Uses the existing toast pattern, no `alert()`/`confirm()`.
- Scene list is saved in node properties so a page reload restores it.

### Limits / behaviour to know
- `MAX_PARTS = 5` today in [chain_store.py](file:///d:/AI/FreeVideo/freevideo_engine/chain_store.py#L20) → max 5 scenes per chain unless raised.
- All scenes share width/height (continuation requirement). Scene 1 must be text-to-video (no first/last/reference image) — the precheck added earlier turns violations into a notice.
- While waiting for review, the engine may unload models to free VRAM; continuing then pays a reload. Option: keep the resident engine warm for N minutes (configurable).

## Verification
- Unit-style: drive `ChainRunner.step()` with the existing fake host (`host_factory`) through `review → approve`, `review → redo`, `review → cancel`.
- Manual: 3 short scenes (2 s each, 448×320), approve 1, redo 2, approve, confirm joined MP4 and seam.

---
## Observations, feedback & tips
- The backend chain was designed for this; the review gate is the only real engine-side addition.
- `chain_runner.py` (705) and `studio.js` (1237) already exceed the 500-line rule — new logic goes in new modules; a split of those files is a separate task.
- Changing the prompt between scenes drifts more than a single prompt; the existing **First-frame reference** option helps keep identity.
- On approval this plan and the wireframe will be copied to `_project/artifacts/` and `_project/ui_wireframes/`.
