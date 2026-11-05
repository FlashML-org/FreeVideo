"""Inspect, sample and assemble media for agents. Clips are streamed, never held whole in RAM."""
from contextlib import contextmanager
from fractions import Fraction
import math
from pathlib import Path
import uuid

from ..comfy_assets import media_kind_of

FPS = 24
MAX_SAMPLES = 64
MAX_CLIPS = 100
# As the sharing export: browsers play it, and quality survives one more encode.
VIDEO_OPTIONS = {'crf': '18', 'preset': 'fast'}
AUDIO_BIT_RATE = 128_000
AUDIO_CHUNK = 1024
EPSILON = Fraction(1, 10_000)  # Seconds given as floats may land a hair before a frame time.
SHARP_SIDE = 384  # Sharpness is measured on a copy this size: fast, and blind to pixel-level noise.


def inspect(path):
    path = _file(path)
    kind = media_kind_of(path)
    info = dict(path=str(path), name=path.name, kind=kind, bytes=path.stat().st_size)
    if kind == 'image':
        from PIL import Image
        try:
            with Image.open(path) as image:
                width, height = image.size
                orientation = image.getexif().get(0x0112, 1)
                info.update(format=image.format, mode=image.mode)
        except Exception as error:
            raise ValueError('Cannot read image %s: %s' % (path.name, error)) from None
        if orientation in (5, 6, 7, 8):
            width, height = height, width
        return dict(info, width=width, height=height)
    with _reading(path) as container:
        video, audio = _video_stream(container), next(iter(container.streams.audio), None)
        if kind == 'video' and video is None:
            raise ValueError('%s has no video stream' % path.name)
        if kind == 'audio' and audio is None:
            raise ValueError('%s has no audio stream' % path.name)
        seconds = _stream_seconds(video if kind == 'video' else audio, container)
        if kind == 'video':
            context = video.codec_context
            rate = video.average_rate or video.guessed_rate
            fps = float(rate) if rate else None
            rotation = 0
            for frame in container.decode(video):
                rotation = _rotation(frame)
                break
            width, height = (context.height, context.width) if rotation % 180 else (context.width, context.height)
            frames, exact = video.frames, video.frames > 0
            if not exact:
                frames = round(seconds * fps) if seconds and fps else None
            info.update(width=width, height=height, rotation=rotation, fps=fps, frames=frames, frames_exact=exact,
                        codec=context.name, pix_fmt=context.pix_fmt)
        info.update(seconds=seconds, has_audio=audio is not None)
        if audio is not None:
            context = audio.codec_context
            info['audio'] = dict(codec=context.name, sample_rate=context.sample_rate, channels=context.channels,
                                 seconds=_stream_seconds(audio, container))
    return info


MAX_UPLOAD_PIXELS = 50 * 1024 * 1024
MAX_UPLOAD_SECONDS = 3600
MAX_UPLOAD_FRAMES = 216000
IMAGE_SUFFIX = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp', 'BMP': '.bmp', 'TIFF': '.tiff'}


