"""Durable production decisions, without storage, clocks, queues or model calls.

Mutators are atomic in memory and increment revision once per actual change.
Persist their state before executing advance()'s actions. The executor must
atomically deduplicate *all* actions by ref (including cancellation tombstones)
and supply its authoritative children snapshot when recovering. Missing children
cause the same saved submit/join to be replayed, never a new take.
Paused attempts retain cancellation receipts across resume; their replacements
keep the take and seed but use a new, durably reserved submission ref.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from . import drama, spec

ACTIVE = ('waiting', 'queued', 'running', 'cancelling')
TERMINAL = ('succeeded', 'failed', 'cancelled', 'interrupted')
ACTORS = ('agent', 'builtin', 'user')
CHANGES = ('action', 'lines', 'camera', 'framing', 'prompt', 'seconds')
NEXT = {
    'invalid_screenplay': 'Fix every listed problem and retry the request.',
    'unknown_shot': 'Use a shot id from the production view.',
    'unknown_take': 'Use a take number from this shot in the production view.',
    'take_not_ready': 'Choose a succeeded, non-stale take, or reshoot this shot.',
    'chosen_by_user': 'Leave this shot unchanged; only the user may change it.',
    'not_ready': 'Resolve each listed shot, then retry finish.',
    'nothing_to_cancel': 'Choose completed takes, reshoot a shot, or finish the production.',
    'not_allowed': 'Only the user may stop or continue a production.',
    'already_ended': 'Use an unfinished, uncancelled production.',
    'stopped_by_user': 'Wait until the user continues the production.',
}


class ProductionError(ValueError):
    """Stable machine code plus an English explanation and actionable next step."""
    def __init__(self, code, message, *, details=None, problems=None):
        self.code, self.message, self.next = code, message, NEXT[code]
        self.details = details if details is not None else {}
        self.problems = problems if problems is not None else []
        super().__init__(message)


def _invalid(problems):
    if problems:
        raise ProductionError('invalid_screenplay', problems[0]['message'], problems=problems,
                              details={'problems': problems})


def _count(value, path, problems):
    if type(value) is not int or not 1 <= value <= 8:
        problems.append(dict(path=path, message='must be an integer from 1 to 8'))
        return False
    return True


def _json(value, problems):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        problems.append(dict(path='', message='must contain only finite JSON values'))


def _compiled(play, shot):
    # The pure route has no enhance job: an idea is literal action text.
    shot = deepcopy(shot)
    if not shot.get('action') and not shot.get('prompt'):
        shot['action'] = shot.get('idea', '')
    return drama.prompt(play, shot)


# What a screenplay shot and a line may say; anything else would be ignored without a word.
SHOT_FIELDS = ('id', 'seconds', 'action', 'idea', 'prompt', 'camera', 'framing', 'sound', 'music', 'takes', 'seed',
               'continue', 'first', 'references', 'cast', 'lines')
LINE_FIELDS = ('who', 'line', 'how', 'language')


def _rule(row):
    """A screenplay problem as a rule about its field; the pointer already travels in path.

    A leading pointer in the text would read as a local path once paths are removed."""
    if isinstance(row.get('allowed'), str):
        rule = row['allowed']
        return rule if rule.startswith('must') else 'must be ' + rule
    return re.sub(r'^/\S*\s+', '', row['message']) if isinstance(row.get('message'), str) else 'is not valid'


def _fields(play, problems):
    for i, shot in enumerate(play.get('shots') or [] if isinstance(play, dict) else []):
        if not isinstance(shot, dict):
            continue
        rows = [('/screenplay/shots/%d/' % i, key, SHOT_FIELDS) for key in shot if key not in SHOT_FIELDS]
        for j, line in enumerate(shot.get('lines') if isinstance(shot.get('lines'), list) else []):
            if isinstance(line, dict):
                rows += [('/screenplay/shots/%d/lines/%d/' % (i, j), key, LINE_FIELDS)
                         for key in line if key not in LINE_FIELDS]
        for prefix, key, known in rows:
            name = str(key).replace('~', '~0').replace('/', '~1')
            problems.append(dict(path=prefix + name, message='is not a field here; use ' + ', '.join(known)))


def plan(request, *, fields=True):
    """Return {'request': normalized copy, 'preview': {'shots': [...], 'total': N}}.

    fields=False rechecks a saved screenplay, which may predate the unknown-field rule."""
    problems = []
    if not isinstance(request, dict):
        _invalid([dict(path='', message='A production request must be an object')])
    _json(request, problems)
    play = deepcopy(request.get('screenplay'))
    play_ok = True
    try:
        drama.check(play)
    except drama.Screenplay as error:
        play_ok = False
        problems.extend(dict(path='/screenplay' + p['path'], message=_rule(p)) for p in error.problems)
    if fields:
        _fields(play, problems)
    director = request.get('director', 'agent')
    if director not in ('agent', 'builtin'):
        problems.append(dict(path='/director', message='must be "agent" or "builtin"'))
    # The built-in director judges takes as they arrive and withdraws the rest once one is chosen, so two cost little.
    count = request.get('takes_per_shot', 2 if director == 'builtin' else 1)
    count_ok = _count(count, '/takes_per_shot', problems)
    rows = play.get('shots', []) if isinstance(play, dict) else []
    total = 0
    if isinstance(rows, list):
        for i, shot in enumerate(rows):
            if not isinstance(shot, dict):
                continue
            n = shot.get('takes', count if count_ok else 0)
            if type(n) is int and 1 <= n <= 8:
                total += n
                if type(shot.get('seed')) is int and shot['seed'] + n > spec.MAX_SEED:
                    problems.append(dict(path='/screenplay/shots/%d/seed' % i,
                                         message='seed plus the take number exceeds the maximum seed'))
    if total > 120:
        problems.append(dict(path='/screenplay/shots', message='The total number of takes must be at most 120'))
    preview = []
    if play_ok:
        for i, shot in enumerate(rows):
            text = _compiled(play, shot)
            label = '%s · %s' % (play.get('title') or 'drama', shot['id'])
            for value, path, limit in ((text, '/screenplay/shots/%d/prompt' % i, 20000),
                                       (label, '/screenplay/title', 120)):
                try:
                    spec._text(value, path, limit)
                except spec.SpecError as error:
                    problems.extend(error.errors)
            preview.append(dict(id=shot['id'], continues=shot.get('continue', False),
                                takes=shot.get('takes', count), prompt=text))
    _invalid(problems)
    notice = {}
    if play_ok and play.get('music'):
        notice['notice'] = ('screenplay.music is not written into the shots. Music for the whole film is composed or '
                            'provided as an audio file, imported, and laid on one sound track after the cut is joined.')
    return dict(request=dict(screenplay=play, takes_per_shot=count, director=director),
                preview=dict(shots=preview, total=total, **notice))


def corrected_prompt(play, shot, note=None):
    """Compile a per-generation correction without changing the screenplay."""
    if not note:
        return _compiled(play, shot)
    correction = 'Correction for this take: ' + note
    if shot.get('prompt'):
        text = shot['prompt']
        # Insert at the end of the visual section, before either audio section.
        visual = re.search(r'(?m)^\s*integrated_multimodal_description\s*:', text)
        start = visual.end() if visual else 0
        end = re.search(r'(?m)^\s*(?:overall_soundscape|non_diegetic_music)\s*:', text[start:])
        stop = start + end.start() if end else len(text)
        return text[:stop].rstrip() + ' ' + correction + ('\n\n' + text[stop:].lstrip() if end else '')
    shot = deepcopy(shot)
    # A unique marker prevents cast name expansion from rewriting the note.
    marker = '\x00'
    shot['action'] = (shot.get('action') or shot.get('idea') or '').rstrip() + ' ' + marker
    return drama.prompt(play, shot).replace(marker, correction)


def _actor(by):
    if by not in ACTORS:
        _invalid([dict(path='/by', message='must be "agent", "builtin" or "user"')])


def _editable(state, by):
    _actor(by)
    if state.get('paused') and by != 'user':
        raise ProductionError('stopped_by_user',
                              'The user stopped this film. It cannot be changed until the user continues.')


def _pause_guard(state, by):
    if by != 'user':
        raise ProductionError('not_allowed', 'Only the user may stop or continue this film.')
    if state['cancelled'] or state['status'] == 'finished':
        raise ProductionError('already_ended', 'This film has already ended.')


def _optional_text(value, path):
    if value is not None:
        try:
            spec._text(value, path, 20000)
        except spec.SpecError as error:
            _invalid(error.errors)


def _decision(state, now, by, action, shot=None, take=None, reason=None):
    state['decisions'].append(dict(at=now, by=by, action=action, shot=shot, take=take, reason=reason))


def _shot(state, identity):
    for row in state['shots']:
        if row['id'] == identity:
            return row
    raise ProductionError('unknown_shot', 'The shot does not exist.', details={'shot': identity})


def _take(shot, number):
    for row in shot['takes']:
        if type(number) is int and row['take'] == number:
            return row
    raise ProductionError('unknown_take', 'The take does not exist in this shot.',
                          details={'shot': shot['id'], 'take': number})


def _chosen(shot):
    return next((t for t in shot['takes'] if t['take'] == shot['chosen']
                 and t['state'] == 'succeeded' and not t['stale']), None)


def _parent(state, shot):
    if shot['continues']:
        return _chosen(state['shots'][shot['index'] - 2])
    return None


def _guard(shot, by):
    if shot['chosen_by'] == 'user' and by != 'user':
        raise ProductionError('chosen_by_user', 'This shot was chosen by the user.', details={'shot': shot['id']})


def _reserve(state, shot, jobs):
    """Assign a generation's durable refs without ever reusing an issued ref."""
    shot['generation'] += 1
    for number, take in enumerate(jobs, shot['next_take']):
        seed = shot['base_seed'] + 1000 * shot['generation'] + number
        if seed > spec.MAX_SEED:
            _invalid([dict(path='/seed', message='This generation exceeds the maximum seed')])
        ref = '%s:s%d:g%d:t%d' % (state['id'], shot['index'], shot['generation'], number)
        take.update(take=number, job=ref, generation=shot['generation'], seed=seed)
        take['spec'].update(ref=ref, seed=seed)
        take['spec']['metadata']['take'] = number
    shot['next_take'] += len(jobs)


