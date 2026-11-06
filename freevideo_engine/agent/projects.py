"""Editing projects for agents: the page's timeline, read and changed through the API.

A project is the editor page's own record (web/editor_model.js). Operations run
the page's rule code in QuickJS (editor_runtime), so the Agent and the page cut,
move and make room the same way; the result is saved with the page's save, whose
revision check makes the later of two writers receive project_changed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
import threading
import time

from .. import comfy_editor
from . import editor_runtime, errors, schema, spec
from .jobs import JobError, library_id, library_view

MAX_OPERATIONS = 64
MAX_RECEIPTS = 256
# Rule codes that describe the timeline's state; the rest are request errors.
STATE_CODES = {'busy': 'clip_busy', 'locked': 'clip_locked', 'audio_full': 'audio_full', 'overlap': 'overlap'}
# What a rejected operation needed, by the page's rule code.
RULE_EXPECTED = {
    'range': 'clip_ids that exist and a frame inside them (frames are whole numbers at 24 fps)',
    'missing': 'clip_ids from this project revision',
    'text': 'a short description of at most one paragraph',
    'ref': 'a ref of 1-128 letters, digits, - or _, or an empty string to remove it',
    'source': 'a source that has a picture or sound',
    'invalid': 'an op named in the operations schema with its fields',
}
# The note operation shares the page's 'text' code with describe; what it needed is its own.
NOTE_EXPECTED = 'picture clips and a text of at most 120 characters on one line (an empty text removes the note)'
# An agent acts on the last answer it read, so the answer that brings new picture clips asks for their notes.
# It comes first in that answer: the project after it carries every clip's description, and a real agent
# that printed only the start of a long answer never saw a request placed after it.
NOTE_NEXT = ('Picture clips %s have no note yet. Write each one now with a note operation: in the user\'s language, '
             'what is in the picture, subject first; not its position and not its progress.')


def note_next(result, project, ids):
    missing = [clip['id'] for track in project['tracks'] if track['kind'] == 'video' for clip in track['clips']
               if clip['id'] in ids and not clip.get('note')]
    return dict(next=NOTE_NEXT % ', '.join(missing), **result) if missing else result


def new_project(given):
    """The page's empty timeline with what the caller sent on top; a name alone is enough to start one."""
    return dict(dict(version=1, id=None, revision=0, name='', fps=comfy_editor.FPS, width=1344, height=768, settings=None, serial=0,
                     tracks=[dict(id='v1', kind='video', clips=[]), dict(id='a1', kind='audio', clips=[])]), **given)


class ProjectError(JobError):
    """A project error; a conflict carries the current project so the caller can retry from it."""

    def __init__(self, code, message=None, *, project=None, operation=None):
        super().__init__(code, message or errors.ERRORS[code][2])
        self.status = errors.ERRORS[code][0]
        self.project, self.operation = project, operation

    def view(self):
        value = errors.build(self.code, validation=True, details=[], message=self.message,
                             next_action=errors.ERRORS[self.code][3])
        if self.operation is not None:
            value['details'].append(dict(operation=self.operation))
        if self.project is not None:
            value['project'] = deepcopy(self.project)
            value['current_revision'] = self.project['revision']
        return value


def _as_expected(project, clip_id, expect):
    from .voice import signature
    return signature(_clips(project).get(clip_id)) == expect


def _invalid(path, expected, received=None):
    return spec.SpecError([schema.diagnostic(path, expected, received)])


def _refused(project, error):
    """The editor's own check names the part it refused; an Agent cannot take its next step without it."""
    # The editor's reasons are fixed phrases ("Invalid clip", "Invalid source"); they repeat nothing supplied.
    reason = str(error) if type(error) is ValueError and re.fullmatch(r'[A-Z][A-Za-z ]{2,60}', str(error)) else ''
    row = schema.diagnostic('/project', 'a valid editor project' + (' (%s)' % (reason[:1].lower() + reason[1:]) if reason else ''),
                            project)
    row['next'] = ('Correct that part of /project. For a new timeline send only {"project": {"name": "..."}}; '
                   'then add each clip with an insert operation.')
    return spec.SpecError([row])


