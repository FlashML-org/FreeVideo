"""Coarse frame previews of the video being sampled.

At each step the scheduler already holds the model's prediction of the clean
latents. A fixed per-pixel affine map from the 24 latent channels to RGB turns
up to sixteen evenly spaced latent frames into one small strip without running
the VAE, which the page plays as a loop. The
observer only reads tensors the sampler already produced and never writes to
them, so the sampled latents are identical with it on or off. Any failure
silently turns it off for the rest of the request.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path

WEIGHTS = Path(__file__).with_name('frame_preview_weights.json')
FILE = 'frame-preview.jpg'
MARKER = 'frame-preview-off'
ENV = 'FREEVIDEO_FRAME_PREVIEW'
FIRST_STEP = 3
FRAMES = 16  # evenly spaced latent frames in the strip; the page guards use the same bound
QUALITY = 82
MAX_FRAME_WIDTH = 336
CHANNELS = 24
ROW_WIDTH = CHANNELS * 4  # 24 channels x a (1, 2, 2) patch

_sink = None
_active = None
_weights = None
_decoder_bytes = 0   # GPU bytes the plan set aside for the small decoder; 0 keeps the per-pixel map
_marker = None  # request-level file: a failure in one process keeps the request's later processes off
_failed = False  # request-level: one failure turns previews off for every later pass
_serial = 0      # request-level: numbers pictures across passes so the page never mistakes a new one for the old


def disabled(environ=None):
    value = (os.environ if environ is None else environ).get(ENV, '')
    return value.strip().lower() in ('0', 'off', 'false', 'no')


def load_weights(path=WEIGHTS):
    """(k, weight[24, 3k^2], bias[3k^2]) as lists; ValueError when malformed."""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    k, weight, bias = value['k'], value['weight'], value['bias']
    outputs = 3 * k * k
    if (value.get('version') != 1 or type(k) is not int or not 1 <= k <= 8 or value.get('channels') != CHANNELS
            or len(weight) != CHANNELS or any(len(row) != outputs for row in weight) or len(bias) != outputs):
        raise ValueError('Unsupported frame preview weights')
    return k, weight, bias


def configure(directory, emit=None, *, weights=None, request_state=None, decoder_bytes=0):
    """Turn previews on for this process; returns whether they are active.

    `directory` receives the picture; `emit(event)` reports each new one.
    `request_state` is a per-request directory shared by the request's worker
    processes: a failure leaves a marker there, so a later process of the same
    request stays off. `decoder_bytes` is the GPU memory the plan reserved for
    the small decoder; without it the per-pixel map draws every picture.
    """
    global _sink, _weights, _failed, _serial, _marker, _decoder_bytes
    _sink = _weights = None
    _decoder_bytes = decoder_bytes if type(decoder_bytes) is int and decoder_bytes > 0 else 0
    _failed, _serial = False, 0
    _marker = None if request_state is None else Path(request_state) / MARKER
    if disabled():
        return False
    if _marker is not None and _marker.exists():
        _failed = True
        return False
    try:
        k, weight, bias = load_weights(WEIGHTS if weights is None else weights)
        if emit is None:
            from .sampling_progress import emit_event as emit
        _weights = (k, weight, bias)
        _sink = (Path(directory), emit)
    except Exception:
        _sink = _weights = None
    return _sink is not None


def _remove(directory):
    for name in (FILE, FILE + '.tmp'):
        try:
            (directory / name).unlink()
        except OSError:
            pass


def clear():
    """Forget the sink and remove the last picture."""
    global _sink, _weights
    sink, _sink, _weights = _sink, None, None
    if sink is not None:
        _remove(sink[0])


def _fail():
    """Previews stay off for the rest of the request: drop the picture and tell the page to do the same."""
    global _sink, _weights, _failed
    sink, _sink, _weights, _failed = _sink, None, None, True
    if _marker is not None:
        try:
            _marker.write_text('off\n', encoding='ascii')
        except OSError:
            pass
    if sink is None:
        return
    _remove(sink[0])
    try:
        sink[1](dict(event='frame_preview_clear'))
    except Exception:
        pass


class _Preview:
    def __init__(self, latent_frames, frame_h, frame_w, step_offset, total, seconds=None):
        self.rows = latent_frames * frame_h * frame_w
        self.shape = (latent_frames, frame_h, frame_w)
        self.step_offset, self.total = step_offset, total
        self.duration_ms = round(seconds * 1000) if isinstance(seconds, (int, float)) and 0 < seconds < 86400 else None
        self.calls = 0
        count = min(FRAMES, latent_frames)
        self.indices = sorted({round(i * (latent_frames - 1) / max(1, count - 1)) for i in range(count)})
        self.tensors = None
        self.decoder = None

    def render(self, model_output, timestep, sample):
        """The preview strip as uint8 (height, frames * width, 3), from the predicted clean latents."""
        import torch
        t, fh, fw = self.shape
        index = torch.tensor(self.indices, device=sample.device)
        with torch.no_grad():
            seen = sample.detach().reshape(t, fh, fw, ROW_WIDTH)[index].float()
            velocity = model_output.detach().reshape(t, fh, fw, ROW_WIDTH)[index].float()
            x0 = seen + (1. - float(timestep)) * velocity
            n = len(self.indices)
            # (n, fh, fw, C, 1, 2, 2) -> (n, fh*2, fw*2, C)
            x0 = x0.reshape(n, fh, fw, CHANNELS, 2, 2).permute(0, 1, 4, 2, 5, 3).reshape(n, fh * 2, fw * 2, CHANNELS)
            out = self._small_decoder(x0, sample.device)
            if out is None:
                out = self._per_pixel(x0, n, fh, fw, sample.device)
            if out.shape[-1] > MAX_FRAME_WIDTH:
                size = (max(1, round(out.shape[-2] * MAX_FRAME_WIDTH / out.shape[-1])), MAX_FRAME_WIDTH)
                out = torch.nn.functional.interpolate(out, size=size, mode='area')
            picture = out.clamp(0., 1.).mul(255.).round().to(torch.uint8)
            return picture.permute(2, 0, 3, 1).reshape(picture.shape[2], n * picture.shape[3], 3).cpu().numpy()

    def _per_pixel(self, x0, n, fh, fw, device):
        import torch
        k, weight, bias = _weights
        if self.tensors is None or self.tensors[0].device != device:
            self.tensors = (torch.tensor(weight, dtype=torch.float32, device=device),
                            torch.tensor(bias, dtype=torch.float32, device=device))
        matrix, offset = self.tensors
        out = torch.addmm(offset, x0.reshape(-1, CHANNELS), matrix)
        out = out.reshape(n, fh * 2, fw * 2, 3, k, k).permute(0, 3, 1, 4, 2, 5)
        return out.reshape(n, 3, fh * 2 * k, fw * 2 * k)

    def _small_decoder(self, x0, device):
        """The small decoder's frames at the pass's picture size, or None for the per-pixel map.

        Any failure, a full card included, only returns this request to the
        per-pixel map; it never turns previews off.
        """
        global _decoder_bytes
        import torch
        if not _decoder_bytes or device.type != 'cuda' or torch.version.hip:
            return None
        try:
            from . import frame_preview_decoder
            if self.decoder is None or next(self.decoder.parameters()).device != device:
                self.decoder = frame_preview_decoder.load(device)
            width = min(MAX_FRAME_WIDTH, x0.shape[2] * 4)
            def finish(picture):
                if picture.shape[-1] > width:
                    picture = torch.nn.functional.interpolate(
                        picture, size=(max(1, round(picture.shape[-2] * width / picture.shape[-1])), width), mode='area')
                return picture
            return frame_preview_decoder.decode(self.decoder, x0.permute(0, 3, 1, 2).contiguous(), finish)
        except Exception:
            _decoder_bytes = 0
            self.decoder = None
            return None

    def observe(self, model_output, timestep, sample):
        global _serial
        if sample.ndim != 2 or tuple(sample.shape) != (self.rows, ROW_WIDTH):
            return
        self.calls += 1
        step = self.step_offset + self.calls
        if step < FIRST_STEP:
            return
        from PIL import Image
        directory, emit = _sink
        strip = self.render(model_output, timestep, sample)
        target = directory / FILE
        temporary = directory / (FILE + '.tmp')
        Image.fromarray(strip).save(temporary, 'JPEG', quality=QUALITY)
        os.replace(temporary, target)
        # Never repeats within a request, also when the second pass runs in another process.
        _serial = max(_serial + 1, step)
        n = len(self.indices)
        emit(dict(event='frame_preview', step=step, total=self.total, serial=_serial, frames=n,
                  frame_width=strip.shape[1] // n, frame_height=strip.shape[0],
                  **({'duration_ms': self.duration_ms} if self.duration_ms else {})))


@contextmanager
def observing(*, latent_frames, frame_h, frame_w, step_offset=0, total=None, seconds=None):
    """Observe the video scheduler of one sampling pass; no-op unless configured."""
    global _active
    previous = _active
    _active = None
    if _sink is not None and not _failed:
        try:
            _active = _Preview(latent_frames, frame_h, frame_w, step_offset, total, seconds)
        except Exception:
            _active = None
            _fail()
    try:
        yield
    finally:
        _active = previous


def observe(model_output, timestep, sample):
    """Called by the scheduler before its step; never raises, never changes tensors."""
    active = _active
    if active is None or _sink is None:
        return
    try:
        active.observe(model_output, timestep, sample)
    except Exception:
        _fail()