def _schedule(state, shot, count, by, note=None, *, automatic=False):
    """Save immutable templates; automatic reconnects reserve refs at advance()."""
    source = state['screenplay']['shots'][shot['index'] - 1]
    text = corrected_prompt(state['screenplay'], source, note)
    try:
        spec._text(text, '/prompt', 20000)
    except spec.SpecError as error:
        _invalid(error.errors)
    jobs = []
    for _ in range(count):
        job = drama.shot_job(state['screenplay'], source, shot['index'] - 1, None)
        job.update(prompt=text, metadata=dict(drama=state['id'], shot=shot['id']))
        job.pop('media', None)
        if job['project'] is None:
            job.pop('project')
        if source.get('first') is not None and not shot['continues']:
            job['media'] = {'first': deepcopy(source['first'])}
        elif source.get('references'):
            job['media'] = {'references': deepcopy(source['references'])}
        jobs.append(dict(take=None, job=None, generation=None, seed=None, state='queued', automatic=automatic,
                         stale=False, continues_take=None, note=note, by=by, library_id=None, seconds=None,
                         cancel_requested=False, error=None, spec=job))
    if not automatic:
        _reserve(state, shot, jobs)
    shot['pending'].append(jobs)


def _launch_ready(state, now, *, automatic=False):
    if state['cancelled'] or state.get('paused'):
        return
    for shot in state['shots']:
        parent = _parent(state, shot)
        if shot['continues'] and parent is None:
            continue
        waiting = []
        for generation in shot['pending']:
            if generation[0].get('automatic') and not automatic:
                waiting.append(generation)
                continue
            if generation[0]['job'] is None:
                _reserve(state, shot, generation)
            for take in generation:
                if parent:
                    take['continues_take'] = parent['job']
                    take['spec']['continue_from'] = {'ref': parent['job']}
                shot['takes'].append(take)
                _decision(state, now, take['by'], 'shot', shot['id'], take['take'])
        shot['pending'] = waiting


