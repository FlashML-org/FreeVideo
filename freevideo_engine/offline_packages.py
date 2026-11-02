"""Import user-downloaded ZIPs. No downloader, model imports or subprocesses."""
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
from . import disk_space
from .long_paths import io_path, resolved, plain, plain_text, path_chars, plain_errors
from .runtime_alias import checked_real_path
from .offline_errors import write_reason
from .package_paths import runtime_budget

PREFIX = 'FreeVideo-Windows/'
# Optional packs (reference-audio tables) carry sampling tables outside the
# video pack's inventory; an installation uses them when they are imported.
OPTIONAL_PREFIX = 'models/base/sampling-cache/'
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
            log = folder / 'import-errors.log'
            log.parent.mkdir(parents=True, exist_ok=True)
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
    root = resolved(destination) / '.freevideo-packages' / ('0' * 64) / 'FreeVideo-Windows'
    return runtime_budget(packages, path_chars(plain(root)) if runtime_root_length is None else runtime_root_length)


def pack_name(path):
    """The download pack a model file belongs to, as the delivery note names it."""
    parts = path.replace('\\', '/').split('/')
    if 'sampling-cache' in parts:
        return '采样缓存'
    if path.startswith('models/encoder/') or 'latent_upscaler' in parts:
        return '提示词模型（FreeVideo-文本编码器.zip）'
    return '视频与音频解码器' if any(p in ('vae', 'audio_vae', 'vocoder') for p in parts) else '视频模型'


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


# Size, modification time and checked digest of each imported file. A later
# check of the same unchanged content avoids hashing tens of GiB again.
VERIFIED = 'import-verified.json'


def stamps(root):
    try:
        value = json.loads((io_path(root) / VERIFIED).read_text(encoding='utf-8'))
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


def import_archive(path, destination, progress=lambda **kw: None, cancelled=lambda: False):
    """Extract once, checking CRC-32, size and inventory SHA-256 while streaming.

    A file is written under a temporary name and renamed only when complete,
    so a file found under its own name was checked when it was written.
    """
    from .locking import runtime_lock
    # This lease also excludes package cleanup and other launcher instances.
    with runtime_lock(resolved(destination) / '.freevideo-packages/import.lock', inherit=False):
        return _import_archive(path, destination, progress, cancelled)


def _import_archive(path, destination, progress, cancelled):
    path = resolved(path)
    progress(done=0, total=None, detail=path.name)
    archive = path.stat()
    # Which ZIP this was, so the launcher can later offer to delete it once installed.
    origin = dict(archive=plain(path), archive_bytes=archive.st_size, archive_mtime_ns=archive.st_mtime_ns)
    value = inspect_archive(path)
    root = resolved(destination) / '.freevideo-packages' / value['id'] / 'FreeVideo-Windows'
    total = sum(r['bytes'] for r in value['files'])
    found = listing(root) if root.is_dir() else {}
    present = {row['path']: found[row['path']] for row in value['files'] if row['path'] in found}
    missing = sum(r['bytes'] for r in value['files'] if r['path'] not in present)
    parent = root
    while not parent.exists():
        parent = parent.parent
    if disk_space.free_bytes(parent) < missing + 64 * 2**20:
        raise ValueError('磁盘空间不足，导入此包还需约 %.1f GiB。' % (missing / 2**30))
    known, verified = stamps(root), {}
    for row in value['files']:
        current = present.get(row['path'])
        if current is None:
            continue
        if current[0] != row['bytes'] or not unchanged(root / row['path'], row, known, current,
                progress=file_progress(progress, 'verify', row['bytes'])):
            raise ValueError('已有导入文件已改变，未覆盖：' + plain(root / row['path']))
        verified[row['path']] = [*current, row['sha256']]
    marker = root / value['marker']
    if len(verified) == len(value['files']) and marker.is_file() and marker.read_bytes() == value['raw']:
        # Imported before and complete: nothing to read again.
        save(root / VERIFIED, verified)
        progress(done=total, total=total, detail=path.name, activity='')
        return dict(root=plain(root), kind=value['kind'], variant=value['variant'], name=path.name, **origin)
    done = sum(r['bytes'] for r in value['files'] if r['path'] in verified)
    root.mkdir(parents=True, exist_ok=True)
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
            target.parent.mkdir(parents=True, exist_ok=True)
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
            if partial.stat().st_size != row['bytes'] or digest.hexdigest() != row['sha256']:
                raise ValueError('离线包校验失败，已保留文件：' + path.name)
            partial.replace(target)
            verified[row['path']] = [*stamp(target), row['sha256']]
    save(root / VERIFIED, verified)
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
            # No hard links on this disk: renaming onto nothing is the safe way left.
            if os.name != 'nt' and os.path.lexists(target):
                raise FileExistsError(str(target))
            os.rename(partial, target)
            return
    except FileExistsError:
        partial.unlink(missing_ok=True)
        raise ValueError('离线安装已有不同模型，未覆盖：' + plain(target)) from None
    partial.unlink(missing_ok=True)


