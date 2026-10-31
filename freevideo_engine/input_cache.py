"""Small persistent inputs, separate from optional performance tuning.

Entries are content checked before reuse. Old namespaces and damaged files stay
on disk; a miss never deletes user artifacts or substitutes a finished video.
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


class InputCache:
    """One receipt per input; a hit refreshes its receipt and `prune` evicts the least recently used ones."""
    def __init__(self, identity, *, root=None, kind='conditioning'):
        encoded = json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()
        self.identity = hashlib.sha256(encoded).hexdigest()
        self.root = Path(root or data_root() / 'input-cache') / kind / self.identity
        self.note = None

    def receipt(self, key):
        return self.root / (hashlib.sha256(key.encode()).hexdigest() + '.json')

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
            publish(temporary, target)
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
        self.prune()
        return True

    def prune(self, max_bytes=1 << 30, max_entries=4096):
        """Drop the least recently used receipts of this kind, across identities, until under both limits.

        Only files this cache wrote are removed; unreadable receipts are left in place."""
        kind = self.root.parent
        rows, owners = [], {}
        try:
            for receipt in kind.glob('*/*.json'):
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
