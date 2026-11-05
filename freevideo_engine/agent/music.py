"""Music jobs: one piece as long as the timeline, composed locally and laid on sound track a2."""
import json
import secrets
import time

from .. import comfy_editor
from .jobs import JobError

FOLDER = 'freevideo-music'
# A video that starts while the music is made stops it; the job waits for the video and composes again.
PREEMPTIONS = 8


def _busy(jobs):
    port = jobs.port
    return jobs.runner_busy() or port.queue_busy() or port.image_busy() or port.vlm_busy()


def run_music(jobs, row, *, poll=.5):
    from ..music import service
    spec, port = row['spec'], jobs.port
    try:
        project = jobs.projects.get(spec['editor_project'])['project']
    except FileNotFoundError as error:
        raise JobError('not_found', 'The project does not exist') from error
    frames = comfy_editor.duration(project)
    if not frames:
        raise JobError('invalid_request', 'The timeline has no clips to score')
    if frames / comfy_editor.FPS > service.MAX_SECONDS:
        raise JobError('invalid_request', 'The timeline is longer than %d s, the longest piece the music model makes' % service.MAX_SECONDS)
    relative = '%s/%s-%s.mp4' % (FOLDER, row['id'], secrets.token_hex(4))
    out = port.output_path(relative, create=True)
    seed = spec.get('seed', secrets.randbelow(2**32))
    quiet = [(span['start'], span['frames']) for span in spec.get('quiet', ())]

    def phase(name, fraction=None):
        row['_phase'] = name
        row['_detail'] = json.dumps(dict(fraction=round(fraction, 3))) if fraction is not None else None

    try:
        numbers = _compose(jobs, row, service, out, frames, seed, quiet, phase, poll)
        phase('placing')
        saved, clip_ids = jobs.projects.lay_music(spec['editor_project'], 'generated:' + relative)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return dict(editor_project=spec['editor_project'], revision=saved['revision'], clip_ids=clip_ids,
                source='generated:' + relative, frames=frames, seed=seed,
                generate_seconds=numbers.get('generate_s'), load_seconds=numbers.get('load_s'))


def _compose(jobs, row, service, out, frames, seed, quiet, phase, poll, *, caption=None):
    for attempt in range(PREEMPTIONS + 1):
        try:
            return service.compose(out, row['spec']['caption'] if caption is None else caption, frames, seed=seed, quiet=quiet, fps=comfy_editor.FPS,
                                   server=getattr(jobs.port, 'server', None), cancelled=lambda: bool(row.get('_cancel')),
                                   phase=phase)
        except service.Preempted:
            if attempt == PREEMPTIONS:
                raise JobError('music_failed', 'Videos kept starting while the music was being made') from None
            phase('waiting')
            # Wait out the video (and anything queued with it) before the GPU is taken again.
            while True:
                if row.get('_cancel'):
                    raise JobError('cancelled', 'Cancelled') from None
                time.sleep(poll)
                if not _busy(jobs):
                    break
        except InterruptedError:
            raise JobError('cancelled', 'Cancelled') from None
        except service.MusicError as error:
            if error.code == 'invalid_request':
                raise JobError('invalid_request', 'The timeline is too long for one piece of music') from None
            raise JobError(error.code, 'The music was not made (%s)' % (error.reason or error.code), kind='music') from None
