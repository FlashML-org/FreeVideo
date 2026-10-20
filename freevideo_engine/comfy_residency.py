"""Cooperate with ComfyUI's native memory manager without changing its models."""
from contextlib import contextmanager
from functools import wraps
import threading
import time

from .resident_process import OWNER

_local = threading.local()
# Set while release_native() waits: ComfyUI's prompt thread runs that unload, so the thread-local
# keep_engine_cache() cannot reach it. Our own unload frees native models only, as a video's does.
_releasing = threading.Event()


@contextmanager
def keep_engine_cache():
    old = getattr(_local, 'keep', False)
    _local.keep = True
    try:
        yield
    finally:
        _local.keep = old


def _native():
    """ComfyUI's memory manager in this process, or None outside ComfyUI."""
    import sys
    memory = sys.modules.get('comfy.model_management')
    return memory if memory is not None and getattr(memory, 'current_loaded_models', None) is not None else None


def _prompt_running(server):
    try:
        return bool(server.prompt_queue.get_current_queue()[0])
    except (AttributeError, TypeError, IndexError):
        return True


def native_reclaimable(server):
    """GPU bytes that ComfyUI's own idle models hold in this process; release_native() returns them.

    0 when nothing is loaded, or while a prompt is running (its models are in use)."""
    memory = _native()
    if memory is None or not memory.current_loaded_models or server is None or _prompt_running(server):
        return 0
    try:
        import torch
        return int(torch.cuda.memory_reserved(memory.get_torch_device()))
    except Exception:
        return 0


def release_native(server, *, timeout=10.):
    """Before an image or prompt-model worker starts, unload ComfyUI's own idle models, as a video does.

    ComfyUI's prompt worker does it between prompts, through the same unload command its own
    "free memory" button sends. Nothing happens when no native model is loaded, or while a prompt
    is running (its models are in use). Returns what was released."""
    memory = _native()
    if memory is None or not memory.current_loaded_models or server is None or _prompt_running(server):
        return None
    import torch
    device = memory.get_torch_device()
    before = dict(models=len(memory.current_loaded_models), reserved_bytes=int(torch.cuda.memory_reserved(device)))
    _releasing.set()
    try:
        server.prompt_queue.set_flag('unload_models', True)
        deadline = time.monotonic() + timeout
        while memory.current_loaded_models and time.monotonic() < deadline:
            time.sleep(.05)
    finally:
        _releasing.clear()
    if not memory.current_loaded_models:
        memory.soft_empty_cache()
    return dict(before, released=not memory.current_loaded_models,
                reserved_after_bytes=int(torch.cuda.memory_reserved(device)))


def install(memory):
    if getattr(memory.free_memory, '_freevideo_resident_cooperation', False) is True:
        return
    original = memory.free_memory

    @wraps(original)
    def free_memory(memory_required, device, *args, **kwargs):
        # FreeVideo's own handoff releases native Comfy models only. Other
        # nodes, and Comfy's explicit unload command, can reclaim our idle
        # process before choosing a lower-memory native loading strategy.
        if (not getattr(_local, 'keep', False) and not _releasing.is_set() and not OWNER.active and
                OWNER.process is not None and OWNER.process.poll() is None):
            if device is None or memory_required > memory.get_free_memory(device):
                OWNER.release('comfy_model')
        return original(memory_required, device, *args, **kwargs)

    free_memory._freevideo_resident_cooperation = True
    memory.free_memory = free_memory
