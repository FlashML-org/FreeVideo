"""Explicit pipe encodings for Python workers and byte capture for native tools."""
import locale
import os
import subprocess
import sys

from . import execution_paths


def python_environment(environ=None):
    """Copy per-child settings without changing the launcher's environment."""
    env = dict(os.environ if environ is None else environ)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return env


def python_command(command):
    command = list(command)
    # The frozen executable has its own argument parser, not Python's. Its
    # --freevideo-module entry configures the streams before dispatching.
    if getattr(sys, 'frozen', False) and str(command[0]) == sys.executable:
        return command
    index = 1
    while index < len(command) and command[index] in ('-B', '-u', '-E', '-s', '-X'):
        index += 2 if command[index] == '-X' else 1
    if command[index:index + 1] == ['-c'] and index + 1 < len(command):  # (-I already leaves the folder out)
        from .installed_entry import INSTALLED_ONLY
        command[index + 1] = INSTALLED_ONLY + command[index + 1]
    # -I/-E and Windows portable ._pth ignore PYTHON* environment settings.
    # The command-line mode still makes redirected stdout/stderr UTF-8.
    return [command[0], '-X', 'utf8', *command[1:]]


def _python_options(options):
    options = dict(options)
    options['env'] = python_environment(options.get('env'))
    options.pop('universal_newlines', None)
    options.update(text=True, encoding='utf-8', errors='backslashreplace')
    return options


def run_python(command, *, runner=None, **options):
    return (runner or execution_paths.run)(python_command(command), **_python_options(options))


def output_python(command, **options):
    return execution_paths.check_output(python_command(command), **_python_options(options))


def decode_output(value, encoding=None):
    """Prefer UTF-8, then the native code page; preserve undecodable bytes.

    Native programs do not share Python's UTF-8 contract. In particular Git
    and uv commonly emit UTF-8 even on legacy Windows installations. An
    unknown byte is displayed as \\xNN, never discarded as empty output.
    """
    if value is None or isinstance(value, str):
        return value
    try:
        text = value.decode('utf-8')
    except UnicodeDecodeError:
        # Support the stdlib-only diagnostic entry on older bootstrap Python,
        # while 3.12 uses the native code page even in Python UTF-8 mode.
        native_encoding = (locale.getencoding() if hasattr(locale, 'getencoding')
                           else locale.getpreferredencoding(False))
        text = value.decode(encoding or native_encoding, errors='backslashreplace')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _byte_options(options):
    options = dict(options)
    encoding = options.pop('encoding', None)
    for name in ('text', 'universal_newlines', 'errors'):
        options.pop(name, None)
    if isinstance(options.get('input'), str):
        options['input'] = options['input'].encode(encoding or 'utf-8')
    return options, encoding


def _decode_error(error, encoding):
    # TimeoutExpired retains partial bytes even for text-mode subprocesses.
    error.output = decode_output(error.output, encoding)
    error.stderr = decode_output(error.stderr, encoding)


def run_external(command, *, runner=None, **options):
    options, encoding = _byte_options(options)
    check = options.pop('check', False)

    def decoded(command, **options):
        try:
            result = (runner or subprocess.run)(command, **options)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            _decode_error(error, encoding)
            raise
        result.stdout = decode_output(getattr(result, 'stdout', None), encoding)
        result.stderr = decode_output(getattr(result, 'stderr', None), encoding)
        return result
    # Decode first: short installation paths are put back in text, not in bytes.
    # A caller's own runner already starts its child from the short folder.
    result = decoded(command, **options) if runner else execution_paths.call(decoded, command, **options)
    if check:
        result.check_returncode()
    return result


def output_external(command, **options):
    options, encoding = _byte_options(options)

    def decoded(command, **options):
        try:
            return decode_output(subprocess.check_output(command, **options), encoding)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            _decode_error(error, encoding)
            raise
    return execution_paths.call(decoded, command, **options)
