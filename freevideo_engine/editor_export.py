"""CPU-only, bounded editor rendering and a serial background export queue.

The renderer is independent of ComfyUI and the editor persistence service.
Only the two persistence adapters below need that service at integration time.
"""
import asyncio
import copy
import errno
import importlib
import json
import logging
import math
import os
import re
import threading
import tempfile
import time
import uuid
from collections import deque
from contextlib import ExitStack
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from .editor_frames import display_frame, frame_not_after, last_frame_end
from .comfy_continuation import audio_lead_in, apply_audio_lead_in
from .editor_limits import PROJECT_BYTES
from .editor_text import is_text_track, has_text_data, validate_text_project, paint_project, check_export_text, MissingGlyphs
from . import editor_export_options
from .audio_loudness import Limiter
from .editor_speed import validate_clip, changed as speed_changed, audio_changed, independent_join, VideoReader, AudioReader
from .editor_audio import (clip_fields, track_fields, clip_audible, track_audible, crossfade_reads, apply_track_audio,
                           duck_ranges, duck_gain, duck_sources)

FPS = 24
SAMPLE_RATE = 48000
SPLICE_RATE = 32000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
FADE_SAMPLES = SAMPLE_RATE // 200  # 5 ms
_PROJECT_ID = re.compile(r'e-[0-9a-f]{32}')
_EXPORT_ID = re.compile(r'x-[0-9a-f]{32}')
_IMPORT_ID = re.compile(r'[A-Za-z0-9_-]{1,80}')
_RECORD_BYTES = 8 * 1024 * 1024


class ExportError(Exception):
    """A public reason code; never retain a decoder's path-bearing message."""
    def __init__(self, reason='failed'):
        self.reason = reason if reason in ('source_missing', 'unreadable', 'disk_full', 'not_writable', 'failed') else 'failed'
        super().__init__(self.reason)


class ExportConflict(ValueError):
    def __init__(self, revision):
        super().__init__('Editor project has changed')
        self.revision = revision


class ExportCancelled(InterruptedError):
    pass


class _SeamRepairError(Exception):
    """An optional seam operation failed; the ordinary export may still work."""


def _seam_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ExportCancelled:
        raise
    except Exception as error:
        # The ordinary export reports a real source error under its own name.
        raise _SeamRepairError() from error


def _reason(error):
    if isinstance(error, ExportError):
        return error.reason
    from .disk_space import ran_out
    if ran_out(error):
        return 'disk_full'
    if isinstance(error, PermissionError) or isinstance(error, OSError) and error.errno in (errno.EACCES, errno.EROFS):
        return 'not_writable'
    return 'failed'


def _without_subtitles(project, settings):
    """`subtitles: none` leaves the subtitle track out of the picture (text clips stay)."""
    if settings.subtitles != 'none':
        return project
    return {**project, 'tracks': [t for t in project['tracks'] if t.get('kind') != 'subtitle']}


def _check(cancelled):
    if cancelled and cancelled():
        raise ExportCancelled('cancelled')


