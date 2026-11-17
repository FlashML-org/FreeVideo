"""Integer clip clocks and bounded CPU speed/reverse readers and previews."""
import asyncio
import math
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from fractions import Fraction
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

FPS = 24
RATE = 48000
SAMPLES_PER_FRAME = RATE // FPS
VIDEO_CACHE_BYTES = 384 * 1024**2
VIDEO_CACHE_FRAMES = 128
AUDIO_BLOCK_SAMPLES = 65536


def percent(clip):
    return round(clip.get('speed', 1) * 100)


def changed(clip):
    return clip is not None and (clip.get('speed', 1) != 1 or clip.get('reverse', False))


def audio_changed(clip):
    return changed(clip) or bool(clip.get('speed_phase', 0))


def source_span(clip):
    return ((int(clip['frames']) - 1) * percent(clip) + clip.get('speed_phase', 0)) // 100 + 1


def validate_clip(clip, *, error=ValueError):
    """Call after ordinary range checks; return only explicitly saved fields."""
    speed, reverse = clip.get('speed', 1), clip.get('reverse', False)
    if (type(speed) not in (int, float) or not math.isfinite(speed) or not .25 <= speed <= 4
            or Decimal(str(speed)) * 100 != round(speed * 100)):
        raise error('Invalid clip speed')
    if type(reverse) is not bool:
        raise error('Invalid clip reverse')
    phase = clip.get('speed_phase', 0)
    if type(phase) is not int or not 0 <= phase <= 99:
        raise error('Invalid speed phase')
    if 'speed_source_frames' in clip and (type(clip['speed_source_frames']) is not int
                                        or clip['speed_source_frames'] <= 0):
        raise error('Invalid speed source frames')
    if clip.get('speed_audio', 'follow') not in ('follow', 'mute'):
        raise error('Invalid speed audio')
    if int(clip['in']) + source_span(clip) > clip['length']:
        raise error('Invalid clip source span')
    return {key: clip[key] for key in ('speed', 'reverse', 'speed_phase', 'speed_source_frames', 'speed_audio')
            if key in clip}


def source_frame(clip, local):
    k = clip['frames'] - 1 - local if clip.get('reverse', False) else local
    return clip['in'] + (k * percent(clip) + clip.get('speed_phase', 0)) // 100


def source_sample_start(clip):
    return clip['in'] * SAMPLES_PER_FRAME + clip.get('speed_phase', 0) * 20


def source_sample_count(clip):
    # At 48 kHz / 24 fps every integer percentage is an exact sample count.
    return clip['frames'] * 20 * percent(clip)


def tempo_factors(speed):
    """Use the older FFmpeg atempo range, even on builds accepting larger rates."""
    rate, factors = Fraction(speed, 100), []
    while rate < Fraction(1, 2):
        factors.append(Fraction(1, 2))
        rate *= 2
    while rate > 2:
        factors.append(Fraction(2))
        rate /= 2
    if rate != 1:
        factors.append(rate)
    return factors


def independent_join(left, right):
    return changed(left) or changed(right)


