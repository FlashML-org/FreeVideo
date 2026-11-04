"""Dubbing for the editor page, the Agent and the voice change: one job at a time, one interface.

A job takes lines (text and target seconds) and one reference voice, and writes one wav per line
into ComfyUI's input folder (`freevideo-dubbing/<job>/`), where the other tools already find media.
The model runs in its own process, so its memory leaves with it. The model is downloaded on the
first use after the user agreed (`install`).
"""
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
import uuid

from . import backends, pack
from ..prompt_vlm.service import Worker

OUTPUT = 'freevideo-dubbing'
MAX_LINES = 500
MAX_TEXT = 2000
MAX_SECONDS = 120.
MIN_SECONDS = .3
RUN_SECONDS = 300
LINE_SECONDS = 45
RUN_LIMIT = 3600
KEPT_RUNS = 8
LINE_KEYS = ('id', 'seconds', 'target', 'samples', 'onset', 'offset', 'onset_error', 'offset_error', 'error', 'speed',
             'passes', 'stretch', 'fitted', 'warning', 'overrun', 'gain_db', 'reference', 'code')
CODES = {'invalid_request', 'unsupported_media', 'busy', 'model_missing', 'consent_required', 'unknown_job',
         'reference_text_required', 'reference_short', 'no_audio'}
KNOWN = {'disk_space', 'download_failed', 'not_published', 'unsupported', 'timeout', 'worker_exit', 'worker_cleanup_failed',
         'out_of_memory', 'model_missing', 'invalid_request', 'reference_text_required', 'reference_short'}


def reference_of(value, *, required):
    """{file, text, start[, end]} of a job or line reference; None when absent and not required."""
    if value is None and not required:
        return None
    if not isinstance(value, dict) or not isinstance(value.get('file'), str) or not value['file']:
        raise ValueError('invalid_request')
    text = value.get('text', '')
    start = value.get('start', 0)
    end = value.get('end')
    if (not isinstance(text, str) or len(text) > 1000 or isinstance(start, bool)
            or not isinstance(start, (int, float)) or not 0 <= start <= 36000):
        raise ValueError('invalid_request')
    result = dict(file=value['file'], text=text.strip(), start=float(start))
    if end is not None:
        if isinstance(end, bool) or not isinstance(end, (int, float)) or not start < end <= 36000:
            raise ValueError('invalid_request')
        result['end'] = float(end)
    return result


def request(value):
    """The job a page, the Agent or the voice change may send; anything else is refused."""
    if not isinstance(value, dict):
        raise ValueError('invalid_request')
    lines = value.get('lines')
    if not isinstance(lines, list) or not 0 < len(lines) <= MAX_LINES:
        raise ValueError('invalid_request')
    clean = []
    seen = set()
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError('invalid_request')
        text = line.get('text')
        seconds = line.get('seconds')
        name = str(line.get('id', 'line%03d' % index))
        if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not MIN_SECONDS <= seconds <= MAX_SECONDS
                or not name or len(name) > 64 or not all(c.isalnum() or c in '-_' for c in name)
                or name in seen):
            raise ValueError('invalid_request')
        seen.add(name)
        row = dict(id=name, text=text.strip(), seconds=float(seconds))
        onset, offset = line.get('onset'), line.get('offset')
        for key, number in (('onset', onset), ('offset', offset)):
            if number is None:
                continue
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= seconds + 1e-9:
                raise ValueError('invalid_request')
            row[key] = float(number)
        if row.get('offset', seconds) - row.get('onset', 0.) < MIN_SECONDS / 2:
            raise ValueError('invalid_request')
        if line.get('reference') is not None:
            row['reference'] = reference_of(line['reference'], required=True)
        instruction = line.get('instruction')
        if instruction not in (None, ''):
            if not isinstance(instruction, str) or len(instruction) > 200:
                raise ValueError('invalid_request')
            row['instruction'] = instruction.strip()
        clean.append(row)
    reference = reference_of(value.get('reference'), required=not all('reference' in row for row in clean))
    result = dict(lines=clean)
    if reference is not None:
        result['reference'] = reference
    seed = value.get('seed')
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
            raise ValueError('invalid_request')
        result['seed'] = seed
    name = value.get('backend')
    if name is not None:
        if name not in backends.names():
            raise ValueError('invalid_request')
        result['backend'] = name
    return result


