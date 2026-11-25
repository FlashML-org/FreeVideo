"""A record of the moments when the engine's event loop stopped answering.

The page, the launcher and every request go through one event loop. When it is
held up (a long call on it, a lock another thread keeps, a computer that pages
heavily) the engine looks stopped from outside, and afterwards nothing says
where the time went. The watchdog only observes: a small thread posts a ping to
the loop once a second. A ping that is not answered within five seconds starts a
stall; the calls of every thread at that moment are written to a small file, once
per stall, and the length is added when the loop answers again. It changes
nothing about how requests are handled and does not touch placement, memory or
the GPU.

The file lives next to the launcher's other records, is capped in size and keeps
only function names, line numbers and file names (every folder is replaced by
``<PATH>``), never source lines, arguments or values.
"""
import json
import math
import os
import sys
import threading
import time
import traceback

from .long_paths import ABSOLUTE_PATH, io_path, path_match, path_placeholder
from .monitoring import save

FILE = 'loop-stalls.json'
INTERVAL = 1.0
THRESHOLD = 5.0
MAX_BYTES = 48 * 1024
MAX_RECORDS = 8
MAX_THREADS = 24
MAX_FRAMES = 14
TEXT_CHARS = 120
SWITCH = 'FREEVIDEO_LOOP_WATCHDOG'
PUBLIC_RECORDS = 2
PUBLIC_FRAMES = 5


def location(engine):
    return io_path(engine) / 'launcher' / FILE


def enabled(environment=None):
    """Off only when the switch is set to 0, so that a run can be compared with and without it."""
    return str((os.environ if environment is None else environment).get(SWITCH, '1')).strip() != '0'


def _text(value):
    return str(value).replace('\r', ' ').replace('\n', ' ')[:TEXT_CHARS]


def _folderless(value):
    """Text of a stored field with any folder in it replaced by <PATH>."""
    return ABSOLUTE_PATH.sub(path_match, _text(value))


def frames_of(frame, limit=MAX_FRAMES):
    """The innermost calls of one thread, outermost first: ``<PATH>/file.py:12 function``."""
    rows = [f'{path_placeholder(item.filename)}:{item.lineno} {_text(item.name)}' for item in traceback.extract_stack(frame)]
    return rows[-limit:]


def snapshot(frames, names, loop_ident, skip, *, threads=MAX_THREADS, depth=MAX_FRAMES):
    """Every thread's calls, the loop's thread first; the watchdog's own thread is left out."""
    rows = []
    for ident, frame in frames.items():
        if ident == skip:
            continue
        rows.append(dict(name=_text(names.get(ident, 'unknown')), loop=ident == loop_ident, frames=frames_of(frame, depth)))
    rows.sort(key=lambda row: not row['loop'])
    return rows[:threads]


def _fits(records):
    return len(json.dumps(dict(stalls=records), ensure_ascii=False, indent=2).encode('utf-8')) <= MAX_BYTES


def capped(records):
    """The newest records that fit the size cap: older ones go first, then the other threads' calls of the newest."""
    records = list(records)[-MAX_RECORDS:]
    while len(records) > 1 and not _fits(records):
        records.pop(0)
    last = records[-1] if records else None
    for threads, depth in ((MAX_THREADS, 8), (6, 6), (1, 6), (0, 0)):
        if _fits(records) or last is None:
            break
        last['threads'] = [dict(row, frames=row['frames'][-depth:]) for row in last['threads'][:threads]] if depth else []
    return records if _fits(records) else []


def sanitized(rows):
    """Only the known fields, with their types, lengths and counts held and any folder replaced: a file that was edited
    or damaged can never put anything else into a report."""
    result = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        at, seconds = row.get('at'), row.get('seconds')
        if type(at) not in (int, float) or type(seconds) not in (int, float) or not math.isfinite(at) or not math.isfinite(seconds):
            continue
        threads = []
        for item in (row.get('threads') if isinstance(row.get('threads'), list) else ())[:MAX_THREADS]:
            if not isinstance(item, dict):
                continue
            calls = [_folderless(call) for call in (item.get('frames') if isinstance(item.get('frames'), list) else ())
                     if isinstance(call, str)][-MAX_FRAMES:]
            threads.append(dict(name=_folderless(item.get('name', '')), loop=item.get('loop') is True, frames=calls))
        result.append(dict(at=at, seconds=seconds, recovered=row.get('recovered') is True, threads=threads))
    return capped(result)


