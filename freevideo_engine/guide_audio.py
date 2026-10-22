"""Keep the guide clip's own sound on a finished guide-based result.

The video stream is copied packet by packet; only the audio stream is replaced, with
the guide's audio trimmed or padded with silence to the video length. Requests
without "keep_source_audio" never reach this module.
"""
from fractions import Fraction
from pathlib import Path

import av
import numpy as np


def has_audio(path):
    try:
        with av.open(str(path)) as container:
            return bool(container.streams.audio)
    except av.error.FFmpegError as error:
        raise ValueError('Cannot read the guide clip: %s' % error) from error


def _video_seconds(container, stream):
    end = 0
    for packet in container.demux(stream):
        if packet.pts is not None and packet.duration is not None:
            end = max(end, packet.pts + packet.duration)
    if not end:
        raise ValueError('Cannot measure the video length')
    return float(end * stream.time_base)


def _guide_samples(path, rate, layout):
    """Decode the guide's first audio stream to float planar samples at the output format."""
    blocks = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format='fltp', layout=layout, rate=rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                blocks.append(out.to_ndarray())
        for out in resampler.resample(None):
            blocks.append(out.to_ndarray())
    channels = len(av.AudioLayout(layout).channels)
    return np.concatenate(blocks, axis=1) if blocks else np.zeros((channels, 0), np.float32)


def replace_audio(video, guide, output, *, rate=48000, layout='stereo'):
    """Write ``output``: ``video``'s picture untouched plus ``guide``'s audio fitted to its length."""
    video, guide, output = Path(video), Path(guide), Path(output)
    if not has_audio(guide):
        raise ValueError('The guide clip has no audio stream')
    with av.open(str(video)) as source:
        picture = source.streams.video[0]
        seconds = _video_seconds(source, picture)
    wanted = round(seconds * rate)
    samples = _guide_samples(guide, rate, layout)[:, :wanted]
    if samples.shape[1] < wanted:
        samples = np.pad(samples, ((0, 0), (0, wanted - samples.shape[1])))
    with av.open(str(video)) as source, av.open(str(output), 'w', format='mp4') as result:
        picture = source.streams.video[0]
        copied = result.add_stream_from_template(picture)
        sound = result.add_stream('aac', rate=rate, layout=layout)
        for packet in source.demux(picture):
            if packet.dts is None:
                continue
            packet.stream = copied
            result.mux(packet)
        position = 0
        while position < wanted:
            block = np.ascontiguousarray(samples[:, position:position + 1024])
            frame = av.AudioFrame.from_ndarray(block, format='fltp', layout=layout)
            frame.sample_rate = rate
            frame.pts = position
            frame.time_base = Fraction(1, rate)
            for packet in sound.encode(frame):
                result.mux(packet)
            position += block.shape[1]
        for packet in sound.encode(None):
            result.mux(packet)
    return seconds


def apply(video, media):
    """Replace ``video`` in place when the request's guide asks to keep its audio. Returns True if replaced."""
    guide = next((row for row in media.get('references', []) if row.get('guide') and row.get('keep_source_audio')), None)
    if guide is None:
        return False
    video = Path(video)
    temporary = video.with_name(video.stem + '.remux.mp4')
    try:
        replace_audio(video, guide['path'], temporary)
        temporary.replace(video)
    finally:
        temporary.unlink(missing_ok=True)
    return True
