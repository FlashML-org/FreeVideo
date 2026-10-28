"""Fixed prefix context on the existing two-pass text sampler's clocks."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

OVERLAP_FRAMES = 22
LATENT_FRAMES = 7
LEAD_IN_TICKS = 15
LEAD_IN_SAMPLES = LEAD_IN_TICKS * 800
LEAD_IN_FADE = 240
LEAD_IN_RATE = 32000
CLEAN_T = 1.
AUDIO_CONTEXT_DEFAULT = 'clean'
ADALN_TASK = 'ref2va_audio'
ANCHOR_ADALN_TASK = 'ref2va_av'  # visual reference clock + CLEAN_T (no reference audio)
# Told to the model when the first frame of the whole film is its reference picture.
FIRST_FRAME_NOTE = ('The reference image is the very first frame of this whole film: keep the same person, clothing, '
                    'location, framing and colour tone as in that image throughout.')
FIRST_FRAME_MAX_BYTES = 8 * 1024 * 1024
FIRST_FRAME_MAX_SIDE = 4096
# The picture the model is given is half the side of the film's first frame, but not under this
# on its short side: on a real six-part chain it held framing and colour as well as the full
# picture for a third of the extra sampling time. Smaller pictures are not tested.
FIRST_FRAME_REFERENCE_SHORT = 384
VERSION = 1

MISSING = 'The previous video has no usable continuation data. Generate it again with this version of FreeVideo.'


def missing_message(video_path):
    """Keep MISSING as the leading sentence so the error panel still matches it."""
    hint = _missing_hint(video_path)
    return MISSING if not hint else MISSING + ' ' + hint


def _missing_hint(video_path):
    report = Path(video_path).with_suffix('.request.json')
    try:
        data = json.loads(report.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return ''
    if not isinstance(data, dict):
        return ''
    if data.get('preview') or data.get('early_stop'):
        return ('That clip was a preview (or kept mid-sampling). Use Continue remaining steps on a first-pass preview, '
                'or generate again with Preview first off before extending.')
    task = ((data.get('profile') or {}).get('engine') or {}).get('task') or (data.get('engine_options') or {}).get('task')
    if task and task != 't2va':
        return 'Only a finished two-pass text-to-video can be continued, not image, last-frame or reference mode.'
    store = data.get('continuation_store')
    if not isinstance(store, dict):
        metrics = data.get('metrics') if isinstance(data.get('metrics'), dict) else {}
        store = metrics.get('continuation_store') if isinstance(metrics.get('continuation_store'), dict) else {}
    if store.get('saved') is False:
        return ('The previous run did not save continuation data (Draft / single-pass). '
                'Generate with Light: two-pass on, Preview first off.')
    return ''
SIZE_MISMATCH = 'Use the same width and height as the previous video.'
UNSUPPORTED = 'Video continuation requires full two-pass text generation on an NVIDIA GPU, without first frames, last frames or reference media.'
INVALID_FRAMES = 'Frame count must be 5 + 17n and at least 39 (39, 56, 73, 90, ...).'
INVALID_FILE = 'Choose an existing MP4 video using its absolute path.'


def validate_audio_context(value):
    if value not in ('clean', 'renoise'):
        raise ValueError('FREEVIDEO_CONTINUATION_AUDIO must be clean or renoise')
    return value


def audio_context():
    """Resolve once at engine admission, then carry the value in the request."""
    return validate_audio_context(os.environ.get('FREEVIDEO_CONTINUATION_AUDIO', AUDIO_CONTEXT_DEFAULT))


def supported():
    """Platform capability, without loading Torch or probing a GPU."""
    if sys.platform == 'darwin':
        return False, 'macos'
    from .paths import data_root
    try:
        machine = json.loads((data_root() / 'machine.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        machine = {}
    if not isinstance(machine, dict):
        machine = {}
    if machine.get('device_backend', 'cuda') != 'cuda':
        return False, 'unsupported_device'
    return True, None


def validate_frames(frames):
    if type(frames) is not int or frames < 39 or (frames - 5) % 17:
        raise ValueError(INVALID_FRAMES)


def validate_route(*, task='t2va', two_pass=True, preview=False, refine_from=None,
                   media=None, backend=None, anchor=False):
    media = media or {}
    references = media.get('references')
    valid_reference = (anchor and isinstance(references, list) and len(references) == 1
                       and isinstance(references[0], dict)
                       and references[0].get('kind') == 'image')
    if (not supported()[0] or backend not in (None, 'cuda') or task != ('ref2va' if anchor else 't2va')
            or not two_pass or preview or refine_from
            or any(media.get(key) for key in ('first', 'last', 'conditioning_info'))
            or (references and not valid_reference)):
        raise ValueError(UNSUPPORTED)


def anchor_media(media, path):
    """Add exactly one explicit global first-frame reference through the media encoder."""
    if any(media.get(key) for key in ('first', 'last', 'references', 'conditioning_info')):
        raise ValueError('--continue-anchor cannot be combined with other media conditioning')
    from .media_request import digest
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or not source.stat().st_size:
        raise ValueError('--continue-anchor requires a nonempty image file')
    return dict(media, version=1, references=[dict(kind='image', path=str(source),
                                                 sha256=digest(source), bytes=source.stat().st_size)])


def first_frame_prompt(text):
    """The prompt with the note about the reference picture, inside the picture's own field when it has fields."""
    import re
    field = re.search(r'(?m)^[ \t]*integrated_multimodal_description[ \t]*:[ \t]*', text)
    position = field.end() if field else 0
    return text[:position] + FIRST_FRAME_NOTE + ' ' + text[position:]


