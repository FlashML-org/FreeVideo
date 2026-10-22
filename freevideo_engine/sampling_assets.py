"""Sampling constants: the refinement tables every installation needs, plus the
optional quality levels, installed together or prepared before generation.

Uses the installer's catalogs, measurements, connection preferences and verified
resumable transfers. No model import, GPU work or prompt text is needed here.
"""
import json
import errno
from functools import lru_cache
from pathlib import Path
import re
import sys
import time
from . import disk_space
from .long_paths import io_path, plain_errors

COMMUNITY_PREFIX = 'community-sigma3-'
WORKERS = 6
UNAVAILABLE = 'SAMPLING_TABLE_UNAVAILABLE: '


class SamplingTableUnavailable(RuntimeError):
    """A request's tables could not be downloaded; keep the transfer cause copyable."""
    def __init__(self, details, cause):
        self.details = details
        super().__init__(UNAVAILABLE + json.dumps(details, separators=(',', ':')) + '\n' + str(cause))


class _DownloadFailure(Exception):
    def __init__(self, cause):
        self.cause = cause


def _cancel_or_disk(error):
    return (isinstance(error, (InterruptedError, KeyboardInterrupt)) or
            isinstance(error, OSError) and error.errno == errno.ENOSPC or
            bool(re.search(r'interrupt|cancel', type(error).__name__, re.I)) or
            bool(re.search(r'cancelled|canceled|interrupted|not enough disk space|insufficient disk space|'
                           r'no space left|\[Errno 28\]|\[WinError 112\]|取消|磁盘空间不足', str(error), re.I)))


def available_levels(cache, shared, manifest, kind, catalog):
    """Complete tables for this task and these weights, including the bundled 8 steps."""
    from . import adaln_assets as assets
    weights = assets.weight_identity(manifest)
    # Bundled tables have no task field; their exact clock identifies the task.
    # Match its published refinement clock's channels/dtype/modality contract.
    task_tables = [t for t in catalog if t.get('task') == kind and t['identity']['weights'] == weights]
    identities = [t['identity'] for t in task_tables]
    bundled = [t for t in manifest.get('adaln_tables', []) if kind in ('t2va', 'i2va') and
               t['identity']['weights'] == weights and any(
                   all(t['identity'].get(k) == identity.get(k) for k in ('channels', 'dtype', 'modality_rows'))
                   and len(t['identity']['timesteps'][0]) == len(identity['timesteps'][0])
                   for identity in identities)]

    def ready(table, embedded=False):
        for row in table['files']:
            found = False
            for folder in ((cache,) if embedded else (cache, shared)):
                path = assets.asset_path(folder, row['file'])
                try:
                    marker = path.with_suffix('.json')
                    # Imported shared tables need no receipt yet: prepare()
                    # verifies them and writes one without downloading.
                    if not embedded and (folder == cache or marker.is_file()):
                        receipt = json.loads(marker.read_text(encoding='utf-8'))
                        if any(receipt.get(k) != row[k] for k in ('bytes', 'sha256')):
                            continue
                    assets.check_table(path, row, table['identity'])
                    found = True
                    break
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            if not found:
                return False
        return bool(table['files'])

    def base_ready(steps):
        return any(len(t['identity']['timesteps']) == steps and ready(t, True) for t in bundled) or any(
            not refinement(t) and len(t['identity']['timesteps']) == steps and ready(t) for t in task_tables)

    refine_ready = any(refinement(t) and ready(t) for t in task_tables)
    from .tiers import table
    return [i for i, tier in enumerate(table(sys.platform == 'darwin').values())
            if base_ready(tier['steps']) and (not tier['two_pass'] or refine_ready)]


