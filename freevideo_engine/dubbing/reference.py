"""The voice reference of a dubbing job: an audio file, or the sound of a video, as a short mono wav."""
import numpy as np

from . import backends, wavio

RATE = 24000
MIN_SECONDS = 1.0


def extract(source, target, start=0., seconds=backends.MAX_REFERENCE_SECONDS):
    """Decode up to `seconds` of the first audio stream of `source` (audio or video) from `start`
    into a mono 16-bit wav at `target`. Returns the seconds written."""
    import av
    chunks = []
    wanted = int(seconds * RATE)
    got = 0
    with av.open(str(source)) as container:
        if not container.streams.audio:
            raise ValueError('no_audio')
        stream = container.streams.audio[0]
        if start > 0:
            container.seek(int(start / stream.time_base) if stream.time_base else 0, stream=stream)
        resampler = av.AudioResampler(format='flt', layout='mono', rate=RATE)
        for frame in container.decode(stream):
            if frame.time is not None and frame.time + frame.samples / max(frame.rate, 1) <= start:
                continue
            for part in resampler.resample(frame):
                chunk = part.to_ndarray().reshape(-1)
                chunks.append(chunk)
                got += len(chunk)
            if got >= wanted:
                break
    if not chunks:
        raise ValueError('no_audio')
    samples = np.concatenate(chunks)[:wanted]
    if len(samples) < MIN_SECONDS * RATE:
        raise ValueError('reference_short')
    wavio.write(target, samples, RATE)
    return len(samples) / RATE