def _file_identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class VideoReader:
    """Forward decode once; reverse bounded windows from a backward keyframe seek.

    A long GOP is decoded in several passes. Only the current window's RGB
    frames are retained, independently of the GOP or clip length.
    """
    def __init__(self, path, clip, width, height, cancelled, *, reader_factory=None):
        from .editor_export import _VideoReader
        self.factory = reader_factory or _VideoReader
        self.path, self.clip = path, clip
        self.width, self.height, self.cancelled = width, height, cancelled
        self.capacity = max(1, min(VIDEO_CACHE_FRAMES, VIDEO_CACHE_BYTES // (width * height * 3)))
        self.cache = {}
        self.peak_cached_frames = self.decode_passes = 0
        self.reader = None
        if not clip.get('reverse', False):
            self.reader = self.factory(path, clip['in'], width, height, cancelled, clip)
            self.decode_passes = 1

    def pixels(self, number):
        from .editor_export import _check
        mapped = source_frame(self.clip, number - self.clip['in'])
        if self.reader is not None:
            return self.reader.pixels(mapped)
        if mapped not in self.cache:
            self.cache.clear()
            low = max(self.clip['in'], mapped - self.capacity + 1)
            reader = self.factory(self.path, low, self.width, self.height, self.cancelled, self.clip)
            try:
                self.decode_passes += 1
                for index in range(low, mapped + 1):
                    _check(self.cancelled)
                    # Copy also detaches a cover crop from its larger backing image.
                    self.cache[index] = reader.pixels(index).copy()
                    self.peak_cached_frames = max(self.peak_cached_frames, len(self.cache))
            finally:
                reader.close()
        return self.cache[mapped]

    def close(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.cache.clear()


class AudioReader:
    """Stream atempo, feeding reverse audio in bounded blocks from the tail."""
    def __init__(self, path, clip, cancelled):
        from .editor_export import _audio_start
        self.path, self.clip, self.cancelled = path, clip, cancelled
        # Keep the caller's old samples() clock; changed clips ignore splice.
        self.call_origin = _audio_start(clip)
        self.chunks = iter(self._chunks())
        self.chunk = None
        try:
            self.chunk = next(self.chunks, None)
        except BaseException:
            self.close()
            raise

    def _input(self):
        from .editor_export import _AudioReader, _check
        first = source_sample_start(self.clip)
        remaining = source_sample_count(self.clip)
        reader = None
        try:
            if not self.clip.get('reverse', False):
                reader = _AudioReader(self.path, first, self.cancelled)
                position = first
                while remaining:
                    _check(self.cancelled)
                    count = min(8192, remaining)
                    yield reader.samples(position, count)
                    remaining -= count
                    position += count
            else:
                while remaining:
                    _check(self.cancelled)
                    count = min(AUDIO_BLOCK_SAMPLES, remaining)
                    position = first + remaining - count
                    reader = _AudioReader(self.path, position, self.cancelled)
                    data = reader.samples(position, count)
                    reader.close()
                    reader = None
                    yield data[:, ::-1].copy()
                    remaining -= count
        finally:
            if reader is not None:
                reader.close()

    def _chunks(self):
        import av
        import numpy as np
        from .editor_export import _check
        graph = None
        factors = tempo_factors(percent(self.clip))
        if factors:
            graph = av.filter.Graph()
            graph.threads = 1
            node = graph.add('abuffer', sample_rate=str(RATE), sample_fmt='fltp',
                             channel_layout='stereo', time_base='1/48000')
            for factor in factors:
                following = graph.add('atempo', str(float(factor)))
                node.link_to(following)
                node = following
            # atempo may negotiate packed float; readers consume planar stereo.
            planar = graph.add('aformat', sample_fmts='fltp', sample_rates=str(RATE), channel_layouts='stereo')
            node.link_to(planar)
            sink = graph.add('abuffersink')
            planar.link_to(sink)
            graph.configure()
        position, native, accepted = 0, 0, 0
        tail = np.empty((2, 0), dtype=np.float32)
        total = self.clip['frames'] * SAMPLES_PER_FRAME

        def emit(output):
            nonlocal position, accepted, tail
            kept = min(output.shape[1], total - accepted)
            if kept:
                accepted += kept
                data = np.concatenate((tail, output[:, :kept]), axis=1)
                count = max(0, data.shape[1] - RATE // 200)
                tail = data[:, count:].copy()
                if count:
                    yield position, data[:, :count]
                    position += count

        def pull():
            while True:
                _check(self.cancelled)
                try:
                    yield graph.pull().to_ndarray()
                except (av.error.BlockingIOError, av.error.EOFError):
                    return

        inputs = self._input()
        try:
            for data in inputs:
                _check(self.cancelled)
                if graph is None:
                    outputs = (data,)
                else:
                    frame = av.AudioFrame.from_ndarray(data, format='fltp', layout='stereo')
                    frame.sample_rate, frame.pts, frame.time_base = RATE, native, Fraction(1, RATE)
                    native += data.shape[1]
                    graph.push(frame)
                    outputs = pull()
                for output in outputs:
                    yield from emit(output)
            if graph is not None:
                # A short slow clip may not fill atempo's analysis window.
                # Supply bounded zero context, without reading beyond its range.
                frame = av.AudioFrame.from_ndarray(np.zeros((2, 8192), dtype=np.float32),
                                                  format='fltp', layout='stereo')
                frame.sample_rate, frame.pts, frame.time_base = RATE, native, Fraction(1, RATE)
                graph.push(frame)
                for output in pull():
                    yield from emit(output)
                graph.push(None)
                for output in pull():
                    yield from emit(output)
            # atempo's overlap rounding need not produce the exact timeline count.
            # Fade a short filter tail before padding, avoiding an early hard cut.
            # The renderer still applies the exact timeline's ordinary 5 ms fades.
            if tail.shape[1]:
                if accepted < total:
                    tail *= np.linspace(1, 0, tail.shape[1], dtype=np.float32)
                yield position, tail
                position += tail.shape[1]
            while position < total:
                _check(self.cancelled)
                count = min(8192, total - position)
                yield position, np.zeros((2, count), dtype=np.float32)
                position += count
        finally:
            inputs.close()

    def samples(self, start, count):
        from .editor_export import _AudioReader
        return _AudioReader.samples(self, start - self.call_origin, count)

    def close(self):
        if self.chunks is not None:
            self.chunks.close()
        self.chunk = self.chunks = None


def reverse_copy(path, target, cancelled=None):
    """Reverse the source's 24 fps display grid; encode a browser-playable copy."""
    import av
    from .editor_export import _check, _audio_frame, _checked_target
    from .editor_frames import last_frame_end
    from .media_encoding import clip_seconds
    _check(cancelled)
    _checked_target(path)
    with av.open(str(path)) as source:
        vin, ain = next(iter(source.streams.video), None), next(iter(source.streams.audio), None)
        stream = vin or ain
        if stream is None:
            raise ValueError('Unplayable reverse source')
        seconds = clip_seconds(source, stream)
        if vin is None and (seconds is None or not math.isfinite(seconds) or seconds <= 0):
            raise ValueError('Reverse duration unavailable')
        length = math.ceil(seconds * FPS - 1e-6) if seconds is not None and seconds > 0 else 0
        width = height = 0
        if vin is not None:
            if min(vin.width, vin.height) < 2 or vin.width * vin.height > 64_000_000:
                raise ValueError('Invalid reverse dimensions')
            scale = min(1., 720 / min(vin.width, vin.height), 1920 / max(vin.width, vin.height))
            width, height = (max(2, int(side * scale) // 2 * 2) for side in (vin.width, vin.height))
            # Some containers report only the last PTS, excluding its duration.
            # Count the actual display grid with one decoded frame, including VFR.
            vin.thread_count = 1
            origin = Fraction(vin.start_time or 0) * vin.time_base
            end = None
            rate = vin.average_rate or vin.guessed_rate or FPS
            for index, frame in enumerate(source.decode(vin)):
                _check(cancelled)
                stamp = (Fraction(frame.pts) * frame.time_base - origin if frame.pts is not None
                         else Fraction(index, 1) / rate)
                end = last_frame_end(stamp, frame, rate)
            if end is None or end <= 0:
                raise ValueError('Empty reverse video')
            length = math.ceil(end * FPS)
    clip = dict(frames=length, length=length, speed=1, reverse=True, **{'in': 0})
    partial = target.with_name('.reverse-' + uuid.uuid4().hex + '.mp4')
    published = complete = False
    try:
        _checked_target(target)
        _checked_target(partial)
        target.parent.mkdir(parents=True, exist_ok=True)
        _checked_target(partial)
        with ExitStack() as readers, av.open(str(partial), 'w', format='mp4',
                options={'movflags': 'faststart', 'movie_timescale': str(RATE)}) as output:
            video = audio = pictures = sound = None
            if width:
                pictures = VideoReader(path, clip, width, height, cancelled)
                readers.callback(pictures.close)
                video = output.add_stream('libx264', rate=FPS)
                video.width, video.height, video.pix_fmt = width, height, 'yuv420p'
                video.options = {'crf': '18', 'preset': 'fast'}
                video.codec_context.thread_count = 2
            if ain is not None:
                sound = AudioReader(path, clip, cancelled)
                readers.callback(sound.close)
                audio = output.add_stream('aac', rate=RATE)
                audio.layout = 'stereo'
                audio.codec_context.time_base = Fraction(1, RATE)
                audio.codec_context.thread_count = 1
            for number in range(length):
                _check(cancelled)
                if video is not None:
                    frame = av.VideoFrame.from_ndarray(pictures.pixels(number), format='rgb24')
                    frame.pts, frame.time_base = number, Fraction(1, FPS)
                    for packet in video.encode(frame):
                        output.mux(packet)
                if audio is not None:
                    frame = _audio_frame(sound.samples(number * SAMPLES_PER_FRAME, SAMPLES_PER_FRAME),
                                         format='fltp', layout='stereo')
                    frame.sample_rate = RATE
                    frame.pts, frame.time_base = number * SAMPLES_PER_FRAME, Fraction(1, RATE)
                    for packet in audio.encode(frame):
                        output.mux(packet)
            for stream in (video, audio):
                if stream is not None:
                    for packet in stream.encode(None):
                        _check(cancelled)
                        output.mux(packet)
        _check(cancelled)
        _checked_target(target)
        _checked_target(partial)
        os.replace(partial, target)
        published = True
        _check(cancelled)
        complete = True
        return dict(frames=length, fps=FPS, width=width, height=height,
                    has_video=video is not None, has_audio=audio is not None)
    finally:
        partial.unlink(missing_ok=True)
        if published and not complete:
            target.unlink(missing_ok=True)


class ReverseCache:
    """One worker per server, deduplicated by _cache's source file identity."""
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='editor-reverse')
        self.pending, self.failed = {}, OrderedDict()
        self.closed = False

    def _location(self, key):
        from .comfy_editor import _cache, _import_path, _key, _safe, media_path
        path = media_path(self.root, key)
        source = _key(key)
        if source['kind'] == 'import':
            # Exports decode the original, rather than the lossy import preview.
            original = _import_path(self.root, source['id'], 'source.bin')
            if original.is_file():
                path = original
        if not path.is_file():
            raise FileNotFoundError('Reverse source unavailable')
        stamp = '24fps-v2:' + ':'.join(map(str, _file_identity(path)))
        target = _cache(self.root, 'reverse', key, path, stamp).with_suffix('.mp4')
        return path, _safe(self.root, target.relative_to(Path(self.root).resolve()).as_posix())

    def status(self, key):
        from .comfy_editor import probe
        path, target = self._location(key)
        with self.lock:
            if self.closed:
                return dict(state='cancelled', reason='cancelled')
            if target in self.pending:
                return dict(state='preparing', poll_after_ms=250)
            if target.is_file():
                info = probe(target)
                address = '/freevideo/editor/reverse?' + urlencode(dict(key=key, file='preview', cache=target.stem))
                return dict(state='ready', **info, video=address if info['has_video'] else None,
                            audio=address if info['has_audio'] else None)
            if target in self.failed:
                return dict(state='failed', reason=self.failed[target])
            if len(self.pending) >= 64:
                return dict(state='busy', poll_after_ms=1000)
            stop = threading.Event()
            self.pending[target] = stop
            self.executor.submit(self._make, key, path, target, stop)
            return dict(state='preparing', poll_after_ms=250)

    def _make(self, key, path, target, stop):
        from .editor_export import _check, _reason
        try:
            identity = _file_identity(path)
            if (path, target) != self._location(key):
                raise ValueError('Reverse source changed')
            reverse_copy(path, target, stop.is_set)
            _check(stop.is_set)
            if identity != _file_identity(path) or (path, target) != self._location(key):
                raise ValueError('Reverse source changed')
            from .comfy_editor import trim_caches
            trim_caches(self.root)
        except BaseException as error:
            target.unlink(missing_ok=True)
            with self.lock:
                self.failed[target] = 'cancelled' if stop.is_set() else _reason(error)
                while len(self.failed) > 128:
                    self.failed.popitem(last=False)
        finally:
            with self.lock:
                self.pending.pop(target, None)

    def media(self, key, token=None):
        _, target = self._location(key)
        with self.lock:
            if target in self.pending or not target.is_file() or token is not None and token != target.stem:
                raise FileNotFoundError('Reverse preview unavailable')
            return target

    def close(self):
        with self.lock:
            self.closed = True
            for stop in self.pending.values():
                stop.set()
        # Let queued workers observe cancellation and clean their owned files.
        self.executor.shutdown(wait=True)


def register_reverse(endpoint, root, server):
    from aiohttp import web
    from .comfy_editor import probe
    cache = ReverseCache(root())
    server._freevideo_editor_reverse = cache

    @endpoint('get', '/freevideo/editor/reverse')
    async def reverse(request):
        key = request.query.get('key')
        if 'file' in request.query:
            if request.query['file'] != 'preview':
                raise ValueError('Invalid reverse media')
            path = await asyncio.to_thread(cache.media, key, request.query.get('cache'))
            info = await asyncio.to_thread(probe, path)
            content_type = 'video/mp4' if info['has_video'] else 'audio/mp4'
            return web.FileResponse(path, headers={'Content-Type': content_type, 'Cache-Control': 'no-store'})
        value = await asyncio.to_thread(cache.status, key)
        status = 202 if value['state'] == 'preparing' else 503 if value['state'] == 'busy' else 200
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    async def shutdown(app):
        await asyncio.to_thread(cache.close)

    if getattr(server, 'app', None) is not None:
        server.app.on_shutdown.append(shutdown)
