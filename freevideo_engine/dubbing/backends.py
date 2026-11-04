"""Dubbing backends behind one interface, so a model can be replaced without touching the service.

A backend is built from its model folder, says a line in the voice of one reference recording and
frees its memory in `close`. `speed` above 1 makes the line shorter; `align.fit` uses it to reach a
target duration.
"""
import importlib

REGISTRY = {
    'cosyvoice3': 'freevideo_engine.dubbing.backends:CosyVoice3Backend',
}
DEFAULT = 'cosyvoice3'
MAX_REFERENCE_SECONDS = 29.0


class Backend:
    name = ''
    sample_rate = 24000
    languages = ()
    # Whether the reference needs a transcript of what it says
    needs_reference_text = True
    supports_instruction = False

    def __init__(self, model_dir, device='cuda'):
        raise NotImplementedError

    def synth(self, text, reference, reference_text, speed=1.0, instruction=None):
        """Return (mono float32 samples, sample rate)."""
        raise NotImplementedError

    def close(self):
        pass


class CosyVoice3Backend(Backend):
    name = 'cosyvoice3'
    sample_rate = 24000
    languages = ('zh', 'en', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru')
    supports_instruction = True

    def __init__(self, model_dir, device='cuda'):
        from .cosyvoice3.build import CosyVoice3
        self.model = CosyVoice3(model_dir, device=device)

    def synth(self, text, reference, reference_text, speed=1.0, instruction=None):
        if instruction:
            samples = self.model.instruct(text, instruction, reference, speed=speed)
        else:
            samples = self.model.zero_shot(text, reference_text, reference, speed=speed)
        return samples, self.sample_rate

    def close(self):
        self.model = None


def names():
    return sorted(REGISTRY)


def create(name, model_dir, device='cuda'):
    """Build the named backend; an unknown name raises KeyError."""
    module, _, cls = REGISTRY[name].partition(':')
    return getattr(importlib.import_module(module), cls)(model_dir, device=device)