@plain_errors
def tables():
    from .prepared_model import CATALOG
    catalog = json.loads(io_path(CATALOG).read_text(encoding='utf-8'))
    banks = catalog.get('optional_adaln_sets', []) + [catalog.get('optional_adaln', {})]
    seen, result = set(), []
    for bank in banks:
        for table in bank.get('tables', []):
            count = len(table['identity']['timesteps'])
            # Every first-pass count a request accepts (two_pass.validate_steps).
            if not 1 <= count <= 32 or table['directory'] in seen:
                continue
            seen.add(table['directory'])
            # The tables for other step counts (about 54 GB) are fetched only
            # by the request that uses one; offline packs leave them out.
            result.append(dict(table, download={k: bank[k] for k in ('repo', 'revision', 'prefix')},
                               on_demand=bool(bank.get('on_demand')),
                               offline_pack=bank.get('offline_pack', True) is not False))
    return result


def refinement(table):
    """Tables of the independent three-step refinement clock."""
    return table['download']['prefix'].startswith(COMMUNITY_PREFIX)


def required(table):
    """The default 8 + 3 refinement must work offline after setup.

    On-demand tables (reference audio) are never part of setup or of "every
    quality level": the request that needs them fetches them before it starts.
    """
    return refinement(table) and not table.get('on_demand')


def preset(table):
    """Tables the "prepare all quality levels" option installs."""
    return not table.get('on_demand')


def files(selected=None):
    result = []
    for table in tables() if selected is None else selected:
        remote = table['download']
        result.extend(dict(row, repo=remote['repo'], revision=remote['revision'],
            file=remote['prefix'] + '/' + row['file'], sampling_file=row['file'])
            for row in table['files'])
    return result


def usable_with(cache):
    """Published tables match prepared weights; a locally converted cache may not."""
    from . import adaln_assets as assets
    try:
        manifest = json.loads((io_path(cache) / 'manifest.json').read_text(encoding='utf-8'))
        weights = assets.weight_identity(manifest)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return any(table['identity']['weights'] == weights for table in tables())


def installed(machine):
    """Every quality level is on disk, or none applies to these weights."""
    if not usable_with(machine['cache']):
        return True
    folders = (io_path(cache_root(machine['model_root'])), io_path(machine['cache']))
    def present(row):
        return any((folder / row['sampling_file']).is_file()
                   and (folder / row['sampling_file']).stat().st_size == row['bytes'] for folder in folders)
    return all(present(row) for row in files([t for t in tables() if preset(t)]))


def install_files(everything):
    """Setup always installs the refinement tables; the option adds every level."""
    return files([t for t in tables() if (preset(t) if everything else required(t))])


@lru_cache(maxsize=1)
def total_bytes():
    """Extra bytes the "prepare all quality levels" option adds to setup."""
    return sum(row['bytes'] for table in tables() if preset(table) and not required(table) for row in table['files'])


def cache_root(model_root):
    return Path(model_root) / 'sampling-cache'


def engine_task(media, base=None):
    """The task the engine selects after encoding these references.

    A reference video counts as audio when it has an audio stream, exactly as
    the encoder decides, unless the request turned that video's sound off;
    tables therefore match before the request starts.
    """
    from .media_request import task_for
    task = task_for(media)
    if task != 'ref2va' or media.get('conditioning_info'):
        return task
    visual = audio = False
    for ref in media.get('references', []):
        visual |= ref['kind'] in ('image', 'video')
        audio |= ref['kind'] == 'audio'
        if ref['kind'] == 'video' and not audio and ref.get('sound') is not False and not ref.get('guide'):
            path = Path(ref['path'])
            if base is not None and not path.is_absolute():
                path = Path(base) / path
            try:
                import av
                with av.open(str(path)) as container:
                    audio = bool(container.streams.audio)
            except Exception:  # Unreadable media fail in the encoder with details.
                pass
    return 'ref2va_av' if visual and audio else 'ref2va_audio' if audio else 'ref2va'


