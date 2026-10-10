"""Verified, resumable Windows GPU wheels before the package installation step."""
import importlib.metadata
import json
from pathlib import Path

from . import network


def catalog():
    return json.loads(Path(__file__).with_name('torch_downloads.json').read_text(encoding='utf-8'))['windows_cp312']


def select(pins, cuda):
    available = {row['package'] + '==' + row['version']: row for row in catalog()}
    selected = []
    for pin in pins:
        exact = pin if '+' in pin else pin + '+' + cuda
        if exact not in available:
            return None
        selected.append(available[exact])
    return selected


def installed_versions(python):
    # Read metadata without importing torch or starting CUDA. Windows setup uses
    # an isolated venv with the standard Lib/site-packages layout.
    directory = Path(python).parent.parent / 'Lib' / 'site-packages'
    return {dist.metadata['Name'].lower(): dist.version
            for dist in importlib.metadata.distributions(path=[str(directory)])
            if dist.metadata['Name']}


def fetch(row, root, family, networking, env, ui, check):
    path = Path(root) / 'downloads' / 'torch-wheels' / row['filename']
    names = network.ordered(networking, family)
    if not networking.get('sources', {}).get(family) and networking.get('mode') != 'official':
        names = list(row['sources'])
    candidates = [(name, row['sources'][name]) for name in names if name in row['sources']]
    key = 'download-' + row['filename']
    name = 'PyTorch' if row['package'] == 'torch' else row['package']
    source = ''
    ui.begin(key, 'Download PyTorch + CUDA', detail=row['filename'])
    success = False

    def feedback(event):
        nonlocal source
        source = {'official': 'PyTorch', 'nju': 'Nanjing University mirror'}.get(event.get('source'), '')
        ui.update(key, activity='resume' if event['action'] in ('retry', 'route-retry') else '', name=name,
                  detail=row['package'] + (' · Source: ' + source if source else '') +
                  ' · ' + event['action'] + (' · ' + event['reason'] if event.get('reason') else ''))

    def progress(done, total, speed):
        check()
        unit, divisor = ('GiB', 2**30) if total >= 2**30 else ('MiB', 2**20)
        rate = '%.1f MiB/s' % (speed / 2**20) if speed >= 2**20 else '%.1f KiB/s' % (speed / 1024)
        ui.update(key, done=done, total=total, rate=speed, unit='bytes', scope=row['filename'],
                  activity='download', name=name,
                  detail='%s · %.2f / %.2f %s · %s%s' % (row['package'], done/divisor, total/divisor,
                      unit, rate, ' · Source: ' + source if source else ''))

    try:
        check()
        network.download(candidates, path, row['sha256'], progress,
            network=dict(networking, event_callback=feedback, resource_check=check), env=env,
            size=row['bytes'], category=family, low_speed_limit=256 * 1024,
            slow_seconds=30, stall_seconds=30, keep_partial=True)
        success = True
    finally:
        ui.end(key, success=success, detail='Verified and ready' if success else None)
    return path


def install(uv, python, pins, cuda, *, root, networking, env, ui, run, constraints=(), check=None):
    """Return False for a pin set outside the verified catalog; caller keeps its route."""
    selected = select(pins, cuda)
    if selected is None:
        return False
    check = check or (lambda: None)
    check()
    installed = installed_versions(python)
    # The right versions can still hold changed files: a cuDNN library
    # replaced in this environment fails every request with
    # CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH, and setup or repair would
    # otherwise find nothing to do.
    from .runtime_libraries import damaged, reinstall_arguments
    broken = damaged(python)
    if all(installed.get(row['package']) == row['version'] for row in selected) and not broken:
        return True
    family = 'torch-' + cuda + '-' + next(row['version'].split('+')[0] for row in selected if row['package'] == 'torch')

    def install_packages(present, fresh):
        requirements = []
        for row in selected:
            if present.get(row['package']) == row['version'] and row['package'] not in fresh:
                requirements.append(row['package'] + '==' + row['version'])
            else:
                requirements.append(fetch(row, root, family, networking, env, ui, check))
        command = [uv, 'pip', 'install', '--python', python, *reinstall_arguments(fresh), *requirements, *constraints]
        # The large CUDA wheels are now local. Small ordinary dependencies use the
        # measured PyPI mirrors, without accidentally selecting a different torch.
        network.package_command(networking, 'pypi', lambda attempt_env, _: run(command, attempt_env), env)

    install_packages(installed, broken)
    if not broken:
        # A new environment links whatever the uv cache holds, so a file
        # changed there arrives with it. Check once; unpack fresh files if so.
        broken = damaged(python)
        if broken:
            install_packages(installed_versions(python), broken)
    return True


def release_cache(root):
    """Release completed wheel archives only after every environment has used them."""
    directory = Path(root) / 'downloads' / 'torch-wheels'
    if directory.is_symlink() or not directory.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('The GPU package cache points outside this FreeVideo folder; cleanup stopped.')
    for row in catalog():
        path = directory / row['filename']
        if path.is_file() and not path.is_symlink():
            path.unlink()
