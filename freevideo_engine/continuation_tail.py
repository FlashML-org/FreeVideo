"""A continuation context encoded from the ending of a finished video.

A video made by an earlier version, or one whose file was replaced after it
was made, has no stored context. Its last 22 frames and the sound under them
are read back and encoded instead. The video encoder works on independent
17-frame clips, so those frames (17 + 5) fall on the same clip grid as the
last 7 latent frames of a generation: nothing before them has to be encoded.
The sound encoder looks back, so it is given up to 15 s and only its last
latents are kept.

The record describes the ending as a first block of 39 frames. Only the sound
clock reads that length: the kept sound ends exactly where the picture ends.
"""
import math
from pathlib import Path

from .continuation import (LATENT_FRAMES, MISSING, OVERLAP_FRAMES, SIZE_MISMATCH, audio_origin, validate_frames,
                           validate_path)


NOMINAL_FRAMES = 39
AUDIO_TICKS = round(NOMINAL_FRAMES * 40 / 24) - audio_origin(NOMINAL_FRAMES - OVERLAP_FRAMES)
AUDIO_RATE = 32000
TICK_SAMPLES = 800
AUDIO_WINDOW_TICKS = 600  # 15 s, the longest sound H3 generates
FPS = 24
TOO_SHORT = 'The previous video is too short to continue; it needs to be at least 1.7 s long.'  # NOMINAL_FRAMES is 1.625 s at 24 fps; the message rounds up so a 1.7 s video always fits
UNREADABLE = 'The previous video cannot be read.'
LOW_RATE = "The previous video's frame rate is too low to continue; it needs at least 10 frames per second."


def _fit(image, width, height):
    from PIL import Image
    return image if image.size == (width, height) else image.resize((width, height), Image.Resampling.LANCZOS)


def _picture(frame):
    """The frame as an RGB image.

    The decoder library's own conversion comes out about 1/255 darker than the picture that was saved,
    which would show as a step where the next part begins. An 8-bit 4:2:0 frame of the usual range is
    converted with the exact matrix instead.
    """
    import numpy as np
    from PIL import Image
    if frame.format.name != 'yuv420p' or getattr(frame.color_range, 'value', frame.color_range) not in (0, 1):
        return frame.to_image().convert('RGB')
    y, cb, cr = frame.reformat(format='yuv444p', interpolation='BICUBIC').to_ndarray().astype(np.float32)
    kr, kb = (.2126, .0722) if getattr(frame.colorspace, 'value', frame.colorspace) == 1 else (.299, .114)
    y, cb, cr = (y - 16) * (255 / 219), (cb - 128) * (255 / 224), (cr - 128) * (255 / 224)
    red, blue = y + 2 * (1 - kr) * cr, y + 2 * (1 - kb) * cb
    green = (y - kr * red - kb * blue) / (1 - kr - kb)
    return Image.fromarray(np.clip(np.stack([red, green, blue], -1) + .5, 0, 255).astype(np.uint8))


def _rate(stream):
    rate = float(stream.average_rate or stream.guessed_rate or 0)
    if 0 < rate < 10:
        raise ValueError(LOW_RATE)
    if not 10 <= rate <= 121:
        raise ValueError(UNREADABLE)
    return rate


def _admit(count, rate, width, height, canvas):
    """The length of the video as 24 fps frames, when its ending can be continued on `canvas`."""
    frames = count if abs(rate - FPS) <= .01 else round(count * FPS / rate)
    if frames < NOMINAL_FRAMES:
        raise ValueError(TOO_SHORT)
    # The same shape within one 32-pixel step of the canvas; another size of the same shape is scaled.
    if min(width, height) < 1 or abs(width * canvas['height'] - height * canvas['width']) > 32 * max(width, height):
        raise ValueError(SIZE_MISMATCH)
    return frames


