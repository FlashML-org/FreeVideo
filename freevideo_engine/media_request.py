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
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Expected a version 1 FreeVideo media request')
    if 'early_steps' in value:
        raise ValueError('early_steps is only allowed on video references')
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
        entries = value.get(field)
        if entries is None:
            entries = []
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError('At most 32 ' + field + ' per request')
        result[field] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Invalid ' + field + ' entry')
            if field != 'references' and 'early_steps' in entry:
                raise ValueError('early_steps is only allowed on video references')
            allowed = ({'path', 'kind', 'early_steps', 'sound', 'guide', 'keep_source_audio', 'use', 'range', 'segment'} if field == 'references'
                       else {'path', 'strength', 'alpha'})
            if set(entry) - allowed:
                raise ValueError('Unknown ' + field + ' fields')
            row = file_info(entry.get('path'))
            if field == 'references':
                if entry.get('kind') not in ('image', 'video', 'audio'):
                    raise ValueError('Reference kind must be image, video or audio')
                row['kind'] = entry['kind']
                if 'early_steps' in entry:
                    validate_early_steps(entry)
                    row['early_steps'] = entry['early_steps']
                if 'sound' in entry:
                    validate_sound(entry)
                    if not entry['sound']:
                        row['sound'] = False
                if 'guide' in entry:
                    validate_guide(entry)
                    # A false marker is not stored, so requests without a guide
                    # keep exactly the same rows and cache key as before.
                    if entry['guide']:
                        row['guide'] = True
                if 'keep_source_audio' in entry:
                    validate_keep_source_audio(entry, row['path'])
                    if entry['keep_source_audio']:
                        row['keep_source_audio'] = True
                from .original_audio import fields
                row.update(fields(entry))
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
    from .original_audio import original
    original(result)
    if result['references'] and any(result.get(k) for k in ('first', 'last')):
        raise ValueError('Reference and first/last keyframe layouts cannot be mixed in one request; '
                         'mark the clip that carries the motion with "guide": true instead')
    guides = [row for row in result['references'] if row.get('guide')]
    if len(guides) > 1:
        raise ValueError('At most one guide clip per request')
    # The packed layout needs the guide after the other references.
    result['references'].sort(key=lambda row: bool(row.get('guide')))
    if value.get('conditioning_info') is not None:
        info = value['conditioning_info']
        if not isinstance(info, dict) or info.get('task') not in TASKS:
            raise ValueError('Invalid preencoded conditioning information')
        result['conditioning_info'] = info
    return result


def task_for(media):
    if media.get('conditioning_info'):
        return media['conditioning_info']['task']
    if any(row.get('early_steps') != 0 for row in media.get('references', [])):
        return 'ref2va'
    if media.get('first') and media.get('last'):
        return 'fl2va'
    return 'i2va' if media.get('first') else 'l2va' if media.get('last') else 't2va'


def validate_early_steps(row):
    if row.get('kind') != 'video':
        raise ValueError('early_steps is only allowed on video references')
    if type(row['early_steps']) is not int or row['early_steps'] < 0:
        raise ValueError('Video reference early_steps must be an integer >= 0')


def validate_sound(row):
    if row.get('kind') != 'video':
        raise ValueError('sound is only allowed on video references')
    if type(row['sound']) is not bool:
        raise ValueError('Video reference sound must be true or false')


def validate_guide(row):
    """A guide is a video reference that shares the generated clip's time origin.

    It is encoded like a silent video reference, so it cannot carry its own
    audio or a delayed start.
    """
    if row.get('kind') != 'video':
        raise ValueError('guide is only allowed on video references')
    if type(row['guide']) is not bool:
        raise ValueError('Video reference guide must be true or false')
    if row['guide'] and 'early_steps' in row:
        raise ValueError('A guide clip cannot use early_steps')


def validate_keep_source_audio(row, path):
    """Keeping the guide's sound needs a guide clip that has an audio stream."""
    if type(row['keep_source_audio']) is not bool:
        raise ValueError('keep_source_audio must be true or false')
    if not row['keep_source_audio']:
        return
    if row.get('guide') is not True:
        raise ValueError('keep_source_audio is only allowed on a guide clip')
    from .guide_audio import has_audio
    if not has_audio(path):
        raise ValueError('keep_source_audio needs a guide clip with an audio stream')


def reference_stages(media):
    """Active references at each boundary; zero-step inputs never reach an encoder."""
    refs = media.get('references', [])
    for row in refs:
        if 'early_steps' in row:
            validate_early_steps(row)
    boundaries = sorted({0} | {row['early_steps'] for row in refs if 'early_steps' in row})
    return [(step, dict(media, references=[row for row in refs
             if 'early_steps' not in row or step < row['early_steps']])) for step in boundaries]


def cache_key(prompt, media, canvas):
    # LoRA affects the denoiser, not the encoder. Paths do not identify content.
    if not any(media.get(k) for k in ('first', 'last', 'references')):
        return prompt
    identity = {'version': 1, 'prompt': prompt, 'task': task_for(media),
                'width': canvas['width'], 'height': canvas['height'], 'frames': canvas['frames']}
    for key in ('first', 'last', 'references'):
        value = media.get(key)
        if value:
            identity[key] = [{k: v for k, v in row.items() if k not in ('path', 'keep_source_audio')}
                             for row in value] if isinstance(value, list) else value['sha256']
            if key == 'references':
                from .original_audio import identity as audio_identity
                for row in identity[key]:
                    if row.get('original_audio'):
                        row['original_audio'] = audio_identity(row['original_audio'])
    return 'multimodal:' + json.dumps(identity, sort_keys=True, ensure_ascii=False)


def verify_files(media):
    rows = [media[k] for k in ('first', 'last') if media.get(k)]
    rows += media.get('references', []) + media.get('loras', [])
    for row in rows:
        path = Path(row['path'])
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError('Input changed after inspection: ' + str(path))
