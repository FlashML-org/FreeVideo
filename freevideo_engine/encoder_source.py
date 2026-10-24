"""Keep the encoder's pinned ComfyUI source tree whole.

The tree is a sparse Git checkout of one commit, so a file that disappears after
installation (security software, a cleanup tool) is still in the local object
store: put it back from there instead of downloading anything.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

from . import subprocess_output
from .paths import data_root
from .windows_ux import hidden_console

# Read when the native Qwen tokenizer is built. Without vocab.json or merges.txt
# the tokenizers library fails with an error that names neither file.
REQUIRED = ('comfy/text_encoders/qwen25_tokenizer/vocab.json',
            'comfy/text_encoders/qwen25_tokenizer/merges.txt',
            'comfy/text_encoders/qwen25_tokenizer/tokenizer_config.json')


# A Windows command line holds 32767 characters; a deleted tree can name more paths than that.
BATCH = 8000


class EncoderSourceError(RuntimeError):
    """Text encoder files are missing and could not be restored."""


def executable(env=None):
    """The Git to use: the one the installer exported, one on PATH, else the one it recorded.

    Windows installs keep their own MinGit under the installation folder and
    note it only in machine.json, so a machine without a system Git has neither
    of the first two.
    """
    found = (env or os.environ).get('FREEVIDEO_GIT') or shutil.which('git')
    if found:
        return found
    try:
        recorded = json.loads((data_root() / 'machine.json').read_text(encoding='utf-8')).get('git')
    except (OSError, ValueError, AttributeError):
        return None
    return recorded if recorded and Path(recorded).is_file() else None


def missing(root):
    return [name for name in REQUIRED if not (Path(root) / name).is_file()]


def deleted(root, env=None):
    """Tracked files that are gone from the work tree; None if Git cannot say."""
    git = executable(env)
    if not git or not (Path(root) / '.git').exists():
        return None
    try:
        result = subprocess_output.run_external([git, '-C', str(root), 'ls-files', '-d', '-z'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, **hidden_console())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    return [name for name in (result.stdout or '').split('\0') if name]


def restore(root, names, env=None):
    """Check the named tracked files out again; True if every command succeeded."""
    git = executable(env)
    if not git or not names:
        return False
    batches, used = [[]], 0
    for name in names:
        if batches[-1] and used + len(name) + 1 > BATCH:
            batches.append([])
            used = 0
        batches[-1].append(name)
        used += len(name) + 1
    for batch in batches:
        try:
            result = subprocess_output.run_external([git, '-C', str(root), 'checkout', '--', *batch],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, **hidden_console())
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        if result.returncode:
            return False
    return True


def ensure(root, env=None):
    """Restore the tokenizer files if they are gone; the usual case reads three paths only."""
    absent = missing(root)
    if not absent:
        return []
    gone = deleted(root, env)
    # Another ComfyUI layout, or no Git to ask: leave it to the library.
    lost = [name for name in absent if name in (gone or ())]
    if not lost:
        return []
    restore(root, gone, env)
    lost = missing(root)
    if lost:
        raise EncoderSourceError('FreeVideo text encoder files are missing: %s' % ', '.join(lost))
    return gone
