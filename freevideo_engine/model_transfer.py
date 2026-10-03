"""Provider-native model downloads with bounded resources and retained failures.

SDKs run in disposable workers so a stalled transfer can be stopped on Linux and
Windows. Small files and unsupported routes retain the verified curl path.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from . import network, processes
from .monitoring import save
from .system import system_memory
from .storage import fingerprint

GiB = 1 << 30
MiB = 1 << 20
MIN_SDK_BYTES = 64 * MiB
STALL_SECONDS = 240
SAME_SOURCE_ATTEMPTS = 3


def stage_directory(source, row, path):
    base = Path(path)
    for _ in Path(row['file']).parts:
        base = base.parent
    return base / '.freevideo-downloads' / ((row.get('sha256') or row['git_blob'])[:16] + '-' + source)


def retained_payload(stage, row):
    """Ignore metadata and quarantined attempts; count only active model files."""
    total = 0
    for path in stage.rglob('*') if stage.is_dir() else ():
        if any(marker in str(path.relative_to(stage)) for marker in
               ('.rejected-', '.hash-rejected-', '.interrupted-merge-', '.restart-approved-')):
            continue
        if (path == stage / row['file'] or path.name.endswith(('.incomplete', '.partial', '.parallel_tmp'))
                or re.fullmatch(re.escape(Path(row['file']).name) + r'_\d+_\d+', path.name)):
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def policy(ram_budget, build_peak=0):
    """Reserve process overhead in addition to Xet's native buffer limit."""
    guard = min(2 * GiB, max(GiB, int(ram_budget) // 8))
    buffer_limit = min(GiB, max(256 * MiB, guard - GiB))
    streams = 8 if buffer_limit < GiB else 16
    # A single file already uses range/concurrent streams.  Whole-file
    # concurrency is therefore deliberately more conservative: account for
    # the compiler that may run beside model setup and leave the default at
    # one worker on small machines.  ``provision.models`` uses this as a
    # bounded scheduler limit; it never changes the per-file stream count.
    overlap = build_peak + guard + GiB <= ram_budget
    free_for_files = max(0, int(ram_budget) - (int(build_peak) if overlap else 0))
    # The second slot is the verifier; a third transfer would only compete
    # for the same network and native range buffers.
    file_workers = 1 if free_for_files < 16 * GiB else 2
    return dict(ram_guard_bytes=guard, buffer_limit_bytes=buffer_limit,
                buffer_bytes=buffer_limit // 2, per_file_buffer_bytes=min(256 * MiB, buffer_limit // 2),
                prefetch_bytes=min(256 * MiB, buffer_limit // 2), max_streams=streams,
                modelscope_streams=4, part_bytes=64 * MiB, file_cache_window_bytes=256 * MiB,
                overlap_build=overlap, file_workers=file_workers,
                mode='native-adaptive-bounded', high_performance=False,
                scope='Native buffer limits plus sampled process guard; not a kernel RAM cap')


def worker_environment(source, directory, selected, environ=None):
    env = network.proxy_environment(environ)
    # These settings are owned by setup and read by SDKs at import time.
    for key in list(env):
        if key.startswith('HF_XET_') or key in ('HF_HUB_ENABLE_HF_TRANSFER', 'HF_HUB_OFFLINE', 'HF_HUB_DISABLE_XET'):
            env.pop(key)
    if source not in ('official', 'user'):
        for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HF_TOKEN_PATH'):
            env.pop(key, None)
    env.update(HF_HUB_DISABLE_IMPLICIT_TOKEN='1', HF_HUB_DISABLE_TELEMETRY='1',
               HF_HUB_DISABLE_UPDATE_CHECK='1', HF_HUB_DISABLE_PROGRESS_BARS='0',
               HF_HUB_DOWNLOAD_TIMEOUT='60', HF_HUB_ETAG_TIMEOUT='15',
               HF_XET_HIGH_PERFORMANCE='0', HF_XET_CHUNK_CACHE_SIZE_BYTES='0',
               HF_XET_CACHE=str(directory / 'xet-cache'),
               HF_XET_RECONSTRUCTION_DOWNLOAD_BUFFER_LIMIT=str(selected['buffer_limit_bytes']),
               HF_XET_RECONSTRUCTION_DOWNLOAD_BUFFER_SIZE=str(selected['buffer_bytes']),
               HF_XET_RECONSTRUCTION_DOWNLOAD_BUFFER_PERFILE_SIZE=str(selected['per_file_buffer_bytes']),
               HF_XET_RECONSTRUCTION_MIN_PREFETCH_BUFFER=str(selected['prefetch_bytes']),
               HF_XET_RECONSTRUCTION_MIN_RECONSTRUCTION_FETCH_SIZE=str(64 * MiB),
               HF_XET_RECONSTRUCTION_MAX_RECONSTRUCTION_FETCH_SIZE=str(selected['buffer_bytes']),
               HF_XET_CLIENT_ENABLE_ADAPTIVE_CONCURRENCY='true',
               HF_XET_CLIENT_AC_MAX_DOWNLOAD_CONCURRENCY=str(selected['max_streams']),
               HF_XET_CLIENT_CONNECT_TIMEOUT='10s', HF_XET_CLIENT_READ_TIMEOUT='60s',
               HF_XET_CLIENT_RETRY_MAX_ATTEMPTS='3', HF_XET_CLIENT_RETRY_MAX_DURATION='180s',
               MODELSCOPE_HOME=str(directory / 'ms-home'), MODELSCOPE_CACHE=str(directory / 'ms-cache'),
               MODELSCOPE_DOWNLOAD_PARALLEL_WORKERS=str(selected['modelscope_streams']),
               MODELSCOPE_DOWNLOAD_PARALLEL_THRESHOLD_MB='32', MODELSCOPE_DOWNLOAD_PART_SIZE_MB='64',
               MODELSCOPE_DOWNLOAD_MAX_RETRIES='2', MODELSCOPE_DOWNLOAD_TIMEOUT='60',
               MODELSCOPE_API_TIMEOUT='20', MODELSCOPE_API_CONNECT_TIMEOUT='5', MODELSCOPE_API_MAX_RETRIES='1',
               MODELSCOPE_DOWNLOAD_INTRA_CLOUD='false', MODELSCOPE_LOG_LEVEL='ERROR')
    env.pop('MODELSCOPE_API_TOKEN', None)  # This integration downloads public VDN only.
    return env


def emit(**value):
    sys.stdout.write(json.dumps(dict(event='model_transfer', **value)) + '\n')
    sys.stdout.flush()


class Progress:
    def __init__(self, total):
        self.total = total
        self.done = self.transferred = 0
        self.last = 0.
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.completed = None

    def update(self, amount, *, transfer=False):
        with self.lock:
            if transfer:
                self.transferred += amount
            else:
                self.done += amount
            now = time.monotonic()
            if now - self.last >= .5:
                if self.completed is not None:
                    self.done = min(self.total, self.completed())
                emit(phase='download', done_bytes=self.done, total_bytes=self.total,
                     transfer_bytes=self.transferred, elapsed_seconds=now-self.started)
                self.last = now


def verify(path, row):
    if path.stat().st_size != row['bytes']:
        raise ValueError('Downloaded size differs from the pinned manifest')
    return network.hash_file(path, 'sha256' if row.get('sha256') else 'sha1', not row.get('sha256'), discard_cache=True) == (row.get('sha256') or row['git_blob'])


def checked_range_response(response, requested, total, progress):
    """Validate SDK ranges before writing, including MS's 200 + Content-Range.

    The pinned SDK resumes part files but does not validate their ranges. Keep
    its streaming/retry implementation while rejecting ignored or wrong ranges.
    """
    match = re.fullmatch(r'bytes=(\d+)-(\d+)', requested)
    if not match:
        response.close()
        raise ValueError('Invalid model range request')
    start, end = map(int, match.groups())
    expected = end - start + 1
    content_range = response.headers.get('Content-Range', '')
    length = response.headers.get('Content-Length')
    if (response.status_code not in (200, 206) or
            content_range != 'bytes %d-%d/%d' % (start, end, total) or
            (length is not None and length != str(expected))):
        response.close()
        raise ValueError('Model source did not honor the requested byte range')
    original = response.iter_content

    def chunks(*args, **kwargs):
        received = 0
        try:
            for chunk in original(*args, **kwargs):
                received += len(chunk)
                if received > expected:
                    raise ValueError('Model range exceeded its expected size')
                progress.update(len(chunk), transfer=True)
                yield chunk
            if received != expected:
                raise OSError('Model range ended before its expected size')
        finally:
            response.close()

    response.iter_content = chunks
    return response


def sdk_worker(request):
    row, source = request['row'], request['source']
    stage = Path(request['stage'])
    target = stage / row['file']
    progress = Progress(row['bytes'])
    backend = []
    started = time.monotonic()
    try:
        if target.is_file():
            emit(phase='verify')
            if verify(target, row):
                emit(phase='ready', backend='verified-stage', seconds=time.monotonic()-started, fingerprint=fingerprint(target))
                return 0
            network.retain_partial(target, 'hash-rejected')
        emit(phase='connect')
        if source == 'modelscope' or request.get('manual_ranges'):
            from modelscope_hub import HubApi
            from modelscope_hub import _download
            from modelscope_hub.constants import DOWNLOAD_PARALLELS, DOWNLOAD_PART_SIZE
            mirror = network.modelscope_mapping(row)
            if not mirror and not request.get('manual_ranges'):
                raise ValueError('No verified ModelScope mapping')
            # SDK retry callbacks include already retained bytes again. Derive
            # completion from the part files, and count wire bytes separately.
            from .provider_resume import ms_completed
            progress.completed = lambda: ms_completed(target, row['bytes'], DOWNLOAD_PART_SIZE)

            class Callback:
                def __init__(self, filename, total):
                    pass
                def update(self, amount):
                    progress.update(0)
                def end(self):
                    emit(phase='reconstruct', done_bytes=row['bytes'], total_bytes=row['bytes'])

            original_get, original_parallel = _download.requests.get, getattr(_download, '_parallel_download', None)

            def get(*args, **kwargs):
                response = original_get(*args, **kwargs)
                requested = (kwargs.get('headers') or {}).get('Range')
                return checked_range_response(response, requested, row['bytes'], progress) if requested else response

            method='http-ranges' if request.get('manual_ranges') else 'modelscope-ranges'
            emit(phase='backend', backend=method, streams=DOWNLOAD_PARALLELS, part_bytes=DOWNLOAD_PART_SIZE)
            backend.append(method)
            # FreeVideo owns final integrity handling. Passing no SDK hash
            # avoids its delete-and-retry behavior for a rejected download.
            _download.requests.get = get
            from .provider_resume import ms_parallel
            _download._parallel_download = lambda *a, **k: ms_parallel(_download, *a, **k)
            try:
                if request.get('manual_ranges'):
                    headers={}
                    if source in ('official','user'):
                        from huggingface_hub import get_token
                        token=get_token()
                        if token:headers['Authorization']='Bearer '+token
                    ms_parallel(_download,network.model_url(row,source),target,row['bytes'],headers,None,
                                [Callback(row['file'],row['bytes'])])
                else:
                    HubApi(endpoint=network.source_url(mirror['family'], source), token='').downloader.download_file(
                        mirror['repo'], 'model', row['file'], revision=mirror['revision'], local_dir=stage,
                        file_size=row['bytes'], expected_sha256=None, progress_callbacks=[Callback])
            finally:
                _download.requests.get = original_get
                if original_parallel is not None:
                    _download._parallel_download = original_parallel
        else:
            from huggingface_hub import hf_hub_download, get_token, file_download
            from hf_xet import XetConfig
            config = dict(XetConfig().items())
            requested = request['policy']
            if config['reconstruction.download_buffer_limit'] > requested['buffer_limit_bytes']:
                raise RuntimeError('Xet did not apply its requested buffer limit')
            emit(phase='configuration', buffer_limit_bytes=config['reconstruction.download_buffer_limit'],
                 max_streams=config['client.ac_max_download_concurrency'])
            originals = (file_download.xet_get, file_download.http_get, getattr(file_download, '_download_to_tmp_and_move', None))

            def xet(*args, **kwargs):
                backend.append('hf-xet')
                emit(phase='backend', backend='hf-xet')
                return originals[0](*args, **kwargs)

            def http(*args, **kwargs):
                if request.get('require_xet'):
                    raise RuntimeError('HF selected HTTP but --model-downloader xet requires Xet; no fallback performed')
                backend.append('hf-http')
                emit(phase='backend', backend='hf-http')
                return originals[1](*args, **kwargs)

            class Bar:
                def __init__(self, *args, total=None, initial=0, **kwargs):
                    self.total, self.n = total, initial
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def update(self, amount=1):
                    self.n += amount or 0
                    progress.update(amount or 0)
                def update_transfer(self, amount):
                    progress.update(amount, transfer=True)
                def set_postfix_str(self, *args, **kwargs):
                    pass
                def set_transfer_postfix_str(self, *args, **kwargs):
                    pass
                def refresh(self, *args, **kwargs):
                    pass
                def close(self):
                    pass

            file_download.xet_get, file_download.http_get = xet, http
            from .provider_resume import hf_download
            def durable_download(*args, **kwargs):
                def actual(name):
                    backend.append(name)
                    emit(phase='backend', backend=name)
                def report(done, total, rate):
                    emit(phase='download', done_bytes=done, total_bytes=total,
                         bytes_per_second=rate, transfer_bytes=0, elapsed_seconds=time.monotonic()-started)
                return hf_download(request, file_download, report, *args, backend_callback=actual, **kwargs)
            file_download._download_to_tmp_and_move = durable_download
            token = (get_token() or False) if source in ('official', 'user') else False
            try:
                hf_hub_download(row['repo'], row['file'], revision=row['revision'], token=token,
                                endpoint=network.source_url('models', source), local_dir=stage,
                                cache_dir=stage / 'hub-cache', tqdm_class=Bar)
            finally:
                file_download.xet_get, file_download.http_get = originals[:2]
                if originals[2] is not None:
                    file_download._download_to_tmp_and_move = originals[2]
        # The provider has returned and closed its staged output.  This is a
        # safe hand-off point for the installer to start the next file while
        # this worker performs the pinned digest check below.  Do not emit
        # this before the provider call returns: progress reaching the byte
        # count alone does not prove that the file descriptor is closed.
        emit(phase='transfer_complete', done_bytes=row['bytes'], total_bytes=row['bytes'])
        downloaded = time.monotonic()
        emit(phase='verify')
        if not verify(target, row):
            raise ValueError('Downloaded hash differs from the pinned manifest')
        emit(phase='ready', backend=backend[-1] if backend else 'hf-cache',
             download_seconds=downloaded-started, verification_seconds=time.monotonic()-downloaded,
             done_bytes=row['bytes'], total_bytes=row['bytes'], transfer_bytes=progress.transferred,
             fingerprint=fingerprint(target))
        return 0
    except Exception as error:
        # Signed CDN URLs and authentication details can appear in SDK errors.
        if target.is_file():
            network.retain_partial(target, 'rejected')
        import traceback
        traceback.print_exc()
        emit(phase='failed', error_type=type(error).__name__, message=safe_log(str(error)))
        return 1


def safe_log(line):
    from .diagnostics import Redactor
    # SDK exceptions sometimes include signed CDN queries or proxy userinfo.
    line = re.sub(r'https?://[^\s\"<>]+', '<download URL>', line)
    return Redactor().text(line)


def stage_overflow(directory, row, part_bytes):
    """Check each active payload against its own range, not a moving disk sum.

    During merge a part and its durable prefix temporarily own the same bytes.
    Walking the directory is not an atomic snapshot: it can see every part
    before unlink and the completed prefix afterwards. Summing those sizes can
    double count an entire valid model. Metadata and retained rejected attempts
    are not new transfer payload either. Final size AND digest checks still
    authorize publication; this check only stops an overlong active writer.
    """
    target = directory / row['file']
    prefixes = (target, target.with_suffix(target.suffix + '.partial'),
                target.with_suffix(target.suffix + '.parallel_tmp'), directory / 'model.xet.incomplete')
    ignored = ('.rejected-', '.hash-rejected-', '.interrupted-merge-',
               '.restart-approved-', '.switch-retained-', '.oversized-', '.oversized-response-')
    for root, directories, files in os.walk(directory):
        directories[:] = [name for name in directories if name not in ('xet-cache', 'ms-cache', 'ms-home', 'hub-cache')
                          and not any(marker in name for marker in ignored)]
        for name in files:
            if any(marker in name for marker in ignored):
                continue
            path = Path(root) / name
            limit = row['bytes'] if path in prefixes or name.endswith('.incomplete') else None
            if path.parent == target.parent:
                match = re.fullmatch(re.escape(target.name) + r'_(\d+)_(\d+)', name)
                if match:
                    start, end = map(int, match.groups())
                    limit = (end - start + 1 if start % part_bytes == 0 and
                             0 <= start <= end < row['bytes'] and
                             end == min(row['bytes'], start + part_bytes) - 1 else -1)
            if limit is None:
                continue
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue  # Merged/published since the directory was enumerated.
            if size > limit:
                return dict(file=str(path.relative_to(directory)), observed_bytes=size,
                            allowed_bytes=max(0, limit), expected_model_bytes=row['bytes'],
                            reason='invalid-range' if limit < 0 else 'file-exceeds-range')
    return None


class TransferSizeError(RuntimeError):
    def __init__(self, details):
        self.details = details
        super().__init__('unexpected-transfer-size')


def run_sdk(source, row, path, plan, progress_callback, networking, transfer_complete=None):
    from .ram import ProcessMemory
    from .hardware import cgroup_capacity
    resources = plan.get('installation_resources') or plan.get('policy_estimate') or {}
    selected = dict(plan.get('model_transfer') or policy(resources.get('ram_budget_bytes', 16*GiB)))
    # The compiler and desktop can consume RAM after preflight. Recheck here.
    available = system_memory()['available_bytes']
    _, group_available = cgroup_capacity()
    if group_available is not None:
        available = min(available, group_available)
    if available < 3 * GiB:
        raise RuntimeError('low-live-memory-use-streaming')
    if available < selected['ram_guard_bytes'] + 2 * GiB:
        selected = policy(max(GiB, available - 2 * GiB))
    stage = stage_directory('manual', row, path) if plan.get('manual_ranges') else stage_directory(source, row, path)
    stage.mkdir(parents=True, exist_ok=True)
    if plan.get('manual_ranges'):
        save(stage/'manual-source.json',dict(source=source,expected=row.get('sha256') or row['git_blob']))
    events_path = networking.get('events_path') or os.environ.get('FREEVIDEO_NETWORK_EVENTS')
    reports = Path(events_path).parent if events_path else Path(plan['root']) / 'transfer-reports'
    report = reports / 'model-transfers' / (str(time.time_ns()) + '-' + source)
    report.mkdir(parents=True)
    request = dict(row=row, source=source, stage=str(stage), policy=selected,
                   proxy_route=networking.get('active_route', network.route_order(networking, network.model_family(row), source)[0]),
                   require_xet=plan.get('model_downloader') == 'xet',
                   allow_model_restart=plan.get('allow_model_restart', False))
    if plan.get('manual_ranges'):
        request['manual_ranges']=True
    save(report / 'request.json', request)
    state = dict(phase='connect', last_activity=time.monotonic(), done_bytes=0, transfer_bytes=0)
    state_lock = threading.Lock()
    memory = ProcessMemory()
    process = None
    reader = None
    failure = None
    size_error = None
    transfer_signalled = False

    def read_output():
        try:
            with (report / 'worker.log').open('w', encoding='utf-8') as log:
                for line in iter(process.stdout.readline, ''):
                    log.write(safe_log(line))
                    log.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get('event') == 'network':
                        network.event(networking, **{k: v for k, v in event.items() if k not in ('event', 'epoch')})
                        if event.get('action') == 'verifying':
                            with state_lock:
                                state.update(phase='verify', last_activity=time.monotonic())
                        continue
                    if not isinstance(event, dict) or event.get('event') != 'model_transfer':
                        continue
                    with state_lock:
                        phase = event.get('phase')
                        if (event.get('done_bytes', 0) > state['done_bytes'] or
                                event.get('transfer_bytes', 0) > state['transfer_bytes'] or phase != state['phase']):
                            state['last_activity'] = time.monotonic()
                        state.update(event)
        except Exception as error:
            with state_lock:
                state['reader_error'] = type(error).__name__

    started = time.monotonic()
    try:
        process = processes.popen([sys.executable, '-m', 'freevideo_engine.model_transfer', '--worker', str(report / 'request.json')],
            env=worker_environment(source, stage, selected,
                network.route_environment(networking, network.model_family(row), source, route=request['proxy_route'])),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', start_new_session=True, pass_fds=processes.descriptors(), supervise=True)
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        last_emit = 0.
        prior_phase = None
        prior_backend = None
        prior_io = 0
        while process.poll() is None:
            if networking.get('resource_check'):
                networking['resource_check']()
            sample = memory.sample(process.pid)
            guard = sample['guard_bytes']
            if guard is None or guard > selected['ram_guard_bytes'] or sample.get('effective_available_bytes', sample['system_available_bytes']) < 2*GiB:
                failure = 'memory-guard'
                break
            with state_lock:
                disk_io = sum(memory.result().get(key, 0) or 0 for key in
                              ('process_tree_disk_write_bytes', 'process_tree_disk_read_bytes'))
                if disk_io > prior_io and state['phase'] in ('verify', 'reconstruct'):
                    state['last_activity'] = time.monotonic()
                prior_io = disk_io
                current = dict(state)
            if current.get('reader_error'):
                failure = 'progress-reader'
                break
            # Xet reconstruction can lag network transfer; either counter counts
            # as activity. Verification gets a size-aware disk allowance.
            timeout = max(STALL_SECONDS, row['bytes'] / (8*MiB)) if current['phase'] in ('verify', 'reconstruct') else STALL_SECONDS
            if time.monotonic() - current['last_activity'] > timeout:
                failure = 'stalled-transfer'
                break
            size_error = stage_overflow(stage, row, selected['part_bytes'])
            if size_error:
                failure = 'unexpected-transfer-size'
                network.event(networking, category=network.model_family(row), source=source,
                              action='invalid-transfer-size', **size_error)
                break
            if time.monotonic() - last_emit >= .5:
                if current.get('backend') and current['backend'] != prior_backend:
                    network.event(networking, category=network.model_family(row), source=source,
                                  file=path.name, action='backend', method=current['backend'])
                    prior_backend = current['backend']
                if current['phase'] == 'verify' and prior_phase != 'verify':
                    sys.stdout.write(json.dumps(dict(event='verify_model', file=str(path), bytes=row['bytes'])) + '\n')
                    sys.stdout.flush()
                elif current['phase'] == 'reconstruct' and prior_phase != 'reconstruct':
                    sys.stdout.write(json.dumps(dict(event='assemble_model', file=str(path), bytes=row['bytes'])) + '\n')
                    sys.stdout.flush()
                elif current['phase'] == 'download':
                    elapsed = max(.001, current.get('elapsed_seconds', time.monotonic()-started))
                    progress_callback(min(row['bytes'], current['done_bytes']), row['bytes'], current.get('bytes_per_second', current['done_bytes']/elapsed),
                        network_bytes_per_second=current['transfer_bytes']/elapsed if current['transfer_bytes'] else None,
                        method=current.get('backend'))
                elif current['phase'] == 'transfer_complete':
                    progress_callback(row['bytes'], row['bytes'], 0,
                        network_bytes_per_second=current.get('transfer_bytes', 0) /
                        max(.001, current.get('elapsed_seconds', time.monotonic()-started)),
                        method=current.get('backend'))
                prior_phase = current['phase']
                last_emit = time.monotonic()
            # The SDK reports the transfer complete before it starts its final
            # integrity check.  Signal outside the throttled UI emission
            # branch so a short file that exits quickly cannot lose the
            # pipeline hand-off.  Keep this callback one-shot: retries and
            # duplicate progress lines must not create duplicate workers.
            if (transfer_complete is not None and not transfer_signalled and
                    current.get('phase') == 'transfer_complete' and
                    current.get('done_bytes', 0) >= row['bytes']):
                transfer_signalled = True
                transfer_complete()
            try:
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                pass
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        if process is not None and process.poll() is None:
            processes.stop(process, grace=6)
        if reader is not None:
            reader.join(timeout=3)
            if reader.is_alive():
                failure = failure or 'progress-reader-did-not-exit'
        if process is not None:
            process.stdout.close()
        with state_lock:
            final = dict(state)
        final.pop('last_activity', None)
        receipt = dict(source=source, proxy_route=request['proxy_route'], file=row['file'], policy=selected, state=final, promoted=False,
             failure=failure, returncode=process.returncode if process else None,
             seconds=time.monotonic()-started, memory=memory.result())
        if size_error:
            receipt['size_error'] = size_error
        save(report / 'result.json', receipt)
    try:
        if size_error:
            raise TransferSizeError(size_error)
        if failure or process.returncode or final.get('phase') != 'ready':
            raise RuntimeError(failure or final.get('error_type') or 'incomplete-sdk-transfer')
        staged = stage / row['file']
        if fingerprint(staged) != final.get('fingerprint'):
            raise RuntimeError('verified-stage-changed-before-promotion')
        if path.exists():
            raise ValueError('Download destination appeared while downloading; both files retained')
        path.parent.mkdir(parents=True, exist_ok=True)
        from .file_ops import publish
        publish(staged, path)  # Same filesystem: no second full-file copy.
    except Exception as error:
        receipt['failure'] = safe_log(str(error))
        save(report / 'result.json', receipt)
        raise
    receipt['promoted'] = True
    save(report / 'result.json', receipt)
    return final


def download(row, path, plan, progress_callback, headers_for, *, transfer_complete=None):
    from .download_cache import DownloadCache, mount_type, transfer_paths
    if row['bytes'] >= max(MIN_SDK_BYTES, 64 * MiB) and mount_type(path) in ('tmpfs', 'ramfs'):
        raise ValueError('Model directory is on a RAM-backed filesystem. Choose a disk directory with --models /path/on/disk (and --encoder-models for the encoder). Existing files retained.')
    selected = plan.get('model_transfer') or policy(16 * GiB)
    # A transport-complete notification is intentionally separate from the
    # function's return: the latter means the pinned hash has been checked.
    # Keep the notification once per file even when a source/route retry is
    # needed after the first body finished.
    if transfer_complete is not None:
        callback_lock = threading.Lock()
        callback_state = [False]
        original_transfer_complete = transfer_complete
        def transfer_complete():
            with callback_lock:
                if callback_state[0]:
                    return
                callback_state[0] = True
            original_transfer_complete()
    cache = DownloadCache(lambda: transfer_paths(path, row), selected.get('file_cache_window_bytes', 256 * MiB))
    parent_check = (plan.get('network') or {}).get('resource_check')
    from .download_settings import current, SwitchRequested
    active=current(plan.get('network') or {})
    def resource_check():
        selected=current(plan.get('network') or {})
        if selected.get('switch_id') != active.get('switch_id'):
            raise SwitchRequested(selected)
        if parent_check:
            parent_check()
        cache.check()
    networking = dict(plan.get('network', {}), resource_check=resource_check)
    retained_manual=retained_payload(stage_directory('manual',row,path),row)
    if active.get('source') not in (None,'auto') or retained_manual:
        candidates=network.model_urls(networking,row)
        # On a rerun, honor the user's last explicit switch rather than the
        # normal affinity ordering intended for automatic recovery.
        selected_source=active['source']
        if selected_source=='auto' and retained_manual:
            receipt=stage_directory('manual',row,path)/'manual-source.json'
            previous=json.loads(receipt.read_text()) if receipt.is_file() else {}
            selected_source=previous.get('source') if previous.get('expected')==(row.get('sha256') or row['git_blob']) else candidates[0][0]
        if selected_source in [name for name,_ in candidates]:
            from .transfer_handoff import migrate
            retained=migrate(row,path,candidates,allow_restart=plan.get('allow_model_restart',False))
            if path.is_file() and retained['bytes']==row['bytes']:
                return
            networking.update(manual_source_switch=True,manual_source=selected_source)
            plan=dict(plan,manual_ranges=retained['ranges'],model_downloader='auto')
    try:
        with cache:
            while True:
                try:
                    kwargs = ({'transfer_complete': transfer_complete}
                              if transfer_complete is not None else {})
                    _download(row, path, dict(plan, network=networking), progress_callback, headers_for, **kwargs)
                    break
                except SwitchRequested as switch:
                    active=switch.value
                    candidates=network.model_urls(networking,row)
                    selected=active['source']
                    if selected=='auto':selected=candidates[0][0]
                    if selected not in [name for name,_ in candidates]:
                        raise RuntimeError('The selected source has no compatible file for '+row['file']+'. Current download stopped; all fragments retained.')
                    networking.update(manual_source_switch=True,manual_source=selected)
                    # run_sdk/curl have joined their workers before this point.
                    # Reuse known byte ranges, including disjoint MS parts.
                    from .transfer_handoff import migrate
                    # An explicit switch applies to this file even when the
                    # old provider has no portable resume map. Preserve those
                    # fragments on disk and report the restart separately.
                    retained=migrate(row,path,candidates,allow_restart=True)
                    plan=dict(plan,manual_ranges=retained['ranges'],model_downloader='auto',allow_model_restart=True)
                    network.event(networking,category=network.model_family(row),source=selected,
                        file=path.name,action='manual-switch',resume_bytes=retained['bytes'],
                        retained_unmapped_bytes=retained.get('unmapped_bytes',0),
                        reason='User selected a new source; stopped the previous worker')
                    if path.is_file() and retained['bytes']==row['bytes']:
                        break
    finally:
        network.event(networking, category=network.model_family(row), source='local', file=path.name,
                      action='disk-cache', **cache.result())


def _download(row, path, plan, progress_callback, headers_for, *, transfer_complete=None):
    networking = dict(plan.get('network', {}), allow_model_restart=plan.get('allow_model_restart', False))
    candidates = network.model_urls(networking, row)
    family = network.model_family(row)
    require_xet = plan.get('model_downloader') == 'xet' and row['bytes'] >= MIN_SDK_BYTES
    if require_xet:
        candidates = network.model_urls(dict(networking, mode='official'), row)
    # A new speed probe must not strand yesterday's partially downloaded file.
    if networking.get('manual_source_switch'):
        candidates.sort(key=lambda item: item[0] != networking['manual_source'])
    else:
        candidates.sort(key=lambda item: -retained_payload(stage_directory(item[0], row, path), row))
    # Existing contiguous curl partials can be resumed without discarding bytes.
    partial = path.with_suffix(path.suffix + '.partial')
    if plan.get('manual_ranges') or (row['bytes'] >= MIN_SDK_BYTES and (require_xet or not partial.exists())):
        for source_index, (name, _) in enumerate(candidates):
            resource_failure = False
            stage = stage_directory('manual', row, path) if plan.get('manual_ranges') else stage_directory(name, row, path)
            routes = network.route_order(networking, family, name)
            route_index = 0
            for attempt in range(SAME_SOURCE_ATTEMPTS):
                route = routes[route_index]
                network.event(networking, category=family, source=name, file=path.name, action='attempt',
                              route=route, method='native-sdk', resume_bytes=retained_payload(stage, row))
                try:
                    kwargs = ({'transfer_complete': transfer_complete}
                              if transfer_complete is not None else {})
                    final = run_sdk(name, row, path, plan, progress_callback,
                                    dict(networking, active_route=route), **kwargs)
                    network.source_health(networking, family, name, True)
                    network.route_health(networking, family, name, route)
                    network.event(networking, category=family, source=name, file=path.name, action='verified',
                                  method=final.get('backend'), bytes=row['bytes'])
                    return
                except RuntimeError as error:
                    reason = str(error)
                    retained = retained_payload(stage, row)
                    resource_failure = reason in ('memory-guard', 'low-live-memory-use-streaming')
                    unresumable = reason == 'ResumeUnavailable' or (stage / 'model.xet.incomplete').exists()
                    permanent = reason in ('ValueError', 'ModuleNotFoundError', 'ImportError', 'unexpected-transfer-size')
                    if reason in ('PermissionError', 'OSError'):
                        network.pause_download(networking, name, path, retained, 'local disk or file access failed')
                    if (not resource_failure and not permanent and not (unresumable and retained)
                            and route_index + 1 < len(routes) and attempt + 1 < SAME_SOURCE_ATTEMPTS
                            and (network.connection_failure(reason) or reason in
                                 ('ConnectError', 'ConnectTimeout', 'ReadTimeout', 'HTTPError', 'RuntimeError', 'InvalidURL', 'stalled'))):
                        route_index += 1
                        network.event(networking, category=family, source=name, file=path.name, action='route-retry',
                                      route=routes[route_index], resume_bytes=retained, reason='connection-failed')
                        continue
                    if require_xet:
                        network.event(networking, category=family, source=name, file=path.name,
                                      action='failed', method='hf-xet', reason=reason)
                        raise RuntimeError('Required HF Xet download failed (%s); no fallback performed; all files retained' % error) from error
                    next_source = source_index + 1 < len(candidates)
                    if not resource_failure and not unresumable and not permanent and not next_source and attempt+1 < SAME_SOURCE_ATTEMPTS:
                        network.retry_wait(networking, attempt, category=family, source=name, file=path.name,
                                           resume_bytes=retained, reason=reason)
                        continue
                    if retained:
                        if next_source and not (resource_failure or unresumable or permanent):
                            from .transfer_handoff import migrate
                            fragments = migrate(row, path, candidates, allow_restart=False)
                            if path.is_file() and fragments['bytes'] == row['bytes']:
                                return
                            plan = dict(plan, manual_ranges=fragments['ranges'])
                        elif not plan.get('allow_model_restart') or resource_failure:
                            network.pause_download(networking, name, path, retained, 'xet-or-legacy-resume-unavailable' if unresumable else reason,
                                                   details=getattr(error, 'details', None))
                        else:
                            network.event(networking, category=family, source=name, file=path.name,
                                      action='restart-approved', retained_bytes=retained, reason=reason)
                            network.retain_partial(stage, 'restart-approved')
                    if not resource_failure:
                        network.source_health(networking, family, name, False)
                    network.event(networking, category=family, source=name, file=path.name, action='fallback',
                                  method='native-sdk', reason=reason)
                    break
            if resource_failure:
                break  # Another SDK cannot solve the same machine RAM pressure.
    # The curl fallback verifies and publishes before returning, so there is
    # no separate closed-but-unverified boundary to expose here.  Keep the
    # callback limited to the SDK path, where the worker emits
    # ``transfer_complete`` after its provider has closed the staged file.
    network.download(candidates, path, row.get('sha256') or row['git_blob'], progress_callback,
        network=networking, size=row['bytes'], headers_for=headers_for,
        algorithm='sha256' if row.get('sha256') else 'sha1', git_blob=not row.get('sha256'), category=family)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, required=True)
    args = parser.parse_args()
    with processes.worker_signals():
        sys.exit(sdk_worker(json.loads(args.worker.read_text(encoding='utf-8'))))