def validate_upload(path):
    """Decode before publication, with bounded dimensions, duration and work."""
    info = inspect(path)
    if info['kind'] == 'image':
        from PIL import Image
        with Image.open(path) as image:
            if image.width * image.height > MAX_UPLOAD_PIXELS or image.width > 16384 or image.height > 16384:
                raise ValueError('Image exceeds upload pixel limit')
            suffix = IMAGE_SUFFIX.get(image.format)
            if suffix is None:
                raise ValueError('Unsupported decoded image format')
            if getattr(image, 'n_frames', 1) > MAX_SAMPLES:
                raise ValueError('Too many image frames')
            for i in range(getattr(image, 'n_frames', 1)):
                image.seek(i)
                image.load()
        return info, suffix
    if info.get('seconds') is None or not 0 < info['seconds'] <= MAX_UPLOAD_SECONDS:
        raise ValueError('Media duration exceeds upload limit')
    if info.get('width', 1) * info.get('height', 1) > MAX_UPLOAD_PIXELS:
        raise ValueError('Video dimensions exceed upload limit')
    count = video_frames = 0
    audio_seconds = 0.
    with _reading(path) as container:
        formats = set(container.format.name.split(','))
        suffix = (('.m4a' if info['kind'] == 'audio' else '.mp4') if formats & {'mov', 'mp4'} else '.webm' if 'webm' in formats else
                  '.mkv' if 'matroska' in formats else '.wav' if 'wav' in formats else
                  '.mp3' if 'mp3' in formats else '.flac' if 'flac' in formats else
                  '.ogg' if 'ogg' in formats else '.aac' if 'aac' in formats else None)
        if suffix is None:
            raise ValueError('Unsupported decoded container format')
        for frame in container.decode():
            count += 1
            if hasattr(frame, 'width'):
                video_frames += 1
            elif getattr(frame, 'sample_rate', 0):
                audio_seconds += frame.samples / frame.sample_rate
            if count > MAX_UPLOAD_FRAMES or getattr(frame, 'width', 1) * getattr(frame, 'height', 1) > MAX_UPLOAD_PIXELS:
                raise ValueError('Media exceeds decoded resource limit')
    if count == 0:
        raise ValueError('Empty media')
    if info.get('frames_exact') and video_frames < info['frames']:
        raise ValueError('Incomplete video frames')
    if info['kind'] == 'audio' and audio_seconds + .1 < info['seconds']:
        raise ValueError('Incomplete audio samples')
    return info, suffix


def extract_frame(path, out, at='last'):
    """Save one frame as PNG: 'first', 'last' (decoded, not taken from metadata) or the frame at/before `at` s."""
    path = _file(path, 'video')
    if at not in ('first', 'last'):
        at = _seconds(at, 'at')
    with _reading(path) as container:
        stream = _decodable(container, path)
        origin, rate = _origin(stream), _rate(stream)
        if at == 'first':
            chosen = next(_timed(container, stream, origin), None)
        elif at == 'last':
            chosen = _last(container, stream, origin)
        else:
            chosen = _at(container, stream, origin, at, path)
        if chosen is None:
            raise ValueError('%s contains no frames' % path.name)
        time, frame = chosen
        image = _upright(frame.to_image(), _rotation(frame))
    _save(image, out, 'PNG')
    return dict(path=str(out), width=image.width, height=image.height, seconds=float(time),
                frame_index=round(time * rate))


