"""Install a pinned ComfyUI application into a newly selected workspace."""
import json
import subprocess
from pathlib import Path

from .long_paths import io_path, resolved, plain, plain_errors
from . import network
from .monitoring import save

RECEIPT = '.freevideo-comfy-source.json'
SOURCE_DISK_BYTES = 2**30


@plain_errors
def specification():
    # The same revision already supplies the native H3 encoder. Fetch the full
    # application separately; never expand or modify the encoder's sparse tree.
    spec = json.loads((io_path(Path(__file__).parent) / 'dependencies.json').read_text(encoding='utf-8'))['encoder']
    return dict(url=spec['comfy_url'], commit=spec['comfy_commit'])


@plain_errors
def validate_target(target):
    target = io_path(target)
    if not target.exists() and not target.is_symlink():
        return
    try:
        owned = json.loads((target / RECEIPT).read_text(encoding='utf-8')) == specification()
    except (OSError, ValueError):
        owned = False
    if not owned or target.is_symlink():
        raise ValueError('This folder already contains ComfyUI or other files. Choose Use existing ComfyUI or another installation folder: ' + plain(target))


@plain_errors
def new_layout(directory):
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError('Choose a folder for the new ComfyUI installation')
    parent = resolved(Path(directory).expanduser())
    if parent.exists() and not parent.is_dir():
        raise ValueError('The installation folder is a file: ' + plain(parent))
    target = parent / 'ComfyUI'
    validate_target(target)
    return dict(root=plain(target), python=None, portable=False, separate=True,
                new_comfy=True, source_spec=specification())


def download(target, *, plan, env, run):
    """Publish a complete checkout with its receipt; retain interrupted stages."""
    target = Path(target)
    validate_target(target)
    spec = specification()
    if target.exists():
        try:
            return network.clone(target, spec['url'], spec['commit'], network=plan, env=env, run=run)
        except ValueError:
            # A folder FreeVideo downloaded (it holds the receipt) that lost files or only
            # differs in permissions or line endings is repaired; the user's edits never are.
            if not restore_untouched(target, spec['commit'], plan=plan, env=env, run=run):
                raise
            return network.clone(target, spec['url'], spec['commit'], network=plan, env=env, run=run)
    stage = target.with_name(target.name + '.freevideo-download')
    # A finished checkout survives cancellation before publication.
    network.clone(stage, spec['url'], spec['commit'], network=plan, env=env, run=run)
    if not all((stage / name).is_file() for name in ('main.py', 'folder_paths.py', 'requirements.txt')) or not (stage / 'comfy_api/latest').is_dir():
        raise ValueError('The downloaded ComfyUI application is incomplete; files retained at ' + str(stage))
    save(stage / RECEIPT, spec)
    if target.exists() or target.is_symlink():
        raise ValueError('The ComfyUI destination changed during installation; the downloaded files are retained: ' + str(stage))
    stage.rename(target)
    return target


RESTORE_BATCH = 100


WAYS_OUT = ('To download a fresh copy, rename or move this folder, then click "Retry installation". '
            'To keep using it as it is, choose "Use existing ComfyUI".')


def different_message(first):
    return first + ' FreeVideo left the folder unchanged. ' + WAYS_OUT


def restore_untouched(target, commit, *, plan, env, run):
    """Bring back the downloaded files an existing ComfyUI lost or only re-wrote; refuse anything else.

    Returns True when the folder now matches the pinned source, False when this cannot
    be judged (not a readable Git checkout, or nothing differs), and raises ValueError
    naming the files when the folder holds another version or edited files.
    """
    if not io_path(Path(target) / '.git').exists():
        return False
    env = dict(env or {}, GIT_TERMINAL_PROMPT='0')
    git = env.get('FREEVIDEO_GIT') or 'git'
    base = [git, '-c', 'core.longpaths=true', '-C', str(target)]
    content = ['-c', 'core.filemode=false', '-c', 'core.autocrlf=false']
    def output(*arguments, settings=()):
        command = [base[0], *settings, *base[1:], *arguments]
        return network.subprocess_output.output_external(command, env=env, timeout=120, **network.hidden_console())
    try:
        head = output('rev-parse', 'HEAD').strip()
        # Files whose content changed; a file that only lost its permissions or got other line endings is not one.
        changed = [row.split('\t', 2)[2] for row in output(
            'diff', '--numstat', '-z', '--ignore-cr-at-eol', '--diff-filter=d', 'HEAD', settings=content).split('\0') if row]
        listed = output('status', '--porcelain', '-z', '--untracked-files=no').split('\0')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, IndexError):
        return False
    if head != commit:
        raise ValueError(different_message('The ComfyUI folder %s holds a different version from the one FreeVideo needs.' % plain(target)))
    if changed:
        shown = ', '.join(changed[:8]) + (', and %d more' % (len(changed) - 8) if len(changed) > 8 else '')
        raise ValueError(different_message('Some files in the ComfyUI folder %s differ from the version FreeVideo needs. '
                                           'Changed files (%d): %s.' % (plain(target), len(changed), shown)))
    names = [row[3:] for row in listed if len(row) > 3]
    if not names:
        return False
    for start in range(0, len(names), RESTORE_BATCH):
        command = [git, '-c', 'core.longpaths=true', '-C', str(target), '-c', 'core.autocrlf=false',
                   'checkout', '--force', commit, '--', *names[start:start + RESTORE_BATCH]]
        if run:
            run('ComfyUI-restore', command, env=env)
        else:
            result = network.subprocess_output.run_external(list(map(str, command)), env=env, stdout=subprocess.PIPE,
                                                            stderr=subprocess.STDOUT, **network.hidden_console())
            if result.returncode:
                raise RuntimeError(result.stdout[-2500:])
    try:
        if output('status', '--porcelain', '--untracked-files=no').strip():
            # The file system cannot hold permissions; Git is told to stop comparing them.
            output('config', 'core.fileMode', 'false')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    network.event(plan or {}, category='git', file=Path(target).name, action='restored', commit=commit, files=len(names))
    return True
