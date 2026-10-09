"""Sampling constants: the refinement tables every installation needs, plus the
optional quality levels, installed together or prepared before generation.

Uses the installer's catalogs, measurements, connection preferences and verified
resumable transfers. No model import, GPU work or prompt text is needed here.
"""
import json
from functools import lru_cache
from pathlib import Path
import time
from . import disk_space

COMMUNITY_PREFIX = 'community-sigma3-'
WORKERS = 6


def tables():
    from .prepared_model import CATALOG
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    banks = catalog.get('optional_adaln_sets', []) + [catalog.get('optional_adaln', {})]
    seen, result = set(), []
    for bank in banks:
        for table in bank.get('tables', []):
            count = len(table['identity']['timesteps'])
            if count not in (3, 8, 12, 16, 20) or table['directory'] in seen:
                continue
            seen.add(table['directory'])
            result.append(dict(table, download={k: bank[k] for k in ('repo', 'revision', 'prefix')},
                               on_demand=bool(bank.get('on_demand'))))
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
        manifest = json.loads((Path(cache) / 'manifest.json').read_text(encoding='utf-8'))
        weights = assets.weight_identity(manifest)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return any(table['identity']['weights'] == weights for table in tables())


def installed(machine):
    """Every quality level is on disk, or none applies to these weights."""
    if not usable_with(machine['cache']):
        return True
    folders = (cache_root(machine['model_root']), Path(machine['cache']))
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
    the encoder decides; tables therefore match before the request starts.
    """
    from .media_request import task_for
    task = task_for(media)
    if task != 'ref2va' or media.get('conditioning_info'):
        return task
    visual = audio = False
    for ref in media.get('references', []):
        visual |= ref['kind'] in ('image', 'video')
        audio |= ref['kind'] == 'audio'
        if ref['kind'] == 'video' and not audio:
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


def prepare(root, machine, sampling, task, *, progress, interrupted=None, environ=None):
    """Fetch only this request's missing tables before starting its timer."""
    from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
    import shutil
    import threading
    from . import adaln_assets as assets, network, provision
    from .monitoring import save
    from .refine_schedule import COMMUNITY
    cache = Path(machine['cache'])
    if not (cache / 'manifest.json').is_file():
        return dict(seconds=0., downloaded_bytes=0)  # Generation reports an invalid installation.
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
    weights = assets.weight_identity(manifest)
    kind = 'i2va' if task in ('i2va', 'l2va', 'fl2va', 'ref2va') else task
    def needed(table):
        count = len(table['identity']['timesteps'])
        return (count == 3 and sampling.get('refine_schedule') == COMMUNITY if refinement(table)
                else count == sampling['base_steps'])
    selected = [t for t in tables() if t.get('task') == kind
                and needed(t) and t['identity']['weights'] == weights]
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
        network.download(network.model_urls(networking, spec, environ), path, row['sha256'], moved,
            network=networking, size=row['bytes'], category=network.model_family(spec), headers_for=headers,
            keep_partial=True, stall_seconds=30, slow_seconds=15, low_speed_limit=64*1024)
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
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(missing)), thread_name_prefix='sampling-cache') as pool:
        futures = [pool.submit(fetch, index, *item) for index, item in enumerate(missing)]
        # A cancel reaches one worker (ComfyUI clears its flag once raised): stop
        # the rest at once instead of waiting for earlier transfers to finish.
        done, _ = wait(futures, return_when=FIRST_EXCEPTION)
        failed = next((future for future in futures if future in done and future.exception()), None)
        if failed is not None:
            stop.set()
            for future in futures:
                future.cancel()
            raise failed.exception()
    seconds = time.monotonic() - started
    retire_superseded(machine)
    return dict(seconds=seconds, downloaded_bytes=total)
