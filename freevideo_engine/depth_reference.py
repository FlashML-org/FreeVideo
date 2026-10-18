"""Optional, file-backed depth references. Torch lives only in a disposable worker.

Small's vendored Apache-2.0 network and preprocessing follow Quinn's fixed
DAV2 experiment. Cache identity deliberately excludes prompt, seed and K.
"""
from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from . import processes
from .paths import data_root

PREPROCESSING_VERSION = 'dav2-small-pyav-torch-bgr-imagenet-lower-bound-14-clay-relief-v1'
CODE_VERSION = 'freevideo-depth-reference-v1'
STAGE = 'depth_reference'


def catalog():
    return json.loads(Path(__file__).with_name('depth_reference_model.json').read_text(encoding='utf-8'))


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(path, check=lambda: None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            check()
            value.update(block)
    return value.hexdigest()


def cache_directory(root=None):
    # download_cache is OS page-cache advice, not an application cache folder.
    return Path(root or data_root()) / 'input-cache' / 'depth-cache'


@contextmanager
def _lock(path, check, *, inherit=False):
    from .locking import runtime_lock
    while True:
        check()
        try:
            lease = runtime_lock(path, inherit=inherit)
            descriptor = lease.__enter__()
            break
        except BlockingIOError:
            time.sleep(.1)
    try:
        yield descriptor
    finally:
        lease.__exit__(None, None, None)


def ensure_weights(root, emit, check):
    """The same optional-model catalog/transfer mechanism as FreeToken and ASR."""
    from . import network
    from .adaln_assets import _download_plan
    from .prompt_vlm.assets import remote
    spec = catalog()
    row = spec['files'][0]
    folder = Path(root) / 'models' / 'depth-anything-v2-small'
    path = folder / row['file']
    with _lock(folder / 'download.lock', check):
        if path.is_file() and path.stat().st_size == row['bytes'] and digest(path, check) == row['sha256']:
            return path
        if path.exists():
            network.retain_partial(path, 'rejected')
        plan = _download_plan(Path(root))
        sources = plan['sources'] = dict(plan.get('sources', {}))
        if not sources.get('image-models'):
            sources['image-models'] = [dict(row) for row in sources.get('models', [])
                                       if row['id'] in ('official', 'hf-mirror')]
            sources['image-models'].append({'id': 'modelscope'})
        plan.update(quiet=True, resource_check=check)
        # remote() describes HF pins and a hash-locked ModelScope fallback;
        # network retains its measured routes and the user's connection mode.
        def moved(current, total, speed, **_):
            emit(phase='dependencies', stage='depth_reference_download', label='Downloading depth reference model',
                 done=current, total=row['bytes'], unit='bytes', bytes_per_second=speed)
        check()
        try:
            network.download(network.model_urls(plan, remote(spec, row)), path, row['sha256'], moved,
                             network=plan, size=row['bytes'], category='image-models', keep_partial=True,
                             stall_seconds=30, slow_seconds=15, low_speed_limit=64 * 1024)
        except Exception as error:
            from .sampling_assets import _cancel_or_disk
            if _cancel_or_disk(error):
                raise
            check()
            raise RuntimeError('Depth reference weights are unavailable. Retry with a connection, or put the '
                               'official depth_anything_v2_vits.pth (99,218,434 bytes, SHA-256 '
                               + row['sha256'] + ') in ' + str(folder)) from error
        if path.stat().st_size != row['bytes'] or digest(path, check) != row['sha256']:
            raise ValueError('Depth reference weight size/SHA-256 verification failed: ' + str(path))
        return path


def _rotation(frame):
    # PyAV exposes the display matrix as counterclockwise degrees, as in agent.media.
    try:
        return round(frame.rotation or 0) % 360
    except (AttributeError, ValueError):
        return 0


def _upright_bgr(frame):
    import numpy as np
    return np.ascontiguousarray(np.rot90(frame.to_ndarray(format='bgr24'), round(_rotation(frame) / 90) % 4))


def inspect_video(path, check=lambda: None, *, depth=False):
    """Decode every frame, rather than trusting container frame-count headers."""
    import av
    pts, dimensions, rotation = [], None, None
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError('Depth reference needs a decodable video')
        if depth and container.streams.audio:
            raise ValueError('Depth reference cache unexpectedly contains audio')
        stream = container.streams.video[0]
        rate = stream.average_rate
        if rate is None or rate <= 0:
            raise ValueError('Depth reference video has no valid frame rate')
        time_base = stream.time_base
        for frame in container.decode(stream):
            check()
            current_rotation = _rotation(frame)
            if rotation is not None and current_rotation != rotation:
                raise ValueError('Depth reference video changes display rotation')
            rotation = current_rotation
            size = (frame.width, frame.height)
            if dimensions is not None and dimensions != size:
                raise ValueError('Depth reference video changes dimensions')
            dimensions = size
            pts.append(frame.pts)
    if not pts:
        raise ValueError('Depth reference video has no decodable frames')
    if depth and rotation:
        raise ValueError('Depth reference cache unexpectedly contains display rotation')
    if round(rotation / 90) % 2:
        dimensions = dimensions[::-1]
    return dict(width=dimensions[0], height=dimensions[1], fps_num=rate.numerator, fps_den=rate.denominator,
                frames=len(pts), pts=pts, time_base=str(time_base), rotation=rotation)


def identity(source, video, device, check=lambda: None):
    spec = catalog()
    output = {key: video[key] for key in ('width', 'height', 'fps_num', 'fps_den', 'frames')}
    output.update(fit='source', audio=False)
    pix_fmt = 'yuv420p' if video['width'] % 2 == video['height'] % 2 == 0 else 'yuv444p'
    return dict(source_sha256=digest(source, check), source_size=Path(source).stat().st_size,
                model_id=spec['repo'], weights_sha256=spec['files'][0]['sha256'], source_commit=spec['source_commit'],
                code_version=CODE_VERSION, preprocessing_version=PREPROCESSING_VERSION,
                selection_spec=dict(source_start_pts=video['pts'][0], source_end_pts=video['pts'][-1],
                                    source_time_base=video['time_base'], crop_rect=None,
                                    rotation=video['rotation'], tail_policy='none'),
                output_spec=output,
                inference_spec=dict(input_size=518, precision='fp16' if device == 'cuda' else 'fp32',
                    normalization='whole_clip_p1_p99_from_240x136_near_white_v1',
                    render='clay-relief-v1',
                    encoder='rgb-to-'+pix_fmt+'/libx264/crf16/threads2', runtime_backend='torch-'+device))


def cache_key(config):
    return hashlib.sha256(canonical_json(config).encode('utf-8')).hexdigest()


def _validate(path, expected, check):
    observed = inspect_video(path, check, depth=True)
    if any(observed[key] != expected[key] for key in ('width', 'height', 'fps_num', 'fps_den', 'frames')):
        raise ValueError('Depth reference output dimensions, frame rate or frame count differ from the input')
    return observed


def _cached(folder, config, check):
    try:
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        output = folder / 'depth.mp4'
        if (manifest['config'] != config or manifest['output_sha256'] != digest(output, check)
                or manifest['output_bytes'] != output.stat().st_size):
            return None
        _validate(output, config['output_spec'], check)
        return output
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, InterruptedError):
            raise
        return None