def _usable(shot):
    # A cancellation may already have reached the executor; never withdraw it.
    # A success that wins that race remains usable, regardless of the request.
    return any(not t['stale'] and (t['state'] == 'succeeded' or
               (t['state'] in ACTIVE + ('paused',) and not t['cancel_requested'])) for t in shot['takes'])


def _remember(shot):
    chosen = next((t for t in shot['takes'] if t['take'] == shot['chosen']), None)
    if chosen and chosen['continues_take']:
        shot.setdefault('choices', {})[chosen['continues_take']] = dict(take=chosen['take'], by=shot['chosen_by'])


def _drop_automatic(shot):
    shot['pending'] = [g for g in shot['pending'] if not g[0].get('automatic')]


def _reconnect(state, shot):
    """Derive validity and restore the choice made under this exact parent job."""
    if not shot['continues']:
        return
    _remember(shot)
    parent = _parent(state, shot)
    for take in shot['takes']:
        take['stale'] = bool(take['continues_take'] and
                             (parent is None or take['continues_take'] != parent['job']))
        if take['stale'] and take['state'] in ACTIVE + ('paused',):
            take['cancel_requested'] = True
    if not _chosen(shot):
        shot['chosen'] = shot['chosen_by'] = None
        choice = shot.get('choices', {}).get(parent['job']) if parent else None
        if choice:
            take = _take(shot, choice['take'])
            if take['state'] == 'succeeded' and not take['stale']:
                shot['chosen'], shot['chosen_by'] = take['take'], choice['by']
    if _usable(shot):
        _drop_automatic(shot)


