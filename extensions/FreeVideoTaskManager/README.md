# FreeVideo Task Manager — v0.1.0 (preview)
Independent ComfyUI frontend extension targeting FreeVideo 0.6.8. No changes to upstream files or engine lifecycle.

## Installation
Place this folder in ComfyUI/custom_nodes/FreeVideoTaskManager. Restart ComfyUI **only when the queue and agent activity are idle**. Reload the browser; select Tasks in the main FreeVideo navigation. To remove, stop ComfyUI when idle, remove this folder, and start normally. Do not restart during a generation.

## Design
- Native top bars, Appearance settings and Tasks expose a shared reactive language selector. All three languages switch without reloading through a reversible, scoped UI adapter (web/native_pt.js) and the generated compatibility catalog (web/native_locales.js). The original startup locale is preserved internally, so native lazy-loaded modules remain consistent. Known navigation, Create controls and common settings labels are translated; specialized messages and views are not fully covered. Native palette language commands outside the shared selector still use the original reload behavior. No original source files, prompts or workflow data are changed. The Tasks header includes Creations, which opens the original library after leaving the dashboard.
- The sun/moon control follows the shared FreeVideo theme, via FreeVideoShell.setTheme and the native freevideo.theme preference. The dashboard no longer stores a separate theme.
- The extension also replaces the native Settings sun-like glyph with a cog and adds a separate theme toggle next to Settings. Existing Settings behavior is unchanged; no upstream files are edited.
- Local UI string catalog (web/i18n.js): Brazilian Portuguese, English and Simplified Chinese. The globe selector beside the theme icon changes the shared language immediately, saves the preference in the current tab's sessionStorage and preserves filters and task monitoring. A fresh launch (freevideo=launch) or new independent tab honors an explicit freevideo_lang parameter; otherwise detects browser locale and falls back to English. Refreshing the current tab retains its selection. Old global language preferences are ignored. Native job names and unknown engine diagnostic strings remain unchanged. The native application itself still supports only English and Chinese; the reactive adapter is a compatibility layer, not a complete upstream localization.
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

Verified on Windows 10 with an NVIDIA RTX 5060 Ti 16 GB and FreeVideo 0.6.8: the dashboard displayed live generation progress and hardware metrics, both themes rendered, and the user confirmed the dashboard language menu visually. The native Portuguese overlay and Creations shortcut still need a visual acceptance check. The offline checks do not replace the live-job acceptance tests below.

Upstream discussion: https://github.com/FlashML-org/FreeVideo/issues/1054. The prototype was developed with AI assistance. Upstream main and other platforms still require validation.

### Locale validation

The compatibility catalog is derived from FreeVideo's Apache-2.0 licensed UI
strings and the initial locale catalogs on the separate `feat/native-i18n` branch.
To regenerate it from matching source trees, run from the extension directory:

```sh
node scripts/build_native_locales.mjs <FreeVideo-web-directory> <native-locales-directory>
```

The optional Windows Chromium smoke test uses disposable profiles and the
running local server (port 8188), without submitting generation requests:

```powershell
node tests/browser_locale_smoke.cjs pt-BR en-US zh-CN
```

Verified first-launch navigation, locale badge and generation-button rendering
in fresh Chrome profiles for these three locales. Seventeen JavaScript
regression tests and one Python host test also passed. Full live switching
coverage across specialized views and destructive task-action acceptance
remain outstanding.

1. Idle restart and confirm Tasks appears in each native view; switch themes and back.
2. Generate a small video from UI and agent, verify progress, queue, ETA, origin and history.
3. Verify no interruptions when opening or closing Tasks.
4. Queue disposable jobs to verify confirmed targeted cancellation, chain resume, and video retry; never test on valuable jobs.
5. Compare hardware values to native monitor and Task Manager, and check browser console.
6. Assess upstream extension API, translations and cross-platform compatibility before proposing PR.
