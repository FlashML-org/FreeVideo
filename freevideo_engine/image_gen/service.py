"""One active image and one waiting job, with a worker leased until idle or video work."""
import atexit
import asyncio
from collections import OrderedDict
from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import threading
import time
import uuid

from . import image_pack
from ..download_settings import observed_speed
from .failure import (Context, ERRORS, FailureError, MESSAGE_ZH, NO_REPORT, STAGES, UNIFIED_MESSAGE_ZH,
                      classify_exception)

BODY_LIMIT = 64 * 1024
CANCEL_GRACE = 5.
MAX_IMAGES = 4
REPORT_LIMIT = 16
HISTORY_NAME = re.compile(r'image-\d{8}-\d{6}-\d{2}\.png')
LEGACY_NAME = re.compile(r'[0-9a-f]{32}\.png')
HISTORY_LIMIT = 500
# The 高清 size is offered from this much card memory; smaller cards keep one size.
HIGH_SIZE_MIN_TOTAL = 6 * 1024 ** 3
INFERENCE_TIMEOUT = 600.
QUEUE_TIMEOUT = 600.
QUEUE_POLL = .1
INVALID_ERRORS = frozenset(('invalid_request', 'invalid_prompt', 'invalid_dimensions', 'invalid_seed',
                            'invalid_media', 'invalid_points', 'too_many_images'))


def comfy_server():
    """ComfyUI's server in this process, or None (tests, the engine's own command line)."""
    try:
        from server import PromptServer
        return PromptServer.instance
    except (ImportError, AttributeError):
        return None


class WorkerIdleExit(RuntimeError):
    """The worker closed itself after its idle timeout before it answered anything."""


FAILURE_KINDS = tuple((name, re.compile(pattern, re.I)) for name, pattern in (
    ('panic', r'\bpanick?ed\b'), ('CUDA error', r'\bcuda\b[^\n]{0,40}\berror\b|\bcublas\w*_status|\bcudnn\w*_status'),
    ('out of memory', r'out of memory|\bOOM\b'), ('MemoryError', r'\bMemoryError\b'),
    ('access violation', r'access violation|segmentation fault|\bSIGSEGV\b'),
    ('DLL load failed', r'DLL load failed|cannot open shared object'), ('ImportError', r'\b(Import|ModuleNotFound)Error\b')))


class WorkerEarlyExit(WorkerIdleExit):
    """The worker process ended with an error before it answered anything: nothing ran."""


class Worker:
    """One reader owns each pipe; stderr is drained without retaining user text."""
    def __init__(self, process):
        self.process = process
        self.rows = queue.Queue()
        self.writer_mutex = threading.Lock()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.error_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.tail = ''
        self.reader.start()
        self.error_reader.start()

    def read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 128 * 1024:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    self.rows.put(row)
        finally:
            self.rows.put(None)

    def read_errors(self):
        # Only the last words are kept, for the report of a worker that ended abnormally.
        for chunk in iter(lambda: self.process.stderr.read(1024), ''):
            self.tail = (self.tail + chunk)[-1500:]

    def send(self, **row):
        with self.writer_mutex:
            self.process.stdin.write(json.dumps(row, ensure_ascii=True) + '\n')
            self.process.stdin.flush()

    def close(self):
        self.reader.join(timeout=5)
        self.error_reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if not stream.closed:
                stream.close()
        crash_file = getattr(self.process, 'crash_file', None)
        if crash_file:
            try:
                os.remove(crash_file)
            except OSError:
                pass


