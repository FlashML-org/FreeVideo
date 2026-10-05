"""Small, Torch-free media/adapter request contract shared by CLI and ComfyUI."""
import hashlib
import json
import math
from pathlib import Path


TASKS = ('t2va', 'i2va', 'l2va', 'fl2va', 'ref2va', 'ref2va_audio', 'ref2va_av')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read(path):
    if path is None:
        return {}
    path = Path(path).resolve()
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Media request JSON exceeds 1 MiB')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Expected a version 1 FreeVideo media request')
    if set(value) - {'version', 'first', 'last', 'references', 'loras', 'conditioning_info'}:
        raise ValueError('Unknown media request fields')
    result = {'version': 1}
    def file_info(name):
        if not isinstance(name, str) or not name:
            raise ValueError('Expected a media/LoRA file path')
        source = Path(name).expanduser()
        if not source.is_absolute():
            source = path.parent / source
        source = source.resolve(strict=True)
        if not source.is_file() or not source.stat().st_size:
            raise ValueError('Input file is empty or not a file: ' + str(source))
        return {'path': str(source), 'sha256': digest(source), 'bytes': source.stat().st_size}
    for anchor in ('first', 'last'):
        if value.get(anchor):
            result[anchor] = file_info(value[anchor])
    for field in ('references', 'loras'):
        entries = value.get(field, [])
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError('At most 32 ' + field + ' per request')
        result[field] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Invalid ' + field + ' entry')
            allowed = {'path', 'kind'} if field == 'references' else {'path', 'strength', 'alpha'}
            if set(entry) - allowed:
                raise ValueError('Unknown ' + field + ' fields')
            row = file_info(entry.get('path'))
            if field == 'references':
                if entry.get('kind') not in ('image', 'video', 'audio'):
                    raise ValueError('Reference kind must be image, video or audio')
                row['kind'] = entry['kind']
            else:
                if Path(row['path']).suffix.lower() != '.safetensors':
                    raise ValueError('LoRA requires safetensors; pickle adapters are not loaded')
                for name, default in (('strength', 1.), ('alpha', None)):
                    number = entry.get(name, default)
                    if (number is None and name == 'strength') or (number is not None and
                            (isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number))):
                        raise ValueError('LoRA ' + name + ' must be finite')
                    row[name] = number
            result[field].append(row)
    if result['references'] and any(result.get(k) for k in ('first', 'last')):
        raise ValueError('Reference and keyframe layouts cannot be mixed; use separate requests')
    if value.get('conditioning_info') is not None:
        info = value['conditioning_info']
        if not isinstance(info, dict) or info.get('task') not in TASKS:
            raise ValueError('Invalid preencoded conditioning information')
        result['conditioning_info'] = info
    return result


def task_for(media):
    if media.get('conditioning_info'):
        return media['conditioning_info']['task']
    if media.get('references'):
        return 'ref2va'
    if media.get('first') and media.get('last'):
        return 'fl2va'
    return 'i2va' if media.get('first') else 'l2va' if media.get('last') else 't2va'


def cache_key(prompt, media, canvas):
    # LoRA affects the denoiser, not the encoder. Paths do not identify content.
    if not any(media.get(k) for k in ('first', 'last', 'references')):
        return prompt
    identity = {'version': 1, 'prompt': prompt, 'task': task_for(media),
                'width': canvas['width'], 'height': canvas['height'], 'frames': canvas['frames']}
    for key in ('first', 'last', 'references'):
        value = media.get(key)
        if value:
            identity[key] = [{k: v for k, v in row.items() if k != 'path'}
                             for row in value] if isinstance(value, list) else value['sha256']
    return 'multimodal:' + json.dumps(identity, sort_keys=True, ensure_ascii=False)


def verify_files(media):
    rows = [media[k] for k in ('first', 'last') if media.get(k)]
    rows += media.get('references', []) + media.get('loras', [])
    for row in rows:
        path = Path(row['path'])
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError('Input changed after inspection: ' + str(path))