def sample_frames(path, out_dir, count=6, max_side=768):
    """Evenly spaced frames including the first and last, downscaled for a vision model."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_side = _integer(max_side, 'max_side', 64, 4096)
    result = []
    for number, (index, time, image) in enumerate(_picked(path, count), 1):
        image.thumbnail((max_side, max_side))
        target = out_dir / ('frame-%02d.jpg' % number)
        _save(image.convert('RGB'), target, 'JPEG', quality=92)
        result.append(dict(path=str(target), seconds=float(time), frame_index=index,
                           width=image.width, height=image.height))
    return result


def contact_sheet(path, out, count=8, columns=4, width=1536):
    """A JPEG grid of evenly spaced frames, each labelled with its MM:SS.mmm time."""
    from PIL import Image, ImageDraw, ImageFont
    columns = _integer(columns, 'columns', 1, 16)
    width = _integer(width, 'width', 256, 4096)
    picked = list(_picked(path, count))
    columns = min(columns, len(picked))
    rows = math.ceil(len(picked) / columns)
    gap = max(4, width // 192)
    cell_width = (width - gap * (columns + 1)) // columns
    first = picked[0][2]
    cell_height = max(1, round(cell_width * first.height / first.width))
    sheet = Image.new('RGB', (width, rows * cell_height + gap * (rows + 1)), '#111720')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(11, cell_width // 20))
    for number, (index, time, image) in enumerate(picked):
        x = gap + number % columns * (cell_width + gap)
        y = gap + number // columns * (cell_height + gap)
        image = image.convert('RGB')
        image.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
        sheet.paste(image, (x + (cell_width - image.width) // 2, y + (cell_height - image.height) // 2))
        label = clock(time)
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        pad = max(3, (bottom - top) // 3)
        box = (x + pad, y + cell_height - pad * 3 - (bottom - top), x + pad * 3 + right - left, y + cell_height - pad)
        draw.rounded_rectangle(box, radius=pad, fill='#0b0f14')
        draw.text((box[0] + pad - left, box[1] + pad - top), label, font=font, fill='#f2f5f8')
    _save(sheet, out, 'JPEG', quality=88)
    return dict(path=str(out), width=sheet.width, height=sheet.height, columns=columns, rows=rows,
                frames=[dict(seconds=float(time), frame_index=index) for index, time, _ in picked])


def clock(seconds):
    milliseconds = round(float(seconds) * 1000)
    return '%02d:%02d.%03d' % (milliseconds // 60000, milliseconds // 1000 % 60, milliseconds % 1000)


def assemble(clips, out, *, transition='cut', transition_seconds=.5, width=None, height=None, audio=True,
             progress=None, cancelled=None):
    """Join trimmed clips into one 24 fps H.264/AAC MP4, with cuts or crossfades."""
    import av
    import numpy as np
    from ..file_ops import publish
    if not isinstance(clips, (list, tuple)) or not 1 <= len(clips) <= MAX_CLIPS:
        raise ValueError('clips must list 1 to %d videos' % MAX_CLIPS)
    if transition not in ('cut', 'crossfade'):
        raise ValueError("transition must be 'cut' or 'crossfade'")
    if type(audio) is not bool:
        raise ValueError('audio must be true or false')
    plans = [_clip(row, index) for index, row in enumerate(clips)]
    overlap = 0
    if transition == 'crossfade' and len(plans) > 1:
        seconds = _seconds(transition_seconds, 'transition_seconds')
        if not Fraction(1, FPS) <= seconds <= 3:
            raise ValueError('transition_seconds must be between 1/24 and 3 seconds')
        overlap = max(1, round(seconds * FPS))
        for index, plan in enumerate(plans):
            needed = overlap * (2 if 0 < index < len(plans) - 1 else 1)
            if plan['frames'] < needed + (0 if 0 < index < len(plans) - 1 else 1):
                raise ValueError('clips[%d] is too short (%.3f s) for a %.3f s crossfade'
                                 % (index, plan['frames'] / FPS, overlap / FPS))
    if (width is None) != (height is None):
        raise ValueError('Give both width and height, or neither')
    if width is None:
        width, height = plans[0]['width'] // 2 * 2, plans[0]['height'] // 2 * 2
    width, height = _integer(width, 'width', 16, 4096), _integer(height, 'height', 16, 4096)
    if width % 2 or height % 2:
        raise ValueError('width and height must be even')
    starts, position = [], 0
    for plan in plans:
        starts.append(position)
        position += plan['frames'] - overlap
    total = position + overlap
    rate = next((plan['sample_rate'] for plan in plans if plan['sample_rate']), None) if audio else None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name('.assemble-%s.mp4' % uuid.uuid4().hex)
    written = 0
    def emit(writer, stream, array, sound, limit):
        nonlocal written
        if cancelled and cancelled():
            raise InterruptedError('Assembly cancelled')
        frame = av.VideoFrame.from_ndarray(array, format='rgb24')
        frame.pts, frame.time_base = written, Fraction(1, FPS)
        for packet in stream.encode(frame):
            writer.mux(packet)
        written += 1
        if sound is not None:
            sound.flush(min(_sample(written, rate), limit))
        if progress and (written % 12 == 0 or written == total):
            progress(dict(done=written, total=total))
    try:
        with av.open(str(temporary), 'w', format='mp4', options={'movflags': 'faststart'}) as writer:
            stream = writer.add_stream('libx264', rate=FPS)
            stream.width, stream.height, stream.pix_fmt = width, height, 'yuv420p'
            stream.options = dict(VIDEO_OPTIONS)
            sound = _Audio(writer, rate) if rate else None
            if progress:
                progress(dict(done=0, total=total))
            held = []
            for index, plan in enumerate(plans):
                final = index == len(plans) - 1
                begin, end = starts[index], starts[index] + plan['frames']
                limit = _sample(end - (0 if final else overlap), rate) if rate else 0
                if sound is not None:
                    sound.add(_sample(begin, rate), _clip_audio(plan, _sample(end, rate) - _sample(begin, rate), rate))
                kept = []
                for number, array in enumerate(_frames(plan, width, height)):
                    if index and number < overlap:
                        weight = number + 1
                        mixed = (held[number].astype(np.uint16) * (overlap + 1 - weight)
                                 + array.astype(np.uint16) * weight + (overlap + 1) // 2) // (overlap + 1)
                        emit(writer, stream, mixed.astype(np.uint8), sound, limit)
                    elif not final and number >= plan['frames'] - overlap:
                        kept.append(array)
                    else:
                        emit(writer, stream, array, sound, limit)
                held = kept
            for packet in stream.encode():
                writer.mux(packet)
            if sound is not None:
                sound.flush(_sample(total, rate), final=True)
        if written != total:
            raise RuntimeError('Assembled %d frames instead of %d' % (written, total))
        publish(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(path=str(out), seconds=total / FPS, frames=total, width=width, height=height,
                has_audio=rate is not None, sample_rate=rate, transition=transition,
                transition_seconds=overlap / FPS if overlap else 0.,
                clips=[dict(source=plan['path'], start=float(plan['start']), end=float(plan['end']),
                            seconds=plan['frames'] / FPS, frames=plan['frames'], output_start=starts[index] / FPS)
                       for index, plan in enumerate(plans)])


class _Audio:
    """Encode the mixed soundtrack as video frames are written, holding back a crossfade's overlap."""
    def __init__(self, writer, rate):
        import numpy as np
        self.np = np
        self.writer, self.rate = writer, rate
        self.stream = writer.add_stream('aac', rate=rate, layout='stereo')
        self.stream.codec_context.bit_rate = AUDIO_BIT_RATE
        self.base = 0
        self.pending = np.zeros((0, 2), np.float32)

    def add(self, start, samples):
        np = self.np
        overlap = self.base + len(self.pending) - start
        if not 0 <= overlap <= min(len(self.pending), len(samples)):
            raise RuntimeError('Audio overlap out of range')
        if overlap:
            # Equal-power fade: two unrelated soundtracks keep a steady loudness.
            curve = (np.arange(overlap, dtype=np.float32) + .5) / overlap * (np.pi / 2)
            self.pending[-overlap:] = (self.pending[-overlap:] * np.cos(curve)[:, None]
                                       + samples[:overlap] * np.sin(curve)[:, None])
        self.pending = np.concatenate([self.pending, samples[overlap:]])

    def flush(self, until, final=False):
        import av
        count = min(until, self.base + len(self.pending)) - self.base
        if not final:
            count -= count % AUDIO_CHUNK
        for offset in range(0, max(0, count), AUDIO_CHUNK):
            chunk = self.np.ascontiguousarray(self.pending[offset:min(count, offset + AUDIO_CHUNK)].T)
            frame = av.AudioFrame.from_ndarray(chunk, format='fltp', layout='stereo')
            frame.sample_rate, frame.pts, frame.time_base = self.rate, self.base + offset, Fraction(1, self.rate)
            for packet in self.stream.encode(frame):
                self.writer.mux(packet)
        if count > 0:
            self.pending = self.pending[count:]
            self.base += count
        if final:
            for packet in self.stream.encode():
                self.writer.mux(packet)