def assemble(runtime, models, source, progress=lambda **kw: None):
    """Bind imported components to the current EXE's engine, without running it."""
    runtime = io_path(runtime)
    check_bound_source(runtime, source)
    config = runtime_config(runtime)
    models = [io_path(root) for root in models]
    candidates = []
    for root in models:
        path = io_path(root) / 'models/model-pack.json'
        if path.is_file():
            candidates.append((io_path(root), json.loads(path.read_text(encoding='utf-8'))))
    variants = {value['variant'] for _, value in candidates}
    if not candidates:
        raise ValueError('还需导入「视频模型」包。')
    if len(variants) != 1:
        raise ValueError('请只导入一个「视频模型」包。')
    model = candidates[0][1]
    inventory(model['files'])
    if any(not r['path'].startswith('models/') for r in model['files']):
        raise ValueError('Invalid model inventory')
    from .network import hash_file
    known = {root: stamps(root) for root in models}
    # One directory walk per folder: no file is opened to be checked.
    walks, here = {root: listing(root) for root in models}, listing(runtime)
    total, done = sum(r['bytes'] for r in model['files']), 0
    progress(done=0, total=total, detail='检查配套包')

    def source_for(row):
        for root in models:
            current = walks[root].get(row['path'])
            if current and unchanged(io_path(root) / row['path'], row, known[root], current,
                    progress=file_progress(progress, 'verify', row['bytes'])):
                return io_path(root) / row['path']
        return None

    def place(src, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, target)
        except OSError:
            # Keep an interrupted copy outside the final name. A unique staging
            # name also preserves any existing neighbors the user added.
            with tempfile.NamedTemporaryFile(prefix='model-copy-', suffix='.copying',
                    dir=target.parent, delete=False) as output:
                partial = Path(output.name)
            copy_progress(src, partial, file_progress(progress, 'copy_bytes', src.stat().st_size))
            publish_without_overwrite(partial, target)

    included = []
    for row in model['files']:
        target, src = runtime / row['path'], source_for(row)
        if src is None:
            if pack_name(row['path']) == '采样缓存':
                total -= row['bytes']
                progress(done=done, total=total, detail='检查配套包', activity='')
                continue
            raise ValueError('还需导入「%s」包，缺少文件：%s' % (pack_name(row['path']), row['path']))
        if row['path'] in here:
            if not os.path.samefile(target, src) and hash_file(target,
                    progress=file_progress(progress, 'verify', row['bytes'])) != row['sha256']:
                raise ValueError('离线安装已有不同模型，未覆盖：' + plain(target))
        else:
            place(src, target)
        included.append(row)
        done += row['bytes']
        progress(done=done, total=total, detail='检查配套包', activity='')
    listed = {row['path'] for row in model['files']}
    for root in models:
        package = io_path(root) / 'offline-package.json'
        if not package.is_file():
            continue
        for row in json.loads(package.read_text(encoding='utf-8')).get('files', []):
            if row['path'] in listed or not row['path'].startswith(OPTIONAL_PREFIX) or row['path'] in here:
                continue
            current = walks[root].get(row['path'])
            if not current or not unchanged(io_path(root) / row['path'], row, known[root], current,
                    progress=file_progress(progress, 'verify', row['bytes'])):
                continue  # The engine fetches a table it cannot find when a request needs it.
            place(io_path(root) / row['path'], runtime / row['path'])
            listed.add(row['path'])
    progress(done=done, total=total, detail='检查配套包', activity='')
    # Only placed rows are recorded: a skipped Sampling caches table is fetched when needed.
    return finish(runtime, config, dict(model, files=included), source, here)


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


