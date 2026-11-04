"""Stem jobs: the sound of one clip split into dialogue, music and ambience, laid on three sub-tracks."""
import json
import secrets
import time

from .jobs import JobError

FOLDER = 'freevideo-stems'
# A video that starts while the sound is split stops it; the job waits for the video and splits again.
PREEMPTIONS = 8
NAMES = ('dialogue', 'music', 'ambience')


def _busy(jobs):
    port = jobs.port
    return jobs.runner_busy() or port.queue_busy() or port.image_busy() or port.vlm_busy()


def run_stems(jobs, row, *, poll=.5):
    from ..editor_export import resolve_source
    from ..stems import service
    spec, port = row['spec'], jobs.port
    try:
        project = jobs.projects.get(spec['editor_project'])['project']
    except FileNotFoundError as error:
        raise JobError('not_found', 'The project does not exist') from error
    clip = next((clip for track in project['tracks'] if track['kind'] == 'audio' for clip in track['clips']
                 if clip['id'] == spec['clip_id']), None)
    if clip is None or not clip.get('source'):
        raise JobError('invalid_request', 'The clip is not a sound clip with media')
    try:
        found = resolve_source('%s:%s' % (clip['source']['kind'], clip['source']['id']))
    except (OSError, ValueError) as error:
        raise JobError('stems_failed', 'The clip media is not readable', kind='stems') from error
    source = found['audio']
    if source is None:
        raise JobError('stems_failed', 'The clip has no sound', kind='stems')
    folder = '%s/%s-%s' % (FOLDER, row['id'], secrets.token_hex(4))
    out = port.output_path(folder + '/dialogue.mp4', create=True).parent

    def phase(name, fraction=None):
        row['_phase'] = name
        row['_detail'] = json.dumps(dict(fraction=round(fraction, 3))) if fraction is not None else None

    try:
        numbers = _split(jobs, row, service, source, out, phase, poll)
        sources = {name: 'generated:%s/%s.mp4' % (folder, name) for name in NAMES}
        if not spec.get('lay', True):
            return dict(editor_project=spec['editor_project'], clip_id=spec['clip_id'], sources=sources, parts=[], clip_ids=[],
                        seconds=numbers.get('seconds'), separate_seconds=numbers.get('elapsed'))
        phase('placing')
        saved, clip_ids = jobs.projects.lay_stems(spec['editor_project'], spec['clip_id'], sources)
    except BaseException:
        for path in out.glob('*'):
            path.unlink(missing_ok=True)
        out.rmdir()
        raise
    fresh = set(clip_ids)
    parts = [dict(track=track['id'], name=track['stem'], clip_id=clip['id'], start=clip['start'], frames=clip['frames'],
                  end=clip['start'] + clip['frames'])
             for track in saved['tracks'] if track.get('stem') for clip in track['clips'] if clip['id'] in fresh]
    return dict(editor_project=spec['editor_project'], revision=saved['revision'], clip_ids=clip_ids, sources=sources,
                parts=parts, seconds=numbers.get('seconds'),
                separate_seconds=numbers.get('elapsed'))


def _split(jobs, row, service, source, out, phase, poll):
    for attempt in range(PREEMPTIONS + 1):
        try:
            return service.separate(source, out, server=getattr(jobs.port, 'server', None),
                                    cancelled=lambda: bool(row.get('_cancel')), phase=phase)
        except service.Preempted:
            if attempt == PREEMPTIONS:
                raise JobError('stems_failed', 'Videos kept starting while the sound was being split', kind='stems') from None
            phase('waiting')
            while True:
                if row.get('_cancel'):
                    raise JobError('cancelled', 'Cancelled') from None
                time.sleep(poll)
                if not _busy(jobs):
                    break
        except InterruptedError:
            raise JobError('cancelled', 'Cancelled') from None
        except service.StemsError as error:
            raise JobError(error.code, 'The sound was not split (%s)' % (error.reason or error.code), kind='stems') from None
