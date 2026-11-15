"""White-model scenes: the Agent writes the scene, the user's page renders it, the video joins the library.

The video is a rough stand-in for the real shot (boxes, camera, motion). A confirmed one is then used as a
reference video that only guides the first steps (early_steps), so the final video keeps its layout and motion."""
import json
import os
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import media
from .jobs import JobError

ID = r'^wb[0-9a-f]{10}$'
MAX_CODE = 64 * 1024
MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 12
MIN_SECONDS = 1
KEPT = 20
PAGE_FRESH = 15.       # A page that asked for work this recently is open.
RENDER_LEASE = 150.    # A claimed scene nobody finished goes back to the queue once, then fails.
EARLY_STEPS = 3       # For an 8-step first pass; other lengths scale by 3/8 (20 steps -> 8).
FOLLOW_RATIO = 3 / 8
SCENE_PROMPT = """You are a 3D blocking programmer. From one shot description, write a Three.js scene that builds a white model with as few shapes as possible: only boxes, cylinders, spheres, cones and planes, with no textures, no external files and no text. It is a composition and motion reference for a video model, not a finished picture: shapes must be recognisable, with plausible positions, scale and motion paths.

Shot description: {SHOT}

Output rules (follow strictly):
1. Output only one ES module that exports `export function build(THREE)` and returns `{ scene, camera, update }`.
2. `update(t)`: t is in seconds, 0 to 5.0. Every object and the camera depend only on t (a pure function: no Date, no Math.random, no accumulated state). The whole action in the description must finish within 5 seconds, and the camera and the main subject stay visible throughout.
3. Right-handed, y up, metres, true scale (a person about 1.7 m, a cat about 0.3 m, a room about 3 m high).
4. Camera: `PerspectiveCamera(fov, 960/544, 0.1, 200)`; set its position and `lookAt` inside `update` on every frame; move it with smooth interpolation (smoothstep or a spline): no jitter, no jumps.
5. Materials: always `new THREE.MeshStandardMaterial({ color, roughness: 1 })`, one simple colour per object that differs from its neighbours (a striking colour for the subject; grey, beige or light blue for the background). Set `mesh.name`; the subject's name starts with `hero`.
6. Lights: one HemisphereLight and one DirectionalLight with shadows; set `scene.background` to a light sky or wall colour. No fog, no post-processing.
7. At most 80 meshes. Build everything in `build`; `update` only changes positions, rotations and scales. No dependency other than the module itself.
8. Write motion as interpolation between key points (position, orientation, scale where needed); no physics simulation.

Return only the code.

Fix list for a retry: THREE.Geometry no longer exists (use BufferGeometry classes); never create objects in `update`; camera position and lookAt target must differ; if too dark use hemisphere light >= 1.2 and directional >= 1.4; if the camera enters an object, move its path farther or higher."""


def _remux(source, target):
    """A webm recording becomes a real mp4 without re-encoding: the library opens every work as video.mp4."""
    import av
    with av.open(str(source)) as incoming, av.open(str(target), 'w', format='mp4') as outgoing:
        video = incoming.streams.video[0]
        stream = outgoing.add_stream_from_template(video)
        for packet in incoming.demux(video):
            if packet.dts is None:
                continue
            packet.stream = stream
            outgoing.mux(packet)


