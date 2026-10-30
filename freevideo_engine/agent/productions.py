"""Durable execution of the pure production machine, sharing Jobs' queue and lock.

State, operation replies, rejected submissions and cancellation tombstones share
one atomic receipt. Persist intent before dispatch; Jobs persists parent.ref in
its own submission transaction, so a crash between the two writes cannot repeat
an accepted child. No director loop or model calls are started here.
"""
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
from statistics import median
import tempfile
import threading
import time
import uuid

from . import drama, production, schema, spec, errors, operation_receipts
from .jobs import DONE, JobError, iso, parts_problems
from .forecast import generation_estimate

SHEET_LEAST, SHEET_MOST = 6, 24  # frames per take when the caller gives no count: one per second

MAX_GENERATION = 4  # Generations are zero-based: original 0, reshoots 1 through 4.
MAX_CHILDREN = 60   # Includes assembly, rejected submissions and resumed attempts.
MAX_WAITERS = 32    # Long GET waits served at once; more callers receive the current state.
MAX_WAIT = 25
REQUEST_ID = re.compile(r'r-[0-9a-f]{32}')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
_POINTER = re.compile(r'(?:/(?:[A-Za-z0-9_-]{1,64}|\?))*|/')
_GOT = re.compile(r'missing|None|True|False|-?\d{1,20}(?:\.\d{1,20})?(?:e[+-]?\d{1,3})?|<string length \d{1,9}>|<[A-Za-z_]{1,32}>')
_ROW_KEYS = frozenset(('path', 'field', 'allowed', 'got', 'message', 'next'))


def _quoted(title):
    return '"%s"' % title if title else 'the film'


def _detail(value):
    """Domain details carry identifiers only; anything else is not repeated."""
    if value is None or type(value) in (bool, int):
        return value
    return value if isinstance(value, str) and _NAME.fullmatch(value) else '?'


def _rows(error):
    """Shared diagnostics for a domain error: validator-made rows stay, other problems are rebuilt."""
    if not error.problems:
        return [{k: _detail(v) for k, v in error.details.items()}] if error.details else []
    rows = []
    for row in error.problems:
        if row.get('_safe'):
            rows.append({k: deepcopy(v) for k, v in row.items() if k != '_safe'})
        else:
            # Character names are caller-chosen keys: a pointer may name the place, never the key.
            path = re.sub(r'(?<=/characters/)[^/]*', '?', row['path']) if isinstance(row.get('path'), str) else row.get('path')
            rows.append({k: v for k, v in schema.problem_diagnostic(
                path, production._public(row.get('message'))).items() if k != '_safe'})
    return rows


def pictures_each(resolved):
    """Pictures per person a shot used; None when no card was carried."""
    return resolved['pictures_each'] if resolved['people'] else None


class MaterialRejected(JobError):
    """External callers named material the content-only boundary does not admit: the job validation error."""
    status = 422

    def __init__(self, rows):
        super().__init__('invalid_request', 'The material is not accepted here.')
        self.rows = rows

    def view(self):
        return errors.validation(self.rows)


# A paused film keeps its own wording; the shared code's text speaks of work sessions.
FILM_STOPPED_NEXT = 'Wait for the user to continue the film; do not retry or start a new one.'


class ProductionFailure(JobError):
    def __init__(self, error):
        super().__init__(error.code, error.message)
        self.status = errors.PRODUCTION_STATUS.get(error.code, errors.ERRORS[error.code][0])
        message = production._public(error.message) if isinstance(error.message, str) else ''
        rows = _rows(error)
        if error.code == 'invalid_screenplay' and rows and all('field' in row for row in rows):
            # Like a request validation error: every problem with its field, as the tool text shows only this.
            message = '; '.join(row['message'] for row in rows)[:2000]
        self.error = errors.build(error.code, validation=True, message=message[:2000],
            details=rows, next_action=(FILM_STOPPED_NEXT if error.code == 'stopped_by_user' else error.next))
        if error.code == 'not_ready':
            self.error['retryable'] = False

    def view(self):
        return deepcopy(self.error)


