"""Pinned model download and bounded cache preparation inside the new environment."""
import argparse
import json
import os
from pathlib import Path
import time
import threading
from concurrent.futures import ThreadPoolExecutor, CancelledError

from .bootstrap import PACKAGE, model_target
from .locking import runtime_lock
from .monitoring import save
from .install_tuning import required_models, hf_receipt, unchanged_before
from . import network
from .storage import record_download, compact_sources, fingerprint
from . import model_transfer


def model_headers(source):
    """Automatic public mirrors never receive the user's Hub token."""
    from huggingface_hub import get_token
    token = get_token() if source in ('official', 'user') else None
    return ['Authorization: Bearer ' + token] if token else []


def file_identity(path, row):
    return dict(fingerprint(path), sha256=row.get('sha256'), git_blob=row.get('git_blob'))


def verified(path, row, stamps, *, mode='auto', hf_directory=None, producer_receipt=None, progress=None):
    if not path.is_file() or path.stat().st_size != row['bytes']:
        return False
    identity = file_identity(path, row)
    prior = stamps.get(str(path), {})
    reliable = 'change_time_ns' not in identity or identity['change_time_ns'] is not None
    if mode == 'auto' and reliable:
        # Recently closed Windows files can share a filesystem clock tick.
        # Hash those again instead of trusting a same-tick identity collision.
        settled = ('change_time_ns' not in identity or
                   max(identity['mtime_ns'], identity['change_time_ns']) < time.time_ns() - 10**9)
        if settled and isinstance(prior, dict) and all(prior.get(k) == v for k, v in identity.items()):
            return True
        method = None
        if not prior and hf_directory and hf_receipt(path, row, hf_directory):
            method = 'pinned_huggingface_download_receipt_unchanged_file'
        if not prior and producer_receipt and unchanged_before(path, producer_receipt):
            method = 'local_conversion_hash_manifest_unchanged_file'
        if method:
            stamps[str(path)] = dict(identity, method=method)
            return True
    kwargs = dict(discard_cache=True)
    if progress is not None:
        kwargs['progress'] = progress
    matches = (network.hash_file(path, **kwargs) == row['sha256'] if row.get('sha256') else
               network.hash_file(path, 'sha1', git_blob=True, **kwargs) == row['git_blob'])
    if matches:
        stamps[str(path)] = dict(identity, method='full_content_hash')
    return matches


