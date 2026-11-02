"""Where someone speaks in a sound file, so generated music can sit lower under dialogue.

Silero VAD (snakers4/silero-vad, MIT; the onnx-community export) runs on the CPU in torch: the 16 kHz
weights are read straight from the ONNX file, so no ONNX runtime is needed. The file is downloaded with the
music model. One pass over a file gives speech spans in seconds of that file; they are cached next to the
editor's other derived data, and the preview and the export read the same spans.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading

import numpy as np

MODEL_FILE = 'music/silero-vad/model.onnx'
VERSION = 1             # change when the spans for the same file would change
RATE = 16000
WINDOW, CONTEXT = 512, 64
ENTER, LEAVE = .5, .35  # Silero's own hysteresis
MIN_SPEECH, MIN_SILENCE, PAD = .25, .1, .03
# Music under dialogue stays down across short pauses between sentences instead of pumping.
JOIN_GAP = .6
LIMIT_SECONDS = 4 * 3600


class SpeechError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# --- a small protobuf reader: only what an ONNX file's tensors need -----------------------------------------
def _varint(data, index):
    shift = value = 0
    while True:
        byte = data[index]
        index += 1
        value |= (byte & 0x7f) << shift
        if byte < 0x80:
            return value, index
        shift += 7


def _fields(data):
    index = 0
    while index < len(data):
        key, index = _varint(data, index)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, index = _varint(data, index)
        elif wire == 1:
            value, index = data[index:index + 8], index + 8
        elif wire == 2:
            size, index = _varint(data, index)
            value, index = data[index:index + size], index + size
        elif wire == 5:
            value, index = data[index:index + 4], index + 4
        else:
            raise SpeechError('model')
        yield number, wire, value


def _tensor(data):
    dims, kind, name, raw = [], None, '', None
    for number, wire, value in _fields(data):
        if number == 1:
            if wire == 2:
                index = 0
                while index < len(value):
                    dim, index = _varint(value, index)
                    dims.append(dim)
            else:
                dims.append(value)
        elif number == 2:
            kind = value
        elif number == 8:
            name = bytes(value).decode()
        elif number == 9:
            raw = value
    if kind != 1 or raw is None or len(raw) != 4 * int(np.prod(dims or [1])):
        return name, None
    return name, np.frombuffer(bytes(raw), '<f4').reshape(dims).copy()


def _tensors(graph, found):
    for number, _, value in _fields(graph):
        if number == 5:
            name, array = _tensor(value)
            if array is not None:
                found[name] = array
        elif number == 1:
            for field, _, attribute in _fields(value):
                if field == 5:
                    for part, _, inner in _fields(attribute):
                        if part == 6:
                            _tensors(inner, found)
    return found


def onnx_weights(path):
    """The 16 kHz branch of the onnx-community Silero export, as numpy arrays named for _Model."""
    data = memoryview(Path(path).read_bytes())
    found = {}
    for number, _, value in _fields(data):
        if number == 7:
            _tensors(value, found)
    prefix = 'If_0_then_branch__Inline_0__'
    lstm = [array for name, array in found.items()
            if name.startswith(prefix) and name.endswith('_subg_96_sub_graph2') and array.ndim >= 2]
    try:
        weights = {'basis': found[prefix + 'stft.forward_basis_buffer']}
        for layer in range(4):
            weights[f'conv{layer}.weight'] = found[f'{prefix}encoder.{layer}.reparam_conv.weight']
            weights[f'conv{layer}.bias'] = found[f'{prefix}encoder.{layer}.reparam_conv.bias']
        weights['out.weight'] = found[prefix + 'decoder.decoder.2.weight']
        weights['out.bias'] = found[prefix + 'decoder.decoder.2.bias']
        w, r = [array[0] for array in lstm if array.shape == (1, 512, 128)]
        (b,) = [array[0] for array in lstm if array.shape == (1, 1024)]
    except (KeyError, ValueError):
        raise SpeechError('model') from None
    # ONNX gates are input, output, forget, cell; torch's are input, forget, cell, output.
    order = lambda x: np.concatenate([x[0:128], x[256:384], x[384:512], x[128:256]])
    weights.update({'lstm.weight_ih_l0': order(w), 'lstm.weight_hh_l0': order(r),
                    'lstm.bias_ih_l0': order(b[:512]), 'lstm.bias_hh_l0': order(b[512:])})
    if weights['basis'].shape != (258, 1, 256) or weights['conv0.weight'].shape != (128, 129, 3):
        raise SpeechError('model')
    return weights


def _model(weights):
    import torch
    from torch import nn

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer('basis', torch.zeros(258, 1, 256))
            self.conv0 = nn.Conv1d(129, 128, 3, padding=1)
            self.conv1 = nn.Conv1d(128, 64, 3, stride=2, padding=1)
            self.conv2 = nn.Conv1d(64, 64, 3, stride=2, padding=1)
            self.conv3 = nn.Conv1d(64, 128, 3, padding=1)
            self.lstm = nn.LSTM(128, 128, batch_first=True)
            self.out = nn.Conv1d(128, 1, 1)

        def forward(self, windows):
            """[n, 576] windows (64 context + 512 new samples) in order → [n] speech probabilities."""
            x = nn.functional.pad(windows, (0, 64), mode='reflect').unsqueeze(1)
            x = nn.functional.conv1d(x, self.basis, stride=128)
            x = torch.sqrt(x[:, :129] ** 2 + x[:, 129:] ** 2)
            for conv in (self.conv0, self.conv1, self.conv2, self.conv3):
                x = torch.relu(conv(x))
            x, _ = self.lstm(x.squeeze(-1).unsqueeze(0))
            x = torch.sigmoid(self.out(torch.relu(x[0]).unsqueeze(-1)))
            return x[:, 0, 0]

    model = Model()
    model.load_state_dict({name: torch.from_numpy(array) for name, array in weights.items()})
    return model.eval()


_LOADED = {}
_LOCK = threading.Lock()


def probabilities(samples, model_path):
    """float32 mono samples at 16 kHz → one speech probability per 512 samples (32 ms)."""
    import torch
    with _LOCK:
        key = str(model_path)
        if key not in _LOADED:
            _LOADED.clear()
            _LOADED[key] = _model(onnx_weights(model_path))
        model = _LOADED[key]
    samples = np.asarray(samples, np.float32)
    count = -(-len(samples) // WINDOW)
    if count == 0:
        return np.zeros(0, np.float32)
    padded = np.zeros(CONTEXT + count * WINDOW, np.float32)
    padded[CONTEXT:CONTEXT + len(samples)] = samples
    windows = np.lib.stride_tricks.sliding_window_view(padded, CONTEXT + WINDOW)[::WINDOW][:count]
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(min(threads, 4))
        with torch.inference_mode():
            return model(torch.from_numpy(np.ascontiguousarray(windows))).numpy()
    finally:
        torch.set_num_threads(threads)


def spans(probs, duration):
    """Speech probabilities → merged [start, end] seconds, with Silero's hysteresis and padding."""
    step = WINDOW / RATE
    found, start, quiet = [], None, None
    for index, value in enumerate(probs):
        at = index * step
        if start is None:
            if value >= ENTER:
                start, quiet = at, None
        elif value < LEAVE:
            quiet = at if quiet is None else quiet
            if at + step - quiet >= MIN_SILENCE:
                found.append([start, quiet])
                start, quiet = None, None
        else:
            quiet = None
    if start is not None:
        found.append([start, len(probs) * step])
    joined = []
    for begin, end in found:
        if end - begin < MIN_SPEECH:
            continue
        begin, end = max(0., begin - PAD), min(duration, end + PAD)
        if joined and begin - joined[-1][1] <= JOIN_GAP:
            joined[-1][1] = max(joined[-1][1], end)
        else:
            joined.append([begin, end])
    return [[round(begin, 3), round(end, 3)] for begin, end in joined]


