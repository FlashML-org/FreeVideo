"""The CUDA device this process works on, instead of an assumed device 0.

An engine child normally sees one card through CUDA_VISIBLE_DEVICES, where the
current device is 0, so these helpers return what the hardcoded 0 did. They keep
budgets, probes and workers on the card the process owns when more than one is visible.
"""


def active_index(torch):
    try:
        return torch.cuda.current_device()
    except (AttributeError, RuntimeError, AssertionError):
        return 0  # No device context to ask: the first (only) visible card.


def active_name(torch):
    return 'cuda:%d' % active_index(torch)


def device_properties(torch):
    return torch.cuda.get_device_properties(active_index(torch))


def card_report(hardware):
    """The card this request ran on, as report fields; never fails the request."""
    import os
    import sys
    result = dict(gpu_uuid=getattr(hardware, 'gpu_uuid', None), device_order=os.environ.get('CUDA_DEVICE_ORDER'))
    visible = [part for part in (os.environ.get('CUDA_VISIBLE_DEVICES') or '').split(',') if part.strip()]
    result['visible_device_count'] = len(visible) or None
    try:
        # The lightweight parent must not open a CUDA context; only read a context that already exists.
        torch = sys.modules.get('torch')
        if torch is not None and torch.cuda.is_initialized():
            result.update(device_index=active_index(torch), visible_device_count=torch.cuda.device_count())
    except Exception:
        pass
    return result


def observed_card(torch):
    """The card index and visible count as the process that runs the model sees them."""
    try:
        return dict(device_index=active_index(torch), visible_device_count=torch.cuda.device_count())
    except Exception:
        return {}
