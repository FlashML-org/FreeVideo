"""One active image and one waiting job, with a worker leased until idle or video work."""
import atexit
import contextlib
from datetime import datetime, timezone
import asyncio
from collections import OrderedDict, deque
from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid

from . import image_model, image_pack, keep, progress_model
from .. import model_repair, server_mode
from .loras import ERRORS as LORA_ERRORS, resolve_selection
from ..disk_space import ran_out


from ..download_settings import observed_speed
from ..exit_diagnostics import clean_reading
from ..monitoring import save
from .failure import (Context, classify_exception, HOST_MESSAGE_ZH, host_memory_shortage, ERRORS, FailureError, MESSAGE_ZH, NO_REPORT, STAGES, UNIFIED_MESSAGE_ZH,
                      classify_exception)

def write_refused(error):
    """Whether an OSError says a folder or file cannot be written (no permission, read-only volume, in use)."""
    import errno
    return isinstance(error, PermissionError) or getattr(error, 'errno', None) in (errno.EACCES, errno.EPERM, errno.EROFS)


BODY_LIMIT = 64 * 1024
CANCEL_GRACE = 5.
MAX_IMAGES = 4
# Reference pictures H3 Image takes, each one labelled "Picture N" in upload order.
H3_MAX_IMAGES = 16
H3_MAX_AREA = image_model.H3_AREAS['high'] ** 2
H3_MAX_SIDE = 8192
H3_GROUP = 'h3-image:' + image_pack.default_tier()
MOSS_GROUP = image_pack.MOSS_GROUP
MOSS_MAX_SIDE = 2048
REPORT_LIMIT = 16
# A point selection's picture stays this long after its last use, and at most this many at once.
SEGMENT_KEEP = 900.
SEGMENT_INPUTS = 8
# A page's picture that no job here holds is taken for one of an ended job after this long.
PAGE_UPLOAD_KEEP = 24 * 3600.
WARM_LEVELS = ('light', 'full')
# How long after registration a server starts loading its models (ComfyUI finishes starting meanwhile).
START_DELAY_SECONDS = 5.
# How often the host memory of a picture worker waiting between jobs is read.
HOST_READ_SECONDS = 5.
# How long a step of a server-mode session (a trim of the video worker, an encoder placement) waits for the lease.
LEASE_WAIT_SECONDS = 30.
WARM_MODES = ('eager', 'conservative')
HISTORY_NAME = re.compile(r'image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)')
# Which model made each history picture; a picture from before this record has no entry.
MODELS_FILE = 'image-models.json'
LEGACY_NAME = re.compile(r'[0-9a-f]{32}\.png')
# The pictures a page sends with one request: a random name in FreeVideo's own folder, which ComfyUI's Load Image list
# does not show (it lists only the input folder itself). Earlier pages put them in the input folder under the
# second form. The service removes them when the job ends; nothing else in the input folder is ever removed.
PAGE_FOLDER = 'freevideo-image-ref'
# Earlier versions wrote the pictures made for a card that is not saved yet (purpose "cast-draft") here; they are works
# of the library now, and this folder is only cleaned of what older versions left (a day at most).
DRAFT_FOLDER = 'freevideo-draft'
DRAFT_NAME = re.compile(r'freevideo-draft/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(?:jpg|png)')
# What a picture is for, and the source its record keeps: a card's view, or a picture made inside the image editor.
PURPOSES = ('cast-draft', 'edit')
PURPOSE_ORIGIN = {'cast-draft': 'cast', 'edit': 'edit'}
PAGE_UPLOAD = re.compile(r'freevideo-image-ref/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(?:png|jpg|webp)'
                         r'|freevideo-image-(?:reference|mask)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.png')
HISTORY_LIMIT = 500  # most names one trash or delete request carries
HISTORY_PAGE = 200  # default page of the history
CAST_NAME_LIMIT = 40
# Where a picture came from (editor saves, and generations that name a source), beside image-models.json.
RECORDS_FILE = 'image-records.json'
WORK_SOURCE = re.compile(r'freevideo-image/image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)|[0-9a-f]{64}')
CONTENT_ID = re.compile(r'[0-9a-f]{64}')
SAVE_LIMIT = 256 * 1024 ** 2
# Deleted pictures wait in freevideo-image/.deleted this long, so a click can bring them back.
TRASH = '.deleted'
TRASH_BATCH = re.compile(r'[0-9]{1,20}-[0-9a-f]{6}')
UNDO_SECONDS = 10
UNDO_GRACE = 3
# The 高清 size is offered from this much card memory; smaller cards keep one size.
HIGH_SIZE_MIN_TOTAL = 6 * 1024 ** 3
INFERENCE_TIMEOUT = 600.
H3_INFERENCE_TIMEOUT = 1800.
MOSS_INFERENCE_TIMEOUT = 1800.
QUEUE_TIMEOUT = 600.
QUEUE_POLL = .1
# Pictures that may wait behind the running one; a further request is turned away with 'busy'.
QUEUE_LIMIT = 8
INVALID_ERRORS = frozenset(('invalid_request', 'invalid_prompt', 'invalid_dimensions', 'invalid_seed',
                            'invalid_media', 'invalid_points', 'too_many_images',
                            'invalid_layout', 'too_many_boxes', 'layout_too_long')) | LORA_ERRORS


def native_allocator(env):
    """``env`` with CUDA's native caching allocator where ComfyUI had chosen the asynchronous one.

    ComfyUI turns ``backend:cudaMallocAsync`` on in its own process and every child inherits it. The video worker
    names its allocator itself; the picture worker did not, so its card memory was counted by an allocator the
    placement plan was never measured with."""
    selected = env.get('PYTORCH_ALLOC_CONF', env.get('PYTORCH_CUDA_ALLOC_CONF'))
    if not selected or 'cudaMallocAsync' not in selected:
        return env
    options = ['backend:native' if option.strip() == 'backend:cudaMallocAsync' else option
               for option in selected.split(',')]
    if 'backend:native' not in options:
        options.insert(0, 'backend:native')
    value = ','.join(option for option in options if 'cudaMallocAsync' not in option)
    env.update(PYTORCH_ALLOC_CONF=value, PYTORCH_CUDA_ALLOC_CONF=value)
    return env


def comfy_server():
    """ComfyUI's server in this process, or None (tests, the engine's own command line)."""
    try:
        from server import PromptServer
        return PromptServer.instance
    except (ImportError, AttributeError):
        return None


class AnyOf:
    """A stop for code that only asks ``is_set()``: set once any of its checks is true."""

    def __init__(self, *checks):
        self.checks = checks

    def is_set(self):
        return any(check() for check in self.checks)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(.05)
        return True


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
                try:
                    stream.close()
                except OSError:
                    # A row that could not reach a worker that already left is still buffered; closing flushes it
                    # again into the closed pipe. The worker is gone either way.
                    pass
        crash_file = getattr(self.process, 'crash_file', None)
        if crash_file:
            try:
                os.remove(crash_file)
            except OSError:
                pass


class MemorySampler:
    """Daemon thread sampling memory during an image request (every ~2 seconds).

    Swallows all exceptions so sampling never interferes with inference.
    """
    def __init__(self, context, interval=2.0, clock=time.monotonic):
        self.session = None
        self.context = context
        self.interval = interval
        self.clock = clock
        self.started_at = self.clock()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.min_physical = None
        self.min_physical_stage = None
        self.min_commit = None
        self.min_commit_stage = None
        self.peak_worker_private_commit = None
        self.peak_worker_private_commit_stage = None
        self.peak_worker_working_set = None
        self.peak_worker_working_set_stage = None
        self.last_worker_reading = None
        self.samples = deque(maxlen=5)

    def start(self, session):
        """Sample the given worker session; a retry on a fresh worker keeps the readings of the first try."""
        self.session = session
        self.stop_event = threading.Event()
        try:
            self.thread = threading.Thread(target=self._run, name='freevideo-image-memory-sampler', daemon=True)
            self.thread.start()
        except Exception:
            pass
        return self

    def stop(self):
        self.stop_event.set()
        try:
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=2.0)
        except Exception:
            pass

    def is_alive(self):
        try:
            return self.thread is not None and self.thread.is_alive()
        except Exception:
            return False

    @staticmethod
    def _linux_tree_resident(root_pid, limit=64):
        """Resident bytes of a process and its descendants: the worker runs under a supervising host process."""
        try:
            page = os.sysconf('SC_PAGE_SIZE')
        except Exception:
            return None
        total, seen, pending = None, set(), [root_pid]
        while pending and len(seen) < limit:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            try:
                with open('/proc/%d/statm' % pid, encoding='ascii') as stream:
                    pages = int(stream.read().split()[1])
                total = (total or 0) + pages * page
            except Exception:
                continue
            try:
                for task in os.listdir('/proc/%d/task' % pid):
                    with open('/proc/%d/task/%s/children' % (pid, task), encoding='ascii') as stream:
                        pending.extend(int(child) for child in stream.read().split())
            except Exception:
                pass
        return total

    def _read_worker_memory(self, pid):
        if not pid or not isinstance(pid, int):
            return None, None
        try:
            from ..system import windows
            if windows():
                try:
                    from ..win32 import ProcessMemory
                    tree = ProcessMemory().sample(pid)
                    if tree and tree.get('processes', 0) > 0:
                        rss = tree.get('rss_bytes')
                        private = tree.get('private_commit_bytes')
                        count_rss = rss if type(rss) is int and rss >= 0 else None
                        count_priv = private if type(private) is int and private >= 0 else None
                        return count_priv, count_rss
                except Exception:
                    return None, None
            else:
                return None, self._linux_tree_resident(pid)
        except Exception:
            return None, None
        return None, None

    def sample_once(self):
        try:
            now = self.clock()
            elapsed = max(0.0, now - self.started_at)
            seconds = round(elapsed, 1)

            stage = getattr(getattr(self.context, 'timing', None), 'stage', 'start')
            if stage not in STAGES:
                stage = 'start'

            from ..exit_diagnostics import memory_sample
            sys_mem = memory_sample()
            phys = sys_mem.get('physical_available_bytes')
            phys = phys if type(phys) is int and phys >= 0 else None
            commit = sys_mem.get('commit_available_bytes')
            commit = commit if type(commit) is int and commit >= 0 else None

            pid = None
            if self.session is not None:
                process = getattr(self.session, 'process', None)
                pid = getattr(process, 'pid', None) if process is not None else getattr(self.session, 'pid', None)
                if pid is None and isinstance(self.session, int):
                    pid = self.session

            worker_private, worker_ws = self._read_worker_memory(pid)

            with self.lock:
                sample = dict(seconds=seconds, stage=stage)
                if phys is not None:
                    sample['physical_available_bytes'] = phys
                    if self.min_physical is None or phys < self.min_physical:
                        self.min_physical = phys
                        self.min_physical_stage = stage
                if commit is not None:
                    sample['commit_available_bytes'] = commit
                    if self.min_commit is None or commit < self.min_commit:
                        self.min_commit = commit
                        self.min_commit_stage = stage
                if worker_private is not None:
                    sample['worker_private_commit_bytes'] = worker_private
                    if self.peak_worker_private_commit is None or worker_private > self.peak_worker_private_commit:
                        self.peak_worker_private_commit = worker_private
                        self.peak_worker_private_commit_stage = stage
                if worker_ws is not None:
                    sample['worker_working_set_bytes'] = worker_ws
                    if self.peak_worker_working_set is None or worker_ws > self.peak_worker_working_set:
                        self.peak_worker_working_set = worker_ws
                        self.peak_worker_working_set_stage = stage

                if worker_private is not None or worker_ws is not None:
                    worker_reading = dict(seconds=seconds, stage=stage)
                    if worker_private is not None:
                        worker_reading['worker_private_commit_bytes'] = worker_private
                    if worker_ws is not None:
                        worker_reading['worker_working_set_bytes'] = worker_ws
                    self.last_worker_reading = worker_reading

                self.samples.append(sample)
        except Exception:
            pass

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.sample_once()
            except Exception:
                pass
            if self.stop_event.wait(self.interval):
                break

    def summary(self, is_failure=False):
        with self.lock:
            result = {}
            if self.min_physical is not None:
                result['min_physical_available_bytes'] = self.min_physical
                if self.min_physical_stage in STAGES:
                    result['min_physical_available_stage'] = self.min_physical_stage
            if self.min_commit is not None:
                result['min_commit_available_bytes'] = self.min_commit
                if self.min_commit_stage in STAGES:
                    result['min_commit_available_stage'] = self.min_commit_stage
            if self.peak_worker_private_commit is not None:
                result['peak_worker_private_commit_bytes'] = self.peak_worker_private_commit
                if self.peak_worker_private_commit_stage in STAGES:
                    result['peak_worker_private_commit_stage'] = self.peak_worker_private_commit_stage
            if self.peak_worker_working_set is not None:
                result['peak_worker_working_set_bytes'] = self.peak_worker_working_set
                if self.peak_worker_working_set_stage in STAGES:
                    result['peak_worker_working_set_stage'] = self.peak_worker_working_set_stage

            if is_failure and self.last_worker_reading:
                result['last_worker_reading'] = dict(self.last_worker_reading)

            if self.samples:
                result['samples'] = list(self.samples)

            return result


