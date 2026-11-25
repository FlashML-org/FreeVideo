"""What one generation request still needs that setup did not install.

Read-only: nothing is downloaded, written, moved or hashed here, and the
network is not probed. The sources named are the ones the request's own
download would try first, from the same ordering and download settings.
"""
from pathlib import Path


def _first_source(root, spec):
    """Id of the source the download would start with, or '' when unknown."""
    from . import network
    from .adaln_assets import _download_plan
    try:
        return network.model_urls(_download_plan(root), spec)[0][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ''


def _sampling(root, machine, request):
    from . import sampling_assets
    from .geometry import geometry
    from .two_pass import plan, sampling_canvas
    task = request.get('task', 't2va')
    if request.get('references'):
        # Same rule as generation: a reference video with an audio track selects the audio tables.
        task = sampling_assets.engine_task({'references': request['references']}, request.get('media_base'))
    canvas = sampling_canvas(geometry(request['width'], request['height'], seconds=request['seconds']))[0]
    if request.get('fast_mode') not in (None, 'none'):
        # Its modulation comes from the adapter, so no sampling table is needed.
        from . import fast_tier
        two_pass, base, refine = fast_tier.bridge_steps(request['fast_mode'])
        return plan(canvas, two_pass, task, base_steps=base, refine_steps=refine), []
    two_pass = bool(request.get('two_pass', True))
    sampled = plan(canvas, two_pass, task, base_steps=request.get('base_steps', 8),
                   refine_steps=request.get('refine_steps', 3), scale=request.get('upscale_factor', 2))
    rows = sampling_assets.pending(root, machine, sampled, task)
    items = []
    if rows:
        remote = rows[0]['table']['download']
        spec = dict(repo=remote['repo'], revision=remote['revision'],
                    file=remote['prefix'] + '/' + rows[0]['row']['file'])
        items.append(dict(id='sampling', kind='sampling', files=len(rows),
                          bytes=sum(r['row']['bytes'] for r in rows),
                          source=_first_source(root, spec), action='auto'))
    return sampled, items


def _upscaler(root, sampled):
    from . import two_pass
    if not sampled.get('enabled') or not sampled.get('upscaler_sha256'):
        return []
    try:
        path = two_pass.checkpoint_path()
        if path.is_file() and path.stat().st_size == two_pass.UPSCALER['bytes']:
            return []
    except OSError:
        pass
    spec = dict(repo=two_pass.UPSCALER['repo'], revision=two_pass.UPSCALER['revision'],
                file=two_pass.UPSCALER['file'])
    return [dict(id='upscaler', kind='upscaler', files=1, bytes=two_pass.UPSCALER['bytes'],
                 source=_first_source(root, spec), action='auto')]


def _prompt_vlm(root):
    from .prompt_vlm import assets
    if assets.settle(root):
        return []
    try:
        spec = assets.catalog()
    except (OSError, ValueError):
        # Nothing to download from, so the request is not ready for the rewrite step.
        return [dict(id='prompt-vlm', kind='prompt-vlm', files=0, bytes=0, source='', action='unavailable')]
    return [dict(id='prompt-vlm', kind='prompt-vlm', files=len(spec['files']),
                 bytes=sum(row['bytes'] for row in spec['files']), source='', action='consent')]


def inspect(root, machine, request, *, lora_names=None):
    """Missing items for one request, in the order generation would need them.

    request: width, height, seconds, task, base_steps, refine_steps, two_pass,
    enhance (the prompt will be rewritten by the local model), loras (names),
    references (kind/path rows; replace task) with media_base for relative paths.
    lora_names: the LoRA files that exist; None skips the LoRA check.
    Every item: id, kind, files, bytes, source (id of the source tried first,
    '' when not applicable) and action: 'auto' is fetched when Generate starts,
    'consent' asks first, 'manual' means the user adds the file, 'unavailable' means
    the item's own list of files cannot be read, so it cannot be fetched at all.
    """
    root = Path(root)
    sampled, items = _sampling(root, machine, request)
    items += _upscaler(root, sampled)
    if request.get('enhance'):
        items += _prompt_vlm(root)
    if lora_names is not None:
        absent = [name for name in dict.fromkeys(request.get('loras') or []) if name not in set(lora_names)]
        if absent:
            items.append(dict(id='lora', kind='lora', files=len(absent), bytes=0, source='',
                              action='manual', names=absent[:8]))
    from .download_settings import observed_speed
    return dict(items=items, bytes=sum(item['bytes'] for item in items), ready=not items,
                bytes_per_second=observed_speed(root))