def first_frame_png(video_path):
    """The first picture of a finished video."""
    import io
    import av
    with av.open(str(video_path)) as container:
        image = next(container.decode(video=0)).to_image()
    target = io.BytesIO()
    image.convert('RGB').save(target, format='PNG')
    return target.getvalue()


def pack_first_frame(raw):
    """A PNG as continuation record metadata; every continuation of the film carries the same value."""
    import io
    from PIL import Image
    if not isinstance(raw, bytes) or not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw) > FIRST_FRAME_MAX_BYTES:
        raise ValueError('Invalid first frame')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if not (32 <= width <= FIRST_FRAME_MAX_SIDE and 32 <= height <= FIRST_FRAME_MAX_SIDE):
                raise ValueError('Invalid first frame size')
            image.load()  # A cut-off file opens; it does not decode.
    except (OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError('Invalid first frame') from None
    return dict(format='png', width=width, height=height, data=base64.b64encode(raw).decode('ascii'))


def unpack_first_frame(value):
    """The PNG a record carries, checked again: a record is a file anyone can replace."""
    try:
        if value['format'] != 'png' or len(value['data']) > (FIRST_FRAME_MAX_BYTES + 2) // 3 * 4:
            raise ValueError('Invalid first frame')
        raw = base64.b64decode(value['data'], validate=True)
        if pack_first_frame(raw) != value:
            raise ValueError('Invalid first frame')
        return raw
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError('Invalid first frame') from None


def first_frame_reference(raw):
    """(PNG bytes, width, height) of the reference picture made from a kept first frame."""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        scale = max(.5, FIRST_FRAME_REFERENCE_SHORT / min(width, height))
        if scale >= 1:
            return raw, width, height
        small = image.convert('RGB').resize((round(width * scale), round(height * scale)), Image.LANCZOS)
    target = io.BytesIO()
    small.save(target, format='PNG')
    return target.getvalue(), small.width, small.height


def chain_first_frame(request, video_path=None):
    """The first frame of the whole film `request` continues, as record metadata; None when nothing has it.

    A continued video's record carries it from its own source. The first part of a film never
    stored one: its saved video starts with that frame. A continued part made before records
    carried the frame has no way back to it. Anything that keeps it from being read is a ValueError,
    so every caller falls back the same way.
    """
    try:
        return _chain_first_frame(request, video_path)
    except (ValueError, MemoryError):
        raise
    except Exception:
        raise ValueError('Invalid first frame') from None


def chain_first_frame_picture(request, video_path=None):
    """(PNG bytes, width, height) of the picture a continuation of `request` is given; None when nothing
    has the film's first frame. The bridge and the engine both decide with this, so they cannot disagree."""
    try:
        kept = chain_first_frame(request, video_path)
        return None if kept is None else first_frame_reference(unpack_first_frame(kept))
    except (ValueError, MemoryError):
        raise
    except Exception:
        raise ValueError('Invalid first frame') from None


def _chain_first_frame(request, video_path):
    metadata = _snapshot_value(request)['metadata']
    if metadata.get('first_frame') is not None:
        unpack_first_frame(metadata['first_frame'])
        return metadata['first_frame']
    if metadata['chain']['segment'] != 1 or video_path is None:
        return None
    from .continuation_store import video_id
    if video_id(video_path) != request.get('source_id'):
        raise ValueError(MISSING)
    return pack_first_frame(first_frame_png(video_path))


def validate_path(video_path):
    path = Path(video_path)
    if not path.is_absolute() or path.suffix.lower() != '.mp4' or not path.is_file():
        raise ValueError(INVALID_FILE)
    return path


def audio_origin(frame):
    return int(round(frame * 40 / 24))


def identity(manifest):
    """Only weights and the persisted context contract determine compatibility."""
    dependencies = json.loads(Path(__file__).with_name('dependencies.json').read_text(encoding='utf-8'))
    weights = dict(groups=sorted((row['group'], row['sha256']) for row in manifest['groups']),
        online_lora={key: spec['modules'] for key, spec in manifest.get('online_lora', {}).get('blocks', {}).items()})
    weights = hashlib.sha256(json.dumps(weights, sort_keys=True).encode('utf-8')).hexdigest()
    return dict(version=VERSION, weights=weights, precision=manifest.get('precision'),
                vdn=dependencies['vdn']['commit'], diffusers=dependencies['diffusers']['patched_tree'],
                models=dependencies['models']['vdn_revision'])


def prepare(video_path, canvas, manifest=None, sampling_plan=None, *, directory=None):
    from .continuation_store import load
    from safetensors.numpy import save
    value = load(validate_path(video_path), directory=directory)
    if value is None:
        raise ValueError(missing_message(video_path))
    from .continuation_store import TAG
    metadata = dict(value['metadata'], source_id=value['id'])
    metadata.pop('audio_lead_in', None)
    if 'storage_max_abs_error' in metadata:
        metadata['storage_max_abs_error'] = {key: error for key, error in
            metadata['storage_max_abs_error'].items() if key != 'audio_lead_in'}
    # Freeze the accepted record as immutable bytes. Only the caller's run
    # directory owns a persisted copy; store retention never reaches it.
    raw = save({key: value[key] for key in ('video_lo', 'video_hi', 'audio')},
               metadata={TAG: json.dumps(metadata, sort_keys=True)})
    request = dict(snapshot_data=base64.b64encode(raw).decode('ascii'),
                   snapshot_sha256=hashlib.sha256(raw).hexdigest(), source_id=value['id'])
    return _prepare_snapshot(request, value, canvas, manifest, sampling_plan)


def _prepare_snapshot(request, value, canvas, manifest=None, sampling_plan=None):
    validate_frames(canvas['frames'])
    metadata = value['metadata']
    if any(metadata['geometry'][key] != canvas[key] for key in ('width', 'height')):
        raise ValueError(SIZE_MISMATCH)
    if manifest is not None and metadata['identity'] != identity(manifest):
        raise ValueError(MISSING)
    if sampling_plan is not None and metadata['first_canvas'] != {
            key: sampling_plan['first'][key] for key in ('width', 'height')}:
        raise ValueError(MISSING)
    chain = metadata['chain']
    origin = chain['frame_origin'] + metadata['geometry']['frames'] - OVERLAP_FRAMES
    ticks = value['audio'].shape[2]
    return dict(request, overlap_frames=OVERLAP_FRAMES, new_frames=canvas['frames'] - OVERLAP_FRAMES,
                frame_origin=origin, audio_origin=audio_origin(origin), segment=chain['segment'] + 1,
                audio_latents=ticks)


def _snapshot_bytes(request):
    from .continuation_store import MAX_BYTES
    try:
        if 'snapshot_data' in request:
            if len(request['snapshot_data']) > (MAX_BYTES + 2) // 3 * 4:
                raise ValueError(MISSING)
            raw = base64.b64decode(request['snapshot_data'], validate=True)
        else:
            with Path(request['snapshot']).open('rb') as source:
                raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != request['snapshot_sha256']:
            raise ValueError(MISSING)
        return raw
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(MISSING) from None


def _snapshot_value(request):
    from .continuation_store import read_bytes
    from safetensors import SafetensorError
    try:
        return read_bytes(_snapshot_bytes(request))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, SafetensorError):
        raise ValueError(MISSING) from None


