"""The isolated Python uv unpacked for setup, when its python.exe is gone or does not answer.

uv keeps that Python under UV_PYTHON_INSTALL_DIR. When security software quarantines its python.exe or stops it from
running, every later uv step that looks for Python fails ("Python interpreter not found at ...", or "... returned an
invalid response: EOF while parsing ..."), while `uv python install` still reports the version as installed. Setup then
installs it once more with --reinstall; if it is still broken, the error names the file and the launcher shows the card
for a missing or a blocked Python file. freevideo.ps1 applies the same rules before setup starts
(Get-FreeVideoManagedPythonProblem in windows_bootstrap.ps1).
"""
import re
import subprocess
from pathlib import Path

from . import subprocess_output

# The kinds problem() reports: the file is not there; it ran and printed nothing (or not its version); uv could not
# use it for another reason.
MISSING, SILENT, FAILED = 'missing', 'silent', 'failed'
# The error leads with failure_details.ENVIRONMENT_PYTHON_REMOVED for a missing file (the card for a missing program
# file) and with failure_details.MANAGED_PYTHON_NO_ANSWER otherwise (the card for a Python file Windows refused to run).


def installed(folder, pattern):
    """Whether uv's Python folder holds an installation of this version (a folder named for it)."""
    try:
        return any(Path(folder).glob(pattern))
    except OSError:
        return False


def problem(uv, folder, request, version, env, timeout=30):
    """None when the folder holds no such Python, or it answers with its version; otherwise (kind, path, detail)."""
    folder = Path(folder)
    try:
        found = subprocess_output.run_external([str(uv), '--no-config', 'python', 'find', '--managed-python',
                                                '--no-python-downloads', request],
                                               env=dict(env, UV_PYTHON_INSTALL_DIR=str(folder)), capture_output=True,
                                               timeout=timeout)
    except subprocess.TimeoutExpired:
        # uv waits for the Python it queries: one that hangs instead of answering.
        return SILENT, '', 'uv python find did not finish in %d s' % timeout
    except (OSError, ValueError, subprocess.SubprocessError):
        return None  # uv itself did not run: the steps that need it report that, not this check.
    output = (getattr(found, 'stderr', '') or '') + (found.stdout or '')
    if found.returncode != 0:
        named = re.search(r'at `([^`\r\n]+)`', output)
        path = named[1] if named else ''
        if re.search(r'interpreter not found at', output, re.I):
            return MISSING, path, _line(output)
        if re.search(r'invalid response|EOF while parsing', output, re.I):
            return SILENT, path, _line(output)
        return FAILED, path, _line(output)
    path = (found.stdout or '').strip().splitlines()[-1:] or ['']
    path = path[0].strip()
    if not path or not Path(path).is_file():
        return MISSING, path, 'uv named a Python file that is not there'
    try:
        check = subprocess_output.run_python([path, '-I', '-B', '-c', 'import platform; print(platform.python_version())'],
                                             capture_output=True, timeout=timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Windows refused to start it (security software, a policy) or it did not finish.
        return FAILED, path, type(error).__name__ + (' %s' % getattr(error, 'winerror', '') if getattr(error, 'winerror', None) else '')
    answer = (check.stdout or '').strip()
    if check.returncode == 0 and answer == version:
        return None
    return (SILENT if not answer else FAILED), path, 'exit %s, printed %r' % (check.returncode, answer[:80])


def _line(output):
    """uv's last line that says what went wrong, for the log and the report (no paths are added here)."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return (next((line for line in reversed(lines) if line.startswith('Caused by:')), lines[-1] if lines else ''))[:300]


def message(kind, path):
    """The error once the Python is still broken after it was installed again."""
    from .failure_details import ENVIRONMENT_PYTHON_REMOVED, MANAGED_PYTHON_NO_ANSWER
    if kind == MISSING:
        return ('%s: %s\nThe isolated Python that setup uses was installed again and its file is still missing.'
                % (ENVIRONMENT_PYTHON_REMOVED, path))
    return '%s: %s\nIt was installed again and still does not answer.' % (MANAGED_PYTHON_NO_ANSWER, path)