def retire_superseded(machine):
    """Remove reference-audio tables 0.3.5 replaced, once their replacements are on disk.

    Never fails a request: what cannot be removed stays and is reported.
    """
    from .paths import model_root
    from .superseded_tables import retire
    shared = cache_root(machine['model_root'])
    roots = [shared, Path(machine['cache'])]
    # 0.2.0 engine processes fetched tables below FREEVIDEO_HOME/models as well.
    legacy = cache_root(model_root())
    if legacy != shared:
        roots.append(legacy)
    try:
        result = retire(roots, tables())
    except Exception as error:  # Cleanup is best effort; the request continues.
        result = dict(removed=[], kept=[], removed_bytes=0, error=type(error).__name__)
    if result['removed'] or result['kept'] or result.get('error'):
        print(json.dumps(dict(event='superseded_tables', removed=len(result['removed']),
                              removed_bytes=result['removed_bytes'], kept=result['kept'][:10],
                              error=result.get('error'))), flush=True)
    return result


def select(manifest, sampling, task, catalog=None):
    """The published tables one request samples with; the single selection rule.

    prepare() fetches exactly these and pending() reports them without touching disk.
    """
    from . import adaln_assets as assets
    from .refine_schedule import COMMUNITY
    weights = assets.weight_identity(manifest)
    kind = 'i2va' if task in ('i2va', 'l2va', 'fl2va', 'ref2va') else task
    def needed(table):
        count = len(table['identity']['timesteps'])
        return (count == 3 and sampling.get('refine_schedule') == COMMUNITY if refinement(table)
                else count == sampling['base_steps'])
    return kind, [t for t in (tables() if catalog is None else catalog) if t.get('task') == kind
                  and needed(t) and t['identity']['weights'] == weights]