def check(video_path, canvas):
    """Whether the ending of this video can be encoded for `canvas`; a ValueError says why not.

    Reads the file's packets and decodes nothing, so a caller can ask before it queues a run.
    """
    import av
    try:
        with av.open(str(validate_path(video_path))) as container:
            if not container.streams.video:
                raise ValueError(UNREADABLE)
            stream = container.streams.video[0]
            count = sum(1 for packet in container.demux(stream) if packet.size)
            return _admit(count, _rate(stream), stream.width or 0, stream.height or 0, canvas)
    except (av.FFmpegError, OSError) as error:
        raise ValueError(UNREADABLE) from error


def pending(video_path, canvas):
    """A continuation request whose context is encoded from the video when the run starts."""
    validate_frames(canvas['frames'])
    check(video_path, canvas)
    origin = NOMINAL_FRAMES - OVERLAP_FRAMES
    return dict(tail=dict(video=str(Path(video_path).resolve())), overlap_frames=OVERLAP_FRAMES,
                new_frames=canvas['frames'] - OVERLAP_FRAMES, frame_origin=origin,
                audio_origin=audio_origin(origin), segment=2, audio_latents=AUDIO_TICKS)


def read(video_path, canvas, first, directory):
    """The last 22 frames at both pass sizes and the sound under them, as files of this run.

    `first` is the first pass's width and height. A video at another frame rate gives the
    pictures a 24 fps copy of it would end with. Runs without a GPU.
    """
    import av
    import numpy as np
    from collections import deque
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with av.open(str(video_path)) as container:
            if not container.streams.video:
                raise ValueError(UNREADABLE)
            rate = _rate(container.streams.video[0])
            kept, count = deque(maxlen=math.ceil(OVERLAP_FRAMES * rate / FPS) + 2), 0
            for frame in container.decode(video=0):
                kept.append(frame)
                count += 1
            if not count:
                raise ValueError(UNREADABLE)
            frames = _admit(count, rate, kept[-1].width, kept[-1].height, canvas)
            oldest = count - len(kept)
            if abs(rate - FPS) <= .01:
                picked = range(count - OVERLAP_FRAMES, count)
            else:
                picked = [min(count - 1, max(oldest, round((frames - OVERLAP_FRAMES + index) * rate / FPS)))
                          for index in range(OVERLAP_FRAMES)]
            images = [_picture(kept[index - oldest]) for index in picked]
            del kept
        sound = _sound(video_path, count / rate)
    except (av.FFmpegError, OSError) as error:
        raise ValueError(UNREADABLE) from error
    width, height = images[0].size
    result = {'frames': frames, 'source': {'width': width, 'height': height, 'fps': rate}}
    for name, size in (('hi', canvas), ('lo', first)):
        target = directory / ('continuation-tail-%s.npy' % name)
        np.save(target, np.stack([np.asarray(_fit(image, size['width'], size['height'])) for image in images]),
                allow_pickle=False)
        result[name] = str(target)
    target = directory / 'continuation-tail-audio.npy'
    np.save(target, sound, allow_pickle=False)
    result['audio'] = str(target)
    return result


