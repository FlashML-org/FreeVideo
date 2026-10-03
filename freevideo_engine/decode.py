"""Decode with original tiles and FP32 normalization; optional Linear compute cache."""
import gc
import json
from pathlib import Path
import time

import torch

from .offload import LayerOffloader, pin_layer_weights
from .vae_tiles import TileDecoder


@torch.no_grad()
def decode_to_file(latents, audio_latents, out_path, *, base, offload=False, prefetch=True, tile_group=False,
                   preload=False, pin_weights=False, artifacts_dir=None, phase_callback=None,
                   stream_output=False, stream_weights=False, resident_blocks=0, model_cache=None,
                   linear_compute_cache=False):
    from diffusers import AutoencoderKLMiniMaxH3
    from diffusers.utils.export_utils import encode_video
    from src.inference.render import PIXEL_MEAN, PIXEL_STD, FPS

    started = time.perf_counter()
    def phase(name):
        if phase_callback is not None:
            phase_callback(name)
    phase('vae_load')
    print(json.dumps({'event': 'decode_phase', 'phase': 'Loading video VAE'}), flush=True)
    vae = None
    weight_source = None
    compute_cache_metrics = None
    if type(linear_compute_cache) is not bool:
        raise ValueError('linear_compute_cache must be a boolean')
    if type(resident_blocks) is not int or resident_blocks < 0:
        raise ValueError('VAE resident block count is outside this decoder')
    if resident_blocks and not offload:
        raise ValueError('Partial VAE residency requires decoder offloading')
    if pin_weights and not offload:
        raise ValueError('Pinned VAE weights require layer offloading')
    if stream_weights and (not offload or pin_weights or preload):
        raise ValueError('Streamed VAE weights require offloading without host preload or whole-model pinning')
    if model_cache is not None and not offload:
        from .resident_models import key, file_identity
        vae_key = key([file_identity(path) for path in sorted((Path(base)/'vae').iterdir()) if path.is_file()])
        if linear_compute_cache:
            vae_key = key([vae_key, 'decoder-fp16-linear-v1'])
        vae = model_cache.take('video_vae', vae_key)
    vae_cache_hit = vae is not None
    if vae is None:
        # Decode runs after sampling has consumed most of the RAM budget.
        # from_pretrained would hold the whole 9.7 GiB VAE on the CPU (Windows:
        # whole 4.71 GiB shard reads); read only decoder tensors, one at a time,
        # straight to their device. Without the Linear cache all stay FP32.
        from .vae_weights import load_video_decoder
        layers = AutoencoderKLMiniMaxH3.load_config(
            str(Path(base)/'vae'), local_files_only=True)['decoder_num_layers']
        count = resident_blocks if offload else layers
        if stream_weights:
            from .streamed_weights import SafetensorLayers
            weight_source = SafetensorLayers((Path(base)/'vae').glob('*.safetensors'),
                [f'decoder.transformer_blocks.{index}.' for index in range(count, layers)],
                intermediate_dtype=torch.float32 if linear_compute_cache else None)
        vae, compute_cache_metrics = load_video_decoder(base, resident_blocks=count, weight_source=weight_source,
                                                       linear_fp16=linear_compute_cache)
    vae.eval().requires_grad_(False)
    if type(resident_blocks) is not int or not 0 <= resident_blocks <= len(vae.decoder.transformer_blocks):
        raise ValueError('VAE resident block count is outside this decoder')
    vae.encoder = None
    vae.quant_conv = None
    vae.post_quant_conv.to('cuda')
    if offload:
        for name, child in vae.decoder.named_children():
            if name != 'transformer_blocks':
                child.to('cuda')
        vae.decoder.register_tokens.data = vae.decoder.register_tokens.data.to('cuda')
        offloaded_blocks = list(vae.decoder.transformer_blocks)[resident_blocks:]
        pinned_bytes = pin_layer_weights(offloaded_blocks) if pin_weights else 0
        if pin_weights:
            # The text-to-video decoder never calls these modules. Releasing
            # their mappings also drops references to shards shared with the
            # now-pinned decoder weights.
            vae.encoder = None
            vae.quant_conv = None
            gc.collect()
        tile_decoder = TileDecoder(vae.decoder, prefetch=prefetch, weight_source=weight_source,
                                   resident_blocks=resident_blocks) if tile_group else None
        if tile_decoder is not None:
            tile_decoder.install(vae)
        offloader = tile_decoder.offloader if tile_decoder is not None else LayerOffloader(offloaded_blocks, prefetch=prefetch, weight_source=weight_source)
    else:
        pinned_bytes = 0
        vae.decoder.to('cuda')
        offloader = None
        tile_decoder = None
        # Release unused encoder views of the checkpoint shards. Otherwise
        # they keep CPU mappings alive after all decoder weights move to GPU.
        vae.encoder = None
        vae.quant_conv = None
        gc.collect()
        if model_cache is not None:
            model_cache.put('video_vae', vae_key, vae)
    preload_seconds = 0.
    if offload and preload:
        # Touch the immutable files sequentially after allocating transfer
        # buffers, avoiding small random page faults in the offload thread.
        read_started = time.perf_counter()
        buffer = bytearray(8 * 1024 * 1024)
        for path in sorted((Path(base) / 'vae').glob('*.safetensors')):
            with path.open('rb', buffering=0) as stream:
                while stream.readinto(buffer):
                    pass
        del buffer
        preload_seconds = time.perf_counter() - read_started
    mean = torch.tensor(vae.config.latents_mean, device='cuda').view(1, -1, 1, 1, 1)
    std = torch.tensor(vae.config.latents_std, device='cuda').view(1, -1, 1, 1, 1)
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started
    decode_started = time.perf_counter()
    phase('video_decode')
    print(json.dumps({'event': 'decode_phase', 'phase': 'Decoding video tiles'}), flush=True)
    offload_stats = None
    rgb_mapping = None
    streamed_timings = {}
    try:
        # Autocast follows the original renderer. The opt-in Linear cache stores
        # its existing FP16 compute values once; all other weights stay FP32.
        with torch.autocast(device_type='cuda', dtype=torch.float16, cache_enabled=not offload):
            if stream_output:
                from .decode_stream import render_rgb
                directory = Path(artifacts_dir) if artifacts_dir else Path(out_path).with_suffix('.artifacts')
                def decoded(done, total, elapsed):
                    print(json.dumps(dict(event='decode_progress', done=done, total=total,
                                          elapsed_seconds=elapsed)), flush=True)
                frames, rgb_mapping, streamed_timings = render_rgb(vae, latents * std + mean,
                    PIXEL_MEAN, PIXEL_STD, directory/'rgb.npy', progress=decoded)
            else:
                video = vae.decode(latents * std + mean, return_dict=False)[0]
                video = video.cpu()
        if offloader is not None:
            offload_stats = offloader.stats()
    finally:
        if offloader is not None:
            offloader.close()
        if tile_decoder is not None:
            tile_decoder.close()
    try:
        del vae, offloader, tile_decoder, mean, std
        if offload:
            del offloaded_blocks
        gc.collect()
        torch.cuda.empty_cache()
        video_decode_seconds = streamed_timings.get('video_decode_seconds', time.perf_counter() - decode_started)
        phase('video_postprocess')
        # Bound postprocessing memory by frame chunks; use the same FP32 operations
        # and rounding before converting to uint8. Do this on CPU after decoding.
        if not stream_output:
            frames = torch.empty((video.shape[2], video.shape[3], video.shape[4], 3), dtype=torch.uint8)
            pixel_mean = torch.tensor(PIXEL_MEAN).view(1, -1, 1, 1, 1)
            pixel_std = torch.tensor(PIXEL_STD).view(1, -1, 1, 1, 1)
            for start in range(0, video.shape[2], 8):
                chunk = (video[:, :, start:start + 8].float() * pixel_std + pixel_mean).clamp_(0, 1)
                frames[start:start + chunk.shape[2]] = (chunk[0].permute(1, 2, 3, 0) * 255).round().to(torch.uint8)
            del video
        artifact_seconds = streamed_timings.get('decoded_artifact_save_seconds', 0.)
        if artifacts_dir:
            import numpy as np
            phase('rgb_save')
            tick = time.perf_counter()
            artifacts_dir = Path(artifacts_dir)
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            if not stream_output:
                np.save(artifacts_dir / 'rgb.npy', frames.numpy(), allow_pickle=False)
            artifact_seconds += time.perf_counter() - tick
        audio_start = time.perf_counter()
        phase('audio_load_decode')
        print(json.dumps({'event': 'decode_phase', 'phase': 'Loading and decoding audio'}), flush=True)
        audio_vae = None
        if model_cache is not None:
            from .resident_models import key, file_identity
            audio_key = key([file_identity(path) for path in sorted((Path(base)/'audio_vae').iterdir()) if path.is_file()])
            audio_vae = model_cache.take('audio_vae', audio_key)
            model_cache.make_room('audio_vae', 1*2**30, model=audio_vae, ram_need=256*2**20)
        audio_cache_hit = audio_vae is not None
        if audio_vae is None:
            from .vae_weights import load_audio_vae
            audio_vae, _ = load_audio_vae(base)
        if model_cache is not None:
            model_cache.put('audio_vae', audio_key, audio_vae)
        audio_vae.eval().requires_grad_(False)
        audio_mean = torch.tensor(audio_vae.config.latents_mean, device='cuda').view(1, -1, 1)
        audio_std = torch.tensor(audio_vae.config.latents_std, device='cuda').view(1, -1, 1)
        audio = audio_vae.decode(audio_latents * audio_std + audio_mean, return_dict=False)[0]
        audio = audio.float().permute(1, 0, 2)[0].cpu()
        rate = audio_vae.config.sampling_rate
        del audio_vae, audio_mean, audio_std
        gc.collect()
        torch.cuda.empty_cache()
        audio_seconds = time.perf_counter() - audio_start
        if artifacts_dir:
            import wave
            phase('audio_save')
            tick = time.perf_counter()
            np.save(artifacts_dir / 'audio.npy', audio.numpy(), allow_pickle=False)
            # Preserve exact float audio above; WAV is an independently playable copy.
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
        print(json.dumps({'event': 'decode_phase', 'phase': 'Saving MP4 and audio'}), flush=True)
        encode_video(frames, fps=FPS, output_path=str(temporary), audio=audio, audio_sample_rate=rate)
        temporary.replace(destination)
        frame_count = len(frames)
    finally:
        if rgb_mapping is not None:
            del frames
            rgb_mapping._mmap.close()
    return {'vae_load_seconds': load_seconds, 'video_decode_seconds': video_decode_seconds,
            'vae_linear_compute_cache': linear_compute_cache, 'vae_compute_cache_preparation': compute_cache_metrics,
            'resident_vae_cache_hit': vae_cache_hit, 'resident_audio_vae_cache_hit': audio_cache_hit,
            'vae_preload_seconds': preload_seconds,
            'audio_load_decode_seconds': audio_seconds, 'encode_seconds': time.perf_counter() - encode_started,
            'decoded_artifact_save_seconds': artifact_seconds,
            'streamed_video_output': stream_output, 'streamed_vae_weights': stream_weights,
            'vae_resident_blocks': resident_blocks,
            'video_postprocess_seconds': streamed_timings.get('video_postprocess_seconds'),
            'vae_pinned_weight_bytes': pinned_bytes,
            'decode_save_seconds': time.perf_counter() - started,
            'vae_offload': offload_stats, 'frames': frame_count, 'output': str(destination)}
