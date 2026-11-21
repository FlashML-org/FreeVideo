"""Persistent ImageRunner owner; stdin/stdout contain one JSON object per line."""
import argparse
import base64
from contextlib import ExitStack, contextmanager, redirect_stdout
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

from . import keep
from .failure import Context, classify_exception, exception_details, host_memory_shortage, safe_plan
from .step_preview import LIMIT as PREVIEW_LIMIT


class Inbox:
    """Read cancellation while the main thread is inside a denoising step."""
    def __init__(self, stream, send):
        self.stream = stream
        self.send = send
        self.rows = queue.Queue()
        self.cancel = threading.Event()
        self.closed = threading.Event()
        self.mutex = threading.Lock()
        self.busy = False
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        try:
            while not self.closed.is_set():
                line = self.stream.readline(64 * 1024 + 1)
                if not line:
                    break
                try:
                    if len(line.encode('utf-8')) > 64 * 1024:
                        raise ValueError('invalid_request')
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError('invalid_request')
                    action = value.get('action')
                    with self.mutex:
                        if action == 'cancel':
                            if self.busy:
                                self.cancel.set()
                        elif action == 'close':
                            break
                        elif action == 'generate':
                            if self.busy:
                                self.send(phase='error', code='busy')
                            else:
                                self.busy = True
                                self.cancel.clear()
                                self.rows.put(value)
                        elif action in ('warm', 'unwarm'):
                            # A warm-up during a picture has nothing left to prepare.
                            if not self.busy:
                                self.rows.put(value)
                        else:
                            raise ValueError('invalid_request')
                except (ValueError, UnicodeError):
                    self.send(phase='error', code='invalid_request')
        finally:
            self.closed.set()
            self.cancel.set()
            self.rows.put(None)


def _flatten(image):
    """RGB copy; transparency is shown on white, as the encoder's vision tower sees it."""
    from PIL import Image
    if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
        rgba = image.convert('RGBA')
        base = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert('RGB')
    return image.convert('RGB')


ORIENTATION = 0x0112


def _upright(image):
    """The picture as it is shown: turned by its orientation tag, as H3 Image reads references and the page draws them.
    Pictures without a tag (or tagged upright) come back as they are."""
    from PIL import ImageOps
    try:
        if image.getexif().get(ORIENTATION, 1) not in (2, 3, 4, 5, 6, 7, 8):
            return image
        return ImageOps.exif_transpose(image)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError):
        return image


def load_references(stack, paths, *, edited_first=False):
    """The reference pictures as the model reads them: upright, RGB, transparency on white. With edited_first (a mask
    is drawn on the first one) that picture keeps its stored pixels, which the mask and the requested size are in."""
    from PIL import Image
    references = []
    for index, path in enumerate(paths):
        image = stack.enter_context(Image.open(path))
        references.append(_flatten(image if edited_first and index == 0 else _upright(image)))
    return references


def next_request(inbox, wait, crowding=None):
    """The next row, or None when the pipe closed, `wait` seconds passed, or the card is crowded.

    Without `crowding` this is one blocking read, as for an ordinary idle wait."""
    if crowding is None:
        try:
            return inbox.rows.get(timeout=wait)
        except queue.Empty:
            return None
    deadline = time.monotonic() + wait
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return inbox.rows.get(timeout=min(remaining, keep.POLL_SECONDS))
        except queue.Empty:
            if crowding:
                return None


def latest(inbox, value):
    """The row to handle now: a picture that waits behind warm-ups goes first and those warm-ups are dropped;
    of several warm-ups only the last counts, except that a light one never replaces a full one. None when the
    pipe closed."""
    rows = [value]
    while True:
        try:
            rows.append(inbox.rows.get_nowait())
        except queue.Empty:
            break
    if any(row is None for row in rows):
        return None
    picked = next((row for row in rows if row.get('action') == 'generate'), rows[-1])
    if picked.get('action') == 'warm' and picked.get('level') != 'full':
        # A light warm-up after a full one for the same model (with no leaving in between) does not undo it.
        for row in reversed(rows):
            if row.get('action') == 'unwarm':
                break
            if row.get('action') == 'warm' and row.get('level') == 'full' and row.get('model') == picked.get('model'):
                return row
    return picked


