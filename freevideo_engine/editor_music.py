"""Music for the editing page: a stretch of the sound track made into one piece of music.

The page plans the piece where the user dragged (start and length on the sound track), sends the
length and what the music should be (its own words, or a style and a mood), and lays the finished
file into that stretch. This module only makes the file: first-use download of the music model,
one job at a time, progress, cancel. The music service yields to video: a video that starts while
the music is made stops it, and the job waits for the video and composes again.
"""
import logging
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path

FPS = 24
FOLDER = 'freevideo-music'
MAX_JOBS = 20
MAX_QUIET = 500
MAX_TEXT = 512
# A video that starts while the music is made stops it; the job waits and composes again (as the Agent's job does).
PREEMPTIONS = 8
POLL_SECONDS = .5
_JOB_ID = re.compile(r'm-[0-9a-f]{32}')
_FIELDS = {'frames', 'text', 'style', 'mood', 'seed', 'quiet'}
REASONS = ('music_download_failed', 'out_of_memory', 'music_failed', 'invalid_request', 'busy', 'unsupported', 'failed')

# Presets: the genre and instruments, then the feeling and pace. Instrumental only, as the Agent asks for.
STYLES = {
    'cinematic': 'Cinematic orchestral score with strings, soft brass and piano',
    'pop': 'Modern instrumental pop with clean electric guitar, bass, light drums and synth pads',
    'electronic': 'Electronic track with warm synthesizers, a steady drum machine and a deep bass line',
    'piano': 'Solo piano piece with a clear melody and gentle sustain',
    'folk': 'Acoustic folk tune with fingerpicked guitar, light percussion and a soft violin',
    'ambient': 'Ambient soundscape with slow evolving pads, soft textures and distant bells',
}
MOODS = {
    'calm': 'calm and peaceful, slow tempo around 70 BPM',
    'upbeat': 'bright and upbeat, lively tempo around 120 BPM',
    'tense': 'tense and suspenseful, a low pulse around 90 BPM that slowly builds',
    'warm': 'warm and tender, moderate tempo around 85 BPM',
    'sad': 'sad and reflective, slow tempo around 65 BPM in a minor key',
}
# The feeling alone, without the pace: used after the user's own words, which set the pace themselves
# (a fixed "around 70 BPM" after "a festival parade" pulled the piece back to the preset).
FEELINGS = {
    'calm': 'calm and peaceful',
    'upbeat': 'bright and upbeat',
    'tense': 'tense and suspenseful',
    'warm': 'warm and tender',
    'sad': 'sad and reflective, in a minor key',
}
DEFAULT_STYLE, DEFAULT_MOOD = 'cinematic', 'calm'


def caption(style, mood, words=None):
    """The English description the music model reads for a preset pair, or for the user's words with the pair after them."""
    if style not in STYLES or mood not in MOODS:
        raise ValueError('Unknown style or mood')
    if words:
        return '%s. %s, %s. Instrumental only, no singing, no vocals.' % (words.rstrip('.'), STYLES[style], FEELINGS[mood])
    return '%s, %s. Instrumental only, no singing, no vocals.' % (STYLES[style], MOODS[mood])


def _whole(value, low, high):
    return type(value) is int and low <= value <= high


def max_frames():
    from .music import service
    return service.MAX_SECONDS * FPS


def validate_request(body):
    """The page's request -> what the job runs. Unknown fields, unknown presets and bad ranges are refused."""
    if not isinstance(body, dict) or set(body) - _FIELDS:
        raise ValueError('Invalid request')
    frames = body.get('frames')
    if not _whole(frames, 1, max_frames()):
        raise ValueError('Invalid length')
    text = body.get('text', '')
    if not isinstance(text, str) or len(text) > MAX_TEXT or '\x00' in text:
        raise ValueError('Invalid description')
    text = ' '.join(text.split())
    style, mood = body.get('style'), body.get('mood')
    if (style is None) != (mood is None):
        raise ValueError('Style and mood go together')
    if style is not None and (style not in STYLES or mood not in MOODS):
        raise ValueError('Unknown style or mood')
    if not text and style is None:
        style, mood = DEFAULT_STYLE, DEFAULT_MOOD
    seed = body.get('seed')
    if seed is None:
        seed = secrets.randbelow(2**32)
    elif not _whole(seed, 0, 2**32 - 1):
        raise ValueError('Invalid seed')
    quiet = body.get('quiet', [])
    if not isinstance(quiet, list) or len(quiet) > MAX_QUIET:
        raise ValueError('Invalid quiet spans')
    spans = []
    for span in quiet:
        if (not isinstance(span, dict) or set(span) != {'start', 'frames'} or not _whole(span['start'], 0, frames - 1)
                or not _whole(span['frames'], 1, frames - span['start'])):
            raise ValueError('Invalid quiet span')
        spans.append((span['start'], span['frames']))
    # The user's own words go first, with the chosen style and feeling after them (not the preset's pace);
    # the full preset alone when there are none.
    if text:
        words, stem = text if style is None else caption(style, mood, text), 'text'
    else:
        words, stem = caption(style, mood), '%s-%s' % (style, mood)
    return dict(frames=frames, caption=words, stem=stem, style=style, mood=mood, seed=seed, quiet=spans)


