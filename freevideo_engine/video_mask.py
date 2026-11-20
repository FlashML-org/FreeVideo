"""Redraw part of a video: the unselected latent tokens keep the original clip.

``data`` for :func:`sampling`:
  video       original clip latent (1, C, F, H/16, W/16), from the continuation's VAE encode path
  video_mask  bool (F, H/32, W/32); True = redraw. One cell is one 2x2 latent patch (one token).
  audio       original sound latent (2, 32, ticks) or None
  audio_mask  'keep' | 'redo' | bool (ticks,) (True = redo; whole latent frames, both channels)

Each step the kept tokens are put back on the original's own noised path (same noise as the
sampler's start); redrawn tokens step normally. ``finish`` writes the kept tokens back exactly.
"""
from contextlib import contextmanager

import numpy as np
from PIL import Image

PIXELS_PER_TOKEN = 32
CLEAN_T = 1.


def token_mask(frame_masks, latent_frames, width, height, *, grow=24, feather=16, time_grow=1):
    """bool (F, H/32, W/32): a cell is redrawn when any selected pixel of the latent frame reaches it.

    The VAE packs 17n+5 pixel frames into 5n+2 latent frames unevenly, so the proportional split below can put a
    selection one latent frame early or late. A kept latent frame is written back as it was, so a moving selection
    would leave the object there; each redrawn cell also takes ``time_grow`` latent frames before and after it.
    """
    from .image_gen import region_edit
    count = len(frame_masks)
    if count < 1 or latent_frames < 1:
        raise ValueError('A video mask needs at least one frame')
    rows, cols = height // PIXELS_PER_TOKEN, width // PIXELS_PER_TOKEN
    result = np.zeros((latent_frames, rows, cols), dtype=bool)
    for frame in range(latent_frames):
        start = frame * count // latent_frames
        stop = max(start + 1, (frame + 1) * count // latent_frames)
        for mask in frame_masks[start:stop]:
            if region_edit.selection_is_empty(mask):
                continue
            base = region_edit.grown_mask(mask.resize((width, height), Image.Resampling.NEAREST), grow + feather)
            cells = np.asarray(base, dtype=np.uint8)[:rows * PIXELS_PER_TOKEN, :cols * PIXELS_PER_TOKEN] > 0
            result[frame] |= cells.reshape(rows, PIXELS_PER_TOKEN, cols, PIXELS_PER_TOKEN).any(axis=(1, 3))
    grown = result.copy()
    for shift in range(1, max(0, int(time_grow)) + 1):
        grown[shift:] |= result[:-shift]
        grown[:-shift] |= result[shift:]
    return grown


def paste_back(original_frames, generated_frames, frame_masks, *, grow=24, feather=16):
    """Generated pixels inside each frame's feathered selection, the original's everywhere else."""
    from .image_gen import region_edit
    if not len(original_frames) == len(generated_frames) == len(frame_masks):
        raise ValueError('Frame counts differ')
    out = []
    for original, generated, mask in zip(original_frames, generated_frames, frame_masks):
        if region_edit.selection_is_empty(mask):
            out.append(original)
            continue
        alpha = region_edit.blend_alpha(mask, original.size, grow=grow, feather=feather)
        out.append(region_edit.composite(original, generated, alpha))
    return out


def validate(data, channels, patch=(1, 2, 2)):
    video, mask = data['video'], np.asarray(data['video_mask'], dtype=bool)
    if video.ndim != 5 or video.shape[0] != 1 or video.shape[1] != channels:
        raise ValueError('The original clip latent has the wrong shape')
    frames, rows, cols = video.shape[2], video.shape[3] // patch[1], video.shape[4] // patch[2]
    if mask.shape != (frames, rows, cols):
        raise ValueError(f'The video mask must be {(frames, rows, cols)}, not {mask.shape}')
    audio, audio_mask = data.get('audio'), data.get('audio_mask', 'keep')
    # Redoing the sound alone leaves every picture token kept.
    if not mask.any() and not (isinstance(audio_mask, str) and audio_mask == 'redo'):
        raise ValueError('The video mask selects nothing')
    if audio is not None:
        if audio.ndim != 3 or audio.shape[:2] != (2, 32):
            raise ValueError('The original sound latent has the wrong shape')
        if not isinstance(audio_mask, str):
            audio_mask = np.asarray(audio_mask, dtype=bool)
            if audio_mask.shape != (audio.shape[2],):
                raise ValueError('The sound mask must have one entry per latent frame')
        elif audio_mask not in ('keep', 'redo'):
            raise ValueError('The sound mask must be keep, redo or a per-frame list')
    return mask


class Hold:
    def __init__(self, transformer, data, seed, device, *, video_offset=0, audio_offset=0):
        import torch
        from diffusers.modular_pipelines.minimax_h3.before_denoise import patchify_video_latents
        mask = validate(data, transformer.config.in_channels, tuple(transformer.config.patch_size))
        self.video_offset, self.audio_offset = video_offset, audio_offset
        self.video = data['video'].to(device=device, dtype=torch.float32)
        rows = patchify_video_latents(self.video, tuple(transformer.config.patch_size))
        self.redo_cells = mask
        self.keep = torch.from_numpy(~mask.reshape(-1)).to(device)
        self.video_rows = rows
        self.noise = None
        self.audio = None
        audio = data.get('audio')
        if audio is not None:
            self.audio = audio.to(device=device, dtype=torch.float32)
            ticks = self.audio.shape[2]
            mode = data.get('audio_mask', 'keep')
            redo = (np.full(ticks, mode == 'redo') if isinstance(mode, str) else np.asarray(mode, dtype=bool))
            self.audio_keep = torch.from_numpy(np.tile(~redo, 2)).to(device)
            self.audio_rows = self.audio.permute(0, 2, 1).reshape(-1, 32).contiguous()
        self.audio_noise = None
        self.current = None

    def _noised(self, clean, noise, t, device):
        import torch
        t = torch.as_tensor(float(t), dtype=torch.float32, device=device)
        return t * clean + (1 - t) * noise

    def before(self, model, args, kwargs):
        import torch
        video, audio = kwargs['hidden_states'][0], kwargs['audio_hidden_states'][0]
        row_times = kwargs['timestep'][kwargs['timestep_indices']]
        count = self.video_rows.shape[0]
        window = slice(self.video_offset, self.video_offset + count)
        if video.shape[0] < window.stop:
            raise ValueError('The video mask does not fit the sampled clip')
        ticks = self.audio.shape[2] if self.audio is not None else 0
        if self.audio is not None:
            generated = audio.shape[0] - self.audio_offset
            if generated // 2 < ticks:
                raise ValueError('The sound mask does not fit the sampled clip')
            rows = torch.cat([torch.arange(ticks, device=audio.device) + self.audio_offset + channel * (generated // 2)
                              for channel in range(2)])
        if self.noise is None:
            # The first call sees the sampler's own starting noise.
            self.noise = video[window].detach().clone()
            if self.audio is not None:
                self.audio_noise = audio[rows].detach().clone()
        t = row_times[kwargs['video_indices'][self.video_offset]]
        keep = self.keep
        video[window][keep] = self._noised(self.video_rows[keep], self.noise[keep], t, video.device)
        if self.audio is not None:
            audio_t = row_times[kwargs['audio_indices'][self.audio_offset]]
            ak = self.audio_keep
            audio[rows[ak]] = self._noised(self.audio_rows[ak], self.audio_noise[ak], audio_t, audio.device)
        self.current = video
        return args, kwargs

    def restore_video(self):
        pass  # the next step's pre-hook puts the kept tokens back

    def finish(self, video, audio):
        import torch
        redo = torch.from_numpy(np.repeat(np.repeat(self.redo_cells, 2, axis=1), 2, axis=2)).to(video.device)
        video = torch.where(redo[None, None], video, self.video.to(video.dtype))
        if self.audio is not None:
            ticks = self.audio.shape[2]
            keep = self.audio_keep.reshape(2, ticks)[:, None, :]
            audio = audio.clone()
            audio[:, :, :ticks] = torch.where(keep, self.audio.to(audio.dtype), audio[:, :, :ticks])
        return video, audio


@contextmanager
def sampling(transformer, data, seed, device, *, video_offset=0, audio_offset=0):
    if data is None:
        yield None
        return
    hold = Hold(transformer, data, seed, device, video_offset=video_offset, audio_offset=audio_offset)
    handle = transformer.register_forward_pre_hook(hold.before, with_kwargs=True, prepend=True)
    try:
        yield hold
    finally:
        handle.remove()
        hold.current = None
