"""One dubbing job per process: the model, its CUDA context and its memory leave with the process.

Arguments: the model folder, then the folder of the downloaded runtime libraries (optional).
The request is one JSON object on stdin; progress and the result are JSON lines on stdout. A line is
fitted to its target duration by `align.fit`, then written as a wav file in `output_dir`.
"""
import json
import math
import os
import sys
import time
import traceback

from . import align, backends

IDLE_SECONDS = 600
ROW_LIMIT = 128 * 1024


def emit(**row):
    sys.stdout.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
    sys.stdout.flush()


LEVEL_TARGET = 0.06
LEVEL_PEAK = 0.95
LEVEL_GAIN = 10.


def leveled(data, rate=None):
    """The model gives back the level of its reference, so a quiet recording would come back nearly
    silent. Samples quieter than LEVEL_TARGET (RMS) are raised towards it: the gain is 1 at the target and
    grows smoothly as the recording gets quieter, never below 1, never past LEVEL_GAIN, never into clipping.
    With `rate` the level is that of the voiced part, so the silence around a line cut from a separated
    voice does not raise the line more than its voice needs; a recording that is loud as a whole is left
    as it is."""
    import numpy as np
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if len(data) else 0.
    peak = float(np.max(np.abs(data))) if len(data) else 0.
    if rms <= 1e-5 or rms >= LEVEL_TARGET:
        return data, 1.
    if rate:
        rms = voiced_level(data, rate) or rms
    gain = max(1., min(LEVEL_TARGET / rms, LEVEL_PEAK / peak, LEVEL_GAIN))
    if gain == 1.:
        return data, 1.
    return data * np.float32(gain), gain


MATCH_RANGE = (0.1, 10.)


def voiced_level(data, rate):
    """RMS of the voiced part of `data` (None when there is none)."""
    import numpy as np
    span = align.voiced_span(data, rate)
    if span is None:
        return None
    part = np.asarray(data[span[0]:span[1]], dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(part)))) if len(part) else None


def matched(samples, rate, onset, offset, level):
    """`samples` scaled so that the voiced part (from `onset` to `offset` seconds) has RMS `level`: the line
    comes back as loud as the original line it replaces, whatever level the model was given. Returns
    (samples, gain); the gain stays inside MATCH_RANGE and never clips."""
    import numpy as np
    current = voiced_level(samples[int(onset * rate):int(offset * rate)], rate)
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.
    if not level or not current or peak <= 0:
        return samples, 1.
    gain = min(max(level / current, MATCH_RANGE[0]), MATCH_RANGE[1], LEVEL_PEAK / peak)
    if abs(gain - 1.) < 0.01:
        return samples, 1.
    return samples * np.float32(gain), gain


CLAUSE_END = '。！？；，、.!?;,\n'


