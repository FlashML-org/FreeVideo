"""Optional, hash-pinned image weights under <model_root>/image_gen/.

Downloads use the engine's source ranking, proxy policy and resumable transfers.
Status reads verification stamps without hashing gigabytes on the caller's thread;
a full-size file whose stamp no longer matches (a hard link raised its change time,
the folder moved) is hashed once in the background and re-stamped. Repair hashes
every file and retains mismatches; removal touches only unchanged files we created.
Installer.start('install', background=True) yields to the existing engine lease
or an optional busy callback. The CLI writes atomic, read-only status snapshots.
"""
from collections import deque
from copy import deepcopy
from contextlib import ExitStack, contextmanager
import errno
import json
import logging
import platform
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import threading
import time
import traceback
import uuid
from urllib.parse import urlsplit

from .. import disk_space, model_repair, network, provision
from ..locking import runtime_lock
from ..monitoring import save
from ..failure_details import Redactor, image_pack_error_code, redacted_launcher_error

LEDGER = 'download-verified.json'
OWNERSHIP = 'owned-files.json'
RESERVE = 256 * 2**20
FAMILY = 'image-models'
BACKGROUND_RESERVE = 2 * 2**30
GENERATION_POLL_SECONDS = .25
GENERATION_RESUME_SECONDS = 10.
# Asking ComfyUI whether it is busy can take seconds (a closed port on Windows
# refuses only after about 2 s), and hashing checks once per 4 MiB block. Ask at
# most every 2 s, and a slow answer waits four times its own cost before the next.
GENERATION_PROBE_SECONDS = 2.
GENERATION_PROBE_COST_FACTOR = 4
SPEED_WINDOW_SECONDS = 10.
STATUS_INTERVAL_SECONDS = .25
OFFLINE_TRANSFER_REASONS = frozenset(('curl-5', 'curl-6', 'curl-7'))


class PackError(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


class _GenerationPause(Exception):
    """Unwind the transfer so curl is stopped before waiting for generation."""


class _Background:
    def __init__(self, installer, stop):
        self.installer, self.stop = installer, stop
        self.resume_at = None
        self.paused = False
        self.probed_at, self.probe_gap, self.busy_seen = None, 0., False

    def generating(self):
        if self.installer.generation_busy:
            if self.probed_at is None or time.monotonic() - self.probed_at >= self.probe_gap:
                began = time.monotonic()
                self.busy_seen = bool(self.installer.generation_busy())
                self.probed_at = time.monotonic()
                self.probe_gap = max(GENERATION_PROBE_SECONDS,
                                     GENERATION_PROBE_COST_FACTOR * (self.probed_at - began))
            if self.busy_seen:
                return True
        # Probe the existing lease exclusively, without borrowing an inherited
        # descriptor. This also detects shared device work. Release immediately:
        # downloads never hold engine.lock or delay a generation acquiring it.
        # A holder is the only cross-process signal; non-generation holders also
        # cause a conservative pause. In-process callers can supply busy().
        path = (Path(self.installer.engine_root) / 'engine.lock'
                if self.installer.engine_root is not None else None)
        try:
            with runtime_lock(path, inherit=False):
                return False
        except BlockingIOError:
            return True

    def check(self):
        if self.stop.is_set():
            raise InterruptedError('cancelled')
        if self.generating():
            self.paused = True
            self.resume_at = None
            raise _GenerationPause()

    def wait(self):
        while True:
            try:
                self.check()
            except _GenerationPause:
                pass
            else:
                if self.paused and self.resume_at is None:
                    self.resume_at = time.monotonic() + GENERATION_RESUME_SECONDS
                if not self.paused or time.monotonic() >= self.resume_at:
                    self.resume_at = None
                    self.paused = False
                    self.installer._set(state='running', reason=None)
                    return
            # Reached only after the active transfer has been reaped, or at an
            # in-process checkpoint, so paused really means no active download.
            self.installer._set(state='paused_for_generation', reason='busy_video')
            self.stop.wait(GENERATION_POLL_SECONDS)


def catalog():
    try:
        spec = json.loads(Path(__file__).with_name('image_models.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise PackError('invalid_catalog') from error
    validate_catalog(spec)
    return spec


def _relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))):
        raise PackError('invalid_catalog')
    return PurePosixPath(name)