class Service:
    def __init__(self, installation, input_directory, *, video_busy=None, idle_seconds=45., folders=None):
        self.installation = installation
        self.input_directory = input_directory
        self.folders = folders
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
        self.preview = None  # {'serial', 'jpeg'}: the running job's newest step picture
        self._busy = False
        self.uploads = OrderedDict()
        self.closed = False
        self.yielding = False
        self.reports = OrderedDict()
        self.context = None
        self.last_machine = {}
        # The last H3 encoder placement for the page (state, reason, seconds), read by measurements and tests.
        self.warm_placement = None
        # The model the image worker holds or is loading (a picture or a full warm-up), for a conservative warm-up.
        self.loaded_model = None
        # Server mode: the worker's last report of a full warm-up, and why the last session ended.
        self.warm_facts = None
        self.warm_full = threading.Event()
        self.end_reason = None
        # Server mode: the worker takes the GPU lease for each picture and model load, and the session holds none
        # between them (a video starts beside the kept picture model). ``placing``: the session holds it to place the
        # encoder.
        self.per_request = False
        self.placing = False
        # A full warm-up asked for and not reported done: the worker may be loading the model under its lease.
        self.loading_full = False
        self.worker_restarts = 0
        # The last memory_budget reading of each calling thread, for the report.
        self.budget_reading = threading.local()
        self.worker_started = False
        self.pending = []
        self.habit_root = None
        self.queue_thread = None
        self.queue_wake = threading.Event()
        self.finished = OrderedDict()
        self.pack_installer = None
        self.h3_installer = None
        self.moss_installer = None
        # Model ids waiting for the other model's download to end; started one by one in memory only.
        self.pack_queue = []
        self.pack_drain = None
        self.segment_installer = None
        self.segment_worker = None
        self.segmenting = False
        self.segment_stop = threading.Event()
        self.segment_done = threading.Event()
        self.segment_done.set()
        # The picture each point selection reads, by name, with when it was last used: the page sends one layer
        # once for as long as it looks the same, so it stays a while and then goes.
        self.segment_inputs = OrderedDict()
        self.trash_mutex = threading.RLock()
        self.on_works_deleted = None
        # names -> {name: [card names]} for pictures a character card keeps; the server sets it.
        self.works_in_use = None
        atexit.register(self.close)

    @staticmethod
    def _video_busy():
        from ..resident_process import OWNER
        return OWNER.active

    def busy(self):
        return self._busy or self.segmenting

    def lease_holder(self):
        """What holds engine.lock in this process, for a model download deciding whether to wait: 'picture' while a
        picture is made, 'idle' while the image worker only keeps or warms its model (its session holds the lock
        for its whole life), None when this process's image worker does not hold it."""
        with self.mutex:
            worker = self.worker
            if self.per_request or worker is None or worker.process.poll() is not None:
                return None
            return 'picture' if self._busy else 'idle'

    @staticmethod
    def pack_checked(call, *args, **kwargs):
        """(value, problem): a model folder that cannot be read safely (a link leading nowhere) marks that model as
        failed with the reason and the next step, instead of failing the whole page."""
        try:
            return call(*args, **kwargs), None
        except image_pack.PackError as error:
            from ..failure_details import image_pack_error_code, image_pack_failure
            code = image_pack_error_code(error)
            return None, dict(state='failed', done=0, total=0, bytes_per_second=0, reason=code,
                              message_en=image_pack_failure(code)['detail'], message_zh=image_pack_failure(code, zh=True)['detail'])

    def info(self):
        engine_root, machine = self.installation()
        spec = image_pack.catalog()
        root = machine['model_root']
        ready, problem = self.pack_checked(image_pack.ready, root)
        ready = bool(ready)
        downloading = not ready and not problem and bool(self.pack_checked(image_pack.downloading, root, engine_root)[0])
        return dict(ready=ready, model=spec['repo'], models=self.model_rows(machine, ready, downloading, problem),
                    checking=not ready and image_pack.checking(root),
                    downloading=downloading,
                    bytes=sum(row['bytes'] for row in spec['groups']['image']), busy=self.busy(),
                    max_images=MAX_IMAGES, sizes=self.size_tiers(machine), install=self.pack_state(ready, problem),
                    face_box=True,
                    segment=dict(ready=bool(self.pack_checked(image_pack.ready, root, group='segment')[0]),
                                 max_points=16, device='cpu',
                                 install=self.segment_installer.state() if self.segment_installer else None),
                    disk_free_bytes=self.disk_free(machine['model_root']),
                    bytes_per_second=None if ready else observed_speed(engine_root), history_trash=True, history_save=True,
                    purposes=list(PURPOSES),
                    **({'unified_memory': True} if machine.get('device_backend') == 'mps' else {}))

    def forget_stale_shared_failure(self, root):
        """A failure 'a file the video install shares differs' stays in memory until something replaces the installer.
        Repair runs in the launcher and may fix the file while this service keeps running (a ComfyUI the launcher did
        not start is not restarted), and the page has no button then: so the failure goes once the file is no longer the
        one it was recorded for: it is gone, or its identity (size, times, inode) differs from the record the check left.
        No record with the file still there (a link, an unreadable record) keeps the failure. No hashing here; the next
        download then checks the file for real and either finishes or reports it again."""
        installer = self.h3_installer
        if installer is None or getattr(installer, 'busy', False):
            return
        state = installer.state()
        if not isinstance(state, dict) or state.get('state') != 'failed' or state.get('reason') != 'shared_mismatch':
            return
        try:
            records = model_repair.read(image_pack.base(root))
            for row in image_pack.rows(group=H3_GROUP):
                if row.get('shared'):
                    path = image_pack.target(row, root)
                    record = records.get(str(path))
                    if os.path.lexists(path) and (record is None or model_repair.same_identity(model_repair.identity(path), record)):
                        return                      # still the file that failed (or no way to tell: a link, no record)
        except (OSError, ValueError):
            return
        with self.mutex:
            if self.h3_installer is installer:
                self.h3_installer = None

    def model_rows(self, machine, qwen_ready, qwen_downloading, qwen_problem=None):
        """Both picture models for the page: installed, or being fetched with the bytes still to download."""
        root = machine['model_root']
        mac = sys.platform == 'darwin'
        if not mac:
            self.forget_stale_shared_failure(root)
        h3_ready, h3_problem = False, None
        if not mac:
            h3_ready, h3_problem = self.pack_checked(image_pack.ready, root, group=H3_GROUP)
            h3_ready = bool(h3_ready)
        h3_status = {} if h3_ready or mac or h3_problem else image_pack.status(root, group=H3_GROUP)
        installer = self.h3_installer
        h3_state = None if h3_ready or installer is None else installer.state()
        h3_active = h3_state is not None and h3_state['state'] in ('running', 'paused_for_generation')
        qwen_state = None if qwen_ready else self.pack_state(False, qwen_problem)
        qwen_status = {} if qwen_ready or qwen_problem else image_pack.status(root)
        with self.mutex:
            waiting = list(self.pack_queue)
            active = self._active_pack()
        h3_rows = image_pack.rows(group=H3_GROUP)
        queued = dict(state='queued', done=0, total=0, bytes_per_second=0)
        rows = [dict(id=image_model.H3, label='MiniMax H3 Image', available=not mac, ready=h3_ready,
                     downloading=h3_active, sizes=['standard', 'high'], max_images=H3_MAX_IMAGES,
                     bytes_total=sum(r['bytes'] for r in h3_rows) if mac else h3_status.get('total_bytes', 0),
                     bytes=h3_status.get('bytes', 0), bytes_done=(h3_state or {}).get('done', 0),
                     bytes_needed=h3_status.get('remaining_bytes', 0),
                     checking=bool(h3_status.get('checking')),
                     shared_bytes=sum(r['bytes'] for r in h3_rows if r.get('shared')),
                     blocked_by=active if active == image_model.QWEN and not h3_ready else None,
                     install=None if mac else queued if image_model.H3 in waiting else self.pack_row(self.h3_installer, h3_ready, h3_problem))]
        if mac:
            rows[0]['unavailable_reason'] = 'platform'
        rows.append(dict(id=image_model.QWEN, label='Qwen-Image 2.1', available=True, ready=qwen_ready, max_images=MAX_IMAGES,
                         downloading=qwen_downloading and not h3_active,
                         bytes_total=sum(row['bytes'] for row in image_pack.catalog()['groups']['image']),
                         bytes_done=(qwen_state or {}).get('done', 0), bytes_needed=qwen_status.get('remaining_bytes', 0),
                         checking=bool(qwen_status.get('checking')),
                         shared_bytes=0,
                         blocked_by=active if active == image_model.H3 and not qwen_ready else None,
                         install=queued if image_model.QWEN in waiting else qwen_state))
        moss_ready, moss_problem = self.pack_checked(image_pack.ready, root, group=MOSS_GROUP)
        moss_ready = bool(moss_ready)
        moss_status = {} if moss_ready or moss_problem else image_pack.status(root, group=MOSS_GROUP)
        moss_state = None if moss_ready or self.moss_installer is None else self.moss_installer.state()
        moss_active = moss_state is not None and moss_state['state'] in ('running', 'paused_for_generation')
        moss_rows = image_pack.rows(group=MOSS_GROUP)
        rows.append(dict(id=image_model.MOSS, label='MOSS-Image', available=not mac, ready=moss_ready,
                         downloading=moss_active, sizes=['standard', 'high'], max_images=0,
                         bytes_total=moss_status.get('total_bytes', sum(r['bytes'] for r in moss_rows)),
                         bytes=moss_status.get('bytes', 0), bytes_done=(moss_state or {}).get('done', 0),
                         bytes_needed=moss_status.get('remaining_bytes', 0), shared_bytes=0,
                         blocked_by=active if active not in (None, image_model.MOSS) and not moss_ready else None,
                         install=None if moss_ready else queued if image_model.MOSS in waiting
                         else self.pack_row(self.moss_installer, moss_ready, moss_problem)))
        if mac:
            rows[-1]['unavailable_reason'] = 'platform'
        return rows

    def _active_pack(self):
        """The image model this engine is downloading now (running or waiting for a video), or None."""
        for model, installer in ((image_model.H3, self.h3_installer), (image_model.QWEN, self.pack_installer),
                                     (image_model.MOSS, self.moss_installer)):
            if installer is not None and installer.state()['state'] in ('running', 'paused_for_generation'):
                return model
        return None

    @staticmethod
    def disk_free(root):
        """Free bytes on the volume that holds the models, read from the nearest existing folder; None if unknown."""
        path = Path(root)
        try:
            while not path.exists() and path != path.parent:
                path = path.parent
            return int(shutil.disk_usage(path).free)
        except (OSError, ValueError):
            return None

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

    def pack_state(self, ready, problem=None):
        """Progress of the Qwen-Image download this page started, in both languages for a failure."""
        return self.pack_row(self.pack_installer, ready, problem)

    @staticmethod
    def pack_row(installer, ready, problem=None):
        """Progress of one picture-model download; a failure carries both languages and the report id. A model
        folder that cannot be read safely shows as that failure unless a download of it is under way."""
        if ready:
            return None
        if installer is None or problem and installer.state()['state'] not in ('running', 'paused_for_generation', 'failed'):
            return problem
        from ..failure_details import image_pack_failure
        value = installer.state()
        row = dict(state=value['state'], done=value.get('done', 0), total=value.get('total', 0),
                   bytes_per_second=round(value.get('bytes_per_second', 0)),
                   **({'phase': 'verify', 'verify_done': value.get('verify_done'), 'verify_total': value.get('verify_total')}
                      if value.get('phase') == 'verify' else {}))
        if value['state'] == 'paused_for_generation':
            # What it waits for, in the page's own words: a video, a picture, or another task on the card.
            row['reason'] = value.get('reason') or 'busy_engine'
        if value['state'] == 'failed':
            row.update(reason=value.get('reason'), message_en=image_pack_failure(value.get('reason'))['detail'],
                       message_zh=image_pack_failure(value.get('reason'), zh=True)['detail'])
            if value.get('report_id'):
                row['report_id'] = value['report_id']
        return row

    def _pack_installer(self, model, root, engine_root):
        attribute = {image_model.H3: 'h3_installer', image_model.MOSS: 'moss_installer'}.get(model, 'pack_installer')
        installer = getattr(self, attribute)
        if installer is None or installer.root != root or installer.engine_root != engine_root:
            options = dict(group=self.model_group(model)) if model != image_model.QWEN else {}
            installer = image_pack.Installer(root, engine_root=engine_root, busy=self.video_busy, holder=self.lease_holder, **options)
            setattr(self, attribute, installer)
        return installer

    def _pack_ready(self, model, root):
        """Not ready when its folder cannot be read safely either: the download then stops with the reason."""
        group = self.model_group(model)
        return bool(self.pack_checked(image_pack.ready, root, **({'group': group} if group else {}))[0])

    @staticmethod
    def model_group(model):
        """The installer group of a picture model; None for Qwen-Image, whose files are the default group."""
        return {image_model.H3: H3_GROUP, image_model.MOSS: MOSS_GROUP}.get(model)

    def _drain_packs(self):
        """Start each waiting model once the other download has ended; the queue lives in memory only."""
        while True:
            time.sleep(1.)
            with self.mutex:
                if self.closed or not self.pack_queue:
                    self.pack_drain = None
                    return
                if self._active_pack() is not None:
                    continue
                model = self.pack_queue[0]
                try:
                    engine_root, machine = self.installation()
                    root = machine['model_root']
                    if image_pack.downloading(root, engine_root):
                        continue
                    self.pack_queue.pop(0)
                    if not self._pack_ready(model, root):
                        self._pack_installer(model, root, engine_root).start('install', background=True)
                except Exception:
                    logging.getLogger(__name__).exception('queued image model install failed to start')
                    if self.pack_queue and self.pack_queue[0] == model:
                        self.pack_queue.pop(0)

    def pack_action(self, action, *, group='image', model=None):
        """Start or cancel an image model download from the page; it yields to a running video.

        `model` names the H3 Image pack (Qwen-Image is the default). The two are fetched one at a time:
        asking for the second while the first downloads queues it, and it starts when the first ends."""
        engine_root, machine = self.installation()
        root = machine['model_root']
        if model is not None and model not in image_model.MODELS:
            raise ValueError('invalid_request')
        if model == image_model.H3:
            if sys.platform == 'darwin':
                raise ValueError('invalid_request')
            group = H3_GROUP
        if model == image_model.MOSS:
            if sys.platform == 'darwin':
                raise ValueError('invalid_request')
            group = MOSS_GROUP
        with self.mutex:
            if group == 'segment':
                installer = self.segment_installer
                if action == 'cancel':
                    if installer is not None:
                        installer.cancel()
                elif installer is not None and installer.state()['state'] == 'paused_for_generation':
                    installer.continue_anyway()
                elif (not self.pack_checked(image_pack.ready, root, group=group)[0]
                      and not self.pack_checked(image_pack.downloading, root, engine_root)[0]):
                    if installer is None or installer.root != root or installer.engine_root != engine_root:
                        installer = image_pack.Installer(root, engine_root=engine_root, busy=self.video_busy, holder=self.lease_holder, group=group)
                        self.segment_installer = installer
                    installer.start('install', background=True)
                return self.info()
            target = {H3_GROUP: image_model.H3, MOSS_GROUP: image_model.MOSS}.get(group, image_model.QWEN)
            if action == 'cancel':
                if model is None:
                    target = self._active_pack() or (self.pack_queue[0] if self.pack_queue else image_model.QWEN)
                if target in self.pack_queue:
                    self.pack_queue.remove(target)
                installer = {image_model.H3: self.h3_installer, image_model.MOSS: self.moss_installer}.get(target, self.pack_installer)
                if installer is not None:
                    installer.cancel()
            elif target in self.pack_queue:
                pass
            elif self._paused_installer(target) is not None:
                # Asked again while it waits for the card: the user wants the download now, beside whatever runs.
                self._paused_installer(target).continue_anyway()
            elif not self._pack_ready(target, root):
                other = self._active_pack()
                if other is not None and other != target:
                    self.pack_queue.append(target)
                    if self.pack_drain is None:
                        self.pack_drain = threading.Thread(target=self._drain_packs, daemon=True)
                        self.pack_drain.start()
                elif not image_pack.downloading(root, engine_root):
                    self._pack_installer(target, root, engine_root).start('install', background=True)
        return self.info()

    def _paused_installer(self, target):
        installer = {image_model.H3: self.h3_installer, image_model.MOSS: self.moss_installer}.get(target, self.pack_installer)
        return installer if installer is not None and installer.state()['state'] == 'paused_for_generation' else None

    def media_path(self, name):
        """Shared input-name and symlink policy for generation and point selection."""
        directory = Path(self.input_directory()).resolve()
        folder, _, leaf = name.rpartition('/') if isinstance(name, str) else ('', '', '')
        if (not isinstance(name, str) or not leaf or name.startswith('/') or leaf in ('.', '..')
                or folder not in ('', 'freevideo-image', PAGE_FOLDER, DRAFT_FOLDER)
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
        try:
            output.mkdir(exist_ok=True)
        except OSError as error:
            # A full disk is its own answer; a missing, read-only or occupied folder is not a wrong setting.
            raise ValueError('disk_full' if ran_out(error) else 'output_unwritable') from None
        return output

    def output_path(self, named=False, suffix='.jpg'):
        """A new picture's path. Generated pictures get a readable name that the history lists."""
        output = self.output_directory()
        if not named:
            return str(output / (uuid.uuid4().hex + '.png'))
        stamp = time.strftime('image-%Y%m%d-%H%M%S')
        for number in range(1, 100):
            base = '%s-%02d' % (stamp, number)
            if not any(os.path.lexists(output / (base + other)) for other in ('.jpg', '.png')):
                return str(output / (base + suffix))
        raise ValueError('invalid_media')

    def copy_directory(self):
        """Where the copy of a finished picture goes: the output folder of ComfyUI, in a folder named like the one the
        pictures are kept in. None when the user switched the copy off or no output folder is known."""
        try:
            from ..comfy_bridge import installation_root
            from ..output_settings import read
            if not read(installation_root())['image_copy']:
                return None
            output = Path(self.folders.get_output_directory()).resolve()
        except Exception:
            return None
        directory = output / 'freevideo-image'
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            return None
        return directory

    def copy_to_output(self, path):
        """Copy a finished picture into the output folder, as a file of its own (a link would let an edit there change
        the picture the library keeps). It belongs to the picture: works_deleted removes it. A copy that fails never
        fails the picture."""
        try:
            directory = self.copy_directory()
            if directory is None:
                return
            source = Path(path)
            target = directory / source.name
            if source.is_symlink() or not source.is_file() or target.is_symlink():
                return
            directory.mkdir(exist_ok=True)
            temporary = directory / ('.%s.%s.partial' % (source.name, uuid.uuid4().hex[:8]))
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except Exception:
            logging.warning('[FreeVideo] Could not copy a picture into the output folder.', exc_info=True)

    def forget_copies(self, names):
        """Remove the copies in the output folder of pictures deleted for good; only the plain file of that name."""
        try:
            directory = self.copy_directory_any()
            if directory is None:
                return
            for name in names:
                target = directory / name.rpartition('/')[2]
                try:
                    if target.parent == directory and not target.is_symlink() and target.is_file():
                        target.unlink()
                except OSError:
                    logging.warning('[FreeVideo] Could not remove a picture copy in the output folder.', exc_info=True)
        except Exception:
            logging.warning('[FreeVideo] Could not remove the picture copies of deleted pictures.', exc_info=True)

    def copy_directory_any(self):
        """The copy folder whether or not copying is on now: a picture copied earlier is removed all the same."""
        try:
            output = Path(self.folders.get_output_directory()).resolve()
        except Exception:
            return None
        directory = output / 'freevideo-image'
        if directory.is_symlink() or not directory.is_dir():
            return None
        return directory

    @staticmethod
    def work_record(value, origin):
        """What a new picture keeps about where it came from: a picture of the history or a content id, and the content
        ids of its prompt and references. Never a prompt or a file name the user chose."""
        record = dict(origin=origin)
        name = value.get('cast_name')
        if name is not None:
            if origin != 'cast' or not isinstance(name, str) or len(name) > CAST_NAME_LIMIT or '\0' in name:
                raise ValueError('invalid_request')
            if name.strip():
                record['cast_name'] = name.strip()
        source = value.get('source')
        if source is not None:
            if not isinstance(source, str) or not WORK_SOURCE.fullmatch(source):
                raise ValueError('invalid_request')
            record['source'] = source
        prompt = value.get('prompt')
        if isinstance(prompt, str) and prompt.strip():
            # A keyed hash, never the text: when the last picture made with this prompt goes, the pages drop it from
            # their history of prompts too.
            from .. import private_store
            record['prompt_hash'] = private_store.name('prompt', prompt.strip())
        prompt_ref = value.get('prompt_ref')
        if prompt_ref is not None:
            if not isinstance(prompt_ref, str) or not CONTENT_ID.fullmatch(prompt_ref):
                raise ValueError('invalid_request')
            record['prompt_ref'] = prompt_ref
        refs = value.get('reference_refs')
        if refs is not None:
            if (not isinstance(refs, list) or len(refs) > H3_MAX_IMAGES
                    or not all(isinstance(ref, str) and CONTENT_ID.fullmatch(ref) for ref in refs)):
                raise ValueError('invalid_request')
            record['reference_refs'] = list(refs)
        return record

    def records(self, output=None):
        try:
            rows = json.loads(((output or self.output_directory()) / RECORDS_FILE).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return {name: row for name, row in rows.items() if isinstance(row, dict)} if isinstance(rows, dict) else {}

    def note_record(self, path, record):
        """Keep a picture's record beside the model record; records of pictures that are gone are dropped."""
        if not record or set(record) == {'origin'} and record['origin'] == 'generate':
            return
        try:
            output = self.output_directory()
            rows = self.records(output)
            rows[Path(path).name] = record
            present = {entry.name for entry in output.iterdir()}
            # A picture waiting in the trash keeps its record: undo needs it, and the final delete forgets its prompt by it.
            try:
                for entry in os.listdir(output / TRASH):
                    found = re.fullmatch(r'%s-(.+)' % TRASH_BATCH.pattern, entry)
                    if found:
                        present.add(found.group(1))
            except OSError:
                pass
            save(output / RECORDS_FILE, {name: row for name, row in rows.items() if name in present})
        except (OSError, ValueError):
            pass

    def history_save(self, value):
        """Save the image editor's picture as a new work: the page uploads it to the request folder, it is written
        under the history's own name (PNG when it has transparency, else JPEG) and the upload goes at once."""
        if not isinstance(value, dict):
            raise ValueError('invalid_request')
        name = value.get('image')
        if not isinstance(name, str) or not name.startswith(PAGE_FOLDER + '/') or not PAGE_UPLOAD.fullmatch(name):
            raise ValueError('invalid_media')
        try:
            record = self.work_record(value, 'editor')
            path = Path(self.media_path(name))
            if path.stat().st_size > SAVE_LIMIT:
                raise ValueError('too_large')
            from PIL import Image
            try:
                with Image.open(path) as image:
                    if max(image.size) > H3_MAX_SIDE:
                        raise ValueError('too_large')
                    image.load()
                    clear = image.convert('RGBA') if image.mode in ('RGBA', 'LA', 'PA') or 'transparency' in image.info else None
                    transparent = clear is not None and clear.getchannel('A').getextrema()[0] < 255
                    target = Path(self.output_path(named=True, suffix='.png' if transparent else '.jpg'))
                    temporary = target.with_name('.%s.%s.partial' % (target.name, uuid.uuid4().hex[:8]))
                    try:
                        if transparent:
                            clear.save(temporary, 'PNG')
                        else:
                            image.convert('RGB').save(temporary, 'JPEG', quality=95)
                        from ..file_ops import publish
                        publish(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
            except (OSError, Image.DecompressionBombError, SyntaxError) as error:
                # A full disk or a folder that cannot be written is not a wrong picture: each has its own answer.
                if isinstance(error, OSError) and ran_out(error):
                    raise ValueError('disk_full') from None
                if isinstance(error, OSError) and write_refused(error):
                    raise ValueError('output_unwritable') from None
                raise ValueError('invalid_media') from None
            self.note_record(target, record)
            self.copy_to_output(target)
        finally:
            self.remove_uploads([name])
        return dict(file='freevideo-image/' + target.name, name=target.name)

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

    def history(self, offset=0, limit=None):
        """The pictures generated here, newest first, a page at a time (`more` says whether older ones remain); the
        folder is the record, so it outlives sessions. `origin` and `cast_name` come from the picture's record."""
        if type(offset) is not int or offset < 0 or limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
            raise ValueError('invalid_request')
        limit = HISTORY_PAGE if limit is None else limit
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
        # A picture removed some other way (by hand, by a cleanup) takes its small copy for the grids with it now.
        from ..work_thumbnails import prune_pictures
        prune_pictures(self.input_directory(), present=set(names))
        made, records = self.made_by(output), self.records(output)

        def item(name):
            row = dict(file='freevideo-image/' + name, name=name)
            if name in made:
                row['model'] = made[name]
            kept = records.get(name, {})
            for key in ('origin', 'cast_name'):
                if isinstance(kept.get(key), str):
                    row[key] = kept[key]
            return row
        page = names[offset:offset + limit]
        return dict(items=[item(name) for name in page], more=offset + limit < len(names), total=len(names))

    @staticmethod
    def made_by(output):
        """File name -> model id for the pictures that have a record; an unreadable file counts as no record."""
        try:
            rows = json.loads((output / MODELS_FILE).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        if not isinstance(rows, dict):
            return {}
        return {name: model for name, model in rows.items() if isinstance(model, str) and model in image_model.MODELS}

    def note_model(self, path, model):
        """Record the model that made a picture, and drop the records of pictures that are gone. A failed write only loses the label."""
        if model not in image_model.MODELS:
            return
        try:
            output = self.output_directory()
            rows = self.made_by(output)
            rows[Path(path).name] = model
            present = {entry.name for entry in output.iterdir()}
            save(output / MODELS_FILE, {name: value for name, value in rows.items() if name in present})
        except (OSError, ValueError):
            pass

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

    def used_by_cards(self, names):
        """The cards that keep each of these pictures: a picture a card uses is not deleted from the library (the card
        holds no copy), so no picture stays behind in a card after it was deleted."""
        hook = getattr(self, 'works_in_use', None)
        if hook is None:
            return {}
        try:
            return {name: list(cards) for name, cards in hook(list(names)).items() if cards}
        except Exception:
            logging.warning('[FreeVideo] Could not tell which cards keep a picture.', exc_info=True)
            return {}

    def history_delete(self, name):
        path = self.history_path(name)
        if not path.is_file():
            raise ValueError('invalid_media')
        if self.used_by_cards([name]):
            raise ValueError('picture_in_use')
        path.unlink()
        self.works_deleted([name])
        return self.history()

    @staticmethod
    def video_prompts():
        """The prompt hashes the library's videos still use: a prompt a video keeps stays in the history."""
        try:
            import folder_paths
            from .. import comfy_library
            return comfy_library._kept_facts(Path(folder_paths.get_output_directory()) / 'FreeVideo')[1]
        except Exception:
            return set()

    def works_deleted(self, names):
        """What only a deleted picture had goes with it: its model record, the files and caches it held, and (through
        the hook the server sets) its library card and ComfyUI's record of the request."""
        from .. import private_store
        names = [name for name in names if isinstance(name, str)]
        if not names:
            return
        self.forget_copies(names)
        try:
            output = self.output_directory()
            rows = self.made_by(output)
            gone = {name.rpartition('/')[2] for name in names}
            if gone & set(rows):
                save(output / MODELS_FILE, {name: model for name, model in rows.items() if name not in gone})
            records = self.records(output)
            if gone & set(records):
                kept = {name: row for name, row in records.items() if name not in gone}
                save(output / RECORDS_FILE, kept)
                kept_prompts = {row.get('prompt_hash') for row in kept.values()} | self.video_prompts()
                private_store.forget_prompts({row.get('prompt_hash') for name, row in records.items() if name in gone}
                                             - kept_prompts - {None})
        except (OSError, ValueError):
            pass
        try:
            from .. import private_refs
            input_root = self.input_directory()
            from .. import work_thumbnails
            for name in names:
                # What the engine made of the picture (its small copy for the grids) goes with what it held.
                work_thumbnails.release('image:' + name.rpartition('/')[2], input_root)
                private_refs.release_owner('image:' + name.rpartition('/')[2], input_root=input_root)
        except (OSError, ValueError):
            logging.warning('[FreeVideo] Could not release what a deleted picture held.', exc_info=True)
        # The failure reports kept in memory for the page drop the ones about these pictures.
        leaves = [name.rpartition('/')[2] for name in names]
        with self.mutex:
            for report_id in [key for key, report in self.reports.items()
                              if any(leaf in json.dumps(report, default=str) for leaf in leaves)]:
                self.reports.pop(report_id, None)
        hook = getattr(self, 'on_works_deleted', None)
        if hook is not None:
            try:
                hook(names)
            except Exception:
                logging.warning('[FreeVideo] Could not remove what a deleted picture left elsewhere.', exc_info=True)

    # Deleting from the page: the pictures move to freevideo-image/.deleted at once and go for good UNDO_SECONDS
    # later, also when the page is closed meanwhile; a restart finishes what an earlier run left.
    def trash_directory(self):
        trash = self.output_directory() / TRASH
        if trash.is_symlink() or (trash.exists() and not trash.is_dir()):
            raise ValueError('invalid_media')
        trash.mkdir(exist_ok=True)
        return trash

    def history_trash(self, names):
        if not isinstance(names, list) or not names or len(names) > HISTORY_LIMIT:
            raise ValueError('invalid_request')
        with self.trash_mutex:
            trash = self.trash_directory()
            token = '%d-%s' % (time.time_ns(), secrets.token_hex(3))
            moved, failed = [], []
            kept = self.used_by_cards([name for name in names if isinstance(name, str)])
            for name in dict.fromkeys(names):
                try:
                    path = self.history_path(name)
                except ValueError:
                    failed.append(dict(file=name if isinstance(name, str) else '', reason='invalid'))
                    continue
                if name in kept:
                    failed.append(dict(file=name, reason='card', cards=kept[name]))
                    continue
                target = trash / ('%s-%s' % (token, path.name))
                try:
                    path.rename(target)
                except FileNotFoundError:
                    failed.append(dict(file=name, reason='missing'))
                    continue
                except OSError:
                    failed.append(dict(file=name, reason='busy'))
                    continue
                moved.append(path.name)
            if moved:
                save(trash / ('.pending-%s.json' % token), dict(due=time.time() + UNDO_SECONDS + UNDO_GRACE, items=moved))
                timer = threading.Timer(UNDO_SECONDS + UNDO_GRACE + 1, self.finish_trash)
                timer.daemon = True
                timer.start()
        return dict(self.history(), batch=token if moved else None, undo_seconds=UNDO_SECONDS,
                    deleted=['freevideo-image/' + leaf for leaf in moved], failed=failed)

    def history_undo(self, token):
        if not isinstance(token, str) or not TRASH_BATCH.fullmatch(token):
            raise ValueError('invalid_request')
        with self.trash_mutex:
            trash = self.trash_directory()
            manifest = trash / ('.pending-%s.json' % token)
            try:
                value = json.loads(manifest.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise ValueError('undo_expired') from None
            output = self.output_directory()
            kept = []
            for leaf in value.get('items', []):
                if not isinstance(leaf, str) or not (HISTORY_NAME.fullmatch(leaf) or LEGACY_NAME.fullmatch(leaf)):
                    continue
                source, target = trash / ('%s-%s' % (token, leaf)), output / leaf
                try:
                    if not os.path.lexists(target):
                        source.rename(target)
                        continue
                except OSError:
                    pass
                kept.append(leaf)
            manifest.unlink(missing_ok=True)
            if kept:
                # A picture that could not be put back is not left half deleted.
                self.purge_trash([(token, leaf) for leaf in kept])
        return self.history()

    def finish_trash(self, *, everything=False):
        """Delete for good the batches whose undo time is over (all with ``everything``), and pictures a stopped
        deletion moved without a record. Returns the names deleted."""
        with self.trash_mutex:
            try:
                trash = self.trash_directory()
                entries = sorted(os.listdir(trash))
            except (OSError, ValueError):
                return []
            held, due = set(), []
            for entry in entries:
                found = re.fullmatch(r'\.pending-(%s)\.json' % TRASH_BATCH.pattern, entry)
                if not found:
                    continue
                try:
                    value = json.loads((trash / entry).read_text(encoding='utf-8'))
                    leaves = [leaf for leaf in value.get('items', []) if isinstance(leaf, str)]
                    late = everything or time.time() >= float(value.get('due', 0))
                except (OSError, ValueError, TypeError):
                    leaves, late = [], True
                pairs = [(found.group(1), leaf) for leaf in leaves]
                if late:
                    due += pairs
                    (trash / entry).unlink(missing_ok=True)
                else:
                    held.update('%s-%s' % pair for pair in pairs)
            for entry in entries:
                found = re.fullmatch(r'(%s)-(.+)' % TRASH_BATCH.pattern, entry)
                if found and entry not in held and (found.group(1), found.group(2)) not in due:
                    due.append((found.group(1), found.group(2)))
            return self.purge_trash(due)

    def purge_trash(self, pairs):
        from .. import private_store
        trash, deleted = self.trash_directory(), []
        input_root = self.input_directory()
        for token, leaf in pairs:
            if not (HISTORY_NAME.fullmatch(leaf) or LEGACY_NAME.fullmatch(leaf)):
                continue
            relative = 'freevideo-image/%s/%s-%s' % (TRASH, token, leaf)
            status, _ = private_store.safe_remove(input_root, relative, patterns=[re.compile(re.escape(relative))], folders=0)
            if status in ('removed', 'gone'):
                deleted.append('freevideo-image/' + leaf)
        self.works_deleted(deleted)
        return deleted

    def history_delete_many(self, names):
        """Delete each picture on its own; the ones that stay are listed with why (busy, missing, invalid)."""
        if not isinstance(names, list) or len(names) > HISTORY_LIMIT:
            raise ValueError('invalid_request')
        failed, deleted = [], []
        kept = self.used_by_cards([name for name in names if isinstance(name, str)])
        for name in dict.fromkeys(names):
            try:
                path = self.history_path(name)
            except ValueError:
                failed.append(dict(file=name if isinstance(name, str) else '', reason='invalid'))
                continue
            if name in kept:
                failed.append(dict(file=name, reason='card', cards=kept[name]))
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                failed.append(dict(file=name, reason='missing'))
            except OSError:
                failed.append(dict(file=name, reason='busy'))
            else:
                deleted.append(name)
        self.works_deleted(deleted)
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
            from ..windows_ux import open_folder
            if not open_folder(folder):
                return dict(opened=False, folder=folder)  # the page tells the user where the images are
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

    def held_tokens(self):
        """The private_store tokens of the pictures a queued or running job, or a recent point selection, still reads."""
        from .. import private_store
        with self.mutex:
            names = [name for kept in self.uploads.values() for name in kept] + list(self.segment_inputs)
        return {found for found in map(private_store.classify, names) if found}

    def note_segment_input(self, name, now=None):
        """Keep the picture a point selection read for SEGMENT_KEEP seconds after its last use; older ones go."""
        now = time.monotonic() if now is None else now
        stale = []
        with self.mutex:
            if isinstance(name, str) and PAGE_UPLOAD.fullmatch(name):
                self.segment_inputs.pop(name, None)
                self.segment_inputs[name] = now
            while self.segment_inputs and (len(self.segment_inputs) > SEGMENT_INPUTS
                                           or now - next(iter(self.segment_inputs.values())) > SEGMENT_KEEP):
                stale.append(self.segment_inputs.popitem(last=False)[0])
            held = {held for kept in self.uploads.values() for held in kept}
        self.remove_uploads([old for old in stale if old not in held and old != name])

    def clean_page_uploads(self, *, older=PAGE_UPLOAD_KEEP, now=None):
        """Remove the pictures of jobs that ended without this process seeing them end (a restart, a closed
        program), when no job here holds them. Only the page's own temporary names; returns how many went."""
        from .. import private_store
        now = time.time() if now is None else now
        try:
            root = Path(self.input_directory())
        except (OSError, ValueError):
            return 0
        names = []
        for folder, prefix in ((root / PAGE_FOLDER, PAGE_FOLDER + '/'), (root, ''), (root / DRAFT_FOLDER, DRAFT_FOLDER + '/')):
            try:
                if folder.is_symlink() or not folder.is_dir():
                    continue
                names += [prefix + leaf for leaf in os.listdir(folder)
                          if PAGE_UPLOAD.fullmatch(prefix + leaf) or DRAFT_NAME.fullmatch(prefix + leaf)]
            except OSError:
                continue
        with self.mutex:
            held = {name for kept in self.uploads.values() for name in kept} | set(self.segment_inputs)
        removed = 0
        for name in names:
            try:
                if name in held or now - os.lstat(root / name).st_mtime < older:
                    continue
            except OSError:
                continue
            removed += private_store.safe_remove(root, name)[0] == 'removed'
        return removed

    def segment(self, value):
        """Immediate admission, no waiting queue. The HTTP caller runs this off its event loop."""
        from .segment import validate_points
        from ..locking import runtime_lock
        if not isinstance(value, dict):
            raise ValueError('invalid_request')
        validate_points(value.get('points'))
        path = self.media_path(value.get('image'))
        self.note_segment_input(value.get('image'))
        with self.mutex:
            if self.closed:
                raise ValueError('closed')
            if self.yielding or self.video_busy():
                raise ValueError('busy_video')
            if self.busy() or self.pending:
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

    def face_box(self, value):
        """The faces in an uploaded picture, found on the CPU at once: no queue, no graphics card, and the upload stays."""
        if not isinstance(value, dict):
            raise ValueError('invalid_request')
        path = self.media_path(value.get('image'))
        from .face_box import WeightsError, detect
        try:
            return detect(path)
        except WeightsError:
            logging.warning('[FreeVideo] The face detector weights are missing or changed; faces are not looked for.')
            raise ValueError('model_unreadable') from None

    def request(self, value):
        if not isinstance(value, dict):
            raise ValueError('invalid_request')
        prompt = value.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
            raise ValueError('invalid_prompt')
        model = value.get('model')
        if model is not None and model not in image_model.MODELS:
            raise ValueError('invalid_request')
        dimensions = [value.get(name) for name in ('width', 'height')]
        if model == image_model.H3:
            # 4 MP up to 16 MP; a smaller shape is asked for again at 4 MP in the same proportion.
            if any(type(size) is not int or not 256 <= size <= H3_MAX_SIDE for size in dimensions):
                raise ValueError('invalid_dimensions')
            dimensions = list(image_model.at_least_four_megapixels(model, *dimensions))
            if dimensions[0] * dimensions[1] > H3_MAX_AREA * 1.05:
                raise ValueError('invalid_dimensions')
            if any(value.get(name) is not None for name in ('mask', 'cast_view', 'loras')):
                raise ValueError('invalid_request')
        elif model == image_model.MOSS:
            if any(type(size) is not int or not 256 <= size <= MOSS_MAX_SIDE for size in dimensions):
                raise ValueError('invalid_dimensions')
            if value.get('images') or any(value.get(name) is not None for name in ('mask', 'cast_view', 'loras')):
                raise ValueError('invalid_request')
        elif any(type(size) is not int or not 256 <= size <= 2048 for size in dimensions):
            raise ValueError('invalid_dimensions')
        seed = value.get('seed')
        if seed is not None and (type(seed) is not int or not -2**63 <= seed < 2**64):
            raise ValueError('invalid_seed')
        images = value.get('images')
        if images is None:
            images = []
        if not isinstance(images, list):
            raise ValueError('invalid_media')
        if len(images) > (H3_MAX_IMAGES if model == image_model.H3 else MAX_IMAGES):
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
        loras = resolve_selection(value.get('loras', []), folders=self.folders)
        if loras:
            from ..lora_triggers import apply_triggers
            prompt = apply_triggers(prompt, loras, folders=self.folders)
        purpose = value.get('purpose')
        if purpose is not None and purpose not in PURPOSES:
            raise ValueError('invalid_request')
        dimensions = [size // 32 * 32 for size in dimensions]
        layout = value.get('layout')
        if layout is not None:
            # Boxes drawn on the picture (MOSS only): written against the size actually made, so each box covers the
            # same part of the picture as on the page. The prompt is the caption of the whole picture.
            if model != image_model.MOSS:
                raise ValueError('invalid_request')
            from .moss_layout import LayoutError, compile_layout
            try:
                prompt = compile_layout(prompt, layout, *dimensions)
            except LayoutError as error:
                raise ValueError(error.code) from None
        result = dict(prompt=prompt, width=dimensions[0], height=dimensions[1],
                      steps=8, seed=seed, images=paths, **(dict(mask=mask_path) if mask_path else {}),
                      **(dict(cast_view='back') if cast_view == 'back' else {}),
                      path=self.output_path(named=True),
                      **(dict(loras=loras) if loras else {}), **(dict(model=model) if model else {}))
        # Leave room for action, kind and the small size-fit record added at dispatch.
        if loras and len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > BODY_LIMIT - 1024:
            raise ValueError('invalid_loras')
        return result

    def start(self, action, value):
        try:
            return self.admit(action, value)
        except BaseException:
            # A request turned away at the door leaves no job to clean up after it.
            images = value.get('images') if isinstance(value, dict) else None
            if isinstance(images, list):
                self.remove_uploads([*images[:REPORT_LIMIT], value.get('mask')])
            raise

    def admit(self, action, value):
        if action not in ('generate', 'compose'):
            raise ValueError('invalid_request')
        context = Context(action, value)
        context.work_record = (self.work_record(value, PURPOSE_ORIGIN.get(value.get('purpose'), 'generate'))
                               if isinstance(value, dict) else None)
        context.note_start()
        with self.mutex:
            if self.closed:
                raise ValueError('closed')
            if self.segmenting:
                raise ValueError('busy')
            if len(self.pending) >= QUEUE_LIMIT:
                raise ValueError('busy')
            if self.yielding:
                context.set_machine(self.last_machine)
                raise FailureError(self.failed('busy_video', context, video_busy=True))
            video = self.video_busy()
            waiting = (video or self.busy() or bool(self.pending) or self.thread is not None
                       and self.thread.is_alive() and self.session_stop.is_set())
            raw = deepcopy(value)
            root, machine, prepared = self.prepare(action, raw, context, waiting=waiting)
            if raw.get('remember') is True:
                # Only the picture page asks for this; the Agent's own choice leaves the page's menu alone.
                image_model.remember(machine['model_root'], raw.get('model'))
            key = secrets.token_urlsafe(24)
            self.uploads[key] = [name for name in [*(raw.get('images') or []), raw.get('mask')] if isinstance(name, str)]
            while len(self.uploads) > REPORT_LIMIT:
                # Only a job that ended keeps an entry this old: its pictures go with it.
                _, names = self.uploads.popitem(last=False)
                self.remove_uploads(names)
            if waiting:
                self.pending.append(dict(key=key, action=action, value=raw, context=context,
                                         state=dict(phase='queued', queued_behind='video' if video else 'image',
                                                    queue_position=len(self.pending) + 1),
                                         video_since=time.monotonic() if video else None))
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
            model = value.get('model') if isinstance(value, dict) else None
            h3 = model == image_model.H3
            # A model this computer does not run (H3 Image on a Mac) is said so; its files are not looked for.
            if h3 and sys.platform == 'darwin':
                raise ValueError('model_unavailable')
            moss = model == image_model.MOSS
            if not image_pack.ready(machine['model_root'], group=self.model_group(model) or 'image'):
                raise ValueError('pack_missing')
            if (h3 or moss) and action != 'generate':
                raise ValueError('invalid_request')
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
            if h3:
                # What the worker needs to run the video text encoder for this prompt and to plan against the card.
                value['h3'] = dict(encoder=machine.get('encoder'), comfy_root=machine.get('comfy_root'),
                                   comfy_python=machine.get('comfy_python'), model_paths=machine.get('model_paths'),
                                   resident_endpoint=self.resident_endpoint())
            context.update_request(value)
            # H3 Image plans its own residency in the worker; the Qwen plan below does not describe it.
            context.plan = None if h3 or moss else self.preflight(machine, value, fit=not waiting, reading=context.gpu_reading)
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

    def warm(self, value):
        """Get ready for the picture the page is setting up ({model, level, mode}); no prompt or picture is sent.

        ``level`` 'light' (the default: the page was opened or touched) reads the model files ahead and, for H3
        Image, puts the shared text encoder on the card. 'full' (a picture is likely next) also loads the model as a
        picture leaves it. ``mode`` 'eager' (the default) may start a model that is not loaded; 'conservative' only
        keeps ready a model this worker already holds. Never a job and never in the way of one: skipped while video
        or a picture is running or queued, while the card is being given to video, or when the plan says the model
        does not fit. Nothing about a warm-up shows on the page until a picture is asked for.

        ``beside`` (the view was opened, nothing more): a full warm-up only into room that is free; where loading
        the model would give back the video models the resident worker keeps, it reads the files ahead instead."""
        if (not isinstance(value, dict) or value.get('model') not in image_model.MODELS
                or value.get('level', 'light') not in WARM_LEVELS or value.get('mode', 'eager') not in WARM_MODES
                or not isinstance(value.get('beside', False), bool)):
            raise ValueError('invalid_request')
        model, level = value['model'], value.get('level', 'light')
        h3 = model == image_model.H3
        if level == 'full' and value.get('beside'):
            # Read before the mutex: it may ask the resident worker what it holds.
            try:
                if self.takes_video_room(self.installation()[1], model):
                    level = 'light'
            except (OSError, ValueError, RuntimeError):
                level = 'light'

        def skipped(reason):
            return dict(warm='skipped', reason=reason)

        with self.mutex:
            if self.closed:
                return skipped('closed')
            if self.segmenting or self._busy or bool(self.pending):
                return skipped('busy')
            if self.thread is not None and self.thread.is_alive() and self.session_stop.is_set():
                return skipped('busy')
            try:
                video = self.yielding or self.video_busy()
            except Exception:
                video = True
            if video:
                return skipped('video_busy')
            alive = self.thread is not None and self.thread.is_alive()
            if value.get('mode') == 'conservative' and not (alive and self.loaded_model == model):
                return skipped('not_loaded')
            try:
                root, machine = self.installation()
            except (OSError, ValueError):
                return skipped('pack_missing')
            if not image_pack.ready(machine['model_root'], group=self.model_group(model) or 'image'):
                return skipped('pack_missing')
            from .image_policy import UNIFIED_MIN_TOTAL
            unified = self.unified_total(machine)
            if unified is not None and unified < UNIFIED_MIN_TOTAL:
                return skipped('memory')
            row = dict(action='warm', model=model, level=level, keep_seconds=keep.keep_seconds(keep.path(root)))
            if h3:
                row['files'] = self.warm_files(machine)
                row['place_encoder'] = self.h3_resident(machine)
            elif level == 'light':
                row['files'] = self.model_files(machine, model)
            elif model == image_model.MOSS:
                pass    # a MOSS picture is not planned like Qwen's (no preflight): its warm-up is not either
            else:
                try:
                    plan = self.preflight(machine, dict(images=[], width=1024, height=1024))
                except Exception:
                    return skipped('memory')
                if plan is not None and not plan['feasible']:
                    return skipped('memory')
            # The same model already held: the row still goes, to keep it and the encoder ready for longer.
            ready = alive and self.loaded_model == model
            if level == 'full':
                self.loaded_model = model
                self.warm_full.clear()
                self.loading_full = True
            if not alive:
                self.session_stop = threading.Event()
                self.end_reason = None
                self.requests = queue.Queue()
                self.thread = threading.Thread(target=self.work, args=(root, machine),
                                               name='freevideo-image-gen', daemon=True)
            self.requests.put(row)
            if not alive:
                self.thread.start()
            return dict(warm='ready' if ready else 'loading', level=level)

    def unwarm(self):
        """The page left the picture view: the worker goes back to the wait a picture would have left."""
        with self.mutex:
            if self.thread is not None and self.thread.is_alive() and not self.session_stop.is_set():
                self.requests.put(dict(action='unwarm', keep_seconds=keep.keep_seconds(keep.path(self.habit_root))))
        return dict(warm='cancelled')

    def drain_warm(self, session):
        """Read the rows a worker without a job sends about a warm-up; nothing else arrives between jobs."""
        while True:
            try:
                row = session.rows.get_nowait()
            except queue.Empty:
                return
            if row is None:
                # The end of the pipe stays for whoever reads next.
                session.rows.put(None)
                return
            if isinstance(row, dict) and row.get('phase') == 'warmed':
                self.warm_facts = row
                if row.get('level') == 'full':
                    self.warm_full.set()
                    with self.mutex:
                        self.loading_full = False

    def wait_warm(self, timeout):
        """Wait until the worker reports a full warm-up done, the session ends, or ``timeout`` seconds pass; True
        when it reported."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.warm_full.wait(.25):
                return True
            with self.mutex:
                if self.thread is None or not self.thread.is_alive():
                    return False
        return False

    def worker_alive(self):
        """True while a picture worker process runs."""
        with self.mutex:
            process = self.process
        return process is not None and process.poll() is None

    def installed_models(self):
        """The picture models whose files are installed."""
        _, machine = self.installation()
        return [model for model, group in ((image_model.H3, H3_GROUP), (image_model.QWEN, 'image'))
                if image_pack.ready(machine['model_root'], group=group)]

    def place_encoder(self, root, machine, env, descriptor, stop=None):
        """Put H3 Image's text encoder on the card in the resident worker, as it idles after a video, and wait for it.
        ``stop`` (default: the session's) ends it: the warm-up passes one that is also set by a picture asked for
        meanwhile, which loads the encoder itself (a speculative placement can be slower than that load). A video
        or another user of the lease stops the session and with it the placement, so they wait only for the worker
        to yield. Returns the worker's report."""
        from .. import resident_process
        from ..comfy_bridge import source_root
        from ..hardware import detect
        from ..idle_encoder import place_now
        from ..kernel_capabilities import available_backends
        from ..policy import choose
        endpoint = self.resident_endpoint()
        if endpoint is None:
            return dict(state='skipped', reason='no resident worker')
        hardware = detect()
        # From the saved kernel checks, never running them here: a check cannot be stopped for a video or a picture.
        policy = choose(hardware, available_backends=available_backends(hardware, probe_missing=False),
                        allow_capacity_trial=True)
        directory = Path(endpoint).parent
        environment = dict(env, PYTHONPATH=str(source_root()))
        environment.pop(resident_process.ENV, None)
        return place_now(endpoint, dict(encoder=machine.get('encoder'), comfy_root=machine.get('comfy_root'),
                                        model_paths=machine.get('model_paths')),
                         descriptor=descriptor, environment=environment, directory=directory,
                         gpu_budget_bytes=policy.gpu_budget_bytes, ram_budget_bytes=policy.ram_budget_bytes,
                         allocator=policy.allocator_config, limits=dict(system=hardware.system),
                         stop=stop or self.session_stop)

    @staticmethod
    def group_files(machine, group):
        try:
            rows = [image_pack.target(row, machine['model_root']) for row in image_pack.rows(group=group)]
        except Exception:
            return []
        return [str(item) for item in rows if Path(item).is_file()]

    @classmethod
    def qwen_files(cls, machine):
        """The picture model's files to read ahead for a light warm-up of the default model."""
        return cls.group_files(machine, 'image')

    @classmethod
    def model_files(cls, machine, model):
        """The files to read ahead for a picture model: each model's own, from one table."""
        if model == image_model.H3:
            return cls.warm_files(machine)
        if model == image_model.MOSS:
            return cls.group_files(machine, MOSS_GROUP)
        return cls.qwen_files(machine)

    @staticmethod
    def warm_files(machine):
        """H3 Image's picture model files to read ahead into the OS file cache, in the order a picture reads them.
        The encoder is not among them: placing it reads it, and a second copy in the file cache would only crowd it."""
        from .h3_runner import files
        rows = []
        try:
            found = files(Path(image_pack.base(machine['model_root'])).parent)
            rows += [Path(found['dit']), Path(found['vae'])]
        except Exception:
            pass
        return [str(item) for item in rows if item.is_file()]

    def remember(self, key, state):
        self.finished[key] = deepcopy(state)
        while len(self.finished) > REPORT_LIMIT:
            self.finished.popitem(last=False)

    def begin(self, key, root, machine, value, context):
        if self.key is not None:
            self.remember(self.key, self.state)
        self.key = key
        self.started_at = time.time()
        self.stop.clear()
        self.state = dict(phase='starting', stage='start')
        self.preview = None
        if 'fitted' in value:
            self.state['fitted'] = deepcopy(value['fitted'])
        self.context = context
        self._busy = True
        if self.thread is None or not self.thread.is_alive():
            self.session_stop = threading.Event()
            self.end_reason = None
            self.requests = queue.Queue()
            self.thread = threading.Thread(target=self.work, args=(root, machine),
                                           name='freevideo-image-gen', daemon=True)
            self.requests.put(value)
            self.thread.start()
        else:
            self.requests.put(value)

    def discard_queued(self, state, pending=None):
        """Drop one waiting request (the first unless named) and move the ones behind it up."""
        pending = pending or self.pending[0]
        self.drop_uploads(pending['key'])
        self.remember(pending['key'], state)
        # Every way a waiting picture ends without running comes here: cancelled, failed, timed out, or closed.
        from .. import job_record
        job_record.image(pending['key'], state)
        self.pending.remove(pending)
        for place, other in enumerate(self.pending, 1):
            other['state']['queue_position'] = place
        self.queue_wake.set()

    def wait_queued(self):
        while True:
            with self.mutex:
                if not self.pending:
                    # Clearing under the lock lets a new admission start a reader.
                    self.queue_thread = None
                    return
                pending = self.pending[0]
                if self.closed:
                    self.discard_queued(dict(phase='cancelled'))
                    continue
                try:
                    video = self.yielding or self.video_busy()
                except Exception as error:
                    self.discard_queued(self.failed(classify_exception(error), pending['context'], error=error))
                    continue
                for place, other in enumerate(self.pending, 1):
                    other['state']['queued_behind'] = 'video' if video else 'image'
                    other['state']['queue_position'] = place
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
                            self.pending.remove(pending)
                            for place, other in enumerate(self.pending, 1):
                                other['state']['queue_position'] = place
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
        seen = {}

        def read():
            gpu = Gpu(machine.get('gpu_uuid'))
            try:
                memory = gpu.memory()
                if memory is None:
                    return None
                used, total = memory
                if not total or not 0 <= used <= total:
                    return None
                seen.update(total_bytes=total, used_bytes=used, native_credit_bytes=native)
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
                    seen.update(owned_unreported=True, budget_bytes=total)
                    return total
                credit = sum(size for pid, size in rows.items() if pid in owned)
                budget = min(total, total - used + credit + native)
                seen.update(owned_credit_bytes=credit, budget_bytes=budget)
                return budget
            finally:
                gpu.close()

        try:
            budget = open_bounded(read, .5)
        except (OSError, RuntimeError, AttributeError, ValueError):
            return None
        # What the plan was made from, for the report: a refusal at 0 s otherwise says
        # only that the budget was small, not how much of the card was in use.
        self.budget_reading.value = dict(seen) if budget is not None else None
        return budget

    def preflight(self, machine, value, *, fit=True, reading=None):
        self.budget_reading.value = None
        budget = self.memory_budget(machine)
        if reading is not None:
            reading.update(self.budget_reading.value or {})
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
            if code == 'no_memory' and (isinstance(row, dict) and row.get('memory') == 'host'
                                        or error is not None and host_memory_shortage(error)):
                result.update(memory='host', message_zh=HOST_MESSAGE_ZH)
        if code in ('no_memory', 'small_memory'):
            from .failure import append_other_usage
            append_other_usage(result, context.machine)
        if code not in NO_REPORT:
            if video_busy is None:
                try:
                    video_busy = self.yielding or self.video_busy()
                except Exception:
                    video_busy = False
            if context.pack is None and context.machine.get('model_root'):
                try:
                    # The files of the model that was asked for, not always the default one's.
                    asked = context.request.get('model')
                    context.pack = image_pack.status(context.machine['model_root'], group=H3_GROUP) if asked == image_model.H3 else image_pack.status(context.machine['model_root'])
                except Exception:
                    # A missing/broken pack must not prevent reporting the original error.
                    pass
            report = context.report(code, row=row, error=error, video_busy=video_busy,
                                    worker_restarts=self.worker_restarts)
            with self.mutex:
                report_id = getattr(context, 'report_id', None) or uuid.uuid4().hex
                self.reports[report_id] = report
                self.reports.move_to_end(report_id)
                while len(self.reports) > REPORT_LIMIT:
                    self.reports.popitem(last=False)
            result['report_id'] = report_id
        self.record_failure(result, context, error, video_busy)
        return result

    def record_failure(self, result, context, error, video_busy):
        """One line per failed image request in logs/image-failures.jsonl (a short history), so the reason can be read after the
        page is gone. Fixed words and numbers only: no prompt, no picture, no path."""
        try:
            exit_code = getattr(error, 'exit_code', None)
            if type(exit_code) is not int:
                exit_code = (clean_reading(getattr(error, 'exit_reading', None)) or {}).get('exit_code')
            row = dict(time=datetime.now(timezone.utc).isoformat(timespec='seconds'), kind=getattr(context, 'kind', None),
                       error=result.get('error'), stage=result.get('stage'), report_id=result.get('report_id'),
                       worker_exit_code=exit_code if type(exit_code) is int else None,
                       video_busy=bool(video_busy), worker_restarts=self.worker_restarts)
            logs = Path(self.installation()[0]) / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            file = logs / 'image-failures.jsonl'
            lines = file.read_text(encoding='utf-8').splitlines()[-199:] if file.is_file() else []
            temporary = logs / 'image-failures.jsonl.tmp'
            temporary.write_text('\n'.join([*lines, json.dumps(row, ensure_ascii=False)]) + '\n', encoding='utf-8')
            os.replace(temporary, file)
        except Exception:
            pass  # a log that cannot be written must never change the failure the user sees

    def report(self, report_id):
        if (not isinstance(report_id, str) or len(report_id) != 32
                or any(char not in '0123456789abcdef' for char in report_id)):
            return None
        with self.mutex:
            report = self.reports.get(report_id)
            context = self.context
            if report is None and context is not None and getattr(context, 'report_id', None) == report_id and self._busy:
                report = self.progress_report(context)
            # Callers cannot mutate a retained report through an in-process reference.
            return json.loads(json.dumps(report, allow_nan=False)) if report is not None else None

    def forecast_facts(self, context):
        forecast = getattr(context, 'forecast', None)
        if forecast is None:
            return None
        return dict(basis=forecast.kind, typical_seconds=round(sum(forecast.predicted.values()), 1),
                    predicted={stage: round(seconds, 1) for stage, seconds in forecast.predicted.items()})

    def progress_report(self, context):
        """The report of a request that is still running: what has happened so far."""
        return context.report('running', outcome='running', worker_restarts=self.worker_restarts,
                              forecast=self.forecast_facts(context))

    def start_forecast(self, context, value, habit):
        """The stage-time forecast of this request: this machine's earlier runs of the same size, else priors."""
        try:
            images = value.get('images') or []
            key = progress_model.key_for(value.get('model') or '', value['width'], value['height'], len(images))
            history = progress_model.History(Path(habit).with_name('image-stage-times.json'))
            predicted, kind = history.predict(key, bool(images))
            context.forecast = progress_model.Forecast(predicted, kind)
            context.forecast.update('load', 0.)
            context.forecast_key, context.forecast_history = key, history
        except (KeyError, TypeError, ValueError, OSError):
            context.forecast = None

    def keep_forecast(self, context):
        if getattr(context, 'forecast', None) is None:
            return
        seconds = {row['stage']: row['seconds'] for row in context.timing.snapshot()}
        context.forecast_history.record(context.forecast_key, seconds)

    def keep_preview(self, row):
        """Keep the worker's newest step picture; the status row says which one it is, and `preview_image` returns it."""
        from .step_preview import LIMIT
        step, total, width, height, data = (row.get(name) for name in ('step', 'total', 'width', 'height', 'jpeg'))
        if not (type(step) is int and type(total) is int and 1 <= step <= total <= 64
                and type(width) is int and type(height) is int and 1 <= width <= 1024 and 1 <= height <= 1024
                and isinstance(data, str) and len(data) <= LIMIT):
            return
        import base64
        try:
            jpeg = base64.b64decode(data, validate=True)
        except ValueError:
            return
        if not jpeg.startswith(b'\xff\xd8'):
            return
        with self.mutex:
            if self.state.get('phase') not in ('starting', 'progress', 'cancelling'):
                return
            serial = (self.preview or {}).get('serial', 0) + 1
            self.preview = dict(serial=serial, jpeg=jpeg)
            self.state['preview'] = dict(step=step, total=total, serial=serial, width=width, height=height)

    def preview_image(self, key):
        """(serial, JPEG bytes) of the running job's newest step picture, or None."""
        with self.mutex:
            if (isinstance(key, str) and self.key is not None and self.preview is not None
                    and secrets.compare_digest(key.encode('utf-8'), self.key.encode('utf-8'))):
                return self.preview['serial'], self.preview['jpeg']
        return None

    def status(self, key):
        with self.mutex:
            if isinstance(key, str):
                def matches(candidate):
                    return candidate is not None and secrets.compare_digest(key.encode('utf-8'), candidate.encode('utf-8'))
                if matches(self.key):
                    row = deepcopy(self.state)
                    forecast = getattr(self.context, 'forecast', None)
                    if forecast is not None and row.get('phase') in ('progress', 'cancelling'):
                        row.update(forecast.view())
                    if row.get('phase') in ('progress', 'cancelling') and getattr(self.context, 'report_id', None):
                        row['report_id'] = self.context.report_id
                    if row.get('phase') in ('complete', 'failed', 'cancelled'):
                        self.drop_uploads(self.key)
                    return row
                for waiting in self.pending:
                    if matches(waiting['key']):
                        return deepcopy(waiting['state'])
                for candidate, state in self.finished.items():
                    if matches(candidate):
                        self.drop_uploads(candidate)
                        return deepcopy(state)
            raise ValueError('unknown_job')

    def drop_uploads(self, key):
        """Remove the reference pictures and mask the page uploaded for a finished job; nothing else is touched."""
        self.remove_uploads(self.uploads.pop(key, []))

    def discard_uploads(self, names):
        """Remove page uploads that never became a job: the page turned the request away or was stopped before
        it was sent. A name that a job holds stays until that job ends."""
        if not isinstance(names, list) or len(names) > H3_MAX_IMAGES + 1:
            raise ValueError('invalid_request')
        with self.mutex:
            held = {name for kept in self.uploads.values() for name in kept}
            self.remove_uploads([name for name in names if name not in held])
            drafts = [name for name in names if isinstance(name, str) and DRAFT_NAME.fullmatch(name) and name not in held]
        from .. import private_store
        for name in drafts:
            private_store.safe_remove(self.input_directory(), name)
        return dict(discarded=True)

    def remove_uploads(self, names):
        from .. import private_store
        try:
            directory = self.input_directory()
        except (OSError, ValueError):
            return
        for name in names:
            if isinstance(name, str) and PAGE_UPLOAD.fullmatch(name):
                private_store.safe_remove(directory, name)

    def cancel(self, key):
        with self.mutex:
            row = self.status(key)
            if row['phase'] == 'queued':
                mine = next(w for w in self.pending if secrets.compare_digest(key.encode('utf-8'), w['key'].encode('utf-8')))
                self.discard_queued(dict(phase='cancelled'), mine)
                return dict(phase='cancelled')
            if key != self.key or not self.busy():
                return row
            self.stop.set()
            self.state['phase'] = 'cancelling'
            return dict(phase='cancelling')

    def stop_for_generation(self, only_idle=False, video=None):
        """Reap even an in-flight spawn before video acquires the runtime lease.

        ``only_idle`` is for other users of the lease (the prompt model): they get it
        back from an idle worker but never cut off an image that is being made.
        ``video`` (width, height, seconds, steps, two_pass, refine, step_cache, upscale of a text-to-video request)
        lets an idle picture worker stay when the planner places that video the same beside it.
        """
        with self.mutex:
            if only_idle and (self.busy() or (self.segment_worker is None and
                    (self.thread is None or not self.thread.is_alive()))):
                return False
            if not only_idle and self.keeps_through_video() and self.video_fits(video):
                # The worker holds no lease and no job: the video takes the lease and the picture model stays.
                server_mode.note_keep('image', True, reason='video_start')
                return False
            self.yielding = True
            if self.thread is not None and self.thread.is_alive():
                # Only a session that is running ends for this; with none, a reason left here would be given to the
                # next session's end, whatever ended it.
                self.end_reason = 'video_start' if not only_idle else 'lease_needed'
            self.segment_stop.set()
            self.stop.set()
            self.session_stop.set()
            for waiting in self.pending:
                waiting['state']['queued_behind'] = 'video'
                if waiting['video_since'] is None:
                    waiting['video_since'] = time.monotonic()
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

    def keeps_through_video(self):
        """With co-residency (server_mode.coresident), whether the picture worker stays while a video runs: it is
        between jobs (no picture, no placement, no model load asked for or under way), so it holds no lease. Called
        under the mutex."""
        return (self.per_request and server_mode.coresident() and not self._busy and not self.placing
                and not self.segmenting and self.segment_worker is None and not self.pending
                and self.thread is not None and self.thread.is_alive() and not self.session_stop.is_set()
                and self.worker is not None and self.requests.empty()
                and not self.loading_full)

    def video_fits(self, video):
        """Whether the video is planned the same beside the idle picture worker (its card and host memory held) as
        without it. Server mode keeps the worker without a request to plan (the start of the server). Called under
        the mutex."""
        if video is None:
            return server_mode.enabled()
        try:
            from ..background_room import Gpu, family
            from ..comfy_capabilities import _machine_context
            from ..other_usage import _rss, fits_beside
            # machine.json keeps the card and host totals only in its setup plan; the planner needs them, as the
            # capabilities route reads them.
            machine = _machine_context()
            pids = family(self.process.pid)
            gpu = Gpu(machine.get('gpu_uuid'))
            try:
                rows = gpu.processes()
            finally:
                gpu.close()
            if rows is None or any(rows.get(pid) is None for pid in pids if pid in rows):
                return False
            vram = sum(rows[pid] for pid in pids if pid in rows)
            fits = fits_beside(machine, vram, _rss(pids), **video)
        except Exception:  # an unreadable card or plan keeps the earlier behaviour: the picture worker goes
            logging.warning('[FreeVideo] Could not plan the video beside the picture model', exc_info=True)
            return False
        server_mode.note_keep('image', fits, reason='video_start', card_bytes=vram)
        return fits

    @staticmethod
    def lease(path, descriptor):
        """The GPU lease for one step of a session: the session's own on the desktop, else taken for this step."""
        if descriptor is not None:
            return contextlib.nullcontext(descriptor)
        from ..locking import runtime_lock
        return runtime_lock(path, inherit=False, wait_seconds=LEASE_WAIT_SECONDS)

    def close(self):
        with self.mutex:
            self.closed = True
            while self.pending:
                self.discard_queued(dict(phase='cancelled'))
            queue_thread = self.queue_thread
        self.stop_for_generation()
        if queue_thread is not None and queue_thread is not threading.current_thread():
            queue_thread.join()
        with self.mutex:
            names, self.segment_inputs = list(self.segment_inputs), OrderedDict()
        self.remove_uploads(names)

    def resident_endpoint(self):
        """The video worker's endpoint when it is alive, so the image worker's text encoding can reuse its resident encoder."""
        from .. import resident_process
        owner = resident_process.OWNER
        if owner.process is not None and owner.process.poll() is None and owner.endpoint:
            return str(owner.endpoint)
        return None

    def environment(self, root):
        from ..comfy_bridge import source_root
        from ..comfy_environment import isolated_environment
        from .. import processes, resident_process
        env = isolated_environment(root, source_root())
        for name in ('FREEVIDEO_RUNTIME_LOCK_FD', processes.HANDLE_ENV, resident_process.ENV,
                     'FREEVIDEO_PROMPT_PRESTART_WORKER'):
            env.pop(name, None)
        return native_allocator(env)

    def takes_video_room(self, machine, model):
        """Whether loading ``model`` now would give back what the kept video worker holds, as a picture session does
        when it starts: the worker holds more than H3 Image's shared text encoder (or the model is not H3 Image)
        and the card has no room for both."""
        from .. import resident_process
        owner = resident_process.OWNER
        if owner.active or owner.process is None or owner.process.poll() is not None or not owner.endpoint:
            return bool(owner.active)
        if model == image_model.H3 and self.h3_resident(machine):
            held = resident_process.status({resident_process.ENV: str(owner.endpoint)})
            if isinstance(held, dict) and all(row.get('role') == 'encoder' for row in held.get('models') or []):
                return False
        return not self.server_keeps_video(machine)

    def server_keeps_video(self, machine):
        """With co-residency, whether the video models in the resident worker stay while the picture is made: the
        card has room for the picture model's files and the picture's reserve beside everything on it now. Where it
        has not (a 24 GB card), they go as before."""
        if not server_mode.coresident():
            return False
        from ..resident_process import gpu_memory
        memory = gpu_memory(machine.get('gpu_uuid'))
        if not memory:
            return False
        # Which picture model comes is not known yet when the session starts: the larger installed one counts.
        sizes = []
        for files in (self.model_files(machine, model) for model in image_model.MODELS):
            try:
                sizes.append(sum(os.path.getsize(item) for item in files))
            except OSError:
                return False
        need = max(sizes) + keep.RESERVE_BYTES
        keeps = max(sizes) > 0 and memory[1] - memory[0] >= need
        server_mode.note_keep('image', keeps, free=memory[1] - memory[0], need=need)
        return keeps

    @staticmethod
    def h3_resident(machine):
        """Whether H3 Image is installed on a card whose encoder the resident worker can hold."""
        from ..comfy_bridge import source_root
        return (sys.platform != 'darwin' and machine.get('device_backend', 'cuda') == 'cuda'
                and image_pack.ready(machine['model_root'], group=H3_GROUP)
                and (Path(source_root())/'freevideo_engine'/'resident_worker.py').is_file())

    @staticmethod
    def start_resident(root, machine, env):
        """Start (or reuse) the resident worker for the picture session; it encodes text and needs no video weights."""
        from ..comfy_bridge import source_root
        from .. import resident_process
        return resident_process.OWNER.start(root, source_root(), machine['comfy_python'], env)

    def spawn(self, root, machine, env, descriptor):
        from .. import processes
        command = processes.module_command('freevideo_engine.image_gen.worker', image_pack.base(machine['model_root']),
                                           '--device', machine.get('device_backend', 'cuda'),
                                           '--idle-seconds', self.idle_seconds)
        if server_mode.coresident():
            # The worker stays until this installation stops or a program outside it needs the card or host memory;
            # its own processes (the video worker) are not "another program" that makes it leave.
            command += ['--server-root', str(os.getpid())]
        command[0] = machine['comfy_python']
        from ..exit_diagnostics import CRASH_FILE_ENV, new_crash_file
        crash_file = None
        try:
            crash_file = new_crash_file()
        except OSError:
            pass
        if descriptor is None:
            # Co-residency: the worker takes the lease itself for each picture and model load.
            command += ['--lock-path', str(Path(root) / 'engine.lock')]
        else:
            env = dict(env, FREEVIDEO_RUNTIME_LOCK_FD=str(descriptor))
        if crash_file:
            env[CRASH_FILE_ENV] = crash_file
        process = processes.popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                  pass_fds=() if descriptor is None else (descriptor,), supervise=True,
                                  start_new_session=True)
        process.crash_file = crash_file
        return process

    def work(self, root, machine):
        from ..locking import runtime_lock
        from .. import processes, resident_process
        self.habit_root = root
        session = None
        started_resident = False
        # The most host memory the worker tree took while it waited between jobs, for bringing it back (rewarm).
        host_seen, host_read_at = 0, 0.
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
            from .. import video_warm
            video_warm.stop()
            IDLE.stop()
            from ..comfy_residency import release_native
            try:
                released = release_native(comfy_server())
            except Exception:  # only frees memory early; the picture's own budget still decides
                logging.warning('[FreeVideo] Could not unload ComfyUI models before the picture', exc_info=True)
                released = None
            if released is not None:
                logging.info('[FreeVideo] Unloaded ComfyUI models before the picture: %s', released)
                with self.mutex:
                    if self.context is not None:
                        self.context.native_release = released
            per_request = server_mode.coresident()
            lock_path = Path(root) / 'engine.lock'
            with (contextlib.nullcontext(None) if per_request else runtime_lock(lock_path, inherit=False)) as descriptor:
                with self.mutex:
                    self.per_request = per_request
                try:
                    if self.session_stop.is_set():
                        return
                    env = self.environment(root)
                    owner = resident_process.OWNER
                    # H3 Image encodes in the resident worker, which then keeps the text encoder between pictures.
                    keep_encoder = self.h3_resident(machine)
                    if owner.process is not None and owner.process.poll() is None and owner.endpoint:
                        env[resident_process.ENV] = str(owner.endpoint)
                        held = resident_process.status(env) if keep_encoder else None
                        # While the card holds only the encoder nothing is given back, so a picture finds it ready.
                        # Video models in the worker compete with the picture for the card, so those go.
                        foreign = not isinstance(held, dict) or any(
                            row.get('role') != 'encoder' for row in held.get('models') or [])
                        try:
                            if (not keep_encoder or foreign) and not self.server_keeps_video(machine):
                                with self.lease(lock_path, descriptor) as held:
                                    try:
                                        resident_process.release_idle_cache(env, held, keep=('encoder',) if keep_encoder else ())
                                    except resident_process.SessionRestarted:
                                        raise
                                    except RuntimeError:
                                        if not keep_encoder:
                                            raise
                                        # A worker from before partial release: give everything back as before.
                                        resident_process.release_idle_cache(env, held)
                                resident_process.OWNER.record(action='trimmed' if keep_encoder else 'released', reason='image_model')
                        except resident_process.SessionRestarted:
                            pass
                        env.pop(resident_process.ENV, None)
                    if keep_encoder:
                        env[resident_process.ENV] = self.start_resident(root, machine, env)
                        started_resident = True
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
                            self.drain_warm(session)
                            if time.monotonic() - host_read_at >= HOST_READ_SECONDS:
                                host_read_at = time.monotonic()
                                host_seen = max(host_seen, resident_process.worker_memory(
                                    session.process.pid).get('worker_rss_bytes') or 0)
                            with self.mutex:
                                if session.process.poll() is not None and self.requests.empty():
                                    self.session_stop.set()
                                    break
                            continue
                        if session.process.poll() == 0:
                            respawn()
                        if value.get('action') in ('warm', 'unwarm'):
                            place = value.pop('place_encoder', False)
                            # H3 Image loads its picture model only once the encoder is placed: the room left on the
                            # card beside the encoder decides how much of it stays there.
                            later = value.get('level') == 'full' and value.get('model') == image_model.H3
                            try:
                                session.send(**(dict(value, level='light') if later else value))
                            except OSError:
                                pass
                            if place and not self.session_stop.is_set() and not self._busy:
                                from .h3_pipeline import placed_encoder_bytes
                                # The placement is speculative: whatever goes wrong in it, the session and a picture
                                # that arrived meanwhile carry on (the picture places the encoder itself).
                                try:
                                    endpoint = self.resident_endpoint()
                                    if endpoint is not None and placed_encoder_bytes(endpoint) > 0:
                                        placed = dict(state='kept', reason='The encoder is already on the card')
                                    else:
                                        with self.mutex:
                                            self.placing = True
                                        try:
                                            with self.lease(lock_path, descriptor) as held:
                                                placed = self.place_encoder(root, machine, env, held, stop=AnyOf(
                                                    self.session_stop.is_set, lambda: self._busy))
                                        finally:
                                            with self.mutex:
                                                self.placing = False
                                        if self._busy and placed.get('state') != 'ready':
                                            placed = dict(placed, state='stopped', reason='A picture was asked for')
                                except Exception as error:  # noqa: BLE001
                                    from ..failure_details import raised_at
                                    logging.warning('[FreeVideo] Encoder placement for the warm-up failed: %s at %s',
                                                    type(error).__name__, raised_at(error))
                                    placed = dict(state='failed', reason='Encoder placement failed')
                                with self.mutex:
                                    self.warm_placement = {k: placed.get(k) for k in ('state', 'reason', 'elapsed_seconds')}
                            # A picture asked for meanwhile loads its model itself.
                            if later and not self.session_stop.is_set() and not self._busy:
                                if session.process.poll() == 0:
                                    # The worker left at its idle limit while the encoder was placed.
                                    respawn()
                                try:
                                    session.send(**dict(value, files=[], h3=dict(resident_endpoint=self.resident_endpoint())))
                                except OSError:
                                    pass
                            continue
                        self.loaded_model = value.get('model') or image_model.QWEN
                        with self.mutex:
                            # A picture loads the model itself; a warm-up it overtook is not reported.
                            self.loading_full = False
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
                        # A worker without a job (idle, or warming for the page) has nothing to finish: it is
                        # not waited for, so video starts as soon as it would without it.
                        self.drain_warm(session)
                        with self.mutex:
                            grace = 5 if self._busy else 0
                        try:
                            if session.process.poll() is None:
                                session.send(action='close')
                                session.process.wait(timeout=grace or .2)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                        processes.stop(session.process, grace=5 if grace else .5)
                        session.process.wait()
                        session.close()
                    # Every end of the picture worker is recorded with its reason, for the running status: 'crowded'
                    # when it left because another program needed the card or host memory, 'idle' after an idle
                    # time (no model held, or where it does not keep one with no time limit).
                    reason = self.end_reason or ('closed' if self.closed else ('crowded' if keep.CROWDED_LINE in session.tail else 'idle')
                                                 if session is not None and session.process.poll() == 0 else 'worker_exit')
                    self.end_reason = None
                    # The model it held comes back when the release only made room (rewarm).
                    server_mode.released('image', dict(action='released', reason=reason, held=self.loaded_model,
                                                       worker_rss_bytes=host_seen or None))
                    if started_resident and not resident_process.OWNER.active and not server_mode.coresident():
                        # The picture session started this worker only to hold the encoder between pictures
                        # (with co-residency it stays: it is the video worker too).
                        resident_process.OWNER.release('image_session_end')
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
                self.loaded_model = None
                self.per_request = False
                self.loading_full = False
                self.session_stop.set()
                if self._busy:
                    context = self.context or Context('generate', {})
                    fitted = self.state.get('fitted')
                    self.state = (dict(phase='cancelled') if self.stop.is_set()
                                  else self.failed(failure, context, error=failure_error,
                                                   video_busy=failure == 'busy_video'))
                    if fitted is not None:
                        self.state['fitted'] = fitted
                    self.preview = None
                    self._busy = False
                    self.context = None
                    from .. import job_record
                    job_record.image(self.key, self.state, started_at=getattr(self, 'started_at', None))
                    # The worker is gone, so nothing reads the job's pictures any more.
                    self.drop_uploads(self.key)
                elif failure_error is not None:
                    # Without a job (a warm-up for the page) nothing shows on the page; the run log keeps why.
                    logging.warning('[FreeVideo] Picture worker stopped without a job (%s): %s', failure, failure_error)
                self.queue_wake.set()

    def shrink(self, value, row, context):
        """After a memory error while working on the picture, step the size down for one more try.

        The ladder is the one start-up fitting uses (32-pixel grid, 512 pixels at the least);
        the step grows with the size so a big canvas needs few tries. False at the smallest size.
        """
        if (value.get('model') in (image_model.H3, image_model.MOSS) or row.get('code') != 'no_memory'
                or row.get('stage') in ('load', 'text', 'encode')
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
    def result_size(value):
        """The size of the picture the worker writes: the requested one, except for a region edit, which is made at the
        requested (or memory-fitted) size and blended over the picture it edits, so it keeps that picture's size."""
        if value.get('mask') and value.get('images'):
            from PIL import Image
            try:
                with Image.open(value['images'][0]) as picture:
                    return picture.size
            except (OSError, Image.DecompressionBombError):
                pass
        return value['width'], value['height']

    @staticmethod
    def worker_gone(session, heard, requested_stop=False, loading=False):
        """The error for a worker pipe that ended; it carries the exit code for the report.

        ``loading`` is true while the worker has reported nothing beyond loading its weights.
        """
        error = Service.worker_gone_error(session, heard, loading=loading, requested_stop=requested_stop)
        code = getattr(error, 'exit_code', None)
        if code is not None:
            # The worker cannot report its memory once it is gone: read this process's view now.
            from ..exit_diagnostics import exit_reading, memory_sample, read_crash_stack
            reading = dict(exit_reading(code, requested_stop=requested_stop), memory=memory_sample())
            kinds = getattr(error, 'stderr_kinds', None)
            if kinds:
                reading['stderr_kinds'] = kinds
            if reading.get('exit_class') in ('native_crash', 'abort'):
                stack = read_crash_stack(getattr(session.process, 'crash_file', None))
                if stack:
                    reading['native_stack'] = stack
            error.exit_reading = reading
        return error

    @staticmethod
    def worker_gone_error(session, heard, loading=False, requested_stop=False):
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
        from ..exit_diagnostics import stderr_kinds
        kinds = stderr_kinds(session.tail)
        session.tail = ''
        message = 'worker_exit (exit code %d%s)' % (code, ', ' + kind if kind else '')
        # A worker that ended with an error before answering never ran the request; one
        # fresh worker gets it, and a second exit is reported with its own last words.
        # A native crash while the weights were still loading ran nothing of the request either.
        from ..exit_diagnostics import exit_reading
        crashed_loading = (loading and code != 0 and not requested_stop
                           and exit_reading(code).get('exit_class') == 'native_crash')
        error = (WorkerEarlyExit if (not heard and code > 0) or crashed_loading else RuntimeError)(message)
        error.exit_code = code
        error.stderr_kinds = kinds
        return error

    def infer(self, session, value):
        # The readings taken while the worker runs are what a report needs when the system ends it.
        context = self.context
        sampler = None
        if context is not None:
            try:
                sampler = context.memory_sampler or MemorySampler(context)
                context.memory_sampler = sampler
                sampler.start(session)
            except Exception:
                sampler = None
        try:
            return self.run_inference(session, value)
        finally:
            if sampler is not None:
                sampler.stop()

    def run_inference(self, session, value):
        context = self.context or Context('generate', value)
        heard = False
        habit = keep.path(self.habit_root)
        if 'keep_seconds' not in value:
            keep.note('image', habit)
        # How long the worker may wait for the next picture; it applies that only with the whole model on the card.
        value['keep_seconds'] = keep.keep_seconds(habit)
        context.report_id = uuid.uuid4().hex
        self.start_forecast(context, value, habit)
        if isinstance(value.get('h3'), dict):
            # The picture session may have started the resident worker after this request was prepared.
            value['h3']['resident_endpoint'] = self.resident_endpoint()
        loading = True
        limit = {image_model.H3: H3_INFERENCE_TIMEOUT, image_model.MOSS: MOSS_INFERENCE_TIMEOUT}.get(value.get('model'), INFERENCE_TIMEOUT)
        try:
            session.send(action='generate', **value)
        except OSError:
            raise self.worker_gone(session, heard, self.stop.is_set())
        cancelled_at = None
        deadline = time.monotonic() + limit
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
                raise self.worker_gone(session, heard, self.stop.is_set(), loading=loading)
            phase = row.get('phase')
            # The end of a warm-up sent before this picture is no answer to it: a worker that leaves at its idle limit
            # right after one never read the picture, which then goes to a fresh worker.
            heard = heard or phase != 'warmed'
            if phase == 'progress':
                stage, fraction = row.get('stage'), row.get('fraction')
                loading = loading and stage in ('start', 'load')
                if (stage in STAGES and type(fraction) in (int, float)
                        and math.isfinite(fraction) and 0 <= fraction <= 1):
                    # The limit is for a worker that stops reporting, not for a slow first load.
                    deadline = time.monotonic() + limit
                    with self.mutex:
                        if self.state.get('stage') == stage and type(self.state.get('fraction')) in (int, float):
                            fraction = max(fraction, self.state['fraction'])
                        context.timing.enter(stage)
                        context.note_progress(stage, fraction)
                        if getattr(context, 'forecast', None) is not None:
                            context.forecast.update(stage, float(fraction))
                        self.state.update(phase='cancelling' if self.stop.is_set() else 'progress',
                                          stage=stage, fraction=fraction)
            elif phase == 'warmed':
                # The end of a warm-up the queue sent before this picture; the picture's own rows follow.
                pass
            elif phase == 'preview':
                self.keep_preview(row)
            elif phase == 'plan':
                context.note_plan(row.get('plan'))
            elif phase in ('result', 'error'):
                if self.stop.is_set() or row.get('code') == 'cancelled':
                    result = dict(phase='cancelled')
                elif phase == 'error' and self.shrink(value, row, context):
                    # The memory estimate was optimistic for this machine: same worker, next size down.
                    session.send(action='generate', **value)
                    deadline = time.monotonic() + limit
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
                        if image.format != ('JPEG' if path.suffix == '.jpg' else 'PNG') or image.size != self.result_size(value):
                            raise RuntimeError('worker_exit')
                        image.verify()
                    seconds = row.get('seconds')
                    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                        raise RuntimeError('worker_exit')
                    self.keep_forecast(context)
                    self.note_model(path, value.get('model') or image_model.QWEN)
                    self.note_record(path, getattr(context, 'work_record', None))
                    self.copy_to_output(path)
                    receipts = row.get('receipts')
                    from ..private_refs import hold
                    hold('image:' + path.name, value.get('images') or (), input_root=self.input_directory(),
                         receipts=[r for r in receipts if isinstance(r, str)] if isinstance(receipts, list) else ())
                    report_id = getattr(context, 'report_id', None)
                    if report_id:
                        done = context.report('succeeded', row=row, outcome='succeeded', worker_restarts=self.worker_restarts,
                                              forecast=self.forecast_facts(context))
                        with self.mutex:
                            self.reports[report_id] = done
                            self.reports.move_to_end(report_id)
                            while len(self.reports) > REPORT_LIMIT:
                                self.reports.popitem(last=False)
                    result = dict(phase='complete', file='freevideo-image/' + path.name,
                                  seconds=seconds, plan=row.get('plan'), report_id=report_id)
                    if isinstance(row.get('attention'), dict):
                        result['attention'] = row['attention']
                with self.mutex:
                    if 'fitted' in self.state:
                        result['fitted'] = self.state['fitted']
                    self.state = result
                    self.preview = None
                    self._busy = False
                    from .. import job_record
                    job_record.image(self.key, result, started_at=getattr(self, 'started_at', None))
                    self.context = None
                    # Remove the job's uploads now, not when the page next asks: a page that was stopped,
                    # closed or reloaded never asks again.
                    self.drop_uploads(self.key)
                    self.queue_wake.set()
                return


def stop_prestart_for_generation(server, video=None):
    service = getattr(server, '_freevideo_image_gen', None)
    if service is not None:
        service.stop_for_generation(video=video)


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

    service = Service(installation, folder_paths.get_input_directory, video_busy=video_busy, folders=folder_paths)
    server._freevideo_image_gen = service

    def works_deleted(names):
        # A deleted picture's library card goes with it, whichever page deleted it and even if that page is closed;
        # an Agent job that made it keeps no prompt or pictures of it.
        jobs = getattr(server, '_freevideo_agent', None)
        if jobs is not None and hasattr(jobs, 'forget_works'):
            try:
                jobs.forget_works(list(names))
            except Exception:
                logging.warning('[FreeVideo] Could not update the Agent jobs of a deleted picture.', exc_info=True)
        cast = getattr(jobs, 'cast', None)
        if cast is None:
            return
        wanted = set(names)
        with cast.changed:
            cards = [record['card']['id'] for record in list(cast.records.values())
                     if record['card'].get('kind') == 'image' and record['card'].get('description') in wanted]
        for card in cards:
            try:
                cast.delete(card, by='user')
            except Exception:
                logging.warning('[FreeVideo] Could not remove the library card of a deleted picture.', exc_info=True)
    service.on_works_deleted = works_deleted

    def works_in_use(names):
        cast = getattr(getattr(server, '_freevideo_agent', None), 'cast', None)
        if cast is None:
            return {}
        wanted, found = set(names), {}
        with cast.changed:
            for record in list(cast.records.values()):
                card = record['card']
                if card.get('kind') == 'image' or card.get('tombstone'):
                    continue
                for key in ('images', 'candidates'):
                    for row in card.get(key) or []:
                        file = row.get('media', {}).get('file') if isinstance(row, dict) and isinstance(row.get('media'), dict) else None
                        if file in wanted:
                            found.setdefault(file, [])
                            if card.get('name') not in found[file]:
                                found[file].append(card.get('name') or '')
        return found
    service.works_in_use = works_in_use

    def clean_page_uploads():
        # A deletion an earlier run did not finish; then pictures of jobs that ended while no one here saw them end,
        # again every few hours.
        try:
            service.finish_trash(everything=True)
        except Exception:
            logging.warning('[FreeVideo] Could not finish deleting pictures.', exc_info=True)
        while not service.closed:
            try:
                removed = service.clean_page_uploads()
                if removed:
                    logging.info('[FreeVideo] Removed %d pictures left by image requests that had ended.', removed)
            except Exception:
                logging.warning('[FreeVideo] Could not remove pictures left by ended image requests.', exc_info=True)
            try:
                # Small copies of pictures that went while the engine was stopped do not stay (a listing also does this).
                from ..work_thumbnails import prune_pictures
                prune_pictures(service.input_directory())
            except Exception:
                logging.warning('[FreeVideo] Could not remove the small copies of removed pictures.', exc_info=True)
            time.sleep(6 * 3600)
    threading.Thread(target=clean_page_uploads, name='freevideo-image-page-uploads', daemon=True).start()
    from .. import video_warm
    videos = video_warm.warmer(installation, busy=lambda: video_busy() or service.busy())
    token = secrets.token_urlsafe(24)
    if server_mode.enabled() or server_mode.keeps_idle():
        # Models load as FreeVideo starts, once ComfyUI has finished starting (its queue answers): all of them on a
        # server, the kinds used lately on a desktop.
        def load_at_start():
            time.sleep(START_DELAY_SECONDS)
            logging.info('[FreeVideo] Loading the models at start: %s', server_mode.warm_at_start())
        threading.Thread(target=load_at_start, name='freevideo-server-start', daemon=True).start()

    @server.routes.get('/freevideo/image-gen')
    async def info(request):
        try:
            return web.json_response(dict(service.info(), token=token))
        except (OSError, ValueError):
            return web.json_response(dict(error='setup_required'), status=400)
        except image_pack.PackError as error:
            # Never an empty 500: the reason and the next step, in both languages.
            from ..failure_details import image_pack_error_code, image_pack_failure
            code = image_pack_error_code(error)
            return web.json_response(dict(error=code, message_en=image_pack_failure(code)['detail'],
                                          message_zh=image_pack_failure(code, zh=True)['detail']), status=409)
        except Exception:
            # A damaged model folder or pack record must not turn into a plain-text 500 that blocks every later action.
            logging.getLogger(__name__).exception('image info failed')
            return web.json_response(dict(error='model_unreadable', message_zh=MESSAGE_ZH['model_unreadable']), status=500)

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

    @server.routes.post('/freevideo/video/{action}')
    async def video_action(request):
        """The creation panel's warm-up: {level, mode} only. The panel asks through ComfyUI's own request helper,
        as for its other /freevideo calls, without the image view's page token: the request carries nothing but
        the level and the mode, and the worst it can do is read the video model ahead."""
        try:
            if request.content_length and request.content_length > 1024:
                raise ValueError('invalid_request')
            value = json.loads(await request.content.read(1025) or b'{}')
            name = request.match_info['action']
            if name == 'warm':
                result = await asyncio.to_thread(videos.warm, value)
            elif name == 'warm-cancel':
                result = await asyncio.to_thread(videos.cancel)
            else:
                return web.json_response(dict(error='unknown_action'), status=404)
        except (ValueError, TypeError):
            return web.json_response(dict(error='invalid_request'), status=400)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/image-gen/{action}')
    async def action(request):
        header = request.headers.get('X-FreeVideo-Image', '')
        if not secrets.compare_digest(header.encode('utf-8'), token.encode('utf-8')):
            return web.json_response(dict(error='reload'), status=403)
        value = None
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
                result = page_views(value.get('description'), value.get('kind', 'character'))
            elif name == 'status':
                result = service.status(value.get('job'))
            elif name == 'preview':
                found = service.preview_image(value.get('job'))
                if found is None:
                    return web.json_response(dict(error='no_preview'), status=404, headers={'Cache-Control': 'no-store'})
                return web.Response(body=found[1], content_type='image/jpeg',
                                    headers={'Cache-Control': 'no-store', 'X-FreeVideo-Preview-Serial': str(found[0])})
            elif name == 'cancel':
                result = service.cancel(value.get('job'))
            elif name == 'warm':
                result = await asyncio.to_thread(service.warm, value)
            elif name == 'warm-cancel':
                result = service.unwarm()
            elif name == 'discard-uploads':
                result = service.discard_uploads(value.get('images'))
            elif name in ('pack-install', 'pack-cancel'):
                result = service.pack_action(name.split('-')[1], model=value.get('model'))
            elif name in ('segment-install', 'segment-cancel'):
                result = service.pack_action(name.split('-')[1], group='segment')
            elif name == 'segment':
                result = await asyncio.to_thread(service.segment, value)
            elif name == 'face-box':
                result = await asyncio.to_thread(service.face_box, value)
            elif name == 'history':
                result = service.history(value.get('offset', 0), value.get('limit'))
            elif name == 'history-delete':
                result = await asyncio.to_thread(service.history_delete, value.get('file'))
            elif name == 'history-delete-many':
                result = await asyncio.to_thread(service.history_delete_many, value.get('files'))
            elif name == 'history-save':
                result = await asyncio.to_thread(service.history_save, value)
            elif name == 'history-trash':
                result = await asyncio.to_thread(service.history_trash, value.get('files'))
            elif name == 'history-undo':
                result = await asyncio.to_thread(service.history_undo, value.get('batch'))
            elif name == 'history-open':
                if request.remote not in ('127.0.0.1', '::1'):
                    raise ValueError('invalid_request')
                result = await asyncio.to_thread(service.history_open)
            else:
                result = await asyncio.to_thread(service.start, name, value)
            return web.json_response(result)
        except FailureError as error:
            return web.json_response(error.status, status=400)
        except (ValueError, OSError) as error:
            code = str(error)
            if code in ('unknown_job', 'closed'):
                result = dict(error=code)
            elif isinstance(error, ValueError):
                code = code if code in ERRORS else 'invalid_request'
                result = dict(phase='failed', error=code, report_id=None, stage='start')
                if code in MESSAGE_ZH:
                    result['message_zh'] = MESSAGE_ZH[code]
            else:
                # An OSError is not one of the request checks: a disk, a folder or a file went wrong, or something unexpected did.
                # It says what it is and carries a report, instead of being called a wrong setting.
                logging.getLogger(__name__).exception('image action failed')
                if isinstance(error, OSError) and ran_out(error):
                    reason = 'disk_full'
                elif isinstance(error, OSError) and write_refused(error):
                    reason = 'output_unwritable'
                else:
                    reason = classify_exception(error)
                try:
                    context = Context(request.match_info.get('action', ''), value if isinstance(value, dict) else {})
                    result = service.failed(reason, context, error=error)
                    result.setdefault('error', reason)
                    if result['error'] in MESSAGE_ZH:
                        result.setdefault('message_zh', MESSAGE_ZH[result['error']])
                except Exception:
                    result = dict(phase='failed', error=reason, report_id=None, stage='start')
                    if reason in MESSAGE_ZH:
                        result['message_zh'] = MESSAGE_ZH[reason]
                return web.json_response(result, status=500)
            return web.json_response(result, status=400)
        except Exception as error:
            # Any other fault still answers in JSON with a reason code, the stage and a report, so the page can show
            # why it failed instead of failing to read a plain-text 500.
            logging.getLogger(__name__).exception('image action failed')
            try:
                context = Context(request.match_info.get('action', ''), value if isinstance(value, dict) else {})
                result = service.failed(classify_exception(error), context, error=error)
            except Exception:
                result = dict(phase='failed', error='generation_failed', report_id=None, stage='start')
            result.setdefault('error', 'generation_failed')
            if result['error'] in MESSAGE_ZH:
                result.setdefault('message_zh', MESSAGE_ZH[result['error']])
            return web.json_response(result, status=500)
