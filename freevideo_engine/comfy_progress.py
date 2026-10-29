"""Transient progress snapshots for reopened ComfyUI tabs; no request inputs."""
from copy import deepcopy
from pathlib import Path
import heapq
import json
import os
import threading
import time
import uuid

from .progress_timeline import Timeline


class ProgressState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.stream_id = uuid.uuid4().hex
        self.sequence = 0
        self.rows = {}
        self.timelines = {}
        self.report_timelines = {}
        self.lock = threading.Lock()

    def _record_timeline(self, node, value, message, now, measured=None):
        timeline = self.timelines.get(node)
        try:
            token = message.get('report_id')
            if timeline is None or message.get('new_request'):
                # Node eviction must not reset a report that is still retained.
                timeline = (self.report_timelines.get(token)
                            if not message.get('new_request') and isinstance(token, str) else None)
                if timeline is None:
                    timeline = Timeline(self.clock)
                # Insertion order tracks the age of the node's current run.
                self.timelines.pop(node, None)
                self.timelines[node] = timeline
                while len(self.timelines) > 64:
                    # Bound reports own their receipt independently of this map.
                    self.timelines.pop(next(iter(self.timelines)))
            if timeline.report_id is None and isinstance(token, str) and token:
                timeline.report_id = token
                self.report_timelines[token] = timeline
                while len(self.report_timelines) > 64:
                    expired = self.report_timelines.pop(next(iter(self.report_timelines)))
                    # A node may still point to an old run; discard its receipt too.
                    expired.rows.clear()
                    expired.pending = None
                    expired.state.clear()
                    expired._last_count = None
            if timeline.report_id is not None and timeline.report_id not in self.report_timelines:
                return
            # publish decides when the row was measured (an overlay keeps the previous time).
            timeline.record(value, now, message=message, measured=now if measured is None else measured)
        except Exception:
            # This receipt must never interfere with progress delivery.
            if timeline is not None:
                timeline.errors += 1

    def timeline(self, report_id):
        with self.lock:
            timeline = self.report_timelines.get(report_id)
            if timeline is None:
                return None
            try:
                return timeline.snapshot()
            except Exception:
                timeline.errors += 1
                return None

    def publish(self, node, message):
        # Only presentation fields. In particular, do not retain the prompt,
        # workflow, paths or full resource forecast here.
        keys = ('label', 'detail', 'phase', 'stage', 'timing_phase', 'done', 'total', 'unit', 'low_memory', 'reference_trimmed',
                'sampling_plan', 'bytes_per_second', 'block', 'blocks', 'elapsed_seconds', 'step_elapsed_seconds',
                'estimated_step_seconds', 'remaining_seconds', 'display_fraction',
                'estimated', 'uniform_remaining_steps', 'overall', 'retry', 'warning', 'kernel_cache_note',
                'new_request', 'reset', 'result', 'report_id', 'compatibility', 'memory_tight', 'references', 'chain',
                'frame_preview')
        value = deepcopy({key: message[key] for key in keys if key in message})
        if message.get('task_id'):
            value.update({key: message[key] for key in ('task_id', 'prompt_id', 'executor_id', 'worker_prompt_id')
                          if key in message})
        if isinstance(message.get('prediction'), dict):
            # The UI only needs a history marker, not the resource forecast.
            value['prediction'] = deepcopy({key: message['prediction'][key]
                                            for key in ('key', 'stage') if key in message['prediction']})
        node = str(node)
        key = (str(message['task_id']), node) if message.get('task_id') else node
        with self.lock:
            now = self.clock()
            old = self.rows.get(key)
            metadata_only = ('compatibility' in value and not value.get('new_request')
                             and not any(key in value for key in ('phase', 'stage', 'timing_phase', 'label', 'reset')))
            # A heartbeat carries a newer estimate, not engine progress (Harper's
            # bridge repeats the last message's fields with a fresh overall).
            heartbeat = isinstance(value.get('overall'), dict) and value['overall'].get('heartbeat') is True
            overall_measured = now
            if old and heartbeat:
                # Replace only overall: the row and its age keep describing the
                # last engine message, so a reopened page still sees a quiet engine.
                value = dict(old['message'], overall=value['overall'])
                value.pop('new_request', None)
                value.pop('reset', None)
                measured = old['measured']
            elif old and metadata_only:
                # A settings notice is not engine progress. Keep the current
                # activity and its timestamp, including in reopened snapshots.
                value = dict(old['message'], compatibility=value['compatibility'])
                value.pop('new_request', None)
                value.pop('reset', None)
                if isinstance(value.get('overall'), dict) and 'reset' in value['overall']:
                    value['overall'] = {key: item for key, item in value['overall'].items() if key != 'reset'}
                measured, overall_measured = old['measured'], old.get('overall_measured', old['measured'])
            elif old and value.get('warning'):
                # A memory warning must not replace the current sampling step.
                value = dict(old['message'], warning=value['warning'])
                measured, overall_measured = old['measured'], old.get('overall_measured', old['measured'])
            else:
                measured = now
                if message.get('task_id'):
                    age = message.get('age_seconds', 0.)
                    overall_age = message.get('overall_age_seconds', age)
                    if isinstance(age, (int, float)) and age >= 0:
                        measured = now - age
                    if isinstance(overall_age, (int, float)) and overall_age >= 0:
                        overall_measured = now - overall_age
                # The page starts a new request on these too and clears its notes.
                fresh = (value.get('new_request') or (value.get('overall') or {}).get('reset')
                         or (value.get('reset') and not value.get('phase')))
                if old and not fresh and old['message'].get('low_memory'):
                    value.setdefault('low_memory', old['message']['low_memory'])
                if old and not fresh and old['message'].get('compatibility'):
                    value.setdefault('compatibility', old['message']['compatibility'])
                if old and not fresh and 'references' in old['message']:
                    value.setdefault('references', old['message']['references'])
                # What a reopened page needs to tell the preview and the second
                # pass from a first pass, without repeating the whole plan.
                plan = message.get('sampling_plan')
                if isinstance(plan, dict):
                    value['plan_context'] = {key: deepcopy(plan[key]) for key in ('enabled', 'requested', 'preview', 'base_steps', 'refine_steps', 'second')
                                             if key in plan}
                elif old and not fresh and old['message'].get('plan_context'):
                    value.setdefault('plan_context', deepcopy(old['message']['plan_context']))
                if old and not value.get('new_request') and old['message'].get('retry'):
                    value.setdefault('retry', old['message']['retry'])
                if old and not value.get('new_request') and old['message'].get('report_id'):
                    value.setdefault('report_id', old['message']['report_id'])
                if old and not value.get('new_request') and old['message'].get('memory_tight'):
                    value.setdefault('memory_tight', old['message']['memory_tight'])
            tight = value.get('memory_tight')
            if isinstance(tight, dict):
                done, steps = value.get('done'), tight.get('first_pass_steps')
                # Model-loading snapshots have no sampling counters for a reopened page.
                if (value.get('stage') in ('latent_upscale', 'first_pass_reused')
                        or (value.get('retry') or {}).get('reuse_first_pass') is True
                        or value.get('phase') in ('decode', 'complete')
                        or (value.get('phase') == 'sampling' and isinstance(done, (int, float))
                            and isinstance(steps, (int, float)) and steps > 0 and done >= steps)):
                    value['memory_tight'] = dict(tight, first_pass_ended=True)
            self._record_timeline(key, value, message, now, measured)
            self.sequence += 1
            value.update(node=node, stream_id=self.stream_id, sequence=self.sequence)
            self.rows[key] = {'message': value, 'measured': measured, 'overall_measured': overall_measured}
            while len(self.rows) > 32:
                oldest = min(self.rows, key=lambda key: self.rows[key]['message']['sequence'])
                del self.rows[oldest]
            return self._aged(value, now, measured, overall_measured)

    @staticmethod
    def _aged(message, now, measured, overall_measured):
        # age_seconds: since the last engine message; overall_age_seconds: since
        # the estimate in `overall` (a heartbeat refreshes only that one).
        return dict(message, age_seconds=max(0., now - measured), overall_age_seconds=max(0., now - overall_measured))

    def snapshot(self):
        with self.lock:
            now = self.clock()
            return [self._aged(deepcopy(row['message']), now, row['measured'], row.get('overall_measured', row['measured']))
                    for row in self.rows.values()]


