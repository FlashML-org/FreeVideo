"""File-backed original-audio preflight for editor and background requests."""
from copy import deepcopy
import json

from .original_audio import AudioUseError, fields, inspect, canvas_for, original
from .chain_plan import MAX_SECONDS


def selection(value, input_directory):
    from .comfy_assets import resolve_assets
    if not isinstance(value, dict) or set(value) - {'file', 'use', 'range'}:
        raise AudioUseError('audio_use_invalid')
    assets = resolve_assets(json.dumps([dict(value, role='reference')]), input_directory)
    row = original(assets)
    if row is None:
        return dict(use='reference')
    info = inspect(row, max_seconds=MAX_SECONDS)
    canvas = canvas_for(info, 256, 256, max_seconds=MAX_SECONDS)
    samples, rate = info['end_sample'] - info['start_sample'], info['sample_rate']
    return dict(use='original', source_id=value['file'], sample_rate=rate,
                range=dict(start_sample=info['start_sample'], end_sample=info['end_sample']),
                samples=samples, seconds=samples / rate, frames=canvas['frames'],
                output_frames=(samples * 24 + rate - 1) // rate, source_sha256=info['sha256'])


def graph_audio(prompt, node):
    """Inspect only connected known media nodes; never log the graph or text."""
    from .chain_runner import node_prompt
    from .comfy_assets import media_kind
    rows = []
    for item in node_prompt(prompt, node).values():
        if not isinstance(item, dict):
            continue
        inputs = item.get('inputs') or {}
        kind = item.get('class_type')
        if kind == 'FreeVideoMedia':
            assets = inputs.get('assets', '[]')
            if not isinstance(assets, str):
                continue  # Dynamic sockets are validated when executed.
            assets = json.loads(assets)
            if not isinstance(assets, list):
                raise AudioUseError('audio_use_invalid')
            for asset in assets:
                if not isinstance(asset, dict):
                    raise AudioUseError('audio_use_invalid')
                if not asset.get('enabled', True):
                    continue
                if 'use' in asset or 'range' in asset:
                    options = fields(dict(asset, kind=media_kind(asset.get('file', ''))))
                    if options:
                        rows.append(dict(kind='audio', file=asset['file'], **options))
            use = inputs.get('audio_use', 'reference')
            if isinstance(use, str) and fields(dict(kind='audio', use=use)):
                rows.append(dict(kind='audio', use='original'))
        elif kind == 'FreeVideoReference':
            use = inputs.get('use', 'reference')
            if isinstance(use, str) and fields(dict(kind='audio', use=use)):
                rows.append(dict(kind='audio', use='original'))
    original(dict(references=rows))
    return rows


def prepare_chain(payload, input_directory):
    rows = graph_audio(payload['prompt'], payload['node'])
    if not rows:
        return payload
    value = deepcopy(payload)
    blocks = value.get('blocks')
    if blocks is not None and (not isinstance(blocks, list) or any(not isinstance(row, dict) for row in blocks)):
        raise AudioUseError('audio_original_route')
    if 'file' not in rows[0]:
        # Tensor sockets are resolved in the node, not by the HTTP thread.
        return value
    from .comfy_assets import resolve_assets
    asset = {k: v for k, v in rows[0].items() if k != 'kind'}
    media = resolve_assets(json.dumps([dict(asset, role='reference')]), input_directory)
    info = inspect(original(media), max_seconds=MAX_SECONDS)
    selected = canvas_for(info, 256, 256, max_seconds=MAX_SECONDS)
    value['original_audio'] = dict(file=asset['file'], use='original', info=info)
    if blocks is not None:
        if len(blocks) == 1 and not any(blocks[0].get(k) for k in ('after', 'before')):
            if blocks[0]['frames'] < (info['end_sample'] - info['start_sample']) * 24 / info['sample_rate']:
                raise AudioUseError('audio_original_slot')
            blocks[0]['frames'] = selected['frames']
    else:
        value['seconds'] = (info['end_sample'] - info['start_sample']) / info['sample_rate']
    return value