def validate_catalog(spec):
    try:
        if (spec['format'] != 1 or spec['family'] != 'image' or not spec['model_id']
                or not isinstance(spec['repo'], str) or not spec['repo']
                or not isinstance(spec['revision'], str) or not spec['revision']):
            raise PackError('invalid_catalog')
        image_rows = spec['groups']['image']
        # A group of its own holds only its own component; the image group holds neither.
        own = {group: spec['groups'].get(group, []) for group in ('segment', 'music')}
        found = image_rows + [row for found in own.values() for row in found]
        if (not image_rows or len({r['file'] for r in found}) != len(found) or set(spec['groups']) - {'image', *own}
                or any(row['component'] in own for row in image_rows)
                or any(row['component'] != group for group, found in own.items() for row in found)):
            raise PackError('invalid_catalog')
        for row in found:
            _relative(row['file'])
            if (type(row['bytes']) is not int or row['bytes'] <= 0
                    or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                    or row['component'] not in ('dit', 'text_encoder', 'vae', 'text_encoder_config', 'processor', 'segment', 'music')):
                raise PackError('invalid_catalog')
            ids = [s['id'] for s in row['sources']]
            required = {'official', 'modelscope'}
            if (len(set(ids)) != len(ids) or not required <= set(ids)
                    or not set(ids) <= {'official', 'modelscope', 'hf-mirror'}):
                raise PackError('invalid_catalog')
            for source in row['sources']:
                url = urlsplit(source['url'])
                if url.scheme not in ('https', 'http') or not url.netloc or url.username or url.password:
                    raise PackError('invalid_catalog')
    except (KeyError, TypeError, ValueError) as error:
        raise PackError('invalid_catalog') from error


def rows(spec=None, *, group='image'):
    spec = spec if spec is not None else catalog()
    validate_catalog(spec)
    groups = spec['groups'].values() if group is None else [spec['groups'][group]]
    return [dict(row, family='image', model_id=row.get('model_id', spec['model_id']),
                 repo=row.get('repo', spec['repo']), revision=row.get('revision', spec['revision']))
            for found in groups for row in found]


def base(root=None):
    if root is None:
        from ..paths import installed_model_root
        root = installed_model_root()
    return Path(root).expanduser().absolute() / 'image_gen'


def _path(folder, name):
    """Refuse links at the pack root or below it, including partials/receipts."""
    relative = _relative(name)
    path = folder
    if path.is_symlink():
        raise PackError('unsafe_path')
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise PackError('unsafe_path')
    return path


def target(row, root=None):
    return _path(base(root), row['file'])