def _load_model(weights, device):
    import torch
    from .depth_anything_v2.dpt import DepthAnythingV2
    model = DepthAnythingV2()
    model.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True), strict=True)
    return model.to(device).eval()


def _clay_relief(gray):
    """Render quantized, near-bright depth with Quinn's fixed shade.py recipe.

    SciPy's default Gaussian uses sigma=3, radius=12 and half-sample symmetric
    reflection (including the edge pixel). Torch's reflect pad excludes that
    pixel, so use reflected indices; this also works on frames below 13 pixels.
    """
    import numpy as np
    import torch
    from torch.nn.functional import conv2d

    d = torch.from_numpy(gray.astype(np.float32) / 255)[None, None]
    offsets = torch.arange(-12, 13, dtype=torch.float64)
    kernel = torch.exp(-0.5 * offsets.square() / 3.0**2)
    kernel = (kernel / kernel.sum()).float()
    # Match gaussian_filter's axis order and float32 intermediate images.
    for axis in (2, 3):
        size = d.shape[axis]
        indices = torch.arange(-12, size + 12) % (2 * size)
        indices = torch.where(indices < size, indices, 2 * size - 1 - indices)
        weight = kernel.reshape(1, 1, 25, 1) if axis == 2 else kernel.reshape(1, 1, 1, 25)
        d = conv2d(d.index_select(axis, indices), weight)
    d = d[0, 0].numpy()
    height, width = d.shape
    # numpy.gradient uses central differences inside and one-sided edges.
    gy = np.gradient(d, axis=0) if height > 1 else np.zeros_like(d)
    gx = np.gradient(d, axis=1) if width > 1 else np.zeros_like(d)
    k = 5.0
    nx, ny, nz = -gx*k*width/64, -gy*k*height/64, np.ones_like(d)
    n = np.sqrt(nx**2 + ny**2 + nz**2)
    nx, ny, nz = nx/n, ny/n, nz/n
    light = np.array([-0.5, -0.6, 0.62])
    light /= np.linalg.norm(light)
    lam = np.clip(nx*light[0] + ny*light[1] + nz*light[2], 0, 1)
    shade = (0.25 + 0.75*lam) * (0.75 + 0.25*d)
    base = np.array([0.82, 0.80, 0.77])
    return (np.clip(shade, 0, 1)[..., None] * base * 255).astype(np.uint8)


