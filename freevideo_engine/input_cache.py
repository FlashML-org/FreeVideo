"""Small persistent inputs, separate from optional performance tuning.

Entries are content checked before reuse. Old namespaces and damaged files stay
on disk; a miss never deletes user artifacts or substitutes a finished video.

A receipt's name is a keyed hash with this installation's salt, so neither a
prompt nor its plain hash can be read from the folder; an entry holding a
prompt as text is removed when it is next read. All kinds together stay under
one size (Settings), least recently used first; the receipts a run used are
listed in its report, so deleting the work removes what only it used.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from .monitoring import save
from .paths import data_root
from . import disk_space


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


# The receipts this process used or wrote, as <kind>/<identity>/<receipt>.json, for the run's report.
USED = []
# All kinds together, unless Settings says otherwise.
DEFAULT_LIMIT = 2 * 10 ** 9
MAX_ENTRIES = 16384
# A pickled dict key "prompt" (protocol 2 and 4 spellings): the encoder used to keep the prompt as text in its result.
PLAIN_PROMPT = (b'X\x06\x00\x00\x00prompt', b'\x8c\x06prompt')


def used():
    return list(dict.fromkeys(USED))


def _note(path):
    try:
        relative = Path(path).relative_to(Path(path).parents[2]).as_posix()
    except (ValueError, IndexError):
        return
    if relative not in USED:
        USED.append(relative)


def holds_prompt_text(path):
    """Whether a saved tensor file keeps a prompt as text (a torch archive with the key in its pickle)."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.endswith('data.pkl'):
                    data = archive.read(name)
                    return any(marker in data for marker in PLAIN_PROMPT)
    except (OSError, zipfile.BadZipFile, KeyError):
        return False
    return False


def limit_bytes(base):
    """The size all kinds together stay under: Settings' value, or DEFAULT_LIMIT."""
    try:
        value = json.loads((Path(base).parent / 'private-store' / 'settings.json').read_text(encoding='utf-8'))
        chosen = value.get('cache_bytes')
        if type(chosen) is int and 256 * 2**20 <= chosen <= 2**40:
            return chosen
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_LIMIT