def models(plan):
    # Use the same canonical spelling as compact_sources/ownership, including
    # Windows 8.3 temporary-directory aliases. Keep managed_model's link and
    # ownership checks intact; a different spelling must not lose the receipt.
    root = Path(plan['root']).expanduser().resolve()
    ledger = root / 'verified-models.json'
    stamps = json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {}
    model_dir, encoder_dir = (Path(plan[name]).expanduser().resolve() for name in ('model_dir', 'encoder_dir'))
    from . import prepared_model
    prepared = plan.get('prepared_model')
    files = list(required_models(json.loads((PACKAGE / 'model_files.json').read_text(encoding='utf-8')), plan.get('reuse_cache') or prepared))
    files += prepared_model.files(prepared)
    if plan.get('sampling_caches'):
        from .sampling_assets import files as sampling_files
        files += sampling_files()
    mode = plan.get('verification', 'auto')
    from .local_models import key as local_key, import_file
    local_files, missing = [], []
    destinations = set()
    # Cheap metadata only: start known downloads without first hashing tens of
    # gigabytes of unrelated local weights. A local match is never downloaded.
    for row in files:
        path = model_target(row, model_dir, encoder_dir, prepared['directory'] if prepared else None)
        hf_directory = Path(prepared['directory']) if row.get('prepared') else model_dir if row['repo'].startswith('OpenVDN/') else encoder_dir
        if path in destinations:
            raise ValueError('Duplicate model destination in installation plan: ' + str(path))
        destinations.add(path)
        local = (plan.get('local_models') or {}).get('matches', {}).get(local_key(row))
        entry = (row, path, hf_directory, local)
        (local_files if path.exists() or path.is_symlink() or local else missing).append(entry)
    space_saver = plan.get('disk_mode') == 'extreme'
    if space_saver:
        from .install_disk import check_models
        check_models(local_files + missing)
    stopped = threading.Event()
    lock = threading.RLock()
    failures = []
    ready = [0]

    def check():
        if stopped.is_set():
            raise CancelledError('Parallel model preparation stopped; all files retained')
        if space_saver:
            from .install_disk import check_floor
            check_floor((root, model_dir, encoder_dir))

    def emit(event, **details):
        with lock:
            # One write preserves JSON lines when the verifier and downloader
            # both report progress. The only retained buffers are hash chunks.
            import sys
            sys.stdout.write(json.dumps(dict(event=event, **details)) + '\n')
            sys.stdout.flush()

    from .model_status import ModelProgress
    model_progress = ModelProgress([(entry[0], 'found') for entry in local_files] +
                                   [(entry[0], 'download') for entry in missing], emit)
    model_progress.update()

    def complete(row, path, stamp, operation, origin=None):
        with lock:
            stamps[str(path)] = stamp
            if row.get('sampling_file'):
                save(path.with_suffix('.json'), dict(bytes=row['bytes'], sha256=row['sha256']))
            if origin:
                record_download(root, path, row, origin=origin)
            save(ledger, stamps)
            ready[0] += 1
            emit('model_ready', index=ready[0], total=len(files), file=str(path),
                 verification=stamp.get('method'), operation=operation)
        model_progress.update(row, phase='ready', done=row['bytes'], local=operation != 'download')

    def verify_local():
        try:
            for index, (row, path, hf_directory, local) in enumerate(local_files):
                check()
                started, last = time.monotonic(), [0.]
                def progress(done, total):
                    check()
                    now = time.monotonic()
                    if done == 0 or done == total or now - last[0] >= .5:
                        emit('model_verification_progress', file=path.name, done_bytes=done,
                             total_bytes=total, bytes_per_second=done / max(.001, now-started),
                             files_done=index, files_total=len(local_files), state='running')
                        model_progress.update(row, phase='verifying')
                        last[0] = now
                progress(0, row['bytes'])
                prior = {str(path): stamps.get(str(path), {})}
                if verified(path, row, prior, mode=mode, hf_directory=hf_directory, progress=progress):
                    complete(row, path, prior[str(path)], 'verify')
                elif path.exists() or path.is_symlink():
                    raise ValueError('Existing model failed pinned integrity check; preserved without overwrite: ' + str(path))
                elif local:
                    def local_progress(value):
                        check()
                        progress(value.get('done_bytes', 0), value.get('total_bytes', row['bytes']))
                    method = import_file(row, path, local, callback=local_progress)
                    complete(row, path, dict(file_identity(path, row), method='verified_local_' + method),
                             'verify', 'local-copy' if method == 'copy' else None)
                else:
                    # The reviewed existing file disappeared. A changed plan
                    # must not silently enlarge the approved download set.
                    raise FileNotFoundError('Local model disappeared; inspect setup again: ' + str(path))
            emit('model_verification_progress', files_done=len(local_files), files_total=len(local_files), state='complete')
        except BaseException as error:
            failures.append(error)
            stopped.set()
            emit('model_verification_progress', state='failed', files_total=len(local_files))
            raise

    def download_missing():
        networking = dict(plan.get('network', {}))
        parent_check = networking.get('resource_check')
        def resource_check():
            check()
            if parent_check:
                parent_check()
        networking['resource_check'] = resource_check
        download_plan = dict(plan, network=networking)
        requested_workers = (plan.get('model_transfer') or {}).get('file_workers', 1)
        if space_saver:
            requested_workers = 1
        try:
            workers = max(1, min(len(missing), int(requested_workers)))
        except (TypeError, ValueError):
            workers = 1
        def download_one(entry, transfer_ready=None):
            row, path, hf_directory, _ = entry
            check()
            if space_saver:
                check_models(local_files + missing)
            # A user may copy a file in while setup is active. Do not claim
            # ownership of, replace, or download over that newly present file.
            if path.exists() or path.is_symlink():
                prior = {}
                if not verified(path, row, prior, mode=mode, hf_directory=hf_directory):
                    raise ValueError('Model destination appeared with unexpected content: ' + str(path))
                complete(row, path, prior[str(path)], 'verify')
                return
            emit('download_model', file=str(path), bytes=row['bytes'])
            model_progress.update(row, phase='downloading')
            last_progress = [0.]
            def progress(done, total, speed, **extra):
                check()
                if time.monotonic() - last_progress[0] >= 1:
                    emit('download_progress', description=path.name, done_bytes=done,
                         total_bytes=total, bytes_per_second=speed, **extra)
                    model_progress.update(row, phase='verifying' if done == row['bytes'] else 'downloading', done=done, rate=speed)
                    last_progress[0] = time.monotonic()
            def transfer_complete():
                # This event is emitted before the provider performs its
                # final pinned hash check.  Other file workers may already
                # be transferring the next shard, so the UI can distinguish
                # network completion from verified publication.
                emit('download_transfer_complete', file=str(path), bytes=row['bytes'])
                model_progress.update(row, phase='verifying', done=row['bytes'], rate=None)
                if transfer_ready is not None:
                    transfer_ready()
            try:
                if workers > 1:
                    model_transfer.download(row, path, download_plan, progress, model_headers,
                                            transfer_complete=transfer_complete)
                else:
                    # Keep compatibility with small test/tool integrations
                    # that implement the historic five-argument hook.
                    model_transfer.download(row, path, download_plan, progress, model_headers)
            except BaseException:
                model_progress.update(row, phase='paused')
                raise
            # download verified the complete content before renaming. Save the
            # final inode stamp without a second pass through large model files.
            complete(row, path, dict(file_identity(path, row), method='full_content_hash_resumable_download'),
                     'download', 'download')
        # Explicitly preserve the old one-file behavior for plans without the
        # new policy field. When enabled, use a two-slot pipeline: one child
        # can finish its pinned hash while the next child transfers. The
        # transfer-ready callback dispatches the next file only after the
        # current body has ended, preventing multiple large network bodies
        # from being started at once.
        if workers == 1:
            for entry in missing:
                download_one(entry)
            return
        # Do not turn this into an unbounded file pool: each transfer child
        # has its own native range buffers and process guard. Two slots are
        # enough to overlap network I/O with the previous file's final hash.
        condition = threading.Condition()
        entries = iter(missing)
        futures, state = set(), {'active': 0, 'finished': 0, 'error': None}
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='model-download')

        def submit_next():
            with condition:
                if stopped.is_set():
                    return False
                try:
                    entry = next(entries)
                except StopIteration:
                    return False
                state['active'] += 1
                # The callback is invoked before the worker returns, while
                # its final integrity check is still running. It may submit
                # the next file to the second slot.
                future = pool.submit(download_one, entry, submit_next)
                futures.add(future)
                future.add_done_callback(finished)
                return True

        def finished(future):
            error = None
            try:
                if future.cancelled():
                    error = CancelledError()
                else:
                    error = future.exception()
            except BaseException as caught:
                error = caught
            with condition:
                state['active'] -= 1
                state['finished'] += 1
                if error is not None and state['error'] is None:
                    state['error'] = error
                    stopped.set()
                # Files without a transport callback (a copied-in file or a
                # tiny fallback transfer) still advance the pipeline after
                # they finish.
                if error is None and state['active'] == 0 and state['finished'] < len(missing):
                    submit_next()
                condition.notify_all()

        try:
            submit_next()
            with condition:
                while state['finished'] < len(missing) and state['error'] is None:
                    condition.wait(.2)
            if state['error'] is not None:
                raise state['error']
        finally:
            if state['error'] is not None:
                stopped.set()
            for future in futures:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
    # One bounded disk verifier plus a bounded native multi-connection
    # downloader. Same-file SDK concurrency and its live RAM guard remain
    # unchanged; file_workers only overlaps separate files.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='model-verification') as pool:
        if space_saver:
            verify_local()  # Local copies must also finish before downloads start.
        verification = pool.submit(verify_local) if local_files and not space_saver else None
        try:
            download_missing()
            if verification is not None:
                verification.result()
        except BaseException:
            stopped.set()
            model_progress.update(phase='paused')
            if failures:
                raise failures[0]
            raise
    # JSON is valid YAML and safely represents spaces and punctuation in paths.
    save(root / 'encoder-paths.yaml', {'freevideo': {'base_path': str(encoder_dir), 'text_encoders': 'text_encoders/'}})