def _read(folder, name):
    try:
        value = json.loads(_path(folder, name).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _intact(path, row, ledger):
    try:
        prior = ledger.get(str(path), {})
        return path.is_file() and path.stat().st_size == row['bytes'] and isinstance(prior, dict) and all(
            prior.get(k) == v for k, v in provision.file_identity(path, row).items())
    except OSError:
        return False


def _owned_intact(path, stamp):
    return path.is_file() and isinstance(stamp, dict) and all(
        stamp.get(k) == v for k, v in provision.file_identity(path, stamp).items())


# The notice written next to the weights. The text lives here; licenses/NOTICE.txt carries the same words
# for people reading the source and is never needed at run time.
NOTICE_TEXT = (b'Qwen is licensed under the Qwen RESEARCH LICENSE AGREEMENT, Copyright (c) 2026 Hangzhou Tongyi '
               b'Laboratory Technology Co., Ltd. All Rights Reserved.\n')
SEGMENT_NOTICE_TEXT = (b'SAM 2.1 Hiera small, Copyright (c) Meta Platforms, Inc. and affiliates.\n'
                       b'Licensed under Apache-2.0; see freevideo_engine/licenses/SAM2-Apache-2.0.txt.\n'
                       b'https://github.com/facebookresearch/sam2\n')
MUSIC_NOTICE_TEXT = (b'ACE-Step 1.5, Copyright (c) 2026 ACEStep. Licensed under the MIT License; see '
                     b'freevideo_engine/licenses/ACE-Step-MIT.txt.\nhttps://github.com/ace-step/ACE-Step-1.5\n'
                     b'Qwen3-Embedding-0.6B, Copyright (c) Alibaba Cloud. Licensed under Apache-2.0.\n'
                     b'Silero VAD, Copyright (c) 2020-present Silero Team. Licensed under the MIT License; see '
                     b'freevideo_engine/licenses/Silero-VAD-MIT.txt.\nhttps://github.com/snakers4/silero-vad\n')
NOTICES = {'image': ('NOTICE.txt', NOTICE_TEXT), 'segment': ('SAM2-NOTICE.txt', SEGMENT_NOTICE_TEXT),
           'music': ('MUSIC-NOTICE.txt', MUSIC_NOTICE_TEXT)}


def _notice():
    return NOTICE_TEXT


def _notice_ready(path, *, group='image'):
    """Informational only: a missing or different notice never makes the pack unusable."""
    try:
        return path.is_file() and path.read_bytes() == NOTICES[group][1]
    except OSError:
        return False


def _write_notice(folder, owned, *, group='image'):
    """Put the notice into the model folder when possible; a failure is logged and the install goes on."""
    try:
        notice = _path(folder, NOTICES[group][0])
        if notice.exists():
            return   # present (ours or the user's own text): left as it is
        notice.write_bytes(NOTICES[group][1])
        _own(folder, owned, notice)
    except Exception as error:
        logging.warning('[FreeVideo] Could not write the image model notice: %s', redacted_launcher_error(error))


_RECHECKS = {}
_RECHECKED = set()
_RETRY_AT = {}
_KEYS = {}
_RECHECK_GUARD = threading.Lock()
_RETRY_SECONDS = 300


def _stale(path, row, ledger):
    """A complete file that was verified before and whose stamp no longer matches."""
    try:
        return path.is_file() and path.stat().st_size == row['bytes'] and isinstance(ledger.get(str(path)), dict)
    except OSError:
        return False


def _same_file(path, row, before):
    try:
        return provision.file_identity(path, row) == before
    except OSError:
        return False


def _recheck(root, folder, pending):
    """Hash files whose stamp went stale and refresh the receipt; contents decide, not the stamp.

    Hashing only reads. Under the pack lease the result is merged only into the receipt and the
    file it was read from: a file or receipt changed in the meantime (a repair, an install) keeps
    its newer state and is looked at again later. A file is marked as checked only once its result
    was published; any failure leaves it to be retried after a pause.
    """
    published = []
    try:
        stamps = deepcopy(_read(folder, LEDGER))
        results = []
        for row in rows(group=None):
            path = target(row, root)
            name = str(path)
            if name in pending and _stale(path, row, stamps):
                prior, before, shape = deepcopy(stamps[name]), provision.file_identity(path, row), model_repair.identity(path)
                results.append((row, path, prior, before, shape, provision.verified(path, row, stamps, mode='full')))
        with _lease(root):
            ledger = _read(folder, LEDGER)
            for row, path, prior, before, shape, intact in results:
                name = str(path)
                if ledger.get(name) != prior or not _same_file(path, row, before):
                    continue
                if intact:
                    stamp = stamps[name]
                    if not all(stamp.get(k) == v for k, v in before.items()):
                        continue
                    ledger[name] = stamp
                else:
                    ledger.pop(name, None)
                    model_repair.record(folder, path, row, shape)
                published.append(name)
            save(_path(folder, LEDGER), ledger)
    except Exception as error:
        published = []
        logging.warning('[FreeVideo] Could not re-check the image model files: %s', redacted_launcher_error(error))
    finally:
        with _RECHECK_GUARD:
            for name in pending:
                key = _KEYS.pop((folder, name), None)
                if key is None:
                    continue
                if name in published and key[1] is not None:
                    _RECHECKED.add(key[0])
                else:
                    _RETRY_AT[key[0]] = time.monotonic() + _RETRY_SECONDS
            _RECHECKS.pop(folder, None)



def _start_recheck(root, folder, stale):
    """One background check per folder; a file already checked at this identity is not hashed again."""
    keys = {}
    for path in stale:
        try:
            info = provision.fingerprint(path)
        except OSError:
            continue
        # The same identity the receipt is compared by, so a change the stamp would notice is noticed here.
        keys[str(path)] = (str(path), tuple(sorted(info.items())))
    now = time.monotonic()
    with _RECHECK_GUARD:
        if folder in _RECHECKS:
            return True
        pending = {name for name, key in keys.items()
                   if key not in _RECHECKED and _RETRY_AT.get(key, 0) <= now}
        if not pending or downloading(root):
            return False
        for name in pending:
            reliable = dict(keys[name][1]).get('change_time_ns', 0) is not None
            _KEYS[(folder, name)] = (keys[name], reliable)
        worker = threading.Thread(target=_recheck, args=(root, folder, pending), name='image-pack-recheck', daemon=True)
        try:
            worker.start()
        except Exception:
            for name in pending:
                _KEYS.pop((folder, name), None)
            raise
        # Registered only after it runs, so a lease taken meanwhile never joins a thread that has not started.
        _RECHECKS[folder] = worker
    return True


def status(root=None, *, group='image'):
    """Missing files and unique retained bytes; no network and no weight reads on this thread."""
    folder = base(root)
    ledger = _read(folder, LEDGER)
    files, missing, downloaded, verified_bytes = [], [], 0, 0
    stale = []
    for row in rows(group=group):
        path = target(row, root)
        intact = _intact(path, row, ledger)
        old = not intact and _stale(path, row, ledger)
        if old:
            stale.append(path)
        size = path.stat().st_size if path.is_file() else 0
        part = _path(folder, row['file'] + '.partial')
        partial = part.stat().st_size if part.is_file() else 0
        retained = row['bytes'] if intact else min(row['bytes'], max(size, partial))
        downloaded += retained
        verified_bytes += row['bytes'] if intact else 0
        if not intact:
            missing.append(row['file'])
        files.append(dict(file=row['file'], bytes=row['bytes'], ready=intact, downloaded_bytes=retained, stale=old))
    checking = bool(stale) and _start_recheck(root, folder, stale)
    try:
        notice_ready = _notice_ready(_path(folder, NOTICES[group][0]), group=group)
    except PackError:
        notice_ready = False
    total = sum(r['bytes'] for r in files)
    available = not missing
    # Files being re-checked are all there: nothing to download for them.
    return dict(installable=True, ready=available, total_bytes=total, verified_bytes=verified_bytes,
                downloaded_bytes=downloaded,
                bytes=sum(r['bytes'] for r in files if not r['ready'] and not (checking and r['stale'])),
                remaining_bytes=sum(r['bytes'] - r['downloaded_bytes'] for r in files if not r['ready']),
                missing=missing, notice_ready=notice_ready, files=files, checking=checking,
                state='ready' if available else 'partial' if downloaded else 'missing')


def checking(root=None) -> bool:
    """True while a background stamp re-check of this pack is hashing."""
    return base(root) in _RECHECKS


def ready(root=None, *, group='image'):
    return status(root, group=group)['ready']


def downloading(root=None, engine_root=None) -> bool:
    """Probe the existing pack lease without creating files or waiting."""
    try:
        path = _path(base(root), 'pack.lock')
        # install() holds this lease through transfers and generation pauses.
        # A separate descriptor also detects a holder in this process.
        with path.open('rb') as stream:
            if sys.platform == 'win32':
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBRLCK, 1)
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    return True
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
        return False
    except Exception:
        return False


