"""Bounded media normalization and native H3 encoder inputs.

Reference video pixels stay uint8 on disk; only the Qwen 2 fps sample and one
VAE input are materialized. Neither ComfyUI nor a whole float RGB movie is kept
in the generation process. A reference clip contributes as much as the video
being generated, at most 15 s, as the official MiniMax H3 pipeline and ComfyUI's
native H3 node truncate references to the generated length. A shortened clip is
reported, never cut silently.
"""
import json
import math
from contextlib import nullcontext
from pathlib import Path


REFERENCE_FRAMES = 360  # 15 s at 24 fps, the longest H3 generation


def reference_frames(canvas):
    """Frames of a reference clip the generation uses: its own length, at most 15 s."""
    frames = canvas.get('frames')
    return min(REFERENCE_FRAMES, int(frames)) if frames else REFERENCE_FRAMES


def clip_seconds(container, stream):
    """A clip's length from its metadata, or None when the file does not say."""
    if stream.duration and stream.time_base:
        return float(stream.duration * stream.time_base)
    return container.duration / 1e6 if container.duration else None


def reference_size(width, height, canvas):
    scale = min(1., math.sqrt(canvas['width'] * canvas['height'] / (width * height)))
    return max(32, round(width * scale / 32) * 32), max(32, round(height * scale / 32) * 32)


def encode_visual(vae, pixels, *, device='cuda', posterior='sample', progress=None):
    """The pinned VAE chunk/padding/posterior recipe with one live pixel chunk.

    Posterior sampling still occurs once on the original device with seed 42;
    sampling separately per chunk would change the conditioning noise.
    `progress(frames_done)` is told after each temporal chunk; it never touches the tensors.
    """
    import numpy as np
    import torch
    from diffusers.models.autoencoders.vae import DiagonalGaussianDistribution
    from src.inference.render import PIXEL_MEAN, PIXEL_STD
    mean = torch.tensor(PIXEL_MEAN, device=device).view(1, -1, 1, 1, 1)
    std = torch.tensor(PIXEL_STD, device=device).view(1, -1, 1, 1, 1)
    clip = vae.config.clip_length
    moments = []
    for begin in range(0, len(pixels), clip):
        current = torch.from_numpy(np.array(pixels[begin:begin + clip])).to(device).permute(3, 0, 1, 2)[None]
        current = (current.float().div(255.) - mean) / std
        if len(pixels) != 1 and current.shape[2] < clip:
            current = torch.cat([current, current[:, :, -1:].repeat(1, 1, clip - current.shape[2], 1, 1)], dim=2)
        moments.append(vae._encode_clip(current).cpu())
        del current
        if progress is not None:
            progress(min(len(pixels), begin + clip))
    moments = torch.cat(moments, dim=2).to(device)
    if len(pixels) > 1 and vae.config.token_drop:
        moments = moments[:, :, :-vae.config.token_drop]
    distribution = DiagonalGaussianDistribution(moments)
    # References keep the pipeline's seeded sample; a continuation context wants the picture itself.
    latent = distribution.mode() if posterior == 'mode' else distribution.sample(generator=torch.Generator().manual_seed(42))
    latent = latent.to(torch.float16).float().cpu()
    mean = torch.tensor(vae.config.latents_mean).view(1, -1, 1, 1, 1)
    std = torch.tensor(vae.config.latents_std).view(1, -1, 1, 1, 1)
    return (latent - mean) / std


def _retry_in_host_memory(memory, encode):
    """Run `encode`; if MPS runs out of memory, run it once more with each residual block's input held in host memory.

    The copy is exact, so the latent is the same; `memory` is the stats of
    bounded_encoder, which is None on CUDA.
    """
    if memory is None:
        return encode()
    try:
        return encode()
    except RuntimeError as error:
        if 'MPS backend out of memory' not in str(error) or memory.get('residual_offload'):
            raise
        failure = str(error)[:300]
    import gc
    import torch
    gc.collect()
    torch.mps.empty_cache()
    memory.update(residual_offload=True, memory_retry=failure)
    return encode()


