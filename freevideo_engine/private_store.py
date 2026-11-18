"""FreeVideo's own private files: what may be deleted, and the one function that deletes them.

The user's uploads and the page's temporary pictures live in a few folders of ComfyUI's input
directory under names only FreeVideo writes. A file there is kept while anything holds it: a
kept work, a queued or running job, a card, a project, a conversation, a saved workflow or a page
that has it open. Once nothing does, it goes for good.

Every deletion goes through ``safe_remove``, which refuses anything outside FreeVideo's own names
and folders, links, junctions and other reparse points, files with more than one hard link, files on
another volume, read-only files and anything that resolves outside the input directory, even when
ComfyUI's input directory is a folder of the user's own. A folder is removed only when it is empty;
a folder holding anything FreeVideo did not write is left alone whole.

Names written to disk never carry a prompt or an original file name: ``name`` gives a keyed hash
with a random salt kept on this machine.

Scans of earlier files only list what they would remove until deletion is switched on
(``settings.json``); a user's explicit delete removes what provably belongs only to the deleted item.
"""
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import time

REPARSE_POINT = 0x400
HEX32 = '[0-9a-f]{32}'
UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
# A folder and file name FreeVideo's uploads use: one file per folder.
LEAF = r'[^/\\:\x00]{1,255}'
# FreeVideo's own names in ComfyUI's input directory, by kind. Nothing else there is ever touched.
PATTERNS = (
    ('upload', re.compile(r'freevideo/(' + HEX32 + ')/' + LEAF)),
    ('page', re.compile(r'freevideo-image-ref/(' + UUID + r')\.(?:png|jpg|webp)')),
    ('page', re.compile(r'freevideo-image-(?:reference|mask)-(' + UUID + r')\.png')),
    # A picture made for a card not saved yet.
    ('draft', re.compile(r'freevideo-draft/(' + UUID + r')\.(?:jpg|png)')),
    ('mask', re.compile(r'freevideo-image/(' + HEX32 + r')\.png')),
    ('asset', re.compile(r'freevideo/agent/assets/([0-9a-f]{64})\.(?:png|jpg|jpeg|webp)')),
    ('asset', re.compile(r'freevideo/agent/assets/\.([0-9a-f]{64})\.(?:png|jpg|jpeg|webp)\.json')),
    # What the Agent made for one card or job: frames, edits, sheets and voice clips, in that card's or job's folder.
    ('agent', re.compile(r'freevideo/agent/(?!assets/)([A-Za-z0-9_-]{1,64})/(?:' + LEAF + '/){0,3}' + LEAF)),
)
# The derived caches (encodings, embeddings, latents) under the engine's input-cache folder.
CACHE_PATTERNS = (
    re.compile(r'[a-z0-9-]{1,40}/[0-9a-f]{64}/[0-9a-f]{64}\.json'),
    re.compile(r'[a-z0-9-]{1,40}/[0-9a-f]{64}/[0-9a-f]{38}-[0-9a-f]{16}\.previous\.json'),
    re.compile(r'[a-z0-9-]{1,40}/[0-9a-f]{64}/(?:[0-9a-f]{64}|[0-9a-f]{31}-[0-9a-f]{32})\.pt'),
)
# FreeVideo's own folders stay even when empty; only a folder of one upload goes with its file.
KEPT_FOLDERS = ('freevideo', 'agent', 'assets', 'freevideo-image-ref', 'freevideo-image', 'freevideo-draft')
# A page's temporary pictures are kept a day at most; an upload nothing holds, a week.
PAGE_GRACE = 24 * 3600.
UPLOAD_GRACE = 7 * 24 * 3600.
PIN_LIFETIME = UPLOAD_GRACE
PIN_LIMIT = 64
PIN_FILES = 512
# Taken by an upload that reuses an existing file and by every deletion, so a file is never handed
# out again in the moment it is being removed.
LOCK = threading.RLock()
_SALTS = {}


def store_root(root=None):
    if root is not None:
        return Path(root)
    from .paths import data_root
    return data_root() / 'private-store'


