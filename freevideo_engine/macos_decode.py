"""MPS H3 decoding through the shared bounded readers and RGB writer."""
import gc
import json
from pathlib import Path
import time


def weight_cache_limits(allocator_bytes):
    """Bound optional retained weights by the actual admitted Metal capacity."""
    if type(allocator_bytes) is not int or allocator_bytes <= 0:
        raise ValueError('Decoder cache requires a positive admitted byte limit')
    # At 6 GiB the native decoder evicted 17 of 23 preloaded layers and loaded
    # 562 layers, versus 216 with the active-layer schedule. The 8 GiB retained
    # path loaded only 63 and was faster. Keep the 5 GiB cache plus 3 GiB of
    # workspace together; partial preloading below that point causes churn.
    retained = 0 if allocator_bytes < 8 * 2**30 else min(5 * 2**30, allocator_bytes - 3 * 2**30)
    return dict(resident_bytes=retained,
                working_reserve_bytes=2 * 2**30)


def tile_group_size(allocator_bytes, *, cache_weights=True):
    """Reuse streamed layers when the admitted allowance cannot retain a set."""
    limits = weight_cache_limits(allocator_bytes)
    if type(cache_weights) is not bool:
        raise ValueError('Decoder weight caching must be explicitly enabled or disabled')
    # Four simultaneous tile states were measured at 4 GiB. Smaller admitted
    # allowances keep one tile's activations instead of assuming those fit.
    return 4 if cache_weights and allocator_bytes >= 4 * 2**30 and not limits['resident_bytes'] else 0


def decode_to_file(latents, audio_latents, output, *, base, artifacts_dir,
                   budget_bytes=4 * 2**30, reserve_bytes=2 * 2**30, cache_weights=True):
    if type(cache_weights) is not bool:
        raise ValueError('Decoder weight caching must be explicitly enabled or disabled')
    from .macos_vdn import activate
    activate()
    import torch
    import numpy as np
    from diffusers import AutoencoderKLMiniMaxH3
    from diffusers.utils.export_utils import encode_video
    from src.inference.render import PIXEL_MEAN, PIXEL_STD, FPS
    from .backends import get_backend
    from .vae_weights import skeleton, load_parameters, load_audio_vae
    from .decode_stream import render_rgb
    from .system import system_memory
    backend = get_backend('mps')
    policy = backend.configure_budget(budget_bytes, reserve_bytes=reserve_bytes)
    cache_options = (dict(weight_cache_limits(policy['effective_allocator_limit_bytes']),
                          linear_dtype=torch.float16, preload=True) if cache_weights else {})
    tile_group = tile_group_size(policy['effective_allocator_limit_bytes'], cache_weights=cache_weights)
    output, artifacts_dir, base = Path(output), Path(artifacts_dir), Path(base)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    vae = video_decoder = audio_vae = frames = mapping = None
    handles, specs = [], []
    def phase(text):
        print(json.dumps(dict(event='decode_phase', phase=text)), flush=True)
    def guard(module, inputs):
        if system_memory()['available_bytes'] < 2**30:
            raise MemoryError('MPS decoding reached the 1 GiB physical RAM emergency floor')
    def finite_video(module, inputs, output):
        if not bool(output.isfinite().all()):
            raise ValueError('MPS video decoder produced nonfinite pixels')
    try:
        with torch.no_grad():
            phase('Loading video VAE')
            vae = skeleton(AutoencoderKLMiniMaxH3, base / 'vae')
            vae.encoder = vae.quant_conv = None
            paths = tuple(sorted((base / 'vae').glob('*.safetensors')))
            load_parameters(vae, paths, device_for_name=lambda name:
                'meta' if name.startswith('decoder.transformer_blocks.') else 'mps',
                linear_fp16=False, mapped=False)
            # Constructor-computed RoPE buffers are not checkpoint parameters.
            for name, value in list(vae.named_buffers()):
                parent, _, leaf = name.rpartition('.')
                setattr(vae.get_submodule(parent) if parent else vae, leaf, value.to('mps'))
            vae.eval().requires_grad_(False)
            from .backends.mps_vae import install_mlx_attention
            mlx_attention = install_mlx_attention(vae)
            handles.append(vae.decoder.register_forward_hook(finite_video))
            for index, layer in enumerate(vae.decoder.transformer_blocks):
                specs.append((layer, paths, f'decoder.transformer_blocks.{index}.', ()))
                handles.append(layer.register_forward_pre_hook(guard))
            mean = torch.tensor(vae.config.latents_mean, device='mps').view(1, -1, 1, 1, 1)
            std = torch.tensor(vae.config.latents_std, device='mps').view(1, -1, 1, 1, 1)
            phase('Decoding the video')
            def progress(done, total, elapsed):
                print(json.dumps(dict(event='decode_progress', done=done, total=total,
                                      elapsed_seconds=elapsed)), flush=True)
            if tile_group:
                from .macos_decode_tiles import TileDecoder
                residency = video_decoder = TileDecoder(vae, backend, specs, group_tiles=tile_group)
            else:
                residency = backend.make_offloader(specs, linears={}, **cache_options)
                video_decoder = vae
            with residency, \
                    torch.autocast(device_type='mps', dtype=torch.float16, cache_enabled=False):
                frames, mapping, timings = render_rgb(video_decoder, latents.to('mps') * std + mean,
                    PIXEL_MEAN, PIXEL_STD, artifacts_dir / 'rgb.npy', progress=progress)
                video_residency = residency.stats()
            for handle in handles:
                handle.remove()
            handles.clear()
            specs.clear()
            residency.specs.clear()
            del mean, std
            layer = value = None
            vae = video_decoder = None
            gc.collect()
            backend.empty_cache()
            phase('Loading and decoding audio')
            audio_vae, audio_loading = load_audio_vae(base, device='mps')
            mean = torch.tensor(audio_vae.config.latents_mean, device='mps').view(1, -1, 1)
            std = torch.tensor(audio_vae.config.latents_std, device='mps').view(1, -1, 1)
            audio = audio_vae.decode(audio_latents.to('mps') * std + mean, return_dict=False)[0]
            audio = audio.float().permute(1, 0, 2)[0].cpu()
            backend.synchronize()
            if not bool(audio.isfinite().all()):
                raise ValueError('MPS audio decoder produced nonfinite samples')
            rate = audio_vae.config.sampling_rate
            np.save(artifacts_dir / 'audio.npy', audio.numpy(), allow_pickle=False)
            audio_vae = None
            del mean, std
            gc.collect()
            backend.empty_cache()
            phase('Saving MP4 and audio')
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.stem + '.partial' + output.suffix)
            encode_video(frames, fps=FPS, output_path=str(temporary), audio=audio, audio_sample_rate=rate)
            temporary.replace(output)
            return dict(device_backend='mps', output=str(output), frames=len(frames),
                audio_sample_rate=rate, audio_shape=list(audio.shape), elapsed_seconds=time.monotonic()-started,
                policy=policy, video_residency=video_residency, audio_loading=audio_loading, timings=timings,
                precision='FP32 checkpoint; FP16 video autocast; FP32 audio decoder',
                mlx_attention_modules=mlx_attention)
    finally:
        for handle in handles:
            handle.remove()
        specs.clear()
        vae = video_decoder = audio_vae = frames = None
        if mapping is not None:
            mapping._mmap.close()
        gc.collect()
        backend.empty_cache()