STATE = ProgressState()


class ReportDownloads:
    """Only server-registered runs are readable; clients never supply a path."""
    def __init__(self):
        self.runs = {}
        self.roots = set()
        self.dispatches = {}
        self.lock = threading.Lock()

    def register(self, output):
        token = uuid.uuid4().hex
        dispatch = None
        try:
            from server import PromptServer
            from .comfy_chain import execution_dispatch, deepcopy_dispatch
            context = execution_dispatch(PromptServer.instance)
            if context is not None:
                dispatch = deepcopy_dispatch(context)
                dispatch['worker_prompt_id'] = PromptServer.instance.last_prompt_id
        except (ImportError, AttributeError):
            pass
        with self.lock:
            self.runs[token] = Path(output)
            if dispatch is not None:
                self.dispatches[token] = dispatch
            while len(self.runs) > 64:
                expired = next(iter(self.runs))
                del self.runs[expired]
                self.dispatches.pop(expired, None)
        self._remember(token, Path(output))
        return token

    def _lookup(self, token):
        with self.lock:
            output = self.runs.get(token)
        if output is None and isinstance(token, str) and len(token) == 32 and token.isalnum():
            output = self._recall(token)
        return output

    @staticmethod
    def _index(output):
        # The run folder is <output>/FreeVideo/<date>/<run>; any other layout is not persisted.
        root = output.parent.parent.parent
        return (root / '.agent' / 'report-ids.json') if root.name == 'FreeVideo' else None

    def _remember(self, token, output):
        """Keep the token readable after the service restarts: a failed run's report is otherwise unreachable."""
        index = self._index(output)
        if index is None:
            return
        try:
            try:
                rows = json.loads(index.read_text('utf-8'))
            except (OSError, ValueError):
                rows = {}
            rows = {key: value for key, value in (rows if isinstance(rows, dict) else {}).items()
                    if isinstance(value, str)}
            self.roots.add(index.parent.parent)
            rows[token] = output.relative_to(index.parent.parent).as_posix()
            while len(rows) > 256:
                del rows[next(iter(rows))]
            index.parent.mkdir(parents=True, exist_ok=True)
            temporary = index.with_name('report-ids.%s.tmp' % uuid.uuid4().hex)
            temporary.write_text(json.dumps(rows), 'utf-8')
            os.replace(temporary, index)
        except Exception:
            pass  # Reports stay readable in this process; the index is only a convenience.

    def _recall(self, token):
        """The registered output of an earlier service, from the index beside its runs."""
        for root in self.roots:
            try:
                rows = json.loads((root / '.agent' / 'report-ids.json').read_text('utf-8'))
                relative = rows.get(token) if isinstance(rows, dict) else None
                if isinstance(relative, str) and relative and not Path(relative).is_absolute() and '..' not in Path(relative).parts:
                    return root / relative
            except (OSError, ValueError):
                continue
        return None

    def snapshot(self, token):
        with self.lock:
            restarted = token not in self.runs
        output = self._lookup(token)
        if output is None:
            raise KeyError('Unknown report')
        from .support_report import write
        from .diagnostics import is_link
        target = output.with_suffix('.live.debug.json')
        if is_link(output.parent) or target.is_symlink() or target.exists() and is_link(target):
            raise KeyError('Report unavailable')
        path = write(output, live=True, restarted=restarted)
        if path is None:
            raise RuntimeError('Report not available yet')
        data = path.read_bytes()
        with self.lock:
            dispatch = deepcopy(self.dispatches.get(token))
        if dispatch is not None:
            import json
            report = json.loads(data)
            report['freevideo_dispatch'] = dispatch
            data = json.dumps(report, ensure_ascii=False).encode('utf-8')
        return data


    def preview(self, token):
        """The newest frame preview picture of a registered run, or KeyError."""
        from .frame_preview import FILE
        from .diagnostics import is_link
        output = self._lookup(token)
        if output is None:
            raise KeyError('Unknown run')
        path = output.parent / FILE
        if is_link(output.parent) or path.is_symlink() or not path.is_file():
            raise KeyError('No preview')
        with path.open('rb') as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            raise KeyError('No preview')
        return data


