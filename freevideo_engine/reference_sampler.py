"""Experimental VDN reference sampling, adapted from pinned OpenVDN render.py.

Uses the official Diffusers Ref2VA packed layout and conditioning schedules.
Only generated rows are stepped. The transformer, attention, offloading and
FP8 kernels are the same Engine modules used by ordinary VDN requests.
The released VDN checkpoint is not a trained Ref2VA checkpoint.
"""
import time

import torch

from diffusers import MiniMaxH3Scheduler
from diffusers.modular_pipelines.minimax_h3.before_denoise import (
    MiniMaxH3Ref2VAPrepareLayoutStep, patchify_video_latents)
from diffusers.modular_pipelines.minimax_h3.modular_pipeline import (
    MINIMAX_H3_AUDIO_CHANNELS as AUDIO_CHANNELS, MINIMAX_H3_AUDIO_TAG as AUDIO_TAG,
    MINIMAX_H3_VIDEO_TAG as VIDEO_TAG, align_num_frames,
    audio_latent_num_frames, video_latent_num_frames)

from src.models.sequence_layout import layout_from_indices
from src.inference.render import KEYFRAME_NOISE_AUG
from src.models.hybrid_transform import iter_hybrids, set_layout

# Rebound per Engine request by sampler_for_canvas.
LATENT_H, LATENT_W = 48, 84


