"""Original soundtrack contract, integer sample selection and lossless export.

Only the opt-in path imports media libraries. Reference requests retain their
old serialized representation and never pass through audio inspection here.
"""
from fractions import Fraction
from pathlib import Path
import math

VERSION = 'original-clean-v1'
MAX_SECONDS = 30
MESSAGES = {
    'audio_use_invalid': ('Set the audio to "Reference" or "Use as is".', '音频只能选“参考”或“原样使用”。'),
    'audio_original_multiple': ('Only one audio file can be set to "Use as is".', '只能有一段音频选“原样使用”。'),
    'audio_original_format': ('This format cannot be used as is. Try a WAV, FLAC or M4A file.', '这个格式不能原样使用，请换成 WAV、FLAC 或 M4A 文件。'),
    'audio_original_range': ('The selected range is outside the audio. Select it again.', '所选范围超出了这段音频，请重新选择。'),
    'audio_original_short': ('The selected audio is too short. Select at least 2 seconds.', '所选音频太短，请至少选 2 秒。'),
    'audio_original_long': ('This audio is longer than 30 seconds. Generate it in the Create view, or use an audio file of 30 seconds or less.', '这段音频超过 30 秒，请到创作面板里生成，或换一段 30 秒以内的音频。'),
    'audio_original_limit': ('This audio is longer than 2 hours. Use an audio file of 2 hours or less.', '这段音频超过 2 小时，请换一段 2 小时以内的音频。'),
    'audio_original_part': ('With "Use as is", each part can be up to 30 seconds.', '用“原样使用”时，每一段最长 30 秒。'),
    'audio_original_passes': ('Audio longer than 30 seconds used as is is made in parts, which needs "Two-pass acceleration". Turn it on or set the audio to "Reference".', '超过 30 秒的音频选“原样使用”时会分段生成，需要“二采加速”。请打开“二采加速”，或把音频改选“参考”。'),
    'audio_original_route': ('"Use as is" is not available here yet. Set the audio to "Reference".', '这里还不能用“原样使用”的音频，请改选“参考”。'),
    'audio_original_backend': ('"Use as is" is not available on this computer yet.', '这台电脑上还不能用“原样使用”。'),
    'audio_original_lead': ('To continue a video with audio used as is, select audio that starts at least 1 second into the file: the first second overlaps the saved video.', '续接已保存的视频并原样使用音频时，所选音频要从文件第 1 秒之后开始：前 1 秒与已保存的视频重叠。'),
    'audio_original_slot': ('The selected audio is longer than this part. Make the part longer or select less audio.', '所选音频比这一段长，请把这一段拉长，或少选一些音频。'),
}


def _too_long(max_seconds):
    # One generation takes 30 seconds; only a background chain is asked for more, up to its own limit.
    return AudioUseError('audio_original_long' if max_seconds <= MAX_SECONDS else 'audio_original_limit')


class AudioUseError(ValueError):
    def __init__(self, code):
        self.code = code
        self.message_en, self.message_zh = MESSAGES[code]
        super().__init__(f'{code}: {self.message_en} / {self.message_zh}')

    def as_dict(self):
        return dict(code=self.code, message=self.message_en, message_zh=self.message_zh)


def audio_use_option(value):
    """Audio use for the Media and Reference widgets: missing or empty means Reference, as before the
    widget existed. Workflows saved before it existed hold the panel's '' in its position; any other
    value is checked as given (fields)."""
    return 'reference' if value is None or value == '' else value


