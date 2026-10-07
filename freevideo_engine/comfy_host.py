"""Prepare an optional ComfyUI environment, never install into the user's Python."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from . import network, processes
from .comfy_bridge import installation
from .comfy_environment import isolated_environment
from .environments import uv_file_arguments
from .locking import runtime_lock
from .monitoring import save
from .package_progress import PackageOutput
from .terminal_ui import LogProgress, TerminalUI


def prepare(root, comfy, run=None, download=False):
    root, comfy = Path(root).resolve(), Path(comfy).resolve()
    _, machine = installation(environ={'FREEVIDEO_HOME': str(root)})
    with runtime_lock(root / 'launcher' / 'host-setup.lock', inherit=False):
        return _prepare(root, comfy, machine, run, download)


def prepare_for_setup(root, comfy, plan_path):
    """Finish the frontend before models, without marking the engine ready."""
    root, comfy, plan_path = Path(root).resolve(), Path(comfy).resolve(), Path(plan_path).resolve()
    setup = json.loads(plan_path.read_text(encoding='utf-8'))
    frontend = setup.get('frontend') or {}
    if (setup.get('disk_mode') != 'extreme' or Path(setup['root']).resolve() != root
            or not frontend.get('separate') or Path(frontend['root']).resolve() != comfy):
        raise ValueError('Frontend does not match the space-saving installation plan')
    from .system import venv_python
    python = venv_python(root / 'envs' / 'unified', setup['inventory']['hardware']['system'])
    if Path(sys.executable).resolve() != python.resolve():
        raise ValueError('Run frontend preparation with the installed engine Python')
    machine = dict(python=str(python), setup_run=str(plan_path.parent), disk_mode='extreme',
                   git=os.environ.get('FREEVIDEO_GIT'))
    with runtime_lock(root / 'launcher' / 'host-setup.lock', inherit=False):
        return _prepare(root, comfy, machine, None, frontend.get('download', False))


def previous_environment(root, identity):
    """The environment this ComfyUI used before its requirements changed, if still complete."""
    try:
        record = json.loads((root / 'launcher' / 'comfy-host.json').read_text(encoding='utf-8'))
        environment = Path(record['environment'])
        previous = json.loads((environment / 'freevideo-host.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if (isinstance(previous, dict) and all(previous.get(k) == identity[k] for k in ('comfy', 'engine_python', 'python'))
            and environment.parent.resolve() == (root / 'envs').resolve() and python.is_file()):
        return environment
    return None


def _prepare(root, comfy, machine, run, download):
    source = Path(__file__).resolve().parents[1]
    env = isolated_environment(root, source)
    if machine.get('git'):
        env['FREEVIDEO_GIT'] = machine['git']
        env['PATH'] = str(Path(machine['git']).parent) + os.pathsep + env.get('PATH', '')
    env.update(UV_LINK_MODE='hardlink', UV_CACHE_DIR=str(root / 'downloads' / 'uv-cache'), PYTHONUNBUFFERED='1')
    if machine.get('disk_mode') == 'extreme':
        from .install_disk import environment
        env = environment(root, env)
    directory = root / 'launcher' / 'host-runs' / (time.strftime('%Y%m%dT%H%M%S') + '-' + str(time.time_ns()))
    directory.mkdir(parents=True, exist_ok=False)
    try:
        plan = json.loads((Path(machine['setup_run']) / 'plan.json').read_text(encoding='utf-8')).get('network', {})
    except (KeyError, OSError, ValueError):
        plan = {}
    plan = dict(plan, quiet=True, events_path=str(directory / 'network.jsonl'),
                download_settings_path=str(root / 'download-settings.json'))
    ui = TerminalUI('Prepare ComfyUI', plain=True)
    stage_offset, stage_total = int(bool(download)), 5 + int(bool(download))
    count = 0
    package_logs = []

    def log_tag(value):
        value = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value or 'unknown')).strip('._')
        return value[:48] or 'unknown'

    def execute(command, environment, label='Prepare ComfyUI environment'):
        nonlocal count
        if run:
            return run(command, environment)
        count += 1
        key = 'comfy-host-' + str(count)
        package_source = environment.get('FREEVIDEO_PACKAGE_SOURCE')
        package_route = environment.get('FREEVIDEO_PACKAGE_ROUTE')
        if package_source:
            log = directory / ('%02d-command-%s-%s.log' %
                               (count, log_tag(package_source), log_tag(package_route)))
            package_logs.append(str(log))
        else:
            log = directory / ('%02d-command.log' % count)
        progress, child, success = LogProgress(log), None, False
        command, cwd = uv_file_arguments(command, comfy if comfy.is_dir() else root)
        ui.begin(key, label)
        try:
            with log.open('w', encoding='utf-8') as stream:
                with PackageOutput(command, stream, environment) as output:
                    child = processes.popen(command, env=output.env,
                        cwd=cwd, stdin=subprocess.DEVNULL,
                        stdout=output.stdout, stderr=subprocess.STDOUT, supervise=True, start_new_session=True)
                    output.spawned()
                    while child.poll() is None:
                        if machine.get('disk_mode') == 'extreme':
                            from .install_disk import check_floor
                            check_floor((root, comfy))
                        if output.error is not None:
                            raise RuntimeError('Could not retain ComfyUI package output') from output.error
                        try:
                            child.wait(timeout=.3)
                        except subprocess.TimeoutExpired:
                            pass
                        ui.update(key, **progress.read())
                ui.update(key, **progress.read())
            if child.returncode:
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 8192))
                    tail = stream.read().decode('utf-8', errors='replace')
                raise RuntimeError(tail + '\nLog: ' + str(log))
            success = True
        finally:
            if child is not None and child.poll() is None:
                processes.stop(child)
            ui.end(key, success=success, detail=None if success else 'Log: ' + str(log))

    if download:
        ui.phase('Download ComfyUI', 0, stage_total)
        from .comfy_source import download as download_source
        def git_run(label, command, env):
            if 'fetch' in command:
                command = list(command)
                command.insert(command.index('fetch') + 1, '--progress')
            return execute(command, env, 'Download ComfyUI' if 'fetch' in command else 'Prepare ComfyUI files')
        download_source(comfy, plan=plan, env=env, run=git_run)

    ui.phase('Create ComfyUI Python environment', stage_offset, stage_total)
    requirements = comfy / 'requirements.txt'
    if not requirements.is_file():
        raise ValueError('ComfyUI requirements.txt is missing')
    # An environment is reused only for this exact ComfyUI dependency contract.
    identity = dict(comfy=str(comfy), requirements=hashlib.sha256(requirements.read_bytes()).hexdigest(),
                    engine_python=machine['python'], python=sys.version.split()[0])
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    env_root = root / 'envs' / ('comfyui-' + key)
    if not env_root.exists():
        # An update of ComfyUI changes only requirements.txt: update the
        # environment it already uses, PyTorch included as is, instead of
        # downloading and installing another full environment beside it.
        env_root = previous_environment(root, identity) or env_root
    python = env_root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    receipt = env_root / 'freevideo-host.json'
    if not (python.is_file() and receipt.is_file() and json.loads(receipt.read_text(encoding='utf-8')) == identity):
        uv = root / 'tools' / ('uv.exe' if os.name == 'nt' else 'uv' if sys.platform == 'darwin' else 'uv-x86_64-unknown-linux-gnu/uv')
        if not uv.is_file():
            raise ValueError('The engine download tool is missing. Run engine repair first.')
        if not python.is_file():
            execute([uv, 'venv', '--python', machine['python'], env_root], env, 'Create ComfyUI Python environment')
        # Match the already validated CUDA packages. Hardlinked uv cache
        # entries share their disk bytes without sharing a writable env.
        pins = directory / ('native-constraints.txt' if sys.platform == 'darwin' else 'cuda-constraints.txt')
        rows = [name + '==' + importlib.metadata.version(name) for name in ('torch', 'torchvision', 'torchaudio')]
        pins.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        torch_version = importlib.metadata.version('torch')
        cuda = torch_version.split('+', 1)[1] if '+' in torch_version else 'cpu'
        ui.phase('Prepare ComfyUI GPU packages', stage_offset + 1, stage_total)
        def package_install(family, command, label):
            try:
                return network.package_command(plan, family, command, env)
            except network.PackageSourcesError as error:
                error.log_files = list(package_logs)
                error.args = (str(error) + '. Attempt logs: ' + ', '.join(package_logs),)
                raise

        prepared = False
        if os.name == 'nt':
            from .torch_download import install
            def check():
                if machine.get('disk_mode') == 'extreme':
                    from .install_disk import check_floor
                    check_floor((root, comfy))
            prepared = install(uv, python, rows, cuda, root=root, networking=plan, env=env, ui=ui,
                run=lambda args, environment: execute(args, environment, 'Prepare ComfyUI GPU packages'), check=check)
        if not prepared:
            package_install('pypi' if sys.platform == 'darwin' else 'torch-' + cuda,
                lambda environment, _: execute([uv, 'pip', 'install', '--python', python, *rows], environment, 'Prepare ComfyUI GPU packages'),
                'Prepare ComfyUI GPU packages')
        ui.phase('Install ComfyUI packages', stage_offset + 2, stage_total)
        package_install('pypi', lambda environment, _: execute(
            [uv, 'pip', 'install', '--python', python, '-r', requirements, '-c', pins], environment, 'Install ComfyUI packages'),
            'Install ComfyUI packages')
        ui.phase('Check ComfyUI dependencies', stage_offset + 3, stage_total)
        execute([uv, 'pip', 'check', '--python', python], env, 'Check ComfyUI dependencies')
        save(receipt, identity)
    ui.phase('Finish ComfyUI setup', stage_offset + 4, stage_total)
    result = dict(python=str(python), environment=str(env_root), identity=identity)
    save(root / 'launcher' / 'comfy-host.json', result)
    ui.phase('ComfyUI ready', stage_total, stage_total)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--comfy', type=Path, required=True)
    parser.add_argument('--download-comfy', action='store_true', help='Download the pinned ComfyUI application into a new folder')
    parser.add_argument('--setup-plan', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.setup_plan:
        prepare_for_setup(args.root, args.comfy, args.setup_plan)
    else:
        prepare(args.root, args.comfy, download=args.download_comfy)


if __name__ == '__main__':
    main()