def decode(path, cancelled=None):
    """The first audio stream of a media file as float32 mono at 16 kHz."""
    import av
    parts, total = [], 0
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                return np.zeros(0, np.float32)
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format='flt', layout='mono', rate=RATE)
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    part = out.to_ndarray().reshape(-1)
                    parts.append(part)
                    total += len(part)
                if total > LIMIT_SECONDS * RATE:
                    raise SpeechError('too_long')
                if cancelled is not None and cancelled():
                    raise SpeechError('cancelled')
            for out in resampler.resample(None):
                parts.append(out.to_ndarray().reshape(-1))
    except av.error.FFmpegError:
        raise SpeechError('unreadable') from None
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)


def _identity(path, model_path):
    stat, model = Path(path).stat(), Path(model_path).stat()
    raw = json.dumps([str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns, model.st_size, VERSION])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def cached(path, model_path, cache):
    """The cached spans for this exact file and model, or None."""
    try:
        value = json.loads((Path(cache) / (_identity(path, model_path) + '.json')).read_text(encoding='utf-8'))
        return value['spans'] if isinstance(value, dict) and isinstance(value.get('spans'), list) else None
    except (OSError, ValueError, KeyError):
        return None


def speech_spans(path, model_path, cache, cancelled=None):
    """Speech spans in seconds of `path`; computed once per file and model, then read from `cache`."""
    found = cached(path, model_path, cache)
    if found is not None:
        return found
    if not Path(model_path).is_file():
        raise SpeechError('model_missing')
    samples = decode(path, cancelled)
    found = spans(probabilities(samples, model_path), len(samples) / RATE)
    folder = Path(cache)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (_identity(path, model_path) + '.json')
    temporary = target.with_name(f'{target.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_text(json.dumps({'spans': found}), encoding='utf-8')
    os.replace(temporary, target)
    return found
