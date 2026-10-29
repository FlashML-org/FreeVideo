"""File-backed ComfyUI media bundles; no tensor or model imports."""
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from . import launcher_bridge
from .comfy_continuation import optional_text
from .monitoring import thermal_slowdown, shared_gpu_note, worker_gpu
from .paths import data_root
from .support_report import encoder_sizes


def saved_video(output, output_directory):
    """Name the saved file for ComfyUI's preview/history; the file is never copied or rewritten."""
    output = Path(output).resolve()
    relative = output.relative_to(Path(output_directory).resolve())
    if not output.is_file():
        raise FileNotFoundError(output)
    # ComfyUI adds executed outputs to its assets itself, and only while the user has assets on;
    # FreeVideo never writes to the asset database, so an off switch stays off.
    return dict(filename=relative.name, subfolder=relative.parent.as_posix(), type='output')


EXTENSIONS = {
    'image': {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'},
    'video': {'.mp4', '.mov', '.webm', '.mkv', '.m4v'},
    'audio': {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus'},
}
REFERENCE_INPUTS = ('reference', *(f'reference_{i}' for i in range(2, 9)))


def resolve_loras(serialized, names, resolve, previous=None):
    """Resolve a UI stack through Comfy's registered libraries, never raw paths."""
    serialized = optional_text(serialized, '[]')
    if not isinstance(serialized, str) or len(serialized) > 128 * 1024:
        raise ValueError('Invalid LoRA selection')
    rows = json.loads(serialized)
    result = list(previous or [])
    if not isinstance(rows, list) or len(rows) + len(result) > 32:
        raise ValueError('Use at most 32 LoRAs per request')
    available = set(names)
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'name', 'strength', 'enabled'}:
            raise ValueError('Invalid LoRA item')
        if type(row.get('enabled', True)) is not bool:
            raise ValueError('LoRA enabled must be a boolean')
        if not row.get('enabled', True):
            continue
        name, strength = row.get('name'), row.get('strength', 1.)
        if (isinstance(strength, bool) or not isinstance(strength, (int, float))
                or not math.isfinite(strength) or not -100 <= strength <= 100):
            raise ValueError('LoRA strength must be a finite number between -100 and 100')
        if not isinstance(name, str) or name not in available or not name.lower().endswith('.safetensors'):
            raise ValueError('Select an available safetensors LoRA from your ComfyUI model folders')
        if strength != 0:
            result.append({'path': str(Path(resolve(name)).resolve()), 'strength': strength})
    return result


def media_kind(filename):
    suffix = Path(filename).suffix.lower()
    for kind, extensions in EXTENSIONS.items():
        if suffix in extensions:
            return kind
    raise ValueError('Choose a supported image, video or audio file')


def resolve_assets(serialized, input_directory):
    serialized = optional_text(serialized, '[]')
    if not isinstance(serialized, str) or len(serialized) > 128 * 1024:
        raise ValueError('Invalid media selection')
    rows = json.loads(serialized)
    if not isinstance(rows, list) or len(rows) > 32:
        raise ValueError('Use at most 32 media items')
    root = Path(input_directory).resolve()
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'file', 'role', 'enabled', 'cast', 'early_steps', 'sound', 'guide', 'keep_source_audio', 'depth',
                                                    'use', 'range', 'segment'}:
            raise ValueError('Invalid media item')
        if 'depth' in row:
            if type(row['depth']) is not bool:
                raise ValueError('Media depth must be a boolean')
            if (row.get('role') != 'reference' or not isinstance(row.get('file'), str)
                    or media_kind(row['file']) != 'video'):
                raise ValueError('depth is only allowed on video references')
        # The character an @ mention added the row for; a continued part keeps one picture per character.
        if 'cast' in row and (set(row['cast'] if isinstance(row['cast'], dict) else ('?',)) != {'id'}
                              or not isinstance(row['cast']['id'], str) or not 0 < len(row['cast']['id']) <= 128):
            raise ValueError('Invalid media item')
        if type(row.get('enabled', True)) is not bool:
            raise ValueError('Media enabled must be a boolean')
        if not row.get('enabled', True):
            continue
        name, role = row.get('file'), row.get('role')
        if (not isinstance(name, str) or not name or '\x00' in name or '\\' in name
                or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
                or '..' in PurePosixPath(name).parts or ':' in name):
            raise ValueError('Media must be a relative file inside ComfyUI input')
        path = (root / name).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError('Media link points outside ComfyUI input') from None
        if not path.is_file():
            raise ValueError('Upload or select the missing media file: ' + name)
        kind = media_kind(name)
        from .original_audio import fields
        audio_fields = fields(dict(row, kind=kind))
        if role in ('first', 'last'):
            if kind != 'image':
                raise ValueError('First and last frames must be images')
            if role in result:
                raise ValueError('Select only one ' + role + ' frame')
            result[role] = str(path)
        elif role == 'reference':
            reference = {'kind': kind, 'path': str(path), **audio_fields}
            if row.get('depth') is True:
                reference.update(depth=True, early_steps=3)
            if cast_id(row) is not None:
                reference['cast'] = cast_id(row)   # only a continued part reads it (comfy_media.continued)
            if 'early_steps' in row:
                from .media_request import validate_early_steps
                validate_early_steps(dict(row, kind=kind))
                reference['early_steps'] = row['early_steps']
            if 'sound' in row:
                from .media_request import validate_sound
                validate_sound(dict(row, kind=kind))
                if not row['sound']:
                    reference['sound'] = False
            if 'guide' in row:
                from .media_request import validate_guide
                validate_guide(dict(row, kind=kind))
                if row['guide']:
                    reference['guide'] = True
            if 'keep_source_audio' in row:
                from .media_request import validate_keep_source_audio
                validate_keep_source_audio(dict(row, kind=kind), path)
                if row['keep_source_audio']:
                    reference['keep_source_audio'] = True
            result.setdefault('references', []).append(reference)
        else:
            raise ValueError('Media role must be first, last or reference')
    from .original_audio import original
    original(result)
    # Mixing keyframes and references is checked when the video is made (comfy_media.export):
    # a part that continues a video uses its keyframes as pictures.
    return result


