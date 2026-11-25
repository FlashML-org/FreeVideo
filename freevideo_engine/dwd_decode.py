"""The fastest mode's decode: the pixel head's frames, the audio VAE for the sound, the usual MP4.

The video VAE is neither loaded nor read. Everything after the picture (the audio
decode with the resident audio VAE, the saved artifacts and the MP4) is the same as
decode_to_file's, and the metrics carry the same names, with the video VAE ones at zero.
"""
import gc
import json
from pathlib import Path
import time

import torch

from .decode import DECODE_LABELS

# The phase the pixel head reports while it runs.
HEAD_LABEL = 'Drawing the video'


@torch.no_grad()
def pixels_to_file(features, grid, audio_latents, out_path, *, base, adapter, artifacts_dir=None, phase_callback=None,
                   model_cache=None, output_size=None, **_decoder_options):
    """features, grid: Features.take() of the fastest mode's Engine. Decoder placement options do not apply."""
    from .video_threads import encode_video
    from src.inference.render import FPS
    from . import dwd_head

    def phase(name, label=None):
        if phase_callback is not None:
            phase_callback(name)
        print(json.dumps({'event': 'decode_phase', 'phase': label or DECODE_LABELS[name]}), flush=True)
    started = time.perf_counter()
    phase('video_decode', HEAD_LABEL)
    gc.collect()
    torch.cuda.empty_cache()  # what sampling left cached is not the head's to count
    frames, head = dwd_head.render(features, grid, adapter, torch.device('cuda'))
    del features
    gc.collect()
    torch.cuda.empty_cache()
    video_decode_seconds = time.perf_counter() - started
    artifact_seconds = 0.
    if artifacts_dir:
        import numpy as np
        phase('rgb_save')
        tick = time.perf_counter()
        artifacts_dir = Path(artifacts_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        np.save(artifacts_dir / 'rgb.npy', frames.numpy(), allow_pickle=False)
        artifact_seconds += time.perf_counter() - tick
    audio_start = time.perf_counter()
    phase('audio_load_decode')
    audio_vae = None
    if model_cache is not None:
        from .resident_models import key, file_identity
        audio_key = key([file_identity(path) for path in sorted((Path(base) / 'audio_vae').iterdir()) if path.is_file()])
        audio_vae = model_cache.take('audio_vae', audio_key)
        model_cache.make_room('audio_vae', 1 * 2**30, model=audio_vae, ram_need=256 * 2**20)
    audio_cache_hit = audio_vae is not None
    if audio_vae is None:
        from .vae_weights import load_audio_vae
        audio_vae, _ = load_audio_vae(base)
    if model_cache is not None:
        model_cache.put('audio_vae', audio_key, audio_vae)
    audio_vae.eval().requires_grad_(False)
    audio_mean = torch.tensor(audio_vae.config.latents_mean, device='cuda').view(1, -1, 1)
    audio_std = torch.tensor(audio_vae.config.latents_std, device='cuda').view(1, -1, 1)
    print(json.dumps({'event': 'decode_phase', 'phase': 'Decoding audio'}), flush=True)
    audio = audio_vae.decode(audio_latents * audio_std + audio_mean, return_dict=False)[0]
    audio = audio.float().permute(1, 0, 2)[0].cpu()
    rate = audio_vae.config.sampling_rate
    audio_level = {'audio_rms': float(audio.square().mean().sqrt()) if audio.numel() else 0.,
                   'audio_peak': float(audio.abs().max()) if audio.numel() else 0.,
                   'audio_samples': int(audio.shape[-1])}
    del audio_vae, audio_mean, audio_std
    gc.collect()
    torch.cuda.empty_cache()
    audio_seconds = time.perf_counter() - audio_start
    if artifacts_dir:
        import numpy as np
        import wave
        phase('audio_save')
        tick = time.perf_counter()
        np.save(artifacts_dir / 'audio.npy', audio.numpy(), allow_pickle=False)
        pcm = (audio.clamp(-1, 1).t().contiguous().numpy() * 32767).round().astype('<i2')
        with wave.open(str(artifacts_dir / 'audio.wav'), 'wb') as wav:
            wav.setnchannels(audio.shape[0])
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm.tobytes())
        artifact_seconds += time.perf_counter() - tick
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + '.partial' + destination.suffix)
    encode_started = time.perf_counter()
    phase('mp4_save')
    picture = frames
    if output_size is not None:
        from .output_fit import fit_frames
        picture = fit_frames(frames, *output_size)
    encode_video(picture, fps=FPS, output_path=str(temporary), audio=audio, audio_sample_rate=rate)
    temporary.replace(destination)
    return {'vae_load_seconds': 0., 'video_decode_seconds': video_decode_seconds, 'pixel_head': head,
            'vae_linear_compute_cache': False, 'vae_compute_cache_preparation': None,
            'resident_vae_cache_hit': False, 'resident_audio_vae_cache_hit': audio_cache_hit,
            'vae_preload_seconds': 0., **audio_level,
            'audio_load_decode_seconds': audio_seconds, 'encode_seconds': time.perf_counter() - encode_started,
            'decoded_artifact_save_seconds': artifact_seconds, 'streamed_video_output': False,
            'streamed_vae_weights': False, 'vae_resident_blocks': 0, 'vae_compiled_blocks': 0,
            'video_postprocess_seconds': None, 'vae_pinned_weight_bytes': 0,
            'decode_save_seconds': time.perf_counter() - started, 'vae_offload': None,
            'frames': len(frames), 'output': str(destination)}
