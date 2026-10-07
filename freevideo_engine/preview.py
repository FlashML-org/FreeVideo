"""Previews: a two-pass request that stops after its half-resolution first pass.

The first pass is kept beside the preview MP4, so an upscale continues from the
same latents: the learned latent upscale, the refinement steps and a full
decode, as the uninterrupted request would have run them. Torch-free.
"""
import json
from pathlib import Path

# CUDA keeps the worker request and a copy of its receipt next to the latents;
# the decode_resume loaders require all three in one folder.
CUDA_FILES = ('first-pass.pt', 'first-pass.engine.json', 'video.json', 'conditioning.pt')
# Native Mac stages keep their own first-pass receipt and engine request.
MAC_FILES = ('first-pass.pt', 'first-pass.json', 'engine-request.json', 'conditioning.pt')


def validate_flags(preview, refine_from, two_pass):
    if preview and refine_from:
        raise ValueError('Choose either a preview or an upscale of one, not both')
    if (preview or refine_from) and not two_pass:
        raise ValueError('Previews and their upscale use two-pass generation')


def source(output, native=False):
    """The retained first pass of a finished preview MP4, or a clear error."""
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != '.mp4' or not output.is_file():
        raise ValueError('Choose the preview MP4 to upscale')
    folder = output.with_suffix('.artifacts')
    names = MAC_FILES if native else CUDA_FILES
    missing = [name for name in names if not (folder / name).is_file()]
    if missing:
        raise ValueError('This preview no longer has its first pass (%s). Generate the preview again.'
                         % ', '.join(missing))
    report = json.loads(output.with_suffix('.request.json').read_text(encoding='utf-8'))
    if not isinstance(report, dict) or report.get('success') is not True or not report.get('preview'):
        raise ValueError('This video is not a finished preview')
    if not native:
        from .decode_resume import first_pass_complete
        if not first_pass_complete(json.loads((folder / 'first-pass.engine.json').read_text(encoding='utf-8'))):
            raise ValueError('This preview did not keep a complete first pass')
    result = dict(output=str(output), report=report, conditioning=str(folder / 'conditioning.pt'),
                  input=str(folder / 'first-pass.pt'))
    if native:
        result.update(metrics=str(folder / 'first-pass.json'), request=str(folder / 'engine-request.json'))
    else:
        result.update(metrics=str(folder / 'first-pass.engine.json'), request=str(folder / 'video.json'))
    return result


def upscale_reads(cache, video, upscaler_checkpoint):
    """Files the upscale of this preview reads from disk, in the order it needs them.

    Transformer blocks the engine keeps on the GPU are not read again; the
    others are mapped from the prepared cache at every step, and a decode in
    between can push them out of a small OS file cache. The latent upscaler
    is read once. An idle session can warm exactly these files.
    """
    resident = ((video or {}).get('config') or {}).get('resident_blocks')
    files = []
    if type(resident) is int and resident >= 0:
        blocks = sorted(Path(cache, 'blocks').glob('[0-9][0-9].safetensors'), key=lambda path: int(path.stem))
        files += [path for path in blocks if int(path.stem) >= resident]
    if upscaler_checkpoint:
        files.append(Path(upscaler_checkpoint))
    files = [path for path in files if path.is_file()]
    return dict(files=[str(path) for path in files], bytes=sum(path.stat().st_size for path in files))
