"""Validate the pinned VDN first/last-frame conditioning contract."""
import torch


def first_pass_conditions(conditions, target, canvas):
    """Align target keyframe latents to the smaller first-pass canvas.

    Pad the same border that will be cropped after upscaling, then resample
    spatially with antialiasing. The original encoded anchors are untouched
    and used at full resolution for the tail. This also supports native
    ComfyUI conditioning, where the source pixels are no longer available.
    """
    from torch.nn import functional as F
    from .two_pass import plan, SCALES
    # The first-pass canvas tells the upscale factor apart: each factor has its own size.
    for scale in SCALES:
        sampling = plan(target, staged=True, scale=scale)
        if all(canvas[key] == sampling['first'][key] for key in ('width', 'height', 'frames')):
            break
    else:
        raise ValueError('Keyframe resize must match the planned first-pass canvas')
    anchors, latents = conditions
    left, top = (sampling['crop'][key] // 16 for key in ('left', 'top'))
    size = (canvas['height'] // 16, canvas['width'] // 16)
    resized = []
    for latent in latents:
        image = latent.squeeze(2)
        if left or top:
            image = F.pad(image, (left, left, top, top), mode='replicate')
        resized.append(F.interpolate(image, size=size, mode='bilinear',
                                    align_corners=False, antialias=True).unsqueeze(2).contiguous())
    return anchors, resized


def validate_conditioning(prompt, tags, conditions, task, width, height):
    if prompt.ndim != 2 or prompt.shape[1] != 5120 or not prompt.shape[0]:
        raise ValueError('Expected H3 layer-50 prompt embeddings [L,5120]')
    if tags.ndim != 1 or tags.shape[0] != prompt.shape[0] or not bool(((tags == 0) | (tags == 1)).all()):
        raise ValueError('Invalid H3 text/vision token tags')
    if task == 't2va':
        if conditions or not bool((tags == 1).all()):
            raise ValueError('Keyframe input requires an Engine with task=fl2va')
        return
    expected_anchors = {'i2va': ('first',), 'l2va': ('last',), 'fl2va': ('first', 'last')}
    if task not in expected_anchors or not conditions:
        raise ValueError('Keyframe task requires its encoded anchors')
    anchors, latents = conditions
    from .mid_keyframes import native_anchors, validate_order
    validate_order(tuple(anchors), task)
    if native_anchors(anchors) != expected_anchors[task] or len(latents) != len(anchors):
        raise ValueError(task + ' requires anchors ' + repr(expected_anchors[task]))
    if not bool((tags == 0).any()):
        raise ValueError('Keyframes require prompt embeddings encoded with their images')
    expected = (1, 24, 1, height // 16, width // 16)
    for value in latents:
        if not isinstance(value, torch.Tensor) or tuple(value.shape) != expected:
            raise ValueError('Keyframe latent shape does not match the requested canvas')
        if value.dtype != torch.float32 or not bool(torch.isfinite(value).all()):
            raise ValueError('Keyframe latents must be finite float32 tensors')
