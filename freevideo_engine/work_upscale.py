"""Upscaling a finished 24 fps video by one 1.5x step.

The video's own frames, encoded by the video VAE, take the place of a first
pass: the learned latent upscale and three light refinement steps follow
(refine_schedule.UPSCALE), and the original sound is copied as is (the
"Use as is" audio path). Torch is imported only where frames are encoded.
"""
import hashlib
import json
from fractions import Fraction
from pathlib import Path

FACTOR = 1.5
NOT_A_WORK = ('Choose a finished FreeVideo video to upscale', '请选择一个已完成的 FreeVideo 视频来放大。')


def size(width, height):
    """The upscaled size, or None: both sides must be multiples of 64 so the larger
    size stays on the 32-pixel grid with the same shape."""
    if any(type(n) is not int or n <= 0 or n % 64 for n in (width, height)):
        return None
    return width * 3 // 2, height * 3 // 2


def work_request(output_directory, relative):
    """A finished, non-preview FreeVideo work in this output folder: (video, saved state)."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(NOT_A_WORK[0])
    output_root = Path(output_directory).resolve()
    video = (output_root / relative).resolve()
    try:
        video.relative_to(output_root / 'FreeVideo')
    except ValueError:
        raise ValueError(NOT_A_WORK[0]) from None
    path = video.parent / 'comfy-request.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        state = {}
    geometry = state.get('geometry') if isinstance(state, dict) else None
    if (not isinstance(state, dict) or state.get('status') != 'complete' or state.get('preview')
            or Path(state.get('output', '')).resolve() != video or not isinstance(geometry, dict)
            or geometry.get('fps') != 24 or size(geometry.get('width'), geometry.get('height')) is None
            or type(state.get('seed')) is not int):
        raise ValueError(NOT_A_WORK[0])
    if not video.is_file():
        raise ValueError(NOT_A_WORK[0])
    return video, state


def _digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def source(path):
    """Inspect a finished MP4: one 24 fps picture stream whose sides are multiples
    of 64, and a sound track (copied as is). Returns its identity and size."""
    import av
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != '.mp4' or not path.is_file():
        raise ValueError(NOT_A_WORK[0])
    try:
        with av.open(str(path)) as container:
            if len(container.streams.video) != 1 or not container.streams.audio:
                raise ValueError(NOT_A_WORK[0])
            stream = container.streams.video[0]
            rate = stream.average_rate or stream.guessed_rate
            width, height = stream.codec_context.width, stream.codec_context.height
            frames = sum(1 for _ in container.demux(stream) if _.size)
    except (av.error.FFmpegError, OSError) as error:
        raise ValueError(NOT_A_WORK[0]) from error
    if rate is None or Fraction(rate) != 24 or size(width, height) is None or frames <= 0:
        raise ValueError(NOT_A_WORK[0])
    return dict(output=str(path), sha256=_digest(path), bytes=path.stat().st_size,
                width=width, height=height, frames=frames, fps=24)


def audio_row(source_info):
    """The video's own sound, used as is and cut where its pictures end: an encoder pads
    the last sound packet, which would otherwise add a block of repeated frames."""
    from .original_audio import inspect
    row = dict(path=source_info['output'], sha256=source_info['sha256'], bytes=source_info['bytes'],
               kind='audio', use='original')
    info = inspect(row)
    end = min(info['end_sample'], source_info['frames'] * info['sample_rate'] // 24)
    if end * 24 < (source_info['frames'] - 1) * info['sample_rate']:
        raise ValueError("The video's sound is shorter than its pictures; it cannot be upscaled")
    return dict(row, range=dict(start_sample=0, end_sample=end))


def media(requested, source_info):
    """The request's media: only the video's own sound (audio_row)."""
    row = audio_row(source_info)
    rows = (requested or {}).get('references') or []
    if (requested or {}).get('loras') or any(value != row for value in rows):
        raise ValueError('An upscale of a finished video takes no other inputs')
    return {'version': 1, 'references': [row], 'loras': []}


def check(source_info, canvas, sampling_plan):
    """The request samples exactly this video's frames at 1.5x its size."""
    first = sampling_plan.get('first') or {}
    from .geometry import geometry
    from .refine_schedule import UPSCALE
    if (sampling_plan.get('refine_schedule') != UPSCALE or sampling_plan.get('scale') != FACTOR
            or (first.get('width'), first.get('height')) != (source_info['width'], source_info['height'])
            or (canvas['width'], canvas['height']) != size(source_info['width'], source_info['height'])
            or canvas.get('requested_frames') != source_info['frames']
            or canvas['frames'] != geometry(frames=source_info['frames'])['frames']):
        raise ValueError('The upscale request does not match the video it upscales')


def _frames(path, width, height, count):
    """All frames as RGB uint8, the last one repeated up to the aligned count."""
    import av
    import numpy as np
    pixels = np.empty((count, height, width, 3), dtype=np.uint8)
    index = 0
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            if index == count:
                break
            image = frame.to_ndarray(format='rgb24')
            if image.shape != (height, width, 3):
                raise ValueError('The video changed size; it cannot be upscaled')
            pixels[index] = image
            index += 1
    if not index:
        raise ValueError(NOT_A_WORK[0])
    pixels[index:] = pixels[index - 1]
    return pixels, index


def first_pass(request, torch, *, base, progress=None):
    """Encode the finished video as the request's first pass on the CPU.

    Returns (video latents, audio latents, first-pass record, receipt). The audio
    latents are the clean "Use as is" track from the request's conditioning, so
    the sound is never resampled by the model.
    """
    import time
    info = request['upscale_source']
    path = Path(info['output'])
    if path.stat().st_size != info['bytes'] or _digest(path) != info['sha256']:
        raise ValueError('The video changed after it was chosen; choose it again to upscale')
    first = request['sampling_plan']['first']
    width, height, frames = first['width'], first['height'], first['frames']
    if (width, height) != (info['width'], info['height']):
        raise ValueError('The upscale request does not match the video it upscales')
    started = time.perf_counter()
    pixels, used = _frames(path, width, height, frames)
    from .vae_weights import load_video_encoder
    from .media_encoding import encode_visual
    vae, _ = load_video_encoder(base)
    try:
        if hasattr(torch, 'cuda') and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        video = encode_visual(vae, pixels, posterior='mode', progress=progress)
        peak = torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0
    finally:
        del vae
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    del pixels
    from .fixed_track import split
    value = torch.load(request['conditioning'], map_location='cpu', weights_only=True)
    _, fixed = split(list(value.get('references') or []), frames, 'cpu')
    del value
    if fixed is None:
        raise ValueError('An upscale copies the video\'s own sound; it was not in the request')
    ticks = fixed.shape[0] // 2
    audio = fixed.reshape(2, ticks, 32).permute(0, 2, 1).contiguous()
    expected = (1, 24, (frames - 5) // 17 * 5 + 2, height // 16, width // 16)
    if tuple(video.shape) != expected or not bool(torch.isfinite(video).all()):
        raise ValueError('The encoded video does not match the planned first pass')
    seconds = time.perf_counter() - started
    record = dict(source='finished_video', step_seconds=[], sample_seconds=seconds, encode_seconds=seconds,
                  frames_read=used, torch_peak_allocated_bytes=peak, torch_peak_reserved_bytes=peak)
    receipt = dict(first_pass_reused=False, first_pass_source='finished_video',
                   first_pass_source_output=info['output'], upscale_source_sha256=info['sha256'])
    return video, audio, record, receipt
