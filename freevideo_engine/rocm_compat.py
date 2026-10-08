"""HIP adaptations for the pinned VDN implementation, preserving its math."""
from functools import wraps
from contextlib import contextmanager
import os


def activate():
    import torch
    if not torch.version.hip:
        return
    spatial_backend = os.environ.get('FREEVIDEO_ROCM_SPATIAL_CONV', 'miopen')
    if spatial_backend not in ('miopen', 'triton'):
        raise ValueError('FREEVIDEO_ROCM_SPATIAL_CONV must be miopen or triton')
    if spatial_backend == 'triton':
        arch = torch.cuda.get_device_properties(torch.cuda.current_device()).gcnArchName.split(':')[0]
        if arch != 'gfx1201':
            raise ValueError('Triton H3 spatial convolution is validated only on gfx1201')
    from src.models.linear_attention.features import LinearAttentionSepConv
    from src.models.linear_attention.branch import _HeadSliceSepConv

    def wrap(original):
        @wraps(original)
        def spatial(self, proj, tokens, num_frames, frame_size):
            if spatial_backend == 'triton':
                from .rocm_spatial import spatial as depthwise
                if torch.is_grad_enabled():
                    raise RuntimeError('Triton H3 spatial convolution requires inference without autograd')
                conv = getattr(self, '_conv', self)
                channels = getattr(self, 'channels', slice(None))
                weight = getattr(conv, f'{proj}_sp').weight[channels]
                x = depthwise(tokens, weight, num_frames, frame_size)
                temporal = getattr(conv, f'{proj}_tm').weight[channels].squeeze(1).to(x.dtype)
                return x, temporal.contiguous()
            x, weight = original(self, proj, tokens, num_frames, frame_size)
            # MIOpen can return NCHW even for the channels-last input. The pinned
            # temporal Triton kernel requires contiguous [frames, tokens, channels].
            return x.contiguous(), weight.contiguous()
        spatial._freevideo_rocm = True
        return spatial

    for kind in (LinearAttentionSepConv, _HeadSliceSepConv):
        if not getattr(kind.spatial, '_freevideo_rocm', False):
            kind.spatial=wrap(kind.spatial)


@contextmanager
def audio_convolutions():
    """Select native FP32 audio convolutions without MIOpen startup work."""
    import torch
    requested = os.environ.get('FREEVIDEO_ROCM_AUDIO_CONV', 'miopen')
    if requested not in ('miopen', 'native'):
        raise ValueError('FREEVIDEO_ROCM_AUDIO_CONV must be miopen or native')
    if requested == 'miopen':
        yield dict(requested=requested, changed=False)
        return
    if not torch.version.hip or torch.cuda.get_device_properties(0).gcnArchName.split(':')[0] != 'gfx1201':
        raise ValueError('Native H3 audio convolutions are validated only on gfx1201')
    previous = torch.backends.cudnn.enabled
    with torch.backends.cudnn.flags(enabled=False):
        yield dict(requested=requested, changed=True, previous_enabled=previous,
                   active_enabled=torch.backends.cudnn.enabled,
                   scope='GPU FP32 audio VAE convolutions only')


@contextmanager
def video_blas():
    """Temporarily select HIP video GEMM without changing sampling/audio GEMM."""
    import torch
    requested = os.environ.get('FREEVIDEO_ROCM_VIDEO_BLAS', 'default')
    if not torch.version.hip or requested == 'default':
        yield dict(requested=requested, changed=False)
        return
    if requested not in ('cublas', 'cublaslt'):
        raise ValueError('FREEVIDEO_ROCM_VIDEO_BLAS must be default, cublas, or cublaslt')
    previous = torch.backends.cuda.preferred_blas_library()
    torch.cuda.synchronize()
    torch.backends.cuda.preferred_blas_library(requested)
    try:
        yield dict(requested=requested, changed=True, previous=str(previous),
                   active=str(torch.backends.cuda.preferred_blas_library()),
                   scope='Video VAE only; sampling and audio retain their prior BLAS backend')
    finally:
        try:
            torch.cuda.synchronize()
        finally:
            torch.backends.cuda.preferred_blas_library(previous)
