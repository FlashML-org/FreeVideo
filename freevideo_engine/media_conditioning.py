"""Explicit adapter for native ComfyUI H3 conditioning and reference latents.

No generic CLIP embeddings or MODEL patches can masquerade as H3 inputs. The
native model's layout metadata is retained and checked before CUDA loading.
"""
import math
import torch
from .conditioning import to_cache
from .keyframes import validate_conditioning


def _tensor(value, shape, name):
    if not isinstance(value, torch.Tensor) or value.ndim != len(shape):
        raise ValueError('Invalid ' + name + ' tensor')
    if any(expected is not None and value.shape[i] != expected for i, expected in enumerate(shape)):
        raise ValueError('Invalid ' + name + ' shape: ' + str(tuple(value.shape)))
    if not value.is_floating_point() or not value.numel() or not bool(torch.isfinite(value).all()):
        raise ValueError(name + ' must contain finite floating point values')
    return value.detach().to(device='cpu', dtype=torch.float32).contiguous()


def condition_at(value, step=None):
    """None selects the second pass; an integer selects a zero-based first-pass step."""
    selected = value
    for stage in value.get('early_reference', {}).get('conditions', []):
        if step is None or step >= stage['start_step']:
            selected = stage['value']
    return selected


def describe(value, width, height, frames):
    info = _describe(value, width, height, frames)
    schedule = value.get('early_reference')
    if schedule is None:
        if any('early_steps' in row for row in value.get('references', [])):
            raise ValueError('early_steps requires separately encoded early reference conditions')
        return info
    if (not isinstance(schedule, dict) or set(schedule) != {'version', 'early_steps', 'conditions'}
            or type(schedule['version']) is not int or schedule['version'] != 1):
        raise ValueError('Invalid early reference conditioning version/fields')
    from .media_request import validate_early_steps
    early = schedule['early_steps']
    if not isinstance(early, list) or not early or len(early) > 32:
        raise ValueError('Invalid early_steps metadata')
    for row in early:
        if not isinstance(row, dict) or set(row) != {'reference_index', 'early_steps'}:
            raise ValueError('Invalid early_steps metadata entry')
        validate_early_steps(dict(row, kind='video'))
        if type(row['reference_index']) is not int or not 0 <= row['reference_index'] < 32:
            raise ValueError('Invalid early reference index')
    if len({row['reference_index'] for row in early}) != len(early):
        raise ValueError('Duplicate early reference index')
    if sorted(row['early_steps'] for row in early if row['early_steps']) != sorted(
            row['early_steps'] for row in value.get('references', []) if 'early_steps' in row):
        raise ValueError('Early reference metadata disagrees with active video references')
    stages = schedule['conditions']
    expected = sorted({row['early_steps'] for row in early if row['early_steps'] > 0})
    if not isinstance(stages, list) or any(not isinstance(row, dict) or set(row) != {'start_step', 'value'} for row in stages):
        raise ValueError('Invalid early reference conditions')
    if [row['start_step'] for row in stages] != expected or any(type(row['start_step']) is not int for row in stages):
        raise ValueError('Early reference boundaries disagree with early_steps')
    counts = [dict(start_step=0, **info)]
    previous = value.get('references', [])
    for stage in stages:
        current = stage['value']
        if not isinstance(current, dict) or 'early_reference' in current or current.get('keyframe_anchors'):
            raise ValueError('Invalid nested early reference condition')
        current_info = _describe(current, width, height, frames)
        kept = [row for row in previous if row.get('early_steps', stage['start_step'] + 1) > stage['start_step']]
        actual = current.get('references', [])
        if len(kept) != len(actual) or any(set(a) != set(b) or any(
                not torch.equal(a[k], b[k]) if isinstance(a[k], torch.Tensor) else a[k] != b[k]
                for k in a) for a, b in zip(kept, actual)):
            raise ValueError('Early reference conditions must retain exactly the remaining references')
        previous = actual
        counts.append(dict(start_step=stage['start_step'], **current_info))
    for name in ('text_tokens', 'reference_video_tokens', 'reference_audio_tokens'):
        info[name] = max(row[name] for row in counts)
    for index, row in enumerate(counts):
        row['condition'] = 'base' if index == len(counts) - 1 else 'full' if index == 0 else 'after_' + str(row['start_step'])
    info['early_reference'] = dict(early_steps=early, conditions=counts)
    return info


