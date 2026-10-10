"""Request geometry without importing CUDA; follow the pinned H3 VAE grid."""
import inspect
import math
import types


def geometry(width=1344, height=768, frames=None, seconds=None):
    if any(not isinstance(n, int) or n < 256 or n % 32 for n in (width, height)):
        raise ValueError('Width and height must be multiples of 32, at least 256 pixels.')
    if frames is not None and seconds is not None:
        raise ValueError('Specify frames or seconds, not both.')
    if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
        raise ValueError('Duration must be a positive finite number.')
    requested = math.ceil(seconds * 24) if seconds is not None else (243 if frames is None else frames)
    if not isinstance(requested, int) or requested < 1:
        raise ValueError('Frame count must be a positive integer.')
    aligned = requested + (5 - requested) % 17
    latent_frames = (aligned - 5) // 17 * 5 + 2
    if latent_frames < 12:
        raise ValueError('The video must be at least 1.625 s long.')
    return dict(width=width, height=height, fps=24, requested_frames=requested,
                requested_seconds=seconds, frames=aligned, seconds=aligned / 24,
                latent_frames=latent_frames, video_tokens=latent_frames * (height // 32) * (width // 32),
                alignment='Pinned H3 VAE: round up to 17*n+5 frames; no resampling or hidden resolution reduction.')


def sampler_for_canvas(sampler, width, height):
    """Bind canvas constants in an isolated globals dict, preserving upstream code.

    Engine.sample supplies no_grad. Never edit the upstream file or mutate its
    module globals: two Engine instances can use different canvases safely.
    """
    original = inspect.unwrap(sampler)
    bound = types.FunctionType(original.__code__,
        dict(original.__globals__, LATENT_H=height // 16, LATENT_W=width // 16),
        original.__name__, original.__defaults__, original.__closure__)
    bound.__kwdefaults__ = original.__kwdefaults__
    return bound