def _gpu_busy(server):
    """True while a video (queued or running), a picture or the prompt model has the GPU."""
    try:
        from . import resident_process
        if resident_process.OWNER.active:
            return True
    except Exception:  # noqa: BLE001 - an optional check; the music service refuses again if the GPU is taken
        pass
    if server is None:
        return False
    try:
        front = getattr(server, '_freevideo_front', None)
        running, pending = front.current_queue() if front is not None else server.prompt_queue.get_current_queue()
        if running or pending:
            return True
    except Exception:  # noqa: BLE001
        pass
    for name in ('_freevideo_prompt_vlm',):
        service = getattr(server, name, None)
        try:
            if service is not None and service.busy():
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        from .image_gen.service import get_service
        service = get_service(server)
        return service is not None and service.busy()
    except Exception:  # noqa: BLE001
        return False


def run_job(request, *, root, out, phase, cancelled, server=None, service=None, busy=None, poll=POLL_SECONDS):
    """Compose one piece into `out`; returns the music service's numbers. Raises InterruptedError when cancelled."""
    if service is None:
        from .music import service
    busy = busy or (lambda: _gpu_busy(server))
    for attempt in range(PREEMPTIONS + 1):
        try:
            return service.compose(out, request['caption'], request['frames'], seed=request['seed'], quiet=request['quiet'],
                                   fps=FPS, server=server, cancelled=cancelled, phase=phase)
        except service.Preempted:
            if attempt == PREEMPTIONS:
                raise service.MusicError('music_failed', 'preempted') from None
            phase('waiting')
            # Wait out the video (and anything queued with it) before the GPU is taken again.
            while True:
                if cancelled():
                    raise InterruptedError('cancelled') from None
                time.sleep(poll)
                if not busy():
                    break


