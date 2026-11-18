"""The page's own files arrive once, compressed, and are checked again instead of downloaded again.

ComfyUI marks every .js and .css file ``no-store`` and sends them uncompressed, so each visit downloads the whole
interface (about 30 MB) again; on a remote link that is most of the time before anything appears. This middleware,
which runs inside ComfyUI's ``cache_control`` (that one only adds Cache-Control when none is set):

* ``/assets/<name>-<hash>.<ext>``: the frontend build names these after their content, so a new version always has new
  addresses; the browser keeps them (``public, max-age=31536000, immutable``).
* every other page file (FreeVideo's ``/extensions/...``, ``/scripts/...``, unhashed files): ``no-cache``, so the
  browser asks again on every visit and an unchanged file answers 304 with no body. The ETag is taken from the file's
  content, so a file changed by an update is always sent again, even when the update kept its time and size.
* text files (JS, CSS, JSON, SVG, HTML, maps) go out gzip-compressed to browsers that accept it; the compressed copy is
  kept in memory until the file changes.
* ``/api/object_info``: ETag from the body and ``no-cache``, so an unchanged node list answers 304; large JSON answers
  are compressed.

The page itself (``/``, ``/index.html``) keeps ComfyUI's ``no-store``: it is the one file that names the hashed
assets, so after an update the first visit reads the new names. Answers that already carry Cache-Control, Content-
Encoding or a Range request are left alone.
"""
import asyncio
import collections
import gzip
import hashlib
import logging
import os
import re
import threading

IMMUTABLE = 'public, max-age=31536000, immutable'
REVALIDATE = 'no-cache'
HASHED = re.compile(r'^/assets/[^/]+-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+$')
TEXT = frozenset(('.js', '.mjs', '.css', '.json', '.svg', '.html', '.htm', '.txt', '.map', '.xml', '.wasm'))
PAGE = frozenset(('/', '/index.html'))
OBJECT_INFO = frozenset(('/object_info', '/api/object_info'))
SMALLEST = 1024            # below this gzip saves less than one round trip costs
JSON_SMALLEST = 8192
LEVEL = 6
CACHE_BYTES = 64 << 20     # compressed copies kept; the whole interface is about 8 MB compressed


# Fixed, so a Windows registry that maps .js to text/plain cannot break module loading.
TYPES = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
         '.map': 'application/json', '.svg': 'image/svg+xml', '.html': 'text/html', '.htm': 'text/html',
         '.txt': 'text/plain', '.xml': 'application/xml', '.wasm': 'application/wasm'}


def content_type(path):
    suffix = os.path.splitext(str(path))[1].lower()
    if suffix in TYPES:
        return TYPES[suffix]
    import mimetypes
    return mimetypes.guess_type(str(path))[0] or 'application/octet-stream'


def accepts_gzip(request):
    for part in request.headers.get('Accept-Encoding', '').lower().split(','):
        name, _, quality = part.strip().partition(';')
        if name.strip() in ('gzip', '*'):
            quality = quality.strip()
            if quality.startswith('q='):
                try:
                    return float(quality[2:]) > 0
                except ValueError:
                    return False
            return True
    return False


def matches(request, etag):
    """Whether If-None-Match names this ETag (weak comparison, as for a GET)."""
    try:
        tags = request.if_none_match
    except Exception:
        return False
    if not tags:
        return False
    value = etag.strip('"')
    return any(tag.value in ('*', value) for tag in tags)


class Copies:
    """Compressed (or, when gzip does not help, plain) copies of page files, by path, until the file changes."""

    def __init__(self, limit=CACHE_BYTES):
        self.limit = limit
        self.size = 0
        self.rows = collections.OrderedDict()
        self.lock = threading.Lock()

    def get(self, path):
        """(etag, last_modified, body, gzipped) for the file as it is now; None when it cannot be read."""
        try:
            info = os.stat(path)
        except OSError:
            return None
        key = (info.st_mtime_ns, info.st_size)
        with self.lock:
            row = self.rows.get(path)
            if row is not None and row[0] == key:
                self.rows.move_to_end(path)
                return row[1]
        try:
            with open(path, 'rb') as source:
                data = source.read()
        except OSError:
            return None
        digest = hashlib.sha1(data).hexdigest()[:20]
        packed = gzip.compress(data, compresslevel=LEVEL, mtime=0) if len(data) >= SMALLEST else data
        if len(packed) < len(data) * 0.9:
            value = ('"%s-gz"' % digest, info.st_mtime, packed, True)
        else:
            value = ('"%s"' % digest, info.st_mtime, data, False)
        with self.lock:
            old = self.rows.pop(path, None)
            if old is not None:
                self.size -= len(old[1][2])
            self.rows[path] = (key, value)
            self.size += len(value[2])
            while self.size > self.limit and len(self.rows) > 1:
                _, dropped = self.rows.popitem(last=False)
                self.size -= len(dropped[1][2])
        return value


