"""File-backed ComfyUI media bundles; no tensor or model imports."""
import json
import logging
import math
from pathlib import Path, PurePosixPath, PureWindowsPath


def saved_video(output, output_directory):
    """Publish the same file to ComfyUI's preview/history and native assets."""
    output = Path(output).resolve()
    relative = output.relative_to(Path(output_directory).resolve())
    if not output.is_file():
        raise FileNotFoundError(output)
    entry = dict(filename=relative.name, subfolder=relative.parent.as_posix(), type='output')
    try:
        from comfy.cli_args import args
        if getattr(args, 'enable_assets', False):
            # ComfyUI enriches output entries itself when global assets are on.
            return entry
        from app.assets.services.ingest import register_file_in_place
        registered = register_file_in_place(abs_path=str(output), name=output.name, tags=['output'])
        entry['id'] = registered.ref.id
    except ImportError:
        logging.getLogger(__name__).info('Native asset registration is unavailable in this ComfyUI; video remains in output history.')
    except Exception:
        # An unavailable asset database must never invalidate a saved video.
        logging.getLogger(__name__).warning('FreeVideo video was saved, but ComfyUI asset registration failed: %s', output, exc_info=True)
    return entry


EXTENSIONS = {
    'image': {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'},
    'video': {'.mp4', '.mov', '.webm', '.mkv', '.m4v'},
    'audio': {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus'},
}


def resolve_loras(serialized, names, resolve, previous=None):
    """Resolve a UI stack through Comfy's registered libraries, never raw paths."""
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
                or not math.isfinite(strength) or not -4 <= strength <= 4):
            raise ValueError('LoRA strength must be a finite number between -4 and 4')
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
    if not isinstance(serialized, str) or len(serialized) > 128 * 1024:
        raise ValueError('Invalid media selection')
    rows = json.loads(serialized)
    if not isinstance(rows, list) or len(rows) > 32:
        raise ValueError('Use at most 32 media items')
    root = Path(input_directory).resolve()
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'file', 'role', 'enabled'}:
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
        if role in ('first', 'last'):
            if kind != 'image':
                raise ValueError('First and last frames must be images')
            if role in result:
                raise ValueError('Select only one ' + role + ' frame')
            result[role] = str(path)
        elif role == 'reference':
            result.setdefault('references', []).append({'kind': kind, 'path': str(path)})
        else:
            raise ValueError('Media role must be first, last or reference')
    if result.get('references') and any(name in result for name in ('first', 'last')):
        raise ValueError('Use first/last frames or references in this request. Disable unused media cards.')
    return result


def connect_assets(assets, *, first=None, last=None, reference=None, reference_audio=None):
    """Keep connected IMAGE/AUDIO values native until request input export."""
    result = dict(assets)
    connected = {name: value for name, value in (('first', first), ('last', last),
                 ('reference', reference), ('reference_audio', reference_audio))
                 if value is not None}
    for name in ('first', 'last'):
        if name in result and name in connected:
            raise ValueError('Choose the connected ' + name + ' frame or the uploaded one; disable the duplicate')
    count = len(result.get('references', [])) + int(reference is not None) + int(reference_audio is not None)
    if count > 32:
        raise ValueError('Use at most 32 references, including connected inputs')
    if count and any(
            name in result or name in connected for name in ('first', 'last')):
        raise ValueError('Choose keyframes or references; disable the unused inputs')
    if connected:
        result['_connected'] = connected
    return result


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
                geometry=report.get('geometry', {}),
                sampling_plan=report.get('sampling_plan'),
                low_memory=bool(report.get('low_memory_mode')),
                reference_trims=report.get('encoding', {}).get('reference_trims') or [],
                preview=bool(report.get('preview')), upscaled_preview=bool(report.get('upscaled_preview')),
                video=relative_video.as_posix(), report=relative_video.with_suffix(
                    '.debug.json' if report.get('diagnostic_file') == relative_video.with_suffix('.debug.json').name
                    else '.request.json').as_posix())
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
    return result
