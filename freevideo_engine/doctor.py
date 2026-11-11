"""Readiness checks with an isolated CUDA detector."""
import json
import os
import subprocess
import sys
from .hardware import detect, installed_backends
from .paths import vdn_root, base_path, checkpoint_path, comfy_root
from .locking import LOCK_ENV, runtime_lock
from .kernel_capabilities import identity, package_versions, readiness, receipt_path, view_root, temp_root
from .monitoring import save
from . import processes, subprocess_output


def missing_required_paths(result, *, video_models=True):
    """Names of required directories that do not exist; an image-only installation has no video model to check."""
    skipped = () if video_models else ('base', 'checkpoint')
    return [k for k, row in result['paths'].items() if k not in skipped and not row['exists']]


def doctor(*, probe=False, hardware=None, backends=None, progress=None):
    if progress is None and os.environ.get('FREEVIDEO_INSTALL_PROGRESS') == '1':
        from .install_activity import step
        progress = step
    if progress:
        progress('GPU information reading')
    from .triton_compat import own_temporary_environment
    own_temporary_environment()
    versions = package_versions()
    hardware = hardware or detect()
    backends = set(installed_backends() if backends is None else backends)
    paths = {'vdn': vdn_root(), 'base': base_path(), 'checkpoint': checkpoint_path(), 'comfy': comfy_root()}
    result = {'hardware': hardware.to_dict(), 'versions': versions,
            'cuda_compatibility': hardware.cuda_compatibility(),
            'installed_attention_packages': sorted(backends),
            'paths': {k: {'path': str(p), 'exists': p.exists()} for k, p in paths.items()},
            'validation': 'Package and path discovery only. Run bench to check actual kernels and performance.'}
    if probe:
        result['kernel_probes'] = []
        with runtime_lock() as descriptor:
            env = dict(os.environ, **{LOCK_ENV: str(descriptor)})
            linear = ['linear'] + (['linear-int8'] if hardware.system in ('Windows', 'Linux') else [])
            probes = [*sorted(backends), *linear]
            for index, backend in enumerate(probes):
                if progress:
                    progress('GPU tests running', index, len(probes))
                command = [sys.executable, '-m', 'freevideo_engine.probe', backend]
                try:
                    child = subprocess_output.run_python(command, capture_output=True, timeout=180, env=env,
                                                         pass_fds=(descriptor,), runner=processes.run)
                    try:
                        row = json.loads(child.stdout.strip().splitlines()[-1])
                        if not isinstance(row, dict) or row.get('backend') != backend:
                            raise ValueError('Malformed kernel probe result')
                    except (ValueError, IndexError):
                        row = {'backend': backend, 'status': 'error', 'error': 'Kernel probe produced no valid result'}
                    if child.returncode or row.get('status') != 'complete':
                        row.update(status='error', returncode=child.returncode,
                                   stdout_tail=child.stdout[-4000:], stderr_tail=child.stderr[-8000:])
                    elif child.stderr:
                        row['warnings'] = child.stderr[-8000:]
                except subprocess.TimeoutExpired as error:
                    def tail(value):
                        return (subprocess_output.decode_output(value, 'utf-8') or '')[-8000:]
                    row = {'backend': backend, 'status': 'error', 'error': 'Kernel probe exceeded 180 seconds',
                           'stdout_tail': tail(error.stdout), 'stderr_tail': tail(error.stderr)}
                except OSError as error:
                    row = {'backend': backend, 'status': 'error', 'error': 'Could not start kernel probe: ' + str(error)}
                result['kernel_probes'].append(row)
                if progress:
                    progress('GPU tests running', index + 1, len(probes))
            result.update(readiness(result['kernel_probes']), identity=identity(hardware, versions),
                          view_root=view_root())
            if temp_root():
                result['temp_root'] = temp_root()  # absent unless the installation's own temporary folder is used
            result['receipt'] = str(receipt_path())
            result['validation'] = 'Small kernels executed on the listed GPU; this does not validate full-video speed, quality or capacity.'
            save(receipt_path(), result)
            if progress:
                progress('GPU tests finished', len(probes), len(probes))
    return result
