"""Explicit device selection; importing this package does not load Torch kernels."""
from .base import BackendCapabilities, DeviceBackend


def get_backend(name='cuda', *, torch_module=None):
    if name == 'cuda':
        from .cuda import CUDABackend
        return CUDABackend(torch_module=torch_module)
    if name == 'mps':
        from .mps import MPSBackend
        return MPSBackend(torch_module=torch_module)
    raise ValueError('Unknown device backend: ' + str(name))
