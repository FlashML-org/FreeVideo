"""Upscaling a finished 24 fps video to the 4K size with a small frame-wise model.

realesr-general-x4v3 (Real-ESRGAN's SRVGGNetCompact network, BSD-3-Clause, (c) 2021 Xintao Wang) makes each frame four
times larger; the result is scaled to the 4K size and written with the video's own sound copied packet for packet. The
model is a 4.9 MB optional download (frame_upscale_model.json). Torch lives only in a disposable worker, as for depth
references: the bridge supervises it, passes its progress on and stops it on cancel.

The 4K size (Iris, 11-18): the long side is 3840 and the other side follows the picture's shape on an 8-pixel grid;
a picture over 36864 16x16 macroblocks (the usual H.264 decoding limit, 4096 x 2304) is scaled down in proportion
until it fits, so a square becomes 3072 x 3072 and 4:3 becomes 3544 x 2656.
"""
from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

from . import processes

STAGE = 'frame_upscale'
LONG_SIDE = 3840
MAX_MACROBLOCKS = 36864
SCALE = 4
# Input tiles overlap by the network's reach (34 3x3 convolutions): the same pixels as one whole frame, with about
# 0.5 GiB of GPU memory at any input size.
TILE, PAD = 512, 34
# GPU memory the worker needs; below it the bridge first frees idle models.
NEEDED_GPU_BYTES = 3 * 2**30 // 2
NOT_A_WORK = 'Choose a finished FreeVideo video to upscale'


class UpscaleError(RuntimeError):
    """code: 'upscale_download' (the model could not be fetched), 'upscale_disk' (no room for the result) or
    'upscale_memory' (not enough memory)."""
    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code


def catalog():
    return json.loads(Path(__file__).with_name('frame_upscale_model.json').read_text(encoding='utf-8'))


def _grid(value):
    # Half up, as Math.round in upscale_offer.js (Python's round would go to even).
    return int(math.floor(value / 8 + .5)) * 8


def size(width, height):
    """The 4K size for a width x height picture, or None when that would not make it larger."""
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        return None
    longest = max(width, height)
    for side in range(LONG_SIDE, 7, -8):
        target = _grid(width * side / longest), _grid(height * side / longest)
        if min(target) >= 8 and math.ceil(target[0] / 16) * math.ceil(target[1] / 16) <= MAX_MACROBLOCKS:
            break
    else:
        return None
    return target if max(target) > longest else None


