"""ComfyUI expert MCP (stdio): node index, errors, and local tech support.

    freevideo comfy-expert
    freevideo mcp --expert
    python -m freevideo_engine.agent.comfy_expert --root <install>

LM Studio can run this beside the FreeVideo Autopilot MCP. The catalog is a
compact on-disk index of GET /object_info, searched per query — the full dump
never enters the chat context.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .client import Client, LocalProxyHandler, default_root, endpoint_url
from .mcp import PROTOCOLS

INDEX_VERSION = 1
INDEX_NAME = 'comfy-nodes-index.json'
INSTRUCTIONS = """You are the ComfyUI expert for this local installation (tech support, nodes, workflow JSON).
Use comfy_nodes_search before naming any class_type; never invent nodes. Use comfy_node_schema for widget
defaults of matches you will put in a graph. Use comfy_errors for failed ComfyUI / FreeVideo runs.
Do not paste the whole node index into the chat. Workflow JSON is optional: API format
({ "1": { "class_type", "inputs" } }) for the user to save and Load in ComfyUI, only with class_types
returned by search. Unload a local LM Studio GPU model before MiniMax H3 generate only when that LLM
shares this computer and VRAM is tight. Cloud agents (Claude, Gemini, Cursor) and machines with spare
VRAM skip unload."""


def index_path(root):
    return Path(root).expanduser().resolve() / 'agent' / INDEX_NAME


def machine_of(root):
    try:
        value = json.loads((Path(root) / 'machine.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def custom_nodes_stamp(comfy_root):
    folder = Path(comfy_root) / 'custom_nodes' if comfy_root else None
    if folder is None or not folder.is_dir():
        return dict(mtime=0, packs=[])
    packs, latest = [], 0.0
    try:
        children = list(folder.iterdir())
    except OSError:
        return dict(mtime=0, packs=[])
    for child in children:
        name = child.name
        if name.startswith('.') or name in ('__pycache__', 'node_modules'):
            continue
        packs.append(name)
        try:
            latest = max(latest, child.stat().st_mtime)
            init = child / '__init__.py'
            if init.is_file():
                latest = max(latest, init.stat().st_mtime)
        except OSError:
            pass
    return dict(mtime=int(latest), packs=sorted(packs))


def compact_input(name, spec, required):
    row = dict(name=name, required=bool(required))
    if isinstance(spec, list) and spec:
        kind = spec[0]
        row['type'] = kind if isinstance(kind, str) else str(kind)
        extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        if 'default' in extra and extra['default'] is not None and not isinstance(extra['default'], (dict, list)):
            row['default'] = extra['default']
        options = extra.get('options') or extra.get('choices')
        if isinstance(options, list) and options:
            row['choices'] = len(options)
            row['examples'] = [str(item) for item in options[:8]]
        tooltip = extra.get('tooltip')
        if isinstance(tooltip, str) and tooltip.strip():
            row['hint'] = tooltip.strip()[:160]
    elif isinstance(spec, str):
        row['type'] = spec
    return row


def compact_node(name, info):
    if not isinstance(info, dict):
        return None
    inputs, names = [], []
    block = info.get('input') if isinstance(info.get('input'), dict) else {}
    for group, required in (('required', True), ('optional', False)):
        fields = block.get(group)
        if not isinstance(fields, dict):
            continue
        for key, spec in fields.items():
            names.append(key)
            inputs.append(compact_input(key, spec, required))
    outputs = info.get('output_name') or info.get('output') or []
    if not isinstance(outputs, list):
        outputs = [outputs] if outputs else []
    description = info.get('description') or info.get('help') or ''
    return dict(
        type=str(info.get('name') or name),
        title=str(info.get('display_name') or info.get('name') or name),
        category=str(info.get('category') or ''),
        module=str(info.get('python_module') or ''),
        description=str(description).strip()[:240],
        inputs=names[:48],
        input_detail=inputs[:48],
        outputs=[str(item) for item in outputs[:16]],
    )


def compact_catalog(object_info):
    nodes = []
    if not isinstance(object_info, dict):
        return nodes
    for name, info in object_info.items():
        row = compact_node(name, info)
        if row:
            nodes.append(row)
    nodes.sort(key=lambda row: (row['category'].lower(), row['type'].lower()))
    return nodes


def search_nodes(catalog, query='', category='', limit=12):
    needle = ' '.join(str(query or '').lower().split())
    group = str(category or '').lower().strip()
    scored = []
    for row in catalog:
        if group and group not in (row.get('category') or '').lower():
            continue
        hay = ' '.join([
            row.get('type') or '', row.get('title') or '', row.get('category') or '',
            row.get('module') or '', row.get('description') or '', ' '.join(row.get('inputs') or []),
        ]).lower()
        if not needle:
            score = 1
        elif hay == needle or (row.get('type') or '').lower() == needle:
            score = 100
        elif (row.get('type') or '').lower().startswith(needle) or (row.get('title') or '').lower().startswith(needle):
            score = 80
        elif needle in hay:
            score = 40 + hay.count(needle)
        else:
            parts = needle.split()
            if parts and all(part in hay for part in parts):
                score = 20
            else:
                continue
        scored.append((score, row['type'], row))
    scored.sort(key=lambda item: (-item[0], item[1].lower()))
    return [row for _, _, row in scored[:max(1, min(int(limit or 12), 40))]]


def slim_schema(info, *, choice_limit=12):
    node = compact_node(info.get('name') or 'node', info) or {}
    detail = []
    for row in node.get('input_detail') or []:
        item = {k: v for k, v in row.items() if k != 'examples' or not row.get('choices') or row['choices'] <= choice_limit}
        if row.get('choices', 0) > choice_limit:
            item['examples'] = (row.get('examples') or [])[:choice_limit]
            item['choices_omitted'] = row['choices'] - len(item['examples'])
        detail.append(item)
    node['input_detail'] = detail
    return node


class ComfyHttp:
    def __init__(self, url, timeout=60):
        self.url = endpoint_url(url)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(LocalProxyHandler())

    def get(self, path, query=None, timeout=None):
        if not path.startswith('/'):
            raise ValueError('path')
        url = self.url + path
        if query:
            url += '?' + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        request = urllib.request.Request(url, method='GET')
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            body = error.read().decode('utf-8', 'replace')[:800]
            raise RuntimeError('ComfyUI HTTP %s %s: %s' % (error.code, path, body)) from None
        except urllib.error.URLError as error:
            raise RuntimeError('ComfyUI is not reachable at %s (%s)' % (self.url, error.reason)) from None


def redact(text, roots):
    value = str(text)
    for root in roots:
        if not root:
            continue
        raw = str(root)
        value = value.replace(raw, '<local>')
        value = value.replace(raw.replace('\\', '/'), '<local>')
    home = str(Path.home())
    value = value.replace(home, '<home>').replace(home.replace('\\', '/'), '<home>')
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)[:4000]


class Expert:
    def __init__(self, client):
        self.client = client
        self.comfy = ComfyHttp(client.url, timeout=90)
        self._index = None

    def roots(self):
        machine = machine_of(self.client.root)
        return [self.client.root, machine.get('comfy_root'), machine.get('root'), Path.home()]

    def load_index(self, *, refresh=False):
        path = index_path(self.client.root)
        machine = machine_of(self.client.root)
        stamp = custom_nodes_stamp(machine.get('comfy_root'))
        cached = None
        if path.is_file() and not refresh:
            try:
                cached = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                cached = None
        if isinstance(cached, dict) and cached.get('version') == INDEX_VERSION and cached.get('stamp') == stamp \
                and isinstance(cached.get('nodes'), list):
            self._index = cached
            return cached, False
        info = self.comfy.get('/object_info', timeout=180)
        nodes = compact_catalog(info)
        categories = {}
        for row in nodes:
            key = row.get('category') or '(none)'
            categories[key] = categories.get(key, 0) + 1
        catalog = dict(version=INDEX_VERSION, generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       url=self.comfy.url, count=len(nodes), stamp=stamp, categories=categories, nodes=nodes)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        self._index = catalog
        return catalog, True

    def catalog(self, *, refresh=False):
        if refresh or self._index is None:
            return self.load_index(refresh=refresh)[0]
        return self._index

    def status(self):
        machine = machine_of(self.client.root)
        stamp = custom_nodes_stamp(machine.get('comfy_root'))
        stats = {}
        try:
            stats = self.comfy.get('/system_stats')
        except RuntimeError as error:
            stats = dict(error=str(error))
        catalog = None
        path = index_path(self.client.root)
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding='utf-8'))
                catalog = dict(count=cached.get('count'), generated_at=cached.get('generated_at'),
                               stale=cached.get('stamp') != stamp, path=str(path))
            except (OSError, ValueError):
                catalog = dict(path=str(path), stale=True)
        return dict(comfy=self.comfy.url, device=dict(backend=machine.get('device_backend'),
                    vram_limit_gib=machine.get('vram_gib')), custom_nodes=stamp['packs'][:80],
                    custom_nodes_count=len(stamp['packs']), index=catalog, system=stats)

    def schema(self, class_type):
        name = str(class_type or '').strip()
        if not name:
            raise ValueError('class_type is required')
        encoded = urllib.parse.quote(name, safe='')
        try:
            payload = self.comfy.get('/object_info/' + encoded)
        except RuntimeError:
            payload = {}
        info = payload.get(name) if isinstance(payload, dict) else None
        if not isinstance(info, dict) and isinstance(payload, dict) and len(payload) == 1:
            info = next(iter(payload.values()))
        if not isinstance(info, dict):
            catalog = (self.catalog().get('nodes') or [])
            match = next((row for row in catalog if row['type'].lower() == name.lower()), None)
            if match is None:
                raise LookupError('Unknown node: ' + name)
            return dict(from_index=True, **{k: match[k] for k in match if k != 'input_detail'},
                        input_detail=match.get('input_detail') or [])
        return slim_schema(dict(info, name=info.get('name') or name))

    def errors(self, limit=5):
        limit = max(1, min(int(limit or 5), 12))
        rows = []
        try:
            history = self.comfy.get('/history', query={'max_items': str(limit * 4)}, timeout=90)
        except RuntimeError as error:
            history = {}
            rows.append(dict(source='history', error=str(error)))
        if isinstance(history, dict):
            items = []
            for prompt_id, entry in list(history.items())[-40:]:
                if not isinstance(entry, dict):
                    continue
                status = entry.get('status') or {}
                messages = status.get('messages') or []
                text, kind = None, None
                for row in messages:
                    if not (isinstance(row, (list, tuple)) and row):
                        continue
                    if row[0] == 'execution_error':
                        data = row[1] if len(row) > 1 and isinstance(row[1], dict) else {}
                        text = str(data.get('exception_message') or data.get('exception_type') or 'execution_error')
                        kind = str(data.get('node_type') or data.get('node_id') or '')
                    elif row[0] == 'execution_interrupted':
                        text, kind = 'Interrupted', ''
                if text:
                    items.append(dict(source='comfy_history', prompt_id=str(prompt_id)[:80],
                                      node=kind, message=redact(text, self.roots())))
            rows.extend(items[-limit:])
        try:
            jobs = self.client.jobs(status='failed,interrupted', limit=limit)
            for job in jobs[:limit]:
                error = job.get('error') or {}
                rows.append(dict(source='freevideo_job', id=job.get('id'), status=job.get('status'),
                                 code=error.get('code'), message=redact(error.get('message') or '', self.roots()),
                                 next=error.get('next'), failure_kind=(error.get('details') or [{}])[0].get('failure_kind')
                                 if isinstance(error.get('details'), list) and error.get('details') else error.get('failure_kind')))
        except Exception as error:  # noqa: BLE001 - jobs are optional beside Comfy history
            rows.append(dict(source='freevideo_job', error=str(error)[:400]))
        machine = machine_of(self.client.root)
        for run in recent_runs(machine.get('comfy_root'), limit=2):
            try:
                from ..failure_details import generation_failure
                text = generation_failure(run)
            except Exception as error:  # noqa: BLE001
                text = str(error)
            rows.append(dict(source='freevideo_run', run=redact(str(run), self.roots()),
                             message=redact(text, self.roots())[:2500]))
        return dict(errors=rows[: limit + 4])


def recent_runs(comfy_root, limit=2):
    base = Path(comfy_root) / 'output' / 'FreeVideo' if comfy_root else None
    if base is None or not base.is_dir():
        return []
    found = []
    try:
        days = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)[:10]
    except OSError:
        return []
    for day in days:
        try:
            runs = sorted((p for p in day.iterdir() if p.is_dir()), reverse=True)
        except OSError:
            continue
        for run in runs:
            if (run / 'video.engine.log').is_file() or (run / 'video.request.json').is_file():
                found.append(run)
            if len(found) >= limit:
                return found
    return found


def tool_list():
    read = {'readOnlyHint': True, 'openWorldHint': False}
    write = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}
    string = {'type': 'string'}
    integer = {'type': 'integer'}
    return [
        dict(name='comfy_status', title='ComfyUI expert status',
             description='This ComfyUI URL, custom node packs, node-index freshness, and system_stats.',
             annotations=read, inputSchema={'type': 'object', 'properties': {}}),
        dict(name='comfy_nodes_search', title='Search installed nodes',
             description='Search the cached compact index of this ComfyUI\'s nodes (class_type, title, category, inputs). Rebuilds the index if custom nodes changed.',
             annotations=read, inputSchema={'type': 'object', 'properties': {
                 'query': dict(string, description='Name, category, or words to match.'),
                 'category': dict(string, description='Optional category substring, for example MiniMax or sampling.'),
                 'limit': dict(integer, description='Matches to return (default 12, max 40).')}}),
        dict(name='comfy_node_schema', title='One node schema',
             description='Widget types and defaults for one installed class_type. Prefer this after search, not the full object_info.',
             annotations=read, inputSchema={'type': 'object', 'properties': {
                 'class_type': dict(string, description='Exact ComfyUI class_type from search.')}, 'required': ['class_type']}),
        dict(name='comfy_index_refresh', title='Rebuild node index',
             description='Fetch GET /object_info once and rewrite the compact on-disk index. Use after installing or removing custom nodes.',
             annotations=write, inputSchema={'type': 'object', 'properties': {}}),
        dict(name='comfy_errors', title='Recent ComfyUI / FreeVideo errors',
             description='Last ComfyUI execution errors, failed FreeVideo agent jobs, and a redacted tail of recent FreeVideo run logs.',
             annotations=read, inputSchema={'type': 'object', 'properties': {
                 'limit': dict(integer, description='How many history/job failures to include (default 5).')}}),
        dict(name='comfy_queue', title='ComfyUI queue',
             description='What is running or waiting in this ComfyUI prompt queue.',
             annotations=read, inputSchema={'type': 'object', 'properties': {}}),
    ]


class Server:
    def __init__(self, expert, out=None):
        self.expert = expert
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.cancelled = set()
        self.tools = tool_list()
        self.calls = []

    def send(self, message):
        line = json.dumps(message, ensure_ascii=False)
        with self.lock:
            self.out.write(line + '\n')
            self.out.flush()

    def serve(self, stream=None):
        stream = stream or sys.stdin
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send(dict(jsonrpc='2.0', id=None, error=dict(code=-32700, message='Parse error')))
                continue
            if isinstance(message, list):
                for row in message:
                    self.handle(row)
            else:
                self.handle(message)
        for call in self.calls:
            call.join()

    def handle(self, message):
        method, identity = message.get('method'), message.get('id')
        params = message.get('params') or {}
        if method == 'notifications/cancelled':
            self.cancelled.add(params.get('requestId'))
            return
        if identity is None:
            return
        if method == 'tools/call':
            call = threading.Thread(target=self.respond, args=(identity, method, params), daemon=True)
            self.calls = [c for c in self.calls if c.is_alive()] + [call]
            call.start()
        else:
            self.respond(identity, method, params)

    def respond(self, identity, method, params):
        try:
            result = self.dispatch(identity, method, params)
            self.send(dict(jsonrpc='2.0', id=identity, result=result))
        except LookupError as error:
            self.send(dict(jsonrpc='2.0', id=identity, error=dict(code=-32601, message=str(error))))
        except Exception:  # noqa: BLE001
            self.send(dict(jsonrpc='2.0', id=identity, error=dict(code=-32603, message='An internal MCP transport error occurred.')))

    def dispatch(self, identity, method, params):
        from .. import __version__
        if method == 'initialize':
            version = params.get('protocolVersion')
            return dict(protocolVersion=version if version in PROTOCOLS else PROTOCOLS[0],
                        capabilities=dict(tools=dict(listChanged=False), prompts=dict(listChanged=False),
                                          resources=dict(listChanged=False)),
                        serverInfo=dict(name='comfyui-expert', title='ComfyUI expert', version=__version__),
                        instructions=INSTRUCTIONS)
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return dict(tools=self.tools)
        if method == 'tools/call':
            return self.call(params.get('name'), params.get('arguments') or {})
        if method == 'prompts/list':
            return dict(prompts=[])
        if method == 'resources/list':
            return dict(resources=[dict(uri='comfy://index', name='index-stats',
                                        title='Node index stats', mimeType='application/json')])
        if method == 'resources/read':
            if params.get('uri') != 'comfy://index':
                raise LookupError('Unknown resource')
            catalog = self.expert.catalog()
            stats = dict(count=catalog.get('count'), generated_at=catalog.get('generated_at'),
                         categories=catalog.get('categories'), path=str(index_path(self.expert.client.root)))
            return dict(contents=[dict(uri='comfy://index', mimeType='application/json',
                                       text=json.dumps(stats, ensure_ascii=False))])
        raise LookupError('Method not found: %s' % method)

    def call(self, name, args):
        args = dict(args or {})
        try:
            value = self.tool(name, args)
        except LookupError:
            return dict(content=[dict(type='text', text='Unknown tool')], isError=True)
        except Exception as error:  # noqa: BLE001
            text = redact(str(error), self.expert.roots())
            return dict(content=[dict(type='text', text=text)], structuredContent={'error': text}, isError=True)
        return dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False, indent=1))],
                    structuredContent=value if isinstance(value, dict) else {'items': value}, isError=False)

    def tool(self, name, args):
        expert = self.expert
        if name == 'comfy_status':
            return expert.status()
        if name == 'comfy_nodes_search':
            catalog = expert.catalog()
            matches = search_nodes(catalog.get('nodes') or [], args.get('query') or '',
                                   args.get('category') or '', args.get('limit') or 12)
            return dict(count=catalog.get('count'), generated_at=catalog.get('generated_at'),
                        matches=[{k: row[k] for k in ('type', 'title', 'category', 'module', 'description', 'inputs', 'outputs')
                                  if k in row} for row in matches])
        if name == 'comfy_node_schema':
            return expert.schema(args.get('class_type'))
        if name == 'comfy_index_refresh':
            catalog, rebuilt = expert.load_index(refresh=True)
            return dict(rebuilt=rebuilt, count=catalog.get('count'), generated_at=catalog.get('generated_at'),
                        path=str(index_path(expert.client.root)))
        if name == 'comfy_errors':
            return expert.errors(args.get('limit') or 5)
        if name == 'comfy_queue':
            return expert.comfy.get('/queue')
        raise LookupError(name)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='freevideo comfy-expert',
                                     description='ComfyUI expert MCP (node index, errors, stdio)')
    parser.add_argument('--root', type=Path, help='FreeVideo installation folder')
    parser.add_argument('--url', help='ComfyUI address (default: from the installation, else http://127.0.0.1:8188)')
    parser.add_argument('--print-config', action='store_true', help='Print LM Studio mcpServers JSON and exit')
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else default_root()
    if args.print_config:
        from .mcp import print_config
        print_config(root)
        return 0
    client = Client(args.url, root=root, name='comfyui-expert')
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    Server(Expert(client)).serve()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
