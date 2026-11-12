"""Install model code at a pinned commit without changing the user's models."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from . import execution_paths
from .long_paths import io_path
from .paths import vdn_root
from . import network, subprocess_output


def install(root=None, *, packages=False):
    dependencies = json.loads((Path(__file__).resolve().parent / 'dependencies.json').read_text(encoding='utf-8'))
    spec = dependencies['vdn']
    root = (root or vdn_root()).expanduser().resolve()
    network.clone(root, spec['url'], spec['commit'])
    head, dirty = network.repository_state(root, 'git', None)
    if head != spec['commit'] or dirty:
        raise RuntimeError('Existing VDN checkout does not match the clean pinned dependency; use another --vdn-root.')
    diffusers = prepare_diffusers(root, dependencies)
    if packages:
        # Install Torch explicitly first; preserve that CUDA build here.
        if not os.environ.get('FREEVIDEO_UV') and importlib.util.find_spec('pip') is None:
            execution_paths.run([sys.executable, '-m', 'ensurepip', '--upgrade'], check=True)
        def install_packages(env, source):
            # The managed installer already has uv, including native SOCKS
            # support; avoid pip's optional PySocks dependency during bootstrap.
            command = ([env['FREEVIDEO_UV'], 'pip', 'install', '--python', sys.executable]
                       if env.get('FREEVIDEO_UV') else [sys.executable, '-m', 'pip', 'install'])
            # uv is a native program; pip runs in this interpreter's own Python.
            run = subprocess_output.run_external if env.get('FREEVIDEO_UV') else subprocess_output.run_python
            if pth_spellable(diffusers):
                target = ['-e', str(diffusers)]
            else:
                # A regular copy carries no path; replace any copy from an older tree.
                target = [str(diffusers), *(['--reinstall-package', 'diffusers'] if env.get('FREEVIDEO_UV')
                                            else ['--force-reinstall'])]
            result = run([*command, *target, '--no-deps'],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print(result.stdout, flush=True)
            if result.returncode:
                raise RuntimeError(result.stdout[-2500:])
        network.package_command(network.installed_plan(), 'pypi', install_packages)
        # src is imported from this pinned checkout by paths.add_vdn(). Its
        # training-package metadata unconditionally requires FA4, whereas the
        # Engine also runs without that optional attention backend.
    print(json.dumps({'vdn_root': str(root), 'commit': head, 'packages_installed': packages,
                      'diffusers_tree': dependencies['diffusers']['patched_tree'],
                      'models_downloaded': False, 'environment_variable': 'FREEVIDEO_VDN_ROOT'}))


def pth_spellable(path, *, windows=None, encoding=None):
    """Whether an editable install may name this folder in a .pth file.

    setuptools writes a src-layout folder into the .pth in the locale code page,
    and Python 3.12 reads it the same way; UTF-8 mode changes neither. Where the
    Windows code page cannot spell the folder, a regular copy is installed
    instead: add_vdn() puts the pinned source first on sys.path either way.
    """
    if not (sys.platform == 'win32' if windows is None else windows):
        return True
    if encoding is None:
        import locale
        encoding = locale.getencoding() if hasattr(locale, 'getencoding') else locale.getpreferredencoding(False)
    try:
        str(path).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def prepare_diffusers(root, dependencies):
    """Build the official two-patch tree atomically; never reset a user checkout."""
    spec = dependencies['diffusers']
    target = root / dependencies['vdn']['diffusers_subdirectory']
    built = False
    state = None
    if io_path(target).exists():
        try:
            state = network.repository_state(target, 'git', None, 'HEAD^{tree}')
        except (subprocess.CalledProcessError, OSError) as error:
            # Git that cannot start is a problem of this machine, not of the folder.
            if not isinstance(error, subprocess.CalledProcessError) and io_path(target / '.git' / 'HEAD').exists():
                raise
            # A folder Git cannot read (its .git removed by a cleaner or a sync
            # tool, objects lost) can never pass the check below, so every retry
            # would fail the same way. Keep it beside the new one and build again.
            network.relocate(target, root / ('engine-diffusers-unreadable-' + str(time.time_ns())))
    if state is None:
        # Where the folder is too deep for Git to work in, build in a short real one.
        scratch = network.git_scratch_folder() if network.git_folder_too_deep(root / 'engine-diffusers-0') else None
        checkout = scratch / 'repo' if scratch else root / ('engine-diffusers-' + str(time.time_ns()))
        try:
            network.clone(checkout, spec['url'], spec['base'])
            patches = sorted(io_path(root / 'diffusers_patches').glob('*.patch'))
            if len(patches) != 2:
                raise RuntimeError('Expected the two pinned VDN Diffusers patches')
            for path in patches:
                # The patch path carries the long-path prefix, which Git cannot open; pass the content instead.
                execution_paths.run(['git', '-c', 'core.longpaths=true', 'apply', '--index', '-'],
                                    cwd=checkout, input=path.read_bytes(), check=True)
            tree = subprocess_output.output_external(['git', '-c', 'core.longpaths=true', 'write-tree'], cwd=checkout).strip()
            if tree != spec['patched_tree']:
                raise RuntimeError('Patched Diffusers source tree does not match the tested dependency')
            execution_paths.run(['git', '-c', 'core.longpaths=true', '-c', 'user.name=VDN Engine dependency setup', '-c', 'user.email=engine@localhost',
                            'commit', '--quiet', '-m', 'Apply pinned OpenVDN MiniMax H3 patches'], cwd=checkout, check=True)
            if scratch:
                partial = root / ('engine-diffusers-' + str(time.time_ns()))
                network.relocate(checkout, partial)
                checkout = partial
            io_path(checkout).rename(io_path(target))
            built = True
        finally:
            if scratch and scratch.exists():
                network.remove_tree(scratch)
    if not built:
        tree, dirty = state
        if tree != spec['patched_tree'] or dirty:
            raise RuntimeError('Existing Diffusers differs from the clean tested tree; use another VDN dependency directory.')
    return target
