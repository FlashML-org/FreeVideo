"""Durable launcher preferences, independent of an EXE's unpack directory."""
from contextlib import ExitStack, contextmanager
import json
import os
import re
from pathlib import Path
import shutil
import sys
import time

from .monitoring import save as _save
from .long_paths import io_path, plain_data

TEXT = ('comfy', 'destination', 'engine', 'models', 'python', 'url', 'language', 'offline_runtime')
SELECTION = ('root', 'engine', 'source', 'python', 'url')
ARCHIVE = ('path', 'bytes', 'mtime_ns', 'kind', 'root')
ARCHIVE_IDENTITY = ('path', 'bytes', 'mtime_ns')


def save(path, value):
    # sanitize() already normalizes the other settings. Keep directory choices
    # verbatim, including a user's explicit Windows namespace spelling.
    _save(path, value, plain_paths=False)


def default_language():
    if sys.platform == 'darwin':
        from .macos_preferences import preferred_language
        try:
            language = preferred_language()
            if language:
                return language
        except (OSError, AttributeError, ValueError):
            pass
    import locale
    return (locale.getlocale()[0] or '').lower()


def sanitize(value):
    extra = value.get('extra_model_dirs', []) if isinstance(value, dict) else []
    value = plain_data(value) if isinstance(value, dict) else {}
    result = {k: value[k] for k in TEXT if isinstance(value.get(k), str)}
    for key in ('separate', 'new_comfy', 'sampling_caches', 'lan_access', 'image_model', 'prompt_model', 'prompt_hint_dismissed'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if value.get('model_method') in ('auto', 'manual', 'reuse'):
        result['model_method'] = value['model_method']
    if value.get('environment_method') in ('auto', 'manual'):
        result['environment_method'] = value['environment_method']
    folders = value.get('model_dirs')
    if isinstance(folders, list) and all(isinstance(p, str) for p in folders):
        result['model_dirs'] = list(dict.fromkeys(folders))
    offline = value.get('offline_models')
    if isinstance(offline, list) and all(isinstance(p, str) for p in offline):
        result['offline_models'] = list(dict.fromkeys(offline))
    archives = value.get('imported_archives')
    if isinstance(archives, list):
        result['imported_archives'] = [
            {k: row[k] for k in ARCHIVE} for row in archives
            if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ('path', 'root', 'kind'))
            and all(type(row.get(k)) is int for k in ('bytes', 'mtime_ns'))][-64:]
    kept = value.get('kept_archives')
    if isinstance(kept, list):
        result['kept_archives'] = [
            {k: row[k] for k in ARCHIVE_IDENTITY} for row in kept
            if isinstance(row, dict) and isinstance(row.get('path'), str)
            and all(type(row.get(k)) is int for k in ('bytes', 'mtime_ns'))][-64:]
    record = value.get('installation')
    if isinstance(record, dict) and all(isinstance(record.get(k), str) and record[k] for k in SELECTION):
        result['installation'] = {k: record[k] for k in SELECTION}
        result['installation']['separate'] = record.get('separate') is True
        result['installation']['lan_access'] = record.get('lan_access') is True
        if type(record.get('lan_port')) is int and 1 <= record['lan_port'] <= 65535:
            result['installation']['lan_port'] = record['lan_port']
    if type(value.get('saved_at_ns')) is int:
        result['saved_at_ns'] = value['saved_at_ns']
    resume = value.get('setup')
    if isinstance(resume, dict):
        result['setup'] = {k: resume[k] for k in ('status', 'action', 'phase') if isinstance(resume.get(k), str)}
    if 'extra_model_dirs' in value:
        from .local_models import directory_choices
        try:
            result['extra_model_dirs'] = directory_choices(extra)
        except (OSError, RuntimeError, ValueError, TypeError):
            result['extra_model_dirs'] = []
    return result


