"""The H3 Image runner the image worker uses for a request that names `minimax-h3-image`.

It has the same surface as ``ImageRunner`` (``generate``, ``stats``, ``at_boundary``, ``close``) so the worker's
request loop, cancellation and error classes are shared. The sampling itself is ``h3_pipeline.generate``
(prompt -> text encoding with the video text encoder -> DiT -> tiled VAE -> picture).
"""
import os
import secrets
from pathlib import Path

from . import image_pack

TIER_GROUP = 'h3-image:int8'
STUB_ENV = 'FREEVIDEO_H3_STUB'


def files(model_root):
    """Where the installed H3 Image files are: {'dit', 'vae', 'text_encoder', 'folder'}."""
    found = {}
    for row in image_pack.rows(group=TIER_GROUP):
        if row.get('component') in ('dit', 'vae', 'text_encoder'):
            found[row['component']] = image_pack.target(row, model_root)
    found['folder'] = image_pack.base(model_root) / 'h3-image'
    return found


def _stub(prompt, *, width, height, seed, progress, cancel):
    """A flat picture of the asked size with the stage callbacks, for protocol tests and page checks only."""
    from PIL import Image
    shade = (seed or 0) % 160 + 40
    for index in range(6):
        if cancel is not None and cancel():
            raise InterruptedError('cancelled')
        progress('denoise', (index + 1) / 6)
    return Image.new('RGB', (width, height), (shade, 90, 255 - shade))


class H3Runner:
    full_card = False

    def __init__(self, pack_dir, *, device='cuda', progress=None):
        self.pack_dir = Path(pack_dir)
        self.model_root = self.pack_dir.parent
        self.device = device
        self.files = files(self.model_root)
        self.last_plan = None
        self.last_stats = None
        if progress is not None:
            progress('load', 1.)

    def generate(self, prompt, *, width, height, seed=None, reference_images=None, progress=None, cancel=None,
                 setup=None, **_):
        """`setup` is what the service read from the installation: the shared encoder's runtime and the card budget."""
        progress = progress or (lambda stage, fraction: None)
        # A request without a seed draws a fresh one, as the other image model does; the one used is reported
        # with the plan so the picture can carry it.
        seed = secrets.randbits(32) if seed is None else seed
        self.last_plan = dict(seed=seed)
        if os.environ.get(STUB_ENV) == '1':
            return _stub(prompt, width=width, height=height, seed=seed, progress=progress, cancel=cancel)
        try:
            from . import h3_pipeline
        except ImportError:
            raise RuntimeError('H3 Image generation is not available in this build') from None
        setup = setup or {}
        # The engine reports (stage, done, total) with stage encode/denoise/decode; the page's stages are text/denoise/decode.
        names = dict(encode='text')

        def report(stage, done, total):
            progress(names.get(stage, stage), done / total if total else 0.)

        stats = {}
        self.last_stats = dict(h3=stats)
        try:
            return h3_pipeline.generate(
                prompt, width, height, seed, reference_images or None, report,
                (lambda: bool(cancel and cancel())), dit_path=self.files['dit'], vae_path=self.files['vae'],
                encoder=setup.get('encoder'), comfy_root=setup.get('comfy_root'), comfy_python=setup.get('comfy_python'),
                model_paths=setup.get('model_paths'), vram_gib=setup.get('vram_gib'), ram_gib=setup.get('ram_gib'),
                stats=stats)
        except h3_pipeline.Cancelled:
            raise InterruptedError('cancelled') from None
        finally:
            self.last_plan = dict(seed=seed, **{key: stats[key] for key in ('canvas', 'resident', 'planned_peak_gib', 'host_mode') if key in stats})

    def at_boundary(self):
        return True

    def stats(self):
        return dict(plan=self.last_plan, **(self.last_stats or {}))

    def close(self):
        self.last_plan = self.last_stats = None
