"""Append-only work conversations, independent of HTTP and the model runtime."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time

from . import errors, schema
from .jobs import JobError
from .spec import SpecError

MAX_EVENT_BYTES = 16 * 1024


class SessionError(JobError):
    def view(self):
        if self.code == 'invalid_request':
            return errors.validation(self.details['details'])
        if self.code == 'not_found':
            return errors.build(self.code, kind='session')
        return super().view()


def _check(shape, value):
    try:
        return schema.validate(shape, value)
    except SpecError as error:
        raise SessionError('invalid_request', 'Invalid session fields', details=error.errors) from None


def _invalid(field, allowed):
    return SessionError('invalid_request', 'Invalid session event',
                        details=[schema.diagnostic(field, allowed, None)])


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _line(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


STAGE_SECONDS = 120


class Sessions:
    def __init__(self, root: Path, *, clock=time.time, max_events=20000):
        self.root = Path(root)
        self.clock = clock
        self.max_events = max_events
        self.lock = threading.Lock()
        self.on_stop = []  # HTTP invokes these after persisting a user stop.
        self._meta = {}
        self._unreadable = set()
        self._ids = {}
        self._clients = {}
        self._initial_seq = {}
        self._readers = {}
        self._user_seq = {}  # Last event the user wrote; unread() skips the file when nothing is newer.
        self._turn = {}  # [last message the user wrote, the one they gave up on]: a turn is given up until they speak again.
        self._stage = {}  # What the agent last said it was doing: (key, when). Not kept across restarts.
        # When each session began or its user last acted in it (spoke, answered), and the user's first words
        # there: the conversation the user is in, and what the page calls each one in its history.
        self._acted = {}
        self._said = {}
        self._titles = {}  # What the user named a conversation, over the first thing they said.
        self._chosen = set()
        self._waiters = {}
        self._partial = set()
        self._closing = set()  # Being deleted: no new work may start in them.
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    def _path(self, sid):
        _check(schema.obj(dict(id=schema.SESSION), ('id',)), dict(id=sid))
        return self.root / (sid + '.jsonl')

    def _get(self, sid):
        self._path(sid)
        if sid not in self._meta:
            raise SessionError('not_found', 'Session does not exist')
        return self._meta[sid]

    def _load(self):
        try:
            paths = sorted(self.root.glob('s*.jsonl'))
        except OSError:
            logging.getLogger(__name__).warning('Could not list saved sessions; starting without them')
            return
        for path in paths:
            sid = path.stem
            if schema.issues(schema.SESSION, sid):
                continue
            try:
                if path.is_symlink():
                    continue
                self._load_file(path, sid)
            except (OSError, ValueError, UnicodeDecodeError, SessionError, AttributeError) as error:
                # One unreadable file must not stop the agent from starting.
                for table in (self._meta, self._ids, self._clients, self._acted, self._said, self._initial_seq, self._user_seq):
                    table.pop(sid, None)
                self._chosen.discard(sid)
                self._partial.discard(sid)
                if not isinstance(error, FileNotFoundError):
                    self._unreadable.add(sid)
                    logging.getLogger(__name__).warning('Skipped an unreadable session file')

    def _load_file(self, path, sid):
        with path.open('rb') as stream:
            first = stream.readline()
            if not first.endswith(b'\n'):
                return  # Creation has not committed a complete header yet.
            meta = json.loads(first)
            if meta.get('session') != sid:
                raise _invalid('/session', 'the session id of the saved file')
            _check(schema.session_metadata(), meta)
            self._meta[sid] = dict(id=sid, title=meta['title'], created_at=meta['created_at'],
                updated_at=meta['created_at'], client=meta['client'], last_seq=0, stopped=False)
            self._ids[sid] = {}
            self._clients[sid] = {meta['client']} if meta['client'] else set()
            self._acted[sid] = (meta['created_at'], 1)
            if meta.get('by') == 'user':
                self._chosen.add(sid)
            for row in stream:
                if not row.endswith(b'\n'):
                    self._partial.add(sid)
                    break
                try:
                    event = json.loads(row)
                    _check(schema.session_event(stored=True), event)
                    if event['seq'] != self._meta[sid]['last_seq'] + 1:
                        continue
                except (ValueError, UnicodeDecodeError, SessionError):
                    # Also skips an interrupted tail separated by a later append.
                    continue
                self._fold(sid, event)
            self._initial_seq[sid] = self._meta[sid]['last_seq']
        self._unreadable.discard(sid)

    @property
    def unreadable(self):
        with self.lock:
            return len(self._unreadable)

    def _fold(self, sid, event):
        meta = self._meta[sid]
        if event['type'] == 'control' and event['action'] == 'renamed' and event['by'] == 'user':
            # Naming a conversation is not activity in it: it keeps its place in the list and gives the agent nothing to read.
            meta['last_seq'] = event['seq']
            if event['title'].strip():
                meta['title'] = event['title']
                self._titles[sid] = event['title'].strip()[:60]
            else:
                self._titles.pop(sid, None)
            return
        meta.update(last_seq=event['seq'], updated_at=event['at'])
        turn = self._turn.setdefault(sid, [0, 0])
        if event['by'] == 'user' and event['type'] == 'message':
            turn[0] = event['seq']
        elif event['type'] == 'control' and event['action'] == 'abandoned':
            turn[1] = turn[0]
        if event['by'] == 'user':
            self._user_seq[sid] = event['seq']
            # No counter here: the order must come out the same when the files are read again after a
            # restart, so two things in one clock tick are ordered by what they are, then by session.
            self._acted[sid] = max(self._acted[sid], (event['at'], 0))
            self._chosen.add(sid)
            if event['type'] == 'message' and sid not in self._said:
                self._said[sid] = event['text'].strip()[:60]
        if event.get('client'):
            self._clients[sid].add(event['client'])
        if event.get('id'):
            self._ids[sid][event['id']] = event
        if event['type'] == 'control':
            if event['action'] in ('stopped', 'resumed'):
                meta['stopped'] = event['action'] == 'stopped'
            elif event['action'] == 'renamed':
                meta['title'] = event['title']

    def _write(self, sid, value, *, create=False):
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(self._path(sid), flags, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            # Never truncate crash evidence. A separator lets new whole lines survive.
            stream.write((b'\n' if sid in self._partial else b'') + _line(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        self._partial.discard(sid)

    def create(self, *, title=None, client=None, by='agent') -> dict:
        meta = dict(session='s' + secrets.token_hex(6), title=title, created_at=_stamp(self.clock()),
                    client=client, by=by)
        _check(schema.session_metadata(), meta)
        with self.lock:
            while True:
                sid = meta['session']
                try:
                    self._write(sid, meta, create=True)
                    break
                except FileExistsError:
                    meta['session'] = 's' + secrets.token_hex(6)
            self._meta[sid] = dict(id=sid, title=title, created_at=meta['created_at'],
                updated_at=meta['created_at'], client=client, last_seq=0, stopped=False)
            self._ids[sid], self._clients[sid] = {}, {client} if client else set()
            self._initial_seq[sid] = 0
            self._acted[sid] = (meta['created_at'], 1)
            self._unreadable.discard(sid)
            if by == 'user':
                self._chosen.add(sid)
            return deepcopy(self._meta[sid])

    def require_open(self, sid) -> None:
        """not_found unless the conversation exists and is not being deleted; work calls this as it is committed."""
        with self.lock:
            self._get(sid)
            if sid in self._closing:
                raise SessionError('not_found', 'Session does not exist')

    def begin_delete(self, sid) -> None:
        """Refuse new work in the conversation until delete() or end_delete(); the caller then checks for work in it."""
        with self.lock:
            self._get(sid)
            self._closing.add(sid)

    def end_delete(self, sid) -> None:
        with self.lock:
            self._closing.discard(sid)

    def delete(self, sid) -> None:
        """Forget a conversation and remove its file. A wait in it ends empty; anything later says not_found."""
        with self.lock:
            self._get(sid)
            self._path(sid).unlink(missing_ok=True)
            self._closing.discard(sid)
            waiters = self._waiters.pop(sid, ())
            for kept in (self._meta, self._ids, self._clients, self._initial_seq, self._user_seq, self._turn, self._stage, self._acted, self._said, self._titles):
                kept.pop(sid, None)
            self._chosen.discard(sid)
            self._partial.discard(sid)
            for key in [key for key in self._readers if key[0] == sid]:
                del self._readers[key]
        for loop, signal in waiters:
            try:
                loop.call_soon_threadsafe(signal.set)
            except RuntimeError:
                pass  # A closed client loop no longer needs notification.

    def list(self, *, limit=50, before=None) -> dict:
        _check(schema.session_schema('list'), dict(limit=limit, **({'before': before} if before is not None else {})))
        with self.lock:
            rows = sorted(self._meta.values(), key=lambda r: (r['updated_at'], r['id']), reverse=True)
            if before is not None:
                self._get(before)
                start = next(i for i, row in enumerate(rows) if row['id'] == before) + 1
                rows = rows[start:]
            page = rows[:limit]
            return dict(sessions=deepcopy(page), next_before=page[-1]['id'] if len(rows) > limit else None)

    def get(self, sid) -> dict:
        with self.lock:
            return deepcopy(self._get(sid))

    def append(self, sid, event: dict, *, by, client=None) -> dict:
        _check(schema.obj(dict(by=schema.enum(('user', 'agent', 'builtin', 'system'))), ('by',)), dict(by=by))
        _check(schema.obj(dict(client=schema.nullable(schema.string(80)))), dict(client=client))
        if not isinstance(event, dict):
            raise _invalid('/', 'an event object')
        event = deepcopy(event)
        with self.lock:
            meta = self._get(sid)
            kind = event.get('type')
            if kind == 'step' and event.get('state') == 'started' and 'id' not in event:
                event['id'] = 'st' + secrets.token_hex(5)
            previous = self._ids[sid].get(event.get('id')) if isinstance(event.get('id'), str) else None
            if kind in ('step', 'confirm'):
                initial = 'started' if kind == 'step' else 'asked'
                if event.get('state') != initial:
                    if previous is None or previous['type'] != kind or previous['state'] != initial:
                        raise _invalid('/id', 'the ID of an unfinished ' + kind)
                    inherited = {k: v for k, v in previous.items() if k not in ('seq', 'at', 'by', 'client')}
                    if kind == 'confirm' and any(k not in ('type', 'id', 'state') for k in event):
                        raise _invalid('/', 'a confirmation answer containing type, id and state only')
                    event = inherited | event
                elif previous is not None:
                    raise _invalid('/id', 'a new event ID')
            _check(schema.session_event(), event)
            if kind == 'step' and by == 'user':
                raise SessionError('forbidden', 'Steps are written by the server')
            if kind == 'confirm' and ((event['state'] == 'asked') == (by == 'user')):
                raise SessionError('forbidden', 'Only the user may answer a confirmation')
            if kind == 'control' and event['action'] != 'renamed' and by != 'user':
                raise SessionError('forbidden', 'Only the user may stop or continue')
            result = dict(event, seq=meta['last_seq'] + 1, at=_stamp(self.clock()), by=by)
            if client is not None:
                result['client'] = client
            try:
                size = len(_line(result))
            except (ValueError, TypeError, UnicodeEncodeError):
                raise _invalid('/', 'finite UTF-8 JSON') from None
            if size > MAX_EVENT_BYTES:
                raise SessionError('too_large', 'Session event exceeds the byte limit', limit_bytes=MAX_EVENT_BYTES)
            if meta['last_seq'] >= self.max_events:
                raise SessionError('session_full', 'Session event limit reached')
            self._write(sid, result)
            self._fold(sid, result)
            if kind in ('message', 'plan', 'step'):
                self._stage.pop(sid, None)
            for loop, signal in self._waiters.get(sid, ()):
                try:
                    loop.call_soon_threadsafe(signal.set)
                except RuntimeError:
                    pass  # A closed client loop no longer needs notification.
            return deepcopy(result)

    def _events(self, sid, after, limit):
        rows = []
        expected = 1
        with self._path(sid).open('rb') as stream:
            stream.readline()
            for line in stream:
                if not line.endswith(b'\n'):
                    break
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get('seq') != expected:
                        continue
                    _check(schema.session_event(stored=True), row)
                except (ValueError, UnicodeDecodeError, SessionError):
                    continue
                expected += 1
                if row['seq'] > after:
                    rows.append(row)
                    if len(rows) >= limit:
                        break
        return rows

    def events(self, sid, *, after=0, limit=500) -> list[dict]:
        _check(schema.session_schema('events'), dict(after=after, limit=limit))
        with self.lock:
            self._get(sid)
            return self._events(sid, after, limit)

    async def wait(self, sid, *, after, seconds, types=None, by=None, leave=None) -> list[dict]:
        """Events after a cursor, waiting up to some seconds. leave, when given, ends an empty wait early
        once it answers true: wake() asks for that look when the user moves to another conversation."""
        _check(schema.session_schema('events'), dict(after=after, wait=seconds))
        if types is not None:
            _check(schema.array(schema.enum(('message', 'plan', 'step', 'confirm', 'control')), high=5), types)
        if by is not None:
            _check(schema.enum(('user', 'agent', 'builtin', 'system')), by)
        loop = asyncio.get_running_loop()
        signal = asyncio.Event()
        waiter = (loop, signal)
        deadline = loop.time() + seconds
        cursor = after
        def register():
            with self.lock:
                self._get(sid)
                self._waiters.setdefault(sid, set()).add(waiter)

        def read():
            with self.lock:
                if sid not in self._meta:
                    return [], None  # Deleted while waiting.
                rows = self._events(sid, cursor, self.max_events)
                found = [row for row in rows if (types is None or row['type'] in types)
                         and (by is None or row['by'] == by)]
                return found[:500], rows[-1]['seq'] if rows else cursor

        def remove():
            with self.lock:
                waiters = self._waiters.get(sid)
                if waiters is not None:
                    waiters.discard(waiter)
                    if not waiters:
                        self._waiters.pop(sid)

        async def offload(work):
            # A cancelled to_thread keeps running. Finish registration/scanning before
            # removing its waiter, so a disconnect cannot leave a late registration.
            task = asyncio.create_task(asyncio.to_thread(work))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise

        try:
            await offload(register)
            while True:
                # Event operations stay on their loop. Clear before scanning: writes
                # after the scan schedule set() on the loop and cannot lose a wakeup.
                signal.clear()
                found, cursor = await offload(read)
                if found or cursor is None:
                    return found
                if leave is not None and await offload(leave):
                    return []
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return []
                try:
                    await asyncio.wait_for(signal.wait(), remaining)
                except asyncio.TimeoutError:
                    return []
        finally:
            await offload(remove)

    def confirmation(self, sid, digest) -> dict | None:
        """The newest server confirmation in this session for one operation digest, with its latest answer."""
        with self.lock:
            self._get(sid)
            rows = [e for e in self._ids[sid].values() if e['type'] == 'confirm' and e.get('digest') == digest]
            return deepcopy(max(rows, key=lambda e: e['seq'])) if rows else None

    def latest_for(self, client) -> str | None:
        with self.lock:
            rows = [row for sid, row in self._meta.items() if client in self._clients[sid]]
            return max(rows, key=lambda r: (r['updated_at'], r['id']))['id'] if rows else None

    def current_for(self, client) -> str | None:
        """The conversation this client's user is in: the newest one, or an older one the user spoke or
        answered in after that; what the agent or the server writes never moves the user. Until the user
        has opened a conversation or spoken on the page, it is wherever the latest work is, as before."""
        with self.lock:
            rows = [sid for sid in self._meta if client in self._clients[sid]]
            if not rows:
                return None
            if not self._chosen.intersection(rows):
                return max(rows, key=lambda sid: (self._meta[sid]['updated_at'], sid))
            return max(rows, key=lambda sid: (self._acted[sid], sid))

    def has(self, client, sid) -> bool:
        with self.lock:
            return sid in self._meta and client in self._clients[sid]

    def spoken(self, sid) -> bool:
        """Whether the user has said anything in this conversation."""
        with self.lock:
            return sid in self._said

    def wake(self, client) -> None:
        """Let this client's waits look again: its user moved to another conversation."""
        with self.lock:
            waiters = [w for sid, group in self._waiters.items() if client in self._clients[sid] for w in group]
        for loop, signal in waiters:
            try:
                loop.call_soon_threadsafe(signal.set)
            except RuntimeError:
                pass  # A closed client loop no longer needs notification.

    def history(self, client, *, limit=50) -> list[dict]:
        """This client's conversations the user has spoken in, for the page: latest change first."""
        current = self.current_for(client)
        with self.lock:
            rows = sorted((row for sid, row in self._meta.items() if client in self._clients[sid] and sid in self._said),
                          key=lambda r: (r['updated_at'], r['id']), reverse=True)
            return [dict(id=r['id'], title=self._titles.get(r['id']) or self._said[r['id']], updated_at=r['updated_at'], current=r['id'] == current)
                    for r in rows[:limit]]

    def read_until(self, sid, reader) -> int:
        """The last event this reader has been given; unread() moves it."""
        with self.lock:
            self._get(sid)
            return self._readers.get((sid, reader), self._initial_seq[sid])

    def stopped(self, sid) -> bool:
        with self.lock:
            return self._get(sid)['stopped']

    def abandoned(self, sid) -> bool:
        """The user gave up on the turn they last spoke in, and has not spoken since."""
        with self.lock:
            self._get(sid)
            said, gave_up = self._turn.get(sid, (0, 0))
            return gave_up > 0 and gave_up == said

    def report_stage(self, sid, key) -> None:
        _check(schema.STAGE, key)
        with self.lock:
            self._get(sid)
            self._stage[sid] = (key, self.clock())

    def stage(self, sid, *, within=STAGE_SECONDS) -> str | None:
        """The stage the agent reported, while it is recent and the turn it belongs to is still open."""
        with self.lock:
            self._get(sid)
            known = self._stage.get(sid)
            if known is None or self.clock() - known[1] > within:
                return None
            return known[0]

    def clear_stage(self, sid) -> None:
        with self.lock:
            self._stage.pop(sid, None)

    def unread(self, sid, reader: str) -> list[dict]:
        _check(schema.string(80), reader)
        with self.lock:
            meta = self._get(sid)
            key = (sid, reader)
            after = self._readers.get(key, self._initial_seq[sid])
            self._readers[key] = meta['last_seq']
            if self._user_seq.get(sid, 0) <= after:
                return []
            rows = self._events(sid, after, self.max_events)
            return [row for row in rows if row['by'] == 'user' and (row['type'] == 'message'
                    or row['type'] == 'confirm' and row['state'] in ('approved', 'declined')
                    or row['type'] == 'control' and row['action'] == 'abandoned')]