REPORTS = ReportDownloads()


def progress_timeline(output):
    """Resolve only registered outputs, without retaining paths in receipts."""
    output = Path(output)
    with REPORTS.lock:
        token = next((token for token, path in reversed(REPORTS.runs.items())
                      if path == output), None)
    return STATE.timeline(token) if token is not None else None


def publish(server, node, message):
    try:
        from .comfy_chain import execution_dispatch
        dispatch = execution_dispatch(server)
        if dispatch is not None:
            message = dict(message, task_id=dispatch['task_id'], prompt_id=server.last_prompt_id,
                           executor_id=dispatch['executor_id'])
    except (AttributeError, KeyError):
        pass
    try:
        from .comfy_chain import RUNNER
        chain = RUNNER.progress(node, message) if RUNNER is not None else None
        if chain is not None:
            message = dict(message, chain=chain)
    except Exception:
        pass  # Background scheduling must never interrupt ordinary progress.
    value = STATE.publish(node, message)
    if server is not None:
        # Progress belongs to the running node, not to the tab that queued it.
        server.send_sync('freevideo_progress', value)
    return value


def cancel_request(queue, prompt_id, node_id, interrupt):
    """Remove or interrupt exactly this FreeVideo request under the queue lock."""
    with queue.mutex:
        running, pending = queue.get_current_queue()
        def matches(row):
            return (row[1] == prompt_id
                    and row[2].get(str(node_id), {}).get('class_type') == 'FreeVideoGenerate')
        if any(matches(row) for row in pending):
            queue.delete_queue_item(matches)
            return 'cancelled'
        if any(matches(row) for row in running):
            # Hold the same lock used by task_done/get: the next request cannot
            # start between checking the ID and setting Comfy's interrupt flag.
            interrupt()
            return 'cancelling'
        return 'finished'


