"""FreeVideo as an MCP server (stdio, JSON-RPC 2.0), for Claude Code, Claude Desktop, Cursor and other agents.

    ./freevideo mcp                     # Linux x86_64
    freevideo.cmd mcp                   # Windows
    ./freevideo mcp --print-config      # Linux client configuration
    # macOS / other Unix platforms: use the installed Python and source folder.
    env FREEVIDEO_AGENT=1 PYTHONPATH=/path/to/FreeVideo/engine-source \
        /path/to/installed/python -X utf8 -m freevideo_engine.agent.mcp --root /path/to/FreeVideo
    # Add --print-config to print client configuration.

Standard library only; it talks to the running ComfyUI through the agent API.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import sys
import threading
import time

from .client import ApiError, Client, DONE

PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = """FreeVideo generates videos (MiniMax H3) on this computer's GPU.
Start with freevideo_status and freevideo_capabilities. Write prompts as the guide says (freevideo_prompt_guide): English sections
integrated_multimodal_description / overall_soundscape / non_diegetic_music, [Shot N] cuts, (S1) <d>[Language] ...</d>
dialogue. Generation uses continuous chains and takes minutes: submit, then call freevideo_wait (at most 25 seconds)
or freevideo_job. Interrupted/failed/cancelled generation can continue with freevideo_resume; completed blocks stay.
Chain shots by starting the next shot from the previous one's last frame (media.first = {"from_job": id,
"frame": "last"}), look at results with freevideo_look, ask the local VLM with freevideo_ask, and join clips
with freevideo_assemble. For durable filming, use freevideo_drama_plan → start → get → sheet → choose/reshoot → finish.
Cast first: search freevideo_cast_list, reuse the card the user named or one that fits, create a card only if none does.
If paused is set, wait for the user using get with since and wait=25. Only the user may pause/resume.
Bring your own footage: render complete video clips on your computer with ffmpeg/Canvas, baking effects into pixels.
freevideo_import_media reads your local file and uploads content (up to 2 GiB); reuse ref for safe retries.
Poll freevideo_imports about every two seconds until state=ready (failed is terminal), then insert its source=import:<id>
at an integer frame in a 24 fps project, add subtitles/audio, and export. GET /guide bring_your_own_footage explains
authenticated source downloads, dimensions and audio. No second video track or blend mode is needed."""


def tool_list():
    from .schema import mcp_tool_schema
    read = {'readOnlyHint': True, 'openWorldHint': False}
    write = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}
    rows = [dict(name='freevideo_status', title='FreeVideo status', description='Readiness, limits, quality levels, aspects, installed LoRAs, local VLM and the queue.', annotations=read),
        dict(name='freevideo_capabilities', title='Device capabilities', description='Read the same capability cache as the studio. computing means retry after retry_after_seconds.', annotations=read),
        dict(name='freevideo_asset', title='Retrieve a library asset', description='Download a saved video or thumbnail through authenticated HTTP to target. Use a library_id from a job or library.', annotations=write),
        dict(name='freevideo_prompt_guide', title='H3 prompt guide', description='How to write prompts: sections, shots and cuts, camera motion, dialogue, references, short-drama tips, and a full example.', annotations=read),
        dict(name='freevideo_schema', title='Job JSON Schemas', description='JSON Schemas of every job kind and of image edit recipes.', annotations=read),
        dict(name='freevideo_plan', title='Plan a job', description="Validate a job without running it: actual frames/seconds, sampling passes, warnings and a time estimate from this computer's history. Same fields as freevideo_generate.", annotations=read),
        dict(name='freevideo_generate', title='Generate a video', description='Generate one video from an H3 prompt, up to 60 seconds. Under 30 s defaults to one generation if the device supports this size and quality; unknown limits use 15 s. Otherwise the video is cut into as few parts as possible, each continuing part 10 s or longer where the length allows and never under 5 s, and how many parts depends on how long this computer can generate in one run; the parts continue and join automatically. Prefer a new shot with its reference pictures wherever the film can cut; continue only a take, action or sound that must stay unbroken. Use this for any single video, also after freevideo_enhance_prompt; the drama tools are only for a film of several separately chosen shots. Optionally from first/last frames or references (files, local paths, or frames of earlier jobs). Returns the job; with wait, its result (video path and URL).', annotations=write),
        dict(name='freevideo_generate_image', title='Generate a picture', description='Generate a picture from a prompt; with images, change or combine input pictures; with people, make a first frame with them near the places you say, in the scene from scene_image or the prompt. The result is an input file to use as a first frame, as a reference, or in images again.', annotations=write),
        dict(name='freevideo_submit_batch', title='Submit a storyboard', description='Submit several jobs at once. Each row is a job object with "kind" (generate default, assemble, vlm, enhance, image) and an optional "ref"; later rows use "@ref" wherever a job id is expected (from_job, after, clips, refine_from). FreeVideo runs them in dependency order.', annotations=write),
        dict(name='freevideo_job', title='Job status', description='Status, progress, result or error of a job.', annotations=read),
        dict(name='freevideo_wait', title='Wait for a job or the user', description="Wait at most 25 seconds for a job (with id) or, without id, at most 50 seconds for what the user says in FreeVideo (inbox). again=true means nothing new yet: call it again. A job wait returns early when the user says something. If the job is still active, call it again with the same ID; do not resubmit.", annotations=read),
        dict(name='freevideo_jobs', title='List jobs', description='Recent agent jobs, optionally by status (comma-separated), project or kind.', annotations=read),
        dict(name='freevideo_cancel', title='Cancel a job', description='Cancel a waiting, queued or running job.', annotations={'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': False}),
        dict(name='freevideo_resume', title='Resume generation', description='Resume a failed, interrupted or cancelled generation; only unfinished blocks run. Poll the same job ID.', annotations=write),
        dict(name='freevideo_add_media', title='Add media', description='Read a local image, video or audio file and upload its content; returns the "file" to use in media.', annotations=write),
        dict(name='freevideo_import_media', title='Import footage', description='Read a local video or audio file and upload it into the editor. Returns import.source (import:<id>) and state. Poll freevideo_imports until ready, then insert that source into a project. Same ref and content safely replay.', annotations=write),
        dict(name='freevideo_imports', title='Imported footage', description='List editor imports with source, state (preparing/ready/failed), fraction and authenticated preview URLs. Wait for ready before insert; failed is terminal.', annotations=read),
        dict(name='freevideo_look', title='Look at media', description='Return a picture you can see: one frame of a video (at = first, last or seconds) or a contact sheet of timed frames (mode = sheet), or an image file. Use it to review shots.', annotations=read),
        dict(name='freevideo_edit_image', title='Edit an image', description="Crop/rotate/flip/pad/adjust an image, add text, shapes or image overlays, or remove burned-in captions (remove_text), without the browser. The result is a new input file plus an editor document a person can reopen in FreeVideo's image editor.", annotations=write),
        dict(name='freevideo_ask', title='Ask the local VLM', description='Ask FreeVideo\'s local vision-language model about images or sampled frames of a video (e.g. "Does this shot match its description? Answer as JSON {score, issues}").', annotations=write),
        dict(name='freevideo_enhance_prompt', title='Enhance a prompt', description='Rewrite a rough idea (any language) into a complete H3 prompt with the local VLM. Images become <Picture N> references.', annotations=write),
        dict(name='freevideo_assemble', title='Assemble clips', description='Join finished clips (job ids, saved video ids or files) into one video with cuts or crossfades; trims with start/end.', annotations=write),
        dict(name='freevideo_library', title='Saved videos', description='Saved FreeVideo videos, newest first, with prompts and the agent job that made them.', annotations=read)]
    from .schema import DRAMA_OPERATIONS
    rows.extend(dict(name='freevideo_drama_' + name, title='Film: ' + name, description=summary,
                     annotations=read if method == 'GET' or name == 'plan' else write)
                for name, (method, _, summary) in DRAMA_OPERATIONS.items())
    from .schema import CAST_OPERATIONS
    rows.extend(dict(name='freevideo_cast_' + name, title='Cast: ' + name, description=summary,
                     annotations=read if method == 'GET' else dict(write, destructiveHint=True) if name == 'delete' else write)
                for name, (method, _, summary) in CAST_OPERATIONS.items())
    from .schema import PROJECT_OPERATIONS, PROJECT_TOOLS
    rows.extend(dict(name=PROJECT_TOOLS[name], title='Editing project: ' + name, description=summary,
                     annotations=read if method == 'GET' else write)
                for name, (method, _, summary) in PROJECT_OPERATIONS.items())
    from .schema import WHITEBOX_OPERATIONS, WHITEBOX_TOOLS
    rows.extend(dict(name=WHITEBOX_TOOLS[name], title='White model: ' + name, description=summary,
                     annotations=read if method == 'GET' else write)
                for name, (method, _, summary) in WHITEBOX_OPERATIONS.items())
    from .schema import IMAGE_OPERATIONS, IMAGE_TOOLS
    rows.extend(dict(name=IMAGE_TOOLS[name], title='Image: ' + name, description=summary,
                     annotations=read if method == 'GET' else write)
                for name, (method, _, summary) in IMAGE_OPERATIONS.items())
    from .schema import SESSION_OPERATIONS, SESSION_TOOLS
    rows.extend(dict(name=tool, title='Session: ' + name, description=SESSION_OPERATIONS[name][2],
                     annotations=read if SESSION_OPERATIONS[name][0] == 'GET' else write)
                for name, tool in SESSION_TOOLS.items())
    for row in rows:
        row['inputSchema'] = mcp_tool_schema(row['name'])
    return rows


PROMPTS = [
    dict(name='short_drama', title='Make a short drama',
         description='Plan and produce a short drama: script, consistent characters, chained shots, review, assembly.',
         arguments=[dict(name='idea', description='The story idea', required=True),
                    dict(name='shots', description='Number of shots (default 4)', required=False),
                    dict(name='aspect', description='9:16 (default) or 16:9', required=False)]),
]


def short_drama(arguments):
    idea = arguments.get('idea') or 'a short story'
    shots = arguments.get('shots') or '4'
    aspect = arguments.get('aspect') or '9:16'
    return f"""Produce a {shots}-shot short drama with FreeVideo from this idea: {idea}