def read(engine):
    """The stored records, oldest first, as sanitized(); none when the file is missing or cannot be read."""
    try:
        value = json.loads(location(engine).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return []
    return sanitized(value.get('stalls') if isinstance(value, dict) else None)


def public(records, *, count=PUBLIC_RECORDS, frames=PUBLIC_FRAMES):
    """What goes into a feedback message: when, how long and the loop thread's innermost calls. Strings only as stored."""
    result = []
    for row in reversed(records or []):
        at, seconds = row.get('at'), row.get('seconds')
        if type(at) not in (int, float) or not math.isfinite(at) or type(seconds) not in (int, float) or not math.isfinite(seconds):
            continue
        loop = next((item for item in row.get('threads') or () if isinstance(item, dict) and item.get('loop')), None)
        calls = [_text(call) for call in (loop or {}).get('frames') or () if isinstance(call, str)][-frames:]
        result.append(dict(at=at, seconds=seconds, recovered=row.get('recovered') is True, loop=calls))
        if len(result) >= count:
            break
    return result


class Watchdog:
    def __init__(self, loop, path, *, records=(), threshold=THRESHOLD, interval=INTERVAL, clock=time.monotonic, wall=time.time,
                 frames=sys._current_frames, names=None):
        self.loop, self.path, self.records = loop, path, list(records)
        self.threshold, self.interval = threshold, interval
        self.clock, self.wall, self.frames = clock, wall, frames
        self.names = names or (lambda: {thread.ident: thread.name for thread in threading.enumerate()})
        self.lock = threading.Lock()
        self.pending = None        # when the unanswered ping was posted
        self.loop_ident = None
        self.answered = None       # when the loop last answered
        self.open = None           # the record of the stall in progress
        self.started = None        # when the ping of that stall was posted
        self.last_tick = None
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='freevideo-loop-watchdog', daemon=True)
            self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()

    def pong(self):
        """Runs on the loop: it answered."""
        with self.lock:
            self.loop_ident = threading.get_ident()
            self.answered = self.clock()
            self.pending = None

    def run(self):
        while not self.stop_event.wait(self.interval):
            try:
                if not self.tick():
                    return
            except Exception:
                # The watchdog never takes the engine down; the next tick tries again.
                continue

    def tick(self):
        """One check; False once the loop is gone."""
        if self.loop.is_closed():
            return False
        if not self.loop.is_running():
            with self.lock:
                self.pending = None
            return True
        now = self.clock()
        gap, self.last_tick = (None if self.last_tick is None else now - self.last_tick), now
        if gap is not None and gap >= self.threshold:
            # This thread itself did not run for that long (the computer slept, or a call held every thread): what it
            # would record is not a stall of the loop, so start again without writing anything.
            with self.lock:
                self.pending, self.open, self.started = None, None, None
            return True
        with self.lock:
            pending, answered, opened, started = self.pending, self.answered, self.open, self.started
            if pending is None:
                self.pending = now
        if pending is None:
            if opened is not None:
                self.close(opened, started, answered)
            try:
                self.loop.call_soon_threadsafe(self.pong)
            except RuntimeError:
                return False
        elif opened is None and now - pending >= self.threshold:
            self.begin(pending, now)
        return True

    def begin(self, posted, now):
        """The ping has gone unanswered for the threshold: write down what every thread is doing, once."""
        with self.lock:
            ident = self.loop_ident
        record = dict(at=round(self.wall() - (now - posted), 3), seconds=round(now - posted, 1), recovered=False,
                      threads=snapshot(self.frames(), self.names(), ident, threading.get_ident()))
        with self.lock:
            self.open, self.started = record, posted
        self.write(record)

    def close(self, record, posted, answered):
        """The loop answered again: add how long it was held up."""
        with self.lock:
            self.open = self.started = None
        record['recovered'] = True
        record['seconds'] = round(max(record['seconds'], (answered or posted) - posted), 1)
        self.write(record)

    def write(self, record):
        try:
            self.records = capped([row for row in self.records if row is not record] + [record])
            save(self.path, dict(stalls=self.records), plain_paths=False)
        except Exception:
            pass


def install(loop, engine, **options):
    """Start the watchdog for the engine's loop, unless the switch turned it off. Returns it, or None."""
    if loop is None or not enabled():
        return None
    return Watchdog(loop, location(engine), records=read(engine), **options).start()
