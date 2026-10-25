"""Agent jobs: one durable record per request, dependencies, GPU turn-taking and status.

Generation uses the studio's durable chain runner. Jobs keeps Agent chains in
FIFO order until the runner is free. Local image and VLM jobs (prompt enhancement and questions) only
start when that queue is empty, and video generation waits while either runs. Jobs
may read other jobs' results (a frame, a clip, an enhanced prompt); such a job
waits until those finish and fails with dependency_failed if one does not.
"""
import asyncio
import atexit
import hashlib
from copy import deepcopy
import json
import logging
import math
import os
import re
import secrets
import shutil
import stat
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from . import spec as specs
from . import schema, errors, assets
from .length import single_frame_limit
from ..system import windows

ACTIVE = ('waiting', 'queued', 'running', 'cancelling')
CAST_AGAIN = 'POST /freevideo/agent/v1/cast/%s/generate to make the reference pictures again.'
DONE = ('succeeded', 'failed', 'cancelled', 'interrupted')
GPU_KINDS = ('vlm', 'enhance', 'image')
IMAGE_ERRORS = errors.IMAGE_ERRORS
IMAGE_WAIT_ERRORS = ('busy', 'busy_video')
IMAGE_WAIT_NOTICES = {
    'video': 'A video is being generated; this picture starts when it finishes. Tell the user that once, then wait.',
    'image': 'Another picture is being generated; this picture starts when it finishes. Tell the user that once, then wait.',
    'gpu': 'The GPU is in use; this picture starts when it is free. Tell the user that once, then wait.',
}


def image_reference(port, relative):
    """The name the image service reads an input picture by.

    It takes files in the input folder itself and its own results as they are, and checks them itself;
    a picture anywhere else under input is copied next to them once, named by its contents."""
    folder, _, leaf = relative.rpartition('/')
    if folder in ('', 'freevideo-image'):
        return relative
    try:
        source = port.input_path(relative)
        with open(source, 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        name = 'agent-image-' + digest[:16] + source.suffix.lower()
        target = port.input_root / name
        if not target.exists():
            part = target.with_name('%s.%s.part' % (name, uuid.uuid4().hex[:8]))
            try:
                shutil.copyfile(source, part)
                os.replace(part, target)
            finally:
                part.unlink(missing_ok=True)
        return name
    except OSError:
        raise JobError('invalid_media', IMAGE_ERRORS['invalid_media']) from None


USER_STOP_NEXT = ('The user stopped this job on the page, and the page already shows it as stopped. Send no message '
                  'about it, and do not retry or resume it unless they ask. Wait for what they say next.')


def user_stop_notice(row):
    """What a job the user stopped on the page tells the agent. A job the agent cancelled, and one that finished
    some other way before the stop took hold, read as before."""
    stopped = row.get('cancelled_by') == 'user' and row.get('status') not in ('succeeded', 'failed', 'interrupted')
    return dict(cancelled_by='user', next=USER_STOP_NEXT) if stopped else {}


class JobError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def view(self):
        return errors.build(self.code, details=self.details.get('details'),
                            owner=self.details.get('owner'),
                            retry_after_seconds=self.details.get('retry_after_seconds'),
                            kind=self.details.get('kind'), report_id=self.details.get('report_id'))


class ManagedByProduction(JobError):
    """A film's child job: only the film may decide whether another version runs."""
    status = 409

    def __init__(self, production):
        super().__init__('managed_by_production', errors.ERRORS['managed_by_production'][2])
        self.production = production

    def view(self):
        return errors.build(self.code, validation=True, details=[],
                            next_action='This take belongs to film %s; reshoot the shot or wait for the user to continue.' % self.production)


def chain_error(code, message, next_action, *, details=None, retryable=False):
    return JobError(code, message, details=details or [], next=next_action, retryable=retryable)


def single_block(spec, seconds=None):
    from ..chain_plan import PlanError, plan
    try:
        return plan(spec['seconds'] if seconds is None else seconds, max_frames=single_frame_limit(spec),
                    continued=bool(spec.get('continue_from')))['single']
    except PlanError:
        return False  # Too long for one generation and it cannot be cut into parts: parts_problems says so.


def continuation_problems(spec, prefix='', *, editor=None):
    """Continuing a video (a second block, or continue_from) runs only on two-pass text generation.

    The engine refuses the continuation block itself, after the first block has already been made.
    """
    if spec['kind'] != 'generate' or spec.get('refine_from'):
        return []
    if not (any(block['after'] for block in editor['blocks']) if editor is not None
            else spec.get('continue_from') or not single_block(spec)):
        return []
    why = ' when continuing a saved video or generating multiple blocks'
    problems = []
    if not spec['two_pass']:
        # The quality name is a contract enum, never caller text.
        if spec.get('sampling_note'):
            # Two-pass was asked for with a first-pass count other than 8: it runs one pass.
            problem = schema.diagnostic(prefix + '/quality',
                                        '"light" or "medium" (two-pass), or sampling.two_pass=true with base_steps=8,' + why, None)
            got = '%d first-pass steps (one pass)' % spec['base_steps']
        else:
            problem = schema.diagnostic(prefix + '/quality', '"light" or "medium" (two-pass), or sampling.two_pass=true,' + why, None)
            got = '"%s" (one pass)' % spec['quality']
        problems.append(dict(problem, got=got, message=problem['message'].replace('received None', 'received ' + got)))
    if spec['preview']:
        problems.append(schema.diagnostic(prefix + '/preview', 'false' + why, True))
    others = [key for key, value in spec['media'].items() if key != 'references' and value]
    pictures = spec['media'].get('references') or []
    if others:
        problems.append(schema.diagnostic(prefix + '/media', 'only references' + why + ' (continue_from carries the previous video)',
                                          None))
    elif len(pictures) > specs.CONTINUED_PICTURES or not all(specs.still_picture(item) for item in pictures):
        problems.append(schema.diagnostic(prefix + '/media/references', 'at most %d pictures%s' % (specs.CONTINUED_PICTURES, why),
                                          len(pictures)))
    return problems


def cut_problems(spec, seconds, prefix='', *, editor=None):
    """A shot cut at or after the end of the video never appears; refuse it before anything is generated."""
    if spec['kind'] != 'generate' or spec.get('refine_from') or spec.get('continue_from') or seconds is None:
        return []
    if any(block['after'] for block in editor['blocks']) if editor is not None else not single_block(spec, seconds):
        return []  # Every block of a continued video reads the same text.
    late = [cut for cut in specs.cut_times(spec.get('prompt')) if cut >= seconds]
    if not late:
        return []
    problem = schema.diagnostic(prefix + '/prompt', 'shot cuts ("[Shot N] At MM:SS.mmm") before the end of the '
                                '%.3f s video' % seconds, None)
    got = 'a cut at %02d:%06.3f' % divmod(late[0], 60)
    return [dict(problem, got=got, message=problem['message'].replace('received None', 'received ' + got))]


def part_prompt_problems(spec, prefix=''):
    """part_prompts gives one description per planned part; the chain backend refuses any other count."""
    if spec['kind'] != 'generate' or not spec.get('part_prompts'):
        return []
    from ..chain_plan import PlanError, plan
    try:
        count = len(plan(spec['seconds'], max_frames=single_frame_limit(spec),
                         continued=bool(spec.get('continue_from')))['blocks'])
    except PlanError:
        return []  # parts_problems names the length.
    given = len(spec['part_prompts'])
    if given == count:
        return []
    allowed = '%d description%s, one for each of the chain_plan.blocks POST /plan gives for %g s' % (
        count, '' if count == 1 else 's', spec['seconds'])
    return [schema.diagnostic(prefix + '/part_prompts', allowed, given)]


def _continued_part_problem(seconds, chain):
    """True when a video that continues another has a part the backend refuses, or is under MIN_PART_SECONDS.

    The backend compares each part's node input frames (overlap included); a part that
    continues is also at least MIN_PART_SECONDS long.
    """
    from ..chain_plan import MIN_PART_FRAMES, MIN_PART_SECONDS
    return seconds < MIN_PART_SECONDS or any(block['frames'] < MIN_PART_FRAMES for block in chain['blocks'])


def length_problem(seconds, continued, prefix='', *, minimum=None, frames=None):
    """Suggest nearby whole-second lengths the same planner, device limit (`frames`) and minimum accept."""
    from ..chain_plan import PlanError, plan
    available = []
    for candidate in range(max(1, int(seconds) - 30), int(seconds) + 31):
        try:
            chain = plan(candidate, max_frames=frames, continued=continued)
        except PlanError:
            continue
        if not continued or not _continued_part_problem(candidate, chain):
            available.append(candidate)
    if minimum is not None and seconds >= minimum:
        minimum = None  # long enough on its own: the length only cannot be cut into parts that long
    nearby = [s for s in available if s < seconds][-2:] + [s for s in available if s > seconds][:2]
    rule = ('at least %d s per continued part' % minimum if minimum is not None
            else 'a length that can be split into supported parts')
    allowed = rule + ('; available lengths: ' + ', '.join('%d s' % s for s in nearby) if nearby
                      else '; this device makes no length near this one at this size')
    problem = schema.diagnostic(prefix + '/seconds', allowed, seconds, nearest=nearby)
    if not nearby:
        problem['next'] = 'Try a smaller width and height.'
    if minimum is not None:
        problem['message'] = ('Each continued part must be at least %d seconds. Make it longer. ' % minimum
                              + problem['message'])
    return problem


def parts_problems(spec, runner, prefix='', *, editor=None, work=None, planned=None):
    """One video continues through a limited number of parts; say so before anything is generated.

    The chain backend refuses the same request when the chain is created, with the same sentence.
    `work` is the saved video continue_from names (output-relative), or None while it cannot be told.
    `planned` is the number of parts an unfinished job it continues will have, when that is known instead.
    """
    if spec['kind'] != 'generate' or spec.get('refine_from'):
        return []
    from ..chain_plan import MIN_PART_SECONDS, PlanError, parts_limit, plan
    from ..chain_runner import ChainShortPartsTooMany, ChainTooLong
    from ..chain_store import reference
    continued, frames = bool(spec.get('continue_from')), single_frame_limit(spec)
    if editor is None:
        # Planning errors must not be swallowed by the source-depth fallback below.
        try:
            chain = plan(spec['seconds'], max_frames=frames, continued=continued)
        except PlanError:
            return [length_problem(spec['seconds'], continued, prefix, frames=frames)]
        if continued and _continued_part_problem(spec['seconds'], chain):
            return [length_problem(spec['seconds'], continued, prefix, minimum=MIN_PART_SECONDS, frames=frames)]
    if runner is None:
        return []
    # A video with a continuing part shorter than full_part_frames has fewer parts, unless every part sees the first frame.
    limit, full = runner.max_parts, runner.full_part_frames
    short_limit = parts_limit(True, spec.get('first_frame_reference') is True, limit)

    def short_blocks(blocks):
        return any(block['frames'] < full for index, block in enumerate(blocks) if index or continued)
    try:
        with runner.lock:
            if editor is not None:
                rows = [dict(id=b['id'], after=reference(b.get('after')), before=reference(b.get('before')),
                             inputs=dict(frames=b['frames'])) for b in editor['blocks']]
                lineage = runner._lineage(dict(id=None, blocks=rows)).values()
                earlier, needed = 0, max(parts for parts, _ in lineage)
                short = max((parts for parts, flag in lineage if flag), default=0)
            else:
                # A source the backend has no record of counts as one part, as it does there.
                earlier, made_short = ((runner._work_lineage(work, ()) if work else (planned or 1, False))
                                       if continued else (0, False))
                needed = earlier + len(chain['blocks'])
                short = needed if made_short or short_blocks(chain['blocks']) else 0
    except (ValueError, KeyError, TypeError):
        return []  # Not a chain the backend accepts; it says why when the chain is created.
    if needed <= limit and short <= short_limit:
        return []
    error = ChainTooLong(needed, limit) if needed > limit else ChainShortPartsTooMany(short, short_limit)
    if editor is not None:
        problem, got = schema.diagnostic(prefix + '/target', 'a range that takes at most %d parts' % error.limit, None), '%d parts' % error.parts
    else:
        # A length the planner refuses does not fit; a part that continues a video is at least MIN_PART_SECONDS.
        def fits(seconds):
            try:
                blocks = plan(seconds, max_frames=frames, continued=continued)['blocks']
            except ValueError:
                return False
            return earlier + len(blocks) <= (short_limit if made_short or short_blocks(blocks) else limit)
        shortest = MIN_PART_SECONDS if continued else 2
        fit = next((s for s in range(int(spec['seconds']), shortest - 1, -1) if fits(s)), None)
        if fit is None:
            problem = schema.diagnostic(prefix + '/continue_from', 'a video of fewer than %d parts' % error.limit, None)
            got = 'a video of %d parts' % earlier
        else:
            problem, got = schema.diagnostic(prefix + '/seconds', 'at most %d' % fit, spec['seconds']), None
    message = problem['message'] if got is None else problem['message'].replace('received None', 'received ' + got)
    return [dict(problem, got=got or problem['got'], message='%s %s' % (error, message))]


def continuation_unavailable():
    return chain_error('continuation_unavailable', 'This backend cannot continue multiple video blocks.',
                       'Use POST /plan for a supported single block (unknown limit: seconds<=15), or enable continuation and resume.')


def library_id(relative):
    parts = Path(relative).parts
    identity = '/'.join(parts[1:3]) if len(parts) == 4 and parts[0] == 'FreeVideo' else None
    if identity and specs.LIBRARY_ID.fullmatch(identity):
        return identity
    raise chain_error('invalid_output', 'The chain output is not a saved library video.',
                      'Read the job again after repairing the saved output, then resume it.')


def library_view(port, relative):
    identity = library_id(relative)
    view = dict(file=relative, url='/freevideo/agent/v1/library/%s/file' % identity)
    return view


def result_view(result):
    """Normalize retained library URLs at presentation time, without changing receipts."""
    value = deepcopy(result)
    if not isinstance(value, dict):
        return value
    blocks = value.get('blocks')
    items = [value] + ([b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else [])
    for item in items:
        video = item.get('video')
        if not isinstance(video, dict):
            continue
        identity = item.get('library_id')
        if not isinstance(identity, str) or not specs.LIBRARY_ID.fullmatch(identity):
            identity = None
            relative = video.get('file')
            if not relative and isinstance(video.get('url'), str) and video['url'].startswith('/view?'):
                query = parse_qs(urlsplit(video['url']).query)
                if query.get('type', ['output'])[0] == 'output':
                    relative = '/'.join((query.get('subfolder', [''])[0], query.get('filename', [''])[0]))
            try:
                identity = library_id(relative) if relative else None
            except (JobError, TypeError, ValueError):
                pass
        if identity:
            base = '/freevideo/agent/v1/library/' + identity
            video['url'] = base + '/file'
            if 'thumbnail' in item:
                item['thumbnail'] = base + '/thumbnail'
        else:
            video.pop('url', None)
            item.pop('thumbnail', None)
    return assets.public(value)


def replace(row, value):
    """Make a shared job row equal to value without ever leaving it empty: views read rows off the lock."""
    row.update(value)
    for key in [k for k in row if k not in value]:
        del row[key]


def now():
    return time.time()


def iso(stamp):
    try:
        return None if stamp is None else time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stamp))
    except (OSError, OverflowError, TypeError, ValueError):
        return None


