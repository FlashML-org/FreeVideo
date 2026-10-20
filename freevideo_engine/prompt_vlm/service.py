"""Optional rewrite and ask jobs; prompts, questions and answers stay in memory, never in job reports."""
import atexit
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import tempfile
import threading
import time
import uuid

from . import assets, rules


class Worker:
    """One pipe owner across idle imports and the subsequent rewrite."""
    def __init__(self, process):
        self.process = process
        self.started = time.monotonic()
        self.claimed = False
        self.rows = queue.Queue()
        self.ready = threading.Event()
        self.ready_at = None
        self.import_seconds = None
        self.pid = None
        self.cuda_initialized = None
        self.native = dict(stderr_characters=0, out_of_memory=False, cuda_error=False, fatal_python_error=False)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.error_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.reader.start()
        self.error_reader.start()

    def read(self):
        for line in self.process.stdout:
            if len(line) > 128 * 1024:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get('phase') == 'ready':
                self.pid = row.get('pid')
                self.cuda_initialized = row.get('cuda_initialized')
                self.import_seconds = row.get('import_seconds')
                self.ready_at = time.monotonic()
                self.ready.set()
            else:
                self.rows.put(row)

    def read_errors(self):
        tail = ''
        for chunk in iter(lambda: self.process.stderr.read(1024), ''):
            self.native['stderr_characters'] += len(chunk)
            content = (tail + chunk).lower()
            for key, phrase in [('out_of_memory', 'out of memory'), ('cuda_error', 'cuda error'),
                                ('fatal_python_error', 'fatal python error')]:
                self.native[key] |= phrase in content
            tail = content[-32:]

    def close(self):
        self.reader.join(timeout=5)
        self.error_reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if not stream.closed:
                stream.close()


