"""Attach the selected rewrite receipt, never the installation's latest job."""
import json
from pathlib import Path
import re


def attach(root, identity, run):
    from ..diagnostics import is_link, read_bounded
    from ..monitoring import save
    selected = identity.get('selected') if isinstance(identity, dict) else None
    identity = identity.get('report_id') if isinstance(identity, dict) else identity
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
        return
    parent = Path(root) / 'prompt-vlm-runs'
    path = parent / identity / 'prompt-rewrite.json'
    if is_link(parent) or is_link(path.parent) or is_link(path):
        return
    try:
        raw, truncated = read_bounded(path, 128 * 1024)
        row = json.loads(raw)
        if truncated or row.get('schema') != 'freevideo.prompt-vlm' or row.get('report_id') != identity:
            return
        row['video_input_version'] = selected if selected in ('original', 'rewritten') else 'unknown'
        save(Path(run) / 'prompt-rewrite.json', row)
    except (OSError, ValueError, AttributeError):
        pass  # Missing historical diagnostics must never block video generation.
