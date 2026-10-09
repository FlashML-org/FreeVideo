"""Explicit, resumable installation through FreeVideo's download routing."""
import json
from pathlib import Path
import shutil
from .. import disk_space


def catalog():
    return json.loads(Path(__file__).with_name('model.json').read_text(encoding='utf-8'))


def directory(root):
    spec = catalog()
    return Path(root) / 'models' / 'prompt-vlm' / spec['revision']


def ready(root):
    spec, folder = catalog(), directory(root)
    try:
        receipt = json.loads((folder / 'verified.json').read_text(encoding='utf-8'))
        return receipt.get('revision') == spec['revision'] and all(
            (folder / row['file']).stat().st_size == row['bytes'] and
            receipt['files'].get(row['file']) == (folder / row['file']).stat().st_mtime_ns
            for row in spec['files'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def install(root, progress, check):
    from .. import network, provision
    from ..adaln_assets import _download_plan
    from ..monitoring import save
    if ready(root):
        return
    folder, spec = directory(root), catalog()
    folder.mkdir(parents=True, exist_ok=True)
    ledger_path = folder / 'download-verified.json'
    try:
        ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        ledger = {}
    missing = [row for row in spec['files'] if not provision.verified(folder / row['file'], row, ledger)]
    total = sum(row['bytes'] for row in missing)
    retained = 0
    for row in missing:
        path = folder / row['file']
        partial = path.with_suffix(path.suffix + '.partial')
        if partial.is_file():
            size = partial.stat().st_size
            if size < row['bytes']:
                retained += size
    if disk_space.free_bytes(folder) < total - retained + 256 * 2**20:
        raise ValueError('disk_space')
    plan = _download_plan(root)
    # This catalog has no verified ModelScope mirror yet. Preserve configured
    # routing, but never guess a mirror revision or send credentials to it.
    plan['sources'] = dict(plan.get('sources', {}), models=[
        row for row in plan.get('sources', {}).get('models', []) if row['id'] in ('official', 'hf-mirror')])
    if not plan['sources']['models']:
        plan['sources']['models'] = [{'id': 'official'}, {'id': 'hf-mirror'}]
    plan.update(quiet=True, resource_check=check)
    done = 0
    for row in missing:
        check()
        path = folder / row['file']
        remote = dict(repo=spec['repo'], revision=spec['revision'], file=row['file'])
        def moved(current, size, speed, **kwargs):
            progress(dict(phase='download', done=done + current, total=total))
        network.download(network.model_urls(plan, remote), path, row['sha256'], moved,
                         network=plan, size=row['bytes'], keep_partial=True, stall_seconds=30)
        ledger[str(path)] = provision.file_identity(path, row)
        save(ledger_path, ledger)
        done += row['bytes']
    save(folder / 'verified.json', dict(revision=spec['revision'], files={
        row['file']: (folder / row['file']).stat().st_mtime_ns for row in spec['files']}))