def persist_snapshot(request, path):
    """Publish one exact, read-only copy for the managed CLI and all retries."""
    raw = _snapshot_bytes(request)
    path = Path(path)
    with path.open('xb') as target:
        target.write(raw)
    path.chmod(0o400)
    return dict({key: value for key, value in request.items() if key != 'snapshot_data'},
                snapshot=str(path.resolve()))


def restore_snapshot(path, digest, canvas):
    request = dict(snapshot=str(Path(path).resolve()), snapshot_sha256=digest)
    value = _snapshot_value(request)
    from .continuation_store import _path
    source_id = value['metadata'].get('source_id')
    try:
        _path(source_id)  # Validate the content identifier, without reading storage.
    except ValueError:
        raise ValueError(MISSING) from None
    return _prepare_snapshot(dict(request, source_id=source_id), value, canvas)


def validate_snapshot(request, canvas, manifest=None, sampling_plan=None):
    """Later admission checks inspect the accepted snapshot, never the store."""
    return _prepare_snapshot(request, _snapshot_value(request), canvas, manifest, sampling_plan)


def summary(request):
    return dict({key: request[key] for key in ('overlap_frames', 'new_frames', 'frame_origin', 'audio_origin', 'segment')},
                audio_lead_in_samples=request.get('audio_lead_in_samples', 0))