def _sample(frame, rate):
    return round(Fraction(frame * rate, FPS))


def _clip(row, index):
    name = 'clips[%d]' % index
    if isinstance(row, (str, Path)):
        row = dict(path=row)
    if not isinstance(row, dict) or set(row) - {'path', 'start', 'end'} or 'path' not in row:
        raise ValueError('%s must be {path, start?, end?}' % name)
    path = _file(row['path'], 'video')
    start = _seconds(row.get('start', 0), name + '.start')
    end = None if row.get('end') is None else _seconds(row['end'], name + '.end')
    with _reading(path) as container:
        stream = _decodable(container, path)
        origin, rate = _origin(stream), _rate(stream)
        first = next(container.decode(stream), None)
        if first is None:
            raise ValueError('%s contains no frames' % path.name)
        rotation = _rotation(first)
        _seek(container, stream, origin, 0)
        # The clip's true length from its packets; header durations can be rounded or missing.
        length, step = None, int(1 / (rate * stream.time_base)) or 1
        for packet in container.demux(stream):
            if packet.pts is not None:
                finish = packet.pts + (packet.duration or step)
                length = finish if length is None else max(length, finish)
        seconds = ((length - origin) * stream.time_base if length is not None
                   else Fraction(_stream_seconds(stream, container) or 0))
        audio = next(iter(container.streams.audio), None)
        sample_rate = audio.codec_context.sample_rate if audio is not None else None
        width, height = stream.codec_context.width, stream.codec_context.height
    stop = seconds if end is None else min(end, seconds)
    if start >= stop:
        raise ValueError('%s: start %.3f s is not before its end %.3f s' % (name, start, stop))
    if rotation % 180:
        width, height = height, width
    return dict(path=str(path), start=start, end=stop, frames=math.ceil((stop - start) * FPS - EPSILON),
                width=width, height=height, rotation=rotation, sample_rate=sample_rate)