def _whole(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53 - 1


def _layout(project):
    """Bound encoder inputs; persistence/model own the full editing rules."""
    if not isinstance(project, dict) or project.get('version') != 1 or project.get('fps') != FPS:
        raise ExportError()
    width, height = project.get('width'), project.get('height')
    if any(not _whole(side, 2) or side > 8192 or side % 2 for side in (width, height)):
        raise ExportError()
    raw_tracks = project.get('tracks')
    if has_text_data(project):
        from .comfy_editor import validate
        project = validate(project)
        raw_tracks = [t for t in project['tracks'] if not is_text_track(t)]
    if not isinstance(raw_tracks, list) or [t.get('id') for t in raw_tracks if isinstance(t, dict)] not in (
            ['v1', 'a1'], ['v1', 'a1', 'a2'], ['v1', 'a1', 'a3'], ['v1', 'a1', 'a2', 'a3'], ['v1', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6']):
        raise ExportError()
    tracks, seen = {}, set()
    for raw in raw_tracks:
        if raw.get('kind') != ('video' if raw['id'] == 'v1' else 'audio'):
            raise ExportError()
        try:
            track_fields(raw)
        except ValueError:
            raise ExportError() from None
        clips = raw.get('clips')
        if not isinstance(clips, list) or len(clips) > 10000:
            raise ExportError()
        end = 0
        for clip in clips:
            if not isinstance(clip, dict) or not isinstance(clip.get('id'), str) or clip['id'] in seen:
                raise ExportError()
            seen.add(clip['id'])
            if any(not _whole(clip.get(key), 1 if key == 'frames' else 0)
                   for key in ('start', 'frames', 'in', 'length')):
                raise ExportError()
            validate_clip(clip, error=ExportError)
            try:
                clip_fields(clip, raw['kind'])
            except ValueError:
                raise ExportError() from None
            if clip['start'] < end:
                raise ExportError()
            end = clip['start'] + clip['frames']
            source = clip.get('source')
            if source is not None and (not isinstance(source, dict) or source.get('kind') not in (
                    'work', 'import', 'generated') or not isinstance(source.get('id'), str) or not source['id']):
                raise ExportError()
            if raw['id'] != 'v1':
                volume = clip.get('volume', 1)
                if type(volume) not in (int, float) or not math.isfinite(volume) or not 0 <= volume <= 2:
                    raise ExportError()
                splice = clip.get('splice')
                if splice is not None and (not isinstance(splice, dict) or any(
                        not _whole(splice.get(key)) for key in ('frames', 'audio_samples'))):
                    raise ExportError()
        tracks[raw['id']] = clips
    # Picture and sound set the length; text after them is not exported.
    frames = max((c['start'] + c['frames'] for clips in tracks.values() for c in clips), default=0)
    if not frames:
        raise ExportError()
    return width, height, frames, tracks


def _media(resolve, clip, kind):
    source = clip.get('source')
    if source is None:
        return None  # Unfilled ranges render as black/silence, even while queued.
    key = source['kind'] + ':' + source['id']
    try:
        value = resolve(key)
        path = value.get(kind) if isinstance(value, dict) else None
        if path is None or not Path(path).is_file():
            raise ExportError('source_missing')
        return Path(path)
    except (FileNotFoundError, KeyError):
        raise ExportError('source_missing') from None


class _VideoReader:
    """Two decoded frames, chosen by their timestamps, with backward keyframe seek."""
    def __init__(self, path, first, width, height, cancelled, clip=None):
        import av
        self.clip = clip or {}
        self.container = None
        try:
            self.container = av.open(str(path))
            self.stream = self.container.streams.video[0]
            self.stream.thread_count = 1
            self.origin = Fraction(self.stream.start_time or 0) * self.stream.time_base
            self.rate = self.stream.average_rate or self.stream.guessed_rate or FPS
            desired = Fraction(first, FPS)
            if desired > 0:
                self.container.seek(int((self.origin + desired) / self.stream.time_base),
                                    stream=self.stream, backward=True, any_frame=False)
            self.decoder = iter(self.container.decode(self.stream))
            self.index = 0
            self.cancelled = cancelled
            self.width, self.height = width, height
            self.previous = None
            self.following = self._next()
            self.cached_frame = self.cached_pixels = None
            if self.following is None:
                raise ExportError('unreadable')
        except BaseException as error:
            self.close()
            if isinstance(error, (ExportCancelled, ExportError)):
                raise
            raise ExportError('unreadable') from None

    def _next(self):
        _check(self.cancelled)
        frame = next(self.decoder, None)
        if frame is None:
            return None
        stamp = (Fraction(frame.pts) * frame.time_base - self.origin if frame.pts is not None
                 else Fraction(self.index, 1) / self.rate)
        self.index += 1
        return stamp, frame

    def pixels(self, number):
        import numpy as np
        try:
            desired = Fraction(number, FPS)
            while self.following is not None and frame_not_after(self.following[0], number):
                self.previous, self.following = self.following, self._next()
            chosen = self.previous
            if chosen is None or (self.following is None and desired >= last_frame_end(chosen[0], chosen[1], self.rate)):
                raise ExportError('unreadable')
            frame = chosen[1]
            if frame is not self.cached_frame:
                picture = display_frame(frame)
                from .editor_picture import place, plan
                # Anything but the default placement uses the shared routine: crop, flip,
                # rotate, fit, scale, offset, and a blurred fill where the picture leaves room.
                if not plan(self.clip, picture.width, picture.height, self.width, self.height)['legacy']:
                    self.cached_pixels = place(picture.to_ndarray(format='rgb24'), self.clip, self.width, self.height)
                    self.cached_frame = frame
            if frame is not self.cached_frame:
                ratio = max(self.width / picture.width, self.height / picture.height)
                w, h = max(1, round(picture.width * ratio)), max(1, round(picture.height * ratio))
                image = picture.reformat(width=w, height=h, format='rgb24').to_ndarray()
                if (w, h) == (self.width, self.height):
                    self.cached_pixels = image
                else:
                    # Odd surplus is cropped more on right/bottom (legacy cover).
                    x, y = (w - self.width) // 2, (h - self.height) // 2
                    self.cached_pixels = image[y:y + self.height, x:x + self.width]
                self.cached_frame = frame
            return self.cached_pixels
        except (ExportError, ExportCancelled):
            raise
        except Exception:
            raise ExportError('unreadable') from None

    def close(self):
        if self.container is not None:
            self.container.close()
            self.container = None


class _AudioReader:
    """One resampled packet at a time; timestamps preserve offsets and seek alignment."""
    def __init__(self, path, first_sample, cancelled, *, rate=SAMPLE_RATE):
        import av
        self.container = None
        try:
            self.container = av.open(str(path))
            self.stream = self.container.streams.audio[0]
            self.stream.thread_count = 1
            origin_stream = next(iter(self.container.streams.video), self.stream)
            self.origin = Fraction(origin_stream.start_time or 0) * origin_stream.time_base
            self.mono = len(self.stream.layout.channels) == 1
            self.rate = rate
            self.resampler = av.AudioResampler(format='fltp', layout='mono' if self.mono else 'stereo',
                                              rate=rate)
            if first_sample > rate:
                stamp = self.origin + Fraction(first_sample, rate) - Fraction(1, 10)
                if self.stream.duration is not None:
                    # A legal picture range may extend past the sound's end.
                    # Seek into its last packet, then let samples() pad with zero.
                    end = Fraction((self.stream.start_time or 0) + self.stream.duration) * self.stream.time_base
                    stamp = min(stamp, max(self.origin, end - Fraction(1, 10)))
                self.container.seek(int(stamp / self.stream.time_base), stream=self.stream,
                                    backward=True, any_frame=False)
            self.cancelled = cancelled
            self.chunks = iter(self._chunks())
            self.chunk = next(self.chunks, None)
            if self.chunk is None:
                raise ExportError('unreadable')
        except BaseException as error:
            self.close()
            if isinstance(error, (ExportCancelled, ExportError)):
                raise
            raise ExportError('unreadable') from None

    def _chunks(self):
        import numpy as np
        fallback = 0
        for frame in self.container.decode(self.stream):
            _check(self.cancelled)
            for item in self.resampler.resample(frame):
                stamp = (round((Fraction(item.pts) * item.time_base - self.origin) * self.rate)
                         if item.pts is not None else fallback)
                data = item.to_ndarray()
                if self.mono:
                    data = np.repeat(data, 2, axis=0)
                if not np.isfinite(data).all():
                    raise ExportError('unreadable')
                fallback = stamp + item.samples
                yield stamp, data
        for item in self.resampler.resample(None):
            _check(self.cancelled)
            data = item.to_ndarray()
            if self.mono:
                data = np.repeat(data, 2, axis=0)
            yield fallback, data
            fallback += item.samples

    def samples(self, start, count):
        import numpy as np
        result = np.zeros((2, count), dtype=np.float32)
        try:
            while self.chunk is not None:
                stamp, data = self.chunk
                end = stamp + data.shape[1]
                if stamp >= start + count:
                    break
                lo, hi = max(start, stamp), min(start + count, end)
                if hi > lo:
                    result[:, lo - start:hi - start] = data[:, lo - stamp:hi - stamp]
                if end > start + count:
                    break
                self.chunk = next(self.chunks, None)
            return result
        except (ExportError, ExportCancelled, _SeamRepairError):
            raise
        except Exception:
            raise ExportError('unreadable') from None

    def close(self):
        if self.container is not None:
            self.container.close()
            self.container = None
        # _chunks() captures self; clear the generator to break that cycle at
        # every cut instead of retaining retired packets until cyclic GC runs.
        self.chunk = self.chunks = None


def _audio_start(clip):
    splice = clip.get('splice')
    seconds = (Fraction(splice['audio_samples'], SPLICE_RATE) +
               Fraction(clip['in'] - splice['frames'], FPS) if splice else Fraction(clip['in'], FPS))
    return round(seconds * SAMPLE_RATE)


class _CrossfadeReads:
    """Separate bounded pre-read buffers for junctions touching the current block."""
    def __init__(self, track, resolve, cancelled):
        self.track, self.resolve, self.cancelled = track, resolve, cancelled
        self.plans = crossfade_reads(track)
        self.clips = {clip['id']: clip for clip in track['clips']}
        self.cache = {}
        self.incoming = {plan['right'] for plan in self.plans}
        self.outgoing = {plan['left'] for plan in self.plans}

    def extras(self, start, count):
        current = {plan['right']: plan for plan in self.plans
                   if plan['boundary'] - plan['pre'] < start + count
                   and plan['boundary'] + plan['post'] > start}
        self.cache = {key: value for key, value in self.cache.items() if key in current}
        for key, plan in current.items():
            if key in self.cache:
                continue
            extra = {}
            for side, clip_id, position in (('left_tail', plan['left'], plan['left_read_start']),
                                             ('right_head', plan['right'], plan['right_read_start'])):
                _check(self.cancelled)
                size = min(plan[side], 5 * SAMPLE_RATE)
                if not size:
                    continue
                path = _media(self.resolve, self.clips[clip_id], 'audio')
                reader = _AudioReader(path, position, self.cancelled)
                try:
                    extra[side] = reader.samples(position, size)
                finally:
                    reader.close()
            self.cache[key] = extra
        return self.cache



def _audio_frame(samples, **kwargs):
    import av
    return av.AudioFrame.from_ndarray(samples, **kwargs)


def _native_audio_length(path, cancelled=None):
    """Only generated stereo 32 kHz media with an exact duration can join."""
    import av
    with av.open(str(path)) as source:
        if len(source.streams.audio) != 1:
            return None
        stream = source.streams.audio[0]
        origin = next(iter(source.streams.video), stream)
        if (stream.rate != SPLICE_RATE or stream.layout.name != 'stereo'
                or stream.duration is None
                or Fraction(stream.start_time or 0) * stream.time_base !=
                   Fraction(origin.start_time or 0) * origin.time_base):
            return None
        count = Fraction(stream.duration) * stream.time_base * SPLICE_RATE
        if count.denominator != 1 or count <= 0:
            return None
        # MP4 edit lists can make the first decoded frame appear at zero while
        # a later frame still starts late. Verify the whole native clock with
        # one decoded packet in memory, including availability of the tail.
        expected = Fraction(origin.start_time or 0) * origin.time_base
        heard = 0
        for frame in source.decode(stream):
            _check(cancelled)
            if (not heard and stream.codec_context.name == 'aac'
                    and frame.samples != (stream.codec_context.frame_size or 1024)):
                return None  # a leading edit/offset hidden by an MP4 edit list
            if (frame.pts is None or frame.time_base is None
                    or Fraction(frame.pts) * frame.time_base != expected):
                return None
            heard += frame.samples
            expected += Fraction(frame.samples, SPLICE_RATE)
        return int(count) if heard >= count else None


class _LeadStore:
    """Snapshot accepted seam data on disk, retaining one small window in RAM.

    The continuation retention task may remove records while a long export is
    running. Never silently switch a planned chain to a different audio clock.
    """
    def __init__(self, directory):
        self.file = tempfile.TemporaryFile(dir=directory)

    def save(self, lead):
        offset = self.file.seek(0, 2)
        self.file.write(lead['samples'].tobytes(order='C'))
        return offset

    def load(self, offset):
        import numpy as np
        self.file.seek(offset)
        data = self.file.read(12240 * 2 * 4)
        return dict(samples=np.frombuffer(data, dtype=np.float32).reshape(12240, 2),
                    before=12000, fade=240, sample_rate=SPLICE_RATE)

    def close(self):
        self.file.close()


def _audio_chains(clips, resolve, cancelled, lead_store=None, joinable=None, *, save_lead=None):
    """Plan eligible runs; keep paths/counts, never all the lead-in tensors."""
    from av import FFmpegError
    chains, lengths = {}, {}

    def part(clip):
        path = _media(resolve, clip, 'audio')
        if path is None:
            return None
        if path not in lengths:
            try:
                lengths[path] = _native_audio_length(path, cancelled)
            except FFmpegError:
                # Full-source preflight may encounter damage beyond the kept
                # range. Let the ordinary reader validate only exported audio.
                lengths[path] = None
        length = lengths[path]
        first_output = _audio_start(clip)
        desired = Fraction(first_output, SAMPLE_RATE) * SPLICE_RATE
        first = round(desired)
        if length is None or first < 0:
            return None
        # 32 -> 48 kHz has a two-native-sample phase cycle. Frame trims
        # need not land on a native sample. Decode a little earlier on that
        # grid and drop the exact 48 kHz prefix after rate conversion.
        first = max(0, (int(desired) // 2) * 2 - 64) if first_output else 0
        skip_output = first_output - first * SAMPLE_RATE // SPLICE_RATE
        count = length - first
        if clip['in'] + clip['frames'] < clip['length']:
            count = min(count, math.ceil(Fraction(clip['frames'] * SAMPLES_PER_FRAME + skip_output,
                                                 SAMPLE_RATE) * SPLICE_RATE))
        return dict(clip=clip, path=path, first=first, count=count, skip_output=skip_output,
                    usable=count - math.ceil(Fraction(skip_output, SAMPLE_RATE) * SPLICE_RATE))

    run = []
    for previous, current in zip(clips, clips[1:]):
        _check(cancelled)
        if (previous['start'] + previous['frames'] != current['start']
                or previous['in'] + previous['frames'] != previous['length']
                or current['in'] != 0 or _audio_start(current) != 0
                or previous.get('volume', 1) != 1 or current.get('volume', 1) != 1
                or not previous.get('source') or not current.get('source')
                or joinable is not None and not joinable(previous, current)):
            run = []
            continue
        left_path = _media(resolve, previous, 'audio')
        right_path = _media(resolve, current, 'audio')
        lead = audio_lead_in(right_path, previous=left_path)
        if lead is None:
            run = []
            continue
        left, right = part(previous), part(current)
        if (left is None or right is None
                or left['usable'] < lead['before'] + (lead['fade'] if run else 0)
                or right['usable'] < lead['fade']):
            run = []
            continue
        if not run:
            run = [left]
            chains[previous['id']] = run
        if save_lead is not None or lead_store is not None:
            run[-1]['lead_offset'] = (save_lead or lead_store.save)(lead)
        run.append(right)
        chains[current['id']] = run
    return chains


class _JoinedAudioReader(_AudioReader):
    """Bounded native seam windows, then one resampler on a continuous clock."""
    def __init__(self, parts, cancelled, lead_store=None):
        self.parts, self.cancelled = parts, cancelled
        self.lead_store = lead_store
        self.container = None
        self.chunks = iter(self._joined_chunks())
        self.chunk = None
        try:
            self.chunk = next(self.chunks, None)
            if self.chunk is None:
                raise _SeamRepairError()
        except BaseException:
            self.close()
            raise

    def samples(self, start, count):
        # Whatever goes wrong while reading joined sound, the ordinary export is tried instead.
        return _seam_call(super().samples, start, count)

    def _native_chunks(self):
        import numpy as np
        reader = None
        try:
            reader = _AudioReader(self.parts[0]['path'], self.parts[0]['first'], self.cancelled,
                                  rate=SPLICE_RATE)
            consumed = 0
            for index, part in enumerate(self.parts):
                _check(self.cancelled)
                next_part = self.parts[index + 1] if index + 1 < len(self.parts) else None
                lead = (self.lead_store.load(part['lead_offset']) if self.lead_store is not None
                        else audio_lead_in(next_part['path'], previous=part['path'])) if next_part else None
                before = lead['before'] if lead is not None else 0
                # Leave just the replacement window undecoded until the next
                # block's head is ready. Prefix storage is independent of length.
                stop = part['count'] - before
                while consumed < stop:
                    count = min(8192, stop - consumed)
                    yield reader.samples(part['first'] + consumed, count)
                    consumed += count
                tail = reader.samples(part['first'] + consumed, before) if before else None
                reader.close()
                if next_part is None:
                    break
                reader = _AudioReader(next_part['path'], next_part['first'], self.cancelled,
                                      rate=SPLICE_RATE)
                consumed = 0
                if lead is not None:
                    head = reader.samples(next_part['first'], lead['fade'])
                    previous_samples = np.ascontiguousarray(tail.T)
                    samples = np.ascontiguousarray(head.T)
                    apply_audio_lead_in(previous_samples, samples, lead)
                    yield previous_samples.T.copy()
                    yield samples.T.copy()
                    consumed = lead['fade']
        finally:
            if reader is not None:
                reader.close()

    @classmethod
    def native_chunks(cls, parts, cancelled=None, lead_store=None):
        """The export's exact native samples, also used by editor playback."""
        reader = cls.__new__(cls)
        reader.parts, reader.cancelled, reader.lead_store = parts, cancelled, lead_store
        return reader._native_chunks()

    def _joined_chunks(self):
        import av
        native_position, output_position = 0, -self.parts[0].get('skip_output', 0)
        chunks = self._native_chunks()
        try:
            resampler = av.AudioResampler(format='fltp', layout='stereo', rate=SAMPLE_RATE)
            for data in chunks:
                _check(self.cancelled)
                frame = _audio_frame(data, format='fltp', layout='stereo')
                frame.sample_rate = SPLICE_RATE
                frame.pts, frame.time_base = native_position, Fraction(1, SPLICE_RATE)
                native_position += data.shape[1]
                for item in resampler.resample(frame):
                    yield output_position, item.to_ndarray()
                    output_position += item.samples
            for item in resampler.resample(None):
                _check(self.cancelled)
                yield output_position, item.to_ndarray()
                output_position += item.samples
        except ExportCancelled:
            raise
        except Exception as error:
            # Mark failures before _AudioReader.samples can classify them as
            # unreadable media. Only the joined repair/resampling path is here.
            raise _SeamRepairError() from error
        finally:
            chunks.close()

    def close(self):
        if self.chunks is not None:
            self.chunks.close()
        self.chunk = self.chunks = None


def _checked_target(target, root=None):
    from .comfy_editor import _safe
    target = Path(target).absolute()
    root = Path(root).absolute() if root is not None else Path(target.anchor)
    try:
        return _safe(root, target.relative_to(root).as_posix())
    except (ValueError, OSError):
        raise ExportError('failed') from None


def render(project: dict, resolve, target: Path, *, progress=None, cancelled=None, options=None):
    """Render exactly the saved timeline, publishing atomically after encoder flush.

    Resolve returns local Paths, never HTTP URLs. Memory contains at most one
    video decoder, at most two native audio decoders per audio track, small
    seam windows, one separate extra reader at a time and the current output
    frame. Reverse RGB windows and crossfade buffers are bounded; duration
    changes disk/time usage, not resident media storage.
    Raises ExportError(reason) or ExportCancelled and removes owned partials.
    Existing destinations are refused and never removed.
    """
    try:
        return _render_once(project, resolve, target, progress=progress, cancelled=cancelled,
                            options=options, repair_seams=True)
    except _SeamRepairError:
        # _render_once has closed every reader/snapshot and removed its partial.
        # Cancellation wins even when it arrives while a seam operation fails.
        _check(cancelled)
    return _render_once(project, resolve, target, progress=progress, cancelled=cancelled,
                        options=options, repair_seams=False)


def _render_once(project, resolve, target, *, progress, cancelled, options, repair_seams):
    import av
    import numpy as np
    target = Path(target)
    temporary = target.with_name('.editor-export-' + uuid.uuid4().hex + '.mp4')
    published = False
    complete = False
    try:
        width, height, total, tracks = _layout(project)
        settings = editor_export_options.effective(options, width, height, total)
        peak = project.get('master_peak_db')
        if peak is not None and (type(peak) not in (int, float) or not math.isfinite(peak) or not -12 <= peak <= 0):
            raise ExportError()
        width, height, total = settings.width, settings.height, settings.frames
        if settings.only == 'sound':
            tracks = {key: clips for key, clips in tracks.items() if key != 'v1'}
        else:
            check_export_text(_without_subtitles(project, settings))
        _check(cancelled)
        _checked_target(target)
        _checked_target(temporary)
        if target.exists() or target.is_symlink():
            raise ExportError()
        target.parent.mkdir(parents=True, exist_ok=True)
        _checked_target(target)
        if progress:
            progress(0.)
        # Resolve each reference once, without opening all sources/decoders.
        resolved = {}
        for kind, clips in tracks.items():
            for clip in clips:
                _check(cancelled)
                if clip.get('source'):
                    source = clip['source']
                    key = source['kind'] + ':' + source['id']
                    if key not in resolved:
                        try:
                            resolved[key] = resolve(key)
                        except (FileNotFoundError, KeyError):
                            raise ExportError('source_missing') from None
                    _media(resolved.__getitem__, clip, 'video' if kind == 'v1' else 'audio')
        audio_tracks = {track['id']: track for track in project['tracks'] if track['kind'] == 'audio'}
        envelopes = {key: _CrossfadeReads(track, resolved.__getitem__, cancelled)
                     for key, track in audio_tracks.items()}
        # Lower during speech: the same cached spans the preview reads.
        keys = duck_sources(project['tracks'])
        ducking = duck_ranges(project['tracks'], _speech_spans(
            keys, lambda key: (resolved[key] if key in resolved else resolve(key)).get('audio'), cancelled)) if keys else []

        def joinable(track):
            # A continuation joins its sound only where nothing else shapes that seam:
            # no speed or reverse, no fade or crossfade there, and both parts audible.
            row, reads = audio_tracks[track], envelopes[track]
            return lambda left, right: not (
                audio_changed(left) or audio_changed(right) or left.get('fade_out') or right.get('fade_in')
                or left['id'] in reads.outgoing or right['id'] in reads.incoming
                or not clip_audible(left, row, project['tracks']) or not clip_audible(right, row, project['tracks']))
        with ExitStack() as readers, av.open(str(temporary), 'w', format=settings.container,
                                            options=settings.container_options) as output:
            lead_store = None

            def save_lead(lead):
                nonlocal lead_store
                if lead_store is None:
                    lead_store = _LeadStore(target.parent)
                    readers.callback(lead_store.close)
                return lead_store.save(lead)

            chains = {track: _seam_call(_audio_chains, clips, resolved.__getitem__, cancelled,
                                       joinable=joinable(track), save_lead=save_lead) if repair_seams else {}
                      for track, clips in tracks.items() if track != 'v1'}
            video = None
            if settings.only != 'sound':
                video = output.add_stream('libx264', rate=FPS)
                video.width, video.height, video.pix_fmt = width, height, 'yuv420p'
                # High preserves the original CRF 23 / medium / yuv420p encode.
                video.options = settings.video_options
                video.codec_context.thread_count = 2
            audio = output.add_stream(settings.audio_codec, rate=SAMPLE_RATE)
            audio.layout = 'stereo'
            audio.codec_context.time_base = Fraction(1, SAMPLE_RATE)
            audio.codec_context.thread_count = 1
            indices = {track: 0 for track in tracks}
            active, decoders, decoder_keys = {}, {}, {}
            readers.callback(lambda: [decoder.close() for decoder in decoders.values()])
            black = np.zeros((height, width, 3), dtype=np.uint8) if video is not None else None
            written = 0

            def encode_sound(block, number):
                samples, sample_format = editor_export_options.sound_samples(block, settings)
                sound = _audio_frame(samples, format=sample_format, layout='stereo')
                sound.sample_rate = SAMPLE_RATE
                sound.pts, sound.time_base = (number - settings.start) * SAMPLES_PER_FRAME, Fraction(1, SAMPLE_RATE)
                for packet in audio.encode(sound):
                    output.mux(packet)
                if video is None and progress:
                    progress((number - settings.start + 1) / total * .99)

            # With a master ceiling the mix goes through a true-peak limiter, whose output lags its input;
            # its frames are encoded by their own timeline position.
            limiter = Limiter(peak, SAMPLE_RATE) if peak is not None else None
            limited, limited_frame = np.zeros((2, 0)), None

            def drain(final):
                nonlocal limited, limited_frame
                while limited.shape[1] >= SAMPLES_PER_FRAME or (final and limited.shape[1]):
                    block = np.clip(limited[:, :SAMPLES_PER_FRAME], -1., 1.).astype(np.float32)
                    limited = limited[:, SAMPLES_PER_FRAME:]
                    if limited_frame >= settings.start:
                        encode_sound(block, limited_frame)
                    limited_frame += 1
            lead_start = editor_export_options.audio_lead_start(settings.start, tracks)
            limited_frame = lead_start
            for number in range(lead_start, settings.start + total):
                _check(cancelled)
                for track, clips in editor_export_options.frame_tracks(tracks, settings.start, number):
                    index = indices[track]
                    while index < len(clips) and number >= clips[index]['start'] + clips[index]['frames']:
                        index += 1
                    indices[track] = index
                    clip = clips[index] if index < len(clips) and clips[index]['start'] <= number else None
                    if clip is not active.get(track):
                        chain = chains.get(track, {}).get(clip['id']) if clip is not None else None
                        key = chain if chain is not None else clip
                        changed = key is not decoder_keys.get(track)
                        if changed and decoders.get(track):
                            decoders.pop(track).close()
                        active[track] = clip
                        decoder_keys[track] = key
                        if changed and clip is not None and (track == 'v1' or clip_audible(clip, audio_tracks[track], project['tracks'])):
                            path = _media(resolved.__getitem__, clip, 'video' if track == 'v1' else 'audio')
                            if path is not None:
                                decoder = (VideoReader(path, clip, width, height, cancelled) if track == 'v1' and speed_changed(clip)
                                           else _VideoReader(path, clip['in'], width, height, cancelled, clip) if track == 'v1'
                                           else AudioReader(path, clip, cancelled) if audio_changed(clip)
                                           else _JoinedAudioReader(chain, cancelled, lead_store) if chain is not None
                                           else _AudioReader(path, _audio_start(clip), cancelled))
                                decoders[track] = decoder
                if video is not None and number >= settings.start:
                    clip = active.get('v1')
                    pixels = (decoders['v1'].pixels(clip['in'] + number - clip['start'])
                              if clip is not None and 'v1' in decoders else black)
                    # Text is placed on the timeline frame; its layout is proportional to the output size.
                    pixels = paint_project(pixels, _without_subtitles(project, settings), number, width, height)
                    frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
                    frame.pts, frame.time_base = number - settings.start, Fraction(1, FPS)
                    for packet in video.encode(frame):
                        output.mux(packet)
                        written += 1
                        if progress:
                            progress(written / total * .99)
                mixed = np.zeros((2, SAMPLES_PER_FRAME), dtype=np.float32)
                for track in ('a1', 'a2', 'a3', 'a4', 'a5', 'a6'):
                    clip = active.get(track)
                    if track not in audio_tracks:
                        continue
                    track_block = np.zeros_like(mixed)
                    if clip is not None and track in decoders:
                        local = (number - clip['start']) * SAMPLES_PER_FRAME
                        chain = chains[track].get(clip['id'])
                        start = ((number - chain[0]['clip']['start']) * SAMPLES_PER_FRAME if chain is not None
                                 else _audio_start(clip) + local)
                        samples = decoders[track].samples(start, SAMPLES_PER_FRAME)
                        positions = local + np.arange(SAMPLES_PER_FRAME)
                        clips = tracks[track]; index = indices[track]
                        previous = clips[index - 1] if index else None
                        following = clips[index + 1] if index + 1 < len(clips) else None
                        gain = np.ones(SAMPLES_PER_FRAME, dtype=np.float32)
                        if (not clip.get('fade_in') and clip['id'] not in envelopes[track].incoming
                                and (independent_join(previous, clip) or previous is None or not previous.get('source')
                                     or previous.get('volume', 1) == 0
                                     or not clip_audible(previous, audio_tracks[track], project['tracks'])
                                     or previous['start'] + previous['frames'] != clip['start'])):
                            gain *= np.clip(positions / (FADE_SAMPLES - 1), 0, 1).astype(np.float32)
                        if (not clip.get('fade_out') and clip['id'] not in envelopes[track].outgoing
                                and (independent_join(clip, following) or following is None or not following.get('source')
                                     or following.get('volume', 1) == 0
                                     or not clip_audible(following, audio_tracks[track], project['tracks'])
                                     or following['start'] != clip['start'] + clip['frames'])):
                            gain *= np.clip((clip['frames'] * SAMPLES_PER_FRAME - 1 - positions) / (FADE_SAMPLES - 1), 0, 1).astype(np.float32)
                        if clip.get('duck') and ducking:
                            gain *= duck_gain(ducking, number * SAMPLES_PER_FRAME, SAMPLES_PER_FRAME).astype(np.float32)
                        track_block = samples * gain[None, :] * clip.get('volume', 1)
                    extras = (envelopes[track].extras(number * SAMPLES_PER_FRAME, SAMPLES_PER_FRAME)
                              if track_audible(audio_tracks[track], project['tracks']) else None)
                    mixed += apply_track_audio(track_block, number * SAMPLES_PER_FRAME, audio_tracks[track],
                                               project['tracks'], extras=extras)
                    del extras
                if limiter is not None:
                    limited = np.concatenate([limited, limiter.push(mixed)], axis=1)
                    drain(False)
                    continue
                np.clip(mixed, -1., 1., out=mixed)
                if number < settings.start:
                    continue
                encode_sound(mixed, number)
            if limiter is not None:
                limited = np.concatenate([limited, limiter.flush()], axis=1)
                drain(True)
            _check(cancelled)
            for stream in (video, audio):
                if stream is None:
                    continue
                for packet in stream.encode(None):
                    _check(cancelled)
                    output.mux(packet)
                    if stream is video:
                        written += 1
                        if progress:
                            progress(written / total * .99)
        _check(cancelled)
        _checked_target(temporary)
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        _check(cancelled)
        _checked_target(target)
        temporary.replace(target)
        published = True
        _check(cancelled)
        if progress:
            progress(1.)
        _check(cancelled)
        complete = True
        return target
    except (ExportError, ExportCancelled, _SeamRepairError):
        raise
    except Exception as error:
        raise ExportError(_reason(error)) from None
    finally:
        temporary.unlink(missing_ok=True)
        # A cancellation in the final publish window still owns this target.
        if published and (not complete or (cancelled and cancelled())):
            target.unlink(missing_ok=True)


def _speech_spans(keys, audio, cancelled):
    from .editor_music import SpeechSpans
    from .speech import SpeechError
    try:
        return SpeechSpans(_output_root(), resolve=audio).spans(keys, cancelled)
    except SpeechError:
        raise ExportCancelled() from None


def _output_root():
    import folder_paths
    return Path(folder_paths.get_output_directory()).resolve()


def _read_json(path, limit=_RECORD_BYTES):
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Record too large')
    return json.loads(value)


def _contained(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Invalid source')
    return path


def _editor_service():
    try:
        return importlib.import_module('.comfy_editor', __package__)
    except ModuleNotFoundError as error:
        if error.name != __package__ + '.comfy_editor':
            raise
        return None


def load_project(identity, revision=None):
    """Persistence seam: a saved record snapshot; no unsaved browser state."""
    if not isinstance(identity, str) or not _PROJECT_ID.fullmatch(identity):
        raise ValueError('Invalid project')
    service = _editor_service()
    output = _output_root()
    if service is not None:
        # Read under the persistence lock, without decoding/probing the source
        # catalogue that load() adds for the browser.
        with service._STATE:
            record = service._read_project(output, identity)
            if revision is not None and record['revision'] != revision:
                raise ExportConflict(record['revision'])
            return copy.deepcopy(record)
    root = output / 'FreeVideo' / '.editor' / 'projects'
    record = _read_json(_contained(root, root / identity / 'project.json'), PROJECT_BYTES)
    if revision is not None and record['revision'] != revision:
        raise ExportConflict(record['revision'])
    return record


def resolve_source(key):
    """Source seam. Local paths only; browser media URLs never reach render()."""
    from .comfy_library import _report, _video
    if not isinstance(key, str) or ':' not in key:
        raise ValueError('Invalid source')
    kind, identity = key.split(':', 1)
    root = _output_root()
    if kind == 'work':
        path = _video(root, identity)
        _report(path)
    elif kind == 'generated':
        relative = Path(identity)
        if relative.is_absolute() or '\\' in identity or '..' in relative.parts:
            raise ValueError('Invalid source')
        service = _editor_service()
        path = service.media_path(root, key) if service is not None else _contained(root, root / relative)
    elif kind == 'import' and _IMPORT_ID.fullmatch(identity):
        service = _editor_service()
        if service is not None:
            # The preview is readiness-checked by the server. Render the retained
            # original so the preview's 1920 px/32 kHz limits do not reduce export.
            preview = service.media_path(root, key)
            original = service._import_path(root, identity, 'source.bin')
            path = original if original.is_file() else preview
            if path == preview:
                logging.getLogger(__name__).warning('Editor export: original missing; using import preview (%s).', identity)
            metadata = service._read_import(root, identity)
            return dict(video=path if metadata['kind'] == 'video' else None,
                        audio=path if metadata['kind'] == 'audio' or metadata.get('has_audio') else None)
        folder = _contained(root / 'FreeVideo' / '.editor' / 'imports',
                            root / 'FreeVideo' / '.editor' / 'imports' / identity)
        metadata = _read_json(folder / 'import.json')
        if metadata.get('state') != 'ready' or metadata.get('kind') not in ('video', 'audio'):
            raise FileNotFoundError()
        original = _contained(folder, folder / 'source.bin')
        preview = _contained(folder, folder / ('preview.mp4' if metadata['kind'] == 'video' else 'preview.m4a'))
        path = original if original.is_file() else preview
        if path == preview:
            logging.getLogger(__name__).warning('Editor export: original missing; using import preview (%s).', identity)
        return dict(video=path if metadata['kind'] == 'video' else None,
                    audio=path if metadata['kind'] == 'audio' or metadata.get('has_audio') else None)
    else:
        raise ValueError('Invalid source')
    if not path.is_file():
        raise FileNotFoundError()
    # A linked audio clip uses this same video file. The decoder checks the
    # requested stream exists, so a broken source cannot silently turn mute.
    return dict(video=path, audio=path)


class FrameError(ValueError):
    """Why one frame cannot be saved as a picture: no_picture (nothing is shown there), generating (its part is not made yet) or range."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def render_frame(project, resolve, number, *, cancelled=None):
    """One timeline frame as the export draws it (same placement, fit, picture edits and text), without encoding a video."""
    width, height, total, tracks = _layout(project)
    if not _whole(number) or number >= total:
        raise FrameError('range')
    clip = next((c for c in tracks['v1'] if c['start'] <= number < c['start'] + c['frames']), None)
    if clip is None:
        raise FrameError('no_picture')
    path = _media(resolve, clip, 'video')
    if path is None:
        raise FrameError('generating')
    wanted = clip['in'] + number - clip['start']
    # A plain clip seeks straight to the wanted frame; a changed speed or direction keeps the export's own reader.
    reader = (VideoReader(path, clip, width, height, cancelled) if speed_changed(clip)
              else _VideoReader(path, wanted, width, height, cancelled, clip))
    try:
        pixels = reader.pixels(wanted)
        return paint_project(pixels, project, number, width, height)
    finally:
        reader.close()


def _picture_folder():
    """The folder of pictures made here (the image editor's history), created on first use and never a link."""
    import folder_paths
    base = Path(folder_paths.get_input_directory()).resolve()
    folder = base / 'freevideo-image'
    if folder.is_symlink() or folder.resolve().parent != base:
        raise ExportError()
    folder.mkdir(exist_ok=True)
    return folder


def store_picture(pixels):
    """Write a frame as a lossless PNG named like the pictures the image editor lists (image-YYYYMMDD-HHMMSS-NN.png)."""
    from PIL import Image
    from .file_ops import publish
    folder = _picture_folder()
    temporary = folder / ('.frame-' + uuid.uuid4().hex + '.partial')
    try:
        with temporary.open('xb') as stream:
            Image.fromarray(pixels, 'RGB').save(stream, 'PNG')
            stream.flush()
            os.fsync(stream.fileno())
        stamp = time.strftime('image-%Y%m%d-%H%M%S')
        for number in range(1, 100):
            target = folder / ('%s-%02d.png' % (stamp, number))
            try:
                publish(temporary, target)
            except FileExistsError:
                continue
            return dict(file='freevideo-image/' + target.name, name=target.name,
                        width=int(pixels.shape[1]), height=int(pixels.shape[0]))
        raise ExportError()
    finally:
        temporary.unlink(missing_ok=True)


def save_frame(identity, revision, number, *, cancelled=None):
    """Render one frame of a saved project revision and keep it as a picture."""
    project = load_project(identity, revision)
    return store_picture(render_frame(project, resolve_source, number, cancelled=cancelled))


def _save_report(target, project, seconds, *, options=None):
    """Completion marker after media closes; only video.mp4 enters the library."""
    frames = _layout(project)[2]
    report = dict(success=True, geometry=dict(width=project['width'], height=project['height'],
                  frames=frames, fps=FPS, seconds=frames / FPS), request_seconds=seconds,
                  video={}, editor_export=dict(project=project.get('id'), revision=project.get('revision'),
                  name=str(project.get('name') or '')[:200]),
                  encoding=dict(video_codec='h264', pixel_format='yuv420p', crf=23, preset='medium',
                                audio_codec='aac', sample_rate=SAMPLE_RATE, channels=2))
    editor_export_options.report_values(report, editor_export_options.effective(
        options, project['width'], project['height'], frames))
    _checked_target(target)
    path = _checked_target(target.with_suffix('.request.json'))
    temporary = _checked_target(path.with_name(path.name + '.tmp'))
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        _checked_target(path)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ExportQueue:
    """One worker, FIFO, independently cancellable jobs (queued = running/0)."""
    def __init__(self, root, *, loader=None, resolver=None, renderer=None):
        self.root = Path(root).absolute()
        self.loader = loader or load_project
        self.resolver = resolver or resolve_source
        self.renderer = renderer or render
        self.lock = threading.RLock()
        self.jobs = {}
        self.pending = deque()
        self.worker = None

    def _view(self, job):
        # queued: still waiting for its turn; state stays 'running' for older readers.
        return {**{key: job[key] for key in ('id', 'state', 'fraction', 'work', 'reason')},
                'queued': job['state'] == 'running' and job['id'] in self.pending,
                **job['options'].status(), 'path': job.get('path'), 'url': job.get('url'),
                'project_id': job['project'].get('id'), 'revision': job['project'].get('revision')}

    def submit(self, identity, revision=None, *, options=None):
        if not isinstance(identity, str) or not _PROJECT_ID.fullmatch(identity):
            raise ValueError('Invalid project')
        if revision is not None and not _whole(revision):
            raise ValueError('Invalid revision')
        if self.loader is load_project:
            project = load_project(identity, revision)
        else:
            project = copy.deepcopy(self.loader(identity))
            if revision is not None and project['revision'] != revision:
                raise ExportConflict(project['revision'])
        try:
            width, height, frames, _ = _layout(project)
        except ExportError:
            raise ValueError('Project cannot be exported') from None
        settings = editor_export_options.validate({} if options is None else options, width, height, frames)
        if settings.only != 'sound':
            check_export_text(_without_subtitles(project, settings))
        job = dict(id='x-' + uuid.uuid4().hex, state='running', fraction=0., work=None, reason=None,
                   project=project, options=settings, stop=threading.Event())
        with self.lock:
            # Prune completed entries only; never drop queued/running jobs.
            if len(self.jobs) >= 200:
                for old in list(self.jobs):
                    if self.jobs[old]['state'] != 'running':
                        del self.jobs[old]
                        if len(self.jobs) < 200:
                            break
            if len(self.jobs) >= 200:
                raise ExportError()
            self.jobs[job['id']] = job
            self.pending.append(job['id'])
            initial = self._view(job)
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name='freevideo-editor-export', daemon=True)
                self.worker.start()
            return initial

    def get(self, identity):
        if not isinstance(identity, str) or not _EXPORT_ID.fullmatch(identity):
            raise ValueError('Invalid export')
        with self.lock:
            if identity not in self.jobs:
                raise KeyError(identity)
            return self._view(self.jobs[identity])

    def cancel(self, identity):
        with self.lock:
            self.get(identity)
            job = self.jobs[identity]
            if job['state'] == 'running':
                job['stop'].set()
                if identity in self.pending:
                    self.pending.remove(identity)
                    job['state'] = 'cancelled'
            return self._view(job)

    def _run(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.worker = None
                    return
                job = self.jobs[self.pending.popleft()]
                if job['stop'].is_set():
                    job['state'] = 'cancelled'
                    continue
            stamp = datetime.now(timezone.utc)
            work = stamp.strftime('%Y-%m-%d') + '/' + uuid.uuid4().hex
            folder = self.root / 'FreeVideo' / work
            target = folder / job['options'].filename
            started = time.monotonic()

            def progress(fraction):
                with self.lock:
                    job['fraction'] = max(job['fraction'], min(.99, float(fraction)))

            owned = False
            try:
                _checked_target(target, self.root)
                _checked_target(target.with_suffix('.request.json'), self.root)
                owned = True
                self.renderer(job['project'], self.resolver, target, progress=progress,
                              cancelled=job['stop'].is_set, options=job['options'])
                _check(job['stop'].is_set)
                # Prevent cancel from racing the completion marker/publication.
                with self.lock:
                    _check(job['stop'].is_set)
                    _checked_target(target, self.root)
                    _save_report(target, job['project'], time.monotonic() - started, options=job['options'])
                    job.update(state='done', fraction=1., work=work if job['options'].only == 'video' else None,
                               **editor_export_options.result_file(work, job['options']))
            except Exception as error:
                # Only this job's UUID folder is owned by the worker.
                for path in (target, target.with_suffix('.request.json')):
                    try:
                        if owned:
                            _checked_target(path, self.root).unlink(missing_ok=True)
                    except (OSError, ExportError):
                        pass
                try:
                    if owned:
                        _checked_target(folder, self.root).rmdir()
                except (OSError, ExportError):
                    pass
                reason = _reason(error)
                if reason == 'failed' and not isinstance(error, ExportCancelled):
                    # The page only says the export failed; the log keeps why for the diagnostic report.
                    logging.getLogger(__name__).error('Editor export failed.', exc_info=error)
                with self.lock:
                    if isinstance(error, ExportCancelled) or job['stop'].is_set():
                        job.update(state='cancelled', reason=None)
                    else:
                        job.update(state='failed', reason=reason)


def register():
    """Register the export and frame endpoints once on the existing ComfyUI server."""
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_editor_export', False):
        return
    queue = ExportQueue(_output_root())
    server._freevideo_editor_export = queue

    def response(value):
        return web.json_response({'export': value}, headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/editor/export')
    async def start(request):
        try:
            body = await request.json()
            if (not isinstance(body, dict) or not {'id', 'revision'} <= set(body)
                    or set(body) - {'id', 'revision', 'options'} or not _whole(body.get('revision'))
                    or 'options' in body and not isinstance(body['options'], dict)):
                raise ValueError('Invalid request')
            return response(await asyncio.to_thread(queue.submit, body['id'], body['revision'], options=body.get('options')))
        except MissingGlyphs as error:
            return web.json_response({'reason': 'missing_glyphs', 'items': error.items}, status=400)
        except ExportConflict as error:
            return web.json_response({'revision': error.revision}, status=409, headers={'Cache-Control': 'no-store'})
        except FileNotFoundError:
            return web.json_response({'reason': 'source_missing'}, status=404)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'reason': 'failed'}, status=400)
        except Exception as error:
            return web.json_response({'reason': _reason(error)}, status=503)

    @server.routes.get('/freevideo/editor/export')
    async def status(request):
        try:
            return response(queue.get(request.query.get('id')))
        except KeyError:
            return web.json_response({'reason': 'failed'}, status=404)
        except ValueError:
            return web.json_response({'reason': 'failed'}, status=400)

    @server.routes.post('/freevideo/editor/export/cancel')
    async def cancel(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'id'}:
                raise ValueError('Invalid request')
            return response(queue.cancel(body['id']))
        except KeyError:
            return web.json_response({'reason': 'failed'}, status=404)
        except (ValueError, TypeError):
            return web.json_response({'reason': 'failed'}, status=400)

    frame_lock = asyncio.Lock()

    @server.routes.post('/freevideo/editor/frame')
    async def frame(request):
        """Keep one frame of a saved project revision, as the export would draw it, in the pictures made here."""
        try:
            body = await request.json()
            if (not isinstance(body, dict) or set(body) != {'id', 'revision', 'frame'}
                    or not _whole(body['revision']) or not _whole(body['frame'])):
                raise ValueError('Invalid request')
            async with frame_lock:
                value = await asyncio.to_thread(save_frame, body['id'], body['revision'], body['frame'])
            return web.json_response({'picture': value}, headers={'Cache-Control': 'no-store'})
        except FrameError as error:
            return web.json_response({'reason': error.reason}, status=422)
        except ExportConflict as error:
            return web.json_response({'revision': error.revision}, status=409, headers={'Cache-Control': 'no-store'})
        except FileNotFoundError:
            return web.json_response({'reason': 'source_missing'}, status=404)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'reason': 'failed'}, status=400)
        except Exception as error:
            return web.json_response({'reason': _reason(error)}, status=503)
