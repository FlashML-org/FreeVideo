"""User paths; importing the CLI or planner never initializes CUDA."""
import os
from pathlib import Path
import sys
from .system import install_root


def data_root():
    return install_root()


def vdn_root():
    return Path(os.environ.get('FREEVIDEO_VDN_ROOT', data_root() / 'vendor' / 'vdn')).expanduser()


def model_root():
    return Path(os.environ.get('FREEVIDEO_MODEL_ROOT', data_root() / 'models')).expanduser()


def installed_model_root():
    """The model folder setup recorded (models/vdn), unless explicitly overridden.

    Engine processes only receive FREEVIDEO_HOME, where model_root() means
    models/. Files setup downloads, such as the latent upscaler and sampling
    tables, live in the recorded folder.
    """
    if not os.environ.get('FREEVIDEO_MODEL_ROOT'):
        import json
        try:
            return Path(json.loads((data_root() / 'machine.json').read_text(encoding='utf-8'))['model_root']).expanduser()
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return model_root()


def _ensure_triton_import():
    """Put the engine's Triton stand-in on ``sys.path`` when the real package is missing.

    VDN and torch._inductor import Triton submodules at load time. Unknown
    ``triton.*`` names become empty packages so inductor can import them.
    """
    try:
        import triton  # noqa: F401
        return
    except ImportError:
        pass
    stub = Path(__file__).resolve().parent / 'triton_stub'
    if str(stub) not in sys.path:
        sys.path.insert(0, str(stub))
    import importlib.abc
    import importlib.machinery

    class _Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.__path__ = []

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname != 'triton' and not fullname.startswith('triton.'):
                return None
            for candidate in (path, [str(stub)], [str(stub / 'triton')]):
                if candidate is None:
                    continue
                found = importlib.machinery.PathFinder.find_spec(fullname, list(candidate))
                if found is not None:
                    return found
            return importlib.machinery.ModuleSpec(fullname, _Loader(), is_package=True)

    if not any(type(finder).__name__ == '_Finder' for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())


def _pascal_vdn_fallbacks():
    """Eager temporal conv / no torch.compile on SM<80 (Triton does not target Pascal)."""
    try:
        import torch
        import torch.nn.functional as F
    except ImportError:
        return
    if not torch.cuda.is_available() or torch.cuda.get_device_capability() >= (8, 0):
        return
    from src.models.linear_attention import features
    features._TCONV['fn'] = features._temporal_shift
    import src.models.ops.temporal_conv as temporal_conv
    temporal_conv.temporal_conv_activate = features._tconv_activate_body
    features.temporal_conv_activate = features._tconv_activate_body

    def _spatial_pascal(self, proj, tokens, num_frames, frame_size):
        """Depthwise 5x5 in FP32 NCHW: Pascal cuDNN has no BF16 grouped conv."""
        heads, head_dim = tokens.shape[-2], tokens.shape[-1]
        grid_h, grid_w = frame_size
        channels = heads * head_dim
        volume = (tokens.reshape(num_frames, grid_h, grid_w, channels)
                  .permute(0, 3, 1, 2).contiguous().float())
        w_sp = getattr(self, f'{proj}_sp').weight.contiguous().float()
        volume = F.conv2d(volume, w_sp, padding=self.KERNEL // 2, groups=channels)
        x = volume.to(tokens.dtype).permute(0, 2, 3, 1).reshape(num_frames, grid_h * grid_w, channels)
        w_tm = getattr(self, f'{proj}_tm').weight.squeeze(1).to(x.dtype)
        return x, w_tm

    features.LinearAttentionSepConv.spatial = _spatial_pascal


def add_vdn():
    root = vdn_root().resolve()
    if not (root / 'src/models/hybrid_attention.py').is_file():
        raise RuntimeError('VDN dependency is missing. Run freevideo setup or set FREEVIDEO_VDN_ROOT.')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Editable-install .pth files retain the original absolute checkout path.
    # Resolve the pinned sibling source explicitly when a workspace is mounted
    # or moved under a different prefix (for example a capacity container).
    diffusers = root / 'diffusers' / 'src'
    if not (diffusers / 'diffusers/__init__.py').is_file():
        raise RuntimeError('Patched Diffusers dependency is missing. Run freevideo setup --install-packages.')
    if str(diffusers) not in sys.path:
        sys.path.insert(0, str(diffusers))
    import torch
    if torch.cuda.is_available():
        torch.cuda.get_device_capability()
    _ensure_triton_import()
    from .fa4_guard import activate
    activate()
    _pascal_vdn_fallbacks()
    return root


def base_path():
    return model_root() / 'h3-base'


def checkpoint_path():
    return model_root() / 'stage-dmd-step-250'


def comfy_root():
    value = os.environ.get('FREEVIDEO_COMFY_ROOT')
    return Path(value).expanduser() if value else data_root() / 'vendor' / 'ComfyUI'
