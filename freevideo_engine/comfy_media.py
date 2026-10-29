"""Native Comfy sockets -> retained local files, without model duplication."""
import logging
from pathlib import Path
from .comfy_assets import REFERENCE_INPUTS
from .media_request import validate_early_steps


def _image(value, path):
    import torch
    from PIL import Image
    if not isinstance(value, torch.Tensor) or value.ndim != 4 or value.shape[0] != 1 or value.shape[-1] not in (3, 4):
        raise ValueError('Connect one RGB IMAGE per keyframe/reference; select a frame from a batch first')
    if not bool(torch.isfinite(value).all()):
        raise ValueError('Input image contains nonfinite pixels')
    image = (value[0, ..., :3].detach().to('cpu').clamp(0, 1) * 255).round().to(torch.uint8).numpy()
    Image.fromarray(image).save(path, format='PNG')


def _audio(value, path):
    import av
    import numpy as np
    import torch
    from fractions import Fraction
    if not isinstance(value, dict):
        raise ValueError('Expected a finite mono/stereo Comfy AUDIO input')
    waveform, rate = value.get('waveform'), value.get('sample_rate')
    if (not isinstance(waveform, torch.Tensor) or waveform.ndim != 3 or waveform.shape[0] != 1
            or not waveform.is_floating_point() or waveform.shape[-1] == 0
            or waveform.shape[1] not in (1, 2) or type(rate) is not int or not 8000 <= rate <= 192000
            or not bool(torch.isfinite(waveform).all())):
        raise ValueError('Expected a finite mono/stereo Comfy AUDIO input')
    samples = waveform[0].detach().float().cpu().contiguous().numpy()
    layout = 'mono' if len(samples) == 1 else 'stereo'
    with av.open(str(path), 'w', format='wav') as container:
        stream = container.add_stream('pcm_f32le', rate=rate)
        stream.layout = layout
        for offset in range(0, samples.shape[1], 65536):
            # WAV packed float preserves the input's float32 values.
            packed = np.ascontiguousarray(samples[:, offset:offset + 65536].T.reshape(1, -1))
            frame = av.AudioFrame.from_ndarray(packed, format='flt', layout=layout)
            frame.sample_rate, frame.pts, frame.time_base = rate, offset, Fraction(1, rate)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def continued(exported, notes=None):
    """What a continued part keeps of exported inputs: up to three pictures, and the LoRAs.

    It starts from the footage, so a first or last frame image becomes one more picture after the
    reference pictures; pre-encoded conditioning, clips and sound are left out, except a sound used
    as is, which every part plays its own range of. When keyframes are used or pictures are left out,
    notes gets one line saying so."""
    from .continuation import PICTURES_MAX, pick_pictures
    media = (exported or {}).get('media') or {}
    kept = {key: value for key, value in media.items()
            if key not in ('first', 'last', 'references', 'conditioning_info')}
    pictures = [row for row in media.get('references', []) if row.get('kind') == 'image']
    original = [row for row in media.get('references', []) if row.get('kind') == 'audio' and row.get('use') == 'original']
    keyframes = [name for name in ('first', 'last') if media.get(name)]
    # Characters added by an @ mention keep one picture each when there are several (pick_pictures).
    chosen = pick_pictures(pictures, lambda row: row.get('cast'))
    candidates = [_plain(row) for row in chosen] + [{'kind': 'image', 'path': media[name]} for name in keyframes]
    if candidates or original:
        kept['references'] = candidates[:PICTURES_MAX] + original
    lines = [continued_note(len(pictures), keyframes, len(candidates[:PICTURES_MAX]))]
    if any(row.get('kind') != 'image' and row not in original for row in media.get('references', [])) \
            or media.get('conditioning_info') \
            or (exported or {}).get('conditioning'):
        lines.append('A continued part uses picture references only. Video, audio and pre-encoded inputs are skipped.')
    for line in filter(None, lines):
        logging.info('[FreeVideo] %s', line)
        if notes is not None:
            notes.append(line)
    return {'media': kept} if len(kept) > 1 else {}


def _plain(row):
    """A reference row without the panel's character mark, which the engine's request does not carry."""
    return {key: value for key, value in row.items() if key != 'cast'}


