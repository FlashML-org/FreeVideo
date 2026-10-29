"""Clean target audio for either sampling pass; no archived-video requirement."""


def split(conditions, frames, device):
    import torch
    fixed = [row for row in conditions if row.get('use') == 'original']
    if not fixed:
        return conditions, None
    if len(fixed) != 1:
        from .original_audio import AudioUseError
        raise AudioUseError('audio_original_multiple')
    sound = fixed[0]['audio_latent']
    expected = (2 * round(frames / 24 * 40), 32)
    if (not isinstance(sound, torch.Tensor) or tuple(sound.shape) != expected
            or not sound.is_floating_point() or not bool(torch.isfinite(sound).all())):
        raise ValueError('Original audio latent shape or values are invalid')
    return [row for row in conditions if row.get('use') != 'original'], sound.to(device=device, dtype=torch.float32)


def timesteps(row_times, video_t, audio_t):
    """Keep the unused audio clock in the existing reference modulation bank."""
    import torch
    times = torch.unique(torch.cat((row_times, video_t.float().reshape(1), audio_t.float().reshape(1))), sorted=True)
    return times, torch.searchsorted(times, row_times)
