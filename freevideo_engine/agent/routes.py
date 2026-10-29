"""HTTP API for agents: /freevideo/agent/v1/*, bearer-token protected, JSON in and out.

The token lives in <installation>/agent/token. Local agents (and the MCP server)
read it from disk; web pages cannot, and they cannot send an Authorization header
to ComfyUI without a CORS preflight that ComfyUI does not grant.
"""
import asyncio
import base64
import binascii
import json
import hashlib
from inspect import isawaitable
import logging
import math
import os
import re
from pathlib import Path
import secrets
import stat
import tempfile
import time
import uuid

from . import API_VERSION, enabled
from . import assets, attachments
from . import private_file
from . import spec as specs
from .. import tiers as quality_tiers
from . import schema as contract, errors, access
from .jobs import ACTIVE, DONE, JobError, Jobs, chain_error, library_view

PREFIX = '/freevideo/agent/v1'
from .whitebox import MAX_VIDEO_BYTES as MAX_WHITEBOX_VIDEO
from . import whitebox_pick
from .whitebox_presets import PRESETS, preset as whitebox_preset
# What an empty wait tells the agent: the conversation is open for as long as the user keeps the page.
KEEP_WAITING = ('Nothing new yet. The user is still in FreeVideo and may speak at any moment: call this wait '
                'again now. An empty wait never ends the conversation; do not end your turn or write a summary.')
# The user may open another conversation while the agent waits; its words go where the user now is.
MOVED = ('The user is now in another conversation, session %s. Post your words to that session from now on; '
         'the user no longer sees the earlier one.')
MOVED_NEW = ' It starts afresh: do not carry requests over from the earlier conversation unless the user refers to them.'
MOVED_BACK = (' It is one the user was in before, and the user means what was said there, not the conversation you just '
              'left. Its last lines are in "before"; all of it: GET /sessions/%s/events.')
# An agent that connects again finds the conversation where the user left it, and has greeted there once already.
RESUMED = ('This conversation is already under way; its last lines are in "before". Do not introduce yourself again: '
           'say nothing until the user speaks, then carry on from there.')
BEFORE_LINES, BEFORE_TEXT = 8, 600
# The user started a white-model scene and then spoke: the words may be about it, not a new request.
# Seconds one question to the local prompt model may take, loading the model included.
WHITEBOX_ASK_SECONDS = 120
WHITEBOX_PENDING = ('The user started a white-model scene in this conversation ("whitebox" lists it). If the words ask about '
                    'it or about progress, answer from its status in one sentence (waiting or rendering: still being made; '
                    'failed: say it was not made, and why) and start nothing. A question is never a prompt.')



def _activity_cursor(row):
    return '%r:%s' % (row['created_at'], row['id'])

def local_address(request):
    """The backend's bound port, independent of page origins and forwarding headers."""
    socket = request.transport.get_extra_info('sockname') if request.transport else None
    if not socket:
        raise JobError('not_ready', 'Listening address unavailable')
    # A specific loopback address is the only one that listens; anything else is reached through localhost.
    host = '[::1]' if ':' in socket[0] else socket[0] if socket[0].startswith('127.') else '127.0.0.1'
    scheme = 'https' if request.transport.get_extra_info('sslcontext') is not None else 'http'
    return f'{scheme}://{host}:{socket[1]}'


def token_path(root):
    return Path(root) / 'agent' / 'token'


class TokenUnavailable(JobError):
    def __init__(self):
        super().__init__('not_ready', 'Agent unavailable: a private installation token could not be prepared.')

    def view(self):
        return errors.build(self.code, validation=True, message=self.message)


def ensure_token(root):
    """Read/create a private token; replace existing Windows files with a protected DACL."""
    path = token_path(root)

    def secure_existing(value):
        temporary = path.with_name('.token-' + uuid.uuid4().hex + '.tmp')
        descriptor = private_file.create(temporary)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write((value + '\n').encode('ascii'))
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(8):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    # Another program may hold the old file open for a moment.
                    if attempt == 7:
                        raise
                    time.sleep(.1 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    def read_existing():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and info.st_uid != os.getuid()):
            raise ValueError('Unsafe installation token; repair its ownership or remove it and restart')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                    or os.name == 'posix' and opened.st_uid != os.getuid()):
                raise ValueError('Unsafe installation token')
            if os.name == 'posix':
                os.fchmod(stream.fileno(), 0o600)
            data = stream.read(256)
        try:
            value = data.decode('ascii').strip()
        except UnicodeDecodeError:
            raise ValueError('Invalid installation token') from None
        if data.endswith(b'\n') and 32 <= len(value) <= 128 and all(c.isalnum() or c in '-_' for c in value):
            if os.name == 'nt':
                secure_existing(value)
            return value
        return None

    try:
        value = read_existing()
        if value is not None:
            return value
    except FileNotFoundError:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = secrets.token_urlsafe(32)
        try:
            descriptor = private_file.create(path)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write((value + '\n').encode('ascii'))
                stream.flush()
                os.fsync(stream.fileno())
            return value
    for _ in range(100):
        value = read_existing()
        if value is not None:
            return value
        time.sleep(.01)
    raise ValueError('Incomplete installation token; remove it and restart')


def substitute(value, names, _field=None):
    """Replace "@name" job references inside one batch with the ids assigned to that batch."""
    if (_field in ('from_job', 'after', 'clips', 'refine_from') and isinstance(value, str)
            and value.startswith('@') and value[1:] in names):
        return names[value[1:]]
    if isinstance(value, list):
        return [substitute(row, names, _field) for row in value]
    if isinstance(value, dict):
        return {key: row if key in ('metadata', 'edit') else substitute(row, names, key) for key, row in value.items()}
    return value


def submit_batch(jobs, body, client):
    """{"jobs": [{"ref": "s1", ...}, {"media": {"first": {"from_job": "@s1", "frame": "last"}}}]} in one call."""
    return jobs.submit_request(body, client=client)[0]


def features(continuation, vlm):
    """What an agent can use here (ready says whether the engine can run now). A missing name is not offered."""
    from .editor_runtime import available
    rules = available()
    return {'capabilities': True, 'plan': True, 'jobs.cancel': True, 'jobs.resume': True, 'takes': True,
            'library.read': True, 'library.download': True, 'imports.create': True, 'imports.read': True,
            'errors.validation': True, 'errors.missing_media': True,
            'generate.text': True, 'generate.size': True, 'generate.first_frame': True, 'generate.seed': True,
            'queue.shared': True, 'queue.busy': True, 'chain.auto': bool(continuation),
            'enhance': bool(vlm.get('ready')), 'drama.film': True, 'images.edit': rules,
            'images.layers': rules, 'images.heal': rules, 'images.render': rules, 'projects.read': True,
            **{'projects.' + name: rules for name in ('insert', 'split', 'move', 'trim', 'remove', 'export', 'generate', 'listen')}}


def whitebox_support(jobs):
    """White-model guidance needs the user's open page to render, and the Mac engine cannot use it yet."""
    if quality_tiers.on_mac():
        return dict(available=False, needs_page=True, note='Mac 暂不支持白模。')
    return dict(available=True, needs_page=True, page_open=jobs.whitebox.page_open(), note='白模预览在 FreeVideo 页面中显示，需保持页面打开。')


def capabilities(port, jobs):
    from ..lora_triggers import list_triggers
    from .. import __version__
    from ..desktop_runtime import source_release
    from ..comfy_assets import EXTENSIONS
    from ..comfy_bridge import installation
    from .guide import GUIDE
    try:
        root, machine = installation()
        ready, problem = True, None
    except (OSError, ValueError) as error:
        root, machine, ready, problem = None, {}, False, errors.ERRORS['not_ready'][2]
    vlm = {}
    service = getattr(port.server, '_freevideo_prompt_vlm', None)
    if service is not None:
        try:
            vlm = {k: v for k, v in service.info(wait=False).items() if k in ('ready', 'model', 'license', 'bytes', 'busy')}
        except (OSError, ValueError):
            vlm = dict(ready=False)
    running, pending = port.snapshot()
    from ..chain_plan import BLOCK_SECONDS
    # One block is the limit when the backend cannot continue a video; plan and submit refuse the same requests.
    continuation = jobs.continuation_ready()
    # Users know the release (0.4.0), not the build number; a source checkout has no release stamp.
    release = source_release(Path(__file__).resolve().parents[2]).get('product_version')
    return dict(
        api_version=API_VERSION, freevideo_version=release or __version__, build=__version__, ready=ready, problem=problem,
        continuation=continuation,
        features=features(continuation, vlm),
        whitebox=whitebox_support(jobs),
        device=dict(backend=machine.get('device_backend', 'cuda') if machine else None,
                    vram_limit_gib=machine.get('vram_gib'), ram_limit_gib=machine.get('ram_gib')),
        kinds={'generate': 'Video from a prompt and optional keyframes or references',
               'assemble': 'Join finished clips into one video (cut or crossfade)',
               'vlm': 'Ask the local vision-language model about images or sampled video frames',
               'image': 'Generate a picture from text or change input pictures with the local image model',
               'enhance': 'Rewrite a rough idea into a full H3 prompt with the local VLM',
               'export': 'Render a saved revision of an editing project to a video',
               'listen': 'Listen to the sound of an editing project for subtitle lines and times',
               'image_edit': 'Render a saved revision of an image session to a picture'},
        limits=dict(width=[256, 4096, 32], height=[256, 4096, 32], seconds=[1.625, 60 if continuation else BLOCK_SECONDS],
                    seconds_tested=[4, 15],
                    fps=24, base_steps=[1, 32], refine_steps=[1, 31], references=32, loras=32, lora_strength=[-4, 4],
                    upload_bytes=2 * 1024 ** 3, vlm_images=24, takes=8, batch_jobs=64),
        quality={name: dict(two_pass=plan[0], base_steps=plan[1], refine_steps=plan[2],
                            step_cache=quality_tiers.step_cache(name, quality_tiers.on_mac()),
                            upscale_factor=quality_tiers.upscale_factor(name, quality_tiers.on_mac()))
                 for name, plan in specs.QUALITY.items()},
        aspects=sorted(specs.ASPECTS), native_megapixels=.98,
        tasks=GUIDE['tasks'], media_extensions={k: sorted(v) for k, v in EXTENSIONS.items()},
        loras=sorted(n for n in port.folders.get_filename_list('loras') if n.lower().endswith('.safetensors')),
        lora_trigger_words=list_triggers(port.folders),
        vlm=vlm, queue=dict(running=len(running), pending=len(pending),
                            agent_active=len(jobs.active())),
        docs=dict(guide=PREFIX + '/guide', schema=PREFIX + '/schema', openapi=PREFIX + '/openapi.json'))


def plan(port, value, jobs=None):
    from ..two_pass import plan as sampling_plan
    from ..geometry import geometry
    spec = specs.normalize(value, port=port)
    result = dict(spec={k: v for k, v in spec.items() if k != 'depends_on'}, warnings=[])
    if spec['kind'] != 'generate' or spec.get('refine_from'):
        return result
    from .jobs import continuation_problems, cut_problems, part_prompt_problems, parts_problems
    problems = continuation_problems(spec) + cut_problems(spec, spec['seconds']) + part_prompt_problems(spec)
    if jobs is not None:
        problems += parts_problems(spec, jobs.port.chain_runner(), work=jobs.continued_work(spec),
                                   planned=jobs.planned_parts(spec))
    else:
        problems += parts_problems(spec, None)
    if problems:
        raise specs.SpecError(problems)
    if jobs is not None:
        jobs.require_continuation(spec)
        if spec.get('continue_from') and not jobs.continuation_ready():
            from .jobs import continuation_unavailable
            raise continuation_unavailable()
    canvas = geometry(spec['width'], spec['height'], seconds=spec['seconds'])
    passes = sampling_plan(canvas, spec['two_pass'], spec['task'], base_steps=spec['base_steps'],
                           refine_steps=spec['refine_steps'])
    result['geometry'] = {k: canvas[k] for k in ('width', 'height', 'frames', 'seconds', 'fps', 'latent_frames')}
    result['sampling'] = dict(two_pass=passes['enabled'], total_steps=passes['total_steps'],
                              first_pass=passes.get('first'), second_pass=passes.get('second') if passes['enabled'] else None)
    if spec.get('sampling_note'):
        result['warnings'].append(spec['sampling_note'])
    if spec['seconds'] > 15:
        result['warnings'].append('Longer than 15 s is experimental')
    if spec['task'] == 'ref2va':
        result['warnings'].append('Reference generation is experimental')
    if spec['width'] * spec['height'] > 2 * 1024 ** 2:
        result['warnings'].append('Above 2 MP takes much longer and needs more memory')
    from ..chain_plan import plan as chain_plan
    from .length import single_frame_limit
    result['chain_plan'] = chain_plan(spec['seconds'], max_frames=single_frame_limit(spec),
                                     continued=bool(spec.get('continue_from')))
    from .forecast import generation_estimate
    result['estimate'], result['capabilities_revision'] = generation_estimate(port, spec, result['chain_plan'])
    return result