def warm_up(runner, value, should_stop, send):
    """Prepare for the picture the page is setting up: read the model files ahead into the OS file cache and, for a
    full warm-up of a runner that can (H3 Image), load the model as a picture leaves it. Stops once
    ``should_stop()`` is true. No prompt is part of a warm-up."""
    facts = dict(phase='warmed', model=value.get('model'), level=value.get('level'))
    files = value.get('files') or []
    if files and not should_stop():
        from ..encoder_prewarm import available_memory, plan, prefetch
        cancelled = threading.Event()
        sizes = sum(os.path.getsize(item) for item in files)
        available, complete = available_memory()
        read = prefetch(files, plan(sizes, available, complete=complete, partial=True), cancelled, busy=should_stop)
        facts['read'] = read.get('state')
    if value.get('level') == 'full' and runner is not None and hasattr(runner, 'prepare') and not should_stop():
        facts['prepared'] = runner.prepare(value.get('h3'), should_stop)
    send(**facts)


def hold(runner, idle_seconds=keep.IDLE_SECONDS):
    """Start the long idle wait: hand back the allocator's cached memory, then watch the card."""
    torch = sys.modules.get('torch')
    if torch is not None:
        torch.cuda.empty_cache()
    return keep.Crowding(lambda: keep.card_free_bytes(runner.device), limit=keep.limit(runner.device),
                         others=lambda: keep.card_other_use(runner.device), host=keep.host_available_bytes,
                         host_least=getattr(runner, 'host_least_bytes', keep.HOST_LEAST_BYTES), host_after=idle_seconds)


def drop(runner):
    """Close the runner and hand its card memory back; returns None for the caller's variable. The caller still holds the
    runner while this runs, so its locked host pages are only returned by release_memory(), called once the variable is None."""
    runner.close()
    import gc
    gc.collect()
    torch = sys.modules.get('torch')
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
    return None


def release_memory():
    """Collect, then return the card's and the host's cached memory: torch keeps freed locked (pinned) pages cached, and they stay charged to the process, so the next model loaded in this worker would find them still taken."""
    import gc
    gc.collect()
    torch = sys.modules.get('torch')
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        from ..torch_compat import empty_host_cache
        empty_host_cache(torch)


# The process the server's whole process tree starts from, in server mode (see server_mode); None on the desktop.
SERVER_ROOT = None
# In server mode the worker takes the installation's GPU lease itself, for each picture and each model load, and holds
# nothing between them: a video then starts beside the kept picture model. None: the service holds the lease for the
# worker's whole life (the desktop).
LOCK_PATH = None
# How long a picture waits for the lease another job holds (the service sends none while a video runs).
LEASE_WAIT_SECONDS = 30.


@contextmanager
def lease():
    """The GPU lease for one piece of work in server mode (its descriptor is what the text encoding hands on); on the
    desktop the service already holds it and this does nothing."""
    if LOCK_PATH is None:
        yield None
        return
    from ..locking import LOCK_ENV, runtime_lock
    with runtime_lock(LOCK_PATH, inherit=False, wait_seconds=LEASE_WAIT_SECONDS) as descriptor:
        os.environ[LOCK_ENV] = str(descriptor)
        try:
            yield descriptor
        finally:
            os.environ.pop(LOCK_ENV, None)


def server_hold(runner, root):
    """The watch of a server-mode wait: card memory taken by programs outside the server's process tree (the video
    worker is in it), or host memory below the least the worker keeps. None when the card's use per program cannot
    be read, so the caller falls back to the desktop wait."""
    def others():
        use = keep.card_other_use(runner.device)
        if use is None:
            return None
        try:
            from ..background_room import family
            ours = family(root)
        except OSError:
            return None
        return {pid: size for pid, size in use.items() if pid not in ours}

    if others() is None:
        return None
    torch = sys.modules.get('torch')
    if torch is not None:
        torch.cuda.empty_cache()
    # The whole-card reading is left out: the video worker in the same tree takes and gives back card memory.
    return keep.Crowding(lambda: None, others=others, host=keep.host_available_bytes, host_limit=float('inf'),
                         host_least=getattr(runner, 'host_least_bytes', keep.HOST_LEAST_BYTES))


