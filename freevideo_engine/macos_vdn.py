"""Verified Mac import overlay for the pinned VDN source; originals stay clean.

CUDA-only imports, sampler synchronization and dead tensor lifetimes are adapted.
The reference hybrid equations, model topology, schedule and checkpoint names are unchanged.
Optimized CUDA inference kernels remain unavailable on this reference MPS path.
"""
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


EDITS = {
    'src/models/hybrid_attention.py': (
        ('from src.models.ops.fp8_linear import Fp8Linear, quantize_activation\n', ''),
        ('        if all(isinstance(p, Fp8Linear) for p in projections):\n',
         '        fp8 = False\n'
         '        if x.is_cuda:\n'
         '            from src.models.ops.fp8_linear import Fp8Linear, quantize_activation\n'
         '            fp8 = all(isinstance(p, Fp8Linear) for p in projections)\n'
         '        if fp8:\n'),
        ('        del softmax_out\n', '        del softmax_out, flat\n'),
    ),
    'src/models/linear_attention/features.py': (
        ('from src.models.ops.temporal_conv import temporal_conv_activate\n', ''),
        ('        out = temporal_conv_activate(x, w_tm, conv.KERNEL, conv.KERNEL // 2,\n',
         '        from src.models.ops.temporal_conv import temporal_conv_activate\n'
         '        out = temporal_conv_activate(x, w_tm, conv.KERNEL, conv.KERNEL // 2,\n'),
    ),
    'src/models/hybrid_transform.py': (
        ('from src.models.ops.fused_block import fast_block_forward, fast_ff_forward\n', ''),
        ('            block.forward = types.MethodType(fast_block_forward, block)\n',
         '            from src.models.ops.fused_block import fast_block_forward, fast_ff_forward\n'
         '            block.forward = types.MethodType(fast_block_forward, block)\n'),
    ),
    'src/inference/render.py': (
        ('torch.cuda.synchronize(device)', 'torch.mps.synchronize()'),
        # RoPE immediately casts coordinates to FP32. Move that exact cast
        # before device transfer because MPS cannot store float64 tensors.
        ('position_ids.to(device), token_tags.to(device)',
         'position_ids.to(dtype=torch.float32).to(device), token_tags.to(device)'),
    ),
}


def adapt(name, content):
    for before, after in EDITS.get(name, ()):
        count = 3 if before == 'torch.cuda.synchronize(device)' else 1
        if content.count(before) != count:
            raise ValueError('Pinned VDN Mac patch no longer matches: ' + name)
        content = content.replace(before, after)
    compile(content, name, 'exec')
    return content


def prepare(source, directory):
    source, directory = Path(source).resolve(), Path(directory).resolve()
    spec = json.loads(Path(__file__).with_name('dependencies.json').read_text())['vdn']
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=source).decode('utf-8')
    if git('rev-parse', 'HEAD').strip() != spec['commit'] or git('status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Mac overlay requires the clean pinned VDN checkout')
    files = [name for name in git('ls-files', '-z', 'src').split('\0') if name]
    if not files or not set(EDITS).issubset(files):
        raise ValueError('Pinned VDN source is incomplete')
    content = {}
    for name in files:
        path = source / name
        if path.is_symlink():
            raise ValueError('Unexpected source symlink in the pinned VDN tree')
        data = path.read_bytes()
        content[name] = adapt(name, data.decode('utf-8')).encode('utf-8') if name in EDITS else data
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    identity = dict(schema_version=1, upstream_commit=spec['commit'], files=hashes)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    target = directory / ('vdn-mps-' + key)
    def validate():
        for name, expected in hashes.items():
            path = target / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('VDN Mac overlay failed verification: ' + name)
    if target.exists():
        validate()
        return target
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vdn-mps-', dir=directory) as temp:
        stage = Path(temp) / 'overlay'
        for name, data in content.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (stage / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
        shutil.copyfile(source / 'LICENSE', stage / 'LICENSE')
        try:
            stage.rename(target)
        except OSError:
            if not target.is_dir():
                raise
            validate()
    return target


def activate():
    if sys.platform != 'darwin':
        raise RuntimeError('The VDN MPS overlay is for the native Mac runtime')
    from .paths import add_vdn, data_root
    source = add_vdn()
    target = prepare(source, data_root() / 'vendor')
    loaded = sys.modules.get('src')
    if loaded is not None and not Path(loaded.__file__).resolve().is_relative_to(target):
        raise RuntimeError('VDN was already imported without its Mac overlay; start a fresh worker')
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    importlib.import_module('src')
    return target
