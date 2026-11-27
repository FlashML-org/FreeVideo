"""Prompt rewrites of queued videos, when their turn comes.

A request sent while the queue was busy carries its rewrite (the prompt as it was when Generate was pressed, and what
the rewrite reads) in the FreeVideoGenerate node's inputs under ENHANCE_KEY. When the first such request reaches the
head of the queue and no video is running, every waiting request that carries one is rewritten, one after another,
before any is run: the prompt model and the video model take the GPU in turn, and a queue of rewritten videos loads the
video model again only once. Each request then generates with its rewrite, or with the prompt as it was when its rewrite
could not be made, and says why on its own result.

Both queues do this: the control process's (comfy_front.Front, several cards) before it dispatches, and ComfyUI's own
(one card) when its worker asks for the next request (install_native). A request that reaches the node with its rewrite
still attached came by neither; the node generates with the prompt as it was and its result says so.
"""
import heapq
import logging
import re
import threading
import time
from types import SimpleNamespace

ENHANCE_KEY = 'freevideo_enhance'
RUNNING = ('submitting', 'running', 'cancelling', 'unconfirmed')
# How long a batch waits for the prompt model while something else (another window's rewrite) holds it.
WAIT_LIMIT = 600


def restore_cast(text, subjects):
    """The rewrite as the page shows it (web/cast_mention.js restoreCast): each <Subject N> back to its @mention."""
    text = '' if text is None else str(text)
    handles = [(subject.get('handle') or subject.get('name')) if isinstance(subject, dict) else None
               for subject in (subjects if isinstance(subjects, list) else [])]
    numbers = [int(number) for number in re.findall(r'<Subject (\d+)>', text)]
    if not numbers or any(number < 1 or number > len(handles) or not handles[number - 1] for number in numbers):
        return text
    body = re.sub(r'^\s*subject_definitions:(?:[\s\S]*?\n\s*\n(?=[a-z_]+:)|[\s\S]*?\n\s*\n|[\s\S]*$)', '', text).strip()
    return re.sub(r'<Subject (\d+)>', lambda match: '@' + handles[int(match.group(1)) - 1], body or text)


def enhancement(row):
    """(node id, the request's rewrite) of a queued request that carries one, else None."""
    for node, value in (row.prompt or {}).items():
        if isinstance(value, dict) and value.get('class_type') == 'FreeVideoGenerate':
            request = (value.get('inputs') or {}).get(ENHANCE_KEY)
            if isinstance(request, dict) and isinstance(request.get('body'), dict):
                return node, request
    return None


