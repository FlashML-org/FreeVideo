"""How long each upscale row of a finished video takes on this computer (the menu's times).

Three ways to make a finished video larger, each its own menu row:
  fast      the quick 1.5x size: every frame by the small model (frame_upscale, kind 'fast');
  detailed  the 1.5x step that samples again with the video model (work_upscale, three refinement steps);
  4k        the 4K size, by the same small model as fast.

detailed comes from the stage model's prior for an upscale (stage_estimate, flow 'upscale': no first pass),
fast and 4k from what the small model and the encoder take per pixel, scaled by this card's speed and the CPU threads
the encoder uses. Each kind is then corrected by this computer's own finished upscales of that kind
(TIMES_FILE, written when an upscale completes); without any, detailed takes the correction its whole-video
generations give (capabilities._calibration). A row whose card is not known has no time (None).
"""
import json
import math
import os
from pathlib import Path
import tempfile
from statistics import median
import threading

TIMES_FILE = 'upscale-times.json'
KEEP = 24
KINDS = ('fast', 'detailed', '4k')
# general-x4v3 on an RTX PRO 6000 (nominal speed 5.27 in stage_estimate's table): 7.4 s for 243 frames of 1344 x 768,
# 3.4 s for 120 frames of a 5 s video at the same size; per input pixel and frame at speed 1.
MODEL_SECONDS_PER_PIXEL = 1.56e-7
# libx264 at CRF 18, preset fast: 4 to 5.5 s for 243 frames of 2016 x 1152 on a 12-thread test machine, 16 s for 120 frames
# of 2192 x 3840 on 8; thread-seconds per output pixel and frame.
ENCODE_THREAD_SECONDS_PER_PIXEL = 1.5e-7
# Starting the worker (Python and Torch) and loading the 4.9 MB weights.
START_SECONDS = 6.
_LOCK = threading.Lock()


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _encode_threads():
    from .video_threads import X264_THREADS
    return max(1, min(X264_THREADS, os.cpu_count() or 1))


def card_speed(hardware):
    """The card's nominal speed in stage_estimate's table (1 = its reference), or None for a card it does not know."""
    from .stage_estimate import _apple, _gpu_item, _params
    if not isinstance(hardware, dict) or _apple(hardware):
        return None
    item = _gpu_item(hardware, _params(None))
    if not item or not _positive(item.get('G_spec')):
        return None
    return item['G_spec'] * (item.get('laptop_utilization_prior') or 1.) if item.get('laptop') else item['G_spec']


def frame_prior(hardware, source, target, frames):
    """Seconds for the small model to make `frames` frames of `source` (w, h) at `target` (w, h), or None."""
    speed = card_speed(hardware)
    if speed is None or frames <= 0:
        return None
    model = frames * source[0] * source[1] * MODEL_SECONDS_PER_PIXEL / speed
    encode = frames * target[0] * target[1] * ENCODE_THREAD_SECONDS_PER_PIXEL / _encode_threads()
    return START_SECONDS + model + encode


def detailed_prior(hardware, target, frames):
    """The stage model's seconds for the 1.5x step that samples again (no first pass), or None."""
    from .stage_estimate import predict_prior
    try:
        request = dict(width=target[0], height=target[1], frames=frames, two_pass=True, base_steps=8, refine_steps=3,
                       flow='upscale', upscale_factor=1.5, task='ref2va_audio')
        seconds = predict_prior(hardware, request, dict(encode_cached=None))['total']['seconds']
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return None
    return seconds if _positive(seconds) else None


def prior(kind, hardware, source, target, frames):
    if kind == 'detailed':
        return detailed_prior(hardware, target, frames)
    return frame_prior(hardware, source, target, frames)


def _times(root):
    try:
        rows = json.loads((Path(root) / TIMES_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def remember(root, kind, hardware, source, target, frames, seconds):
    """Keep a finished upscale's time, so the next estimate of its kind follows this computer. Never fails."""
    try:
        if kind not in KINDS or not _positive(seconds) or not frames:
            return
        row = dict(kind=kind, gpu=(hardware or {}).get('gpu_name'), source=list(source), target=list(target),
                   frames=int(frames), seconds=round(float(seconds), 2))
        with _LOCK:
            rows = ([row] + _times(root))[:KEEP]
            path = Path(root) / TIMES_FILE
            # One executor per card may finish at the same moment: each writes a file of its own name, then replaces;
            # at worst one of two simultaneous rows is lost, never the file.
            stream = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.upscale-times-%d-' % os.getpid(),
                                                 suffix='.tmp', delete=False)
            try:
                with stream:
                    stream.write(json.dumps(rows, indent=1))
                os.replace(stream.name, path)
            except BaseException:
                # A full disk or a failed replace leaves no temporary file behind.
                Path(stream.name).unlink(missing_ok=True)
                raise
    except Exception:
        pass  # a time not kept only leaves the next estimate uncorrected


def correction(kind, hardware, rows):
    """This computer's measured over predicted time for one kind (its last five on this card), or None."""
    found = []
    for row in rows:
        try:
            if row.get('kind') != kind or row.get('gpu') != hardware.get('gpu_name'):
                continue
            expected = prior(kind, hardware, tuple(row['source']), tuple(row['target']), int(row['frames']))
            if _positive(expected) and _positive(row.get('seconds')):
                found.append(row['seconds'] / expected)
        except (LookupError, TypeError, ValueError):
            continue
        if len(found) == 5:
            break
    return max(.3, min(3., median(found))) if found else None


def estimates(hardware, root, source, frames, *, sizes, generation_ratio=None):
    """{kind: seconds or None} for the kinds in `sizes` ({kind: (w, h) or None}) of a `source` (w, h) video."""
    rows = _times(root)
    result = {}
    for kind in KINDS:
        target = sizes.get(kind)
        seconds = prior(kind, hardware, source, target, frames) if target else None
        if seconds is not None:
            ratio = correction(kind, hardware, rows)
            if ratio is None and kind == 'detailed':
                ratio = generation_ratio
            seconds *= ratio or 1.
        result[kind] = round(seconds, 1) if seconds is not None else None
    return result


def card_name(machine):
    """The card's name as capabilities._machine reads it, so a time is kept and looked up under the same name."""
    machine = machine if isinstance(machine, dict) else {}
    observed = machine.get('hardware') if isinstance(machine.get('hardware'), dict) else {}
    device = machine.get('device_identity') if isinstance(machine.get('device_identity'), dict) else {}
    return observed.get('gpu_name') or machine.get('gpu_name') or device.get('name')
