"""Durable transfers around the pinned providers' metadata and range clients."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path

from . import network
from .download_cache import discard_read_cache
from .file_ops import publish

BLOCK = 4 * 2**20


class ResumeUnavailable(RuntimeError):
    pass


def ms_completed(target, total, part_size):
    target = Path(target)
    if target.is_file():
        return min(total, target.stat().st_size)
    merged = target.with_suffix(target.suffix+'.parallel_tmp')
    prefix = merged.stat().st_size if merged.is_file() else 0
    done = min(total, prefix)
    for start in range(0, total, part_size):
        end = min(total, start+part_size)
        part = target.with_name('%s_%d_%d' % (target.name, start, end-1))
        try:
            done += max(0, min(end, start+part.stat().st_size)-max(start, prefix))
        except FileNotFoundError:
            pass
    return min(total, done)


def ms_parallel(module, url, target, file_size, headers, cookies, progress_callbacks=None):
    """Resume both range parts and a merge interrupted after parts were removed.

    The SDK normally opens its merge file with 'wb'. Keep its concurrent Range
    client, but append to our retained prefix and flush before deleting a part.
    Final pinned content verification is still required by the caller.
    """
    callbacks = progress_callbacks or []
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    merged = target.with_suffix(target.suffix + '.parallel_tmp')
    offset = merged.stat().st_size if merged.exists() else 0
    if offset > file_size:
        raise ValueError('Retained merge exceeds the pinned model size')
    if offset % module.DOWNLOAD_PART_SIZE:
        # A switched HTTP download may end in the middle of an SDK part.
        # Seed that part so its first Range starts after the retained prefix.
        # Only the one intersecting part is copied, using bounded buffers.
        start=offset-offset%module.DOWNLOAD_PART_SIZE
        end=min(file_size,start+module.DOWNLOAD_PART_SIZE)
        part=target.with_name('%s_%d_%d' % (target.name,start,end-1))
        existing=part.stat().st_size if part.exists() else 0
        if existing > end-start:
            raise ValueError('Retained part exceeds its range')
        with merged.open('rb') as prefix, part.open('a+b') as output:
            prefix.seek(start); output.seek(0)
            remaining=min(existing,offset-start)
            while remaining:
                count=min(BLOCK,remaining)
                if prefix.read(count)!=output.read(count):
                    raise ValueError('Retained part differs from switched prefix')
                remaining-=count
            if existing<offset-start:
                prefix.seek(start+existing); output.seek(0,2)
                remaining=offset-start-existing
                while remaining:
                    block=prefix.read(min(BLOCK,remaining))
                    if not block:raise ValueError('Retained prefix changed while resuming')
                    output.write(block); remaining-=len(block)
                    discard_read_cache(prefix.fileno(),prefix.tell()-len(block),len(block))
                output.flush(); os.fsync(output.fileno())
    # A crash after fsync but before unlink can leave a duplicate complete
    # part. Compare it with the merged prefix before releasing the duplicate.
    for start in range(0, offset, module.DOWNLOAD_PART_SIZE):
        end = min(file_size, start+module.DOWNLOAD_PART_SIZE)
        part = target.with_name('%s_%d_%d' % (target.name, start, end-1))
        if end <= offset and part.is_file():
            if part.stat().st_size != end-start:
                raise ValueError('Unexpected size of a retained merged part')
            with merged.open('rb') as prefix, part.open('rb') as stream:
                prefix.seek(start)
                for block in iter(lambda: stream.read(BLOCK), b''):
                    if block != prefix.read(len(block)):
                        raise ValueError('Retained part differs from merged prefix')
                    discard_read_cache(prefix.fileno(), prefix.tell()-len(block), len(block))
                    discard_read_cache(stream.fileno(), stream.tell()-len(block), len(block))
            part.unlink()
    tasks = [(str(target), callbacks, start, min(file_size, start+module.DOWNLOAD_PART_SIZE)-1,
              url, target.name, headers, cookies)
             for start in range(0, file_size, module.DOWNLOAD_PART_SIZE)
             if min(file_size, start+module.DOWNLOAD_PART_SIZE) > offset]
    with ThreadPoolExecutor(max_workers=min(module.DOWNLOAD_PARALLELS, 16)) as pool:
        list(pool.map(module._download_part_with_retry, tasks))
    for callback in callbacks:
        callback.end()
    digest = hashlib.sha256()
    if offset:
        with merged.open('rb') as prefix:
            for block in iter(lambda: prefix.read(BLOCK), b''):
                digest.update(block)
                discard_read_cache(prefix.fileno(), prefix.tell()-len(block), len(block))
    with merged.open('ab') as output:
        for task in tasks:
            start, end = task[2:4]
            part = Path('%s_%d_%d' % (target, start, end))
            if part.stat().st_size != end-start+1:
                raise ValueError('Incomplete retained model range')
            with part.open('rb') as stream:
                stream.seek(max(0, offset-start))
                for block in iter(lambda: stream.read(BLOCK), b''):
                    output.write(block)
                    digest.update(block)
                    discard_read_cache(stream.fileno(), stream.tell()-len(block), len(block))
            output.flush()
            os.fsync(output.fileno())
            offset = end+1
            # The durable merged prefix owns these bytes now. A crash before
            # unlink leaves overlap, which the next merge skips without replay.
            part.unlink()
    if merged.stat().st_size != file_size:
        raise ValueError('Merged model has an unexpected size')
    publish(merged, target)
    return digest.hexdigest()


def hf_download(request, module, progress, *, incomplete_path, destination_path, url_to_download,
                headers, expected_size, filename, force_download, etag, xet_file_data, tqdm_class=None,
                backend_callback=None):
    """Keep HTTP prefixes; never pretend an Xet output is a resumable prefix.

    Hub 1.30 creates a fresh UUID temporary file and unlinks it on exceptions.
    FreeVideo owns the installation lease, so it can retain a stable file here.
    Metadata/authentication and the actual Xet engine remain provider-owned.
    """
    row, stage = request['row'], Path(request['stage'])
    destination = Path(destination_path)
    if destination.is_file() and not force_download:
        return
    if expected_size is not None and expected_size != row['bytes']:
        raise ValueError('Hub metadata size differs from the pinned model')
    for old in stage.rglob('*.incomplete'):
        if old.name != 'model.xet.incomplete' and old.is_file() and old.stat().st_size:
            if not request.get('allow_model_restart'):
                raise ResumeUnavailable('Older SDK partial has no reliable resume metadata; explicit restart required')
            network.retain_partial(old, 'restart-approved')
    http_partial = destination.with_suffix(destination.suffix + '.partial')
    continuing_http = http_partial.is_file() and http_partial.stat().st_size > 0
    if xet_file_data is not None and module.is_xet_available() and not continuing_http:
        temporary = stage / 'model.xet.incomplete'
        if temporary.exists() and temporary.stat().st_size:
            complete = temporary.stat().st_size == row['bytes'] and network.hash_file(temporary,
                'sha256' if row.get('sha256') else 'sha1', not row.get('sha256'), discard_cache=True) == (row.get('sha256') or row['git_blob'])
            if complete:
                publish(temporary, destination)
                return
            if not request.get('allow_model_restart'):
                raise ResumeUnavailable('Xet cannot resume this retained output; an explicit restart is required')
            network.retain_partial(temporary, 'restart-approved')
        module.xet_get(incomplete_path=temporary, xet_file_data=xet_file_data, headers=headers,
                       expected_size=row['bytes'], displayed_filename=filename, tqdm_class=tqdm_class)
        publish(temporary, destination)
    else:
        if request.get('require_xet'):
            raise ResumeUnavailable('HF selected HTTP but --model-downloader xet requires Xet; no fallback performed')
        if backend_callback:
            backend_callback('hf-http')
        # Let curl resume only a real contiguous HTTP prefix. It validates the
        # pinned digest and never appends an ignored Range response.
        network.download([(request['source'], url_to_download)], destination, row.get('sha256') or row['git_blob'],
            lambda done, total, rate: progress(done, total, rate), size=row['bytes'],
            # The parent tries routes in separate supervised workers. Keep the
            # HTTP fallback on this worker's selected route as well.
            network={'allow_model_restart': request.get('allow_model_restart', False),
                     'proxy_mode': 'direct' if request.get('proxy_route') == 'direct' else 'proxy'},
            headers_for=lambda _: [key + ': ' + value for key, value in headers.items()],
            algorithm='sha256' if row.get('sha256') else 'sha1', git_blob=not row.get('sha256'),
            category=network.model_family(row))
