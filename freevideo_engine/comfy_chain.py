"""Local ComfyUI queue adapter and optional background-chain routes."""
import asyncio
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import platform
import ssl
import time
from urllib import error, request

from .chain_runner import ChainBusy, ChainPartTooShort, ChainRunner, ChainTooLong
from . import chain_plan, chain_store

RUNNER = None
CONTINUE_INPUTS = {'after': 'continue_from', 'before': 'continue_to'}


def _generate_inputs():
    try:
        from .comfy_nodes import FreeVideoGenerate
        return {row.id: row for row in FreeVideoGenerate.define_schema().inputs}
    except Exception:
        return {}


def seconds_for_frames(frames, minimum=1.625, maximum=60., step=.1):
    """Pick a legal duration inside the requested VAE cell, below its upper edge."""
    from .geometry import geometry
    value = (frames - .5) / 24
    if step is not None and step > 0:
        value = round(math.floor(value / step) * step, 10)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    if geometry(seconds=value)['frames'] != frames:
        raise ValueError('Block frames cannot be represented by the node duration input')
    return value


class ComfyHost:
    continuation_inputs = (*CONTINUE_INPUTS.values(), 'voice_reference', 'hide_tail')
    # Inputs a continued part drops. The media panel and references stay: the Generate node keeps their pictures.
    media_inputs = ('first', 'last', 'conditioning')

    def __init__(self, server):
        self.server = server

    def continued(self, prompt, node):
        """A continued part's media panel keeps only the pictures it uses (prompt is this submission's copy)."""
        link = prompt[node]['inputs'].get('media')
        if not (isinstance(link, list) and len(link) == 2 and link[0] in prompt):
            return
        media = prompt[link[0]]
        if not isinstance(media, dict) or media.get('class_type') != 'FreeVideoMedia':
            return
        inputs = media.setdefault('inputs', {})
        for name in ('first', 'last', 'reference_audio'):
            inputs.pop(name, None)
        if isinstance(inputs.get('assets'), str):
            from .comfy_assets import continued_assets
            inputs['assets'] = continued_assets(inputs['assets'])

    def inputs(self, block_inputs):
        schema = _generate_inputs()
        values = {key: block_inputs[key] for key in ('text', 'seed')}
        if 'frames' in schema:
            values['frames'] = block_inputs['frames']
        else:
            seconds = schema.get('seconds')
            values['seconds'] = seconds_for_frames(block_inputs['frames'],
                getattr(seconds, 'min', 1.625), getattr(seconds, 'max', 60.), getattr(seconds, 'step', .1))
        independent = os.environ.get('FREEVIDEO_CHAIN_INDEPENDENT') == '1'
        for key, name in CONTINUE_INPUTS.items():
            # Each direction is filled once the node offers its input.
            if name in schema:
                values[name] = '' if independent else block_inputs[key]
        if 'voice_reference' in schema and values.get('continue_from'):
            values['voice_reference'] = True
        if ('hide_tail' in schema and not independent and block_inputs.get('index', 0) < block_inputs.get('count', 0) - 1
                and continuation_supported()):
            # Only a backend that continues videos makes the extra tail frames; others ignore the option.
            values['hide_tail'] = True
        return values

    def work_exists(self, path):
        import folder_paths
        root = Path(folder_paths.get_output_directory()).resolve()
        output = (root / path).resolve()
        return output.is_relative_to(root) and output.is_file()

    def submit(self, prompt_id, prompt, extra_data):
        try:
            from comfy.cli_args import args
        except ImportError:
            args = None
        address = getattr(self.server, 'address', None) or getattr(args, 'listen', None) or '127.0.0.1'
        port = getattr(self.server, 'port', None) or getattr(args, 'port', None) or 8188
        address = address.split(',')[0].strip('[]')
        address = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(address, address)
        if ':' in address:
            address = '[' + address + ']'
        tls = bool(getattr(args, 'tls_keyfile', None))
        url = '%s://%s:%s/prompt' % ('https' if tls else 'http', address, port)
        body = dict(prompt=prompt, prompt_id=prompt_id, extra_data=extra_data)
        if extra_data.get('client_id') is not None:
            body['client_id'] = extra_data['client_id']
        message = request.Request(url, data=json.dumps(body).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'}, method='POST')
        # This request belongs to our own server, even with an inherited proxy.
        handlers = [request.ProxyHandler({})]
        if tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(request.HTTPSHandler(context=context))
        opener = request.build_opener(*handlers)
        try:
            with opener.open(message, timeout=30) as response:
                response.read()
        except error.HTTPError as failure:
            if failure.code != 400:
                raise
            try:
                value = json.loads(failure.read())
                parts = [(value.get('error') or {}).get('message') or 'Prompt validation failed']
                first = next(iter((value.get('node_errors') or {}).values()), None)
                if first:
                    parts.append(json.dumps(first, ensure_ascii=False))
                raise RuntimeError('; '.join(parts)) from None
            except (ValueError, TypeError, AttributeError):
                raise RuntimeError('Prompt validation failed') from None

    def status(self, prompt_id):
        queue = self.server.prompt_queue
        running, pending = queue.get_current_queue()
        for rows, state in ((running, 'running'), (pending, 'pending')):
            if any(row[1] == prompt_id for row in rows):
                return dict(state=state, error=None, output=None)
        history = queue.get_history(prompt_id=prompt_id).get(prompt_id)
        if history is None:
            return dict(state='missing', error=None, output=None)
        status = history.get('status')
        outputs = history.get('outputs') or {}
        output = None
        for node in outputs.values():
            summaries = node.get('freevideo_summary') or []
            if summaries:
                output = summaries[0].get('video')
                break
            for row in node.get('videos', node.get('images', [])):
                if row.get('type') == 'output' and row.get('filename', '').lower().endswith('.mp4'):
                    output = str(PurePosixPath(row.get('subfolder', '')) / row['filename'])
                    break
        if status and status.get('status_str') == 'success' or status is None and outputs:
            return dict(state='success', error=None, output=output)
        messages = (status or {}).get('messages', [])
        if any(row[0] == 'execution_interrupted' for row in messages):
            return dict(state='interrupted', error=None, output=None)
        message = next((row[1].get('exception_message') for row in messages if row[0] == 'execution_error'), None)
        return dict(state='error', error=message or 'Prompt execution failed', output=None)

    def cancel(self, prompt_id, node):
        from .comfy_progress import cancel_request
        import nodes
        return cancel_request(self.server.prompt_queue, prompt_id, node, nodes.interrupt_processing)

    def join(self, record, cancelled):
        try:
            from .chain_join import join_outputs
        except ImportError:
            raise RuntimeError('Joining is not available in this version') from None
        planned = record.get('plan')
        blocks = chain_store.leading_blocks(record)
        # A planned chain shows exactly the requested length: what the last block made beyond it is left out.
        frames = planned['frames'] if planned and not planned['single'] and len(blocks) == len(record['blocks']) else None
        kept = {'leads': record['lead_reader'], 'expected_seams': record.get('expected_seams')} \
            if record.get('lead_reader') is not None else {}
        from .chain_level import voiced
        # Parts with spoken lines keep their voice level and fullness to the first part's.
        speaking = [voiced(block.get('inputs', {}).get('text')) for block in blocks]
        return join_outputs([block['output'] for block in blocks], record['id'], cancelled,
                            frames=frames, replace=record.get('output'), speaking=speaking, **kept)

    def split_texts(self, value, cancelled):
        """One description per part from the local prompt model: (parts, None) or (None, reason)."""
        service = getattr(self.server, '_freevideo_prompt_vlm', None)
        if service is None:
            return None, 'unavailable'
        # A rewrite or a single video the user started a moment ago finishes first; busy is said only after
        # ten minutes of waiting. Up to ten minutes waiting plus fifteen splitting hold the runner thread; there
        # is one active chain, so nothing else waits behind it.
        waiting = time.monotonic() + 600
        while True:
            try:
                job = service.start('split', value)['job']
            except ValueError as failure:
                if str(failure) != 'busy' or time.monotonic() > waiting or cancelled():
                    return None, {'model_missing': 'model_missing', 'busy': 'busy'}.get(str(failure), 'failed')
                time.sleep(2)
                continue
            parts, reason = self._split_job(service, job, cancelled)
            if reason != 'busy' or time.monotonic() > waiting or cancelled():
                return parts, reason
            time.sleep(2)

    def _split_job(self, service, job, cancelled):
        deadline = time.monotonic() + 900
        while True:
            try:
                row = service.status(job)
            except ValueError:
                return None, 'failed'
            phase = row.get('phase')
            if phase == 'complete':
                return row.get('parts'), None
            if phase in ('failed', 'cancelled'):
                error = row.get('error')
                return None, ('memory' if error in ('ram_space', 'disk_space') else 'busy' if error == 'busy'
                              else 'unusable' if error == 'split_failed' else 'failed')
            if cancelled() or time.monotonic() > deadline:
                service.cancel(job)
                stopping = time.monotonic() + 30
                try:
                    while (time.monotonic() < stopping
                           and service.status(job).get('phase') not in ('complete', 'failed', 'cancelled')):
                        time.sleep(.2)
                except ValueError:
                    pass
                return None, 'failed'
            time.sleep(.5)

    def lead(self, output, previous):
        """A finished block's seam sound, read while its record is still in the shared store."""
        from .comfy_continuation import audio_lead_in
        return audio_lead_in(output, previous=previous)


def continuation_directions():
    """'after' and 'before', each once FreeVideoGenerate has the input that carries it."""
    schema = _generate_inputs()
    return {key for key, name in CONTINUE_INPUTS.items() if name in schema}


def continuation_supported():
    from .continuation import supported
    return supported()[0]


def continue_seconds():
    """The length a new continuation starts with where this device sets it: short parts on Apple silicon, where
    a full part takes long and often does not fit; None elsewhere, where the page goes by the measured limits."""
    if platform.system() == 'Darwin' and platform.machine().lower() in ('arm64', 'aarch64'):
        return chain_plan.SHORT_CONTINUE_SECONDS
    return None


def continuation_available():
    return 'after' in continuation_directions() and continuation_supported()


def report_fields(chain):
    def source(value):
        if isinstance(value, str):
            return dict(type='block', id=value)
        return dict(type='work') if value is not None else None
    return dict(index=chain['index'], count=chain['count'],
                after=source(chain['after']), before=source(chain['before']))


def block_context(server, node_id):
    try:
        if RUNNER is not None and server is not None:
            prompt_id = server.last_prompt_id
            chain = RUNNER.context(prompt_id, node_id)
            return dict(chain, _prompt_id=prompt_id) if chain is not None else None
    except Exception:
        pass
    return None


def block_finished(chain, node_id, output, report_id, continuation=None):
    try:
        if RUNNER is not None and chain is not None:
            # Capture the executing attempt, so a late callback cannot finish a retry.
            RUNNER.finished(chain['_prompt_id'], node_id, output, report_id, continuation)
    except Exception:
        pass


def hidden_outputs(root):
    try:
        root = Path(root).resolve()
        def exists(output):
            path = (root / output).resolve()
            return path.is_relative_to(root) and path.is_file()
        return RUNNER.hidden(exists) if RUNNER is not None else set()
    except Exception:
        return set()


def register(host_factory=None):
    """Routes and runner. The package never passes `host_factory`; only a test harness supplies another host."""
    global RUNNER
    from aiohttp import web
    from server import PromptServer
    from .comfy_bridge import installation, installation_root
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_chain', False):
        return
    fake = host_factory is not None
    root = installation_root() if fake else installation()[0]
    host = host_factory(server) if fake else ComfyHost(server)
    if fake:
        logging.warning('[FreeVideo] Test background chain host is enabled')
    # Only the test host may lift the limits on parts, never the package.
    runner = ChainRunner(Path(root) / 'chains', host, estimate=None,
                         max_parts=getattr(host, 'max_parts', None) if fake else None,
                         min_part_frames=getattr(host, 'min_part_frames', None) if fake else None)
    if fake:
        host.runner = runner
    RUNNER = runner
    server._freevideo_chain = True

    def response(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    async def body(request):
        try:
            value = await request.json()
        except (ValueError, TypeError):
            raise ValueError('A JSON object is required') from None
        if not isinstance(value, dict):
            raise ValueError('A JSON object is required')
        return value

    @server.routes.post('/freevideo/chain/plan')
    async def plan(request):
        try:
            value = await body(request)
            result = chain_plan.plan(
                value.get('seconds'), value.get('single', False), value.get('max_frames'),
                value.get('single_limit_seconds'), value.get('block_seconds'), max_parts=runner.max_parts,
                first_frame=value.get('first_frame_reference') is True)
            if not result['single'] and not continuation_supported():
                # A device that cannot continue a video has one block to give: never a plan in parts.
                raise chain_plan.PlanError('device_limit', 'This device cannot generate this length at this size')
            return response({'plan': result})
        except chain_plan.PlanError as failure:
            return response({'error': str(failure), 'code': failure.code}, 400)
        except ValueError as failure:
            return response({'error': str(failure), 'code': 'invalid'}, 400)

    @server.routes.post('/freevideo/chain')
    async def create(request):
        try:
            value = await body(request)
            normalized = chain_store.validate(value)
            if not fake and os.environ.get('FREEVIDEO_CHAIN_INDEPENDENT') != '1':
                missing = {key for row in normalized['blocks'] for key in CONTINUE_INPUTS
                           if row[key] is not None} - continuation_directions()
                if 'after' in missing:
                    return response({'error': 'Continuing a video is not available in this version'}, 409)
                if missing:
                    return response({'error': 'Extending backwards or filling a gap is not available in this version'}, 409)
            return response({'chain': await asyncio.to_thread(runner.create, value)})
        except ChainBusy as failure:
            return response({'error': str(failure)}, 409)
        except ChainTooLong as failure:
            return response({'error': str(failure), 'error_zh': failure.message_zh, 'code': failure.code,
                             'parts': failure.parts, 'max_parts': failure.limit}, 400)
        except ChainPartTooShort as failure:
            return response({'error': str(failure), 'error_zh': failure.message_zh, 'code': failure.code,
                             'min_seconds': failure.limit}, 400)
        except chain_plan.PlanError as failure:
            return response({'error': str(failure), 'code': failure.code}, 400)
        except ValueError as failure:
            return response({'error': str(failure)}, 400)

    def asking(request):
        # ?client_id=... marks each chain as this page's own or not.
        query = getattr(request, 'query', None)
        return query.get('client_id') if query is not None else None

    @server.routes.get('/freevideo/chains')
    async def listing(request):
        value = {'chains': runner.list(asking(request)), 'continuation': continuation_available(),
                 'continue_seconds': continue_seconds()}
        if fake:
            value['fake'] = True
        return response(value)

    @server.routes.get('/freevideo/chain/{id}')
    async def get(request):
        try:
            return response({'chain': runner.get(request.match_info['id'], asking(request))})
        except KeyError:
            return response({'error': 'Unknown chain'}, 404)

    @server.routes.post('/freevideo/chain/{id}/cancel')
    async def cancel(request):
        try:
            return response({'chain': runner.cancel(request.match_info['id'])})
        except KeyError:
            return response({'error': 'Unknown chain'}, 404)

    @server.routes.post('/freevideo/chain/{id}/blocks/{block}/text')
    async def text(request):
        try:
            value = await body(request)
            return response({'chain': runner.set_text(request.match_info['id'], request.match_info['block'],
                                                      value.get('text') if isinstance(value, dict) else None)})
        except KeyError:
            return response({'error': 'Unknown chain or part'}, 404)
        except ChainBusy as failure:
            return response({'error': str(failure)}, 409)
        except ValueError as failure:
            return response({'error': str(failure)}, 400)

    @server.routes.post('/freevideo/chain/{id}/blocks/{block}/redo')
    async def redo(request):
        # A new chain from this part on; the original chain and its video are kept.
        try:
            value = await body(request) if request.can_read_body else {}
            identity, block = request.match_info['id'], request.match_info['block']
            if not fake and os.environ.get('FREEVIDEO_CHAIN_INDEPENDENT') != '1':
                missing = {key for row in runner.get(identity)['blocks'] for key in CONTINUE_INPUTS
                           if row[key] is not None} - continuation_directions()
                if missing:
                    return response({'error': 'Continuing a video is not available in this version'}, 409)
            return response({'chain': await asyncio.to_thread(runner.redo, identity, block,
                                                              value.get('text'), value.get('client_id'))})
        except KeyError:
            return response({'error': 'Unknown chain or part'}, 404)
        except ChainBusy as failure:
            return response({'error': str(failure)}, 409)
        except ChainTooLong as failure:
            return response({'error': str(failure), 'error_zh': failure.message_zh, 'code': failure.code,
                             'parts': failure.parts, 'max_parts': failure.limit}, 400)
        except ChainPartTooShort as failure:
            return response({'error': str(failure), 'error_zh': failure.message_zh, 'code': failure.code,
                             'min_seconds': failure.limit}, 400)
        except ValueError as failure:
            return response({'error': str(failure)}, 400)

    @server.routes.post('/freevideo/chain/{id}/resume')
    async def resume(request):
        try:
            value = await body(request) if request.can_read_body else {}
            return response({'chain': runner.resume(request.match_info['id'], value.get('client_id'))})
        except KeyError:
            return response({'error': 'Unknown chain'}, 404)
        except ValueError as failure:
            return response({'error': str(failure)}, 409)
