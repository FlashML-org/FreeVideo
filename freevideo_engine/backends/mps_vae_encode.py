"""Bound the pending Metal work of the original FP32 input-video encoder."""
from contextlib import contextmanager


@contextmanager
def bounded_encoder(model):
    """Finish each convolution before submitting the next encoder operation.

    MPS can retain large temporary allocations for pending 3D convolutions even
    after their Python tensors are gone. Fencing the original operators bounds
    that lifetime without changing weights, precision, tiles or posterior noise.
    Hooks belong to this VAE instance and are always removed.
    """
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('Bounded MPS input encoding requires inference')
    modules = [module for module in model.modules() if isinstance(module, torch.nn.Conv3d)]
    if not modules:
        raise ValueError('Expected the H3 input encoder convolution modules')
    handles = []
    stats = dict(policy='Complete original FP32 convolutions and release unused MPS allocations',
                 convolution_completions=0, convolution_modules=len(modules))
    def complete(module, inputs, output):
        torch.mps.synchronize()
        torch.mps.empty_cache()
        stats['convolution_completions'] += 1
    try:
        for module in modules:
            handles.append(module.register_forward_hook(complete))
        yield stats
    finally:
        for handle in handles:
            handle.remove()