def load(request, stage):
    import torch
    value = _snapshot_value(request)
    # Each pass receives writable tensors of its own; sampling cannot mutate
    # the immutable snapshot used by the next pass or retry.
    return dict(audio_context=validate_audio_context(request.get('audio_context', AUDIO_CONTEXT_DEFAULT)),
                **({'anchor': request['anchor']} if request.get('anchor') else {}),
                video=torch.from_numpy(value['video_lo' if stage == 'first' else 'video_hi'].copy()),
                audio=torch.from_numpy(value['audio'].copy()))


def tail(video):
    # clone avoids keeping the entire clip's storage behind a small view.
    return video[:, :, -LATENT_FRAMES:].detach().cpu().clone()


def tail_data(first, video, audio, canvas, sampling_plan, manifest, request=None):
    chain = ({key: request[key] for key in ('segment', 'frame_origin', 'audio_origin')} if request
             else dict(segment=1, frame_origin=0, audio_origin=0))
    start = audio_origin(chain['frame_origin'] + canvas['frames'] - OVERLAP_FRAMES) - chain['audio_origin']
    return dict(video_lo=first, video_hi=tail(video), audio=audio[:, :, start:].detach().cpu().clone(),
                metadata=dict(identity=identity(manifest), geometry={key: canvas[key] for key in ('width', 'height', 'frames')},
                    first_canvas={key: sampling_plan['first'][key] for key in ('width', 'height')}, chain=chain,
                    **({'source_id': request['source_id']} if request and 'source_id' in request else {})))


def capture_audio_lead_in(audio, request, sample_rate):
    """Copy only the seam context from decoded channel-first audio, before trimming."""
    if request is None or sample_rate != LEAD_IN_RATE:
        return None
    ticks = request.get('audio_latents')
    if type(ticks) is not int or ticks < LEAD_IN_TICKS:
        return None
    end = ticks * 800
    if audio.ndim != 2 or audio.shape[0] != 2 or audio.shape[1] < end + LEAD_IN_FADE:
        return None
    import numpy as np
    value = audio[:, end - LEAD_IN_SAMPLES:end + LEAD_IN_FADE]
    if hasattr(value, 'detach'):
        value = value.detach().float().cpu().numpy()
    return np.array(value.T, dtype=np.float32, order='C', copy=True)