def _reconnect_after(state, shot, by, now, cascade):
    for child in state['shots'][shot['index']:]:
        if not child['continues']:
            break
        _reconnect(state, child)
        if not cascade:
            _drop_automatic(child)
        elif child['takes'] and not _usable(child) and not child['pending']:
            # No bound takes means there is nothing to reconnect (for example,
            # the user cancelled the original, still-unstarted generation).
            _schedule(state, child, child['default_takes'], by, automatic=True)
            _decision(state, now, by, 'restale', child['id'], reason='The preceding chosen take changed.')


def _refresh(state):
    for shot in state['shots']:
        _reconnect(state, shot)
        usable = [t for t in shot['takes'] if not t['stale']]
        if _chosen(shot):
            shot['state'] = 'chosen'
        elif any(t['state'] in ACTIVE and not t['cancel_requested'] for t in usable):
            shot['state'] = 'shooting'
        elif any(t['state'] == 'succeeded' for t in usable):
            shot['state'] = 'needs_choice'
        elif any(t['state'] == 'paused' and not t['cancel_requested'] for t in usable):
            shot['state'] = 'paused'
        elif shot['pending']:
            shot['state'] = 'waiting' if shot['continues'] and _parent(state, shot) is None else 'shooting'
        else:
            shot['state'] = 'failed'
    current = next((j for j in state['joins'] if j['ref'] == state['current_join']), None)
    if state['cancelled']:
        state['status'] = 'cancelled'
    elif state.get('paused'):
        state['status'] = 'paused'
    elif current and current['state'] in ACTIVE:
        state['status'] = 'joining'
    elif current and current['state'] == 'succeeded':
        state['status'] = 'finished'
    elif any(s['state'] == 'shooting' for s in state['shots']) or any(
             t['state'] in ACTIVE and not t['stale'] and not t['cancel_requested']
             for s in state['shots'] for t in s['takes']):
        state['status'] = 'shooting'
    elif all(_chosen(s) for s in state['shots']):
        state['status'] = 'ready'
    else:
        state['status'] = 'needs_choice'