class Service:
    def __init__(self, installation, input_directory):
        self.installation = installation
        self.input_directory = input_directory
        self.mutex = threading.Lock()
        self.stop = threading.Event()
        self.state = dict(phase='idle')
        self.key = None
        self.thread = None
        self.process = None
        self.result_expires = 0.
        self.report = {}
        self.keep = False
        self.folder = None
        self.closed = False
        import atexit
        atexit.register(self.close)

    def info(self, wait=True):
        root, _ = self.installation()
        try:
            state = pack.status(root)
        except (OSError, ValueError):
            return dict(ready=False, bytes=0, busy=self.busy(), backends=backends.names())
        if wait and not state['ready']:
            state['ready'] = pack.settle(root)
            if state['ready']:
                state['bytes'] = 0
        return dict(ready=state['ready'], model=pack.NAME, license=state['license'], bytes=state['bytes'],
                    published=state['published'], supported=state['supported'], busy=self.busy(), backends=backends.names())

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def check(self):
        if self.stop.is_set():
            raise InterruptedError('cancelled')

    def progress(self, row):
        with self.mutex:
            self.state.update(row)

    def start(self, action, value, *, trusted=False, keep=False):
        """`trusted`: reference files are absolute paths the caller has already checked (the voice change reads
        its own separated files); `keep`: a cancelled or failed run leaves the finished lines in its folder,
        and the caller removes the folder."""
        if action not in ('install', 'dub'):
            raise ValueError('invalid_request')
        if self.busy():
            raise ValueError('busy')
        root, machine = self.installation()
        if action == 'install':
            if not isinstance(value, dict) or value.get('accept_download') is not True:
                raise ValueError('consent_required')
        else:
            if not pack.settle(root):
                raise ValueError('model_missing')
            value = request(value)
            for item in [value.get('reference')] + [line.get('reference') for line in value['lines']]:
                if item:
                    item['path'] = item['file'] if trusted and os.path.isabs(item['file']) else self.resolve(item['file'])
        with self.mutex:
            if self.busy():
                raise ValueError('busy')
            self.key = secrets.token_urlsafe(24)
            self.stop.clear()
            self.result_expires = 0.
            self.state = dict(phase='starting')
            self.report = {}
            self.keep = keep
            self.folder = None
            self.thread = threading.Thread(target=self.work, args=(root, machine, action, value),
                                           name='freevideo-dubbing', daemon=True)
            self.thread.start()
        return dict(job=self.key)

    def resolve(self, name):
        """A reference inside ComfyUI's input folder; audio, or a video whose sound is used."""
        from ..comfy_assets import media_kind, resolve_assets
        resolved = resolve_assets(json.dumps([dict(file=name, role='reference')]), self.input_directory())
        path = resolved['references'][0]['path']
        if media_kind(path) not in ('audio', 'video'):
            raise ValueError('unsupported_media')
        return path

    def status(self, key):
        if not isinstance(key, str) or not self.key or not secrets.compare_digest(key, self.key):
            raise ValueError('unknown_job')
        with self.mutex:
            if self.result_expires and time.monotonic() > self.result_expires:
                self.state.pop('lines', None)
                self.state.update(phase='failed', error='expired')
            row = dict(self.state)
            # Do not announce completion while the runtime lease is still held.
            if self.busy() and row.get('phase') in ('complete', 'failed', 'cancelled'):
                row = dict(phase='finishing')
            return row

    def cancel(self, key):
        self.status(key)
        self.stop.set()
        return dict(phase='cancelling')

    def close(self):
        self.closed = True
        self.stop.set()
        process = self.process
        if process is not None and process.poll() is None:
            from .. import processes
            processes.stop(process, grace=5)

    @staticmethod
    def release_others():
        """Give the GPU back: compile, idle image worker, resident engine process, ComfyUI models."""
        from ..background_compile import COMPILE
        COMPILE.stop()
        try:
            from server import PromptServer
            from ..image_gen.service import release_idle_worker
            release_idle_worker(PromptServer.instance)
        except Exception:
            pass
        from ..resident_process import OWNER
        if OWNER.active:
            raise BlockingIOError('busy')
        OWNER.stop_prewarm()
        from ..encoder_prewarm import IDLE
        IDLE.stop()
        from ..comfy_residency import release_native
        from ..image_gen.service import comfy_server
        try:
            release_native(comfy_server())
        except Exception:  # only frees memory early; the worker's own allocation still decides
            logging.warning('[FreeVideo] Could not unload ComfyUI models before dubbing', exc_info=True)

    def output_folder(self, key_id):
        base = Path(self.input_directory()) / OUTPUT
        base.mkdir(parents=True, exist_ok=True)
        # Keep the latest few runs; older ones are the page's old results.
        runs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for old in runs[:max(0, len(runs) - KEPT_RUNS + 1)]:
            shutil.rmtree(old, ignore_errors=True)
        folder = base / key_id
        folder.mkdir()
        return folder

    def work(self, root, machine, action, value):
        from ..locking import runtime_lock
        from ..monitoring import save
        started = time.monotonic()
        try:
            if action == 'install':
                with runtime_lock(Path(root) / 'dubbing-install.lock', inherit=False):
                    self.check()
                    pack.install(root, self.progress, self.check)
                self.check()
                self.progress(dict(phase='complete'))
            else:
                self.release_others()
                with runtime_lock(Path(root) / 'engine.lock', inherit=False) as descriptor:
                    self.check()
                    self.dub(root, machine, descriptor, value)
        except InterruptedError:
            self.progress(dict(phase='cancelled'))
        except BlockingIOError:
            self.progress(dict(phase='failed', error='busy'))
        except pack.PackError as error:
            self.progress(dict(phase='failed', error=error.reason))
        except Exception as error:
            reason = str(error)
            self.progress(dict(phase='failed', exception=type(error).__name__,
                               error=reason if reason in KNOWN else 'download_failed' if action == 'install'
                               else 'dub_failed'))
        finally:
            if self.stop.is_set():
                self.progress(dict(phase='cancelled'))
            self.result_expires = time.monotonic() + 600
            # Allowlisted metadata only: never the text, file names or paths.
            report = {key: item for key, item in self.state.items()
                      if key in ('phase', 'error', 'exception', 'timings', 'worker_returncode', 'backend',
                                 'sample_rate', 'reference_seconds', 'loaded_seconds')}
            if self.state.get('lines'):
                report['lines'] = [{key: row.get(key) for key in LINE_KEYS if key not in ('id', 'reference')}
                                   for row in self.state['lines']]
            report.update(schema='freevideo.dubbing', schema_version=1, action=action, created_epoch=time.time(),
                          report_id=uuid.uuid4().hex, elapsed_seconds=time.monotonic() - started)
            self.report = report
            try:
                save(Path(root) / 'dubbing-report.json', report)
            except OSError:
                pass

    @staticmethod
    def environment(root):
        from ..comfy_environment import isolated_environment
        from ..comfy_bridge import source_root
        from .. import processes, resident_process
        env = isolated_environment(root, source_root())
        for name in ('FREEVIDEO_RUNTIME_LOCK_FD', processes.HANDLE_ENV, resident_process.ENV):
            env.pop(name, None)
        return env

    @staticmethod
    def spawn(root, machine, env, descriptor=None):
        from .. import processes
        command = processes.module_command('freevideo_engine.dubbing.worker', pack.directory(root), pack.runtime_path(root) or '')
        command[0] = machine['comfy_python']
        return processes.popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                               pass_fds=() if descriptor is None else (descriptor,),
                               supervise=True, start_new_session=True)

    def dub(self, root, machine, descriptor, value):
        from .. import processes, resident_process
        from . import reference
        import psutil
        env = self.environment(root)
        key_id = uuid.uuid4().hex[:12]
        folder = self.output_folder(key_id)
        self.folder = folder
        self.progress(dict(phase='preparing'))
        extracted = {}

        def extract(ref, name):
            key = (ref['path'], ref['start'], ref.get('end'))
            if key not in extracted:
                wav = folder / name
                span = ref['end'] - ref['start'] if ref.get('end') else backends.MAX_REFERENCE_SECONDS
                try:
                    reference.extract(ref['path'], wav, start=ref['start'], seconds=min(span, backends.MAX_REFERENCE_SECONDS))
                except ValueError as error:
                    if str(error) not in CODES:
                        raise ValueError('unsupported_media') from None
                    extracted[key] = str(error)
                    return extracted[key]
                except Exception:
                    raise ValueError('unsupported_media') from None
                extracted[key] = str(wav)
            return extracted[key]
        job = dict(value, output_dir=str(folder))
        if value.get('reference'):
            made = extract(value['reference'], 'reference.wav')
            if made not in (str(folder / 'reference.wav'),):
                raise ValueError(made)
            job['reference'] = dict(path=made, text=value['reference']['text'])
        job['lines'] = []
        for index, line in enumerate(value['lines']):
            row = {key: item for key, item in line.items() if key != 'reference'}
            if line.get('reference'):
                made = extract(line['reference'], 'reference-%d.wav' % index)
                if os.path.isfile(made):
                    row['reference'] = dict(path=made, text=line['reference']['text'])
                elif made == 'reference_short' and job.get('reference'):
                    # the job's reference is used, and the line says so
                    row['reference_short'] = True
                else:
                    raise ValueError(made)
            job['lines'].append(row)
        session = Worker(self.spawn(root, machine, env, descriptor))
        child = session.process
        self.process = child
        result = {}
        lines = []
        write_errors = []

        def submit():
            try:
                child.stdin.write(json.dumps(job, ensure_ascii=True))
                child.stdin.close()
            except Exception as error:
                write_errors.append(error)
        writer = threading.Thread(target=submit, daemon=True)

        def read_rows():
            import queue
            while True:
                try:
                    row = session.rows.get_nowait()
                except queue.Empty:
                    return
                phase = row.get('phase')
                if phase in ('complete', 'failed', 'cancelled'):
                    result.update(row)
                elif phase == 'line':
                    entry = {key: row[key] for key in LINE_KEYS if key in row}
                    if row.get('file'):
                        entry['file'] = '%s/%s/%s' % (OUTPUT, key_id, row['file'])
                    lines.append(entry)
                    self.progress(dict(phase='synthesizing', index=row.get('index'), count=len(job['lines']),
                                       lines=list(lines)))
                elif phase in ('loading', 'loaded', 'synthesizing'):
                    self.progress(dict((k, v) for k, v in row.items() if k in ('phase', 'index', 'count', 'seconds')))
        observed = {}
        try:
            writer.start()
            deadline = time.monotonic() + min(RUN_LIMIT, RUN_SECONDS + LINE_SECONDS * len(job['lines']))
            while child.poll() is None:
                read_rows()
                self.check()
                if write_errors:
                    raise write_errors[0]
                if time.monotonic() > deadline:
                    raise TimeoutError('timeout')
                try:
                    for process in psutil.Process(child.pid).children(recursive=True):
                        observed[process.pid] = process
                except psutil.Error:
                    pass
                self.stop.wait(.25)
            session.reader.join(timeout=5)
            read_rows()
            self.check()
            if session.reader.is_alive() or not result:
                raise RuntimeError('worker_exit')
            if result.get('phase') == 'failed':
                raise RuntimeError(result.get('code') if result.get('code') in KNOWN else 'dub_failed')
            if result.get('phase') == 'cancelled':
                raise InterruptedError('cancelled')
            if child.returncode:
                raise RuntimeError('worker_exit')
            self.progress(dict(phase='complete', lines=lines, backend=result.get('backend'),
                               sample_rate=result.get('sample_rate'),
                               reference_seconds=result.get('reference_seconds'),
                               worker_returncode=child.returncode))
        finally:
            if child.poll() is None:
                processes.stop(child, grace=15)
            child.wait()
            writer.join(timeout=5)
            session.close()
            self.process = None
            for wav in folder.glob('reference*.wav'):
                try:
                    wav.unlink()
                except OSError:
                    pass
            surviving = []
            for process in observed.values():
                try:
                    if process.is_running() and process.status() != 'zombie':
                        surviving.append(process)
                except psutil.NoSuchProcess:
                    pass
            for process in surviving:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            if surviving:
                psutil.wait_procs(surviving, timeout=5)
                self.progress(dict(error='worker_cleanup_failed'))
            # Failed or cancelled runs leave nothing behind, unless the caller keeps the finished lines.
            if self.state.get('phase') != 'complete' and not self.keep:
                shutil.rmtree(folder, ignore_errors=True)