class Bodies:
    """The last few compressed JSON bodies by content, so an unchanged node list is not compressed again."""

    def __init__(self, keep=4):
        self.keep = keep
        self.rows = collections.OrderedDict()
        self.lock = threading.Lock()

    def get(self, digest, body):
        with self.lock:
            if digest in self.rows:
                self.rows.move_to_end(digest)
                return self.rows[digest]
        packed = gzip.compress(body, compresslevel=LEVEL, mtime=0)
        with self.lock:
            self.rows[digest] = packed
            while len(self.rows) > self.keep:
                self.rows.popitem(last=False)
        return packed


def middleware_for(copies=None, bodies=None):
    from aiohttp import web
    copies = copies or Copies()
    bodies = bodies or Bodies()
    failed = []
    comfy_compresses = []

    def warn(error):
        if not failed:
            failed.append(error)
            logging.warning('[FreeVideo] Page files sent without compression: %s', error)

    def headers_from(response, *names):
        return {name: response.headers[name] for name in names if name in response.headers}

    async def static(request, response, path):
        # A file from web.static (the frontend package or an extension folder).
        if 'Cache-Control' in response.headers or 'Content-Encoding' in response.headers:
            return response
        policy = IMMUTABLE if HASHED.match(request.path) else REVALIDATE
        if 'Range' in request.headers or os.path.splitext(str(path))[1].lower() not in TEXT or not accepts_gzip(request):
            response.headers['Cache-Control'] = policy
            return response
        try:
            copy = await asyncio.to_thread(copies.get, str(path))
        except Exception as error:
            warn(error)
            copy = None
        if copy is None:
            response.headers['Cache-Control'] = policy
            return response
        etag, modified, body, gzipped = copy
        headers = {'Cache-Control': policy, 'ETag': etag, 'Vary': 'Accept-Encoding'}
        if matches(request, etag):
            return web.Response(status=304, headers=headers)
        if gzipped:
            headers['Content-Encoding'] = 'gzip'
        out = web.Response(body=body, headers=headers)
        out.content_type = response.headers.get('Content-Type') or content_type(path)
        out.last_modified = modified
        return out

    async def json_answer(request, response):
        body = response.body
        if not isinstance(body, (bytes, bytearray)) or 'Content-Encoding' in response.headers:
            return response
        etag = None
        if request.path in OBJECT_INFO and response.status == 200:
            digest = hashlib.sha1(body).hexdigest()[:20]
            etag = '"%s"' % digest
            if matches(request, etag):
                headers = {'ETag': etag, 'Vary': 'Accept-Encoding', 'Cache-Control': response.headers.get('Cache-Control', REVALIDATE)}
                return web.Response(status=304, headers=headers)
            response.headers['ETag'] = etag
            response.headers.setdefault('Cache-Control', REVALIDATE)
        if len(body) < JSON_SMALLEST or not accepts_gzip(request) or comfy_compresses[0]:
            return response
        try:
            if etag is not None:
                packed = await asyncio.to_thread(bodies.get, etag, bytes(body))
            elif len(body) > (256 << 10):
                packed = await asyncio.to_thread(gzip.compress, bytes(body), LEVEL, mtime=0)
            else:
                packed = gzip.compress(bytes(body), compresslevel=LEVEL, mtime=0)
        except Exception as error:
            warn(error)
            return response
        response.body = packed
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Vary'] = 'Accept-Encoding'
        return response

    @web.middleware
    async def page_files(request, handler):
        response = await handler(request)
        if request.method not in ('GET', 'HEAD') or request.path in PAGE or response.status != 200:
            return response
        if not comfy_compresses:
            # ComfyUI's --enable-compress-response-body compresses JSON itself; never compress twice.
            comfy_compresses.append(any(getattr(m, '__name__', '') == 'compress_body' for m in request.app.middlewares))
        if isinstance(response, web.FileResponse):
            # Only the page's folders (web.static); a picture or video from /view keeps its own rules.
            resource = getattr(getattr(request.match_info, 'route', None), 'resource', None)
            path = getattr(response, '_path', None) if isinstance(resource, web.StaticResource) else None
            return response if path is None else await static(request, response, path)
        if type(response) is web.Response and response.content_type == 'application/json':
            return await json_answer(request, response)
        return response

    return page_files


def register(server=None):
    """Install on ComfyUI's page server; called while the custom node loads, before the server starts."""
    if os.environ.get('FREEVIDEO_PAGE_CACHE', '').strip().lower() in ('0', 'off', 'false', 'no'):
        return False
    if server is None:
        from server import PromptServer
        server = PromptServer.instance
    app = getattr(server, 'app', None)
    if server is None or app is None or getattr(server, '_freevideo_page_cache', False):
        return False
    if app.frozen:
        logging.warning('[FreeVideo] Page file caching not added: the page server is not open for additions')
        return False
    app.middlewares.append(middleware_for())
    server._freevideo_page_cache = True
    return True