def _sound(video_path, seconds):
    """Up to 15 s of 32 kHz stereo ending where the picture ends; silence where the file has none."""
    import av
    import numpy as np
    end = round(seconds * AUDIO_RATE)
    window = min(AUDIO_WINDOW_TICKS, end // TICK_SAMPLES) * TICK_SAMPLES
    begin = end - window
    data = np.zeros((2, window), dtype=np.float32)
    with av.open(str(video_path)) as container:
        if not container.streams.audio:
            return data
        stream = container.streams.audio[0]
        mono = stream.layout.name == 'mono' or stream.channels == 1
        # A mono track has its channel repeated, as reference sound is; a stereo resampler would lower it by 3 dB.
        resampler = av.AudioResampler(format='fltp', layout='mono' if mono else 'stereo', rate=AUDIO_RATE)
        position = 0
        def place(item):
            nonlocal position
            block = item.to_ndarray()
            start, stop = max(position, begin), min(position + block.shape[1], end)
            if stop > start:
                data[:, start - begin:stop - begin] = block[:, start - position:stop - position]
            position += block.shape[1]
        for frame in container.decode(audio=0):
            for item in resampler.resample(frame):
                place(item)
            if position >= end:
                break
        else:
            for item in resampler.resample(None):
                place(item)
    if not np.isfinite(data).all():
        raise ValueError(UNREADABLE)
    return data


def encode(tail, base, *, device='cuda', before_upload=None, posterior='mode'):
    """Encode the files of `read` into the three tensors of a continuation record."""
    import gc
    import numpy as np
    import torch
    from .media_encoding import encode_visual
    from .vae_weights import load_audio_vae, load_video_encoder
    empty = torch.mps.empty_cache if torch.device(device).type == 'mps' else (
        torch.cuda.empty_cache if torch.device(device).type == 'cuda' else lambda: None)
    result = {}
    vae, _ = load_video_encoder(base, before_upload=None if before_upload is None else
                                lambda model: before_upload(model, False), device=device)
    for name in ('hi', 'lo'):
        pixels = np.load(tail[name], mmap_mode='r', allow_pickle=False)
        latent = encode_visual(vae, pixels, device=device, posterior=posterior)
        if latent.shape[2] != LATENT_FRAMES:
            raise ValueError('Unexpected continuation latent frames')
        result['video_' + name] = latent.numpy()
        del pixels, latent
    del vae
    gc.collect()
    empty()
    vae, _ = load_audio_vae(base, before_upload=None if before_upload is None else
                            lambda model: before_upload(model, True), device=device)
    if vae.config.sampling_rate != AUDIO_RATE:
        raise ValueError('Continuation sound requires the pinned 32 kHz H3 VAE')
    mean = torch.tensor(vae.config.latents_mean).view(1, 1, -1)
    std = torch.tensor(vae.config.latents_std).view(1, 1, -1)
    audio = torch.from_numpy(np.load(tail['audio'], allow_pickle=False)).to(device)
    latent = vae.encode(audio[:, None], return_dict=False)[0].mode().float().cpu().transpose(1, 2)
    latent = ((latent - mean) / std).transpose(1, 2)
    if latent.shape[2] < AUDIO_TICKS:
        raise ValueError('Unexpected continuation sound latents')
    result['audio'] = latent[:, :, -AUDIO_TICKS:].contiguous().numpy()
    del vae, audio, latent
    gc.collect()
    empty()
    return result


def metadata(identity, canvas, first):
    """The record's description: a first block whose last 22 frames are the file's."""
    return dict(identity=identity,
                geometry=dict(width=canvas['width'], height=canvas['height'], frames=NOMINAL_FRAMES),
                first_canvas=dict(width=first['width'], height=first['height']),
                chain=dict(segment=1, frame_origin=0, audio_origin=0), context='encoded')


def freeze(request, canvas, first, directory, base):
    """Keep the ending `pending` asked for as files of this run; the worker encodes them."""
    from .continuation_store import video_id
    directory = Path(directory)
    video = request['tail']['video']
    size = lambda value: {key: value[key] for key in ('width', 'height')}
    tail = read(video, canvas, first, directory)
    return dict(request, tail=dict(tail, video=video, canvas=size(canvas), first=size(first), base=str(base),
                                   source_id=video_id(video),
                                   snapshot=str((directory / 'continuation-encoded.safetensors').resolve())))


CACHE_REVISION = 1  # Raise when encoding changes what the same ending encodes to.
CACHE_KEEP = 8


def _cache_file(tail, manifest, device):
    """Where this ending, encoded for this canvas, these weights and this device, is kept between runs.

    The key holds everything the encoded record depends on: the picture and sound of the file, both
    sampled sizes, the weights, the two encoders' files and the device that ran them.
    """
    import hashlib
    import json
    from .continuation import identity
    from .continuation_store import root
    try:
        base = Path(tail['base'])
        encoders = sorted((folder.name, item.name, item.stat().st_size)
                          for folder in (base / 'vae', base / 'audio_vae') if folder.is_dir()
                          for item in folder.glob('*.safetensors'))
        key = json.dumps([CACHE_REVISION, tail['source_id'], tail['canvas'], tail['first'], identity(manifest),
                          encoders, str(device).split(':')[0]], sort_keys=True)
        return root() / 'encoded' / (hashlib.sha256(key.encode('utf-8')).hexdigest() + '.safetensors')
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _digest(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cache_take(cache, path):
    """Copy a kept record to the run's own snapshot path; False when there is none or it changed on disk."""
    import os
    import shutil
    stamp = cache.with_suffix('.sha256')
    try:
        expected = stamp.read_text(encoding='ascii').strip()
        shutil.copyfile(cache, path)
        if _digest(path) != expected:
            raise OSError('kept record changed')
        os.utime(cache, None)  # Retention follows use.
        return True
    except (OSError, ValueError):
        Path(path).unlink(missing_ok=True)
        _cache_drop(cache)
        return False


def _cache_drop(cache):
    for item in (cache, cache.with_suffix('.sha256')):
        try:
            item.unlink(missing_ok=True)
        except OSError:
            pass


def _cache_keep(cache, path):
    """Keep the run's snapshot for the next run; a full or read-only disk only loses the saving."""
    import os
    import shutil
    import uuid
    temporary = cache.with_name(cache.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, temporary)
        stamp = cache.with_suffix('.sha256')
        stamp.unlink(missing_ok=True)  # A record without its digest is never read.
        os.replace(temporary, cache)
        stamp.write_text(_digest(cache) + '\n', encoding='ascii')
        records = []
        for item in cache.parent.glob('*.safetensors'):
            try:
                records.append((item.stat().st_mtime_ns, item))
            except OSError:
                continue
        for _, item in sorted(records, reverse=True)[CACHE_KEEP:]:
            _cache_drop(item)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def complete(request, canvas, manifest, sampling_plan=None, *, resident=None, device='cuda'):
    """The ordinary snapshot request for a frozen ending, and how long encoding it took.

    The first attempt of a run encodes and writes the record beside its other files; a retry reads it.
    A later run for the same file, canvas and weights reads the copy kept in the data folder instead of
    encoding the ending again; a copy that no longer validates is dropped and encoded afresh.
    """
    import hashlib
    import time
    from .continuation import identity, validate_snapshot
    from .continuation_store import write
    tail = request['tail']
    path = Path(tail['snapshot'])
    if sampling_plan is not None and any(sampling_plan['first'][key] != tail['first'][key] for key in ('width', 'height')):
        raise ValueError(MISSING)

    def snapshot():
        result = dict({key: value for key, value in request.items() if key != 'tail'}, snapshot=str(path),
                      snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), source_id=tail['source_id'])
        return validate_snapshot(result, canvas, manifest, sampling_plan)

    seconds = None
    if not path.is_file():
        started = time.perf_counter()
        cache = _cache_file(tail, manifest, device)
        if cache is not None and _cache_take(cache, path):
            try:
                value = snapshot()
            except ValueError:
                path.unlink(missing_ok=True)
                _cache_drop(cache)
            else:
                path.chmod(0o400)
                return value, dict(seconds=time.perf_counter() - started, reused=True, cache=True)
        started = time.perf_counter()
        data = encode(tail, tail['base'], device=device, before_upload=None if resident is None else
                      lambda model, audio: resident.input_vae_room(model, tail['canvas'], audio=audio, tail=True))
        write(path, dict(data, metadata=dict(metadata(identity(manifest), tail['canvas'], tail['first']),
                                             source_id=tail['source_id'])))
        path.chmod(0o400)
        seconds = time.perf_counter() - started
        value = snapshot()
        if cache is not None:
            _cache_keep(cache, path)
        return value, dict(seconds=seconds, reused=False)
    return snapshot(), dict(seconds=None, reused=True)