def read(path):
    try:
        return sanitize(json.loads(io_path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return {}


KEPT_COPIES = 3


def keep_unreadable(path):
    """Copy a settings file that exists but cannot be parsed aside before a save replaces it."""
    try:
        text = io_path(path).read_text(encoding='utf-8')
        if not text.strip() or isinstance(json.loads(text), dict):
            return None
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        pass
    kept = Path(str(path) + '.bad-' + time.strftime('%Y%m%d-%H%M%S'))
    try:
        shutil.copyfile(io_path(path), io_path(kept))
    except OSError:
        return None
    # A save that keeps failing leaves the file unreadable, so every retry copies it again.
    older = sorted(kept.parent.glob(Path(path).name + '.bad-*'))[:-KEPT_COPIES]
    for stale in older:
        try:
            io_path(stale).unlink()
        except OSError:
            pass
    return kept


def write(path, values, language, selection=None):
    payload = dict(values, language=language, installation=selection)
    save(path, sanitize(payload))


def documents_directory():
    override = os.environ.get('FREEVIDEO_DOCUMENTS_HOME')
    if override:
        return Path(override).expanduser()
    if os.name == 'nt':
        # CSIDL_PERSONAL honors redirected/OneDrive Documents and localized names.
        import ctypes
        from ctypes import wintypes
        buffer = ctypes.create_unicode_buffer(32768)
        function = ctypes.windll.shell32.SHGetFolderPathW
        function.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
        function.restype = ctypes.c_long
        if function(None, 5, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    if os.name != 'nt':
        configured = xdg_documents_directory()
        if configured:
            return configured
    return Path.home() / 'Documents'


def xdg_documents_directory():
    """The Documents folder the desktop chose (localized or moved), from the XDG user-dirs file."""
    value = os.environ.get('XDG_DOCUMENTS_DIR')
    if not value:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        try:
            text = (base / 'user-dirs.dirs').read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return None
        match = re.search(r'^\s*XDG_DOCUMENTS_DIR\s*=\s*"([^"\n]+)"', text, re.M)
        value = match.group(1) if match else None
    if not value:
        return None
    value = value.replace('$HOME', str(Path.home()))
    path = Path(value)
    # An unset folder is written as $HOME itself; that is not a Documents folder.
    if not path.is_absolute() or path == Path.home():
        return None
    return path


class Store:
    def __init__(self, legacy_root, documents=None):
        self.local = Path(legacy_root) / 'comfy-launcher.json'
        try:
            self.primary = (Path(documents) if documents is not None else documents_directory()) / 'FreeVideo' / 'launcher.json'
        except (OSError, RuntimeError, AttributeError):
            self.primary = self.local
        self.paths = list(dict.fromkeys((self.primary, self.local)))

    def read(self):
        candidates = [read(p) for path in self.paths for p in (path, path.with_suffix('.previous.json'))]
        candidates = [value for value in candidates if value]
        return max(candidates, key=lambda v: v.get('saved_at_ns', 0)) if candidates else {}

    def currently_allows(self, key):
        """Permissions require explicit current copies; recovery preferences cannot grant access."""
        values = [read(path) for path in self.paths]
        return bool(values) and all(value.get(key) is True for value in values)

    @contextmanager
    def exclusive(self, wait=10.):
        """Serialize read-merge-write between the launcher and the node panel.

        A lock that cannot be taken in time never stops a save: the settings
        are small and a rare lost update is better than a button that does nothing.
        """
        from .locking import runtime_lock
        stack, deadline = ExitStack(), time.monotonic() + wait
        while True:
            try:
                stack.enter_context(runtime_lock(self.local.with_suffix('.lock'), inherit=False))
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    break
                time.sleep(.05)
            except (OSError, ValueError):
                break
        with stack:
            yield

    def write(self, values, language, selection=None, setup=None):
        with self.exclusive():
            return self.write_locked(values, language, selection, setup)

    def write_locked(self, values, language, selection=None, setup=None):
        # A launcher form opened before a node-panel save does not own this
        # field. Preserve its current value when other preferences are saved.
        values = dict(values)
        if 'extra_model_dirs' not in values:
            current = self.read()
            if 'extra_model_dirs' in current:
                values['extra_model_dirs'] = current['extra_model_dirs']
        payload = sanitize(dict(values, language=language, installation=selection,
                                setup=setup, saved_at_ns=time.time_ns()))
        failures, written = [], []
        for path in self.paths:
            try:
                previous = read(path)
                if previous:
                    save(path.with_suffix('.previous.json'), previous)
                else:
                    keep_unreadable(path)
                save(path, payload)
                written.append(path)
            except OSError as error:
                failures.append((path, error))
        if not written:
            raise failures[0][1]
        return written, failures

    def save_model_directories(self, values):
        from .local_models import directory_choices
        choices = directory_choices(values)
        with self.exclusive():
            saved = self.read()
            saved['extra_model_dirs'] = choices
            self.write_locked(saved, saved.get('language', default_language()),
                              saved.get('installation'), saved.get('setup'))
        return choices


def model_directory_store():
    from .desktop_runtime import launcher_root
    return Store(launcher_root())


def extra_model_directories():
    """Untrusted preferences are validated on every read, including startup."""
    try:
        return model_directory_store().read().get('extra_model_dirs', [])
    except (OSError, RuntimeError, ValueError, TypeError):
        return []


def saved_model_libraries():
    from .local_models import library_roots
    try:
        return library_roots(extra_model_directories(), skip_missing=True)
    except (OSError, RuntimeError, ValueError, TypeError):
        return []