def job_answer(view):
    """One job answers the way a submission does: the job is also under 'job'."""
    return dict(view, job=view)


async def wait_for_job(jobs, row, seconds, *, clock=time.monotonic, sleep=asyncio.sleep):
    if not math.isfinite(seconds) or seconds < 0:
        raise chain_error('invalid_wait', 'wait must be a finite nonnegative number of seconds.',
                          'Use GET /jobs/{id}?wait=25 or wait=0.',
                          details=[dict(field='/wait', allowed='finite number >= 0')])
    deadline = clock() + min(25., seconds)
    while row['status'] not in DONE and clock() < deadline:
        await sleep(min(.5, max(0., deadline - clock())))
    return await asyncio.to_thread(jobs.view, row)


def saved_identity(port, day, run):
    from datetime import date
    from ..comfy_library import _video
    identity = day + '/' + run
    try:
        date.fromisoformat(day)
        _video(port.output_root, identity)
    except (ValueError, TypeError):
        raise chain_error('invalid_library_id', 'day and run must identify a video inside the saved library.',
            'GET /freevideo/agent/v1/library and use an item ID (YYYY-MM-DD/32 lowercase hex characters).',
            details=[dict(field='/day', allowed='valid YYYY-MM-DD'),
                     dict(field='/run', allowed='32 lowercase hex characters')]) from None
    return identity


def library(port, jobs, *, limit=24, before=None, query=None, project=None):
    from ..comfy_library import list_videos
    page = list_videos(port.output_root, before=before, limit=limit)
    by_video = {}
    for row in jobs.rows():
        video = ((row.get('result') or {}).get('video') or {}).get('file')
        if video:
            by_video[video] = row
    items = []
    for item in page['items']:
        folder = port.output_root / Path(item['video']).parent
        try:
            prompt = (folder / 'prompt.txt').read_text(encoding='utf-8')
        except OSError:
            prompt = ''
        agent = by_video.get(item['video'])
        if query and query.lower() not in prompt.lower():
            continue
        if project and (not agent or agent['spec'].get('project') != project):
            continue
        geometry = item.get('geometry') or {}
        items.append(dict(id=item['id'], created_at=item['created_at'], video=library_view(port, item['video']),
                          width=geometry.get('width'), height=geometry.get('height'), seconds=geometry.get('seconds'),
                          preview=item.get('preview'), request_seconds=item.get('request_seconds'),
                          prompt=prompt[:600] + ('…' if len(prompt) > 600 else ''),
                          job=dict(id=agent['id'], label=agent['spec'].get('label'),
                                   project=agent['spec'].get('project')) if agent else None,
                          thumbnail=PREFIX + '/library/' + item['id'] + '/thumbnail'))
    return dict(items=items, next=page['next'])