class MusicQueue:
    """One worker, first in first out, cancellable; keeps the last MAX_JOBS jobs for the page to read back."""
    def __init__(self, root, *, runner=None, read=None, clock=time.monotonic, **kwargs):
        self.root = Path(root).absolute()
        self.runner = runner or run_job
        self.read = read or self._read
        self.clock = clock
        self.kwargs = kwargs
        self.lock = threading.RLock()
        self.jobs = {}
        self.pending = deque()
        self.worker = None
        # Seconds of composing per second of music on this machine, from the last finished job.
        self.pace = None

    def _read(self, source):
        from . import comfy_editor
        return comfy_editor.sources(self.root, [source])[source]

    def _view(self, job):
        value = {key: job[key] for key in ('id', 'state', 'phase', 'fraction', 'reason', 'result')}
        # Composing reports no steps; the page shows time instead, from how long it took here last time.
        if job['state'] == 'running' and job['phase'] == 'composing' and job['began'] is not None:
            value['elapsed'] = round(self.clock() - job['began'], 1)
            value['estimate'] = None if self.pace is None else round(self.pace * job['request']['frames'] / FPS, 1)
        return value

    def submit(self, body):
        request = validate_request(body)
        hexid = secrets.token_hex(16)
        job = dict(id='m-' + hexid, state='queued', phase=None, fraction=0., reason=None, result=None, began=None,
                   request=request, relative='%s/%s-%s.mp4' % (FOLDER, request['stem'], hexid), stop=threading.Event())
        with self.lock:
            for old in list(self.jobs):
                if len(self.jobs) < MAX_JOBS:
                    break
                if self.jobs[old]['state'] not in ('queued', 'running'):
                    del self.jobs[old]
            if len(self.jobs) >= MAX_JOBS:
                raise MusicBusy()
            self.jobs[job['id']] = job
            self.pending.append(job['id'])
            initial = self._view(job)
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name='freevideo-editor-music', daemon=True)
                self.worker.start()
            return initial

    def get(self, identity):
        if not isinstance(identity, str) or not _JOB_ID.fullmatch(identity):
            raise ValueError('Invalid job')
        with self.lock:
            if identity not in self.jobs:
                raise KeyError(identity)
            return self._view(self.jobs[identity])

    def cancel(self, identity):
        with self.lock:
            self.get(identity)
            job = self.jobs[identity]
            if job['state'] in ('queued', 'running'):
                job['stop'].set()
                if identity in self.pending:
                    self.pending.remove(identity)
                    job.update(state='cancelled', phase=None)
            return self._view(job)

    def busy(self):
        with self.lock:
            return any(job['state'] in ('queued', 'running') for job in self.jobs.values())

    def _run(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.worker = None
                    return
                job = self.jobs[self.pending.popleft()]
                if job['stop'].is_set():
                    job['state'] = 'cancelled'
                    continue
                job['state'] = 'running'

            def phase(name, fraction=None):
                with self.lock:
                    if name == 'composing' and job['phase'] != 'composing':
                        job['began'] = self.clock()
                    job['phase'] = name
                    job['fraction'] = 0. if fraction is None else max(0., min(.99, float(fraction)))

            out = self.root / job['relative']
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                began = self.clock()
                numbers = self.runner(job['request'], root=self.root, out=out, phase=phase, cancelled=job['stop'].is_set,
                                      **self.kwargs) or {}
                if job['stop'].is_set():
                    raise InterruptedError('cancelled')
                source = 'generated:' + job['relative']
                found = self.read(source)
                if found.get('missing') or not found.get('frames') or not found.get('has_audio'):
                    raise MusicFailed('music_failed')
                spent = numbers.get('load_s', 0) + numbers.get('generate_s', 0) if numbers.get('generate_s') else self.clock() - began
                with self.lock:
                    self.pace = max(.05, spent / (job['request']['frames'] / FPS))
                    job.update(state='done', phase=None, fraction=1., result=dict(
                        source=source, frames=int(found['frames']), seed=job['request']['seed'],
                        style=job['request']['style'], mood=job['request']['mood']))
            except BaseException as error:  # noqa: BLE001 - every outcome is the job's; the worker keeps serving
                out.unlink(missing_ok=True)
                with self.lock:
                    if isinstance(error, InterruptedError) or job['stop'].is_set():
                        job.update(state='cancelled', phase=None, reason=None)
                    else:
                        code = getattr(error, 'code', None)
                        if code not in REASONS:
                            logging.warning('[FreeVideo] Editor music failed', exc_info=error)
                            code = 'music_failed'
                        job.update(state='failed', phase=None, reason=code)
                if not isinstance(error, Exception):
                    raise


class MusicBusy(Exception):
    code = 'busy'


class MusicFailed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class MusicPack:
    """First-use download of the music model, with the download sources chosen in Settings."""
    def __init__(self, *, installation=None, service=None, status=None, free_bytes=None):
        self._installation = installation
        self._service = service
        self._status = status
        self._free = free_bytes
        self.lock = threading.Lock()
        self.value = dict(running=False, fraction=0., reason=None)
        self.stop = threading.Event()
        self.worker = None

    def installation(self):
        if self._installation is not None:
            return self._installation()
        from .comfy_bridge import installation
        return installation()

    def service(self):
        if self._service is not None:
            return self._service
        from .music import service
        return service

    def status(self):
        root, machine = self.installation()
        if self._status is not None:
            pack = self._status(machine['model_root'])
        else:
            from .image_gen import image_pack
            pack = image_pack.status(machine['model_root'], group='music')
        if self._free is not None:
            free = self._free(machine['model_root'])
        else:
            from . import disk_space
            from .image_gen import image_pack
            folder = image_pack.base(machine['model_root'])
            while not folder.exists() and folder.parent != folder:
                folder = folder.parent
            free = disk_space.free_bytes(folder)
        remaining = int(pack.get('remaining_bytes', pack.get('bytes', 0)))
        from .image_gen.image_pack import RESERVE
        with self.lock:
            value = dict(self.value)
        value.update(ready=bool(pack['ready']), bytes=remaining,
                     disk_ok=bool(pack['ready']) or free >= remaining + RESERVE)
        return value

    def start(self):
        """Begin the download unless it runs already; returns False when the disk cannot take it."""
        value = self.status()
        if value['ready'] or value['running']:
            return True
        if not value['disk_ok']:
            with self.lock:
                self.value.update(reason='disk_space')
            return False
        root, machine = self.installation()
        with self.lock:
            if self.value['running']:
                return True
            self.stop = threading.Event()
            self.value = dict(running=True, fraction=0., reason=None)
            self.worker = threading.Thread(target=self._run, args=(root, machine['model_root'], self.stop),
                                           name='freevideo-editor-music-pack', daemon=True)
            self.worker.start()
        return True

    def cancel(self):
        self.stop.set()

    def _run(self, root, model_root, stop):
        service = self.service()

        def progress(fraction):
            with self.lock:
                self.value['fraction'] = max(self.value['fraction'], min(.99, float(fraction)))

        reason = None
        try:
            service.install(model_root, root, progress, stop.is_set)
        except InterruptedError:
            pass
        except service.MusicError as error:
            reason = error.reason or 'failed'
        except Exception:  # noqa: BLE001 - the page says it did not download; the log keeps why
            logging.warning('[FreeVideo] Music model download failed', exc_info=True)
            reason = 'failed'
        with self.lock:
            self.value = dict(running=False, fraction=0. if reason or stop.is_set() else 1., reason=reason)


def available(installation=None):
    """The music model runs where video runs through CUDA (NVIDIA and AMD ROCm); not on Apple silicon yet."""
    try:
        if installation is None:
            from .comfy_bridge import installation
        _root, machine = installation()
        return machine.get('device_backend', 'cuda') != 'mps'
    except Exception:  # noqa: BLE001 - no engine, no music
        return False


class SpeechSpans:
    """Speech spans per editor source for 'Lower during speech'; one file at a time, off the request thread.

    The preview asks per source and gets pending until the spans are ready; the export asks for all of its
    sources at once and waits. Both read the same cached spans for the same file.
    """
    def __init__(self, root, *, resolve=None, model_path=None, compute=None):
        self.root = Path(root)
        self._resolve, self._model_path, self._compute = resolve, model_path, compute
        self.lock = threading.Lock()
        self.pending, self.failed, self.known = deque(), {}, {}
        self.worker = None

    def cache(self):
        return self.root / 'FreeVideo' / '.editor' / 'speech'

    def model_path(self):
        if self._model_path is not None:
            return Path(self._model_path)
        from .comfy_bridge import installation
        from .image_gen import image_pack
        from .speech import MODEL_FILE
        return image_pack.base(installation()[1]['model_root']) / MODEL_FILE

    def audio(self, key):
        if self._resolve is not None:
            return self._resolve(key)
        from .editor_export import resolve_source
        return resolve_source(key).get('audio')

    def compute(self, path, cancelled=None):
        if self._compute is not None:
            return self._compute(path)
        from .speech import speech_spans
        return speech_spans(path, self.model_path(), self.cache(), cancelled)

    def get(self, key):
        """{'spans': [...]} when known, {'state': 'pending'} while working, {'spans': None, 'reason': …} otherwise."""
        if not isinstance(key, str) or not re.fullmatch(r'(work|generated|import):[^\x00]{1,512}', key):
            raise ValueError('Invalid source')
        try:
            path = self.audio(key)
        except (FileNotFoundError, KeyError):
            return dict(spans=None, reason='source_missing')
        if path is None:
            return dict(spans=[])
        with self.lock:
            if key in self.known:
                return dict(spans=self.known[key])
        model = self.model_path()
        if not model.is_file():
            return dict(spans=None, reason='model_missing')
        from .speech import cached
        found = cached(path, model, self.cache())
        if found is not None:
            return dict(spans=found)
        with self.lock:
            if key in self.failed:
                return dict(spans=None, reason=self.failed[key])
            if key not in self.pending:
                if len(self.pending) >= MAX_QUIET:
                    return dict(spans=None, reason='busy')
                self.pending.append(key)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, name='freevideo-editor-speech', daemon=True)
                self.worker.start()
        return dict(state='pending')

    def _run(self):
        from .speech import SpeechError
        while True:
            with self.lock:
                if not self.pending:
                    self.worker = None
                    return
                key = self.pending[0]
            reason = found = None
            try:
                found = self.compute(self.audio(key))
            except SpeechError as error:
                reason = error.reason
            except Exception:  # noqa: BLE001 - one unreadable source must not stop the others
                logging.getLogger(__name__).warning('Editor speech spans failed for %s.', key, exc_info=True)
                reason = 'failed'
            with self.lock:
                self.pending.popleft()
                if reason is not None:
                    self.failed[key] = reason
                else:
                    self.known[key] = found

    def spans(self, keys, cancelled=None):
        """Speech spans for each key the export needs; a source that cannot be read has none."""
        found = {}
        for key in keys:
            try:
                path = self.audio(key)
                found[key] = self.compute(path, cancelled) if path is not None else []
            except Exception as error:  # noqa: BLE001 - no spans for that source, as in the preview
                if getattr(error, 'reason', None) == 'cancelled':
                    raise
                logging.getLogger(__name__).warning('Editor export: no speech spans for %s (%s).', key,
                                                    getattr(error, 'reason', type(error).__name__))
        return found