def fields(row):
    """Sparse canonical fields: explicit reference and omission are identical."""
    if not isinstance(row, dict):
        raise AudioUseError('audio_use_invalid')
    use = row.get('use', 'reference')
    if use not in ('reference', 'original') or ('use' in row and row.get('kind', 'audio') != 'audio'):
        raise AudioUseError('audio_use_invalid')
    selected = row.get('range')
    if selected is not None:
        if (use != 'original' or not isinstance(selected, dict)
                or set(selected) != {'start_sample', 'end_sample'}
                or any(type(selected[k]) is not int for k in selected)
                or not 0 <= selected['start_sample'] < selected['end_sample']):
            raise AudioUseError('audio_original_range')
    result = (dict(use='original', **({'range': dict(selected)} if selected is not None else {}))
              if use == 'original' else {})
    if 'segment' in row:
        segment = row['segment']
        keys = {'frames', 'frame_origin', 'start_sample', 'end_sample',
                'source_start_sample', 'source_end_sample', 'source_sha256'}
        # A chain that continues a saved video marks every part; its first part starts OVERLAP frames
        # before the selection, on the audio of the frames the saved video already holds.
        from .chain_plan import OVERLAP as OVERLAP_FRAMES
        from_work = isinstance(segment, dict) and segment.get('from_work') is True
        if from_work:
            segment = {k: v for k, v in segment.items() if k != 'from_work'}
        if (use != 'original' or selected is None or not isinstance(segment, dict) or set(segment) != keys
                or any(type(segment[k]) is not int for k in keys - {'source_sha256'})
                or not isinstance(segment['source_sha256'], str) or len(segment['source_sha256']) != 64
                or segment['frames'] < 39 or (segment['frames'] - 5) % 17
                or segment['frame_origin'] < (-OVERLAP_FRAMES if from_work else 0)
                or -OVERLAP_FRAMES < segment['frame_origin'] < 0
                or not 0 <= segment['source_start_sample']
                or not (segment['source_start_sample'] if segment['frame_origin'] >= 0 else 0)
                <= selected['start_sample'] <= segment['start_sample']
                < segment['end_sample'] == selected['end_sample'] <= segment['source_end_sample']):
            raise AudioUseError('audio_original_range')
        segment = dict(row['segment'])
        result['segment'] = segment
    return result


def original(media):
    rows = [row for row in media.get('references', []) if fields(row).get('use') == 'original']
    if len(rows) > 1:
        raise AudioUseError('audio_original_multiple')
    return rows[0] if rows else None


def cli_media(args, media):
    """Apply CLI shorthand before planning, with no changes for old calls."""
    from .media_request import digest
    path, use = getattr(args, 'audio', None), getattr(args, 'audio_use', None)
    if path is not None:
        path = Path(path).expanduser().resolve(strict=True)
        media = dict(media or {'version': 1, 'references': [], 'loras': []})
        row = dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size, kind='audio')
        row.update(fields(dict(kind='audio', use=use or 'reference')))
        media['references'] = [*media.get('references', []), row]
    elif use is not None:
        rows = [row for row in media.get('references', []) if row['kind'] == 'audio']
        if len(rows) != 1:
            raise AudioUseError('audio_use_invalid')
        rows[0].pop('use', None)
        rows[0].update(fields(dict(rows[0], use=use)))
    original(media)
    if media.get('references') and any(media.get(k) for k in ('first', 'last')):
        raise ValueError('Reference and keyframe layouts cannot be mixed; use separate requests')
    return media


