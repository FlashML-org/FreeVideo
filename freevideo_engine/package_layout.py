"""Imported package locations; ZIP member names are independent of this layout."""
from contextlib import ExitStack, contextmanager
import hashlib
import stat
import json
import re

from .long_paths import io_path, resolved, plain
from .runtime_alias import is_junction
from . import runtime_alias, long_paths

PACKAGES = '.fv'
INNER = 'w'
LEGACY_PACKAGES = '.freevideo-packages'
LEGACY_INNER = 'FreeVideo-Windows'
CONTAINERS = (LEGACY_PACKAGES, PACKAGES)
VERIFIED = 'import-verified.json'
DIGEST = re.compile(r'[0-9a-f]{64}')


def layout_name(name):
    # Windows stores case-preserving names but compares them without case.
    return name.casefold() if long_paths.is_windows() else name


def inner_root(pack):
    pack = io_path(pack)
    return pack / (INNER if layout_name(pack.parent.name) == PACKAGES else LEGACY_INNER)


def redirected(path):
    path = io_path(path)
    return path.is_symlink() or is_junction(path)


def check_boundaries(path):
    # Resolving a selected installation first would erase its linked container.
    path = io_path(path)
    for entry in (path, *path.parents):
        if (any(layout_name(parent.name) in CONTAINERS
                for parent in (entry, entry.parent, entry.parent.parent))
                and redirected(entry)):
            raise ValueError('Package folders must not redirect: ' + plain(entry))


def read_manifest(path):
    """Only our inventory format establishes ownership; a filename cannot."""
    from .offline_packages import inventory, MODEL_VARIANTS
    path = io_path(path)
    try:
        if redirected(path) or path.stat().st_size > 16 * 2**20:
            return None
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get('files'), list) or not value['files']:
            return None
        rows = value['files']
        inventory(rows)
        if path.name == 'offline-package.json':
            if (value.get('schema_version') != 1 or value.get('kind') != 'models'
                    or value.get('variant') not in MODEL_VARIANTS
                    or any(not row['path'].startswith('models/') for row in rows)):
                return None
        elif (value.get('schema_version') != 2
                or not any(isinstance(value.get(key), str) and DIGEST.fullmatch(value[key])
                           for key in ('runtime_id', 'dependency_id'))
                or not isinstance(value.get('configuration'), dict)
                or any(row['path'].startswith('models/') or row['path'].lower().endswith('freevideo.exe')
                       for row in rows)):
            return None
        return raw, value
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None


def package_id(root):
    """Read a full identity, including an interrupted import's existing receipt.

    Old unfinished imports predate identity receipts; only those may still use
    the full directory name. Completed imports use the original manifest bytes.
    """
    root = io_path(root)
    if any(redirected(path) for path in (root.parent.parent, root.parent, root)):
        return None
    for marker in ('offline-package.json', 'runtime.json'):
        path = root / marker
        if path.exists():
            manifest = read_manifest(path)
            return hashlib.sha256(manifest[0]).hexdigest() if manifest else None
    receipt = root / VERIFIED
    if redirected(receipt):
        return None
    try:
        # The current inventory has ~50,000 rows. Bound metadata reads even
        # when an unrelated occupied prefix contains a foreign file.
        if receipt.stat().st_size <= 16 * 2**20:
            value = json.loads(receipt.read_text(encoding='utf-8'))
            identity = value.get('package_id') if isinstance(value, dict) else None
            if isinstance(identity, str) and DIGEST.fullmatch(layout_name(identity)):
                return layout_name(identity)
    except (OSError, ValueError):
        pass
    pack = root.parent
    if layout_name(pack.parent.name) == LEGACY_PACKAGES and DIGEST.fullmatch(layout_name(pack.name)):
        return layout_name(pack.name)
    return None


def select_root(destination, identity, *, reserved=None):
    """Reuse either layout by content; otherwise choose an unoccupied prefix.

    Read-only, also used for pre-import path budgets. The importer reserves a
    new directory exclusively and retries this selection if another wins it.
    """
    identity = layout_name(identity) if isinstance(identity, str) else identity
    if not isinstance(identity, str) or not DIGEST.fullmatch(identity):
        raise ValueError('Invalid package id')
    check_boundaries(destination)
    destination = resolved(destination)
    reserved = reserved or {}
    destinations = [destination]
    allocation = None
    # A selected offline installation imports through engine.parent. Keep its
    # own package in place and find siblings in the original selected folder;
    # no launcher/UI caller needs to guess the enclosing container's layers.
    for pack in (destination, *destination.parents):
        if layout_name(pack.parent.name) not in CONTAINERS:
            continue
        current = inner_root(pack)
        if current != destination and current not in destination.parents:
            continue
        recorded = package_id(current)
        if recorded:
            if recorded == identity:
                return current
            owner = pack.parent.parent
            if allocation is None:
                allocation = owner
            if owner not in destinations:
                destinations.append(owner)
    for name in CONTAINERS:
        for base in destinations:
            folder = base / name
            if redirected(folder):
                raise ValueError('Package folders must not redirect: ' + plain(folder))
            if folder.exists() and not folder.is_dir():
                if layout_name(name) == PACKAGES:
                    continue  # A file of the user's own; new imports go to the old folder below.
                raise ValueError('Package container is not a directory: ' + plain(folder))
            for pack in sorted(folder.iterdir()) if folder.is_dir() else ():
                if not pack.is_dir() or '.retired-' in pack.name or redirected(pack):
                    continue
                root = inner_root(pack)
                if package_id(root) == identity:
                    return root
    for pack, planned_id in reserved.items():
        if isinstance(planned_id, str) and layout_name(planned_id) == identity:
            return inner_root(pack)
    owner = allocation or destination
    if (owner / PACKAGES).exists() and not (owner / PACKAGES).is_dir():
        # 0.3.25 imported into this folder; a file named like the short folder must not stop that.
        pack = owner / LEGACY_PACKAGES / identity
        if pack not in reserved and (not pack.exists() or empty_reservation(pack)) and not redirected(pack):
            return pack / LEGACY_INNER
        raise ValueError('Package id directory is occupied: ' + identity)
    for length in range(8, 65, 4):
        pack = owner / PACKAGES / identity[:length]
        if pack not in reserved and (not pack.exists() or empty_reservation(pack)) and not redirected(pack):
            return pack / INNER
    raise ValueError('Package id directory is occupied: ' + identity)


