"""Execution receipts for automatic backend selection, without importing CUDA."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from .hardware import installed_backends
from .paths import data_root
from .triton_compat import temp_root

PACKAGES = ('torch', 'triton', 'triton-windows', 'diffusers', 'sageattention', 'flash-attn', 'flash-attn-4')
COMPUTE_FILES = ('attention.py', 'backends/__init__.py', 'backends/base.py', 'backends/cuda.py',
                 'backends/cuda_attention.py', 'probe.py', 'fp8_gemm.py', 'fp8_ops.py', 'int8_ops.py', 'weight_only.py',
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


def view_root(environ=None):
    """The short folder this process runs the installation from; None from the installation's own."""
    from . import launch_view
    view = launch_view.current(environ) or launch_view.current()
    return str(view.root) if view is not None and view.used else None


LINEAR_PROBES = ('linear', 'linear-int8')


def needs_path_recheck(receipt, environ=None):
    """Retry failed checks once after changing the installation or temporary view."""
    return (not readiness(receipt['kernel_probes'])['ready']
            and (receipt.get('view_root') != view_root(environ)
                 or receipt.get('temp_root') != temp_root(environ)))


def readiness(rows):
    passed = {row['backend'] for row in rows if row.get('status') == 'complete'}
    attention = sorted(passed - set(LINEAR_PROBES))
    return dict(ready='linear' in passed and bool(attention), usable_attention_backends=attention,
                int8_ready='linear-int8' in passed,
                failed_optional_backends=[row['backend'] for row in rows
                                          if row.get('status') != 'complete' and row['backend'] != 'linear'])


def _saved_receipt():
    try:
        receipt = json.loads(receipt_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return receipt if isinstance(receipt, dict) else None


def _expected_identity(hardware):
    # Normalize tuples exactly as on disk.
    return json.loads(json.dumps(identity(hardware)))


def _matching(receipt, expected, discovered):
    try:
        if receipt['identity'] != expected or set(receipt['installed_attention_packages']) != discovered:
            return None
        if (not isinstance(receipt.get('kernel_probes'), list)
                or not discovered | {'linear'} <= {row.get('backend') for row in receipt['kernel_probes'] if isinstance(row, dict)}
                   <= discovered | set(LINEAR_PROBES)
                or len(receipt['kernel_probes']) != len({row.get('backend') for row in receipt['kernel_probes'] if isinstance(row, dict)})
                or any(not isinstance(row, dict) or row.get('status') not in ('complete', 'error')
                       for row in receipt['kernel_probes'])):
            return None
    except (KeyError, TypeError):
        return None
    return receipt


def matching_receipt(hardware, discovered=None):
    """The saved kernel checks when they still match this GPU, driver and packages; else None."""
    discovered = set(installed_backends() if discovered is None else discovered)
    receipt = _saved_receipt()
    return None if receipt is None else _matching(receipt, _expected_identity(hardware), discovered)


# Compute capabilities SageAttention 2.x has kernels for (its sageattn dispatch);
# on any other GPU its first call raises "Unsupported CUDA architecture".
SAGE2_CAPABILITIES = frozenset({(8, 0), (8, 6), (8, 9), (9, 0), (12, 0), (12, 1)})


def withheld_without_receipt(hardware, discovered, receipt=None, expected=None):
    """Installed backends known not to run here although this GPU has no matching checks.

    Planning without a matching receipt offers what is installed, so the first
    request after a driver or FreeVideo update, or on another card in the same
    machine, keeps the backend its checks chose before. Two kinds of backend
    are left out: one whose package has no kernels for this compute capability,
    and one whose last check failed on the same architecture with the same
    packages (only the card, driver or FreeVideo's own files differ). Returns
    {backend: reason}.
    """
    withheld = {}
    capability = tuple(hardware.capability) if isinstance(hardware.capability, (tuple, list)) else None
    if 'sage2' in discovered and capability not in SAGE2_CAPABILITIES:
        withheld['sage2'] = 'no SageAttention 2 kernels for compute capability %s' % (
            '.'.join(map(str, capability)) if capability else 'unknown')
    try:
        saved = receipt['identity']
        expected = _expected_identity(hardware) if expected is None else expected
        comparable = (all(saved['hardware'][key] == expected['hardware'][key]
                          for key in ('system', 'capability', 'torch_version', 'cuda_version'))
                      and saved['packages'] == expected['packages']
                      and saved.get('fa4_overlay_sha256') == expected['fa4_overlay_sha256']
                      and set(receipt['installed_attention_packages']) == discovered)
        if comparable:
            for row in receipt['kernel_probes']:
                if isinstance(row, dict) and row.get('status') == 'error' and row.get('backend') in discovered:
                    withheld.setdefault(row['backend'], 'failed its last check on this architecture with these packages')
    except (KeyError, TypeError, AttributeError):
        pass
    return withheld


def available_backends(hardware, *, discovered=None, probe_missing=True, evidence=None):
    """Reuse matching checks; re-probe after a GPU, driver or package/code change.

    Read-only planning may opt out of probing; it then offers the installed
    backends except those known not to run here (``withheld_without_receipt``).
    Execution callers never turn an unavailable requested backend into another
    backend or a math SDPA kernel. ``evidence``, a dict, receives which checks
    the answer rests on.
    """
    discovered = set(installed_backends() if discovered is None else discovered)
    saved = _saved_receipt()
    expected = _expected_identity(hardware) if saved is not None else None
    receipt = None if saved is None else _matching(saved, expected, discovered)
    if receipt is not None and probe_missing and needs_path_recheck(receipt):
        # They failed while the installation ran from another folder, and compilers take some
        # paths only as that folder spells them. What passed is kept wherever it ran from.
        receipt = None
    if receipt is None:
        if not probe_missing:
            withheld = withheld_without_receipt(hardware, discovered, saved, expected)
            if evidence is not None:
                evidence.update(kernel_receipt='unmatched' if saved is not None else 'missing', withheld=withheld)
            return discovered - set(withheld)
        from .doctor import doctor
        receipt = doctor(probe=True, hardware=hardware, backends=discovered)
    checked = readiness(receipt['kernel_probes'])
    if not checked['ready']:
        raise RuntimeError('GPU kernel checks did not find a working linear and attention path. '
                           'Run freevideo doctor --probe; details: ' + str(receipt_path()))
    if evidence is not None:
        evidence.update(kernel_receipt='matching', withheld={})
    return discovered.intersection(checked['usable_attention_backends'])


def fp8_implementation(system, scale_granularity, requested='auto'):
    if requested not in ('auto', 'torch', 'scaled-mm-epilogue'):
        raise ValueError('Unknown FP8 GEMM implementation: ' + requested)
    if requested == 'scaled-mm-epilogue' and scale_granularity != 'rowwise':
        raise ValueError('The FP8 compatibility implementation is only for rowwise caches; use native Torch for tensorwise scales')
    if requested != 'auto':
        return requested
    return 'scaled-mm-epilogue' if system == 'Windows' and scale_granularity == 'rowwise' else 'torch'
