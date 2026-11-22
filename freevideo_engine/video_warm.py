"""The creation panel gets the video model ready ahead of Generate. Only a level and a mode come from the page.

'light' (the panel was opened or touched) reads the text encoder and the video model's files into the OS file cache,
as far as free memory goes. 'full' (a video is likely next: the prompt is being written, the tray was closed back to
the prompt) starts the resident worker, places the text encoder on the card under the installation lease, loads the
video model with the last video's settings where it fits (engine_preload) and reads the video model's files ahead. 'conservative' only keeps ready what the resident worker already holds.

Never in the way: skipped while a video, a picture or another task runs; a video or a picture that starts stops it,
and the encoder placement yields within the worker's own yield time. While the image worker holds the lease (a
picture model is kept), nothing is taken from it: the files are read ahead, and the video model only when the
encoder is already on the card. The kept worker is released by the same idle hold as after a video."""
import logging
import threading
from pathlib import Path

LEVELS = ('light', 'full')
MODES = ('eager', 'conservative')


def skipped(reason):
    return dict(warm='skipped', reason=reason)


def video_files(machine):
    """The video model's files in the order the first step reads them."""
    try:
        return sorted(item for item in Path(machine['checkpoint']).rglob('*') if item.is_file())
    except (KeyError, OSError, TypeError):
        return []


def encoder_file(machine):
    try:
        path = Path(machine['encoder_model_root']) / 'text_encoders' / machine['encoder']
    except (KeyError, TypeError):
        return []
    return [path] if path.is_file() else []