def salt(root=None):
    """32 random bytes made once per installation; never leaves this machine."""
    folder = store_root(root)
    key = str(folder)
    with LOCK:
        if key in _SALTS:
            return _SALTS[key]
        path = folder / 'salt'
        try:
            value = path.read_bytes()
        except FileNotFoundError:
            value = b''
        if len(value) != 32:
            folder.mkdir(parents=True, exist_ok=True)
            fresh = os.urandom(32)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
            except FileExistsError:
                value = path.read_bytes()
                if len(value) != 32:
                    raise ValueError('Damaged private salt') from None
            else:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(fresh)
                value = fresh
        _SALTS[key] = value
        return value


def name(kind, data, *, root=None):
    """A keyed hash of ``data`` for file names and cache keys: no prompt or file name can be read back from it."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hmac.new(salt(root), kind.encode('utf-8') + b'\0' + bytes(data), hashlib.sha256).hexdigest()


def hasher(kind, *, root=None):
    """An HMAC to feed in pieces; its hexdigest() equals ``name(kind, all the pieces)``."""
    return hmac.new(salt(root), kind.encode('utf-8') + b'\0', hashlib.sha256)


def name_file(kind, path, *, root=None):
    """``name`` of a file's bytes, read in blocks."""
    value = hasher(kind, root=root)
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def classify(relative):
    """(kind, key) for one of FreeVideo's own names relative to the input directory, else None."""
    if not isinstance(relative, str) or '\\' in relative or relative.startswith('/'):
        return None
    if any(part in ('', '.', '..') for part in relative.split('/')):
        return None
    for kind, pattern in PATTERNS:
        found = pattern.fullmatch(relative)
        if found:
            return kind, found.group(1)
    return None


def redacted(relative, *, root=None):
    """A path for logs: an upload's own file name is replaced by a hash, since it may be the user's file name."""
    found = classify(relative)
    if found and found[0] == 'upload':
        leaf = relative.rsplit('/', 1)[1]
        suffix = PurePosixPath(leaf).suffix.lower()[:8]
        return 'freevideo/%s/%s%s' % (found[1], name('log', leaf, root=root)[:12], suffix if re.fullmatch(r'\.[a-z0-9]{1,7}', suffix) else '')
    return relative


def _norm(value):
    from .system import windows
    value = os.path.normpath(os.fspath(value))
    return os.path.normcase(value) if windows() else value


def _inside(child, parent):
    child, parent = _norm(child), _norm(parent)
    try:
        return child != parent and os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & REPARSE_POINT)


def _base(base):
    from .long_paths import io_path
    path = io_path(Path(base).absolute())
    return Path(os.path.realpath(path))


def check(base, relative, *, patterns=None):
    """The checked path of one of FreeVideo's own files under ``base`` with its lstat, or (None, reason)."""
    if patterns is None:
        if classify(relative) is None:
            return None, 'not-ours'
    elif not isinstance(relative, str) or not any(p.fullmatch(relative) for p in patterns):
        return None, 'not-ours'
    try:
        root = _base(base)
        root_info = os.stat(root)
    except OSError:
        return None, 'no-base'
    path = root
    parts = relative.split('/')
    for index, part in enumerate(parts):
        path = path / part
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None, 'gone'
        except OSError:
            return None, 'unreadable'
        if _link(info):
            return None, 'link'
        if info.st_dev != root_info.st_dev:
            return None, 'other-volume'
        last = index == len(parts) - 1
        if not last and not stat.S_ISDIR(info.st_mode):
            return None, 'not-folder'
    if not stat.S_ISREG(info.st_mode):
        return None, 'not-file'
    if info.st_nlink > 1:
        return None, 'hard-link'
    if not info.st_mode & stat.S_IWUSR:
        return None, 'read-only'
    try:
        real = os.path.realpath(path)
    except OSError:
        return None, 'unreadable'
    if not _inside(real, root) or _norm(real) != _norm(path):
        return None, 'outside'
    return (path, info), None


def safe_remove(base, relative, *, dry_run=False, patterns=None, folders=1):
    """Remove one of FreeVideo's own files under ``base`` and then up to ``folders`` of its now empty parents.

    Returns (status, bytes): 'removed' or 'would-remove' with the file's size, else the reason it stayed
    with 0. Nothing outside ``base`` or FreeVideo's names is ever removed.
    """
    with LOCK:
        checked, reason = check(base, relative, patterns=patterns)
        if checked is None:
            return reason, 0
        path, info = checked
        if dry_run:
            return 'would-remove', info.st_size
        try:
            os.unlink(path)
        except FileNotFoundError:
            return 'gone', 0
        except PermissionError:
            return 'in-use', 0
        except OSError:
            return 'failed', 0
        root = _base(base)
        parent = path.parent
        for _ in range(folders):
            if not _inside(parent, root) or parent.name in KEPT_FOLDERS:
                break
            try:
                with os.scandir(parent) as entries:
                    if any(entries):
                        break
                os.rmdir(parent)
            except OSError:
                break
            parent = parent.parent
        return 'removed', info.st_size


