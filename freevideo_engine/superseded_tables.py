"""Remove reference-audio tables that 0.3.5 replaced, once each replacement is complete.

Before 0.3.5 the reference-audio AdaLN tables (ref2va_audio, ref2va_av) labeled
reference audio rows t = 0. Their replacements have new identities, so the old
folders are never read again. A folder is removed only when all of this holds:

* its name is one of the ten published folders in superseded_tables.json;
* it lies inside a sampling-table root, reached without links or junctions;
* it holds nothing but that table's files: NN.safetensors with a matching
  NN.json receipt (bytes and SHA-256 of a published copy, checked against the
  file size), identity.json bound to the folder name, producer.json, and the
  partial or set-aside downloads of those files. Any other entry keeps the
  whole folder;
* the replacement table for the same task and step count is complete on disk.

Files are removed one at a time and the folder last, only once it is empty.
A failure (for example a file Windows still holds) keeps what is left and
never fails the request that triggered the cleanup.
"""
import json
from pathlib import Path
import re
import stat

from .variant_cleanup import inside, redirected

MANIFEST = Path(__file__).with_name('superseded_tables.json')
LEFTOVER = re.compile(r'(?P<index>\d{2})\.(?P<kind>safetensors|json)'
                      r'(?:\.partial(?:-\d{1,20}|\.source\.json)?|\.[a-z]{1,16}-\d{1,20})?')
RECEIPT_LIMIT = 4096
IDENTITY_LIMIT = 65536


def _load():
    value = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if value.get('schema_version') != 1 or not isinstance(value.get('tables'), dict):
        raise ValueError('Invalid superseded table manifest')
    return value['tables']


def _regular(path):
    try:
        info = path.lstat()
    except OSError:
        return None
    if redirected(info) or not stat.S_ISREG(info.st_mode):
        return None
    return info


def _small_json(path, limit):
    info = _regular(path)
    if info is None or info.st_size > limit:
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def owned_files(root, folder, published):
    """Every file of one superseded folder, or None unless all of them belong to it."""
    from .adaln_assets import directory
    if not inside(root, folder) or not folder.is_dir():
        return None
    files = []
    for entry in folder.iterdir():
        info = _regular(entry)
        if info is None:
            return None
        if entry.name in ('identity.json', 'producer.json'):
            if entry.name == 'identity.json':
                identity = _small_json(entry, IDENTITY_LIMIT)
                try:
                    if not isinstance(identity, dict) or directory(identity) != folder.name:
                        return None
                except (KeyError, TypeError, ValueError):
                    return None
            files.append(entry)
            continue
        match = LEFTOVER.fullmatch(entry.name)
        if match is None or match['index'] not in published:
            return None
        if entry.name == match['index'] + '.safetensors':
            receipt = _small_json(folder / (match['index'] + '.json'), RECEIPT_LIMIT)
            if (not isinstance(receipt, dict) or [receipt.get('bytes'), receipt.get('sha256')] not in published[match['index']]
                    or info.st_size != receipt['bytes']):
                return None
        elif entry.name == match['index'] + '.json':
            receipt = _small_json(entry, RECEIPT_LIMIT)
            if not isinstance(receipt, dict) or [receipt.get('bytes'), receipt.get('sha256')] not in published[match['index']]:
                return None
        files.append(entry)
    return files


def _complete(table, roots):
    """The replacement's files are all present at their catalogued size, with receipts."""
    for row in table['files']:
        name = '/'.join(row['file'].split('/')[-2:])  # <directory>/NN.safetensors, below any bank prefix
        found = False
        for root in roots:
            path = root / name
            info = _regular(path)
            if info is not None and info.st_size == row['bytes'] and (path.with_suffix('.json')).is_file():
                found = True
                break
        if not found:
            return False
    return True


def retire(roots, replacements):
    """Remove each superseded folder whose replacement is complete; report what happened.

    roots: folders that hold sampling tables (shared sampling cache, model cache).
    replacements: current catalog tables, each with task, identity and files.
    """
    roots = [Path(root) for root in roots if root]
    result = dict(removed=[], kept=[], removed_bytes=0)
    try:
        superseded = _load()
    except (OSError, ValueError) as error:
        result['error'] = str(error)
        return result
    for name, old in superseded.items():
        candidates = [(root, root / name) for root in roots if (root / name).exists() or (root / name).is_symlink()]
        if not candidates:
            continue
        replacement = next((t for t in replacements if t.get('task') == old['task']
                            and len(t['identity']['timesteps']) == old['steps']), None)
        if replacement is None or not _complete(replacement, roots):
            continue  # Its replacement is not on disk yet.
        for root, folder in candidates:
            files = owned_files(root, folder, old['files'])
            if files is None:
                result['kept'].append(dict(folder=str(folder), reason='unexpected content'))
                continue
            try:
                size = 0
                # Receipts last, so an interrupted removal never leaves a table without its receipt.
                for path in sorted(files, key=lambda p: p.suffix == '.json'):
                    size += path.lstat().st_size
                    path.unlink()
                folder.rmdir()
            except OSError as error:
                result['kept'].append(dict(folder=str(folder), reason=type(error).__name__))
                continue
            result['removed'].append(str(folder))
            result['removed_bytes'] += size
    return result
