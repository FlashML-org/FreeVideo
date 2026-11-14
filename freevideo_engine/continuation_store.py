"""Small, bounded continuation records in FreeVideo's own data directory."""
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import uuid

# Normal 1344x768 records are under 2 MiB. Larger requested canvases keep the
# same seven frames; the safety bound must not reduce their resolution.
MAX_BYTES = 64 * 1024 * 1024
TAG = 'freevideo.continuation'


def root():
    from .paths import data_root
    return data_root() / 'continuations'


def video_id(video_path):
    """Bind to media packets, so rename, move and workflow remux keep the ID.

    Hash each stream independently: interleaving and MP4 metadata are not
    content. A replaced or re-encoded file no longer selects the old record.
    """
    from .comfy_metadata import _media
    return hashlib.sha256(json.dumps(_media(video_path), sort_keys=True).encode('utf-8')).hexdigest()


def _path(identifier, directory=None):
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{64}', identifier):
        raise ValueError('Invalid continuation identifier')
    return (Path(directory) if directory is not None else root()) / (identifier + '.safetensors')


def write(path, data):
    """Atomic tensor archive, also used by the existing sampling retry checkpoints."""
    import numpy as np
    from safetensors.numpy import save_file
    tensors, errors = {}, {}
    for key in ('video_lo', 'video_hi', 'audio', *(['audio_lead_in'] if 'audio_lead_in' in data else [])):
        value = data[key]
        if hasattr(value, 'detach'):
            value = value.detach().float().cpu().numpy()
        value = np.asarray(value, dtype=np.float32)
        if not np.isfinite(value).all():
            raise ValueError('Cannot save non-finite continuation data')
        with np.errstate(over='ignore'):
            packed = value.astype(np.float16)
        if not np.isfinite(packed).all():
            packed = value
        packed = np.ascontiguousarray(packed)
        if key != 'audio_lead_in':
            tensors[key] = packed
        errors[key] = float(np.max(np.abs(value - packed.astype(np.float32))))
    metadata = dict(data['metadata'], storage_max_abs_error=errors)
    if 'audio_lead_in' in data:
        from .continuation import LEAD_IN_RATE
        # Old readers validate every tensor name. Optional export audio belongs
        # in metadata, which those readers already accept and ignore.
        metadata['audio_lead_in'] = dict(sample_rate=LEAD_IN_RATE,
            dtype=packed.dtype.str, shape=list(packed.shape),
            samples=base64.b64encode(packed.tobytes()).decode('ascii'))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        save_file(tensors, str(temporary), metadata={TAG: json.dumps(metadata, sort_keys=True)})
        if temporary.stat().st_size > MAX_BYTES:
            raise ValueError('Continuation data exceeds the storage limit')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read(path):
    path = Path(path)
    with path.open('rb') as source:
        raw = source.read(MAX_BYTES + 1)
    return read_bytes(raw)