def idle_wait(runner, requested, idle_seconds, *, at_least=0.):
    """(seconds, crowding) for the next wait: the long wait a kept model may have (``keep.wait_seconds``), at least
    ``at_least`` while the page is warming, and a watch that ends any wait longer than the idle time once another
    program needs the card or host memory. A warm-up never shortens the wait a picture left. In server mode the
    worker waits until the server stops or a program outside it needs the card or host memory."""
    if SERVER_ROOT is not None:
        from ..server_mode import WAIT_SECONDS
        if runner is None:
            return WAIT_SECONDS, None
        try:
            crowding = server_hold(runner, SERVER_ROOT)
        except Exception as error:
            print('image worker server hold failed: %s' % type(error).__name__, file=sys.stderr, flush=True)
            crowding = None
        if crowding is not None:
            return WAIT_SECONDS, crowding
    if runner is None:
        # Nothing is held on the card, so there is nothing to watch: a light warm-up still keeps the worker for the
        # rest of the page's warm-up (H3 Image places its encoder meanwhile, then asks for the full warm-up).
        return max(idle_seconds, at_least), None
    wait = max(keep.wait_seconds(requested, runner, idle_seconds), at_least, idle_seconds)
    if wait <= idle_seconds:
        return idle_seconds, None
    try:
        return wait, hold(runner, idle_seconds)
    except Exception as error:
        # A hold that cannot start is the normal short wait.
        print('image worker hold failed: %s' % type(error).__name__, file=sys.stderr, flush=True)
        return idle_seconds, None


def load_runner(pack_dir, device, *, progress=None):
    # Importing this module in ComfyUI or in CPU protocol tests never loads torch.
    from .image_runner import ImageRunner
    return ImageRunner(pack_dir, device=device, progress=progress)


def load_h3_runner(pack_dir, device, *, progress=None):
    from .h3_runner import H3Runner
    return H3Runner(pack_dir, device=device, progress=progress)


def load_moss_runner(pack_dir, device, *, progress=None):
    from .moss_runner import MossRunner
    return MossRunner(pack_dir, device=device, progress=progress)


