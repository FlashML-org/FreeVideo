"""GPU bytes Windows commit headroom can back while the text encoder is placed.

Windows charges every byte the CUDA allocator reserves to system commit. A native encoder that fits the card can
still take the commit headroom to the RAM guard's floor while its weights are placed (weights it leaves on the host
are read-only file mappings, which are not charged), and the request then stops with commit exhausted although
physical RAM is free. The sampling plan already keeps its GPU allocation inside the headroom (policy.commit_gpu_limit);
the encoder stage did not, because the budget it is given is the card's alone.

The cap here is the same idea for the encoder: the GPU the process already holds, plus the commit headroom, less what
its own CUDA context and working set add beside the weights and a spare for other programs. The encoder streams the
layers beyond the cap, so the arithmetic is unchanged; only the resident share of the weights gives way.
"""
GiB = 2**30
# The process's CUDA context, tokenizer, working set and encoder workspace beside the weights.
ENCODER_HOST_BYTES = 2 * GiB
# Growth of other programs during the stage.
ENCODER_SPARE_BYTES = GiB
# Below this the card's own budget stays: a smaller placement is slower than the failure it avoids.
ENCODER_GPU_FLOOR = 8 * GiB


def encoder_commit_gpu_cap(commit_available, held_gpu_bytes, request_budget):
    """Total GPU bytes (those the process already holds included) the commit headroom backs for the encoder;
    None when it does not bind or is not known."""
    if type(commit_available) is not int or commit_available < 0 or type(request_budget) is not int:
        return None
    held = held_gpu_bytes if type(held_gpu_bytes) is int and held_gpu_bytes > 0 else 0
    limit = max(commit_available + held - ENCODER_HOST_BYTES - ENCODER_SPARE_BYTES,
                min(request_budget, ENCODER_GPU_FLOOR))
    return int(limit) if limit < request_budget else None


def live_commit_available():
    """Windows commit headroom now; None elsewhere or when it cannot be read."""
    from .system import windows
    if not windows():
        return None
    try:
        from .win32 import memory_status
        value = memory_status()['commit_available_bytes']
    except Exception:
        return None
    return value if type(value) is int else None


def live_cap(torch, request_budget):
    """encoder_commit_gpu_cap from the live commit headroom and what this process holds on the card."""
    commit = live_commit_available()
    if commit is None:
        return None
    try:
        held = int(torch.cuda.memory_reserved())
    except Exception:
        held = 0
    return encoder_commit_gpu_cap(commit, held, int(request_budget))


def commit_exhausted(error):
    """Whether a RAM-guard failure is Windows commit exhaustion (physical RAM may be free)."""
    text = str(error)
    return isinstance(error, MemoryError) and 'commit headroom' in text and 'RAM guard' in text