class Service:
    def __init__(self, installation, input_directory, *, video_busy=None, idle_seconds=45.):
        self.installation = installation
        self.input_directory = input_directory
        self.video_busy = video_busy or self._video_busy
        self.idle_seconds = idle_seconds
        self.mutex = threading.RLock()
        self.stop = threading.Event()
        self.session_stop = threading.Event()
        self.requests = queue.Queue()
        self.thread = None
        self.process = None
        self.worker = None
        self.key = None
        self.state = {}
        self._busy = False
        self.closed = False
        self.yielding = False
        self.reports = OrderedDict()
        self.context = None
        self.last_machine = {}
        self.worker_restarts = 0
        self.worker_started = False
        self.queued = None
        self.queue_thread = None
        self.queue_wake = threading.Event()
        self.finished = OrderedDict()
        self.pack_installer = None
        self.segment_installer = None
        self.segment_worker = None
        self.segmenting = False
        self.segment_stop = threading.Event()
        self.segment_done = threading.Event()
        self.segment_done.set()
        atexit.register(self.close)

    @staticmethod
    def _video_busy():
        from ..resident_process import OWNER
        return OWNER.active

    def busy(self):
        return self._busy or self.segmenting

    def info(self):
        engine_root, machine = self.installation()
        spec = image_pack.catalog()
        ready = image_pack.ready(machine['model_root'])
        return dict(ready=ready, model=spec['repo'],
                    checking=not ready and image_pack.checking(machine['model_root']),
                    downloading=not ready and image_pack.downloading(machine['model_root'], engine_root),
                    bytes=sum(row['bytes'] for row in spec['groups']['image']), busy=self.busy(),
                    max_images=MAX_IMAGES, sizes=self.size_tiers(machine), install=self.pack_state(ready),
                    segment=dict(ready=image_pack.ready(machine['model_root'], group='segment'),
                                 max_points=16, device='cpu',
                                 install=self.segment_installer.state() if self.segment_installer else None),
                    bytes_per_second=None if ready else observed_speed(engine_root),
                    **({'unified_memory': True} if machine.get('device_backend') == 'mps' else {}))

    @staticmethod
    def unified_total(machine):
        """Total memory of a Mac whose Apple GPU shares it; None for a separate card."""
        if machine.get('device_backend') != 'mps':
            return None
        from ..system import system_memory
        try:
            return int(system_memory()['total_bytes'])
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def size_tiers(self, machine):
        """The sizes this card offers: the standard one always, the high one only with enough card memory."""
        total = None
        unified = self.unified_total(machine)
        if unified is not None:
            from .image_policy import unified_budget
            total = unified_budget(unified)
        elif machine.get('device_backend', 'cuda') == 'cuda':
            from ..background_room import Gpu
            from ..gpu_budget import open_bounded

            def read():
                gpu = Gpu(machine.get('gpu_uuid'))
                try:
                    memory = gpu.memory()
                    return memory[1] if memory else None
                finally:
                    gpu.close()
            try:
                total = open_bounded(read, .5)
            except (OSError, RuntimeError, AttributeError, ValueError):
                total = None
        return ['standard', 'high'] if total and total >= HIGH_SIZE_MIN_TOTAL else ['standard']

    def pack_state(self, ready):
        """Progress of the download this page started, in both languages for a failure."""
        installer = self.pack_installer
        if ready or installer is None:
            return None
        from ..failure_details import image_pack_failure
        value = installer.state()
        row = dict(state=value['state'], done=value.get('done', 0), total=value.get('total', 0),
                   bytes_per_second=round(value.get('bytes_per_second', 0)))
        if value['state'] == 'failed':
            row.update(reason=value.get('reason'), message_en=image_pack_failure(value.get('reason'))['detail'],
                       message_zh=image_pack_failure(value.get('reason'), zh=True)['detail'])
        return row

    def pack_action(self, action, *, group='image'):
        """Start or pause the image model download from the page; it yields to a running video."""
        engine_root, machine = self.installation()
        root = machine['model_root']
        with self.mutex:
            attribute = 'segment_installer' if group == 'segment' else 'pack_installer'
            installer = getattr(self, attribute)
            if action == 'cancel':
                if installer is not None:
                    installer.cancel()
            elif not image_pack.ready(root, group=group) and not image_pack.downloading(root, engine_root):
                if installer is None or installer.root != root or installer.engine_root != engine_root:
                    options = dict(group=group) if group != 'image' else {}
                    installer = image_pack.Installer(root, engine_root=engine_root, busy=self.video_busy, **options)
                    setattr(self, attribute, installer)
                installer.start('install', background=True)
        return self.info()

    def media_path(self, name):
        """Shared input-name and symlink policy for generation and point selection."""
        directory = Path(self.input_directory()).resolve()
        folder, _, leaf = name.rpartition('/') if isinstance(name, str) else ('', '', '')
        if (not isinstance(name, str) or not leaf or name.startswith('/') or leaf in ('.', '..')
                or folder not in ('', 'freevideo-image')
                or any(char in name for char in ('\\', ':', '\0'))):
            raise ValueError('invalid_media')
        parent = directory / folder if folder else directory
        path = parent / leaf
        if (parent.is_symlink() or path.is_symlink() or parent.resolve() != parent
                or path.resolve().parent != parent or not path.is_file()):
            raise ValueError('invalid_media')
        return str(path)

    def output_directory(self):
        directory = Path(self.input_directory()).resolve()
        output = directory / 'freevideo-image'
        if output.is_symlink() or output.resolve().parent != directory:
            raise ValueError('invalid_media')
        output.mkdir(exist_ok=True)
        return output

    def output_path(self, named=False):
        """A new picture's path. Generated pictures get a readable name that the history lists."""
        output = self.output_directory()
        if not named:
            return str(output / (uuid.uuid4().hex + '.png'))
        stamp = time.strftime('image-%Y%m%d-%H%M%S')
        for number in range(1, 100):
            path = output / ('%s-%02d.png' % (stamp, number))
            if not path.exists():
                return str(path)
        raise ValueError('invalid_media')

    def legacy_picture(self, path):
        """A picture from before generated names: a bare random name whose PNG is colour, since masks are grey."""
        if not LEGACY_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            return False
        try:
            with path.open('rb') as handle:
                head = handle.read(26)
        except OSError:
            return False
        return head[:8] == b'\x89PNG\r\n\x1a\n' and head[25] in (2, 6)

    def history(self):
        """Every picture generated here, newest first; the folder is the record, so it outlives sessions."""
        output = self.output_directory()
        named, older = [], []
        for entry in output.iterdir():
            if HISTORY_NAME.fullmatch(entry.name) and entry.is_file() and not entry.is_symlink():
                named.append(entry.name)
            elif self.legacy_picture(entry):
                try:
                    older.append((entry.stat().st_mtime, entry.name))
                except OSError:
                    pass
        names = sorted(named, reverse=True) + [name for _, name in sorted(older, reverse=True)]
        return dict(items=[dict(file='freevideo-image/' + name, name=name) for name in names[:HISTORY_LIMIT]])

    def history_path(self, name):
        """The picture a history name stands for; ValueError when it is none of ours, missing when it is already gone."""
        leaf = name.rpartition('/')[2] if isinstance(name, str) else ''
        if not (HISTORY_NAME.fullmatch(leaf) or LEGACY_NAME.fullmatch(leaf)) or name != 'freevideo-image/' + leaf:
            raise ValueError('invalid_media')
        path = self.output_directory() / leaf
        if path.is_symlink() or (path.exists() and not path.is_file()) or (
                LEGACY_NAME.fullmatch(leaf) and path.is_file() and not self.legacy_picture(path)):
            raise ValueError('invalid_media')
        return path

    def history_delete(self, name):
        path = self.history_path(name)
        if not path.is_file():
            raise ValueError('invalid_media')
        path.unlink()
        return self.history()

    def history_delete_many(self, names):
        """Delete each picture on its own; the ones that stay are listed with why (busy, missing, invalid)."""
        if not isinstance(names, list) or len(names) > HISTORY_LIMIT:
            raise ValueError('invalid_request')
        failed = []
        for name in dict.fromkeys(names):
            try:
                path = self.history_path(name)
            except ValueError:
                failed.append(dict(file=name if isinstance(name, str) else '', reason='invalid'))
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                failed.append(dict(file=name, reason='missing'))
            except OSError:
                failed.append(dict(file=name, reason='busy'))
        return dict(self.history(), failed=failed)

    def history_open(self):
        """Show the folder in the file manager of this machine."""
        folder = str(self.output_directory())
        if os.name == 'nt':
            # The shell can block for good without a desktop session (SSH, service); never wait on it here.
            failure = []

            def show():
                try:
                    os.startfile(folder)
                except OSError as error:
                    failure.append(error)
            thread = threading.Thread(target=show, name='history-open', daemon=True)
            thread.start()
            thread.join(2)
            if failure:
                raise failure[0]
        else:
            from .. import processes
            processes.popen(['xdg-open', folder], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
        return dict(opened=True)

    def close_segment_worker(self):
        from .. import processes
        with self.mutex:
            session, self.segment_worker = self.segment_worker, None
        if session is not None:
            processes.stop(session.process, grace=1)
            session.process.wait()
            session.close()

    def spawn_segment_worker(self, root, machine):
        from .. import processes
        row = image_pack.rows(group='segment')[0]
        command = processes.module_command('freevideo_engine.image_gen.segment_worker',
                    image_pack.target(row, machine['model_root']), '--idle-seconds', 45.)
        command[0] = machine['comfy_python']
        env = dict(self.environment(root), CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='',
                   OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
        return Worker(processes.popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                      supervise=True, start_new_session=True))

    def segment(self, value):
        """Immediate admission, no waiting queue. The HTTP caller runs this off its event loop."""
        from .segment import validate_points
        from ..locking import runtime_lock
        if not isinstance(value, dict):
            raise ValueError('invalid_request')
        validate_points(value.get('points'))
        path = self.media_path(value.get('image'))
        with self.mutex:
            if self.closed:
                raise ValueError('closed')
            if self.yielding or self.video_busy():
                raise ValueError('busy_video')
            if self.busy() or self.queued is not None:
                raise ValueError('busy')
            try:
                root, machine = self.installation()
            except (OSError, ValueError):
                raise ValueError('segment_not_installed') from None
            try:
                if not image_pack.ready(machine['model_root'], group='segment'):
                    raise ValueError('segment_not_installed')
            except (OSError, image_pack.PackError):
                raise ValueError('segment_not_installed') from None
            self.segmenting = True
            self.segment_stop.clear()
            self.segment_done.clear()
            # Reap the idle Qwen worker before taking its runtime lease.
            self.session_stop.set()
            thread = self.thread
        try:
            if thread is not None:
                thread.join()
            output = self.output_path()
            with runtime_lock(Path(root) / 'engine.lock', inherit=False):
                for attempt in range(2):
                    with self.mutex:
                        if self.closed or self.segment_stop.is_set() or self.yielding or self.video_busy():
                            raise ValueError('busy_video')
                        session = self.segment_worker
                        if session is not None and session.process.poll() is not None:
                            self.close_segment_worker()
                            session = None
                        if session is None:
                            session = self.segment_worker = self.spawn_segment_worker(root, machine)
                    accepted = False
                    deadline = time.monotonic() + INFERENCE_TIMEOUT
                    try:
                        session.send(action='generate', image=path, points=value['points'], path=output)
                        while True:
                            if self.closed or self.segment_stop.is_set() or self.yielding or self.video_busy():
                                raise ValueError('busy_video')
                            if time.monotonic() >= deadline:
                                raise ValueError('timeout')
                            try:
                                row = session.rows.get(timeout=.1)
                            except queue.Empty:
                                continue
                            if row is None:
                                raise WorkerIdleExit()
                            if row.get('phase') == 'accepted':
                                accepted = True
                            elif row.get('phase') == 'result':
                                if row.get('path') != output or not Path(output).is_file():
                                    raise ValueError('worker_exit')
                                return dict(mask='freevideo-image/' + Path(output).name)
                            elif row.get('phase') == 'error':
                                raise ValueError(row['code'] if row.get('code') in ERRORS else 'generation_failed')
                    except (BrokenPipeError, WorkerIdleExit):
                        self.close_segment_worker()
                        if accepted or attempt:
                            raise ValueError('worker_exit') from None
        except BlockingIOError:
            raise ValueError('busy') from None
        except Exception:
            self.close_segment_worker()
            if self.segment_stop.is_set():
                raise ValueError('busy_video') from None
            raise
        finally:
            with self.mutex:
                self.segmenting = False
                self.segment_done.set()
                self.queue_wake.set()

    def request(self, value):
        if not isinstance(value, dict):
            raise ValueError('invalid_request')
        prompt = value.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
            raise ValueError('invalid_prompt')
        dimensions = [value.get(name) for name in ('width', 'height')]
        if any(type(size) is not int or not 256 <= size <= 2048 for size in dimensions):
            raise ValueError('invalid_dimensions')
        seed = value.get('seed')
        if seed is not None and (type(seed) is not int or not -2**63 <= seed < 2**64):
            raise ValueError('invalid_seed')
        images = value.get('images')
        if images is None:
            images = []
        if not isinstance(images, list):
            raise ValueError('invalid_media')
        if len(images) > MAX_IMAGES:
            raise ValueError('too_many_images')
        paths = [self.media_path(name) for name in images]
        mask = value.get('mask')
        mask_path = None
        if mask is not None:
            # The region to change: a mask drawn on the picture being edited, the first one.
            if not paths:
                raise ValueError('invalid_media')
            mask_path = self.media_path(mask)
            from PIL import Image
            try:
                with Image.open(paths[0]) as picture, Image.open(mask_path) as drawn:
                    if drawn.size != picture.size:
                        raise ValueError('invalid_media')
            except OSError:
                raise ValueError('invalid_media') from None
        cast_view = value.get('cast_view')
        if cast_view is not None and (cast_view != 'back' or len(paths) != 1 or mask_path is not None):
            raise ValueError('invalid_request')
        return dict(prompt=prompt, width=dimensions[0] // 32 * 32, height=dimensions[1] // 32 * 32,
                    steps=8, seed=seed, images=paths, **(dict(mask=mask_path) if mask_path else {}),
                    **(dict(cast_view='back') if cast_view == 'back' else {}), path=self.output_path(named=True))

    def start(self, action, value):
        if action not in ('generate', 'compose'):
            raise ValueError('invalid_request')
        context = Context(action, value)
        context.note_start()
        with self.mutex:
            if self.closed:
                raise ValueError('closed')
            if self.segmenting:
                raise ValueError('busy')
            if self.queued is not None:
                raise ValueError('busy')
            if self.yielding:
                context.set_machine(self.last_machine)
                raise FailureError(self.failed('busy_video', context, video_busy=True))
            video = self.video_busy()
            waiting = (video or self.busy() or self.thread is not None
                       and self.thread.is_alive() and self.session_stop.is_set())
            raw = deepcopy(value)
            root, machine, prepared = self.prepare(action, raw, context, waiting=waiting)
            key = secrets.token_urlsafe(24)
            if waiting:
                self.queued = dict(key=key, action=action, value=raw, context=context,
                                   state=dict(phase='queued', queued_behind='video' if video else 'image'),
                                   video_since=time.monotonic() if video else None)
                if self.queue_thread is None or not self.queue_thread.is_alive():
                    self.queue_thread = threading.Thread(target=self.wait_queued,
                                                        name='freevideo-image-queue', daemon=True)
                    self.queue_thread.start()
            else:
                self.begin(key, root, machine, prepared, context)
            return dict(job=key)

    def prepare(self, action, value, context, *, waiting=False):
        """Validate at admission and again at dispatch, against the original request."""
        try:
            root, machine = self.installation()
        except (OSError, ValueError) as error:
            raise FailureError(self.failed('pack_missing', context, error=error)) from None
        context.set_machine(machine)
        self.last_machine = machine
        try:
            if not image_pack.ready(machine['model_root']):
                raise ValueError('pack_missing')
            from .image_policy import UNIFIED_MIN_TOTAL
            unified = self.unified_total(machine)
            if unified is not None and unified < UNIFIED_MIN_TOTAL:
                raise ValueError('small_memory')
            requested = {name: value.get(name) for name in ('width', 'height')} if isinstance(value, dict) else {}
            if action == 'compose':
                from . import compose
                value = compose.build(value, self.input_directory())
            value = self.request(value)
            value['kind'] = context.kind
            context.update_request(value)
            context.plan = self.preflight(machine, value, fit=not waiting)
            # Live free memory while another job owns the GPU is advisory. Keep
            # the original request for dispatch, when the owner has released it.
            if not waiting and context.plan is not None and not context.plan['feasible']:
                raise ValueError('no_memory')
            if 'fitted' in value:
                value['fitted']['requested'] = requested
                context.update_request(value)
            return root, machine, value
        except ValueError as error:
            if str(error) in INVALID_ERRORS:
                raise
            code = str(error) if str(error) in ERRORS else classify_exception(error)
            raise FailureError(self.failed(code, context, error=error)) from None
        except Exception as error:
            raise FailureError(self.failed(classify_exception(error), context, error=error)) from None

    def remember(self, key, state):
        self.finished[key] = deepcopy(state)
        while len(self.finished) > REPORT_LIMIT:
            self.finished.popitem(last=False)

    def begin(self, key, root, machine, value, context):
        if self.key is not None:
            self.remember(self.key, self.state)
        self.key = key
        self.stop.clear()
        self.state = dict(phase='starting', stage='start')
        if 'fitted' in value:
            self.state['fitted'] = deepcopy(value['fitted'])
        self.context = context
        self._busy = True
        if self.thread is None or not self.thread.is_alive():
            self.session_stop = threading.Event()
            self.requests = queue.Queue()
            self.thread = threading.Thread(target=self.work, args=(root, machine),
                                           name='freevideo-image-gen', daemon=True)
            self.requests.put(value)
            self.thread.start()
        else:
            self.requests.put(value)

    def discard_queued(self, state):
        self.remember(self.queued['key'], state)
        self.queued = None
        self.queue_wake.set()

    def wait_queued(self):
        while True:
            with self.mutex:
                pending = self.queued
                if pending is None:
                    # Clearing under the lock lets a new admission start a reader.
                    self.queue_thread = None
                    return
                if self.closed:
                    self.discard_queued(dict(phase='cancelled'))
                    continue
                try:
                    video = self.yielding or self.video_busy()
                except Exception as error:
                    self.discard_queued(self.failed(classify_exception(error), pending['context'], error=error))
                    continue
                pending['state']['queued_behind'] = 'video' if video else 'image'
                if video:
                    if pending['video_since'] is None:
                        pending['video_since'] = time.monotonic()
                    if time.monotonic() - pending['video_since'] >= QUEUE_TIMEOUT:
                        state = self.failed('timeout', pending['context'],
                                            error=TimeoutError('video queue timeout'), video_busy=True)
                        self.discard_queued(state)
                        continue
                else:
                    pending['video_since'] = None
                    stopping = (self.thread is not None and self.thread.is_alive()
                                and self.session_stop.is_set())
                    if not self.busy() and not stopping:
                        try:
                            root, machine, value = self.prepare(pending['action'], pending['value'],
                                                                pending['context'])
                        except FailureError as error:
                            self.discard_queued(error.status)
                        except Exception as error:
                            # A previously valid reference may disappear while waiting; that keeps its
                            # own reason. Other settings were already checked, so a change there is a fault.
                            code = (str(error) if str(error) == 'invalid_media'
                                    else 'generation_failed' if str(error) in INVALID_ERRORS
                                    else classify_exception(error))
                            self.discard_queued(self.failed(code, pending['context'], error=error))
                        else:
                            self.queued = None
                            self.begin(pending['key'], root, machine, value, pending['context'])
                        continue
                self.queue_wake.clear()
            self.queue_wake.wait(QUEUE_POLL)

    def memory_budget(self, machine):
        """Bound the driver query, without importing Torch or starting a worker."""
        unified = self.unified_total(machine)
        if unified is not None:
            from .image_policy import unified_budget
            return unified_budget(unified)
        if machine.get('device_backend', 'cuda') != 'cuda':
            return None
        from ..background_room import Gpu, family
        from ..background_compile import COMPILE
        from ..gpu_budget import open_bounded
        from ..resident_process import OWNER
        from ..comfy_residency import native_reclaimable
        # ComfyUI's own idle models in this process: work() unloads them before the worker starts.
        native = native_reclaimable(comfy_server())

        def read():
            gpu = Gpu(machine.get('gpu_uuid'))
            try:
                memory = gpu.memory()
                if memory is None:
                    return None
                used, total = memory
                if not total or not 0 <= used <= total:
                    return None
                # work() releases idle video/compile models; the image worker
                # reuses its own allocator. Never credit an unrelated process.
                owned = set()
                for process in (self.process, None if OWNER.active else OWNER.process, COMPILE.process):
                    if process is not None and process.poll() is None:
                        owned.update(family(process.pid))
                rows = gpu.processes() if owned else {}
                if owned and (rows is None or not owned.intersection(rows)
                              or any(rows.get(pid) is None for pid in owned if pid in rows)):
                    # An unreported owned pool must not cause a false refusal.
                    # The worker still checks live capacity before inference.
                    return total
                credit = sum(size for pid, size in rows.items() if pid in owned)
                return min(total, total - used + credit + native)
            finally:
                gpu.close()

        try:
            return open_bounded(read, .5)
        except (OSError, RuntimeError, AttributeError, ValueError):
            return None

    def preflight(self, machine, value, *, fit=True):
        budget = self.memory_budget(machine)
        if budget is None:
            return
        from PIL import Image
        from .image_policy import plan_image
        reference_pixels = 0
        for path in value['images']:
            with Image.open(path) as image:
                scale = (1024**2 / (image.width * image.height)) ** .5
                reference_pixels += round(image.width * scale) * round(image.height * scale)
        plan = plan_image(gpu_budget_bytes=budget, width=value['width'], height=value['height'],
                          reference_pixels=reference_pixels)
        if fit and not plan.feasible:
            width, height = value['width'], value['height']
            landscape = width >= height
            long, short = max(width, height), min(width, height)
            # At most 48 probes for the accepted 2048-pixel limit. Round the
            # proportional short side to its nearest 32-pixel grid point.
            for side in range(long - 32, 511, -32):
                other = (short * side + long * 16) // (long * 32) * 32
                if other < 512:
                    break
                fitted_width, fitted_height = (side, other) if landscape else (other, side)
                candidate = plan_image(gpu_budget_bytes=budget, width=fitted_width, height=fitted_height,
                                       reference_pixels=reference_pixels)
                if candidate.feasible:
                    value.update(width=fitted_width, height=fitted_height,
                                 fitted=dict(width=fitted_width, height=fitted_height,
                                             requested=dict(width=width, height=height)))
                    return candidate.to_dict()
        return plan.to_dict()

    def failed(self, code, context, *, row=None, error=None, video_busy=None):
        """One safe failure status, used by admission, worker, HTTP and Agent callers."""
        code = code if isinstance(code, str) and code in ERRORS else 'generation_failed'
        if code == 'cancelled':
            return dict(phase='cancelled')
        stage = row.get('stage') if isinstance(row, dict) else None
        stage = stage if stage in STAGES else context.timing.stage
        result = dict(phase='failed', error=code, report_id=None, stage=stage)
        if code in MESSAGE_ZH:
            result['message_zh'] = MESSAGE_ZH[code]
            if context.machine.get('device_backend') == 'mps' and code in UNIFIED_MESSAGE_ZH:
                result['message_zh'] = UNIFIED_MESSAGE_ZH[code]
        if code not in NO_REPORT:
            if video_busy is None:
                try:
                    video_busy = self.yielding or self.video_busy()
                except Exception:
                    video_busy = False
            if context.pack is None and context.machine.get('model_root'):
                try:
                    context.pack = image_pack.status(context.machine['model_root'])
                except Exception:
                    # A missing/broken pack must not prevent reporting the original error.
                    pass
            report = context.report(code, row=row, error=error, video_busy=video_busy,
                                    worker_restarts=self.worker_restarts)
            with self.mutex:
                report_id = uuid.uuid4().hex
                self.reports[report_id] = report
                while len(self.reports) > REPORT_LIMIT:
                    self.reports.popitem(last=False)
            result['report_id'] = report_id
        return result

    def report(self, report_id):
        if (not isinstance(report_id, str) or len(report_id) != 32
                or any(char not in '0123456789abcdef' for char in report_id)):
            return None
        with self.mutex:
            report = self.reports.get(report_id)
            # Callers cannot mutate a retained report through an in-process reference.
            return json.loads(json.dumps(report, allow_nan=False)) if report is not None else None

    def status(self, key):
        with self.mutex:
            if isinstance(key, str):
                def matches(candidate):
                    return candidate is not None and secrets.compare_digest(key.encode('utf-8'), candidate.encode('utf-8'))
                if matches(self.key):
                    return deepcopy(self.state)
                if self.queued is not None and matches(self.queued['key']):
                    return deepcopy(self.queued['state'])
                for candidate, state in self.finished.items():
                    if matches(candidate):
                        return deepcopy(state)
            raise ValueError('unknown_job')

    def cancel(self, key):
        with self.mutex:
            row = self.status(key)
            if row['phase'] == 'queued':
                self.discard_queued(dict(phase='cancelled'))
                return dict(phase='cancelled')
            if key != self.key or not self.busy():
                return row
            self.stop.set()
            self.state['phase'] = 'cancelling'
            return dict(phase='cancelling')

    def stop_for_generation(self, only_idle=False):
        """Reap even an in-flight spawn before video acquires the runtime lease.

        ``only_idle`` is for other users of the lease (the prompt model): they get it
        back from an idle worker but never cut off an image that is being made.
        """
        with self.mutex:
            if only_idle and (self.busy() or (self.segment_worker is None and
                    (self.thread is None or not self.thread.is_alive()))):
                return False
            self.yielding = True
            self.segment_stop.set()
            self.stop.set()
            self.session_stop.set()
            if self.queued is not None:
                self.queued['state']['queued_behind'] = 'video'
                if self.queued['video_since'] is None:
                    self.queued['video_since'] = time.monotonic()
            thread = self.thread
        try:
            self.close_segment_worker()
            self.segment_done.wait()
            if thread is not None and thread is not threading.current_thread():
                thread.join()
        finally:
            with self.mutex:
                self.yielding = False
                self.queue_wake.set()
        return True

    def close(self):
        with self.mutex:
            self.closed = True
            if self.queued is not None:
                self.discard_queued(dict(phase='cancelled'))
            queue_thread = self.queue_thread
        self.stop_for_generation()
        if queue_thread is not None and queue_thread is not threading.current_thread():
            queue_thread.join()

    def environment(self, root):
        from ..comfy_bridge import source_root
        from ..comfy_environment import isolated_environment
        from .. import processes, resident_process
        env = isolated_environment(root, source_root())
        for name in ('FREEVIDEO_RUNTIME_LOCK_FD', processes.HANDLE_ENV, resident_process.ENV,
                     'FREEVIDEO_PROMPT_PRESTART_WORKER'):
            env.pop(name, None)
        return env

    def spawn(self, root, machine, env, descriptor):
        from .. import processes
        command = processes.module_command('freevideo_engine.image_gen.worker', image_pack.base(machine['model_root']),
                                           '--device', machine.get('device_backend', 'cuda'),
                                           '--idle-seconds', self.idle_seconds)
        command[0] = machine['comfy_python']
        from ..exit_diagnostics import CRASH_FILE_ENV, new_crash_file
        crash_file = None
        try:
            crash_file = new_crash_file()
        except OSError:
            pass
        env = dict(env, FREEVIDEO_RUNTIME_LOCK_FD=str(descriptor))
        if crash_file:
            env[CRASH_FILE_ENV] = crash_file
        process = processes.popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                  pass_fds=(descriptor,), supervise=True, start_new_session=True)
        process.crash_file = crash_file
        return process

    def work(self, root, machine):
        from ..locking import runtime_lock
        from .. import processes, resident_process
        session = None
        failure = 'worker_exit'
        failure_error = None
        try:
            self.close_segment_worker()
            from ..background_compile import COMPILE
            from ..encoder_prewarm import IDLE
            COMPILE.stop()
            if self.video_busy() or resident_process.OWNER.active:
                raise BlockingIOError('busy_video')
            resident_process.OWNER.stop_prewarm()
            IDLE.stop()
            from ..comfy_residency import release_native
            released = release_native(comfy_server())
            if released is not None:
                logging.info('[FreeVideo] Unloaded ComfyUI models before the picture: %s', released)
                with self.mutex:
                    if self.context is not None:
                        self.context.native_release = released
            with runtime_lock(Path(root) / 'engine.lock', inherit=False) as descriptor:
                try:
                    if self.session_stop.is_set():
                        return
                    env = self.environment(root)
                    owner = resident_process.OWNER
                    if owner.process is not None and owner.process.poll() is None and owner.endpoint:
                        env[resident_process.ENV] = str(owner.endpoint)
                        try:
                            resident_process.release_idle_cache(env, descriptor)
                            resident_process.OWNER.record(action='released', reason='image_model')
                        except resident_process.SessionRestarted:
                            pass
                        env.pop(resident_process.ENV, None)
                    if self.session_stop.is_set():
                        return
                    if self.video_busy():
                        raise BlockingIOError('busy_video')
                    def respawn():
                        nonlocal session
                        session.process.wait()
                        session.close()
                        session = Worker(self.spawn(root, machine, env, descriptor))
                        with self.mutex:
                            self.worker = session
                            self.process = session.process
                            self.worker_restarts += 1

                    session = Worker(self.spawn(root, machine, env, descriptor))
                    with self.mutex:
                        self.worker = session
                        self.process = session.process
                        self.worker_restarts += int(self.worker_started)
                        self.worker_started = True
                    while not self.session_stop.is_set():
                        try:
                            value = self.requests.get(timeout=.05)
                        except queue.Empty:
                            with self.mutex:
                                if session.process.poll() is not None and self.requests.empty():
                                    self.session_stop.set()
                                    break
                            continue
                        if session.process.poll() == 0:
                            respawn()
                        try:
                            self.infer(session, value)
                        except WorkerIdleExit as error:
                            # The request reached a worker that was already unloading after
                            # its idle timeout and was never read. Nothing ran: send it to a
                            # fresh worker once; a second exit is reported as a failure.
                            if self.stop.is_set() or self.session_stop.is_set():
                                raise
                            earlier = str(error)
                            respawn()
                            try:
                                self.infer(session, value)
                            except RuntimeError as second:
                                if earlier.startswith('worker_exit (') and str(second).startswith('worker_exit'):
                                    combined = RuntimeError('%s; first try: %s' % (second, earlier[len('worker_exit '):]))
                                    reading = getattr(second, 'exit_reading', None)
                                    if isinstance(reading, dict):
                                        combined.exit_reading = dict(reading, earlier_exit_code=getattr(error, 'exit_code', None))
                                    raise combined from None
                                raise
                finally:
                    # Keep the parent's lease until the whole owned process tree exits.
                    if session is not None:
                        try:
                            if session.process.poll() is None:
                                session.send(action='close')
                                session.process.wait(timeout=5)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                        processes.stop(session.process, grace=5)
                        session.process.wait()
                        session.close()
        except BlockingIOError as error:
            failure = 'busy_video'
            failure_error = error
        except TimeoutError as error:
            failure = 'timeout'
            failure_error = error
        except Exception as error:
            failure = 'worker_exit'
            failure_error = error
        finally:
            with self.mutex:
                self.process = self.worker = None
                self.session_stop.set()
                if self._busy:
                    context = self.context or Context('generate', {})
                    fitted = self.state.get('fitted')
                    self.state = (dict(phase='cancelled') if self.stop.is_set()
                                  else self.failed(failure, context, error=failure_error,
                                                   video_busy=failure == 'busy_video'))
                    if fitted is not None:
                        self.state['fitted'] = fitted
                    self._busy = False
                    self.context = None
                self.queue_wake.set()

    def shrink(self, value, row, context):
        """After a memory error while working on the picture, step the size down for one more try.

        The ladder is the one start-up fitting uses (32-pixel grid, 512 pixels at the least);
        the step grows with the size so a big canvas needs few tries. False at the smallest size.
        """
        if (row.get('code') != 'no_memory' or row.get('stage') in ('load', 'text', 'encode')
                or self.stop.is_set() or self.session_stop.is_set()):
            return False
        width, height = value['width'], value['height']
        long, short = max(width, height), min(width, height)
        # A worker that had to load the model again costs seconds per try; take bigger steps then.
        side = long - max(32, long // (8 if row.get('reloaded') else 16) // 32 * 32)

        def fitted(length):
            return (short * length + long * 16) // (long * 32) * 32

        if side < 512 or fitted(side) < 512:
            # The step would jump past the least size: try that size itself before giving up.
            side = next((n for n in range(512, long, 32) if fitted(n) >= 512), None)
            if side is None:
                return False
        other = fitted(side)
        value['width'], value['height'] = (side, other) if width >= height else (other, side)
        with self.mutex:
            fitted = self.state.get('fitted') or {}
            requested = fitted.get('requested') or dict(width=width, height=height)
            self.state['fitted'] = dict(width=value['width'], height=value['height'], requested=requested)
            self.state.update(phase='progress', stage='start', fraction=0)
        context.update_request(value)
        return True

    @staticmethod
    def worker_gone(session, heard, requested_stop=False):
        """The error for a worker pipe that ended; it carries the exit code for the report."""
        error = Service.worker_gone_error(session, heard)
        code = getattr(error, 'exit_code', None)
        if code is not None:
            # The worker cannot report its memory once it is gone: read this process's view now.
            from ..exit_diagnostics import exit_reading, memory_sample, read_crash_stack
            reading = dict(exit_reading(code, requested_stop=requested_stop), memory=memory_sample())
            if reading.get('exit_class') == 'native_crash':
                stack = read_crash_stack(getattr(session.process, 'crash_file', None))
                if stack:
                    reading['native_stack'] = stack
            error.exit_reading = reading
        return error

    @staticmethod
    def worker_gone_error(session, heard):
        try:
            code = session.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            code = None
        if code == 0 and not heard:
            idle = WorkerIdleExit('worker_exit (exit code 0, before any answer)')
            idle.exit_code = 0
            return idle
        if code is None:
            return RuntimeError('worker_exit')
        session.error_reader.join(timeout=1)
        # What the worker wrote can hold a prompt or a user's folder: only a fixed word for
        # the kind of failure leaves this function, never any of the text.
        kind = next((name for name, pattern in FAILURE_KINDS if pattern.search(session.tail)), '')
        session.tail = ''
        message = 'worker_exit (exit code %d%s)' % (code, ', ' + kind if kind else '')
        # A worker that ended with an error before answering never ran the request; one
        # fresh worker gets it, and a second exit is reported with its own last words.
        error = (WorkerEarlyExit if not heard and code > 0 else RuntimeError)(message)
        error.exit_code = code
        return error

    def infer(self, session, value):
        context = self.context or Context('generate', value)
        heard = False
        try:
            session.send(action='generate', **value)
        except OSError:
            raise self.worker_gone(session, heard, self.stop.is_set())
        cancelled_at = None
        deadline = time.monotonic() + INFERENCE_TIMEOUT
        while True:
            if self.stop.is_set() and cancelled_at is None:
                session.send(action='cancel')
                cancelled_at = time.monotonic()
            if cancelled_at is not None and time.monotonic() - cancelled_at > CANCEL_GRACE:
                raise TimeoutError('cancelled')
            if self.session_stop.is_set():
                return
            if time.monotonic() > deadline:
                raise TimeoutError('timeout')
            try:
                row = session.rows.get(timeout=.05)
            except queue.Empty:
                continue
            if row is None:
                raise self.worker_gone(session, heard, self.stop.is_set())
            heard = True
            phase = row.get('phase')
            if phase == 'progress':
                stage, fraction = row.get('stage'), row.get('fraction')
                if (stage in STAGES and type(fraction) in (int, float)
                        and math.isfinite(fraction) and 0 <= fraction <= 1):
                    # The limit is for a worker that stops reporting, not for a slow first load.
                    deadline = time.monotonic() + INFERENCE_TIMEOUT
                    with self.mutex:
                        if self.state.get('stage') == stage and type(self.state.get('fraction')) in (int, float):
                            fraction = max(fraction, self.state['fraction'])
                        context.timing.enter(stage)
                        self.state.update(phase='cancelling' if self.stop.is_set() else 'progress',
                                          stage=stage, fraction=fraction)
            elif phase in ('result', 'error'):
                if self.stop.is_set() or row.get('code') == 'cancelled':
                    result = dict(phase='cancelled')
                elif phase == 'error' and self.shrink(value, row, context):
                    # The memory estimate was optimistic for this machine: same worker, next size down.
                    session.send(action='generate', **value)
                    deadline = time.monotonic() + INFERENCE_TIMEOUT
                    continue
                elif phase == 'error':
                    code = row.get('code')
                    result = self.failed(code, context, row=row)
                else:
                    path = Path(value['path'])
                    if row.get('path') != str(path) or path.is_symlink() or not path.is_file():
                        raise RuntimeError('worker_exit')
                    from PIL import Image
                    with Image.open(path) as image:
                        if image.format != 'PNG' or image.size != (value['width'], value['height']):
                            raise RuntimeError('worker_exit')
                        image.verify()
                    seconds = row.get('seconds')
                    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                        raise RuntimeError('worker_exit')
                    result = dict(phase='complete', file='freevideo-image/' + path.name,
                                  seconds=seconds, plan=row.get('plan'))
                    if isinstance(row.get('attention'), dict):
                        result['attention'] = row['attention']
                with self.mutex:
                    if 'fitted' in self.state:
                        result['fitted'] = self.state['fitted']
                    self.state = result
                    self._busy = False
                    self.context = None
                    self.queue_wake.set()
                return


def stop_prestart_for_generation(server):
    service = getattr(server, '_freevideo_image_gen', None)
    if service is not None:
        service.stop_for_generation()


def get_service(server=None):
    """The image service of this server, for callers in the same process (the Agent).

    They call ``start('generate', {...})``, ``status(job)``, ``cancel(job)`` and
    ``info()`` directly with the same arguments, results and error codes as the
    HTTP routes; only the page token is not needed. None before ``register()``.
    """
    if server is None:
        from server import PromptServer
        server = PromptServer.instance
    return getattr(server, '_freevideo_image_gen', None)


def release_idle_worker(server):
    """Hand engine.lock back from a worker that is only waiting for its idle exit."""
    service = getattr(server, '_freevideo_image_gen', None)
    if service is not None:
        service.stop_for_generation(only_idle=True)


def register():
    from aiohttp import web
    from server import PromptServer
    import folder_paths
    from ..comfy_bridge import installation
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_image_gen', None) is not None:
        return

    def video_busy():
        running, pending = server.prompt_queue.get_current_queue()
        return bool(running or pending) or Service._video_busy()

    service = Service(installation, folder_paths.get_input_directory, video_busy=video_busy)
    server._freevideo_image_gen = service
    token = secrets.token_urlsafe(24)

    @server.routes.get('/freevideo/image-gen')
    async def info(request):
        try:
            return web.json_response(dict(service.info(), token=token))
        except (OSError, ValueError):
            return web.json_response(dict(error='setup_required'), status=400)

    @server.routes.get('/freevideo/image-gen/report/{report_id}')
    async def report(request):
        value = service.report(request.match_info['report_id'])
        headers = {'Cache-Control': 'no-store'}
        if value is None:
            return web.json_response(dict(error='unknown_report'), status=404, headers=headers)
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        headers['Content-Disposition'] = 'attachment; filename="FreeVideo-report-%s.json"' % stamp
        return web.json_response(value, headers=headers)

    @server.routes.post('/freevideo/image-gen/{action}')
    async def action(request):
        header = request.headers.get('X-FreeVideo-Image', '')
        if not secrets.compare_digest(header.encode('utf-8'), token.encode('utf-8')):
            return web.json_response(dict(error='reload'), status=403)
        try:
            if request.content_length and request.content_length > BODY_LIMIT:
                raise ValueError('invalid_request')
            body = bytearray()
            while True:
                chunk = await request.content.read(min(8192, BODY_LIMIT + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > BODY_LIMIT:
                    raise ValueError('invalid_request')
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError('invalid_request')
            name = request.match_info['action']
            if name == 'cast-views':
                from .cast_views import page_views
                result = page_views(value.get('description'))
            elif name == 'status':
                result = service.status(value.get('job'))
            elif name == 'cancel':
                result = service.cancel(value.get('job'))
            elif name in ('pack-install', 'pack-cancel'):
                result = service.pack_action(name.split('-')[1])
            elif name in ('segment-install', 'segment-cancel'):
                result = service.pack_action(name.split('-')[1], group='segment')
            elif name == 'segment':
                result = await asyncio.to_thread(service.segment, value)
            elif name == 'history':
                result = service.history()
            elif name == 'history-delete':
                result = service.history_delete(value.get('file'))
            elif name == 'history-delete-many':
                result = service.history_delete_many(value.get('files'))
            elif name == 'history-open':
                if request.remote not in ('127.0.0.1', '::1'):
                    raise ValueError('invalid_request')
                result = await asyncio.to_thread(service.history_open)
            else:
                result = service.start(name, value)
            return web.json_response(result)
        except FailureError as error:
            return web.json_response(error.status, status=400)
        except (ValueError, OSError) as error:
            code = str(error)
            if code in ('unknown_job', 'closed'):
                result = dict(error=code)
            else:
                code = code if code in ERRORS else 'invalid_request'
                result = dict(phase='failed', error=code, report_id=None, stage='start')
                if code in MESSAGE_ZH:
                    result['message_zh'] = MESSAGE_ZH[code]
            return web.json_response(result, status=400)
