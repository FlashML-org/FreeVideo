"""Supervise speculative encoder placement after a saved interactive request.

This controller imports no tensor runtime. The existing resident worker owns
CUDA, and a new foreground request cancels this lower-priority work first.
"""
import argparse
from contextlib import contextmanager
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import time

from . import processes
from .monitoring import save


def gpu_preload_budget(previous, limits, *, free, total, reserved):
    """Reconsider idle placement from live bytes, preserving explicit limits.

    Only our idle allocator's storage earns credit. Another application's
    allocations and the CUDA context are absent from ``reserved``. No compute
    profile, quality setting, or foreground placement changes here.
    """
    from .hardware import GiB
    limits = limits or {}
    capacity = total
    if limits.get('vram_gib') is not None:
        capacity = min(capacity, int(limits['vram_gib'] * GiB))
    available = max(0, min(capacity, free + reserved))
    reserve = limits.get('gpu_reserve_gib')
    if reserve is None:
        reserve = 1. if limits.get('system') == 'Windows' else max(.5, min(1., available / GiB * .025))
    reserve = max(.5, reserve) * GiB
    budget = max(0, available - reserve)
    if limits.get('automatic') is not True:
        budget = min(budget, previous)
    if limits.get('allocator_limit_bytes') is not None:
        budget = min(budget, limits['allocator_limit_bytes'])
    return dict(budget_bytes=int(budget), live_free_bytes=free, owned_reserved_bytes=reserved,
                capacity_bytes=capacity, system_reserve_bytes=int(reserve), previous_budget_bytes=previous,
                reason='Refresh idle GPU allowance from live free bytes plus owned cache; keep explicit caps and system headroom')


class PreloadCancelled(Exception):
    """A completed video stays successful when speculative work is cancelled."""


# How long cancelled placement may take to hand the worker back. Past this the
# worker is stopped, and with it every model it holds: the next request then
# pays a new process and loads everything again, which costs far more than
# these seconds. One native transfer in flight cannot be interrupted, and on a
# slow disk a single large weight takes longer than a second.
YIELD_SECONDS = 6.
# The same while the worker keeps video models: stopping it then also drops them, and the video loads them again
# (about 30 s or more). The encoder's build is one native call that cannot stop midway (a few seconds).
KEPT_YIELD_SECONDS = 20.


def kept_models(endpoint):
    """Roles and card bytes of the models other than the encoder that the resident worker at ``endpoint`` keeps."""
    from .resident_process import ENV, status
    try:
        held = status({ENV: str(endpoint)})
        return {str(row.get('role')): int(row.get('gpu_bytes') or 0) for row in held.get('models') or []
                if row.get('role') != 'encoder'}
    except Exception:
        return {}  # Unknown: the usual yield time.


def stop_unyielding(worker, kept, result):
    """Stop a worker whose cancelled request did not yield in time. The models it kept go with it: they are noted in
    ``result`` and, in the process that owns the worker, recorded as a release."""
    try:
        worker.stop_request(grace=.5)
    except Exception:
        pass  # The caller stops it again and reports what failed.
    if kept:
        roles, released = sorted(kept), sum(kept.values())
        result.update(released_roles=roles, released_gpu_bytes=released)
        from .resident_process import OWNER
        if OWNER.process is not None:
            OWNER.record(action='released', reason='preload_did_not_yield', roles=roles, released_gpu_bytes=released)


@contextmanager
def interruptible_load(patcher, check):
    """Yield between native module transfers, with the original loader intact.

    CPU cleanup must remain callable after cancellation. Speculative loading
    also avoids pinning all offloaded weights merely to warm a few GPU layers.
    No forward, tokenization, patched arithmetic or warm-up inference runs here.
    """
    saved = []
    def replace(obj, name, value):
        saved.append((obj, name, name in vars(obj), vars(obj).get(name)))
        setattr(obj, name, value)
    try:
        for module in patcher.model.modules():
            original = module.to
            def move(*args, _original=original, **kwargs):
                device = kwargs.get('device', args[0] if args else None)
                if str(device).startswith('cuda'):
                    check()
                return _original(*args, **kwargs)
            replace(module, 'to', move)
        original_patch = patcher.patch_weight_to_device
        def patch(*args, **kwargs):
            device = kwargs.get('device_to', args[1] if len(args) > 1 else None)
            if str(device).startswith('cuda'):
                check()
            return original_patch(*args, **kwargs)
        replace(patcher, 'patch_weight_to_device', patch)
        replace(patcher, 'pin_weight_to_device', lambda *args, **kwargs: check())
        # The native loader answers a failed placement by moving every weight
        # it already placed back to host memory. Here that is a full
        # GPU-to-CPU copy, as much new host memory as was placed, before the
        # worker can yield; under RAM pressure it is the wrong direction too.
        # The caller discards a model whose placement did not finish, so its
        # tensors stay where they are. None is unpatch_model's own "do not
        # move" mode.
        native_detach = getattr(patcher, 'detach', None)
        def detach(*args, **kwargs):
            native_unpatch = patcher.unpatch_model
            existed, previous = 'unpatch_model' in vars(patcher), vars(patcher).get('unpatch_model')
            patcher.unpatch_model = lambda device_to=None, unpatch_weights=True: native_unpatch(
                device_to=None, unpatch_weights=unpatch_weights)
            try:
                return native_detach(*args, **kwargs)
            finally:
                if existed:
                    patcher.unpatch_model = previous
                else:
                    del patcher.unpatch_model
        if callable(native_detach):
            replace(patcher, 'detach', detach)
        yield
    finally:
        for obj, name, existed, value in reversed(saved):
            if existed:
                setattr(obj, name, value)
            else:
                delattr(obj, name)


