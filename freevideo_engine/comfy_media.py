"""Native Comfy sockets -> retained local files, without model duplication."""
from pathlib import Path


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


def export(run, canvas, *, first=None, last=None, references=None, loras=None, conditioning=None, assets=None):
    media = {'version': 1}
    if assets:
        if conditioning is not None or first is not None or last is not None or references:
            raise ValueError('Use the Media panel or direct media/conditioning sockets; disable duplicate inputs')
        connected = assets.get('_connected', {})
        media.update({key: value for key, value in assets.items() if key != '_connected'})
        first, last = connected.get('first'), connected.get('last')
        references = [{'kind': kind, 'value': connected[name]}
                      for name, kind in (('reference', 'image'), ('reference_audio', 'audio'))
                      if connected.get(name) is not None]
    if conditioning is not None and (first is not None or last is not None or references):
        raise ValueError('Preencoded conditioning already includes media; do not connect a second set')
    if references and (first is not None or last is not None):
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
            media['references'].append({'kind': kind, 'path': str(path)})
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
    if len(media) > 1:
        result['media'] = media
    return result