class Whitebox:
    def __init__(self, directory, output_root, clock=time.time):
        self.directory, self.root, self.clock = Path(directory), Path(output_root), clock
        self.lock = threading.RLock()
        self.seen = None
        self.records = {}
        try:
            for path in sorted(self.directory.glob('wb*.json')):
                try:
                    record = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if isinstance(record, dict) and record.get('id') == path.stem:
                    self.records[record['id']] = record
        except OSError:
            pass

    # Storage -------------------------------------------------------------------------------------------
    def _save(self, record):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (record['id'] + '.json')
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
        stale = sorted(self.records.values(), key=lambda r: r['created_at'])[:-KEPT]
        for old in stale:
            self.records.pop(old['id'], None)
            (self.directory / (old['id'] + '.json')).unlink(missing_ok=True)

    def _get(self, identity):
        record = self.records.get(identity)
        if record is None:
            raise JobError('not_found', 'No such white-model scene')
        return record

    def _expire(self, record):
        """A claim the page never finished: back to waiting once, then failed."""
        if record['status'] == 'rendering' and self.clock() - record['claimed_at'] > RENDER_LEASE:
            if record['attempts'] < 2:
                record['status'] = 'waiting'
            else:
                record.update(status='failed', error='The page did not finish rendering the scene.')
            self._save(record)

    def view(self, record):
        self._expire(record)
        result = {k: record[k] for k in ('id', 'status', 'summary', 'created_at')}
        result['page_open'] = self.page_open()
        if record['status'] in ('waiting', 'rendering'):
            result['notice'] = ('The open FreeVideo page renders the scene by itself; no user action is needed. '
                                'Read it again until it is ready or failed.')
        if record['status'] == 'ready':
            result.update(work=record['work'], seconds=record['seconds'], early_steps=EARLY_STEPS, early_ratio=FOLLOW_RATIO,
                          notice='This clip is only the motion reference, not the requested video, so the task is not finished. '
                                 'Generate the video itself with media.references=[{"library": work, "early_steps": early_steps}] '
                                 'and give the user that job\'s result.')
        if record['status'] == 'failed':
            result['error'] = record['error']
        return result

    def page_open(self):
        return self.seen is not None and self.clock() - self.seen <= PAGE_FRESH

    # Agent ---------------------------------------------------------------------------------------------
    def create(self, code, summary, session=None, notice=None):
        if not isinstance(code, str) or not code.strip() or len(code.encode('utf-8')) > MAX_CODE:
            raise JobError('invalid_request', 'Scene code must be nonempty text of at most %d bytes' % MAX_CODE)
        if 'build' not in code:
            raise JobError('invalid_request', 'Scene code must define build(THREE)')
        with self.lock:
            record = dict(id='wb' + secrets.token_hex(5), status='waiting', code=code, summary=str(summary or '')[:500],
                          session=session, created_at=self.clock(), claimed_at=None, attempts=0, work=None,
                          seconds=None, error=None, notice=notice)
            self.records[record['id']] = record
            self._save(record)
            return self.view(record)

    def open_for(self, session):
        """The scenes this conversation started that are still being made, or failed without the user being told."""
        with self.lock:
            rows = []
            for record in sorted(self.records.values(), key=lambda r: r['created_at']):
                self._expire(record)
                if record.get('session') == session and record.get('notice') and record['status'] != 'ready' \
                        and self.clock() - record['created_at'] < 1800:
                    rows.append(dict(id=record['id'], status=record['status'], summary=record['summary'],
                                     **(dict(error=record['error']) if record['status'] == 'failed' else {})))
            return rows

    def take_notice(self, identity):
        """What the server should tell the user's conversation once the scene is ready; handed out once."""
        with self.lock:
            record = self._get(identity)
            notice = record.get('notice')
            if record['status'] != 'ready' or not notice:
                return None
            record['notice'] = None
            self._save(record)
            return dict(notice, session=record['session'], work=record['work'], seconds=record['seconds'])

    def read(self, identity):
        with self.lock:
            return self.view(self._get(identity))

    # Page ----------------------------------------------------------------------------------------------
    def claim(self):
        """The oldest waiting scene with its code, or None; asking at all tells the Agent a page is open."""
        with self.lock:
            self.seen = self.clock()
            for record in sorted(self.records.values(), key=lambda r: r['created_at']):
                self._expire(record)
                if record['status'] == 'waiting':
                    record.update(status='rendering', claimed_at=self.clock(), attempts=record['attempts'] + 1)
                    self._save(record)
                    return dict(id=record['id'], code=record['code'], summary=record['summary'])
            return None

    def fail(self, identity, message):
        with self.lock:
            record = self._get(identity)
            if record['status'] in ('ready', 'failed'):
                return self.view(record)
            record.update(status='failed', error=str(message or 'The scene failed to render')[:500])
            self._save(record)
            return self.view(record)

    def finish(self, identity, upload):
        """Store the page's rendered mp4 as a library work and mark the scene ready."""
        with self.lock:
            record = self._get(identity)
            if record['status'] == 'ready':
                return self.view(record)
            if record['status'] != 'rendering':
                raise JobError('conflict', 'The scene is not being rendered')
        upload = Path(upload)
        if upload.stat().st_size > MAX_VIDEO_BYTES:
            raise JobError('too_large', 'The rendered video is too large')
        try:
            info, suffix = media.validate_upload(upload)
        except (OSError, ValueError, SyntaxError):
            raise JobError('invalid_media', 'The rendered video cannot be decoded') from None
        # The page records with whatever its browser can encode (H.264, VP9, mp4 or webm): content decides, not the name.
        if info['kind'] != 'video' or suffix not in ('.mp4', '.webm') or not MIN_SECONDS <= info['seconds'] <= MAX_SECONDS:
            raise JobError('invalid_media', 'The rendered video must last %d to %d seconds' % (MIN_SECONDS, MAX_SECONDS))
        day = time.strftime('%Y-%m-%d')
        folder = self.root / 'FreeVideo' / day / uuid.uuid4().hex
        folder.mkdir(parents=True)
        try:
            try:
                if suffix != '.webm':
                    raise ValueError('already mp4')
                _remux(upload, folder / 'video.mp4')
            except Exception:
                # Codecs mp4 cannot hold (VP8) keep the recording as it came; the content still decodes.
                shutil.copyfile(upload, folder / 'video.mp4')
            (folder / 'prompt.txt').write_text(record['summary'], encoding='utf-8')
            fps = info.get('fps') or 24.
            report = dict(success=True, geometry=dict(width=info['width'], height=info['height'],
                          frames=info.get('frames') or round(info['seconds'] * fps), fps=fps, seconds=info['seconds']),
                          request_seconds=0, video={}, encoding={}, whitebox=dict(scene=record['id']))
            request = folder / 'video.request.json'
            temporary = request.with_name(request.name + '.tmp')
            temporary.write_text(json.dumps(report), encoding='utf-8')
            os.replace(temporary, request)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        with self.lock:
            record.update(status='ready', work='%s/%s' % (day, folder.name), seconds=info['seconds'])
            self._save(record)
            return self.view(record)