class IdleEncoder:
    def __init__(self):
        self.thread = None
        self.cancelled = threading.Event()
        self.process = None
        self.cancel_path = None
        self.report = None

    def stop(self):
        self.cancelled.set()
        if self.cancel_path is not None:
            try:
                self.cancel_path.touch(exist_ok=True)
            except OSError:
                if self.process is not None and self.process.poll() is None:
                    processes.stop(self.process, grace=1.)
        if self.thread is not None:
            # The controller's watch() gives the worker its yield time; this only bounds a controller that hangs.
            self.thread.join(timeout=KEPT_YIELD_SECONDS+2.)
            if self.thread.is_alive():
                # A blocked disk read/native copy is not indefinitely allowed
                # to delay the next prompt. The controller stops the real GPU
                # worker on termination; SessionOwner confirms it is idle/dead.
                if self.process is not None:
                    processes.stop(self.process, grace=1.)
                self.thread.join(timeout=2.)
            return not self.thread.is_alive()
        return True

    def schedule(self, endpoint, output, python, environment, *, busy=None, notify=None):
        if not self.stop():
            return False
        self.cancelled = threading.Event()
        cancel = self.cancelled
        report = Path(output).with_suffix('.prewarm.json')
        self.report = report
        self.cancel_path = Path(output).with_suffix('.prewarm.cancel')
        request_path = Path(output).with_suffix('.prewarm-request.json')
        request = dict(endpoint=str(endpoint), previous=str(Path(output).with_suffix('.request.json')),
                       cancel=str(self.cancel_path), metrics=str(report))
        save(request_path, request)
        env = dict(environment)
        env.pop('FREEVIDEO_RUNTIME_LOCK_FD', None)
        env.pop(processes.HANDLE_ENV, None)
        self.process = None

        def work():
            try:
                # Let Comfy publish the video before optional background work.
                if cancel.wait(1.):
                    save(report, dict(state='cancelled', reason='A queued request takes priority'))
                    return
                # Comfy may still be saving/displaying this video's downstream
                # nodes. Wait for the graph to finish instead of permanently
                # skipping preloading because it was busy at one instant.
                while busy and busy():
                    if cancel.wait(.1):
                        save(report, dict(state='cancelled', reason='A queued request takes priority'))
                        return
                with report.with_suffix('.log').open('wb') as log:
                    self.process = processes.popen([str(python), '-B', '-m',
                        'freevideo_engine.idle_encoder', '--request', str(request_path)],
                        env=env, cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True, supervise=True)
                    previous = None
                    while self.process.poll() is None:
                        if cancel.is_set() or busy and busy():
                            cancel.set()
                            self.cancel_path.touch(exist_ok=True)
                        try:
                            current = json.loads(report.read_text(encoding='utf-8'))
                            if current != previous and notify:
                                try:
                                    notify(current)
                                except Exception:
                                    pass  # A disconnected UI does not orphan the controller.
                            previous = current
                        except (OSError, ValueError):
                            pass
                        try:
                            self.process.wait(timeout=.1)
                        except subprocess.TimeoutExpired:
                            pass
                if notify and report.is_file():
                    notify(json.loads(report.read_text(encoding='utf-8')))
            except Exception as error:
                if self.process is not None and self.process.poll() is None:
                    processes.stop(self.process, grace=1.)
                save(report, dict(state='failed', reason=str(error), error_type=type(error).__name__))

        self.thread = threading.Thread(target=work, name='FreeVideo idle encoder', daemon=True)
        self.thread.start()
        return True


