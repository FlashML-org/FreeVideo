"""Authenticated access to a ComfyUI that listens on this computer only.

The launcher's LAN switch or `freevideo server --listen ADDRESS` runs this in front of ComfyUI. The access
link carries the token once; the page then keeps it in a cookie, and API
clients send it as a bearer token. Everything else gets 401, so ComfyUI is
never reachable from other computers without it, and not at all when this
proxy is not running. Runs with ComfyUI's Python, which brings aiohttp.
"""
import argparse
import asyncio
import hmac
import hashlib
import html
import math
import time
import ipaddress
import os
import re
from pathlib import Path
import ssl
from urllib.parse import urlencode, urlsplit

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, WSMsgType, client_exceptions, web
from yarl import URL
from . import proxy_identity, lan_password
from .origin_scheme import effective_scheme
from .lan_auth import PasswordAuth, DEVICE_COOKIE, REMEMBER_SECONDS, LOGIN_DELAY

PHONE_WEB = Path(__file__).resolve().parents[1] / 'web'
PHONE_FILE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:js|css)')
PHONE_ASSETS = ('assets/freevideo.svg', 'assets/freevideo-light.svg')
PHONE_TYPES = {'.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}
# Inline like the password page: installed packages only carry .js/.css/.svg under web/.
PHONE_PAGE = (
    '<!doctype html>'
    '<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="referrer" content="same-origin"><title>FreeVideo</title><link rel="stylesheet" href="./theme.css"><link rel="stylesheet" href="./phone.css"></head><body class="fv-phone"><div id="phone"></div><script type="module" src="./phone.js"></script></body></html>')

COOKIE = 'freevideo_access'
DAYS = 30
LOGIN_PATH = '/m/login'
# All password-page text lives here; the lockout line matches web/phone_model.js.
PASSWORD_COPY = dict(
    en=dict(title='FreeVideo', password='Password', remember='Keep me signed in',
            submit='Sign in', incorrect='Incorrect password', retry='Too many attempts. Try again in {minutes} min.',
            phone='FreeVideo mobile'),
    zh=dict(title='FreeVideo', password='密码', remember='保持登录',
            submit='登录', incorrect='密码不正确', retry='尝试次数过多，请 {minutes} 分钟后再试。',
            phone='FreeVideo 手机版'))


def password_language(request):
    """'zh' when the browser's most preferred language is Chinese, else 'en'."""
    first = request.headers.get('Accept-Language', '').split(',')[0].split(';')[0].strip().lower()
    return 'zh' if first == 'zh' or first.startswith('zh-') else 'en'


def password_page(request, *, error='', retry_after=0, status=401):
    language = password_language(request)
    copy = PASSWORD_COPY[language]
    message = copy['retry'].format(minutes=math.ceil(retry_after / 60)) if retry_after else error
    response = web.Response(status=status, content_type='text/html', text=(
        '<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>{title}</title></head><body>'
        '<h1>{title}</h1><p data-retry-after="{retry_after}">{message}</p><form method="post" action="{action}">'
        '<label>{password}<input type="password" name="password" autocomplete="current-password" required></label>'
        '<label><input type="checkbox" name="remember" value="1" checked>{remember}</label>'
        '<button type="submit">{submit}</button></form></body></html>').format(
            **{k: html.escape(v) for k, v in copy.items()}, language=language, message=html.escape(message), retry_after=retry_after, action=LOGIN_PATH))
    response.headers['Cache-Control'] = 'no-store'
    # Chromium sends Origin: null from a no-referrer page, which the login would refuse.
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'none'; form-action 'self'; frame-ancestors 'none'"
    if retry_after:
        response.headers['Retry-After'] = str(retry_after)
        response.headers['X-FreeVideo-Retry-After'] = str(retry_after)
    return response


def phone_file(path, root=None):
    """The page file /m/<name> stands for: .js/.css directly inside web/ and the two wordmark SVGs (/m/ itself is PHONE_PAGE)."""
    root = (root or PHONE_WEB).resolve()
    name = path[len('/m/'):]
    if not (PHONE_FILE.fullmatch(name) or name in PHONE_ASSETS):
        return None
    try:
        found = (root / name).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return found if found.parent in (root, root / 'assets') and found.is_file() else None


def same_origin(request):
    try:
        if len(request.headers.getall('Host', [])) != 1 or len(request.headers.getall('Origin', [])) != 1:
            return False
        origin = urlsplit(request.headers['Origin'])
        scheme = effective_scheme(request, origin.scheme)
        host = urlsplit(scheme + '://' + request.host)
        socket = request.transport.get_extra_info('sockname')
        allowed = {socket[0].split('%')[0]}
        if ipaddress.ip_address(socket[0].split('%')[0]).is_loopback:
            allowed.update(('localhost', '127.0.0.1', '::1'))
        own_port = host.port or (443 if scheme == 'https' else 80)
        return (host.hostname in allowed and own_port == socket[1] and origin.hostname == host.hostname
                and (origin.port or (443 if origin.scheme == 'https' else 80)) == own_port
                and origin.scheme == scheme and not any((origin.path, origin.query, origin.fragment,
                                                         origin.username, origin.password)))
    except (ValueError, TypeError, IndexError, KeyError):
        return False


# Hop-by-hop headers belong to one connection; Content-Length follows the body actually sent.
HOP_BY_HOP = frozenset(('connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
                        'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length'))
DENIED = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FreeVideo</title></head><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#10151e;
color:#e7edf7;font:15px/1.6 'Segoe UI','Noto Sans CJK SC','Noto Sans SC',system-ui,sans-serif"><main style="max-width:520px;
padding:28px 32px;border:1px solid #2a3443;border-radius:12px;background:#1a222e"><h1 style="margin:0 0 10px;font-size:19px">
This FreeVideo server opens from its access link</h1><p style="margin:0 0 18px;color:#a5b3c6">Copy the link in the launcher under Settings → Preferences → Local network. On a Linux server, run
<code>freevideo status</code> to see it.</p><h1 style="margin:0 0 10px;font-size:19px">请使用访问链接打开这台 FreeVideo 服务器</h1>
<p style="margin:0;color:#a5b3c6">请在启动器的“设置 → 偏好 → 局域网”中复制链接。Linux 服务器上运行 <code>freevideo status</code> 可以看到链接。</p></main></body></html>"""


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
        if lower in HOP_BY_HOP or lower == 'authorization' or lower.startswith('x-freevideo-proxy-'):
            continue
        if lower == 'cookie':
            value = '; '.join(part for part in value.split(';') if part.strip() and
                              part.split('=', 1)[0].strip() not in (COOKIE, DEVICE_COOKIE))
            if not value:
                continue
        headers.append((name, value))
    return headers


def create(upstream, token, engine=None):
    upstream = upstream.rstrip('/')
    sockets = 'ws' + upstream[len('http'):]
    client = []
    hop_secret = os.environ.pop(proxy_identity.ENV, '')
    auth = PasswordAuth(engine)
    login_lock = asyncio.Lock()

    def browser_headers(response, request):
        device = request.get('lan_device')
        if device and request.path in ('/m/', '/m/csrf'):
            response.headers['X-FreeVideo-CSRF'] = proxy_identity.csrf(hop_secret, device)
        session = request.get('lan_session')
        if session and session['remember']:
            response.set_cookie(DEVICE_COOKIE, request.cookies[DEVICE_COOKIE], max_age=REMEMBER_SECONDS,
                                httponly=True, samesite='Lax', secure=request.secure, path='/')
        return response

    async def login(request):
        if not same_origin(request):
            return web.Response(status=403)
        # The rest of the proxy streams unlimited uploads. Only login is bounded.
        body = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            body.extend(chunk)
            if len(body) > 16384:
                return web.Response(status=413)
        from urllib.parse import parse_qs
        try:
            if request.content_type != 'application/x-www-form-urlencoded':
                return web.Response(status=415)
            fields = parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=8)
            texts = fields.get('password', [''])
            text = texts[0] if len(texts) == 1 else ''
            remember = fields.get('remember') == ['1']
        except (ValueError, UnicodeError):
            return web.Response(status=400)
        ip = request.remote or ''  # Never trust X-Forwarded-For for attempt limits.
        async with login_lock:
            started = time.monotonic()
            auth.reload()
            record = auth.record
            correct = await asyncio.to_thread(lan_password.verify_record, record, text)
            await asyncio.sleep(max(0, LOGIN_DELAY - (time.monotonic() - started)))
            auth.reload()
            if not auth.enabled or auth.record != record:
                return password_page(request, error=PASSWORD_COPY[password_language(request)]['incorrect'])
            now = time.monotonic()
            remaining = auth.attempts.remaining(ip, now)
            if remaining:
                auth.log(ip, 'locked')
                return password_page(request, retry_after=remaining, status=429)
            if not correct:
                remaining = auth.attempts.failed(ip, now)
                if remaining:
                    auth.log(ip, 'locked')
                return password_page(request, error=PASSWORD_COPY[password_language(request)]['incorrect'], retry_after=remaining,
                                     status=429 if remaining else 401)
            value = auth.issue(remember, time.time())
            auth.attempts.success(ip)
            auth.log(ip, 'success')
            response = web.Response(status=303, headers={'Location': '/m/', 'Cache-Control': 'no-store'})
            response.set_cookie(DEVICE_COOKIE, value, max_age=REMEMBER_SECONDS if remember else None,
                                httponly=True, samesite='Lax', secure=request.secure, path='/')
            return response

    def forwarding_headers(request):
        headers = forwarded(request) + proxy_identity.headers(request, hop_secret)
        if request.get('lan_device'):
            headers.append((proxy_identity.DEVICE, request['lan_device']))
        return headers

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
                                                     headers=forwarding_headers(request), data=body, allow_redirects=False) as reply:
                response = web.StreamResponse(status=reply.status, reason=reply.reason)
                for name, value in reply.headers.items():
                    if name.lower() not in HOP_BY_HOP:
                        response.headers.add(name, value)
                if reply.content_length is not None and request.method != 'HEAD':
                    response.content_length = reply.content_length
                browser_headers(response, request)
                await response.prepare(request)
                async for chunk in reply.content.iter_chunked(1 << 16):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except client_exceptions.ClientConnectorError:
            return web.json_response(dict(error='ComfyUI is not answering yet.'), status=502)

    async def websocket(request):
        browser = browser_headers(web.WebSocketResponse(max_msg_size=0), request)
        await browser.prepare(request)
        headers = [(k, v) for k, v in forwarding_headers(request) if k.lower() not in (
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
        auth.reload()
        if auth.enabled:
            if request.path == LOGIN_PATH and request.method == 'POST':
                return await login(request)
            session = auth.authenticate(request.cookies.get(DEVICE_COOKIE), time.time())
            if session:
                request['lan_device'] = session['hash']
                request['lan_session'] = session
            else:
                header = request.headers.get('Authorization', '')
                if not (header[:7].lower() == 'bearer ' and matches(header[7:].strip(), token)):
                    remaining = auth.attempts.remaining(request.remote or '', time.monotonic())
                    return password_page(request, retry_after=remaining, status=429 if remaining else 401)
        else:
            offered = request.rel_url.query.get('token')
            if offered is not None:
                if not matches(offered, token):
                    return denied(request)
                # Preserve the existing one-time link exchange and cookie behavior.
                rest = [(k, v) for k, v in request.rel_url.query.items() if k != 'token']
                response = web.Response(status=303, headers={'Location': request.rel_url.path + ('?' + urlencode(rest) if rest else '')})
                response.set_cookie(COOKIE, token, max_age=DAYS * 86400, httponly=True, samesite='Lax',
                                    secure=request.secure, path='/')
                return response
            if not matches(supplied(request), token):
                return denied(request)
            if matches(request.cookies.get(COOKIE), token):
                request['lan_device'] = hashlib.sha256(token.encode()).hexdigest()
        # A query token in password mode neither authenticates nor reaches ComfyUI.
        if auth.enabled and 'token' in request.query:
            rest = [(k, v) for k, v in request.rel_url.query.items() if k != 'token']
            location = request.path + ('?' + urlencode(rest) if rest else '')
            return browser_headers(web.Response(status=303, headers={'Location': location}), request)
        if request.path.startswith('/m/') and request.path != LOGIN_PATH and request.method in ('GET', 'HEAD'):
            if not request.get('lan_device'):
                return web.Response(status=403)
            if request.path == '/m/csrf':
                response = web.json_response(dict(csrf=proxy_identity.csrf(hop_secret, request['lan_device'])))
            elif request.path == '/m/':
                response = web.Response(text=PHONE_PAGE if request.method == 'GET' else '', content_type='text/html', charset='utf-8')
            else:
                found = phone_file(request.path)
                if found is None:
                    return web.Response(status=404, headers={'Cache-Control': 'no-store'})
                response = web.Response(body=found.read_bytes() if request.method == 'GET' else b'',
                                        content_type=PHONE_TYPES[found.suffix], charset='utf-8')
            response.headers['Cache-Control'] = 'no-store'
            return browser_headers(response, request)
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
    web.run_app(create(args.upstream, token, args.token_file.parent.parent), host=args.listen, port=args.port, ssl_context=context,
                access_log=None, print=None)


if __name__ == '__main__':
    main()
