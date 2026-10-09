"""Token-protected access to a ComfyUI that listens on this computer only.

`freevideo server --listen ADDRESS` runs this in front of ComfyUI. The access
link carries the token once; the page then keeps it in a cookie, and API
clients send it as a bearer token. Everything else gets 401, so ComfyUI is
never reachable from other computers without it, and not at all when this
proxy is not running. Runs with ComfyUI's Python, which brings aiohttp.
"""
import argparse
import asyncio
import hmac
from pathlib import Path
import ssl
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, WSMsgType, client_exceptions, web
from yarl import URL

COOKIE = 'freevideo_access'
DAYS = 30
# Hop-by-hop headers belong to one connection; Content-Length follows the body actually sent.
HOP_BY_HOP = frozenset(('connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
                        'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length'))
DENIED = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FreeVideo</title></head><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#10151e;
color:#e7edf7;font:15px/1.6 'Segoe UI','Noto Sans CJK SC','Noto Sans SC',system-ui,sans-serif"><main style="max-width:520px;
padding:28px 32px;border:1px solid #2a3443;border-radius:12px;background:#1a222e"><h1 style="margin:0 0 10px;font-size:19px">
This FreeVideo server opens from its access link</h1><p style="margin:0 0 18px;color:#a5b3c6">To show the link, run
<code>freevideo status</code> on the server.</p><h1 style="margin:0 0 10px;font-size:19px">请使用访问链接打开这台 FreeVideo 服务器</h1>
<p style="margin:0;color:#a5b3c6">在服务器上运行 <code>freevideo status</code> 即可查看链接。</p></main></body></html>"""


def matches(value, token):
    return bool(value) and hmac.compare_digest(value.encode(), token.encode())


def supplied(request):
    header = request.headers.get('Authorization', '')
    return request.cookies.get(COOKIE) or (header[7:].strip() if header[:7].lower() == 'bearer ' else '')


def denied(request):
    if 'text/html' in request.headers.get('Accept', ''):
        return web.Response(status=401, text=DENIED, content_type='text/html')
    return web.json_response(dict(error='This FreeVideo server needs its access token.'), status=401)


def forwarded(request):
    """The browser's headers for ComfyUI, minus this connection's and our cookie.

    Host and Origin pass through unchanged: ComfyUI compares them only for a
    loopback Host, as a guard against other websites, and they match here.
    """
    headers = []
    for name, value in request.headers.items():
        lower = name.lower()
        if lower in HOP_BY_HOP or lower == 'authorization':
            continue
        if lower == 'cookie':
            value = '; '.join(part for part in value.split(';') if part.strip() and
                              part.split('=', 1)[0].strip() != COOKIE)
            if not value:
                continue
        headers.append((name, value))
    return headers


def create(upstream, token):
    upstream = upstream.rstrip('/')
    sockets = 'ws' + upstream[len('http'):]
    client = []

    async def startup(app):
        # No total timeout: downloads and the progress socket last as long as they need.
        client.append(ClientSession(auto_decompress=False, cookie_jar=DummyCookieJar(),
                                    timeout=ClientTimeout(total=None, sock_connect=10), connector=TCPConnector(limit=0)))

    async def cleanup(app):
        await client[0].close()

    async def forward(request):
        body = request.content if request.body_exists else None
        try:
            async with client[0].request(request.method, URL(upstream + request.raw_path, encoded=True),
                                                     headers=forwarded(request), data=body, allow_redirects=False) as reply:
                response = web.StreamResponse(status=reply.status, reason=reply.reason)
                for name, value in reply.headers.items():
                    if name.lower() not in HOP_BY_HOP:
                        response.headers.add(name, value)
                if reply.content_length is not None and request.method != 'HEAD':
                    response.content_length = reply.content_length
                await response.prepare(request)
                async for chunk in reply.content.iter_chunked(1 << 16):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except client_exceptions.ClientConnectorError:
            return web.json_response(dict(error='ComfyUI is not answering yet.'), status=502)

    async def websocket(request):
        browser = web.WebSocketResponse(max_msg_size=0)
        await browser.prepare(request)
        headers = [(k, v) for k, v in forwarded(request) if k.lower() not in (
            'sec-websocket-key', 'sec-websocket-version', 'sec-websocket-extensions', 'sec-websocket-protocol')]
        try:
            async with client[0].ws_connect(URL(sockets + request.raw_path, encoded=True), headers=headers,
                                                        max_msg_size=0, autoping=True) as comfy:
                async def pump(source, target):
                    async for message in source:
                        if message.type == WSMsgType.TEXT:
                            await target.send_str(message.data)
                        elif message.type == WSMsgType.BINARY:
                            await target.send_bytes(message.data)
                        else:
                            break
                tasks = [asyncio.ensure_future(pump(browser, comfy)), asyncio.ensure_future(pump(comfy, browser))]
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
        except (client_exceptions.ClientError, ConnectionError):
            pass
        finally:
            await browser.close()
        return browser

    async def handle(request):
        offered = request.rel_url.query.get('token')
        if offered is not None:
            if not matches(offered, token):
                return denied(request)
            # The access link: keep the token in a cookie and show the address without it.
            rest = [(k, v) for k, v in request.rel_url.query.items() if k != 'token']
            response = web.Response(status=303, headers={'Location': request.rel_url.path + ('?' + urlencode(rest) if rest else '')})
            response.set_cookie(COOKIE, token, max_age=DAYS * 86400, httponly=True, samesite='Lax',
                                secure=request.secure, path='/')
            return response
        if not matches(supplied(request), token):
            return denied(request)
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            return await websocket(request)
        return await forward(request)

    app = web.Application(client_max_size=0)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.router.add_route('*', '/{path:.*}', handle)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--upstream', required=True, help='The local ComfyUI, http://127.0.0.1:PORT')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--tls-cert', type=Path)
    parser.add_argument('--tls-key', type=Path)
    args = parser.parse_args(argv)
    token = args.token_file.read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise SystemExit('The access token is missing or too short: ' + str(args.token_file))
    if not args.upstream.startswith('http://127.0.0.1:'):
        raise SystemExit('The upstream must be the ComfyUI on this computer')
    context = None
    if args.tls_cert:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(args.tls_cert, args.tls_key)
    web.run_app(create(args.upstream, token), host=args.listen, port=args.port, ssl_context=context,
                access_log=None, print=None)


if __name__ == '__main__':
    main()
