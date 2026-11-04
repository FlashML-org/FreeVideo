"""Put replacement lines into the original voice: the same length, the rest of the samples untouched."""
import numpy as np

CROSSFADE = .02


def stitch(original, edits, rate, *, crossfade=CROSSFADE):
    """`original`: float samples (frames, channels) or (frames,). `edits`: [(start_sample, samples)] or
    [(start_sample, samples, (fade_in, fade_out))] where samples is mono or the same channels; each replaces
    `original[start:start + len]` with a short crossfade at both edges. The fades are in seconds (`crossfade` when
    not given); a caller with little quiet before or after a line gives a shorter fade so the fade stays in the quiet.

    Everything outside the edits (and outside the fades) stays identical to the input; the length never changes."""
    result = np.array(original, dtype=np.float32, copy=True)
    flat = result.ndim == 1
    if flat:
        result = result[:, None]
    for edit in edits:
        start, voice = edit[0], edit[1]
        fades = edit[2] if len(edit) > 2 else (crossfade, crossfade)
        voice = np.asarray(voice, dtype=np.float32)
        if voice.ndim == 1:
            voice = np.repeat(voice[:, None], result.shape[1], axis=1)
        elif voice.shape[1] != result.shape[1]:
            voice = np.repeat(voice[:, :1], result.shape[1], axis=1)
        start = max(0, int(start))
        end = min(len(result), start + len(voice))
        if end <= start:
            continue
        voice = voice[:end - start]
        head = min(int(round(fades[0] * rate)), (end - start) // 2)
        tail = min(int(round(fades[1] * rate)), (end - start) // 2)
        ramp = np.ones(end - start, dtype=np.float32)
        if head:
            ramp[:head] = np.linspace(0., 1., head, endpoint=False, dtype=np.float32)
        if tail:
            ramp[-tail:] = np.linspace(0., 1., tail, endpoint=False, dtype=np.float32)[::-1]
        result[start:end] = result[start:end] * (1. - ramp[:, None]) + voice * ramp[:, None]
    return result[:, 0] if flat else result