def _describe(value, width, height, frames):
    task = value.get('task', 'fl2va' if value.get('keyframe_anchors') else 't2va')
    prompt, tags = value['prompt_embeds'], value['text_token_tags']
    refs = value.get('references', [])
    if not task.startswith('ref2va'):
        if refs:
            raise ValueError('Reference latents require task=ref2va')
        conditions = (value['keyframe_anchors'], value['condition_latents']) if value.get('keyframe_anchors') else None
        validate_conditioning(prompt, tags, conditions, task, width, height)
        count = len(value.get('keyframe_anchors', [])) * (height // 32) * (width // 32)
        return {'task': task, 'text_tokens': len(tags), 'reference_video_tokens': count, 'reference_audio_tokens': 0,
                'width': width, 'height': height, 'frames': frames}
    if value.get('keyframe_anchors') or not isinstance(refs, list) or not 1 <= len(refs) <= 32:
        raise ValueError('Ref2VA needs reference latents and no keyframe anchors')
    # Reuse the shared prompt/token validation without pretending references are anchors.
    to_cache([[prompt[None], {'minimax_token_tags': tags}]], task='ref2va')
    video_rows = audio_rows = seen_rows = 0
    guides = 0
    original_count = 0
    for ref in refs:
        kind = ref.get('kind')
        if ref.get('guide'):
            # A guide is an ordinary silent video latent whose rows only differ in
            # time coordinates (guide_layout), so the shape checks below apply
            # unchanged. It must come after the other references and has no audio.
            if kind not in ('image', 'video') or ref.get('audio_latent') is not None or 'early_steps' in ref:
                raise ValueError('A guide must be a silent video or still with no early_steps')
            guides += 1
            index = ref.get('guide_index', 0)
            if type(index) is not int or not 0 <= index < frames:
                raise ValueError('Guide frame index is outside the clip')
        elif guides:
            raise ValueError('Guide references must come after all other references')
        if 'early_steps' in ref:
            from .media_request import validate_early_steps
            validate_early_steps(ref)
        if kind not in ('image', 'video', 'audio'):
            raise ValueError('Unknown reference modality')
        if kind in ('image', 'video'):
            latent = _tensor(ref.get('latent'), (1, 24, 1 if kind == 'image' else None, None, None), 'reference video')
            if latent.shape[2] < 1 or any(n < 2 or n % 2 for n in latent.shape[-2:]):
                raise ValueError('Reference latent dimensions must fit H3 patches')
            video_rows += math.prod(latent.shape[2:]) // 4
            if not ref.get('guide'):
                seen_rows += math.prod(latent.shape[2:]) // 4
        if kind == 'audio' or ref.get('audio_latent') is not None:
            audio = _tensor(ref.get('audio_latent'), (None, 32), 'reference audio rows')
            if audio.shape[0] % 2:
                raise ValueError('Reference audio must contain both channels')
            if ref.get('use') == 'original':
                original_count += 1
                if kind != 'audio' or audio.shape[0] != 2 * round(frames / 24 * 40):
                    raise ValueError('Original audio latents do not match the generation duration')
            else:
                audio_rows += audio.shape[0]
    # Guide rows are never shown to the vision-language encoder, so only the
    # other visual references need jointly encoded vision tokens.
    if seen_rows and not bool((tags == 0).any()):
        raise ValueError('Visual references need jointly encoded vision tokens')
    if original_count > 1:
        from .original_audio import AudioUseError
        raise AudioUseError('audio_original_multiple')
    task = 'ref2va_av' if video_rows and (audio_rows or original_count) else 'ref2va_audio' if (audio_rows or original_count) else 'ref2va'
    value['task'] = task
    return {'task': task, 'text_tokens': len(tags), 'reference_video_tokens': video_rows,
            'reference_audio_tokens': audio_rows, 'width': width, 'height': height, 'frames': frames}


def from_comfy(conditioning, width, height, frames):
    if len(conditioning) != 1:
        raise ValueError('One H3 conditioning sequence is required')
    extra = conditioning[0][1]
    # These modify sampling semantics; ignoring them would silently change a workflow.
    allowed = {'minimax_token_tags', 'minimax_keyframes', 'minimax_refs', 'pooled_output'}
    unknown = set(extra) - allowed
    if unknown:
        raise ValueError('Unsupported conditioning modifiers: ' + ', '.join(sorted(unknown)))
    keyframes, refs = extra.get('minimax_keyframes', []), extra.get('minimax_refs', [])
    anchors = []
    guides = []
    for item in keyframes:
        index = item.get('resolved_frame_index')
        if refs:
            # Keyframes next to references are packed as guides that share the clip's
            # time origin (same as the native AddGuide node), so any in-clip index
            # is a valid position; the latent shape guard in describe() still applies.
            if type(index) is not int or not 0 <= index < frames:
                raise ValueError('Guide frame index is outside the clip')
            guides.append(dict(kind='video', guide=True, guide_index=index,
                               latent=_tensor(item.get('latent'), (1, 24, None, None, None), 'guide video')))
            continue
        if index not in (0, frames - 1):
            raise ValueError('Only first and last keyframe anchors are supported')
        anchors.append('first' if index == 0 else 'last')
    task = ('ref2va' if refs else 'fl2va' if len(anchors) == 2 else
            'i2va' if anchors == ['first'] else 'l2va' if anchors == ['last'] else 't2va')
    value = to_cache(conditioning, task=task)
    value.update(task=task, width=width, height=height)
    if anchors:
        value.update(keyframe_anchors=anchors, condition_latents=[
            _tensor(row['latent'], (1, 24, 1, height // 16, width // 16), 'keyframe') for row in keyframes])
    if refs:
        value['references'] = []
        for row in refs:
            if row.get('use') == 'original':
                raise ValueError('Use as is needs the source audio file or AUDIO socket, not preencoded reference latents')
            kind = row['kind']
            ref = {'kind': 'video' if kind == 'video_audio' else kind}
            if kind in ('image', 'video', 'video_audio'):
                ref['latent'] = _tensor(row.get('latent'), (1, 24, None, None, None), 'reference video')
            if kind in ('audio', 'video_audio'):
                audio = _tensor(row.get('audio_latent'), (1, 32, 2, None), 'native reference audio')
                ref['audio_latent'] = audio[0].permute(1, 2, 0).reshape(-1, 32).contiguous()
            value['references'].append(ref)
        value['references'].extend(guides)
    return value, describe(value, width, height, frames)