def upload_folder(base, key):
    """The relative path of the one file in input/freevideo/<key>/, or (None, reason) when the folder holds
    anything else: a second file, a folder, a link, or nothing FreeVideo wrote."""
    if not re.fullmatch(HEX32, key or ''):
        return None, 'not-ours'
    try:
        root = _base(base)
        folder = root / 'freevideo' / key
        info = os.lstat(folder)
        if _link(info) or not stat.S_ISDIR(info.st_mode):
            return None, 'link'
        with os.scandir(folder) as entries:
            rows = list(entries)
    except FileNotFoundError:
        return None, 'gone'
    except OSError:
        return None, 'unreadable'
    if len(rows) != 1:
        return None, 'unknown-content'
    relative = 'freevideo/%s/%s' % (key, rows[0].name)
    if classify(relative) != ('upload', key):
        return None, 'unknown-content'
    return relative, None


def remove_upload(base, key, *, dry_run=False):
    """Remove input/freevideo/<key>/ when it holds exactly one plain file; see ``upload_folder``."""
    with LOCK:
        relative, reason = upload_folder(base, key)
        if relative is None:
            return reason, 0, None
        status, size = safe_remove(base, relative, dry_run=dry_run)
        return status, size, relative


# Owners: who holds which of these files, written by the code that made the holding, so a later delete
# can tell what belonged only to the item the user deleted.

def _owner_path(owner, root=None):
    return store_root(root) / 'owners' / (name('owner', owner, root=root) + '.json')


def _read(path):
    try:
        if os.path.getsize(path) > 4 * 1024 * 1024:
            return None
        with open(path, encoding='utf-8') as stream:
            value = json.load(stream)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(path, value):
    from .monitoring import save
    save(path, value, plain_paths=False)


def owner(owner_id, *, root=None):
    """The record of what ``owner_id`` holds: {'refs': [...], 'derived': [...], 'names': {...}}."""
    # No owner has been written yet: nothing to read, and no salt is made just to look.
    value = _read(_owner_path(owner_id, root)) or {} if (store_root(root) / 'owners').is_dir() else {}
    return dict(refs=[r for r in value.get('refs', []) if classify(r)],
                derived=[d for d in value.get('derived', []) if _derived_ok(d)],
                names={k: v for k, v in (value.get('names') or {}).items()
                       if classify(k) and isinstance(v, str) and len(v) <= 255})


def _derived_ok(row):
    return (isinstance(row, str) and any(p.fullmatch(row) for p in CACHE_PATTERNS[:1]))


def hold(owner_id, *, refs=(), derived=(), names=None, root=None):
    """Add files to what ``owner_id`` holds. ``refs`` are input paths of FreeVideo's own names, ``derived``
    are input-cache receipts (<kind>/<identity>/<receipt>.json), ``names`` the display names of refs."""
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 512:
        raise ValueError('Invalid owner')
    with LOCK:
        value = owner(owner_id, root=root)
        refs = [r for r in refs if classify(r)]
        derived = [d for d in derived if _derived_ok(d)]
        value['refs'] = list(dict.fromkeys(value['refs'] + refs))
        value['derived'] = list(dict.fromkeys(value['derived'] + derived))
        for key, label in (names or {}).items():
            if classify(key) and isinstance(label, str) and label:
                value['names'][key] = label[:255]
        _write(_owner_path(owner_id, root), dict(owner=owner_id, **value))


def move(source, target, *, root=None):
    """Hand everything ``source`` holds to ``target`` (a job's files to the work it made)."""
    with LOCK:
        value = owner(source, root=root)
        hold(target, refs=value['refs'], derived=value['derived'], names=value['names'], root=root)
        try:
            os.unlink(_owner_path(source, root))
        except FileNotFoundError:
            pass