def directory_identity(path):
    path = io_path(path)
    if redirected(path):
        raise ValueError('Package folders must not redirect: ' + plain(path))
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Package container is not a directory: ' + plain(path))
    return info.st_dev, info.st_ino


def same_directory(path, identity):
    """Whether ``path`` still reaches the directory with this identity (one lookup; links lead elsewhere)."""
    try:
        info = io_path(path).lstat()  # A link to the moved folder has that folder's identity under stat().
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == identity


def ensure_container(path):
    path = io_path(path)
    if redirected(path):
        raise ValueError('Package folders must not redirect: ' + plain(path))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except FileExistsError as error:
        raise ValueError('Package container is not a directory: ' + plain(path)) from error
    directory_identity(path)


class DirectoryGuard:
    """Pin real directories; a replaced name must never become an import target."""
    def __init__(self):
        self.stack = ExitStack()
        self.identities = {}
        # The folders of the file written last, outermost first, as (path, identity, handles).
        self.chain = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            self.release(0)
        finally:
            return self.stack.__exit__(*args)

    def release(self, keep):
        """Let go of the folders below the first ``keep`` of the chain."""
        error = None
        while len(self.chain) > keep:
            try:
                self.chain.pop()[2].close()
            except BaseException as caught:
                error = error or caught
        if error is not None:
            raise error

    def pin(self, path, expected=None):
        path = io_path(path)
        identity = directory_identity(path)
        expected = self.identities.get(path, identity) if expected is None else expected
        if identity != expected:
            raise ValueError('Package directory changed during import: ' + plain(path))
        if path not in self.identities:
            # Windows handles deny rename/reparse while children are written.
            # Files are renamed into pinned folders, which needs write sharing.
            if long_paths.is_windows() and runtime_alias._backend.windows():
                self.stack.enter_context(runtime_alias._backend.parent(path, writers=True))
            if directory_identity(path) != identity:
                raise ValueError('Package directory changed during import: ' + plain(path))
            self.identities[path] = identity
        self.check()

    def check(self):
        for path, identity in self.identities.items():
            if not same_directory(path, identity):
                raise ValueError('Package directory changed during import: ' + plain(path))

    def expect(self, identities):
        for path, identity in identities.items():
            self.pin(path, identity)

    @contextmanager
    def parent(self, target, root):
        # Consecutive files share most of their folders: each folder is created, checked and (on
        # Windows) held once while files keep landing in it, and every file re-checks the innermost
        # one. Its identity is only reached through the folders above it, so a swapped ancestor
        # shows there; walking every ancestor per file cost several times the file write itself.
        parents = []
        current = io_path(target).parent
        while current != root:
            parents.append(current)
            current = current.parent
        parents.reverse()
        try:
            shared = 0
            while shared < min(len(parents), len(self.chain)) and self.chain[shared][0] == parents[shared]:
                shared += 1
            self.release(shared)
            if shared and not same_directory(*self.chain[-1][:2]):
                raise ValueError('Package directory changed during import: ' + plain(self.chain[-1][0]))
            if shared < len(parents) or not parents:
                self.check()  # Before anything is created; a file in the same folder follows the last file's check.
            for folder in parents[shared:]:
                if redirected(folder):
                    raise ValueError('Package folders must not redirect: ' + plain(folder))
                folder.mkdir(exist_ok=True)
                identity = directory_identity(folder)
                handles = ExitStack()
                self.chain.append((folder, identity, handles))
                if long_paths.is_windows() and runtime_alias._backend.windows():
                    handles.enter_context(runtime_alias._backend.parent(folder, writers=True))
            def check(full=True):
                if full:
                    self.check()
                if self.chain:
                    folder, identity, _ = self.chain[-1]
                    if not same_directory(folder, identity):
                        raise ValueError('Package directory changed during import: ' + plain(folder))
            for file in (target, target.with_name(target.name + '.importing')):
                if redirected(file):
                    raise ValueError('Package folders must not redirect: ' + plain(file))
            check(full=False)  # The folder just made or reached is still the one that was read.
            yield check
            check()
        except BaseException:
            try:
                self.release(0)
            finally:
                raise


def identity_temporary(path):
    return path.name.startswith(VERIFIED + '.') and path.name.endswith('.tmp')


def empty_reservation(pack):
    """An interrupted mkdir/staged identity is reusable, never unknown content."""
    pack, root = io_path(pack), inner_root(pack)
    try:
        if any(redirected(path) for path in (pack.parent, pack, root)) or not pack.is_dir():
            return False
        entries = list(pack.iterdir())
        if not entries:
            return True
        if entries != [root] or not root.is_dir():
            return False
        return all(file.is_file() and not redirected(file) and identity_temporary(file)
                   for file in root.iterdir())
    except OSError:
        return False