@contextmanager
def _lease(root, engine_root=None, destructive=False):
    if root is None and engine_root is None:
        from ..paths import data_root
        engine_root = data_root()
    folder = base(root)
    _path(folder, 'pack.lock')
    _path(folder, 'logs/model-mismatches.json')
    # A stamp re-check started by status() only reads and then merges its receipt; let it finish first.
    worker = _RECHECKS.get(folder)
    if worker is not None and worker is not threading.current_thread():
        worker.join()
    with ExitStack() as stack:
        if engine_root is not None:
            engine = Path(engine_root)
            stack.enter_context(runtime_lock(engine / 'setup.lock', inherit=False))
            if destructive:
                stack.enter_context(runtime_lock(engine / 'engine.lock', inherit=False))
                stack.enter_context(runtime_lock(engine / 'launcher/host-setup.lock', inherit=False))
        stack.enter_context(runtime_lock(folder / 'pack.lock', inherit=False))
        yield folder


def verify(root=None, progress=None, check=None, *, group='image'):
    """Explicit full integrity check; bad stamps are discarded, files are kept."""
    with _lease(root) as folder:
        _verify(folder, root, progress or (lambda v: None), check or (lambda: None), full=True, group=group)
        return status(root, group=group)


def _verify(folder, root, progress, check, full=False, *, group='image'):
    ledger = _read(folder, LEDGER)
    todo = []
    total = sum(r['bytes'] for r in rows(group=group))
    done = 0
    for row in rows(group=group):
        check()
        path = target(row, root)
        hashed = False
        def checked(current, size):
            nonlocal hashed
            hashed = True
            check()
            progress(dict(phase='verify', done=done + current, total=total))
        before = model_repair.identity(path)
        intact = provision.verified(path, row, ledger, mode='full' if full else 'auto', progress=checked)
        progress(dict(phase='diagnostic', diagnostic=dict(file=row['file'], verification='verified' if intact else
                                      'mismatch' if path.exists() else 'missing',
                                      method='sha256' if hashed else 'receipt' if intact else 'size')))
        if not intact:
            ledger.pop(str(path), None)
            if path.exists():
                model_repair.record(folder, path, row, before)
            todo.append(row)
        done += row['bytes']
    save(_path(folder, LEDGER), ledger)
    return ledger, todo