def work_request(output_directory, relative):
    """A finished, non-preview 24 fps FreeVideo work in this output folder that the 4K size makes larger:
    (video, saved state)."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(NOT_A_WORK)
    output_root = Path(output_directory).resolve()
    video = (output_root / relative).resolve()
    try:
        video.relative_to(output_root / 'FreeVideo')
    except ValueError:
        raise ValueError(NOT_A_WORK) from None
    path = video.parent / 'comfy-request.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        state = {}
    geometry = state.get('geometry') if isinstance(state, dict) else None
    if (not isinstance(state, dict) or state.get('status') != 'complete' or state.get('preview')
            or Path(state.get('output', '')).resolve() != video or not isinstance(geometry, dict)
            or geometry.get('fps') != 24 or size(geometry.get('width'), geometry.get('height')) is None
            or not video.is_file()):
        raise ValueError(NOT_A_WORK)
    return video, state


def inspect(path):
    """One 24 fps picture stream (and any sound, copied as is): its size and frame count."""
    import av
    try:
        with av.open(str(path)) as container:
            if len(container.streams.video) != 1:
                raise ValueError(NOT_A_WORK)
            stream = container.streams.video[0]
            rate = stream.average_rate or stream.guessed_rate
            width, height = stream.codec_context.width, stream.codec_context.height
            frames = sum(1 for packet in container.demux(stream) if packet.size)
            sound = bool(container.streams.audio)
    except (av.error.FFmpegError, OSError) as error:
        raise ValueError(NOT_A_WORK) from error
    if rate is None or Fraction(rate) != 24 or frames <= 0 or size(width, height) is None:
        raise ValueError(NOT_A_WORK)
    return dict(width=width, height=height, frames=frames, sound=sound)


def needed_bytes(frames):
    # 4K at CRF 18 measured about 0.1 MB a frame; four times that, and room for the file system.
    return frames * 400_000 + 256 * 2**20


def _digest(path, check=lambda: None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            check()
            value.update(block)
    return value.hexdigest()


@contextmanager
def _lock(path, check, *, inherit=False):
    from .locking import runtime_lock
    while True:
        check()
        try:
            lease = runtime_lock(path, inherit=inherit)
            lease.__enter__()
            break
        except BlockingIOError:
            time.sleep(.1)
    try:
        yield
    finally:
        lease.__exit__(None, None, None)


def remote(spec, row):
    """Every source for the weights, the ones reachable from China first (Parker 11-18). None of them is the
    authors' own model hub: each copy is pinned and accepted only with the catalog's size and SHA-256."""
    from .network import source_url
    hf = '/%s/resolve/%s/%s' % (spec['repo'], spec['revision'], row['file'])
    mirror = spec['modelscope']
    return dict(repo=spec['repo'], revision=spec['revision'], file=row['file'], family='image', sources=[
        dict(id='modelscope', url=source_url('image-models', 'modelscope') + '/models/%s/resolve/%s/%s' % (
            mirror['repo'], mirror['revision'], row['file'])),
        dict(id='hf-mirror', url=source_url('models', 'hf-mirror') + hf),
        dict(id='github', url=spec['github']),
        dict(id='official', url=source_url('models', 'official') + hf)])


def ensure_weights(root, emit, check):
    """The 4.9 MB weights in models/frame-upscale, fetched with the same transfer and source settings as other
    optional models; a wrong size or SHA-256 is refused."""
    from . import network
    from .adaln_assets import _download_plan
    spec = catalog()
    row = spec['files'][0]
    folder = Path(root) / 'models' / 'frame-upscale'
    path = folder / row['file']
    folder.mkdir(parents=True, exist_ok=True)
    with _lock(folder / 'download.lock', check):
        if path.is_file() and path.stat().st_size == row['bytes'] and _digest(path, check) == row['sha256']:
            return path
        if path.exists():
            network.retain_partial(path, 'rejected')
        plan = _download_plan(Path(root))
        sources = plan['sources'] = dict(plan.get('sources', {}))
        if not sources.get('image-models'):
            sources['image-models'] = [{'id': 'modelscope'}, {'id': 'hf-mirror'}, {'id': 'github'}, {'id': 'official'}]
        plan.update(quiet=True, resource_check=check)

        def moved(current, total, speed, **_):
            emit(stage=STAGE, label='Downloading the upscale model', done=current, total=row['bytes'], unit='bytes',
                 bytes_per_second=speed, downloading=True)
        check()
        try:
            network.download(network.model_urls(plan, remote(spec, row)), path, row['sha256'], moved,
                             network=plan, size=row['bytes'], category='image-models', keep_partial=True,
                             stall_seconds=30, slow_seconds=15, low_speed_limit=16 * 1024)
        except Exception as error:
            from .sampling_assets import _cancel_or_disk
            if _cancel_or_disk(error):
                raise
            check()
            raise UpscaleError('upscale_download', 'The 4K upscale model could not be downloaded. Check the connection or '
                               'choose another download source in Settings, then try again. Or put the official '
                               'realesr-general-x4v3.pth (%d bytes, SHA-256 %s) in %s' % (row['bytes'], row['sha256'], folder)) from error
        if path.stat().st_size != row['bytes'] or _digest(path, check) != row['sha256']:
            raise UpscaleError('upscale_download', 'The 4K upscale model failed its size/SHA-256 check: ' + str(path))
        return path


def network_model():
    """SRVGGNetCompact as in Real-ESRGAN (realesrgan/archs/srvgg_arch.py at the catalog's source_commit),
    inference only: 64 features, 32 convolutions with PReLU, x4 by pixel shuffle over a nearest-neighbour base."""
    import torch
    from torch import nn
    from torch.nn import functional

    class SRVGGNetCompact(nn.Module):
        def __init__(self, num_feat=64, num_conv=32, upscale=SCALE):
            super().__init__()
            self.upscale = upscale
            body = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            for _ in range(num_conv):
                body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            body.append(nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1))
            self.body = nn.ModuleList(body)
            self.upsampler = nn.PixelShuffle(upscale)

        def forward(self, x):
            out = x
            for layer in self.body:
                out = layer(out)
            return self.upsampler(out) + functional.interpolate(x, scale_factor=self.upscale, mode='nearest')

    return SRVGGNetCompact()


def load(weights, device):
    import torch
    model = network_model()
    model.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True)['params'], strict=True)
    model = model.eval().to(device)
    return model.half() if device != 'cpu' else model


def enlarge(model, picture, scale=SCALE, tile=TILE, pad=PAD):
    """picture: 1x3xHxW in [0, 1]; tiles of tile x tile with pad pixels of overlap, joined without seams."""
    import torch
    _, _, height, width = picture.shape
    if height <= tile and width <= tile:
        return model(picture)
    out = torch.empty((1, 3, height * scale, width * scale), dtype=picture.dtype, device=picture.device)
    for top in range(0, height, tile):
        for left in range(0, width, tile):
            bottom, right = min(top + tile, height), min(left + tile, width)
            y0, x0, y1, x1 = max(top - pad, 0), max(left - pad, 0), min(bottom + pad, height), min(right + pad, width)
            piece = model(picture[:, :, y0:y1, x0:x1])
            oy, ox = (top - y0) * scale, (left - x0) * scale
            out[:, :, top * scale:bottom * scale, left * scale:right * scale] = \
                piece[:, :, oy:oy + (bottom - top) * scale, ox:ox + (right - left) * scale]
    return out


def write(source, target, video, frame, emit, check):
    """Read the 24 fps source frame by frame, write frame(rgb) at the 4K size with libx264, copy its sound packets
    unchanged and interleaved. Memory: one source and one output frame."""
    import av
    import numpy as np
    width, height = size(video['width'], video['height'])
    with av.open(str(source)) as reader, av.open(str(source)) as sound_reader:
        stream = reader.streams.video[0]
        sound = sound_reader.streams.audio[0] if sound_reader.streams.audio else None
        with av.open(str(target), 'w', format='mp4',
                     options={'movflags': 'use_metadata_tags+faststart', 'movie_timescale': '48000'}) as output:
            picture_stream = output.add_stream('libx264', rate=24)
            picture_stream.width, picture_stream.height, picture_stream.pix_fmt = width, height, 'yuv420p'
            picture_stream.options = {'crf': '18', 'preset': 'fast'}
            audio = output.add_stream_from_template(sound) if sound is not None else None
            packets = sound_reader.demux(sound) if sound is not None else iter(())
            pending = None

            def sound_until(seconds):
                nonlocal pending
                while True:
                    if pending is None:
                        pending = next(packets, None)
                        if pending is None:
                            return
                        if pending.dts is None:
                            pending = None
                            continue
                    if seconds is not None and float(pending.dts * pending.time_base) > seconds:
                        return
                    pending.stream = audio
                    output.mux(pending)
                    pending = None

            count = 0
            for decoded in reader.decode(stream):
                check()
                if count >= video['frames']:
                    raise ValueError('The source video changed while it was being upscaled')
                pixels = frame(decoded.to_ndarray(format='rgb24'), width, height)
                if pixels.shape != (height, width, 3):
                    raise ValueError('The upscale model returned a picture of the wrong size')
                picture = av.VideoFrame.from_ndarray(np.ascontiguousarray(pixels), format='rgb24')
                picture.pts, picture.time_base = count, Fraction(1, 24)
                sound_until(count / 24)
                for packet in picture_stream.encode(picture):
                    output.mux(packet)
                count += 1
                emit(done=count, total=video['frames'], unit='frames', downloading=False)
            for packet in picture_stream.encode(None):
                check()
                output.mux(packet)
            sound_until(None)
    if count != video['frames']:
        raise ValueError('The source video changed while it was being upscaled')
    return count


def model_frame(weights, device):
    """frame(rgb uint8 HxWx3, width, height) -> uint8 at width x height, by the model in this process."""
    import torch
    from torch.nn import functional
    model = load(weights, device)
    dtype = torch.float16 if device != 'cpu' else torch.float32

    @torch.inference_mode()
    def frame(rgb, width, height):
        try:
            picture = torch.from_numpy(rgb).to(device).permute(2, 0, 1)[None].to(dtype).div_(255)
            larger = enlarge(model, picture)
            fitted = functional.interpolate(larger.float(), size=(height, width), mode='bicubic', antialias=True,
                                            align_corners=False)
            return fitted.clamp_(0, 1).mul_(255).round_().to(torch.uint8)[0].permute(1, 2, 0).contiguous().cpu().numpy()
        except torch.OutOfMemoryError as error:
            raise UpscaleError('upscale_memory', 'Not enough GPU memory to upscale to 4K. Close other programs that use '
                               'the GPU, then try again') from error
    return frame


def _convert(request, emit, check):
    from .disk_space import free_bytes
    source, root, work, target = Path(request['source']), Path(request['root']), Path(request['work']), Path(request['target'])
    device = request.get('device', 'cuda')
    video = inspect(source)
    if free_bytes(target.parent) < needed_bytes(video['frames']):
        raise UpscaleError('upscale_disk', 'Not enough disk space to save the 4K video (about %d MB is needed). Free some '
                           'space on the drive that holds the output folder, then try again'
                           % (needed_bytes(video['frames']) // 10**6))
    weights = ensure_weights(root, emit, check)
    import torch
    if device == 'cuda':
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable for the 4K upscale')
        # Models other requests keep in the GPU stay unless this small one does not fit beside them.
        if torch.cuda.mem_get_info()[0] < NEEDED_GPU_BYTES:
            emit(stage=STAGE, label='Upscaling to 4K', waiting_for_device=True)
            while not (work / 'device-ready').is_file():
                check()
                time.sleep(.05)
    elif device == 'mps' and not torch.backends.mps.is_available():
        raise RuntimeError('Metal is unavailable for the 4K upscale')
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    emit(stage=STAGE, label='Upscaling to 4K', done=0, total=video['frames'], unit='frames', waiting_for_device=False)
    frame = model_frame(weights, device)
    started = time.monotonic()
    if device == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    frames = write(source, target, video, frame, emit, check)
    return dict(frames=frames, width=size(video['width'], video['height'])[0], height=size(video['width'], video['height'])[1],
                seconds=round(time.monotonic() - started, 2), device=device, model='realesr-general-x4v3',
                weights_sha256=catalog()['files'][0]['sha256'], tile=TILE, sound=video['sound'],
                torch_peak_reserved_bytes=torch.cuda.max_memory_reserved() if device == 'cuda' else None,
                torch=torch.__version__)


def upscale(source, target, *, root, device='cuda', python=None, environ=None, progress=None, interrupted=None,
            release_models=None):
    """Upscale in a supervised child, writing target; returns the child's summary.

    progress receives {stage, label, done, total, unit}: bytes while the model downloads, then frames. Cancelling
    stops the whole child tree; a partly written target is removed.
    """
    root, target = Path(root).resolve(), Path(target)

    def check():
        if interrupted:
            interrupted()
    check()
    with tempfile.TemporaryDirectory(prefix='.frame-upscale-', dir=target.parent) as directory:
        work = Path(directory)
        request = dict(source=str(Path(source).resolve()), root=str(root), work=str(work), target=str(target), device=device)
        (work / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        command = processes.module_command('freevideo_engine.frame_upscale', '--request', work / 'request.json')
        if python is not None:
            command[0] = str(python)
        with (work / 'worker.log').open('wb') as log:
            child = processes.popen(command, env=environ, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, supervise=True, pass_fds=processes.descriptors())
            last, released = None, False

            def poll():
                nonlocal last, released
                check()
                try:
                    value = json.loads((work / 'progress.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    return
                if value.get('waiting_for_device') and not released:
                    if release_models:
                        release_models()
                    check()
                    (work / 'device-ready').touch()
                    released = True
                if progress and value != last:
                    progress({key: val for key, val in value.items() if key != 'waiting_for_device'})
                last = value
            try:
                while child.poll() is None:
                    poll()
                    try:
                        child.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
                poll()
                if child.returncode:
                    try:
                        error = json.loads((work / 'error.json').read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        error = {}
                    message = error.get('error') or 'The 4K upscale worker exited with code %s' % child.returncode
                    if error.get('code'):
                        raise UpscaleError(error['code'], message)
                    raise RuntimeError(message)
                return json.loads((work / 'result.json').read_text(encoding='utf-8'))
            except BaseException:
                (work / 'cancelled').touch()
                processes.stop(child)
                target.unlink(missing_ok=True)
                raise


def main():
    import argparse
    from .monitoring import save
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding='utf-8'))
    work = Path(request['work'])

    def check():
        if (work / 'cancelled').exists():
            raise InterruptedError('4K upscale cancelled')
    state = dict(stage=STAGE, phase=STAGE, label='Upscaling to 4K', done=0, total=None, unit='frames')

    def emit(**values):
        state.update(values)
        save(work / 'progress.json', state)
    try:
        with processes.worker_signals():
            result = _convert(request, emit, check)
        save(work / 'result.json', result)
    except Exception as error:
        save(work / 'error.json', dict(error=str(error) if isinstance(error, UpscaleError) else type(error).__name__ + ': ' + str(error),
                                       code=getattr(error, 'code', None)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