def _domain(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except production.ProductionError as error:
        raise ProductionFailure(error) from None


def _safe_row(row):
    """The row unchanged when it is exactly what schema.diagnostic would have produced, else None."""
    if not isinstance(row, dict) or set(row) != _ROW_KEYS or not all(isinstance(row[k], str) for k in _ROW_KEYS):
        return None
    path, allowed, got = row['path'], row['allowed'], row['got']
    if (row['field'] != path or not _POINTER.fullmatch(path) or not _GOT.fullmatch(got)
            or not 0 < len(allowed) <= 200 or any(c in allowed for c in '/\\\n\r')):
        return None
    if row['message'] != '%s must be %s; received %s.' % (path, allowed, got):
        return None
    if row['next'] != 'Set %s to %s.' % (path, allowed) and not (
            row['next'].startswith('Use %s ' % path) and row['next'].endswith('.') and len(row['next']) <= 300
            and not any(c in row['next'][len(path):] for c in '\\\n\r')):
        return None
    return dict(row)


def _safe_error(error):
    """Use the job boundary, never production._public's best-effort path regex.

    Rebuilding legacy/backend errors removes arbitrary strings (including paths
    with spaces). Only the service's own request id format and exact schema
    diagnostics survive; everything else is rebuilt from the code table.
    """
    if not isinstance(error, dict) or not error:
        return None
    code = error.get('code') if isinstance(error.get('code'), str) else None
    request = error.get('request_id')
    request = request if isinstance(request, str) and REQUEST_ID.fullmatch(request) else errors.request_id()
    details = error.get('details')
    result = errors.build(code, details=details, request=request, action_required=code != 'cancelled')
    rows = [_safe_row(row) for row in details] if isinstance(details, list) and details else []
    if rows and all(rows):
        result.update(details=rows, message='; '.join(row['message'] for row in rows))
    return dict(result, **errors.reasons(error))


def failure(view):
    """The current failure, excluding old takes superseded by a usable take."""
    if view.get('stalled'):
        return view['stalled'], None
    join = view.get('join') or {}
    if join.get('state') in ('failed', 'interrupted') and join.get('error'):
        return join['error'], join.get('task')
    for shot in view.get('shots', []):
        if shot['state'] == 'failed':
            take = next((t for t in reversed(shot['takes']) if t.get('error') and not t['stale']), {})
            if take:
                return take['error'], take.get('task')
    return None, None


class Productions:
    def __init__(self, jobs):
        self.jobs = jobs
        self.directory = jobs.directory / 'productions'
        self.records = {}
        self.changed = threading.Condition(jobs.lock)
        self._waiters = {}
        self._waiter_lock = threading.Lock()
        # gate(action, *, by, estimate_seconds, refs, question) raises when the user must approve first.
        self.gate = None
        self.load()
        jobs.parent_allowed = self.allows

    def load(self):
        try:
            paths = sorted(self.directory.glob('*.json'))
        except OSError:
            logging.getLogger(__name__).warning('Could not list saved films; starting without them')
            return
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                state = record['state']
                schema.validate(schema.PRODUCTION, state['id'])
                if path.stem != state['id'] or state['version'] != 1 or type(state['revision']) is not int:
                    raise ValueError('Invalid receipt identity')
                if not isinstance(record['children'], dict):
                    raise ValueError('Invalid execution receipts')
                record['receipts'] = operation_receipts.load(record['receipts'])
                json.dumps(record, allow_nan=False)
                request = dict(screenplay=state['screenplay'],
                    takes_per_shot=state['takes_per_shot'], director=state['director'])
                for ref, child in record['children'].items():
                    schema.validate(schema.REF, ref)
                    if child['status'] not in DONE:
                        raise ValueError('Invalid child receipt')
                if not isinstance(record.get('submitting', []), list):
                    raise ValueError('Invalid submission marker')
                production.view(state)
                try:
                    checked = production.plan(request, fields=False)
                    if [s['id'] for s in state['shots']] != [s['id'] for s in checked['preview']['shots']]:
                        raise JobError('internal_error', 'Saved shots differ from the current plan')
                    production.advance(deepcopy(state), deepcopy(record['children']), now=state['updated_at'])
                except (production.ProductionError, JobError) as error:
                    # A readable record that cannot advance yet is kept, never renamed away.
                    record['stalled'] = self._stalled(error)
                    logging.getLogger(__name__).warning('Kept a readable production receipt stalled (%s)',
                                                        record['stalled']['code'])
                self.records[state['id']] = record
            except (ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError, OSError):
                try:
                    path.replace(path.with_name(path.name + '.bad-' + uuid.uuid4().hex))
                except OSError:
                    # A receipt that cannot be moved aside is left in place; the agent still starts.
                    logging.getLogger(__name__).warning('Skipped an invalid production receipt that could not be renamed')
                else:
                    logging.getLogger(__name__).warning('Isolated an invalid production receipt; other films remain available')

    def _stalled(self, error):
        code = error.code if isinstance(getattr(error, 'code', None), str) and error.code in errors.ERRORS else 'internal_error'
        return errors.with_reason(dict(code=code, message=errors.ERRORS[code][2], at=iso(self.jobs.clock())))

    def _save(self, record):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (record['state']['id'] + '.json')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self.records[record['state']['id']] = record
        self.changed.notify_all()
        self._wake(record['state']['id'])

    def _wake(self, identity):
        """Tell asynchronous long-waiters on the event loop that a revision may have changed."""
        with self._waiter_lock:
            slots = list(self._waiters.get(identity, ()))
        for loop, event in slots:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # that loop has already closed
                pass

    def _record(self, identity):
        if identity not in self.records:
            raise JobError('not_found', 'The film does not exist')
        return self.records[identity]

    def _receipt(self, scope, payload, ref, client=None):
        if ref is None:
            return None, None
        schema.validate(schema.REF, ref)
        digest = hashlib.sha256(json.dumps(dict(scope=scope, body=payload), sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        for record in self.records.values():
            for old in record['receipts']:
                if old['key'] != ref or old.get('client') != client:
                    continue
                if old['digest'] != digest:
                    raise JobError('idempotency_conflict', 'Key already used')
                return digest, deepcopy(old['result'])
        return digest, None

    def _jobs(self, identity):
        return {row['parent']['ref']: row for row in self.jobs.rows()
                if row.get('parent', {}).get('production') == identity}

    def _children(self, record):
        children = deepcopy(record['children'])
        for ref, row in self._jobs(record['state']['id']).items():
            # Authoritative jobs override tombstones: a success may win a cancel race.
            children[ref] = dict(status=row['status'], result=deepcopy(row.get('result')),
                                 error=_safe_error(row.get('error')))
        return children

    def _count(self, record):
        state = record['state']
        refs = set(record['children']) | set(self._jobs(state['id']))
        unreserved = 0
        for shot in state['shots']:
            for take in shot['takes'] + [t for group in shot['pending'] for t in group]:
                if take['job']:
                    refs.add(take['job'])
                else:
                    unreserved += 1
                refs.update(row['job'] for row in take.get('stopped', []))
        refs.update(row['ref'] for row in state['joins'])
        return len(refs) + unreserved

    def allows(self, parent):
        """May Jobs still run the child that carries this parent? Registered as Jobs.parent_allowed."""
        try:
            identity, ref = parent['production'], parent['ref']
        except (KeyError, TypeError):
            return False
        with self.changed:
            record = self.records.get(identity)
            if record is None or ref in record['children']:
                return False
            state = record['state']
            if state['cancelled'] or state['status'] in ('finished', 'cancelled'):
                return False
            for shot in state['shots']:
                for take in shot['takes']:
                    if take['job'] == ref:
                        return (take['state'] in production.ACTIVE and not take['cancel_requested']
                                and not state.get('paused'))
            # A running join is not suspended by pause; only a current join may start.
            return any(join['ref'] == ref and join['state'] in production.ACTIVE for join in state['joins'])

    def _material(self, state, *, local=True):
        """The submission material policy applied to the planned shots before anything runs.

        In-process callers may name local files; external HTTP callers may not."""
        problems, placeholder = [], 'j000000000000'
        for shot in state['shots']:
            takes = shot['takes'] + [t for group in shot['pending'] for t in group]
            if not takes:
                continue
            item = deepcopy(takes[0]['spec'])
            item.pop('ref', None)
            continued = bool(item.get('continue_from'))
            if continued:
                item['continue_from'] = dict(from_job=placeholder)
            prefix = '/screenplay/shots/%d' % (shot['index'] - 1)
            try:
                normalized = spec.check(item, local=local)
            except spec.SpecError as error:
                for row in error.errors:
                    if not row.get('_safe'):
                        problems.append(schema.problem_diagnostic(prefix, 'has unsupported material'))
                        continue
                    old, new = row['path'], prefix + row['path']
                    problems.append(dict(row, path=new, field=prefix + row.get('field', old),
                        message=row['message'].replace(old, new, 1), next=row['next'].replace(old, new, 1)))
                continue
            if continued or shot['continues']:
                # The same length rule as at submission, with this computer's single-run limit rather than the
                # 15 s default: a shot that cannot be cut into parts of at least 5 s is refused here instead of
                # starting a film in which that shot never gets a job. A shot not yet submitted carries no parent.
                try:
                    pinned = spec.normalize(dict(item, continue_from=dict(from_job=placeholder)), port=self.jobs.port)
                except spec.SpecError:
                    pinned = None  # Its other problems are reported where they are found.
                if pinned is not None:
                    problems.extend(parts_problems(pinned, None, prefix))
            for dependency in normalized['depends_on']:
                if not (continued and dependency == placeholder) and dependency not in self.jobs.jobs:
                    problems.append(schema.diagnostic(prefix + '/first', 'a job that exists on this server', dependency))
        if problems and not local:
            raise MaterialRejected(problems)
        if problems:
            _domain(production._invalid, problems)

    def plan(self, request, *, local=True):
        result = _domain(production.plan, {k: v for k, v in request.items() if k != 'ref'})
        state = _domain(production.new, 'dplanpreview', result['request'], now=iso(self.jobs.clock()))
        play = result['request']['screenplay']
        with self.changed:
            cards = self._cards(play)
        self._material(state, local=local)
        problems = []
        for i, (row, source, shot) in enumerate(zip(result['preview']['shots'], play['shots'], state['shots'])):
            # The same resolution a take gets when it is submitted, with the cards as they are now.
            first = next((t for s, t in self._takes(state) if s is shot), None)
            resolved = self._carry(play, source, cards, first and first['spec'], continues=shot['continues'])
            row['prompt'] = drama.with_cast(row['prompt'], resolved)
            row.update(cast_reference=resolved['cast_reference'], cast=resolved['people'],
                       cast_omitted=resolved['omitted'], cast_voice_omitted=resolved['voice_omitted'],
                       cast_missing=resolved['missing'], cast_pictures_each=pictures_each(resolved),
                       references=len(resolved['references']))
            try:
                spec._text(row['prompt'], '/screenplay/shots/%d/prompt' % i, 20000)
            except spec.SpecError as error:
                problems.extend(error.errors)
        _domain(production._invalid, problems)
        preview = dict(state=state, estimates={})
        with self.changed:
            self._prepare_estimates(preview)
            result['estimate'] = dict(seconds=self._progress(preview)['estimate_seconds'])
        return result

    @staticmethod
    def _takes(state):
        for shot in state['shots']:
            for take in shot['takes'] + [t for group in shot['pending'] for t in group]:
                yield shot, take

    def _take_estimate(self, state, shot, take, item=None):
        """Forecast a submission intent, or its final resolved material at dispatch."""
        try:
            item = deepcopy(take['spec'] if item is None else item)
            if shot['continues']:
                item['continue_from'] = dict(from_job='j000000000000')
            if item.get('continue_from', {}).get('ref'):
                item['continue_from'] = dict(from_job='j000000000000')
            resolved = self._carry(state['screenplay'], state['screenplay']['shots'][shot['index'] - 1],
                                   self._current(state['screenplay']), item, continues=shot['continues'])
            if resolved['references']:
                item['media'] = dict(references=deepcopy(resolved['references']))
            estimate, revision = generation_estimate(self.jobs.port, spec.normalize(item, port=self.jobs.port))
            return dict(estimate, revision=revision)
        except Exception:  # Timing is optional; a forecast failure must not reject a film or job.
            return dict(seconds=None, basis=None, reason='unavailable')

    def _shape(self, state, only=None):
        """Size and length of what a confirmation asks to make: the first shot's canvas, every shot's seconds added up."""
        try:
            rows = []
            for shot in state['shots']:
                take = next((t for s, t in self._takes(state) if s is shot and not t['stale'] and t['state'] != 'cancelled'), None)
                if take is None or only not in (None, shot['id']):
                    continue
                item = deepcopy(take['spec'])
                if shot['continues']:
                    item['continue_from'] = dict(from_job='j000000000000')
                rows.append(spec.normalize(item, port=self.jobs.port))
            if not rows:
                return {}
            return dict(width=rows[0]['width'], height=rows[0]['height'], seconds=round(sum(row['seconds'] for row in rows), 3))
        except Exception:  # The card is complete without it; a failure here must not reject a film.
            return {}

    def _join_estimate(self, state, previous=None):
        """Use only successful local joins with the same input geometry and transition."""
        shapes = []
        for shot in state['shots']:
            take = production._chosen(shot) or next((t for s, t in self._takes(state)
                if s is shot and not t['stale'] and t['state'] != 'cancelled' and not t['cancel_requested']), None)
            if take is None:
                return dict(seconds=None, basis=None, reason='no_matching_estimate')
            item = deepcopy(take['spec'])
            item.pop('continue_from', None)
            normalized = spec.normalize(item, port=self.jobs.port)
            shapes.append([normalized[k] for k in ('width', 'height', 'frames')])
        current = next((j for j in state['joins'] if j['ref'] == state['current_join']), None)
        transition = current['transition'] if current else state['screenplay'].get('transition', 'cut')
        signature = dict(shapes=shapes, transition=transition)
        if previous and previous.get('signature') == signature and previous.get('ref') == state['current_join']:
            return previous
        samples = []
        for row in self.jobs.rows():
            if row['kind'] != 'assemble' or row['status'] != 'succeeded':
                continue
            saved = row['spec']
            if saved.get('transition', 'cut') != transition or saved.get('width') or saved.get('height'):
                continue
            if saved.get('audio', True) is not True or (
                    transition == 'crossfade' and saved.get('transition_seconds', .5) != .5):
                continue
            if any(set(clip) != {'from_job'} for clip in saved['clips']):
                continue
            inputs = [self.jobs.jobs.get(c.get('from_job'), {}).get('spec', {}) for c in saved['clips']]
            if [[s.get(k) for k in ('width', 'height', 'frames')] for s in inputs] != shapes:
                continue
            start, end = row.get('started_at'), row.get('finished_at')
            if (type(start) in (int, float) and type(end) in (int, float)
                    and math.isfinite(end - start) and end > start):
                samples.append(end - start)
        return dict(signature=signature, ref=state['current_join'], seconds=median(samples) if samples else None,
                    basis='matching_local_joins' if samples else None,
                    **({} if samples else {'reason': 'no_matching_estimate'}))

    def _prepare_estimates(self, record):
        """Save planned takes before replying; final material is frozen before Jobs.submit."""
        estimates = record.setdefault('estimates', {})
        for shot, take in self._takes(record['state']):
            if take['job'] and take['job'] not in estimates:
                estimates[take['job']] = self._take_estimate(record['state'], shot, take)
        # A choice or changed screenplay can change the inputs to the final join.
        previous = record.get('join_estimate', {})
        try:
            record['join_estimate'] = self._join_estimate(record['state'], previous)
        except Exception:  # Optional forecasts cannot make an otherwise valid operation fail.
            record['join_estimate'] = dict(seconds=None, basis=None, reason='unavailable')

    def _elapsed(self, state):
        def stamp(value):
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        ended = next((d['at'] for d in reversed(state['decisions'])
                      if d['action'] == state['status']), state['updated_at'])
        end = stamp(ended) if state['status'] in ('finished', 'cancelled') else self.jobs.clock()
        paused, stopped = None, 0.
        for decision in state['decisions']:
            if decision['action'] not in ('paused', 'resumed', 'cancelled'):
                continue
            at = min(end, stamp(decision['at']))
            if decision['action'] == 'paused' and paused is None:
                paused = at
            elif decision['action'] in ('resumed', 'cancelled') and paused is not None:
                stopped += max(0., at - paused)
                paused = None
        if paused is not None:
            stopped += max(0., end - paused)
        return round(max(0., end - stamp(state['created_at']) - stopped), 3)

    def _progress(self, record):
        state = record['state']
        children = self._jobs(state['id'])
        completed, running, fractions, estimates = 0, [], [], []
        def fraction(row, status):
            if status in ('succeeded', 'failed', 'interrupted'):
                return 1.
            value = (self.jobs.progress(row) or {}).get('fraction') if row and status == 'running' else None
            return min(1., max(0., value)) if type(value) in (int, float) and math.isfinite(value) else 0.
        for shot, take in self._takes(state):
            child = children.get(take['job'])
            status = child['status'] if child and take['state'] != 'paused' else take['state']
            if take['stale'] or status == 'cancelled' or (take['cancel_requested'] and status != 'succeeded'):
                continue
            part = fraction(child, status)
            completed += status in ('succeeded', 'failed', 'interrupted')
            fractions.append(part)
            estimates.append(record.get('estimates', {}).get(take['job'], {}).get('seconds'))
            if status == 'running':
                running.append(dict(shot=shot['id'], take=take['take'], fraction=part))
        current = next((j for j in state['joins'] if j['ref'] == state['current_join']), None)
        child = children.get(state['current_join'])
        joined = bool(current and (child['status'] if child else current['state']) == 'succeeded')
        join_fraction = fraction(child, child['status'] if child else current['state']) if current else 0.
        fractions.append(join_fraction)
        estimates.append(record.get('join_estimate', {}).get('seconds'))
        known = all(type(e) in (int, float) and math.isfinite(e) and e > 0 for e in estimates)
        total = sum(estimates) if known else None
        overall = sum(e * f for e, f in zip(estimates, fractions)) / total if known else sum(fractions) / len(fractions)
        remaining = sum(e * (1. - f) for e, f in zip(estimates, fractions)) if known else None
        if state['status'] == 'finished':
            overall, remaining = 1., 0. if known else None
        return dict(takes=dict(completed=completed, total=len(fractions) - 1),
                    shots=dict(completed=sum(production._chosen(s) is not None for s in state['shots']),
                               total=len(state['shots'])), running=running,
                    join=dict(completed=joined, fraction=join_fraction), fraction=overall,
                    elapsed_seconds=self._elapsed(state), estimate_seconds=total, remaining_seconds=remaining)

    @staticmethod
    def _elapsed_budget(progress):
        if progress['estimate_seconds'] is not None and progress['elapsed_seconds'] > 1.5 * progress['estimate_seconds']:
            return dict(reason='elapsed', elapsed_seconds=progress['elapsed_seconds'],
                        estimate_seconds=progress['estimate_seconds'])
        return None

    def _cards(self, play):
        """The current cards of a new film; every characters.<key>.card must be in the library. Hold the lock."""
        cards = self._current(play)
        rows = [schema.diagnostic('/screenplay/characters/?/card', 'a card ID listed by GET /freevideo/agent/v1/cast',
                                  identity) for identity, card in sorted(cards.items()) if card is None]
        if rows:
            raise ProductionFailure(production.ProductionError('invalid_screenplay',
                'A character names a card that is not in the cast library.', problems=rows, details={'problems': rows}))
        return cards

    def _preflight(self, before, draft):
        """Run the next advance on a copy: a failure, or a new generation over the limit, rejects the whole operation."""
        preview = deepcopy(draft)
        _domain(production.advance, preview['state'], self._children(preview), now=iso(self.jobs.clock()))
        for old, new in zip(before['state']['shots'] if before else [], preview['state']['shots']):
            if self._generations(new) > MAX_GENERATION and self._generations(new) > self._generations(old):
                raise JobError('reshoot_limit', 'Too many generations')
        if self._count(preview) > max(MAX_CHILDREN, self._count(before) if before else 0):
            raise JobError('production_limit', 'Too many child jobs')

    @staticmethod
    def _generations(shot):
        # Reconnects waiting for a pause to end already own their next generation.
        return shot['generation'] + sum(1 for group in shot['pending'] if group and group[0]['job'] is None)

    def start(self, request, *, by='agent', ref=None, local=True, client=None):
        _domain(production._actor, by)
        request = deepcopy(request)
        request.setdefault('director', by if by in ('agent', 'builtin') else 'agent')
        alias = request.pop('ref', None)
        if ref and alias and ref != alias:
            raise JobError('idempotency_conflict', 'Conflicting keys')
        ref = ref or alias
        with self.changed:
            digest, replay = self._receipt('start', dict(request=request, by=by), ref, client)
            if replay is not None:
                return replay
            identity = 'd' + uuid.uuid4().hex[:12]
            state = _domain(production.new, identity, request, now=iso(self.jobs.clock()))
            # new() only takes director, not the initiating actor. Attribution
            # belongs to this boundary, including the future in-process UI.
            for decision in state['decisions']:
                decision['by'] = by
            for shot in state['shots']:
                for take in shot['takes'] + [t for group in shot['pending'] for t in group]:
                    take['by'] = by
            record = dict(state=state, receipts=[], children={}, tasks={})
            if errors.SESSION.get():
                record['session'] = errors.SESSION.get()
            if self._count(record) > MAX_CHILDREN:
                raise JobError('production_limit', 'Too many child jobs')
            cards = self._cards(state['screenplay'])
            if not local:
                self._material(state, local=False)
            self._preflight(None, record)
            self._prepare_estimates(record)
            if self.gate is not None:
                self.gate('start', by=by, estimate_seconds=self._progress(record)['estimate_seconds'],
                          shape=self._shape(state),
                          refs={},  # the film gets its ID only when the approved call repeats
                          question='Start shooting %s (%d shots)?' % (_quoted(state['title']), len(state['shots'])))
            result = self._view(record)
            if ref:
                record['receipts'].append(dict(key=ref, digest=digest, result=result,
                                               **({'client': client} if client else {})))
            if self.jobs.session_open is not None and record.get('session'):
                self.jobs.session_open(record['session'])  # Deleting a conversation checks for films under this same lock.
            self._save(record)
            if cards:
                self.jobs.cast.used(sorted(cards), identity)
            self._run(identity)
            return deepcopy(result)

    @staticmethod
    def _context_next(record, join=False):
        """What a caller should do about a failed child, in the context of this film."""
        state = record['state']
        if state.get('paused'):
            return 'Wait for the user to continue.'
        if state['cancelled'] or state['status'] in ('finished', 'cancelled'):
            return None
        return 'Finish the film again.' if join else 'Reshoot this shot.'

    def _view(self, record):
        value = production.view(record['state'])
        tasks = record.get('tasks', {})
        def contextual(error, join=False):
            error = errors.with_reason(deepcopy(error))
            if error and 'next' in error:
                action = self._context_next(record, join)
                if action:
                    error['next'] = action
                else:
                    del error['next']
            return error
        for shot, internal in zip(value['shots'], record['state']['shots']):
            for take, saved in zip(shot['takes'], internal['takes']):
                take['error'] = contextual(saved.get('error'))
                take['task'] = tasks.get(take['job'])
        if value['join'] is not None:
            ref = record['state']['current_join']
            join = next(j for j in record['state']['joins'] if j['ref'] == ref)
            value['join'].update(ref=ref, task=tasks.get(ref), error=contextual(join['error'], True))
        value['stalled'] = errors.with_reason(deepcopy(record.get('stalled')))
        value['progress'] = self._progress(record)
        value['over_budget'] = self._elapsed_budget(value['progress'])
        error, _ = failure(value)
        value['error_zh'], value['error_en'] = (errors.failure_reason(error), errors.failure_reason_en(error)) if error else (None, None)
        return value

    def get(self, identity, *, since=None, wait=0, compact=True):
        schema.validate(schema.drama_schema('get'), dict(wait=wait, **({'since': since} if since is not None else {})))
        deadline = time.monotonic() + min(wait, MAX_WAIT)
        with self.changed:
            while self._record(identity)['state']['revision'] == since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.changed.wait(remaining)
            return self._reply(identity, since, compact)

    def _reply(self, identity, since, compact):
        record = self._record(identity)
        changed = record['state']['revision'] != since
        if not changed and compact:
            progress = self._progress(record)
            value = dict(id=identity, revision=since, changed=False, status=record['state']['status'],
                        paused=deepcopy(record['state'].get('paused')), fraction=progress['fraction'],
                        elapsed_seconds=progress['elapsed_seconds'])
            state = record['state']
            join = next((j for j in state['joins'] if j['ref'] == state['current_join']), None)
            error, _ = failure(dict(stalled=record.get('stalled'), join=join, shots=state['shots']))
            if error:
                value.update(errors.reasons(error))
            return value
        return dict(self._view(record), changed=changed)

    async def get_async(self, identity, *, since=None, wait=0, compact=True):
        """Long wait on the event loop: no thread is held, and callers over the cap get the current state."""
        schema.validate(schema.drama_schema('get'), dict(wait=wait, **({'since': since} if since is not None else {})))
        def read():
            return asyncio.to_thread(self.get, identity, since=since, wait=0, compact=compact)
        wait = min(wait, MAX_WAIT)
        if since is None or wait <= 0:
            return await read()
        loop = asyncio.get_running_loop()
        slot = (loop, asyncio.Event())
        with self._waiter_lock:
            if sum(len(row) for row in self._waiters.values()) >= MAX_WAITERS:
                slot = None
            else:
                self._waiters.setdefault(identity, set()).add(slot)
        if slot is None:
            return await read()
        try:
            deadline = loop.time() + wait
            while True:
                slot[1].clear()  # A save after this read sets it again.
                result = await read()
                left = deadline - loop.time()
                if result['changed'] or left <= 0:
                    return result
                try:
                    await asyncio.wait_for(slot[1].wait(), left)
                except asyncio.TimeoutError:
                    pass
        finally:
            with self._waiter_lock:
                self._waiters.get(identity, set()).discard(slot)
                if not self._waiters.get(identity, True):
                    del self._waiters[identity]

    def list(self, *, status='all', limit=50):
        with self.changed:
            rows = sorted(self.records.values(), key=lambda r: (r['state']['updated_at'], r['state']['id']), reverse=True)
            if status == 'active':
                rows = [r for r in rows if r['state']['status'] not in ('finished', 'cancelled')]
            result = []
            for record in rows[:limit]:
                view = self._view(record)
                shot = next((s for s in view['shots'] if s['chosen'] is None), view['shots'][-1])
                result.append(dict(id=view['id'], title=view['title'], status=view['status'], revision=view['revision'],
                    shot=shot['index'], shots=len(view['shots']), next=view['todo'][0] if view['todo'] else None,
                    paused=view['paused'], stalled=view['stalled'], session=record.get('session'), error_zh=view['error_zh'], error_en=view['error_en']))
            return result

    def _mutate(self, action, identity, *, by, ref=None, client=None, **values):
        with self.changed:
            record = self._record(identity)
            if action in ('choose', 'reshoot', 'finish'):
                _domain(production._editable, record['state'], by)
            if action in ('pause', 'resume') and by != 'user':
                _domain(production._pause_guard, record['state'], by)
            digest, replay = self._receipt(identity + ':' + action, dict(by=by, **values), ref, client)
            if replay is not None:
                return replay
            draft = deepcopy(record)
            state = draft['state']
            _domain(getattr(production, action), state, by=by, now=iso(self.jobs.clock()), **values)
            # The next advance is part of this operation: if it cannot run, nothing changes.
            self._preflight(record, draft)
            self._prepare_estimates(draft)
            result = self._view(draft)
            if action == 'reshoot':
                previous = {t['job'] for _, t in self._takes(record['state'])}
                estimates = [draft['estimates'].get(t['job'], {}).get('seconds')
                             for s, t in self._takes(state) if s['id'] == values['shot'] and t['job'] not in previous]
                known = bool(estimates) and all(e is not None for e in estimates)
                if known and sum(estimates) >= 600:
                    result['over_budget'] = dict(reason='reshoot', shot=values['shot'], estimate_seconds=sum(estimates))
                if self.gate is not None:
                    shot = next(s for s in state['shots'] if s['id'] == values['shot'])
                    self.gate('reshoot', by=by, estimate_seconds=sum(estimates) if known else None,
                              refs=dict(drama=identity, shot=shot['id']), shape=self._shape(state, shot['id']),
                              question='Reshoot shot %d of %s?' % (shot['index'], _quoted(state['title'])))
            if ref:
                draft['receipts'].append(dict(key=ref, digest=digest, result=result,
                                              **({'client': client} if client else {})))
            self._save(draft)
            self._run(identity)
            return deepcopy(result)

    def choose(self, identity, shot, take, *, by='agent', ref=None, **kwargs):
        return self._mutate('choose', identity, shot=shot, take=take, by=by, ref=ref, **kwargs)

    def reshoot(self, identity, shot, *, by='agent', ref=None, **kwargs):
        return self._mutate('reshoot', identity, shot=shot, by=by, ref=ref, **kwargs)

    def finish(self, identity, *, by='agent', ref=None, **kwargs):
        return self._mutate('finish', identity, by=by, ref=ref, **kwargs)

    def cancel(self, identity, *, by='agent', ref=None, client=None):
        return self._mutate('cancel', identity, by=by, ref=ref, client=client)

    def pause(self, identity, *, by, ref=None, client=None):
        return self._mutate('pause', identity, by=by, ref=ref, client=client)

    def resume(self, identity, *, by, ref=None, client=None):
        return self._mutate('resume', identity, by=by, ref=ref, client=client)

    def pause_session(self, session):
        paused = []
        with self.changed:
            for identity, record in list(self.records.items()):
                state = record['state']
                if (record.get('session') == session and not state.get('paused') and not state['cancelled']
                        and state['status'] not in ('finished', 'cancelled')):
                    try:
                        self.pause(identity, by='user')
                        paused.append(identity)
                    except JobError:
                        pass
        return paused

    def tick(self):
        with self.changed:
            for identity in list(self.records):
                self._run(identity)

    def _bump(self, draft):
        draft['state']['revision'] += 1
        draft['state']['updated_at'] = iso(self.jobs.clock())

    def _run(self, identity):
        """Advance one film; a failure stalls only that film and is retried on the next tick."""
        try:
            self._advance(identity)
        except Exception as error:  # noqa: BLE001 - one film must never stop the others
            logging.getLogger(__name__).warning('A film could not advance (%s); other films continue',
                                                type(error).__name__)
            stalled = self._stalled(error)
            record = self._record(identity)
            if (record.get('stalled') or {}).get('code') != stalled['code']:
                draft = deepcopy(record)
                draft['stalled'] = stalled
                self._bump(draft)
                self._save(draft)
        else:
            record = self._record(identity)
            if record.get('stalled'):
                draft = deepcopy(record)
                del draft['stalled']
                self._bump(draft)
                self._save(draft)

    def _interrupted(self, identity):
        """A submission marked before a restart whose child Jobs never recorded: accepted or not is unknown."""
        record = self._record(identity)
        children = self._jobs(identity)
        lost = [r for r in record.get('submitting', []) if r not in children and r not in record['children']]
        if not lost:
            return
        draft = deepcopy(record)
        for ref in lost:
            draft['children'][ref] = dict(status='failed', result=None, error=errors.build('submit_interrupted'))
        marked = [r for r in draft['submitting'] if r not in lost]
        if marked:
            draft['submitting'] = marked
        else:
            draft.pop('submitting', None)
        self._save(draft)

    def _advance(self, identity):
        self._interrupted(identity)
        record = self._record(identity)
        draft = deepcopy(record)
        actions = production.advance(draft['state'], self._children(record), now=iso(self.jobs.clock()))
        self._prepare_estimates(draft)
        if draft != record:
            self._save(draft)
        # Terminal films may still have unacknowledged cancels or older joins.
        for action in actions:
            self._execute(identity, action)
        record = self._record(identity)
        draft = deepcopy(record)
        tasks = {ref: row['id'] for ref, row in self._jobs(identity).items()}
        if tasks != record.get('tasks', {}):
            draft['tasks'] = tasks
            # Task links are an executor-owned part of the public board, so
            # they also wake since/revision readers even before jobs run.
            self._bump(draft)
        # A submission marker is only needed until the child or its rejection receipt exists.
        marked = [r for r in record.get('submitting', []) if r not in tasks and r not in record['children']]
        if marked != record.get('submitting', []):
            if marked:
                draft['submitting'] = marked
            else:
                draft.pop('submitting', None)
        over_budget = self._elapsed_budget(self._progress(draft)) is not None
        if over_budget != record.get('elapsed_over_budget', False):
            draft['elapsed_over_budget'] = over_budget
            self._bump(draft)  # Wake since/wait readers when the elapsed reminder appears or clears.
        if draft != record:
            self._save(draft)

    def _execute(self, identity, action):
        """Carry out one action; True when it changed a child, a receipt or a tombstone."""
        record = self._record(identity)
        children = self._jobs(identity)
        if 'cancel' in action:
            ref = action['cancel']
            changed = False
            if ref not in record['children']:
                draft = deepcopy(record)
                draft['children'][ref] = dict(status='cancelled', result=None, error=None)
                self._save(draft)  # tombstone before calling Jobs, even with no child
                changed = True
            child = children.get(ref)
            if child and child['status'] not in DONE:
                before = child['status']
                self.jobs.cancel(child['id'])
                changed = changed or child['status'] != before
            return changed
        item = deepcopy(action.get('submit') or action['join'])
        ref = item.pop('ref')
        if ref in children or ref in record['children']:
            return False
        if ref in record.get('submitting', ()):
            # Marked in an earlier run, but Jobs has no such child: whether it was accepted is unknown.
            failure = errors.build('submit_interrupted')
        else:
            try:
                if len(set(children) | set(record['children'])) >= MAX_CHILDREN:
                    raise JobError('production_limit', 'Too many child jobs')
                if 'join' in action:
                    item = dict(kind='assemble', clips=[children[r]['id'] for r in item['takes']],
                                transition=item['transition'])
                else:
                    take = next(t for s in record['state']['shots'] for t in s['takes'] if t['job'] == ref)
                    if take['generation'] > MAX_GENERATION:
                        raise JobError('reshoot_limit', 'Too many generations')
                    if item.get('continue_from'):
                        source = children[item['continue_from']['ref']]
                        item['continue_from'] = dict(from_job=source['id'])
                    resolved = self._cast(record['state'], take, item)
                spec.check(item, local=True)  # The same policy as Jobs.submit for in-process callers.
                marker = deepcopy(self._record(identity))
                marker['submitting'] = list(marker.get('submitting', [])) + [ref]
                if 'submit' in action:
                    shot = next(s for s in marker['state']['shots'] if any(t['job'] == ref for t in s['takes']))
                    marker.setdefault('estimates', {})[ref] = self._take_estimate(marker['state'], shot, take, item)
                else:
                    try:
                        marker['join_estimate'] = self._join_estimate(marker['state'])
                    except Exception:
                        marker['join_estimate'] = dict(seconds=None, basis=None, reason='unavailable')
                if 'submit' in action and resolved['cast_reference'] is not None:
                    # The board shows which revision of each card this take used.
                    saved = next(t for s in marker['state']['shots'] for t in s['takes'] if t['job'] == ref)
                    saved.update(cast=dict(resolved['cards']), cast_reference=resolved['cast_reference'],
                                 cast_omitted=list(resolved['omitted']),
                                 cast_voice_omitted=list(resolved['voice_omitted']),
                                 cast_missing=list(resolved['missing']),
                                 cast_pictures_each=pictures_each(resolved))
                self._save(marker)  # Durable before Jobs may accept it.
                self.jobs.submit(item, client='production:' + identity, parent=dict(production=identity, ref=ref))
            except spec.SpecError as error:
                failure = errors.validation(error.errors)
            except JobError as error:
                failure = error.view()
            else:
                return True
        draft = deepcopy(self._record(identity))
        draft['children'][ref] = dict(status='failed', result=None, error=failure)
        marked = [r for r in draft.get('submitting', []) if r != ref]
        if marked:
            draft['submitting'] = marked
        else:
            draft.pop('submitting', None)
        self._save(draft)
        return True

    def _cast(self, state, take, item):
        """Read the cast cards now, at submission: later takes and reshoots use the cards as they are then."""
        play = state['screenplay']
        shot = next(s for s in state['shots'] if any(t['job'] == take['job'] for t in s['takes']))
        resolved = self._carry(play, play['shots'][shot['index'] - 1], self._current(play), item,
                               continues=shot['continues'])
        if resolved['cast_reference'] is None:
            return resolved
        if resolved['references']:
            item['media'] = dict(references=deepcopy(resolved['references']))
            item['prompt'] = drama.with_cast(item['prompt'], resolved)
        item['metadata'] = dict(item.get('metadata') or {}, cast=dict(resolved['cards']),
                                cast_reference=resolved['cast_reference'])
        return resolved

    def _carry(self, play, shot, cards, item, *, continues):
        """The cast a shot carries: every picture of each person (the sheet and face of one person, or one picture a
        person, when the shot continues). Plan previews and submissions both come here, so they agree."""
        return drama.cast_references(play, shot, cards, continues=continues)

    def _current(self, play):
        """Cards as they are now; a card deleted since the film started is None (the shot uses text)."""
        cast = getattr(self.jobs, 'cast', None)
        identities = {p['card'] for p in (play.get('characters') or {}).values()
                      if isinstance(p, dict) and isinstance(p.get('card'), str)}
        return {i: cast.snapshot(i) if cast is not None else None for i in identities}

    def sheet(self, identity, shot, *, frames=None):
        """Return bytes in process; HTTP converts these to inline image content.

        Without a count each take shows one frame per second: H3 may caption a spoken line for only a
        second or so, which a sparser sheet steps over.
        """
        from . import media
        if frames is not None:
            schema.validate(schema.drama_schema('sheet'), dict(frames=frames))
        with self.changed:
            record = self._record(identity)
            row = _domain(production._shot, record['state'], shot)
            takes = [(t['take'], t['library_id'], t.get('seconds')) for t in row['takes']
                     if t['state'] == 'succeeded' and not t['stale']]
            parent = production._parent(record['state'], row)
            previous = parent['library_id'] if parent else None
        result = dict(takes=[], previous=None)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.jobs.cpu, tempfile.TemporaryDirectory(dir=self.directory) as folder:
            for number, library, seconds in takes:
                if not library:
                    raise JobError('no_video', 'Take has no saved video')
                output = Path(folder) / ('take-%d.jpg' % number)  # media never overwrites a finished file
                count = frames or min(SHEET_MOST, max(SHEET_LEAST, math.ceil(seconds or 0)))
                media.contact_sheet(self.jobs.port.library_video(library), output, count=count, columns=min(4, count))
                result['takes'].append(dict(take=number, image=output.read_bytes()))
            if previous:
                output = Path(folder) / 'previous.png'
                self.jobs.port.extract_frame(self.jobs.port.library_video(previous), output, 'last')
                result['previous'] = output.read_bytes()
        return result

    def adapter(self):
        return DirectorAdapter(self)


class DirectorAdapter:
    """Synchronous in-process operations for builtin_director.run.

    Unlike compact HTTP timeouts, the director needs a complete board on every
    get. Both representations share the same wait and durable operations.
    """
    def __init__(self, productions):
        self.productions = productions

    def get(self, identity, *, since=None, wait=0):
        return self.productions.get(identity, since=since, wait=wait, compact=False)

    def __getattr__(self, name):
        if name in ('plan', 'start', 'sheet', 'choose', 'reshoot', 'finish', 'cancel', 'pause', 'resume'):
            return getattr(self.productions, name)
        raise AttributeError(name)