def _download_plan(engine_root=None):
    from ..adaln_assets import _download_plan as installed
    plan = installed(engine_root)
    plan = dict(plan, sources={k: [dict(r) for r in v] for k, v in plan.get('sources', {}).items()})
    # Older installations measured these same hosts for video weights. Keep
    # that measured ranking until an image-specific probe is available.
    if FAMILY not in plan['sources']:
        inherited = plan['sources'].get('vdn-models', [])
        plan['sources'][FAMILY] = [dict(r) for r in inherited if r['id'] in ('official', 'modelscope', 'hf-mirror')]
    known = {r['id'] for r in plan['sources'][FAMILY]}
    plan['sources'][FAMILY] += [dict(id=s) for s in ('official', 'hf-mirror', 'modelscope') if s not in known]
    return plan


def _own(folder, owned, path, row=None):
    owned[path.relative_to(folder).as_posix()] = provision.file_identity(path, row or {})
    save(_path(folder, OWNERSHIP), owned)


def _transfer(plan, row, path, progress, check, background=None, diagnostic=None):
    from ..download_settings import current, SwitchRequested
    failures = deque(maxlen=128)
    plan = dict(plan)
    prior_event = plan.get('event_callback')
    def event(value):
        if value.get('action') in ('fallback', 'route-retry'):
            failures.append((value.get('reason'), value.get('http_status')))
        # Only catalogue labels and bounded numeric transfer metadata cross
        # into diagnostics. Never retain URLs, headers, paths or settings.
        details = {k: value[k] for k in ('action', 'source', 'route', 'reason', 'http_status',
                                        'bytes', 'resume_bytes', 'observed_bytes', 'expected_bytes') if k in value}
        details['file'] = row['file']
        if value.get('action') == 'verified':
            details.update(verification='verified', method='sha256')
        elif value.get('reason') == 'integrity-mismatch':
            details.update(verification='mismatch', method='sha256')
        if diagnostic:
            diagnostic(dict(phase='diagnostic', diagnostic=details))
        if prior_event:
            prior_event(value)
    plan['event_callback'] = event
    while True:
        if background is not None:
            background.wait()
        settings = current(plan)
        # An explicit choice supersedes affinity to the old partial's source.
        plan['manual_source_switch'] = settings['source'] in ('official', 'hf-mirror', 'modelscope')
        def resource_check():
            check()
            latest = current(plan)
            if any(latest[k] != settings[k] for k in ('source', 'proxy_mode')):
                raise SwitchRequested(latest)
        plan['resource_check'] = resource_check
        try:
            failures.clear()
            network.download(network.model_urls(plan, row), path, row['sha256'], progress,
                             network=plan, size=row['bytes'], category=FAMILY,
                             keep_partial=True, stall_seconds=30,
                             slow_seconds=15, low_speed_limit=64 * 1024)
            return
        except SwitchRequested:
            if background is None:
                check()  # Resume with the newly selected policy.
        except _GenerationPause:
            # network.download's finally terminates/reaps curl, retaining the
            # partial and source affinity. The next attempt uses its Range.
            if background is None:
                raise
        except ConnectionError as error:
            raise PackError('offline') from error
        except RuntimeError as error:
            offline = failures and all(reason in OFFLINE_TRANSFER_REASONS and
                                       status in (None, '', '000') for reason, status in failures)
            reason = ('integrity_failed' if failures and failures[-1][0] in ('integrity-mismatch', 'response-size-exceeded')
                      else 'write_failed' if failures and failures[-1][0] == 'curl-23'
                      else 'offline' if offline else 'source_failed')
            raise PackError(reason) from error