class _Warmed:
    """A submit's capability source inside Jobs.lock: the studio's answer as it stands, never a wait."""
    def __init__(self, port):
        self.port = port

    def capability_snapshot(self, query):
        table = self.port.capability_snapshot(query)
        # Still being worked out after the wait before the lock: the unknown limit, as a timeout gives.
        return dict(status='unavailable') if table.get('status') == 'computing' else table


class _ReceiptWrites:
    """Windows receipt outbox. Backoff never holds this lock or Jobs.lock.

    A complete, fsynced pending file is also a restart receipt. Revisions keep an
    older pending file from superseding a newer receipt if cleanup was interrupted.
    The writer never takes Jobs.lock, including when shutdown waits for it.
    """
    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.RLock()
        self.pending = {}
        self.revision = 0
        self.worker = None
        self.stopped = threading.Event()
        self.registered = False

    def save(self, row):
        with self.lock:
            self.revision += 1
            value = {k: v for k, v in row.items() if not k.startswith('_')}
            value['_receipt_revision'] = self.revision
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / (row['id'] + '.' + uuid.uuid4().hex + '.pending')
            try:
                with temporary.open('x', encoding='utf-8') as stream:
                    json.dump(value, stream, indent=1)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            previous = self.pending.get(row['id'])
            self.pending[row['id']] = temporary
            if previous is not None:
                self._remove(previous)
            self.retry()

    @staticmethod
    def _remove(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # A stale pending receipt has a lower revision and cannot win on load.

    def _flush(self):
        # The same lock serializes snapshot publication and retries, so a retry
        # cannot overwrite a later cancellation, resume or updated result.
        with self.lock:
            for identity, temporary in list(self.pending.items()):
                try:
                    os.replace(temporary, self.directory / (identity + '.json'))
                except OSError:
                    continue
                del self.pending[identity]
                for stale in self.directory.glob(identity + '.*.pending'):
                    self._remove(stale)
            if not self.pending and self.registered:
                atexit.unregister(self.close)
                self.registered = False

    def retry(self):
        with self.lock:
            self._flush()
            if not self.pending:
                return
            if not self.registered:
                atexit.register(self.close)
                self.registered = True
            if self.worker is None and not self.stopped.is_set():
                self.worker = threading.Thread(target=self._backoff, name='agent-receipts', daemon=True)
                self.worker.start()

    def _backoff(self):
        # Including retry()'s first attempt: eight tries, 2.8 seconds of waiting.
        try:
            for attempt in range(1, 8):
                if self.stopped.wait(.1 * attempt):
                    return
                self._flush()
                with self.lock:
                    if not self.pending:
                        return
            logging.getLogger(__name__).warning('Agent receipts remain pending; will retry on save, tick or shutdown')
        finally:
            with self.lock:
                self.worker = None

    def close(self):
        self.stopped.set()
        with self.lock:
            worker = self.worker
        if worker is not None:
            worker.join()
        for attempt in range(8):
            self._flush()
            with self.lock:
                if not self.pending:
                    break
            if attempt < 7:
                time.sleep(.1 * (attempt + 1))
        with self.lock:
            if self.pending:
                logging.getLogger(__name__).warning('Agent receipts retained for recovery on restart')
            if self.registered:
                atexit.unregister(self.close)
                self.registered = False


class Jobs:
    def __init__(self, port, directory, *, clock=now):
        self.port = port
        self.ticks = 0
        self.directory = Path(directory)
        self.clock = clock
        self.jobs = {}
        self.unreadable = 0
        self.lock = threading.RLock()
        self._receipts = _ReceiptWrites(self.directory) if windows() else None
        self.cpu = threading.Semaphore(1)
        # Optional executor hook: called as parent_allowed(row['parent']) before a
        # child with a parent may start. A False answer records the job as cancelled.
        self.parent_allowed = None
        # Optional: called as session_open(sid) while work for a conversation is committed; raises if it is gone.
        self.session_open = None
        from .projects import Projects
        # The page keeps its projects under ComfyUI's output folder; loading may already poll project jobs.
        self.projects = Projects(port.output_root)
        from .images import Images
        self.images = Images(self, self.directory.parent / 'images')
        from .whitebox import Whitebox
        self.whitebox = Whitebox(self.directory.parent / 'whitebox', port.output_root)
        self.load()
        from .productions import Productions
        self.productions = Productions(self)
        from .cast import Cast
        self.cast = Cast(self)
        films = self.parent_allowed
        # A card's reference pictures are its own children; everything else belongs to a film.
        self.parent_allowed = lambda parent: (self.cast.allows(parent) if 'cast' in parent
                                              else films(parent) if films is not None else True)

    # Records -------------------------------------------------------------------------------------------
    def load(self):
        self.unreadable = 0
        if not self.directory.is_dir():
            return
        # One atomic batch receipt is the commit point. Later per-job records
        # override its initial rows. A crash never exposes a partial submission.
        initial = {}
        unreadable_ids, unreadable_batches = set(), set()

        def readable(row):
            return (isinstance(row, dict) and specs.JOB_ID.fullmatch(str(row.get('id')))
                    and row.get('kind') in ('generate', 'assemble', 'export', 'listen', 'image_edit') + GPU_KINDS
                    and row.get('status') in ACTIVE + DONE and isinstance(row.get('spec'), dict)
                    and type(row.get('created_at')) in (int, float) and math.isfinite(row['created_at'])
                    and (self._receipts is None or (type(row.get('_receipt_revision', 0)) is int
                         and row.get('_receipt_revision', 0) >= 0)))

        for path in sorted(self.directory.glob('b*.json')):
            if not re.fullmatch(r'b[0-9a-f]{32}', path.stem):
                continue
            try:
                batch = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(batch, dict) or not isinstance(batch.get('jobs'), list):
                    raise ValueError
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError):
                unreadable_batches.add(path)
                logging.getLogger(__name__).warning('Skipped an unreadable batch file')
                continue
            bad = False
            for row in batch['jobs']:
                if readable(row):
                    initial[row['id']] = row
                else:
                    bad = True
                    identity = row.get('id') if isinstance(row, dict) else None
                    if isinstance(identity, str) and specs.JOB_ID.fullmatch(identity):
                        unreadable_ids.add(identity)
                    else:
                        # No task id can be recovered: count this receipt once.
                        unreadable_batches.add(path)
            if bad:
                logging.getLogger(__name__).warning('Skipped an unreadable batch file')
        records = list(initial.values())
        for path in sorted(self.directory.glob('j*.json')):
            if not specs.JOB_ID.fullmatch(path.stem):
                continue
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if not readable(row) or row['id'] != path.stem:
                    raise ValueError
                records.append(row)
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                unreadable_ids.add(path.stem)
                logging.getLogger(__name__).warning('Skipped an unreadable job file')
        if self._receipts is not None:
            for path in sorted(self.directory.glob('j*.pending')):
                try:
                    row = json.loads(path.read_text(encoding='utf-8'))
                    if readable(row) and path.name.startswith(str(row.get('id')) + '.'):
                        records.append(row)
                except (OSError, ValueError):
                    continue  # An interrupted staging write is not a receipt.
            latest = {}
            for row in records:
                if not isinstance(row, dict) or not specs.JOB_ID.fullmatch(str(row.get('id'))):
                    continue
                revision = row.get('_receipt_revision', 0)
                if type(revision) is not int or revision < 0:
                    continue
                self._receipts.revision = max(self._receipts.revision, revision)
                previous = latest.get(row['id'])
                if previous is None or revision >= previous.get('_receipt_revision', 0):
                    latest[row['id']] = row
            records = list(latest.values())
        for row in records:
            if readable(row):
                if row.get('status') == 'running' and row['kind'] in ('assemble', 'export', 'listen', 'image_edit') + GPU_KINDS:
                    # This process restarted while the work ran in it; nothing continues it.
                    self.finish(row, 'failed', error=dict(code='interrupted',
                                message='ComfyUI restarted while this job ran; submit it again'), save=False)
                row['_restored'] = True
                if row['kind'] == 'generate' and row['status'] in ACTIVE:
                    # Waiting, queued or running when the process restarted: this process runs it
                    # under the one-pass rule, however it is started again. A finished job is left.
                    note = self.legacy_one_pass_note(row['spec'])
                    if note:
                        row['spec']['sampling_note'] = note
                if row['kind'] == 'generate' and not row['spec'].get('refine_from'):
                    # Before device-aware Agent planning, every accepted job used the unknown limit.
                    row['spec'].setdefault('single_max_frames', None)
                self.jobs[row['id']] = row
        self.unreadable = len(unreadable_batches) + len(unreadable_ids - self.jobs.keys())
        for row in self.jobs.values():
            if (row['kind'] != 'generate' or row['spec'].get('refine_from')
                    or row['status'] in DONE and not row.get('resume_pending')
                    or row.get('cancelled_in_jobs')):
                continue
            try:
                if row.get('chain_id') or row.get('chain_submission'):
                    runner = self.port.chain_runner()
                    if runner is not None and not row.get('chain_id'):
                        with runner.lock:
                            record = next((r for r in runner.records.values()
                                           if r.get('client_id') == 'agent:' + row['id']), None)
                            if record is not None:
                                row['chain_id'] = record['id']
                    if row.get('chain_id'):
                        self.poll_chain(row)
                        continue
                    self.finish(row, 'interrupted', error=chain_error('interrupted',
                        'Generation stopped before its chain receipt could be recovered.',
                        'POST /freevideo/agent/v1/jobs/%s/resume to continue this job.' % row['id'],
                        retryable=True).view(), save=False)
                elif row['status'] in ('queued', 'running', 'cancelling'):
                    self.finish(row, 'interrupted', error=chain_error('interrupted',
                        'This generation has no durable chain receipt.',
                        'POST /freevideo/agent/v1/jobs/%s/resume to create its chain.' % row['id'],
                        retryable=True).view(), save=False)
            except Exception as error:
                self.poll_failure(row, error)
        for row in self.jobs.values():
            if row.get('_restored'):
                row.pop('_restored')
                try:
                    self.save(row)
                except OSError as error:
                    self.log_poll_failure(row, error)

    def save(self, row):
        if self._receipts is not None:
            with self.lock:
                self._receipts.save(row)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (row['id'] + '.json')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump({k: v for k, v in row.items() if not k.startswith('_')}, stream, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def retry_pending(self):
        if self._receipts is not None:
            self._receipts.retry()

    def close(self):
        if self._receipts is not None:
            self._receipts.close()

    def touch(self, row, **changes):
        with self.lock:
            row.update(changes, updated_at=self.clock())
            self.save(row)

    def finish(self, row, status, *, result=None, error=None, save=True):
        if error is not None:
            code = error.get('code', 'internal_error')
            report_id = error.get('report_id')
            if code == 'invalid_json' and row['kind'] in ('vlm', 'enhance'):
                code = 'vlm_invalid_json'
            reason_zh, reason_en = errors.failure_reason(dict(error, code=code)), errors.failure_reason_en(dict(error, code=code))
            if code != error.get('code') or not error.get('request_id') or row['kind'] == 'image':
                error = errors.build(code, details=error.get('details'),
                    request=row.get('request_id'), action_required=code != 'cancelled',
                    kind=row['kind'], stage='terminal', resumable=not row['spec'].get('refine_from'),
                    report_id=error.get('report_id'))
                if row['kind'] == 'image':
                    reason_zh, reason_en = error['error_zh'], error['error_en']
        if error is not None:
            error = dict(error, request_id=row.get('request_id') or error['request_id'], error_zh=reason_zh, error_en=reason_en)
            if row['kind'] != 'image' and isinstance(report_id, str) and re.fullmatch(r'[a-f0-9]{32}', report_id):
                error['report_id'] = report_id
            if error.get('next'):
                error['next'] = errors.recovery(error['code'], kind=row['kind'], stage='terminal',
                    resumable=not row['spec'].get('refine_from')).replace('{id}', row['id'])
                owner = (row.get('parent') or {}).get('production')
                if owner and '/resume' in error['next']:
                    error['next'] = ManagedByProduction(owner).view()['next']
                card = (row.get('parent') or {}).get('cast')
                if card and '/resume' in error['next']:
                    error['next'] = CAST_AGAIN % card
            if row.get('cancelled_by') == 'user' and status == 'cancelled':
                error['next'] = USER_STOP_NEXT
        if row['status'] == status and row.get('error') == error and row.get('finished_at') is not None:
            if result is not None and row.get('result') != result:
                row['result'] = result
                if save:
                    self.save(row)
            elif save:
                self.retry_pending()
            return
        row.update(status=status, finished_at=self.clock(), updated_at=self.clock())
        if row['kind'] == 'generate' and row.get('spec', {}).get('release_gpu_after') is True:
            # The agent runs another model on this card next. Off the lock: stopping the worker takes a moment.
            from ..resident_process import OWNER
            threading.Thread(target=OWNER.release_idle, args=('agent_requested',), name='freevideo-release-gpu', daemon=True).start()
        if result is not None:
            row['result'] = result
        if error is not None:
            row['error'] = error
        else:
            row.pop('error', None)
        if save:
            self.save(row)

    def save_batch(self, rows):
        self.retry_pending()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ('b' + uuid.uuid4().hex + '.json')
        temporary = path.with_suffix('.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(dict(jobs=rows), stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def idempotency(self, scope, value, key=None, client=None):
        alias = value.get('ref')
        if key is not None:
            schema.validate(schema.REF, key)
        if alias is not None:
            schema.validate(schema.REF, alias)
        if key is not None and alias is not None and key != alias:
            raise specs.SpecError(schema.diagnostic('/ref', 'equal to Idempotency-Key when both are supplied', alias))
        key = key or alias
        payload = {k: v for k, v in value.items() if k != 'ref'}
        digest = hashlib.sha256(json.dumps(dict(scope=scope, body=payload), sort_keys=True,
                                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if key is None:
            return None, None
        # Keys belong to the client that sent them: separate Agents (or conversations with their own client
        # name) pick the same natural refs, and one must not be refused for another's earlier request.
        for row in self.jobs.values():
            for receipt in row.get('receipts', []):
                if receipt['key'] == key and receipt.get('client') == client:
                    if receipt['digest'] != digest:
                        raise JobError('idempotency_conflict', 'Key already used')
                    return receipt, [self.jobs[i] for i in receipt['jobs']]
        return dict(key=key, digest=digest, scope=scope, **({'client': client} if client else {})), None

    def submit(self, value, *, client=None, takes=1, parent=None, editor=None):
        payload = dict(value, **({'takes': takes} if takes != 1 else {}))
        return self.submit_request(payload, client=client, parent=parent, editor=editor)[0]

    def warm_capabilities(self, inputs):
        """Before Jobs.lock: each row waits here (length.WAIT_SECONDS at most) for a size asked for the first time."""
        from .routes import substitute
        names = {}
        for raw in inputs:
            item = substitute({k: v for k, v in raw.items() if k not in ('ref', 'takes')}, names)
            if isinstance(raw.get('ref'), str):
                names[raw['ref']] = specs.new_id()  # Stands in for the id this row gets under the lock.
            try:
                specs.normalize(item, port=self.port)
            except Exception:
                pass  # The locked pass reports it, in batch order.

    def submit_request(self, value, *, client=None, key=None, parent=None, editor=None):
        batch = isinstance(value, dict) and 'jobs' in value
        schema.validate(schema.batch_schema(local=True) if batch else schema.job_schema(local=True), value)
        schema.preflight_submission(value)
        if key is not None or value.get('ref') is not None:
            with self.lock:
                _, replay = self.idempotency('submit', value, key, client)
                if replay is not None:
                    return replay, True
        inputs = value['jobs'] if batch else [value]
        self.warm_capabilities(inputs)
        with self.lock:
            # Another submit may have committed the same key while its tables warmed.
            receipt, replay = self.idempotency('submit', value, key, client)
            if replay is not None:
                return replay, True
            names, rows, problems = {}, [], []
            for i, raw in enumerate(inputs):
                prefix = '/jobs/%d' % i if batch else ''
                ref = raw.get('ref') if batch else None
                if ref in names:
                    problems.append(schema.diagnostic(prefix + '/ref', 'a unique batch name', ref))
                item = {k: v for k, v in raw.items() if k not in ('ref', 'takes')}
                # Batch refs refer only to preceding rows; self/forward/cyclic
                # references are rejected before any receipt is written.
                from .routes import substitute
                item = substitute(item, names)
                try:
                    normalized = specs.normalize(item, port=_Warmed(self.port))
                    self.resolve_image_cast(normalized)
                except specs.SpecError as error:
                    problems.extend(schema.prefix_diagnostic(e, prefix) for e in error.errors)
                    continue
                known = set(self.jobs) | {r['id'] for r in rows}
                for dependency in normalized['depends_on']:
                    if dependency not in known:
                        problems.append(schema.diagnostic(prefix + '/after', 'existing jobs or earlier batch refs', dependency))
                problems.extend(continuation_problems(normalized, prefix, editor=editor))
                problems.extend(parts_problems(normalized, self.port.chain_runner(), prefix, editor=editor,
                                               work=self.continued_work(normalized),
                                               planned=self.planned_parts(normalized, rows)))
                problems.extend(cut_problems(normalized, normalized.get('seconds'), prefix, editor=editor))
                problems.extend(part_prompt_problems(normalized, prefix))
                try:
                    self.port.check(normalized)
                except specs.SpecError as error:
                    problems.extend(schema.prefix_diagnostic(e, prefix) for e in error.errors)
                takes = raw.get('takes', 1)
                for take in range(takes):
                    spec = deepcopy(normalized)
                    if take:
                        spec['seed'] = (secrets.randbelow(specs.MAX_SEED + 1) if spec['seed_random'] else
                                        (spec['seed'] + take) % (specs.MAX_SEED + 1))
                    stamp = self.clock()
                    row = dict(id=specs.new_id(), kind=spec['kind'], status='waiting', spec=spec, client=client,
                               take=take + 1 if takes > 1 else None, created_at=stamp, updated_at=stamp,
                               started_at=None, finished_at=None, depends_on=spec['depends_on'],
                               request_id=errors.request_id())
                    rows.append(row)
                    if errors.SESSION.get():
                        row['session'] = errors.SESSION.get()
                    if parent is not None:
                        row['parent'] = deepcopy(parent)
                    if editor is not None:
                        # A project generation (projects.Projects.generate): its own blocks, filled into the project.
                        row['editor'] = deepcopy(editor)
                    if ref and take == 0:
                        names[ref] = row['id']
            if len(rows) > 64:
                problems.append(schema.diagnostic('/jobs', 'at most 64 jobs after expanding takes', len(rows)))
            if problems:
                raise specs.SpecError(problems)
            chains = [r for r in rows if r['kind'] == 'generate' and not r['spec'].get('refine_from')]
            if chains:
                self.require_runner()
                for row in chains:
                    if row.get('editor'):
                        if any(block['after'] for block in row['editor']['blocks']) and not self.continuation_ready():
                            raise continuation_unavailable()
                        continue
                    self.require_continuation(row['spec'])
                    if row['spec'].get('continue_from') and not self.continuation_ready():
                        raise continuation_unavailable()
            if receipt:
                receipt['jobs'] = [r['id'] for r in rows]
                rows[0]['receipts'] = [receipt]
            if self.session_open is not None and rows and rows[0].get('session'):
                self.session_open(rows[0]['session'])  # Deleting a conversation checks for work under this same lock.
            self.save_batch(rows)
            self.jobs.update({r['id']: r for r in rows})
            return rows, False

    def get(self, identity):
        row = self.jobs.get(identity) if isinstance(identity, str) else None
        if row is None:
            raise KeyError(identity)
        if row.get('chain_id') and row['status'] in ACTIVE:
            with self.lock:
                self.poll_chain(row)
        return row

    def rows(self):
        """Every job, as a copy of the list: readers run on worker threads while another may add a batch."""
        return list(self.jobs.values())

    def list(self, *, status=None, project=None, kind=None, limit=50):
        rows = sorted(self.rows(), key=lambda r: r['created_at'], reverse=True)
        rows = [r for r in rows if (not status or r['status'] in status) and (not kind or r['kind'] == kind)
                and (not project or r['spec'].get('project') == project)]
        return rows[:limit]

    def cancel(self, identity, *, by=None):
        row = self.get(identity)
        with self.lock:
            if row['status'] in DONE:
                return row
            if by == 'user':
                # Save before contacting an executor, including jobs still being dispatched.
                self.touch(row, cancelled_by='user')
            if row['kind'] == 'generate' and not row['spec'].get('refine_from'):
                row['_cancel'] = True
                row['cancel_requested'] = True
                if row['status'] == 'waiting' or not row.get('chain_id'):
                    row['resume_pending'] = False
                    row['cancelled_in_jobs'] = True
                    self.finish(row, 'cancelled', error=chain_error('cancelled',
                        'Generation was cancelled before it started.',
                        'POST /freevideo/agent/v1/jobs/%s/resume to continue.' % row['id']).view())
                else:
                    row['resume_pending'] = False
                    self.touch(row, status='cancelling')
                    try:
                        self.chain(row['chain_id'])
                        self.require_runner().cancel(row['chain_id'])
                    except (KeyError, JobError):
                        self.missing_chain(row)
                        return row
                    self.poll_chain(row)
                return row
            if row['status'] == 'waiting' or row.get('_dispatching'):
                row['_cancel'] = True
                if row['status'] == 'waiting' and not row.get('_dispatching'):
                    self.finish(row, 'cancelled', error=dict(code='cancelled', message='Cancelled before it started'))
                return row
            row['_cancel'] = True
        if row['kind'] == 'generate' and row.get('comfy'):
            state = self.port.cancel(row['comfy']['prompt_id'], row['comfy']['node'])
            if state == 'cancelled':
                self.finish(row, 'cancelled', error=dict(code='cancelled', message='Removed from the queue'))
        elif row['kind'] == 'image' and row.get('_image'):
            self.port.image_cancel(row['_image'])
        elif row['kind'] in ('vlm', 'enhance') and row.get('_vlm'):
            self.port.vlm_cancel(row['_vlm'])
        return row

    def continuation_ready(self):
        """Whether this backend can make a video longer than one block."""
        runner = self.port.chain_runner()
        if runner is None:
            return False
        if getattr(runner.host, 'fake', False):
            return True
        from ..comfy_chain import continuation_available
        return continuation_available() and os.environ.get('FREEVIDEO_CHAIN_INDEPENDENT') != '1'

    def require_continuation(self, spec):
        if not single_block(spec) and not self.continuation_ready():
            raise continuation_unavailable()

    def require_runner(self):
        runner = self.port.chain_runner()
        if runner is None:
            raise chain_error('chain_unavailable', 'The continuous generation backend is unavailable.',
                              'Start FreeVideo with the continuous generation backend enabled, then retry.',
                              retryable=True)
        return runner

    def chain(self, identity):
        runner = self.require_runner()
        try:
            return runner.get(identity)
        except KeyError:
            return runner.load(identity)

    def missing_chain(self, row):
        row['resume_pending'] = False
        self.finish(row, 'interrupted', error=chain_error('chain_missing',
            'The chain receipt for this job is unavailable.',
            'Restore its chain receipt, then POST /freevideo/agent/v1/jobs/%s/resume.' % row['id'],
            retryable=True).view())

    def log_poll_failure(self, row, error):
        # Exception text/tracebacks can contain prompts and absolute paths.
        logging.getLogger(__name__).warning('Agent job %s could not be polled (%s)', row['id'], type(error).__name__)

    def invalid_output(self, row):
        return chain_error('invalid_output', 'The chain finished but its saved output is invalid or unavailable.',
            'Restore the saved output, then POST /freevideo/agent/v1/jobs/%s/resume.' % row['id'], retryable=True).view()

    def poll_failure(self, row, error):
        self.log_poll_failure(row, error)
        if not row.get('chain_id'):
            # Not a chain fault. One backend hiccup must not end the job; a lasting one must not hold the queue.
            count, last = row.get('_poll_failures', (0, None))
            count = count + 1 if last == self.ticks - 1 else 1
            row['_poll_failures'] = count, self.ticks
            if count >= 5 and row['status'] in ACTIVE:
                self.finish(row, 'failed', error=chain_error('internal_error',
                    'This job stopped after repeated internal errors.',
                    'POST /freevideo/agent/v1/jobs to submit it again.', retryable=True).view())
            return
        try:
            chain = self.chain(row['chain_id'])
            terminal = chain['state'] not in ('queued', 'running', 'cancelling') and chain['output_state'] != 'joining'
        except Exception:
            # A broken view can still have a valid terminal state in the runner.
            try:
                runner = self.require_runner()
                with runner.lock:
                    record = runner.records.get(row.get('chain_id'))
                    terminal = record is not None and record.get('state') not in ('queued', 'running', 'cancelling') \
                        and record.get('output_state') != 'joining'
            except Exception:
                record, terminal = None, False
            if record is None and row['status'] in ACTIVE:
                self.missing_chain(row)
                return
        if terminal:
            row['resume_pending'] = False
            try:
                self.finish(row, 'failed', error=self.invalid_output(row))
            except OSError as failure:
                self.log_poll_failure(row, failure)

    def runner_busy(self):
        runner = self.port.chain_runner()
        if runner is None:
            return False
        with runner.lock:
            return any(r['state'] in ('queued', 'running', 'cancelling') or r['output_state'] == 'joining'
                       for r in runner.records.values())

    def resume(self, identity, *, key=None, value=None, replay_result=False, client=None):
        value = value or {}
        schema.validate(schema.contracts()['POST', '/jobs/{id}/resume']['body'], value)
        with self.lock:
            # Checked before any receipt replay: a film's child is never resumed on its own.
            owner = ((self.jobs.get(identity) or {}).get('parent') or {}).get('production')
            if owner:
                raise ManagedByProduction(owner)
            card = ((self.jobs.get(identity) or {}).get('parent') or {}).get('cast')
            if card:
                raise chain_error('not_resumable', 'Reference pictures are not resumed on their own.', CAST_AGAIN % card)
            receipt, replay = self.idempotency('resume:' + identity, value, key, client)
            if replay is not None:
                return (replay[0], True) if replay_result else replay[0]
            try:
                row = self.jobs[identity]
            except KeyError:
                raise chain_error('not_found', 'No job has this ID.',
                                  'GET /freevideo/agent/v1/jobs to find an existing job ID.') from None
            if (row['kind'] != 'generate' or row['spec'].get('refine_from')
                    or row['status'] not in ('failed', 'interrupted', 'cancelled')):
                raise chain_error('not_resumable', 'Only failed, interrupted or cancelled generation jobs can resume.',
                    'GET /freevideo/agent/v1/jobs/%s; wait for active jobs, or POST /jobs for a new generation.' % identity,
                    details=[dict(field='/status', allowed=['failed', 'interrupted', 'cancelled'], got=row['status'])])
            runner = self.require_runner()
            if row.get('chain_id'):
                try:
                    chain = self.chain(row['chain_id'])
                except KeyError:
                    raise chain_error('chain_missing', 'The saved chain receipt is unavailable.',
                                      'Restore the chain receipt and retry resume, or submit a new job.') from None
                for block in chain['blocks']:
                    if block['state'] == 'done':
                        try:
                            self.saved_video(block['output'])
                        except (JobError, OSError, ValueError, TypeError):
                            raise chain_error('source_missing', 'A completed block is no longer in the library.',
                                              'Restore the completed block before resuming, or submit a new job.') from None
                if (chain['state'] == 'done' and chain['output_state'] == 'done'
                        and (row.get('error') or {}).get('code') == 'invalid_output'):
                    # Retry validation of repaired artifacts without replaying completed work.
                    candidate = deepcopy(row)
                    self.poll_chain(candidate)
                    if receipt:
                        candidate.setdefault('receipts', []).append(dict(receipt, jobs=[identity]))
                    self.save(candidate)
                    replace(row, candidate)
                    return (row, False) if replay_result else row
            original = row
            row = deepcopy(row)
            if receipt:
                row.setdefault('receipts', []).append(dict(receipt, jobs=[identity]))
            row.pop('error', None)
            row.pop('_cancel', None)
            row.pop('cancelled_by', None)
            self.touch(row, status='waiting', resume_pending=bool(row.get('chain_id')), cancel_requested=False,
                       cancelled_in_jobs=False,
                       chain_submission='linked' if row.get('chain_id') else None,
                       finished_at=None, attempt=row.get('attempt', 1) + 1)
            replace(original, row)
            return (original, False) if replay_result else original

    def queue_view(self):
        """Describe ownership/order without exposing studio workflows or prompts."""
        runner = self.port.chain_runner()
        entries, chains, prompts = [], [], {}
        if runner is not None:
            with runner.lock:
                order = ([runner.active] if runner.active is not None else []) + runner.queue
                order += [r['id'] for r in runner.records.values() if r['id'] not in order]
                records = [runner.records[identity] for identity in dict.fromkeys(order)]
                for record in records:
                    if record['state'] not in ('queued', 'running', 'cancelling'):
                        continue
                    client = record.get('client_id') or ''
                    entry = dict(owner='agent' if client.startswith('agent:') else 'ui',
                        state=record['state'], **({'job_id': client[6:]} if client.startswith('agent:') else {}))
                    chains.append((record['id'], entry))
                    for block in record['blocks']:
                        if block['prompt_id'] and block['state'] in ('queued', 'running'):
                            prompts[block['prompt_id']] = (record['id'], entry)
        for row in self.active():
            if row.get('comfy'):
                prompts[row['comfy']['prompt_id']] = (row['id'], dict(owner='agent', job_id=row['id'], state=row['status']))
        running, pending = self.port.snapshot()
        seen = set()
        for rows, state in ((running, 'running'), (pending, 'queued')):
            for prompt in rows:
                if prompt in prompts:
                    identity, entry = prompts[prompt]
                    if identity not in seen:
                        entries.append(dict(entry, state=state))
                        seen.add(identity)
                else:
                    entries.append(dict(owner='ui', state=state))
        # A joining chain still holds the generation turn without a ComfyUI prompt.
        entries = [entry for identity, entry in chains if identity not in seen] + entries
        known = {e.get('job_id') for e in entries}
        entries += [dict(owner='agent', job_id=r['id'], state=r['status'])
                    for r in sorted(self.active(), key=lambda r: r['created_at'])
                    if r['kind'] in GPU_KINDS + ('generate',) and r['id'] not in known]
        ahead = {e['job_id']: dict(count=i, agent=sum(x['owner'] == 'agent' for x in entries[:i]),
                                 ui=sum(x['owner'] == 'ui' for x in entries[:i]))
                 for i, e in enumerate(entries) if e.get('job_id')}
        return dict(entries=entries, ahead=ahead)

    # Views ---------------------------------------------------------------------------------------------
    def view(self, row, *, detail=True):
        if row.get('chain_id') and row['status'] in ACTIVE:
            self.poll_chain(row)
        progress = self.progress(row) if row['status'] in ('queued', 'running', 'cancelling') else None
        spec = row['spec']
        result = dict(id=row['id'], kind=row['kind'], status=row['status'], label=spec.get('label'),
                      project=spec.get('project'), take=row.get('take'), client=row.get('client'),
                      created_at=iso(row['created_at']), started_at=iso(row.get('started_at')),
                      finished_at=iso(row.get('finished_at')), depends_on=row.get('depends_on', []))
        result['poll_after_ms'] = 500 if row['status'] in ACTIVE else None
        if row.get('parent'):
            result['parent'] = deepcopy(row['parent'])
        if row.get('chain_id'):
            result['chain_id'] = row['chain_id']
        if row['kind'] == 'generate':
            result.update(cancel_requested=row.get('cancel_requested', False), attempt=row.get('attempt', 1))
            note = spec.get('sampling_note')
            if (note is None and row['status'] not in DONE and spec.get('two_pass') and not spec.get('preview')
                    and not spec.get('refine_from')):
                # A job stored before the one-pass rule and not yet run: the engine applies it when it runs.
                # A finished one keeps what it did, which a stored request cannot tell.
                from ..two_pass import one_pass_note
                note = one_pass_note(spec.get('base_steps', 8))
            if note:
                result['notice'] = note
        if row['status'] == 'waiting':
            pending = [d for d in row.get('depends_on', ()) if self.jobs.get(d, {}).get('status') not in DONE]
            result['waiting_for'] = pending or row.get('waiting_for') or (['gpu'] if row['kind'] in GPU_KINDS + ('generate',) else [])
            if row['kind'] == 'image' and not pending and row.get('waiting_for'):
                # Only once it was really held back; a picture just submitted has not been looked at yet.
                result['notice'] = IMAGE_WAIT_NOTICES[row['waiting_for'][0]]
        if row['status'] in ('queued', 'running', 'cancelling'):
            result['progress'] = progress
            if row.get('chain_id'):
                result['last_heartbeat_at'] = result['progress'].pop('last_heartbeat_at', None)
        if row.get('result') is not None:
            result['result'] = result_view(row['result']) if row['kind'] == 'generate' else assets.public(row['result'])
        if row.get('error') is not None:
            result['error'] = row['error'] if row['error'].get('request_id') else errors.build(
                row['error'].get('code'), details=row['error'].get('details'), request=row.get('request_id'),
                kind=row['kind'], stage='terminal', resumable=not row['spec'].get('refine_from'))
            if row['kind'] == 'image':
                result['error'] = errors.build(result['error']['code'], kind='image', stage='terminal',
                    details=result['error'].get('details'), request=row.get('request_id'),
                    action_required=result['error']['code'] != 'cancelled', report_id=row['error'].get('report_id'))
            else:
                result['error'] = dict(result['error'], **errors.reasons(row['error']))
                report_id = row['error'].get('report_id')
                if isinstance(report_id, str) and re.fullmatch(r'[a-f0-9]{32}', report_id):
                    result['error']['report_id'] = report_id
            if result['error'].get('next'):
                result['error'] = dict(result['error'], next=errors.recovery(result['error']['code'], kind=row['kind'],
                    stage='terminal', resumable=not row['spec'].get('refine_from')).replace('{id}', row['id']))
        notice = user_stop_notice(row)
        result.update(notice)
        if notice and result.get('error'):
            result['error'] = dict(result['error'], next=USER_STOP_NEXT)
        if detail:
            result['spec'] = {k: v for k, v in spec.items() if k not in ('depends_on',)}
            if row.get('comfy'):
                result['comfy'] = row['comfy']
        return result

    def progress(self, row):
        if row.get('chain_id'):
            try:
                chain = self.chain(row['chain_id'])
            except Exception as error:
                if not isinstance(error, (KeyError, JobError)):
                    self.log_poll_failure(row, error)
                if row['status'] in ACTIVE:
                    self.missing_chain(row)
                return dict(phase='interrupted', last_heartbeat_at=None)
            try:
                message = chain.get('progress') or {}
                current = next((b for b in chain['blocks'] if b['id'] == chain.get('current')), None)
                value = dict(phase='joining' if chain['output_state'] == 'joining' else
                             message.get('timing_phase') or message.get('phase') or chain['state'],
                             block=dict(index=current['index'] + 1 if current else min(chain['done'] + 1, chain['count']),
                                        total=chain['count'], done=chain['done']),
                             last_heartbeat_at=iso(chain.get('last_heartbeat_at')))
                if value['phase'] == 'sampling' and message.get('done') is not None:
                    value['step'] = dict(done=message['done'], total=message.get('total'), **({'unit': message['unit']} if message.get('unit') else {}))
                for key in ('label', 'detail', 'fraction', 'elapsed_seconds', 'remaining_seconds'):
                    if message.get(key) is not None and (key != 'remaining_seconds' or message[key] > 0):
                        value[key] = message[key]
                return value
            except Exception as error:
                self.poll_failure(row, error)
                return dict(phase=row['status'], last_heartbeat_at=None)
        if row['kind'] == 'generate' and row.get('comfy'):
            position = self.port.position(row['comfy']['prompt_id'])
            if position:
                return dict(phase='queued', label='Waiting in the ComfyUI queue', queue_position=position)
            message = self.port.progress(row['comfy']['node'])
            if not message:
                return dict(phase='starting', label='Starting')
            overall = message.get('overall') or {}
            value = dict(phase=message.get('timing_phase') or message.get('phase') or 'running',
                         label=message.get('label'), detail=message.get('detail'))
            if value['phase'] == 'sampling' and message.get('done') is not None:
                value['step'] = dict(done=message['done'], total=message.get('total'), **({'unit': message['unit']} if message.get('unit') else {}))
            for key in ('fraction', 'elapsed_seconds', 'remaining_seconds', 'estimated'):
                if overall.get(key) is not None:
                    value[key] = overall[key]
            for key in ('retry', 'warning'):
                if message.get(key):
                    value[key] = message[key]
            return {k: v for k, v in value.items() if v is not None}
        return dict(phase=row.get('_phase') or ('running' if row['status'] == 'running' else 'queued'),
                    **({'detail': row['_detail']} if row.get('_detail') else {}))

    def estimate(self, row):
        """The engine's own timing of the current phase, for a page that moves its bar between two messages
        the way the creation panel does. Only keys the engine sent."""
        if row['status'] not in ('queued', 'running', 'cancelling'):
            return None
        try:
            if row.get('chain_id'):
                # A video in several parts has one bar over all of them; the engine times each part by itself.
                chain = self.chain(row['chain_id'])
                message = self.port.progress(chain['node']) or {} if chain['count'] == 1 and chain.get('node') else {}
            elif row['kind'] == 'generate' and row.get('comfy'):
                message = self.port.progress(row['comfy']['node']) or {}
            else:
                return None
        except Exception:
            return None
        value = {key: message[key] for key in ('age_seconds', 'phase', 'done', 'total', 'display_fraction',
                                               'step_elapsed_seconds', 'estimated_step_seconds') if message.get(key) is not None}
        overall = message.get('overall') if isinstance(message.get('overall'), dict) else {}
        overall = {key: overall[key] for key in ('fraction', 'estimated', 'status', 'phase_start_fraction', 'phase_weight',
                                                 'phase_seconds', 'phase_elapsed_seconds', 'sampling_time_weighted', 'remaining_source')
                   if overall.get(key) is not None}
        if overall:
            value['overall'] = overall
        return value or None

    # Scheduling ----------------------------------------------------------------------------------------
    def active(self):
        return [r for r in self.rows() if r['status'] in ACTIVE]

    def stop_session(self, session):
        """The user stopped a work session: cancel its agent jobs and pause its films. Returns what was stopped."""
        stopped = dict(jobs=[], dramas=[])
        for row in [r for r in self.active() if r.get('session') == session]:
            try:
                self.cancel(row['id'])
                stopped['jobs'].append(row['id'])
            except (KeyError, JobError):
                pass
        stopped['dramas'] = self.productions.pause_session(session)
        return stopped

    async def tick(self):
        rows = []
        def advance():
            self.retry_pending()
            with self.lock:
                rows.extend(self._tick())
                self.productions.tick()
            return self.cast.pending()
        work = asyncio.create_task(asyncio.to_thread(advance))
        try:
            try:
                pending = await asyncio.shield(work)
            except asyncio.CancelledError:
                # The worker still owns any reservations it made. Let it finish before
                # handing them back to the loop, even if this HTTP caller disconnected.
                await asyncio.gather(work, return_exceptions=True)
                raise
        finally:
            for row in rows:
                asyncio.ensure_future(self.dispatch(row))
        # Picking frames for a card's reference pictures decodes video: done off the lock. A card that
        # cannot be landed records it on the card; it never fails the request that ticked.
        if pending:
            await asyncio.to_thread(self.cast.tick)

    def _tick(self):
        """Advance every unfinished job once, oldest first; safe to call often.

        The GPU takes turns: local image and VLM jobs start only with no video ahead
        of them or in ComfyUI's queue, and a video never overtakes an earlier GPU job.
        """
        dispatches = []
        generation_ahead = vlm_ahead = False
        self.ticks += 1
        for row in sorted(self.active(), key=lambda r: r['created_at']):
            try:
                kind, status = row['kind'], row['status']
                if status in ('queued', 'running', 'cancelling') or row.get('_dispatching'):
                    if kind == 'generate' and row.get('chain_id'):
                        self.poll_chain(row)
                    elif kind == 'generate' and row.get('comfy'):
                        self.poll_generation(row)
                    elif kind == 'image' and row.get('_image'):
                        self.poll_image(row)
                    elif kind in ('vlm', 'enhance') and row.get('_vlm'):
                        self.poll_vlm(row)
                    if row['status'] in ACTIVE:
                        generation_ahead |= kind == 'generate'
                        vlm_ahead |= kind in GPU_KINDS
                    continue
                if self.parent_refused(row):
                    continue
                if self.ready(row) != 'ready':
                    continue
                if kind == 'generate':
                    if generation_ahead or vlm_ahead or self.port.vlm_busy() or self.port.image_busy():
                        generation_ahead = True
                        continue
                    generation_ahead = True
                    if not row['spec'].get('refine_from') and (self.runner_busy() or self.port.queue_busy()):
                        continue
                elif kind in GPU_KINDS:
                    chain_busy = self.runner_busy()
                    video_busy = generation_ahead or chain_busy or self.port.queue_busy()
                    image_busy = self.port.image_busy()
                    busy = video_busy or vlm_ahead or image_busy or self.port.vlm_busy()
                    vlm_ahead = True
                    if busy:
                        if kind == 'image':
                            code = 'busy_video' if video_busy else 'busy'
                            if not self.wait_image(row, code,
                                    waiting_for='video' if video_busy else 'image' if image_busy else 'gpu'):
                                self.finish(row, 'failed', error=dict(code=code))
                        continue
                row['_dispatching'] = True
                dispatches.append(row)
            except Exception as error:
                self.poll_failure(row, error)
                if row['status'] in ACTIVE:
                    generation_ahead |= row['kind'] == 'generate'
                    vlm_ahead |= row['kind'] in GPU_KINDS
        return dispatches

    def parent_refused(self, row):
        """Ask the owning executor once whether this job may still run; if not, record it as cancelled."""
        gate = self.parent_allowed
        if gate is None or not row.get('parent'):
            return False
        if gate(row['parent']):
            return False
        self.finish(row, 'cancelled', error=JobError('cancelled', 'Cancelled before it started').view())
        return True

    def ready(self, row):
        for dependency in row.get('depends_on', ()):
            other = self.jobs.get(dependency)
            status = other['status'] if other else 'failed'
            if status in ACTIVE:
                return 'dependency'
            if status != 'succeeded':
                self.finish(row, 'failed', error=dict(code='dependency_failed', job=dependency,
                            message='Job %s did not succeed (%s), so this job cannot run' % (dependency, status)))
                return 'failed'
        return 'ready'

    async def dispatch(self, row):
        def check():
            if row.get('_cancel'):
                raise JobError('cancelled', 'Cancelled before it started')
            with self.lock:
                # The scheduler reserved this row earlier (or the process restarted
                # since): ask the owner again immediately before any work starts.
                if self.parent_allowed is not None and row.get('parent') and not self.parent_allowed(row['parent']):
                    raise JobError('cancelled', 'Cancelled before it started')

        def run():
            self.touch(row, status='running', started_at=self.clock())
            if row['kind'] == 'export':
                from .projects import run_export
                result = run_export(self.port, row)
            elif row['kind'] == 'listen':
                from .projects import run_listen
                result = run_listen(self.port, row)
            elif row['kind'] == 'image_edit':
                from .images import run_render
                result = run_render(self, row)
            else:
                result = self.run_assemble(row)
            return result

        failure = None
        succeeded = False
        result = None
        try:
            await asyncio.to_thread(check)
            if row['kind'] == 'generate':
                await self.start_generation(row)
            elif row['kind'] == 'image':
                await asyncio.to_thread(self.start_image, row)
            elif row['kind'] in ('vlm', 'enhance'):
                await asyncio.to_thread(self.start_vlm, row)
            else:
                result = await asyncio.to_thread(run)
                succeeded = True
        except JobError as error:
            if row['kind'] != 'image' or not self.wait_image(row, error.code):
                failure = ('cancelled' if error.code == 'cancelled' else 'failed', error.view())
        except specs.SpecError as error:
            failure = ('failed', dict(code='invalid_request', message=str(error), details=error.errors))
        except Exception as error:  # noqa: BLE001 - a job must always end with a reason
            failure = ('failed', dict(code='internal_error', message='%s: %s' % (
                type(error).__name__, str(error)[:2000])))
        finally:
            def settle():
                with self.lock:
                    try:
                        if failure is not None:
                            status, reason = failure
                            self.finish(row, status, error=reason)
                        elif succeeded:
                            self.finish(row, 'succeeded', result=result)
                    finally:
                        row.pop('_dispatching', None)
            await asyncio.to_thread(settle)

    # Inputs --------------------------------------------------------------------------------------------
    def job_video(self, identity):
        other = self.get(identity)
        if other['status'] != 'succeeded' or not (other.get('result') or {}).get('video'):
            raise JobError('no_video', 'Job %s has no finished video' % identity, job=identity)
        return self.port.output_path(other['result']['video']['file'])

    def source_video(self, item):
        if 'from_job' in item:
            return self.job_video(item['from_job'])
        if 'library' in item:
            return self.port.library_video(item['library'])
        return self.port.input_path(item['file'])

    def continued_work(self, spec):
        """The saved video a request continues, as the chain backend names it; None while it cannot be told."""
        try:
            return self.port.output_relative(self.source_video(spec['continue_from'])) if spec.get('continue_from') else None
        except Exception:
            return None

    def planned_parts(self, spec, rows=()):
        """Parts the unfinished job a request continues will have, from that job's own plan; None when not known."""
        from ..chain_plan import plan
        runner, total, seen = self.port.chain_runner(), 0, set()
        try:
            while True:
                identity = (spec.get('continue_from') or {}).get('from_job')
                source = next((r for r in rows if r['id'] == identity), None) or self.jobs.get(identity)
                if source is None or identity in seen or source['status'] not in ACTIVE:
                    return None
                seen.add(identity)
                spec = source['spec']
                if spec['kind'] != 'generate' or spec.get('refine_from') or source.get('editor'):
                    return None
                total += len(plan(spec['seconds'], max_frames=single_frame_limit(spec),
                                  continued=bool(spec.get('continue_from')))['blocks'])
                if not spec.get('continue_from'):
                    return total
                work = self.continued_work(spec)
                if work is not None:
                    with runner.lock:
                        return total + runner._work_parts(work, ())
                if 'from_job' not in spec['continue_from']:
                    return total + 1  # A source the backend has no record of counts as one part there.
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def media_file(self, row, item, name, *, target=None):
        """Resolve one media item to a file relative to ComfyUI input (frames, imports, edits)."""
        folder = 'freevideo/agent/' + row['id']
        if 'frame' in item:
            video = self.source_video(item)
            out = self.port.input_path(folder + '/' + name + '.png', create=True)
            self.port.extract_frame(video, out, item['frame'])
            relative = folder + '/' + name + '.png'
        elif 'from_job' in item or 'library' in item:
            video = self.source_video(item)
            relative = folder + '/' + name + video.suffix
            self.port.import_file(video, self.port.input_path(relative, create=True))
        elif 'path' in item:
            source = Path(item['path']).expanduser()
            if not source.is_absolute() or not source.is_file():
                raise JobError('invalid_media', 'Local file not found: %s' % item['path'])
            relative = folder + '/' + name + source.suffix.lower()
            self.port.import_file(source, self.port.input_path(relative, create=True))
        else:
            relative = item['file']
            if not self.port.input_path(relative).is_file():
                raise JobError('invalid_media', 'Upload the media file first: %s' % relative)
        if item.get('edit') is not None:
            edited = folder + '/' + name + '-edit.png'
            self.port.edit(relative, item['edit'], self.port.input_path(edited, create=True), target=target)
            relative = edited
        return relative

    @staticmethod
    def legacy_one_pass_note(spec):
        """The note for an unfinished two-pass job stored before the one-pass rule, else None."""
        base = spec.get('base_steps', 8)
        if (spec.get('sampling_note') is None and spec.get('two_pass') is True and not spec.get('preview')
                and not spec.get('refine_from') and type(base) is int and 1 <= base <= 32 and base != 8):
            from ..two_pass import one_pass_note
            return one_pass_note(base)
        return None  # a damaged stored value gets no note; the row is judged where it is used

    async def start_generation(self, row):
        def prepare():
            spec = row['spec']
            note = self.legacy_one_pass_note(spec)
            if note:
                # A job stored before the one-pass rule starts under it now, on a first start
                # and on a resume alike: keep what it runs as, so the note outlasts the run and
                # a reload (a stored request alone cannot tell, and a finished old job must
                # not gain a note).
                with self.lock:
                    spec['sampling_note'] = note
                self.touch(row)
            if row.get('resume_pending'):
                self.start_chain(row, resume=True)
                return
            node = 'fv' + row['id'][1:]
            if spec.get('refine_from'):
                ref = spec['refine_from']
                video = self.job_video(ref['from_job']) if 'from_job' in ref else self.port.library_video(ref['library'])
                graph = specs.upscale_prompt(node, self.port.output_relative(video))
            else:
                prompt = spec['prompt']
                if isinstance(prompt, dict):
                    other = self.get(prompt['from_job'])
                    prompt = (other.get('result') or {}).get('text')
                    if other['kind'] != 'enhance' or not prompt:
                        raise JobError('dependency_failed', 'Job %s produced no prompt text' % spec['prompt']['from_job'])
                target = (spec['width'], spec['height'])
                def resolve():
                    assets = []
                    for role in ('first', 'last'):
                        if role in spec['media']:
                            assets.append(dict(file=self.media_file(row, spec['media'][role], role, target=target),
                                               role=role))
                    for i, item in enumerate(spec['media'].get('references', []), 1):
                        asset = dict(file=self.media_file(row, item, 'reference-%02d' % i, target=target), role='reference')
                        if 'early_steps' in item:
                            asset['early_steps'] = item['early_steps']
                        assets.append(asset)
                    return assets
                assets = resolve()
                graph = specs.comfy_prompt(spec, node, prompt=prompt, assets=assets)
                prompt = graph[node]['inputs']['text']
                with self.lock:
                    row['inputs'] = dict(media=assets, prompt_chars=len(prompt))
            if row.get('_cancel'):
                raise JobError('cancelled', 'Cancelled before it started')
            extra = {'extra_pnginfo': {'freevideo_agent': dict(job=row['id'], label=spec.get('label'),
                     project=spec.get('project'), client=row.get('client'), api_version=1)}}
            if spec.get('refine_from'):
                return graph, extra, node
            else:
                payload = dict(node=node, prompt=graph, seconds=spec['seconds'],
                               max_frames=single_frame_limit(spec),
                               client_id='agent:' + row['id'], extra_data=extra)
                if row.get('editor'):
                    payload.pop('seconds')
                    payload['blocks'] = deepcopy(row['editor']['blocks'])
                elif spec.get('continue_from'):
                    # Planned as a continuation, so the new video is as long as the request says.
                    payload.update(after=dict(work=self.port.output_relative(self.source_video(spec['continue_from']))),
                                   text=prompt, seed=spec['seed'])
                if spec.get('part_prompts') and 'seconds' in payload:
                    # Each part plays its own description, with the same LoRA trigger words the whole prompt gets.
                    from ..lora_triggers import apply_triggers
                    payload['texts'] = [apply_triggers(text, spec.get('loras', [])) for text in spec['part_prompts']]
                self.start_chain(row, payload=payload)
        work = asyncio.create_task(asyncio.to_thread(prepare))
        try:
            submission = await asyncio.shield(work)
        except asyncio.CancelledError:
            # Dispatch cleanup must not clear the reservation while preparation is
            # still importing media or creating the durable chain on another thread.
            await asyncio.gather(work, return_exceptions=True)
            raise
        if submission is not None:
            graph, extra, node = submission
            prompt_id = await self.port.submit(graph, extra)
            def queued():
                self.touch(row, status='queued', comfy=dict(prompt_id=prompt_id, node=node), queued_at=self.clock())
            await asyncio.to_thread(queued)

    def start_chain(self, row, *, payload=None, resume=False):
        runner = self.require_runner()
        # UI create/resume uses this same lock. There is no check/submit gap.
        with self.lock, runner.lock:
            if row.get('_cancel') or row['status'] == 'cancelled':
                return
            if (any(r['state'] in ('queued', 'running', 'cancelling') or r['output_state'] == 'joining'
                    for r in runner.records.values()) or self.port.queue_busy() or self.port.vlm_busy() or self.port.image_busy()):
                return
            # Submit and plan refuse this up front; the backend can still change before a queued or resumed job starts.
            if (any(b['state'] != 'done' and (b['after'] or b['before'])
                    for b in runner.records[row['chain_id']]['blocks']) if resume
                    else any(b['after'] for b in payload['blocks']) if row.get('editor')
                    else bool(row['spec'].get('continue_from')) or not single_block(row['spec'])) and not self.continuation_ready():
                raise continuation_unavailable()
            self.touch(row, chain_submission='creating', status='queued', queued_at=self.clock())
            from ..chain_plan import PlanError
            from ..chain_runner import ChainBusy, ChainPartTooShort, ChainTooLong
            try:
                if resume:
                    chain = runner.resume(row['chain_id'], client_id='agent:' + row['id'])
                else:
                    chain = runner.create(payload)
            except ChainBusy:
                if resume:
                    self.touch(row, status='waiting')
                    return
                raise
            except ChainTooLong as error:
                # Submit and plan refuse this up front; a source that was still being made can only be counted here.
                row['resume_pending'] = False
                self.finish(row, 'failed', error=errors.build(error.code, validation=True, message=str(error)))
                return
            except (PlanError, ChainPartTooShort) as error:
                row['resume_pending'] = False
                problem = length_problem(row['spec']['seconds'], bool(row['spec'].get('continue_from')),
                                         minimum=error.limit if isinstance(error, ChainPartTooShort) else None,
                                         frames=single_frame_limit(row['spec']))
                self.finish(row, 'failed', error=errors.validation([problem]))
                return
            except Exception:
                row['resume_pending'] = False
                if resume:
                    self.finish(row, 'interrupted', error=chain_error('interrupted',
                        'The backend did not complete this resume request.',
                        'POST /freevideo/agent/v1/jobs/%s/resume to retry.' % row['id'], retryable=True).view())
                else:
                    self.finish(row, 'failed', error=chain_error('chain_rejected',
                        'The continuous generation backend did not accept this request.',
                        'Change the generation parameters, then POST /freevideo/agent/v1/jobs to submit a new job.').view())
                return
            self.touch(row, chain_id=chain['id'], chain_submission='linked', resume_pending=False)
            if row.get('editor'):
                self.projects.attach(row, resumed=resume)
            self.poll_chain(row)

    def poll_chain(self, row):
        with self.lock:
            if row.get('cancelled_in_jobs') or row.get('resume_pending') and row['status'] == 'waiting':
                return
            try:
                if row.get('resume_pending'):
                    try:
                        chain = self.chain(row['chain_id'])
                    except (KeyError, JobError):
                        self.missing_chain(row)
                        return
                    row['resume_pending'] = False
                    if chain['state'] in ('failed', 'cancelled', 'interrupted'):
                        self.finish(row, 'interrupted', error=chain_error('interrupted',
                            'The previous resume request stopped before it could be confirmed.',
                            'POST /freevideo/agent/v1/jobs/%s/resume to retry.' % row['id'], retryable=True).view())
                        return
                    self.touch(row, chain_submission='linked')
                self._poll_chain(row)
            except Exception as error:
                self.poll_failure(row, error)

    def _poll_chain(self, row):
        try:
            chain = self.chain(row['chain_id'])
        except (KeyError, JobError):
            if row['status'] in ACTIVE:
                self.missing_chain(row)
            return
        state = chain['state']
        editor = row.get('editor')
        if editor:
            self.projects.deliver(row, chain)
        status = dict(queued='queued', running='running', cancelling='cancelling', cancelled='cancelled',
                      failed='failed', interrupted='interrupted', done='succeeded')[state]
        # A project generation fills each part from its own block; nothing is joined.
        if state == 'done' and chain['output_state'] != 'done' and not (editor and chain['output_state'] == 'none'):
            status = 'failed' if chain['output_state'] == 'failed' else 'running'
        if status in DONE:
            result = None
            output_error = None
            try:
                blocks = []
                for block in chain['blocks']:
                    identity, video = None, None
                    if block['output']:
                        try:
                            identity = library_id(block['output'])
                        except (JobError, TypeError, ValueError):
                            pass
                        if identity:
                            video = library_view(self.port, block['output'])
                    blocks.append(dict(id=block['id'], index=block['index'] + 1, state=block['state'],
                        library_id=identity, report_id=block['report_id'], video=video))
                result = dict(chain_id=chain['id'], output_state=chain['output_state'],
                    output_blocks=chain['output_blocks'], report_id=chain.get('last_report_id'), blocks=blocks)
                if editor:
                    result.update(editor_project=editor['project'], clip_ids=list(editor['ids']),
                                  revision=editor.get('revision'))
                elif chain.get('output') and chain['output_state'] == 'done':
                    self.saved_video(chain['output'])
                    result.update(self.generation_result(row, self.port.output_summary(chain['output'])))
                elif status == 'succeeded':
                    raise chain_error('invalid_output', 'The completed chain has no saved video.',
                                      'Restore its saved video and read this job again.')
            except Exception as exception:
                self.log_poll_failure(row, exception)
                status = 'failed'
                output_error = self.invalid_output(row)
            error = None
            if status != 'succeeded':
                code = ('join_failed' if state == 'done' and chain['output_state'] == 'failed' else
                        dict(failed='generation_failed', interrupted='interrupted', cancelled='cancelled')[status])
                error = chain_error(code, 'Generation %s; completed blocks are retained.' % status,
                    'POST /freevideo/agent/v1/jobs/%s/resume to retry unfinished work.' % row['id'],
                    details=[dict(field='/chain/state', got=state),
                             dict(field='/chain/output_state', got=chain['output_state'])], retryable=True).view()
                if output_error is not None:
                    error = output_error
                elif code == 'generation_failed':
                    error = self.generation_failure(row, chain)
            if row['status'] != status or row.get('result') != result or row.get('error') != error:
                self.finish(row, status, result=result, error=error)
        elif row['status'] != status:
            self.touch(row, status=status, **({'started_at': self.clock()} if status == 'running'
                                            and row.get('started_at') is None else {}))

    def poll_generation(self, row):
        prompt_id, node = row['comfy']['prompt_id'], row['comfy']['node']
        state = self.port.prompt_state(prompt_id)
        if state == 'running' and row['status'] == 'queued':
            self.touch(row, status='running', started_at=self.clock())
        if state in ('running', 'pending'):
            return
        history = self.port.history(prompt_id)
        if history is None:
            if state == 'missing' and self.clock() - row.get('queued_at', row['updated_at']) > 10:
                self.finish(row, 'cancelled' if row.get('_cancel') else 'failed',
                            error=dict(code='cancelled' if row.get('_cancel') else 'lost',
                                       message='The request left the ComfyUI queue without a result'))
            return
        status = history.get('status') or {}
        outputs = (history.get('outputs') or {}).get(node) or {}
        summary = (outputs.get('freevideo_summary') or [None])[0]
        if status.get('status_str') == 'success' and summary:
            self.finish(row, 'succeeded', result=self.generation_result(row, summary))
            return
        if status.get('completed') is False or status.get('status_str') == 'error' or not summary:
            error = self.port.generation_error(history, node)
            self.finish(row, 'cancelled' if error['code'] == 'cancelled' else 'failed', error=error)

    def generation_result(self, row, summary):
        spec = row['spec']
        if not isinstance(summary, dict) or not isinstance(summary.get('video'), str):
            raise ValueError('Invalid video summary')
        for key in ('geometry', 'preview_geometry'):
            if summary.get(key) is not None and not isinstance(summary[key], dict):
                raise ValueError('Invalid video geometry')
            for field, value in (summary.get(key) or {}).items():
                if field in ('width', 'height', 'frames', 'seconds', 'fps') and value is not None and (
                        type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                    raise ValueError('Invalid video geometry field')
        video = summary['video']
        self.saved_video(video)
        geometry = summary.get('geometry') or {}
        result = dict(video=library_view(self.port, video),
                      library_id='/'.join(Path(video).parts[1:3]) if video.startswith('FreeVideo/') else None,
                      width=geometry.get('width'), height=geometry.get('height'), frames=geometry.get('frames'),
                      seconds=geometry.get('seconds'), seed=spec.get('seed'), preview=summary.get('preview', False),
                      reused=bool(summary.get('result_cache_hit')), request_seconds=summary.get('request_seconds'),
                      sample_seconds=summary.get('sample_seconds'), vram_peak_bytes=summary.get('vram_peak_bytes'),
                      reference_trims=summary.get('reference_trims') or [])
        if summary.get('preview') and summary.get('preview_geometry'):
            result.update(width=summary['preview_geometry'].get('width'),
                          height=summary['preview_geometry'].get('height'),
                          refine_hint='Submit {"refine_from": "%s"} for the full-resolution second pass' % row['id'])
        return result

    def saved_video(self, relative):
        identity = library_id(relative)
        path = self.port.library_video(identity)
        resolved = Path(path).resolve()
        root = self.port.output_root.resolve() / 'FreeVideo'
        if not resolved.is_relative_to(root) or resolved != self.port.output_path(relative).resolve():
            raise ValueError('Video is outside the library')
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or not info.st_size:
            raise ValueError('Video is not a nonempty regular file')
        return resolved

    def generation_failure(self, row, chain):
        block = next((b for b in chain['blocks'] if b['state'] == 'failed'), None)
        reason = {}
        if block and block.get('prompt_id'):
            try:
                history = self.port.history(block['prompt_id'])
                if history:
                    reason = self.port.generation_error(history, chain['node'])
            except Exception as error:
                self.log_poll_failure(row, error)
        try:
            report_id = (self.port.progress(chain['node']) or {}).get('report_id')
        except Exception:
            report_id = None
        code = reason.get('code', 'generation_failed')
        if code not in ('insufficient_memory', 'out_of_memory', 'generation_failed'):
            code = 'generation_failed'
        message = str(reason.get('message') or (block or {}).get('error') or
                      'Generation failed; completed blocks are retained.')
        for prefix in (str(self.port.output_root), str(Path.home())):
            message = message.replace(prefix, '<local>')
        prompt = row['spec'].get('prompt')
        if isinstance(prompt, str) and prompt:
            message = message.replace(prompt, '<prompt>')
        # Enhanced prompts may differ from the submitted prompt.
        if block:
            text = (block.get('inputs') or {}).get('text')
            if isinstance(text, str) and text:
                message = message.replace(text, '<prompt>')
        detail = dict(block=block['index'] + 1 if block else None, total=len(chain['blocks']))
        detail.update({key: reason[key] for key in ('alternatives', 'failure_kind', 'report') if key in reason})
        next_action = ('Lower the size or duration, then POST /freevideo/agent/v1/jobs to submit a new job.'
                       if code == 'insufficient_memory' else
                       'POST /freevideo/agent/v1/jobs/%s/resume to retry unfinished work.' % row['id'])
        if code == 'insufficient_memory' and reason.get('alternatives'):
            next_action = 'Use the alternatives in details to lower size or duration, then POST /freevideo/agent/v1/jobs.'
        failure = dict(chain_error(code, errors.ERRORS[code][2], next_action, details=[detail],
                           retryable=code != 'insufficient_memory').view(),
                    **errors.reasons(dict(code=code, message=message)))
        if isinstance(report_id, str) and re.fullmatch(r'[a-f0-9]{32}', report_id):
            failure['report_id'] = report_id
        return failure

    # Local image generation ----------------------------------------------------------------------------
    def resolve_image_cast(self, spec):
        """Pin character card pictures before saving the job, including across restarts/replays."""
        if spec['kind'] != 'image':
            return
        for i, person in enumerate(spec.get('people', [])):
            identity = person.get('cast')
            if identity is None:
                continue
            path = '/people/%d/cast' % i
            try:
                card = self.cast.get(identity)
            except JobError as error:
                if error.code != 'not_found':
                    raise
                specs.fail(path, 'an existing character card ID from GET /freevideo/agent/v1/cast')
            if card['kind'] != 'character':
                specs.fail(path, 'a character card, rather than a setting card')
            picture = next((row for row in card['images'] if row['label'] == 'front'), None)
            if picture is None:
                specs.fail(path, 'a character card with a front picture; add or generate one first')
            person.pop('cast')
            person['image'] = picture['media']['file']
            person.setdefault('name', card['name'][:40])

    def wait_image(self, row, code, *, waiting_for=None):
        if code not in IMAGE_WAIT_ERRORS:
            return False
        if row.get('_cancel'):
            self.finish(row, 'cancelled', error=dict(code='cancelled'))
            return True
        waiting_for = [waiting_for or ('video' if code == 'busy_video' else 'image')]
        row.pop('_image', None)
        if row['status'] != 'waiting' or row.get('waiting_for') != waiting_for:
            self.touch(row, status='waiting', started_at=None, waiting_for=waiting_for)
        return True

    def start_image(self, row):
        spec = row['spec']
        row['_phase'] = 'preparing images'
        value = {k: spec[k] for k in ('width', 'height', 'seed') if k in spec}
        if spec.get('people'):
            # A first frame: the people's pictures at the asked places, in the scene shown or described.
            action = 'compose'
            value['people'] = [dict(person, image=image_reference(self.port, person['image'])) for person in spec['people']]
            if spec.get('scene_image'):
                value['scene_image'] = image_reference(self.port, spec['scene_image'])
            else:
                value['scene'] = spec['prompt']
        else:
            action = 'generate'
            value.update(prompt=spec['prompt'], images=[image_reference(self.port, name) for name in spec['images']])
            metadata = spec.get('metadata') or {}
            if metadata.get('cast_card') and metadata.get('cast_part') == 'back':
                value['cast_view'] = 'back'
        if row.get('_cancel'):
            raise JobError('cancelled', IMAGE_ERRORS['cancelled'])
        row['_image'] = self.port.image_start(action, value)
        row['_phase'] = 'starting'
        row.pop('waiting_for', None)
        self.touch(row, status='running', started_at=self.clock())
        if row.get('_cancel'):
            self.port.image_cancel(row['_image'])

    def poll_image(self, row):
        try:
            state = self.port.image_status(row['_image'])
        except ValueError:
            self.finish(row, 'failed', error=dict(code='unknown_job'))
            return
        phase = state.get('phase')
        if phase in ('queued', 'starting', 'progress', 'cancelling'):
            row['_phase'] = state.get('stage') if phase == 'progress' else phase
        elif phase == 'complete':
            from PIL import Image
            relative = state['file']
            with Image.open(self.port.input_path(relative)) as picture:
                width, height = picture.size
            result = dict(assets.view(relative, 'input'), width=width, height=height)
            result['thumbnail'] = result['url']
            seed = state.get('seed', row['spec'].get('seed'))
            if seed is not None:
                result['seed'] = seed
            self.finish(row, 'succeeded', result=result)
        elif phase == 'cancelled':
            self.finish(row, 'cancelled', error=dict(code='cancelled'))
        else:
            failure = state.get('error')
            code = failure.get('code') if isinstance(failure, dict) else failure
            code = code or 'generation_failed'
            if not self.wait_image(row, code):
                self.finish(row, 'failed', error=dict(code=code, report_id=state.get('report_id')))

    # Local VLM -----------------------------------------------------------------------------------------
    def start_vlm(self, row):
        spec = row['spec']
        self.touch(row, status='running', started_at=self.clock())
        row['_phase'] = 'preparing images'
        if row['kind'] == 'enhance':
            media = []
            for role in ('first', 'last'):
                if role in spec['media']:
                    media.append(dict(file=self.media_file(row, spec['media'][role], role), role=role))
            for i, item in enumerate(spec['media'].get('references', []), 1):
                media.append(dict(file=self.media_file(row, item, 'reference-%02d' % i), role='reference'))
            value, action = dict(text=spec['text'], seconds=spec['seconds'], media=media), 'rewrite'
        else:
            questions = []
            for q, item in enumerate(spec['questions'], 1):
                images = []
                for i, image in enumerate(item['images'], 1):
                    images.append(dict(file=self.media_file(row, image, 'q%d-image-%02d' % (q, i)),
                                       **({'label': image['label']} if image.get('label') else {})))
                if item.get('video'):
                    video = self.source_video(item['video'])
                    folder = 'freevideo/agent/%s/q%d-frames' % (row['id'], q)
                    frames = self.port.input_path(folder, create=True)
                    frames.mkdir(exist_ok=True)
                    for frame in self.port.sample_frames(video, frames, item['frames']):
                        name = Path(frame['path']).name
                        stamp = frame['seconds']
                        images.append(dict(file=folder + '/' + name, label='frame at %02d:%06.3f' % (
                            int(stamp // 60), stamp % 60)))
                questions.append(dict(text=item['text'], images=images))
            value = dict(questions=questions, format=spec['format'], max_tokens=spec['max_tokens'])
            if spec.get('system'):
                value['system'] = spec['system']
            action = 'ask'
        if row.get('_cancel'):
            raise JobError('cancelled', 'Cancelled before it started')
        row['_phase'] = 'starting'
        row['_vlm'] = self.port.vlm_start(action, value)

    def poll_vlm(self, row):
        try:
            state = self.port.vlm_status(row['_vlm'])
        except ValueError:
            self.finish(row, 'failed', error=dict(code='lost', message='The VLM result is no longer available'))
            return
        phase = state.get('phase')
        if phase in ('starting', 'loading', 'rewriting', 'answering', 'finishing', 'cancelling'):
            row['_phase'] = phase
            stage = (state.get('diagnostics') or {}).get('stage')
            row['_detail'] = stage
            return
        timings = {k: v for k, v in (state.get('diagnostics') or {}).items()
                   if k in ('load_seconds', 'rewrite_seconds', 'answer_seconds', 'worker_seconds', 'input_tokens',
                            'output_tokens', 'decode_tokens_per_second')}
        if phase == 'complete':
            if row['kind'] == 'enhance':
                result = dict(text=state.get('text'), timings=timings)
            else:
                result = dict(answers=state.get('answers') or [], timings=timings)
            self.finish(row, 'succeeded', result=result)
        elif phase == 'cancelled':
            self.finish(row, 'cancelled', error=dict(code='cancelled', message='Cancelled'))
        else:
            code = state.get('error') or 'vlm_failed'
            self.finish(row, 'failed', error=dict(code=code, message=VLM_ERRORS.get(code, 'The local VLM failed: ' + code),
                                                   retryable=code in ('busy', 'out_of_memory', 'timeout')))

    # Assembly ------------------------------------------------------------------------------------------
    def run_assemble(self, row):
        spec = row['spec']
        clips = []
        for item in spec['clips']:
            clips.append(dict(path=str(self.source_video(item)), start=item.get('start', 0.), end=item.get('end')))
        # Every sequence is saved as a library work, so it shows in My works and has a library link.
        # The identity comes from the job id, so it survives a process restart during assembly.
        run = hashlib.sha256(row['id'].encode()).hexdigest()[:32]
        relative = 'FreeVideo/%s/%s/video.mp4' % (iso(row['created_at'])[:10], run)
        out = self.port.output_path(relative, create=True)
        row['_phase'] = 'assembling'
        def progress(value):
            row['_detail'] = value if isinstance(value, str) else json.dumps(value)[:200]
        with self.cpu:
            made = self.port.assemble(clips, out, transition=spec['transition'],
                                      transition_seconds=spec['transition_seconds'], width=spec.get('width'),
                                      height=spec.get('height'), audio=spec['audio'], progress=progress,
                                      cancelled=lambda: bool(row.get('_cancel')))
        if row.get('_cancel'):
            raise JobError('cancelled', 'Cancelled')
        manifest = dict(schema='freevideo.sequence', schema_version=1, job=row['id'], label=spec.get('label'), name=spec.get('name'),
                        project=spec.get('project'), created_at=iso(self.clock()),
                        clips=[dict(source={k: v for k, v in item.items() if k in ('from_job', 'library', 'file')},
                                    start=item.get('start', 0.), end=item.get('end')) for item in spec['clips']],
                        transition=spec['transition'], transition_seconds=spec['transition_seconds'])
        out.with_suffix('.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        out.with_suffix('.request.json').write_text(json.dumps(dict(success=True, geometry={
            k: made.get(k) for k in ('seconds', 'frames', 'width', 'height')}, sequence=manifest)), encoding='utf-8')
        return dict(library_id=library_id(relative), video=library_view(self.port, relative),
                    **{k: made.get(k) for k in ('seconds', 'frames', 'width', 'height', 'has_audio', 'clips')})


VLM_ERRORS = {
    'busy': 'The GPU was busy; the job can be submitted again',
    'model_missing': 'The prompt VLM is not installed; install it from FreeVideo Settings or POST /freevideo/agent/v1/vlm/install',
    'ram_space': 'The local VLM needs about 11 GiB of available RAM',
    'context_length': 'The question or images are too long for the local VLM',
    'invalid_output': 'The VLM answer did not follow the required format',
    'vlm_invalid_json': 'The VLM did not return valid JSON',
    'out_of_memory': 'The local VLM ran out of memory',
    'timeout': 'The local VLM took longer than 10 minutes',
    'image_size': 'An image is larger than 50 megapixels',
    'unsupported_media': 'The local VLM reads images only',
}