def verify_cache(plan, cache):
    """Verify every expected group before acknowledging readiness or cleanup."""
    cache = Path(cache).resolve()
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
    config = json.loads((Path(plan['model_dir']) / 'h3-base/transformer/config.json').read_text(encoding='utf-8'))
    count = config['num_layers']
    from .adaln_assets import SLIM_FORMATS, validate_catalog, check_table, asset_path
    slim = manifest.get('format') in SLIM_FORMATS
    kinds = ('blocks',) if slim else ('blocks', 'adaln')
    expected = {'root'} | {kind + '/' + '%02d' % i for kind in kinds for i in range(count)}
    groups = manifest.get('groups', [])
    if (manifest.get('precision') not in ('fp8', 'int8') or not manifest.get('linears')
            or len(groups) != len(expected) or {r.get('group') for r in groups} != expected):
        raise ValueError('Prepared cache is not complete; original model files retained')
    ledger = Path(plan['root']) / 'verified-cache.json'
    stamps = json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {}
    for row in groups:
        if row['file'] != row['group'] + '.safetensors':
            raise ValueError('Unexpected FP8 group path; original files retained')
        if not verified(cache / row['file'], row, stamps, mode=plan.get('verification', 'auto'),
                        producer_receipt=cache / 'manifest.json'):
            raise ValueError('Existing FP8 cache failed integrity check: ' + row['file'] + '. Rerun setup with --rebuild-cache; failed files are retained.')
    for row, identity in validate_catalog(manifest, count):
        path = asset_path(cache, row['file'])
        if not verified(path, row, stamps, mode=plan.get('verification', 'auto')):
            raise ValueError('AdaLN model asset failed integrity check: ' + row['file'])
        check_table(path, row, identity, verify_hash=False)
    save(ledger, stamps)
    return manifest


