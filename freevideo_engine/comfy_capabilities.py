"""Advisory capabilities route: bounded history IO and a CPU background cache."""
import asyncio
from collections import OrderedDict
from concurrent.futures import Future
import hashlib
import json
import logging
from pathlib import Path
import platform
import sys
import threading
import time

from . import __version__
from .capabilities import (UNAVAILABLE, build, content_revision, normalize_references,
                           reference_parameters, sizes_for)
from .effort_forecast import local_records, same_device
from .media_request import TASKS

COMPUTING = {'version': 1, 'status': 'computing', 'retry_after_seconds': 1}
DEFAULT_QUERY = ('16:9', None, 't2va', False, None)
LONGEST_WAIT_SECONDS = None  # Product decision pending; no time cutoff today.


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()[:12]


def _machine_context():
    from .background_compile import compile_owed
    from .comfy_bridge import installation
    root, machine = installation()
    machine = dict(machine)
    device = machine.get('device_identity') if machine.get('device_backend') == 'mps' else machine.get('gpu_uuid')
    # Older machine.json stores totals only in its setup plan. Match the saved
    # device before using that snapshot, just as start_context does for ETA.
    observed = {}
    if machine.get('setup_run'):
        try:
            with (Path(machine['setup_run']) / 'plan.json').open('rb') as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) <= 1024 * 1024:
                found = json.loads(raw).get('inventory', {}).get('hardware', {})
                if same_device(found, device):
                    observed = {k: found[k] for k in ('gpu_name', 'capability', 'ram_total', 'vram_total',
                                                     'system', 'metal_recommended_working_set_bytes') if k in found}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if isinstance(machine.get('hardware'), dict):
        observed.update(machine['hardware'])
    machine['hardware'] = observed
    # Receipt read only: tells the page the first run still compiles.
    machine['compile_owed'] = compile_owed(root)
    return machine


def _context(output_directory):
    machine = _machine_context()
    device = machine.get('device_identity') if machine.get('device_backend') == 'mps' else machine.get('gpu_uuid')
    # Do not inherit the sampling-estimate route's 15-second history cache:
    # reopening the grid after a generation must notice the new timing record.
    rows = local_records(output_directory, device, system=machine.get('system'), stage_details=True, refresh=True)
    return machine, rows


def _apple_silicon():
    return platform.system() == 'Darwin' and platform.machine().lower() in ('arm64', 'aarch64')