def install(root=None, progress=None, check=None, *, engine_root=None, repair=False, _background=None, group='image'):
    """Install only missing weights. Repair also hashes and retains bad originals."""
    progress = progress or (lambda v: None)
    check = check or (lambda: None)
    transfer_check = _background.check if _background is not None else check
    try:
        with _lease(root, engine_root, destructive=repair) as folder:
            ledger, todo = _verify(folder, root, progress, check, full=repair, group=group)
            total = sum(r['bytes'] for r in todo)
            retained = 0
            for row in todo:
                partial = _path(folder, row['file'] + '.partial')
                if partial.is_file() and partial.stat().st_size < row['bytes']:
                    retained += partial.stat().st_size
            reserve = BACKGROUND_RESERVE if _background is not None else RESERVE
            needed = total - retained + reserve
            free = disk_space.free_bytes(folder)
            if total and free < needed:
                raise PackError('disk_space', needed_bytes=needed, free_bytes=free)
            plan = _download_plan(engine_root)
            plan.update(quiet=True, resource_check=check)
            owned = _read(folder, OWNERSHIP)
            _write_notice(folder, owned, group=group)
            done = 0
            pending_prefixes = 0
            if _background is not None:
                done = sum(r['bytes'] for r in rows(group=group)) - total
                total += done
                pending_prefixes = sum(min(row['bytes'], _path(folder, row['file'] + '.partial').stat().st_size)
                                       for row in todo if _path(folder, row['file'] + '.partial').is_file())
                progress(dict(phase='download', done=done + pending_prefixes, total=total))
            for row in todo:
                check()
                path = target(row, root)
                if path.exists():
                    if not repair:
                        raise PackError('needs_repair')
                    approved = model_repair.replacement(path, row, model_repair.read(folder), set())
                    if approved is None:
                        raise PackError('changed')
                    model_repair.retain(folder, path, approved)
                    owned.pop(row['file'], None)
                    save(_path(folder, OWNERSHIP), owned)
                partial = _path(folder, row['file'] + '.partial')
                if _background is not None and partial.is_file():
                    pending_prefixes -= min(row['bytes'], partial.stat().st_size)
                affinity = _path(folder, row['file'] + '.partial.source.json')
                may_own = {p: not p.exists() or _owned_intact(p, owned.get(p.relative_to(folder).as_posix()))
                           for p in (partial, affinity)}
                def moved(current, size, speed, **kwargs):
                    progress(dict(phase='download', done=done + current + pending_prefixes, total=total))
                    transfer_check()
                try:
                    _transfer(plan, row, path, moved, transfer_check, _background, progress)
                finally:
                    # Completed files and cancellable prefixes have separate identities.
                    for artifact in (partial, affinity):
                        if may_own[artifact] and _path(folder, artifact.relative_to(folder).as_posix()).is_file():
                            _own(folder, owned, artifact)
                ledger[str(path)] = provision.file_identity(path, row)
                save(_path(folder, LEDGER), ledger)
                _own(folder, owned, path, row)
                done += row['bytes']
                progress(dict(phase='download', done=done + pending_prefixes, total=total))
            check()
            return status(root, group=group)
    except (PackError, InterruptedError):
        raise
    except BlockingIOError as error:
        raise PackError('busy') from error
    except Exception as error:
        raise PackError('download_failed') from error


def repair(root=None, progress=None, check=None, *, engine_root=None, group='image'):
    return install(root, progress, check, engine_root=engine_root, repair=True, group=group)


def removal_plan(root=None, *, group='image'):
    folder = base(root)
    owned = _read(folder, OWNERSHIP)
    notice = NOTICES[group][0]
    allowed = {notice}
    for row in rows(group=group):
        allowed.update((row['file'], row['file'] + '.partial', row['file'] + '.partial.source.json'))
    for name in allowed:
        if name == notice:
            continue
        _path(folder, name)
    files, skipped = [], []
    for name, stamp in owned.items():
        if name not in allowed or not isinstance(stamp, dict):
            continue
        try:
            path = _path(folder, name)
        except PackError:
            if name != notice:
                raise
            skipped.append(name)
            continue
        if not path.exists():
            continue
        if _owned_intact(path, stamp):
            files.append(dict(file=name, identity=stamp, bytes=path.stat().st_size))
        else:
            skipped.append(name)
    return dict(files=files, bytes=sum(f['bytes'] for f in files), skipped=skipped)


def remove(root=None, check=None, *, engine_root=None, group='image'):
    """Delete our unchanged downloads/partials/notice; keep imports and repairs."""
    check = check or (lambda: None)
    try:
        with _lease(root, engine_root, destructive=True) as folder:
            plan = removal_plan(root, group=group)
            owned, ledger = _read(folder, OWNERSHIP), _read(folder, LEDGER)
            released, removed = 0, []
            for entry in plan['files']:
                check()
                path = _path(folder, entry['file'])
                stamp = entry['identity']
                if not _owned_intact(path, stamp):
                    plan['skipped'].append(entry['file'])
                    continue
                path.unlink()
                removed.append(entry['file'])
                released += entry['bytes']
                owned.pop(entry['file'], None)
                ledger.pop(str(path), None)
                save(_path(folder, OWNERSHIP), owned)
                save(_path(folder, LEDGER), ledger)
            return dict(released_bytes=released, removed=removed, skipped=plan['skipped'])
    except BlockingIOError as error:
        raise PackError('busy') from error