class QueuedRewrites:
    """The batch of rewrites the control process (comfy_front.Front) makes before dispatching their videos."""

    def __init__(self, front, service=None, clock=time.monotonic, settle=None):
        self.front, self._service, self.clock, self._settle = front, service, clock, settle
        self.batch, self.index, self.job, self.waiting_since = [], 0, None, None
        self._stopping = None

    def service(self):
        return self._service if self._service is not None else getattr(self.front.server, '_freevideo_prompt_vlm', None)

    def holding(self):
        """True while dispatch waits for rewrites: a batch runs, or the head of the queue carries one and its turn
        comes only once the running videos are done. Starts the batch when that turn comes. Called off the event loop."""
        front = self.front
        with front.lock:
            if not self.batch:
                pending = sorted((row for row in front.records.values() if row.state == 'pending'), key=lambda row: row.number)
                if not pending or enhancement(pending[0]) is None:
                    return False
                if front.paused or front.closed or any(row.state in RUNNING for row in front.records.values()):
                    return True
                self.batch = [row.task.id for row in pending if enhancement(row) is not None]
                self.index, self.job, self.waiting_since = 0, None, None
                for position, identity in enumerate(self.batch, 1):
                    self._mark(front.records[identity], 'waiting', position=position, total=len(self.batch))
        front.updated()
        self._drive()
        return bool(self.batch)

    def cancelled(self, identity):
        """The page cancelled or removed a request: its rewrite stops if it is the one being made."""
        if self.job is not None and self.index < len(self.batch) and self.batch[self.index] == identity:
            self._cancel(self.job)

    def close(self):
        """The control process is closing: the batch ends and a rewrite still being made stops."""
        with self.front.lock:
            job, self.batch, self.index, self.job, self.waiting_since = self.job, [], 0, None, None
        if job is not None:
            self._cancel(job)

    def _cancel(self, job):
        service = self.service()
        try:
            if service is not None:
                service.cancel(job)
        except ValueError:
            pass

    def _drive(self):
        front = self.front
        while self.index < len(self.batch):
            with front.lock:
                row = front.records.get(self.batch[self.index])
            if row is None or row.state != 'pending':
                # Cancelled or removed meanwhile: nothing to rewrite; a rewrite still running for it is stopped.
                if self.job is not None and self._finished(self._status()) is None:
                    if self._stopping != self.job:
                        self._stopping = self.job
                        self._cancel(self.job)
                    return
                self.job, self.index = None, self.index + 1
                continue
            service = self.service()
            if service is None:
                self._fall_back(row, 'unavailable')
                continue
            if self.job is None:
                if service.busy():
                    # Another window's rewrite, or a cancelled one still closing.
                    if self.waiting_since is None:
                        self.waiting_since = self.clock()
                    if self.clock() - self.waiting_since <= WAIT_LIMIT:
                        return
                    self._fall_back(row, 'busy')
                    continue
                self.waiting_since = None
                found = enhancement(row)
                try:
                    if self._settle is not None:
                        self._settle(service)
                    else:
                        from .prompt_vlm import assets
                        assets.settle(service.installation()[0])
                    self.job = service.start('rewrite', dict(found[1]['body']))['job']
                except (ValueError, OSError) as error:
                    self._fall_back(row, str(error) if isinstance(error, ValueError) else 'rewrite_failed')
                    continue
                with front.lock:
                    # Cancelled while the rewrite was starting: cancelled() could not see the job yet.
                    gone = front.closed or front.records.get(row.task.id) is not row or row.state != 'pending'
                    if not gone:
                        self._mark(row, 'rewriting', position=self.index + 1, total=len(self.batch))
                if gone:
                    self._cancel(self.job)
                front.updated()
                return
            status = self._status()
            done = self._finished(status)
            if done is None:
                return
            if done == 'complete' and str(status.get('text') or '').strip():
                self._apply(row, status)
            else:
                self._fall_back(row, status.get('error') or ('cancelled' if done == 'cancelled' else 'rewrite_failed'))
        with front.lock:
            self.batch, self.index, self.job = [], 0, None
        front.updated()

    def _status(self):
        try:
            return self.service().status(self.job)
        except (AttributeError, ValueError):
            return dict(phase='failed', error='rewrite_failed')

    @staticmethod
    def _finished(status):
        phase = status.get('phase')
        return phase if phase in ('complete', 'failed', 'cancelled') else None

    def _apply(self, row, status):
        front = self.front
        with front.lock:
            node, request = enhancement(row)
            text = restore_cast(status['text'], request.get('subjects'))
            inputs = row.prompt[node]['inputs']
            inputs.pop(ENHANCE_KEY, None)
            inputs['text'] = text
            self._versions(row, node, request, rewritten=text, report=status.get('rewrite_report_id'))
            self._mark(row, 'rewritten', rewritten=text)
            self.job, self.index = None, self.index + 1
        front.updated()

    def _fall_back(self, row, reason):
        front = self.front
        with front.lock:
            found = enhancement(row)
            if found is not None:
                node, request = found
                row.prompt[node]['inputs'].pop(ENHANCE_KEY, None)
                self._versions(row, node, request, rewritten=None, report=None)
            self._mark(row, 'original', reason=str(reason)[:64])
            self.job, self.index = None, self.index + 1
        front.updated()

    @staticmethod
    def _versions(row, node, request, *, rewritten, report):
        """The node's saved prompt versions, which the result and the history read: both texts, the one used selected."""
        original = request.get('original') if isinstance(request.get('original'), str) else row.prompt[node]['inputs'].get('text', '')
        for extra in (row.extra, row.public_extra):
            workflow = ((extra or {}).get('extra_pnginfo') or {}).get('workflow') or {}
            for item in workflow.get('nodes') or []:
                if isinstance(item, dict) and str(item.get('id')) == str(node):
                    properties = item.setdefault('properties', {})
                    versions = dict(properties.get('freevideo_prompt_versions') or {}, enabled=True, original=original)
                    if rewritten is not None:
                        versions.update(rewritten=rewritten, selected='rewritten')
                    else:
                        versions['selected'] = 'original'
                    properties['freevideo_prompt_versions'] = versions
                    if report:
                        properties['freevideo_prompt_report'] = report

    @staticmethod
    def _mark(row, state, **info):
        row.public_extra[ENHANCE_KEY] = dict(state=state, **info)


