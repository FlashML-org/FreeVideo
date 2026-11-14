"""Import user-downloaded ZIPs. No downloader, model imports or subprocesses."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time
import zipfile
import zlib
import traceback
import logging

from .monitoring import save
from .portable import ENCODER_LIBRARY, bundle_name, inside, listing
from .comfy_launcher_runtime import OlderLauncher, check_source_release
from . import disk_space, model_repair
from .long_paths import io_path, resolved, plain, plain_text, path_chars, plain_errors
from .runtime_alias import checked_real_path
from .offline_errors import write_reason
from .package_paths import runtime_budget
from .locking import runtime_lock
from .package_layout import (PACKAGES, INNER, VERIFIED, LEGACY_PACKAGES, select_root,
                             package_id, DirectoryGuard, ensure_container, redirected,
                             empty_reservation, identity_temporary, directory_identity, layout_name,
                             check_boundaries)

PREFIX = 'FreeVideo-Windows/'
# Optional packs (reference-audio tables) carry sampling tables outside the
# video pack's inventory; an installation uses them when they are imported.
OPTIONAL_PREFIX = 'models/base/sampling-cache/'
MAX_RESERVATION_ATTEMPTS = 16
MAX_BYTES = 300 * 2**30
# Model packs without their own inventory are "common": the shared pack of the
# earlier FP8 delivery, or the text encoder, decoder and sampling cache packs
# since 0.3.0, when the int8 video model pack carries the inventory for GeForce
# cards and Macs.
MODEL_VARIANTS = ('common', 'rowwise', 'per_tensor', 'int8_convrot')


def failure_record(error, root=None, *, log_subdir=None):
    # InterruptedError is deliberate cancellation, not an unknown write failure.
    if root is not None:
        detail = None
        try:
            detail = plain_text(''.join(traceback.format_exception(error)))
            folder = io_path(root)
            if log_subdir:
                folder /= log_subdir
            # Diagnostic failures must not write through a rejected package redirect.
            check_boundaries(folder)
            ensure_container(folder)
            with DirectoryGuard() as guard:
                guard.pin(folder)
                log = folder / 'import-errors.log'
                if redirected(log):
                    raise ValueError('Package folders must not redirect: ' + plain(log))
                with log.open('a', encoding='utf-8') as stream:
                    stream.write('%s\n%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), detail))
        except Exception:
            # A full/read-only disk may prevent the retained log too; keep the
            # complete OS error in state and the launcher's normal log handler.
            try:
                logging.getLogger(__name__).error('%s', detail if detail is not None else error)
            except Exception:
                pass
    try:
        return dict(write_reasons=[write_reason(error)]) if isinstance(error, OSError) and not isinstance(error, InterruptedError) else {}
    except Exception:
        return {}


def import_budget(packages, destination, *, runtime_root_length=None):
    """Pre-extraction batch data; B supplies the selected runtime/alias length."""
    planned, reserved = [], {}
    for package in packages:
        root = select_root(destination, package['id'], reserved=reserved)
        reserved[root.parent] = package['id']
        planned.append((package, root))
    if runtime_root_length is None:
        roots = [root for package, root in planned if package.get('kind') == 'runtime']
        runtime_root_length = max((path_chars(plain(root)) for root in (roots or [root for _, root in planned])),
                                  default=path_chars(plain(resolved(destination) / PACKAGES / '00000000' / INNER)))
    return runtime_budget(packages, runtime_root_length,
                          extraction_root_lengths=[path_chars(plain(root)) for _, root in planned])


def pack_name(path):
    """The download pack a model file belongs to, as the delivery note names it."""
    path = path.replace('\\', '/')
    # Download errors may carry an absolute installation path.
    path = path[path.index('models/'):] if 'models/' in path else path
    parts = path.split('/')
    if 'sampling-cache' in parts:
        return '采样缓存'
    if path.startswith('models/encoder/') or 'text_encoders' in parts or 'latent_upscaler' in parts:
        return '提示词模型（FreeVideo-文本编码器.zip）'
    return '视频与音频解码器' if any(p in ('vae', 'audio_vae', 'vocoder') for p in parts) else '视频模型'


def pack_title(path, zh=False):
    from .model_status import NAMES
    name = pack_name(path)
    if name.startswith('提示词模型'):
        return name if zh else 'Prompt model (FreeVideo-文本编码器.zip)'
    return name if zh else next(en for en, chinese in NAMES.values() if chinese == name)


class ReviewRequired(ValueError):
    """The read-only check found model files that need the user's confirmation."""
    def __init__(self, rows):
        self.replace_models = sorted(rows, key=lambda row: row['path'])
        super().__init__('Review the offline model replacements before continuing.')


def different_model(path, row, reserved, location=None):
    """Reuse the online replacement planner for a difference verified here."""
    before = model_repair.identity(path)
    if before is None:
        raise ValueError(model_repair.FILE_CHANGED + ' ' + plain(path))
    recorded = {str(path): dict(before, expected=model_repair.expected(row))}
    result = model_repair.replacement(path, row, recorded, reserved)
    model_repair.check(path, before)
    if location:
        result['location'] = location
    return result


def confirm_replacements(current, reviewed):
    current = sorted(current, key=lambda row: row['path'])
    if reviewed is None:
        if current:
            raise ReviewRequired(current)
        return
    model_repair.reuse_names(current, reviewed)
    if current != reviewed:
        raise ValueError(model_repair.CHANGED)
    # Check the entire list before the first rename, then retain checks each
    # file again immediately before moving it.
    for row in reviewed:
        model_repair.check(Path(row['path']), row)


class Retained(list):
    """(path, kept name) of each model set aside, and the mismatch ledger as it was before."""
    ledger = None


def retain_models(root, rows, progress, cancelled=lambda: False):
    kept = Retained()
    kept.ledger = model_repair.read(root) if rows else None
    try:
        for row in rows:
            path = Path(row['path'])
            model_repair.check(path, row)
            progress(detail=model_repair.PROGRESS[0] + row['file'] + ' → ' + row['keep_as'])
            if cancelled():
                # A pause stops before this file is renamed; the ones already set aside return.
                raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
            try:
                target = model_repair.retain(root, path, row)
            except BaseException:
                # The record write failed after the rename: the file is under its kept name and the
                # ledger still lists the original one, so it goes back before the others return.
                moved = path.with_name(row['keep_as'])
                if not path.exists() and moved.is_file() and not moved.is_symlink():
                    try:
                        model_repair.rename_noreplace(moved, path)
                    except (OSError, ValueError):
                        pass
                raise
            kept.append((path, target, model_repair.file_id(target)))
    except BaseException:
        model_repair.restore_kept(root, kept, kept.ledger)
        raise
    return kept


def dependency_id():
    """Hash of the whole dependency files; earlier EXEs compare Environment ZIPs by it."""
    root = resolved(__file__).parent
    digest = hashlib.sha256()
    for path in (root / 'dependencies.json', root / 'bootstrap_versions.json',
                 root.parent / 'constraints/windows-portable.txt'):
        digest.update(path.read_bytes())
    return digest.hexdigest()