class InputCache:
    """One receipt per input; a hit refreshes its receipt and `prune` evicts the least recently used ones."""
    def __init__(self, identity, *, root=None, kind='conditioning'):
        encoded = json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()
        self.identity = hashlib.sha256(encoded).hexdigest()
        self.base = Path(root or data_root() / 'input-cache')
        self.kind = kind
        self.root = self.base / kind / self.identity
        self.note = None

    def receipt(self, key):
        from .private_store import name
        return self.root / (name('input-cache\0' + self.kind, key, root=self.base.parent / 'private-store') + '.json')

    def lookup(self, key):
        try:
            entry = json.loads(self.receipt(key).read_text(encoding='utf-8'))
            if (entry.get('identity') != self.identity or not isinstance(entry.get('file'), str)
                    or Path(entry['file']).name != entry['file']
                    or type(entry.get('bytes')) is not int or not 0 < entry['bytes'] <= 128 * 2**20
                    or not isinstance(entry.get('metrics'), dict)):
                return None
            source = self.root / entry['file']
            if source.stat().st_size != entry['bytes'] or digest(source) != entry['sha256']:
                self.note = 'Cache content changed; retained and recomputed'
                return None
            if holds_prompt_text(source):
                # Saved by an earlier version with the prompt as text: it goes, and is computed again.
                self.evict(self.receipt(key))
                return None
            _note(self.receipt(key))
            return source, entry
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.note = type(error).__name__ + ': input cache unavailable; compute normally'
            return None

    def get(self, key, destination):
        started = time.perf_counter()
        found = self.lookup(key)
        if found is None:
            return None
        source, entry = found
        try:
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(destination)
            temporary = destination.with_name(destination.name + '.cache-partial-' + uuid.uuid4().hex)
            with temporary.open('xb') as output, source.open('rb') as stream:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
            if digest(temporary) != entry['sha256']:
                # Keep partial/corrupted copies separate from the usable input.
                self.note = 'Cache changed during reuse; retained and recomputed'
                return None
            from .file_ops import publish
            publish(temporary, destination)
            try:
                os.utime(self.receipt(key))
            except OSError:
                pass
            result = copy.deepcopy(entry['metrics'])
            # A receipt describes the original encoding. Keep its timings as
            # provenance, not work performed by this cache-hit request.
            from .encoder_diagnostics import cached_metrics
            cached_metrics(result)
            result.update(output=str(destination), cache_hit=True, cache_sha256=entry['sha256'],
                          cache_receipt=str(self.receipt(key)), load_seconds=0.,
                          work_seconds=time.perf_counter() - started,
                          torch_peak_allocated_bytes=0, torch_peak_reserved_bytes=0)
            return result
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.note = type(error).__name__ + ': input cache unavailable; compute normally'
            return None

    def put(self, key, source, metrics, *, move=False):
        if metrics.get('success') is not True:
            raise ValueError('Only successful inputs may be cached')
        source = Path(source)
        size = source.stat().st_size
        if not 0 < size <= 128 * 2**20:
            self.note = 'Input exceeds the small-input cache limit; original artifact retained'
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        if disk_space.free_bytes(self.root) < size + 2**30:
            self.note = 'Low disk space; existing caches retained, new input not cached'
            return False
        checksum = digest(source)
        target = self.root / (checksum + '.pt')
        if target.exists() and digest(target) != checksum:
            target = self.root / (checksum[:31] + '-' + uuid.uuid4().hex + '.pt')
        if not target.exists():
            # Nothing written here has a longer name than the receipt. Windows
            # refuses a path past 260 characters: a longer staging name left an
            # installation in a deep folder unable to save an input whose
            # receipt and file both fit, so every repeat encoded it again.
            temporary = source if move else self.root / (uuid.uuid4().hex + '.partial')
            if not move:
                with temporary.open('xb') as output, source.open('rb') as stream:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
            if digest(temporary) != checksum:
                raise ValueError('Input changed during cache copy; partial retained')
            from .file_ops import publish
            try:
                publish(temporary, target)
            except FileExistsError:
                if not target.exists() or digest(target) != checksum:
                    raise
                if not move:
                    temporary.unlink(missing_ok=True)
        receipt = self.receipt(key)
        if receipt.exists():
            # Keep old receipts too, including malformed ones, for diagnostics.
            prior = receipt.read_bytes()
            previous = self.root / (receipt.stem[:38] + '-' + hashlib.sha256(prior).hexdigest()[:16] + '.previous.json')
            if not previous.exists():
                previous.write_bytes(prior)
        save(receipt, {'identity': self.identity, 'file': target.name, 'bytes': size,
                       'sha256': checksum, 'metrics': metrics, 'created_epoch': time.time()},
             staging='receipt.')
        _note(receipt)
        self.prune()
        return True

    def evict(self, receipt):
        """Remove one receipt, its earlier copies and its file when no other receipt names it."""
        from .private_store import evict
        try:
            evict(self.base, Path(receipt).relative_to(self.base).as_posix())
        except ValueError:
            pass

    def prune(self, max_bytes=None, max_entries=MAX_ENTRIES):
        """Drop the least recently used receipts of every kind, across identities, until under both limits.

        Only files this cache wrote are removed; unreadable receipts are left in place."""
        if max_bytes is None:
            max_bytes = limit_bytes(self.base)
        rows, owners = [], {}
        try:
            for receipt in self.base.glob('*/*/*.json'):
                if receipt.name.endswith('.previous.json'):
                    continue
                try:
                    entry = json.loads(receipt.read_text(encoding='utf-8'))
                    if not isinstance(entry['file'], str) or Path(entry['file']).name != entry['file']:
                        continue
                    data = receipt.parent / entry['file']
                    rows.append((receipt.stat().st_mtime, receipt, data))
                    owners[data] = owners.get(data, 0) + 1
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            total = sum({data: data.stat().st_size for _, _, data in rows if data.exists()}.values())
            for _, receipt, data in sorted(rows, key=lambda row: row[0]):
                if total <= max_bytes and len(rows) <= max_entries:
                    break
                receipt.unlink()
                rows = [row for row in rows if row[1] != receipt]
                owners[data] -= 1
                if not owners[data] and data.exists():
                    total -= data.stat().st_size
                    data.unlink()
        except OSError as error:
            self.note = type(error).__name__ + ': input cache pruning skipped'