def serve(pack_dir, *, device='cuda', idle_seconds=45., stream=None, output=None, factory=None, h3_factory=None,
          moss_factory=None, server_root=None, lock_path=None):
    global SERVER_ROOT, LOCK_PATH
    SERVER_ROOT = server_root
    LOCK_PATH = lock_path
    stream = sys.stdin if stream is None else stream
    output = sys.stdout if output is None else output
    factory = factory or load_runner
    h3_factory = h3_factory or load_h3_runner
    moss_factory = moss_factory or load_moss_runner
    runner_model = None
    factories = {'minimax-h3-image': h3_factory, 'moss-image-sft': moss_factory}
    output_mutex = threading.Lock()

    def send(**row):
        with output_mutex:
            output.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + '\n')
            output.flush()

    inbox = Inbox(stream, send)
    runner = None
    back_detector = None
    back_detector_loaded = False
    wait, crowding = idle_seconds, None
    try:
        while not inbox.closed.is_set():
            # When the wait now starting ends; a warm-up keeps at least what is left of it.
            until = time.monotonic() + wait
            value = next_request(inbox, wait, crowding)
            if value is None and crowding is not None and not inbox.closed.is_set() and time.monotonic() < until:
                # Another program needed the room: the service records this reason instead of an idle end.
                print(keep.CROWDED_LINE, file=sys.stderr, flush=True)
            if value is not None and value.get('action') in ('warm', 'unwarm'):
                value = latest(inbox, value)
            if value is None or inbox.closed.is_set():
                break
            if value.get('action') == 'unwarm':
                # The page was left: the wait a picture would have left, from now on.
                wait, crowding = idle_wait(runner, value.get('keep_seconds'), idle_seconds)
                continue
            if value.get('action') == 'warm':
                model = value.get('model') or 'qwen-image-2.1-turbo'
                should_stop = lambda: not inbox.rows.empty() or inbox.closed.is_set()
                load = value.get('level') == 'full'
                with ExitStack() as held, redirect_stdout(sys.stderr):
                    try:
                        if load:
                            held.enter_context(lease())
                        # A light warm-up only reads files ahead: it neither loads nor drops a model.
                        if load and runner is not None and runner_model != model:
                            runner = drop(runner)
                            release_memory()
                        if load and runner is None:
                            # No progress rows: nothing on the page is waiting for this.
                            runner = factories.get(model, factory)(pack_dir, device)
                            runner_model = model
                        warm_up(runner, value, should_stop, send)
                    except Exception as error:
                        print('image worker warm-up failed: %s' % type(error).__name__, file=sys.stderr, flush=True)
                        if runner is not None and not getattr(runner, 'at_boundary', lambda: True)():
                            runner = drop(runner)
                            release_memory()
                wait, crowding = idle_wait(runner, value.get('keep_seconds'), idle_seconds,
                                           at_least=max(keep.WARM_SECONDS, until - time.monotonic()))
                continue
            started = time.monotonic()
            context = Context('generate', value)
            result = None
            try:
                from PIL import Image
                with ExitStack() as stack, redirect_stdout(sys.stderr):
                    if inbox.cancel.is_set():
                        raise InterruptedError('cancelled')
                    stack.enter_context(lease())
                    def progress(stage, fraction):
                        context.timing.enter(stage)
                        send(phase='progress', stage=stage, fraction=fraction)

                    model = value.get('model') or 'qwen-image-2.1-turbo'
                    if runner is not None and runner_model != model:
                        # One model's weights at a time on the card.
                        runner = drop(runner)
                        release_memory()
                    if runner is None:
                        context.timing.enter('load')
                        runner = factories.get(model, factory)(pack_dir, device, progress=progress)
                        runner_model = model
                    if hasattr(runner, 'last_plan'):
                        runner.last_plan = None
                    context.timing.enter('start')
                    references = load_references(stack, value.get('images', []), edited_first=bool(value.get('mask')))
                    region = {}
                    if value.get('mask'):
                        region['edit_mask'] = stack.enter_context(Image.open(value['mask']))
                        region['edit_mask'].load()
                    loras = value.get('loras') or None
                    back_check = None
                    def preview(*, step, total, width, height, jpeg):
                        data = base64.b64encode(jpeg).decode('ascii')
                        if len(data) <= PREVIEW_LIMIT:
                            send(phase='preview', step=step, total=total, width=width, height=height, jpeg=data)

                    def note(facts):
                        send(phase='plan', plan=facts)

                    def generate(seed):
                        return runner.generate(
                            value['prompt'], width=value['width'], height=value['height'],
                            seed=seed, reference_images=references, **region,
                            **(dict(loras=loras) if loras else {}),
                            progress=progress, cancel=inbox.cancel.is_set,
                            **(dict(setup=value['h3']) if model in ('minimax-h3-image', 'moss-image-sft') and value.get('h3') else {}),
                            # Which stage a failure belongs to, without a progress row the page would show.
                            **(dict(preview=preview, stage=context.timing.enter, note=note) if model == 'minimax-h3-image'
                               else dict(stage=context.timing.enter) if model == 'moss-image-sft' else {}))

                    if value.get('cast_view') == 'back' and len(references) == 1 and not region:
                        from .back_view import generate_back, load_detector
                        if not back_detector_loaded:
                            back_detector = load_detector(pack_dir)
                            back_detector_loaded = True
                        image, back_check = generate_back(generate, value.get('seed'), back_detector,
                                                         cancel=inbox.cancel.is_set)
                    else:
                        image = generate(value.get('seed'))
                    if inbox.cancel.is_set():
                        raise InterruptedError('cancelled')
                    # The parent chooses a fresh path under ComfyUI input.
                    # Exclusive creation never replaces an existing user file.
                    plan = runner.stats().get('plan')
                    seed = plan.get('seed') if isinstance(plan, dict) and type(plan.get('seed')) is int else None
                    with Path(value['path']).open('xb') as target:
                        if Path(value['path']).suffix.lower() == '.jpg':
                            # Quality 95 without chroma subsampling is visually lossless at about a quarter of the PNG size.
                            image.convert('RGB').save(target, format='JPEG', quality=95, subsampling=0,
                                                      **(dict(comment=('seed=%d' % seed).encode()) if seed is not None else {}))
                        else:
                            info = None
                            if seed is not None:
                                from PIL.PngImagePlugin import PngInfo
                                info = PngInfo()
                                info.add_text('seed', str(seed))
                            image.save(target, format='PNG', **(dict(pnginfo=info) if info else {}))
                    if back_check is not None:
                        plan = dict(plan or {}, back_check=back_check)
                result = dict(phase='result', path=value['path'], seconds=time.monotonic() - started, plan=plan)
                try:
                    from ..input_cache import used
                    # The cached encodings this picture read or wrote: deleting the picture removes the ones only it used.
                    result['receipts'] = used()[:64]
                except Exception:
                    pass
                try:
                    result.update(attention_report(runner, context.redactor))
                except Exception:
                    # Diagnostics must not turn a saved picture into an error.
                    pass
            except Exception as error:
                code = classify_exception(error, stage=context.timing.stage, cancelled=inbox.cancel.is_set())
                plan = None
                if runner is not None:
                    try:
                        plan = safe_plan(runner.stats().get('plan'), context.redactor)
                    except Exception:
                        # Diagnostics must not replace the inference exception.
                        pass
                reload = not getattr(runner, 'at_boundary', lambda: True)()
                result = dict(phase='error', code=code, stage=context.timing.stage, reloaded=reload,
                              seconds=context.timing.seconds(), stages=context.timing.snapshot(), plan=plan,
                              error=exception_details(error, context.redactor, code))
                if code == 'no_memory' and host_memory_shortage(error):
                    result['memory'] = 'host'
                try:
                    result.update(attention_report(runner, context.redactor))
                except Exception:
                    pass
            finally:
                if result is not None and result.get('reloaded'):
                    # A failure inside a layer pass leaves the weights streamer part-way through it and the
                    # next request would be refused; the model is loaded again for that one.
                    with redirect_stdout(sys.stderr):
                        try:
                            runner.close()
                        except Exception as error:
                            print('image runner close failed: %s' % type(error).__name__, file=sys.stderr, flush=True)
                    runner = None
                    release_memory()
                with inbox.mutex:
                    inbox.busy = False
                    if result is not None:
                        send(**result)
            wait, crowding = idle_seconds, None
            if result is not None and result.get('phase') == 'result' and runner is not None and hasattr(runner, 'idle'):
                # Prepare the next picture until a request, a closed pipe or a cancel arrives.
                try:
                    with redirect_stdout(sys.stderr):
                        runner.idle(lambda: not inbox.rows.empty() or inbox.closed.is_set() or inbox.cancel.is_set())
                except Exception as error:
                    print('image worker prepare failed: %s' % type(error).__name__, file=sys.stderr, flush=True)
            if result is not None and result.get('phase') == 'result' and runner is not None:
                wait, crowding = idle_wait(runner, value.get('keep_seconds'), idle_seconds)
    finally:
        inbox.closed.set()
        inbox.cancel.set()
        if runner is not None:
            with redirect_stdout(sys.stderr):
                runner.close()


