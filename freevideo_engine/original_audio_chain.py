"""Original soundtrack ranges on one global integer clock, independent of chain IO."""
from copy import deepcopy
import json

from . import chain_plan
from .original_audio import AudioUseError, VERSION, fields, verify


def sample_at(frame, rate):
    """Nearest sample, ties to even, without floating point or accumulated rounding."""
    whole, remainder = divmod(frame * rate, chain_plan.FPS)
    return whole + (remainder * 2 > chain_plan.FPS or
                    remainder * 2 == chain_plan.FPS and whole % 2 == 1)


def plan(info, **options):
    count = info['end_sample'] - info['start_sample']
    rate = info['sample_rate']
    if count > chain_plan.MAX_SECONDS * rate:
        raise chain_plan.PlanError('too_long', 'A video is at most 2 hours long')
    # Keep the existing split and short-tail rules. Only the last block's
    # visible length changes: it ends on the selected final source sample.
    output_frames = (count * chain_plan.FPS + rate - 1) // rate
    from .geometry import geometry
    continued = options.get('continued') is True
    aligned = geometry(frames=output_frames)['frames']
    if continued:
        # The first block also spends OVERLAP frames on the saved video, and adds whole units.
        aligned = -(-output_frames // chain_plan.UNIT) * chain_plan.UNIT + chain_plan.OVERLAP
    limit = options.get('max_frames')
    if limit is None:
        seconds = options.get('single_limit_seconds')
        seconds = chain_plan.BLOCK_SECONDS if seconds is None else seconds
        chain_plan._number(seconds, 'The single generation limit')
        limit = chain_plan._first_frames(seconds)
    chain_plan._number(limit, 'The frame limit', whole=True)
    limit = min(limit, chain_plan._first_frames(30))
    options = dict(options, max_frames=limit)
    exact_seconds = count / rate
    length = (output_frames - .5) / 24 if count <= 30 * rate and aligned <= limit else (count + rate - 1) // rate
    result = chain_plan.plan(length, **options)
    if result['single']:
        result['blocks'] = [dict(seconds=exact_seconds, frames=aligned, new_frames=aligned - (chain_plan.OVERLAP if continued else 0))]
    result.update(seconds=exact_seconds, frames=output_frames,
                  hide_frames=sum(row['new_frames'] for row in result['blocks']) - output_frames)
    return result


def ranges(info, blocks, from_work=False):
    """New ranges partition the selection; conditioning also includes the fixed picture prefix.

    `from_work`: the first block continues a saved video, so it too starts OVERLAP frames before what it
    shows. That lead is audio before the selection, which the file must have."""
    rate, start, end = info['sample_rate'], info['start_sample'], info['end_sample']
    shown, result = 0, []
    for index, block in enumerate(blocks):
        frames = block.get('frames', block.get('inputs', {}).get('frames'))
        continues = index > 0 or from_work
        origin = shown - chain_plan.OVERLAP if continues else 0
        new = frames - chain_plan.OVERLAP if continues else frames
        begin = start + sample_at(shown, rate)
        finish = min(end, start + sample_at(shown + new, rate))
        if not start <= begin < finish <= end:
            raise AudioUseError('audio_original_slot')
        if start + sample_at(origin, rate) < 0:
            raise AudioUseError('audio_original_lead')
        result.append(dict(frames=frames, frame_origin=origin,
                           start_sample=begin, end_sample=finish,
                           source_start_sample=start, source_end_sample=end,
                           source_sha256=info['sha256'],
                           range=dict(start_sample=start + sample_at(origin, rate), end_sample=finish),
                           **({'from_work': True} if from_work else {})))
        shown += new
    if result[-1]['end_sample'] != end:
        raise AudioUseError('audio_original_slot')
    return result


def selection_info(selected):
    """Shape validation does not require a still-present source when loading receipts."""
    if (not isinstance(selected, dict) or set(selected) != {'file', 'use', 'info'}
            or selected['use'] != 'original' or not isinstance(selected['file'], str)):
        raise AudioUseError('audio_original_route')
    info = selected['info']
    if (not isinstance(info, dict) or any(type(info.get(key)) is not int for key in
            ('sample_rate', 'channels', 'start_sample', 'end_sample', 'bytes'))
            or not 8000 <= info['sample_rate'] <= 192000 or info['channels'] not in (1, 2)
            or not 0 <= info['start_sample'] < info['end_sample']
            or info['end_sample'] - info['start_sample'] > chain_plan.MAX_SECONDS * info['sample_rate']
            or info.get('version') != VERSION or type(info.get('full_source')) is not bool
            or not isinstance(info.get('sha256'), str) or len(info['sha256']) != 64
            or any(c not in '0123456789abcdef' for c in info['sha256'])
            or not isinstance(info.get('path'), str) or info['bytes'] <= 0):
        raise AudioUseError('audio_original_range')
    return info


def bind(record, selected):
    """Validate a durable forward chain and derive (never trust) its per-block selections."""
    from .audio_use_request import graph_audio
    info = selection_info(selected)
    rows = graph_audio(record['snapshot']['prompt'], record['node'])
    if len(rows) != 1 or rows[0].get('file') != selected['file']:
        raise AudioUseError('audio_original_route')
    wanted = rows[0].get('range')
    if wanted and wanted != {k: info[k] for k in ('start_sample', 'end_sample')}:
        raise AudioUseError('audio_original_range')
    blocks = record['blocks']
    inputs = record['snapshot']['prompt'][record['node']]['inputs']
    if len(blocks) > 1:
        if inputs.get('two_pass', True) is not True:
            raise AudioUseError('audio_original_passes')
        if inputs.get('preview') or inputs.get('refine_from'):
            raise AudioUseError('audio_original_route')
    # The first block may continue a saved video (a reference to a work, not to a block of this chain).
    from_work = isinstance(blocks[0]['after'], dict) and set(blocks[0]['after']) == {'work'}
    for index, block in enumerate(blocks):
        wanted = blocks[index - 1]['id'] if index else blocks[0]['after'] if from_work else None
        if block['before'] is not None or block['after'] != wanted:
            raise AudioUseError('audio_original_route')
        if block['inputs']['frames'] > chain_plan._first_frames(30):
            raise AudioUseError('audio_original_part')
    spans = ranges(info, blocks, from_work)
    record['original_audio'] = deepcopy(selected)
    for block, span in zip(blocks, spans):
        block['original_audio'] = span


def block_prompt(prompt, node, selected, span):
    """Only replace the enabled original asset in the executing prompt copy."""
    from .chain_runner import node_prompt
    verify(selected['info'])
    found = False
    for key in node_prompt(prompt, node):
        item = prompt[key]
        if not isinstance(item, dict) or item.get('class_type') != 'FreeVideoMedia':
            continue
        raw = item['inputs'].get('assets', '[]')
        if not isinstance(raw, str):
            continue
        assets = json.loads(raw)
        changed = False
        for asset in assets:
            if asset.get('enabled', True) and asset.get('use') == 'original':
                if asset['file'] != selected['file']:
                    raise AudioUseError('audio_original_route')
                asset['range'] = deepcopy(span['range'])
                asset['segment'] = {k: v for k, v in span.items() if k != 'range'}
                fields(dict(asset, kind='audio'))
                found = changed = True
        if changed:
            item['inputs']['assets'] = json.dumps(assets, ensure_ascii=False)
    if not found:
        raise AudioUseError('audio_original_route')


def joined_info(record, blocks):
    selected = record.get('original_audio')
    if selected is None:
        return None
    info = deepcopy(selected['info'])
    end = blocks[-1]['original_audio']['end_sample']
    if end != info['end_sample']:
        info.update(end_sample=end, full_source=False)
    return info


def validate_continuation(info, request):
    span = info.get('segment')
    if not span:
        raise AudioUseError('audio_original_route')
    if span.get('from_work'):
        # The saved video counts its own frames: a chain that continues it keeps its own clock, and
        # each part's range was derived from the part before it when the chain was made.
        return
    if span['frame_origin'] != request['frame_origin']:
        raise AudioUseError('audio_original_route')