def continued_assets(serialized):
    """The panel rows a continued part uses: enabled image references in panel order, at most three
    (continuation.pick_pictures: with two or more characters, one picture each).

    Its frames and sounds stay in the panel for parts that start fresh, but never reach a continued part's checks.
    The one exception is a sound used as is: every part plays its own range of it (original_audio_chain.block_prompt)."""
    from .continuation import pick_pictures
    try:
        rows = json.loads(serialized)
    except (TypeError, ValueError):
        return '[]'
    pictures, original = [], []
    for row in rows if isinstance(rows, list) else ():
        if (isinstance(row, dict) and row.get('enabled', True) is True and row.get('role') == 'reference'
                and isinstance(row.get('file'), str)):
            if row.get('use') == 'original':
                original.append(dict(file=row['file'], role='reference', use='original'))
                continue
            try:
                image = media_kind(row['file']) == 'image'
            except ValueError:
                image = False
            if image:
                pictures.append(row)
    return json.dumps([_kept_row(row) for row in pick_pictures(pictures, cast_id)] + original)


def cast_id(row):
    """The character a panel row belongs to (the media list marks rows added by an @ mention), else None."""
    cast = row.get('cast') if isinstance(row, dict) else None
    value = cast.get('id') if isinstance(cast, dict) else None
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _kept_row(row):
    kept = dict(file=row['file'], role='reference')
    if cast_id(row) is not None:
        kept['cast'] = dict(id=cast_id(row))
    return kept


def connect_assets(assets, *, first=None, last=None, reference=None, reference_audio=None, audio_use='reference',
                   **images):
    """Keep connected IMAGE/AUDIO values native until request input export."""
    result = dict(assets)
    if set(images) - set(REFERENCE_INPUTS[1:]):
        raise TypeError('Unknown reference image input')
    images = dict(reference=reference, **images)
    from .original_audio import fields, original
    audio_fields = fields(dict(kind='audio', use=audio_use))
    if audio_fields and reference_audio is None:
        raise ValueError('Connect audio before selecting Use as is')
    original(dict(references=[*result.get('references', []), *([dict(kind='audio', **audio_fields)]
                  if reference_audio is not None else [])]))
    connected = {name: value for name, value in (('first', first), ('last', last),
                 *((name, images.get(name)) for name in REFERENCE_INPUTS), ('reference_audio', reference_audio))
                 if value is not None}
    for name in ('first', 'last'):
        if name in result and name in connected:
            raise ValueError('Choose the connected ' + name + ' frame or the uploaded one; disable the duplicate')
    count = len(result.get('references', [])) + int(reference_audio is not None)
    for value in images.values():
        if value is not None:
            shape = getattr(value, 'shape', ())
            count += int(shape[0]) if len(shape) == 4 else 1
    if count > 32:
        raise ValueError(f'There are {count} references and the limit is 32. '
                         'For a long image batch, connect a clip to the Reference node\'s video input; a clip counts as one reference.')
    if connected:
        result['_connected'] = connected
        if audio_fields:
            result['_audio_use'] = 'original'
    return result


def sampling_plan(report):
    """The report's sampling plan, with the step cache its first pass used (names High and Max)."""
    from .effort_forecast import report_step_cache
    plan, cache = report.get('sampling_plan'), report_step_cache(report)
    return dict(plan, step_cache=cache) if isinstance(plan, dict) and cache else plan


