"""Validate retained test outputs without loading Torch or unpickling tensors."""
import json
import math
import os
import wave
import zipfile
from .system import memory_complete, memory_peak


def read_case_reports(directory, row):
    errors = []
    for key, name in [('request', 'request'), ('encoding', 'encoding'), ('engine', 'engine')]:
        path = directory / ('video.' + name + '.json')
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                raise ValueError('Expected a JSON object')
            row[key] = record
        except (OSError, ValueError) as error:
            errors.append(path.name + ': ' + str(error))
    if errors:
        row['report_errors'] = errors
        if row['status'] == 'complete':
            row.update(status='validation_failed', error='Incomplete stage reports: ' + '; '.join(errors))


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def validate_metrics(row, canvas, *, preencoded=False):
    for name in ('request', 'engine') + (() if preencoded else ('encoding',)):
        if row.get(name, {}).get('success') is not True:
            raise ValueError(name + ' did not report successful completion')
    engine = row['engine']
    if engine.get('finite_latents') is not True:
        raise ValueError('Engine did not confirm finite video and audio latents')
    for name in ('request', 'engine'):
        if any(row[name].get('geometry', {}).get(k) != canvas[k] for k in ('width', 'height', 'frames', 'fps')):
            raise ValueError(name + ' geometry does not match the requested canvas')
    steps = engine.get('step_seconds', [])
    from .two_pass import steps as planned_steps
    sampling_plan = row['request'].get('sampling_plan')
    if (engine.get('sampling_plan') != sampling_plan
            or engine.get('config', {}).get('steps') != (sampling_plan or {}).get('base_steps', 8)
            or len(steps) != planned_steps(sampling_plan) or not all(finite_number(v) for v in steps)):
        raise ValueError('Expected all planned denoising steps with finite timings and matching sampling strategy')
    timings = [('request', ('request_seconds',)),
               ('engine', ('load_seconds', 'sample_seconds', 'decode_save_seconds', 'work_seconds'))]
    if not preencoded:
        timings.append(('encoding', ('load_seconds', 'work_seconds')))
    for name, keys in timings:
        if not all(finite_number(row[name].get(key)) for key in keys):
            raise ValueError('Incomplete timing metrics in ' + name)
    gpu, ram = row.get('gpu', {}), row.get('ram', {})
    if gpu.get('sampling_errors') or not gpu.get('samples') or not gpu.get('gpu_peak_bytes'):
        raise ValueError('GPU telemetry incomplete; generation completed but test metrics are incomplete.')
    if not ram.get('ram_observation_samples') or not memory_complete(ram) or not memory_peak(ram):
        raise ValueError('RAM telemetry incomplete; generation completed but test metrics are incomplete.')


def _retained_array_header(path):
    """Check numeric NPY metadata and payload length before any memory mapping."""
    import numpy as np
    from numpy.lib import format
    with path.open('rb') as stream:
        version = format.read_magic(stream)
        if version == (1, 0):
            reader = format.read_array_header_1_0
        elif version in ((2, 0), (3, 0)):
            # V3 only changes header text encoding. The scalar numeric dtypes
            # accepted below have ASCII descriptors in both versions; Unicode
            # structured dtypes are rejected before mapping.
            reader = format.read_array_header_2_0
        else:
            raise ValueError('Unsupported retained NPY version: ' + path.name)
        shape, _, dtype = reader(stream)
        if dtype not in (np.dtype(np.uint8), np.dtype(np.float32)):
            raise ValueError('Expected retained uint8 or float32 array: ' + path.name)
        if any(type(size) is not int or size < 0 for size in shape):
            raise ValueError('Invalid retained array shape: ' + path.name)
        # Use Python integers: a malformed shape must not overflow the size
        # check or ask Windows to map beyond EOF (which reports WinError 8).
        expected = math.prod(shape) * dtype.itemsize
        available = os.fstat(stream.fileno()).st_size - stream.tell()
        if available < expected:
            raise ValueError('Truncated retained %s: expected %d data bytes, found %d' %
                             (path.name, expected, available))
    return shape, dtype


def validate_artifacts(directory, canvas, *, preencoded=False):
    import numpy as np
    root = directory / 'video.artifacts'
    required = ['conditioning.pt', 'latents.pt', 'rgb.npy', 'audio.npy', 'audio.wav', 'video.json']
    if not preencoded:
        required += ['prompt.txt', 'encode.json']
    missing = [n for n in required if not (root / n).is_file() or not (root / n).stat().st_size]
    if missing:
        raise ValueError('Missing or empty retained artifacts: ' + ', '.join(missing))
    if not preencoded and (root / 'prompt.txt').read_text(encoding='utf-8') != (directory / 'prompt.txt').read_text(encoding='utf-8'):
        raise ValueError('Retained prompt differs from the test input')
    for name in ('video.json',) + (() if preencoded else ('encode.json',)):
        if not isinstance(json.loads((root / name).read_text(encoding='utf-8')), dict):
            raise ValueError('Invalid retained request: ' + name)
    rgb_shape, rgb_dtype = _retained_array_header(root / 'rgb.npy')
    if rgb_dtype != np.uint8 or rgb_shape != (canvas['frames'], canvas['height'], canvas['width'], 3):
        raise ValueError('Retained RGB shape/dtype does not match the video')
    audio_shape, audio_dtype = _retained_array_header(root / 'audio.npy')
    if audio_dtype != np.float32 or len(audio_shape) != 2 or audio_shape[0] != 2:
        raise ValueError('Expected retained float32 stereo audio')
    if abs(audio_shape[1] / 32000 - canvas['seconds']) > 1 / canvas['fps']:
        raise ValueError('Retained audio duration differs from video by more than one frame')
    audio = np.load(root / 'audio.npy', mmap_mode='r', allow_pickle=False)
    # Bounded temporary arrays, even for long custom cases. RGB needs only
    # metadata here; the artifact manifest streams its content hash separately.
    for start in range(0, audio.shape[1], 32000):
        if not np.isfinite(audio[:, start:start + 32000]).all():
            raise ValueError('Non-finite retained audio')
    with wave.open(str(root / 'audio.wav'), 'rb') as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getnframes()) != (2, 32000, audio.shape[1]):
            raise ValueError('Retained WAV does not match raw audio')
        remaining = wav.getnframes() * wav.getsampwidth() * wav.getnchannels()
        while remaining:
            block = wav.readframes(32000)
            if not block:
                raise ValueError('Truncated retained WAV')
            remaining -= len(block)
    for name in ('conditioning.pt', 'latents.pt'):
        with zipfile.ZipFile(root / name) as archive:
            names = archive.namelist()
            if not any(n.endswith('/data.pkl') for n in names) or not any('/data/' in n for n in names):
                raise ValueError('Missing tensor data in ' + name)
            bad = archive.testzip()
            if bad:
                raise ValueError('Tensor archive CRC failed: ' + name + '/' + bad)
    return {'required_files': required, 'rgb_shape': list(rgb_shape), 'audio_shape': list(audio_shape),
            'tensor_archive_crc_pass': True, 'scope': 'Array shape/dtype, finite audio, WAV length and tensor archive integrity; no pickle loading.'}