@torch.no_grad()
def generate_latents(transformer, prompt_embeds, text_token_tags, num_frames, num_steps, seed, device,
                     video_shift=12.0, audio_shift=3.0, runtime=None, step_seconds=None,
                     conditions=None):
    """Preserve the pinned sampler's RNG order and generated-row updates.

    Reference visual rows are held at max(video_t, .999), audio rows at 0.0,
    as MiniMaxH3PrepareTimestepsStep executes (its prose once said 1.0).
    """
    num_frames = align_num_frames(num_frames, 17, 5)
    num_latent_frames = video_latent_num_frames(num_frames, 17, 5)
    num_audio_latents = audio_latent_num_frames(num_frames)
    from types import SimpleNamespace
    refs = conditions
    if not refs:
        raise ValueError('Ref2VA requires references')
    condition_latents = [row['latent'].to(device) for row in refs if row['kind'] in ('image', 'video')]
    audio_conditions = [row['audio_latent'].to(device) for row in refs if row.get('audio_latent') is not None]
    references = [SimpleNamespace(kind=row['kind'], has_audio=row.get('audio_latent') is not None) for row in refs]
    patch = tuple(transformer.config.patch_size)                    # (1, 2, 2)
    channels = transformer.config.in_channels                       # 24
    frame_h, frame_w = LATENT_H // patch[1], LATENT_W // patch[2]

    position_ids, token_tags, video_indices, audio_indices, text_indices, num_condition_rows, num_audio_condition_rows = (
        MiniMaxH3Ref2VAPrepareLayoutStep.build_ref2va_packed_sequence(
            text_token_tags, references, condition_latents, audio_conditions, num_latent_frames, LATENT_H, LATENT_W, num_audio_latents,
            patch, AUDIO_CHANNELS, AUDIO_TAG, VIDEO_TAG,
        )
    )
    if torch.device(device).type == 'mps':
        # RoPE consumes FP32 coordinates; MPS cannot stage the layout's FP64
        # storage first. Keep the existing CUDA transfer and arithmetic.
        position_ids = position_ids.float()
    position_ids, token_tags = position_ids.to(device), token_tags.to(device)
    video_indices, audio_indices, text_indices = (
        video_indices.to(device), audio_indices.to(device), text_indices.to(device),
    )

    # A hybrid-converted transformer needs the packed layout before every forward; the
    # layout is the same at every denoising step, so set it once per generation.
    if next(iter_hybrids(transformer), None) is not None:
        # frame_size and text_indices unconditionally: carrying them is free, and only
        # their consumers are gated (short_conv / text_state).
        set_layout(transformer, layout_from_indices(
            video_indices[num_condition_rows:], num_latent_frames, frame_h * frame_w,
            seq_len=position_ids.shape[0], frame_size=(frame_h, frame_w),
            text_indices=text_indices,
        ))

    scheduler = MiniMaxH3Scheduler(shift=video_shift)
    audio_scheduler = MiniMaxH3Scheduler(shift=audio_shift)
    scheduler.set_timesteps(num_steps, device=device)
    audio_scheduler.set_timesteps(num_steps, device=device)

    generator = torch.Generator(device).manual_seed(seed)
    # The conditioning noise is drawn first, one draw per keyframe, before the generated
    # rows' noise (MiniMaxH3PrepareConditionLatentsStep's order).
    condition_rows = []
    for condition in condition_latents:
        noise = torch.randn(condition.shape, generator=generator, device=device, dtype=torch.float32)
        noised = scheduler.scale_noise(condition, KEYFRAME_NOISE_AUG, noise)
        condition_rows.append(patchify_video_latents(noised, patch))
    latents = torch.randn((1, channels, num_latent_frames, LATENT_H, LATENT_W),
                          generator=generator, device=device, dtype=torch.float32)
    video_rows = patchify_video_latents(latents, patch)
    if condition_rows:
        video_rows = torch.cat(condition_rows + [video_rows])
        if video_rows.shape[0] != video_indices.numel():
            raise ValueError(f"{video_rows.shape[0]} video rows (with conditioning) != "
                             f"{video_indices.numel()} layout rows")
    audio_rows = torch.randn((num_audio_latents * AUDIO_CHANNELS, 32),
                             generator=generator, device=device, dtype=torch.float32)

    if audio_conditions:
        audio_rows = torch.cat(audio_conditions + [audio_rows])
    if audio_rows.shape[0] != audio_indices.numel():
        raise ValueError('Reference audio rows disagree with packed layout')

    def synchronize():
        if torch.device(device).type == 'cuda':
            torch.cuda.synchronize(device)
        elif torch.device(device).type == 'mps':
            torch.mps.synchronize()

    if runtime is not None:
        runtime.barrier()
        synchronize()

    seq_len = position_ids.shape[0]
    for t, audio_t in zip(scheduler.timesteps, audio_scheduler.timesteps):
        step_started = time.perf_counter()
        row_timesteps = torch.full((seq_len,), float(t), dtype=torch.float32, device=device)
        if num_condition_rows:
            row_timesteps[video_indices[:num_condition_rows]] = max(float(t), KEYFRAME_NOISE_AUG)
        row_timesteps[audio_indices[num_audio_condition_rows:]] = float(audio_t)
        row_timesteps[audio_indices[:num_audio_condition_rows]] = 0.0
        timestep, timestep_indices = torch.unique(row_timesteps, sorted=True, return_inverse=True)
        noise_pred, audio_noise_pred = transformer(
            hidden_states=video_rows[None],
            audio_hidden_states=audio_rows[None],
            encoder_hidden_states=prompt_embeds[None],
            timestep=timestep,
            timestep_indices=timestep_indices,
            token_tags=token_tags,
            position_ids=position_ids,
            video_indices=video_indices,
            audio_indices=audio_indices,
            text_indices=text_indices,
            return_dict=False,
        )
        # only the generated rows step; the anchors ride through unchanged
        video_rows[num_condition_rows:] = scheduler.step(
            noise_pred[0, num_condition_rows:].float(), t, video_rows[num_condition_rows:],
            return_dict=False)[0]
        audio_rows[num_audio_condition_rows:] = audio_scheduler.step(
            audio_noise_pred[0, num_audio_condition_rows:].float(), audio_t,
            audio_rows[num_audio_condition_rows:], return_dict=False)[0]

        if step_seconds is not None:
            synchronize()
            step_seconds.append(time.perf_counter() - step_started)

    if runtime is not None:
        synchronize()
        runtime.barrier()

    # Unpatchify (the AfterDenoise step's reshape) and unpack the channel-major audio rows.
    video_rows = video_rows[num_condition_rows:]
    rows = video_rows.reshape(-1, num_latent_frames, frame_h, frame_w, channels, *patch)
    rows = rows.permute(0, 4, 1, 5, 2, 6, 3, 7)
    latents = rows.reshape(-1, channels, num_latent_frames, LATENT_H, LATENT_W).contiguous()
    audio_rows = audio_rows[num_audio_condition_rows:]
    audio_latents = audio_rows.reshape(AUDIO_CHANNELS, num_audio_latents, 32).permute(0, 2, 1).contiguous()
    return latents, audio_latents