def attention_report(runner, redactor=None):
    """{'attention': ...} on Macs where MLX attention was installed (why it fell back, if it did); {} elsewhere."""
    if runner is None:
        return {}
    from . import mlx_attention, qwen_dit
    value = mlx_attention.summary(qwen_dit.attention)
    if value is None:
        return {}
    if redactor is not None and 'fallback_reason' in value:
        value['fallback_reason'] = redactor.text(value['fallback_reason'])[:300]
    return dict(attention=value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack_dir', type=Path)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--idle-seconds', type=float, default=45.)
    parser.add_argument('--server-root', type=int, default=None,
                        help='server mode: the process the server starts from (its tree is not "another program")')
    parser.add_argument('--lock-path', type=Path, default=None,
                        help='server mode: the GPU lease file the worker takes for each picture and model load')
    args = parser.parse_args()
    if args.server_root is not None and args.server_root <= 0:
        parser.error('--server-root must be a process id')
    if args.lock_path is not None and args.server_root is None:
        parser.error('--lock-path belongs to server mode')
    if not 0 < args.idle_seconds < float('inf'):
        parser.error('--idle-seconds must be positive and finite')
    # A native crash (access violation, driver fault) leaves no Python error: the crash
    # handler writes the Python stack to a file the service reads once the worker is gone.
    crash_file = os.environ.get('FREEVIDEO_CRASH_FILE')
    if crash_file:
        try:
            import faulthandler
            faulthandler.enable(file=open(crash_file, 'w'), all_threads=False)
        except (OSError, RuntimeError, ValueError):
            pass
    stream = None
    if os.name == 'nt':
        from ..win32 import private_stdin
        try:
            stream = private_stdin()
        except OSError as error:
            print('private stdin unavailable, reading stdin directly: %s' % error, file=sys.stderr, flush=True)
    serve(args.pack_dir, device=args.device, idle_seconds=args.idle_seconds, stream=stream,
          server_root=args.server_root, lock_path=args.lock_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