def _frames(plan, width, height):
    """Exactly plan['frames'] RGB arrays: at each output time, the source frame shown then."""
    path, start, total = Path(plan['path']), plan['start'], plan['frames']
    produced, pending = 0, None
    for attempt in (0, 1):
        with _reading(path) as container:
            stream = _decodable(container, path)
            stream.thread_type = 'AUTO'
            origin = _origin(stream)
            if start > 2 and not attempt:
                _seek(container, stream, origin, start - 1)
            for time, frame in _timed(container, stream, origin):
                if pending is None and time > start + EPSILON and not attempt and start > 2:
                    break  # The seek overshot; decode from the start instead.
                if pending is not None:
                    while produced < total and start + Fraction(produced, FPS) + EPSILON < time:
                        yield pending
                        produced += 1
                if produced >= total:
                    return
                pending = _fit(frame, plan['rotation'], width, height)
            else:
                break
    if pending is None:
        raise ValueError('%s contains no frames' % path.name)
    while produced < total:
        yield pending
        produced += 1


def _fit(frame, rotation, width, height):
    import numpy as np
    turns = round(rotation / 90) % 4
    source_width, source_height = frame.width, frame.height
    shown_width, shown_height = (source_height, source_width) if turns % 2 else (source_width, source_height)
    if not turns and (shown_width, shown_height) == (width, height):
        return frame.to_ndarray(format='rgb24')
    scale = min(width / shown_width, height / shown_height)
    fit_width, fit_height = max(2, round(shown_width * scale)), max(2, round(shown_height * scale))
    if (fit_width, fit_height) != (shown_width, shown_height):
        size = (fit_height, fit_width) if turns % 2 else (fit_width, fit_height)
        frame = frame.reformat(width=size[0], height=size[1], format='rgb24', interpolation='LANCZOS')
    array = np.rot90(frame.to_ndarray(format='rgb24'), turns)
    if array.shape[:2] == (height, width):
        return np.ascontiguousarray(array)
    canvas = np.zeros((height, width, 3), np.uint8)
    top, left = (height - array.shape[0]) // 2, (width - array.shape[1]) // 2
    canvas[top:top + array.shape[0], left:left + array.shape[1]] = array
    return canvas


