"""VDN tail restart on its original schedule, with fixed audio.

The input video is already normalized H3 latent space. The completed audio is
re-noised at its own clock for each joint forward, then returned unchanged.
Keyframes and image/video/audio references retain their pinned layouts and
conditioning clocks; only generated video rows receive scheduler updates.
This is an explicit quality-changing refinement, not an exact acceleration of
single-pass generation. The automatic two-pass route uses this loop for its tail.
"""
import time

import torch

LATENT_H = 48
LATENT_W = 84


def validate_tail(base_steps, refine_steps):
    if type(base_steps) is not int or type(refine_steps) is not int or not 0 < refine_steps < base_steps:
        raise ValueError('Refinement must use a nonempty proper suffix of the original schedule')


def generate_latents(transformer, prompt_embeds, text_token_tags, num_frames, num_steps,
                     seed, device, *, initial_latents, refine_steps, step_seconds=None,
                     conditions=None, video_shift=12., audio_shift=3.):
    from diffusers import MiniMaxH3Scheduler
    from diffusers.modular_pipelines.minimax_h3.before_denoise import (
        MiniMaxH3PrepareLayoutStep, MiniMaxH3Ref2VAPrepareLayoutStep, patchify_video_latents)
    from diffusers.modular_pipelines.minimax_h3.modular_pipeline import (
        MINIMAX_H3_AUDIO_CHANNELS as AUDIO_CHANNELS, MINIMAX_H3_AUDIO_TAG as AUDIO_TAG,
        MINIMAX_H3_VIDEO_TAG as VIDEO_TAG, align_num_frames, audio_latent_num_frames,
        video_latent_num_frames)
    from src.models.sequence_layout import layout_from_indices
    from src.models.hybrid_transform import iter_hybrids, set_layout
    from src.inference.render import KEYFRAME_NOISE_AUG

    validate_tail(num_steps, refine_steps)
    if not isinstance(initial_latents, (tuple, list)) or len(initial_latents) != 2:
        raise ValueError('Initial latents must contain normalized video and completed audio')
    video, audio = initial_latents
    frames = align_num_frames(num_frames, 17, 5)
    latent_frames = video_latent_num_frames(frames, 17, 5)
    audio_frames = audio_latent_num_frames(frames)
    channels, patch = transformer.config.in_channels, tuple(transformer.config.patch_size)
    expected_video = (1, channels, latent_frames, LATENT_H, LATENT_W)
    expected_audio = (AUDIO_CHANNELS, 32, audio_frames)
    for tensor, shape, name in ((video, expected_video, 'video'), (audio, expected_audio, 'audio')):
        if not isinstance(tensor, torch.Tensor) or tuple(tensor.shape) != shape:
            raise ValueError(f'Initial {name} latent must have shape {shape}')
        if not tensor.is_floating_point() or not bool(torch.isfinite(tensor).all()):
            raise ValueError(f'Initial {name} latent must be finite floating point')

    audio_conditions = []
    if conditions and isinstance(conditions[0], dict):
        from types import SimpleNamespace
        refs = conditions
        condition_latents = [row['latent'].to(device) for row in refs if row['kind'] in ('image', 'video')]
        audio_conditions = [row['audio_latent'].to(device) for row in refs if row.get('audio_latent') is not None]
        references = [SimpleNamespace(kind=row['kind'], has_audio=row.get('audio_latent') is not None) for row in refs]
        layout = MiniMaxH3Ref2VAPrepareLayoutStep.build_ref2va_packed_sequence(
            text_token_tags, references, condition_latents, audio_conditions,
            latent_frames, LATENT_H, LATENT_W, audio_frames, patch, AUDIO_CHANNELS, AUDIO_TAG, VIDEO_TAG)
    else:
        anchors, condition_latents = conditions if conditions else ((), [])
        condition_latents = [latent.to(device) for latent in condition_latents]
        layout = MiniMaxH3PrepareLayoutStep.build_packed_sequence(
            text_token_tags, latent_frames, LATENT_H, LATENT_W, audio_frames,
            patch, AUDIO_CHANNELS, AUDIO_TAG, VIDEO_TAG, keyframe_anchors=anchors)
    positions, tags, video_ids, audio_ids, text_ids, condition_rows, audio_condition_rows = layout
    if torch.device(device).type == 'mps':
        # RoPE evaluates coordinates in FP32. MPS cannot first stage the
        # layout builder's FP64 storage on-device as CUDA does.
        positions = positions.float()
    positions, tags, video_ids, audio_ids, text_ids = (
        value.to(device) for value in (positions, tags, video_ids, audio_ids, text_ids))
    frame_h, frame_w = LATENT_H // patch[1], LATENT_W // patch[2]
    if next(iter_hybrids(transformer), None) is not None:
        set_layout(transformer, layout_from_indices(video_ids[condition_rows:], latent_frames, frame_h * frame_w,
            seq_len=positions.shape[0], frame_size=(frame_h, frame_w), text_indices=text_ids))

    video_scheduler, audio_scheduler = MiniMaxH3Scheduler(shift=video_shift), MiniMaxH3Scheduler(shift=audio_shift)
    video_scheduler.set_timesteps(num_steps, device=device)
    audio_scheduler.set_timesteps(num_steps, device=device)
    start = num_steps - refine_steps
    generator = torch.Generator(device).manual_seed(seed)
    # Match the pinned condition-first RNG order. Reference rows stay fixed
    # throughout the tail, even though the generated rows use a restart seed.
    fixed_video = []
    for latent in condition_latents:
        noise = torch.randn(latent.shape, generator=generator, device=device, dtype=torch.float32)
        fixed_video.append(patchify_video_latents(
            video_scheduler.scale_noise(latent, KEYFRAME_NOISE_AUG, noise), patch))
    del condition_latents
    source_video = video.to(device=device, dtype=torch.float32)
    source_audio = audio.to(device=device, dtype=torch.float32)
    video_noise = torch.randn(source_video.shape, generator=generator, device=device, dtype=torch.float32)
    video_rows = patchify_video_latents(video_scheduler.scale_noise(
        source_video, video_scheduler.timesteps[start], video_noise), patch)
    if fixed_video:
        video_rows = torch.cat(fixed_video + [video_rows])
    if video_rows.shape[0] != video_ids.numel():
        raise ValueError('Refinement video conditioning disagrees with the packed layout')
    del fixed_video
    audio_clean = source_audio.permute(0, 2, 1).reshape(-1, 32).contiguous()
    audio_noise = torch.randn(audio_clean.shape, generator=generator, device=device, dtype=torch.float32)
    del source_video, video_noise
    for video_t, audio_t in zip(video_scheduler.timesteps[start:], audio_scheduler.timesteps[start:]):
        tick = time.perf_counter()
        audio_rows = audio_scheduler.scale_noise(audio_clean, audio_t, audio_noise)
        if audio_conditions:
            audio_rows = torch.cat(audio_conditions + [audio_rows])
        if audio_rows.shape[0] != audio_ids.numel():
            raise ValueError('Refinement audio conditioning disagrees with the packed layout')
        row_times = torch.full((positions.shape[0],), float(video_t), dtype=torch.float32, device=device)
        row_times[video_ids[:condition_rows]] = max(float(video_t), KEYFRAME_NOISE_AUG)
        row_times[audio_ids[audio_condition_rows:]] = float(audio_t)
        row_times[audio_ids[:audio_condition_rows]] = 0.
        times, time_ids = torch.unique(row_times, sorted=True, return_inverse=True)
        prediction, _ = transformer(hidden_states=video_rows[None], audio_hidden_states=audio_rows[None],
            encoder_hidden_states=prompt_embeds[None], timestep=times, timestep_indices=time_ids,
            token_tags=tags, position_ids=positions, video_indices=video_ids, audio_indices=audio_ids,
            text_indices=text_ids, return_dict=False)
        video_rows[condition_rows:] = video_scheduler.step(
            prediction[0, condition_rows:].float(), video_t, video_rows[condition_rows:], return_dict=False)[0]
        if step_seconds is not None:
            if str(device).startswith('cuda'):
                torch.cuda.synchronize(device)
            elif torch.device(device).type == 'mps':
                torch.mps.synchronize()
            step_seconds.append(time.perf_counter() - tick)
    rows = video_rows[condition_rows:].reshape(-1, latent_frames, frame_h, frame_w, channels, *patch)
    rows = rows.permute(0, 4, 1, 5, 2, 6, 3, 7)
    return rows.reshape(expected_video).contiguous(), source_audio
