"""Optional sampling constants: install together or prepare before generation.

Uses the installer's catalogs, measurements, connection preferences and verified
resumable transfers. No model import, GPU work or prompt text is needed here.
"""
import json
from functools import lru_cache
from pathlib import Path
import time


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
            result.append(dict(table, download={k: bank[k] for k in ('repo', 'revision', 'prefix')}))
    return result


def files(selected=None):
    result = []
    for table in tables() if selected is None else selected:
        remote = table['download']
        result.extend(dict(row, repo=remote['repo'], revision=remote['revision'],
            file=remote['prefix'] + '/' + row['file'], sampling_file=row['file'])
            for row in table['files'])
    return result


@lru_cache(maxsize=1)
def total_bytes():
    return sum(row['bytes'] for row in files())


def cache_root(model_root):
    return Path(model_root) / 'sampling-cache'


def prepare(root, machine, sampling, task, *, progress, interrupted=None, environ=None):
    """Fetch only this request's missing tables before starting its timer."""
    from . import adaln_assets as assets, network, provision
    from .download_settings import read
    from .monitoring import save
    cache = Path(machine['cache'])
    if not (cache / 'manifest.json').is_file():
        return dict(seconds=0., downloaded_bytes=0)  # Generation reports an invalid installation.
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
    weights = assets.weight_identity(manifest)
    kind = 'i2va' if task in ('i2va', 'l2va', 'fl2va', 'ref2va') else task
    def needed(table):
        independent = table['download']['prefix'].startswith('community-sigma3-')
        count = len(table['identity']['timesteps'])
        return (count == 3 and sampling.get('refine_schedule') == 'community-sigma3-v1' if independent
                else count == sampling['base_steps'])
    selected = [t for t in tables() if t.get('task') == kind
                and needed(t) and t['identity']['weights'] == weights]
    root = Path(root)
    shared = cache_root(machine['model_root'])
    ledger = root / 'verified-models.json'
    stamps = json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {}
    missing = []
    def check():
        if interrupted:
            interrupted()
    for table in selected:
        for row in table['files']:
            check()
            # Keep valid locally computed constants and prior on-demand files.
            local = assets.asset_path(cache, row['file'])
            marker = local.with_suffix('.json')
            if local.is_file() and marker.is_file():
                prior = json.loads(marker.read_text(encoding='utf-8'))
                assets.check_table(local, prior, table['identity'])
                continue
            path = assets.asset_path(shared, row['file'])
            if provision.verified(path, row, stamps):
                save(path.with_suffix('.json'), dict(bytes=row['bytes'], sha256=row['sha256']))
                continue
            if path.exists():
                raise ValueError('Sampling cache failed verification; existing file retained: ' + str(path))
            missing.append((table, row, path))
    if not missing:
        return dict(seconds=0., downloaded_bytes=0)
    started = time.monotonic()
    total = sum(row['bytes'] for _, row, _ in missing)
    def emit(done, speed=0., stage='download'):
        check()
        progress(dict(phase='dependencies', stage=stage, label='Installing sampling cache',
            done=done, total=total, unit='bytes', bytes_per_second=speed,
            overall=dict(status='preparing', estimated=False, fraction=done/total, elapsed_seconds=0.)))
    emit(0, stage='probe')
    package = Path(__file__).parent
    preferences = read(root / 'download-settings.json')
    measured = preferences.get('probe', {})
    if (time.time()-measured.get('measured_at', 0) < 86400
            and measured.get('proxy_mode', 'auto') == preferences['proxy_mode']):
        networking = dict(sources=measured['sources'], proxy_mode=preferences['proxy_mode'])
    else:
        from .environments import bootstrap_versions
        networking = network.plan(json.loads((package/'dependencies.json').read_text(encoding='utf-8')),
            bootstrap_versions(json.loads((package/'bootstrap_versions.json').read_text(encoding='utf-8'))),
            model_only=True, measure_speed=True, proxy_mode=preferences['proxy_mode'], env=environ,
            progress=lambda _: emit(0, stage='probe'))
    networking['download_settings_path'] = str(root / 'download-settings.json')
    networking['resource_check'] = check
    from .prepared_model import token
    secret = token(environ)
    headers = lambda source: ['Authorization: Bearer '+secret] if secret and source == 'official' else []
    done = 0
    for table, row, path in missing:
        check()
        path.parent.mkdir(parents=True, exist_ok=True)
        remote = table['download']
        spec = dict(repo=remote['repo'], revision=remote['revision'], file=remote['prefix']+'/'+row['file'])
        network.download(network.model_urls(networking, spec, environ), path, row['sha256'],
            lambda current, size, speed, **_: emit(done+current, speed), network=networking,
            size=row['bytes'], category='models', headers_for=headers, keep_partial=True,
            stall_seconds=30, slow_seconds=15, low_speed_limit=64*1024)
        assets.check_table(path, row, table['identity'])
        save(path.with_suffix('.json'), dict(bytes=row['bytes'], sha256=row['sha256']))
        stamps[str(path)] = provision.file_identity(path, row)
        save(ledger, stamps)
        done += row['bytes']
        emit(done)
    return dict(seconds=time.monotonic()-started, downloaded_bytes=done)
