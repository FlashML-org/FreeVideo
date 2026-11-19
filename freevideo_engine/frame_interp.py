"""Export at 48 or 60 fps: frames between the 24 fps frames are made by RIFE 4.25, in a disposable worker.

The editor renders its 24 fps picture first (near-lossless); this worker reads it, writes the faster picture at the
user's quality and copies the sound unchanged. Nothing is made across a cut: there the nearer frame is repeated, and two
equal frames (a still) are copied without the model. Torch lives only in the worker.
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
from .paths import data_root

STAGE = 'frame_interp'
SOURCE_FPS = 24
# Optical flow at half scale above 1080p: as sharp in the measured clips, and less memory.
FULL_SCALE_LONG_SIDE = 1920


class InterpolationError(RuntimeError):
    """code: 'interp_download' (the model could not be fetched) or 'interp_memory' (not enough video memory)."""
    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code


def catalog():
    return json.loads(Path(__file__).with_name('rife_interp_model.json').read_text(encoding='utf-8'))


def output_frames(frames, fps):
    return -(-frames * fps // SOURCE_FPS)


def plan(frames, fps, cuts=()):
    """For every output frame: (left, right, t). right is None where the left frame is copied as it is.

    Output frame j shows time j / fps, that is source position p = j * 24 / fps; t is its fraction.
    cuts holds the source frame numbers that start a new shot: no frame is made between cut - 1 and cut.
    """
    cuts = set(cuts)
    rows = []
    for j in range(output_frames(frames, fps)):
        position = Fraction(j * SOURCE_FPS, fps)
        left, t = math.floor(position), position - math.floor(position)
        right = left + 1
        if t == 0 or right >= frames:
            rows.append((min(left, frames - 1), None, 0.))
        elif right in cuts:
            rows.append((left, None, 0.) if t < Fraction(1, 2) else (right, None, 0.))
        else:
            rows.append((left, right, float(t)))
    return rows


def cuts_of(clips, start, frames):
    """Source frames (relative to the exported range) where the v1 picture changes to another clip or to black."""
    edges = set()
    for clip in clips:
        for edge in (clip['start'], clip['start'] + clip['frames']):
            if start < edge < start + frames:
                edges.add(edge - start)
    return sorted(edges)


def _digest(path, check=lambda: None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            check()
            value.update(block)
    return value.hexdigest()


@contextmanager
def _lock(path, check):
    from .locking import runtime_lock
    while True:
        check()
        try:
            lease = runtime_lock(path)
            lease.__enter__()
            break
        except BlockingIOError:
            time.sleep(.1)
    try:
        yield
    finally:
        lease.__exit__(None, None, None)


def weights_folder(root):
    return Path(root) / 'models' / 'rife'


def ensure_weights(root, emit, check):
    """The same optional-model catalog, pins, resume and mainland fallback as the depth reference model."""
    from . import network
    from .adaln_assets import _download_plan
    from .prompt_vlm.assets import remote
    spec = catalog()
    row = spec['files'][0]
    folder = weights_folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(row['file']).name
    with _lock(folder / 'download.lock', check):
        if path.is_file() and path.stat().st_size == row['bytes'] and _digest(path, check) == row['sha256']:
            return path
        if path.exists():
            network.retain_partial(path, 'rejected')
        plan_ = _download_plan(Path(root))
        sources = plan_['sources'] = dict(plan_.get('sources', {}))
        if not sources.get('image-models'):
            sources['image-models'] = [dict(item) for item in sources.get('models', [])
                                       if item['id'] in ('official', 'hf-mirror')]
            sources['image-models'].append({'id': 'modelscope'})
        plan_.update(quiet=True, resource_check=check)

        def moved(current, total, speed, **_):
            emit(stage='frame_interp_download', done=current, total=row['bytes'], unit='bytes', bytes_per_second=speed)
        check()
        try:
            network.download(network.model_urls(plan_, remote(spec, row)), path, row['sha256'], moved,
                             network=plan_, size=row['bytes'], category='image-models', keep_partial=True,
                             stall_seconds=30, slow_seconds=15, low_speed_limit=64 * 1024)
        except Exception as error:
            from .sampling_assets import _cancel_or_disk
            if _cancel_or_disk(error):
                raise
            check()
            raise InterpolationError('interp_download', 'Frame interpolation weights are unavailable. Retry with a '
                                     'connection, or put rife_v4.25.safetensors (22,674,688 bytes, SHA-256 '
                                     + row['sha256'] + ') in ' + str(folder)) from error
        if path.stat().st_size != row['bytes'] or _digest(path, check) != row['sha256']:
            raise InterpolationError('interp_download', 'Frame interpolation weight size/SHA-256 verification failed')
    return path


def write(source, target, *, fps, video_options, cuts, between, emit, check):
    """Read the 24 fps source, write `fps` frames per second to target, copy its sound packets unchanged.

    between(a, b, t) returns the RGB frame at fraction t from a to b (uint8 arrays). Memory: two source frames.
    """
    import av
    import numpy as np
    with av.open(str(source)) as reader, av.open(str(source)) as sound_reader:
        stream = reader.streams.video[0]
        frames = stream.frames or None
        if not frames:
            with av.open(str(source)) as counter:
                frames = sum(1 for _ in counter.decode(video=0))
        rows = plan(frames, fps, cuts)
        sound = sound_reader.streams.audio[0] if sound_reader.streams.audio else None
        with av.open(str(target), 'w', format='mp4',
                     options={'movflags': 'use_metadata_tags+faststart', 'movie_timescale': '48000'}) as output:
            video = output.add_stream('libx264', rate=fps)
            video.width, video.height, video.pix_fmt = stream.codec_context.width, stream.codec_context.height, 'yuv420p'
            video.options = dict(video_options)
            video.codec_context.thread_count = 4
            audio = output.add_stream_from_template(sound) if sound is not None else None
            packets = sound_reader.demux(sound) if sound is not None else iter(())
            pending = None

            def sound_until(seconds):
                # Interleave sound with the picture so a player never has to read far ahead.
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

            decoded = reader.decode(stream)
            window = {}
            next_number = 0

            def frame(number):
                nonlocal next_number
                while number not in window:
                    value = next(decoded, None)
                    if value is None:
                        # A short stream: hold its last frame.
                        window[number] = window[max(window)]
                        break
                    window[next_number] = value.to_ndarray(format='rgb24')
                    next_number += 1
                for old in [key for key in window if key < number - 1]:
                    del window[old]
                return window[number]

            for j, (left, right, t) in enumerate(rows):
                check()
                a = frame(left)
                if right is None:
                    pixels = a
                else:
                    b = frame(right)
                    pixels = a if np.array_equal(a, b) else between(a, b, t)
                picture = av.VideoFrame.from_ndarray(np.ascontiguousarray(pixels), format='rgb24')
                picture.pts, picture.time_base = j, Fraction(1, fps)
                sound_until(j / fps)
                for packet in video.encode(picture):
                    output.mux(packet)
                emit(done=j + 1, total=len(rows))
            for packet in video.encode(None):
                check()
                output.mux(packet)
            sound_until(None)
    return len(rows)


def model_between(weights, device):
    """RIFE in this process: returns between(a, b, t) and drops to half-scale flow once if memory runs out."""
    import numpy as np
    import torch
    from safetensors.torch import load_file
    from .rife import IFNet, interpolate
    net = IFNet()
    net.load_state_dict(load_file(str(weights)), strict=True)
    net = net.eval().to(device)
    state = dict(scale=None)

    def tensor(image):
        return torch.from_numpy(image).to(device).permute(2, 0, 1)[None].float().div_(255.)

    def between(a, b, t):
        if state['scale'] is None:
            state['scale'] = 1.0 if max(a.shape[:2]) <= FULL_SCALE_LONG_SIDE else .5
        while True:
            try:
                with torch.inference_mode():
                    out = interpolate(net, tensor(a), tensor(b), t, state['scale'])
                    return (out[0].permute(1, 2, 0).mul_(255.).add_(.5).clamp_(0, 255).to(torch.uint8).cpu().numpy())
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if state['scale'] <= .5:
                    raise InterpolationError('interp_memory', 'Not enough video memory for frame interpolation') from None
                state['scale'] = .5
    return between


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
            raise InterruptedError('Frame interpolation cancelled')
    state = dict(stage=STAGE, done=0, total=None, unit='frames')

    def emit(**values):
        state.update(values)
        save(work / 'progress.json', state)
    try:
        with processes.worker_signals():
            weights = ensure_weights(request['root'], emit, check)
            emit(stage=STAGE, done=0, total=None, unit='frames')
            between = model_between(weights, request.get('device', 'cuda'))
            write(request['source'], request['target'], fps=request['fps'], video_options=request['video_options'],
                  cuts=request['cuts'], between=between, emit=emit, check=check)
    except Exception as error:
        from .disk_space import ran_out
        try:
            save(work / 'error.json', dict(error=type(error).__name__ + ': ' + str(error),
                                           code='disk_full' if ran_out(error) else getattr(error, 'code', None)))
        except OSError:
            pass  # A full disk may not take this either; the parent then looks at the free space itself.
        return 1
    return 0


def release_gpu(required=3 * 2 ** 30):
    """Inside ComfyUI, let go of only as much video memory as the worker needs; other models stay loaded."""
    try:
        import comfy.model_management as memory
    except ImportError:
        return
    try:
        device = memory.get_torch_device()
        memory.free_memory(required, device)
        memory.soft_empty_cache()
    except Exception:
        pass


def _nearly_full(folder, margin=16 * 2 ** 20):
    from .disk_space import free_bytes
    try:
        return free_bytes(folder) < margin
    except (OSError, ValueError):
        return False


def convert(source, target, *, fps, video_options, cuts, root=None, python=None, environ=None,
            progress=None, cancelled=None, release=release_gpu):
    """Run the worker; target exists only after it exits cleanly. progress gets done/total output frames."""
    root = Path(root or data_root()).resolve()
    target = Path(target)

    def check():
        if cancelled and cancelled():
            from .editor_export import ExportCancelled
            raise ExportCancelled()
    check()
    if release:
        release()
    with tempfile.TemporaryDirectory(prefix='.interp-', dir=target.parent) as directory:
        work = Path(directory)
        request = dict(source=str(Path(source).resolve()), target=str(target.resolve()), fps=int(fps),
                       video_options=dict(video_options), cuts=[int(c) for c in cuts], root=str(root),
                       device='cuda', work=str(work))
        (work / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        command = processes.module_command('freevideo_engine.frame_interp', '--request', work / 'request.json')
        if python is not None:
            command[0] = str(python)
        with (work / 'worker.log').open('wb') as log:
            child = processes.popen(command, env=environ, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, supervise=True, pass_fds=processes.descriptors())
            last = None

            def poll():
                nonlocal last
                check()
                try:
                    value = json.loads((work / 'progress.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    return
                if progress and value != last:
                    progress(value)
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
                    if not error and _nearly_full(target.parent):
                        error = dict(code='disk_full')
                    if error.get('code') in ('interp_download', 'interp_memory', 'disk_full'):
                        raise InterpolationError(error['code'], error.get('error', ''))
                    raise RuntimeError(error.get('error') or 'Frame interpolation worker exited with code %s'
                                       % child.returncode)
                check()
                return target
            except BaseException:
                (work / 'cancelled').touch()
                processes.stop(child)
                Path(target).unlink(missing_ok=True)
                raise


if __name__ == '__main__':
    raise SystemExit(main())