class Installer:
    """One background action per pack; cancel keeps completed files and prefixes."""
    def __init__(self, root=None, *, engine_root=None, busy=None, group='image'):
        self.root, self.engine_root = root, engine_root
        self.group = group
        self.generation_busy = busy
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.value = dict(state='idle', done=0, total=0, bytes_per_second=0, reason=None)
        self.samples = deque()
        self.transferred = 0
        self.last_done = None
        self.background = False
        self.reports = {}
        self.file_checks = {}
        self.events = deque(maxlen=128)
        self.started = None

    def failure_report(self, report_id=None):
        """An immutable, already redacted snapshot; exports survive a retry."""
        with self.lock:
            return deepcopy(self.reports.get(report_id or self.value.get('report_id')))

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def state(self):
        with self.lock:
            return dict(self.value, bytes_per_second=self._rate(time.monotonic()))

    def _rate(self, now):
        if self.value['state'] != 'running' or not self.samples:
            return 0
        cutoff = now - SPEED_WINDOW_SECONDS
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()
        start, amount = self.samples[0]
        if start < cutoff:
            if len(self.samples) > 1:
                end, next_amount = self.samples[1]
                amount += (next_amount - amount) * (cutoff - start) / (end - start)
            else:
                amount = self.transferred
            start = cutoff
        return max(0, self.transferred - amount) / (now - start) if now > start else 0

    def _progress(self, value):
        value = dict(value)
        diagnostic = value.pop('diagnostic', None)
        if diagnostic is not None:
            with self.lock:
                file = diagnostic['file']
                self.file_checks.setdefault(file, {}).update({k: diagnostic[k] for k in
                    ('verification', 'method', 'source') if k in diagnostic})
                if 'action' in diagnostic:
                    self.events.append(dict(diagnostic))
            return
        with self.lock:
            if value.get('phase') == 'download':
                now, done = time.monotonic(), value['done']
                if self.last_done is not None:
                    self.transferred += max(0, done - self.last_done)
                self.last_done = done
                if not self.samples or now > self.samples[-1][0]:
                    self.samples.append((now, self.transferred))
                self.value.update(bytes_per_second=self._rate(now))
            elif self.background:
                # Verification byte counts measure hashing, not downloaded bytes.
                value.pop('done', None)
                value.pop('total', None)
        self._set(**value)

    def _set(self, **value):
        with self.lock:
            if 'state' in value and value['state'] != self.value['state'] and self.samples:
                self.samples.append((time.monotonic(), self.transferred))
            self.value.update(value)

    def start(self, action='install', *, background=False):
        if action not in ('install', 'repair', 'remove', 'verify'):
            raise ValueError('Unknown image pack action')
        if background and action != 'install':
            raise ValueError('Background mode is only available for install')
        with self.lock:
            if self.busy:
                return dict(self.value)
            self.stop = threading.Event()
            self.background = background
            self.samples.clear()
            self.transferred, self.last_done = 0, None
            self.file_checks.clear()
            self.events.clear()
            self.started = time.monotonic()
            self.value = dict(state='running', action=action, done=0, total=0, bytes_per_second=0, reason=None, report_id=None)
            self.thread = threading.Thread(target=self._run, args=(action, self.stop, background),
                                           name='freevideo-image-pack', daemon=True)
            self.thread.start()
            return dict(self.value)

    def cancel(self):
        self.stop.set()
        return self.state()

    def _run(self, action, stop, background):
        def check():
            if stop.is_set():
                raise InterruptedError('cancelled')
        try:
            control = _Background(self, stop) if background else None
            if background:
                initial = status(self.root, group=self.group)
                self._set(done=initial['downloaded_bytes'], total=initial['total_bytes'])
            options = dict(root=self.root, check=control.wait if control else check)
            if self.group != 'image':
                options['group'] = self.group
            if control:
                options['_background'] = control
            if action != 'verify':
                options['engine_root'] = self.engine_root
            if action != 'remove':
                options['progress'] = self._progress
            result = globals()[action](**options)
            check()
            if action == 'verify' and not result['ready']:
                raise PackError('integrity_failed' if any(v.get('verification') == 'mismatch'
                                for v in self.file_checks.values()) else 'needs_repair')
            if background:
                self._set(done=result['total_bytes'], total=result['total_bytes'])
            self._set(state='done', reason=None, result=result)
        except InterruptedError:
            self._set(state='cancelled', reason=None)
        except Exception as error:
            self._failed(action, error)

    def _failed(self, action, error):
        """Collect metadata only, even when the pack or catalogue is unreadable."""
        from .. import __version__
        code = image_pack_error_code(error)
        try:
            spec = catalog()
            entries = rows(spec, group=self.group)
        except Exception:
            spec, entries = {}, []
        try:
            pack = status(self.root, group=self.group)
        except Exception:
            pack = dict(ready=False, files=[], missing=[], downloaded_bytes=0)
        with self.lock:
            checks, events = deepcopy(self.file_checks), list(self.events)
            downloaded = self.transferred
        live = {f['file']: f for f in pack['files']}
        files = []
        for row in entries:
            observed = live.get(row['file'], {})
            file = dict(file=row['file'], expected_bytes=row['bytes'], expected_sha256=row['sha256'],
                        downloaded_bytes=observed.get('downloaded_bytes', 0), ready=observed.get('ready', False),
                        verification='not_checked', method=None)
            file.update(checks.get(row['file'], {}))
            files.append(file)
        pack = {k: pack[k] for k in ('ready', 'missing', 'downloaded_bytes', 'verified_bytes', 'total_bytes') if k in pack}
        identity = entries[0] if entries and self.group == 'segment' else spec
        pack.update(name=identity.get('model_id', 'image'), repo=identity.get('repo'), revision=identity.get('revision'),
                    format=spec.get('format'), files=files, transferred_bytes=downloaded,
                    sources=[dict(file=row['file'], id=s['id'], host=urlsplit(s['url']).hostname)
                             for row in entries for s in row['sources']], events=events)
        details = {k: v for k, v in getattr(error, 'details', {}).items()
                   if k in ('needed_bytes', 'free_bytes') and type(v) is int}
        redactor = Redactor()
        # No source lines in stack frames: a caller may have embedded user
        # data in source. Retain filenames, line numbers, functions and the
        # complete exception chain, redacted by the shared launcher policy.
        stack, seen, cause = [], set(), error
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            stack.append('Traceback (most recent call last):\n')
            stack.extend('  File "%s", line %d, in %s\n' % (Path(frame.f_code.co_filename).name, line, frame.f_code.co_name)
                         for frame, line in traceback.walk_tb(cause.__traceback__))
            stack.append('%s: %s\n' % (type(cause).__name__, cause))
            cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
            if cause is not None:
                stack.append('Caused by:\n')
        report = dict(schema='freevideo.image-report', schema_version=1,
                      created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      kind='download' if action == 'install' else 'install', operation=action,
                      analysis=dict(version=__version__, hardware=dict(system=platform.system()), geometry={}),
                      summary=dict(request_seconds=max(0, time.monotonic() - self.started) if self.started else 0),
                      request={}, plan=None, stages=[], stage_at_failure=self.value.get('phase', action),
                      error=dict(code=code, **{'class': type(error).__name__}, message=str(error),
                                 traceback=''.join(stack), details=details), pack=pack,
                      service=dict(video_busy=code == 'busy_video', worker_restarts=0))
        report = json.loads(redactor.data(json.dumps(report).encode('utf-8'), '.json'))
        report_id = uuid.uuid4().hex
        with self.lock:
            self.reports[report_id] = report
            while len(self.reports) > 16:
                del self.reports[next(iter(self.reports))]
            self.value.update(state='failed', reason=code, report_id=report_id, **details)