def register():
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if (server is None or getattr(server, '_freevideo_agent', None) is not None
            or getattr(server, '_freevideo_agent_registered', False)):
        return
    from .. import proxy_identity
    hop_secret = os.environ.pop(proxy_identity.ENV, '')
    if getattr(server, 'app', None) is not None:
        server.app[proxy_identity.app_key()] = hop_secret
    if not enabled():
        # Off: a single answer, so the page shows no agent controls. No token, jobs or scheduler.
        if not getattr(server, '_freevideo_agent_off', False):
            server._freevideo_agent_off = True

            @server.routes.get('/freevideo/agent')
            async def off(request):
                return web.json_response({'enabled': False}, headers={'Cache-Control': 'no-store'})
        return
    startup_failed = False
    try:
        from .comfy import ComfyPort
        from .sessions import Confirmations, Sessions
        port = ComfyPort(server)
        jobs = Jobs(port, port.output_root / 'FreeVideo' / '.agent' / 'jobs')
        sessions = Sessions(port.output_root / 'FreeVideo' / '.agent' / 'sessions')
        # The user's stop also cancels this session's queued and running agent work and pauses its productions.
        sessions.on_stop.append(lambda sid: asyncio.to_thread(jobs.stop_session, sid))
        # Operations an agent runs only after the user approves them; the page changes the policy.
        confirmations = Confirmations(port.output_root / 'FreeVideo' / '.agent' / 'confirm.json', sessions)
        jobs.productions.gate = confirmations.gate
        jobs.session_open = sessions.require_open
    except Exception as error:
        # Optional agent state must never prevent the studio or other routes from loading.
        logging.getLogger(__name__).warning('[FreeVideo] Agent startup unavailable: %s', error)
        startup_failed = True
    else:
        server._freevideo_agent = jobs
        server._freevideo_agent_sessions = sessions
    tokens = {}
    token_failures = set()
    thumbnails = asyncio.Semaphore(1)
    upload_decoders = asyncio.Semaphore(1)
    import_lock = asyncio.Lock()

    def expected(root=None):
        from ..comfy_bridge import installation_root
        root = installation_root() if root is None else root
        if root in token_failures:
            raise TokenUnavailable()
        if root not in tokens:
            try:
                tokens[root] = ensure_token(root)
            except (OSError, ValueError):
                if os.name != 'nt':
                    raise
                # Do not retry/enable an unsafe Windows token during this server startup.
                token_failures.add(root)
                logging.getLogger(__name__).warning('%s', TokenUnavailable())
                raise TokenUnavailable() from None
        return tokens[root]

    def prepare_token(root):
        # Startup and public connection help share authentication's Windows cache.
        try:
            return expected(root) if os.name == 'nt' else ensure_token(root)
        except (OSError, ValueError):
            raise TokenUnavailable() from None

    def reply(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'},
                                 dumps=lambda v: json.dumps(v, ensure_ascii=False))

    query_key = web.AppKey('agent_query', dict)
    body_key = web.AppKey('agent_body', dict)
    contracts = contract.contracts()
    page_sessions = {}
    connection = dict(connected=False, client=None, via=None, last_seen_at=None)
    # The conversation in the Autopilot view: the page and the connected agent talk in the agent's latest session.
    chat = dict(client=None, waiting=0, folds={}, told={})
    chat_writes = asyncio.Lock()
    server._freevideo_agent_connection = connection

    def problem(code, message=None, status=None, **details):
        error = errors.build(code, details=details.get('details'), owner=details.get('owner'))
        return reply({'error': error}, errors.ERRORS[error['code']][0] or 500)

    def work_sessions():
        return getattr(server, '_freevideo_agent_sessions', None)

    def guarded(handler):
        async def wrapped(request):
            response = await respond(request)
            try:
                return await session_after(request, response)
            except Exception:
                # The session record is a view of the work; it never fails the work itself.
                logging.getLogger(__name__).warning('Session record failed', exc_info=True)
                return response

        async def respond(request):
            identity = errors.REQUEST_ID.set('r-' + uuid.uuid4().hex)
            attached = None
            suffix = ''
            try:
                if startup_failed:
                    raise JobError('not_ready', 'Agent startup unavailable')
                suffix = request.match_info.route.resource.canonical
                # ComfyUI serves every route again under /api, and its page calls that copy.
                suffix = suffix[4:] if suffix.startswith('/api/freevideo/agent') else suffix
                suffix = suffix[len(PREFIX):] if suffix.startswith(PREFIX) else suffix
                method = 'GET' if request.method == 'HEAD' else request.method
                definition = contracts.get((method, suffix))
                if definition is None:
                    raise JobError('method_not_allowed' if any(p == suffix for _, p in contracts) else 'not_found', 'No contract')
                access.check(request, local=definition.get('local', False), lan=definition.get('lan', False))
                if definition.get('auth') is False and definition.get('local') and method not in ('GET', 'HEAD'):
                    if proxy_identity.HEADER in request.headers:
                        valid = definition.get('lan') and proxy_identity.check_csrf(request)
                    else:
                        valid = valid_page_session(request)
                elif definition.get('page_cookie'):
                    session = page_sessions.get(request.cookies.get('freevideo_agent_page', ''))
                    valid = session is not None and session[1] >= time.monotonic()
                else:
                    valid = True
                if not valid:
                        raise JobError('page_session_required', 'Page session required')
                header = request.headers.get('Authorization', '')
                given = header[7:].strip() if header[:7].lower() == 'bearer ' else request.headers.get('X-FreeVideo-Agent', '')
                if definition.get('auth', True) or given:
                    try:
                        token = expected()
                    except (OSError, ValueError):
                        raise JobError('not_ready', 'Installation not ready') from None
                    if not given or not secrets.compare_digest(given.encode('utf-8'), token.encode('utf-8')):
                        raise JobError('unauthorized', 'Token missing or incorrect')
                    connection.update(connected=True, last_seen_at=time.time())
                    connection['client'] = connection['client'] or request.headers.get('X-FreeVideo-Client', 'HTTP client')[:80]
                    connection['via'] = connection['via'] or 'http'
                    request['agent_client'] = request.headers.get('X-FreeVideo-Client', '')[:80] or 'HTTP client'
                    chat['client'] = request['agent_client']
                if definition.get('path'):
                    contract.validate(definition['path'], dict(request.match_info))
                for key in request.query:
                    if len(request.query.getall(key)) != 1:
                        raise specs.SpecError(contract.diagnostic('/query/' + (contract.pointer(key) if key in definition['query']['properties'] else '<unknown>'), 'supplied once', None))
                try:
                    request[query_key] = contract.query_values(definition['query'], request.query)
                except specs.SpecError as error:
                    error.code = 'invalid_capabilities_query' if suffix == '/capabilities' else 'invalid_wait' if suffix == '/jobs/{id}' else 'invalid_request'
                    raise
                if definition['body'] is not None:
                    if definition.get('multipart') and request.content_type == 'multipart/form-data':
                        pass
                    else:
                        limit = contract.MAX_JSON_UPLOAD_BYTES if suffix in ('/media', '/imports') else contract.MAX_BODY_BYTES
                        value = await read_body(request, limit, definition.get('optional_body', False))
                        try:
                            contract.validate(definition['body'], value)
                        except specs.SpecError as error:
                            error.code = 'invalid_resume' if suffix.endswith('/resume') else 'invalid_request'
                            raise
                        request[body_key] = value
                if request.method == 'HEAD' and suffix.endswith('/events'):
                    if suffix.startswith('/sessions/'):
                        session_caller(request)
                        await asyncio.to_thread(sessions.get, request.match_info['id'])
                    else:
                        await asyncio.to_thread(jobs.get, request.match_info['id'])
                    return web.Response(content_type='text/event-stream', headers={'Cache-Control': 'no-store'})
                attached = await session_before(request, suffix, method, definition)
                return await handler(request)
            except specs.SpecError as error:
                value = errors.validation(error.errors, getattr(error, 'code', 'invalid_request'))
                return reply({'error': value}, getattr(error, 'status', errors.ERRORS[value['code']][0]))
            except JobError as error:
                value = error.view()
                if value.get('next') and request.match_info.get('id'):
                    value['next'] = value['next'].replace('{id}', request.match_info['id'])
                if error.code == 'too_large':
                    limit = error.details.get('limit_bytes', contract.MAX_UPLOAD_BYTES)
                    location = ({'path': '/file' if request.content_type == 'multipart/form-data' else '/data_base64'}
                                if suffix == '/imports' else {})
                    value.update(message='The upload must be at most %d bytes; a larger payload was received.' % limit,
                                 details=[dict(limit_bytes=limit, **location)], next='Reduce the payload to at most %d bytes and retry.' % limit)
                if error.code == 'busy':
                    entries = (await asyncio.to_thread(jobs.queue_view))['entries']
                    value = errors.build('busy', owner=entries[0]['owner'] if entries else 'ui')
                return reply({'error': value}, getattr(error, 'status', errors.ERRORS[value['code']][0]))
            except KeyError:
                return problem('not_found')
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                return problem('invalid_json')
            except OSError:
                return reply({'error': errors.build('storage_unavailable', stage='submission_unknown' if suffix == '/jobs' else 'request')}, 503)
            except ValueError:
                return problem('invalid_edit' if suffix == '/media/edit' else 'invalid_media' if suffix.startswith('/media') else 'invalid_request')
            except web.HTTPException as error:
                return problem('too_large' if error.status == 413 else 'invalid_request')
            except Exception:
                value = errors.build('internal_error', stage='submission_unknown' if suffix == '/jobs' else 'request')
                logging.exception('[FreeVideo] Agent request %s failed', value['request_id'])
                return reply({'error': value}, 500)
            finally:
                for variable, token in attached or ():
                    variable.reset(token)
                errors.REQUEST_ID.reset(identity)
        return wrapped

    # Work sessions (sessions.py): which session a token request belongs to, its step events,
    # the user's stop, and the user's unread words returned with each answer.
    SESSION_FREE = ('/sessions', '/connect')
    REF_KINDS = {'/jobs/': 'job', '/drama/': 'drama', '/projects/': 'project', '/images/': 'image', '/cast/': 'cast'}

    async def session_before(request, suffix, method, definition):
        store = work_sessions()
        client = request.get('agent_client')
        if store is None or client is None or suffix.startswith(SESSION_FREE):
            return None
        write = method not in ('GET', 'HEAD')
        def attach():
            sid = request.headers.get('X-FreeVideo-Session', '').strip()
            if sid:
                store.get(sid)  # not_found for an unknown session
            else:
                # Work belongs to the conversation the agent last heard from; until its next wait it has
                # not learned that the user opened another one.
                told = chat['told'].get(client)
                sid = told if told is not None and store.has(client, told) else (
                    store.current_for(client) or (store.create(client=client)['id'] if write else None))
            if sid is None:
                return None
            request['agent_session'] = sid
            if not write:
                return None
            if store.stopped(sid) and not suffix.endswith('/cancel'):
                raise JobError('stopped_by_user', 'The user stopped this session')
            refs = {}
            for prefix, kind in REF_KINDS.items():
                if suffix.startswith(prefix) and 'id' in request.match_info:
                    refs[kind] = request.match_info['id']
            tool = request.headers.get('X-FreeVideo-Tool', '')[:80] or None
            step = dict(type='step', tool=tool or method + ' ' + suffix, summary=definition.get('summary') or suffix,
                        state='started', refs=refs, thumbs=[])
            request['agent_step'] = store.append(sid, step, by='agent', client=client)
            return sid
        sid = await asyncio.to_thread(attach)
        if sid is None:
            return None
        # A confirmation approves exactly this operation; a new idempotency ref is the same operation.
        value = request.get(body_key)
        value = {k: v for k, v in value.items() if k != 'ref'} if isinstance(value, dict) else value
        digest = hashlib.sha256(json.dumps([method, suffix, dict(request.match_info), value], sort_keys=True,
                                           separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
        return (errors.SESSION, errors.SESSION.set(sid)), (errors.CONFIRM, errors.CONFIRM.set(digest))

    def response_refs(value):
        refs = {}
        if not isinstance(value, dict):
            return refs
        job = value.get('job') if isinstance(value.get('job'), dict) else value if str(value.get('id', '')).startswith('j') else None
        if job and isinstance(job.get('id'), str):
            refs['job'] = job['id']
        for key in ('drama', 'project', 'image'):
            if isinstance(value.get(key), str):
                refs[key] = value[key]
        identity = value.get('id')
        if isinstance(identity, str):
            if identity.startswith('d') and 'shots' in value:
                refs['drama'] = identity
            elif identity.startswith('c') and value.get('kind') in ('character', 'setting'):
                refs['cast'] = identity
        return refs

    async def session_after(request, response):
        store = work_sessions()
        sid = request.get('agent_session')
        if store is None or sid is None or not isinstance(response, web.Response) or response.content_type != 'application/json':
            return response
        value = json.loads(response.body) if response.body else None
        step = request.get('agent_step')
        if step is not None:
            failed = response.status >= 400
            refs = dict(step['refs'], **response_refs(value))
            done = dict(type='step', id=step['id'], tool=step['tool'], summary=step['summary'],
                        state='failed' if failed else 'done', refs=refs, thumbs=[])
            if failed and isinstance(value, dict) and isinstance(value.get('error'), dict):
                done['error'] = value['error'].get('code')
            await asyncio.to_thread(store.append, sid, done, by='agent', client=request['agent_client'])
        unread = await asyncio.to_thread(store.unread, sid, request['agent_client']) if isinstance(value, dict) else []
        headers = {'X-FreeVideo-Session': sid}
        if unread:
            value = dict(value, inbox=unread[-20:])
            headers['X-FreeVideo-Inbox'] = str(len(unread))
            fresh = reply(value, response.status)
            for key, item in response.headers.items():
                if key.lower() not in ('content-length', 'content-type'):
                    fresh.headers[key] = item
            response = fresh
        response.headers.update(headers)
        return response

    async def read_body(request, limit, optional=False):
        if optional and not request.can_read_body:
            return {}
        if request.content_type != 'application/json':
            raise JobError('unsupported_media_type', 'JSON required')
        data = bytearray()
        async for chunk in request.content.iter_chunked(64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise JobError('too_large', 'Body exceeds limit', limit_bytes=limit)
        text = data.decode('utf-8')
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(json.JSONDecodeError('Nonfinite JSON', '', 0)))
        if '\\u' in text:
            # An escape may spell half of a character pair. No file or page can hold that text, and the
            # script runtime behind the editing rules ends the whole program on it.
            try:
                json.dumps(value, ensure_ascii=False).encode('utf-8')
            except UnicodeEncodeError:
                row = contract.diagnostic('/', 'text made of whole characters', None)
                row.update(got='half of a character pair', next='Send whole characters and retry.',
                           message='The body holds half of a character pair (a lone \\ud800 to \\udfff escape); '
                                   'no text can be stored that way.')
                raise specs.SpecError(row) from None
        return value

    async def body(request, **ignored):
        return request[body_key]

    def valid_page_session(request):
        page = page_sessions.get(request.cookies.get('freevideo_agent_page', ''))
        csrf = request.headers.get('X-FreeVideo-CSRF', '')
        return (page is not None and page[1] >= time.monotonic() and bool(csrf)
                and secrets.compare_digest(csrf.encode(), page[0].encode()))

    def session_caller(request):
        """guarded verifies supplied tokens; otherwise require a local page and CSRF."""
        header = request.headers.get('Authorization', '')
        given = header[7:].strip() if header[:7].lower() == 'bearer ' else request.headers.get('X-FreeVideo-Agent', '')
        if given:
            return 'agent'
        if valid_page_session(request):
            access.check(request, local=True)
            return 'user'
        raise JobError('unauthorized', 'Token or valid page session required')

    def session_client(request, by):
        return (request.headers.get('X-FreeVideo-Client', '')[:80] or None) if by == 'agent' else None

    def agent_path(path):
        path = path[4:] if path.startswith('/api/') else path
        return path == '/freevideo/agent' or path.startswith('/freevideo/agent/')

    async def no_cross_site_headers(request, response):
        if agent_path(request.path) or request.path in ('/freevideo/lan', '/api/freevideo/lan'):
            for key in list(response.headers):
                if key.lower().startswith('access-control-'):
                    del response.headers[key]
    if getattr(server, 'app', None) is not None:
        server.app.on_response_prepare.append(no_cross_site_headers)

        @web.middleware
        async def unregistered(request, handler):
            if agent_path(request.path):
                exception = request.match_info.http_exception
                if exception is not None:
                    return problem('method_not_allowed' if exception.status == 405 else 'not_found')
            return await handler(request)
        server.app.middlewares.append(unregistered)

    routes = server.routes

    @routes.get('/freevideo/agent')
    @guarded
    async def discover(request):
        # Unauthenticated: tells a client where to look, reveals nothing else.
        from ..comfy_bridge import installation_root
        from .guide import connection_discovery
        root = installation_root()
        if root in token_failures:
            # No token this startup: the same single answer as when it is off, so the page shows no agent controls.
            return reply({'enabled': False})
        return reply(connection_discovery(root, request.scheme + '://' + request.host, request[query_key].get('lang')))

    @routes.get('/freevideo/agent/session')
    @guarded
    async def page_session(request):
        stamp = time.monotonic()
        for key, (_, expires) in list(page_sessions.items()):
            if expires < stamp:
                page_sessions.pop(key, None)
        current = request.cookies.get('freevideo_agent_page', '')
        if current not in page_sessions:
            current = secrets.token_urlsafe(32)
            if len(page_sessions) >= 256:
                page_sessions.pop(next(iter(page_sessions)))
            page_sessions[current] = (secrets.token_urlsafe(32), stamp + 12 * 3600)
        response = reply(dict(csrf=page_sessions[current][0]))
        # The cookie follows the copy the page asked: its later calls go to the same one.
        response.set_cookie('freevideo_agent_page', current, httponly=True, samesite='Strict', secure=request.secure,
                            path=('/api' if request.path.startswith('/api/') else '') + '/freevideo', max_age=12 * 3600)
        return response

    # Page controls stay outside the Agent contract and never use its bearer token.
    # ComfyUI mirrors these routes under /api along with the other page routes.
    @routes.get('/freevideo/lan')
    @routes.post('/freevideo/lan')
    async def lan_settings(request):
        from .. import lan_page
        from ..comfy_bridge import installation_root
        identity = errors.REQUEST_ID.set('r-' + uuid.uuid4().hex)
        try:
            access.check(request, local=True)
            root = installation_root()
            if request.method == 'POST':
                if not valid_page_session(request):
                    raise JobError('page_session_required', 'Page session required')
                value = await read_body(request, contract.MAX_BODY_BYTES)
                return reply(await asyncio.to_thread(lan_page.apply, root, value))
            return reply(await asyncio.to_thread(lan_page.read, root))
        except lan_page.InvalidRequest as error:
            field = error.field if error.field in ('enabled', 'password', '') else '<unknown>'
            row = contract.diagnostic('/' + field, error.allowed, error.got)
            if field == 'password' and not isinstance(error.got, str):
                row = contract.diagnostic('/password', error.allowed, None)
                row.update(got='<redacted>', message='/password must be %s.' % error.allowed)
            value = errors.validation([row])
            return reply({'error': value}, 422)
        except lan_page.LanUnavailable:
            return problem('lan_unavailable')
        except JobError as error:
            return reply({'error': error.view()}, errors.ERRORS[error.code][0])
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            return problem('invalid_json')
        except specs.SpecError as error:
            return reply({'error': errors.validation(error.errors)}, 422)
        except web.HTTPException as error:
            return problem('too_large' if error.status == 413 else 'invalid_request')
        except OSError:
            return problem('storage_unavailable')
        except Exception:
            # Do not log exception text or request bodies from password controls.
            return problem('internal_error')
        finally:
            errors.REQUEST_ID.reset(identity)

    def activity_item(row, *, card=False):
        view = jobs.view(row, detail=False)
        value = {k: view.get(k) for k in ('id', 'kind', 'status', 'label', 'project', 'client', 'take',
                                          'created_at', 'finished_at')}
        progress = view.get('progress') or {}
        value['progress'] = {k: progress[k] for k in ('phase', 'step', 'fraction', 'remaining_seconds',
                             'queue_position') + (('block',) if card else ()) if progress.get(k) is not None}
        if view.get('waiting_for'):
            value['waiting_for'] = len(view['waiting_for'])
        if view.get('error'):
            value['error'] = view['error']
        report_id = (view.get('error') or {}).get('report_id') or (view.get('result') or {}).get('report_id')
        if isinstance(report_id, str) and re.fullmatch(r'[a-f0-9]{32}', report_id):
            value['report_id'] = report_id
        video = (view.get('result') or {}).get('video') or {}
        if video.get('url'):
            value['video'] = video['url']
        if (view.get('result') or {}).get('library_id'):
            # The work this video became, for the page to show it in My works.
            value['work'] = view['result']['library_id']
        from urllib.parse import quote
        work = value.get('work')
        # The saved work the page can play and picture without the agent's token.
        value['play'] = '/freevideo/agent/chat/video?id=' + quote(work, safe='') if work else None
        value['thumbnail'] = '/freevideo/library/thumbnail?id=' + quote(work, safe='') if work else None
        picture = view.get('result') or {}
        if row['kind'] == 'image' and picture.get('file'):
            # ComfyUI's own view of the input file, which the page can show without the agent's token.
            folder, _, leaf = picture['file'].rpartition('/')
            value['thumbnail'] = '/view?filename=%s&subfolder=%s&type=input' % (quote(leaf, safe=''), quote(folder, safe=''))
        if card:
            # A conversation card: the page plays and pictures the saved work itself.
            value.pop('client')
            value.setdefault('work', None)
            seconds = row['spec'].get('seconds')
            size = [row['spec'].get(k) if type(row['spec'].get(k)) is int else None for k in ('width', 'height')]
            value.update(seconds=seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None,
                         width=size[0], height=size[1])
            if row['kind'] == 'image' and picture.get('file'):
                value.update(width=picture.get('width'), height=picture.get('height'))
        return value

    def agent_state():
        seen, now = connection['last_seen_at'], time.time()
        connected = seen is not None and 0 <= now - seen < 600
        name = (connection['client'] or '').strip()
        # Programs report their own identifiers; the page shows the name a person knows.
        shown = ('Claude Code' if name.lower() in ('claude-code', 'claude code', 'claude') else
                 'Codex' if name.lower().startswith('codex') else name or None)
        # Every answer to the agent carries the user's unread words, so a recent call will hear them too.
        return dict(connected=connected, name=shown, listening=connected and (chat['waiting'] > 0 or now - seen < 90))

    def chat_name(said):
        """A job the agent left unnamed is called by what the user asked for: the first words after a colon."""
        text = re.split(r'[:：]', said or '', maxsplit=1)[-1].strip()
        text = re.split(r'[，。！？；、,.!?;\n]', text, maxsplit=1)[0].strip() or text
        if not text:
            return None
        if text.isascii():
            return text if len(text) <= 24 else text[:25].rsplit(' ', 1)[0][:24]
        return text[:8]

    wide = str.maketrans(',:;!?', '，：；！？')

    def chat_text(text):
        """Chinese an agent writes keeps Chinese punctuation: a mark right after a Chinese character is the wide one."""
        return re.sub(r'(?<=[\u3400-\u4dbf\u4e00-\u9fff])[,:;!?]', lambda found: found.group().translate(wide), text)

    def chat_card(row, fold):
        card = activity_item(row, card=True)
        if not card.get('label') and fold is not None:
            card['label'] = fold['names'].get(row['id'])
        estimate = jobs.estimate(row)
        if estimate:
            card['progress']['estimate'] = estimate
        return card

    def chat_fold(store, sid):
        """The session as the page shows it: sentences, the jobs made along the way, and questions."""
        fold = chat['folds'].get(sid)
        if fold is None:
            while len(chat['folds']) >= 8:
                chat['folds'].pop(next(iter(chat['folds'])))
            # Message IDs persist in session events and rebuild deduplication when a fold is evicted.
            fold = chat['folds'][sid] = dict(seq=0, items=[], jobs={}, entities={}, asked={}, sent={}, ids={}, names={}, said=None)
        while store.get(sid)['last_seq'] > fold['seq']:
            rows = store.events(sid, after=fold['seq'], limit=500)
            if not rows:
                break
            for row in rows:
                fold['seq'] = row['seq']
                base = dict(seq=row['seq'], at=row['at'], type=row['type'])
                if row['type'] == 'message':
                    fold['items'].append(dict(base, role='user' if row['by'] == 'user' else 'agent', text=row['text'],
                                              **({'attachments': row['attachments']} if row.get('attachments') else {})))
                    if row['by'] == 'user':
                        fold['said'] = row['text']
                        if row.get('mid'):
                            fold['sent'][row['mid']] = row['seq']
                            fold['ids'][row['seq']] = row['mid']
                            while len(fold['sent']) > 500:
                                fold['ids'].pop(fold['sent'].pop(next(iter(fold['sent']))), None)
                elif row['type'] == 'step' and row['state'] == 'done':
                    refs = row.get('refs') or {}
                    job = refs.get('job')
                    entity = next(((refs[key], kind) for key, kind in (('drama', 'production'), ('cast', 'cast')) if refs.get(key)), None)
                    new_entity = entity and entity[0] not in fold['entities']
                    if new_entity:
                        fold['entities'][entity[0]] = entity[1]
                    if job and job not in fold['jobs']:
                        fold['jobs'][job] = row['seq']
                        fold['names'][job] = chat_name(fold['said'])
                        fold['items'].append(dict(base, type='job', job=job, **({'entity': entity[0]} if new_entity else {})))
                    elif new_entity:
                        fold['items'].append(dict(base, type=entity[1], entity=entity[0]))
                elif row['type'] == 'confirm':
                    if row['state'] == 'asked':
                        item = dict(base, id=row['id'], question=row.get('question'),
                                    estimate_seconds=row.get('estimate_seconds'), state='asked',
                                    **{key: row[key] for key in ('width', 'height', 'seconds') if row.get(key) is not None})
                        fold['asked'][row['id']] = item
                        fold['items'].append(item)
                    elif row['id'] in fold['asked']:
                        fold['asked'][row['id']]['state'] = row['state']
        return fold

    def chat_current(rows, sid, fold):
        mine = [r for r in rows if r.get('session') == sid or (fold is not None and r['id'] in fold['jobs'])]
        active = sorted((r for r in mine if r['status'] in ACTIVE),
                        key=lambda r: (r['status'] in ('running', 'cancelling'), r['created_at']))
        return mine, active[-1] if active else None

    def chat_session(store, sid=None):
        # The conversation the user is in, or an earlier one of the connected agent that the page names.
        if store is None or chat['client'] is None:
            return None
        if sid is None:
            return store.current_for(chat['client'])
        if not store.has(chat['client'], sid):
            raise JobError('not_found', 'No such conversation')
        return sid

    def chat_moved(store, before):
        # The agent waits in the conversation the user was in; let that wait look again.
        if store.current_for(chat['client']) != before:
            store.wake(chat['client'])

    @routes.get('/freevideo/agent/chat')
    @guarded
    async def chat_read(request):
        after, wanted = request[query_key].get('after'), request[query_key].get('session')
        def read():
            store = work_sessions()
            sid = chat_session(store, wanted)
            rows = jobs.rows()
            value = dict(agent=agent_state(), session=sid, here=chat_session(store), last_seq=0, read_seq=0, items=[], jobs={}, failures={}, confirms={}, current=None,
                         active=sum(1 for r in rows if r['status'] in ACTIVE))
            if sid is None:
                return value
            fold = chat_fold(store, sid)
            known = {r['id']: r for r in rows}
            items = [i for i in fold['items'] if i['type'] != 'job' or i['job'] in known]
            if after is None:
                items, more = items[-200:], False
            else:
                items = [i for i in items if i['seq'] > after]
                items, more = items[:200], len(items) > 200
            mark = store.read_until(sid, chat['client'])
            for item in items:
                item = dict(item)
                if item['type'] == 'message' and item['role'] == 'user':
                    item['read'] = item['seq'] <= mark
                    if item['seq'] in fold['ids']:
                        item['id'] = fold['ids'][item['seq']]
                value['items'].append(item)
            value['last_seq'] = items[-1]['seq'] if more else fold['seq']
            # For what the page already holds: how far the agent has read, and how each question stands now.
            value['read_seq'] = mark
            value['confirms'] = {identity: item['state'] for identity, item in list(fold['asked'].items())[-50:]}
            mine, current = chat_current(rows, sid, fold)
            now = time.time()
            shown = {r['id']: r for r in mine if r['status'] in ACTIVE or (r.get('finished_at') or 0) > now - 1800}
            shown.update((i['job'], known[i['job']]) for i in items if i['type'] == 'job')
            value['jobs'] = {identity: chat_card(row, fold) for identity, row in shown.items()}
            for identity, kind in fold['entities'].items():
                try:
                    entity = jobs.productions.get(identity, compact=False) if kind == 'production' else jobs.cast.get(identity)
                except JobError:
                    continue  # A deleted card is no longer part of this conversation.
                error, task = None, None
                if kind == 'cast':
                    error = entity.get('generate_error')
                    task = (error or {}).get('job')
                else:
                    from .productions import failure
                    error, task = failure(entity)
                if error:
                    task_view = jobs.view(known[task], detail=False) if task in known else {}
                    report_id = ((task_view.get('error') or {}).get('report_id')
                                 or (task_view.get('result') or {}).get('report_id'))
                    value['failures'][identity] = dict(id=identity, kind=kind, status='failed',
                        label=entity.get('title') or entity.get('name'), error=errors.with_reason(error), report_id=report_id)
            value['abandoned'] = store.abandoned(sid)
            value['stage'] = store.stage(sid)
            value['current'] = current['id'] if current else None
            if current and current['id'] not in value['jobs']:
                value['jobs'][current['id']] = chat_card(current, fold)
            return value
        return reply(await asyncio.to_thread(read))

    @routes.get('/freevideo/agent/chat/now')
    @guarded
    async def chat_now(request):
        def read():
            store = work_sessions()
            sid = chat_session(store)
            rows = jobs.rows()
            fold = chat_fold(store, sid) if sid else None
            current = chat_current(rows, sid, fold)[1] if sid else None
            return dict(agent=agent_state(), current=chat_card(current, fold) if current else None,
                        active=sum(1 for r in rows if r['status'] in ACTIVE))
        return reply(await asyncio.to_thread(read))

    def chat_not_connected():
        return reply({'error': dict(code='not_connected', message='No agent is connected.', retryable=True,
                                    error_zh='尚未连接 Agent', error_en='No agent is connected.',
                                    details=[], next='Connect an agent in the Autopilot view, then say it again.',
                                    request_id=errors.request_id())}, 409)

    @routes.post('/freevideo/agent/chat/attach')
    @guarded
    async def chat_attach(request):
        from . import media
        if work_sessions() is None or chat['client'] is None:
            return chat_not_connected()
        limit = contract.MAX_CHAT_ATTACHMENT_BYTES
        if request.content_length is not None and request.content_length > limit:
            raise JobError('too_large', 'Upload exceeds limit', limit_bytes=limit)
        with tempfile.TemporaryDirectory(prefix='.chat-attach-', dir=port.input_root) as temporary:
            # The suffix selects the image decoder; Pillow recognizes the actual content format.
            target = Path(temporary) / 'image.png'
            size, digest = 0, hashlib.sha256()
            with target.open('xb') as output:
                while True:
                    chunk = await request.content.read(min(256 * 1024, limit - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise JobError('too_large', 'Upload exceeds limit', limit_bytes=limit)
                    output.write(chunk)
                    digest.update(chunk)
            async with upload_decoders:
                try:
                    info, suffix = await asyncio.to_thread(media.validate_upload, target)
                    if suffix not in ('.png', '.jpg', '.webp'):
                        raise ValueError('Only PNG, JPEG and WebP chat images are supported')
                except (OSError, ValueError, SyntaxError):
                    raise JobError('invalid_media', 'Image cannot be decoded within the upload limits') from None
            name = digest.hexdigest() + suffix
            original = await asyncio.to_thread(attachments.publish, port.input_root, target, name, request[query_key]['name'])
            return reply(dict(name=name, original_name=original, width=info['width'], height=info['height'], bytes=size), 201)

    @routes.get('/freevideo/agent/chat/attachment')
    @guarded
    async def chat_attachment(request):
        name = request[query_key]['name']
        try:
            path = attachments.path(port.input_root, name)
            async with upload_decoders:
                await asyncio.to_thread(attachments.read, port.input_root, name)
        except (OSError, ValueError, SyntaxError):
            raise JobError('not_found', 'No saved chat image') from None
        kind = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}[path.suffix]
        return web.FileResponse(path, headers={'Content-Type': kind, 'Cache-Control': 'private, no-store',
                                               'X-Content-Type-Options': 'nosniff'})

    def chat_attachments(values):
        result, seen = [], set()
        for i, item in enumerate(values):
            field = '/attachments/%d/name' % i
            if item['name'] in seen:
                raise specs.SpecError(contract.diagnostic(field, 'a distinct attachment name', item['name']))
            seen.add(item['name'])
            try:
                result.append(attachments.read(port.input_root, item['name']))
            except (OSError, ValueError, SyntaxError):
                error = specs.SpecError(contract.diagnostic(field, 'an existing uploaded image', item['name']))
                error.status = 400
                raise error from None
        return result

    @routes.post('/freevideo/agent/chat')
    @guarded
    async def chat_say(request):
        value = await body(request)
        def write():
            store, client = work_sessions(), chat['client']
            if store is None or client is None:
                return None
            before = store.current_for(client)
            sid = chat_session(store, value.get('session'))
            fold = chat_fold(store, sid) if sid else None
            if fold is not None and value['id'] in fold['sent']:
                seq = fold['sent'][value['id']]
                first = next(i for i in fold['items'] if i['seq'] == seq)
                return 200, dict(seq=seq, id=value['id'], at=first['at'], session=sid,
                                 read=seq <= store.read_until(sid, client), agent=agent_state())
            pictures = chat_attachments(value.get('attachments', []))
            sid = sid or store.create(client=client, by='user')['id']
            fold = fold or chat_fold(store, sid)
            if store.stopped(sid):
                # Speaking again continues a conversation the user stopped earlier.
                store.append(sid, dict(type='control', action='resumed'), by='user')
            message = dict(type='message', text=value['text'], mid=value['id'])
            if pictures:
                message['attachments'] = pictures
            if value.get('whitebox'):
                message['whitebox'] = True
            row = store.append(sid, message, by='user')
            fold['sent'][value['id']] = row['seq']
            fold['ids'][row['seq']] = value['id']
            while len(fold['sent']) > 500:
                fold['ids'].pop(fold['sent'].pop(next(iter(fold['sent']))), None)
            chat_moved(store, before)
            return 201, dict(seq=row['seq'], id=value['id'], at=row['at'], session=sid, read=False, agent=agent_state())
        async with chat_writes, upload_decoders:
            done = await asyncio.to_thread(write)
        if done is None:
            # Nothing is kept: no agent has connected since FreeVideo started, so nobody would read it.
            return chat_not_connected()
        return reply(done[1], done[0])

    @routes.post('/freevideo/agent/chat/abandon')
    @guarded
    async def chat_abandon(request):
        value = await body(request)
        def write():
            store = work_sessions()
            sid = chat_session(store, value.get('session'))
            if sid is None:
                raise JobError('not_found', 'No conversation')
            if not store.abandoned(sid):
                store.append(sid, dict(type='control', action='abandoned'), by='user')
            return dict(abandoned=True)
        return reply(await asyncio.to_thread(write))

    @routes.post('/freevideo/agent/chat/confirm')
    @guarded
    async def chat_confirm(request):
        value = await body(request)
        def write():
            store = work_sessions()
            before = chat_session(store)
            sid = chat_session(store, value.get('session'))
            if sid is None:
                raise JobError('not_found', 'No conversation')
            row = store.append(sid, dict(type='confirm', id=value['id'],
                                         state='approved' if value['approve'] else 'declined'), by='user')
            chat_moved(store, before)
            return dict(id=row['id'], state=row['state'])
        return reply(await asyncio.to_thread(write))

    @routes.get('/freevideo/agent/chat/list')
    @guarded
    async def chat_list(request):
        def read():
            from urllib.parse import quote
            store = work_sessions()
            here = chat_session(store)
            rows = store.history(chat['client']) if here else []
            listed = {row['id'] for row in rows}
            running, covers = {}, {}
            # One job snapshot supplies both counts and the first completed video, without reading session logs or files.
            for job in jobs.rows():
                sid = job.get('session')
                if sid not in listed:
                    continue
                if job['status'] in ACTIVE:
                    running[sid] = running.get(sid, 0) + 1
                if job['status'] != 'succeeded' or job['kind'] == 'image':
                    continue
                work = (job.get('result') or {}).get('library_id')
                if not isinstance(work, str) or not work:
                    continue
                finished = job.get('finished_at')
                order = (finished if finished is not None else job['created_at'], job['created_at'], job['id'])
                if sid not in covers or order < covers[sid][0]:
                    covers[sid] = (order, work)
            return dict(agent=agent_state(), here=here, unreadable=store.unreadable if store is not None else 0,
                        conversations=[dict(row, running=running.get(row['id'], 0),
                                            cover='/freevideo/library/thumbnail?id=' + quote(covers[row['id']][1], safe='')
                                            if row['id'] in covers else None) for row in rows])
        return reply(await asyncio.to_thread(read))

    @routes.post('/freevideo/agent/chat/new')
    @guarded
    async def chat_new(request):
        def write():
            store, client = work_sessions(), chat['client']
            if store is None or client is None:
                return None
            before = store.current_for(client)
            # A conversation the user has not spoken in yet is the new one already.
            if before is not None and not store.spoken(before):
                return 200, dict(session=before, agent=agent_state())
            sid = store.create(client=client, by='user')['id']
            chat_moved(store, before)
            return 201, dict(session=sid, agent=agent_state())
        done = await asyncio.to_thread(write)
        if done is None:
            return reply({'error': dict(code='not_connected', message='No agent is connected.', retryable=True,
                                        error_zh='尚未连接 Agent', error_en='No agent is connected.',
                                        details=[], next='Connect an agent in the Autopilot view, then try again.',
                                        request_id=errors.request_id())}, 409)
        return reply(done[1], done[0])

    @routes.post('/freevideo/agent/chat/delete')
    @guarded
    async def chat_delete(request):
        value = await body(request)
        def write():
            store = work_sessions()
            before = chat_session(store)
            sid = chat_session(store, value['session'])
            if sid is None:
                raise JobError('not_found', 'No conversation')
            # Work in progress still writes into its conversation; finished videos stay in the library.
            # New work is refused first, so what is counted below cannot grow before the file goes.
            def busy(leaving=()):
                return (any(r.get('session') == sid and r['status'] in ACTIVE and r['status'] not in leaving for r in jobs.rows())
                        or any(f['session'] == sid and not f['paused']
                               for f in jobs.productions.list(status='active', limit=None)))
            stop = value.get('stop') is True
            if stop:  # The user confirmed: stop its jobs and pause its films first, as the stop button does.
                jobs.stop_session(sid)
                for _ in range(20):  # A running job takes a moment to reach its cancelled state.
                    with jobs.lock:
                        if not busy():
                            break
                    time.sleep(0.25)
            store.begin_delete(sid)
            try:
                with jobs.lock:  # Productions share it.
                    # After the user's stop, a job still winding down no longer holds the conversation: its
                    # writes are refused once deletion begins, so a stuck one cannot block deleting forever.
                    if busy(('cancelling',) if stop else ()):
                        raise JobError('conflict', 'Work is in progress in this conversation')
                    store.delete(sid)
            except BaseException:
                store.end_delete(sid)
                raise
            chat['folds'].pop(sid, None)  # The page cache held its whole text.
            chat_moved(store, before)
            return dict(deleted=sid, here=chat_session(store), agent=agent_state())
        return reply(await asyncio.to_thread(write))

    @routes.get('/freevideo/agent/chat/video')
    @guarded
    async def chat_video(request):
        # The finished video of a work, for the local page to play in the conversation.
        path = await asyncio.to_thread(port.library_video, request[query_key]['id'])
        return web.FileResponse(path, headers={'Content-Type': 'video/mp4', 'Cache-Control': 'private, no-store'})

    @routes.get('/freevideo/agent/activity')
    @guarded
    async def activity(request):
        def read():
            # For the studio: what agents are doing, as ComfyUI's own queue and history already show.
            now = time.time()
            rows = sorted(jobs.rows(), key=lambda r: (r['created_at'], r['id']), reverse=True)
            active = sum(1 for r in rows if r['status'] in ACTIVE)
            cursor = request.query.get('cursor')
            unreadable = jobs.unreadable if cursor is None else 0
            if cursor is None:
                shown = [r for r in rows if r['status'] in ACTIVE or (r.get('finished_at') or 0) > now - 1800][:12]
                after = shown[-1] if shown else None
                older = [r for r in rows if after is None or (r['created_at'], r['id']) < (after['created_at'], after['id'])]
                if after is None and older:
                    # Nothing recent: history starts from the newest job.
                    return dict(active=active, jobs=[], unreadable=unreadable,
                                next_cursor=_activity_cursor({'created_at': older[0]['created_at'] + 1, 'id': ''}))
            else:
                try:
                    stamp, _, ident = cursor.partition(':')
                    key = (float(stamp), ident)
                    if not math.isfinite(key[0]) or len(cursor) > 80:
                        raise ValueError
                except ValueError:
                    return None
                page = [r for r in rows if (r['created_at'], r['id']) < key]
                shown, older = page[:30], page[30:]
            return dict(active=active, jobs=[activity_item(r) for r in shown], unreadable=unreadable,
                        next_cursor=_activity_cursor(shown[-1]) if shown and older else None)
        value = await asyncio.to_thread(read)
        return problem('invalid_request', 'Invalid cursor') if value is None else reply(value)

    @routes.post('/freevideo/agent/activity/cancel')
    @guarded
    async def activity_cancel(request):
        # JSON only, so another site cannot send it without a CORS preflight.
        if request.content_type != 'application/json':
            return problem('unsupported_media_type', 'Send JSON', 415)
        value = await body(request)
        try:
            row = await asyncio.to_thread(jobs.cancel, value.get('id') if isinstance(value, dict) else None, by='user')
        except KeyError:
            return problem('not_found', 'No such job', 404)
        return reply(dict(id=row['id'], status=row['status']))

    @routes.get('/freevideo/agent/connect')
    @guarded
    async def connect(request):
        # For the Autopilot view: how to connect an agent on this computer. The token itself stays in its file.
        from ..comfy_bridge import installation_root, source_root
        from .guide import page_language
        from .mcp import client_config, linux_wrapper_supported, python_shell_command, shell_quote
        page_language(request[query_key].get('lang'))
        try:
            root = installation_root()
            prepare_token(root)
        except (OSError, ValueError) as error:
            return problem('not_ready', str(error), 503)
        config = await asyncio.to_thread(client_config, root)
        if os.name != 'nt' and not linux_wrapper_supported():
            cli = python_shell_command(config, 'freevideo_engine.agent.cli') + ' status'
        else:
            script = next((folder / name for folder in (root, source_root())
                           for name in (('freevideo.cmd',) if os.name == 'nt' else ('freevideo',))
                           if (folder / name).is_file()), None)
            cli = shell_quote(str(script), windows=os.name == 'nt') + ' agent status' if script else None
        seen = {}
        for row in jobs.rows():
            if row.get('client'):
                seen[row['client']] = max(seen.get(row['client'], 0), row['created_at'])
        return reply(dict(address=local_address(request), api=PREFIX, token_file=str(token_path(root)), claude_code=config['claude_code'],
                          mcp_json=config['mcp_json'], cli=cli,
                          token_from_environment=False,
                          agents=[dict(client=name, last_seen=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stamp)))
                                  for name, stamp in sorted(seen.items(), key=lambda item: -item[1])[:5]]))

    @routes.post('/freevideo/agent/connect/reset')
    @guarded
    async def reset(request):
        if request.content_type != 'application/json':
            return problem('unsupported_media_type', 'Send JSON', 415)
        await body(request)
        from ..comfy_bridge import installation_root
        root = installation_root()
        token_path(root).unlink(missing_ok=True)
        tokens.pop(root, None)
        prepare_token(root)
        return reply(dict(reset=True))

    @routes.get(PREFIX + '/connect')
    @guarded
    async def connect_guide(request):
        from ..comfy_bridge import installation_root
        from .guide import connection_guide, connection_text
        root = installation_root()
        prepare_token(root)
        from .editor_runtime import available
        store = work_sessions()
        def begun():
            here = store.current_for(chat['client']) if store is not None and chat['client'] else None
            return bool(here and store.get(here)['last_seq'])
        value = connection_guide(root, request.scheme + '://' + request.host, request[query_key].get('lang'),
                                 dict(continuation=await asyncio.to_thread(jobs.continuation_ready), editing=available()),
                                 resumed=await asyncio.to_thread(begun))
        if request[query_key].get('format') == 'json':
            return reply(value)
        return web.Response(text=connection_text(value), content_type='text/plain', headers={'Cache-Control': 'no-store'})

    @routes.post(PREFIX + '/connect/hello')
    @guarded
    async def hello(request):
        value = await body(request)
        connection.update(connected=True, client=value['client'], via=value['via'], last_seen_at=time.time())
        chat['told'].pop(request['agent_client'], None)   # a newly connected agent has heard from no conversation yet
        unfinished = await asyncio.to_thread(jobs.productions.list, status='active', limit=5)
        return reply(dict(connection, unfinished=unfinished))

    @routes.get(PREFIX + '/connect/status')
    @guarded
    async def connect_status(request):
        from .jobs import iso
        from .editor_runtime import available
        seen = connection['last_seen_at']
        # editing: whether this installation can run project and image edits; the card says so when it cannot.
        return reply(dict(connection, connected=seen is not None and 0 <= time.time() - seen < 600,
                          last_seen_at=iso(seen), editing=available()))

    @routes.get(PREFIX)
    @guarded
    async def info(request):
        return reply(await asyncio.to_thread(capabilities, port, jobs))

    @routes.get(PREFIX + '/capabilities')
    @guarded
    async def capability_route(request):
        try:
            value = await port.capability_view({k: str(v) for k, v in request[query_key].items()})
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise chain_error('invalid_capabilities_query', 'The capabilities query has invalid size, task or material metadata.',
                'Use both width and height (256-4096, multiples of 32), task=t2va and adapters=0; see GET /guide.',
                details=[dict(field='/query', allowed='the same parameters as /freevideo/capabilities')]) from None
        return reply(value)

    @routes.get(PREFIX + '/guide')
    @guarded
    async def guide(request):
        from .guide import GUIDE
        return reply(GUIDE)

    @routes.get(PREFIX + '/schema')
    @guarded
    async def schema(request):
        from .schema import schemas
        return reply(schemas())

    @routes.get(PREFIX + '/openapi.json')
    @guarded
    async def openapi(request):
        from .schema import openapi as document
        return reply(document())

    @routes.post(PREFIX + '/plan')
    @guarded
    async def plan_route(request):
        return reply(await asyncio.to_thread(plan, port, await body(request), jobs))

    @routes.post(PREFIX + '/jobs')
    @guarded
    async def submit(request):
        value = await body(request)
        client = request.headers.get('X-FreeVideo-Client', '')[:80] or None
        rows, replay = await asyncio.to_thread(jobs.submit_request, value, client=client,
                                               key=request.headers.get('Idempotency-Key'))
        await jobs.tick()
        def read():
            return [jobs.view(row) for row in rows]
        views = await asyncio.to_thread(read)
        return reply(dict(job=views[0], jobs=views), 200 if replay else 201)

    @routes.get(PREFIX + '/jobs')
    @guarded
    async def list_jobs(request):
        def read():
            status = request[query_key].get('status')
            rows = jobs.list(status=status.split(',') if status else None, project=request[query_key].get('project'),
                             kind=request[query_key].get('kind'), limit=min(200, int(request[query_key].get('limit', '50'))))
            return dict(jobs=[jobs.view(row, detail=False) for row in rows])
        return reply(await asyncio.to_thread(read))

    @routes.get(PREFIX + '/jobs/{id}')
    @guarded
    async def get_job(request):
        row = await asyncio.to_thread(jobs.get, request.match_info['id'])
        return reply(job_answer(await wait_for_job(jobs, row, float(request[query_key].get('wait', '0') or 0))))

    @routes.post(PREFIX + '/jobs/{id}/cancel')
    @guarded
    async def cancel(request):
        def work():
            return jobs.view(jobs.cancel(request.match_info['id']))
        return reply(job_answer(await asyncio.to_thread(work)))

    @routes.post(PREFIX + '/jobs/{id}/resume')
    @guarded
    async def resume(request):
        row, replay = await asyncio.to_thread(jobs.resume, request.match_info['id'],
            key=request.headers.get('Idempotency-Key'), value=await body(request), replay_result=True,
            client=request.headers.get('X-FreeVideo-Client', '')[:80] or None)
        await jobs.tick()
        return reply(job_answer(await asyncio.to_thread(jobs.view, row)))

    @routes.get(PREFIX + '/jobs/{id}/events')
    @guarded
    async def events(request):
        row = await asyncio.to_thread(jobs.get, request.match_info['id'])
        stream = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store'})
        await stream.prepare(request)
        last = None
        while True:
            view = await asyncio.to_thread(jobs.view, row, detail=False)
            text = json.dumps(view, ensure_ascii=False, sort_keys=True)
            if text != last:
                await stream.write(('event: %s\ndata: %s\n\n' % (
                    'done' if row['status'] in DONE else 'progress', text)).encode())
                last = text
            if row['status'] in DONE:
                break
            await asyncio.sleep(.5)
        return stream

    @routes.post(PREFIX + '/sessions')
    @guarded
    async def session_create(request):
        by = session_caller(request)
        value = await body(request)
        return reply(await asyncio.to_thread(sessions.create, title=value.get('title'),
            client=session_client(request, by), by=by), 201)

    @routes.get(PREFIX + '/sessions')
    @guarded
    async def session_list(request):
        session_caller(request)
        return reply(await asyncio.to_thread(sessions.list, **request[query_key]))

    @routes.get(PREFIX + '/sessions/{id}')
    @guarded
    async def session_get(request):
        session_caller(request)
        return reply(await asyncio.to_thread(sessions.get, request.match_info['id']))

    # No route options: ComfyUI copies every route under /api as route(method, path)(handler) and
    # cannot pass them on. HEAD is answered by guarded without opening the stream.
    @routes.get(PREFIX + '/sessions/{id}/events')
    @guarded
    async def session_events(request):
        session_caller(request)
        sid = request.match_info['id']
        query = request[query_key]
        after, limit = query['after'], query['limit']
        await asyncio.to_thread(sessions.get, sid)
        if 'text/event-stream' not in request.headers.get('Accept', '').lower():
            if query['wait']:
                rows = (await sessions.wait(sid, after=after, seconds=query['wait']))[:limit]
            else:
                rows = await asyncio.to_thread(sessions.events, sid, after=after, limit=limit)
            meta = await asyncio.to_thread(sessions.get, sid)
            return reply(dict(events=rows, last_seq=meta['last_seq'], stopped=meta['stopped']))
        cursor = request.headers.get('Last-Event-ID')
        if cursor is not None:
            if len(request.headers.getall('Last-Event-ID')) != 1 or not cursor.isascii() or not cursor.isdecimal():
                raise specs.SpecError(contract.diagnostic('/headers/Last-Event-ID', 'a nonnegative integer', None))
            after = int(cursor)
        stream = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store'})
        async def disconnected():
            while request.transport is not None and not request.transport.is_closing():
                await asyncio.sleep(.25)
        disconnect = asyncio.create_task(disconnected())
        pending = None
        try:
            await stream.prepare(request)
            while request.transport is not None and not request.transport.is_closing():
                pending = asyncio.create_task(sessions.wait(sid, after=after, seconds=15))
                ready, _ = await asyncio.wait((disconnect, pending), return_when=asyncio.FIRST_COMPLETED)
                if disconnect in ready:
                    break
                rows = pending.result()
                if not rows:
                    await stream.write(b': keepalive\n\n')
                    continue
                for row in rows:
                    payload = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
                    await stream.write(('id: %d\nevent: %s\ndata: %s\n\n' %
                                        (row['seq'], row['type'], payload)).encode('utf-8'))
                    after = row['seq']
        except (ConnectionError, asyncio.CancelledError):
            pass
        except JobError as error:
            if error.code != 'not_found':
                raise  # A deleted conversation ends its stream; the next request for it says it is gone.
        finally:
            tasks = [task for task in (disconnect, pending) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return stream

    async def stop_session(sid):
        row = await asyncio.to_thread(sessions.append, sid, dict(type='control', action='stopped'), by='user')
        for callback in tuple(sessions.on_stop):
            result = callback(sid)
            if isawaitable(result):
                await result
        return row

    @routes.post(PREFIX + '/sessions/{id}/events')
    @guarded
    async def session_post(request):
        by = session_caller(request)
        sid = request.match_info['id']
        value = await body(request)
        if value['type'] == 'step' or value['type'] == 'confirm' and (by != 'user' or value['state'] == 'asked'):
            raise JobError('forbidden', 'This event is written by the server or user only')
        if value['type'] == 'control' and value['action'] != 'renamed':
            if by != 'user':
                raise JobError('forbidden', 'Only the page user may stop or continue')
            if value['action'] == 'stopped':
                return reply(await stop_session(sid), 201)
        if by == 'agent' and value['type'] in ('message', 'plan') and sessions.abandoned(sid):
            raise JobError('turn_abandoned', 'The user gave up on this turn')
        if by == 'agent' and value['type'] == 'message' and isinstance(value.get('text'), str):
            value['text'] = chat_text(value['text'])
        client = session_client(request, by)
        def write():
            if value.get('attachments'):
                value['attachments'] = chat_attachments(value['attachments'])
            row = sessions.append(sid, value, by=by, client=client)
            here = sessions.current_for(client) if client and value['type'] == 'message' else None
            if here not in (None, sid):
                # Said where the user no longer looks: the answer itself tells the agent where to say it.
                row['next'] = MOVED % here
            return row
        return reply(await asyncio.to_thread(write), 201)

    @routes.get(PREFIX + '/confirmations')
    @guarded
    async def confirmations_get(request):
        session_caller(request)
        return reply(await asyncio.to_thread(confirmations.settings))

    @routes.put(PREFIX + '/confirmations')
    @guarded
    async def confirmations_put(request):
        if session_caller(request) != 'user':
            raise JobError('forbidden', 'Only the page user may change confirmations')
        return reply(await asyncio.to_thread(confirmations.update, await body(request)))

    @routes.post(PREFIX + '/sessions/{id}/stop')
    @guarded
    async def session_stop(request):
        if session_caller(request) != 'user':
            raise JobError('forbidden', 'Only the page user may stop')
        return reply(await stop_session(request.match_info['id']))

    @routes.post(PREFIX + '/sessions/{id}/resume')
    @guarded
    async def session_resume(request):
        if session_caller(request) != 'user':
            raise JobError('forbidden', 'Only the page user may continue')
        return reply(await asyncio.to_thread(sessions.append, request.match_info['id'],
            dict(type='control', action='resumed'), by='user'))

    @routes.post(PREFIX + '/sessions/{id}/stage')
    @guarded
    async def session_stage(request):
        if session_caller(request) == 'user':
            raise JobError('forbidden', 'Only the agent reports a stage')
        sid, value = request.match_info['id'], await body(request)
        def write():
            if sessions.abandoned(sid):
                raise JobError('turn_abandoned', 'The user gave up on this turn')
            sessions.report_stage(sid, value['stage'])
            return dict(stage=value['stage'])
        return reply(await asyncio.to_thread(write))

    def publish_media(work, suffix):
        from . import media
        with tempfile.TemporaryDirectory(prefix='.agent-stage-', dir=port.input_root) as temporary:
            stage = Path(temporary)
            target = stage / ('media' + suffix)
            made = work(target) or {}
            info = media.inspect(target)
            folder = 'freevideo/agent/assets/' + uuid.uuid4().hex
            staged = stage.relative_to(port.input_root).as_posix()
            def relocate(value):
                if isinstance(value, str) and value.startswith(staged + '/'):
                    return folder + value[len(staged):]
                if isinstance(value, dict):
                    return {k: relocate(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [relocate(v) for v in value]
                return value
            made = relocate(made)
            for document in stage.glob('*.edit.json'):
                document.write_text(json.dumps(relocate(json.loads(document.read_text(encoding='utf-8')))), encoding='utf-8')
            destination = port.input_path(folder, create=True)
            os.replace(stage, destination)
            relative = folder + '/' + target.name
            info.pop('path', None)
            made.pop('path', None)
            return assets.public(dict(made, **info, file=relative, view=assets.view(relative)))

    @routes.post(PREFIX + '/imports')
    @guarded
    async def import_media(request):
        from . import imports
        value = None if request.content_type == 'multipart/form-data' else await body(request)
        try:
            result, status = await imports.upload(request, value, port.output_root,
                getattr(server, '_freevideo_editor', None), import_lock, upload_decoders)
        except specs.SpecError:
            raise
        except (ValueError, AssertionError):
            imports.invalid('/file', 'a well-formed multipart upload with video or audio content')
        return reply(result, status)

    @routes.get(PREFIX + '/imports')
    @guarded
    async def list_imports(request):
        from . import imports
        return reply(await asyncio.to_thread(imports.listing, port.output_root))

    @routes.post(PREFIX + '/media')
    @guarded
    async def upload(request):
        from ..comfy_assets import media_kind
        from . import media
        with tempfile.TemporaryDirectory(prefix='.agent-upload-', dir=port.input_root) as temporary:
            if request.content_type == 'multipart/form-data':
                reader = await request.multipart()
                part = await reader.next()
                if part is None or part.name != 'file' or not part.filename:
                    raise JobError('invalid_media', 'A file part is required')
                name = part.filename
                contract.validate(contracts['POST', '/media']['body']['properties']['name'], name)
                media_kind(name)
                target = Path(temporary) / ('media' + Path(name).suffix.lower())
                size = 0
                with target.open('xb') as output:
                    while True:
                        chunk = await part.read_chunk(size=256 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > contract.MAX_UPLOAD_BYTES:
                            raise JobError('too_large', 'Upload exceeds limit', limit_bytes=contract.MAX_UPLOAD_BYTES)
                        output.write(chunk)
                if await reader.next() is not None or size == 0:
                    raise JobError('invalid_media', 'Supply one nonempty file')
            else:
                value = await body(request)
                name = value['name']
                media_kind(name)
                try:
                    data = base64.b64decode(value['data_base64'], validate=True)
                except (binascii.Error, ValueError):
                    raise JobError('invalid_media', 'Invalid base64') from None
                if len(data) > contract.MAX_UPLOAD_BYTES:
                    raise JobError('too_large', 'Upload exceeds limit', limit_bytes=contract.MAX_UPLOAD_BYTES)
                if not data:
                    raise JobError('invalid_media', 'Empty file')
                target = Path(temporary) / ('media' + Path(name).suffix.lower())
                target.write_bytes(data)
            async with upload_decoders:
                try:
                    info, suffix = await asyncio.to_thread(media.validate_upload, target)
                except (OSError, ValueError, SyntaxError):
                    raise JobError('invalid_media', 'Media cannot be decoded within the upload limits') from None
            info.pop('path', None)
            def digest():
                with target.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()
            identity = await asyncio.to_thread(digest)
            relative = 'freevideo/agent/assets/' + identity + suffix
            out = port.input_path(relative, create=True)
            os.replace(target, out)
            info['name'] = identity + suffix
            info['original_name'] = name
            return reply(dict(**assets.view(relative), **info), 201)

    @routes.get(PREFIX + '/media/{kind}/{file:.+}/file')
    @guarded
    async def media_file(request):
        from ..comfy_assets import media_kind
        kind, relative = request.match_info['kind'], request.match_info['file']
        contract.validate(contract.FILE, relative)
        if kind not in ('input', 'output'):
            raise JobError('not_found', 'No such media kind')
        media_kind(relative)
        def resolve():
            root = port.input_root if kind == 'input' else port.output_root
            path = root / relative
            # Refuse symlinks even if they point back inside the asset root.
            if any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)):
                raise JobError('not_found', 'Media is unavailable')
            path = port.input_path(relative) if kind == 'input' else port.output_path(relative)
            if not path.is_file() or not path.stat().st_size:
                raise JobError('not_found', 'Media is unavailable')
            if request.headers.get('Range'):
                try:
                    selected = request.http_range
                    if selected.start is not None and selected.start >= path.stat().st_size:
                        raise ValueError
                except ValueError:
                    raise JobError('invalid_range', 'Invalid byte range') from None
            return path
        path = await asyncio.to_thread(resolve)
        return web.FileResponse(path, headers={'Cache-Control': 'private, no-store',
            'Content-Disposition': 'attachment', 'X-Content-Type-Options': 'nosniff'})

    @routes.post(PREFIX + '/media/inspect')
    @guarded
    async def inspect(request):
        from . import media
        value = await body(request)
        item = specs.media_item(value['source'], '/source')
        def read():
            path = source_path(item)
            info = media.inspect(path)
            view = assets.view(item['file']) if 'file' in item else library_view(port, port.output_relative(path))
            return assets.public(dict(info, **view))
        return reply(await asyncio.to_thread(read))

    def source_path(item):
        if 'file' in item:
            path = port.input_path(item['file'])
            if not path.is_file():
                raise KeyError(item['file'])
            return path
        return jobs.source_video(item)

    @routes.post(PREFIX + '/media/frame')
    @guarded
    async def frame(request):
        value = await body(request)
        item = specs.media_item(value['source'], '/source')
        at = value.get('at', item.get('frame', 'last'))
        source = await asyncio.to_thread(source_path, item)
        made = await asyncio.to_thread(publish_media, lambda out: port.extract_frame(source, out, at), '.png')
        return reply(made, 201)

    @routes.post(PREFIX + '/media/edit')
    @guarded
    async def edit(request):
        value = await body(request)
        item = specs.media_item(value['source'], '/source', image=True)
        target = value.get('target')
        def work(out):
            source = item.get('file')
            if not source:
                extracted = out.with_name('source.png')
                port.extract_frame(jobs.source_video(item), extracted, item['frame'])
                source = extracted.relative_to(port.input_root).as_posix()
            made = port.edit(source, value.get('recipe') or value.get('document'), out,
                             target=tuple(target) if target else None)
            return dict(document=made['doc'])
        return reply(await asyncio.to_thread(publish_media, work, '.png'), 201)

    @routes.post(PREFIX + '/media/contact-sheet')
    @guarded
    async def sheet(request):
        from . import media
        value = await body(request)
        item = specs.media_item(value.get('source'), '/source')
        fields = contracts['POST', '/media/contact-sheet']['body']
        value = contract.defaults(fields, value)
        source = await asyncio.to_thread(source_path, item)
        made = await asyncio.to_thread(publish_media, lambda out: media.contact_sheet(source, out,
            count=value['count'], columns=value['columns']), '.jpg')
        return reply(made, 201)

    @routes.get(PREFIX + '/library')
    @guarded
    async def library_route(request):
        return reply(await asyncio.to_thread(library, port, jobs, limit=int(request[query_key].get('limit', '24')),
                                             before=request[query_key].get('before'), query=request[query_key].get('query'),
                                             project=request[query_key].get('project')))

    @routes.get(PREFIX + '/library/{day}/{run}')
    @guarded
    async def library_item(request):
        def read():
            identity = saved_identity(port, request.match_info['day'], request.match_info['run'])
            try:
                video = port.library_video(identity)
            except (JobError, OSError, ValueError):
                raise JobError('not_found', 'Library item does not exist') from None
            folder = video.parent
            from ..comfy_library import _report
            from ..comfy_assets import output_summary
            report = _report(video)
            summary = output_summary(report, Path(port.output_relative(video)))
            if summary.get('geometry'):
                from .media import saved_geometry
                summary['geometry'] = saved_geometry(video, summary['geometry'])
            prompt = (folder / 'prompt.txt').read_text(encoding='utf-8') if (folder / 'prompt.txt').is_file() else None
            return dict(id=identity, video=library_view(port, port.output_relative(video)), prompt=prompt,
                        seed=report.get('seed'), geometry={k: v for k, v in (summary.get('geometry') or {}).items()
                                                           if k in ('width', 'height', 'frames', 'seconds', 'fps')},
                        sampling_plan=summary.get('sampling_plan'), preview=summary.get('preview'),
                        request_seconds=summary.get('request_seconds'), sample_seconds=summary.get('sample_seconds'))
        return reply(await asyncio.to_thread(read))

    @routes.get(PREFIX + '/library/{day}/{run}/file')
    @guarded
    async def library_file(request):
        from ..comfy_library import video_download
        def read():
            identity = saved_identity(port, request.match_info['day'], request.match_info['run'])
            try:
                path, name = video_download(port.output_root, identity)
            except (OSError, ValueError, TypeError):
                return None
            if request.headers.get('Range'):
                try:
                    byte_range = request.http_range
                    size = path.stat().st_size
                    if byte_range.start is not None and byte_range.start >= size:
                        raise ValueError
                except ValueError:
                    raise JobError('invalid_range', 'Invalid byte range') from None
            return path, name
        value = await asyncio.to_thread(read)
        if value is None:
            return problem('not_found', 'The saved video is unavailable.', 404, details=[], retryable=False,
                           next='GET /freevideo/agent/v1/library and select an existing video ID.')
        path, name = value
        return web.FileResponse(path, headers={'Content-Type': 'video/mp4',
            'Content-Disposition': 'attachment; filename="%s"' % name, 'Cache-Control': 'private, no-store'})

    @routes.get(PREFIX + '/library/{day}/{run}/thumbnail')
    @guarded
    async def library_thumbnail(request):
        from ..comfy_library import thumbnail
        identity = await asyncio.to_thread(saved_identity, port, request.match_info['day'], request.match_info['run'])
        async with thumbnails:
            try:
                data = await asyncio.to_thread(thumbnail, port.output_root, identity)
            except Exception:
                return problem('not_found', 'The saved thumbnail is unavailable.', 404, details=[], retryable=False,
                               next='GET /freevideo/agent/v1/library and select an existing video ID.')
        return web.Response(body=data, content_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})

    @routes.get(PREFIX + '/inbox')
    @guarded
    async def inbox(request):
        # The hook already attached this request to the caller's session; answer with its unread user words.
        store, sid = work_sessions(), request.get('agent_session')
        client = request['agent_client']
        if store is not None and sid is None:
            # An agent that only listens has no session yet: give it one, so the page can speak into it.
            sid = await asyncio.to_thread(lambda: store.current_for(client) or store.create(client=client)['id'])
            request['agent_session'] = sid
        # Without a session of its own choosing, the agent listens wherever its user is.
        follow = store is not None and not request.headers.get('X-FreeVideo-Session', '').strip()
        def answer(value):
            told, chat['told'][client] = chat['told'].get(client), value['session']
            scenes = jobs.whitebox.open_for(value['session']) if value['inbox'] else []
            if scenes:
                value['whitebox'] = scenes
                value['next'] = (WHITEBOX_PENDING + ' ' + value.get('next', '')).strip()
            if follow and told != value['session']:
                first = value['inbox'][0]['seq'] if value['inbox'] else store.get(value['session'])['last_seq'] + 1
                if first > 1:
                    # What the user sees above these words. An agent that has to fetch it answers from the conversation it left.
                    value['before'] = [dict(e, text=e['text'][:BEFORE_TEXT]) if isinstance(e.get('text'), str) else e
                                       for e in store.events(value['session'], after=max(0, first - 1 - BEFORE_LINES),
                                                             limit=BEFORE_LINES) if e['seq'] < first]
                said = (RESUMED if first > 1 else '') if told is None else (
                    MOVED % value['session'] + (MOVED_BACK % value['session'] if first > 1 else MOVED_NEW))
                value['next'] = (said + ' ' + value.get('next', '')).strip()
            return reply(value)
        deadline = time.monotonic() + min(50., float(request[query_key].get('wait', '0') or 0))
        chat['waiting'] += 1
        try:
            while True:
                if follow:
                    # The user may delete the conversation the agent waits in; with none left, it listens in a new one.
                    sid = request['agent_session'] = await asyncio.to_thread(
                        lambda: store.current_for(client) or (sid if store.has(client, sid) else store.create(client=client)['id']))
                if store is not None and sid is not None:
                    # Mark before reading so words arriving in between wake the wait below.
                    def read():
                        mark = store.get(sid)['last_seq']
                        return mark, store.unread(sid, client)
                    mark, pending = await asyncio.to_thread(read)
                    if pending:
                        return answer(dict(session=sid, inbox=pending[-20:]))
                left = deadline - time.monotonic()
                if left <= 0:
                    # again: nothing new this time; the caller simply waits once more. An agent decides what to
                    # do next from the last answer it read, so the empty one says it in words as well.
                    return answer(dict(session=sid, inbox=[], again=True, next=KEEP_WAITING))
                if store is None or sid is None:
                    await asyncio.sleep(min(left, 1.))
                else:
                    await store.wait(sid, after=mark, seconds=min(left, 25.), by='user',
                                     leave=(lambda here=sid: store.current_for(client) != here) if follow else None)
        finally:
            chat['waiting'] -= 1
            # The agent was here for the whole wait, not only when it began.
            connection['last_seen_at'] = time.time()

    @routes.get(PREFIX + '/queue')
    @guarded
    async def queue(request):
        def read():
            running, pending = port.snapshot()
            active = [jobs.view(row, detail=False) for row in jobs.list(status=list(ACTIVE), limit=200)]
            return dict(running=len(running), pending=len(pending), vlm_busy=port.vlm_busy(), agent_jobs=active,
                              **jobs.queue_view())
        return reply(await asyncio.to_thread(read))

    def drama_handler(operation, *, local=False):
        async def handler(request):
            productions = jobs.productions
            identity = request.match_info.get('id')
            shot = request.match_info.get('shot')
            if operation == 'list':
                return reply(dict(productions=await asyncio.to_thread(productions.list, **request[query_key])))
            if operation == 'get':
                # Long waits are asynchronous: they hold no worker thread, so user controls never queue behind them.
                return reply(await productions.get_async(identity, **request[query_key]))
            if operation == 'sheet':
                value = await asyncio.to_thread(productions.sheet, identity, shot, **request[query_key])
                def picture(data):
                    return dict(mimeType='image/png' if data.startswith(b'\x89PNG') else 'image/jpeg',
                                data=base64.b64encode(data).decode('ascii'))
                for row in value['takes']:
                    row['image'] = picture(row['image'])
                if value['previous'] is not None:
                    value['previous'] = picture(value['previous'])
                return reply(value)
            value = dict(await body(request))
            ref = value.pop('ref', None)
            key = request.headers.get('Idempotency-Key')
            if key is not None:
                contract.validate(contract.REF, key)
            if key and ref and key != ref:
                raise JobError('idempotency_conflict', 'Conflicting keys')
            ref = key or ref
            client = request.headers.get('X-FreeVideo-Client', '')[:80] or None
            # Credentials always identify an agent, even on the Studio route.
            authenticated = bool(request.headers.get('Authorization') or request.headers.get('X-FreeVideo-Agent'))
            by = 'user' if local and not authenticated else 'agent'
            if operation == 'plan':
                return reply(await asyncio.to_thread(productions.plan, value, local=False))
            if operation == 'start':
                return reply(await asyncio.to_thread(productions.start, value, by=by, ref=ref, local=False, client=client))
            args = [identity] + ([shot] if shot else [])
            return reply(await asyncio.to_thread(getattr(productions, operation), *args, by=by, ref=ref, client=client, **value))
        return guarded(handler)

    for operation, (method, suffix, _) in contract.DRAMA_OPERATIONS.items():
        getattr(routes, method.lower())(PREFIX + suffix)(drama_handler(operation))
    for operation in ('pause', 'resume'):
        routes.post('/freevideo/agent/drama/{id}/' + operation)(drama_handler(operation, local=True))

    def cast_handler(operation, *, local=False):
        async def handler(request):
            cast = jobs.cast
            identity = request.match_info.get('id')
            if operation == 'list':
                return reply(await asyncio.to_thread(cast.list, **request[query_key]))
            if operation == 'get':
                return reply(await asyncio.to_thread(cast.get, identity))
            value = dict(await body(request))
            ref = value.pop('ref', None)
            key = request.headers.get('Idempotency-Key')
            if key is not None:
                contract.validate(contract.REF, key)
            if key and ref and key != ref:
                raise JobError('idempotency_conflict', 'Conflicting keys')
            ref = key or ref
            client = request.headers.get('X-FreeVideo-Client', '')[:80] or None
            # The library page acts for the user; any credential identifies an agent.
            authenticated = bool(request.headers.get('Authorization') or request.headers.get('X-FreeVideo-Agent'))
            by = 'user' if local and not authenticated else 'agent'
            if operation == 'create':
                return reply(await asyncio.to_thread(cast.create, value, by=by, ref=ref, local=False, client=client), 201)
            revision = request.headers.get('If-Match')
            # HTTP callers never name local paths; only in-process callers may pass local=True.
            options = dict(local=False) if operation == 'update' else {}
            return reply(await asyncio.to_thread(getattr(cast, operation), identity, value, by=by, ref=ref,
                                                 revision=revision, client=client, **options), 201 if operation == 'generate' else 200)
        return guarded(handler)

    for operation, (method, suffix, _) in contract.CAST_OPERATIONS.items():
        getattr(routes, method.lower())(PREFIX + suffix)(cast_handler(operation))
        getattr(routes, method.lower())('/freevideo/agent' + suffix)(cast_handler(operation, local=True))

    def project_view(value):
        from urllib.parse import urlencode, urlsplit, parse_qs

        def address(key, file):
            return PREFIX + '/editor/sources/content?' + urlencode(dict(key=key, file=file))

        for key, info in value.get('sources', {}).items():
            for field in ('video', 'audio', 'thumbnail'):
                if info.get(field):
                    info[field] = address(key, 'thumbnail' if field == 'thumbnail' else 'preview')
        for item in value.get('items', []):
            if not item.get('thumbnail'):
                continue
            parsed = urlsplit(item['thumbnail'])
            query = parse_qs(parsed.query)
            key = query.get('key', [None])[0]
            if parsed.path == '/freevideo/library/thumbnail':
                key = 'work:' + query['id'][0]
            elif parsed.path == '/freevideo/editor/media':
                key = 'import:' + query['id'][0]
            item['thumbnail'] = address(key, 'thumbnail') if key else None
        return value

    def project_handler(operation):
        async def handler(request):
            projects = jobs.projects
            identity = request.match_info.get('id')
            if operation in ('list', 'get'):
                args = [identity] if operation == 'get' else []
                return reply(project_view(await asyncio.to_thread(getattr(projects, operation), *args)))
            value = dict(await body(request))
            ref = value.pop('ref', None)
            key = request.headers.get('Idempotency-Key')
            if key is not None:
                contract.validate(contract.REF, key)
            if key and ref and key != ref:
                raise JobError('idempotency_conflict', 'Conflicting keys')
            args = [] if operation == 'create' else [identity]
            extra = dict(client=request.headers.get('X-FreeVideo-Client', '')[:80] or None)
            if operation in ('generate', 'export', 'listen'):
                extra['jobs'] = jobs
            if operation == 'generate':
                def capability(size):
                    try:
                        return port.capability_snapshot({'width': str(size['width']), 'height': str(size['height'])})
                    except (ValueError, KeyError, TypeError):
                        return None
                extra['capability'] = capability
            result = await asyncio.to_thread(getattr(projects, operation), *args, value, ref=key or ref, **extra)
            if operation in ('generate', 'export', 'listen'):
                await jobs.tick()
            return reply(result, 201 if operation in ('create', 'generate', 'export', 'listen') else 200)
        return guarded(handler)

    for operation, (method, suffix, _) in contract.PROJECT_OPERATIONS.items():
        getattr(routes, method.lower())(PREFIX + suffix)(project_handler(operation))

    def image_handler(operation):
        async def handler(request):
            images = jobs.images
            identity = request.match_info.get('id')
            if operation == 'get':
                return reply(await asyncio.to_thread(images.get, identity))
            value = dict(await body(request))
            ref = value.pop('ref', None)
            key = request.headers.get('Idempotency-Key')
            if key is not None:
                contract.validate(contract.REF, key)
            if key and ref and key != ref:
                raise JobError('idempotency_conflict', 'Conflicting keys')
            args = [] if operation == 'create' else [identity]
            extra = dict(client=request.headers.get('X-FreeVideo-Client', '')[:80] or None)
            if operation == 'render':
                extra['jobs'] = jobs
            result = await asyncio.to_thread(getattr(images, operation), *args, value, ref=key or ref, **extra)
            if operation == 'render':
                await jobs.tick()
            return reply(result, 201 if operation in ('create', 'render') else 200)
        return guarded(handler)

    for operation, (method, suffix, _) in contract.IMAGE_OPERATIONS.items():
        getattr(routes, method.lower())(PREFIX + suffix)(image_handler(operation))

    def whitebox_handler(operation):
        async def handler(request):
            scenes = jobs.whitebox
            if operation == 'get':
                deadline = time.monotonic() + request[query_key].get('wait', 0)
                while True:
                    view = await asyncio.to_thread(scenes.read, request.match_info['id'])
                    if view['status'] in ('ready', 'failed') or time.monotonic() >= deadline:
                        return reply(view)
                    await asyncio.sleep(.5)
            value = await body(request)
            if not scenes.page_open():
                raise JobError('page_required', 'No FreeVideo page is open to render the scene')
            return reply(await asyncio.to_thread(scenes.create, value['code'], value.get('summary'),
                                                 request.get('agent_session')), 201)
        return guarded(handler)

    for operation, (method, suffix, _) in contract.WHITEBOX_OPERATIONS.items():
        getattr(routes, method.lower())(PREFIX + suffix)(whitebox_handler(operation))

    # The page renders scenes: it asks for work, then sends back the video or the reason it failed.
    def whitebox_confirm(notice):
        store = work_sessions()
        if not notice or store is None:
            return
        try:
            store.append(notice['session'], dict(type='confirm', id='cf' + secrets.token_hex(5), state='asked',
                         question=notice['question'], whitebox=dict(work=notice['work'], seconds=notice['seconds'],
                                                                      summary=notice['summary'])), by='builtin')
        except Exception:
            logging.getLogger(__name__).warning('White-model confirmation failed', exc_info=True)

    @routes.get('/freevideo/agent/whitebox/next')
    @guarded
    async def whitebox_next(request):
        return reply(dict(scene=await asyncio.to_thread(jobs.whitebox.claim)))

    @routes.get('/freevideo/agent/whitebox/presets')
    @guarded
    async def whitebox_presets(request):
        return reply(dict(presets=[{k: row[k] for k in ('id', 'name', 'shot', 'prompt')} for row in PRESETS]))

    def whitebox_ask(question, system, answer_format, max_tokens):
        """One question to the local prompt model; a refusal, a timeout or a lost job is an OSError for pick()."""
        try:
            key = port.vlm_start('ask', dict(questions=[dict(text=question, images=[])], format=answer_format,
                                              max_tokens=max_tokens, system=system))
        except JobError as error:
            raise OSError(error.code) from None
        deadline = time.monotonic() + WHITEBOX_ASK_SECONDS
        while True:
            try:
                state = port.vlm_status(key)
            except (ValueError, JobError):
                raise OSError('lost') from None
            phase = state.get('phase')
            if phase == 'complete':
                answers = state.get('answers') or []
                if len(answers) != 1 or not isinstance(answers[0], dict):
                    raise OSError('invalid_output')
                return answers[0]
            if phase not in ('starting', 'loading', 'rewriting', 'answering', 'finishing', 'cancelling'):
                raise OSError(str(state.get('error') or phase))
            if time.monotonic() > deadline:
                port.vlm_cancel(key)
                raise TimeoutError('timeout')
            time.sleep(.5)

    @routes.post('/freevideo/agent/whitebox/try')
    @guarded
    async def whitebox_try(request):
        if request.content_type != 'application/json':
            return problem('unsupported_media_type', 'Send JSON', 415)
        value = await body(request)
        if not isinstance(value, dict):
            raise JobError('invalid_request', 'Send a JSON object')
        sid = value.get('session')
        if sid is not None:
            store = work_sessions()
            if store is None or not isinstance(sid, str) or not store.has(chat['client'], sid):
                raise JobError('not_found', 'No such conversation')
        shot = value.get('shot')
        if (shot is None) == (value.get('preset') is None):
            raise JobError('invalid_request', 'Send either a preset or a shot')
        if shot is not None:
            # The built-in AI picks a ready-made scene for the shot; when none fits, the video is made as usual.
            picked = await asyncio.to_thread(whitebox_pick.pick, shot, whitebox_ask)
            if picked['code'] is None:
                return reply(dict(scene=None, reason=picked['reason']))
            row = whitebox_preset(picked['params']['template'])
            code = picked['code']
        else:
            row = whitebox_preset(value.get('preset'))
            if row is None:
                raise JobError('not_found', 'No such ready-made scene')
            shot, code = row['shot'], row['code']
        # The conversation gets the confirmation card once the page has rendered the scene.
        notice = dict(question=row['prompt'], summary=row['name']) if sid is not None else None
        scene = await asyncio.to_thread(jobs.whitebox.create, code, shot, sid, notice)
        return reply(dict(scene, preset=row['id'], name=row['name'], prompt=row['prompt']), 201)

    @routes.post('/freevideo/agent/whitebox/{id}/video')
    @guarded
    async def whitebox_video(request):
        scenes = jobs.whitebox
        with tempfile.TemporaryDirectory(prefix='.whitebox-', dir=port.input_root) as temporary:
            target = Path(temporary) / 'scene.mp4'
            size = 0
            with target.open('xb') as output:
                async for chunk in request.content.iter_chunked(256 * 1024):
                    size += len(chunk)
                    if size > MAX_WHITEBOX_VIDEO:
                        raise JobError('too_large', 'Upload exceeds limit', limit_bytes=MAX_WHITEBOX_VIDEO)
                    output.write(chunk)
            if not size:
                raise JobError('invalid_media', 'Supply the rendered video')
            async with upload_decoders:
                scene = await asyncio.to_thread(scenes.finish, request.match_info['id'], target)
            await asyncio.to_thread(whitebox_confirm, scenes.take_notice(scene['id']))
            return reply(scene)

    @routes.post('/freevideo/agent/whitebox/{id}/failed')
    @guarded
    async def whitebox_failed(request):
        if request.content_type != 'application/json':
            return problem('unsupported_media_type', 'Send JSON', 415)
        value = await body(request)
        message = value.get('message') if isinstance(value, dict) else None
        return reply(await asyncio.to_thread(jobs.whitebox.fail, request.match_info['id'], message))

    @routes.get(PREFIX + '/editor/sources/content')
    @guarded
    async def editor_source_content(request):
        from .. import comfy_editor
        query = request[query_key]
        def resolve():
            try:
                path = (comfy_editor.thumbnail if query['file'] == 'thumbnail' else comfy_editor.media_path)(
                    jobs.projects.root, query['key'])
                if not path.is_file() or not path.stat().st_size:
                    raise FileNotFoundError
            except (OSError, ValueError, IndexError):
                raise JobError('source_missing', 'The project source is unavailable.') from None
            if request.headers.get('Range'):
                try:
                    selected = request.http_range
                    if selected.start is not None and selected.start >= path.stat().st_size:
                        raise ValueError
                except ValueError:
                    raise JobError('invalid_range', 'Invalid byte range') from None
            return path
        if query['file'] == 'thumbnail':
            async with thumbnails:
                path = await asyncio.to_thread(resolve)
        else:
            path = await asyncio.to_thread(resolve)
        kind = 'image/jpeg' if query['file'] == 'thumbnail' else 'audio/mp4' if path.suffix == '.m4a' else 'video/mp4'
        return web.FileResponse(path, headers={'Content-Type': kind, 'Cache-Control': 'private, no-store',
            'Content-Disposition': 'attachment', 'X-Content-Type-Options': 'nosniff'})

    server._freevideo_agent_registered = True
    if startup_failed:
        # Keep the guarded 503 routes, but publish no partial stores, token or scheduler.
        return

    async def scheduler():
        while True:
            try:
                await jobs.tick()
            except Exception:  # noqa: BLE001 - keep scheduling; each job records its own failure
                import logging
                logging.getLogger(__name__).exception('FreeVideo agent scheduler')
            await asyncio.sleep(.5 if await asyncio.to_thread(jobs.active) else 2.)

    try:
        server._freevideo_agent_task = asyncio.get_event_loop().create_task(scheduler())
    except RuntimeError:
        pass
    # Write the token and an endpoint file for local clients (MCP server, SDK, CLI).
    try:
        from ..comfy_bridge import installation_root
        from comfy.cli_args import args
        root = installation_root()
        prepare_token(root)
        listen = (args.listen or '127.0.0.1').split(',')[0]
        host = '127.0.0.1' if listen in ('0.0.0.0', '') else '[::1]' if listen == '::' else listen
        scheme = 'https' if getattr(args, 'tls_keyfile', None) else 'http'
        endpoint = dict(url='%s://%s:%d' % (scheme, host if ':' not in host or host.startswith('[') else '[%s]' % host,
                                            args.port), pid=os.getpid(), api=PREFIX, started_at=time.time())
        path = token_path(root).with_name('endpoint.json')
        path.write_text(json.dumps(endpoint), encoding='utf-8')
    except Exception as error:  # noqa: BLE001 - discovery is a convenience; the API works without it
        logging.getLogger(__name__).warning('[FreeVideo] Agent discovery unavailable: %s', error)