def continued_note(pictures, keyframes, used):
    """One line for a continued part whose keyframes became pictures or whose pictures did not all fit."""
    frames = 'the %s frame%s' % (' and '.join(keyframes), 's' if len(keyframes) > 1 else '')
    if pictures + len(keyframes) > used:
        return ('Continuing the previous video with %d of %d pictures: references first, then %s.'
                % (used, pictures + len(keyframes), frames) if keyframes
                else 'Continuing the previous video with the first %d of %d references.' % (used, pictures))
    if keyframes:
        return 'Continuing the previous video. %s %s used as %s.' % (
            frames[0].upper() + frames[1:], 'are' if len(keyframes) > 1 else 'is',
            'references' if len(keyframes) > 1 else 'a reference')
    return None


def export(run, canvas, *, first=None, last=None, references=None, loras=None, conditioning=None, assets=None,
           continuing=False):
    """continuing: the request continues a video, so keyframes and references may be mixed (see continued)."""
    media = {'version': 1}
    if assets:
        if conditioning is not None or first is not None or last is not None or references:
            raise ValueError('Use the Media panel or direct media/conditioning sockets; disable duplicate inputs')
        connected = assets.get('_connected', {})
        media.update({key: value for key, value in assets.items() if key not in ('_connected', '_audio_use')})
        first, last = connected.get('first'), connected.get('last')
        references = []
        for name in REFERENCE_INPUTS:
            value = connected.get(name)
            if value is None:
                continue
            shape = getattr(value, 'shape', ())
            frames = (value[index:index + 1] for index in range(shape[0])) if len(shape) == 4 and shape[0] > 1 else [value]
            references.extend({'kind': 'image', 'value': frame} for frame in frames)
        if connected.get('reference_audio') is not None:
            references.append({'kind': 'audio', 'value': connected['reference_audio']})
        if assets.get('_audio_use') == 'original':
            next(row for row in references if row['kind'] == 'audio')['use'] = 'original'
    if conditioning is not None and (first is not None or last is not None or references):
        raise ValueError('Preencoded conditioning already includes media; do not connect a second set')
    keyframes = first is not None or last is not None or any(name in media for name in ('first', 'last'))
    if not continuing and keyframes and (references or media.get('references')):
        raise ValueError('Choose keyframes or experimental references; mixed layouts are not supported')
    result = {}
    run = Path(run)
    for anchor, value in (('first', first), ('last', last)):
        if value is not None:
            path = run / (anchor + '.png')
            _image(value, path)
            media[anchor] = str(path)
    if references:
        media['references'] = list(media.get('references', []))
        for index, reference in enumerate(references):
            kind, value = reference['kind'], reference['value']
            if 'early_steps' in reference:
                validate_early_steps(reference)
            path = run / ('reference-%02d' % index)
            if kind == 'image':
                path = path.with_suffix('.png')
                _image(value, path)
            elif kind == 'video':
                # A clip longer than the generated video is shortened, and reported, by media encoding.
                path = path.with_suffix('.mp4')
                # Native save_to honors crops/trims and remuxes compatible file
                # inputs without expanding all frames into host float tensors.
                value.save_to(str(path))
            elif kind == 'audio':
                path = path.with_suffix('.wav')
                _audio(value, path)
            else:
                raise ValueError('Unknown reference type')
            from .original_audio import fields
            row = {'kind': kind, 'path': str(path), **fields(reference)}
            if 'early_steps' in reference:
                row['early_steps'] = reference['early_steps']
            media['references'].append(row)
    if loras:
        media['loras'] = [dict(row) for row in loras]
    if conditioning is not None:
        import torch
        from .media_conditioning import from_comfy
        value, info = from_comfy(conditioning, canvas['width'], canvas['height'], canvas['frames'])
        path = run / 'input-conditioning.pt'
        torch.save(value, path)
        media['conditioning_info'] = info
        result['conditioning'] = str(path)
    if not continuing and media.get('references'):
        media['references'] = [_plain(row) for row in media['references']]
    if len(media) > 1:
        from .original_audio import original
        original(media)
        result['media'] = media
    return result