def watch(endpoint, worker_request, environment, descriptor, result_path, cancelled, ram_budget_bytes, result, memory,
          *, started=None, held=None, stop=None, module='freevideo_engine.encode_worker',
          capability='encoder-weight-preload'):
    """Run one weights-only request in the resident worker under ``descriptor`` and watch it until it ends; the
    worker is appended to ``held`` as soon as it exists, so the caller can stop it. ``stop`` (an event) cancels it
    like the cancel file. ``module`` and ``capability``: the encoder's placement, or the video model's preload
    (freevideo_engine.worker, 'engine-preload').

    Every speculative request in the resident worker is watched here, so this is the one place that decides how long
    a cancelled request has to yield: KEPT_YIELD_SECONDS while the worker keeps video models, else YIELD_SECONDS, and
    under live RAM pressure no more than YIELD_SECONDS. Past that the worker is stopped (stop_unyielding) and
    PreloadCancelled raised. Returns its metrics."""
    from .resident_process import RemoteProcess
    from .system import inference_headroom, inference_memory_sample, windows
    # What the worker keeps before this request: the request only adds the encoder or the video model it loads.
    kept = kept_models(endpoint)
    patience = KEPT_YIELD_SECONDS if kept else YIELD_SECONDS
    worker = RemoteProcess(endpoint,
        [sys.executable, '-B', '-m', module, '--request', str(worker_request)],
        environment, descriptor, result_path.with_name(result_path.stem+'-worker.log'),
        required_capabilities=(capability,))
    if held is not None:
        held.append(worker)
    if started is not None:
        started(worker.pid)
    cancel_started, pressed = None, False
    while worker.poll() is None:
        sample = memory.sample(worker.pid)
        used = inference_memory_sample(sample)
        if used is None or used > ram_budget_bytes or inference_headroom(sample) < (2 if windows() else 1)*2**30:
            cancelled.touch(exist_ok=True)
            result.update(state='released', reason='Live RAM pressure interrupted speculative loading')
            pressed = True
        if stop is not None and stop.is_set() and not cancelled.exists():
            cancelled.touch(exist_ok=True)
            result.update(state='released', reason='Another task took the card')
        if cancelled.exists():
            cancel_started = cancel_started or time.monotonic()
            # Live RAM pressure never waits longer than the usual yield time.
            if time.monotonic()-cancel_started > (min(patience, YIELD_SECONDS) if pressed else patience):
                stop_unyielding(worker, kept, result)
                raise PreloadCancelled('Preload did not yield promptly; stop the worker before foreground work')
        try:
            worker.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            pass
    loaded = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
    if worker.returncode or not loaded.get('success'):
        from .adaptive import WorkerExit
        raise WorkerExit(worker.returncode, result_path.with_name(result_path.stem+'-worker.log'))
    return loaded


def place_now(endpoint, encoding, *, descriptor, environment, directory, gpu_budget_bytes, ram_budget_bytes,
              allocator, limits=None, stop=None):
    """Place the encoder on the card for a picture that is being set up, under the caller's lease, and wait for it.

    ``encoding`` names the encoder recipe only (encoder, comfy_root, model_paths): no prompt or picture is sent.
    ``stop`` (set when a picture is asked for, or a video or another task takes the card) and live RAM pressure
    cancel it: a speculative placement does not page in its weights ahead and can take longer than the picture's own
    load of the encoder, so a picture never waits for it. watch() decides how long it has to yield."""
    from .ram import ProcessMemory
    from .resident_process import ENV
    directory = Path(directory)
    result_path, cancelled = directory/'encoder-place.json', directory/'encoder-place.cancel'
    worker_request = directory/'encoder-place-worker.json'
    for path in (result_path, cancelled):
        path.unlink(missing_ok=True)
    result = dict(state='skipped', scope='Idle GPU encoder weights; no prompt encoding')
    save(worker_request, dict({k: encoding[k] for k in ('encoder', 'comfy_root', 'model_paths') if k in encoding},
                              idle_preload=True, cancel=str(cancelled), metrics=str(result_path),
                              idle_resources=dict(limits or {}, automatic=True), gpu_budget_gb=gpu_budget_bytes/1e9,
                              ram_budget_bytes=int(ram_budget_bytes)))
    environment = dict(environment, **{ENV: str(endpoint)}, PYTORCH_ALLOC_CONF=allocator, PYTORCH_CUDA_ALLOC_CONF=allocator)
    memory, held = ProcessMemory(), []
    if stop is not None and stop.is_set():
        return dict(result, reason='Another task took the card')
    try:
        result.update(watch(endpoint, worker_request, environment, descriptor, result_path, cancelled,
                            ram_budget_bytes, result, memory, held=held, stop=stop),
                      resources=memory.result())
    except BaseException as error:
        if held:
            try:
                held[0].stop_request(grace=.5)
            except BaseException:
                pass
        result.update(state='failed', reason=str(error)[-500:], error_type=type(error).__name__)
        if not isinstance(error, Exception):
            raise
    return result


