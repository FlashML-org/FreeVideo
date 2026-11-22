# FreeVideo Task Manager — v0.1.0 (preview)
Independent ComfyUI frontend extension targeting FreeVideo 0.6.8. No changes to upstream files or engine lifecycle.

## Installation
Place this folder in ComfyUI/custom_nodes/FreeVideoTaskManager. Restart ComfyUI **only when the queue and agent activity are idle**. Reload the browser; select Tasks in the main FreeVideo navigation. To remove, stop ComfyUI when idle, remove this folder, and start normally. Do not restart during a generation.

## Design
- Native top bars expose the shared language selector. English and Simplified Chinese use FreeVideo's existing language switch; Portuguese remains available only for Tasks. The Tasks header includes Creations, which opens the original library after leaving the dashboard.
- The sun/moon control follows the shared FreeVideo theme, via FreeVideoShell.setTheme and the native freevideo.theme preference. The dashboard no longer stores a separate theme.
- The extension also replaces the native Settings sun-like glyph with a cog and adds a separate theme toggle next to Settings. Existing Settings behavior is unchanged; no upstream files are edited.
- Local UI string catalog (web/i18n.js): Brazilian Portuguese, English and Simplified Chinese. The globe selector beside the theme icon changes the dashboard language immediately, saves the preference locally and preserves filters and task monitoring. With no saved selection, honors explicit freevideo_lang query parameter; otherwise detects browser locale and falls back to English. Native job names and unknown engine diagnostic strings remain unchanged. FreeVideo itself supports English and Chinese, not a global Portuguese translation.
- Uses the original task_store singleton and task_float sources (idempotent startup), agent_tasks adapter for agent activity, and studio_queue seededPrompt to clone an original video workflow for explicit retry.
- Full-screen independent view; native views remain mounted. No changes to the original navigation controller.
- GET FreeVideo monitor, jobs, chains, queue and progress; local read-only host route samples psutil non-blockingly, plus disk. GPU readings come from FreeVideo itself. History stores up to 240 samples (within 24 h) in browser storage, captured every five seconds while the dashboard and browser tab are visible (about 20 minutes of continuous samples). No persistent background daemon.
- Agent records are distinct from engine generation records, so totals count records, not unique underlying jobs. MCP is shown only if the server client identifies MCP; CLI stays CLI.
- Cancel uses native task/agent actions or the targeted native prompt/node endpoint with confirmation. Agent-controlled underlying workflow cancellation should use its agent activity row. Chain resume uses native endpoint; simple video retry requeues its frozen workflow if still in ComfyUI history (not checkpoint resume). No automatic retries. Image and agent retries are not yet implemented.
- Compatibility: internal FreeVideo JS exports and DOM selectors can change in future releases. Update-safe folder separation, not a promise of cross-version API compatibility. If exports change, opening safely errors without modifying the engine.
- No prompts are persisted by this extension. Hardware data only in localStorage. No external network or new Python dependency beyond ComfyUI's psutil.
## Validation before upstream PR
From the ComfyUI directory, offline checks (no generation is created or cancelled):

```powershell
node --test custom_nodes/FreeVideoTaskManager/tests/tasks.test.cjs
.venv/Scripts/python.exe -B custom_nodes/FreeVideoTaskManager/tests/test_host.py
```

Verified on Windows 10 with an NVIDIA RTX 5060 Ti 16 GB and FreeVideo 0.6.8: the dashboard displayed live generation progress and hardware metrics, both themes rendered, and the user confirmed the dashboard language menu visually. Twelve offline JavaScript tests and one Python host-metrics test passed. The native language shortcut and Creations shortcut still need a visual acceptance check. The offline checks do not replace the live-job acceptance tests below.

Upstream discussion: https://github.com/FlashML-org/FreeVideo/issues/1054. The prototype was developed with AI assistance. Upstream main and other platforms still require validation.

1. Idle restart and confirm Tasks appears in each native view; switch themes and back.
2. Generate a small video from UI and agent, verify progress, queue, ETA, origin and history.
3. Verify no interruptions when opening or closing Tasks.
4. Queue disposable jobs to verify confirmed targeted cancellation, chain resume, and video retry; never test on valuable jobs.
5. Compare hardware values to native monitor and Task Manager, and check browser console.
6. Assess upstream extension API, translations and cross-platform compatibility before proposing PR.
