"""The workflow a page hands over just before the reload an update makes, kept in this server's memory only.

FreeVideo keeps ComfyUI's workflow drafts off, so a reload opens the default workflow. The reload an update makes is
not the user's choice, so the page hands its workflow (a prompt not yet sent included) to this server, the updated one
already running, and takes it back once after the reload. It is held in this process's memory only: never written to
a file, a log or a report, and gone when the process ends. Only the page that holds the token can take it, once; what
nobody takes is dropped when its time runs out.
"""
import json
import secrets
import threading
import time

LIFETIME = 120.0
LIMIT = 32 * 2**20
COUNT = 8


class Kept:
    def __init__(self, lifetime=LIFETIME, clock=time.monotonic):
        self.lifetime, self.clock = lifetime, clock
        self.items, self.lock = {}, threading.Lock()

    def _expire(self, now):
        for token in [token for token, (_, until) in self.items.items() if until <= now]:
            del self.items[token]

    def put(self, value):
        """Keeps the bytes and returns the token that takes them back."""
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self._expire(now)
            # A few tabs at once at most; the oldest goes first rather than letting copies pile up.
            while len(self.items) >= COUNT:
                del self.items[min(self.items, key=lambda key: self.items[key][1])]
            self.items[token] = (bytes(value), now + self.lifetime)
        return token

    def take(self, token):
        """The bytes for this token, once; None when unknown, already taken or expired."""
        if not isinstance(token, str):
            return None
        with self.lock:
            self._expire(self.clock())
            found = self.items.pop(token, None)
        return found[0] if found else None

    def drop(self, token):
        if isinstance(token, str):
            with self.lock:
                self.items.pop(token, None)

    def expire(self):
        with self.lock:
            self._expire(self.clock())

    def __len__(self):
        with self.lock:
            return len(self.items)


def accept(kept, body):
    """(status, answer) for a page handing its workflow over. The content is never echoed or logged."""
    if len(body) > LIMIT:
        return 413, {'kept': False}
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return 400, {'kept': False}
    if not isinstance(value, dict) or not isinstance(value.get('workflow'), dict):
        return 400, {'kept': False}
    return 200, {'kept': True, 'token': kept.put(body), 'seconds': int(kept.lifetime)}


def token_of(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    token = value.get('token') if isinstance(value, dict) else None
    return token if isinstance(token, str) and 0 < len(token) <= 200 else None


def register():
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_reload_keep', None) is not None:
        return
    kept = server._freevideo_reload_keep = Kept()
    headers = {'Cache-Control': 'no-store'}

    def later():
        # What nobody takes goes when its time runs out, even with no further request.
        import asyncio
        try:
            asyncio.get_running_loop().call_later(kept.lifetime + 1, kept.expire)
        except RuntimeError:
            pass

    @server.routes.post('/freevideo/reload-keep')
    async def keep(request):
        if (request.content_length or 0) > LIMIT:
            return web.json_response({'kept': False}, status=413, headers=headers)
        try:
            body = await request.content.read(LIMIT + 1)
        except Exception:  # The page asks the user instead; nothing of the body is reported.
            return web.json_response({'kept': False}, status=400, headers=headers)
        status, answer = accept(kept, body)
        if status == 200:
            later()
        return web.json_response(answer, status=status, headers=headers)

    @server.routes.post('/freevideo/reload-keep/take')
    async def take(request):
        try:
            token = token_of(await request.content.read(1024))
        except Exception:
            token = None
        value = kept.take(token)
        if value is None:
            return web.json_response({'found': False}, status=404, headers=headers)
        return web.Response(body=value, content_type='application/json', headers=headers)

    @server.routes.post('/freevideo/reload-keep/drop')
    async def drop(request):
        try:
            kept.drop(token_of(await request.content.read(1024)))
        except Exception:
            pass
        return web.json_response({'dropped': True}, headers=headers)