class NativeRow:
    """A request in ComfyUI's own queue, as QueuedRewrites reads one: its saved extra data is also what /queue shows."""

    def __init__(self, item):
        self.item, self.task, self.number, self.state = item, SimpleNamespace(id=item[1]), item[0], 'pending'
        self.prompt, self.extra, self.public_extra = item[2], item[3], item[3]


class NativeQueue:
    """ComfyUI's own PromptQueue (one card, no control process) in the shape QueuedRewrites reads a front."""

    def __init__(self, server, queue):
        self.server, self.queue, self.lock, self.closed, self._rows = server, queue, queue.mutex, False, {}

    @property
    def paused(self):
        return bool(getattr(getattr(self.server, '_freevideo_monitor', None), 'paused', False))

    @property
    def records(self):
        with self.lock:
            rows = {}
            for state, items in (('running', list(self.queue.currently_running.values())), ('pending', list(self.queue.queue))):
                for item in items:
                    row = self._rows.get(item[1])
                    if row is None or row.item is not item:
                        row = NativeRow(item)
                    row.state, rows[item[1]] = state, row
            self._rows = rows
            return rows

    def updated(self):
        self.server.queue_updated()


def install_native(server, *, poll=.5):
    """ComfyUI's worker takes its next request only once the rewrites of the waiting requests are made (QueuedRewrites)."""
    queue = getattr(server, 'prompt_queue', None)
    if getattr(queue, '_freevideo_rewrites', None) is not None:
        return queue._freevideo_rewrites
    if not all(hasattr(queue, name) for name in ('mutex', 'queue', 'currently_running', 'get')):
        return None  # not ComfyUI's PromptQueue
    rewrites = QueuedRewrites(NativeQueue(server, queue))
    original, idle = queue.get, threading.Event()
    arrived = getattr(queue, 'not_empty', None)

    def left(deadline):
        return None if deadline is None else max(0., deadline - time.monotonic())

    def get(timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Wait here for a request, not inside ComfyUI's get, which would hand it over unchecked.
            if arrived is not None:
                with arrived:
                    while not queue.queue:
                        if deadline is not None and time.monotonic() >= deadline:
                            return None
                        arrived.wait(timeout=left(deadline) if deadline is not None else poll)
            try:
                holding = rewrites.holding()
            except Exception:
                # The request goes as it is; the node says it was not rewritten.
                logging.exception('[FreeVideo] Queued prompt rewrites stopped')
                holding = False
            if not holding:
                taken = original(left(deadline))
                if taken is None:
                    return None
                with queue.mutex:
                    # A request with a rewrite put ahead of the head meanwhile goes back for its batch.
                    late = isinstance(taken, tuple) and len(taken) == 2 and isinstance(taken[0], tuple) \
                        and len(taken[0]) > 2 and enhancement(NativeRow(taken[0])) is not None
                    if late:
                        queue.currently_running.pop(taken[1], None)
                        heapq.heappush(queue.queue, taken[0])
                if not late:
                    return taken
                server.queue_updated()
                continue
            if deadline is not None and time.monotonic() >= deadline:
                return None
            idle.wait(poll)

    queue.get = get
    queue._freevideo_rewrites = rewrites
    return rewrites


def request_left(prompt, node_id):
    """True when this node's request still carries a rewrite that no queue made: it generates with the prompt as it is."""
    node = (prompt or {}).get(str(node_id)) if isinstance(prompt, dict) else None
    return isinstance(node, dict) and isinstance((node.get('inputs') or {}).get(ENHANCE_KEY), dict)
