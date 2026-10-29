"""Control-process prompt registry; private executors never own public queue state."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import inspect
import json
import logging
import os
import re
import signal
import threading
import uuid


# The error panel recognises these texts; keep them in step (web/error_panel.js).
NO_EXECUTOR = 'No GPU is available: every GPU process has stopped. Restart FreeVideo.'
EXITED_AGAIN = 'The GPU process exited again while running this task, so it was not moved to another GPU'
LOG_ERROR = re.compile(r'\b\w*(?:Error|Exception)\b:|Fatal Python error|Segmentation fault|CUDA error|\bKilled\b|\bAborted\b')


class PromptRejected(ValueError):
    def __init__(self, body):
        super().__init__('Prompt validation failed')
        self.body = body


@dataclass
class Submission:
    task: object
    number: float
    prompt: dict
    extra: dict
    outputs: list
    public_extra: dict
    state: str = 'pending'
    worker: object = None
    worker_prompt_id: str | None = None
    result: dict | None = None
    chain_notified: bool = False
    cancelled: bool = False
    # Exit causes of executors that stopped while running this task; one move is allowed.
    exits: tuple = ()


def _last_error(path, limit=65536):
    """The last error-looking line of an executor log, if any."""
    try:
        with open(path, 'rb') as handle:
            handle.seek(max(0, os.fstat(handle.fileno()).st_size - limit))
            lines = handle.read().decode('utf-8', 'replace').splitlines()
    except (OSError, TypeError, ValueError):
        return None
    line = next((line.strip() for line in reversed(lines) if LOG_ERROR.search(line)), None)
    return line[:300] if line else None


def exit_cause(worker):
    """Exit code (with its signal name, or hex for Windows status codes) and the last logged error."""
    code = getattr(getattr(worker, 'process', None), 'poll', lambda: None)()
    if code is None:
        cause = 'exit code unknown'
    elif code < 0:
        try:
            cause = 'exit code %d (%s)' % (code, signal.Signals(-code).name)
        except ValueError:
            cause = 'exit code %d' % code
    elif code > 255:
        cause = 'exit code %d (0x%08X)' % (code, code & 0xFFFFFFFF)
    else:
        cause = 'exit code %d' % code
    error = _last_error(getattr(worker, 'log', None))
    return cause + ('; last error: ' + error if error else '')


class Front:
    """Adapt the B/C contracts without changing Comfy's local execution queue.

    envelope(prompt, extra) is a trusted, read-only policy adapter. Unknown
    graphs retain Envelope=None and therefore use the scheduler's exclusive lane.
    Only confirmed worker history releases an assigned device or reservation.
    """
    def __init__(self, server, scheduler, workers, *, envelope=None, validate=None,
                 run_prompt=None, interrupt=None, worker_history=None, worker_lookup=None):
        self.server, self.scheduler, self.workers = server, scheduler, workers
        self.envelope, self.validate = envelope, validate
        if run_prompt is None or interrupt is None or worker_history is None or worker_lookup is None:
            from .comfy_pool import (run_prompt as submit_worker, interrupt as cancel_worker,
                                     worker_history as read_history, worker_lookup as find_prompt)
            run_prompt = run_prompt or submit_worker
            interrupt = interrupt or cancel_worker
            worker_history = worker_history or read_history
            worker_lookup = worker_lookup or find_prompt
        self.run_prompt, self.interrupt, self.worker_history = run_prompt, interrupt, worker_history
        self.worker_lookup = worker_lookup
        self.records, self.reports, self.seen = {}, {}, {}
        self.lock = threading.RLock()
        self.dispatch_lock = asyncio.Lock()
        self.loop = getattr(server, 'loop', None)
        self.closed = False
        self.background = set()
        # Devices whose executor exited. Their work moves to the others until the control process restarts.
        self.unavailable = set()

    def accepting(self):
        # Busy devices and host pressure belong in the scheduler's pending queue.
        return not self.closed

    async def submit(self, value, headers=None):
        from .gpu_scheduler import Task
        value = deepcopy(value)
        if not isinstance(value, dict) or not isinstance(value.get('prompt'), dict):
            raise PromptRejected({'error': {'type': 'no_prompt', 'message': 'No prompt provided'}, 'node_errors': {}})
        trigger = getattr(self.server, 'trigger_on_prompt', None)
        if trigger is not None:
            value = trigger(value)
        identity = value.get('prompt_id')
        if identity is None:
            identity = str(uuid.uuid4())
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise PromptRejected({'error': {'type': 'invalid_prompt_id', 'message': 'prompt_id must be a valid UUID'},
                                  'node_errors': {}}) from None
        prompt, extra = value['prompt'], deepcopy(value.get('extra_data') or {})
        if not isinstance(extra, dict):
            raise PromptRejected({'error': {'type': 'invalid_request', 'message': 'Invalid extra_data'}, 'node_errors': {}})
        # Execution metadata may only be minted by this control process.
        extra.pop('freevideo_dispatch', None)
        if 'client_id' in value:
            extra['client_id'] = value['client_id']
        if headers and headers.get('Comfy-Usage-Source'):
            extra.setdefault('comfy_usage_source', headers['Comfy-Usage-Source'])
        replace = getattr(self.server, 'node_replace_manager', None)
        if replace is not None:
            replace.apply_replacements(prompt)
        if self.validate is None:
            import execution
            validate = execution.validate_prompt
            sensitive = execution.SENSITIVE_EXTRA_DATA_KEYS
        else:
            validate, sensitive = self.validate, ('auth_token_comfy_org', 'api_key_comfy_org')
        partial = value.get('partial_execution_targets')
        # run_prompt has no partial-target argument; do not silently execute a larger graph.
        if partial is not None:
            raise PromptRejected({'error': {'type': 'invalid_request', 'message': 'Partial execution is unavailable'},
                                  'node_errors': {}})
        valid = validate(identity, prompt, partial)
        if inspect.isawaitable(valid):
            valid = await valid
        if not valid[0]:
            raise PromptRejected({'error': valid[1], 'node_errors': valid[3]})
        public = {key: deepcopy(item) for key, item in extra.items() if key not in sensitive}
        chain = extra.get('freevideo_chain') or {}
        envelope = self.envelope(prompt, extra) if self.envelope is not None else None
        task = Task(id=identity, kind='prompt', envelope=envelope, depends_on=(),
                    chain_id=chain.get('id'), client_id=extra.get('client_id') or '')
        with self.lock:
            if self.closed:
                raise RuntimeError('Executors are stopping')
            if self.unavailable and not self._any_executor():
                raise PromptRejected({'error': {'type': 'executors_unavailable', 'message': NO_EXECUTOR},
                                      'node_errors': {}})
            old = self.records.get(identity)
            if old is not None:
                if old.prompt != prompt or old.extra != extra:
                    raise PromptRejected({'error': {'type': 'invalid_prompt_id', 'message': 'Prompt ID already used'},
                                          'node_errors': {}})
                return dict(prompt_id=identity, number=old.number, node_errors=valid[3])
            number = float(value['number']) if 'number' in value else self.server.number
            if 'number' not in value:
                self.server.number += 1
                if value.get('front'):
                    number = -number
            row = Submission(task, number, prompt, extra, valid[2], public)
            self.scheduler.submit(task)
            self.records[identity] = row
            pending = [r for r in self.records.values() if r.state == 'pending']
            if pending != sorted(pending, key=lambda r: r.number):
                self._requeue_pending(sorted(pending, key=lambda r: r.number))
        self.updated()
        return dict(prompt_id=identity, number=number, node_errors=valid[3])

    def submit_sync(self, prompt_id, prompt, extra):
        if self.loop is None or not self.loop.is_running():
            raise RuntimeError('Control event loop unavailable')
        future = asyncio.run_coroutine_threadsafe(self.submit(dict(
            prompt_id=prompt_id, prompt=prompt, extra_data=extra,
            client_id=extra.get('client_id', ''))), self.loop)
        # No timeout/retry here: an accepted request must never be replayed.
        return future.result()['prompt_id']

    def updated(self):
        self.server.queue_updated()

    @staticmethod
    def receipt(row):
        if row.worker is None:
            return dict(task_id=row.task.id)
        device = row.worker.device
        return dict(task_id=row.task.id, executor_id=device.uuid, worker_prompt_id=row.worker_prompt_id,
                    device=dict(uuid=device.uuid, index=device.index, name=device.name), logical_device=0)

    def _queue_row(self, row):
        extra = dict(deepcopy(row.public_extra), freevideo_dispatch=self.receipt(row))
        if row.state in ('submitting', 'running', 'cancelling', 'unconfirmed'):
            from .comfy_progress import STATE
            extra['freevideo_progress'] = {message['node']: message for message in STATE.snapshot()
                                          if message.get('task_id') == row.task.id}
        return (row.number, row.task.id, deepcopy(row.prompt), extra, deepcopy(row.outputs))

    def current_queue(self):
        with self.lock:
            running = [self._queue_row(row) for row in self.records.values()
                       if row.state in ('submitting', 'running', 'cancelling', 'unconfirmed')]
            pending = [self._queue_row(row) for row in self.records.values() if row.state == 'pending']
            return running, sorted(pending, key=lambda row: row[0])

    def queue_info(self):
        running, pending = self.current_queue()
        return {'exec_info': {'queue_remaining': len(running) + len(pending)},
                'freevideo_busy': self.busy()}

    def busy(self):
        running, pending = self.current_queue()
        if running or pending:
            return True
        from .comfy_chain import RUNNER
        if RUNNER is not None:
            with RUNNER.lock:
                if any(row['state'] in ('queued', 'running', 'cancelling') or row['output_state'] == 'joining'
                       for row in RUNNER.records.values()):
                    return True
        jobs = getattr(self.server, '_freevideo_agent', None)
        return jobs is not None and bool(jobs.active())

    def history(self, prompt_id=None, max_items=None, offset=-1):
        with self.lock:
            rows = [(identity, deepcopy(row.result)) for identity, row in self.records.items() if row.result is not None]
            if prompt_id is not None:
                return {identity: result for identity, result in rows if identity == prompt_id}
            if max_items is not None and offset < 0:
                offset = max(0, len(rows) - max_items)
            elif offset < 0:
                offset = 0
            return dict(rows[offset:offset + max_items if max_items is not None else None])

    def _matches(self, row, node):
        return node is None or row.prompt.get(str(node), {}).get('class_type') == 'FreeVideoGenerate'

    def _background(self, coro):
        def schedule():
            task = asyncio.create_task(coro)
            self.background.add(task)
            task.add_done_callback(self.background.discard)
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(schedule)
        else:
            coro.close()
            raise RuntimeError('Control event loop unavailable')

    async def _interrupt(self, row):
        try:
            state = await asyncio.to_thread(self.interrupt, row.worker, row.worker_prompt_id)
        except Exception:
            with self.lock:
                if row.result is None:
                    row.state = 'unconfirmed'
            self.updated()
            return
        if state == 'removed':
            # The executor deleted it before starting: no history will ever follow.
            self._finish(row, 'execution_interrupted', {})

    def _finish(self, row, kind, detail):
        with self.lock:
            if row.result is not None:
                return
            row.state = 'finished'
            row.result = dict(prompt=self._queue_row(row), outputs={},
                status=dict(status_str='error', completed=False,
                            messages=[[kind, dict(detail, prompt_id=row.task.id)]]),
                freevideo_dispatch=self.receipt(row))
            self.scheduler.finished(row.task.id, False)
        if kind == 'execution_error':
            # No executor will report this failure, so the page hears it from here.
            node = next((key for key, value in row.prompt.items() if value.get('class_type') == 'FreeVideoGenerate'), None)
            self.server.send_sync('execution_error', dict(
                prompt_id=row.task.id, task_id=row.task.id, node_id=node, node_type=node and 'FreeVideoGenerate',
                exception_type='RuntimeError', traceback=[], executed=[], current_inputs={}, current_outputs={},
                **detail), row.task.client_id or None)
        self.updated()

    @staticmethod
    def _exited(worker):
        poll = getattr(getattr(worker, 'process', None), 'poll', None)
        return poll is not None and poll() is not None

    def _recovered(self, row, worker_id):
        # An acknowledgement lost after acceptance is resolved from the executor's own records.
        with self.lock:
            if row.worker_prompt_id is not None or row.result is not None:
                return False
            row.worker_prompt_id = worker_id
            row.state = 'cancelling' if row.cancelled else 'running'
        return True

    def cancel(self, prompt_id, node=None):
        with self.lock:
            row = self.records.get(prompt_id)
            if row is None or not self._matches(row, node) or row.result is not None:
                return 'finished'
            if row.state == 'cancelling':
                return 'cancelling'
            state = self.scheduler.cancel(prompt_id)
            if state == 'queued':
                row.state = 'finished'
                row.result = dict(prompt=self._queue_row(row), outputs={},
                    status=dict(status_str='error', completed=False,
                                messages=[['execution_interrupted', {'prompt_id': prompt_id}]]),
                    freevideo_dispatch=self.receipt(row))
            elif state == 'running':
                row.state, row.cancelled = 'cancelling', True
                if row.worker_prompt_id is not None:
                    self._background(self._interrupt(row))
            else:
                return 'finished'
        self.updated()
        return 'cancelled' if state == 'queued' else 'cancelling'

    def move_first(self, prompt_id, node=None, protected=lambda identity: False):
        with self.lock:
            row = self.records.get(prompt_id)
            if row is None or row.result is not None or not self._matches(row, node):
                return 'finished'
            if row.state != 'pending':
                return 'running'
            if protected(prompt_id):
                return 'unavailable'
            pending = sorted((r for r in self.records.values() if r.state == 'pending'), key=lambda r: r.number)
            if pending[0] is row:
                return 'first'
            # The contract has no reprioritization method. Requeue only pending
            # tasks, under the same lock used by submit/dispatch/cancel.
            row.number = pending[0].number - 1
            self._requeue_pending([row] + [item for item in pending if item is not row])
        self.updated()
        return 'moved'

    def _requeue_pending(self, pending):
        reorder = getattr(self.scheduler, 'reprioritize', None)
        if reorder is not None:
            reorder([row.task.id for row in pending])
            return
        for item in pending:
            if self.scheduler.cancel(item.task.id) != 'queued':
                raise RuntimeError('Scheduler pending state changed')
        for item in pending:
            self.scheduler.submit(item.task)

    def auxiliary_busy(self):
        service = getattr(self.server, '_freevideo_prompt_vlm', None)
        if service is not None and service.busy():
            return True
        # Use the same image-service lookup as Agent jobs; importing this adapter
        # does not start either service.
        from .agent.comfy import ComfyPort
        port = ComfyPort.__new__(ComfyPort)
        port.server = self.server
        return port.image_busy()

    async def pump(self):
        async with self.dispatch_lock:
            with self.lock:
                running = [row for row in self.records.values() if row.worker_prompt_id is not None
                           and row.result is None]
            await asyncio.gather(*(self._poll(row) for row in running))
            with self.lock:
                lost = [row for row in self.records.values() if row.state in ('unconfirmed', 'cancelling')
                        and row.worker is not None and row.worker_prompt_id is None and row.result is None]
            await asyncio.gather(*(self._reconcile(row) for row in lost))
            self._reap_exited()
            auxiliary_busy = self.auxiliary_busy()
            with self.lock:
                assignments = self.scheduler.dispatch() if not self.closed and not auxiliary_busy else []
                workers = {worker.device.uuid: worker for worker in self.workers()}
                selected, moved = [], False
                for task, device in assignments:
                    row = self.records[task.id]
                    row.worker, row.state = workers.get(device.uuid), 'submitting'
                    if row.worker is not None and self._exited(row.worker):
                        # Nothing was sent: put it back for another device.
                        self._mark_unavailable(row.worker)
                        self._requeue(row)
                        moved = True
                        continue
                    selected.append(row)
                if moved:
                    self._requeue_pending(sorted((r for r in self.records.values() if r.state == 'pending'),
                                                 key=lambda r: r.number))
            self._fail_unplaceable()
            await asyncio.gather(*(self._dispatch(row) for row in selected))
            if assignments:
                self.updated()

    async def _reconcile(self, row):
        try:
            worker_id = await asyncio.to_thread(self.worker_lookup, row.worker, row.task.id)
        except Exception:
            return
        if isinstance(worker_id, str) and worker_id and self._recovered(row, worker_id) and row.cancelled:
            await self._interrupt(row)

    def _any_executor(self):
        return any(worker.device.uuid not in self.unavailable for worker in self.workers())

    def _mark_unavailable(self, worker):
        uuid = worker.device.uuid
        if uuid in self.unavailable:
            return
        self.unavailable.add(uuid)
        mark = getattr(self.scheduler, 'mark_unavailable', None)
        if mark is not None:
            mark(uuid)
        if any(other.device.uuid not in self.unavailable and not self._exited(other) for other in self.workers()):
            logging.warning('[FreeVideo] The executor for GPU %s exited (%s); its tasks move to the remaining GPUs',
                            worker.device.index, exit_cause(worker))
        else:
            logging.warning('[FreeVideo] The executor for GPU %s exited (%s); no GPU remains',
                            worker.device.index, exit_cause(worker))

    def _requeue(self, row):
        # The executor is gone, so the attempt cannot still run: it may start again elsewhere.
        row.worker, row.worker_prompt_id, row.state = None, None, 'pending'
        self.scheduler.finished(row.task.id, False, retry=True)

    def _reap_exited(self):
        """A confirmed executor exit makes its device unavailable and moves its work to the others."""
        failed = []
        with self.lock:
            for worker in self.workers():
                if self._exited(worker):
                    self._mark_unavailable(worker)
            dead = [row for row in self.records.values() if row.result is None and row.worker is not None
                    and row.state in ('running', 'cancelling', 'unconfirmed')
                    and row.worker.device.uuid in self.unavailable]
            moved = False
            for row in dead:
                if row.cancelled:
                    failed.append((row, 'execution_interrupted', {}))
                elif row.chain_notified:
                    # Its part already reported a result; running it again would repeat that part.
                    failed.append((row, 'execution_error', dict(exception_message='Executor exited')))
                else:
                    row.exits += ('GPU %s: %s' % (row.worker.device.index, exit_cause(row.worker)),)
                    if len(row.exits) > 1:
                        # The task itself is the likely cause (host memory, a native crash):
                        # moving it again would take down the next GPU as well.
                        failed.append((row, 'execution_error', dict(
                            exception_message='%s (%s).' % (EXITED_AGAIN, ' / '.join(row.exits)))))
                        continue
                    self._requeue(row)
                    moved = True
            if moved:
                self._requeue_pending(sorted((r for r in self.records.values() if r.state == 'pending'),
                                             key=lambda r: r.number))
        for row, kind, detail in failed:
            self._finish(row, kind, detail)
        if dead:
            self.updated()

    def _fail_unplaceable(self):
        # With every executor gone nothing could ever start: say so instead of waiting silently.
        unplaceable = getattr(self.scheduler, 'unplaceable', None)
        if not self.unavailable or unplaceable is None:
            return
        with self.lock:
            rows = [self.records[identity] for identity in unplaceable()
                    if identity in self.records and self.records[identity].result is None]
            rows = [row for row in rows if self.scheduler.cancel(row.task.id) == 'queued']
        for row in rows:
            self._finish(row, 'execution_error', dict(exception_message=NO_EXECUTOR))

    async def _dispatch(self, row):
        if row.worker is None:
            row.state = 'unconfirmed'
            return
        dispatch = self.receipt(row)
        chain = row.extra.get('freevideo_chain') or {}
        if chain:
            from .comfy_chain import RUNNER
            if RUNNER is not None:
                node = chain.get('node') or next((node for node, value in row.prompt.items()
                                                  if value.get('class_type') == 'FreeVideoGenerate'), None)
                dispatch['chain_context'] = RUNNER.context(row.task.id, node)
                dispatch['chain_node'] = node
        try:
            worker_id = await asyncio.to_thread(self.run_prompt, row.worker, deepcopy(row.prompt),
                client_id=row.task.client_id, task_id=row.task.id,
                extra=dict(deepcopy(row.extra), freevideo_dispatch=dispatch))
            if not isinstance(worker_id, str) or not worker_id:
                raise RuntimeError('Executor returned no prompt ID')
            with self.lock:
                row.worker_prompt_id = worker_id
                cancelled = row.state == 'cancelling'
                row.state = 'cancelling' if cancelled else 'running'
            if cancelled:
                await self._interrupt(row)
        except Exception:
            # A lost acknowledgement does not prove the prompt was rejected.
            # Retain its reservation and never automatically replay it.
            with self.lock:
                row.state = 'unconfirmed'

    async def _poll(self, row):
        try:
            history = await asyncio.to_thread(self.worker_history, row.worker, row.worker_prompt_id)
        except Exception:
            return
        if not isinstance(history, dict):
            return
        # Accept both a single history entry and the native keyed response.
        history = history.get(row.worker_prompt_id, history)
        if not isinstance(history, dict) or 'status' not in history:
            return
        status = history.get('status') or {}
        if status.get('status_str') not in ('success', 'error'):
            return
        result = deepcopy(history)
        result['freevideo_dispatch'] = self.receipt(row)
        # The worker also retains node callbacks in native history, so a dropped
        # WebSocket event cannot lose continuation/seam receipts.
        prompt = result.get('prompt') or []
        extra = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
        finished = (extra.get('freevideo_dispatch') or {}).get('finished') or {}
        if not row.chain_notified:
            for node, data in finished.items():
                self._chain_finished(row, node, data)
        if prompt:
            result['prompt'] = list(prompt[:5])
            if len(result['prompt']) > 3:
                result['prompt'][3] = {key: value for key, value in extra.items()
                                       if key not in ('auth_token_comfy_org', 'api_key_comfy_org')}
        with self.lock:
            row.result, row.state = result, 'finished'
            self.scheduler.finished(row.task.id, status.get('status_str') == 'success')
        self.updated()

    def _event_row(self, worker, data):
        prompt_id = data.get('prompt_id') or data.get('worker_prompt_id')
        candidates = [row for row in self.records.values() if row.worker is worker and row.result is None]
        return next((row for row in candidates if row.worker_prompt_id == prompt_id
                     or row.task.id == data.get('task_id') or row.task.id == prompt_id), None)

    def event(self, worker, event):
        kind, data = event.get('type'), event.get('data')
        if not isinstance(data, dict):
            return
        with self.lock:
            row = self._event_row(worker, data)
            if row is None:
                return
            sent = data.get('prompt_id')
            recovered = False
            if (row.state in ('unconfirmed', 'cancelling') and row.worker_prompt_id is None
                    and isinstance(sent, str) and sent and sent != row.task.id):
                row.worker_prompt_id = sent
                row.state = 'cancelling' if row.cancelled else 'running'
                recovered = row.cancelled
            data = dict(deepcopy(data), prompt_id=row.task.id, task_id=row.task.id,
                        worker_prompt_id=data.get('prompt_id') or row.worker_prompt_id,
                        executor_id=worker.device.uuid)
            token = data.get('report_id')
            if isinstance(token, str):
                # A token is scoped to its executor; do not let another worker
                # replace an already registered report owner.
                previous = self.reports.get(token)
                if previous is None or previous is row:
                    self.reports[token] = row
        if recovered:
            self._background(self._interrupt(row))
        if kind == 'freevideo_progress':
            key = (row.task.id, str(data.get('node')), data.get('stream_id'))
            sequence = data.get('sequence')
            if isinstance(sequence, int):
                if self.seen.get(key, -1) >= sequence:
                    return
                self.seen[key] = sequence
            from .comfy_progress import STATE
            from .comfy_chain import RUNNER
            if RUNNER is not None:
                RUNNER.context(row.task.id, data.get('node'))
                chain = RUNNER.progress(data.get('node'), data, prompt_id=row.task.id)
                if chain is not None:
                    data['chain'] = chain
            data = STATE.publish(data.get('node'), data)
        elif kind == 'freevideo_chain_finished':
            self._chain_finished(row, data.get('node'), data)
            return
        elif kind == 'status':
            self.updated()
            return
        if kind == 'freevideo_progress':
            self.server.send_sync(kind, data)
        else:
            self.server.send_sync(kind, data, row.task.client_id or None)

    def _chain_finished(self, row, node, data):
        if row.chain_notified:
            return
        from .comfy_chain import RUNNER
        if RUNNER is not None:
            RUNNER.finished(row.task.id, node, data.get('output'), data.get('report_id'), data.get('continuation'))
            row.chain_notified = True
        token = data.get('report_id')
        if isinstance(token, str):
            self.reports.setdefault(token, row)

    async def download(self, token, *, preview=False):
        with self.lock:
            row = self.reports.get(token)
        if row is None:
            raise KeyError('Unknown report')
        worker = row.worker
        if worker is None:
            # Its executor exited and the task is waiting for another GPU.
            raise KeyError('Report unavailable')
        import aiohttp
        from urllib.parse import quote
        path = '/freevideo/%s/%s' % ('frame-preview' if preview else 'report', quote(token, safe=''))
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(worker.base_url + path) as response:
                if response.status != 200:
                    raise KeyError('Report unavailable')
                data = await response.read()
        if not preview:
            report = json.loads(data)
            report['freevideo_dispatch'] = self.receipt(row)
            data = json.dumps(report, ensure_ascii=False).encode('utf-8')
        return data


def register(server, *, scheduler=None, supervisor=None, envelope=None):
    """Install only after pool_enabled; single-device callers never import here."""
    from aiohttp import web
    from .comfy_pool import PoolSupervisor, worker_progress_stream
    if getattr(server, '_freevideo_front', None) is not None:
        return server._freevideo_front
    if scheduler is None:
        from .admission import AdmissionLedger
        from .gpu_pool import eligible_devices
        from .gpu_scheduler import Scheduler
        from .pool_resources import probe_host_memory
        scheduler = Scheduler(eligible_devices(), AdmissionLedger(probe_host_memory))
    if supervisor is None:
        supervisor = getattr(server, '_freevideo_pool', None)
    owns_supervisor = supervisor is None
    if owns_supervisor:
        from .gpu_pool import eligible_devices
        from .comfy_bridge import installation_root
        from .comfy_service import source_root
        supervisor = PoolSupervisor(eligible_devices(), root=installation_root(), source=source_root())
        server._freevideo_pool = supervisor
    server._freevideo_scheduler = scheduler
    front = Front(server, scheduler, supervisor.workers,
                  envelope=envelope or getattr(server, '_freevideo_task_envelope', None))
    server._freevideo_front = front
    server.get_queue_info = front.queue_info

    @web.middleware
    async def middleware(request, handler):
        path = request.path.removeprefix('/api') if request.path.startswith('/api/') else request.path
        if request.method == 'POST' and path == '/prompt':
            try:
                return web.json_response(await front.submit(await request.json(), request.headers))
            except PromptRejected as error:
                return web.json_response(error.body, status=400)
        if request.method == 'GET' and path == '/queue':
            running, pending = front.current_queue()
            return web.json_response(dict(queue_running=running, queue_pending=pending, freevideo_busy=front.busy()))
        if request.method == 'GET' and (path == '/history' or path.startswith('/history/')):
            identity = path[len('/history/'):] if path.startswith('/history/') else None
            try:
                maximum = int(request.query['max_items']) if 'max_items' in request.query else None
                offset = int(request.query.get('offset', -1))
                return web.json_response(front.history(identity, maximum, offset))
            except ValueError:
                raise web.HTTPBadRequest() from None
        if request.method == 'POST' and path in ('/queue', '/interrupt', '/history'):
            value = await request.json()
            if path == '/queue':
                ids = value.get('delete', [])
                if value.get('clear'):
                    ids = [row[1] for row in front.current_queue()[1]]
                pending = {row[1] for row in front.current_queue()[1]}
                for identity in ids:
                    if identity in pending:
                        front.cancel(identity)
            elif path == '/interrupt':
                identity = value.get('prompt_id')
                # Native Comfy's global stop explicitly targets all current
                # prompts. Resolve each owned task; never broadcast to an idle
                # worker or interrupt a task accepted after this snapshot.
                ids = [identity] if identity else [row[1] for row in front.current_queue()[0]]
                for identity in ids:
                    front.cancel(identity)
            else:
                with front.lock:
                    for identity in list(front.records) if value.get('clear') else value.get('delete', []):
                        row = front.records.get(identity)
                        if row is not None and row.result is not None:
                            del front.records[identity]
            return web.json_response({})
        return await handler(request)

    server.app.middlewares.append(middleware)

    async def stream(worker):
        while not front.closed and worker.device.uuid not in front.unavailable:
            try:
                async for event in worker_progress_stream(worker):
                    front.event(worker, event)
            except Exception:
                pass  # A disconnected worker remains busy until its history confirms exit.
            if not front.closed:
                await asyncio.sleep(1)

    async def poll():
        last_error = None
        while not front.closed:
            try:
                await front.pump()
                last_error = None
            except Exception as error:
                # Keep reservations on protocol errors; never report an unknown worker idle.
                kind = type(error).__name__
                if kind != last_error:
                    logging.warning('[FreeVideo] Pool scheduling pass failed (%s)', kind)
                last_error = kind
            await asyncio.sleep(.5)

    async def lifecycle(app):
        front.loop = asyncio.get_running_loop()
        tasks = []
        try:
            if owns_supervisor:
                await asyncio.to_thread(supervisor.start)
            tasks = [asyncio.create_task(stream(worker)) for worker in supervisor.workers()]
            tasks.append(asyncio.create_task(poll()))
            yield
        finally:
            front.closed = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*list(front.background), return_exceptions=True)
            if owns_supervisor:
                await asyncio.to_thread(supervisor.stop)
    server.app.cleanup_ctx.append(lifecycle)
    return front
