"""Optional rewrite jobs; prompts stay in memory and never enter job reports."""
import atexit
import json
from pathlib import Path
import secrets
import subprocess
import threading
import time
import uuid

from . import assets, rules


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
        atexit.register(self.close)

    def info(self):
        root, _ = self.installation()
        spec = assets.catalog()
        return dict(ready=assets.ready(root), model=spec['repo'], license=spec['license'],
                    bytes=sum(row['bytes'] for row in spec['files']), busy=self.busy())

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def check(self):
        if self.stop.is_set():
            raise InterruptedError('cancelled')

    def progress(self, row):
        with self.mutex:
            self.state.update(row)

    def start(self, action, value):
        if action not in ('install', 'rewrite'):
            raise ValueError('invalid_request')
        if self.busy():
            raise ValueError('busy')
        root, machine = self.installation()
        if action == 'install':
            if value.get('accept_download') is not True:
                raise ValueError('consent_required')
        else:
            if not assets.ready(root):
                raise ValueError('model_missing')
            value = rules.request(value)
            from ..comfy_assets import resolve_assets
            resolved = resolve_assets(json.dumps(value['media']), self.input_directory())
            for i, row in enumerate(value['media']):
                path = (resolved['references'][i]['path'] if row['role'] == 'reference'
                        else resolved[row['role']])
                from ..comfy_assets import media_kind
                if media_kind(path) != 'image':
                    raise ValueError('unsupported_media')
                row['path'] = path
        self.key = secrets.token_urlsafe(24)
        self.stop.clear()
        self.result_expires = 0.
        self.state = dict(phase='starting')
        self.report = {}
        self.thread = threading.Thread(target=self.work, args=(root, machine, action, value),
                                       name='freevideo-prompt-vlm', daemon=True)
        self.thread.start()
        return dict(job=self.key)

    def status(self, key):
        if not isinstance(key, str) or not self.key or not secrets.compare_digest(key, self.key):
            raise ValueError('unknown_job')
        with self.mutex:
            if self.result_expires and time.monotonic() > self.result_expires:
                self.state.pop('text', None)
                self.state.update(phase='failed', error='expired')
            row = dict(self.state)
            # The browser may immediately queue video work or the next rewrite.
            # Do not announce completion while we still hold the runtime lease.
            if self.busy() and row.get('phase') in ('complete', 'failed', 'cancelled'):
                row['phase'] = 'finishing'
                row.pop('text', None)
            return row

    def cancel(self, key):
        self.status(key)
        self.stop.set()
        return dict(phase='cancelling')

    def close(self):
        self.stop.set()
        process = self.process
        if process is not None and process.poll() is None:
            from .. import processes
            processes.stop(process, grace=5)

    def work(self, root, machine, action, value):
        from ..locking import runtime_lock
        from ..monitoring import save
        started = time.monotonic()
        try:
            if action == 'rewrite':
                from ..resident_process import OWNER
                if OWNER.active:
                    raise BlockingIOError('busy')
                OWNER.stop_prewarm()
                from ..encoder_prewarm import IDLE
                IDLE.stop()
            with runtime_lock(Path(root) / 'engine.lock', inherit=False) as descriptor:
                self.check()
                if action == 'install':
                    assets.install(root, self.progress, self.check)
                    self.check()
                    self.progress(dict(phase='complete'))
                else:
                    self.infer(root, machine, descriptor, value)
        except InterruptedError:
            self.progress(dict(phase='cancelled'))
        except BlockingIOError:
            self.progress(dict(phase='failed', error='busy'))
        except Exception as error:
            known = {'disk_space', 'ram_space', 'rewrite_failed', 'worker_exit', 'timeout', 'worker_cleanup_failed'}
            self.progress(dict(phase='failed', error=str(error) if str(error) in known
                               else 'download_failed' if action == 'install' else 'rewrite_failed',
                               exception=type(error).__name__))
        finally:
            if self.stop.is_set():
                self.progress(dict(phase='cancelled'))
            self.result_expires = time.monotonic() + 600
            # Allowlisted metadata only, never original/rewritten text or input paths.
            report = {key: item for key, item in self.state.items()
                      if key in ('phase', 'error', 'exception', 'error_details', 'timings', 'diagnostics',
                                 'resources', 'cleanup', 'worker_returncode')}
            spec = assets.catalog()
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
        from .. import processes
        from ..comfy_environment import isolated_environment
        from ..comfy_bridge import source_root
        from .. import resident_process
        from ..ram import ProcessMemory
        import psutil
        env = isolated_environment(root, source_root())
        owner = resident_process.OWNER
        if owner.process is not None and owner.process.poll() is None and owner.endpoint:
            env[resident_process.ENV] = str(owner.endpoint)
        if env.get(resident_process.ENV) and Path(env[resident_process.ENV]).is_file():
            try:
                resident_process.release_idle_cache(env, descriptor)
            except resident_process.SessionRestarted:
                pass  # That worker already exited, which is the release we need.
            env.pop(resident_process.ENV, None)
        command = processes.module_command('freevideo_engine.prompt_vlm.worker', assets.directory(root))
        command[0] = machine['comfy_python']
        child = processes.popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                pass_fds=(descriptor,), supervise=True, start_new_session=True)
        self.process = child
        memory = ProcessMemory()
        memory_errors = 0
        next_sample = 0.
        observed = {}
        result = {}
        result_received_at = []
        native = dict(stderr_characters=0, out_of_memory=False, cuda_error=False, fatal_python_error=False)
        def read_errors():
            # Native libraries can bypass the structured worker exception. Keep
            # symptom flags, never stderr text (which can contain prompt pieces).
            tail = ''
            for chunk in iter(lambda: child.stderr.read(1024), ''):
                native['stderr_characters'] += len(chunk)
                content = (tail + chunk).lower()
                for key, phrase in [('out_of_memory', 'out of memory'), ('cuda_error', 'cuda error'),
                                    ('fatal_python_error', 'fatal python error')]:
                    native[key] |= phrase in content
                tail = content[-32:]
        def read():
            for line in child.stdout:
                if len(line) > 128 * 1024:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('phase') in ('complete', 'failed'):
                    result.update(row)
                    result_received_at[:] = [time.monotonic()]
                elif row.get('phase') in ('loading', 'rewriting'):
                    self.progress(row)
        reader = threading.Thread(target=read, daemon=True)
        error_reader = threading.Thread(target=read_errors, daemon=True)
        reader.start()
        error_reader.start()
        try:
            child.stdin.write(json.dumps(value, ensure_ascii=True))
            child.stdin.close()
            deadline = time.monotonic() + 600
            while child.poll() is None:
                self.check()
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
            reader.join(timeout=5)
            self.check()
            if reader.is_alive() or not result or child.returncode and result.get('phase') == 'complete':
                raise RuntimeError('worker_exit')
            if result.get('phase') == 'complete':
                rules.validate_output(result.get('text'), value)
            self.progress(dict(result, worker_returncode=child.returncode))
        finally:
            releasing = result_received_at[0] if result_received_at else time.monotonic()
            if child.poll() is None:
                # Killing only the Linux supervisor bypasses its descendant
                # cleanup. Stop the owned group/Windows Job and wait instead.
                processes.stop(child, grace=5)
            child.wait()
            reader.join(timeout=5)
            error_reader.join(timeout=5)
            child.stdout.close()
            child.stderr.close()
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
                            scope='Sampled worker tree, not an enforced RAM limit', native_exit=native)
            self.progress(dict(resources=resource, worker_returncode=child.returncode,
                               cleanup=dict(supervisor_exited=child.poll() is not None,
                                            observed_descendants=len(observed), remaining_descendants=len(surviving),
                                            seconds=time.monotonic() - releasing,
                                            model_retained=bool(surviving), kv_retained=bool(surviving),
                                            release_basis='Process exit; sampled descendant identities',
                                            verified=not surviving)))
            self.process = None
            if surviving:
                raise RuntimeError('worker_cleanup_failed')


def register():
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
            return web.json_response(dict(service.info(), token=token))
        except (OSError, ValueError):
            return web.json_response(dict(error='setup_required'), status=400)

    @server.routes.post('/freevideo/prompt-vlm/{action}')
    async def action(request):
        if not secrets.compare_digest(request.headers.get('X-FreeVideo-Prompt', ''), token):
            return web.json_response(dict(error='reload'), status=403)
        try:
            if request.content_length and request.content_length > 128 * 1024:
                raise ValueError('invalid_request')
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError('invalid_request')
            name = request.match_info['action']
            if name == 'status':
                result = service.status(value.get('job'))
            elif name == 'cancel':
                result = service.cancel(value.get('job'))
            else:
                running, pending = server.prompt_queue.get_current_queue()
                if running or pending:
                    raise ValueError('busy')
                result = service.start(name, value)
            return web.json_response(result)
        except (ValueError, OSError) as error:
            codes = {'invalid_request', 'invalid_prompt', 'invalid_duration', 'too_many_images',
                     'unsupported_media', 'mixed_media', 'busy', 'model_missing', 'consent_required', 'unknown_job'}
            return web.json_response(dict(error=str(error) if str(error) in codes else 'invalid_media'), status=400)
