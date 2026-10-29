"""The running ComfyUI server as seen by agent jobs: queue, history, folders and local services."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from urllib.parse import urlencode
import uuid

from . import spec as specs
from .jobs import JobError


class Request:
    """Just enough of an aiohttp request for ComfyUI's own POST /prompt handler."""
    method, path, content_type, query, match_info = 'POST', '/prompt', 'application/json', {}, {}

    def __init__(self, body):
        from multidict import CIMultiDict, CIMultiDictProxy
        self.body = body
        self.headers = CIMultiDictProxy(CIMultiDict({'Content-Type': 'application/json'}))

    async def json(self):
        return self.body


class ComfyPort:
    def __init__(self, server):
        import folder_paths
        self.server = server
        self.folders = folder_paths
        self.handler = None

    # Folders -------------------------------------------------------------------------------------------
    @property
    def input_root(self):
        return Path(self.folders.get_input_directory()).resolve()

    @property
    def output_root(self):
        return Path(self.folders.get_output_directory()).resolve()

    def _inside(self, root, relative, create):
        parts = PurePosixPath(relative).parts
        if not relative or PurePosixPath(relative).is_absolute() or '..' in parts or '\\' in relative or ':' in relative:
            raise JobError('invalid_media', 'Not a relative path: %s' % relative)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise JobError('invalid_media', 'Path leaves the folder: %s' % relative)
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def input_path(self, relative, create=False):
        return self._inside(self.input_root, relative, create)

    def output_path(self, relative, create=False):
        return self._inside(self.output_root, relative, create)

    def output_relative(self, path):
        return Path(path).resolve().relative_to(self.output_root).as_posix()

    def library_video(self, identity):
        from ..comfy_library import _video, _report
        try:
            path = _video(self.output_root, identity)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or not info.st_size:
                raise ValueError('Video is not a nonempty regular file')
            _report(path)
        except (OSError, ValueError):
            raise JobError('invalid_media', 'No finished saved video %s' % identity) from None
        return path

    def file_view(self, relative, kind='output'):
        from .assets import view
        return view(relative, kind)


    def chain_runner(self):
        from ..comfy_chain import RUNNER
        return RUNNER

    async def capability_view(self, query):
        from ..comfy_capabilities import _query
        from ..capabilities import UNAVAILABLE
        key = _query(query)
        cache = getattr(self.server, '_freevideo_capabilities', None)
        if not hasattr(cache, 'get'):
            return dict(UNAVAILABLE)
        try:
            await cache.check()
            return cache.get(key)
        except Exception:
            cache._log_once()
            return dict(UNAVAILABLE)

    def capability_snapshot(self, query):
        from ..comfy_capabilities import _query
        from ..capabilities import UNAVAILABLE
        cache = getattr(self.server, '_freevideo_capabilities', None)
        return cache.get(_query(query)) if hasattr(cache, 'get') else dict(UNAVAILABLE)

    def output_summary(self, relative):
        from .jobs import library_id
        from ..comfy_library import _report
        from ..comfy_assets import output_summary
        path = self.library_video(library_id(relative))
        if path != self.output_path(relative):
            raise ValueError('Invalid library output')
        report = _report(path)
        summary = output_summary(report, Path(relative))
        if summary.get('geometry'):
            from .media import saved_geometry
            summary['geometry'] = saved_geometry(path, summary['geometry'])
        if report.get('result_cache_hit') is not None:
            summary['result_cache_hit'] = report['result_cache_hit']
        return summary

    # Checks at submission (fast; files are checked again when the job starts) --------------------------------
    def check(self, spec):
        from .schema import diagnostic, editor_issues
        errors = []
        names = set(self.folders.get_filename_list('loras')) if spec.get('loras') else set()
        for i, row in enumerate(spec.get('loras', [])):
            if row['name'] not in names:
                errors.append(diagnostic('/loras/%d/name' % i, 'an installed LoRA listed by GET /freevideo/agent/v1', row['name']))
        def walk(value, pointer=''):
            if isinstance(value, list):
                for i, row in enumerate(value):
                    walk(row, pointer + '/' + str(i))
            elif isinstance(value, dict):
                source = None
                for key in ('file', 'path', 'library'):
                    if key in value:
                        try:
                            source = (self.input_path(value[key]) if key == 'file' else
                                      self.library_video(value[key]) if key == 'library' else Path(value[key]).expanduser())
                            if not source.is_file():
                                raise ValueError
                        except (OSError, ValueError, JobError):
                            errors.append(diagnostic(pointer + '/' + key, 'an existing uploaded input or saved library asset', value[key]))
                if value.get('edit') is not None:
                    size = (spec.get('width', 1344), spec.get('height', 768))
                    if source is not None and source.is_file():
                        from .media import inspect
                        try:
                            info = inspect(source)
                            size = (info['width'], info['height'])
                        except (OSError, ValueError, KeyError):
                            errors.append(diagnostic(pointer, 'a readable image or video', None))
                    errors.extend(editor_issues(value['edit'], pointer + '/edit', source_size=size,
                        target=(spec.get('width', 1344), spec.get('height', 768)), resolve=self.input_path))
                for key, child in value.items():
                    if key not in ('metadata', 'edit'):
                        walk(child, pointer + '/' + key)
        for key in ('media', 'clips', 'questions', 'refine_from'):
            if key in spec:
                walk(spec[key], '/' + key)
        if errors:
            raise specs.SpecError(errors)

    # Queue ---------------------------------------------------------------------------------------------
    def scheduler(self):
        return getattr(self.server, '_freevideo_front', None)

    def snapshot(self):
        if self.scheduler() is not None:
            running, pending = self.scheduler().current_queue()
            return [row[1] for row in running], [row[1] for row in pending]
        running, pending = self.server.prompt_queue.get_current_queue()
        return [row[1] for row in running], [row[1] for row in sorted(pending, key=lambda r: r[0])]

    def editor_exports(self):
        """The editor page's export queue (editor_export.register), shared so exports take turns."""
        return getattr(self.server, '_freevideo_editor_export', None)

    def editor_ears(self):
        """The auto-subtitle ears queue (subtitle_ears.register): time lines against the sound or write down speech."""
        return getattr(self.server, '_freevideo_subtitle_ears', None)

    def queue_busy(self):
        running, pending = self.snapshot()
        return bool(running or pending)

    def prompt_state(self, prompt_id):
        running, pending = self.snapshot()
        return 'running' if prompt_id in running else 'pending' if prompt_id in pending else 'missing'

    def position(self, prompt_id):
        running, pending = self.snapshot()
        return pending.index(prompt_id) + 1 if prompt_id in pending else None

    def history(self, prompt_id):
        if self.scheduler() is not None:
            return self.scheduler().history(prompt_id).get(prompt_id)
        return self.server.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)

    def progress(self, node, prompt_id=None):
        from ..comfy_progress import STATE
        with STATE.lock:
            key = (str(prompt_id), str(node)) if self.scheduler() is not None else str(node)
            row = STATE.rows.get(key)
            if not row:
                return None
            # How old the message is, as a reopened page gets it, so a bar can go on from where the engine is now.
            return dict(json.loads(json.dumps(row['message'])), age_seconds=max(0., STATE.clock() - row['measured']))

    async def submit(self, graph, extra):
        if self.scheduler() is not None:
            reply = await self.scheduler().submit(dict(prompt=graph, extra_data=extra,
                client_id=extra.get('client_id', '')))
            return reply['prompt_id']
        if self.handler is None:
            for route in self.server.routes:
                if getattr(route, 'method', None) == 'POST' and getattr(route, 'path', None) == '/prompt':
                    self.handler = route.handler
            if self.handler is None:
                raise JobError('unsupported_comfyui', 'This ComfyUI has no POST /prompt route')
        prompt_id = str(uuid.uuid4())
        value = dict(prompt=graph, prompt_id=prompt_id, extra_data=extra)
        try:
            response = await self.handler(Request(value))
            status, body = response.status, json.loads(response.text or '{}')
        except (AttributeError, TypeError):
            # A newer handler reads more of the request: post to this server instead.
            status, body = await self.post_prompt(value)
        if status != 200:
            error = body.get('error') or {}
            messages = [row.get('message') or row.get('details') for node in (body.get('node_errors') or {}).values()
                        for row in node.get('errors', [])]
            message = '; '.join(m for m in messages if m) or error.get('message') or 'ComfyUI rejected the request'
            details = [row.get('details') for node in (body.get('node_errors') or {}).values()
                       for row in node.get('errors', []) if row.get('details')]
            not_ready = any('setup' in (m or '').lower() or 'install' in (m or '').lower() for m in messages + details)
            raise JobError('not_ready' if not_ready else 'invalid_request',
                           '; '.join(d for d in details if d) or message)
        return body.get('prompt_id', prompt_id)

    async def post_prompt(self, value):
        import aiohttp
        from comfy.cli_args import args
        host = (args.listen or '127.0.0.1').split(',')[0]
        host = '127.0.0.1' if host in ('0.0.0.0', '') else '::1' if host == '::' else host
        scheme = 'https' if getattr(args, 'tls_keyfile', None) else 'http'
        url = '%s://%s:%d/prompt' % (scheme, '[%s]' % host if ':' in host else host, args.port)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=value, ssl=False) as response:
                return response.status, await response.json(content_type=None)

    def cancel(self, prompt_id, node):
        if self.scheduler() is not None:
            return self.scheduler().cancel(prompt_id, node)
        import nodes
        from ..comfy_progress import cancel_request
        return cancel_request(self.server.prompt_queue, prompt_id, node, nodes.interrupt_processing)

    def generation_error(self, history, node):
        messages = (history.get('status') or {}).get('messages') or []
        text = None
        for kind, data in messages:
            if kind == 'execution_interrupted':
                return dict(code='cancelled', message='Cancelled')
            if kind == 'execution_error':
                text = str(data.get('exception_message') or data.get('exception_type') or '').strip()
        error = dict(code='generation_failed', message=(text or 'The generation did not finish')[:4000])
        run = self.run_output(node)
        if run is not None:
            error['run'] = self.output_relative(run.parent)
            report = _read(run.with_suffix('.request.json'))
            engine = _read(run.with_suffix('.engine.json'))
            if report.get('resource_error'):
                details = report['resource_error'] if isinstance(report['resource_error'], dict) else {}
                error.update(code='insufficient_memory', retryable=False,
                             alternatives=(details.get('details') or details).get('alternatives') or [])
            failure = engine.get('failure') or {}
            if failure.get('kind') in ('gpu_oom', 'ram_pressure'):
                error.update(code='out_of_memory', retryable=True)
            elif failure.get('kind'):
                error.setdefault('failure_kind', failure['kind'])
            debug = run.with_suffix('.debug.json')
            if debug.is_file():
                error['report'] = self.file_view(self.output_relative(debug))
        return error

    def run_output(self, node):
        from ..comfy_progress import REPORTS
        message = self.progress(node) or {}
        with REPORTS.lock:
            path = REPORTS.runs.get(message.get('report_id'))
        return path

    # Local VLM -----------------------------------------------------------------------------------------
    @property
    def vlm(self):
        service = getattr(self.server, '_freevideo_prompt_vlm', None)
        if service is None:
            raise JobError('vlm_unavailable', 'The prompt VLM service is not loaded')
        return service

    def vlm_busy(self):
        service = getattr(self.server, '_freevideo_prompt_vlm', None)
        return service is not None and service.busy()

    def vlm_start(self, action, value):
        try:
            return self.vlm.start(action, value)['job']
        except ValueError as error:
            from .jobs import VLM_ERRORS
            code = str(error) if str(error) in VLM_ERRORS or str(error).isidentifier() else 'invalid_request'
            raise JobError(code, VLM_ERRORS.get(code, str(error)), retryable=code == 'busy') from None

    def vlm_status(self, key):
        return self.vlm.status(key)

    def vlm_cancel(self, key):
        try:
            self.vlm.cancel(key)
        except ValueError:
            pass

    # Local image generation ----------------------------------------------------------------------------
    def _image(self):
        from ..image_gen.service import get_service
        return get_service(self.server)

    @property
    def image(self):
        service = self._image()
        if service is None:
            raise JobError('image_unavailable', 'The image service is not loaded')
        return service

    def image_busy(self):
        service = self._image()
        return service is not None and service.busy()

    def image_ready(self):
        try:
            service = self._image()
            return service is not None and bool(service.info()['ready'])
        except Exception:  # noqa: BLE001 - an optional service must not prevent video generation
            return False

    def image_start(self, action, value):
        try:
            return self.image.start(action, value)['job']
        except ValueError as error:
            from .jobs import IMAGE_ERRORS, IMAGE_WAIT_ERRORS
            code = getattr(error, 'code', str(error))
            code = code if code in IMAGE_ERRORS else 'invalid_request'
            raise JobError(code, IMAGE_ERRORS[code], retryable=code in IMAGE_WAIT_ERRORS,
                           kind='image', report_id=getattr(error, 'report_id', None)) from None

    def image_status(self, key):
        return self.image.status(key)

    def image_cancel(self, key):
        try:
            self.image.cancel(key)
        except ValueError:
            pass

    # Media tools ---------------------------------------------------------------------------------------
    def import_file(self, source, target):
        from ..comfy_assets import media_kind
        media_kind(source.name)
        if source.stat().st_size > 2 * 1024 ** 3:
            raise JobError('invalid_media', 'Media files are limited to 2 GiB')
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)

    def extract_frame(self, video, out, at):
        from . import media
        try:
            return media.extract_frame(video, out, at=at)
        except ValueError as error:
            raise JobError('invalid_media', str(error)) from None

    def sample_frames(self, video, out_dir, count):
        from . import media
        return media.sample_frames(video, out_dir, count=count)

    def edit(self, source, recipe, out, target=None):
        """Render a recipe (or an editor document) of the input file `source`; keep the document beside it."""
        from . import edit
        from PIL import Image, ImageOps
        resolve = lambda name: self.input_path(name)
        try:
            if recipe.get('version') == 2 and 'layers' in recipe:
                doc = recipe
            else:
                with Image.open(resolve(source)) as image:
                    size = ImageOps.exif_transpose(image).size
                doc = edit.compile_recipe(recipe, source=source, source_size=size, target=target, resolve=resolve)
            made = edit.save(doc, resolve, out)
        except edit.EditError as error:
            raise JobError('invalid_edit', str(error), path=getattr(error, 'path', None)) from None
        out.with_suffix('.edit.json').write_text(json.dumps(doc), encoding='utf-8')
        return dict(width=made['width'], height=made['height'], doc=doc)

    def assemble(self, clips, out, **options):
        from . import media
        return media.assemble(clips, out, **options)


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