def output_summary(report, relative_video):
    video = report.get('video', {})
    encoding = report.get('encoding', {})
    ram = report.get('resources', {}).get('ram', {})
    encoding_ram = report.get('encoding_resources', {}).get('ram', {})
    metric = ram.get('ram_guard_metric')
    values = [r.get('process_tree_peak_guard_bytes') for r in (ram, encoding_ram)
              if r.get('ram_guard_metric') == metric]
    values = [v for v in values if isinstance(v, (int, float)) and v >= 0]
    gpu_values = [phase.get('torch_peak_reserved_bytes') for phase in (video, encoding)]
    gpu_values = [v for v in gpu_values if isinstance(v, (int, float)) and v >= 0]
    result = dict(sample_seconds=video.get('sample_seconds'), request_seconds=report.get('request_seconds'),
                vram_peak_bytes=max(gpu_values) if gpu_values else None,
                ram_peak_bytes=max(values) if values else None, ram_metric=metric,
                conditioning_cache_hit=report.get('encoding', {}).get('cache_hit', False),
                gpu_budget_bytes=report.get('profile', {}).get('policy', {}).get('gpu_budget_bytes'),
                gpu_total_bytes=report.get('profile', {}).get('policy', {}).get('hardware', {}).get('vram_total'),
                # A small size is sampled larger and scaled back; people see the size they asked for.
                geometry=dict(report.get('geometry', {}), **(report.get('output_size') or {})),
                sampling_plan=sampling_plan(report),
                low_memory=bool(report.get('low_memory_mode')),
                thermal=thermal_slowdown(worker_gpu(report)),
                shared_gpu=shared_gpu_note(worker_gpu(report)),
                reference_trims=report.get('encoding', {}).get('reference_trims') or [],
                preview=bool(report.get('preview')), upscaled_preview=bool(report.get('upscaled_preview')),
                video=relative_video.as_posix(), report=relative_video.with_suffix(
                    '.debug.json' if report.get('diagnostic_file') == relative_video.with_suffix('.debug.json').name
                    else '.request.json').as_posix())
    if report.get('keep_source_audio_failed'):
        result['keep_source_audio_failed'] = True
    if type(report.get('output_frames')) is int and report['output_frames'] > 0:
        # Resource reports keep the generated canvas; result cards show only saved frames.
        result['geometry'].update(frames=report['output_frames'],
                                  seconds=report['output_frames'] / (result['geometry'].get('fps') or 24))
    if video.get('original_audio'):
        result['original_audio'] = {k: v for k, v in video['original_audio'].items() if k != 'path'}
    checkpoint = encoding.get('checkpoint') or {}
    sizes = checkpoint.get('bytes'), checkpoint.get('expected_bytes')
    # Reports are read back from disk; a malformed one gets no notice rather than failing the library.
    if checkpoint.get('tested_size') is False and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in sizes):
        try:
            # Match bootstrap.offline_installation.
            offline_installation = (data_root().parent / 'portable.json').is_file()
        except OSError:
            offline_installation = False
        result['encoder_file'] = dict(bytes=checkpoint['bytes'], expected_bytes=checkpoint['expected_bytes'],
                                      sizes=list(encoder_sizes(checkpoint['bytes'], checkpoint['expected_bytes'])),
                                      offline_installation=offline_installation, launcher=bool(os.environ.get(launcher_bridge.ENV)))
        if offline_installation and isinstance(encoding.get('encoder'), str):
            result['encoder_file']['path'] = encoding['encoder']
    if report.get('preview'):
        result['preview_geometry'] = report['preview'].get('geometry')
    if report.get('device_backend') == 'mps':
        policy = report.get('profile', {}).get('policy', {})
        hardware = report.get('runtime_hardware', {})
        # RSS is a process observation, not total unified or Metal memory. MPS
        # exposes current allocations but no allocator peak; never relabel it.
        result.update(device_backend='mps', memory_model='unified',
            unified_total_bytes=hardware.get('ram_total'),
            unified_reserve_bytes=policy.get('reserve_bytes'),
            vram_peak_bytes=None, gpu_budget_bytes=None, gpu_total_bytes=None)
    continuation = report.get('continuation')
    if isinstance(continuation, dict):
        # Only chain counters, never retained source paths or conditioning.
        counters = {key: continuation[key] for key in
                    ('overlap_frames', 'new_frames', 'frame_origin', 'audio_origin', 'segment', 'audio_lead_in_samples')
                    if type(continuation.get(key)) is int}
        if counters:
            if 'overlap_frames' in counters and 'new_frames' in counters:
                counters['requested_frames'] = counters['overlap_frames'] + counters['new_frames']
            result['continuation'] = counters
    return result
