"""The MOSS-Image runner the image worker uses for a request that names `moss-image-sft`.

Same surface as ``H3Runner`` (``generate``, ``idle``, ``at_boundary``, ``stats``, ``close``). Text prompts only:
the model takes no reference pictures, so a request that carries any is refused with a clear error.
"""
import os
import secrets
from pathlib import Path

from . import image_pack

STUB_ENV = 'FREEVIDEO_MOSS_STUB'


def folder(model_root):
    """Where the installed MOSS-Image files are."""
    return image_pack.base(model_root) / 'moss-image'


class MossRunner:
    full_card = False

    def __init__(self, pack_dir, *, device='cuda', progress=None):
        self.pack_dir = Path(pack_dir)
        self.model_root = self.pack_dir.parent
        self.device = device
        self.folder = folder(self.model_root)
        self.last_plan = None
        self.last_stats = None
        self.kept = None
        if progress is not None:
            progress('load', 1.)

    def generate(self, prompt, *, width, height, seed=None, reference_images=None, progress=None, cancel=None,
                 setup=None, stage=None, **_):
        progress = progress or (lambda stage, fraction: None)
        if reference_images:
            raise ValueError('moss_text_only')
        seed = secrets.randbits(32) if seed is None else seed
        self.last_plan = dict(seed=seed)
        if os.environ.get(STUB_ENV) == '1':
            from .h3_runner import _stub
            return _stub(prompt, width=width, height=height, seed=seed, progress=progress, cancel=cancel)
        from . import moss_pipeline
        setup = setup or {}
        names = dict(encode='text')

        def report(name, done, total):
            progress(names.get(name, name), done / total if total else 0.)

        stats = {}
        self.last_stats = dict(moss=stats)
        if self.kept is None:
            self.kept = moss_pipeline.Kept()
        try:
            return moss_pipeline.generate(
                prompt, width, height, seed, report, (lambda: bool(cancel and cancel())), folder=self.folder,
                vram_gib=setup.get('vram_gib'), kept=self.kept, stats=stats, enter=stage)
        except moss_pipeline.Cancelled:
            raise InterruptedError('cancelled') from None
        finally:
            self.last_plan = dict(seed=seed, **{key: stats[key] for key in ('canvas', 'placement') if key in stats})

    def idle(self, should_stop):
        """Nothing to prepare: the kept weights stay on the card."""

    def at_boundary(self):
        return True

    def stats(self):
        return dict(plan=self.last_plan, **(self.last_stats or {}))

    def close(self):
        if self.kept is not None:
            self.kept.close()
            self.kept = None
        self.last_plan = self.last_stats = None