def _commit(state, draft, now):
    _refresh(draft)
    if draft != state:
        draft['revision'] = state['revision'] + 1
        draft['updated_at'] = now
        state.clear()
        state.update(draft)


def new(identity, request, *, now):
    """Create a JSON receipt. Shot indices start at 1; generations start at 0."""
    request = plan(request)['request']
    if not isinstance(identity, str) or not spec.SLUG.fullmatch(identity):
        _invalid([dict(path='/id', message='Production id must be a unique short name')])
    state = dict(version=1, id=identity, title=request['screenplay'].get('title'), revision=1,
                 status='shooting', director=request['director'], created_at=now, updated_at=now,
                 screenplay=request['screenplay'], takes_per_shot=request['takes_per_shot'], shots=[],
                 decisions=[], outputs=[], joins=[], current_join=None, cancelled=False, paused=None)
    base = int.from_bytes(hashlib.sha256(identity.encode('utf-8')).digest()[:4], 'big')
    _decision(state, now, request['director'], 'started')
    for i, source in enumerate(state['screenplay']['shots'], 1):
        shot = dict(id=source['id'], index=i, continues=source.get('continue', False), state='waiting',
                    chosen=None, chosen_by=None, choices={}, takes=[], pending=[], generation=-1, next_take=1,
                    default_takes=source.get('takes', state['takes_per_shot']), base_seed=source.get('seed', base))
        state['shots'].append(shot)
        _schedule(state, shot, shot['default_takes'], request['director'])
    _launch_ready(state, now)
    _refresh(state)
    return state


def _observe(row, child):
    if row['state'] in TERMINAL or child is None:
        return
    status = child.get('status')
    if status not in ACTIVE + TERMINAL:
        return
    row['state'] = status
    if status in TERMINAL:
        # Whitelist result data: no filesystem path can leak from a job result.
        result = child.get('result') or {}
        library = result.get('library_id')
        row['library_id'] = library if isinstance(library, str) and spec.LIBRARY_ID.fullmatch(library) else None
        seconds = result.get('seconds')
        row['seconds'] = seconds if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0 else None
        row['error'] = deepcopy(child.get('error'))


def advance(state, children, *, now):
    """Observe children, update the receipt, and return replayable executor actions."""
    draft = deepcopy(state)
    for shot in draft['shots']:
        for take in shot['takes']:
            for stopped in take.get('stopped', []):
                _observe(stopped, children.get(stopped['job']))
            if take.get('stopped') and take['job'] == take['stopped'][-1]['job']:
                stopped = take['stopped'][-1]
                # A success can win the cancellation race; other outcomes of a
                # stopped attempt never turn the take into a failed generation.
                if stopped['state'] == 'succeeded':
                    for key in ('state', 'library_id', 'seconds', 'error'):
                        take[key] = deepcopy(stopped[key])
            else:
                _observe(take, children.get(take['job']))
    for join in draft['joins']:
        previous = join['state']
        _observe(join, children.get(join['ref']))
        if previous != 'succeeded' and join['state'] == 'succeeded':
            draft['outputs'].append(dict(revision=join['revision'], job=join['ref'],
                                         library_id=join['library_id'], seconds=join['seconds']))
            _decision(draft, now, join['by'], 'finished')
    _refresh(draft)
    _launch_ready(draft, now, automatic=True)
    _commit(state, draft, now)
    actions = []
    for shot in state['shots']:
        for take in shot['takes']:
            for stopped in take.get('stopped', []):
                if stopped['state'] in ACTIVE:
                    actions.append({'cancel': stopped['job']})
            if take['state'] in TERMINAL + ('paused',):
                continue
            if take['cancel_requested'] or state.get('paused'):
                actions.append({'cancel': take['job']})
            elif take['job'] not in children:
                actions.append({'submit': deepcopy(take['spec'])})
    for join in state['joins']:
        if join['state'] not in ACTIVE:
            continue
        if join.get('cancel_requested'):
            actions.append({'cancel': join['ref']})
        elif not state.get('paused') and join['ref'] not in children:
            actions.append({'join': {key: deepcopy(join[key]) for key in ('ref', 'takes', 'transition')}})
    return actions