def prepare(plan, out):
    root = Path(plan['root'])
    prepared = plan.get('prepared_model')
    cache_format = dict(capability=plan.get('inventory', {}).get('hardware', {}).get('capability'),
                        scale_granularity=plan.get('model_scale_granularity'))
    if prepared:
        from .prepared_model import verify_manifest
        verify_manifest(prepared)
        cache = Path(prepared['cache'])
        from .install_tuning import cache_compatible
        if not cache_compatible(cache, **cache_format):
            raise ValueError('Downloaded prepared model differs from this GPU scale format')
    elif plan.get('reuse_cache'):
        cache = Path(plan['reuse_cache'])
        from .install_tuning import cache_compatible
        manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('precision') not in ('fp8', 'int8'):
            raise ValueError('Reuse cache must contain FP8 or int8 storage.')
        if not cache_compatible(cache, **cache_format):
            raise ValueError('FP8 scale format differs from this GPU. Omit --cache to prepare the correct one.')
    else:
        if plan.get('device_backend') == 'mps':
            raise ValueError('Mac setup requires verified prepared weights; select the prepared model source')
        from .paths import add_vdn
        add_vdn()
        import torch
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        from .fp8 import prepare_streamed
        model_dir = Path(plan['model_dir'])
        base, checkpoint = model_dir / 'h3-base', model_dir / 'stage-dmd-step-250'
        with runtime_lock():
            cache = prepare_streamed(base, checkpoint, root / 'prepared')
    verify_cache(plan, cache)
    record = {'cache': str(cache), 'verified_at_epoch': time.time(),
              'model_revision': json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8'))['models']['vdn_revision']}
    if prepared:
        record['prepared_model'] = {k: prepared[k] for k in ('repo', 'revision', 'scale_granularity')}
    save(out, record)
    save(root / 'prepared-cache.json', record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--prepare', action='store_true')
    operation.add_argument('--cleanup', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if (args.prepare or args.cleanup) and args.out is None:
        parser.error('--out is required for preparation and cleanup reports')
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    if args.cleanup:
        record = json.loads((Path(plan['root']) / 'prepared-cache.json').read_text(encoding='utf-8'))
        verify_cache(plan, record['cache'])
        result = compact_sources(plan, json.loads((PACKAGE / 'model_files.json').read_text(encoding='utf-8')), args.out)
        print(json.dumps({'event': 'storage_compacted', **result}), flush=True)
    elif args.prepare:
        prepare(plan, args.out)
    else:
        models(plan)


if __name__ == '__main__':
    main()
