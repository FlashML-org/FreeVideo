"""Real work counts for the text encoder's long silent spans; reporting only.

Nothing here touches tensors, CUDA or memory planning: a callback is handed
integers that the reader or the forward pass already produced. A failure in
reporting switches reporting off and never reaches the encode.
"""
from contextlib import contextmanager

from .work_progress import WorkProgress


def checkpoint_read_reporter(stage='encoder_device_load'):
    """`progress(read_bytes, total_bytes)` for the checkpoint read-ahead, in bytes of the file read so far.

    The device copy itself has no count (the native loader reports none), so
    the bytes the read-ahead has read of the same file are what is reported."""
    state = {}

    def progress(done, total):
        if state.get('off'):
            return
        try:
            work = state.get('work')
            if work is None:
                work = state['work'] = WorkProgress('encoding', stage, int(total), 'bytes')
            work.update(done, source='checkpoint_read_ahead')
        except Exception:
            state['off'] = True
    return progress


@contextmanager
def layer_progress(model, stage='encoder_compute'):
    """Report finished language-model layers of each forward pass, as `layers` out of the layer count.

    A forward-hook per layer only counts; a retried pass starts again at layer 1 and the count never moves back."""
    handles = []
    try:
        try:
            from .encoder_lowmem import language_model
            layers = list(language_model(model).layers)
            work = WorkProgress('encoding', stage, len(layers), 'layers')
        except Exception:
            layers = []
        if layers:
            state = dict(off=False)

            def hook(index):
                def after(module, args, output):
                    if state['off']:
                        return
                    try:
                        work.update(index + 1)
                    except Exception:
                        state['off'] = True
                return after
            work.update(0)
            for index, layer in enumerate(layers):
                handles.append(layer.register_forward_hook(hook(index)))
        yield
    finally:
        for handle in handles:
            handle.remove()