def pause(state, *, by, now):
    """Suspend unfinished takes, retaining their old refs for cancellation replay."""
    _pause_guard(state, by)
    if state.get('paused'):
        return
    draft = deepcopy(state)
    draft['paused'] = dict(at=now, by=by)
    for shot in draft['shots']:
        for take in shot['takes']:
            if (take['state'] in ('waiting', 'queued', 'running') and
                    not take['stale'] and not take['cancel_requested']):
                # Keep executor outcomes separate from the logical take. The
                # take's cancel_requested remains reserved for other causes.
                stopped = {key: deepcopy(take[key]) for key in ('job', 'state', 'library_id', 'seconds', 'error')}
                take.setdefault('stopped', []).append(stopped)
                take['state'] = 'paused'
    _decision(draft, now, by, 'paused')
    _commit(state, draft, now)


def resume(state, *, by, now):
    """Retry only stop-suspended takes, without consuming a take or generation."""
    _pause_guard(state, by)
    if not state.get('paused'):
        return
    draft = deepcopy(state)
    draft['paused'] = None
    _refresh(draft)
    for shot in draft['shots']:
        for take in shot['takes']:
            if take['state'] != 'paused' or take['stale'] or take['cancel_requested']:
                continue
            ref = '%s:s%d:g%d:t%d:a%d' % (draft['id'], shot['index'], take['generation'],
                                         take['take'], len(take['stopped']) + 1)
            take.update(job=ref, state='queued', library_id=None, seconds=None, error=None)
            take['spec']['ref'] = ref
    _decision(draft, now, by, 'resumed')
    _launch_ready(draft, now)
    _commit(state, draft, now)


def _withdraw_others(row, chosen):
    """Stop the shot's other unfinished takes and unsent manual batches once one is chosen.

    Finished takes stay, so the choice can still change; automatic reconnects are not ours to drop.
    """
    for other in row['takes']:
        if other['take'] == chosen or other['stale'] or other['cancel_requested']:
            continue
        if other['state'] == 'paused':
            other['state'] = 'cancelled'
            other['cancel_requested'] = True
        elif other['state'] in ACTIVE:
            other['cancel_requested'] = True
    row['pending'] = [g for g in row['pending'] if g[0].get('automatic')]


def choose(state, shot, take, *, by, reason=None, cascade=True, now):
    """Choose a current take, optionally reconnecting descendants automatically."""
    _editable(state, by)
    _optional_text(reason, '/reason')
    draft = deepcopy(state)
    row = _shot(draft, shot)
    if row['chosen'] != take:
        _guard(row, by)
    version = _take(row, take)
    if version['state'] != 'succeeded' or version['stale']:
        raise ProductionError('take_not_ready', 'The take is stale or has not succeeded.',
                              details=dict(shot=shot, take=take, state=version['state'], stale=version['stale'],
                                           why='stale' if version['stale'] else 'not_succeeded'))
    if row['chosen'] == take:
        if by == 'user' and row['chosen_by'] != 'user':
            row['chosen_by'] = by
            _decision(draft, now, by, 'chosen', shot, take, reason)
            _commit(state, draft, now)
        return
    row['chosen'], row['chosen_by'] = take, by
    _withdraw_others(row, take)
    _refresh(draft)
    if by != 'user':
        affected = [dict(shot=old['id']) for old, child in zip(state['shots'], draft['shots'])
                    if old['chosen_by'] == 'user' and _chosen(old) and
                    _take(child, old['chosen'])['stale']]
        if affected:
            raise ProductionError('chosen_by_user', 'This change would invalidate a shot chosen by the user.',
                                  details={'shots': affected})
    draft['cancelled'], draft['current_join'] = False, None
    _decision(draft, now, by, 'chosen', shot, take, reason)
    _reconnect_after(draft, row, by, now, cascade)
    _launch_ready(draft, now)
    _commit(state, draft, now)