def owners(root=None):
    """Every owner record, as {owner id: record}."""
    folder = store_root(root) / 'owners'
    result = {}
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return result
    for entry in entries:
        if not re.fullmatch('[0-9a-f]{64}\\.json', entry.name) or not entry.is_file(follow_symlinks=False):
            continue
        value = _read(entry.path)
        if value and isinstance(value.get('owner'), str):
            result[value['owner']] = owner(value['owner'], root=root)
    return result


def evict(cache_root, receipt, *, dry_run=False):
    """Remove one input-cache receipt, its earlier copies and the file it names when no other receipt does.

    ``receipt`` is <kind>/<identity>/<receipt>.json under ``cache_root``. Returns the bytes removed."""
    if not _derived_ok(receipt):
        return 0
    kind_identity, leaf = receipt.rsplit('/', 1)
    folder = _base(cache_root) / kind_identity
    value = _read(folder / leaf) or {}
    payload = value.get('file') if isinstance(value.get('file'), str) else None
    freed = 0
    with LOCK:
        status, size = safe_remove(cache_root, receipt, dry_run=dry_run, patterns=CACHE_PATTERNS, folders=0)
        freed += size
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            names = []
        for other in names:
            if other.startswith(leaf[:38] + '-') and other.endswith('.previous.json'):
                freed += safe_remove(cache_root, kind_identity + '/' + other, dry_run=dry_run,
                                     patterns=CACHE_PATTERNS, folders=0)[1]
        if payload and Path(payload).name == payload:
            # Receipts share a payload when their inputs encode to the same bytes; it goes with the last one.
            users = [other for other in names if other.endswith('.json') and not other.endswith('.previous.json')
                     and other != leaf and (_read(folder / other) or {}).get('file') == payload]
            if not users:
                freed += safe_remove(cache_root, kind_identity + '/' + payload, dry_run=dry_run,
                                     patterns=CACHE_PATTERNS, folders=1)[1]
    return freed


def release(owner_id, *, input_root=None, cache_root=None, keep=frozenset(), dry_run=False, root=None):
    """Forget ``owner_id``, then remove what it held that no other owner holds and ``keep`` does not name.

    ``keep`` is the set of (kind, key) tokens other records still use (``private_refs.scan``); a file
    that is only inferred, not recorded here, is never removed by this. Returns a summary."""
    removed, kept, freed = [], [], 0
    with LOCK:
        value = owner(owner_id, root=root)
        if not value['refs'] and not value['derived']:
            return dict(removed=removed, kept=kept, freed_bytes=freed)
        try:
            os.unlink(_owner_path(owner_id, root))
        except FileNotFoundError:
            pass
        others = owners(root)
        held_refs = {classify(r) for rec in others.values() for r in rec['refs']}
        held_derived = {d for rec in others.values() for d in rec['derived']}
        for relative in value['refs']:
            token = classify(relative)
            if token in held_refs or token in keep or input_root is None:
                kept.append(redacted(relative, root=root))
                continue
            if token[0] == 'upload':
                status, size, _ = remove_upload(input_root, token[1], dry_run=dry_run)
            else:
                status, size = safe_remove(input_root, relative, dry_run=dry_run)
            (removed if status in ('removed', 'would-remove') else kept).append(redacted(relative, root=root))
            freed += size
        for receipt in value['derived']:
            if receipt in held_derived or cache_root is None:
                continue
            freed += evict(cache_root, receipt, dry_run=dry_run)
    return dict(removed=removed, kept=kept, freed_bytes=freed)


# Pins: the files each open page shows right now, which only the page knows. A page's newest list
# replaces its last one; a page that closed keeps its last list for a week.

def pin(page, files, *, root=None, now=None):
    if not isinstance(page, str) or not re.fullmatch('[A-Za-z0-9_-]{8,64}', page):
        raise ValueError('Invalid page')
    if not isinstance(files, list) or len(files) > PIN_FILES:
        raise ValueError('Invalid files')
    now = time.time() if now is None else now
    tokens = sorted({'%s:%s' % found for found in map(classify, files) if found})
    path = store_root(root) / 'pins.json'
    with LOCK:
        value = _read(path) or {}
        value = {key: row for key, row in value.items() if isinstance(row, dict)
                 and isinstance(row.get('t'), (int, float)) and now - row['t'] < PIN_LIFETIME}
        value[page] = dict(t=now, files=tokens)
        while len(value) > PIN_LIMIT:
            del value[min(value, key=lambda key: value[key]['t'])]
        _write(path, value)
    return len(tokens)