def _audio(path, destination, seconds):
    """The clip's first `seconds` at 32 kHz stereo, as the official pipeline truncates reference audio.

    A mono clip has its channel repeated, as the official pipeline upmixes it;
    a stereo resampler would place it in both channels at -3 dB.

    Returns the saved path (None without an audio stream), whether the clip ran
    more than 50 ms longer, and its length from the file's metadata.
    """
    import av
    import numpy as np
    chunks = []
    limit, count = int(seconds * 32000), 0
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None, False, None
        stream = container.streams.audio[0]
        length = clip_seconds(container, stream)
        mono = len(stream.layout.channels) == 1
        resampler = av.AudioResampler(format='fltp', layout='mono' if mono else 'stereo', rate=32000)
        for frame in container.decode(audio=0):
            for item in resampler.resample(frame):
                data = item.to_ndarray()
                count += data.shape[1]
                chunks.append(data)
            if count > limit + 1600:
                break
        else:
            for item in resampler.resample(None):
                count += item.samples
                chunks.append(item.to_ndarray())
    if not chunks:
        raise ValueError('Reference audio is empty')
    data = np.concatenate(chunks, axis=1)[:, :limit]
    if data.shape[0] == 1:
        data = np.repeat(data, 2, axis=0)
    if not np.isfinite(data).all():
        raise ValueError('Invalid reference audio samples')
    np.save(destination, data, allow_pickle=False)
    return str(destination), count > limit + 1600, length


