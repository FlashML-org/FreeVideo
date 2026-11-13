"""A small standard-library client for the FreeVideo agent API (used by the MCP server and the CLI).

    from freevideo_engine.agent.client import Client
    fv = Client()                                   # finds the server and token of this installation
    shot = fv.submit({'prompt': '...', 'aspect': '9:16', 'seconds': 6})
    done = fv.wait(shot['id'])                      # {'status': 'succeeded', 'result': {'video': {...}}}
"""
import base64
import json
import ipaddress
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DONE = ('succeeded', 'failed', 'cancelled', 'interrupted')


class ApiError(Exception):
    def __init__(self, status, code, message, details=None, *, inbox=None, **context):
        from . import errors
        error = errors.build(code, **context)
        # The user's words delivered with a failed call are already read on the server.
        self.inbox = inbox if isinstance(inbox, list) else []
        # Server errors already passed the public boundary. Keep their exact text.
        if details and details.get('next'):
            error.update(message=message, **details)
        self.status, self.code, self.message = status, error['code'], error['message']
        self.details = {k: v for k, v in error.items() if k not in ('code', 'message')}
        super().__init__(errors.text(error))


def local_file(path):
    try:
        source = Path(path).expanduser()
        if source.is_file():
            return source
    except (OSError, ValueError, TypeError):
        pass
    # Agents often pass a path FreeVideo returned (assets, inputs, outputs) as a local file.
    raise ApiError(422, 'invalid_media', 'There is no readable file at this path on this computer.', dict(
        next='Use "path" only for a file on this computer. Send a file FreeVideo returned as {"file": "<that path>"}.',
        details=[dict(field='path')]))


def default_root():
    if os.environ.get('FREEVIDEO_HOME'):
        return Path(os.environ['FREEVIDEO_HOME']).expanduser()
    here = Path(__file__).resolve().parents[2]
    if (here / 'machine.json').is_file() or (here / 'agent').is_dir():
        return here
    try:
        from ..bootstrap import DEFAULT_ROOT
        return Path(DEFAULT_ROOT)
    except Exception:  # noqa: BLE001 - the bootstrap module needs the full engine environment
        return here


def endpoint_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0
                or any(c.isspace() for c in value) or '\\' in value):
            raise ValueError
        parsed.port
    except (TypeError, ValueError, AttributeError):
        raise ApiError(422, 'invalid_request', 'Invalid endpoint') from None
    return value.rstrip('/')


def origin(url):
    value = urllib.parse.urlsplit(endpoint_url(url.split('?', 1)[0].split('#', 1)[0]))
    return value.scheme, value.hostname.lower(), value.port or (443 if value.scheme == 'https' else 80)


def one_job(answer):
    """The job of a single-job answer, without the copy the server adds under 'job'."""
    return {key: value for key, value in answer.items() if key != 'job'}


class LocalProxyHandler(urllib.request.ProxyHandler):
    def proxy_open(self, req, proxy, type):
        host = urllib.parse.urlsplit(req.full_url).hostname
        try:
            address = ipaddress.ip_address(host)
            local = address.is_loopback or bool(getattr(address, 'ipv4_mapped', None) and address.ipv4_mapped.is_loopback)
        except ValueError:
            local = host.lower() == 'localhost'
        if local:
            return None
        return super().proxy_open(req, proxy, type)


class CredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        credentialed = any(req.has_header(k) for k in ('Authorization', 'X-freevideo-agent', 'Cookie'))
        if credentialed and origin(req.full_url) != origin(newurl):
            raise ApiError(403, 'redirect_forbidden', 'Cross-origin redirect refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def controlled_opener():
    return urllib.request.build_opener(LocalProxyHandler(), CredentialRedirect())


class Client:
    def __init__(self, url=None, token=None, *, root=None, name='freevideo-python', timeout=60):
        root = Path(root).expanduser() if root else default_root()
        self.root = root
        endpoint = {}
        try:
            endpoint = json.loads((root / 'agent' / 'endpoint.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        self.url = endpoint_url(url or os.environ.get('FREEVIDEO_URL') or (endpoint.get('url') if isinstance(endpoint, dict) else None) or 'http://127.0.0.1:8188')
        self.opener = controlled_opener()
        if token is None:
            try:
                token = (root / 'agent' / 'token').read_text(encoding='ascii').strip()
            except OSError:
                token = None
        self.token, self.name, self.timeout = token, name, timeout
        # Per-thread extra headers: the MCP server runs each tool call on its own thread.
        self.call_headers = threading.local()

    # Transport ------------------------------------------------------------------------------------------
    def relative_url(self, path):
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '\\' in path:
            raise ApiError(422, 'invalid_request', 'Expected a relative API endpoint')
        return self.url + path

    def request(self, method, path, body=None, *, query=None, timeout=None, raw=False, encoded=None, content_type=None, content_length=None):
        if self.token is None:
            raise ApiError(0, 'no_token', 'The local token is unavailable.')
        url = self.relative_url(path) + ('?' + urllib.parse.urlencode(query) if query else '')
        data = encoded if encoded is not None else None if body is None else json.dumps(body).encode()
        headers = {'Authorization': 'Bearer ' + self.token, 'X-FreeVideo-Client': self.name}
        headers.update(getattr(self.call_headers, 'value', None) or {})
        if data is not None:
            headers['Content-Type'] = content_type or 'application/json'
        if content_length is not None:
            headers['Content-Length'] = str(content_length)
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                body = json.loads(payload)
                problem = body.get('error') or {}
            except (ValueError, AttributeError):
                body, problem = {}, {}
            raise ApiError(error.code, problem.get('code', 'http_%d' % error.code),
                           problem.get('message') or payload.decode('utf-8', 'replace')[:500],
                           {k: v for k, v in problem.items() if k not in ('code', 'message')},
                           inbox=body.get('inbox')) from None
        except urllib.error.URLError:
            submission = method == 'POST' and (path.endswith('/jobs') or path.endswith('/resume'))
            raise ApiError(0, 'unreachable', 'The FreeVideo server could not be reached.',
                           stage='submission_unknown' if submission else 'request') from None
        return payload if raw else json.loads(payload or b'{}')

    def get(self, path, **query):
        return self.request('GET', '/freevideo/agent/v1' + path, query={k: v for k, v in query.items() if v is not None})

    def local_media(self, value):
        if isinstance(value, list):
            return [self.local_media(row) for row in value]
        if isinstance(value, dict):
            if 'path' in value and not any(k in value for k in ('file', 'from_job', 'library')):
                return {k: v for k, v in value.items() if k != 'path'} | {'file': self.upload(value['path'])['file']}
            return {k: v if k in ('metadata', 'edit', 'document', 'recipe') else self.local_media(v) for k, v in value.items()}
        return value

    def check_local_files(self, value):
        """Check the whole client-side input set before publishing the first asset."""
        from .schema import MAX_UPLOAD_BYTES
        if isinstance(value, list):
            for child in value:
                self.check_local_files(child)
        elif isinstance(value, dict):
            if 'path' in value:
                source = local_file(value['path'])
                try:
                    if not 0 < source.stat().st_size <= MAX_UPLOAD_BYTES:
                        raise ValueError
                except (OSError, ValueError, TypeError):
                    raise ApiError(422, 'invalid_media', 'Invalid local input') from None
            for key, child in value.items():
                if key not in ('metadata', 'edit', 'document', 'recipe'):
                    self.check_local_files(child)

    def post(self, path, body, **kw):
        return self.send('POST', path, body, **kw)

    def patch(self, path, body, **kw):
        return self.send('PATCH', path, body, **kw)

    def delete(self, path, body=None, **kw):
        return self.send('DELETE', path, body if body is not None else {}, **kw)

    def send(self, method, path, body, **kw):
        from . import schema
        shape = schema.request_contract(method, path, local=True)
        if shape is not None:
            schema.validate(shape, body)
            if path in ('/jobs', '/plan'):
                schema.preflight_submission(body)
        if path != '/media':
            self.check_local_files(body)
            body = self.local_media(body)
        shape = schema.request_contract(method, path)
        if shape is not None:
            schema.validate(shape, body)
        return self.request(method, '/freevideo/agent/v1' + path, body, **kw)

    def hello(self, via='http'):
        return self.post('/connect/hello', dict(client=self.name, via=via))

    # API ------------------------------------------------------------------------------------------------
    def capabilities(self):
        return self.get('')

    def capability_table(self, **query):
        return self.get('/capabilities', **query)

    def guide(self):
        return self.get('/guide')

    def schema(self):
        return self.get('/schema')

    def plan(self, spec):
        return self.post('/plan', spec)

    def submit(self, spec, takes=1):
        body = dict(spec, takes=takes) if takes != 1 else spec
        return self.post('/jobs', body)['job']

    def submit_takes(self, spec, takes):
        return self.post('/jobs', dict(spec, takes=takes))['jobs']

    def batch(self, jobs, *, ref=None):
        """Jobs may name themselves with "ref" and refer to each other as "@ref"."""
        return self.post('/jobs', dict(jobs=jobs, **({'ref': ref} if ref else {})))['jobs']

    def job(self, identity, wait=0):
        wait = max(0, min(25, wait))
        return one_job(self.request('GET', '/freevideo/agent/v1/jobs/' + identity, query={'wait': wait} if wait else None,
                                    timeout=max(self.timeout, wait + 5)))

    def jobs(self, status=None, project=None, kind=None, limit=None):
        return self.get('/jobs', status=status, project=project, kind=kind, limit=limit)['jobs']

    def wait(self, identity, timeout=None, on_progress=None):
        """Until the job finishes (or timeout seconds pass); returns the last job view."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            left = 25 if deadline is None else max(0, min(25, deadline - time.monotonic()))
            view = self.job(identity, wait=left)
            if on_progress:
                on_progress(view)
            if view['status'] in DONE or (deadline is not None and time.monotonic() >= deadline):
                return view

    def cancel(self, identity):
        return one_job(self.post('/jobs/%s/cancel' % identity, {}))

    def resume(self, identity, *, ref=None):
        return one_job(self.post('/jobs/%s/resume' % identity, {'ref': ref} if ref else {}))

    def asset(self, identity, target, *, thumbnail=False):
        """Retrieve a library file through authenticated HTTP only."""
        path = '/freevideo/agent/v1/library/%s/%s' % (identity, 'thumbnail' if thumbnail else 'file')
        return self.download({'url': path}, target)

    def upload(self, path, *, stage=None):
        """Read on the client computer and upload content; never send a local path.
        ``stage('sending')`` and ``stage('checking')`` say when the content goes out
        and when the server is decoding it, which can take long for a large video."""
        return self._upload(path, '/media', stage=stage)

    def import_media(self, path, *, ref=None):
        """Import a client file into the editor. Poll /imports until its state is ready."""
        from . import schema
        if ref is not None:
            schema.validate(schema.obj({'ref': schema.REF}), dict(ref=ref))
        return self._upload(path, '/imports', ref=ref)

    def _upload(self, path, endpoint, *, ref=None, stage=None):
        from .schema import MAX_UPLOAD_BYTES, MAX_JSON_UPLOAD_BYTES
        source = local_file(path)
        stage = stage or (lambda name: None)
        try:
            size = source.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                raise ApiError(413, 'too_large', 'File exceeds upload limit',
                    dict(next='Reduce the file to at most %d bytes.' % MAX_UPLOAD_BYTES, details=[dict(limit_bytes=MAX_UPLOAD_BYTES)]))
            if size * 4 // 3 + 4096 <= MAX_JSON_UPLOAD_BYTES:
                if endpoint == '/media':
                    data = source.read_bytes()
                    # A small body goes out at once; the wait is the server's check.
                    stage('checking')
                    return self.upload_bytes(source.name, data)
                return self.post(endpoint, dict(name=source.name, data_base64=base64.b64encode(source.read_bytes()).decode(),
                    **({'ref': ref} if ref is not None else {})), timeout=600)
            import secrets
            boundary = 'freevideo-' + secrets.token_hex(16)
            # Only a safe extension crosses a MIME header; local filenames stay local.
            suffix = source.suffix.lower()
            if not suffix[1:].isalnum():
                raise ApiError(422, 'invalid_media', 'Invalid media extension')
            prefix = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="media%s"\r\n'
                      'Content-Type: application/octet-stream\r\n\r\n' % (boundary, suffix)).encode('ascii')
            ending = (('\r\n--%s\r\nContent-Disposition: form-data; name="ref"\r\n\r\n%s' % (boundary, ref)
                       if ref is not None else '') + '\r\n--%s--\r\n' % boundary).encode('ascii')
            with source.open('rb') as stream:
                def chunks():
                    stage('sending')
                    yield prefix
                    while data := stream.read(1024 * 1024):
                        yield data
                    yield ending
                    stage('checking')
                return self.request('POST', '/freevideo/agent/v1' + endpoint, encoded=chunks(),
                    content_type='multipart/form-data; boundary=' + boundary,
                    content_length=len(prefix) + size + len(ending), timeout=600)
        except OSError:
            raise ApiError(0, 'invalid_media', 'Cannot read local file') from None

    def upload_bytes(self, name, data):
        return self.post('/media', {'name': name, 'data_base64': base64.b64encode(data).decode()}, timeout=600)

    def inspect(self, source):
        return self.post('/media/inspect', {'source': source})

    def frame(self, source, at=None):
        return self.post('/media/frame', dict(source=source, **({'at': at} if at is not None else {})))

    def edit(self, source, recipe=None, *, document=None, target=None):
        body = {'source': source}
        body.update({'recipe': recipe} if document is None else {'document': document})
        if target:
            body['target'] = list(target)
        return self.post('/media/edit', body)

    def contact_sheet(self, source, count=None, columns=None):
        return self.post('/media/contact-sheet', dict(source=source, **{k: v for k, v in dict(count=count, columns=columns).items() if v is not None}))

    def library(self, limit=None, before=None, query=None, project=None):
        return self.get('/library', limit=limit, before=before, query=query, project=project)

    def queue(self):
        return self.get('/queue')

    def download(self, view, target):
        """Save a result file (a job result's video/view dict) somewhere else."""
        target = Path(target)
        if self.token is None:
            raise ApiError(0, 'no_token', 'The local token is unavailable.')
        request = urllib.request.Request(self.relative_url(view['url']), headers={
            'Authorization': 'Bearer ' + (self.token or ''), 'X-FreeVideo-Client': self.name})
        try:
            with self.opener.open(request, timeout=600) as response, target.open('wb') as stream:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    stream.write(chunk)
        except urllib.error.HTTPError as error:
            try:
                problem = json.loads(error.read()).get('error') or {}
            except ValueError:
                problem = {}
            raise ApiError(error.code, problem.get('code', 'asset_unavailable'),
                problem.get('message', 'The library asset could not be retrieved.'),
                dict(details=[], next='GET /freevideo/agent/v1/library and use an existing asset ID.',
                     retryable=False) | {k: v for k, v in problem.items() if k not in ('code', 'message')}) from None
        except urllib.error.URLError:
            raise ApiError(0, 'unreachable', 'The FreeVideo server could not be reached.',
                           dict(details=[], next='Start FreeVideo, then retry the asset download.', retryable=True)) from None
        return target