def move_request_first(queue, prompt_id, node_id, protected=lambda prompt_id: False):
    """Put exactly this waiting FreeVideo request ahead of every other waiting one.

    Comfy runs the smallest queue number first, so the request takes a number
    below the current smallest. Everything else keeps its relative order.
    """
    with queue.mutex:
        waiting = queue.queue
        row = next((row for row in waiting if row[1] == prompt_id
                    and row[2].get(str(node_id), {}).get('class_type') == 'FreeVideoGenerate'), None)
        if row is None:
            running = any(item[1] == prompt_id for item in queue.currently_running.values())
            return 'running' if running else 'finished'
        if protected(prompt_id):
            return 'unavailable'
        if row is min(waiting):
            return 'first'
        index = next(i for i, item in enumerate(waiting) if item is row)
        waiting[index] = (min(item[0] for item in waiting) - 1,) + tuple(row[1:])
        heapq.heapify(waiting)
        queue.server.queue_updated()
        return 'moved'


def register():
    import asyncio
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_progress', False):
        return
    # The async control entrypoint owns pool construction and shutdown.
    server._freevideo_progress = True
    try:
        import folder_paths
        REPORTS.roots.add(Path(folder_paths.get_output_directory()).resolve() / 'FreeVideo')
    except Exception:
        pass  # Without it only runs of this process (and their index folders) are found.
    report_lock = asyncio.Lock()

    @server.routes.get('/freevideo/report/{report_id}')
    async def report(request):
        async with report_lock:
            try:
                front = getattr(server, '_freevideo_front', None)
                data = (await front.download(request.match_info['report_id']) if front is not None
                        else await asyncio.to_thread(REPORTS.snapshot, request.match_info['report_id']))
            except KeyError:
                raise web.HTTPNotFound(text='Report unavailable') from None
            except (OSError, RuntimeError):
                raise web.HTTPServiceUnavailable(text='Report not ready; try again') from None
        return web.Response(body=data, content_type='application/json', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': 'attachment; filename="FreeVideo-report-%s.json"' % time.strftime('%Y%m%d-%H%M%S')})

    @server.routes.get('/freevideo/frame-preview/{report_id}')
    async def frame_preview(request):
        try:
            front = getattr(server, '_freevideo_front', None)
            data = (await front.download(request.match_info['report_id'], preview=True) if front is not None
                    else await asyncio.to_thread(REPORTS.preview, request.match_info['report_id']))
        except (KeyError, OSError):
            raise web.HTTPNotFound(text='') from None
        return web.Response(body=data, content_type='image/jpeg', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @server.routes.get('/freevideo/feedback-info')
    async def feedback_info(request):
        from . import feedback
        info = await asyncio.to_thread(feedback.collect, request.query.get('report_id'))
        return web.json_response(info, headers={'Cache-Control': 'no-store'})

    @server.routes.get('/freevideo/progress')
    async def progress(request):
        return web.json_response({'progress': STATE.snapshot()},
                                 headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/cancel')
    async def cancel(request):
        value = await request.json()
        prompt_id, node_id = value.get('prompt_id'), value.get('node_id')
        if not isinstance(prompt_id, str) or not prompt_id or not isinstance(node_id, str):
            return web.json_response({'error': 'A request and node ID are required'}, status=400)
        front = getattr(server, '_freevideo_front', None)
        if front is not None:
            return web.json_response({'status': front.cancel(prompt_id, node_id)})
        import nodes
        return web.json_response({'status': cancel_request(server.prompt_queue, prompt_id,
            node_id, nodes.interrupt_processing)})

    @server.routes.post('/freevideo/queue/first')
    async def queue_first(request):
        value = await request.json()
        prompt_id, node_id = value.get('prompt_id'), value.get('node_id')
        if not isinstance(prompt_id, str) or not prompt_id or not isinstance(node_id, str):
            return web.json_response({'error': 'A request and node ID are required'}, status=400)
        from . import comfy_chain
        runner = comfy_chain.RUNNER
        front = getattr(server, '_freevideo_front', None)
        if front is not None:
            return web.json_response({'status': front.move_first(prompt_id, node_id,
                runner.owns if runner is not None else (lambda prompt_id: False))})
        return web.json_response({'status': move_request_first(server.prompt_queue, prompt_id, node_id,
            runner.owns if runner is not None else (lambda prompt_id: False))})