def main(argv=None):
    """Download worker and read-only status reader for installer/launcher callers."""
    import argparse
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--status':
        argv[0] = 'status'
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    download = commands.add_parser('download')
    download.add_argument('--background', action='store_true')
    download.add_argument('--group', choices=('image', 'segment'), default='image')
    download.add_argument('--root', type=Path)
    download.add_argument('--engine-root', type=Path)
    download.add_argument('--status-file', type=Path, required=True)
    reader = commands.add_parser('status')
    reader.add_argument('--status-file', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'status':
        try:
            sys.stdout.write(args.status_file.read_text(encoding='utf-8'))
            return 0
        except OSError:
            print('Image pack status is unavailable.', file=sys.stderr)
            return 1

    installer = Installer(args.root, engine_root=args.engine_root, group=args.group)
    received = threading.Event()
    def cancelled(signum, frame):
        received.set()
        # A signal can interrupt state() while its lock is held in this thread.
        # Set only the cancellation event here; the worker publishes the state.
        installer.stop.set()
    handlers = {}
    try:
        for name in ('SIGTERM', 'SIGBREAK', 'SIGINT'):
            if hasattr(signal, name):
                signum = getattr(signal, name)
                handlers[signum] = signal.signal(signum, cancelled)
        installer.start('install', background=args.background)
        if received.is_set():
            installer.cancel()
        while True:
            save(args.status_file, dict(installer.state(), updated_at=time.time()))
            if not installer.busy:
                # The thread may finish between snapshotting and checking busy.
                save(args.status_file, dict(installer.state(), updated_at=time.time()))
                return 0 if installer.state()['state'] == 'done' and not received.is_set() else 1
            installer.thread.join(STATUS_INTERVAL_SECONDS)
    except OSError:
        print('Could not update image pack status.', file=sys.stderr)
        return 1
    finally:
        if installer.busy:
            installer.cancel()
            installer.thread.join()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
