"""About how long a prompt rewrite takes on this computer, for the time beside Generate (no model is loaded here).

Every rewrite starts a worker that imports, loads the model, writes its answer and exits; the page's prestart only
does the imports ahead. So there are two cases: cold (imports, load, answer) and prestarted (load, answer).

This computer's own last rewrites decide when there are any (their reports in prompt-vlm-runs: elapsed_seconds and the
worker's import_seconds). Before the first, a prior from measured rewrites:
  RTX PRO 6000 under Linux, five runs each cold and prestarted at 24 and 8 GB: elapsed 12.3 to 12.9 s cold, 9.5 to 9.7 s prestarted;
  imports 3.4 s, load 2.5 to 2.8 s, first output 0.85 s, about 165 tokens at 38 to 42 tokens/s, the service 1.3 s.
  The answer's speed across cards: PRO 6000 about 40, RTX 3090 27.8 and RTX 3070 Ti 19.4 tokens/s (under Windows),
  fitted as 21.5 x speed^0.39 with stage_estimate's card speed (G_spec). Windows imports and loads as measured then.
The prior is rough on cards that were not measured; the first rewrite on this computer replaces it.
"""
import json
import math
import os
from pathlib import Path
from statistics import median

RECENT = 5
OUTPUT_TOKENS = 165
SERVICE_SECONDS = 1.3
IMPORT_SECONDS = {'Linux': 3.5, 'Windows': 6.}
LOAD_SECONDS = {'Linux': 2.7, 'Windows': 6.8}
FIRST_OUTPUT_SECONDS = .85        # on the RTX PRO 6000 (speed 5.27)
REFERENCE_SPEED = 5.274
TOKENS_PER_SECOND = (21.5, .389)  # a x speed^b


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


_RECENT = {}


def recent(root, limit=RECENT):
    """This computer's last finished rewrites: [(elapsed seconds, import seconds)], newest first.

    The folder keeps every report, and the page asks often: the answer is kept until a report is added (the folder's
    modification time changes), unless a report could not be read yet: a run's folder appears before its report is
    written in it, which does not change this folder's time again."""
    folder = Path(root) / 'prompt-vlm-runs'
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return []
    cached = _RECENT.get((str(folder), limit))
    if cached and cached[0] == stamp:
        return list(cached[1])
    try:
        paths = sorted(folder.glob('*/prompt-rewrite.json'), key=lambda path: path.stat().st_mtime, reverse=True)
        folders = sum(1 for entry in os.scandir(folder) if entry.is_dir())
    except OSError:
        return []
    found, complete = [], folders == len(paths)
    for path in paths[:limit * 4]:
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            complete = False
            continue
        if not isinstance(report, dict) or report.get('action') != 'rewrite' or report.get('phase') != 'complete':
            continue
        elapsed = report.get('elapsed_seconds')
        imported = (report.get('diagnostics') or {}).get('import_seconds')
        if _positive(elapsed) and type(imported) in (int, float) and 0 <= imported < elapsed:
            found.append((float(elapsed), float(imported)))
        if len(found) == limit:
            break
    if complete:
        _RECENT[(str(folder), limit)] = (stamp, list(found))
    return found


def card_speed(hardware):
    from ..stage_estimate import _apple, _gpu_item, _params
    if not isinstance(hardware, dict) or _apple(hardware):
        return None
    item = _gpu_item(hardware, _params(None))
    if not item or not _positive(item.get('G_spec')):
        return None
    return item['G_spec'] * ((item.get('laptop_utilization_prior') or 1.) if item.get('laptop') else 1.)


def prior(hardware):
    """{cold, prestarted} seconds from the measured rewrites, or None for a card the table does not know (or a Mac)."""
    speed = card_speed(hardware)
    system = (hardware or {}).get('os') or (hardware or {}).get('system')
    if speed is None or system not in IMPORT_SECONDS:
        return None
    tokens_per_second = TOKENS_PER_SECOND[0] * speed ** TOKENS_PER_SECOND[1]
    first = FIRST_OUTPUT_SECONDS * (REFERENCE_SPEED / speed) ** .74
    prestarted = SERVICE_SECONDS + LOAD_SECONDS[system] + first + OUTPUT_TOKENS / tokens_per_second
    return dict(cold=prestarted + IMPORT_SECONDS[system], prestarted=prestarted)


def estimate(root, hardware):
    """{cold, prestarted, source: 'local' or 'prior'} seconds, rounded to a tenth, or None when nothing can be said."""
    rows = recent(root)
    if rows:
        prestarted = median(elapsed - imported for elapsed, imported in rows)
        imports = [imported for _, imported in rows if imported > 0]
        if imports:
            cold = prestarted + median(imports)
        else:
            guess = prior(hardware)
            cold = prestarted + (guess['cold'] - guess['prestarted'] if guess else IMPORT_SECONDS['Windows'])
        return dict(cold=round(cold, 1), prestarted=round(prestarted, 1), source='local')
    guess = prior(hardware)
    return dict(cold=round(guess['cold'], 1), prestarted=round(guess['prestarted'], 1), source='prior') if guess else None