class Service:
    def __init__(self, installation, input_directory):
        self.installation = installation
        self.input_directory = input_directory
        self.mutex = threading.Lock()
        self.stop = threading.Event()
        self.state = {}
        self.key = None
        self.thread = None
        self.process = None
        self.result_expires = 0.
        self.report = {}
        self.ram = None
        self.prewarm = None
        self.prewarm_state = dict(phase='idle', reason='none')
        self.prestart_thread = None
        self.prestart_cleanup_threads = set()
        self.prestart_ticket = 0
        self.closed = False
        self.request_epoch = None
        self.active_worker = None
        atexit.register(self.close)

    def prestart(self, root, machine, *, generating=False):
        """Schedule imports without a lease or any GPU/model-cache yielding."""
        from ..resident_process import OWNER
        with self.mutex:
            reason = ('not_downloaded' if not assets.ready(root) else
                      'disabled' if self.closed or os.environ.get('FREEVIDEO_PROMPT_PRESTART', '').lower() in ('0', 'off', 'false') else
                      'generating' if generating or self.busy() or OWNER.active else None)
            if reason:
                self.prewarm_state = dict(phase='skipped', reason=reason)
                return dict(self.prewarm_state)
            if self.prewarm is not None and self.prewarm.process.poll() is None:
                return dict(self.prewarm_state)
            if self.prestart_thread is not None and self.prestart_thread.is_alive():
                return dict(self.prewarm_state)
            if self.prewarm is not None:
                expired, self.prewarm = self.prewarm, None
                expired.process.wait()
                expired.close()
            self.prestart_ticket += 1
            ticket = self.prestart_ticket
            self.prewarm_state = dict(phase='starting', reason='none')
            self.prestart_thread = threading.Thread(target=self._prestart, args=(root, machine, ticket),
                                                    name='freevideo-prompt-imports', daemon=True)
            self.prestart_thread.start()
            return dict(self.prewarm_state)

    def _prestart(self, root, machine, ticket):
        from ..resident_process import OWNER
        from .worker import available_memory
        try:
            required = self.ram_bytes(machine) + 2 * 2**30
            available = available_memory()
            env = self.environment(root)
            with self.mutex:
                if ticket != self.prestart_ticket:
                    return
                reason = 'generating' if self.busy() or OWNER.active else 'memory' if available < required else None
                if reason:
                    self.prewarm_state = dict(phase='skipped', reason=reason,
                                             available_bytes=available, required_bytes=required)
                    return
            from .worker import PRESTART_ENV
            env[PRESTART_ENV] = '1'
            session = Worker(self.spawn(root, machine, env))
            with self.mutex:
                admitted = ticket == self.prestart_ticket
                if admitted:
                    self.prewarm = session
                    self.prewarm_state = dict(phase='importing', reason='none')
            if not admitted:
                self._cleanup_prestart(session)
                return
            threading.Thread(target=self._watch_prestart, args=(session,), daemon=True).start()
        except Exception:
            with self.mutex:
                if ticket == self.prestart_ticket:
                    self.prewarm_state = dict(phase='failed', reason='failed')

    def _watch_prestart(self, session):
        while session.process.poll() is None:
            with self.mutex:
                if self.prewarm is not session:
                    return
                if session.ready.is_set():
                    self.prewarm_state = dict(phase='ready', reason='none', import_seconds=session.import_seconds)
            session.ready.wait(.1) if not session.ready.is_set() else time.sleep(.1)
        session.process.wait()
        with self.mutex:
            if self.prewarm is not session:
                return
            self.prewarm = None
            reason = 'idle_exit' if session.process.returncode == 0 else 'failed'
            self.prewarm_state = dict(phase='idle' if reason == 'idle_exit' else 'failed', reason=reason)
        session.close()

    def stop_prestart(self, reason, *, wait=True):
        with self.mutex:
            self.prestart_ticket += 1  # Invalidate an admission check still in progress.
            session, self.prewarm = self.prewarm, None
            self.prewarm_state = dict(phase='stopped', reason=reason)
            if session is not None and not wait:
                cleanup = threading.Thread(target=self._cleanup_prestart, args=(session,),
                                           name='freevideo-prompt-cleanup', daemon=True)
                self.prestart_cleanup_threads.add(cleanup)
                cleanup.start()
                session = None
            cleanups = tuple(self.prestart_cleanup_threads) if wait else ()
            admission = self.prestart_thread if wait else None
        if session is not None:
            self._cleanup_prestart(session)
        for cleanup in cleanups:
            cleanup.join()
        if admission is not None and admission is not threading.current_thread():
            admission.join()  # A spawn in progress must reap its invalidated worker first.

    def _cleanup_prestart(self, session):
        from .. import processes
        try:
            processes.stop(session.process, grace=5)
            session.process.wait()
            session.close()
        finally:
            with self.mutex:
                self.prestart_cleanup_threads.discard(threading.current_thread())

    def environment(self, root):
        from ..comfy_environment import isolated_environment
        from ..comfy_bridge import source_root
        from .worker import PRESTART_ENV
        from .. import processes, resident_process
        env = isolated_environment(root, source_root())
        # No inherited lease, handle, session or prestart mode in an idle worker.
        for name in ('FREEVIDEO_RUNTIME_LOCK_FD', processes.HANDLE_ENV, resident_process.ENV, PRESTART_ENV):
            env.pop(name, None)
        return env

    @staticmethod
    def spawn(root, machine, env, descriptor=None):
        from .. import processes
        command = processes.module_command('freevideo_engine.prompt_vlm.worker', assets.directory(root))
        command[0] = machine['comfy_python']
        return processes.popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                               pass_fds=() if descriptor is None else (descriptor,),
                               supervise=True, start_new_session=True)

    def info(self, wait=True):
        """wait=False answers from the cheap check, for callers on an event loop or UI thread."""
        root, machine = self.installation()
        try:
            spec = assets.catalog()
        except (OSError, ValueError):
            return dict(ready=False, model='', license='', bytes=0, busy=self.busy(), ram_bytes=self.ram_bytes(machine))
        return dict(ready=assets.settle(root) if wait else assets.ready(root), model=spec['repo'], license=spec['license'],
                    bytes=sum(row['bytes'] for row in spec['files']), busy=self.busy(),
                    ram_bytes=self.ram_bytes(machine))

    def ram_bytes(self, machine):
        """Available RAM the model usually needs on this computer, for the download card.

        The worker decides from free VRAM when it loads: 8 GB and larger NVIDIA
        cards keep their layers on the GPU; 6 GB cards stream from RAM the layers
        that do not fit, which needs more of it. Asks the driver once, without CUDA; anything
        unknown states the larger figure.
        """
        if self.ram is None:
            from .worker import MAPPED_RAM, RESIDENT_RAM, STREAMED_RAM
            self.ram = MAPPED_RAM
            if machine.get('device_backend', 'cuda') == 'cuda':
                from .. import processes
                from ..system import nvidia_smi
                try:
                    rows = [line.split(',') for line in processes.run(
                        [nvidia_smi(), '--query-gpu=uuid,memory.total', '--format=csv,noheader,nounits'],
                        timeout=10, capture_output=True, text=True).stdout.splitlines() if line.strip()]
                    totals = {uuid.strip(): float(mib) for uuid, mib in rows}
                    total = totals.get(machine.get('gpu_uuid')) or (len(totals) == 1 and next(iter(totals.values())))
                    if total and total >= 7.5 * 1024:
                        self.ram = RESIDENT_RAM
                    elif total and total >= 5.5 * 1024:
                        self.ram = STREAMED_RAM
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                    pass
        return self.ram

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def check(self):
        if self.stop.is_set():
            raise InterruptedError('cancelled')

    def progress(self, row):
        with self.mutex:
            self.state.update(row)

    def start(self, action, value):
        received_epoch = time.time()
        if action not in ('install', 'rewrite', 'ask', 'split'):
            raise ValueError('invalid_request')
        if self.busy():
            raise ValueError('busy')
        root, machine = self.installation()
        if action == 'install':
            if value.get('accept_download') is not True:
                raise ValueError('consent_required')
        elif action == 'ask':
            if not assets.settle(root):
                raise ValueError('model_missing')
            value = self.ask_images(rules.ask_request(value))
        elif action == 'split':
            if not assets.settle(root):
                raise ValueError('model_missing')
            value = rules.split_request(value)
        else:
            if not assets.settle(root):
                raise ValueError('model_missing')
            value = rules.request(value)
            from ..comfy_assets import resolve_assets
            resolved = resolve_assets(json.dumps([dict(file=row['file'], role=row['role']) for row in value['media']]),
                                      self.input_directory())
            for i, row in enumerate(value['media']):
                path = (resolved['references'][i]['path'] if row['role'] == 'reference'
                        else resolved[row['role']])
                from ..comfy_assets import media_kind
                if media_kind(path) != row['kind']:
                    raise ValueError('unsupported_media')
                row['path'] = path
        with self.mutex:
            if self.busy():
                raise ValueError('busy')
            if action != 'install':
                self.prestart_ticket += 1  # Invalidate pending imports before the worker starts.
            self.key = secrets.token_urlsafe(24)
            self.stop.clear()
            self.result_expires = 0.
            self.state = dict(phase='starting')
            self.report = {}
            self.request_epoch = received_epoch
            self.thread = threading.Thread(target=self.work, args=(root, machine, action, value),
                                           name='freevideo-prompt-vlm', daemon=True)
            self.thread.start()
        return dict(job=self.key)

    def ask_images(self, value):
        """Resolve every question's images inside ComfyUI input; images only."""
        from ..comfy_assets import resolve_assets, media_kind
        rows = [image for question in value['questions'] for image in question['images']]
        if not rows:
            return value
        resolved = resolve_assets(json.dumps([dict(file=row['file'], role='reference') for row in rows]),
                                  self.input_directory())
        paths = iter(row['path'] for row in resolved['references'])
        for question in value['questions']:
            for image in question['images']:
                image['path'] = next(paths)
                if media_kind(image['path']) != 'image':
                    raise ValueError('unsupported_media')
        return value

    def status(self, key):
        if not isinstance(key, str) or not self.key or not secrets.compare_digest(key, self.key):
            raise ValueError('unknown_job')
        with self.mutex:
            if self.result_expires and time.monotonic() > self.result_expires:
                self.state.pop('text', None)
                self.state.pop('answers', None)
                self.state.pop('parts', None)
                self.state.update(phase='failed', error='expired')
            row = dict(self.state)
            # The browser may immediately queue video work or the next rewrite.
            # Do not announce completion while we still hold the runtime lease.
            if self.busy() and row.get('phase') in ('complete', 'failed', 'cancelled'):
                row['phase'] = 'finishing'
                row.pop('text', None)
                row.pop('answers', None)
                row.pop('parts', None)
            return row

    def cancel(self, key):
        self.status(key)
        self.stop.set()
        return dict(phase='cancelling')

    def close(self):
        self.closed = True
        self.stop.set()
        self.stop_prestart('disabled')
        process = self.process
        if process is not None and process.poll() is None:
            from .. import processes
            processes.stop(process, grace=5)

    @staticmethod
    def release_idle_image_worker():
        # An image worker that is only waiting out its idle time still holds the engine lease.
        try:
            from server import PromptServer
            from ..image_gen.service import release_idle_worker
            release_idle_worker(PromptServer.instance)
        except Exception:
            pass

    def video_frames(self, value, folder):
        """The model reads pictures: a reference video is shown as its middle frame."""
        for row in value.get('media', ()):
            if row.get('kind') != 'video':
                continue
            self.check()
            try:
                from ..agent.media import sample_frames
                row['path'] = sample_frames(row['path'], Path(folder) / str(row['number']), count=1)[0]['path']
            except Exception:
                raise ValueError('unreadable_video') from None

    def work(self, root, machine, action, value):
        from ..locking import runtime_lock
        from ..monitoring import save
        started = time.monotonic()
        try:
            from ..background_compile import COMPILE
            COMPILE.stop()
            self.release_idle_image_worker()
            if action != 'install':
                from ..resident_process import OWNER
                if OWNER.active:
                    raise BlockingIOError('busy')
                OWNER.stop_prewarm()
                from ..encoder_prewarm import IDLE
                IDLE.stop()
                from ..comfy_residency import release_native
                from ..image_gen.service import comfy_server
                release_native(comfy_server())
            with runtime_lock(Path(root) / 'engine.lock', inherit=False) as descriptor:
                self.check()
                if action == 'install':
                    # The launcher's background download writes the same partial files under this lock.
                    with runtime_lock(Path(root) / 'prompt-vlm-install.lock', inherit=False):
                        assets.install(root, self.progress, self.check)
                    self.check()
                    self.progress(dict(phase='complete'))
                else:
                    with tempfile.TemporaryDirectory(prefix='freevideo-prompt-', ignore_cleanup_errors=True) as frames:
                        self.video_frames(value, frames)
                        self.infer(root, machine, descriptor, value)
        except InterruptedError:
            self.progress(dict(phase='cancelled'))
        except BlockingIOError:
            self.progress(dict(phase='failed', error='busy'))
        except Exception as error:
            known = {'disk_space', 'ram_space', 'unreadable_video', 'rewrite_failed', 'split_failed', 'worker_exit', 'timeout', 'worker_cleanup_failed'}
            self.progress(dict(phase='failed', error='invalid_output' if isinstance(error, rules.InvalidOutput)
                               else str(error) if str(error) in known
                               else 'download_failed' if action == 'install'
                               else 'ask_failed' if action == 'ask' else 'split_failed' if action == 'split'
                               else 'rewrite_failed',
                               exception=type(error).__name__,
                               **({'output_rejection': error.details} if isinstance(error, rules.InvalidOutput) else {})))
        finally:
            if action != 'install':
                self.stop_prestart(self.prewarm_state.get('reason', 'none'))
            if self.stop.is_set():
                self.progress(dict(phase='cancelled'))
            self.result_expires = time.monotonic() + 600
            # Allowlisted metadata only, never original/rewritten text or input paths.
            # Output rejections contain condition IDs, counts, typed media labels and retry flags only.
            report = {key: item for key, item in self.state.items()
                      if key in ('phase', 'error', 'exception', 'error_details', 'timings', 'diagnostics',
                                 'resources', 'cleanup', 'worker_returncode', 'output_rejection')}
            try:
                spec = assets.catalog()
            except (OSError, ValueError):
                spec = dict(repo='', revision='')
            report.update(schema='freevideo.prompt-vlm', schema_version=2, action=action,
                          created_epoch=time.time(), report_id=uuid.uuid4().hex,
                          elapsed_seconds=time.monotonic() - started, model=spec['repo'],
                          model_revision=spec['revision'])
            self.report = report
            try:
                directory = Path(root) / 'prompt-vlm-runs' / report['report_id']
                save(directory / 'prompt-rewrite.json', report)
                save(Path(root) / 'prompt-vlm-report.json', report)
                if action == 'rewrite':
                    from ..comfy_progress import REPORTS
                    self.progress(dict(rewrite_report_id=report['report_id'],
                                       report_id=REPORTS.register(directory / 'video.mp4')))
            except OSError:
                pass

    def infer(self, root, machine, descriptor, value):
        # A claimed idle worker has no inherited lease. Keep the parent's lease
        # through bounded cleanup, including failures before request submission.
        try:
            self._infer(root, machine, descriptor, value)
        finally:
            session = self.active_worker
            if session is not None:
                from .. import processes
                processes.stop(session.process, grace=5)
                session.process.wait()
                session.close()
                self.active_worker = None
                self.process = None

    def _infer(self, root, machine, descriptor, value):
        from .. import processes
        from .. import resident_process
        from ..ram import ProcessMemory
        from .worker import process_memory
        import psutil
        env = self.environment(root)
        owner = resident_process.OWNER
        if owner.process is not None and owner.process.poll() is None and owner.endpoint:
            env[resident_process.ENV] = str(owner.endpoint)
        if env.get(resident_process.ENV) and Path(env[resident_process.ENV]).is_file():
            try:
                resident_process.release_idle_cache(env, descriptor)
                resident_process.OWNER.record(action='released', reason='prompt_model')
            except resident_process.SessionRestarted:
                pass  # That worker already exited, which is the release we need.
            env.pop(resident_process.ENV, None)
        expired = None
        with self.mutex:
            session, self.prewarm = self.prewarm, None
            reason = self.prewarm_state.get('reason', 'none')
            if reason == 'none' and os.environ.get('FREEVIDEO_PROMPT_PRESTART', '').lower() in ('0', 'off', 'false'):
                reason = 'disabled'
            if session is not None and session.process.poll() is not None:
                expired, session = session, None
                reason = 'idle_exit' if expired.process.returncode == 0 else 'failed'
            if session is not None:
                session.claimed = True
                self.active_worker = session
                self.process = session.process
        if expired is not None:
            expired.process.wait()
            expired.close()
        prewarm = 'cold' if session is None else 'used' if session.ready.is_set() else 'waited'
        diagnostics = dict(prewarm=prewarm, prewarm_reason=reason if prewarm == 'cold' else 'none',
                           prewarm_age_seconds=time.monotonic() - session.started if session else None,
                           prewarm_import_seconds=session.import_seconds if session else None,
                           request_to_first_output_seconds=None)
        footprint = dict(prewarm_rss_bytes=None, prewarm_private_bytes=None)
        if session is not None:
            try:
                pid = session.pid
                if pid is None:
                    children = psutil.Process(session.process.pid).children(recursive=True)
                    if not children:
                        raise RuntimeError('Worker PID not visible yet')
                    pid = children[0].pid
                own = process_memory(pid)
                footprint.update(prewarm_rss_bytes=own['rss_bytes'], prewarm_private_bytes=own['private_bytes'],
                                 prewarm_memory_metric=own['metric'])
                if 'private_working_set_bytes' in own:
                    footprint.update(prewarm_private_working_set_bytes=own['private_working_set_bytes'],
                                     prewarm_private_commit_bytes=own['private_commit_bytes'])
            except (psutil.Error, OSError, RuntimeError):
                footprint['prewarm_memory_unavailable'] = True
        else:
            session = Worker(self.spawn(root, machine, env, descriptor))
            self.active_worker = session
        child = session.process
        self.process = child
        memory = ProcessMemory()
        memory_errors = 0
        next_sample = 0.
        observed = {}
        result = {}
        result_received_at = []
        sent_at = time.monotonic()
        write_errors = []
        def submit():
            try:
                request = dict(value)
                if prewarm != 'cold':
                    request['_prewarm'] = dict(sent_epoch=time.time())
                child.stdin.write(json.dumps(request, ensure_ascii=True))
                child.stdin.close()
            except Exception as error:
                write_errors.append(error)
        writer = threading.Thread(target=submit, daemon=True)
        def read_rows():
            while True:
                try:
                    row = session.rows.get_nowait()
                except queue.Empty:
                    return
                stats = dict(row.get('diagnostics') or {})
                if prewarm != 'cold':
                    waited = max(0., (session.ready_at or time.monotonic()) - sent_at) if prewarm == 'waited' else 0.
                    diagnostics.update(prewarm_import_seconds=session.import_seconds,
                                       prewarm_wait_seconds=waited, import_seconds=waited)
                if stats.get('first_output_epoch') is not None and self.request_epoch is not None:
                    diagnostics['request_to_first_output_seconds'] = max(0., stats['first_output_epoch'] - self.request_epoch)
                row['diagnostics'] = dict(stats, **diagnostics)
                if row.get('phase') in ('complete', 'failed'):
                    result.update(row)
                    result_received_at[:] = [time.monotonic()]
                elif row.get('phase') in ('loading', 'rewriting', 'answering'):
                    self.progress(row)
        try:
            writer.start()
            deadline = time.monotonic() + 600
            while child.poll() is None:
                read_rows()
                self.check()
                if write_errors:
                    raise write_errors[0]
                if time.monotonic() > deadline:
                    raise TimeoutError('timeout')
                if time.monotonic() >= next_sample:
                    try:
                        memory.sample(child.pid)
                    except (OSError, RuntimeError):
                        memory_errors += 1
                    # Track identity, not only PIDs, to verify descendants after
                    # cancellation. Observation failure is diagnostic, not fatal.
                    try:
                        for process in psutil.Process(child.pid).children(recursive=True):
                            observed[process.pid] = process
                    except psutil.Error:
                        memory_errors += 1
                    next_sample = time.monotonic() + .5
                self.stop.wait(.1)
            session.reader.join(timeout=5)
            read_rows()
            self.check()
            if session.reader.is_alive() or not result or child.returncode and result.get('phase') == 'complete':
                raise RuntimeError('worker_exit')
            if result.get('phase') == 'complete':
                if value.get('mode') == 'ask':
                    rules.validate_answers(result.get('answers'), value)
                elif value.get('mode') == 'split':
                    rules.validate_parts(result.get('parts'), value)
                else:
                    try:
                        rules.validate_output(result.get('text'), value)
                    except rules.InvalidOutput as error:
                        stats = result.get('diagnostics') or {}
                        error.details['retried'] = bool(stats.get('media_retry') or stats.get('picture_retry')
                                                       or stats.get('dialogue_retry'))
                        self.progress(dict(diagnostics=stats))
                        raise
            self.progress(dict(result, worker_returncode=child.returncode))
        finally:
            releasing = result_received_at[0] if result_received_at else time.monotonic()
            if child.poll() is None:
                # Killing only the Linux supervisor bypasses its descendant
                # cleanup. Stop the owned group/Windows Job and wait instead;
                # the worker finishes its current GPU operation and exits.
                processes.stop(child, grace=15)
            child.wait()
            writer.join(timeout=5)
            session.close()
            surviving = []
            for process in observed.values():
                try:
                    if process.is_running() and process.status() != 'zombie':
                        surviving.append(process)
                except psutil.NoSuchProcess:
                    pass
            if surviving:
                for process in surviving:
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
                _, surviving = psutil.wait_procs(surviving, timeout=5)
            resource = {key: item for key, item in memory.result().items()
                        if item is None or type(item) in (int, float, bool) or key == 'ram_guard_metric'}
            resource.update(sample_errors=memory_errors, poll_interval_seconds=.5,
                            scope='Sampled worker tree, not an enforced RAM limit', native_exit=session.native,
                            **footprint)
            self.progress(dict(resources=resource, diagnostics=self.state.get('diagnostics') or diagnostics,
                               worker_returncode=child.returncode,
                               cleanup=dict(supervisor_exited=child.poll() is not None,
                                            observed_descendants=len(observed), remaining_descendants=len(surviving),
                                            seconds=time.monotonic() - releasing,
                                            model_retained=bool(surviving), kv_retained=bool(surviving),
                                            release_basis='Process exit; sampled descendant identities',
                                            verified=not surviving)))
            self.process = None
            if surviving:
                raise RuntimeError('worker_cleanup_failed')


