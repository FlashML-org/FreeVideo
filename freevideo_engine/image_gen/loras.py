"""Shared image LoRA admission; names resolve only through ComfyUI's libraries."""
import json
import math
from pathlib import Path

MAX_LORAS = 8
MAX_NAME = 512
MIN_STRENGTH, MAX_STRENGTH = -2., 2.
ERRORS = frozenset(('invalid_loras', 'invalid_lora_strength', 'duplicate_lora',
                    'too_many_loras', 'lora_not_found', 'invalid_lora_model'))
MESSAGE_ZH = {
    'invalid_loras': 'LoRA 设置有误，请重新选择文件。',
    'invalid_lora_strength': 'LoRA 强度必须在 -2 到 2 之间。',
    'duplicate_lora': '同一个 LoRA 只能选择一次，请移除重复项。',
    'too_many_loras': '一张图片最多使用 8 个 LoRA，请减少数量。',
    'lora_not_found': 'LoRA 文件找不到，请重新选择。',
    'invalid_lora_model': '此 LoRA 不适用于图片生成，请换一个。',
}
MESSAGE_EN = {
    'invalid_loras': 'The LoRA selection is invalid. Choose the files again.',
    'invalid_lora_strength': 'LoRA strength must be between -2 and 2.',
    'duplicate_lora': 'Select each LoRA only once. Remove the duplicate.',
    'too_many_loras': 'Use at most eight LoRAs per picture.',
    'lora_not_found': 'The LoRA file could not be found. Choose it again.',
    'invalid_lora_model': 'This LoRA cannot be used for pictures. Choose another one.',
}


def validate_selection(value):
    """Canonical active items, with the same checks for HTTP and in-process jobs."""
    if not isinstance(value, list):
        raise ValueError('invalid_loras')
    if len(value) > MAX_LORAS:
        raise ValueError('too_many_loras')
    result, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) - {'name', 'strength', 'enabled'}:
            raise ValueError('invalid_loras')
        name, strength = row.get('name'), row.get('strength', 1.)
        if type(row.get('enabled', True)) is not bool:
            raise ValueError('invalid_loras')
        if (not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME
                or any(ord(c) < 32 for c in name) or ':' in name
                or any(part in ('', '.', '..') for part in name.replace('\\', '/').split('/'))
                or not name.lower().endswith('.safetensors')):
            raise ValueError('invalid_loras')
        if (type(strength) not in (int, float) or not MIN_STRENGTH <= strength <= MAX_STRENGTH
                or not math.isfinite(strength)):
            raise ValueError('invalid_lora_strength')
        if not row.get('enabled', True):
            continue
        if name in seen:
            raise ValueError('duplicate_lora')
        seen.add(name)
        # Match resolve_loras: zero strength does not load weights or add triggers.
        if strength != 0:
            result.append(dict(name=name, strength=float(strength)))
    return result


def check_file(path):
    """Image counterpart of lora_check.check_installed_file, without exposing paths."""
    from .qwen_lora import inspect_file
    try:
        result = inspect_file(path)
    except (OSError, ValueError):
        result = dict(ok=False, reason='unreadable')
    usable = bool(result['ok'])
    return dict(usable=usable, reason=None if usable else result['reason'], precision=None,
                **({} if usable else {'message_zh': '此 LoRA 不适用于图片生成。'}))


def resolve_paths(value, *, folders=None):
    rows = validate_selection(value)
    if not rows:
        return []
    if folders is None:
        import folder_paths as folders
    names = set(folders.get_filename_list('loras'))
    roots = [Path(root).resolve() for root in folders.get_folder_paths('loras')]

    def resolve(name):
        try:
            path = Path(folders.get_full_path_or_raise('loras', name)).resolve(strict=True)
        except (OSError, ValueError, KeyError):
            raise ValueError('lora_not_found') from None
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError('invalid_loras')
        if not path.is_file():
            raise ValueError('lora_not_found')
        if path.suffix.lower() != '.safetensors':
            raise ValueError('invalid_loras')
        return path

    if any(row['name'] not in names for row in rows):
        raise ValueError('lora_not_found')
    from ..comfy_assets import resolve_loras
    resolved = resolve_loras(json.dumps(rows), names, resolve)
    return [dict(name=row['name'], **item) for row, item in zip(rows, resolved)]


def resolve_selection(value, *, folders=None):
    result = resolve_paths(value, folders=folders)
    for item in result:
        if not check_file(item['path'])['usable']:
            raise ValueError('invalid_lora_model')
    return result


def check_named_file(name, *, folders=None):
    return check_file(resolve_paths([dict(name=name)], folders=folders)[0]['path'])
