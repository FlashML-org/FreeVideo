"""Discover and repair Windows curl without a system install or another curl."""
import hashlib
import base64
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import time
from urllib import error as urlerror, request
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

from . import proxy
from .system import bootstrap_root


_PROBES = {}


class CurlError(RuntimeError):
    def __init__(self, code, detail, **diagnostic):
        self.code, self.diagnostic = code, diagnostic
        super().__init__('[' + code + '] ' + detail)


def catalog():
    return json.loads(Path(__file__).with_name('curl_windows.json').read_text(encoding='utf-8'))


def cache_root(env):
    return (Path(env['FREEVIDEO_BOOTSTRAP_ROOT']).expanduser().absolute()
            if env.get('FREEVIDEO_BOOTSTRAP_ROOT') else bootstrap_root(env.get('FREEVIDEO_HOME')))


def error_fields(error):
    # Provider messages can contain authenticated proxy URLs. Keep typed codes.
    value = dict(exception=type(error).__name__)
    for name in ('errno', 'winerror', 'code'):
        field = getattr(error, name, None)
        if isinstance(field, (int, str)):
            value[name] = field
    reason = getattr(error, 'reason', None)
    if isinstance(reason, BaseException):
        value['cause'] = error_fields(reason)
    if isinstance(error, CurlError):
        value.update(error.diagnostic)
    return value


def candidates(env):
    if env.get('FREEVIDEO_CURL'):
        yield 'selected', Path(env['FREEVIDEO_CURL'])
    system = Path(env.get('SystemRoot') or env.get('WINDIR') or r'C:\Windows')
    for name in ('Sysnative', 'System32'):
        yield 'windows-' + name.lower(), system / name / 'curl.exe'
    yield 'managed', cache_root(env) / ('curl-' + catalog()['version']) / 'bin/curl.exe'
    # Empty/relative PATH entries must not execute a curl from the working folder.
    for entry in env.get('PATH', os.defpath).split(os.pathsep)[:128]:
        path = Path(entry.strip('"'))
        try:
            if path.is_absolute() and (path / 'curl.exe').is_file():
                yield 'path', path / 'curl.exe'
        except OSError:
            continue
    git = shutil.which('git.exe', path=env.get('PATH', os.defpath))
    roots = [Path(git).parent.parent] if git else []
    roots += [Path(env[name]) / 'Git' for name in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)') if env.get(name)]
    if env.get('LOCALAPPDATA'):
        roots.append(Path(env['LOCALAPPDATA']) / 'Programs/Git')
    for root in roots:
        for folder in ('mingw64/bin', 'usr/bin'):
            yield 'git', root / folder / 'curl.exe'


def probe(path, env):
    try:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, env.get('PATH'))
        cached = _PROBES.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return dict(cached[1])
        from .windows_ux import external_python, hidden_console
        with external_python():
            result = subprocess.run([str(path), '--disable', '--version'], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=3, **hidden_console())
        code = result.returncode & 0xffffffff
        value = dict(status='CURL_START_FAILED', returncode=result.returncode,
                     windows_exit_code='0x%08X' % code)
        if code in (0xc0000135, 0xc0000139):
            value['status'] = 'CURL_MISSING_DLL'
        elif code == 0xc000007b:
            value['status'] = 'CURL_INVALID_BINARY'
        elif code == 0:
            output = result.stdout[:8192].decode('utf-8', errors='replace')
            version = re.search(r'^curl ([0-9.]+)', output)
            protocols = re.search(r'^Protocols: (.+)$', output, re.M)
            if version and protocols and 'https' in protocols[1].split():
                value.update(status='ready', version=version[1])
            else:
                value['status'] = 'CURL_HTTPS_UNAVAILABLE'
        if len(_PROBES) > 64:
            _PROBES.clear()
        _PROBES[key] = (time.monotonic(), value)
        return value
    except FileNotFoundError:
        return dict(status='CURL_NOT_FOUND')
    except subprocess.TimeoutExpired:
        return dict(status='CURL_VERSION_TIMEOUT')
    except OSError as error:
        code = ('CURL_START_DENIED' if isinstance(error, PermissionError) else
                'CURL_INVALID_BINARY' if getattr(error, 'winerror', None) in (193, 216) else 'CURL_START_FAILED')
        return dict(status=code, **error_fields(error))