class VideoWarmer:
    def __init__(self, installation, *, busy):
        self.installation = installation
        self.busy = busy
        self.mutex = threading.Lock()
        self.thread = None
        self.halt = threading.Event()
        self.level = None
        self.last = None

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def warm(self, value):
        if (not isinstance(value, dict) or value.get('level', 'light') not in LEVELS
                or value.get('mode', 'eager') not in MODES):
            raise ValueError('invalid_request')
        level, mode = value.get('level', 'light'), value.get('mode', 'eager')
        from .resident_process import OWNER
        try:
            if self.busy():
                return skipped('busy')
        except Exception:
            return skipped('busy')
        held = OWNER.process is not None and OWNER.process.poll() is None
        if mode == 'conservative' and not held:
            return skipped('not_loaded')
        try:
            root, machine = self.installation()
        except (OSError, ValueError, RuntimeError):
            return skipped('pack_missing')
        if not video_files(machine):
            return skipped('pack_missing')
        with self.mutex:
            if self.alive() and (self.level == 'full' or level == 'light'):
                # Already on its way at this level or above.
                return dict(warm='loading', level=self.level)
        self.stop()
        with self.mutex:
            self.halt = threading.Event()
            self.level = level
            self.thread = threading.Thread(target=self.run, args=(level, root, machine, self.halt),
                                           name='freevideo-video-warm', daemon=True)
            self.thread.start()
        return dict(warm='loading', level=level)

    def run(self, level, root, machine, halt):
        try:
            self.last = (self.prepare(root, machine, halt) if level == 'full'
                         else self.read_ahead(root, machine, encoder=True))
        except Exception as error:
            from .failure_details import raised_at
            logging.warning('[FreeVideo] Video warm-up failed: %s at %s', type(error).__name__, raised_at(error))
            self.last = dict(state='failed', reason=type(error).__name__)

    def read_ahead(self, root, machine, *, encoder):
        from .encoder_prewarm import IDLE
        files = (encoder_file(machine) if encoder else []) + video_files(machine)
        started = IDLE.schedule(files, Path(root) / 'video-warm.json', busy=self.busy, purpose='startup', delay=0)
        return dict(state='reading' if started else 'skipped', encoder_read=encoder and started)

    def prepare(self, root, machine, halt):
        """Encoder on the card in the resident worker, under the lease; then the video model read ahead."""
        from . import comfy_bridge, resident_process
        from .image_gen.h3_pipeline import placed_encoder_bytes
        from .locking import runtime_lock
        owner = resident_process.OWNER
        try:
            lease = runtime_lock(Path(root) / 'engine.lock', inherit=False)
            descriptor = lease.__enter__()
        except (BlockingIOError, OSError):
            # The image worker keeps a picture model: nothing is taken from it.
            endpoint = owner.endpoint if owner.process is not None and owner.process.poll() is None else None
            on_card = endpoint is not None and placed_encoder_bytes(str(endpoint)) > 0
            return dict(self.read_ahead(root, machine, encoder=not on_card), state='lease_busy', encoder_kept=on_card)
        try:
            if halt.is_set() or self.busy():
                return dict(state='cancelled')
            source = comfy_bridge.source_root()
            environment = comfy_bridge.engine_environment(root, source)
            environment.update(FREEVIDEO_HOME=str(root), PYTHONPATH=str(source))
            try:
                endpoint = owner.start(root, source, machine['python'], environment, stop=halt)
            except resident_process.StartStopped:
                return dict(state='cancelled')
            if placed_encoder_bytes(endpoint) > 0:
                placed = dict(state='kept', reason='The encoder is already on the card')
            else:
                placed = self.place(endpoint, machine, environment, descriptor, halt)
            if not halt.is_set():
                placed = dict(placed, video_model=self.place_engine(endpoint, environment, descriptor, halt))
        finally:
            lease.__exit__(None, None, None)
        if halt.is_set():
            return dict(placed, state='cancelled')
        # The worker is kept as after a video: released when idle, under pressure or for another task.
        owner.finished(warm=True, gpu=machine.get('gpu_uuid'))
        return dict(placed, video_files=self.read_ahead(root, machine, encoder=False)['state'])

    @staticmethod
    def place(endpoint, machine, environment, descriptor, halt):
        from . import resident_process
        from .hardware import detect
        from .idle_encoder import place_now
        from .kernel_capabilities import available_backends
        from .policy import choose
        hardware = detect()
        # Plans from the saved kernel checks and never runs them: a check cannot be stopped for a video, and this
        # process (ComfyUI's own Python) is not the one the video runs in, so its checks never match the video's and
        # would run again at every start. The video runs its checks in its own process.
        policy = choose(hardware, available_backends=available_backends(hardware, probe_missing=False),
                        allow_capacity_trial=True)
        environment = dict(environment)
        environment.pop(resident_process.ENV, None)
        return place_now(endpoint, dict(encoder=machine.get('encoder'), comfy_root=machine.get('comfy_root'),
                                        model_paths=machine.get('model_paths')),
                         descriptor=descriptor, environment=environment, directory=Path(endpoint).parent,
                         gpu_budget_bytes=policy.gpu_budget_bytes, ram_budget_bytes=policy.ram_budget_bytes,
                         allocator=policy.allocator_config, limits=dict(system=hardware.system), stop=halt)

    @staticmethod
    def place_engine(endpoint, environment, descriptor, halt):
        """The video model with the last video's settings into the resident worker, under the caller's lease."""
        from . import engine_preload, resident_process
        from .idle_encoder import PreloadCancelled, watch
        from .monitoring import save
        from .ram import ProcessMemory
        value = engine_preload.recall()
        if value is None:
            return dict(state='skipped', reason='no_settings')
        environment = dict(environment, **{resident_process.ENV: str(endpoint)})
        try:
            held = resident_process.status(environment)
        except Exception:
            held = None
        allocator = value.get('allocator') or ''
        current = held.get('allocator_config') if isinstance(held, dict) else None
        if current is not None and current != allocator:
            # Another allocator restarts the worker and loses what it holds: the video loads its model as before.
            result = dict(state='skipped', reason='allocator_changed')
            logging.info('[FreeVideo] Video model not preloaded: the last video used other allocator settings')
            return result
        environment.update(PYTORCH_ALLOC_CONF=allocator, PYTORCH_CUDA_ALLOC_CONF=allocator)
        directory = Path(endpoint).parent
        result_path, cancelled = directory / 'engine-preload.json', directory / 'engine-preload.cancel'
        request_path = directory / 'engine-preload-worker.json'
        for item in (result_path, cancelled):
            item.unlink(missing_ok=True)
        save(request_path, engine_preload.worker_request(value, metrics=result_path, cancel=cancelled))
        result, memory, workers = dict(state='skipped'), ProcessMemory(), []
        try:
            result.update(watch(endpoint, request_path, environment, descriptor, result_path, cancelled,
                                int(value.get('ram_budget_bytes') or 1 << 62), result, memory, held=workers,
                                stop=halt, module='freevideo_engine.worker', capability=engine_preload.CAPABILITY))
        except PreloadCancelled as error:
            if workers:
                try:
                    workers[0].stop_request(grace=.5)
                except BaseException:
                    pass
            result.update(state='cancelled', reason=str(error))
        except Exception as error:
            result.update(state='cancelled' if halt.is_set() else 'failed', error_type=type(error).__name__,
                          reason=str(error)[-300:])
        result.pop('success', None)
        geometry = value.get('geometry') or {}
        logging.info('[FreeVideo] Video model preload with the last video settings (%sx%s, %s s): %s%s',
                     geometry.get('width'), geometry.get('height'), geometry.get('requested_seconds'),
                     result.get('state'), (' (%s)' % result['reason']) if result.get('reason') else
                     (' in %.1f s' % result['seconds']) if result.get('seconds') is not None else '')
        return result

    def cancel(self):
        """The page left the panel: the warm-up and its own file read stop; a kept worker stays to its idle hold."""
        from .encoder_prewarm import IDLE
        self.stop()
        if IDLE.report is not None and Path(IDLE.report).name == 'video-warm.json':
            IDLE.stop()
        return dict(warm='cancelled')

    def stop(self, timeout=8.):
        """A video or a picture starts, or the page left: the warm-up stops; the file read stops with IDLE.stop()."""
        from .idle_encoder import KEPT_YIELD_SECONDS
        with self.mutex:
            thread, halt = self.thread, self.halt
        halt.set()
        if thread is not None and thread is not threading.current_thread():
            # The placement's watch() decides its yield time (longer while the worker keeps video models); this
            # only bounds a warm-up that hangs.
            limit = max(timeout, KEPT_YIELD_SECONDS + 2.)
            thread.join(timeout=limit)
            if thread.is_alive():
                # The request then waits for the GPU lease on its own; this line names who kept it.
                logging.warning('[FreeVideo] Video warm-up did not stop within %.0f s; it still holds the GPU.', limit)
        return dict(warm='cancelled')


WARMER = None


def warmer(installation=None, busy=None):
    """The one warmer of this engine, made on first use."""
    global WARMER
    if WARMER is None and installation is not None:
        WARMER = VideoWarmer(installation, busy=busy)
    return WARMER


def stop():
    if WARMER is not None:
        WARMER.stop()
