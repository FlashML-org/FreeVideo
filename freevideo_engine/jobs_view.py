"""One list of generation jobs: running and waiting ones from the queue, finished ones from the journal.

Pure merging only. The caller supplies what the queue and the progress rows say; nothing here
touches an execution path, and no prompt or path is ever put into a row.
"""
import math

from .jobs_journal import CODE


def _percent(message):
    done, total = message.get('done'), message.get('total')
    if (all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (done, total))
            and total > 0):
        return max(0., min(100., 100. * done / total))
    return None


def build(running, pending, progress, journal):
    """running/pending: ComfyUI queue tuples (number, prompt_id, ...); progress: ProgressState.snapshot();
    journal: jobs_journal.read(). Returns rows newest-activity first: running, waiting by position, then finished."""
    by_prompt = {}
    for message in progress or ():
        if isinstance(message, dict) and message.get('prompt_id'):
            by_prompt[str(message['prompt_id'])] = message
    rows, seen = [], set()
    for status, queue in (('running', running), ('queued', pending)):
        for place, entry in enumerate(queue or ()):
            try:
                identity = str(entry[1])
            except (IndexError, TypeError):
                continue
            if identity in seen:
                continue
            seen.add(identity)
            message = by_prompt.get(identity, {})
            row = dict(id=identity, kind='video', status=status, position=0 if status == 'running' else place + 1)
            phase = message.get('phase')
            if isinstance(phase, str) and CODE.fullmatch(phase):
                row['phase'] = phase
            percent = _percent(message)
            if percent is not None:
                row['percent'] = percent
            rows.append(row)
    for entry in journal or ():
        if isinstance(entry, dict) and entry.get('id') and str(entry['id']) not in seen:
            seen.add(str(entry['id']))
            rows.append(dict(entry))
    return rows