# An Environment ZIP holds Python with the pinned packages, the bootstrap tools,
# and ComfyUI, vdn, diffusers and SageAttention at their pinned commits. Model
# rows in dependencies.json are not part of it, so a model change keeps the ZIP.
RUNTIME_KEYS = ('vdn', 'diffusers', 'encoder', 'sageattention')
# Earlier ZIPs carry only dependency_id, which also hashes the model rows.
# Each of these described the same runtime as runtime_id 5237c884... .
EARLIER_RUNTIMES = dict.fromkeys((
    '32bb93ff31957e629714fb5445562bad39acd51d22bd266705947646072f6175',
    '5df201020be0ca6b9cd71f4be2c6b331b402280fec556c721388fd9ed28ec001',
    'a6a447449e2fb66b360846902498256e1f7a243a51bbc7d2269202a9b5786eac',
    'f8f960e68e4c6caac38ded62c87133097dc39e726e0918352460a5ae81397378',
    'ea87a269c0fafa5446d9d1c501c5accc3f1f1f6970ad6fa5d6d725fde33a9593',
    # The ModelScope mapping of the tables for other step counts (a model row).
    'c5275824a6b316e95e30f0c9438ee080352122460a94124f2f1660480131cfea',
), '5237c88452f01b060b664dde4ac237329e28314c3f5a82fe4d1a01c9612543cb')
SKIPPED_RUNTIME = '运行环境包与这个版本不配套，已跳过；运行环境改为自动安装，其余离线包照常使用。'


class RuntimeMismatch(ValueError):
    """An Environment ZIP for another runtime: skip it and install the runtime online."""


def runtime_id():
    """What the current version needs inside an Environment ZIP, independent of models."""
    root = resolved(__file__).parent
    dependencies = json.loads((root / 'dependencies.json').read_text(encoding='utf-8'))
    value = dict(dependencies={key: dependencies[key] for key in RUNTIME_KEYS if key in dependencies},
                 bootstrap=json.loads((root / 'bootstrap_versions.json').read_text(encoding='utf-8')),
                 constraints=(root.parent / 'constraints/windows-portable.txt').read_text(encoding='utf-8')
                 .replace('\r\n', '\n'))
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def runtime_fits(value):
    """Any earlier ZIP whose runtime is unchanged serves this version."""
    found = value.get('runtime_id') or EARLIER_RUNTIMES.get(value.get('dependency_id'))
    return value.get('schema_version') == 2 and found == runtime_id()