def reference_load(rows):
    """(packed video rows, encoding seconds) the visual references add, as capabilities.admit counts them.

    A still is one patch grid and five legacy encoding seconds; a clip adds five
    rows of patches per 17 frames plus two, and its own length.
    """
    video, seconds = 0, 0.
    for row in rows:
        if row.get('kind') not in ('image', 'video') or not row.get('pixels'):
            continue
        patches = (int(row.get('height', 0)) // 32) * (int(row.get('width', 0)) // 32)
        frames = int(row.get('frames', 1))
        if frames == 1:
            video, seconds = video + patches, seconds + 5.
        else:
            video, seconds = video + max(0, (frames - 5) // 17 * 5 + 2) * patches, seconds + frames / 24.
    return video, seconds


def prepare(media, canvas, directory):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    from .media_request import verify_files
    verify_files(media)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result, key_images, ref_items = [], [], []
    limit = reference_frames(canvas)
    numbers = {'image': 0, 'video': 0, 'audio': 0}
    keyframe_rows = ([('first', media['first'])] if media.get('first') else []) + [
        ('mid:%d' % row['frame'], row) for row in media.get('middle', [])] + (
        [('last', media['last'])] if media.get('last') else [])
    for anchor, info in keyframe_rows:
        c_hash = info.get('sha256')
        if not c_hash and info.get('path'):
            from .input_cache import digest
            c_hash = digest(info['path'])
        with Image.open(info['path']) as source:
            image = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGB'),
                                 (canvas['width'], canvas['height']), method=Image.Resampling.LANCZOS)
        target = directory / (anchor.replace(':', '_') + '.npy')
        np.save(target, np.array(image)[None], allow_pickle=False)
        key_images.append(torch.from_numpy(np.array(image)).float().div_(255)[None])
        result.append({'kind': 'image', 'anchor': anchor, 'pixels': str(target), 'frames': 1,
                       'content_hash': c_hash, 'source_path': info['path'],
                       'width': canvas['width'], 'height': canvas['height'], 'crop_mode': 'fit'})
    for index, row in enumerate(media.get('references', [])):
        kind, path = row['kind'], row['path']
        if kind == 'video' and row.get('voice_only'):
            # Only the sound is wanted, so the clip is read as an audio reference and no picture is encoded.
            kind = 'audio'
        if row.get('use') == 'original':
            from .original_audio import model_audio, inspect
            numbers[kind] += 1
            info = row.get('original_audio') or inspect(row)
            target = directory / ('original-%02d.npy' % index)
            result.append(dict(kind='audio', use='original', original_audio=info,
                               audio=model_audio(info, canvas, target)))
            ref_items.append({'type': 'audio'})
            continue
        c_hash = row.get('sha256')
        if not c_hash and path:
            from .input_cache import digest
            c_hash = digest(path)
        ref = {'kind': kind, 'content_hash': c_hash, 'source_path': path}
        if 'early_steps' in row:
            ref['early_steps'] = row['early_steps']
        if row.get('guide'):
            ref['guide'] = True
        if row.get('voice_only'):
            ref['voice_only'] = True
        numbers[kind] += 1
        longer, length, used = False, None, limit / 24.
        target = directory / ('reference-%02d.npy' % index)
        if kind == 'image':
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                target_w, target_h = reference_size(*image.size, canvas)
                image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
            np.save(target, np.array(image)[None], allow_pickle=False)
            ref.update(pixels=str(target), frames=1, width=target_w, height=target_h, crop_mode='resize')
            ref_items.append({'type': 'image', 'data': torch.from_numpy(np.array(image)).float().div_(255)[None]})
        elif kind == 'video':
            import av
            with av.open(path) as container:
                if not container.streams.video:
                    raise ValueError('Reference video has no video stream')
                stream = container.streams.video[0]
                width, height = reference_size(stream.width, stream.height, canvas)
                length = clip_seconds(container, stream)
                pixels = np.lib.format.open_memmap(target, mode='w+', dtype=np.uint8, shape=(limit, height, width, 3))
                origin = None
                count = 0
                previous = None
                fps = float(stream.average_rate or 24)
                for index_frame, frame in enumerate(container.decode(video=0)):
                    timestamp = float(frame.time) if frame.time is not None else index_frame / fps
                    if origin is None:
                        origin = timestamp
                    timestamp -= origin
                    if count >= limit:
                        # Only the generated length is used; a frame 50 ms past it means the clip was longer.
                        if timestamp > limit / 24. + .05:
                            longer = True
                            break
                        continue
                    image = frame.reformat(width=width, height=height, format='rgb24').to_ndarray()
                    # Explicit 24 fps reference normalization, independent of output geometry.
                    while count / 24. <= timestamp + 1e-6 and count < limit:
                        pixels[count] = image if previous is None or abs(count / 24. - timestamp) < .5 / fps else previous
                        count += 1
                    previous = image
                del previous
                count = count - (count - 5) % 17
                if count < 22:
                    raise ValueError('The reference video is too short; it needs to be at least about 1 s long.')
                pixels.flush()
                ref.update(pixels=str(target), frames=count, normalized_fps=24, width=width, height=height, crop_mode='video_24fps')
                used = count / 24.
                # The clip's own sound is a reference too, unless the request turned it off.
                soundtrack, longer_audio, _ = (None, False, None) if row.get('sound') is False or row.get('guide') else \
                    _audio(path, directory / ('reference-%02d-audio.npy' % index), limit / 24.)
                longer = longer or longer_audio
                if soundtrack:
                    ref['audio'] = soundtrack
                    ref_items.append({'type': 'audio'})
                selected = list(range(0, count, 12))
                sampled = torch.from_numpy(np.array(pixels[selected])).float().div_(255)
                if not row.get('guide'):
                    # A guide only supplies motion latents; it is not described to the
                    # vision-language encoder, like the native AddGuide node.
                    ref_items.append({'type': 'video', 'data': sampled, 'timestamps': [i / 2. for i in range(len(selected))]})
                del pixels
        else:
            ref['audio'], longer, length = _audio(path, target, limit / 24.)
            if ref['audio'] is None:
                raise ValueError('The voice-only reference video has no audio stream' if row['kind'] == 'video'
                                 else 'Reference audio file has no audio stream')
            ref.update(width=0, height=0, frames=0, crop_mode='audio_32khz')
            ref_items.append({'type': 'audio'})
        if longer:
            # Numbered per kind like the prompt's <Video 1> and <Audio 1> tags.
            ref['trimmed'] = dict(kind=kind, number=numbers[kind], used_seconds=round(used, 2),
                                  seconds=round(length, 2) if length and length > used else None)
            print(json.dumps(dict(event='reference_trimmed', **ref['trimmed'])), flush=True)
        result.append(ref)
    (directory / 'normalization.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    rows, seconds = reference_load(result)
    if rows:
        # Known long before the first encoded chunk, and what the time estimate
        # needs: reference rows enlarge every sampling step.
        print(json.dumps({'event': 'reference_media', 'reference_video_tokens': rows,
                          'reference_seconds': seconds}), flush=True)
    return result, {'images': key_images} if key_images else {'minimax_ref_items': ref_items} if ref_items else {}


def reference_options(normalized, options, step):
    """Filter whole references, including a video's optional audio presentation."""
    items, offset = [], 0
    for row in normalized:
        count = 0 if row.get('guide') else 1 + int(row['kind'] == 'video' and bool(row.get('audio')))
        if 'early_steps' not in row or step < row['early_steps']:
            items.extend(options['minimax_ref_items'][offset:offset + count])
        offset += count
    return {'minimax_ref_items': items} if items else {}


def vae_identity(base):
    """File identity of the VAE encoder, matching existing model identity fields."""
    from .input_cache import digest
    directory = Path(base) / 'vae'
    files = sorted(directory.glob('*.safetensors'))
    if not files:
        return None
    return {
        'config': digest(directory / 'config.json') if (directory / 'config.json').exists() else None,
        'weights': [[f.name, f.stat().st_size, f.stat().st_mtime_ns] for f in files]
    }


def device_compute_capability(device):
    """Hardware compute capability for CUDA/MPS/CPU."""
    import torch
    try:
        dev = torch.device(device)
        if dev.type == 'cuda':
            if hasattr(torch, 'cuda') and hasattr(torch.cuda, 'is_available') and torch.cuda.is_available():
                if hasattr(torch.cuda, 'get_device_capability'):
                    return list(torch.cuda.get_device_capability(dev))
            return None
        elif dev.type == 'mps':
            return 'mps'
        return 'cpu'
    except Exception:
        return None


def make_latent_cache_key(
    content_hash,
    width,
    height,
    frames,
    crop_mode,
    base=None,
    device='cuda',
    dtype='float32',
    *,
    model_identity=None,
    code_version=None,
    torch_version=None,
    compute_capability=None,
    return_dict=False,
):
    """Deterministic cache key containing all required material and environment properties."""
    import hashlib
    import torch
    from . import __version__

    if not content_hash or not isinstance(content_hash, str):
        raise ValueError('Material file content hash is required')

    if model_identity is None:
        if base is not None:
            model_identity = vae_identity(base)
        else:
            model_identity = None

    if code_version is None:
        # The release alone does not change between development builds, so the encoder code itself counts.
        code_version = __version__ + ':' + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]

    if torch_version is None:
        torch_version = getattr(torch, '__version__', 'unknown')

    if compute_capability is None:
        compute_capability = device_compute_capability(device)

    if hasattr(dtype, '__name__'):
        dtype_str = dtype.__name__
    elif isinstance(dtype, str):
        dtype_str = dtype
    else:
        dtype_str = str(dtype)

    key_dict = {
        'content_hash': str(content_hash),
        'width': int(width),
        'height': int(height),
        'frames': int(frames),
        'crop_mode': str(crop_mode),
        'model_identity': model_identity,
        'code_version': str(code_version),
        'torch_version': str(torch_version),
        'compute_capability': compute_capability,
        'dtype': dtype_str,
    }

    serialized = json.dumps(key_dict, sort_keys=True, separators=(',', ':'))
    key_string = 'latent-content-v1:' + hashlib.sha256(serialized.encode('utf-8')).hexdigest()

    if return_dict:
        return key_string, key_dict
    return key_string


def get_latent_cache_key_dict(**kwargs):
    """Return the raw dictionary of cache key properties for verification."""
    kwargs['return_dict'] = True
    return make_latent_cache_key(**kwargs)[1]


def _latent_cache(root, base, device, *, folder='vae', recipe='reference-latent-v2', cache_kind='reference-latent'):
    """Per-input latent receipts; another encoder, recipe or device never matches."""
    import torch
    from .input_cache import InputCache, digest
    from .paths import data_root
    directory = Path(base) / folder
    files = sorted(directory.glob('*.safetensors'))
    if not files:
        return None
    kind = torch.device(device).type
    try:
        from importlib.metadata import version
        diffusers = version('diffusers')
    except Exception:
        diffusers = None
    from . import __version__
    identity = {'recipe': recipe, 'engine': __version__, 'diffusers': diffusers,
                'torch': getattr(torch, '__version__', 'unknown'), 'device': kind,
                'gpu': torch.cuda.get_device_name() if (kind == 'cuda' and hasattr(torch, 'cuda') and hasattr(torch.cuda, 'is_available') and torch.cuda.is_available()) else None,
                'config': digest(directory / 'config.json') if (directory / 'config.json').exists() else None,
                'weights': [[f.name, f.stat().st_size, f.stat().st_mtime_ns] for f in files]}
    target_root = root or (data_root() / 'input-cache')
    return InputCache(identity, root=target_root, kind=cache_kind)


def _audio_key(path):
    """The samples with their shape, so the length counts; every normalized reference is 32 kHz."""
    import numpy as np
    mapped = np.load(path, mmap_mode='r', allow_pickle=False)
    try:
        return 'audio-32000-' + _pixels_key(mapped)
    finally:
        del mapped


def _use_audio_latent(row, ref, latent, canvas):
    """Attach a normalized latent; cached ones are whole, so a clip used as is trims here."""
    ref['audio_latent'] = latent
    if row.get('use') == 'original':
        ticks = round(canvas['frames'] / 24 * 40)
        sound = latent.reshape(2, -1, 32)
        if sound.shape[1] < ticks:
            raise ValueError('Original audio encoder returned too few latent frames')
        ref.update(use='original', original_audio=row['original_audio'],
                   audio_latent=sound[:, :ticks].reshape(-1, 32).contiguous())


def _pixels_key(mapped):
    import hashlib
    import numpy as np
    value = hashlib.sha256(json.dumps([list(mapped.shape), str(mapped.dtype)]).encode())
    for begin in range(0, len(mapped), 8):
        value.update(np.ascontiguousarray(mapped[begin:begin + 8]).reshape(-1).view('u1').data)
    return value.hexdigest()


def _key_slug(key):
    import hashlib
    if isinstance(key, str) and len(key) == 64 and all(c in '0123456789abcdef' for c in key.lower()):
        return key[:16]
    return hashlib.sha256(str(key).encode('utf-8')).hexdigest()[:16]


def _cached_latent(cache, key, directory=None):
    import torch
    import uuid
    if cache is None:
        return None
    staging_dir = Path(directory or cache.root)
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / ('reference-latent-' + _key_slug(key) + '-' + uuid.uuid4().hex + '.hit.pt')
    try:
        if cache.get(key, target) is None:
            return None
        latent = torch.load(target, map_location='cpu', weights_only=True)
        if isinstance(latent, torch.Tensor) and latent.is_floating_point() and bool(torch.isfinite(latent).all()):
            return latent
    except Exception:
        pass
    finally:
        target.unlink(missing_ok=True)
    return None


def _remember_latent(cache, key, latent, directory=None):
    import torch
    import uuid
    if cache is None or latent is None:
        return
    staging_dir = Path(directory or cache.root)
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = staging_dir / ('reference-latent-' + _key_slug(key) + '-' + uuid.uuid4().hex + '.save.pt')
    try:
        torch.save(latent, path)
        cache.put(key, path, {'success': True})
    except Exception:
        pass
    finally:
        path.unlink(missing_ok=True)


class _encode_progress:
    """Tells the caller how many reference frames are encoded, once per temporal chunk.

    Reference video encoding is the longest silent stage of a request on a
    modest GPU, so each finished chunk prints one `media_encode_progress` row
    with the elapsed time, from which the remaining time follows. The count only
    moves forward, also when a stage is retried.
    """

    def __init__(self, rows, clock=None):
        import time
        self.total = sum(int(frames) for frames in rows)
        self.clock = clock or time.perf_counter
        self.started = self.clock()
        self.before = self.done = 0

    def __call__(self, frames):
        done = min(self.total, self.before + int(frames))
        if done <= self.done or not self.total:
            return
        self.done = done
        print(json.dumps({'event': 'media_encode_progress', 'done': done, 'total': self.total,
                          'elapsed_seconds': round(self.clock() - self.started, 3)}), flush=True)

    def finish(self, frames):
        """One reference is complete: the next one counts on top of it."""
        self.before += int(frames)
        self.done = max(self.done, min(self.total, self.before))


def encode_latents(normalized, value, base, canvas, *, resident=None, device='cuda', cache_root=None):
    import gc
    import numpy as np
    import torch
    from .paths import data_root
    if torch.device(device).type not in ('cuda', 'mps'):
        raise ValueError('Media encoding requires a CUDA or MPS device')
    empty_cache = torch.mps.empty_cache if torch.device(device).type == 'mps' else torch.cuda.empty_cache
    refs = [{key: row[key] for key in ('kind', 'early_steps', 'guide') if key in row} for row in normalized]
    keys, cache, hits = {}, None, set()
    cache_root = cache_root or (data_root() / 'input-cache')
    if cache_root and any(row.get('pixels') for row in normalized):
        # The same picture or clip with another prompt reuses its latent and may skip loading the encoder.
        try:
            cache = _latent_cache(cache_root, base, device)
            for index, (row, ref) in enumerate(zip(normalized, refs)):
                if cache is None or not row.get('pixels'):
                    continue
                if row.get('content_hash'):
                    keys[index] = make_latent_cache_key(
                        content_hash=row['content_hash'],
                        width=row.get('width', canvas.get('width', 0)),
                        height=row.get('height', canvas.get('height', 0)),
                        frames=row.get('frames', 1),
                        crop_mode=row.get('crop_mode', 'fit'),
                        base=base,
                        device=device,
                        dtype='float32',
                    )
                else:
                    mapped = np.load(row['pixels'], mmap_mode='r', allow_pickle=False)
                    keys[index] = _pixels_key(mapped[:row['frames']])
                    del mapped
                latent = _cached_latent(cache, keys[index], Path(row['pixels']).parent)
                if latent is not None:
                    ref['latent'] = latent
                    hits.add(index)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            cache = None
        print(json.dumps({'event': 'reference_latent_cache', 'hits': len(hits), 'misses': len(keys) - len(hits)}), flush=True)
    if any(row.get('pixels') for index, row in enumerate(normalized) if index not in hits):
        print(json.dumps({'event': 'media_encode_phase', 'phase': 'Encoding image/video references'}), flush=True)
        from diffusers.modular_pipelines.minimax_h3.encoders import encode_vae_condition
        from .vae_weights import load_video_encoder
        from src.inference.render import PIXEL_MEAN, PIXEL_STD
        # Conditioning never decodes: the 9.0 GiB decoder in the shared shards
        # is not read. The independent output decoder is loaded after sampling.
        on_mps = torch.device(device).type == 'mps'
        # On MPS the weights stay in the checkpoint mapping and each encoder
        # stage uploads its own while it runs (bounded_encoder).
        if resident is not None and not on_mps:
            vae = resident.input_video_encoder(base, canvas, device=device)
        else:
            vae, _ = load_video_encoder(base, before_upload=None if resident is None else
                                        lambda model: resident.input_vae_room(model, canvas),
                                        device='cpu' if on_mps else device)
        lifetime = nullcontext()
        if on_mps:
            from .backends.mps_vae_encode import bounded_encoder
            lifetime = bounded_encoder(vae, device=device)
        report = _encode_progress([row['frames'] for index, row in enumerate(normalized)
                                   if row.get('pixels') and index not in hits])
        with lifetime as memory:
            for index, (row, ref) in enumerate(zip(normalized, refs)):
                if row.get('pixels') and index not in hits:
                    mapped = np.load(row['pixels'], mmap_mode='r', allow_pickle=False)
                    if row['frames'] == 1:
                        pixels = torch.from_numpy(np.array(mapped[:1])).to(device).permute(3, 0, 1, 2)[None]
                        ref['latent'] = _retry_in_host_memory(memory, lambda: encode_vae_condition(
                            vae, pixels, PIXEL_MEAN, PIXEL_STD, 42)).cpu()
                        del pixels
                        report(1)
                    else:
                        ref['latent'] = _retry_in_host_memory(
                            memory, lambda: encode_visual(vae, mapped[:row['frames']], device=device,
                                                          progress=report))
                    report.finish(row['frames'])
                    del mapped
                    if cache is not None and index in keys:
                        _remember_latent(cache, keys[index], ref['latent'], Path(row['pixels']).parent)
                    from .streamed_weights import release_file_pages
                    release_file_pages([Path(row['pixels'])])
        if memory is not None:
            print(json.dumps(dict(event='media_encode_memory', video_encoder=memory)), flush=True)
        del vae
        gc.collect()
        empty_cache()
    audio_rows = [index for index, row in enumerate(normalized) if row.get('audio')]
    if audio_rows:
        # The same voice with another prompt reuses its latent and skips loading the audio VAE.
        audio_cache, audio_keys, audio_latents = None, {}, {}
        try:
            audio_cache = _latent_cache(cache_root, base, device, folder='audio_vae',
                                        recipe='reference-audio-latent-v1', cache_kind='reference-audio-latent')
            for index in audio_rows if audio_cache is not None else ():
                audio_keys[index] = _audio_key(normalized[index]['audio'])
                latent = _cached_latent(audio_cache, audio_keys[index], Path(normalized[index]['audio']).parent)
                if latent is not None:
                    audio_latents[index] = latent
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            audio_cache = None
        print(json.dumps({'event': 'reference_audio_latent_cache', 'hits': len(audio_latents),
                          'misses': len(audio_rows) - len(audio_latents)}), flush=True)
        if len(audio_latents) < len(audio_rows):
            print(json.dumps({'event': 'media_encode_phase', 'phase': 'Encoding reference audio'}), flush=True)
            from .vae_weights import load_audio_vae
            vae = (resident.input_audio_encoder(base, canvas, device=device)
                   if resident is not None and torch.device(device).type != 'mps' else
                   load_audio_vae(base, device=device)[0])
            mean = torch.tensor(vae.config.latents_mean).view(1, 1, -1)
            std = torch.tensor(vae.config.latents_std).view(1, 1, -1)
            if vae.config.sampling_rate != 32000:
                raise ValueError('Reference audio normalization requires the pinned 32 kHz H3 VAE')
            for index in audio_rows:
                if index not in audio_latents:
                    audio = torch.from_numpy(np.load(normalized[index]['audio'], allow_pickle=False)).to(device)
                    latent = vae.encode(audio[:, None], return_dict=False)[0].mode().float().cpu().transpose(1, 2)
                    audio_latents[index] = ((latent - mean) / std).reshape(-1, 32).contiguous()
                    if audio_cache is not None:
                        _remember_latent(audio_cache, audio_keys[index], audio_latents[index],
                                         Path(normalized[index]['audio']).parent)
                    del audio, latent
            del vae
            gc.collect()
            empty_cache()
        for index in audio_rows:
            _use_audio_latent(normalized[index], refs[index], audio_latents[index], canvas)
    if any(row.get('anchor') for row in normalized):
        value.update(keyframe_anchors=[row['anchor'] for row in normalized],
                     condition_latents=[row['latent'] for row in refs])
    else:
        value['references'] = refs
        for stage in value.get('early_reference', {}).get('conditions', []):
            remaining = [ref for ref in refs if 'early_steps' not in ref or stage['start_step'] < ref['early_steps']]
            if remaining:
                stage['value']['references'] = remaining
    from .media_conditioning import describe
    return describe(value, canvas['width'], canvas['height'], canvas['frames'])