def prepare_request(media, width, height, *, continuation=False, backend='cuda'):
    row = original(media)
    if row is None:
        return None, None
    segment = row.get('segment')
    if continuation and segment is None:
        raise AudioUseError('audio_original_route')
    if backend != 'cuda':
        raise AudioUseError('audio_original_backend')
    info = inspect(row, max_seconds=(segment['frames'] + 23) // 24) if segment else inspect(row)
    if segment is not None:
        from .geometry import geometry
        from .original_audio_chain import sample_at
        rate = info['sample_rate']
        prefix = 22 if segment['frame_origin'] else 0
        origin, frames = segment['frame_origin'], segment['frames']
        if (frames > geometry(frames=720)['frames'] or info['sha256'] != segment['source_sha256']
                or continuation != bool(origin)
                or info['start_sample'] != segment['source_start_sample'] + sample_at(origin, rate)
                or segment['start_sample'] != segment['source_start_sample'] + sample_at(origin + prefix, rate)
                or segment['end_sample'] != min(segment['source_end_sample'],
                    segment['source_start_sample'] + sample_at(origin + frames, rate))):
            raise AudioUseError('audio_original_range')
        info['segment'] = dict(segment)
        canvas = geometry(width, height, frames=frames)
    else:
        canvas = canvas_for(info, width, height)
    # Source identity and normalization version become part of the encoder key.
    row['original_audio'] = info
    return info, canvas


def _samples(frame):
    """Channel-major native decoded samples, without a resampler or gain."""
    data = frame.to_ndarray()
    return data if frame.format.is_planar else data.reshape(-1, len(frame.layout.channels)).T


def _fits_flac(data):
    """Whether native samples fit FLAC's signed 24-bit grid without rounding."""
    import numpy as np
    if data.dtype.kind == 'f':
        if not ((data >= -1).all() and (data < 1).all()):
            return False
        scaled = data.astype(np.float64) * (1 << 23)
        return bool((scaled == np.trunc(scaled)).all())
    if data.dtype.itemsize <= 2:
        return True
    return bool(((data & 255) == 0).all())


def inspect(row, *, max_seconds=MAX_SECONDS):
    """Read a bounded selection on the source's integer decoded-sample clock.

    An explicit range may start late in a long file; scanning uses one decoded
    frame at a time and stops after that range. An unselected long file fails
    as soon as its length exceeds the single-generation limit.
    """
    import av
    from .media_request import digest
    path = Path(row['path'])
    selection = row.get('range') or {}
    start, end = selection.get('start_sample', 0), selection.get('end_sample')
    fields(row)
    try:
        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            rate, channels = stream.rate, len(stream.layout.channels)
            if not rate or not 8000 <= rate <= 192000 or channels not in (1, 2):
                raise AudioUseError('audio_original_format')
            if end is not None and end - start > rate * max_seconds:
                raise _too_long(max_seconds)
            count, flac_compatible = 0, True
            for frame in container.decode(audio=0):
                if frame.sample_rate != rate or len(frame.layout.channels) != channels:
                    raise AudioUseError('audio_original_format')
                begin, stop = max(0, start - count), min(frame.samples, end - count) if end is not None else frame.samples
                if begin < stop and flac_compatible:
                    flac_compatible = _fits_flac(_samples(frame)[:, begin:stop])
                count += frame.samples
                if end is not None and count >= end:
                    break
                if end is None and count > rate * max_seconds:
                    raise _too_long(max_seconds)
            end = count if end is None else end
            if not start < end <= count:
                raise AudioUseError('audio_original_range')
            if (end - start) * 24 < 39 * rate:
                raise AudioUseError('audio_original_short')
            codec = stream.codec_context.name
    except (IndexError, av.error.FFmpegError, OSError, ValueError) as error:
        if isinstance(error, AudioUseError):
            raise
        raise AudioUseError('audio_original_format') from error
    sha = digest(path)
    if row.get('sha256') and sha != row['sha256']:
        raise ValueError('Original audio changed after inspection')
    return dict(version=VERSION, path=str(path.resolve()), sha256=sha, bytes=path.stat().st_size,
                sample_rate=rate, channels=channels, start_sample=start, end_sample=end,
                codec=codec, full_source=not selection, flac_compatible=flac_compatible,
                normalization='PyAV-native-to-fltp-32000-v1',
                decoder_version=av.__version__,
                library_versions={k: list(v) for k, v in av.library_versions.items()})


def identity(info):
    return {key: value for key, value in info.items() if key != 'path'}


def verify(info):
    from .media_request import digest
    path = Path(info['path'])
    if path.stat().st_size != info['bytes'] or digest(path) != info['sha256']:
        raise ValueError('Original audio changed after inspection; select the audio again')


def canvas_for(info, width, height, *, max_seconds=MAX_SECONDS):
    from .geometry import geometry
    count, rate = info['end_sample'] - info['start_sample'], info['sample_rate']
    if count * 24 < 39 * rate:
        raise AudioUseError('audio_original_short')
    if count > max_seconds * rate:
        raise _too_long(max_seconds)
    canvas = geometry(width, height, frames=(count * 24 + rate - 1) // rate)
    canvas['requested_seconds'] = float(Fraction(count, rate))
    return canvas


def _selected_chunks(info):
    """The selection in native samples, one decoded frame at a time."""
    import av
    import numpy as np
    offset = count = 0
    with av.open(info['path']) as container:
        for frame in container.decode(audio=0):
            begin, end = max(0, info['start_sample'] - offset), min(frame.samples, info['end_sample'] - offset)
            if begin < end:
                data = _samples(frame)[:, begin:end]
                if data.shape[0] != info['channels'] or not np.isfinite(data).all():
                    raise AudioUseError('audio_original_format')
                count += data.shape[1]
                yield data
            offset += frame.samples
            if offset >= info['end_sample']:
                break
    if not count:
        raise AudioUseError('audio_original_range')
    if count != info['end_sample'] - info['start_sample']:
        raise AudioUseError('audio_original_format')


def selected_samples(info):
    """The whole selection in memory: for one generation's worth, not for a long chain."""
    import numpy as np
    verify(info)
    return np.concatenate(list(_selected_chunks(info)), axis=1)


def model_audio(info, canvas, destination):
    """Resample only the conditioning copy; retain original rate and samples."""
    import av
    import numpy as np
    samples = selected_samples(info)
    rate, channels = info['sample_rate'], info['channels']
    layout = 'mono' if channels == 1 else 'stereo'
    dtype = samples.dtype
    # Convert native integers to float using the codec's power-of-two scale.
    if dtype.kind in 'iu':
        samples = ((samples.astype(np.float32) - 128) / 128 if dtype == np.uint8
                   else samples.astype(np.float32) / (1 << (dtype.itemsize * 8 - 1)))
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    resampler = av.AudioResampler(format='fltp', layout=layout, rate=32000)
    frame = av.AudioFrame.from_ndarray(samples, format='fltp', layout=layout)
    frame.sample_rate, frame.pts, frame.time_base = rate, 0, Fraction(1, rate)
    parts = [item.to_ndarray() for item in resampler.resample(frame) + resampler.resample(None)]
    data = np.concatenate(parts, axis=1)
    if channels == 1:
        data = np.repeat(data, 2, axis=0)
    # Match the sampler's 40 Hz audio latent grid, including its final rounding.
    ticks = round(canvas['frames'] / 24 * 40)
    target = max(math.ceil(canvas['frames'] * 32000 / 24), ticks * 800)
    data = np.pad(data, ((0, 0), (0, max(0, target - data.shape[1]))))
    np.save(destination, data, allow_pickle=False)
    return str(destination)


def av_errors():
    import av
    return av.FFmpegError


_FORMATS = {'pcm_f32le': 'flt', 'pcm_f64le': 'dbl', 'pcm_s32le': 's32', 'pcm_s16le': 's16',
            'alac': 's16p', 'flac': 's16', 'aac': 'fltp'}


def _audio_stream(container, codec, rate, channels, itemsize):
    stream = container.add_stream(codec, rate=rate, layout='mono' if channels == 1 else 'stereo')
    fmt = _FORMATS[codec]
    stream.codec_context.format = fmt
    if codec == 'aac':
        stream.codec_context.bit_rate = 320000
    if codec == 'flac' and itemsize == 4:
        fmt = stream.codec_context.format = 's32'
        stream.codec_context.options = {'bits_per_raw_sample': '24'}
    return stream, fmt


AAC_RATES = frozenset((7350, 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000))
AAC_FALLBACK_RATE = 48000


def _resampled(chunks, rate, channels, target=AAC_FALLBACK_RATE):
    """Channel-first float chunks converted from `rate` to `target`, piece by piece."""
    import av
    import numpy as np
    layout = 'mono' if channels == 1 else 'stereo'
    resampler = av.AudioResampler(format='fltp', layout=layout, rate=target)

    def arrays(frames):
        for frame in frames or ():
            yield np.ascontiguousarray(frame.to_ndarray(), dtype=np.float32)

    for chunk in chunks:
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(chunk, dtype=np.float32), format='fltp', layout=layout)
        frame.sample_rate = rate
        yield from arrays(resampler.resample(frame))
    yield from arrays(resampler.resample(None))


def _to_float(data):
    """Native samples as 32-bit float amplitudes for the AAC encoder."""
    import numpy as np
    return _normalized(data).astype(np.float32, copy=False)


def _encoded(stream, fmt, chunks, rate):
    """Packets for channel-first chunks of any length, encoded as they arrive."""
    import av
    import numpy as np
    size = stream.codec_context.frame_size or 4096
    layout = stream.codec_context.layout.name
    position, held = 0, None

    def frame(chunk):
        nonlocal position
        chunk = np.ascontiguousarray(chunk)
        array = chunk if fmt.endswith('p') else chunk.T.reshape(1, -1)
        value = av.AudioFrame.from_ndarray(np.ascontiguousarray(array), format=fmt, layout=layout)
        value.sample_rate, value.pts, value.time_base = rate, position, Fraction(1, rate)
        position += chunk.shape[1]
        return stream.encode(value)

    for chunk in chunks:
        held = chunk if held is None else np.concatenate((held, chunk), axis=1)
        while held.shape[1] >= size:
            yield from frame(held[:, :size])
            held = held[:, size:]
    if held is not None and held.shape[1]:
        yield from frame(held)
    yield from stream.encode(None)


def encode_audio(container, codec, samples, rate):
    """Encode channel-first samples into an open PyAV container; return the packets.

    Packets are returned rather than muxed so callers can interleave them with
    copied video. Integer formats keep their exact values; float formats keep
    their bits.
    """
    stream, fmt = _audio_stream(container, codec, rate, samples.shape[0], samples.dtype.itemsize)
    return stream, list(_encoded(stream, fmt, [samples], rate))


def _mux_streams(picture, target, rate, copy_from=None, encode=None, audio_rate=None):
    """Write picture's video plus one audio track, copied or losslessly encoded.

    Both tracks are read and written as they go: a long chain's video and
    soundtrack never sit in memory.
    """
    import av
    from math import lcm
    from .comfy_metadata import _copy_stream
    scale = str(lcm(rate, 24))
    clock = Fraction(1, lcm(rate, 24))
    with av.open(str(picture)) as reader, av.open(str(target), 'w', format='mp4',
            options={'video_track_timescale': scale, 'movie_timescale': scale}) as writer:
        source = reader.streams.video[0]
        video = _copy_stream(writer, source)
        video.time_base = clock

        def pictures():
            for packet in reader.demux(source):
                if packet.dts is None or packet.pts is None:
                    continue
                base = packet.time_base
                packet.pts, packet.dts = round(packet.pts * base / clock), round(packet.dts * base / clock)
                packet.duration = round((packet.duration or 0) * base / clock)
                packet.time_base, packet.stream = clock, video
                yield packet

        if copy_from is not None:
            with av.open(str(copy_from)) as audio_reader:
                audio_source = audio_reader.streams.audio[0]
                audio = writer.add_stream_from_template(audio_source)
                delay = _decoder_delay(audio_source)

                def sounds():
                    for packet in audio_reader.demux(audio_source):
                        if packet.dts is None:
                            continue
                        if delay:
                            # The MP4 edit list drops the decoder's start delay, as the MP3 reader did.
                            packet.pts, packet.dts = packet.pts - delay, packet.dts - delay
                        packet.stream = audio
                        yield packet

                _interleave(writer, pictures(), sounds())
        else:
            codec, chunks, channels, itemsize = encode
            audio_rate = audio_rate or rate
            stream, fmt = _audio_stream(writer, codec, audio_rate, channels, itemsize)
            _interleave(writer, pictures(), _encoded(stream, fmt, chunks, audio_rate))


def _decoder_delay(stream):
    """An MP3's start delay in the packets' time base; 0 for other codecs.

    The MP3 reader skips these samples before the first one it returns. A copy
    into MP4 keeps them unless the first packet starts that much before zero.
    """
    if stream.codec_context.name not in ('mp3', 'mp3float') or not stream.start_time or stream.start_time < 0:
        return 0
    return stream.start_time


def _interleave(writer, pictures, sounds):
    # Each track is already in decode order, so merging them equals sorting them all.
    from heapq import merge
    for packet in merge(pictures, sounds, key=lambda item: (item.dts * item.time_base, item.stream.type != 'video')):
        writer.mux(packet)


def _normalized(data):
    """Numeric sample amplitudes that compare exactly across PCM storage sizes."""
    import numpy as np
    if data.dtype.kind in 'iu':
        return ((data.astype(np.float64) - 128) / 128 if data.dtype == np.uint8
                else data.astype(np.float64) / (1 << (data.dtype.itemsize * 8 - 1)))
    return data


def _rate(path):
    import av
    with av.open(str(path)) as container:
        return container.streams.audio[0].rate


def _decoded_chunks(path):
    import av
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            yield _normalized(_samples(frame))


def _decoded(path):
    """A whole track in memory: for tests and one generation's worth."""
    import numpy as np
    return _rate(path), np.concatenate(list(_decoded_chunks(path)), axis=1)


def _same_samples(left, right, tail=0):
    """Whether two chunked tracks hold the same samples, whatever their chunk sizes.

    The first track may run up to `tail` samples past the second.
    """
    import numpy as np
    a = b = None
    while True:
        while a is None or not a.shape[1]:
            a = next(left, None)
            if a is None:
                break
        while b is None or not b.shape[1]:
            b = next(right, None)
            if b is None:
                break
        if b is None and a is not None and tail:
            return _spare(a, left, tail)
        if a is None or b is None:
            return a is None and b is None
        count = min(a.shape[1], b.shape[1])
        if a.shape[0] != b.shape[0] or not np.array_equal(a[:, :count], b[:, :count]):
            return False
        a, b = a[:, count:], b[:, count:]


def _close_samples(left, right, tail=2048, share=0.1):
    """Whether the first track is the second one within coding error: at most `share` of its energy is error.

    The first track may run up to `tail` samples past the second but not stop short of it.
    Tracks are read in pieces of any size, so a long soundtrack is never held whole.
    """
    import numpy as np
    error = energy = extra = 0.0
    a = b = None
    while True:
        while a is None or not a.shape[1]:
            a = next(left, None)
            if a is None:
                break
        while b is None or not b.shape[1]:
            b = next(right, None)
            if b is None:
                break
        if b is None:
            rest = (0 if a is None else a.shape[1]) + sum(chunk.shape[1] for chunk in left)
            return rest <= tail and (energy == 0 or error <= energy * share)
        if a is None or a.shape[0] != b.shape[0]:
            return False
        count = min(a.shape[1], b.shape[1])
        x, y = a[:, :count].astype(np.float64), b[:, :count].astype(np.float64)
        error += float(((x - y) ** 2).sum())
        energy += float((y ** 2).sum())
        a, b = a[:, count:], b[:, count:]


def _spare(first, rest, limit):
    count = first.shape[1]
    for chunk in rest:
        count += chunk.shape[1]
        if count > limit:
            return False
    return count <= limit


def _trim_video(source, target, count, rate, frames=None):
    """Remux video on a sample-accurate clock, adjusting the last kept frame.

    A chain caller may name its verified frame count to retain a rounded
    sample boundary up to half a source sample past the picture grid.
    """
    import av
    from .comfy_metadata import _copy_stream
    from math import lcm
    duration = Fraction(count, rate)
    with av.open(str(source)) as reader, av.open(str(target), 'w', format='mp4',
            options={'video_track_timescale': str(lcm(rate, 24)), 'movie_timescale': str(lcm(rate, 24))}) as writer:
        stream = reader.streams.video[0]
        output = _copy_stream(writer, stream)
        clock = Fraction(1, lcm(rate, 24))
        output.time_base = clock
        pending = None
        for packet in reader.demux(stream):
            if packet.dts is None or packet.pts is None:
                continue
            base = packet.time_base
            when = packet.pts * base
            if when >= duration:
                continue
            length = min(Fraction(packet.duration or 1, 1) * base, duration - when)
            packet.pts = round(when / clock)
            packet.dts = round(packet.dts * base / clock)
            packet.duration = round(length / clock)
            packet.time_base, packet.stream = clock, output
            if frames is None:
                writer.mux(packet)
            else:
                if pending is not None:
                    writer.mux(pending)
                pending = packet
        if pending is not None:
            # MP4 derives intermediate sample durations from consecutive DTS.
            # The last decoded packet may display before the final frame, so
            # close the decode-order duration table, not just the highest PTS.
            pending.duration = round((duration - Fraction(frames - 1, 24)) / clock)
            writer.mux(pending)


def mux(video, output, info, *, trim_video=True, video_frames=None):
    """Copy a full AAC or MP3 track as it is; store any other sound as AAC 320 kbps.

    Decode and compare the saved track before making it visible. A copy must
    hold every sample; an AAC track must hold the same length and sound within
    AAC's error, and its receipt says so (sample_identical False, stored_as).
    The exact samples stay in the source file the work keeps, so editing and
    regeneration still read them. The selection is read, written and compared a
    frame at a time, so a two-hour soundtrack costs no more memory than a short one.
    """
    import numpy as np
    import tempfile
    from itertools import chain
    verify(info)
    destination = Path(output)
    rate, count = info['sample_rate'], info['end_sample'] - info['start_sample']

    def saved(path):
        return _rate(path) == rate and _same_samples(_decoded_chunks(path), map(_normalized, _selected_chunks(info)))

    stored_rate = rate if rate in AAC_RATES else AAC_FALLBACK_RATE

    def reference():
        chunks = map(_to_float, _selected_chunks(info))
        return chunks if stored_rate == rate else _resampled(chunks, rate, info['channels'], stored_rate)

    def saved_as_aac(path):
        # Lossy: the same length (the last AAC frame may run on) at the same rate, and the same sound within AAC's error.
        return _rate(path) == stored_rate and _close_samples(_decoded_chunks(path), reference())

    def saved_with_padding(path):
        # The MP4 cannot carry an MP3's end padding: at most two frames of it follow the last sample.
        return _rate(path) == rate and _same_samples(_decoded_chunks(path), map(_normalized, _selected_chunks(info)),
                                                     tail=2304)

    with tempfile.TemporaryDirectory(prefix='original-audio-', dir=destination.parent) as work:
        work = Path(work)
        temporary = work / 'mux.mp4'
        picture = work / 'picture.mp4'
        if trim_video:
            _trim_video(video, picture, count, rate, frames=video_frames)
        else:
            picture = Path(video)
        copied = False
        if info['full_source'] and info['codec'] in ('aac', 'mp3', 'mp3float'):
            try:
                _mux_streams(picture, temporary, rate, copy_from=info['path'])
                copied = (saved_with_padding if info['codec'] in ('mp3', 'mp3float') else saved)(temporary)
            except AudioUseError:
                raise
            except (OSError, ValueError, av_errors()):
                copied = False
        if not copied:
            # FLAC and PCM in an MP4 are silent on Apple devices and many system players (WebKit bugs 198583,
            # 260491, 275895), and browsers do not play ALAC. AAC plays everywhere; the exact samples stay as the
            # work's own source file, which editing and regeneration read.
            try:
                _mux_streams(picture, temporary, rate, encode=('aac', reference(), info['channels'], 4), audio_rate=stored_rate)
            except (OSError, ValueError, av_errors()):
                raise AudioUseError('audio_original_format') from None
            if not saved_as_aac(temporary):
                raise ValueError('Original audio export did not keep the sound; no output was saved')
        verify(info)
        temporary.replace(destination)
    return dict(use='original', mode='stream_copy' if copied else 'aac320',
                sample_identical=copied, stored_as=None if copied else ('AAC 320 kbps' if stored_rate == rate else '48 kHz AAC 320 kbps'),
                stored_sample_rate=rate if copied else stored_rate,
                source_samples_kept=True, sample_rate=rate, channels=info['channels'],
                samples_per_channel=count, source_sha256=info['sha256'],
                start_sample=info['start_sample'], end_sample=info['end_sample'],
                duration_seconds=count / rate)