def register():
    """Register the editing page's music endpoints once on the existing ComfyUI server."""
    import asyncio
    from aiohttp import web
    from server import PromptServer
    from .editor_export import _output_root
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_editor_music', None) is not None:
        return
    queue = MusicQueue(_output_root(), server=server)
    pack = MusicPack()
    server._freevideo_editor_music = queue
    server._freevideo_editor_speech = speech = SpeechSpans(_output_root())
    headers = {'Cache-Control': 'no-store'}

    def response(value):
        return web.json_response({'job': value}, headers=headers)

    def refused(reason, status):
        return web.json_response({'reason': reason}, status=status, headers=headers)

    @server.routes.post('/freevideo/editor/music')
    async def start(request):
        try:
            body = await request.json()
            if not await asyncio.to_thread(available):
                return refused('unsupported', 409)
            return response(await asyncio.to_thread(queue.submit, body))
        except MusicBusy:
            return refused('busy', 503)
        except (ValueError, TypeError, KeyError):
            return refused('invalid_request', 400)

    @server.routes.get('/freevideo/editor/music')
    async def status(request):
        identity = request.query.get('id')
        if identity is None:
            supported = await asyncio.to_thread(available)
            try:
                ready = supported and (await asyncio.to_thread(pack.status))['ready']
            except Exception:  # noqa: BLE001 - an unreadable pack reads as not downloaded
                ready = False
            # pace: seconds of composing per second of music here, from the last piece (null before the first).
            return web.json_response(dict(available=supported, ready=bool(ready), pace=queue.pace), headers=headers)
        try:
            return response(queue.get(identity))
        except KeyError:
            return refused('missing', 404)
        except ValueError:
            return refused('invalid_request', 400)

    @server.routes.post('/freevideo/editor/music/cancel')
    async def cancel(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'id'}:
                raise ValueError('Invalid request')
            return response(queue.cancel(body['id']))
        except KeyError:
            return refused('missing', 404)
        except (ValueError, TypeError):
            return refused('invalid_request', 400)

    @server.routes.get('/freevideo/editor/speech')
    async def speech_spans(request):
        try:
            return web.json_response(await asyncio.to_thread(speech.get, request.query.get('source')), headers=headers)
        except ValueError:
            return refused('invalid_request', 400)

    @server.routes.get('/freevideo/editor/music-pack')
    async def pack_status(request):
        return web.json_response(await asyncio.to_thread(pack.status), headers=headers)

    @server.routes.post('/freevideo/editor/music-pack')
    async def pack_action(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'action'} or body['action'] not in ('install', 'cancel'):
                raise ValueError('Invalid request')
            if body['action'] == 'cancel':
                pack.cancel()
            elif not await asyncio.to_thread(available):
                return refused('unsupported', 409)
            elif not await asyncio.to_thread(pack.start):
                return web.json_response(await asyncio.to_thread(pack.status), status=507, headers=headers)
        except (ValueError, TypeError):
            return refused('invalid_request', 400)
        return web.json_response(await asyncio.to_thread(pack.status), headers=headers)