def inventory(rows):
    seen = set()
    for row in rows:
        name = row['path']
        bundle_name(name)
        # Windows strips trailing dots/spaces and recognizes device names.
        for part in name.split('/'):
            if (part.rstrip(' .') != part or part.split('.')[0].upper() in
                    ('CON', 'PRN', 'AUX', 'NUL', *('COM%d' % n for n in range(1, 10)),
                     *('LPT%d' % n for n in range(1, 10)))):
                raise ValueError('Invalid Windows package path: ' + name)
        if (name.casefold() in seen or type(row['bytes']) is not int or row['bytes'] < 0
                or not isinstance(row['sha256'], str) or len(row['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in row['sha256'])):
            raise ValueError('Invalid package inventory: ' + name)
        seen.add(name.casefold())
    if sum(r['bytes'] for r in rows) > MAX_BYTES:
        raise ValueError('Offline package exceeds supported size')


def check_runtime_platform():
    import sys
    if sys.platform == 'darwin':
        raise ValueError('The Windows runtime package cannot be used on macOS.')


# A damaged download shows up as a bad CRC or directory (BadZipFile), a broken
# compressed stream (zlib.error) or a cut-off one (EOFError).
DAMAGED = (zipfile.BadZipFile, zlib.error, EOFError)


def damaged(path):
    return ValueError('离线包已损坏，请重新下载：' + Path(path).name)


def inspect_archive(path):
    """Read only bounded metadata; file contents are checked during import."""
    try:
        return _inspect_archive(path)
    except DAMAGED as error:
        raise damaged(path) from error


def _inspect_archive(path):
    path = io_path(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        marker = next((name for name in ('offline-package.json', 'runtime.json')
                       if PREFIX + name in names), None)
        if marker is None:
            raise ValueError('The file is not a FreeVideo offline package.')
        info = archive.getinfo(PREFIX + marker)
        if info.file_size > 16 * 2**20:
            raise ValueError('Oversized offline inventory')
        raw = archive.read(info)
        value = json.loads(raw)
        if marker == 'runtime.json':
            check_runtime_platform()
            if not runtime_fits(value):
                raise RuntimeMismatch(SKIPPED_RUNTIME)
            value = dict(value, kind='runtime', variant=None)
        elif value.get('schema_version') != 1 or value.get('kind') != 'models' or value.get('variant') not in MODEL_VARIANTS:
            raise ValueError('Invalid model package')
        import sys
        if sys.platform == 'darwin' and value.get('variant') in ('rowwise', 'per_tensor'):
            # Macs run the int8 model; FP8 packs only serve Windows workstation cards.
            raise ValueError('Mac 请使用「视频模型」包（int8），此 FP8 模型包不适用于 Mac。')
        rows = value['files']
        if not isinstance(rows, list) or not rows:
            raise ValueError('Empty offline package')
        inventory(rows)
        if value['kind'] == 'models' and any(not r['path'].startswith('models/') for r in rows):
            raise ValueError('Model packages may contain only models')
        if value['kind'] == 'runtime' and any(r['path'].startswith('models/') or r['path'].lower().endswith('freevideo.exe') for r in rows):
            raise ValueError('环境包不得包含模型或 FreeVideo.exe')
        permitted = {PREFIX + r['path']: r for r in rows}
        extra = {PREFIX + marker, PREFIX + '模型包使用说明.txt', PREFIX + '运行环境使用说明.txt'}
        seen = set()
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            name = entry.filename
            if name.casefold() in seen or (name not in permitted and name not in extra):
                raise ValueError('Unexpected or duplicate ZIP entry: ' + name)
            seen.add(name.casefold())
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                raise ValueError('Encrypted files and links are not supported')
            if name in permitted and entry.file_size != permitted[name]['bytes']:
                raise ValueError('Incorrect package file size: ' + name)
        if not set(permitted).issubset(names):
            raise ValueError('离线包缺少文件，请重新下载这个 ZIP。')
        return dict(value, marker=marker, raw=raw, id=hashlib.sha256(raw).hexdigest())


def reserve_root(root, value):
    """Reserve a new prefix after disk/file checks; an occupied name must retry."""
    root = io_path(root)
    ensure_container(root.parent.parent)
    created, identities = False, {}
    try:
        with DirectoryGuard() as guard:
            guard.pin(root.parent.parent)
            try:
                root.parent.mkdir()
                created = True
            except FileExistsError:
                if not empty_reservation(root.parent):
                    raise
            guard.pin(root.parent)
            identities = dict(guard.identities)
            root.mkdir(exist_ok=True)
            guard.pin(root)
            identities = dict(guard.identities)
            for temporary in root.iterdir():
                guard.check()
                if identity_temporary(temporary) and not redirected(temporary):
                    temporary.unlink()
            # save closes a complete temporary identity before publishing its atomic rename.
            save(root / VERIFIED, dict(package_id=value['id']))
            guard.check()
            return identities
    except BaseException:
        # Only this call's empty mkdir may be reclaimed after an identity failure.
        if created and identities:
            try:
                if (all(directory_identity(path) == identity for path, identity in identities.items())
                        and empty_reservation(root.parent)):
                    if root.is_dir():
                        for temporary in root.iterdir():
                            temporary.unlink()
                        root.rmdir()
                    root.parent.rmdir()
            except (OSError, ValueError):
                pass
        raise


@contextmanager
def package_lock(folder, identity, cancelled, guard):
    """A full-id lease covers the receipt window and all payload writes."""
    path = io_path(folder) / ('.import-' + identity + '.lock')
    while True:
        guard.check()
        if redirected(path):
            raise ValueError('Package folders must not redirect: ' + plain(path))
        lease = runtime_lock(path, inherit=False)
        try:
            lease.__enter__()
        except BlockingIOError:
            if cancelled():
                raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
            time.sleep(.05)
            continue
        break
    try:
        guard.check()
        yield
    finally:
        lease.__exit__(None, None, None)


def stamps(root):
    # Each checked file has [bytes, mtime_ns, sha256]; short extractions also
    # retain package_id and may refer to installed files without a second copy.
    return stamps_of(io_path(root) / VERIFIED)


def stamps_of(path):
    """Read a JSON inventory or receipt, treating unavailable/malformed records as absent."""
    try:
        value = json.loads(io_path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def stamp(path):
    info = io_path(path).stat()
    return [info.st_size, info.st_mtime_ns]


def unchanged(path, row, known, current=None, progress=None):
    """Still the content its import checked: size, time and expected digest match.

    Missing or earlier size/time-only records require hashing. `current` is
    its [size, mtime] when a directory walk already read it.
    """
    try:
        current = current or stamp(path)
    except OSError:
        return False
    if current[0] != row['bytes']:
        return False
    if known.get(row['path']) == [*current, row['sha256']]:
        return True
    from .network import hash_file
    return hash_file(path, **({'progress': progress} if progress else {})) == row['sha256']


def file_progress(progress, activity, total):
    from .install_activity import byte_progress
    return byte_progress('', '', report=lambda **row:
        progress(activity=activity, activity_done=row['done_bytes'], activity_total=row['total_bytes']))


def copy_progress(source, target, progress):
    """The existing cross-disk copy, with a bounded buffer and actual bytes."""
    total, done = source.stat().st_size, 0
    progress(0, total)
    with source.open('rb') as input_file, target.open('wb') as output:
        for chunk in iter(lambda: input_file.read(4 * 2**20), b''):
            output.write(chunk)
            done += len(chunk)
            progress(done, total)


def is_cut_short(path, reader, size):
    """The file at `path` stops early: shorter than `size`, every byte the start of `reader`.

    That is what an interrupted write leaves. A file that differs in any other way
    is someone's own and is never treated as cut short.
    """
    try:
        have = io_path(path).stat().st_size
        if have >= size:
            return False
        with io_path(path).open('rb') as stored:
            while True:
                chunk = stored.read(4 * 2**20)
                if not chunk:
                    return True
                if reader.read(len(chunk)) != chunk:
                    return False
    except OSError:
        return False


def cut_short_of(target, source, have, size):
    """True when `target` (of `have` bytes) is an unfinished copy of the verified `source`."""
    if have >= size:
        return False
    try:
        if os.path.samefile(target, source):
            return False
        with io_path(source).open('rb') as reader:
            return is_cut_short(target, reader, size)
    except OSError:
        return False


def complete_cut_short(source, target, progress=None):
    """Give a cut-short copy its full content: a new file beside it, then renamed over it."""
    source, target = io_path(source), io_path(target)
    partial = target.with_name(target.name + '.repairing')
    partial.unlink(missing_ok=True)
    try:
        try:
            os.link(source, partial)
        except OSError:
            copy_progress(source, partial, progress or (lambda *_: None))
        os.replace(partial, target)
    except BaseException:
        # A refused rename or an interrupted copy must not leave gigabytes beside the model.
        partial.unlink(missing_ok=True)
        raise


def installed_inventory(runtime, config=None):
    """Validated model rows from an earlier assembly, excluding environment/library files."""
    value = stamps_of(io_path(inside(runtime, 'portable.json')))
    environment = {row['path'] for row in config['files']} if config else set()
    rows = [row for row in value.get('files', [])
            if row['path'].startswith('models/') and row['path'] not in environment]
    inventory(rows)
    if not rows or value.get('variant') not in MODEL_VARIANTS[1:]:
        return None
    return dict(variant=value['variant'], files=rows)


def installed_models(runtime):
    """Installed inventory and unchanged model files; never hash large payloads.

    A missing receipt is not evidence. Match path, digest, size and mtime against
    the installation ledger, and refuse redirects before trusting a file.
    """
    runtime = io_path(runtime)
    try:
        # Check before inside() resolves the root and erases linked ancestors.
        if any(redirected(entry) for entry in (runtime, *runtime.parents)):
            return None, {}
        value = installed_inventory(runtime)
        if value is None:
            return None, {}
        rows = value['files']
        ledger = stamps_of(io_path(inside(runtime, 'engine/portable-verified.json')))
        names = {row['path'] for row in rows}
        optional = [dict(path=name, bytes=entry['bytes'], sha256=entry['sha256'])
                    for name, entry in ledger.items() if name.startswith(OPTIONAL_PREFIX) and name not in names
                    and isinstance(entry, dict) and 'bytes' in entry and 'sha256' in entry]
        inventory(optional)
        available = rows + optional
        verified = {}
        for row in available:
            path = io_path(inside(runtime, row['path']))
            try:
                current = stamp(path)
            except OSError:
                continue
            record = dict(bytes=row['bytes'], mtime_ns=current[1], sha256=row['sha256'])
            entry = ledger.get(row['path'])
            if current[0] == row['bytes'] and isinstance(entry, dict) and all(entry.get(k) == v for k, v in record.items()):
                verified[row['path']] = current
        return dict(variant=value['variant'], files=rows, available_files=available,
                    by_path={row['path']: (row['bytes'], row['sha256']) for row in available}), verified
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, {}


def same_model_file(model, row):
    return bool(model and model['by_path'].get(row['path']) == (row['bytes'], row['sha256']))


def installed_sources(destination):
    """Only installations beside this import, in either package layout."""
    from .package_cleanup import package_roots, packages
    from .package_layout import inner_root
    sources = {}
    for folder in package_roots(io_path(destination) / 'engine'):
        for pack, kind, _ in packages(folder):
            if kind != 'runtime':
                continue
            runtime = inner_root(pack)
            model, verified = installed_models(runtime)
            for row in model['available_files'] if model else ():
                if row['path'] in verified:
                    sources.setdefault((row['path'], row['sha256'], row['bytes']),
                                       (runtime, verified[row['path']]))
    return sources


def reusable_files(value, destination, present):
    # Recheck after acquiring the lease: a source may have changed while waiting.
    sources = installed_sources(destination) if value['kind'] == 'models' else {}
    return {row['path']: sources[(row['path'], row['sha256'], row['bytes'])]
            for row in value['files'] if row['path'] not in present
            and (row['path'], row['sha256'], row['bytes']) in sources}


def model_package_count(runtime, models):
    """Count usable packages, including those whose extraction was cleaned."""
    model, verified = installed_models(runtime) if runtime else (None, {})
    groups = {pack_name(row['path']) for row in model['files']} if model else set()
    groups -= {pack_name(row['path']) for row in model['files']
               if row['path'] not in verified} if model else set()
    count = 0
    installed = {}
    for root in models:
        try:
            value = json.loads(io_path(inside(root, 'offline-package.json')).read_text(encoding='utf-8'))
            rows = value['files']
            inventory(rows)
            known = stamps(root)
            references = known.get('installed_files', {})
            complete = True
            for row in rows:
                if row['path'] in references:
                    ref = references[row['path']]
                    if ref not in installed:
                        installed[ref] = installed_models(ref)
                    prior, found = installed[ref]
                    complete = same_model_file(prior, row) and row['path'] in found
                else:
                    current = stamp(inside(root, row['path']))
                    complete = current[0] == row['bytes'] and known.get(row['path']) == [*current, row['sha256']]
                if not complete:
                    break
            if complete and rows:
                count += 1
                groups -= {pack_name(row['path']) for row in rows if row['path'] != 'models/model-pack.json'}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return count + len(groups)


def model_packages_at(destination):
    from .package_cleanup import package_roots, packages
    from .package_layout import inner_root, read_manifest
    return [plain(inner_root(pack)) for folder in package_roots(io_path(destination) / 'engine')
            for pack, kind, _ in packages(folder)
            if kind == 'models' and read_manifest(inner_root(pack) / 'offline-package.json')]


def package_model_dirs(root):
    """The native installer also needs the real folders behind references."""
    references = stamps(root).get('installed_files', {})
    references = references if isinstance(references, dict) else {}
    return list(dict.fromkeys([plain(io_path(root) / 'models')] +
                             [plain(io_path(ref) / 'models') for ref in references.values() if isinstance(ref, str)]))


def archive_plan(path, destination, value=None, progress=lambda **kw: None):
    """Inspect the ZIP and what is already imported, without writing anything."""
    path = resolved(path)
    archive = path.stat()
    # Which ZIP this was, so the launcher can later offer to delete it once installed.
    origin = dict(archive=plain(path), archive_bytes=archive.st_size, archive_mtime_ns=archive.st_mtime_ns)
    value = value or inspect_archive(path)
    root = select_root(destination, value['id'])
    state = import_state(root, value, destination, progress, path)
    replacements, reserved = [], set()
    for row in state['differing']:
        if value['kind'] != 'models':
            raise ValueError('已有导入文件已改变，未覆盖：' + plain(root / row['path']))
        replacements.append(different_model(root / row['path'], row, reserved, location='cache'))
    room(root, state['missing'])
    return dict(path=path, root=root, destination=destination, value=value, origin=origin,
                missing=state['missing'], total=sum(r['bytes'] for r in value['files']),
                replace_models=replacements)


def import_state(root, value, destination, progress=lambda **kw: None, archive=None):
    """Unchanged files, files that differ from the package, and files an installed copy supplies.

    A file an interrupted write left short, every byte the start of the ZIP's own, is neither
    unchanged nor different: it is written again from the ZIP.
    """
    found = listing(root) if root.is_dir() else {}
    present = {row['path']: found[row['path']] for row in value['files'] if row['path'] in found}
    reused = reusable_files(value, destination, present)
    known, valid, differing, cut = stamps(root), {}, [], set()
    if archive is not None:
        with zipfile.ZipFile(archive) as opened:
            cut = {row['path'] for row in value['files'] if row['path'] in present and present[row['path']][0] < row['bytes']
                   and is_cut_short(root / row['path'], opened.open(PREFIX + row['path']), row['bytes'])}
    for row in value['files']:
        current = present.get(row['path'])
        if current is None or row['path'] in cut:
            continue  # Absent, or cut short by an interrupted write: extracted again.
        if current[0] != row['bytes'] or (known.get(row['path']) != [*current, row['sha256']]
                and not content_matches(root / row['path'], row, current, progress)):
            differing.append(row)
        else:
            valid[row['path']] = [*current, row['sha256']]
    missing = sum(r['bytes'] for r in value['files'] if r['path'] not in valid and r['path'] not in reused)
    return dict(valid=valid, differing=differing, reused=reused, missing=missing, cut=cut)


def room(root, missing):
    parent = root
    while not parent.exists():
        parent = parent.parent
    if disk_space.free_bytes(parent) < missing + 64 * 2**20:
        raise ValueError('磁盘空间不足，导入此包还需约 %.1f GiB。' % (missing / 2**30))


def import_archive(path, destination, progress=lambda **kw: None, cancelled=lambda: False, *, reviewed=None):
    """Review differing models, then extract under temporary names with ZIP CRC checks."""
    progress(done=0, total=None, detail=resolved(path).name)
    plan = archive_plan(path, destination, progress=progress)
    confirm_replacements(plan['replace_models'], reviewed)
    return extract_archive(plan, progress, cancelled)


def extract_archive(plan, progress, cancelled):
    """Extract once, checking ZIP CRC-32, size and inventory SHA-256 while streaming.

    A file is written under a temporary name and renamed only when complete,
    so a file found under its own name was checked when it was written. A
    differing model file the user confirmed is kept under its new name first.
    """
    path, destination, value, origin = plan['path'], plan['destination'], plan['value'], plan['origin']
    approved = {plain(row['path']): row for row in plan['replace_models']}
    root = select_root(destination, value['id'])
    with DirectoryGuard() as guard:
        if root.parent.parent.exists():
            guard.pin(root.parent.parent)
        if root.parent.exists() and package_id(root) == value['id']:
            guard.pin(root.parent)
            if root.exists():
                guard.pin(root)
        ensure_container(root.parent.parent)
        guard.pin(root.parent.parent)
        with package_lock(root.parent.parent, value['id'], cancelled, guard):
            lease = root.parent.parent / 'import.lock'
            if redirected(lease):
                raise ValueError('Package folders must not redirect: ' + plain(lease))
            # Imports can share the container; cleanup takes this lease exclusively.
            with runtime_lock(lease, inherit=False, shared=True):
                return _extract_archive(path, destination, value, origin, approved, progress, cancelled, guard)


def _extract_archive(path, destination, value, origin, approved, progress, cancelled, guard):
    total = sum(row['bytes'] for row in value['files'])
    for attempt in range(MAX_RESERVATION_ATTEMPTS):
        # Cancellation and a bound prevent a busy prefix from trapping the launcher.
        if attempt and cancelled():
            raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
        root = select_root(destination, value['id'])
        guard.check()
        # Reselection must stay inside the container whose identity/lease we hold.
        if root.parent.parent not in guard.identities:
            raise ValueError('Package directory changed during import: ' + plain(root.parent.parent))
        if root.parent.exists() and package_id(root) == value['id']:
            guard.pin(root.parent)
            if root.exists():
                guard.pin(root)
        state = import_state(root, value, destination, progress, path)
        reused, missing, cut = state['reused'], state['missing'], state['cut']
        room(root, missing)
        # Only a file the user saw on the review page may be moved aside.
        stale = []
        for row in state['differing']:
            reviewed = approved.get(plain(root / row['path']))
            if reviewed is None:
                raise ValueError('已有导入文件已改变，未覆盖：' + plain(root / row['path']))
            stale.append(reviewed)
        verified = dict(state['valid'])
        by_path = {row['path']: row for row in value['files']}
        verified.update({name: [*current, by_path[name]['sha256']] for name, (_, current) in reused.items()})
        if root.parent.exists() and package_id(root) == value['id']:
            break
        try:
            reservation = reserve_root(root, value)
            guard.expect(reservation)
        except FileExistsError:
            guard.check()
            if not root.parent.is_dir() or redirected(root.parent):
                raise
            continue  # Only a real competing package directory may reselect.
        break
    else:
        raise ValueError('Package reservation retry limit reached; retry the import.')
    retain_models(root, stale, progress, cancelled)

    def record_verified():
        guard.check()
        receipt = dict(verified, package_id=value['id']) if layout_name(root.parent.parent.name) == PACKAGES else verified
        if reused:
            # References cost no second payload, including without hard links.
            # Assembly checks the installed ledger again.
            receipt = dict(receipt, installed_files={name: plain(runtime) for name, (runtime, _) in reused.items()})
        save(root / VERIFIED, receipt)
    marker = root / value['marker']
    if len(verified) == len(value['files']) and marker.is_file() and marker.read_bytes() == value['raw']:
        # Imported before and complete: nothing to read again.
        if value['kind'] == 'models':
            with guard.parent(root / 'models' / '.placeholder', root):
                pass
        record_verified()
        progress(done=total, total=total, detail=path.name, activity='')
        return dict(root=plain(root), kind=value['kind'], variant=value['variant'], name=path.name, **origin)
    done = sum(r['bytes'] for r in value['files'] if r['path'] in verified)
    guard.check()
    root.mkdir(parents=True, exist_ok=True)
    guard.pin(root)
    if value['kind'] == 'models':
        with guard.parent(root / 'models' / '.placeholder', root):
            pass
    progress(done=done, total=total, detail=path.name, activity='')
    with zipfile.ZipFile(path) as archive:
        for row in value['files']:
            if cancelled():
                raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
            if row['path'] in verified:
                continue
            # The walk above refused links anywhere in this folder; rows passed
            # the lexical path rules in inspect_archive.
            target = root / row['path']
            with guard.parent(target, root) as check_parent:
                partial = target.with_name(target.name + '.importing')
                digest = hashlib.sha256()
                try:
                    # Reading a member to its end checks its CRC-32 (BadZipFile otherwise).
                    with archive.open(PREFIX + row['path']) as source, partial.open('wb') as output:
                        while True:
                            if cancelled():
                                raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
                            data = source.read(4 * 2**20)
                            if not data:
                                break
                            output.write(data); done += len(data)
                            digest.update(data)
                            progress(done=done, total=total, detail=path.name)
                except DAMAGED as error:
                    raise damaged(path) from error
                check_parent(full=False)
                if partial.stat().st_size != row['bytes'] or digest.hexdigest() != row['sha256']:
                    raise ValueError('离线包校验失败，已保留文件：' + path.name)
                try:
                    if row['path'] in cut:
                        partial.replace(target)  # Only a short copy of this very file is replaced.
                    else:
                        model_repair.rename_noreplace(partial, target)
                except FileExistsError as error:
                    raise ValueError(model_repair.FILE_CHANGED + ' ' + plain(target)) from error
                verified[row['path']] = [*stamp(target), row['sha256']]
    guard.release(0)  # Folders held for the run of files are let go before the receipt and marker.
    record_verified()
    guard.check()
    marker.write_bytes(value['raw'])
    return dict(root=plain(root), kind=value['kind'], variant=value['variant'], name=path.name, **origin)


ENCODER_COPY = 'engine/vendor/h3-text-encoder'


def encoder_library(runtime, config, present):
    """Give the text encoder its own copy of ComfyUI's library, as the ZIP had it.

    Updating the bundled ComfyUI then cannot change how prompts are encoded,
    as with the automatic installation's separate checkout. Made once, from
    files unchanged since import; if any changed, the bundled ComfyUI is used
    as before. Returns the copy's folder (or None) and its inventory rows.
    """
    runtime = io_path(runtime)
    comfy = config['configuration']['comfy'].rstrip('/') + '/'
    known = stamps(runtime)
    rows = []
    for row in config['files']:
        inner = row['path'][len(comfy):] if row['path'].startswith(comfy) else None
        if inner and any(inner == item or (item.endswith('/') and inner.startswith(item)) for item in ENCODER_LIBRARY):
            rows.append((row, dict(row, path=ENCODER_COPY + '/' + inner)))
    if not rows:
        return None, []
    pending = [(row, copy) for row, copy in rows if copy['path'] not in present]
    if not all(row['path'] in present and unchanged(runtime / row['path'], row, known, present[row['path']])
               for row, _ in pending):
        return None, []
    for row, copy in pending:
        target = runtime / copy['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.copying')
        shutil.copyfile(runtime / row['path'], partial)  # A copy, not a link: an update replaces neither.
        partial.replace(target)
    return ENCODER_COPY, [copy for _, copy in rows]


def publish_without_overwrite(partial, target):
    """Give a finished copy its final name, never replacing a file that appeared meanwhile."""
    try:
        try:
            os.link(partial, target)
        except FileExistsError:
            raise
        except OSError:
            model_repair.rename_noreplace(partial, target)
            return
    except FileExistsError:
        partial.unlink(missing_ok=True)
        raise ValueError('离线安装已有不同模型，未覆盖：' + plain(target)) from None
    partial.unlink(missing_ok=True)


class MissingModels(ValueError):
    def __init__(self, message, *, needs_inventory=False):
        super().__init__(message)
        self.needs_inventory = needs_inventory


_HASHED = {}


def content_matches(path, row, stamped, progress=lambda **kw: None):
    """Whether a file has the expected digest; one read per (file, size, mtime) in this process."""
    key = (plain(path), stamped[0], stamped[1], row['sha256'])
    if key not in _HASHED:
        from .network import hash_file
        if len(_HASHED) > 512:
            _HASHED.clear()
        _HASHED[key] = hash_file(path, progress=file_progress(progress, 'verify', row['bytes'])) == row['sha256']
    return _HASHED[key]


def assembly_plan(runtime, models, progress=lambda **kw: None, *, model_dirs=()):
    """Check every model placement before any directory or file is made."""
    runtime = io_path(runtime)
    config = runtime_config(runtime)
    installed, ready = installed_models(runtime)
    models = [io_path(root) for root in models]
    candidates = []
    for root in models:
        path = io_path(root) / 'models/model-pack.json'
        if path.is_file():
            candidates.append((io_path(root), json.loads(path.read_text(encoding='utf-8'))))
    variants = {value['variant'] for _, value in candidates}
    if not candidates and installed:
        candidates.append((runtime, installed))
        variants.add(installed['variant'])
    if not candidates:
        raise MissingModels('还需导入「视频模型」包。', needs_inventory=True)
    if len(variants) != 1:
        raise ValueError('请只导入一个「视频模型」包。')
    model = candidates[0][1]
    inventory(model['files'])
    if any(not r['path'].startswith('models/') for r in model['files']):
        raise ValueError('Invalid model inventory')
    from .network import hash_file
    known = {root: stamps(root) for root in models}
    references = {io_path(ref): installed_models(ref) for record in known.values()
                  for ref in record.get('installed_files', {}).values()}
    # One directory walk per folder: no file is opened to be checked.
    walks, here = {root: listing(root) for root in models}, listing(runtime)
    total, done = sum(r['bytes'] for r in model['files']), 0
    progress(done=0, total=total, detail='检查离线包')

    def source_for(row):
        if same_model_file(installed, row) and row['path'] in ready:
            return runtime / row['path']
        for root in models:
            current = walks[root].get(row['path'])
            if current and unchanged(io_path(root) / row['path'], row, known[root], current,
                    progress=file_progress(progress, 'verify', row['bytes'])):
                return io_path(root) / row['path']
            ref = known[root].get('installed_files', {}).get(row['path'])
            prior, found = references.get(io_path(ref), (None, {})) if ref else (None, {})
            if same_model_file(prior, row) and row['path'] in found:
                return io_path(inside(ref, row['path']))
        return None

    placements, replacements, reserved = [], [], set()
    def check_place(row, src):
        target = runtime / row['path']
        if row['path'] not in here:
            placements.append(('place', src, target))
        elif not os.path.samefile(target, src):
            if cut_short_of(target, src, here[row['path']][0], row['bytes']):
                # An interrupted write left the start of this very file: it is finished, not reviewed.
                placements.append(('complete', src, target))
                return
            # The receipt does not vouch for this file (changed since it was
            # verified, or never recorded): read it once. The same content is
            # certified where it is; a different one is kept and reviewed.
            if here[row['path']][0] != row['bytes'] or not content_matches(target, row, here[row['path']], progress):
                replacements.append(different_model(target, row, reserved))
                placements.append(('place', src, target))

    # Package receipts and the installed inventory remain the first choices.
    # Ordinary libraries have no trusted receipt: match their actual content.
    from .local_models import scan_many, key
    needed = [dict(row, repo='offline', file=row['path']) for row in model['files']
              if source_for(row) is None]
    local = scan_many(list(model_dirs), needed) if model_dirs and needed else dict(matches={})
    included = []
    for row in model['files']:
        target, src = runtime / row['path'], source_for(row)
        match = local['matches'].get(key(dict(row, repo='offline', file=row['path'])))
        if src is None and match:
            # A library file is placed after the review, like every other one.
            if target.exists() or target.is_symlink():
                if hash_file(target, progress=file_progress(progress, 'verify', row['bytes'])) != row['sha256']:
                    replacements.append(different_model(target, row, reserved))
                    placements.append(('import', row, match, target))
            else:
                placements.append(('import', row, match, target))
        elif src is None:
            if pack_name(row['path']) == '采样缓存':
                total -= row['bytes']
                progress(done=done, total=total, detail='检查离线包', activity='')
                continue
            raise MissingModels('还需导入「%s」包，缺少文件：%s' % (pack_name(row['path']), row['path']))
        else:
            check_place(row, src)
        included.append(row)
        done += row['bytes']
        progress(done=done, total=total, detail='检查离线包', activity='')
    listed = {row['path'] for row in model['files']}
    optional = {row['path']: row for row in installed['available_files']
                if row['path'].startswith(OPTIONAL_PREFIX) and row['path'] not in listed
                and row['path'] in ready} if installed else {}
    for root in models:
        package = io_path(root) / 'offline-package.json'
        if not package.is_file():
            continue
        for row in json.loads(package.read_text(encoding='utf-8')).get('files', []):
            if row['path'] in listed or not row['path'].startswith(OPTIONAL_PREFIX):
                continue
            src = source_for(row)
            if src is None:
                continue  # The engine fetches a table it cannot find when a request needs it.
            check_place(row, src)
            optional[row['path']] = row
            listed.add(row['path'])
    return dict(runtime=runtime, config=config, model=dict(model, files=included), here=here, placements=placements,
                optional=optional, replace_models=replacements)


def place_model(item, here, progress=lambda **kw: None):
    """Place one file; return the identity of the file this call created, if it did."""
    if item[0] == 'import':
        from .local_models import can_link, import_file
        from .bootstrap import existing_parent
        _, row, match, target = item
        match['method'] = 'hardlink' if can_link(match['source'], existing_parent(target.parent)) else 'copy'
        import_file(dict(row, repo='offline', file=row['path']), target, match)
        here[row['path']] = stamp(target)
        return model_repair.file_id(target)
    kind, src, target = item
    if kind == 'complete':
        complete_cut_short(src, target, file_progress(progress, 'copy_bytes', src.stat().st_size))
        return None  # The file was there before; a failed run leaves it as it was.
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, target)
        return model_repair.file_id(target)
    except FileExistsError as error:
        raise ValueError(model_repair.FILE_CHANGED + ' ' + plain(target)) from error
    except OSError:
        pass
    # Preserve an interrupted copy under a unique staging name; the final name
    # is published only when complete, without replacing a newly arrived file.
    with tempfile.NamedTemporaryFile(prefix='model-copy-', suffix='.copying',
                                    dir=target.parent, delete=False) as output:
        partial = Path(output.name)
    copy_progress(src, partial, file_progress(progress, 'copy_bytes', src.stat().st_size))
    publish_without_overwrite(partial, target)
    return model_repair.file_id(target)


def metadata_snapshot(runtime):
    """What the installation's own records say now, to be put back with the models."""
    snapshot = {}
    for name in ('portable.json', 'engine/portable-verified.json'):
        try:
            snapshot[name] = (runtime / name).read_bytes()
        except FileNotFoundError:
            snapshot[name] = None
    return snapshot


def restore_metadata(runtime, snapshot):
    """Put the records back; False when that could not be done (then the new models stay)."""
    try:
        for name, data in snapshot.items():
            path = runtime / name
            if data is None:
                path.unlink(missing_ok=True)
                continue
            temporary = path.with_name(path.name + '.restoring')
            temporary.write_bytes(data)
            temporary.replace(path)
        return True
    except OSError:
        return False


def assemble(runtime, models, source, progress=lambda **kw: None, *, model_dirs=(), reviewed=None,
             cancelled=lambda: False):
    """Bind imported components to the current EXE's engine, without running it, after reviewing every differing model."""
    runtime = io_path(runtime)
    # An older launcher is refused before any planning or review.
    check_bound_source(runtime, source)
    plan = assembly_plan(runtime, models, progress, model_dirs=model_dirs)
    confirm_replacements(plan['replace_models'], reviewed)
    if plan['replace_models'] and cancelled():
        raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
    snapshot = metadata_snapshot(plan['runtime']) if plan['replace_models'] else None
    kept = retain_models(plan['runtime'], plan['replace_models'], progress, cancelled)
    owned = {}
    try:
        placed = set()
        for item in plan['placements']:
            if cancelled():
                # A pause between two models: the set-aside originals return with the new files removed.
                raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
            created = place_model(item, plan['here'], progress)
            target = item[-1]
            if created is not None:
                owned[plain(target)] = created
            placed.add(target.relative_to(plan['runtime']).as_posix())
        return finish(plan['runtime'], plan['config'], plan['model'], source, plan['here'],
                      optional=list(plan['optional'].values()), placed=placed)
    except BaseException:
        # A model set aside returns to its name together with the records that describe it; a
        # failure to put the records back leaves the new models, which those records now match.
        if kept and restore_metadata(plan['runtime'], snapshot):
            model_repair.restore_kept(plan["runtime"], kept, kept.ledger, owned)
        raise


def runtime_config(runtime):
    check_runtime_platform()
    config = json.loads((io_path(runtime) / 'runtime.json').read_text(encoding='utf-8'))
    if not runtime_fits(config):
        raise RuntimeMismatch(SKIPPED_RUNTIME)
    return config


def check_bound_source(runtime, source, value=None):
    """Check the existing binding before copying code, models or encoder files."""
    runtime = io_path(runtime)
    if value is None:
        try:
            value = json.loads((runtime / 'portable.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
    if not isinstance(value, dict) or not value.get('source'):
        return
    check_source_release(source, inside(runtime, value['source']))


def finish(runtime, config, model, source, here=None, *, optional=(), placed=None):
    """Write portable.json for verified models, give the encoder its library copy and trust both.

    With the plan's own stamps in `here`, a model file is trusted only if this
    assembly placed it or it still has the stamp it was verified with.
    """
    from .desktop_runtime import materialize_source
    runtime = io_path(runtime)
    check_bound_source(runtime, source)
    library, copies = encoder_library(runtime, config, listing(runtime) if here is None else here)
    code = materialize_source(source, runtime / 'engine/launcher/source')
    variant = model['variant']
    from .prepared_model import catalog
    value = dict(config['configuration'], schema_version=1, variant=variant,
        source=str(io_path(code).relative_to(runtime).as_posix()),
        source_commit=code.name, cache='models/edge/' + catalog()['variants'][variant]['cache_prefix'],
        files=config['files'] + model['files'] + copies, **({'encoder_library': library} if library else {}))
    inventory(value['files'])
    save(runtime / 'portable.json', value)
    # Every model file was verified above and the environment's files at import;
    # the first start takes that instead of reading tens of GiB once more.
    imported, final = stamps(runtime), listing(runtime)
    trusted = {row['path'] for row in copies}
    for row in model['files'] + list(optional):
        # A file changed after the plan verified it is not certified by this run.
        if placed is None or row['path'] in placed or here.get(row['path']) == final.get(row['path']):
            trusted.add(row['path'])
    trusted |= {row['path'] for row in config['files'] if row['path'] in final
                and imported.get(row['path']) == [*final[row['path']], row['sha256']]}
    save(runtime / 'engine' / 'portable-verified.json',
         {row['path']: dict(bytes=final[row['path']][0], mtime_ns=final[row['path']][1], sha256=row['sha256'])
          for row in value['files'] + list(optional) if row['path'] in trusted and row['path'] in final})
    return Path(plain(runtime))


@plain_errors
def rebind(runtime, source):
    """Run another launcher's engine code in an assembled installation; environment and models stay."""
    from .desktop_runtime import materialize_source
    runtime = io_path(checked_real_path(runtime))
    value = json.loads((runtime / 'portable.json').read_text(encoding='utf-8'))
    check_bound_source(runtime, source, value)
    code = materialize_source(source, runtime / 'engine/launcher/source')
    value.update(source=io_path(code).relative_to(runtime).as_posix(), source_commit=code.name)
    save(runtime / 'portable.json', value)
    return code


DOWNLOADING = '下载模型'


def download(runtime, source, sampling_caches, progress=lambda **kw: None, cancelled=lambda: False,
             *, model_dirs=(), local_only=False):
    """Fill an imported environment's model folders from the network, then assemble it.

    The installation's own Python runs the automatic installer's downloader
    (fastest source, resume, verification); its reports drive the progress.
    """
    import subprocess
    from .comfy_environment import isolated_environment
    from .desktop_runtime import materialize_source
    from . import launch_view, processes
    runtime = io_path(runtime)
    check_bound_source(runtime, source)
    config = runtime_config(runtime)
    code = materialize_source(source, runtime / 'engine/launcher/source')
    out = runtime / 'engine' / 'downloaded-models.json'
    out.unlink(missing_ok=True)
    env = isolated_environment(Path(plain(runtime / 'engine')), Path(plain(code)))
    for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'PYTHONHOME'):
        env.pop(key, None)
    env.update(PYTHONPATH=plain(code), PYTHONNOUSERSITE='1', PYTHONIOENCODING='utf-8',
               HF_HOME=plain(runtime / 'engine/cache/huggingface'))
    command = [plain(inside(runtime, config['configuration']['python'])), '-B', '-m', 'freevideo_engine.portable_models',
               '--root', plain(runtime), '--out', plain(out)] + (['--sampling-caches'] if sampling_caches else [])
    if model_dirs:
        libraries = runtime / 'engine' / 'model-libraries.json'
        save(libraries, dict(version=1, roots=list(model_dirs)))
        command += ['--reuse-models-manifest', plain(libraries)]
    if local_only:
        command += ['--local-only']
    log = runtime / 'engine' / 'model-download.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    detail = '检查配套包' if local_only else DOWNLOADING
    progress(done=0, total=None, detail=detail)
    tail = ''
    with log.open('w', encoding='utf-8') as stream:
        view = launch_view.view_for(runtime)
        child = processes.popen(command, env=launch_view.environment_view(env, view), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', start_new_session=True, supervise=True)
        try:
            for line in child.stdout:
                stream.write(plain_text(line)); stream.flush()
                tail = (tail + line)[-4000:]
                if cancelled():
                    processes.stop(child)
                    raise InterruptedError('已暂停下载，已下载的文件保留，再次开始会接着下载。')
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('event') == 'model_groups':
                    groups = event.get('groups') or []
                    total = sum(g.get('total_bytes') or 0 for g in groups)
                    done = sum(max(g.get('ready_bytes') or 0, g.get('downloaded_bytes') or 0) for g in groups)
                    progress(done=min(done, total), total=total or None, detail=detail)
                elif isinstance(event, dict) and event.get('event') == 'local_model_progress':
                    progress(done=event.get('done_bytes', 0), total=event.get('total_bytes') or None, detail='检查配套包')
        finally:
            if child.poll() is None:
                processes.stop(child)
            child.stdout.close()
    if child.wait() != 0 or not out.is_file():
        raise RuntimeError('模型下载未完成，已下载的文件保留，可以重试。日志：%s\n%s' % (plain(log), plain_text(tail[-1500:])))
    model = json.loads(out.read_text(encoding='utf-8'))
    inventory(model['files'])
    return finish(runtime, config, model, source)


class Importer:
    def __init__(self):
        self.thread = None
        self.cancelled = threading.Event()
        self.state = dict(status='idle', done=0, total=None, detail='', packages=[])
        self.pending = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def _work(self, work, root, *, log_subdir=None):
        failure = None
        try:
            work()
        except Exception as error:
            failure = error
        finally:
            if failure is not None:
                # Set the final state before any best-effort diagnostics. The
                # thread then exits, so busy becomes False even if logging fails.
                self.state = dict(self.state, status='error')
                try:
                    self.state['error'] = str(failure)
                except Exception:
                    self.state['error'] = type(failure).__name__
                try:
                    self.state.update(error=plain_text(failure))
                    self.state.update(failure_record(failure, root, log_subdir=log_subdir))
                except Exception:
                    pass

    def review(self, error, action, args, **options):
        rows = error.replace_models
        self.pending = (action, args, options)
        self.state = dict(self.state, status='review', replace_models=rows,
                          replace_models_text=dict(en=model_repair.review_lines(rows, offline=True),
                                                   zh=model_repair.review_lines(rows, zh=True, offline=True)))

    def confirm(self):
        if self.busy or self.state.get('status') != 'review' or self.pending is None:
            return
        action, args, options = self.pending
        reviewed = self.state['replace_models']
        getattr(self, action)(*args, reviewed=reviewed, **options)

    def prepare(self, runtime, models, source, *, model_dirs=(), download_missing=False, sampling_caches=False,
                reviewed=None):
        if self.busy:
            return
        self.cancelled.clear()
        self.state = dict(status='preparing', done=0, total=None, detail='检查离线包', packages=[])
        def work():
            def update(**row):
                self.state = dict(self.state, **row)
            try:
                try:
                    options = dict(model_dirs=model_dirs) if model_dirs else {}
                    root = assemble(runtime, models, source, update, reviewed=reviewed,
                                   cancelled=self.cancelled.is_set, **options)
                except MissingModels as error:
                    if not download_missing and not (error.needs_inventory and model_dirs):
                        raise
                    # A directory-only check needs the installation's Python to
                    # choose the pinned GPU inventory. Manual mode stays offline.
                    libraries = list(model_dirs) + [folder for root in models for folder in package_model_dirs(root)]
                    update(status='downloading' if download_missing else 'preparing')
                    root = download(runtime, source, sampling_caches, update, self.cancelled.is_set,
                                    model_dirs=libraries, local_only=not download_missing)
                self.state = dict(self.state, status='prepared', ready_root=plain(root))
            except ReviewRequired as error:
                self.review(error, 'prepare', (runtime, list(models), source), model_dirs=model_dirs,
                            download_missing=download_missing, sampling_caches=sampling_caches)
            except RuntimeMismatch as error:
                # An earlier import no longer fits after an update: the models stay.
                self.state = dict(self.state, status='complete', runtime_skipped=True, detail=str(error))
            except OlderLauncher as error:
                # Session routes error to the localized card; QML also displays detail verbatim.
                self.state = dict(self.state, status='error', error=plain_text(error), detail='', done=0, total=None)
        self.thread = threading.Thread(target=lambda: self._work(work, runtime), name='freevideo-offline-prepare', daemon=True)
        self.thread.start()

    def download(self, runtime, source, sampling_caches, *, model_dirs=()):
        if self.busy:
            return
        self.cancelled.clear()
        self.state = dict(status='downloading', done=0, total=None, detail=DOWNLOADING, packages=[])
        def update(**row):
            self.state = dict(self.state, **row)
        def work():
            try:
                options = dict(model_dirs=model_dirs) if model_dirs else {}
                root = download(runtime, source, sampling_caches, update, self.cancelled.is_set, **options)
                self.state = dict(self.state, status='prepared', ready_root=plain(root))
            except RuntimeMismatch as error:
                self.state = dict(self.state, status='complete', runtime_skipped=True, detail=str(error))
            except OlderLauncher as error:
                self.state = dict(self.state, status='error', error=plain_text(error), detail='', done=0, total=None)
        self.thread = threading.Thread(target=lambda: self._work(work, runtime), name='freevideo-model-download', daemon=True)
        self.thread.start()

    def start(self, paths, destination, *, reviewed=None):
        if self.busy:
            return
        self.cancelled.clear()
        self.state = dict(status='running', done=0, total=None, detail='', packages=[])
        def update(**row):
            self.state = dict(self.state, **row)
        def work():
            # Take every ZIP that fits; one that does not never holds back the others.
            # The whole batch is checked before anything is extracted or retained.
            packages, skipped, failed, reasons = [], False, [], []
            def record(error):
                try:
                    reasons.extend(failure_record(error, destination, log_subdir=LEGACY_PACKAGES).get('write_reasons', []))
                except Exception:
                    pass
            inspected = []
            for path in paths:
                try:
                    inspected.append((path, inspect_archive(path)))
                except RuntimeMismatch:
                    skipped = True
                except Exception as error:
                    failed.append('%s：%s' % (Path(path).name, plain_text(error)))
                    record(error)
            # Data only: alias selection/admission is deliberately left to B.
            try:
                update(path_budget=import_budget([value for _, value in inspected], destination))
            except Exception as error:
                update(path_budget=dict(error=plain_text(error)))
            plans, seen = [], set()
            for index, (path, value) in enumerate(inspected):
                update(index=index + 1, count=len(paths))
                try:
                    plan = archive_plan(path, destination, value, progress=update)
                    if plan['root'] not in seen:
                        plans.append(plan)
                        seen.add(plan['root'])
                except RuntimeMismatch:
                    skipped = True
                except Exception as error:
                    failed.append('%s：%s' % (Path(path).name, plain_text(error)))
                    record(error)
            try:
                confirm_replacements([row for plan in plans for row in plan['replace_models']], reviewed)
            except ReviewRequired as error:
                self.review(error, 'start', (list(paths), destination))
                return
            except Exception as error:
                update(status='error', error=plain_text(error))
                return
            for index, plan in enumerate(plans):
                update(index=index + 1, count=len(plans))
                try:
                    packages.append(extract_archive(plan, update, self.cancelled.is_set))
                except InterruptedError as error:
                    failed.append(plain_text(error))
                    break
                except Exception as error:
                    failed.append('%s：%s' % (plan['path'].name, plain_text(error)))
                    record(error)
                    if model_repair.CHANGED in str(error):
                        break
                update(packages=list(packages))
            # A competing prefix may have lengthened the roots after preflight.
            try:
                update(path_budget=import_budget([plan['value'] for plan in plans], destination))
            except Exception as error:
                update(path_budget=dict(error=plain_text(error)))
            if skipped:
                update(runtime_skipped=True, detail=SKIPPED_RUNTIME)
            update(**(dict(status='error', error='\n'.join(failed), write_reasons=reasons) if failed else dict(status='complete')))
        self.thread = threading.Thread(target=lambda: self._work(work, destination, log_subdir=LEGACY_PACKAGES),
                                       name='freevideo-package-import', daemon=True)
        self.thread.start()