def finish(runtime, config, model, source, here=None):
    """Write portable.json for verified models, give the encoder its library copy and trust both."""
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
    trusted = {row['path'] for row in model['files'] + copies}
    trusted |= {row['path'] for row in config['files'] if row['path'] in final
                and imported.get(row['path']) == [*final[row['path']], row['sha256']]}
    save(runtime / 'engine' / 'portable-verified.json',
         {row['path']: dict(bytes=final[row['path']][0], mtime_ns=final[row['path']][1], sha256=row['sha256'])
          for row in value['files'] if row['path'] in trusted and row['path'] in final})
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


def download(runtime, source, sampling_caches, progress=lambda **kw: None, cancelled=lambda: False):
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
    log = runtime / 'engine' / 'model-download.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    progress(done=0, total=None, detail=DOWNLOADING)
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
                    progress(done=min(done, total), total=total or None, detail=DOWNLOADING)
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

    def prepare(self, runtime, models, source):
        if self.busy:
            return
        self.state = dict(status='preparing', done=0, total=None, detail='检查配套包', packages=[])
        def work():
            def update(**row):
                self.state = dict(self.state, **row)
            try:
                root = assemble(runtime, models, source, update)
                self.state = dict(self.state, status='prepared', ready_root=plain(root))
            except RuntimeMismatch as error:
                # An earlier import no longer fits after an update: the models stay.
                self.state = dict(self.state, status='complete', runtime_skipped=True, detail=str(error))
            except OlderLauncher as error:
                # Session routes error to the localized card; QML also displays detail verbatim.
                self.state = dict(self.state, status='error', error=plain_text(error), detail='', done=0, total=None)
        self.thread = threading.Thread(target=lambda: self._work(work, runtime), name='freevideo-offline-prepare', daemon=True)
        self.thread.start()

    def download(self, runtime, source, sampling_caches):
        if self.busy:
            return
        self.cancelled.clear()
        self.state = dict(status='downloading', done=0, total=None, detail=DOWNLOADING, packages=[])
        def update(**row):
            self.state = dict(self.state, **row)
        def work():
            try:
                root = download(runtime, source, sampling_caches, update, self.cancelled.is_set)
                self.state = dict(self.state, status='prepared', ready_root=plain(root))
            except RuntimeMismatch as error:
                self.state = dict(self.state, status='complete', runtime_skipped=True, detail=str(error))
            except OlderLauncher as error:
                self.state = dict(self.state, status='error', error=plain_text(error), detail='', done=0, total=None)
        self.thread = threading.Thread(target=lambda: self._work(work, runtime), name='freevideo-model-download', daemon=True)
        self.thread.start()

    def start(self, paths, destination):
        if self.busy:
            return
        self.cancelled.clear()
        self.state = dict(status='running', done=0, total=None, detail='', packages=[])
        def update(**row):
            self.state = dict(self.state, **row)
        def work():
            # Take every ZIP that fits; one that does not never holds back the others.
            packages, skipped, failed, reasons = [], False, [], []
            def record(error):
                try:
                    reasons.extend(failure_record(error, destination, log_subdir='.freevideo-packages').get('write_reasons', []))
                except Exception:
                    pass
            plans = []
            for path in paths:
                try:
                    plans.append((path, inspect_archive(path)))
                except RuntimeMismatch:
                    skipped = True
                except Exception as error:
                    failed.append('%s：%s' % (Path(path).name, plain_text(error)))
                    record(error)
            # Data only: alias selection/admission is deliberately left to B.
            try:
                update(path_budget=import_budget([value for _, value in plans], destination))
            except Exception as error:
                update(path_budget=dict(error=plain_text(error)))
            for index, (path, value) in enumerate(plans):
                update(index=index + 1, count=len(paths))
                try:
                    packages.append(import_archive(path, destination, update, self.cancelled.is_set))
                except RuntimeMismatch:
                    skipped = True
                except InterruptedError as error:
                    failed.append(plain_text(error))
                    break
                except Exception as error:
                    failed.append('%s：%s' % (Path(path).name, plain_text(error)))
                    record(error)
                update(packages=list(packages))
            if skipped:
                update(runtime_skipped=True, detail=SKIPPED_RUNTIME)
            update(**(dict(status='error', error='\n'.join(failed), write_reasons=reasons) if failed else dict(status='complete')))
        self.thread = threading.Thread(target=lambda: self._work(work, destination, log_subdir='.freevideo-packages'),
                                       name='freevideo-package-import', daemon=True)
        self.thread.start()