def stop_prestart_for_generation(server):
    service = getattr(server, '_freevideo_prompt_vlm', None)
    if service is not None:
        service.stop_prestart('generating', wait=False)


def register():
    import asyncio
    from aiohttp import web
    from server import PromptServer
    import folder_paths
    from ..comfy_bridge import installation
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_prompt_vlm', None) is not None:
        return
    service = Service(installation, folder_paths.get_input_directory)
    server._freevideo_prompt_vlm = service
    token = secrets.token_urlsafe(24)

    @server.routes.get('/freevideo/prompt-vlm')
    async def info(request):
        try:
            return web.json_response(dict(await asyncio.to_thread(service.info), token=token))
        except (OSError, ValueError):
            return web.json_response(dict(error='setup_required'), status=400)

    @server.routes.post('/freevideo/prompt-vlm/{action}')
    async def action(request):
        if not secrets.compare_digest(request.headers.get('X-FreeVideo-Prompt', ''), token):
            return web.json_response(dict(error='reload'), status=403)
        try:
            name = request.match_info['action']
            # Ask jobs carry up to eight questions; rewrite keeps its smaller limit.
            limit = (512 if name == 'ask' else 128) * 1024
            if request.content_length and request.content_length > limit:
                raise ValueError('invalid_request')
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError('invalid_request')
            if name == 'status':
                result = service.status(value.get('job'))
            elif name == 'cancel':
                result = service.cancel(value.get('job'))
            elif name == 'prestart':
                running, pending = server.prompt_queue.get_current_queue()
                root, machine = service.installation()
                result = dict(state=service.prestart(root, machine, generating=bool(running or pending)))
            else:
                running, pending = server.prompt_queue.get_current_queue()
                if running or pending:
                    raise ValueError('busy')
                if name in ('ask', 'split', 'rewrite'):
                    # A moved modified time is checked by content here, off the event loop;
                    # start() then finds the remembered answer.
                    await asyncio.to_thread(assets.settle, service.installation()[0])
                result = service.start(name, value)
            return web.json_response(result)
        except (ValueError, OSError) as error:
            codes = {'invalid_request', 'invalid_prompt', 'invalid_duration', 'too_many_images',
                     'unsupported_media', 'mixed_media', 'busy', 'model_missing', 'consent_required', 'unknown_job'}
            return web.json_response(dict(error=str(error) if str(error) in codes else 'invalid_media'), status=400)