def reshoot(state, shot, *, by, note=None, changes=None, takes=1, reason=None, now):
    """Reserve a fresh generation; keep all previous non-stale takes selectable."""
    _editable(state, by)
    draft = deepcopy(state)
    row = _shot(draft, shot)
    _guard(row, by)
    problems = []
    _count(takes, '/takes', problems)
    if changes is not None and not isinstance(changes, dict):
        problems.append(dict(path='/changes', message='must be an object'))
    elif changes:
        for key in changes:
            if key not in CHANGES:
                problems.append(dict(path='/changes/' + str(key), message='is not an allowed shot change'))
    _invalid(problems)
    _optional_text(note, '/note')
    _optional_text(reason, '/reason')
    source = draft['screenplay']['shots'][row['index'] - 1]
    source.update(deepcopy(changes or {}))
    # Recheck the complete effective screenplay, transactionally.
    plan(dict(screenplay=draft['screenplay'], takes_per_shot=draft['takes_per_shot'], director=draft['director']),
         fields=False)
    _schedule(draft, row, takes, by, note)
    draft['cancelled'], draft['current_join'] = False, None
    _decision(draft, now, by, 'reshoot', shot, reason=reason)
    _launch_ready(draft, now)
    _commit(state, draft, now)


def _missing(state):
    result = []
    for shot in state['shots']:
        if _chosen(shot):
            continue
        chosen = next((t for t in shot['takes'] if t['take'] == shot['chosen']), None)
        why = 'stale' if chosen and chosen['stale'] else 'not_succeeded' if chosen else shot['state']
        result.append(dict(shot=shot['id'], why=why))
    return result


def finish(state, *, by, transition=None, now):
    """Snapshot the chosen refs for a new join, retaining every previous output."""
    _editable(state, by)
    missing = _missing(state)
    if missing:
        raise ProductionError('not_ready', 'Every shot needs a succeeded, non-stale chosen take.',
                              details={'shots': missing})
    transition = transition if transition is not None else state['screenplay'].get('transition', 'cut')
    if transition not in ('cut', 'crossfade'):
        _invalid([dict(path='/transition', message='must be "cut" or "crossfade"')])
    refs = [_chosen(shot)['job'] for shot in state['shots']]
    current = next((j for j in state['joins'] if j['ref'] == state['current_join']), None)
    if current and current['state'] in ACTIVE and current['takes'] == refs and current['transition'] == transition:
        return
    draft = deepcopy(state)
    ref = '%s:join%d' % (state['id'], len(state['joins']) + 1)
    draft['joins'].append(dict(ref=ref, takes=refs, transition=transition, state='waiting', by=by,
                               revision=state['revision'] + 1, library_id=None, seconds=None, error=None))
    draft['current_join'], draft['cancelled'] = ref, False
    _commit(state, draft, now)


def cancel(state, *, by, now):
    """Stop unstarted generations and request cancellation of unfinished takes and joins."""
    _actor(by)
    draft = deepcopy(state)
    found = bool(draft.get('paused'))
    for shot in draft['shots']:
        if shot['pending']:
            found = True
            shot['pending'] = []
        for take in shot['takes']:
            if take['state'] == 'paused':
                take['state'] = 'cancelled'
                take['cancel_requested'] = True
                found = True
            if take['state'] in ACTIVE and not take['cancel_requested']:
                take['cancel_requested'] = True
                found = True
    for join in draft['joins']:
        # The final join stops too; advance cancels it (or tombstones it before it starts).
        if join['state'] in ACTIVE and not join.get('cancel_requested'):
            join['cancel_requested'] = True
            found = True
    if not found:
        raise ProductionError('nothing_to_cancel', 'There are no unfinished takes or joins to cancel.')
    draft['cancelled'] = True
    draft['paused'] = None
    draft['current_join'] = None
    _decision(draft, now, by, 'cancelled')
    _commit(state, draft, now)


