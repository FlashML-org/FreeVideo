# Copyright 2026 The MiniMax and HuggingFace Teams. All rights reserved.
# Copyright 2026 FreeVideo contributors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""Stream the pinned H3 VAE's original temporal chunks and overlap arithmetic.

Adapted from AutoencoderKLMiniMaxH3._decode at OpenVDN
30b6b380c2482f3519469350810c2955d8847fd9. Spatial decoding and blending still
call the original VAE. Only storage lifetime changes: completed frames leave
the GPU before the next clip, without concatenating a whole floating video.
"""


def pad_temporal(z, count):
    import torch
    return torch.cat([z, z[:, :, -1:].repeat(1, 1, count, 1, 1)], dim=2) if count else z


def decoded_frames(vae, count):
    tokens = vae.tokens_chunk_size
    drop = vae.config.token_drop
    ratio = vae.temporal_compression_ratio
    padding = (-(count + drop)) % tokens
    clips = (count + drop + padding) // tokens - int(drop > 0)
    tail = vae.config.clip_length % ratio
    trim = sum(tail if tail and (count + k) % tokens == 0 else ratio for k in range(padding))
    return clips * (tokens * ratio - vae.frame_pre_padding) + (vae.frame_overlap if drop > 0 else 0) - trim


def iter_decoded_chunks(vae, z):
    tokens = vae.tokens_chunk_size
    drop = vae.config.token_drop
    ratio = vae.temporal_compression_ratio
    count = z.shape[2]
    padding = (-(count + drop)) % tokens
    clips = (count + drop + padding) // tokens - int(drop > 0)
    if clips < 1:
        raise ValueError('No temporal VAE clip for the supplied latents')
    tail = vae.config.clip_length % ratio
    trim = sum(tail if tail and (count + k) % tokens == 0 else ratio for k in range(padding))
    z = pad_temporal(z, padding)
    overlap = None
    # Hold only the final candidate chunk until trailing padding is known to
    # be removed. This also handles a trim spanning more than one short chunk.
    pending = []
    pending_frames = 0
    for i in range(clips):
        start = i * tokens
        clip = vae._decode_clip(z[:, :, start:start + tokens + vae.token_overlap])
        for j in range(int(drop > 0) + 1):
            chunk = clip[:, :, j*tokens*ratio:(j+1)*tokens*ratio]
            chunk = chunk[:, :, vae.frame_pre_padding:]
            if j == 0:
                if overlap is not None:
                    chunk = vae._blend(overlap, chunk, vae.frame_overlap, dim=-3)
                pending.append(chunk)
                pending_frames += chunk.shape[2]
                while pending and pending_frames - pending[0].shape[2] >= trim:
                    item = pending.pop(0)
                    pending_frames -= item.shape[2]
                    yield item
                    del item
            else:
                overlap = chunk
        del clip, chunk
    if overlap is not None:
        pending.append(overlap)
        pending_frames += overlap.shape[2]
    keep = pending_frames - trim
    if keep < 0:
        raise ValueError('VAE temporal padding exceeds the retained output')
    for chunk in pending:
        count = min(keep, chunk.shape[2])
        if count:
            yield chunk[:, :, :count]
            keep -= count


def render_rgb(vae, latents, pixel_mean, pixel_std, path, progress=None, wide_path=None):
    """Write exact uint8 output a temporal clip at a time; retain partial data.

    With wide_path, the same frames are also written there as 16-bit RGB, from the
    float values and not from the uint8 ones (the optional 10-bit file).
    """
    import time
    from pathlib import Path
    import numpy as np
    import torch
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + '.partial.npy')
    count = decoded_frames(vae, latents.shape[2])
    if count <= 0 or latents.shape[0] != 1:
        raise ValueError('Streamed video export requires one nonempty video')
    shape = (count, latents.shape[-2] * vae.spatial_compression_ratio,
             latents.shape[-1] * vae.spatial_compression_ratio, vae.config.out_channels)
    mapping = np.lib.format.open_memmap(temporary, mode='w+', dtype=np.uint8, shape=shape)
    wide = None
    if wide_path is not None:
        from .video_10bit import open_store, to_rgb16
        wide = open_store(wide_path, *shape[:3])
    mean = torch.tensor(pixel_mean).view(1, -1, 1, 1, 1)
    std = torch.tensor(pixel_std).view(1, -1, 1, 1, 1)
    timings = dict(video_decode_seconds=0., video_postprocess_seconds=0., decoded_artifact_save_seconds=0.)
    offset = 0
    iterator = iter_decoded_chunks(vae, latents)
    started = time.perf_counter()
    complete = False
    try:
        while True:
            tick = time.perf_counter()
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            video = chunk.cpu()
            del chunk
            timings['video_decode_seconds'] += time.perf_counter() - tick
            tick = time.perf_counter()
            for start in range(0, video.shape[2], 8):
                part = (video[:, :, start:start + 8].float() * std + mean).clamp_(0, 1)
                rgb = (part[0].permute(1, 2, 3, 0) * 255).round().to(torch.uint8)
                mapping[offset:offset + len(rgb)] = rgb.numpy()
                if wide is not None:
                    wide[offset:offset + len(rgb)] = to_rgb16(part[0].permute(1, 2, 3, 0))
                offset += len(rgb)
                del part, rgb
            del video
            timings['video_postprocess_seconds'] += time.perf_counter() - tick
            tick = time.perf_counter()
            mapping.flush()
            timings['decoded_artifact_save_seconds'] += time.perf_counter() - tick
            if progress is not None:
                # The existing chunk.cpu() already waits for these frames.
                # Reporting adds no CUDA fence or extra tensor allocation.
                progress(offset, count, time.perf_counter() - started)
        if offset != count:
            raise ValueError('Streamed VAE output length differs from its original temporal plan')
        complete = True
    finally:
        iterator.close()
        mapping.flush()
        mapping._mmap.close()
        if wide is not None:
            wide.flush()
            wide._mmap.close()
            if not complete:
                # A failed or cancelled decode leaves no multi-gigabyte 16-bit store behind.
                from .video_10bit import discard_store
                discard_store(None, wide_path)
    temporary.replace(path)
    # Keep one mapped uint8 plane for the original encoder; no full float video
    # or second RGB copy is materialized. The caller owns this mapping's life.
    mapping = np.load(path, mmap_mode='r+', allow_pickle=False)
    return torch.from_numpy(mapping), mapping, timings
