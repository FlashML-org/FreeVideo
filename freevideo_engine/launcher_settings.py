"""Durable launcher preferences, independent of an EXE's unpack directory."""
import json
import os
from pathlib import Path
import time

from .monitoring import save

TEXT = ('comfy', 'destination', 'engine', 'models', 'python', 'url', 'language', 'offline_runtime')
SELECTION = ('root', 'engine', 'source', 'python', 'url')


def sanitize(value):
    value = value if isinstance(value, dict) else {}
    result = {k: value[k] for k in TEXT if isinstance(value.get(k), str)}
    for key in ('separate', 'new_comfy'):
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
    record = value.get('installation')
    if isinstance(record, dict) and all(isinstance(record.get(k), str) and record[k] for k in SELECTION):
        result['installation'] = {k: record[k] for k in SELECTION}
        result['installation']['separate'] = record.get('separate') is True
    if type(value.get('saved_at_ns')) is int:
        result['saved_at_ns'] = value['saved_at_ns']
    resume = value.get('setup')
    if isinstance(resume, dict):
        result['setup'] = {k: resume[k] for k in ('status', 'action', 'phase') if isinstance(resume.get(k), str)}
    return result


def read(path):
    try:
        return sanitize(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return {}


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
    return Path.home() / 'Documents'


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

    def write(self, values, language, selection=None, setup=None):
        payload = sanitize(dict(values, language=language, installation=selection,
                                setup=setup, saved_at_ns=time.time_ns()))
        failures, written = [], []
        for path in self.paths:
            try:
                previous = read(path)
                if previous:
                    save(path.with_suffix('.previous.json'), previous)
                save(path, payload)
                written.append(path)
            except OSError as error:
                failures.append((path, error))
        if not written:
            raise failures[0][1]
        return written, failures