class CapabilityCache:
    """One builder per server, at most 32 query variants, no foreground builds."""
    def __init__(self, output_directory, server=None):
        self.output_directory = output_directory
        self.server = server
        self.lock = threading.Condition()
        self.entries = OrderedDict([(DEFAULT_QUERY, dict(COMPUTING))])
        self.pending = OrderedDict()
        self.snapshot = None
        self.signature = None
        self.generation = 0
        self.initialized = False
        self.failed = False
        self.logged = False
        self.check_task = None
        self.checked_at = 0.
        self.refresh_requested = True
        self.refresh_done = Future()
        self.active_refresh = None
        threading.Thread(target=self._work, name='freevideo-capabilities', daemon=True).start()

    def _log_once(self):
        with self.lock:
            if self.logged:
                return
            self.logged = True
        # Exception messages can contain installation/report paths. Keep this
        # advisory failure private and never let it interfere with generation.
        logging.warning('FreeVideo capabilities are unavailable; background estimation failed.')

    def refresh(self):
        """History/saved snapshot IO only; called in a thread, never the loop."""
        try:
            machine, records = _context(self.output_directory)
            # Count/time alone misses old records lacking chronology, or a
            # replaced record at the same count: hash the allowlisted timings.
            signature = _hash((machine, records, __version__))
            with self.lock:
                if signature != self.signature or self.failed:
                    self.signature = signature
                    self.snapshot = (machine, records)
                    self.generation += 1
                    self.failed = False
                    mac = machine.get('device_backend') == 'mps' or (machine.get('device_identity') or {}).get('backend') == 'mps'
                    for key in self.entries:
                        if mac:
                            self.entries[key] = dict(UNAVAILABLE)
                        elif self.entries[key]['status'] != 'ready':
                            self.entries[key] = dict(COMPUTING)
                        if not mac:
                            self.pending[key] = None
                    if mac:
                        self.pending.clear()
                    self.lock.notify()
        except Exception:
            self._log_once()
            with self.lock:
                self.failed = True
                self.generation += 1
                self.pending.clear()
                for key in self.entries:
                    if self.entries[key]['status'] != 'ready':
                        self.entries[key] = dict(UNAVAILABLE)
        finally:
            with self.lock:
                self.initialized = True

    def _request_refresh(self, *, join_active=False):
        with self.lock:
            if join_active and self.active_refresh is not None:
                return self.active_refresh
            if not self.refresh_requested:
                self.refresh_requested = True
                self.refresh_done = Future()
            self.lock.notify()
            return self.refresh_done

    def changed(self):
        """Best-effort wakeup from generation; IO/builds stay on the worker.

        Calls coalesce into one pending read. A change during a read requests
        one more read so a report written mid-snapshot cannot be missed.
        """
        try:
            self._request_refresh()
        except Exception:
            pass

    def _notify(self, key, result):
        aspect, size, task, adapters, references = key
        try:
            if self.server is not None:
                # ComfyUI's send_sync schedules delivery on its event loop,
                # exactly as comfy_progress.publish does from worker threads.
                self.server.send_sync('freevideo_capabilities', dict(
                    version=1, revision=result['revision'], aspect=aspect,
                    width=size[0] if size else None, height=size[1] if size else None,
                    task=task, adapters=adapters, references=reference_parameters(references)))
        except Exception:
            pass

    def _work(self):
        while True:
            with self.lock:
                while not self.pending and not self.refresh_requested:
                    self.lock.wait()
                if self.refresh_requested:
                    self.refresh_requested = False
                    done = self.active_refresh = self.refresh_done
                else:
                    done = None
                if done is not None:
                    key = None
                else:
                    key, _ = self.pending.popitem(last=False)
                    generation = self.generation
                    machine, records = self.snapshot
            if done is not None:
                self.refresh()
                with self.lock:
                    self.active_refresh = None
                done.set_result(None)
                continue
            try:
                aspect, size, task, adapters, references = key
                result = build(machine, records, aspect=aspect, size=size, task=task,
                               adapters=adapters, version=__version__,
                               references=reference_parameters(references),
                               longest_wait_seconds=LONGEST_WAIT_SECONDS)
                if result['status'] == 'ready':
                    result = dict(result, revision=content_revision(result))
                built = True
            except Exception:
                self._log_once()
                result = dict(UNAVAILABLE)
                built = False
            notify = False
            with self.lock:
                if key in self.entries and generation == self.generation:
                    previous = self.entries[key]
                    if built or previous['status'] != 'ready':
                        self.entries[key] = result
                        notify = (previous['status'] == result['status'] == 'ready'
                                  and previous['revision'] != result['revision'])
            if notify:
                self._notify(key, result)

    async def check(self):
        # Await a worker snapshot for at most 50 ms; no IO on the event loop.
        # shield prevents timeout from cancelling the shared completion.
        if not self.initialized:
            return
        if self.check_task is None or self.check_task.done():
            if time.monotonic() - self.checked_at < 1:
                return
            self.checked_at = time.monotonic()
            done = self._request_refresh(join_active=True)
            async def wait_snapshot():
                await asyncio.shield(asyncio.wrap_future(done))
            self.check_task = asyncio.create_task(wait_snapshot())
        try:
            await asyncio.wait_for(asyncio.shield(self.check_task), timeout=.05)
        except asyncio.TimeoutError:
            pass

    def get(self, key):
        with self.lock:
            if self.snapshot is not None:
                machine = self.snapshot[0]
                if machine.get('device_backend') == 'mps' or (machine.get('device_identity') or {}).get('backend') == 'mps':
                    return dict(UNAVAILABLE)
            if key not in self.entries:
                if len(self.entries) == 32:
                    old, _ = self.entries.popitem(last=False)
                    self.pending.pop(old, None)
                self.entries[key] = dict(UNAVAILABLE if self.failed else COMPUTING)
                if self.initialized and not self.failed:
                    self.pending[key] = None
                    self.lock.notify()
            self.entries.move_to_end(key)
            return self.entries[key]