def inspect(env=None):
    env = dict(os.environ if env is None else env)
    report = dict(schema='freevideo.curl-diagnostic', schema_version=1,
                  checked_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  platform=platform.platform(), process_bits=8 * struct.calcsize('P'),
                  candidates=[], selected=None, repair_attempts=[])
    seen = set()
    for origin, path in candidates(env):
        path = path.absolute()
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        value = probe(path, env)
        report['candidates'].append(dict(origin=origin, path=str(path), **value))
        if value['status'] == 'ready':
            report.update(selected=str(path), status='ready')
            return report
    failed = [row['status'] for row in report['candidates'] if row['status'] != 'CURL_NOT_FOUND']
    report['status'] = failed[0] if failed else 'CURL_NOT_FOUND'
    return report


def failure_message(report):
    causes = ', '.join(dict.fromkeys(row['status'] for row in report['candidates']))
    return ('[' + report['status'] + '] No usable Windows download tool. '
            'Local checks: ' + causes + '. Automatic repair: ' +
            str(report.get('repair', 'not run')) +
            '. Retry installation or export the report; curl diagnostics are included.')


class HTTPSRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith('https://'):
            raise CurlError('CURL_REPAIR_REDIRECT', 'The download redirected away from HTTPS.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HTTPProxy(request.ProxyHandler):
    def proxy_open(self, req, address, protocol):
        # ProxyHandler normally reads global NO_PROXY and Windows registry bypass
        # rules. An explicitly selected repair route must not silently bypass it.
        parsed = urlsplit(address if '://' in address else 'http://' + address)
        if parsed.scheme != 'http' or not parsed.hostname:
            raise CurlError('CURL_REPAIR_PROXY_UNSUPPORTED', 'The bootstrap requires an HTTP proxy.')
        host = ('[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname)
        if parsed.port:
            host += ':' + str(parsed.port)
        if parsed.username is not None:
            auth = unquote(parsed.username) + ':' + unquote(parsed.password or '')
            req.add_header('Proxy-authorization', 'Basic ' + base64.b64encode(auth.encode()).decode('ascii'))
        req.set_proxy(host, 'http')
        return None  # HTTPSHandler performs CONNECT and verifies the target TLS.


def download(spec, path, env, route, mode, emit, cancelled):
    routed = proxy.environment(env, route)
    address = routed.get('https_proxy') or routed.get('all_proxy')
    if mode == 'proxy' and not address:
        raise CurlError('CURL_REPAIR_PROXY_MISSING', 'Proxy only is selected but no HTTPS download proxy is configured.')
    proxies = {'https': address} if address else {}
    opener = request.build_opener(HTTPProxy(proxies), HTTPSRedirect())
    started, last = time.monotonic(), 0.
    digest, size = hashlib.sha256(), 0
    req = request.Request(spec['url'], headers={'User-Agent': 'FreeVideo-Setup/1', 'Accept-Encoding': 'identity'})
    with opener.open(req, timeout=10) as response, path.open('wb') as stream:
        if response.status != 200:
            raise CurlError('CURL_REPAIR_HTTP', 'Unexpected HTTP response: ' + str(response.status))
        declared = response.headers.get('Content-Length')
        if declared and int(declared) != spec['bytes']:
            raise CurlError('CURL_REPAIR_SIZE', 'The download size does not match the pinned tool.',
                            expected_bytes=spec['bytes'], actual_bytes=int(declared))
        while True:
            if cancelled():
                raise CurlError('CURL_REPAIR_CANCELLED', 'Download tool preparation was cancelled.')
            if time.monotonic() - started > 60:
                raise CurlError('CURL_REPAIR_TIMEOUT', 'Download tool transfer exceeded 60 seconds.')
            block = response.read1(min(128 * 1024, spec['bytes'] - size + 1))
            if not block:
                break
            size += len(block)
            if size > spec['bytes']:
                raise CurlError('CURL_REPAIR_SIZE', 'The download exceeded the pinned tool size.',
                                expected_bytes=spec['bytes'], actual_bytes=size)
            stream.write(block)
            digest.update(block)
            now = time.monotonic()
            if now - last >= .5:
                emit('Downloading curl', done=size, total=spec['bytes'])
                last = now
    if size != spec['bytes'] or digest.hexdigest() != spec['sha256']:
        raise CurlError('CURL_REPAIR_INTEGRITY', 'Download tool verification failed.',
                        expected_bytes=spec['bytes'], actual_bytes=size,
                        expected_sha256=spec['sha256'], actual_sha256=digest.hexdigest())


def unpack(archive, destination, spec):
    with zipfile.ZipFile(archive) as zipped:
        for relative, expected in spec['files'].items():
            # Names come exclusively from our committed catalog, never the ZIP.
            info = zipped.getinfo(spec['prefix'] + relative)
            if info.file_size != expected['bytes']:
                raise CurlError('CURL_REPAIR_INTEGRITY', 'Unexpected archive member size.')
            content = zipped.read(info)
            if hashlib.sha256(content).hexdigest() != expected['sha256']:
                raise CurlError('CURL_REPAIR_INTEGRITY', 'Archive member verification failed.')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def ensure(root, env=None, *, progress=None, cancelled=lambda: False):
    """Explicit setup action; read-only discovery never calls this function."""
    from .download_settings import read
    from .locking import runtime_lock
    from .monitoring import save
    env = dict(os.environ if env is None else env)
    env['FREEVIDEO_HOME'] = str(root)
    cache = cache_root(env)
    report = inspect(env)
    receipt = cache / 'curl-diagnostic.json'

    def emit(detail, **fields):
        if progress:
            progress(dict(event='freevideo_ui', kind='progress', key='prepare-curl',
                          label='Prepare download tools', detail=detail, **fields))

    try:
        if report['selected']:
            report['repair'] = 'not needed'
            return report['selected']
        emit('No usable curl found; preparing a verified local copy')
        cache.mkdir(parents=True, exist_ok=True)
        with runtime_lock(cache / 'curl.lock', inherit=False):
            spec = catalog()
            mode = read(Path(root) / 'download-settings.json')['proxy_mode']
            report.update(proxy_mode=mode, repair='running')
            for route in proxy.routes(env, mode=mode):
                if cancelled():
                    report.update(status='CURL_REPAIR_CANCELLED', repair='cancelled')
                    raise CurlError('CURL_REPAIR_CANCELLED', 'Download tool preparation was cancelled.')
                attempt = dict(source='curl.se', route=route)
                started = time.monotonic()
                report['repair_attempts'].append(attempt)
                try:
                    with tempfile.TemporaryDirectory(prefix='curl-stage-', dir=cache) as temp:
                        staging = Path(temp)
                        archive = staging / 'curl.zip'
                        download(spec, archive, env, route, mode, emit, cancelled)
                        folder = staging / 'tool'
                        unpack(archive, folder, spec)
                        check = probe(folder / 'bin/curl.exe', env)
                        attempt['probe'] = check
                        if check['status'] != 'ready':
                            raise CurlError(check['status'], 'The verified download tool could not start.')
                        target = cache / ('curl-' + spec['version'])
                        if target.exists():
                            target.rename(cache / ('curl-rejected-' + uuid.uuid4().hex))
                        folder.rename(target)
                        report.update(selected=str(target / 'bin/curl.exe'), status='ready', repair='complete')
                        attempt['status'] = 'ready'
                        emit('Download tool ready', done=spec['bytes'], total=spec['bytes'])
                        return report['selected']
                except (OSError, ValueError, KeyError, CurlError, zipfile.BadZipFile) as error:
                    status = (error.code if isinstance(error, CurlError) else
                              'CURL_REPAIR_STORAGE' if isinstance(error, PermissionError) or getattr(error, 'errno', None) == 28 else
                              'CURL_REPAIR_TLS' if isinstance(getattr(error, 'reason', error), ssl.SSLError) else
                              'CURL_REPAIR_DNS' if isinstance(getattr(error, 'reason', error), socket.gaierror) else
                              'CURL_REPAIR_TIMEOUT' if isinstance(getattr(error, 'reason', error), (TimeoutError, socket.timeout)) else
                              'CURL_REPAIR_HTTP' if isinstance(error, urlerror.HTTPError) else 'CURL_REPAIR_FAILED')
                    attempt.update(status=status, **error_fields(error))
                    report.update(status=status, repair='failed')
                    if status == 'CURL_REPAIR_CANCELLED':
                        break
                finally:
                    attempt['elapsed_seconds'] = round(time.monotonic() - started, 3)
            raise RuntimeError(failure_message(report))
    except (OSError, ValueError) as error:
        report.update(status='CURL_REPAIR_BUSY' if isinstance(error, BlockingIOError) else 'CURL_REPAIR_STORAGE',
                      repair='failed', failure=error_fields(error))
        raise RuntimeError(failure_message(report)) from error
    finally:
        try:
            save(receipt, report)
        except (OSError, ValueError) as error:
            report['receipt_error'] = error_fields(error)
        # The log still contains the diagnostic if the selected disk is unwritable.
        if progress:
            progress(dict(event='curl_diagnostic', **report))