class Confirmations:
    """Agent operations that wait for the user's approval (sessions-contract.md section 7).

    gate() runs inside the operation before anything is saved. A request is
    identified by errors.CONFIRM, its digest; an approval lets that exact
    request through once.
    """
    def __init__(self, path: Path, sessions: Sessions):
        self.path, self.sessions = Path(path), sessions
        self.lock = threading.Lock()
        self._used = set()

    def settings(self) -> dict:
        try:
            value = json.loads(self.path.read_text('utf-8'))
            _check(schema.confirm_settings(), value)
        except (OSError, ValueError, SessionError):
            value = {}
        return dict(schema.CONFIRM_DEFAULTS, **value)

    def update(self, values: dict) -> dict:
        _check(schema.confirm_settings(), values)
        with self.lock:
            value = dict(self.settings(), **values)
            temporary = self.path.with_name('.' + self.path.name + '.tmp')
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(value), 'utf-8')
            os.replace(temporary, self.path)
        return value

    def gate(self, action, *, by, estimate_seconds, refs, question, shape=None):
        sid, digest = errors.SESSION.get(), errors.CONFIRM.get()
        if by != 'agent' or sid is None or digest is None:
            return
        policy = self.settings()
        if action == 'start' and not policy['start'] or action == 'reshoot' and (
                policy['reshoot_seconds'] is None or estimate_seconds is None
                or estimate_seconds < policy['reshoot_seconds']):
            return
        with self.lock:
            row = self.sessions.confirmation(sid, digest)
            if row is not None and row['state'] == 'approved' and row['id'] not in self._used:
                self._used.add(row['id'])
                return
            if row is not None and row['state'] == 'declined':
                raise SessionError('declined_by_user', 'The user declined this operation')
            if row is None or row['state'] != 'asked':
                event = dict(type='confirm', id='cf' + secrets.token_hex(5), question=question, action=action,
                             refs=refs, digest=digest, state='asked')
                if estimate_seconds is not None:
                    event['estimate_seconds'] = round(estimate_seconds)
                # What is about to be made, for the card the user answers; only what is known is written.
                event.update({key: shape[key] for key in ('width', 'height', 'seconds') if shape and shape.get(key) is not None})
                row = self.sessions.append(sid, event, by='system')
        raise SessionError('needs_confirmation', 'Waiting for the user',
                           details=[dict(confirm=row['id'], seconds=row.get('estimate_seconds'))])
