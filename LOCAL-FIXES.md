# Local patches — FreeVideo macOS fast2 (this checkout is NOT a clean upstream clone)

**Original upstream source:** https://github.com/FlashML-org/FreeVideo (cloned 2026-11-28)
**Base commit:** `git rev-parse HEAD` at the time of this file
**Status:** 2 fixes to make fast2 mode (the "极速档" / Fastest mode) work on Apple Silicon. Originally un-patched; do **not** delete this file.

## The two fixes (do not lose on upgrade)

All fixes are in-place edits under `freevideo_engine/`. The exact diff is saved next to this README as `fixes-2026-11-28-fast2-mac.patch` — apply with `git apply` from this directory.

1. `freevideo_engine/macos_runtime.py`
   - Line ~280: `self.cursor = ScheduleCursor(times)` → `self.cursor = None if fast is not None else ScheduleCursor(times)`
   - Line ~337: wrap the `register_forward_pre_hook(self.cursor.before, ...)` call in `if self.cursor is not None:`
   - Line ~627: wrap `self.cursor.reset(...)` in `if self.cursor is not None:`
   - Why: in fast2 mode the scheduler uses pi-H3's RAW_GRID `(.93, .56, 0.)`. Building the *default*-schedule cursor makes the sampled timestep mismatch ("The request changed the precomputed AdaLN schedule"). CUDA already keeps the cursor None in fast mode; this aligns the macOS runtime.

2. `freevideo_engine/macos_stages.py`
   - In the `phase == 'decode' and value.get('fast_tier') is not None:` branch, before `from .dwd_decode import pixels_to_file`:
     ```python
     from .macos_vdn import activate
     activate()
     ```
   - Why: the fast2 decode subprocess imports `src.inference.render` (VDN Mac overlay) at module import time. Without activate, it crashes with `ModuleNotFoundError: No module named 'src'`.

## Upgrading

`git pull` (or `git fetch && git merge` / `git rebase`) **keeps this README and the patch file** (untracked), but it **overwrites the two files with upstream versions** — wiping these fixes.

Safe upgrade flow:
```bash
cd /Users/cuijs/ComfyUI/custom_nodes/FreeVideo
git stash                       # stash local modifications
git pull                        # bring upstream version
git stash pop                   # try to restore local fixes
# if the pop conflicts (likely): discard the stashed edits, apply the saved diff instead
git checkout -- freevideo_engine/macos_runtime.py freevideo_engine/macos_stages.py
git apply ./fixes-2026-11-28-fast2-mac.patch
```
(If a future upstream release includes these fixes upstream, `git apply` will fail with "already applied" — then it's a no-op re-check, not a problem; see upstream release notes.)

**Better long-term:** send a PR upstream with these two fixes (or ask the FreeVideo team). When upstream merges them, delete this README and patch file and drop the manual fixes.
