"""Execution receipts for automatic backend selection, without importing CUDA."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from .hardware import installed_backends
from .paths import data_root

PACKAGES = ('torch', 'triton', 'triton-windows', 'diffusers', 'sageattention', 'flash-attn', 'flash-attn-4')
COMPUTE_FILES = ('attention.py', 'probe.py', 'fp8_gemm.py', 'fp8_ops.py', 'weight_only.py',
                 'kernel_capabilities.py', 'doctor.py', 'fa4_guard.py', 'triton_compat.py', 'runtime.py',
                 'head_chunk.py', 'blocks.py', 'packing.py', 'dependencies.json')


def package_versions():
    result = {}
    for name in PACKAGES:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    for distribution in importlib.metadata.distributions():
        name = (distribution.metadata.get('Name') or '').lower().replace('_', '-')
        if name.startswith(('nvidia-', 'cuda-')):
            result[name] = distribution.version
    return result


def identity(hardware, versions=None):
    package = Path(__file__).parent
    overlay = data_root() / 'vendor/fa4-b26-valid-tile/flash_attn/cute/flash_fwd.py'
    return dict(schema_version=1,
                hardware={k: v for k, v in hardware.to_dict().items() if k in (
                    'gpu_name', 'capability', 'vram_total', 'system', 'torch_version',
                    'cuda_version', 'gpu_uuid', 'driver_version')},
                python=str(Path(sys.executable).resolve()),
                fa4_overlay_sha256=hashlib.sha256(overlay.read_bytes()).hexdigest() if overlay.is_file() else None,
                packages=versions if versions is not None else package_versions(),
                source_sha256={name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                               for name in COMPUTE_FILES})


def receipt_path():
    return data_root() / 'kernel-capabilities.json'


def readiness(rows):
    passed = {row['backend'] for row in rows if row.get('status') == 'complete'}
    attention = sorted(passed - {'linear'})
    return dict(ready='linear' in passed and bool(attention), usable_attention_backends=attention,
                failed_optional_backends=[row['backend'] for row in rows
                                          if row.get('status') != 'complete' and row['backend'] != 'linear'])


def available_backends(hardware, *, discovered=None, probe_missing=True):
    """Reuse matching checks; re-probe after a GPU, driver or package/code change.

    Read-only planning may opt out of probing. Execution callers never turn an
    unavailable requested backend into another backend or a math SDPA kernel.
    """
    discovered = set(installed_backends() if discovered is None else discovered)
    expected = identity(hardware)
    # Normalize tuples exactly as on disk.
    expected = json.loads(json.dumps(expected))
    try:
        receipt = json.loads(receipt_path().read_text(encoding='utf-8'))
        if receipt['identity'] != expected or set(receipt['installed_attention_packages']) != discovered:
            receipt = None
        elif (not isinstance(receipt.get('kernel_probes'), list)
              or {row.get('backend') for row in receipt['kernel_probes'] if isinstance(row, dict)} != discovered | {'linear'}
              or len(receipt['kernel_probes']) != len(discovered) + 1
              or any(not isinstance(row, dict) or row.get('status') not in ('complete', 'error')
                     for row in receipt['kernel_probes'])):
            receipt = None
    except (OSError, ValueError, KeyError, TypeError):
        receipt = None
    if receipt is None:
        if not probe_missing:
            return discovered
        from .doctor import doctor
        receipt = doctor(probe=True, hardware=hardware, backends=discovered)
    checked = readiness(receipt['kernel_probes'])
    if not checked['ready']:
        raise RuntimeError('GPU kernel checks did not find a working linear and attention path. '
                           'Run freevideo doctor --probe; details: ' + str(receipt_path()))
    return discovered.intersection(checked['usable_attention_backends'])


def fp8_implementation(system, scale_granularity, requested='auto'):
    if requested not in ('auto', 'torch', 'scaled-mm-epilogue'):
        raise ValueError('Unknown FP8 GEMM implementation: ' + requested)
    if requested == 'scaled-mm-epilogue' and scale_granularity != 'rowwise':
        raise ValueError('The FP8 compatibility implementation is only for rowwise caches; use native Torch for tensorwise scales')
    if requested != 'auto':
        return requested
    return 'scaled-mm-epilogue' if system == 'Windows' and scale_granularity == 'rowwise' else 'torch'