def pinned(*, root=None, now=None):
    now = time.time() if now is None else now
    value = _read(store_root(root) / 'pins.json') or {}
    result = set()
    for row in value.values():
        if isinstance(row, dict) and isinstance(row.get('t'), (int, float)) and now - row['t'] < PIN_LIFETIME:
            for token in row.get('files', []):
                if isinstance(token, str) and ':' in token:
                    result.add(tuple(token.split(':', 1)))
    return result


# Settings and the scan of earlier files.

def set_setting(key, value, *, root=None):
    path = store_root(root) / 'settings.json'
    with LOCK:
        settings = _read(path) or {}
        settings[key] = value
        _write(path, settings)


def deletion_enabled(root=None):
    """Whether a scan removes what it finds. It does unless Settings turned it off (sweep_delete false)."""
    value = _read(store_root(root) / 'settings.json') or {}
    return value.get('sweep_delete') is not False


def candidates(input_root, *, now=None, grey=None):
    """FreeVideo's own files in the input directory with their kind, key, size and age; nothing else is listed."""
    now = time.time() if now is None else now
    try:
        root = _base(input_root)
    except OSError:
        return []
    rows = []

    def add(relative, kind_key):
        try:
            info = os.lstat(root / relative)
        except OSError:
            return
        if stat.S_ISREG(info.st_mode) and not _link(info):
            rows.append(dict(path=relative, kind=kind_key[0], key=kind_key[1], bytes=info.st_size,
                             age=now - info.st_mtime))

    def listing(folder):
        try:
            info = os.lstat(folder)
            if _link(info) or not stat.S_ISDIR(info.st_mode):
                return []
            return sorted(os.listdir(folder))
        except OSError:
            return []

    for leaf in listing(root / 'freevideo'):
        if re.fullmatch(HEX32, leaf):
            relative, _ = upload_folder(input_root, leaf)
            if relative:
                add(relative, ('upload', leaf))
    for folder in ('freevideo-image-ref', 'freevideo-draft'):
        for leaf in listing(root / folder):
            found = classify(folder + '/' + leaf)
            if found:
                add(folder + '/' + leaf, found)
    for leaf in listing(root):
        found = classify(leaf)
        if found and found[0] == 'page':
            add(leaf, found)
    for leaf in listing(root / 'freevideo-image'):
        found = classify('freevideo-image/' + leaf)
        if found and (grey or is_mask)(root / 'freevideo-image' / leaf):
            add('freevideo-image/' + leaf, found)
    for leaf in listing(root / 'freevideo' / 'agent' / 'assets'):
        found = classify('freevideo/agent/assets/' + leaf)
        if found:
            add('freevideo/agent/assets/' + leaf, found)
    for identity in listing(root / 'freevideo' / 'agent'):
        if identity == 'assets':
            continue
        for relative in _agent_files(root, identity):
            found = classify(relative)
            if found == ('agent', identity):
                add(relative, found)
    return rows


def _agent_files(root, identity, depth=4):
    """The files under input/freevideo/agent/<identity>, never through a link; a folder holding a link is skipped whole."""
    folder = root / 'freevideo' / 'agent' / identity
    found, pending = [], [(folder, 'freevideo/agent/' + identity, 0)]
    while pending:
        current, prefix, level = pending.pop()
        try:
            info = os.lstat(current)
            if _link(info) or not stat.S_ISDIR(info.st_mode):
                return []
            entries = sorted(os.listdir(current))
        except OSError:
            return []
        for leaf in entries:
            try:
                info = os.lstat(current / leaf)
            except OSError:
                return []
            if _link(info):
                return []
            if stat.S_ISDIR(info.st_mode):
                if level + 1 >= depth:
                    return []
                pending.append((current / leaf, prefix + '/' + leaf, level + 1))
            elif stat.S_ISREG(info.st_mode):
                found.append(prefix + '/' + leaf)
    return found


def is_mask(path):
    """A point-selection mask: a grey PNG. Colour PNGs of the same shape of name are pictures from before
    generated names, which the image history lists; those are works and are never touched."""
    try:
        with open(path, 'rb') as stream:
            head = stream.read(26)
    except OSError:
        return False
    return len(head) == 26 and head[:8] == b'\x89PNG\r\n\x1a\n' and head[25] in (0, 4)