def _infer(source, stage, video, weights, device, emit, check):
    import av
    import numpy as np
    import psutil
    import torch
    # Reserve system RAM and account for live free memory before loading.
    if psutil.virtual_memory().available < 2 * 2**30:
        raise MemoryError('Depth reference needs 1 GiB working RAM plus 1 GiB system reserve; free memory and retry')
    if device == 'cuda':
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable for depth reference; select the CPU backend to use FP32')
        free, total = torch.cuda.mem_get_info()
        reserve = max(512 * 2**20, total // 10)
        if free < reserve + 512 * 2**20:
            raise MemoryError('Not enough free GPU memory for depth reference and the GPU reserve')
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    shape = (video['frames'], video['height'], video['width'])
    from .disk_space import free_bytes
    if free_bytes(stage) < (shape[0] * (shape[1] * shape[2] + 240 * 136) * 4 + 256 * 2**20):
        raise OSError('Not enough disk space to stage depth frames; free space and retry')
    model = _load_model(weights, device)
    raw_path, small_path = stage / 'raw.f32', stage / 'small.f32'
    depths = np.memmap(raw_path, mode='w+', dtype=np.float32, shape=shape)
    small = np.memmap(small_path, mode='w+', dtype=np.float32, shape=(video['frames'], 136, 240))
    try:
        with av.open(str(source)) as container:
            count = 0
            for frame in container.decode(video=0):
                check()
                bgr = _upright_bgr(frame)
                if (count >= video['frames'] or bgr.shape[:2] != shape[1:]
                        or _rotation(frame) != video['rotation']):
                    raise ValueError('Source video changed during depth conversion')
                if psutil.virtual_memory().available < 2**30:
                    raise MemoryError('Depth reference stopped to preserve 1 GiB of free system RAM')
                with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16, enabled=device == 'cuda'):
                    depth = np.asarray(model.infer_image(bgr, 518), dtype=np.float32)
                if depth.shape != shape[1:] or not np.isfinite(depth).all():
                    raise ValueError('Depth model returned invalid/nonfinite pixels')
                depths[count] = depth
                small[count] = torch.nn.functional.interpolate(torch.from_numpy(depth)[None, None],
                                                               (136, 240), mode='area')[0, 0].numpy()
                count += 1
                emit(done=count, total=video['frames'], unit='frames', phase='depth_reference',
                     detail='%d/%d frames' % (count, video['frames']))
            if count != video['frames']:
                raise ValueError('Source video frame count changed during depth conversion')
        # Whole-clip p1/p99, in-place selection on disk-backed samples, bounded RAM.
        small.flush()
        lo, hi = np.percentile(small.reshape(-1), [1, 99], overwrite_input=True)
        span = max(float(hi - lo), 1e-6)
        output = stage / 'depth.mp4'
        emit(phase='depth_reference', detail='Encoding depth video')
        with av.open(str(output), 'w') as container:
            stream = container.add_stream('libx264', rate=Fraction(video['fps_num'], video['fps_den']))
            stream.width, stream.height = video['width'], video['height']
            stream.pix_fmt = 'yuv420p' if stream.width % 2 == stream.height % 2 == 0 else 'yuv444p'
            stream.options = {'crf': '16', 'threads': '2'}
            for index, depth in enumerate(depths):
                check()
                gray = (np.clip((depth - lo) / span, 0, 1) * 255).round().astype(np.uint8)
                frame = av.VideoFrame.from_ndarray(_clay_relief(gray), format='rgb24')
                frame.pts, frame.time_base = index, Fraction(video['fps_den'], video['fps_num'])
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        return dict(low=float(lo), high=float(hi), near='white', torch=torch.__version__,
                    av=av.__version__, ffmpeg=av.library_versions,
                    attention='ordinary-torch-matmul-softmax', device=device)
    finally:
        # Close mappings before deleting or moving files (Windows).
        depths._mmap.close()
        small._mmap.close()
        del depths, small
        raw_path.unlink(missing_ok=True)
        small_path.unlink(missing_ok=True)