def _query(query):
    aspect = query.get('aspect', '16:9')
    size = None
    if 'width' in query or 'height' in query:
        if 'width' not in query or 'height' not in query:
            raise ValueError('Specify both width and height.')
        size = (int(query['width']), int(query['height']))
    task = query.get('task', 't2va')
    if task not in TASKS or query.get('adapters', '0') not in ('0', '1'):
        raise ValueError('Unsupported task or adapters flag.')
    sizes_for(aspect, size)
    references = None
    if 'references' in query:
        raw = query['references']
        if len(raw) > 16384:
            raise ValueError('Reference metadata is too large.')
        references = json.loads(raw)
        if references is None:
            raise ValueError('Use a reference metadata list.')
    for forbidden in ('reference_video_tokens', 'reference_audio_tokens', 'reference_rows'):
        if forbidden in query:
            raise ValueError('Send material metadata, not row counts.')
    return aspect, size, task, query.get('adapters') == '1', normalize_references(task, references)


def _usage_query(query):
    """Same canvas/material fields as capabilities, plus this request's duration/profile."""
    from .geometry import geometry
    from .tiers import TIERS, STEP_CACHES
    kind = query.get('kind', 'video')
    if kind not in ('video', 'image'):
        raise ValueError('Unsupported generation kind.')
    if kind == 'image':
        width, height = int(query.get('width', 1024)), int(query.get('height', 1024))
        if not 256 <= width <= 2048 or not 256 <= height <= 2048:
            raise ValueError('Use image dimensions from 256 to 2048.')
        count = int(query.get('image_references', '0'))
        if not 0 <= count <= 4:
            raise ValueError('Use at most four image references.')
        # Image preflight normalizes each reference to 1024**2 pixels.
        return dict(kind=kind, width=width, height=height, reference_pixels=count * 1024**2)
    _, size, task, _, references = _query(query)
    width, height = size or (1344, 768)
    tier = query.get('tier', 'light')
    if tier not in (*TIERS, 'custom'):
        raise ValueError('Unsupported tier.')
    defaults = TIERS.get(tier, TIERS['light'])
    flag = query.get('two_pass', '1' if defaults['two_pass'] else '0')
    if flag not in ('0', '1'):
        raise ValueError('Use a boolean two_pass flag.')
    steps, refine = int(query.get('base_steps', defaults['steps'])), int(query.get('refine_steps', 3))
    cache = query.get('step_cache', defaults['step_cache'])
    cache = None if cache in (None, 'none', '') else cache
    upscale = float(query.get('upscale_factor', defaults['upscale']))
    if not 1 <= steps <= 32 or not 0 <= refine <= 31 or cache not in STEP_CACHES or upscale not in (1.2, 1.5, 2):
        raise ValueError('Unsupported sampling profile.')
    if 'frames' in query and 'seconds' in query:
        raise ValueError('Specify frames or seconds, not both.')
    shape = geometry(width, height, frames=int(query['frames'])) if 'frames' in query else geometry(
        width, height, seconds=float(query.get('seconds', 10.125)))
    return dict(kind=kind, width=width, height=height, frames=shape['frames'], steps=steps,
                two_pass=flag == '1', task=task, refine=refine, references=reference_parameters(references),
                step_cache=cache, upscale=upscale)


def _other_usage(params, failure=False):
    from . import other_usage
    try:
        machine = _machine_context()
        usage = other_usage.read(machine)
        return other_usage.notice(other_usage.net(usage, machine)) if failure else other_usage.for_request(machine, usage, **params)
    except Exception:
        return None


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_capabilities', False):
        return
    mac = _apple_silicon()
    cache = None if mac else CapabilityCache(folder_paths.get_output_directory(), server)
    server._freevideo_capabilities = cache if cache is not None else True

    @server.routes.get('/freevideo/other-usage')
    async def other_usage(request):
        result = None
        if not mac:
            try:
                params = _usage_query(request.query)
            except (ValueError, TypeError, OverflowError, RecursionError) as error:
                raise web.HTTPBadRequest(text=str(error)) from error
            result = await asyncio.to_thread(_other_usage, params, request.query.get('failure') == '1')
        return web.json_response(dict(status='ok', notice=result), headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/capabilities')
    async def capabilities(request):
        if mac:
            return web.json_response(dict(UNAVAILABLE), headers={'Cache-Control': 'no-store'})
        try:
            key = _query(request.query)
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        try:
            await cache.check()
            result = cache.get(key)
        except Exception:
            cache._log_once()
            result = dict(UNAVAILABLE)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})


def changed():
    """Notify only an already registered route, without affecting generation."""
    try:
        module = sys.modules.get('server')
        server = module.PromptServer.instance if module is not None else None
        cache = getattr(server, '_freevideo_capabilities', None)
        if isinstance(cache, CapabilityCache):
            cache.changed()
    except Exception:
        pass