def register():
    import asyncio
    from aiohttp import web
    from server import PromptServer
    import folder_paths
    from ..comfy_bridge import installation
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_dubbing', None) is not None:
        return
    service = Service(installation, folder_paths.get_input_directory)
    server._freevideo_dubbing = service
    token = secrets.token_urlsafe(24)

    @server.routes.get('/freevideo/dubbing')
    async def info(request):
        try:
            return web.json_response(dict(await asyncio.to_thread(service.info, False), token=token))
        except (OSError, ValueError):
            return web.json_response(dict(error='setup_required'), status=400)

    @server.routes.post('/freevideo/dubbing/{action}')
    async def action(request):
        if not secrets.compare_digest(request.headers.get('X-FreeVideo-Dubbing', ''), token):
            return web.json_response(dict(error='reload'), status=403)
        try:
            name = request.match_info['action']
            if request.content_length and request.content_length > 512 * 1024:
                raise ValueError('invalid_request')
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError('invalid_request')
            if name == 'status':
                result = service.status(value.get('job'))
            elif name == 'cancel':
                result = service.cancel(value.get('job'))
            else:
                running, pending = server.prompt_queue.get_current_queue()
                if running or pending:
                    raise ValueError('busy')
                if name == 'dub':
                    await asyncio.to_thread(pack.settle, service.installation()[0])
                result = service.start(name, value)
            return web.json_response(result)
        except (ValueError, OSError) as error:
            return web.json_response(dict(error=str(error) if str(error) in CODES else 'invalid_media'), status=400)
