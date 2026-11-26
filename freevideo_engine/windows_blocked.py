"""What Windows answers when security software, a policy or another program stops a file from being written or
started, by its error code: one table (windows_blocked.json) for the launcher update, setup in Python and
freevideo.ps1, which reads the same file before any Python exists.
"""
import json
import re
from pathlib import Path

_TABLE = json.loads(Path(__file__).with_name('windows_blocked.json').read_text(encoding='utf-8'))
REASONS = {int(code): reason for code, reason in _TABLE['codes'].items()}
RETRYABLE = frozenset(int(code) for code in _TABLE['retry'])
RETRY_WAITS = tuple(int(wait) for wait in _TABLE['retry_waits'])
# uv (Rust) writes "(os error 5)", Python "[WinError 5]"; the text around it is in the system language.
_CODE = re.compile(r'\(os error (\d+)\)|\[WinError (\d+)\]')
# A Python runtime file setup could not write (uv: "failed to unpack `python/vcruntime140.dll` into ...").
PYTHON_FILE_MARKER = 'PYTHON_FILE_BLOCKED'
_UNPACK = re.compile(r'failed to unpack `([^`\r\n]+)`', re.I)
# uv's chain on Windows: "failed to unpack `<full path being written>`", then "failed to unpack `python/DLLs/x.pyd`
# into `<folder>`", then the system's words with "(os error 5)". The line with "into" names the file as the archive has it.
_UNPACK_INTO = re.compile(r'failed to unpack `([^`\r\n]+)` into `', re.I)


# uv could not write a file of a package into its cache or an environment: "failed to rename file from <temporary> to
# <cache entry>" under "Failed to write to the distribution cache", or unpacking, persisting, copying or linking it.
_FILE_WRITE = re.compile(r'Failed to write to the distribution cache|failed to (?:rename|unpack|persist|write|create|copy|hard ?link)\b', re.I)
# The package uv names first: "Failed to read `torch @ file:///...`", "Failed to download `numpy==2.1.0`",
# "Failed to install: torch-2.7.1+cu128-cp312-cp312-win_amd64.whl (torch==2.7.1+cu128)".
_PACKAGE = re.compile(r'Failed to (?:read|download|build|prepare|install|unzip|extract)[^`\r\n]*`([A-Za-z0-9][A-Za-z0-9_.-]*)'
                      r'|Failed to (?:install|unzip wheel): ([A-Za-z0-9][A-Za-z0-9_.]*)-\d', re.I)


def code_in(text):
    """The first Windows error in text that the table knows, or None."""
    for match in _CODE.finditer(text or ''):
        code = int(match.group(1) or match.group(2))
        if code in REASONS:
            return code
    return None


def unpack_blocked(text):
    """(code, file) when uv could not unpack a Python file because Windows refused it, else None."""
    text = text or ''
    unpack = _UNPACK.search(text)
    code = code_in(text[unpack.end():]) if unpack else None
    return (code, (_UNPACK_INTO.search(text) or unpack).group(1)) if code else None


def package_blocked(text):
    """(code, package, file) when uv could not write a file of a package because Windows refused it, else None.
    package and file are '' when uv names none. file is the name of the file Windows refused (for a rename, where it was
    going), never a folder above it, which holds the user's name; an archive member keeps its path inside the archive.
    Only for Windows: elsewhere "os error 5" is an input/output error."""
    text = text or ''
    write = _FILE_WRITE.search(text)
    code = code_in(text[write.end():]) if write else None
    if not code:
        return None
    package = _PACKAGE.search(text)
    member = _UNPACK_INTO.search(text)
    if member:
        name = member.group(1)
    else:
        # The last path before the Windows error: "failed to rename file from <temporary> to <entry>: <words> (os error 5)".
        segment = text[write.end():_CODE.search(text, write.end()).start()]
        cut = max(segment.rfind('\\'), segment.rfind('/'))
        found = re.match(r'[^\s`:\\/]+', segment[cut + 1:]) if cut >= 0 else None
        name = found.group(0) if found else ''
    return code, (package.group(1) or package.group(2)) if package else '', name


def package_file_message(code, package, name=''):
    """The sentence a package step ends with; the same marker as a Python file, so the launcher shows the same card.
    It names the file, which is what the security software's records show."""
    return ('Windows did not let setup write a file of the Python package %s [%s %s] (Windows error %d)%s'
            % (package or '(not named)', PYTHON_FILE_MARKER, REASONS[code], code, ': ' + name if name else ''))


def retry_package(run, *, write, wait):
    """run(); when Windows refused a package file with a code worth waiting for, write a line and try again after each of
    RETRY_WAITS (wait(seconds) is True when setup was cancelled meanwhile). When it still fails, or for a code waiting does
    not change, the error is raised again with package_file_message as its first line."""
    for pause in (*RETRY_WAITS, None):
        try:
            return run()
        except RuntimeError as error:
            blocked = package_blocked(str(error))
            if not blocked:
                raise
            if pause is None or blocked[0] not in RETRYABLE:
                raise blocked_failure(blocked, error) from error
            write('Windows did not let setup write a file of %s (Windows error %d). Trying again in %d s.\n'
                  % (blocked[1] or 'a package', blocked[0], pause))
            if wait(pause):
                raise


def blocked_failure(blocked, error):
    """RuntimeError(package_file_message + the original text), keeping the sources it tried."""
    failure = RuntimeError(package_file_message(*blocked) + '\n' + str(error))
    attempts = getattr(error, 'package_attempts', None)
    if attempts is not None:
        failure.package_attempts = attempts
    return failure


def python_file_message(code, name):
    """The sentence setup ends with; failure_details reads the marker and the reason in brackets."""
    return ('Windows did not let setup write a Python file [%s %s] (Windows error %d): %s'
            % (PYTHON_FILE_MARKER, REASONS[code], code, name))