def grace(kind):
    return PAGE_GRACE if kind in ('page', 'draft') else UPLOAD_GRACE


def sweep(input_root, referenced, *, root=None, now=None, dry_run=None, released=frozenset()):
    """Remove (or with ``dry_run`` only list) FreeVideo's own input files nothing references, past their grace.

    ``referenced`` is the set of (kind, key) tokens that records, jobs and pages still use; ``released``
    names tokens whose owner the user deleted, which need no grace. The summary is also written to
    ``sweep-log.json`` without file names a user chose."""
    now = time.time() if now is None else now
    if dry_run is None:
        dry_run = not deletion_enabled(root)
    held = set(referenced) | pinned(root=root, now=now)
    for record in owners(root).values():
        held.update(classify(r) for r in record['refs'])
    items, total, files = [], 0, 0
    for row in candidates(input_root, now=now):
        token = (row['kind'], row['key'])
        if token in held:
            continue
        if token not in released and row['age'] < grace(row['kind']):
            continue
        if row['kind'] == 'upload':
            status, size, _ = remove_upload(input_root, row['key'], dry_run=dry_run)
        else:
            status, size = safe_remove(input_root, row['path'], dry_run=dry_run, folders=4 if row['kind'] == 'agent' else 1)
        if status in ('removed', 'would-remove'):
            files += 1
            total += size
        items.append(dict(path=redacted(row['path'], root=root), kind=row['kind'], bytes=row['bytes'], status=status))
    summary = dict(time=now, dry_run=dry_run, files=files, bytes=total, items=items[:2000])
    try:
        _write(store_root(root) / 'sweep-log.json', summary)
    except OSError:
        pass
    logging.info('[FreeVideo] Private file cleanup%s: %d files, %.1f MB.', ' (listed only)' if dry_run else '',
                 files, total / 1e6)
    return summary


def last_sweep(root=None):
    value = _read(store_root(root) / 'sweep-log.json') or {}
    return dict(time=value.get('time'), dry_run=value.get('dry_run', True), files=value.get('files', 0),
                bytes=value.get('bytes', 0))


# Prompts of works deleted for good, as keyed hashes, so an open page can drop them from its own history unless a
# kept work or a saved preset still uses them. A month is enough for every page to have been opened again.
FORGOTTEN_DAYS = 30


def forget_prompts(hashes, *, root=None, now=None):
    hashes = {h for h in hashes if isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h)}
    if not hashes:
        return
    now = time.time() if now is None else now
    path = store_root(root) / 'forgotten.json'
    with LOCK:
        value = _read(path) or {}
        value = {key: when for key, when in value.items()
                 if isinstance(when, (int, float)) and now - when < FORGOTTEN_DAYS * 86400}
        value.update(dict.fromkeys(hashes, now))
        _write(path, value)


def forgotten(texts, *, root=None, now=None):
    """Of the given prompts, the indexes of those a deleted work had and no kept work has."""
    now = time.time() if now is None else now
    value = _read(store_root(root) / 'forgotten.json') or {}
    live = {key for key, when in value.items() if isinstance(when, (int, float)) and now - when < FORGOTTEN_DAYS * 86400}
    if not live:
        return []
    return [index for index, text in enumerate(texts)
            if isinstance(text, str) and text.strip() and name('prompt', text.strip(), root=root) in live]


# The derived caches (encodings, embeddings, latents): their size for Settings, a one-click clear, and removing the
# entries an earlier version saved with the prompt as text.

def _tree_files(base):
    """Regular files under ``base`` that are its own: never through or of a link, junction or hard link, never on
    another volume. A folder holding a link is skipped whole."""
    try:
        root = _base(base)
        root_info = os.stat(root)
    except OSError:
        return []
    found = []
    for parent, folders, files in os.walk(root):
        keep = []
        for leaf in folders:
            try:
                info = os.lstat(os.path.join(parent, leaf))
            except OSError:
                continue
            if not _link(info) and info.st_dev == root_info.st_dev:
                keep.append(leaf)
        folders[:] = keep
        for leaf in files:
            path = os.path.join(parent, leaf)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and not _link(info) and info.st_nlink == 1 and info.st_dev == root_info.st_dev:
                found.append((Path(path), info.st_size))
    return found


def cache_usage(roots):
    rows = [row for base in roots if base is not None for row in _tree_files(base)]
    return dict(files=len(rows), bytes=sum(size for _, size in rows))


