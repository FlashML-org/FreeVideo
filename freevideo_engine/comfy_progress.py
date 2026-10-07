"""Transient progress snapshots for reopened ComfyUI tabs; no request inputs."""
from copy import deepcopy
from pathlib import Path
import threading
import time
import uuid


class ProgressState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.stream_id = uuid.uuid4().hex
        self.sequence = 0
        self.rows = {}
        self.lock = threading.Lock()

    def publish(self, node, message):
        # Only presentation fields. In particular, do not retain the prompt,
        # workflow, paths, device identity or full resource forecast here.
        keys = ('label', 'detail', 'phase', 'stage', 'timing_phase', 'done', 'total',
                'unit', 'bytes_per_second', 'block', 'blocks', 'elapsed_seconds', 'step_elapsed_seconds',
                'estimated_step_seconds', 'remaining_seconds', 'display_fraction',
                'estimated', 'uniform_remaining_steps', 'overall', 'retry', 'warning', 'kernel_cache_note',
                'new_request', 'reset', 'result', 'report_id')
        value = deepcopy({key: message[key] for key in keys if key in message})
        node = str(node)
        with self.lock:
            now = self.clock()
            old = self.rows.get(node)
            if old and value.get('warning'):
                # A memory warning must not replace the current sampling step.
                value = dict(old['message'], warning=value['warning'])
                measured = old['measured']
            else:
                measured = now
                if old and not value.get('new_request') and old['message'].get('retry'):
                    value.setdefault('retry', old['message']['retry'])
                if old and not value.get('new_request') and old['message'].get('report_id'):
                    value.setdefault('report_id', old['message']['report_id'])
            self.sequence += 1
            value.update(node=node, stream_id=self.stream_id, sequence=self.sequence)
            self.rows[node] = {'message': value, 'measured': measured}
            while len(self.rows) > 32:
                oldest = min(self.rows, key=lambda key: self.rows[key]['message']['sequence'])
                del self.rows[oldest]
            return dict(value, age_seconds=max(0., now - measured))

    def snapshot(self):
        with self.lock:
            now = self.clock()
            return [dict(deepcopy(row['message']), age_seconds=max(0., now-row['measured']))
                    for row in self.rows.values()]


STATE = ProgressState()


class ReportDownloads:
    """Only server-registered runs are readable; clients never supply a path."""
    def __init__(self):
        self.runs = {}
        self.lock = threading.Lock()

    def register(self, output):
        token = uuid.uuid4().hex
        with self.lock:
            self.runs[token] = Path(output)
            while len(self.runs) > 64:
                del self.runs[next(iter(self.runs))]
        return token

    def snapshot(self, token):
        with self.lock:
            output = self.runs.get(token)
        if output is None:
            raise KeyError('Unknown report')
        from .support_report import write
        from .diagnostics import is_link
        target = output.with_suffix('.live.debug.json')
        if is_link(output.parent) or target.is_symlink() or target.exists() and is_link(target):
            raise KeyError('Report unavailable')
        path = write(output, live=True)
        if path is None:
            raise RuntimeError('Report not available yet')
        return path.read_bytes()


REPORTS = ReportDownloads()


def publish(server, node, message):
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


def register():
    import asyncio
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_progress', False):
        return
    server._freevideo_progress = True
    report_lock = asyncio.Lock()

    @server.routes.get('/freevideo/report/{report_id}')
    async def report(request):
        async with report_lock:
            try:
                data = await asyncio.to_thread(REPORTS.snapshot, request.match_info['report_id'])
            except KeyError:
                raise web.HTTPNotFound(text='Report unavailable') from None
            except (OSError, RuntimeError):
                raise web.HTTPServiceUnavailable(text='Report not ready; try again') from None
        return web.Response(body=data, content_type='application/json', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': 'attachment; filename="FreeVideo-report-%s.json"' % time.strftime('%Y%m%d-%H%M%S')})

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
        import nodes
        return web.json_response({'status': cancel_request(server.prompt_queue, prompt_id,
            node_id, nodes.interrupt_processing)})