def trim_output(frames, audio, request):
    if request is None:
        return frames, audio
    return frames[OVERLAP_FRAMES:], audio[:, request['audio_latents'] * 800:]


class Prefix:
    """Own one pass's small context; no changes to the sampler or its RNG.

    The pre-hook runs before ScheduleCursor and before offload hooks. The
    existing step callback restores the in-place video buffer after Euler's
    update. Audio is either kept clean or re-noised at each call, and restored
    on return. Only re-noising owns an independent RNG.
    """
    def __init__(self, transformer, data, seed, device, *, video_offset=0):
        import torch
        from diffusers.modular_pipelines.minimax_h3.before_denoise import patchify_video_latents
        self.audio_context = validate_audio_context(data.get('audio_context', AUDIO_CONTEXT_DEFAULT))
        if type(video_offset) is not int or video_offset < 0:
            raise ValueError('Invalid continuation reference row offset')
        self.video_offset = video_offset
        self.video = data['video'].to(device=device, dtype=torch.float32)
        self.audio = data['audio'].to(device=device, dtype=torch.float32)
        if (self.video.ndim != 5 or self.video.shape[:3] != (1, transformer.config.in_channels, LATENT_FRAMES)
                or self.audio.ndim != 3 or self.audio.shape[:2] != (2, 32)):
            raise ValueError(MISSING)
        self.video_rows = patchify_video_latents(self.video, tuple(transformer.config.patch_size))
        self.audio_rows = self.audio.permute(0, 2, 1).reshape(-1, 32).contiguous()
        if self.audio_context == 'renoise':
            generator = torch.Generator(device).manual_seed((seed + 1) % (1 << 64))
            self.noise = torch.randn(self.audio_rows.shape, generator=generator, device=device, dtype=torch.float32)
            from diffusers import MiniMaxH3Scheduler
            self.scheduler = MiniMaxH3Scheduler(shift=3.)
        self.current_video = None

    def before(self, model, args, kwargs):
        import torch
        video, audio = kwargs['hidden_states'][0], kwargs['audio_hidden_states'][0]
        count, ticks = self.video_rows.shape[0], self.audio.shape[2]
        if video.shape[0] <= self.video_offset + count or audio.shape[0] // 2 < ticks:
            raise ValueError(MISSING)
        self.current_video = video
        self.restore_video()
        audio_rows = torch.cat([torch.arange(ticks, device=audio.device) + channel * (audio.shape[0] // 2)
                                for channel in range(2)])
        row_times = kwargs['timestep'][kwargs['timestep_indices']].clone()
        if self.audio_context == 'clean':
            audio[audio_rows] = self.audio_rows
            row_times[kwargs['audio_indices'][audio_rows]] = CLEAN_T
        else:
            audio_t = row_times[kwargs['audio_indices'][0]]
            # scale_noise uses H3's clean-at-one convention, not a standard DDPM clock.
            audio[audio_rows] = self.scheduler.scale_noise(self.audio_rows, audio_t, self.noise)
        row_times[kwargs['video_indices'][self.video_offset:self.video_offset + count]] = CLEAN_T
        kwargs['timestep'], kwargs['timestep_indices'] = torch.unique(row_times, sorted=True, return_inverse=True)
        return args, kwargs

    def restore_video(self):
        if self.current_video is not None:
            self.current_video[self.video_offset:self.video_offset + self.video_rows.shape[0]] = self.video_rows

    def finish(self, video, audio):
        video[:, :, :LATENT_FRAMES] = self.video
        audio[:, :, :self.audio.shape[2]] = self.audio
        return video, audio


@contextmanager
def sampling(transformer, data, seed, device, *, video_offset=0):
    if data is None:
        yield None
        return
    # Keep the legacy constructor call identical for existing continuations.
    prefix = Prefix(transformer, data, seed, device, **({'video_offset': video_offset} if video_offset else {}))
    handle = transformer.register_forward_pre_hook(prefix.before, with_kwargs=True, prepend=True)
    try:
        yield prefix
    finally:
        handle.remove()
        prefix.current_video = None