def read_bytes(raw):
    import numpy as np
    from safetensors.numpy import load
    from .continuation import (VERSION, LATENT_FRAMES, audio_origin, OVERLAP_FRAMES,
                               LEAD_IN_SAMPLES, LEAD_IN_FADE, LEAD_IN_RATE)
    if len(raw) > MAX_BYTES:
        raise ValueError('Oversized continuation data')
    tensors = load(raw)
    header_size = int.from_bytes(raw[:8], 'little')
    metadata = json.loads(json.loads(raw[8:8 + header_size])['__metadata__'][TAG])
    canvas, first, chain = (metadata[key] for key in ('geometry', 'first_canvas', 'chain'))
    version = metadata['identity']['version']
    if (type(version) is not int or version != VERSION
            or any(type(canvas[key]) is not int for key in ('width', 'height', 'frames'))
            or any(canvas[key] < 256 or canvas[key] % 32 for key in ('width', 'height'))
            or any(type(first[key]) is not int or first[key] < 256 or first[key] % 32
                   or first[key] > canvas[key] for key in ('width', 'height'))
            or canvas['frames'] < 39 or canvas['frames'] % 17 != 5
            or any(type(chain[key]) is not int for key in ('segment', 'frame_origin', 'audio_origin'))
            or chain['segment'] < 1 or chain['frame_origin'] < 0 or chain['audio_origin'] < 0
            or chain['segment'] == 1 and (chain['frame_origin'] != 0 or chain['audio_origin'] != 0)
            or chain['audio_origin'] != audio_origin(chain['frame_origin'])):
        raise ValueError('Invalid continuation metadata')
    ticks = round(canvas['frames'] * 40 / 24) - (
        audio_origin(chain['frame_origin'] + canvas['frames'] - OVERLAP_FRAMES) - chain['audio_origin'])
    expected = dict(video_hi=(1, 24, LATENT_FRAMES, canvas['height'] // 16, canvas['width'] // 16),
                    video_lo=(1, 24, LATENT_FRAMES, first['height'] // 16, first['width'] // 16),
                    audio=(2, 32, ticks))
    if not 0 < ticks <= 38:
        raise ValueError('Invalid continuation audio clock')
    if set(tensors) != set(expected):
        raise ValueError('Invalid continuation tensors')
    for key, tensor in tensors.items():
        if tensor.shape != expected[key] or tensor.dtype not in (np.float16, np.float32) or not np.isfinite(tensor).all():
            raise ValueError('Invalid continuation tensor')
    if 'audio_lead_in' in metadata:
        source_id = metadata.get('source_id')
        if not isinstance(source_id, str) or not re.fullmatch('[0-9a-f]{64}', source_id):
            raise ValueError('Invalid continuation audio source')
        lead = metadata['audio_lead_in']
        if (not isinstance(lead, dict) or type(lead.get('sample_rate')) is not int
                or lead['sample_rate'] != LEAD_IN_RATE
                or lead.get('shape') != [LEAD_IN_SAMPLES + LEAD_IN_FADE, 2]
                or any(type(size) is not int for size in lead['shape'])
                or lead.get('dtype') not in ('<f2', '>f2', '<f4', '>f4')):
            raise ValueError('Invalid continuation audio format')
        dtype = np.dtype(lead['dtype'])
        byte_count = (LEAD_IN_SAMPLES + LEAD_IN_FADE) * 2 * dtype.itemsize
        encoded = lead.get('samples')
        if not isinstance(encoded, str) or len(encoded) != (byte_count + 2) // 3 * 4:
            raise ValueError('Invalid continuation audio size')
        pcm = base64.b64decode(encoded, validate=True)
        if len(pcm) != byte_count:
            raise ValueError('Invalid continuation audio size')
        value = np.frombuffer(pcm, dtype=dtype).reshape(LEAD_IN_SAMPLES + LEAD_IN_FADE, 2)
        if not np.isfinite(value).all():
            raise ValueError('Invalid continuation audio samples')
        tensors['audio_lead_in'] = value
    return dict(tensors, metadata=metadata)


def save(video_path, data):
    identifier = video_id(video_path)
    write(_path(identifier), data)
    result = dict(id=identifier, bytes=_path(identifier).stat().st_size)
    try:
        prune()
    except Exception as error:
        if isinstance(error, MemoryError) or type(error).__name__ in ('OutOfMemoryError', 'CancelledError', 'InterruptProcessingException'):
            raise
        logging.warning('[FreeVideo] Continuation retention failed (%s).', type(error).__name__)
    return result


def _must_propagate(error):
    return isinstance(error, MemoryError) or type(error).__name__ in (
        'OutOfMemoryError', 'CancelledError', 'InterruptProcessingException')


def save_with_retry(video_path, data):
    """`save`, tried once more at once when the first try fails; `retried=True` marks a result that needed it.

    Memory exhaustion and cancellation are never retried. When both tries fail, the last error is raised, marked `freevideo_retried`."""
    try:
        return save(video_path, data)
    except Exception as error:
        if _must_propagate(error):
            raise
        logging.warning('[FreeVideo] Continuation record could not be saved (%s); trying once more.', type(error).__name__)
    try:
        return dict(save(video_path, data), retried=True)
    except Exception as error:
        try:
            error.freevideo_retried = True
        except Exception:
            pass
        raise


def load_id(identifier, *, directory=None):
    from safetensors import SafetensorError
    try:
        path = _path(identifier, directory)
        value = read(path)
        # Retention follows successful use. Losing a concurrent cleanup race
        # cannot invalidate the data already read by this request.
        try:
            os.utime(path, None)
        except OSError:
            pass
        return dict(value, id=identifier)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, SafetensorError):
        return None


def load(video_path, *, directory=None):
    from av import FFmpegError
    try:
        return load_id(video_id(video_path), directory=directory)
    except (OSError, ValueError, FFmpegError):
        return None


def delete(video_path):
    delete_id(video_id(video_path))


def delete_id(identifier):
    _path(identifier).unlink(missing_ok=True)


def prune(keep=64):
    if type(keep) is not int or keep < 0:
        raise ValueError('Continuation retention must be a nonnegative integer')
    records = []
    for path in root().glob('*.safetensors'):
        try:
            records.append((path.stat().st_mtime_ns, path))
        except FileNotFoundError:
            continue
    for _, path in sorted(records, reverse=True)[keep:]:
        path.unlink(missing_ok=True)
