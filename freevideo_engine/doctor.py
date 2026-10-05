"""Readiness checks with an isolated CUDA detector."""
import json
import os
import subprocess
import sys
from .hardware import detect, installed_backends
from .paths import vdn_root, base_path, checkpoint_path, comfy_root
from .locking import LOCK_ENV, runtime_lock
from .kernel_capabilities import identity, package_versions, readiness, receipt_path
from .monitoring import save
from . import processes


def doctor(*, probe=False, hardware=None, backends=None):
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
            for backend in [*sorted(backends), 'linear']:
                command = [sys.executable, '-m', 'freevideo_engine.probe', backend]
                try:
                    child = processes.run(command, capture_output=True, text=True, timeout=180,
                                          env=env, pass_fds=(descriptor,))
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
                        return (value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or '')[-8000:]
                    row = {'backend': backend, 'status': 'error', 'error': 'Kernel probe exceeded 180 seconds',
                           'stdout_tail': tail(error.stdout), 'stderr_tail': tail(error.stderr)}
                except OSError as error:
                    row = {'backend': backend, 'status': 'error', 'error': 'Could not start kernel probe: ' + str(error)}
                result['kernel_probes'].append(row)
            result.update(readiness(result['kernel_probes']), identity=identity(hardware, versions))
            result['receipt'] = str(receipt_path())
            result['validation'] = 'Small kernels executed on the listed GPU; this does not validate full-video speed, quality or capacity.'
            save(receipt_path(), result)
    return result
