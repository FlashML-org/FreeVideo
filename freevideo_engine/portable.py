"""Relocatable offline bundle initialization; the GUI never imports Torch."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import traceback

from .monitoring import save
from .long_paths import io_path, resolved, plain, plain_data, plain_text
from .runtime_alias import checked_real_path


@contextmanager
def child_lease(descriptor):
    from .locking import LOCK_ENV
    previous = os.environ.get(LOCK_ENV)
    os.environ[LOCK_ENV] = str(descriptor)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(LOCK_ENV, None)
        else:
            os.environ[LOCK_ENV] = previous


# What the native text encoder library needs from ComfyUI: the same paths as
# the automatic installation's sparse checkout (bootstrap encoder-source).
ENCODER_LIBRARY = ('comfy/', 'utils/', 'folder_paths.py', 'node_helpers.py', 'LICENSE')


def bundle_name(name):
    """The lexical rules for a file inside a bundle, without touching the disk.

    Inventories list ~58,000 files; resolving each (inside) opens it on Windows.
    """
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Invalid bundle path: ' + str(name))
    return name


def inside(root, name):
    bundle_name(name)
    root = resolved(root)
    path = root / name
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           for p in [path, *path.parents] if p != root and root in p.parents):
        raise ValueError('Bundle files must not redirect outside their directory: ' + name)
    if root not in resolved(path).parents:
        raise ValueError('Bundle path escapes its directory: ' + name)
    return Path(plain(path))


def listing(root):
    """Size and modification time of every file under root, from one directory walk.

    Directory entries carry both, so on Windows no file is opened; opening
    each of ~58,000 files (for stat or its NTFS change time) took minutes under
    real-time scanning. Links are refused.
    """
    root = os.path.join(str(resolved(checked_real_path(root))), '')
    found, pending = {}, [root]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                if entry.is_symlink() or entry.is_junction():
                    raise ValueError('Bundle files must not redirect outside their directory: ' + plain_text(entry.path))
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    info = entry.stat(follow_symlinks=False)
                    found[entry.path[len(root):].replace(os.sep, '/')] = [info.st_size, info.st_mtime_ns]
    return found


def manifest(root):
    value = json.loads((io_path(root) / 'portable.json').read_text(encoding='utf-8'))
    if value.get('schema_version') != 1 or value.get('variant') not in ('rowwise', 'per_tensor', 'int8_convrot'):
        raise ValueError('Invalid FreeVideo portable bundle')
    seen = set()
    for row in value['files']:
        # Links anywhere in the bundle are refused by the directory walk in verify.
        bundle_name(row['path'])
        if row['path'].casefold() in seen or type(row['bytes']) is not int or row['bytes'] < 0:
            raise ValueError('Invalid or duplicate bundle file')
        seen.add(row['path'].casefold())
        if len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256']):
            raise ValueError('Invalid bundle checksum')
    for name in ('python', 'source', 'comfy', 'vdn', 'model_root', 'encoder_root', 'cache'):
        inside(root, value[name])
    return value


def verify(root, value, progress=lambda **kw: None):
    """Hash a file only when it is not as last verified (size, modification time)."""
    from .network import hash_file
    root = resolved(checked_real_path(root))
    ledger = root / 'engine' / 'portable-verified.json'
    try:
        prior = json.loads(ledger.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        prior = {}
    present = listing(root)
    stamps = {}
    total = sum(r['bytes'] for r in value['files'])
    done, started, last_event = 0, time.monotonic(), [0.]
    def report(detail, current=0):
        elapsed = time.monotonic()-started
        if elapsed-last_event[0] >= .2:
            last_event[0] = elapsed
            progress(stage='verify', done=done+current, total=total, detail=detail,
                     elapsed_seconds=elapsed, bytes_per_second=(done+current)/max(.001, elapsed))
    for row in value['files']:
        found = present.get(row['path'])
        if found is None or found[0] != row['bytes']:
            raise ValueError('Missing or incomplete bundled file: ' + row['path'])
        stamp = dict(bytes=found[0], mtime_ns=found[1], sha256=row['sha256'])
        entry = prior.get(row['path'])
        # Earlier ledgers kept more fields; size, time and digest decide.
        known = isinstance(entry, dict) and all(entry.get(k) == v for k, v in stamp.items())
        if not known or found[1] >= time.time_ns()-10**9:
            path = io_path(inside(root, row['path']))
            if hash_file(path, discard_cache=True, progress=lambda current, length: report(row['path'], current)) != row['sha256']:
                raise ValueError('Bundled file failed verification: ' + row['path'])
            info = path.stat()
            if [info.st_size, info.st_mtime_ns] != found:
                raise ValueError('Bundled file changed during verification: ' + row['path'])
        stamps[row['path']] = stamp
        done += row['bytes']
        report(row['path'])
    save(ledger, stamps)
    progress(stage='verify', done=total, total=total, detail='All bundled files verified')


def environment(root, value, environ=None):
    from .comfy_environment import isolated_environment
    root = resolved(root)
    env = isolated_environment(Path(plain(root/'engine')), Path(plain(inside(root, value['source']))), environ)
    env.update(FREEVIDEO_MODEL_ROOT=plain(inside(root, value['model_root'])),
               FREEVIDEO_VDN_ROOT=plain(inside(root, value['vdn'])),
               FREEVIDEO_COMFY_ROOT=plain(inside(root, value.get('encoder_library') or value['comfy'])),
               TORCHINDUCTOR_CACHE_DIR=plain(root/'engine/cache/inductor'),
               TRITON_CACHE_DIR=plain(root/'engine/cache/triton'),
               HF_HOME=plain(root/'engine/cache/huggingface'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    # Bundled Python/Triton provide their DLLs and compiler. An unrelated CUDA
    # installation or build compiler must not override this sealed runtime.
    for key in ('PYTHONHOME', 'CUDA_HOME', 'CUDA_PATH', 'CC', 'CXX'):
        env.pop(key, None)
    python = inside(root, value['python']).parent
    env['PATH'] = os.pathsep.join([plain(python), plain(python/'Scripts'), plain(python/'Lib/site-packages/bin'), env.get('PATH', '')])
    from .triton_compat import environment as compiler_environment
    return compiler_environment(Path(plain(root/'engine')), env)


def own_environment(root, value):
    """environment() for the process that sets it on itself.

    A process started from the runtime's short folder (launch_view) names the
    runtime through that folder, as the environment it was started with did:
    what it starts with the inherited environment imports and loads from there.
    """
    from .triton_compat import own_temporary_environment
    own_temporary_environment(Path(plain(root)) / 'engine')
    from .execution_paths import execution_environment
    return execution_environment(environment(root, value))


def configuration(root, value, hardware):
    root = resolved(root)
    from .prepared_model import preferred_format
    # The GPU's own FP8 granularity, and int8 where it is the faster format.
    allowed = {'per_tensor' if hardware.capability[0] >= 10 else 'rowwise'}
    if preferred_format(hardware) == 'int8_convrot':
        allowed.add('int8_convrot')
    if hardware.capability < (8, 0) or value['variant'] not in allowed:
        raise ValueError('This GPU needs the %s bundle; this archive contains %s. No models were downloaded or changed.'
                         % (' or '.join(sorted(allowed)), value['variant']))
    from . import __version__
    spec = json.loads((io_path(__file__).with_name('dependencies.json')).read_text(encoding='utf-8'))
    models = inside(root, value['model_root'])
    return plain_data(dict(schema_version=1, root=str(root/'engine'), source=str(inside(root, value['source'])),
        environment_layout='unified', system=hardware.system, engine_version=__version__,
        python=str(inside(root, value['python'])), comfy_python=str(inside(root, value['python'])),
        # The encoder's own copy when assembly made one; the bundled ComfyUI otherwise.
        comfy_root=plain(inside(root, value.get('encoder_library') or value['comfy'])), vdn_root=plain(inside(root, value['vdn'])),
        model_root=str(models), encoder_model_root=str(inside(root, value['encoder_root'])),
        base=str(models/'h3-base'), checkpoint=str(models/'stage-dmd-step-250'),
        cache=str(inside(root, value['cache'])), encoder=spec['models']['encoder_file'].split('/')[-1],
        prepared_format='int8_convrot' if value['variant'] == 'int8_convrot' else 'fp8',
        model_paths=str(root/'engine/encoder-paths.yaml'), kernel_capabilities=str(root/'engine/kernel-capabilities.json'),
        gpu_uuid=hardware.gpu_uuid, gpu_name=hardware.gpu_name, model_revision=spec['models']['vdn_revision'],
        vram_gib=None, ram_gib=None, ready=False, portable=True,
        portable_revision=value['source_commit'], setup_run=str(root/'engine/portable-runs'),
        platform_validation='Local kernel checks only; full-video diagnostics remain separate.'))


def initialize(root, progress=lambda **kw: None):
    root = resolved(root)
    value = manifest(root)
    os.environ.update(own_environment(root, value))
    from .long_paths import long_paths_unavailable
    if long_paths_unavailable():
        # Generating would stop at the first file under the real folder; say so before anything is prepared.
        from .runtime_alias import AliasUnavailable
        raise AliasUnavailable('other', OSError('This Windows does not open long paths for the process'))
    from .locking import runtime_lock
    with runtime_lock(root/'engine/engine.lock', inherit=False) as descriptor, child_lease(descriptor):
        verify(root, value, progress)
        progress(stage='gpu', detail='Detecting your GPU and testing acceleration', done=0, total=1)
        from .hardware import detect
        hardware = detect()
        machine = configuration(root, value, hardware)
        save(root/'engine/machine.json', machine)
        save(root/'engine/encoder-paths.yaml', {'freevideo': {
            'base_path': machine['encoder_model_root'], 'text_encoders': 'text_encoders/'}})
        # Reuse only this device/driver/runtime's local kernel receipt. No
        # foreign ready machine.json or precompiled GPU cache ships in a ZIP.
        from .kernel_capabilities import available_backends, readiness, receipt_path
        available_backends(hardware)
        if machine['prepared_format'] == 'int8_convrot':
            rows = json.loads(io_path(receipt_path()).read_text(encoding='utf-8')).get('kernel_probes', [])
            if not readiness(rows).get('int8_ready'):
                raise ValueError('The int8 model needs int8 GPU kernels, and they did not pass on this GPU. '
                                 'No models were changed. Details: ' + plain(receipt_path()))
        machine['ready'] = True
        save(root/'engine/machine.json', machine)
        save(root/'engine/prepared-cache.json', dict(cache=machine['cache'],
             model_revision=machine['model_revision'], verified_at_epoch=time.time()))
        progress(stage='gpu', detail=hardware.gpu_name, done=1, total=1)
    return machine


def connect(root, value, machine, progress=lambda **kw: None, cancelled=None, *, url='http://127.0.0.1:8188',
            on_controller=None, previous=None, lan_access=False, lan_port=None):
    """Use the existing ComfyUI connection/startup behavior without installing.

    previous: the controller of an earlier attempt, whose ComfyUI may still run.
    """
    from .comfy_launcher_runtime import Controller, deploy, node_target
    comfy = io_path(inside(root, value['comfy']))
    if resolved(node_target(comfy)) != resolved(machine['source']):
        # An environment package brings ComfyUI without the FreeVideo node: the
        # launcher source it was imported with backs one, as in any installation.
        deploy(comfy, machine['source'], machine['root'])
    controller = Controller(machine['source'], child_environment=lambda: environment(root, value))
    controller.shortcut_root = Path(plain(root))
    if not machine['gpu_uuid'].startswith(('GPU-', 'MIG-')):
        raise ValueError('The CUDA device did not provide a valid GPU identity; rerun the GPU check.')
    # CUDA UUIDs survive a Windows display-adapter order change. The host and
    # its engine children must see the same device that passed initialization.
    os.environ['CUDA_VISIBLE_DEVICES'] = machine['gpu_uuid']
    os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    if cancelled is not None:
        controller.cancelled = cancelled
        if cancelled.is_set():
            raise RuntimeError('Startup cancelled')
    if previous is not None and previous.owns_server():
        # A ComfyUI this launcher started for an earlier attempt, e.g. one that
        # came up without FreeVideo: connecting restarts it instead of asking to.
        controller.server, controller.server_log = previous.server, previous.server_log
        if isinstance(previous, Controller):
            for name in ('server_address', 'server_url', 'access_proxy', 'access_configuration', 'access_links', 'access_polled'):
                setattr(controller, name, getattr(previous, name))
            previous.server = previous.access_proxy = None
    controller.selection = dict(ready=True, root=plain(comfy), engine=machine['root'],
        source=machine['source'], python=machine['python'], url=url,
        portable=False, separate=False, lan_access=lan_access is True, lan_port=lan_port)
    controller.restore_terminal(machine['root'])
    if on_controller:
        on_controller(controller)
    controller.stage = lambda name, **kw: progress(stage='comfy', detail=kw.get('label', name))
    controller._connect()
    if controller.state.get('status') != 'open':
        raise RuntimeError(controller.state.get('error', 'ComfyUI could not open'))
    return controller.state['url']


def gui(root):
    from .portable_launcher import gui as launcher_gui
    launcher_gui(root)


def main(root=None, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--smoke', action='store_true', help='Check native imports and GUI without touching a GPU or downloading models')
    args = parser.parse_args(argv)
    root = Path(root or Path(sys.argv[0]).resolve().parent)
    if args.smoke:
        value = manifest(root)
        os.environ.update(own_environment(root,value))
        os.environ['CUDA_VISIBLE_DEVICES']=''
        import importlib
        import importlib.metadata
        packages={}
        for name in ('torch','torchvision','torchaudio','triton','sageattention','av','numpy','safetensors','tkinter'):
            importlib.import_module(name)
            packages[name]='imported'
        import torch
        if torch.cuda.is_initialized():raise RuntimeError('Smoke check must not initialize CUDA')
        from .paths import add_vdn
        add_vdn()
        from diffusers import MiniMaxH3Transformer3DModel
        import tkinter as tk
        from . import branding
        window=tk.Tk();branding.theme(window);branding.wordmark(window).pack();window.update();window.destroy()
        save(root/'engine/portable-smoke.json',dict(success=True,python=sys.version,executable=sys.executable,
             system=sys.platform,root=str(root.resolve()),imports=packages,cuda_initialized=False,
             scope='Native imports and Tk window only; no models or GPU generation'))
        return 0
    if args.initialize:
        try:
            initialize(root, lambda **kw: print(json.dumps(kw), flush=True))
        except BaseException:
            traceback.print_exc(); return 1
        return 0
    gui(root)
    return 0