def _convert(request, emit, check):
    source, root, work = Path(request['source']), Path(request['root']), Path(request['work'])
    device = request.get('device', 'auto')
    if device == 'auto':
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if device not in ('cpu', 'cuda'):
        raise ValueError('Depth reference device must be cpu, cuda or auto')
    emit(phase='depth_reference', detail='Checking depth reference cache')
    initial_hash, initial_size = digest(source, check), source.stat().st_size
    video = inspect_video(source, check)
    config = identity(source, video, device, check)
    if config['source_sha256'] != initial_hash or config['source_size'] != initial_size:
        raise ValueError('Source video changed during depth inspection')
    cache = Path(request['cache'])
    key = cache_key(config)
    target = cache / key
    with _lock(cache / '.locks' / (key + '.lock'), check):
        found = _cached(target, config, check)
        if found is not None:
            return dict(path=str(found), cache_hit=True, key=key)
        weights = ensure_weights(root, emit, check)
        # Parent releases idle Comfy/engine/encoder models only on a cache miss.
        emit(phase=STAGE, stage=STAGE, label='Extracting depth from the reference video',
             done=0, total=video['frames'], unit='frames', detail='Loading depth model', waiting_for_device=True)
        while not (work / 'device-ready').is_file():
            check()
            time.sleep(.05)
        stage = work / 'result'
        stage.mkdir()
        with _lock(Path(os.environ.get('FREEVIDEO_LOCK_PATH', root / 'engine.lock')), check, inherit=True):
            metrics = _infer(source, stage, video, weights, device, emit, check)
        observed = _validate(stage / 'depth.mp4', video, check)
        if digest(source, check) != config['source_sha256'] or source.stat().st_size != config['source_size']:
            raise ValueError('Source video changed during depth conversion')
        manifest = dict(config=config, source_pts=video['pts'], source_time_base=video['time_base'], output=observed,
                        output_sha256=digest(stage / 'depth.mp4', check), output_bytes=(stage / 'depth.mp4').stat().st_size,
                        normalization=metrics, render='clay-relief-v1', license='Apache-2.0',
                        license_sources=['https://github.com/DepthAnything/Depth-Anything-V2/tree/'+catalog()['source_commit'],
                                         'https://huggingface.co/'+catalog()['repo']+'/tree/'+catalog()['revision']])
        (stage / 'manifest.json').write_text(canonical_json(manifest), encoding='utf-8')
        check()
        if target.exists():
            # Retain invalid old artifacts separately; never accept a partial directory.
            target.rename(cache / (key + '.rejected-' + uuid.uuid4().hex))
        os.replace(stage, target)
        return dict(path=str(target / 'depth.mp4'), cache_hit=False, key=key)


def convert(source, *, root=None, cache=None, device='auto', python=None, environ=None,
            progress=None, interrupted=None, release_models=None):
    """Convert in a supervised child; return a validated cache path after it exits.

    Progress carries stage=depth_reference and done/total in frames. Cancellation
    stops the whole child tree and removes only this request's temporary files.
    """
    root = Path(root or data_root()).resolve()
    cache = Path(cache or cache_directory(root)).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    def check():
        if interrupted:
            interrupted()
    check()
    with tempfile.TemporaryDirectory(prefix='.depth-', dir=cache) as directory:
        work = Path(directory)
        request = dict(source=str(Path(source).resolve()), root=str(root), cache=str(cache), device=device, work=str(work))
        (work / 'request.json').write_text(canonical_json(request), encoding='utf-8')
        command = processes.module_command('freevideo_engine.depth_reference', '--request', work / 'request.json')
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
                        error = json.loads((work / 'error.json').read_text(encoding='utf-8'))['error']
                    except (OSError, ValueError, KeyError):
                        error = 'Depth reference worker exited with code %s' % child.returncode
                    raise RuntimeError(error)
                result = json.loads((work / 'result.json').read_text(encoding='utf-8'))
                check()
                return Path(result['path'])
            except BaseException:
                (work / 'cancelled').touch()
                processes.stop(child)
                raise


def prepare_media(media, *, root, machine, environ, progress, interrupted, release_models):
    """Execution only. With depth off, return the original object unchanged."""
    if not any(row.get('depth') is True for row in media.get('references', [])):
        return media
    result = dict(media, references=[])
    for row in media.get('references', []):
        if 'depth' in row and (type(row['depth']) is not bool or row.get('kind') != 'video'):
            raise ValueError('depth must be a boolean on a video reference')
        if row.get('depth') is not True:
            result['references'].append(row)
            continue
        converted = convert(row['path'], root=root, python=machine['python'], environ=environ,
                            device='cpu' if machine.get('device_backend') in ('cpu', 'mps') else 'cuda',
                            progress=progress, interrupted=interrupted, release_models=release_models)
        value = {key: val for key, val in row.items() if key != 'depth'}
        value['path'] = str(converted)
        value.setdefault('early_steps', 3)
        result['references'].append(value)
    return result


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
            raise InterruptedError('Depth reference conversion cancelled')
    state = dict(stage=STAGE, phase=STAGE, timing_phase='encoding', label='Extracting depth from the reference video',
                 done=0, total=None, unit='frames', references=True)
    def emit(**values):
        state.update(values)
        save(work / 'progress.json', state)
    try:
        with processes.worker_signals():
            result = _convert(request, emit, check)
        save(work / 'result.json', result)
    except Exception as error:
        save(work / 'error.json', dict(error=type(error).__name__ + ': ' + str(error)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
