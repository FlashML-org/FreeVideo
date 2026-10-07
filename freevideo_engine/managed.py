"""Use the setup environment and model paths without shell activation."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys

from .bootstrap import DEFAULT_ROOT
from . import __version__
from .locking import runtime_lock, LOCK_ENV
from . import processes
from .system import windows


def _prism_requested(command, arguments):
    if command != 'generate':
        return False
    for index, argument in enumerate(arguments):
        if argument == '--model' and index + 1 < len(arguments):
            return arguments[index + 1] == 'prism'
        if argument.startswith('--model='):
            return argument.split('=', 1)[1] == 'prism'
    return False


def command_for(machine, command, arguments):
    if _prism_requested(command, arguments):
        # Prism keeps its own record under machine['models']['prism'];
        # none of the H3 cache/encoder keys apply (they may be absent).
        record = (machine.get('models') or {}).get('prism') or {}
        if not record.get('ready') or not record.get('root'):
            raise ValueError('Prism (preview) is not installed. Rerun setup and select Prism.')
        defaults = dict(cache=record['root'], vram_gib=machine.get('vram_gib'), ram_gib=machine.get('ram_gib'))
        given = {argument.split('=', 1)[0] for argument in arguments if argument.startswith('--')}
        if '--profile' in given:
            defaults.pop('vram_gib', None)
            defaults.pop('ram_gib', None)
        extra = []
        for key, value in defaults.items():
            flag = '--' + key.replace('_', '-')
            if value is not None and flag not in given:
                extra += [flag, str(value)]
        return [machine['python'], '-m', 'freevideo_engine', command, *extra, *arguments]
    defaults = {}
    if command in ('generate', 'bench', 'predict', 'calibrate'):
        defaults.update(cache=machine['cache'])
    if command in ('generate', 'predict'):
        defaults.update(base=machine['base'], checkpoint=machine['checkpoint'])
    if command in ('generate', 'encode', 'calibrate'):
        defaults.update(encoder_python=machine['comfy_python'], encoder_root=machine['comfy_root'],
                        model_paths=machine['model_paths'], encoder=machine['encoder'])
    if command in ('generate', 'encode', 'plan', 'bench', 'predict'):
        defaults.update(vram_gib=machine.get('vram_gib'), ram_gib=machine.get('ram_gib'))
    given = {argument.split('=', 1)[0] for argument in arguments if argument.startswith('--')}
    # Saved profiles already include budgets; explicit command arguments win.
    if '--profile' in given:
        defaults.pop('vram_gib', None)
        defaults.pop('ram_gib', None)
    aliases = {'encoder_python': '--comfy-python', 'encoder_root': '--comfy-root'}
    extra = []
    for key, value in defaults.items():
        flag = '--' + key.replace('_', '-')
        if value is not None and flag not in given and aliases.get(key) not in given:
            extra += [flag, str(value)]
    return [machine['python'], '-m', 'freevideo_engine', command, *extra, *arguments]


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='./freevideo', add_help=False)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('FREEVIDEO_HOME', DEFAULT_ROOT)))
    parser.add_argument('--config', type=Path)
    args, rest = parser.parse_known_args(argv)
    if not rest or rest[0] in ('-h', '--help'):
        print('FreeVideo Engine ' + __version__ + '\n\nUsage: ./freevideo [--root PATH] COMMAND [OPTIONS]\n\n'
              '  setup      Detect hardware, review and install from scratch\n'
              '  test       Run full video/audio tests with a live dashboard\n'
              '  optimize   Reuse test results and validate lightweight local tuning\n'
              '  generate   Generate from --prompt-file or --conditioning\n'
              '  encode     Save reusable text conditioning\n'
              '  plan       Show current inference resource policy\n'
              '  predict    Forecast time and memory for another resolution or length\n'
              '  resource-history  Inspect attempts and acknowledged retries\n'
              '  doctor     Check dependencies and GPU kernels\n'
              '  diagnose   Collect a small local report, including failed installs\n'
              '  bench      Compare attention backends\n\n'
              'Examples:\n  ./freevideo setup --plan\n  ./freevideo test --suite quick\n'
              '  ./freevideo generate --prompt-file prompt.txt --out video.mp4\n\n'
              'Use COMMAND --help for options. Windows: .\\setup.ps1 / .\\test.ps1 / .\\freevideo.ps1; Linux: ./setup.sh / ./test.sh / ./freevideo.')
        return 0
    command, tail = rest[0], rest[1:]
    if command == 'setup':
        from .bootstrap import main as setup
        return setup(['--root', str(args.root), *tail])
    if command == 'diagnose':
        from .diagnostics import main as diagnose
        return diagnose(['--root', str(args.root), *(['--config', str(args.config)] if args.config else []), *tail])
    if command == 'resource-history':
        from .resource_cli import main as history
        if not any(flag.split('=', 1)[0] == '--history' for flag in tail):
            tail += ['--history', str(args.root / 'resource-history.sqlite3')]
        return history(tail)
    if command not in ('test', 'optimize', 'generate', 'encode', 'plan', 'doctor', 'bench',
                       'prepare', 'predict', 'calibrate'):
        parser.error('Unknown command: ' + command)
    if command == 'test' and any(flag in tail for flag in ('-h', '--help')):
        from .testing import main as testing
        return testing(['--help'])
    if command == 'optimize' and any(flag in tail for flag in ('-h', '--help')):
        from .optimize import main as optimize
        return optimize(['--help'])
    config = (args.config or args.root / 'machine.json').expanduser().resolve()
    if not config.is_file():
        parser.error('Run ./setup.sh first or select the installation with --root (missing %s).' % config)
    # Acquire before reading readiness: setup cannot replace dependencies between
    # this check and exec. Keep the lease through every encoder/video child.
    readonly = (command in ('plan', 'predict') or (command in ('test', 'optimize') and '--plan' in tail)
                or (command == 'doctor' and '--probe' not in tail) or '--help' in tail or '-h' in tail)
    snapshot = json.loads(config.read_text(encoding='utf-8'))
    lock_path = os.environ.get('FREEVIDEO_LOCK_PATH', str(Path(snapshot['root']) / 'engine.lock'))
    with nullcontext() if readonly else runtime_lock(lock_path) as descriptor:
        machine = json.loads(config.read_text(encoding='utf-8'))
        if machine != snapshot:
            raise ValueError('Installation changed while starting. Rerun the command.')
        if not machine.get('ready'):
            parser.error('Setup is incomplete. Rerun ./setup.sh with the same --root; saved files are reused.')
        from .testing import environment
        env = environment(machine)
        if descriptor is not None:
            os.set_inheritable(descriptor, True)
            env[LOCK_ENV] = str(descriptor)
            env['FREEVIDEO_LOCK_PATH'] = lock_path
        executable = ([machine['python'], '-m', 'freevideo_engine.' + ('testing' if command == 'test' else 'optimize'), '--config', str(config), *tail]
                      if command in ('test', 'optimize') else command_for(machine, command, tail))
        if windows():
            return processes.run(executable, env=env, pass_fds=() if descriptor is None else (descriptor,)).returncode
        os.execve(machine['python'], executable, env)


def main(argv=None):
    try:
        return _main(argv)
    except BlockingIOError:
        print('Setup, testing or generation is already using this installation/GPU lock. Retry after it finishes.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Cancelled. Workers stopped; existing outputs and reports retained.', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError) as error:
        print('Engine could not start: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
