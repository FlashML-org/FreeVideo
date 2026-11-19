"""Validated export settings and encoder helpers, independent of editor state."""
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlencode


@dataclass(frozen=True)
class ExportOptions:
    width: int
    height: int
    start: int
    frames: int
    only: str = 'video'
    quality: str = 'high'
    sound_format: str = 'm4a'
    subtitles: str = 'burn'
    # Above 24 the picture is first rendered at 24 fps (intermediate) and then interpolated.
    fps: int = 24
    intermediate: bool = False

    @property
    def filename(self):
        return 'audio.' + self.sound_format if self.only == 'sound' else 'video.mp4'

    @property
    def container(self):
        return 'wav' if self.only == 'sound' and self.sound_format == 'wav' else 'mp4'

    @property
    def container_options(self):
        return ({} if self.container == 'wav' else
                {'movflags': 'use_metadata_tags+faststart', 'movie_timescale': '48000'})

    @property
    def video_options(self):
        if self.intermediate:
            # Near-lossless and quick: only the interpolation step reads it, then it is removed.
            return {'crf': '10', 'preset': 'veryfast'}
        return {'crf': str({'high': 23, 'standard': 25, 'small': 30}[self.quality]), 'preset': 'medium'}

    @property
    def audio_codec(self):
        return 'pcm_s16le' if self.container == 'wav' else 'aac'

    def status(self):
        return dict(width=self.width, height=self.height, frames=self.frames,
                    only=self.only, quality=self.quality if self.only == 'video' else None,
                    range=dict(start=self.start, frames=self.frames),
                    sound_format=self.sound_format if self.only == 'sound' else None,
                    **({'subtitles': 'none'} if self.only == 'video' and self.subtitles == 'none' else {}),
                    **({'fps': self.fps} if self.only == 'video' and self.fps != 24 else {}))


FRAME_RATES = (24, 48, 60)


def validate(options, width, height, frames):
    """Validate wire values once; the queue passes the immutable result to render."""
    if not isinstance(options, dict) or set(options) - {'short', 'quality', 'range', 'only', 'sound_format', 'subtitles', 'fps'}:
        raise ValueError('Invalid export options')
    if 'short' in options and (type(options['short']) is not int
                              or not 144 <= options['short'] <= 4320 or options['short'] % 2):
        raise ValueError('Invalid export short side')
    for key, allowed in (('quality', ('high', 'standard', 'small')), ('only', ('sound',)),
                         ('sound_format', ('m4a', 'wav')), ('subtitles', ('burn', 'none'))):
        if key in options and (type(options[key]) is not str or options[key] not in allowed):
            raise ValueError('Invalid export ' + key)
    start, count = 0, frames
    if 'range' in options:
        span = options['range']
        if (not isinstance(span, dict) or set(span) != {'start', 'frames'}
                or type(span['start']) is not int or type(span['frames']) is not int
                or span['start'] < 0 or span['frames'] < 1 or span['start'] + span['frames'] > frames):
            raise ValueError('Invalid export range')
        start, count = span['start'], span['frames']
    if 'fps' in options and (type(options['fps']) is not int or options['fps'] not in FRAME_RATES):
        raise ValueError('Invalid export fps')
    if options.get('only') == 'sound':
        return ExportOptions(0, 0, start, count, only='sound', sound_format=options.get('sound_format', 'm4a'))
    output_width, output_height = width, height
    if 'short' in options:
        short = options['short']
        # Nearest even long side, halfway cases up; preserve portrait orientation.
        long = max(2, 2 * ((max(width, height) * short + min(width, height)) // (2 * min(width, height))))
        output_width, output_height = (long, short) if width >= height else (short, long)
    if max(output_width, output_height) > 8192:
        raise ValueError('Export picture is too large')
    return ExportOptions(output_width, output_height, start, count, quality=options.get('quality', 'high'),
                         subtitles=options.get('subtitles', 'burn'), fps=options.get('fps', 24))


def effective(options, width, height, frames):
    return options if isinstance(options, ExportOptions) else validate({} if options is None else options, width, height, frames)


def audio_lead_start(start, tracks):
    """Replay active clips from their original start for identical filter history.

    Crossfade extras use independent readers. Envelopes keep timeline positions;
    only the encoder input is cut. The current base has no continuation chains.
    """
    return min([start] + [clip['start'] for track, clips in tracks.items() if track != 'v1'
                         for clip in clips if clip['start'] <= start < clip['start'] + clip['frames']])


def frame_tracks(tracks, start, number):
    return tracks.items() if number >= start else ((key, clips) for key, clips in tracks.items() if key != 'v1')


def sound_samples(mixed, options):
    if options.container != 'wav':
        return mixed, 'fltp'
    import numpy as np
    samples = np.clip(np.rint(mixed.T * 32768), -32768, 32767).astype(np.int16)
    return np.ascontiguousarray(samples.reshape(1, -1)), 's16'


def report_values(report, options):
    geometry = report['geometry']
    if (options.only == 'video' and options.quality == 'high' and options.start == 0 and options.fps == 24
            and (options.width, options.height, options.frames) ==
                (geometry['width'], geometry['height'], geometry['frames'])):
        return  # Preserve the original default completion marker as well as media.
    report['geometry'].update(width=options.width, height=options.height,
                              frames=options.frames, seconds=options.frames / 24)
    if options.only == 'video' and options.fps != 24:
        # The picture has fps times as many frames per second; the length stays the same.
        report['geometry'].update(fps=options.fps, frames=-(-options.frames * options.fps // 24))
    if options.only == 'sound':
        report.pop('video', None)
        report['audio'] = dict(format=options.sound_format)
        report['encoding'] = dict(audio_codec=options.audio_codec, sample_rate=48000, channels=2)
        if options.container == 'wav':
            report['encoding']['bits_per_sample'] = 16
    else:
        report['encoding']['crf'] = int(options.video_options['crf'])
    report['editor_export'].update(options.status())


def result_file(work, options):
    """Use ComfyUI's existing output media route; audio is not a library video."""
    path = PurePosixPath('FreeVideo') / work / options.filename
    return dict(path=path.as_posix(), url='/view?' + urlencode(
        dict(filename=path.name, subfolder=str(path.parent), type='output')))
