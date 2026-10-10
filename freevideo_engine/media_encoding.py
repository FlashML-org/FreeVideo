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


def encode_visual(vae, pixels, *, device='cuda'):
    """The pinned VAE chunk/padding/posterior recipe with one live pixel chunk.

    Posterior sampling still occurs once on the original device with seed 42;
    sampling separately per chunk would change the conditioning noise.
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
    moments = torch.cat(moments, dim=2).to(device)
    if len(pixels) > 1 and vae.config.token_drop:
        moments = moments[:, :, :-vae.config.token_drop]
    latent = DiagonalGaussianDistribution(moments).sample(generator=torch.Generator().manual_seed(42))
    latent = latent.to(torch.float16).float().cpu()
    mean = torch.tensor(vae.config.latents_mean).view(1, -1, 1, 1, 1)
    std = torch.tensor(vae.config.latents_std).view(1, -1, 1, 1, 1)
    return (latent - mean) / std


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
    for anchor in ('first', 'last'):
        if not media.get(anchor):
            continue
        with Image.open(media[anchor]['path']) as source:
            image = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGB'),
                                 (canvas['width'], canvas['height']), method=Image.Resampling.LANCZOS)
        target = directory / (anchor + '.npy')
        np.save(target, np.array(image)[None], allow_pickle=False)
        key_images.append(torch.from_numpy(np.array(image)).float().div_(255)[None])
        result.append({'kind': 'image', 'anchor': anchor, 'pixels': str(target), 'frames': 1})
    for index, row in enumerate(media.get('references', [])):
        kind, path = row['kind'], row['path']
        ref = {'kind': kind}
        numbers[kind] += 1
        longer, length, used = False, None, limit / 24.
        target = directory / ('reference-%02d.npy' % index)
        if kind == 'image':
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                image = image.resize(reference_size(*image.size, canvas), Image.Resampling.LANCZOS)
            np.save(target, np.array(image)[None], allow_pickle=False)
            ref.update(pixels=str(target), frames=1)
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
                ref.update(pixels=str(target), frames=count, normalized_fps=24)
                used = count / 24.
                soundtrack, longer_audio, _ = _audio(path, directory / ('reference-%02d-audio.npy' % index), limit / 24.)
                longer = longer or longer_audio
                if soundtrack:
                    ref['audio'] = soundtrack
                    ref_items.append({'type': 'audio'})
                selected = list(range(0, count, 12))
                sampled = torch.from_numpy(np.array(pixels[selected])).float().div_(255)
                ref_items.append({'type': 'video', 'data': sampled, 'timestamps': [i / 2. for i in range(len(selected))]})
                del pixels
        else:
            ref['audio'], longer, length = _audio(path, target, limit / 24.)
            if ref['audio'] is None:
                raise ValueError('Reference audio file has no audio stream')
            ref_items.append({'type': 'audio'})
        if longer:
            # Numbered per kind like the prompt's <Video 1> and <Audio 1> tags.
            ref['trimmed'] = dict(kind=kind, number=numbers[kind], used_seconds=round(used, 2),
                                  seconds=round(length, 2) if length and length > used else None)
            print(json.dumps(dict(event='reference_trimmed', **ref['trimmed'])), flush=True)
        result.append(ref)
    (directory / 'normalization.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result, {'images': key_images} if key_images else {'minimax_ref_items': ref_items} if ref_items else {}


def encode_latents(normalized, value, base, canvas, *, resident=None, device='cuda'):
    import gc
    import numpy as np
    import torch
    from diffusers.modular_pipelines.minimax_h3.encoders import encode_vae_condition
    from .vae_weights import load_audio_vae, load_video_encoder
    from src.inference.render import PIXEL_MEAN, PIXEL_STD
    if torch.device(device).type not in ('cuda', 'mps'):
        raise ValueError('Media encoding requires a CUDA or MPS device')
    empty_cache = torch.mps.empty_cache if torch.device(device).type == 'mps' else torch.cuda.empty_cache
    refs = [{'kind': row['kind']} for row in normalized]
    if any(row.get('pixels') for row in normalized):
        print(json.dumps({'event': 'media_encode_phase', 'phase': 'Encoding image/video references'}), flush=True)
        # Conditioning never decodes: the 9.0 GiB decoder in the shared shards
        # is not read. The independent output decoder is loaded after sampling.
        vae, _ = load_video_encoder(base, before_upload=None if resident is None else
                                    lambda model: resident.input_vae_room(model, canvas), device=device)
        lifetime = nullcontext()
        if torch.device(device).type == 'mps':
            from .backends.mps_vae_encode import bounded_encoder
            lifetime = bounded_encoder(vae)
        with lifetime as memory:
            for row, ref in zip(normalized, refs):
                if row.get('pixels'):
                    mapped = np.load(row['pixels'], mmap_mode='r', allow_pickle=False)
                    if row['frames'] == 1:
                        pixels = torch.from_numpy(np.array(mapped[:1])).to(device).permute(3, 0, 1, 2)[None]
                        ref['latent'] = encode_vae_condition(vae, pixels, PIXEL_MEAN, PIXEL_STD, 42).cpu()
                        del pixels
                    else:
                        ref['latent'] = encode_visual(vae, mapped[:row['frames']], device=device)
                    del mapped
                    from .streamed_weights import release_file_pages
                    release_file_pages([Path(row['pixels'])])
        if memory is not None:
            print(json.dumps(dict(event='media_encode_memory', video_encoder=memory)), flush=True)
        del vae
        gc.collect()
        empty_cache()
    if any(row.get('audio') for row in normalized):
        print(json.dumps({'event': 'media_encode_phase', 'phase': 'Encoding reference audio'}), flush=True)
        vae, _ = load_audio_vae(base, before_upload=None if resident is None else
                                lambda model: resident.input_vae_room(model, canvas, audio=True), device=device)
        mean = torch.tensor(vae.config.latents_mean).view(1, 1, -1)
        std = torch.tensor(vae.config.latents_std).view(1, 1, -1)
        if vae.config.sampling_rate != 32000:
            raise ValueError('Reference audio normalization requires the pinned 32 kHz H3 VAE')
        for row, ref in zip(normalized, refs):
            if row.get('audio'):
                audio = torch.from_numpy(np.load(row['audio'], allow_pickle=False)).to(device)
                latent = vae.encode(audio[:, None], return_dict=False)[0].mode().float().cpu().transpose(1, 2)
                ref['audio_latent'] = ((latent - mean) / std).reshape(-1, 32).contiguous()
                del audio, latent
        del vae
        gc.collect()
        empty_cache()
    if any(row.get('anchor') for row in normalized):
        value.update(keyframe_anchors=[row['anchor'] for row in normalized],
                     condition_latents=[row['latent'] for row in refs])
    else:
        value['references'] = refs
    from .media_conditioning import describe
    return describe(value, canvas['width'], canvas['height'], canvas['frames'])