1. Call freevideo_status and freevideo_prompt_guide. Write a screenplay with characters and {shots} shots.
2. Call freevideo_drama_plan with screenplay and takes_per_shot. Inspect the prompts, then freevideo_drama_start.
3. Use freevideo_drama_get with since=revision and wait=25. If paused is set, stop acting and long-wait.
4. Follow todo: freevideo_drama_sheet shows each take, then freevideo_drama_choose or freevideo_drama_reshoot
   with a concrete note. Continuations wait for the previous chosen take. Prefer aspect "{aspect}" and quality light.
5. When every shot is chosen, freevideo_drama_finish accepts assembly; get until the output library_id is ready.
Use ref to replay writes safely. Ordinary job batches remain available through freevideo_submit_batch."""


def preload():
    """Load what tool calls import before stdin is read.

    Tool calls run on threads while this thread waits on stdin. On Windows a DLL loaded for the first
    time then (numpy's OpenBLAS, loaded through spec) can wait for the client's next line, so the first
    tool call would stall."""
    from . import errors, schema, spec  # noqa: F401
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pass


class Server:
    def __init__(self, client, out=None):
        self.client = client
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.cancelled = set()
        self.tools = None
        self.calls = []

    def send(self, message):
        line = json.dumps(message, ensure_ascii=False)
        with self.lock:
            self.out.write(line + '\n')
            self.out.flush()

    def serve(self, stream=None):
        stream = stream or sys.stdin
        try:
            self.client.hello(via='mcp')
        except (ApiError, AttributeError):
            pass  # Starting before FreeVideo is allowed; later authenticated requests mark it online.
        preload()
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
            call.join()  # input closed: finish answering what was asked

    def handle(self, message):
        method, identity = message.get('method'), message.get('id')
        params = message.get('params') or {}
        if method == 'notifications/cancelled':
            self.cancelled.add(params.get('requestId'))
            return
        if identity is None:
            return  # other notifications (initialized, ...)
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
        except Exception as error:  # noqa: BLE001 - report, keep serving
            self.send(dict(jsonrpc='2.0', id=identity, error=dict(code=-32603, message='An internal MCP transport error occurred.')))

    def dispatch(self, identity, method, params):
        from .. import __version__
        if method == 'initialize':
            from .guide import installation_language
            language = 'Chinese' if installation_language(getattr(self.client, 'root', None)) == 'zh' else 'English'
            name = (params.get('clientInfo') or {}).get('name')
            if isinstance(name, str) and name.strip():
                self.client.name = name[:80]
                try:
                    self.client.hello(via='mcp')
                except (ApiError, AttributeError):
                    pass
            version = params.get('protocolVersion')
            return dict(protocolVersion=version if version in PROTOCOLS else PROTOCOLS[0],
                        capabilities=dict(tools=dict(listChanged=False), prompts=dict(listChanged=False),
                                          resources=dict(listChanged=False)),
                        serverInfo=dict(name='freevideo', title='FreeVideo', version=__version__),
                        instructions=INSTRUCTIONS + '\nThis installation\'s interface language is ' + language +
                        '; reply to the user in ' + language + ', and follow their language if they switch.')
        if method == 'ping':
            return {}
        if method == 'tools/list':
            if self.tools is None:
                self.tools = tool_list()
            return dict(tools=self.tools)
        if method == 'tools/call':
            return self.call(identity, params.get('name'), params.get('arguments') or {},
                             (params.get('_meta') or {}).get('progressToken'))
        if method == 'prompts/list':
            return dict(prompts=PROMPTS)
        if method == 'prompts/get':
            if params.get('name') != 'short_drama':
                raise LookupError('Unknown prompt')
            return dict(description=PROMPTS[0]['description'], messages=[dict(role='user', content=dict(
                type='text', text=short_drama(params.get('arguments') or {})))])
        if method == 'resources/list':
            return dict(resources=[
                dict(uri='freevideo://guide', name='guide', title='H3 prompt guide', mimeType='application/json'),
                dict(uri='freevideo://schema', name='schema', title='Job JSON Schemas', mimeType='application/json')])
        if method == 'resources/read':
            uri = params.get('uri')
            value = {'freevideo://guide': self.client.guide, 'freevideo://schema': self.client.schema}.get(uri)
            if value is None:
                raise LookupError('Unknown resource')
            return dict(contents=[dict(uri=uri, mimeType='application/json', text=json.dumps(value(), ensure_ascii=False))])
        raise LookupError('Method not found: %s' % method)

    # Tools -----------------------------------------------------------------------------------------------
    def call(self, identity, name, arguments, progress_token):
        from . import schema, spec, errors
        try:
            schema.validate(schema.tool_schema(name), arguments)
            arguments = dict(arguments)
            session = arguments.pop('session', None)
            headers = getattr(self.client, 'call_headers', None)
            if headers is not None:
                headers.value = dict({'X-FreeVideo-Tool': name}, **({'X-FreeVideo-Session': session} if session else {}))
            try:
                value, images = self.tool(identity, name, arguments, progress_token)
            finally:
                if headers is not None:
                    headers.value = {}
        except spec.SpecError as error:
            problem = errors.validation(error.errors)
            return dict(content=[dict(type='text', text=errors.text(problem))], structuredContent={'error': problem}, isError=True)
        except LookupError:
            problem = errors.build('invalid_request')
            return dict(content=[dict(type='text', text=errors.text(problem))], structuredContent={'error': problem}, isError=True)
        except (OSError, ValueError):
            problem = errors.build('invalid_media')
            return dict(content=[dict(type='text', text=errors.text(problem))], structuredContent={'error': problem}, isError=True)
        except ApiError as error:
            body = dict(error=dict(code=error.code, message=error.message, **error.details))
            content = [dict(type='text', text=errors.text(body['error']))]
            if error.inbox:
                body['inbox'] = error.inbox
                content.append(dict(type='text', text=json.dumps(dict(inbox=error.inbox), ensure_ascii=False, indent=1)))
            return dict(content=content, structuredContent=body, isError=True)
        if isinstance(value, dict) and value.get('error'):
            return dict(content=[dict(type='text', text=errors.text(value['error']))],
                        structuredContent=value, isError=True)
        content = [dict(type='text', text=json.dumps(value, ensure_ascii=False, indent=1))]
        content += images
        return dict(content=content, structuredContent=value if isinstance(value, dict) else {'items': value},
                    isError=False)

    def tool(self, identity, name, args, token):
        c = self.client
        wait = args.pop('wait', None)
        if name == 'freevideo_status':
            return c.capabilities(), []
        if name == 'freevideo_capabilities':
            return c.capability_table(**args), []
        if name == 'freevideo_asset':
            return dict(id=args['id'], path=str(c.asset(args['id'], args['target'],
                                                      thumbnail=args.get('thumbnail', False)))), []
        if name == 'freevideo_prompt_guide':
            return c.guide(), []
        if name == 'freevideo_schema':
            return c.schema(), []
        if name == 'freevideo_plan':
            return c.plan(dict(args, kind='generate')), []
        from .schema import TOOL_KINDS
        if name in TOOL_KINDS:
            kind = TOOL_KINDS[name]
            takes = args.pop('takes', 1)
            response = c.post('/jobs', dict(args, kind=kind, **({'takes': takes} if takes != 1 else {})))
            jobs = response['jobs']
            if wait is None:
                wait = 0 if kind == 'generate' else 25
            if wait and len(jobs) == 1:
                return self.wait(identity, jobs[0]['id'], wait, token), []
            return (jobs[0] if len(jobs) == 1 else dict(jobs=jobs)), []
        if name.startswith('freevideo_drama_'):
            from .schema import DRAMA_OPERATIONS
            operation = name.removeprefix('freevideo_drama_')
            method, path, _ = DRAMA_OPERATIONS[operation]
            for key in ('id', 'shot'):
                if '{' + key + '}' in path:
                    path = path.replace('{' + key + '}', args.pop(key))
            if method == 'GET' and wait is not None:
                args['wait'] = wait
            value = c.get(path, **args) if method == 'GET' else c.post(path, args)
            images = []
            if operation == 'sheet':
                value = dict(value, takes=[dict(row) for row in value['takes']])
                for row in value['takes']:
                    images.append(dict(type='image', **row.pop('image')))
                if value.get('previous'):
                    images.append(dict(type='image', **value['previous']))
                    value['previous'] = True
            return value, images
        from .schema import IMAGE_TOOLS, PROJECT_TOOLS, TOOL_ENDPOINTS, WHITEBOX_TOOLS
        from .schema import SESSION_OPERATIONS, SESSION_TOOLS
        if name in SESSION_TOOLS.values():
            operation = next(op for op, tool in SESSION_TOOLS.items() if tool == name)
            method, path, _ = SESSION_OPERATIONS[operation]
            if '{id}' in path:
                path = path.replace('{id}', args.pop('id'))
            if method == 'GET' and wait is not None:
                args['wait'] = wait
            return (c.get(path, **args) if method == 'GET' else c.post(path, args)), []
        if name in PROJECT_TOOLS.values() or name in IMAGE_TOOLS.values() or name in WHITEBOX_TOOLS.values():
            method, path = TOOL_ENDPOINTS[name]
            if '{id}' in path:
                path = path.replace('{id}', args.pop('id'))
            return (c.get(path, **args) if method == 'GET' else c.send(method, path, args)), []
        if name.startswith('freevideo_cast_'):
            from .schema import CAST_OPERATIONS
            method, path, _ = CAST_OPERATIONS[name.removeprefix('freevideo_cast_')]
            if '{id}' in path:
                path = path.replace('{id}', args.pop('id'))
            if method == 'GET':
                return c.get(path, **args), []
            return c.send(method, path, args), []
        if name == 'freevideo_submit_batch':
            return dict(jobs=c.batch(args['jobs'], **({'ref': args['ref']} if 'ref' in args else {}))), []
        if name == 'freevideo_job':
            return c.job(args['id'], **({'wait': wait} if wait is not None else {})), []
        if name == 'freevideo_wait':
            if not args.get('id'):
                return c.get('/inbox', wait=min(50., float(args.get('timeout', 50)))), []
            return self.wait(identity, args['id'], min(25., float(args.get('timeout', 25))), token), []
        if name == 'freevideo_jobs':
            return dict(jobs=c.jobs(**args)), []
        if name == 'freevideo_cancel':
            return c.cancel(args['id']), []
        if name == 'freevideo_resume':
            return c.resume(args['id'], **({'ref': args['ref']} if 'ref' in args else {})), []
        if name == 'freevideo_add_media':
            return c.upload(args['path']), []
        if name == 'freevideo_import_media':
            return c.import_media(args['path'], **({'ref': args['ref']} if 'ref' in args else {})), []
        if name == 'freevideo_imports':
            return c.get('/imports'), []
        if name == 'freevideo_edit_image':
            made = c.edit(args['source'], args.get('recipe'), document=args.get('document'), target=args.get('target'))
            return made, self.picture(made['view'])
        if name == 'freevideo_library':
            return c.library(**args), []
        if name == 'freevideo_look':
            return self.look(args)
        raise LookupError('Unknown tool: %s' % name)

    def wait(self, identity, job, timeout, token):
        deadline = time.monotonic() + max(0., min(25., float(timeout)))
        last = None
        while True:
            left = max(0., deadline - time.monotonic())
            view = self.client.job(job, wait=min(5., left))
            progress = view.get('progress') or {}
            if token is not None:
                fraction = progress.get('fraction')
                message = progress.get('label') or view['status']
                if progress.get('remaining_seconds'):
                    message += ' · about %d s left' % progress['remaining_seconds']
                key = (round(fraction or 0, 3), message)
                if key != last:
                    last = key
                    self.send(dict(jsonrpc='2.0', method='notifications/progress', params=dict(
                        progressToken=token, progress=round(100 * (fraction or 0), 1), total=100, message=message)))
            if view['status'] in DONE or left <= 0 or identity in self.cancelled or view.get('inbox'):
                return view

    def source(self, value):
        if isinstance(value, str):
            from .spec import JOB_ID, LIBRARY_ID
            if JOB_ID.fullmatch(value):
                return {'from_job': value}
            if LIBRARY_ID.fullmatch(value):
                return {'library': value}
            return {'path': value} if Path(value).is_absolute() else {'file': value}
        return value

    def look(self, args):
        source = self.source(args['source'])
        mode = args.get('mode', 'sheet')
        if mode == 'image' or ('file' in source or 'path' in source) and Path(
                source.get('path') or source.get('file')).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
            made = self.client.inspect(source)
            return made, self.picture(made)
        if mode == 'frame':
            made = self.client.frame(source, args.get('at'))
        else:
            made = self.client.contact_sheet(source, count=args.get('count', 8))
        return made, self.picture(made['view'])

    def picture(self, view, side=1568):
        """An MCP image block (JPEG, at most `side` px) so the model sees the picture."""
        if not view or not view.get('url'):
            return []
        data = self.client.request('GET', view['url'], raw=True)
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                image = image.convert('RGB')
                image.thumbnail((side, side))
                buffer = io.BytesIO()
                image.save(buffer, format='JPEG', quality=85)
                data = buffer.getvalue()
        except ImportError:
            pass
        return [dict(type='image', data=base64.b64encode(data).decode(), mimeType='image/jpeg')]


def shell_quote(value, *, windows=None):
    """Quote display arguments for cmd.exe or a POSIX shell, not C argv parsing."""
    windows = sys.platform == 'win32' if windows is None else windows
    if windows:
        return '"' + value + '"' if not value or any(c.isspace() or c in '&()^%!|<>;,=' for c in value) else value
    import shlex
    return shlex.quote(value)


def linux_wrapper_supported():
    import platform
    return sys.platform.startswith('linux') and platform.machine() == 'x86_64'


def python_shell_command(config, module):
    """Direct Unix entry using this installation's interpreter, source and root."""
    command = list(config['command'])
    command[command.index('-m') + 1] = module
    command[1:1] = ['-X', 'utf8']
    env = ['FREEVIDEO_AGENT=1'] + [key + '=' + value for key, value in config['env'].items()]
    return 'env ' + ' '.join(shell_quote(value) for value in env + command)


def client_config(root):
    """How MCP clients start this server for this installation."""
    root = Path(root).resolve()
    try:
        machine = json.loads((root / 'machine.json').read_text(encoding='utf-8'))
        python = machine['python']
    except (OSError, ValueError, KeyError):
        python = sys.executable
    command = [python, '-B', '-m', 'freevideo_engine.agent.mcp', '--root', str(root)]
    env = {'PYTHONPATH': str(Path(__file__).resolve().parents[2])}
    shell_command = list(command)
    if sys.platform != 'win32' and not linux_wrapper_supported():
        shell_command[1:1] = ['-X', 'utf8']
    # Keep JSON argv untouched. Windows command text targets Command Prompt.
    assignment = shell_quote('PYTHONPATH=' + env['PYTHONPATH']) if sys.platform == 'win32' else \
        'PYTHONPATH=' + shell_quote(env['PYTHONPATH'])
    return dict(command=command, env=env, codex='codex mcp add freevideo --env %s -- %s' % (
                    assignment, ' '.join(shell_quote(p) for p in shell_command)), mcp_json={'mcpServers': {'freevideo': {
                    'command': command[0], 'args': command[1:], 'env': env}}},
                claude_code='claude mcp add freevideo --env %s -- %s' % (
                    assignment, ' '.join(shell_quote(p) for p in shell_command)))


def print_config(root):
    config = client_config(root)
    print('Claude Code:\n  %s\n' % config['claude_code'])
    print('Codex:\n  %s\n' % config['codex'])
    print('Claude Desktop / Cursor (mcpServers):')
    print(json.dumps(config['mcp_json'], indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='freevideo mcp', description='FreeVideo MCP server (stdio)')
    parser.add_argument('--root', type=Path, help='FreeVideo installation folder')
    parser.add_argument('--url', help='ComfyUI address (default: from the installation, else http://127.0.0.1:8188)')
    parser.add_argument('--print-config', action='store_true', help='Print client configuration and exit')
    args = parser.parse_args(argv)
    client = Client(args.url, root=args.root, name='freevideo-mcp')
    if args.print_config:
        print_config(client.root)
        return 0
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    Server(client).serve()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
