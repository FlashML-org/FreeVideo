"""Pixel 2× upscale of a finished MP4. Does not load MiniMax H3 or resample.

RealESRGAN_x2plus runs on decoded frames; the original audio packets are copied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

SCALE = 2
MAX_LONG_SIDE = 2048
TILE = 192
OVERLAP = 16

MODEL = dict(
    repo='2kpr/Real-ESRGAN',
    revision='d43aff1f1c922c1d974ae3d9ee9be86580428231',
    file='RealESRGAN_x2plus.pth',
    bytes=67061725,
    sha256='49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb',
    role='pixel_upscaler')


def output_size(width, height, scale=SCALE):
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ValueError('Video size is unknown')
    if type(scale) is not int or scale != SCALE:
        raise ValueError('This upscaler is 2× only')
    wide, tall = width * scale, height * scale
    if max(wide, tall) > MAX_LONG_SIDE:
        raise ValueError('Upscale would be %d × %d; the long side must stay at %d or less'
                         % (wide, tall, MAX_LONG_SIDE))
    return wide, tall


def can_upscale(width, height, already=False, scale=SCALE):
    if already:
        return False
    try:
        output_size(width, height, scale)
    except ValueError:
        return False
    return True


def resolve_video(output_directory, relative):
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError('Choose a video to upscale')
    output_root = Path(output_directory).resolve()
    video = (output_root / relative).resolve()
    try:
        video.relative_to(output_root / 'FreeVideo')
    except ValueError:
        raise ValueError('Choose a FreeVideo clip from this output folder') from None
    if video.suffix.lower() != '.mp4' or not video.is_file():
        raise ValueError('The video is no longer there; generate it again')
    report_path = video.with_suffix('.request.json')
    report = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            report = {}
    if isinstance(report, dict) and report.get('pixel_upscale'):
        raise ValueError('This clip is already a pixel upscale. Generate a new clip if you want another size.')
    return video, report if isinstance(report, dict) else {}


def checkpoint_path():
    from .paths import installed_model_root, model_root
    installed = installed_model_root() / 'upscale_models' / Path(MODEL['file']).name
    earlier = model_root() / 'upscale_models' / installed.name
    return earlier if earlier.is_file() and not installed.is_file() else installed


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_checkpoint():
    path = checkpoint_path()
    if path.is_file() and path.stat().st_size == MODEL['bytes'] and _sha256(path) == MODEL['sha256']:
        return path
    print(json.dumps(dict(event='pixel_upscale_download', file=MODEL['file'], bytes=MODEL['bytes'])), flush=True)
    from .network import download, model_urls
    from .paths import data_root
    network = {}
    machine = data_root() / 'machine.json'
    if machine.is_file():
        installed = json.loads(machine.read_text(encoding='utf-8'))
        network = installed.get('network', {}) or {}
        if not network and installed.get('setup_run'):
            setup = Path(installed['setup_run']) / 'plan.json'
            if setup.is_file():
                network = json.loads(setup.read_text(encoding='utf-8')).get('network', {}) or {}
    network.setdefault('download_settings_path', str(data_root() / 'download-settings.json'))
    network.setdefault('sources', {}).setdefault('models', [{'id': 'official'}, {'id': 'hf-mirror'}])
    path.parent.mkdir(parents=True, exist_ok=True)
    download(model_urls(network, MODEL), path, MODEL['sha256'],
             network=network, size=MODEL['bytes'], category='models')
    return path


def _rrdbnet():
    import torch
    from torch import nn
    import torch.nn.functional as F

    def pixel_unshuffle(x, scale):
        b, c, hh, hw = x.size()
        out_channel = c * (scale ** 2)
        h, w = hh // scale, hw // scale
        view = x.view(b, c, h, scale, w, scale)
        return view.permute(0, 1, 3, 5, 2, 4).reshape(b, out_channel, h, w)

    class ResidualDenseBlock(nn.Module):
        def __init__(self, num_feat=64, num_grow_ch=32):
            super().__init__()
            self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
            self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
            return x5 * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, num_feat, num_grow_ch=32):
            super().__init__()
            self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
            self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
            self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, num_in_ch=3, num_out_ch=3, scale=2, num_feat=64, num_block=23, num_grow_ch=32):
            super().__init__()
            self.scale = scale
            if scale == 2:
                num_in_ch = num_in_ch * 4
            self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
            self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            feat = pixel_unshuffle(x, 2) if self.scale == 2 else x
            feat = self.conv_first(feat)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode='nearest')))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode='nearest')))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet()


def load_model(checkpoint, device):
    import torch
    print(json.dumps(dict(event='pixel_upscale_prepare', file=str(checkpoint))), flush=True)
    net = _rrdbnet()
    try:
        raw = torch.load(str(checkpoint), map_location='cpu', weights_only=True)
    except (TypeError, RuntimeError):
        # Known SHA-256 RealESRGAN_x2plus only; older torch has no weights_only.
        raw = torch.load(str(checkpoint), map_location='cpu')
    state = raw['params_ema'] if isinstance(raw, dict) and 'params_ema' in raw else (
        raw['params'] if isinstance(raw, dict) and 'params' in raw else raw)
    net.load_state_dict(state, strict=True)
    net.eval().to(device)
    return net


def _pad_to(tensor, multiple):
    import torch.nn.functional as F
    _, _, height, width = tensor.shape
    pad_h = (multiple - height % multiple) % multiple
    pad_w = (multiple - width % multiple) % multiple
    if pad_h or pad_w:
        tensor = F.pad(tensor, (0, pad_w, 0, pad_h), mode='reflect')
    return tensor, height, width


def upscale_image(net, image, device, tile=TILE, overlap=OVERLAP):
    """image: uint8 HWC RGB numpy array → uint8 HWC RGB at 2×."""
    import numpy as np
    import torch
    rgb = torch.from_numpy(np.ascontiguousarray(image)).permute(2, 0, 1).float().div_(255).unsqueeze(0)
    rgb, orig_h, orig_w = _pad_to(rgb, 2)
    _, _, height, width = rgb.shape
    scale = SCALE
    if height <= tile and width <= tile:
        with torch.no_grad():
            out = net(rgb.to(device)).float().cpu()
        out = out[:, :, :orig_h * scale, :orig_w * scale]
        return (out.squeeze(0).clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).numpy())
    stride = tile - overlap
    out = rgb.new_zeros(1, 3, height * scale, width * scale)
    weight = rgb.new_zeros(1, 1, height * scale, width * scale)
    ys = list(range(0, max(height - overlap, 1), stride))
    xs = list(range(0, max(width - overlap, 1), stride))
    if ys[-1] + tile < height:
        ys.append(height - tile)
    if xs[-1] + tile < width:
        xs.append(width - tile)
    with torch.no_grad():
        for top in ys:
            for left in xs:
                patch = rgb[:, :, top:top + tile, left:left + tile]
                ph, pw = patch.shape[-2:]
                if ph < 2 or pw < 2:
                    continue
                if ph % 2 or pw % 2:
                    patch, rh, rw = _pad_to(patch, 2)
                else:
                    rh, rw = ph, pw
                lifted = net(patch.to(device)).float().cpu()[:, :, :rh * scale, :rw * scale]
                t, l = top * scale, left * scale
                h, w = rh * scale, rw * scale
                fade = lifted.new_ones(1, 1, h, w)
                edge = overlap * scale
                if edge > 0:
                    ramp = torch.linspace(0, 1, edge)
                    fade[:, :, :edge, :] *= ramp.view(1, 1, edge, 1)
                    fade[:, :, -edge:, :] *= ramp.flip(0).view(1, 1, edge, 1)
                    fade[:, :, :, :edge] *= ramp.view(1, 1, 1, edge)
                    fade[:, :, :, -edge:] *= ramp.flip(0).view(1, 1, 1, edge)
                out[:, :, t:t + h, l:l + w] += lifted * fade
                weight[:, :, t:t + h, l:l + w] += fade
    out = out / weight.clamp_min(1e-6)
    out = out[:, :, :orig_h * scale, :orig_w * scale]
    return out.squeeze(0).clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).numpy()


def _probe_source(path):
    import av
    container = av.open(str(path))
    try:
        video = container.streams.video[0]
        width, height = video.width, video.height
        frames = video.frames if video.frames and video.frames > 0 else 0
        rate = video.average_rate or video.time_base and (1 / float(video.time_base)) or 24
        fps = float(rate)
        has_audio = bool(container.streams.audio)
        if not frames:
            frames = sum(1 for _ in container.decode(video))
        output_size(width, height)
        return dict(width=width, height=height, frames=frames, fps=fps, has_audio=has_audio)
    finally:
        container.close()


def _copy_audio(source, silent_video, destination):
    import av
    src = av.open(str(source))
    vid = av.open(str(silent_video))
    dest = av.open(str(destination), 'w')
    try:
        vin = vid.streams.video[0]
        vout = dest.add_stream(template=vin)
        ain = src.streams.audio[0] if src.streams.audio else None
        aout = dest.add_stream(template=ain) if ain is not None else None
        for packet in vid.demux(vin):
            if packet.dts is None:
                continue
            packet.stream = vout
            dest.mux(packet)
        if aout is not None:
            for packet in src.demux(ain):
                if packet.dts is None:
                    continue
                packet.stream = aout
                dest.mux(packet)
    finally:
        dest.close()
        vid.close()
        src.close()


def run(source, destination, *, scale=SCALE, checkpoint=None):
    import av
    import numpy as np
    import torch
    started = time.perf_counter()
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if source.suffix.lower() != '.mp4' or not source.is_file():
        raise ValueError('Choose an MP4 to upscale')
    info = _probe_source(source)
    wide, tall = output_size(info['width'], info['height'], scale)
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(dict(event='pixel_upscale_start', **info, scale=scale, out_width=wide, out_height=tall)),
          flush=True)
    weights = Path(checkpoint) if checkpoint else ensure_checkpoint()
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    net = load_model(weights, device)
    silent = destination.with_name(destination.stem + '.silent.mp4')
    container = av.open(str(source))
    writer = av.open(str(silent), 'w')
    try:
        vin = container.streams.video[0]
        try:
            stream = writer.add_stream('libx264', rate=vin.average_rate or 24)
            stream.options = {'crf': '17', 'preset': 'fast'}
        except Exception:
            stream = writer.add_stream('mpeg4', rate=vin.average_rate or 24)
        stream.width, stream.height = wide, tall
        stream.pix_fmt = 'yuv420p'
        done = 0
        total = info['frames'] or 1
        for frame in container.decode(vin):
            rgb = frame.to_ndarray(format='rgb24')
            lifted = np.ascontiguousarray(upscale_image(net, rgb, device)[:tall, :wide])
            video_frame = av.VideoFrame.from_ndarray(lifted, format='rgb24')
            for packet in stream.encode(video_frame):
                writer.mux(packet)
            done += 1
            print(json.dumps(dict(event='pixel_upscale', done=done, total=max(total, done))), flush=True)
        for packet in stream.encode():
            writer.mux(packet)
    finally:
        writer.close()
        container.close()
        del net
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    print(json.dumps(dict(event='pixel_upscale_encode')), flush=True)
    temporary = destination.with_suffix('.tmp.mp4')
    if info['has_audio']:
        _copy_audio(source, silent, temporary)
        silent.unlink(missing_ok=True)
    else:
        silent.replace(temporary)
    os.replace(temporary, destination)
    elapsed = time.perf_counter() - started
    geometry = dict(width=wide, height=tall, frames=done, fps=info['fps'],
                    seconds=done / info['fps'] if info['fps'] else None)
    report = dict(success=True, request_seconds=elapsed, geometry=geometry, output_size=None,
                  pixel_upscale=dict(source=str(source), scale=scale, model=MODEL['file'],
                                     sha256=MODEL['sha256'], frames=done, tile=TILE),
                  video=dict(sample_seconds=0, pixel_upscale_seconds=elapsed),
                  preview=False)
    from .monitoring import save
    save(destination.with_suffix('.request.json'), report)
    prompt = source.parent / 'prompt.txt'
    if prompt.is_file() and not (destination.parent / 'prompt.txt').is_file():
        (destination.parent / 'prompt.txt').write_text(prompt.read_text(encoding='utf-8'), encoding='utf-8')
    print(json.dumps(dict(event='pixel_upscale_done', output=str(destination), **geometry, seconds=elapsed)),
          flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog='freevideo pixel-upscale',
                                     description='2× RealESRGAN upscale of a finished MP4; keeps the original audio')
    parser.add_argument('--in', dest='source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--scale', type=int, default=SCALE, choices=(SCALE,))
    parser.add_argument('--checkpoint', type=Path)
    args = parser.parse_args(argv)
    run(args.source, args.out, scale=args.scale, checkpoint=args.checkpoint)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
