"""Optional Generate inputs that a workflow from an older version can hold in a form the node refuses.

The inputs below were appended after the first public workflow, so an older save holds an empty or
missing value in their place and ComfyUI refuses the whole request before the node runs. Each one
falls back to its default. Prompt, size, duration, seed and step counts are not here: a wrong value
in those is a real error and keeps its message.
"""
import json
import time

from .long_paths import io_path

# name -> (kind, default, options); kept equal to the node's inputs by tests/test_workflow_repairs.py.
TOLERATED = {
    'force_regenerate': ('bool', False, None),
    'preview': ('bool', False, None),
    'refine_from': ('text', '', None),
    'upscale_from': ('text', '', None),
    'upscale_to': ('text', '', None),
    'continue_from': ('text', '', None),
    'first_frame_reference': ('bool', False, None),
    'voice_reference': ('bool', False, None),
    'hide_tail': ('bool', False, None),
    'step_cache': ('choice', 'none', ('none', 'easycache-av', 'fbcache')),
    'upscale_factor': ('choice', '2', ('2', '1.5', '1.2')),
}
KEPT = 50


def tolerated_value(name, value):
    kind, default, options = TOLERATED[name]
    if kind == 'bool':
        return value if isinstance(value, bool) else default
    if kind == 'text':
        return value if isinstance(value, str) else default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = '%g' % value
    return value if value in options else default


def repair_inputs(inputs):
    """Reset the tolerated inputs that hold an unusable value; returns the names changed.

    A value that is a link (a list) or an input the request does not have is left as it is."""
    changed = []
    for name in TOLERATED:
        if name not in inputs or isinstance(inputs[name], list):
            continue
        value = tolerated_value(name, inputs[name])
        if value != inputs[name] or type(value) is not type(inputs[name]):
            was_number = isinstance(inputs[name], (int, float)) and not isinstance(inputs[name], bool)
            if not (was_number and value == '%g' % inputs[name]):
                changed.append(name)
            inputs[name] = value
    return changed


def record(engine_root, origin, names):
    """Keep a short history of what was reset for the diagnostic report (logs/workflow-repairs.jsonl)."""
    if not names:
        return
    logs = io_path(engine_root) / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    history = logs / 'workflow-repairs.jsonl'
    line = json.dumps(dict(at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), origin=origin, reset=sorted(set(names))))
    rows = history.read_text(encoding='utf-8').splitlines()[-(KEPT - 1):] if history.is_file() else []
    history.write_text('\n'.join([*rows, line]) + '\n', encoding='utf-8')