def clear_cache(roots):
    """Remove every file in the given cache folders (their own files only, as ``_tree_files`` lists them) and then
    the folders left empty. Returns the bytes removed."""
    freed = 0
    with LOCK:
        for base in roots:
            if base is None:
                continue
            try:
                root = _base(base)
            except OSError:
                continue
            for path, size in _tree_files(base):
                if not _inside(os.path.realpath(path), root):
                    continue
                try:
                    os.unlink(path)
                    freed += size
                except OSError:
                    continue
            for parent, folders, _ in os.walk(root, topdown=False):
                for leaf in folders:
                    try:
                        os.rmdir(os.path.join(parent, leaf))
                    except OSError:
                        pass
    return freed


def purge_prompt_text(cache_root, tuning_root=None):
    """Remove cached encodings an earlier version saved with the prompt as text. Returns how many went."""
    from .input_cache import holds_prompt_text
    removed = 0
    base = Path(cache_root)
    for receipt in sorted(base.glob('*/*/*.json')) if base.is_dir() else []:
        relative = receipt.relative_to(base).as_posix()
        if not _derived_ok(relative) or receipt.name.endswith('.previous.json'):
            continue
        value = _read(receipt) or {}
        payload = value.get('file')
        if isinstance(payload, str) and Path(payload).name == payload and holds_prompt_text(receipt.parent / payload):
            evict(base, relative)
            removed += 1
    if tuning_root is not None:
        for path in sorted(Path(tuning_root).glob('*/conditioning/*.pt')) + sorted(Path(tuning_root).glob('conditioning/*.pt')):
            if re.fullmatch('[0-9a-f]{64}\\.pt', path.name) and holds_prompt_text(path):
                relative = path.relative_to(tuning_root).as_posix()
                status, _ = safe_remove(tuning_root, relative, patterns=[re.compile(re.escape(relative))], folders=0)
                removed += status == 'removed'
    return removed


# Working folders the image worker makes in the system's temporary folder (the pictures it reads and the prompt), which
# it removes itself unless it was stopped hard. Windows never empties %TEMP%.
TEMP_PREFIXES = ('freevideo-h3img-', 'freevideo-h3ref-')
TEMP_AGE = 3600.


def clean_temp(*, folder=None, now=None, older=TEMP_AGE):
    """Remove FreeVideo's own working folders left in the temporary folder, untouched for ``older`` seconds. Only
    their own plain files go; a folder holding a link keeps it. Returns how many folders went."""
    import tempfile
    folder = Path(folder or tempfile.gettempdir())
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return 0
    for leaf in entries:
        if not leaf.startswith(TEMP_PREFIXES) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', leaf):
            continue
        path = folder / leaf
        try:
            info = os.lstat(path)
            if _link(info) or not stat.S_ISDIR(info.st_mode):
                continue
            files = _tree_files(path)
            newest = max([info.st_mtime] + [os.lstat(item).st_mtime for item, _ in files])
        except OSError:
            continue
        if now - newest < older:
            continue
        with LOCK:
            clear_cache([path])
            try:
                os.rmdir(path)
                removed += 1
            except OSError:
                pass
    return removed


def adopt(input_root, relative, *, root=None):
    """Make one of FreeVideo's temporary pictures (a card's draft) a kept file: one copy per content at
    freevideo/<keyed hash>/media.<ext>, where uploads go, with the temporary name gone. Returns the new path."""
    found = classify(relative)
    if not found or found[0] not in ('draft', 'page'):
        raise ValueError('Not a temporary picture')
    with LOCK:
        checked, reason = check(input_root, relative)
        if checked is None:
            raise ValueError('Temporary picture unavailable: ' + reason)
        path, info = checked
        suffix = path.suffix.lower()
        key = name_file('upload' + suffix, path, root=root)[:32]
        target = 'freevideo/%s/media%s' % (key, suffix)
        existing, _ = check(input_root, target)
        base = _base(input_root)
        if existing is not None and existing[1].st_size == info.st_size:
            os.utime(existing[0])
            safe_remove(input_root, relative, folders=0)
        else:
            if os.path.lexists(base / target):
                raise ValueError('The kept name is taken')
            (base / 'freevideo' / key).mkdir(parents=True, exist_ok=True)
            from .file_ops import publish
            publish(path, base / target)
        return target