def _todo(state):
    if state.get('paused'):
        return [dict(action='wait', why='The user stopped this film. Wait until the user continues.')]
    if state['status'] in ('finished', 'joining'):
        return [] if state['status'] == 'finished' else [dict(action='wait', why='The final video is joining.')]
    if all(_chosen(s) for s in state['shots']):
        return [dict(action='finish', why='Every shot has a chosen take.')]
    result = []
    for shot in state['shots']:
        if _chosen(shot):
            continue
        usable = [t for t in shot['takes'] if not t['stale']]
        if any(t['state'] == 'succeeded' for t in usable):
            action, why = 'choose', 'Choose a succeeded, non-stale take.'
        elif not _usable(shot) and not shot['pending'] and any(t['stale'] for t in shot['takes']):
            action, why = 'reshoot', 'The preceding shot changed; this shot no longer connects.'
        elif shot['state'] == 'failed':
            action, why = 'reshoot', 'No selectable take succeeded.'
        else:
            action = 'wait'
            why = 'Waiting for the preceding shot to be chosen.' if shot['state'] == 'waiting' else 'Takes are shooting.'
        result.append(dict(action=action, shot=shot['id'], why=why))
    return result


def _public(value):
    """Remove local paths even in free-form notes and screenplay media fields."""
    if isinstance(value, dict):
        return {_public(k): _public(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_public(v) for v in value]
    if isinstance(value, str):
        from ..diagnostics import ABSOLUTE_PATH
        # Keep prose slashes, URLs and bare roots. Once a path starts, use
        # the shared matcher, which includes spaces through the full filename.
        starts = re.compile(r'''(?<!\w)(?:[A-Za-z]:[\\/](?=[^\s"'<>|])|\\\\[^\\/\s"'<>]+[\\/])'''
                            r'''|(?<![^\W_])(?<![:/])/(?![/\s])[^/"'<>;|\r\n]+/(?![/\s])''')
        parts, cursor = [], 0
        for start in starts.finditer(value):
            if start.start() < cursor:
                continue
            found = ABSOLUTE_PATH.match(value[start.start():])
            if found is None:
                continue
            span = found.group().split(' | ', 1)[0].rstrip()
            parts.extend((value[cursor:start.start()], '[local path]'))
            cursor = start.start() + len(span)
        return ''.join(parts) + value[cursor:]
    return value


def view(state):
    """Return the path-free public board; internal job templates stay private."""
    result = {key: deepcopy(state[key]) for key in ('id', 'title', 'revision', 'status', 'director', 'created_at',
                                                   'updated_at', 'screenplay', 'decisions', 'outputs')}
    result['paused'] = deepcopy(state.get('paused'))
    public_states = {'waiting': 'queued', 'cancelling': 'running', 'interrupted': 'failed'}
    current = next((j for j in state['joins'] if j['ref'] == state['current_join']), None)
    result['join'] = (dict(state=public_states.get(current['state'], current['state']),
                           error=deepcopy(current['error'])) if current else None)
    result['shots'] = []
    for shot in state['shots']:
        row = {key: deepcopy(shot[key]) for key in ('id', 'index', 'continues', 'state', 'chosen', 'chosen_by')}
        # The cast of the latest submitted take: cards are read when a take is submitted.
        latest = next((t for t in reversed(shot['takes']) if 'cast_reference' in t), {})
        row.update(cast_reference=latest.get('cast_reference'), cast_omitted=deepcopy(latest.get('cast_omitted', [])),
                   cast_voice_omitted=deepcopy(latest.get('cast_voice_omitted', [])),
                   cast_missing=deepcopy(latest.get('cast_missing', [])),
                   cast_pictures_each=latest.get('cast_pictures_each'))
        row['takes'] = []
        for take in shot['takes']:
            item = {key: deepcopy(take[key]) for key in ('take', 'job', 'generation', 'seed', 'state', 'stale',
                                                        'continues_take', 'note', 'by', 'library_id', 'seconds')}
            item['state'] = public_states.get(item['state'], item['state'])
            item['cast'] = deepcopy(take.get('cast'))
            row['takes'].append(item)
        result['shots'].append(row)
    result['todo'] = _todo(state)
    return _public(result)