def _digest(scope, payload):
    return hashlib.sha256(json.dumps(dict(scope=scope, body=payload), sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _operation_key(ref, client=None):
    """The page save's 32-hex operation id for an agent ref, so a lost reply can be replayed."""
    key = 'agent:' + ref if client is None else 'agent-client:' + json.dumps(
        [client, ref], ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _clips(project):
    return {clip['id']: clip for track in project['tracks'] for clip in track['clips']}


def changed_clip_ids(before, after):
    """Ids of parts added, removed or changed by an edit, in timeline order of the result, then removed ones."""
    old, new = _clips(before), _clips(after)
    changed = [identity for identity, clip in new.items() if old.get(identity) != clip]
    return changed + [identity for identity in old if identity not in new]


def _claims(project):
    """(path, source key, length) wherever a project says how long a source is: every clip and every version of it."""
    tracks = project.get('tracks') if isinstance(project, dict) else None
    for t, track in enumerate(tracks if isinstance(tracks, list) else ()):
        clips = track.get('clips') if isinstance(track, dict) else None
        for c, clip in enumerate(clips if isinstance(clips, list) else ()):
            if not isinstance(clip, dict):
                continue
            path, versions = '/project/tracks/%d/clips/%d' % (t, c), clip.get('versions')
            rows = [(path, clip.get('source'), clip.get('length'))]
            rows += [('%s/versions/%d' % (path, v), row, row.get('length'))
                     for v, row in enumerate(versions if isinstance(versions, list) else ()) if isinstance(row, dict)]
            for where, source, length in rows:
                if (isinstance(source, dict) and isinstance(source.get('kind'), str) and isinstance(source.get('id'), str)
                        and comfy_editor._whole(length, 1, 10**7)):
                    yield where + '/length', source['kind'] + ':' + source['id'], int(length)


class Projects:
    def __init__(self, root, *, directory=None):
        self.root = root  # The ComfyUI output folder, where the page keeps FreeVideo/.editor
        self.lock = threading.Lock()
        self.listen_lock = threading.Lock()
        self.receipts = {}  # (client, ref) -> {digest, result}; in memory, bounded; the page save keeps the durable one.
        self.prepared = {}  # job id -> the reserved project, until its chain id is written into it.

    def _load(self, identity):
        try:
            return comfy_editor.load(self.root, identity)
        except (FileNotFoundError, ValueError) as error:
            raise ProjectError('not_found', 'The project does not exist.') from error

    def _receipt(self, scope, payload, ref, client=None):
        if ref is None:
            return None, None
        digest = _digest(scope, payload)
        with self.lock:
            old = self.receipts.get((client, ref))
        if old is not None:
            if old['digest'] != digest:
                raise JobError('idempotency_conflict', 'Key already used')
            return digest, deepcopy(old['result'])
        return digest, None

    def _remember(self, ref, digest, result, client=None):
        if ref is None:
            return
        with self.lock:
            self.receipts[client, ref] = dict(digest=digest, result=deepcopy(result))
            while len(self.receipts) > MAX_RECEIPTS:
                self.receipts.pop(next(iter(self.receipts)))

    def _save(self, project, *, ref=None, create=None, client=None):
        operation = _operation_key(ref, client) if ref is not None else None
        try:
            return comfy_editor.save(self.root, project, create=create, operation=operation)
        except comfy_editor.Conflict as error:
            raise ProjectError('project_changed', project=self._load(error.identity or project['id'])['project']) from error
        except comfy_editor.MissingProject as error:
            raise ProjectError('not_found', 'The project does not exist.') from error
        except comfy_editor.TooLarge as error:
            raise ProjectError('too_large') from error
        except OSError as error:
            raise ProjectError('storage_unavailable') from error
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise _refused(project, error) from error

    def _rejected(self, error, stored, path, received, *, operation=None):
        if error['code'] in STATE_CODES:
            raise ProjectError(STATE_CODES[error['code']], project=stored, operation=operation)
        if error['code'] == 'text' and isinstance(received, dict) and received.get('op') == 'note':
            raise _invalid(path, NOTE_EXPECTED, received)
        raise _invalid(path, RULE_EXPECTED.get(error['code'], 'an operation this timeline allows'), received)

    # Reads ---------------------------------------------------------------------------------------------
    def list(self):
        return dict(items=comfy_editor.list_projects(self.root))

    def get(self, identity):
        return self._load(identity)

    # Whole-project writes ------------------------------------------------------------------------------
    def _lengths(self, project, stored=None):
        """A written project cannot say a source is longer than it is.

        The page takes every length from the file; a whole-project write states it. A clip that asks for
        frames its source does not have would only be found by the export, which stops there.
        """
        known = {row[1:] for row in _claims(stored)} if stored else set()
        for path, key, length in _claims(project):
            if (key, length) in known:
                continue
            try:
                info = comfy_editor.sources(self.root, [key])[key]
            except ValueError:
                continue  # not a source at all: the editor's own check refuses it and names the part
            known.add((key, length))
            if not info['missing'] and info['frames'] and length > info['frames']:
                raise _invalid(path, 'at most %d, the number of frames this source has' % info['frames'], length)

    def create(self, value, *, ref=None, client=None):
        schema.validate(schema.project_schema('create'), value)
        digest, replay = self._receipt('projects:create', value, ref, client)
        if replay is not None:
            return replay
        project = value.get('project') if isinstance(value, dict) else None
        project = new_project(project) if isinstance(project, dict) else None
        if project is None or project.get('id') is not None or project.get('revision') != 0:
            raise _invalid('/project', 'a new project: its name is enough, with no id and no revision')
        # The page's receipt makes a repeated create return the first project.
        create = _operation_key(ref, client) if ref is not None else None
        self._lengths(project)
        result = dict(project=self._save(deepcopy(project), create=create))
        self._remember(ref, digest, result, client)
        return result

    def write(self, identity, value, *, ref=None, client=None):
        schema.validate(schema.project_schema('write'), value)
        digest, replay = self._receipt('projects:write', dict(id=identity, body=value), ref, client)
        if replay is not None:
            return replay
        project = value.get('project') if isinstance(value, dict) else None
        if not isinstance(project, dict) or project.get('id') != identity:
            raise _invalid('/project/id', 'the project id in the path', project.get('id') if isinstance(project, dict) else None)
        before = self._load(identity)['project']
        self._lengths(project, before)
        saved = self._save(deepcopy(project), ref=ref, client=client)
        result = dict(project=saved, changed_clip_ids=changed_clip_ids(before, saved))
        self._remember(ref, digest, result, client)
        return result

    # Operations ----------------------------------------------------------------------------------------
    def _media(self, operations):
        """Insert operations name a source key; the server fills in its length and streams, and for a
        saved video the description it was generated from."""
        keys = [op['source'] for op in operations if op.get('op') == 'insert']
        found = comfy_editor.sources(self.root, keys) if keys else {}
        resolved = []
        for index, op in enumerate(operations):
            if op.get('op') != 'insert':
                resolved.append(op)
                continue
            info = found[op['source']]
            if info['missing'] or not info['frames']:
                raise ProjectError('source_missing', 'The source to insert is not available.', operation=index)
            kind, identity = op['source'].split(':', 1)
            media = dict(source=dict(kind=kind, id=identity), frames=info['frames'], hasVideo=info['has_video'],
                         hasAudio=info['has_audio'], width=info['width'], height=info['height'])
            if info.get('text'):
                media['text'] = info['text']
            resolved.append(dict({k: v for k, v in op.items() if k != 'source'}, media=media))
        return resolved

    def operations(self, identity, value, *, client=None, ref=None):
        """Apply 1-64 operations to the revision the caller read; all succeed and save once, or nothing changes."""
        schema.validate(schema.project_schema('operations'), value)
        revision, operations = value.get('revision'), value.get('operations')
        digest, replay = self._receipt('projects:operations', dict(id=identity, body=value), ref, client)
        if replay is not None:
            return replay
        stored = self._load(identity)['project']
        if stored['revision'] != revision:
            raise ProjectError('project_changed', project=stored)
        try:
            for index, op in enumerate(operations):
                if op.get('op') == 'insert':
                    comfy_editor.source_key(dict(zip(('kind', 'id'), op['source'].split(':', 1))))
        except (ValueError, TypeError, AttributeError) as error:
            raise _invalid('/operations/%d/source' % index,
                           'a source key: work:<library id>, generated:<file> or import:<id>', op.get('source')) from error
        reply = editor_runtime.runtime().batch(stored, self._media(operations))
        if not reply['ok']:
            index = reply['index']
            self._rejected(reply['error'], stored, '/operations/%d' % index if index is not None else '/project',
                           operations[index] if index is not None else None, operation=index)
        saved = self._save(reply['project'], ref=ref, client=client)
        result = dict(project=saved, changed_clip_ids=changed_clip_ids(stored, saved), results=reply['results'])
        result = note_next(result, saved, set(result['changed_clip_ids']) - set(_clips(stored)))
        self._remember(ref, digest, result, client)
        return result


    # Generation ----------------------------------------------------------------------------------------
    def generate(self, identity, value, *, jobs, capability=None, ref=None, client=None):
        """Make new video and sound for a range or parts, as the page's Generate does.

        capability(size) returns the device's capability reply for that size, or None.

        The parts are planned and saved first (the reservation), so a page that
        saved in between makes this fail with project_changed before any GPU
        work; only then is the chain submitted. Its blocks fill the parts as
        they finish (deliver), whether or not the page is open.
        """
        schema.validate(schema.project_schema('generate'), value)
        digest, replay = self._receipt('projects:generate', dict(id=identity, body=value), ref, client)
        if replay is not None:
            return replay
        text, target, settings = value.get('text', ''), value['target'], value.get('settings') or {}
        stored = self._load(identity)['project']
        if stored['revision'] != value['revision']:
            raise ProjectError('project_changed', project=stored)
        rules = editor_runtime.runtime()
        plan = rules.generation(mode='plan', project=stored, target=target, text=text, capability=None)
        # Part lengths follow what this device can make at the project's size, as on the page;
        # until that is measured the page's 15 s parts apply.
        reply = capability(plan['size']) if capability is not None and plan['ok'] else None
        if isinstance(reply, dict) and reply.get('status', 'ready') == 'ready':
            plan = rules.generation(mode='plan', project=stored, target=target, text=text, capability=reply)
        if not plan['ok']:
            self._rejected(plan['error'], stored, '/target', target)
        found = comfy_editor.sources(self.root, plan['keys']) if plan['keys'] else {}
        if any(info['missing'] or not info.get('path') for info in found.values()):
            raise ProjectError('source_missing', 'The part this generation continues from is not available.')
        request = rules.generation(mode='request', project=plan['project'], ids=plan['ids'], text=text,
                                   paths={key: info['path'] for key, info in found.items()},
                                   sizes={key: dict(width=info['width'], height=info['height']) for key, info in found.items()})
        if not request['ok']:
            self._rejected(request['error'], stored, '/target', target)
        size, limit = plan['size'], plan['max_request_frames']
        if request.get('size') and request['size'] != size:
            # A part continuing footage of another size has that size's limit, as on the page.
            size = request['size']
            reply = capability(size) if capability is not None else None
            if isinstance(reply, dict) and reply.get('status', 'ready') == 'ready':
                limit = rules.generation(mode='plan', project=stored, target=target, text=text, capability=reply,
                                         size=size).get('max_request_frames')
        longest = max(block['frames'] for block in request['blocks'])
        if limit is not None and longest > limit:
            raise _invalid('/target', 'parts of at most %d frames at this size and quality; choose a shorter range'
                           % limit, longest)
        seed = settings['seed'] if settings.get('seed') is not None else secrets.randbelow(2**32)
        blocks = [dict(block, seed=seed + index) for index, block in enumerate(request['blocks'])]
        prompt = text.strip() or next((b['text'] for b in blocks if b['text'].strip()), '') or 'Continue the video.'
        frames = sum(block['frames'] for block in blocks)
        spec = dict({k: v for k, v in settings.items() if k in ('quality', 'sampling', 'loras', 'force_regenerate')},
                    kind='generate', prompt=prompt, seed=seed, width=size['width'],
                    height=size['height'], seconds=min(60., max(1.625, frames / 24)),
                    label='Editing project ' + identity)
        saved = self._save(plan['project'])  # The reservation: the page's revision check orders it.
        editor = dict(project=identity, ids=plan['ids'], blocks=blocks, revision=saved['revision'])
        try:
            job = jobs.submit(spec, client=client, editor=editor)[0]
        except Exception:
            self._release(identity, plan['ids'])
            raise
        with self.lock:
            self.prepared[job['id']] = saved
        result = note_next(dict(job=jobs.view(job), jobs=[jobs.view(job)], project=saved, clip_ids=plan['ids']),
                           saved, set(plan['ids']))
        self._remember(ref, digest, result, client)
        return result

    def export(self, identity, value, *, jobs, ref=None, client=None):
        """Queue an export job for the revision the caller read; a stale revision starts nothing."""
        schema.validate(schema.project_schema('export'), value)
        digest, replay = self._receipt('projects:export', dict(id=identity, body=value), ref, client)
        if replay is not None:
            return replay
        stored = self._load(identity)['project']
        if stored['revision'] != value['revision']:
            raise ProjectError('project_changed', project=stored)
        job = jobs.submit(dict(kind='export', editor_project=identity, revision=value['revision']), client=client)[0]
        result = dict(job=jobs.view(job), jobs=[jobs.view(job)])
        self._remember(ref, digest, result, client)
        return result

    def listen(self, identity, value, *, jobs, ref=None, client=None):
        """Queue a listening job (align or transcribe) for the revision the caller read; a stale revision starts nothing."""
        schema.validate(schema.project_schema('listen'), value)
        if (value['mode'] == 'align') != ('lines' in value):
            raise _invalid('/lines', 'lines for mode align, and none for transcribe', value.get('lines'))
        with self.listen_lock:  # the same key sent twice at once queues one job
            digest, replay = self._receipt('projects:listen', dict(id=identity, body=value), ref, client)
            if replay is not None:
                return replay
            stored = self._load(identity)['project']
            if stored['revision'] != value['revision']:
                raise ProjectError('project_changed', project=stored)
            job = jobs.submit(dict(kind='listen', editor_project=identity,
                                   **{k: v for k, v in value.items() if k != 'ref'}), client=client)[0]
            result = dict(job=jobs.view(job), jobs=[jobs.view(job)])
            self._remember(ref, digest, result, client)
            return result

    def music(self, identity, value, *, jobs, ref=None, client=None):
        """Queue a music job for the revision the caller read; a stale revision starts nothing."""
        schema.validate(schema.project_schema('music'), value)
        with self.listen_lock:  # the same key sent twice at once queues one job
            digest, replay = self._receipt('projects:music', dict(id=identity, body=value), ref, client)
            if replay is not None:
                return replay
            stored = self._load(identity)['project']
            if stored['revision'] != value['revision']:
                raise ProjectError('project_changed', project=stored)
            if not comfy_editor.duration(stored):
                raise _invalid('/revision', 'a revision whose timeline has clips', value['revision'])
            job = jobs.submit(dict(kind='music', editor_project=identity,
                                   **{k: v for k, v in value.items() if k != 'ref'}), client=client)[0]
            result = dict(job=jobs.view(job), jobs=[jobs.view(job)])
            self._remember(ref, digest, result, client)
            return result

    def finish_sound(self, identity, value, *, jobs, ref=None, client=None):
        """Queue one job that finishes the sound of the cut the caller read; a stale revision starts nothing."""
        schema.validate(schema.project_schema('finish_sound'), value)
        with self.listen_lock:
            digest, replay = self._receipt('projects:finish_sound', dict(id=identity, body=value), ref, client)
            if replay is not None:
                return replay
            stored = self._load(identity)['project']
            if stored['revision'] != value['revision']:
                raise ProjectError('project_changed', project=stored)
            if not comfy_editor.duration(stored):
                raise _invalid('/revision', 'a revision whose timeline has clips', value['revision'])
            job = jobs.submit(dict(kind='finish_sound', editor_project=identity,
                                   **{k: v for k, v in value.items() if k != 'ref'}), client=client)[0]
            result = dict(job=jobs.view(job), jobs=[jobs.view(job)])
            self._remember(ref, digest, result, client)
            return result

    def stems(self, identity, value, *, jobs, ref=None, client=None):
        """Queue a sound-splitting job for one sound clip of the revision the caller read."""
        schema.validate(schema.project_schema('stems'), value)
        with self.listen_lock:
            digest, replay = self._receipt('projects:stems', dict(id=identity, body=value), ref, client)
            if replay is not None:
                return replay
            stored = self._load(identity)['project']
            if stored['revision'] != value['revision']:
                raise ProjectError('project_changed', project=stored)
            clip = _clips(stored).get(value['clip_id'])
            if clip is None or not clip.get('source') or clip.get('stem_of'):
                raise _invalid('/clip_id', 'a sound clip with media that is not itself a separated part', value['clip_id'])
            job = jobs.submit(dict(kind='stems', editor_project=identity,
                                   **{k: v for k, v in value.items() if k != 'ref'}), client=client)[0]
            result = dict(job=jobs.view(job), jobs=[jobs.view(job)])
            self._remember(ref, digest, result, client)
            return result

    def stems_export(self, identity, value, *, ref=None, client=None):
        """Copy the four wav files of a split clip into the saved sounds; the clip must be one that was split."""
        from ..stems.separate import SeparationError, export_tracks
        schema.validate(schema.project_schema('stems_export'), value)
        with self.listen_lock:
            digest, replay = self._receipt('projects:stems_export', dict(id=identity, body=value), ref, client)
            if replay is not None:
                return replay
            stored = self._load(identity)['project']
            if stored['revision'] != value['revision']:
                raise ProjectError('project_changed', project=stored)
            parts = [clip for clip in _clips(stored).values() if clip.get('stem_of') == value['clip_id']]
            if not parts:
                raise _invalid('/clip_id', 'a clip that was split into dialogue, music and ambience', value['clip_id'])
            folder = parts[0]['source']['id'].rsplit('/', 1)[0]
            if not folder.startswith('freevideo-stems/') or folder.count('/') != 1:
                raise _invalid('/clip_id', 'a clip split by this version', value['clip_id'])
            name = folder.split('/', 1)[1]
            try:
                files = export_tracks(Path(self.root) / folder, Path(self.root) / 'freevideo-stems-saved' / name)
            except SeparationError as error:
                raise JobError('stems_failed', 'The separated sound cannot be saved (%s)' % error) from error
            result = dict(editor_project=identity, revision=stored['revision'], clip_id=value['clip_id'],
                          folder='freevideo-stems-saved/' + name, files=files)
            self._remember(ref, digest, result, client)
            return result

    def _voice_job(self, name, kind, identity, value, *, jobs, ref, client):
        """Queue a voice job for the revision the caller read; the selected clip must be a sound clip with media."""
        schema.validate(schema.project_schema(name), value)
        with self.listen_lock:
            digest, replay = self._receipt('projects:' + name, dict(id=identity, body=value), ref, client)
            if replay is not None:
                return replay
            stored = self._load(identity)['project']
            if stored['revision'] != value['revision']:
                raise ProjectError('project_changed', project=stored)
            selection = value['selection']
            clip = _clips(stored).get(selection['clip_id'])
            if clip is None or not clip.get('source'):
                raise _invalid('/selection/clip_id', 'a sound clip with media', selection['clip_id'])
            if 'end' in selection and selection['end'] <= selection.get('start', 0):
                raise _invalid('/selection/end', 'a time after the start', selection['end'])
            if kind == 'voice':
                ids = [line['id'] for line in value['lines']]
                if len(set(ids)) != len(ids):
                    raise _invalid('/lines', 'lines with different ids', ids[:3])
                for i, line in enumerate(value['lines']):
                    if line['end'] <= line['start']:
                        raise _invalid('/lines/%d/end' % i, 'a time after the start', line['end'])
                from . import voice
                if not value.get('recognition'):
                    raise _invalid('/recognition', 'the recognition voice_lines gave for these lines; run voice_lines first')
                wrong = voice.check(value['recognition'], identity, selection['clip_id'], value['lines'])
                if wrong:
                    raise _invalid(*wrong)
                reference = (value.get('voice') or {}).get('reference')
                if reference and reference['clip_id'] not in _clips(stored):
                    raise _invalid('/voice/reference/clip_id', 'a clip of this project', reference['clip_id'])
            job = jobs.submit(dict(kind=kind, editor_project=identity,
                                   **{k: v for k, v in value.items() if k != 'ref'}), client=client)[0]
            result = dict(job=jobs.view(job), jobs=[jobs.view(job)])
            self._remember(ref, digest, result, client)
            return result

    def voice_lines(self, identity, value, *, jobs, ref=None, client=None):
        """Queue the writing down of a selection's lines (what is said, and when)."""
        return self._voice_job('voice_lines', 'voice_lines', identity, value, jobs=jobs, ref=ref, client=client)

    def voice_replace(self, identity, value, *, jobs, ref=None, client=None):
        """Queue the new voice for the lines the caller changed."""
        return self._voice_job('voice_replace', 'voice', identity, value, jobs=jobs, ref=ref, client=client)

    def voice_estimate(self, identity, value, *, ref=None, client=None):
        """Seconds the changed lines take to make; asked at once, nothing is stored."""
        from . import voice
        schema.validate(schema.project_schema('voice_estimate'), value)
        self._load(identity)
        return voice.estimate(value['lines'], value.get('limit'))

    def voice_shorten(self, identity, value, *, ref=None, client=None):
        """Rewriting a line to a length needs a text model; none is wired in this version."""
        schema.validate(schema.project_schema('voice_shorten'), value)
        self._load(identity)
        raise JobError('shorten_unavailable', 'No text model is available to shorten the line')

    def lay_stems(self, identity, clip_id, sources, *, expect=None):
        """Put the three parts on sub-tracks under the clip and silence the clip; returns the save and the new clip IDs.

        `expect` (voice.signature of the clip) makes it a no-op that raises project_changed when the clip is not as it was."""
        before, refused = {}, {}
        def change(project):
            before['ids'] = set(_clips(project))
            if expect is not None and not _as_expected(project, clip_id, expect):
                refused['stale'] = True
                return dict(ok=True, project=project)
            reply = editor_runtime.runtime().call('placeStems', project, [clip_id, {
                name: dict(kind='generated', id=source.split(':', 1)[1]) for name, source in sources.items()}],
                module='model', serialize=True)[0]
            if not reply['ok']:
                refused.update(reply['error'])
                return dict(ok=True, project=project)
            return dict(ok=True, project=reply['value'])
        saved = self._update(identity, change)
        if refused.get('stale'):
            raise JobError('project_changed', 'The clip changed while the voice was made')
        if refused:
            code = STATE_CODES.get(refused.get('code'), 'stems_failed')
            raise JobError(code, 'The separated sound cannot be laid under the clip (%s)' % refused.get('code'))
        return saved, sorted(set(_clips(saved)) - before['ids'])

    def replace_voice_source(self, identity, clip_id, source, *, expect=None):
        """The dialogue clip takes the file with the replaced voice; its place and range stay. Returns the save.

        `expect` (voice.signature of the clip) makes it a no-op that raises project_changed when the clip is not as it was."""
        refused = {}
        def change(project):
            if expect is not None and not _as_expected(project, clip_id, expect):
                refused['stale'] = True
                return dict(ok=True, project=project)
            reply = editor_runtime.runtime().call('replaceSource', project, [clip_id, dict(
                kind='generated', id=source.split(':', 1)[1])], module='model', serialize=True)[0]
            if not reply['ok']:
                refused.update(reply['error'])
                return dict(ok=True, project=project)
            return dict(ok=True, project=reply['value'])
        saved = self._update(identity, change)
        if refused.get('stale'):
            raise JobError('project_changed', 'The clip changed while the voice was made')
        if refused:
            code = STATE_CODES.get(refused.get('code'), 'voice_failed')
            raise JobError(code, 'The replaced voice cannot be put on the sound track (%s)' % refused.get('code'))
        return saved

    def lay_music(self, identity, source):
        """Put a finished score on a2 from frame 0 of the project as it is now; returns the save and the new clip IDs."""
        found = comfy_editor.sources(self.root, [source])[source]
        if found['missing'] or not found['frames']:
            raise JobError('music_failed', 'The music file is not readable')
        kind, path = source.split(':', 1)
        operation = dict(op='insert', at=0, media=dict(source=dict(kind=kind, id=path), frames=found['frames'], hasVideo=False,
                                                        hasAudio=True, width=0, height=0, targetTrack='a2'))
        before, refused = {}, {}
        def change(project):
            before['ids'] = set(_clips(project))
            if any(track['id'] == 'a2' and track['clips'] for track in project['tracks']):
                # An insert there would push the whole cut later; an earlier score is removed first.
                refused.update(code='audio_full')
                return dict(ok=True, project=project)
            reply = editor_runtime.runtime().batch(project, [operation])
            if not reply['ok']:
                refused.update(reply['error'])
                return dict(ok=True, project=project)  # nothing changes; the reason is raised below
            return reply
        saved = self._update(identity, change)
        if refused:
            code = STATE_CODES.get(refused.get('code'), 'music_failed')
            raise JobError(code, 'Sound track a2 cannot take the music from frame 0 (%s)' % refused.get('code'))
        return saved, sorted(set(_clips(saved)) - before['ids'])

    def _update(self, identity, change, *, tries=4):
        """Read, change and save; a page save in between makes it read again. None when nothing changed."""
        for _ in range(tries):
            current = self._load(identity)['project']
            reply = change(current)
            if not reply['ok']:
                raise ProjectError('invalid_output', reply['error']['message'])
            if reply['project'] == current:
                return None
            try:
                return comfy_editor.save(self.root, reply['project'])
            except comfy_editor.Conflict:
                continue
        raise ProjectError('project_changed', project=self._load(identity)['project'])

    def _release(self, identity, ids):
        """A reservation whose job was refused returns to draft, as the page's failed start does."""
        try:
            self._update(identity, lambda project: editor_runtime.runtime().deliver(
                project=project, ids=ids, job=None, settle='draft'))
        except (JobError, OSError) as error:
            logging.getLogger(__name__).warning('Project %s parts stayed reserved (%s)', identity, type(error).__name__)

    def attach(self, row, *, resumed=False):
        """Write the chain id into the reserved parts (the page's assignJob), or mark them queued again on resume."""
        editor, chain = row['editor'], row['chain_id']
        with self.lock:
            prepared = self.prepared.pop(row['id'], None)
        def change(project):
            if resumed:
                return editor_runtime.runtime().deliver(project=project, ids=editor['ids'], job=chain, settle='queued')
            # After a restart the reservation is gone from memory; the parts' own plan stands in for it.
            return editor_runtime.runtime().deliver(project=project, ids=editor['ids'], job=chain,
                                                    assign=prepared or project)
        self._apply(row, change)

    def deliver(self, row, chain):
        """Fill, fail or settle the parts from the chain's blocks; idempotent, so every poll may call it."""
        editor, sources = row['editor'], {}
        state = [[b['id'], b['state'], b['output']] for b in chain['blocks']] + [chain['state']]
        if editor.get('delivered') == state:
            return
        outputs = [b['output'] for b in chain['blocks'] if b['state'] == 'done' and b['output']]
        if outputs:
            found = comfy_editor.sources(self.root, ['generated:' + output for output in outputs])
            sources = {output: found['generated:' + output] for output in outputs}
        if self._apply(row, lambda project: editor_runtime.runtime().deliver(
                project=project, ids=editor['ids'], job=chain['id'], chain=chain, sources=sources)):
            editor['delivered'] = state

    def _apply(self, row, change):
        editor = row['editor']
        try:
            saved = self._update(editor['project'], change)
        except (JobError, OSError) as error:
            # The job goes on; the next poll tries again, and an open page fills the parts the same way.
            editor['problem'] = getattr(error, 'code', 'storage_unavailable')
            return False
        editor.pop('problem', None)
        if saved is not None:
            editor['revision'] = saved['revision']
        return True


# Export ----------------------------------------------------------------------------------------------
# Both are about a clip in the project, so the answer says to look there, not to resume a generation.
EXPORT_REASONS = {'source_missing': 'clip_source_unusable', 'unreadable': 'clip_source_unusable', 'disk_full': 'storage_unavailable'}


def run_export(port, row, *, poll=.25):
    """An export job: one saved revision rendered by the page's export queue, in its turn with the page's exports."""
    from .. import editor_export
    from ..editor_text import MissingGlyphs
    spec = row['spec']
    queue = port.editor_exports()
    if queue is None:
        raise JobError('not_ready', 'The editor export service is not running')
    try:
        view = queue.submit(spec['editor_project'], spec['revision'])
    except editor_export.ExportConflict as error:
        raise JobError('project_changed', 'The project changed since you read it (now revision %d)' % error.revision) from error
    except MissingGlyphs as error:
        raise JobError('missing_glyphs', 'Text in the project uses characters the fonts cannot draw',
                       details=[dict(items=error.items)]) from error
    except FileNotFoundError as error:
        raise JobError('not_found', 'The project does not exist') from error
    except editor_export.ExportError as error:
        raise JobError('busy', 'The editor export queue is full; retry after the page\'s exports finish') from error
    except ValueError as error:
        raise JobError('invalid_request', 'This project cannot be exported: it has no picture or sound') from error
    identity, cancelled = view['id'], False
    row['_phase'] = 'exporting'
    while view['state'] == 'running':
        if row.get('_cancel') and not cancelled:
            view, cancelled = queue.cancel(identity), True
            continue
        row['_detail'] = json.dumps(dict(fraction=round(view['fraction'], 3)))
        time.sleep(poll)
        view = queue.get(identity)
    if view['state'] == 'cancelled':
        raise JobError('cancelled', 'Cancelled')
    if view['state'] != 'done':
        code = EXPORT_REASONS.get(view.get('reason'), 'export_failed')
        raise JobError(code, 'The export failed (%s)' % (view.get('reason') or 'failed'))
    relative = view['path']
    return dict(library_id=library_id(relative), video=library_view(port, relative),
                editor_project=spec['editor_project'], revision=spec['revision'])


# Listen ----------------------------------------------------------------------------------------------
LISTEN_REASONS = {'no_engine': 'no_speech_model', 'language': 'no_speech_model', 'no_sound': 'invalid_request', 'too_long': 'invalid_request',
                  'source_missing': 'clip_source_unusable', 'unreadable': 'clip_source_unusable',
                  'disk_full': 'storage_unavailable'}
LISTEN_MESSAGES = {
    'no_engine': 'No speech model is installed on this machine; write the subtitle lines yourself with the subtitles edit',
    'no_sound': 'The timeline has no sound to listen to',
    'too_long': 'The sound is too long for one listening job; give a shorter range'}


def run_listen(port, row, *, poll=.25):
    """A listening job: the page's subtitle ears (align or transcribe) over one saved revision."""
    spec = row['spec']
    queue = port.editor_ears()
    if queue is None:
        raise JobError('not_ready', 'The subtitle service is not running')
    request = dict(kind=spec['mode'], id=spec['editor_project'], revision=spec['revision'], language=spec['language'])
    request.update({k: spec[k] for k in ('lines', 'range') if k in spec})
    try:
        view = queue.submit(request)
    except FileNotFoundError as error:
        raise JobError('not_found', 'The project does not exist') from error
    except (ValueError, TypeError) as error:
        raise JobError('invalid_request', 'This project cannot be listened to') from error
    except Exception as error:
        if getattr(error, 'reason', None) is None:
            raise
        raise JobError('busy', 'The subtitle queue is full; retry shortly') from error
    identity, cancelled = view['id'], False
    row['_phase'] = 'listening'
    try:
        while view['state'] == 'running':
            if row.get('_cancel') and not cancelled:
                view, cancelled = queue.cancel(identity), True
                continue
            row['_detail'] = json.dumps(dict(fraction=round(view['fraction'], 3)))
            time.sleep(poll)
            view = queue.get(identity)
    except KeyError as error:
        # The page's queue forgot this job (its record cap recycles finished ones) before the result was read.
        raise JobError('listen_failed', 'Listening finished but the subtitle service no longer has the result; retry') from error
    if view['state'] == 'cancelled':
        raise JobError('cancelled', 'Cancelled')
    if view['state'] != 'done':
        reason = view.get('reason') or 'failed'
        raise JobError(LISTEN_REASONS.get(reason, 'listen_failed'),
                       LISTEN_MESSAGES.get(reason) or 'Listening failed (%s)' % reason)
    return dict(editor_project=spec['editor_project'], revision=spec['revision'], **view['result'])