def run(request):
    from .adaptive import classify_failure
    from .locking import runtime_lock
    from .resource_history import ResourceHistory
    from .resident_process import ENV
    from .ram import ProcessMemory

    result_path = Path(request['metrics'])
    cancelled = Path(request['cancel'])
    worker = None
    held = []
    attempt = None
    history = None
    result = dict(state='skipped', scope='Idle GPU encoder weights; no prompt encoding')
    def interrupted(signum, frame):
        cancelled.touch(exist_ok=True)
        raise KeyboardInterrupt('Foreground work takes priority over encoder preloading')
    previous_handler = processes.termination_handler(interrupted)
    try:
        if cancelled.exists():
            raise PreloadCancelled('A new request takes priority')
        previous = json.loads(Path(request['previous']).read_text(encoding='utf-8'))
        attempts = previous.get('resource_attempts', [])
        if not previous.get('success') or not attempts or not previous.get('encoding'):
            result['reason'] = 'No completed interactive encoder request to prepare again'
            return result
        history = ResourceHistory(previous['resource_history'])
        measured = history.attempt(attempts[-1]['id'])
        if measured is None or measured['state'] != 'success':
            raise ValueError('Previous request has no settled success in local history')
        original = json.loads((Path(previous['artifacts'])/'encode.json').read_text(encoding='utf-8'))
        # Deliberately do not forward text, images, seed or other next-request
        # inputs. Preloading uses only the already validated encoder recipe.
        encoding = {k: original[k] for k in ('encoder', 'comfy_root', 'model_paths') if k in original}
        profile = previous['profile']
        budget = profile['gpu_budget_gb']*1e9
        if profile.get('benchmark_allocator_limit_bytes') is not None:
            budget = min(budget, profile['benchmark_allocator_limit_bytes'])
        encoding.update(idle_preload=True, cancel=str(cancelled), metrics=str(result_path),
                        idle_resources=previous.get('idle_resources'),
                        gpu_budget_gb=budget/1e9,
                        ram_budget_bytes=round(profile['inference_ram_budget_gb']*1e9))
        worker_request = result_path.with_name(result_path.stem+'-worker.json')
        save(worker_request, encoding)
        environment = dict(os.environ)
        environment[ENV] = request['endpoint']
        allocator = profile.get('allocator_config', '')
        environment.update(PYTORCH_ALLOC_CONF=allocator, PYTORCH_CUDA_ALLOC_CONF=allocator)
        memory = ProcessMemory()
        with runtime_lock(inherit=False) as descriptor:
            if cancelled.exists():
                raise PreloadCancelled('A new request takes priority')
            attempt = history.begin(measured['identity'],
                dict(encoder=encoding['encoder'], operation='idle-preload'),
                measured['geometry'], purpose='encoding',
                evidence=dict(reason='Saved video returned; speculative weights only', previous=measured['id']))
            loaded = watch(request['endpoint'], worker_request, environment, descriptor, result_path, cancelled,
                           encoding['ram_budget_bytes'], result, memory,
                           started=lambda pid: history.attach_worker(attempt, pid, phase='idle-encoder-load'), held=held)
            worker = held[0]
            result.update(loaded, resources=memory.result())
            history.finish(attempt, 'cancelled' if result.get('state') in ('cancelled', 'released') else 'success', details=result)
            attempt = None
    except BaseException as error:
        worker = worker or (held[0] if held else None)
        if worker is not None:
            try:
                worker.stop_request(grace=.5)
            except BaseException as cleanup:
                error.worker_stop_confirmed = False
                error.worker_stop_error = repr(cleanup)
        failure = classify_failure(KeyboardInterrupt(str(error)) if isinstance(error, PreloadCancelled) else error)
        if getattr(error, 'worker_stop_confirmed', None) is False:
            failure = classify_failure(error)
        result.update(state='cancelled' if failure['outcome'] == 'cancelled' else 'skipped' if isinstance(error, BlockingIOError) else 'failed',
                      reason=str(error), failure=failure, error_type=type(error).__name__)
        if attempt is not None:
            history.finish(attempt, failure['outcome'], details=result)
    finally:
        for signum, handler in previous_handler.items():
            signal.signal(signum, handler)
        result['completed_epoch'] = time.time()
        save(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    run(json.loads(args.request.read_text(encoding='utf-8')))


if __name__ == '__main__':
    main()