def pending(root, machine, sampling, task):
    """Read-only: table files this request would download. Nothing is written,
    moved or fetched; a file of the right size counts as present (prepare()
    hashes it once and downloads again if it does not match)."""
    from . import adaln_assets as assets
    cache = Path(machine['cache'])
    try:
        manifest = json.loads(io_path(cache / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    _, selected = select(manifest, sampling, task)
    shared = cache_root(machine['model_root'])
    missing = []
    for table in selected:
        for row in table['files']:
            for folder in (cache, shared):
                path = assets.asset_path(folder, row['file'])
                try:
                    if io_path(path).stat().st_size == row['bytes']:
                        break
                except OSError:
                    pass
            else:
                missing.append(dict(table=table, row=row))
    return missing


def prepare(root, machine, sampling, task, *, progress, interrupted=None, environ=None):
    """Fetch only this request's missing tables before starting its timer."""
    from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
    import shutil
    import threading
    from . import adaln_assets as assets, network, provision
    from .monitoring import save
    cache = Path(machine['cache'])
    if not (cache / 'manifest.json').is_file():
        return dict(seconds=0., downloaded_bytes=0)  # Generation reports an invalid installation.
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
    catalog = tables()
    kind, selected = select(manifest, sampling, task, catalog)
    root = Path(root)
    shared = cache_root(machine['model_root'])
    ledger = root / 'verified-models.json'
    stamps = json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {}
    stop = threading.Event()
    def check():
        if stop.is_set():
            raise RuntimeError('Sampling cache download stopped')
        if interrupted:
            interrupted()
    missing, recorded = [], len(stamps)
    for table in selected:
        for row in table['files']:
            check()
            # The engine verifies a cache's own receipt and content before use.
            local = assets.asset_path(cache, row['file'])
            if local.is_file() and local.with_suffix('.json').is_file():
                continue
            path = assets.asset_path(shared, row['file'])
            if provision.verified(path, row, stamps):
                if not path.with_suffix('.json').is_file():
                    save(path.with_suffix('.json'), dict(bytes=row['bytes'], sha256=row['sha256']))
                continue
            if path.exists():
                # A damaged or superseded copy must not block every request.
                network.retain_partial(path, 'rejected')
                if path.with_suffix('.json').exists():
                    network.retain_partial(path.with_suffix('.json'), 'rejected')
            missing.append((table, row, path))
    if len(stamps) != recorded:
        save(ledger, stamps)
    if not missing:
        retire_superseded(machine)
        return dict(seconds=0., downloaded_bytes=0)
    started = time.monotonic()
    total = sum(row['bytes'] for _, row, _ in missing)
    shared.mkdir(parents=True, exist_ok=True)
    free = disk_space.free_bytes(shared)
    if free < total + 256 * 2**20:
        raise ValueError('Not enough disk space for sampling caches: %.0f MiB needed, %.0f MiB free in %s. '
                         'Free space and retry.' % ((total + 256 * 2**20) / 2**20, free / 2**20, shared))
    lock = threading.Lock()
    done, speeds, shown = [0] * len(missing), [0.] * len(missing), [0.]
    def emit(stage='download', force=False):
        check()
        with lock:
            now = time.monotonic()
            if not force and now - shown[0] < .25:
                return
            shown[0] = now
            current = sum(done)
            progress(dict(phase='dependencies', stage=stage, label='Installing sampling cache',
                done=current, total=total, unit='bytes', bytes_per_second=sum(speeds),
                overall=dict(status='preparing', estimated=False, fraction=current/total, elapsed_seconds=0.)))
    # Sources ranked at setup, with the current download settings and fallback;
    # no new speed test before every request.
    networking = assets._download_plan(root)
    networking.update(quiet=True, resource_check=check)
    from .prepared_model import token
    secret = token(environ)
    headers = lambda source: ['Authorization: Bearer '+secret] if secret and source == 'official' else []
    def fetch(index, table, row, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        remote = table['download']
        spec = dict(repo=remote['repo'], revision=remote['revision'], file=remote['prefix']+'/'+row['file'])
        def moved(current, size, speed, **_):
            done[index], speeds[index] = current, speed or 0.
            emit()
        try:
            network.download(network.model_urls(networking, spec, environ), path, row['sha256'], moved,
                network=networking, size=row['bytes'], category=network.model_family(spec), headers_for=headers,
                keep_partial=True, stall_seconds=30, slow_seconds=15, low_speed_limit=64*1024)
        except Exception as error:
            if _cancel_or_disk(error):
                raise
            raise _DownloadFailure(error) from error
        # network.download already verified the SHA-256.
        assets.check_table(path, row, table['identity'], verify_hash=False)
        save(path.with_suffix('.json'), dict(bytes=row['bytes'], sha256=row['sha256']))
        with lock:
            done[index], speeds[index] = row['bytes'], 0.
            stamps[str(path)] = provision.file_identity(path, row)
            save(ledger, stamps)
        emit(force=True)
    emit(force=True)
    # Each table is fifty small files; several transfers hide per-file latency.
    failure = None
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(missing)), thread_name_prefix='sampling-cache') as pool:
        futures = [pool.submit(fetch, index, *item) for index, item in enumerate(missing)]
        # A cancel reaches one worker (ComfyUI clears its flag once raised): stop
        # the rest at once instead of waiting for earlier transfers to finish.
        completed, _ = wait(futures, return_when=FIRST_EXCEPTION)
        failed = next((future for future in futures if future in completed and future.exception()), None)
        if failed is not None:
            stop.set()
            for future in futures:
                future.cancel()
            failure = failed.exception()
    if isinstance(failure, _DownloadFailure):
        details = dict(base_steps=sampling['base_steps'], two_pass=bool(sampling.get('enabled')),
                       refine_steps=sampling.get('refine_steps', 0), missing_bytes=total,
                       available=available_levels(cache, shared, manifest, kind, catalog),
                       decimal_sizes=sys.platform == 'darwin',
                       **({'step_cache': sampling['step_cache']} if sampling.get('step_cache') else {}),
                       **({'scale': sampling['scale']} if sampling.get('scale', 2) != 2 else {}))
        raise SamplingTableUnavailable(details, failure.cause) from failure.cause
    if failure is not None:
        raise failure
    seconds = time.monotonic() - started
    retire_superseded(machine)
    return dict(seconds=seconds, downloaded_bytes=total)