def _clip_audio(plan, count, rate):
    """`count` stereo float samples at `rate` from the clip's start; silence where it has none."""
    import av
    import numpy as np
    result = np.zeros((count, 2), np.float32)
    path = Path(plan['path'])
    with _reading(path) as container:
        audio, video = next(iter(container.streams.audio), None), _video_stream(container)
        if audio is None or not count:
            return result
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=rate)
        position = None  # Clip sample index of the next converted sample.
        def place(frames):
            nonlocal position
            for converted in frames:
                data = converted.to_ndarray().T
                begin, end = max(0, position), min(count, position + len(data))
                if begin < end:
                    result[begin:end] = data[begin - position:end - position]
                position += len(data)
        for frame in container.decode(audio):
            if position is None:
                # Samples before the clip's start are skipped; a late audio start leaves leading silence.
                began = Fraction(frame.pts or 0) * audio.time_base - _origin(video) * video.time_base
                position = round((began - plan['start']) * rate)
            place(resampler.resample(frame))
            if position >= count:
                break
        else:
            if position is not None:
                place(resampler.resample(None))
    return result


def trim_audio(path, out, start, end):
    """Write seconds [start, end) of the audio track as 16-bit mono WAV; returns the seconds written."""
    import av
    import numpy as np
    import wave
    from ..file_ops import publish
    chunks, rate, position = [], None, None
    with _reading(_file(path)) as container:
        audio = next(iter(container.streams.audio), None)
        if audio is None:
            raise ValueError('No audio stream')
        rate = audio.codec_context.sample_rate or 48000
        origin = Fraction(audio.start_time or 0) * audio.time_base if audio.start_time is not None else 0
        resampler = av.AudioResampler(format='s16', layout='mono', rate=rate)
        first, last = round(start * rate), round(end * rate)
        def place(frames):
            nonlocal position
            for converted in frames:
                data = converted.to_ndarray().reshape(-1)
                begin, stop = max(first, position), min(last, position + len(data))
                if begin < stop:
                    chunks.append(data[begin - position:stop - position])
                position += len(data)
        for frame in container.decode(audio):
            if position is None:
                position = round((Fraction(frame.pts or 0) * audio.time_base - origin) * rate)
            place(resampler.resample(frame))
            if position >= last:
                break
        else:
            if position is not None:
                place(resampler.resample(None))
    samples = np.concatenate(chunks).astype('<i2') if chunks else np.zeros(0, '<i2')
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name('.%s-%s.partial' % (out.name, uuid.uuid4().hex[:8]))
    try:
        with wave.open(str(temporary), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(samples.tobytes())
        publish(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)
    return len(samples) / rate


def sharpness(frame):
    """Variance of the 4-neighbour Laplacian on a small grey copy: higher is sharper."""
    import numpy as np
    scale = min(1., SHARP_SIDE / max(frame.width, frame.height))
    width, height = max(8, round(frame.width * scale)), max(8, round(frame.height * scale))
    grey = frame.reformat(width=width, height=height, format='gray').to_ndarray().astype(np.float32)
    laplacian = grey[:-2, 1:-1] + grey[2:, 1:-1] + grey[1:-1, :-2] + grey[1:-1, 2:] - 4 * grey[1:-1, 1:-1]
    return float(laplacian.var())


def sharpest_frames(path, outs, *, edge=.08, blur=.35):
    """Split a clip into len(outs) equal parts by progress and save the sharpest frame of each as PNG.

    Frames within `edge` of either end are skipped, and a part whose sharpest frame scores below
    `blur` times the clip's sharpest is left out (every frame in it is blurred). Decodes once and
    keeps one image per part. Returns one row per part: None for a part left out.
    """
    path = _file(path, 'video')
    count = _integer(len(outs), 'count', 1, MAX_SAMPLES)
    with _reading(path) as container:
        total = sum(1 for packet in container.demux(_decodable(container, path)) if packet.size)
    if not total:
        raise ValueError('%s contains no frames' % path.name)
    skip = max(0, min(math.floor(total * edge), (total - count) // 2))
    usable = total - 2 * skip
    best = [None] * count
    with _reading(path) as container:
        stream = _decodable(container, path)
        for index, (time, frame) in enumerate(_timed(container, stream, _origin(stream))):
            if index < skip:
                continue
            if index >= total - skip:
                break
            part = min(count - 1, (index - skip) * count // usable)
            score = sharpness(frame)
            if best[part] is None or score > best[part][0]:
                best[part] = score, index, time, _upright(frame.to_image(), _rotation(frame))
    top = max((row[0] for row in best if row), default=0)
    result = []
    for out, row in zip(outs, best):
        if row is None or top <= 0 or row[0] < blur * top:
            result.append(None)
            continue
        score, index, time, image = row
        _save(image.convert('RGB'), out, 'PNG')
        result.append(dict(seconds=float(time), frame_index=index, width=image.width, height=image.height,
                           sharpness=round(score, 3), progress=round(index / max(1, total - 1), 4)))
    return result


def _picked(path, count):
    """(frame index, seconds, upright PIL image) for evenly spaced frames, decoding once."""
    path = _file(path, 'video')
    count = _integer(count, 'count', 1, MAX_SAMPLES)
    with _reading(path) as container:
        total = sum(1 for packet in container.demux(_decodable(container, path)) if packet.size)
    if not total:
        raise ValueError('%s contains no frames' % path.name)
    count = min(count, total)
    wanted = {(total - 1) // 2} if count == 1 else {round(i * (total - 1) / (count - 1)) for i in range(count)}
    result, last = [], None
    with _reading(path) as container:
        stream = _decodable(container, path)
        for index, (time, frame) in enumerate(_timed(container, stream, _origin(stream))):
            last = index, time, frame
            if index in wanted:
                result.append((index, time, _upright(frame.to_image(), _rotation(frame))))
                if len(result) == len(wanted):
                    break
        if last is None:
            raise ValueError('%s contains no frames' % path.name)
        if len(result) < len(wanted) and (not result or result[-1][0] != last[0]):
            # Fewer decodable frames than packets: the true last frame stands in.
            result.append((last[0], last[1], _upright(last[2].to_image(), _rotation(last[2]))))
    return result


def _at(container, stream, origin, at, path):
    """The last frame shown at or before `at` seconds; up to one frame past the end means the last frame."""
    rate = _rate(stream)
    for seeking in ((True, False) if at > 2 else (False,)):
        if seeking and not _seek(container, stream, origin, at - 1):
            continue
        if not seeking and at > 2:
            _seek(container, stream, origin, 0)
        chosen = None
        for time, frame in _timed(container, stream, origin):
            if time > at + EPSILON:
                break
            chosen = time, frame
        else:
            if chosen is not None and at > chosen[0] + 1 / rate + EPSILON:
                raise ValueError('at=%.3f s is past the end of %s (%.3f s)'
                                 % (at, path.name, chosen[0] + 1 / rate))
        if chosen is not None:
            return chosen
        # A seek past `at` decodes again from the start; a first frame after `at` stands in for it.
    _seek(container, stream, origin, 0)
    return next(_timed(container, stream, origin), None)


def _last(container, stream, origin):
    duration = _stream_seconds(stream, container)
    chosen = None
    # Seek near the end by the header, then decode to the real end: a wrong duration only costs time.
    if duration and duration > 3 and _seek(container, stream, origin, Fraction(duration) - 3):
        for chosen in _timed(container, stream, origin):
            pass
    if chosen is None:
        _seek(container, stream, origin, 0)
        for chosen in _timed(container, stream, origin):
            pass
    if chosen is None:
        raise ValueError('The video contains no frames')
    return chosen


def _timed(container, stream, origin):
    rate = _rate(stream)
    previous = None
    for frame in container.decode(stream):
        if frame.pts is not None:
            time = (Fraction(frame.pts) - origin) * stream.time_base
        else:
            time = Fraction(0) if previous is None else previous + 1 / rate
        previous = time
        yield time, frame


def _seek(container, stream, origin, seconds):
    import av
    try:
        container.seek(int(origin + max(Fraction(0), seconds) / stream.time_base), stream=stream, backward=True)
        return True
    except av.error.FFmpegError:
        return False


def _origin(stream):
    return Fraction(stream.start_time or 0)


def _rate(stream):
    rate = stream.average_rate or stream.guessed_rate or stream.base_rate
    rate = Fraction(rate) if rate else Fraction(FPS)
    return rate if 1 <= rate <= 240 else Fraction(FPS)


def _rotation(frame):
    try:
        return round(frame.rotation or 0) % 360
    except (AttributeError, ValueError):
        return 0


def _upright(image, rotation):
    from PIL import Image
    turns = round(rotation / 90) % 4
    return image.transpose((None, Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_180,
                            Image.Transpose.ROTATE_270)[turns]) if turns else image


def _stream_seconds(stream, container):
    if stream is not None and stream.duration and stream.time_base:
        return float(stream.duration * stream.time_base)
    return container.duration / 1e6 if container.duration else None


def _video_stream(container):
    import av
    import av.stream
    attached = getattr(getattr(av.stream, 'Disposition', None), 'attached_pic', 0)
    for stream in container.streams.video:
        if not (attached and stream.disposition & attached):
            return stream  # Cover art in audio files is not a video.
    return None


def _decodable(container, path):
    stream = _video_stream(container)
    if stream is None:
        raise ValueError('%s has no video stream' % path.name)
    return stream


@contextmanager
def _reading(path):
    import av
    try:
        try:
            container = av.open(str(path), metadata_errors='ignore')
        except TypeError:  # PyAV 19 dropped the option
            container = av.open(str(path))
        with container:
            yield container
    except av.error.FFmpegError as error:
        raise ValueError('Cannot read %s: %s' % (path.name, error)) from None


def saved_geometry(path, geometry):
    """The report's geometry with the length the saved video really has.

    A continued clip's report also counts the frames it took over from the clip before it.
    """
    try:
        info = inspect(path)
    except Exception:  # noqa: BLE001 - the report's own numbers stand when the file cannot be read here
        return geometry
    if not info.get('frames_exact') or info['frames'] == geometry.get('frames'):
        return geometry
    fps = geometry.get('fps') or info.get('fps')
    return dict(geometry, frames=info['frames'], **(dict(seconds=info['frames'] / fps) if fps else {}))


def _file(path, kind=None):
    if not isinstance(path, (str, Path)) or not str(path):
        raise ValueError('A media file path is required')
    path = Path(path)
    if not path.is_file():
        raise ValueError('No such media file: %s' % path.name)
    if kind and media_kind_of(path) != kind:
        raise ValueError('%s is not a %s file' % (path.name, kind))
    return path


def _seconds(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float, Fraction))
            or not math.isfinite(value) or value < 0):
        raise ValueError('%s must be a number of seconds, 0 or more' % name)
    return Fraction(value)


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('%s must be an integer from %d to %d' % (name, low, high))
    return value


def _save(image, out, format, **options):
    from ..file_ops import publish
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name('.%s-%s.partial' % (out.name, uuid.uuid4().hex[:8]))
    try:
        image.save(temporary, format, **options)
        publish(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)