def cut_text(text, share):
    """The longest start of `text` that is no more than `share` of it and ends where a clause or a word
    ends; returns (start of the text, its share of the whole text)."""
    limit = int(len(text) * share)
    if limit <= 0:
        return '', 0.
    stops = [i for i in range(limit, 0, -1) if text[i - 1] in CLAUSE_END or text[i - 1] == ' ' or text[i:i + 1] == ' ']
    end = next((i for i in stops if i >= limit // 2), limit)
    kept = text[:end].strip()
    return kept, len(kept) / len(text)


def usable_reference(path, folder, name='reference-prepared.wav', text='', texts=None):
    """Return a mono wav no longer than the models accept and not too quiet (a changed copy if either
    applies), and its seconds. A reference that is too long is cut where its text is cut (the model reads the
    text it is given, so a text longer than the audio is spoken into the line): the cut text goes to
    `texts[name]`."""
    from . import wavio
    import numpy as np
    data, rate = wavio.read(path)
    seconds = len(data) / rate
    if seconds < 1.0:
        raise ValueError('reference_short')
    changed = False
    if texts is not None:
        texts[name] = text
    if seconds > backends.MAX_REFERENCE_SECONDS:
        end = int(backends.MAX_REFERENCE_SECONDS * rate)
        if text:
            kept, share = cut_text(text, backends.MAX_REFERENCE_SECONDS / seconds)
            if kept:
                text, end = kept, int(len(data) * share)
                # the cut falls on the quietest 10 ms of the last half second
                frame = max(1, rate // 100)
                first = max(0, end - rate // 2) // frame
                count = (end - first * frame) // frame
                if count > 1:
                    rms = np.sqrt(np.square(np.asarray(data[first * frame:(first + count) * frame], dtype=np.float64))
                                  .reshape(count, frame).mean(axis=1))
                    end = (first + int(np.argmin(rms))) * frame + frame // 2
                if texts is not None:
                    texts[name] = text
        data = data[:end]
        seconds = len(data) / rate
        changed = True
    data, gain = leveled(data, rate)
    if gain != 1.:
        changed = True
    if not changed:
        return path, seconds
    target = os.path.join(folder, name)
    wavio.write(target, data, rate)
    return target, seconds


def validate(request):
    lines = request.get('lines')
    if not isinstance(lines, list) or not lines or len(lines) > 500:
        raise ValueError('invalid_request')
    for line in lines:
        if not isinstance(line, dict) or not str(line.get('text') or '').strip():
            raise ValueError('invalid_request')
        if not float(line.get('seconds') or 0) > 0:
            raise ValueError('invalid_request')
    reference = request.get('reference') or {}
    own = [(line.get('reference') or {}) for line in lines]
    if any(not isinstance(item, dict) for item in own):
        raise ValueError('invalid_request')
    for item in own:
        if item and not os.path.isfile(str(item.get('path') or '')):
            raise ValueError('invalid_request')
    if (reference or not all(own)) and not os.path.isfile(str(reference.get('path') or '')):
        raise ValueError('invalid_request')
    if not os.path.isdir(str(request.get('output_dir') or '')):
        raise ValueError('invalid_request')
    return lines, reference


def run(request, model_dir, device='cuda'):
    from . import wavio
    from ..prompt_vlm.worker import Cancelled
    lines, reference = validate(request)
    folder = request['output_dir']
    name = request.get('backend') or backends.DEFAULT
    seed = request.get('seed')
    # References are prepared before the model loads: a bad one is refused in seconds, not after the load.
    prepared = {}
    levels = {}
    cut = {}

    def prepare(source, key):
        if key not in prepared:
            name = 'reference-%s.wav' % key
            prepared[key] = usable_reference(source['path'], folder, name, str(source.get('text') or '').strip(), cut)
            data, rate = wavio.read(source['path'])
            levels[key] = voiced_level(data, rate)
        return prepared[key]
    job_ref = None
    if reference:
        job_ref = prepare(reference, 'job') + (cut['reference-job.wav'],)
    used = []
    match = {}  # per line with its own reference: the voiced RMS the line is to come back with
    for index, line in enumerate(lines):
        own = line.get('reference')
        if own:
            try:
                used.append(('line',) + prepare(own, 'line%d' % index) + (cut['reference-line%d.wav' % index], None))
                match[index] = levels.get('line%d' % index)
                continue
            except ValueError as error:
                if str(error) != 'reference_short' or job_ref is None:
                    raise
                used.append(('job',) + job_ref + ('reference_short',))
                continue
        used.append(('job',) + job_ref + ('reference_short' if line.get('reference_short') else None,))
    emit(phase='loading')
    started = time.monotonic()
    backend = backends.create(name, model_dir, device=device)
    emit(phase='loaded', seconds=round(time.monotonic() - started, 2))
    try:
        if backend.needs_reference_text and any(not item[3] for item in used):
            raise ValueError('reference_text_required')
        results = []
        for index, line in enumerate(lines):
            emit(phase='synthesizing', index=index, count=len(lines))
            source, ref_path, ref_seconds, ref_text, note = used[index]
            if seed is not None:
                from .cosyvoice3.utils.common import set_all_random_seed
                set_all_random_seed(int(seed) + index)
            instruction = (line.get('instruction') or '').strip() or None
            text = str(line['text']).strip()
            target = float(line['seconds'])
            row = dict(id=line.get('id'), target=target, reference=source)
            try:
                def synth(speed, text=text, instruction=instruction, ref_path=ref_path, ref_text=ref_text):
                    return backend.synth(text, ref_path, ref_text, speed, instruction)

                fitted = align.fit(synth, target, onset=float(line.get('onset') or 0.),
                                   offset=None if line.get('offset') is None else float(line['offset']))
            except Cancelled:
                raise
            except Exception as error:
                traceback.print_exc(file=sys.stderr)
                row.update(failure(error))
                results.append(row)
                emit(phase='line', index=index, **row)
                continue
            samples, gain = fitted['samples'], 1.
            if match.get(index):
                samples, gain = matched(samples, fitted['rate'], fitted['onset'], fitted['offset'], match[index])
            path = os.path.join(folder, '%s.wav' % (line.get('id') or 'line%03d' % index))
            wavio.write(path, samples, fitted['rate'])
            row.update(file=os.path.basename(path), seconds=round(fitted['seconds'], 4), samples=len(fitted['samples']),
                       onset=round(fitted['onset'], 3), offset=round(fitted['offset'], 3),
                       onset_error=round(fitted['onset_error'], 3), offset_error=round(fitted['offset_error'], 3),
                       error=round(fitted['error'], 3), speed=round(fitted['speed'], 3), passes=fitted['passes'],
                       stretch=round(fitted['stretch'], 3), fitted=bool(fitted['fitted']),
                       warning=fitted['warning'] or (note if note == 'reference_short' else None),
                       overrun=round(fitted['overrun'], 3), gain_db=round(20 * math.log10(gain), 1))
            results.append(row)
            emit(phase='line', index=index, **row)
    finally:
        backend.close()
    if all('code' in row for row in results):
        raise _LineFailure(results[0])
    return dict(lines=results, reference_seconds=round(used[0][2], 2), backend=name, sample_rate=backend.sample_rate)


class _LineFailure(Exception):
    """Every line failed: the job fails with the first line's code."""
    def __init__(self, row):
        super().__init__(row['code'])
        self.row = row


CODES = ('invalid_request', 'reference_text_required', 'reference_short')


def failure(error):
    if isinstance(error, _LineFailure):
        return dict(code=error.row['code'], **({'error': error.row['error']} if 'error' in error.row else {}))
    text = str(error)
    if isinstance(error, ValueError) and text in CODES:
        return dict(code=text)
    if isinstance(error, FileNotFoundError):
        return dict(code='model_missing')
    low = text.lower()
    if isinstance(error, MemoryError) or 'out of memory' in low:
        return dict(code='out_of_memory')
    return dict(code='failed', error=type(error).__name__)


def main():
    from ..prompt_vlm.worker import Cancelled, stop_on_signal
    stop_on_signal()
    try:
        request = json.loads(sys.stdin.buffer.read(1024 * 1024))
        if not isinstance(request, dict):
            raise ValueError('invalid_request')
        if len(sys.argv) > 2 and sys.argv[2]:
            # -I ignores PYTHONPATH: the downloaded runtime (onnxruntime) is handed over as an argument.
            from .pack import activate
            activate(sys.argv[2])
        result = run(request, sys.argv[1], device=request.get('device') or 'cuda')
        emit(phase='complete', **result)
        return 0
    except Cancelled:
        emit(phase='cancelled')
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit(phase='failed', **failure(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
