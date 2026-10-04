"""Combine actual stage receipts without treating 8 + 2 as 8 full-size steps."""


def sampling_workspace_peak(sampled):
    """Peak needed again, excluding only measured optional pass-cache planes.

    Each pass has its own CUDA peak/reset. Never subtract a first-pass cache
    from the refinement or upscaler, which may have the larger real workspace.
    Missing cache measurements conservatively keep the full reserved peak.
    """
    peaks = []
    for stage in sampled.get('sampling_passes') or [sampled]:
        cache = stage.get('pass_cache_admission', {})
        peaks.append(max(cache.get('workspace_peak_bytes', 0),
                         stage['torch_peak_reserved_bytes'] - cache.get('peak_cache_bytes', 0)))
    peaks.append(sampled.get('latent_upscale', {}).get('torch_peak_reserved_bytes', 0))
    return max(peaks)


def combine(first, second, upscale, sampling_plan):
    result = dict(second, sampling_plan=sampling_plan,
                  sampling_passes=[dict(first), dict(second)], latent_upscale=upscale,
                  step_seconds=list(first['step_seconds']) + list(second['step_seconds']),
                  sample_seconds=first['sample_seconds'] + upscale['stage_seconds'] + second['sample_seconds'])
    for key in ('torch_peak_allocated_bytes', 'torch_peak_reserved_bytes'):
        result[key] = max(first.get(key, 0), second[key], upscale.get(key, 0))
    for key in ('attention_backend_calls', 'fp8_kernel_calls'):
        rows = [value[key] for value in (first, second) if value.get(key) is not None]
        result[key] = ({name: sum(row.get(name, 0) for row in rows)
                        for name in set().union(*(row.keys() for row in rows))} if rows else None)
    result['residual_offload_steps'] = list(first.get('residual_offload_steps', [])) + list(second.get('residual_offload_steps', []))
    if first.get('head_execution') and second.get('head_execution'):
        result['head_execution'] = dict(second['head_execution'])
        for name in ('parallel_head_calls', 'parallel_head_warmups'):
            result['head_execution'][name] = first['head_execution'].get(name, 0) + second['head_execution'].get(name, 0)
    return result
