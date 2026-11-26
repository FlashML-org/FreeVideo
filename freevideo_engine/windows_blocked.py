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


def python_file_message(code, name):
    """The sentence setup ends with; failure_details reads the marker and the reason in brackets."""
    return ('Windows did not let setup write a Python file [%s %s] (Windows error %d): %s'
            % (PYTHON_FILE_MARKER, REASONS[code], code, name))
