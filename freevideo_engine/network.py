"""Bounded route probes, ranked mirrors and resumable verified downloads.

Only curl and Python stdlib are needed before setup. Proxy and direct routes are
compared without changing user settings. Reports never include proxy credentials.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import hashlib
import html
import json
import math
import os
import sys
from pathlib import Path
import re
import shutil
import subprocess
from .windows_ux import hidden_console
import threading
import time
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit
from . import proxy


SOURCES = {
    'pypi': {'official': 'https://pypi.org/simple', 'tuna': 'https://pypi.tuna.tsinghua.edu.cn/simple'},
    'torch': {'official': 'https://download.pytorch.org/whl', 'nju': 'https://mirrors.nju.edu.cn/pytorch/whl'},
    'models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com'},
    'vdn-models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com',
                   'modelscope': 'https://www.modelscope.cn'},
    'edge-models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com',
                    'modelscope': 'https://modelscope.ai'},
    'github': {'official': 'https://github.com', 'ghfast': 'https://ghfast.top/https://github.com'},
    'cuda': {'official': 'https://developer.download.nvidia.com/compute/cuda/redist',
             'nvidia-cn': 'https://developer.download.nvidia.cn/compute/cuda/redist'},
}
PROXY_KEYS = proxy.PROXY_KEYS
SAMPLE_BYTES = 256 * 1024
SPEED_SAMPLE_BYTES = 32 * 2**20
SPEED_SAMPLE_SECONDS = 3.
MIN_TRANSFER_LIMIT = 1024 * 1024


def proxy_environment(environ=None):
    return proxy.inherited(environ)


def configured_source(family, env):
    names = {'pypi': ('UV_DEFAULT_INDEX', 'UV_INDEX_URL', 'PIP_INDEX_URL'),
             'models': ('HF_ENDPOINT',), 'vdn-models': ('HF_ENDPOINT',), 'edge-models': ('HF_ENDPOINT',),
             'github': ('FREEVIDEO_GITHUB_MIRROR',)}.get(family, ())
    return next((env[k].rstrip('/') for k in names if env.get(k)), None)


def source_url(family, name, env=None):
    family = 'torch' if family.startswith('torch-') else 'github' if family == 'git' else family
    env = os.environ if env is None else env
    if name == 'user':
        value = configured_source(family, env)
        if not value:
            raise ValueError('The user-configured source is no longer set in the environment')
        return value
    return SOURCES[family][name]


def model_spec(spec=None):
    return (spec if spec is not None else
            json.loads(Path(__file__).with_name('dependencies.json').read_text(encoding='utf-8')))['models']


def modelscope_mapping(row, spec=None):
    """Return only a published, verified HF-to-ModelScope revision pair."""
    settings = model_spec(spec)
    # A ModelScope commit is not a Hugging Face commit. Only the verified pair
    # shares paths/content; an encoder or a future HF pin needs its own route.
    for prefix in ('vdn', 'edge'):
        mirror = settings.get(prefix + '_modelscope', {})
        if (mirror and row['repo'] == settings.get(prefix + '_repo', mirror['repo']) and
                row['revision'] == settings.get(prefix + '_revision', mirror['hf_revision']) == mirror['hf_revision']):
            return dict(mirror, family=prefix + '-models')
        # Additional formats in the same repository keep independent immutable
        # pins. Never move old installations just because a new variant exists.
        for variant in settings.get(prefix + '_modelscope_variants', {}).values():
            if row['repo'] == variant['repo'] and row['revision'] == variant['hf_revision']:
                return dict(variant, family=prefix + '-models')
    return None


def model_family(row, spec=None):
    mirror = modelscope_mapping(row, spec)
    return mirror['family'] if mirror else 'models'


def model_url(row, name, env=None, *, spec=None):
    if name == 'modelscope':
        mirror = modelscope_mapping(row, spec)
        if not mirror:
            raise ValueError('No verified ModelScope mapping for this model revision')
        return (source_url(mirror['family'], name, env) + '/api/v1/models/' + quote(mirror['repo'], safe='/') +
                '/repo?' + urlencode({'Revision': mirror['revision'], 'FilePath': row['file']}))
    return (source_url('models', name, env) + '/' + quote(row['repo'], safe='/') +
            '/resolve/' + quote(row['revision'], safe='') + '/' + quote(row['file'], safe='/'))


def model_urls(network, row, env=None, *, spec=None):
    family = model_family(row, spec)
    # Plans made before ModelScope support keep their existing HF ordering.
    if family not in network.get('sources', {}):
        family = 'models'
    names = ['official'] if network.get('mode') == 'official' or row.get('official_only') else ordered(network, family)
    return [(name, model_url(row, name, env, spec=spec)) for name in names]


def curl_config(url, headers=()):
    def quote(value):
        if any(c in str(value) for c in ('\n', '\r', '\x00')):
            raise ValueError('Invalid newline in network configuration')
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
    return ('url = ' + quote(url) + '\n' + ''.join('header = ' + quote(h) + '\n' for h in headers)).encode()


def curl_command(timeout, env=None):
    if not shutil.which('curl'):
        raise RuntimeError('Network setup needs curl (Ubuntu/Debian: sudo apt install curl).')
    command = ['curl', '--config', '-', '--silent', '--show-error', '--no-location-trusted', '--location',
            '--max-redirs', '5', '--fail', '--proto', '=https,http', '--proto-redir', '=https,http',
            '--connect-timeout', str(timeout), '--user-agent', 'FreeVideo-Setup/1']
    if (env or {}).get('FREEVIDEO_PROXY_ROUTE') == 'direct':
        command += ['--proxy', '', '--noproxy', '*']
    return command


def sample(url, timeout=5, limit=SAMPLE_BYTES, *, ranged=True, env=None, headers=()):
    """Read at most limit bytes; a hard curl deadline bounds DNS and slow reads."""
    command = curl_command(timeout, env) + ['--max-time', str(timeout)]
    if url.startswith('https://'):
        command += ['--proto-redir', '=https']
    if ranged:
        command += ['--range', '0-' + str(limit - 1)]
    config = curl_config(url, headers)
    started = time.monotonic()
    process = subprocess.Popen(command, env=proxy_environment(env), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_console())
    try:
        process.stdin.write(config)
        process.stdin.close()
        data = process.stdout.read(limit)
        bounded = len(data) == limit
        if bounded and process.poll() is None:
            process.terminate()
        code = process.wait(timeout=timeout + 2)
        if (code and not bounded) or not data:
            raise RuntimeError('HTTP probe failed (curl %s)' % code)
        return data, time.monotonic() - started
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()


def speed_sample(url, timeout=5, limit=SPEED_SAMPLE_BYTES, duration=SPEED_SAMPLE_SECONDS, *, env=None, headers=()):
    """Time a bounded body transfer separately from connection/redirect startup.

    Retain only the first chunk for content validation. Curl bounds startup;
    a timer also ends a stalled body without relying on another byte arriving.
    A time/byte-limited sample is intentional, a disconnected transfer is not.
    """
    command = curl_command(timeout, env) + ['--no-buffer', '--max-time', str(timeout + duration),
                                       '--range', '0-' + str(limit - 1)]
    if url.startswith('https://'):
        command += ['--proto-redir', '=https']
    started = time.monotonic()
    process = subprocess.Popen(command, env=proxy_environment(env), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_console())
    timer = None
    stopped = []

    def stop(reason):
        if not stopped:
            stopped.append((reason, time.monotonic()))
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass  # The process may have exited between poll and terminate.

    try:
        process.stdin.write(curl_config(url, headers))
        process.stdin.close()
        first = process.stdout.read(1)
        body_started = time.monotonic()
        prefix = first
        count = len(first)
        last_body = body_started
        timer = threading.Timer(duration, stop, args=('time-limit',))
        timer.daemon = True
        timer.start()
        while count < limit:
            block = process.stdout.read(min(64 * 1024, limit - count))
            if not block:
                break
            count += len(block)
            if len(prefix) < 64 * 1024:
                prefix += block[:64 * 1024-len(prefix)]
            last_body = time.monotonic()
        finished = time.monotonic()
        if count == limit:
            stop('byte-limit')
        code = process.wait(timeout=2)
        if not prefix or (code and not stopped and code != 28):
            raise RuntimeError('HTTP speed probe failed (curl %s)' % code)
        ended = min(finished, stopped[0][1]) if stopped else finished
        seconds = max(0., ended - body_started)
        measured = count - len(first)
        # Tiny files/bursts establish connectivity but cannot rank bandwidth.
        reliable = measured >= 256 * 1024 and (seconds >= .2 or measured >= 4 * 2**20)
        return prefix, dict(bytes=count, startup_seconds=body_started-started, ttfb_seconds=body_started-started,
            transfer_seconds=seconds, seconds=finished-started, measured_bytes=measured,
            bytes_per_second=measured / max(.001, seconds) if reliable else None,
            body_stalled=ended-last_body > max(.5, duration/2),
            speed_measured=reliable, stop_reason=stopped[0][0] if stopped else ('deadline' if code == 28 else 'eof'),
            scope='Bounded single-connection HTTP body transfer; startup excluded; SDK/Xet concurrency not measured')
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if process.poll() is None:
            process.kill()
            process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def wheel_link(index, pattern, timeout, env):
    data, _ = sample(index, timeout, 2 * 2**20, ranged=False, env=env)
    links = [urljoin(index, html.unescape(value)) for value in re.findall(r'href=[\'"]([^\'"]+)', data.decode(errors='replace'))]
    matched = [link for link in links if re.search(pattern, unquote(urlsplit(link).path).rsplit('/', 1)[-1])]
    if not matched:
        raise RuntimeError('Mirror does not list the required wheel')
    return matched[-1]


def probe(family, name, spec, versions, timeout, env, *, measure_speed=False):
    base = source_url(family, name, env)
    started = time.monotonic()
    kind = 'artifact range'
    try:
        if family == 'pypi':
            url = wheel_link(base + '/packaging/', r'^packaging-26\.3-py3-none-any\.whl$', timeout, env)
        elif family.startswith('torch-'):
            _, cuda, version = family.split('-')
            target = r'win_amd64' if versions.get('target_system') == 'Windows' else r'(?:manylinux[^/]*|linux)_x86_64'
            url = wheel_link(base + '/' + cuda + '/torch/', r'^torch-' + re.escape(version + '+' + cuda) + r'-cp312-cp312-' + target + r'\.whl$', timeout, env)
        elif family == 'models':
            settings = spec['models']
            url = model_url({'repo': settings['encoder_repo'], 'revision': settings['encoder_revision'],
                             'file': settings['encoder_file']}, name, env, spec=spec)
        elif family in ('vdn-models', 'edge-models'):
            settings = spec['models']
            mirror = settings[family.split('-')[0] + '_modelscope']
            url = model_url({'repo': mirror['repo'], 'revision': mirror['hf_revision'],
                             'file': mirror['probe_file']}, name, env, spec=spec)
        elif family == 'github':
            url = versions['uv']['url'].replace(SOURCES['github']['official'], base, 1)
        elif family == 'git':
            endpoint = spec['vdn']['url'].replace(SOURCES['github']['official'], base, 1)
            kind = 'Git remote availability with effective Git configuration'
            # curl does not read Git's global/URL-scoped proxy settings. Probe
            # with Git itself, keeping custom URL credentials out of argv.
            import uuid
            remote = 'freevideo-probe-' + uuid.uuid4().hex
            settings = {'remote.' + remote + '.url': endpoint}
            # remote.url is multivalued: reusing this checkout's origin can
            # select its existing SSH URL instead of the HTTPS URL being tested.
            # A private temporary name avoids that; carry origin's proxy policy
            # so the probe still matches the subsequent dependency fetch.
            origin_proxy = subprocess.run(['git', 'config', '--get', 'remote.origin.proxy'], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, **hidden_console())
            if origin_proxy.returncode == 0:
                settings['remote.' + remote + '.proxy'] = origin_proxy.stdout.decode().rstrip('\r\n')
            elif origin_proxy.returncode != 1:
                raise RuntimeError('Git proxy configuration could not be read')
            git_env = proxy.git_environment(env, settings)
            git_env['GIT_TERMINAL_PROMPT'] = '0'
            from . import processes
            result = processes.run(['git', 'ls-remote', '--exit-code', remote, 'HEAD'],
                env=git_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, supervise=True)
            if result.returncode or not re.match(rb'[0-9a-f]{40,64}\s+HEAD', result.stdout):
                raise RuntimeError('Git connection probe failed')
            seconds = time.monotonic() - started
            return dict(id=name, ok=True, seconds=seconds, probe_seconds=seconds, bytes=len(result.stdout), scope=kind)
        else:
            component = versions['cuda_toolkits']['13.0']['components']['cuda_cudart']
            url = base + '/' + component['relative_path']
        headers = ()
        if (family in ('models', 'vdn-models', 'edge-models') and name == 'official'
                and urlsplit(url).scheme == 'https' and urlsplit(url).netloc == 'huggingface.co'):
            token = env.get('HF_TOKEN') or env.get('HUGGING_FACE_HUB_TOKEN')
            if token:
                # curl drops Authorization across redirect hosts; never use
                # --location-trusted. The secret is passed through stdin.
                headers = ('Authorization: Bearer ' + token,)
        measurement = None
        if measure_speed:
            data, measurement = speed_sample(url, timeout, env=env, headers=headers)
            seconds = measurement['seconds']
        else:
            data, seconds = sample(url, timeout, env=env, **({'headers': headers} if headers else {}))
        if family in ('pypi', 'github', 'cuda') or family.startswith('torch-'):
            if not data.startswith((b'PK', b'\x1f\x8b', b'\xfd7zXZ')):
                raise RuntimeError('Source returned a page instead of an archive')
        elif len(data) < 8 or int.from_bytes(data[:8], 'little') > 100 * 2**20:
            raise RuntimeError('Source did not return the pinned safetensors file')
        result = {'id': name, 'ok': True, 'seconds': seconds, 'probe_seconds': time.monotonic() - started,
                  'bytes': len(data), 'bytes_per_second': len(data) / max(.001, seconds), 'scope': kind}
        if measurement is not None:
            result.update(measurement)
        return result
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # Do not persist URLs, proxy errors, access tokens or redirect queries.
        return {'id': name, 'ok': False, 'probe_seconds': time.monotonic() - started,
                'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__, 'scope': kind}


def probe_score(row, family):
    """Expected bounded transfer rate, including startup and observed stalls."""
    if not row.get('ok'):
        return 0.
    latency = row.get('ttfb_seconds', row.get('startup_seconds', row.get('seconds', 0.))) or 0.
    if not isinstance(latency, (int, float)) or not math.isfinite(latency):
        latency = 0.
    latency = max(0., latency)
    if family == 'git':
        return 1. / max(.001, row.get('seconds') or .001)
    rate = row.get('bytes_per_second')
    if (row.get('speed_measured') is False or not isinstance(rate, (int, float))
            or not math.isfinite(rate) or rate <= 0):
        return 0.
    score = SPEED_SAMPLE_BYTES / (latency + SPEED_SAMPLE_BYTES/rate)
    return score * (.2 if row.get('body_stalled') else 1.)


def plan(spec, versions, layout='unified', *, mode='auto', timeout=5, offline=False, env=None,
         model_only=False, measure_speed=True, progress=None, proxy_mode='auto'):
    from .environments import ENVIRONMENTS, environment_names
    env = proxy_environment(env)
    started = time.monotonic()
    if proxy_mode not in proxy.MODES:
        raise ValueError('Unknown download connection mode')
    proxy_configured = proxy.configured(env)
    git_keys = [] if offline or model_only else proxy.git_proxy_keys(env)
    families = ['pypi', 'models', 'vdn-models', 'github', 'git', 'cuda']
    if spec['models'].get('edge_modelscope'):
        families.insert(3, 'edge-models')
    if versions.get('target_system') == 'Windows':
        families.remove('cuda')
    for name in environment_names(layout):
        role = ENVIRONMENTS[name]
        family = 'torch-' + role['cuda'] + '-' + role['torch'][0].split('==')[1]
        if family not in families:
            families.append(family)
    if model_only:
        families=[family for family in families if family in ('models','vdn-models','edge-models')]
    selections, jobs = {}, []
    for family in families:
        group = 'torch' if family.startswith('torch-') else 'github' if family == 'git' else family
        names = ['official'] if mode == 'official' else list(SOURCES[group])
        if mode != 'official' and configured_source(group, env):
            names.insert(0, 'user')
        selections[family] = []
        routes = proxy.routes(env, git=family == 'git', mode=proxy_mode)
        jobs += [(family, name, route) for name in names for route in routes]
    if offline:
        for family, name, route in jobs:
            selections[family].append({'id': name, 'route': route, 'ok': None, 'scope': 'Offline fixture; not measured'})
    else:
        if progress:
            progress(dict(done=0, total=len(jobs)))
        # Bound parallel traffic and memory. Measure real body bytes, keeping
        # first-byte latency separate from throughput; no regional assumptions.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(probe, family, name, spec, versions, timeout,
                proxy.direct(env, git=family == 'git', keys=git_keys) if route == 'direct' else env,
                measure_speed=measure_speed): (family, name, route) for family, name, route in jobs}
            for index, future in enumerate(as_completed(futures), 1):
                family, name, route = futures[future]
                result = dict(future.result(), route=route)
                result['score'] = probe_score(result, family)
                selections[family].append(result)
                if progress:
                    progress(dict(done=index, total=len(jobs), family=family, source=name, route=route))
        for family, rows in selections.items():
            rows.sort(key=lambda r: (not r['ok'], -r['score'],
                r.get('ttfb_seconds', r.get('seconds', float('inf'))), r['id'], r['route']))
    return {'schema_version': 3, 'mode': mode, 'proxy_mode': proxy_mode,
            'proxy_variables': [k for k in PROXY_KEYS if env.get(k)],
            'proxy_configured': proxy_configured,
            'git_proxy_configured': bool(git_keys or env.get('GIT_PROXY_COMMAND')),
            'proxy_policy': 'Per-download connection mode; system proxy settings unchanged',
            'timeout_seconds': timeout, 'sources': selections, 'probe_seconds': time.monotonic() - started,
            'scope': 'Concurrent bounded samples: connectivity, first-byte latency and body transfer. No location lookup; pinned files unchanged.'}


def installed_plan(env=None):
    env = os.environ if env is None else env
    path = env.get('FREEVIDEO_NETWORK_PLAN')
    return json.loads(Path(path).read_text(encoding='utf-8')).get('network', {}) if path else {}


def ordered(network, family):
    rows = network.get('sources', {}).get(family)
    names=list(dict.fromkeys(row['id'] for row in rows)) if rows else ['official']
    from .download_settings import order
    names = order(network,family,names)
    failed = {r['id'] for r in (rows or []) if r.get('last_transfer_success') is False}
    return sorted(names, key=lambda name: name in failed)


def source_health(network, family, name, success):
    rows = network.get('sources', {}).get(family, [])
    selected = [r for r in rows if r['id'] == name]
    for row in selected:
        row['last_transfer_success'] = success
    others = [r for r in rows if r['id'] != name]
    rows[:] = selected + others if success else others + selected


def route_order(network, family, name, env=None):
    from .download_settings import current
    preferences = current(network)
    mode = preferences['proxy_mode']
    allowed = proxy.routes(env, git=family == 'git', mode=mode)
    rows = [r for r in network.get('sources', {}).get(family, []) if r['id'] == name]
    measured = preferences.get('probe', {})
    if (time.time()-measured.get('measured_at', 0) < 86400 and
            measured.get('proxy_mode', 'auto') == mode):
        rows = [r for r in measured.get('sources', {}).get(family, [])
                if r.get('id') == name and r.get('ok')] + rows
    preferred = [r['last_successful_route'] for r in rows if r.get('last_successful_route') in allowed]
    tested = [r['route'] for r in rows if r.get('route') in allowed]
    return list(dict.fromkeys(preferred + tested + allowed))


def route_health(network, family, name, route):
    for row in network.get('sources', {}).get(family, []):
        if row['id'] == name:
            row['last_successful_route'] = route


def route_environment(network, family, name, env=None, route=None):
    allowed = route_order(network, family, name, env)
    selected = route if route in allowed else allowed[0]
    return proxy.environment(env, selected, git=family == 'git')


def urls(network, family, original, env=None):
    origin = SOURCES[family]['official']
    if not original.startswith(origin + '/'):
        return [('original', original)]
    return [(name, source_url(family, name, env) + original[len(origin):]) for name in ordered(network, family)]


def event(network, **details):
    row = dict(event='network', epoch=time.time(), **details)
    if network.get('event_callback'):
        network['event_callback'](row)
    elif not network.get('quiet'):
        sys.stdout.write(json.dumps(row) + '\n')
        sys.stdout.flush()
    path = network.get('events_path') or os.environ.get('FREEVIDEO_NETWORK_EVENTS')
    if path:
        # One bounded line per attempt, no URLs or credentials.
        with Path(path).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')


def retryable(message):
    message = str(message).lower()
    return any(s in message for s in ('timed out', 'timeout', 'connection', 'could not resolve', 'failed to download',
        'failed to fetch', 'network is unreachable', 'ssl', 'tls', 'http status', 'status code',
        'requested url returned error', 'no solution found', 'no matching distribution', 'could not find a version',
        'rpc failed', 'early eof', 'unable to access', 'remote end hung up', 'couldn\'t connect', 'proxy'))


def connection_failure(message):
    return retryable(message) and not any(s in str(message).lower() for s in
        ('no solution found', 'no matching distribution', 'could not find a version'))


class PackageSourcesError(RuntimeError):
    """A package install failed on every retained source.

    The old error only named the package family, which made a setup report
    impossible to act on.  Keep the structured attempts on the exception so
    callers can render them locally while the message remains short enough for
    the launcher UI.
    """

    def __init__(self, family, attempts):
        self.family = family
        self.attempts = [dict(row) for row in attempts]
        summaries = []
        for row in self.attempts:
            detail = row.get('summary') or row.get('detail') or row.get('error_type') or 'unknown failure'
            summaries.append('%s/%s: %s' % (row.get('source', 'unknown'),
                                            row.get('route', 'unknown'), detail))
        message = 'All package sources failed for %s. Check your network or connection mode in Downloads. Attempts: %s' % (family, '; '.join(summaries))
        super().__init__(message)


_ANSI_ESCAPE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _package_failure(error):
    """Return a bounded, report-safe package error detail.

    Package managers frequently echo index URLs and environment-derived
    credentials.  Preserve useful stderr while removing credentials, bearer
    tokens and machine-specific absolute paths before it reaches events or a
    shared diagnostic report.
    """
    value = getattr(error, 'stderr', None) or getattr(error, 'stdout', None) or str(error)
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    value = _ANSI_ESCAPE.sub('', str(value)).replace('\x00', '')
    value = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1<REDACTED>@', value)
    value = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+', r'\1<REDACTED>', value)
    value = re.sub(r'(?i)(\b[\w-]{0,64}(?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s,;&]+',
                   r'\1<REDACTED>', value)
    # Keep a stable path marker without exposing usernames, drives or mounts.
    value = re.sub(r'(?<![A-Za-z0-9_:/])(?:[A-Za-z]:[\\/]|/)([^\s"\'<>;,)\]}]*)',
                   lambda match: '<PATH>/' + re.split(r'[\\/]', match.group(1).rstrip('\\/'))[-1], value)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    detail = '\n'.join(lines[-12:])[-4096:] if lines else 'no package-manager output'
    summary = next((line for line in lines if line), detail).strip()[:320]
    return detail, summary


def _package_attempt(error, source, route):
    detail, summary = _package_failure(error)
    return dict(source=source, route=route, error_type=type(error).__name__,
                returncode=getattr(error, 'returncode', None), detail=detail, summary=summary)


def package_environment(network, family, name, environ=None, *, route=None):
    env = route_environment(network, family, name, environ, route)
    endpoint = source_url(family, name, environ)
    if family.startswith('torch-'):
        endpoint += '/' + family.split('-')[1]
    env.update(UV_DEFAULT_INDEX=endpoint, UV_INDEX_URL=endpoint, PIP_INDEX_URL=endpoint,
               UV_HTTP_TIMEOUT='20', UV_HTTP_RETRIES='1', PIP_DEFAULT_TIMEOUT='20', PIP_RETRIES='1',
               UV_NO_CONFIG='1')
    for key in ('UV_INDEX', 'UV_EXTRA_INDEX_URL', 'PIP_EXTRA_INDEX_URL'):
        env.pop(key, None)
    return env


def package_command(network, family, run, env=None):
    attempts = []
    for name in ordered(network, family):
        for route in route_order(network, family, name, env):
            event(network, category=family, source=name, route=route, action='package-attempt')
            try:
                attempt_env = package_environment(network, family, name, env, route=route)
                # The host setup uses these private markers to retain one log
                # per source/route instead of overwriting the first failure.
                # They are never included in a report or sent to a package
                # manager as configuration.
                attempt_env['FREEVIDEO_PACKAGE_SOURCE'] = name
                attempt_env['FREEVIDEO_PACKAGE_ROUTE'] = route
                result = run(attempt_env, name)
                source_health(network, family, name, True)
                route_health(network, family, name, route)
                return result
            except (RuntimeError, subprocess.CalledProcessError) as error:
                attempt = _package_attempt(error, name, route)
                attempts.append(attempt)
                event(network, category=family, source=name, route=route, action='package-failed',
                      error_type=attempt['error_type'], returncode=attempt['returncode'],
                      detail=attempt['detail'], summary=attempt['summary'])
                detail = attempt['detail']
                if not retryable(detail):
                    # Preserve the original exception type and traceback for
                    # build/configuration errors, but make the attempt data
                    # available to the local report collector.
                    try:
                        error.package_attempts = attempts
                    except Exception:
                        pass
                    raise
                event(network, category=family, source=name, route=route, action='package-fallback', reason='network-or-unavailable-pinned-package')
                if not connection_failure(detail):
                    break
        source_health(network, family, name, False)
    raise PackageSourcesError(family, attempts)


def hash_file(path, algorithm='sha256', git_blob=False, *, discard_cache=False, progress=None):
    value = hashlib.new(algorithm)
    if git_blob:
        value.update(('blob %d\0' % path.stat().st_size).encode())
    with path.open('rb') as stream:
        offset = 0
        total = path.stat().st_size
        if progress:
            progress(0, total)
        for block in iter(lambda: stream.read(4 * 2**20), b''):
            value.update(block)
            if discard_cache:
                from .download_cache import discard_read_cache
                discard_read_cache(stream.fileno(), offset, len(block))
            offset += len(block)
            if progress:
                progress(offset, total)
    return value.hexdigest()


def retain_partial(path, reason):
    retained = path.with_name(path.name + '.' + reason + '-' + str(time.time_ns()))
    path.rename(retained)
    return retained


RETRY_DELAYS = (2, 5)


def retry_wait(network, attempt, **details):
    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]
    event(network, action='retry', retry_number=attempt+1, wait_seconds=delay, **details)
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if network.get('resource_check'):
            network['resource_check']()
        time.sleep(min(.2, max(0, deadline-time.monotonic())))


def pause_download(network, name, path, retained, reason, *, details=None):
    event(network, category='models', source=name, file=Path(path).name, action='paused',
          retained_bytes=retained, reason=reason, **({'size_error': details} if details else {}))
    explanation = ''
    if details:
        explanation = '%s: observed %d bytes; allowed %d bytes; complete model %d bytes. ' % (
            details['file'], details['observed_bytes'], details['allowed_bytes'], details['expected_model_bytes'])
    raise RuntimeError('Model download paused: %s (%s). Temporary files retained. '
        '%s'
        'Retry setup to continue the same source. If resume is unavailable, enable '
        '"Allow restarting incomplete downloads" or --allow-model-restart to permit a fresh download.' %
        (Path(path).name, reason, explanation))


class DownloadError(RuntimeError):
    """Every permitted source failed; an alternate artifact may be tried."""


def download(candidates, path, expected, progress=None, *, network=None, env=None, size=None,
             algorithm='sha256', git_blob=False, headers_for=None, stall_seconds=None, category='download',
             low_speed_limit=1024, max_seconds=None, cycles=2, slow_seconds=None, keep_partial=False):
    """Resume across ranked sources; hash before accepting, retain rejected bytes."""
    network = network or {}
    path = Path(path)
    model = category in ('models', 'vdn-models', 'edge-models')
    stall_seconds = stall_seconds if stall_seconds is not None else 120 if model else 20

    def matches(candidate, source='local'):
        if size is not None and candidate.stat().st_size != size:
            return False
        event(network, category=category, source=source, file=path.name, action='verifying', algorithm=algorithm)
        check = network.get('resource_check') if keep_partial else None
        return hash_file(candidate, algorithm, git_blob, discard_cache=category in ('models', 'vdn-models'),
                         progress=(lambda done, total: check()) if check else None) == expected
    if path.is_file():
        if matches(path):
            return
        raise ValueError('Existing file failed integrity check; retained without overwrite: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.partial')
    if partial.is_file() and (size is None or partial.stat().st_size == size):
        if matches(partial):
            from .file_ops import publish
            publish(partial, path)
            return
        if size is not None:
            # A complete-but-corrupt file is not a resumable prefix.
            retain_partial(partial, 'hash-rejected')
    timeout = network.get('timeout_seconds', 5)
    affinity = partial.with_name(partial.name + '.source.json')
    candidates = [(name, url, route) for name, url in candidates
                  for route in route_order(network, category, name, env)]
    if not candidates:
        raise RuntimeError('No download sources configured; existing files retained')
    if model and not network.get('manual_source_switch') and partial.exists() and affinity.is_file():
        try:
            prior = json.loads(affinity.read_text(encoding='utf-8'))
            if prior.get('expected') == expected:
                candidates.sort(key=lambda row: row[0] != prior.get('source'))
        except (OSError, ValueError, AttributeError):
            pass
    # Try the next ranked candidate promptly. The final candidate gets bounded
    # retries; every mirror must honor the retained prefix, with no silent reset.
    measured_rates = {}
    for cycle in range(cycles):
        # Once every route has been tried, keep the fastest observed route even
        # if the user's entire connection is slow. Do not oscillate forever.
        if cycle and slow_seconds is not None:
            candidates.sort(key=lambda row: -measured_rates.get((row[0], row[2]), 0))
        for candidate_index, (name, url, route) in enumerate(candidates):
            for restart in range(3 if model else 2):
                offset = partial.stat().st_size if partial.exists() else 0
                if keep_partial and size is not None and offset == size:
                    if matches(partial, name):
                        from .file_ops import publish
                        publish(partial, path)
                        return
                    retain_partial(partial, 'hash-rejected')
                    offset = 0
                if size and offset > size:
                    retain_partial(partial, 'oversized')
                    offset = 0
                remembered = False
                def remember_source():
                    nonlocal remembered
                    if model and not remembered:
                        from .monitoring import save
                        save(affinity, dict(source=name, expected=expected))
                        remembered = True
                if model and offset == 0:
                    remember_source()
                event(network, category=category, source=name, route=route, file=path.name, action='attempt', method='curl', cycle=cycle, resume_bytes=offset)
                attempt_env = route_environment(network, category, name, env, route)
                command = curl_command(timeout, attempt_env) + ['--output', str(partial), '--write-out', '%{http_code}']
                if not model and slow_seconds is None:
                    command += ['--speed-limit', str(low_speed_limit), '--speed-time', str(stall_seconds)]
                if max_seconds is not None:
                    command += ['--max-time', str(max_seconds)]
                if url.startswith('https://'):
                    command += ['--proto-redir', '=https']
                if size:
                    # curl also applies this cap to intermediate redirect
                    # bodies (e.g. HF Mirror's 307 for a 96-byte JSON file).
                    # Keep transfers bounded; the final file must still match
                    # its exact pinned size AND content hash below.
                    # The cap applies to this response, excluding an existing
                    # prefix. A broken Range reply must not append a full file.
                    command += ['--max-filesize', str(max(size - offset, MIN_TRANSFER_LIMIT))]
                if offset:
                    command += ['--continue-at', str(offset)]
                process = subprocess.Popen(command, env=attempt_env, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_console())
                started = time.monotonic()
                oversized = None
                stalled = False
                slow = False
                last_body, previous_done = started, offset
                recent = deque([(started, offset)])
                try:
                    process.stdin.write(curl_config(url, headers_for(name) if headers_for else ()))
                    process.stdin.close()
                    while process.poll() is None:
                        if network.get('resource_check'):
                            network['resource_check']()
                        done = partial.stat().st_size if partial.exists() else 0
                        if done > previous_done:
                            previous_done, last_body = done, time.monotonic()
                            remember_source()
                        now = time.monotonic()
                        if now-last_body > stall_seconds:
                            stalled = True
                            break
                        if size is not None and done > size:
                            # Also cover chunked responses and curl versions
                            # whose size cap relies on Content-Length. Never
                            # turn these excess bytes into progress or an ETA.
                            oversized = done
                            break
                        recent.append((now, done))
                        window = max(5, slow_seconds or 0)
                        while len(recent) > 2 and recent[1][0] < now - window:
                            recent.popleft()
                        if (slow_seconds is not None and cycle == 0 and len(candidates) > 1
                                and now - recent[0][0] >= slow_seconds
                                and (done - recent[0][1]) / (now - recent[0][0]) < low_speed_limit):
                            slow = True
                            break
                        if progress:
                            sample = next((row for row in recent if row[0] >= now - 5), recent[0])
                            progress(done, size or 0, max(0, done - sample[1]) / max(.001, now - sample[0]))
                        try:
                            process.wait(timeout=.25)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    status = process.stdout.read(16).decode(errors='replace')
                    process.stdout.close()
                current_size = partial.stat().st_size if partial.exists() else 0
                if slow_seconds is not None:
                    measured_rates[name, route] = (max(0, current_size - offset) /
                        max(.001, time.monotonic() - started)) if slow else 0
                if model and current_size > offset:
                    remember_source()
                if oversized is not None:
                    retain_partial(partial, 'oversized-response')
                    source_health(network, category, name, False)
                    event(network, category=category, source=name, file=path.name, action='fallback',
                          method='curl', reason='response-size-exceeded', observed_bytes=oversized,
                          expected_bytes=size, resume_bytes=offset)
                    break
                if process.returncode == 0:
                    if matches(partial, name):
                        from .file_ops import publish
                        publish(partial, path)
                        source_health(network, category, name, True)
                        route_health(network, category, name, route)
                        event(network, category=category, source=name, route=route, file=path.name, action='verified', method='curl', bytes=path.stat().st_size)
                        return
                    retain_partial(partial, 'hash-rejected')
                    source_health(network, category, name, False)
                    event(network, category=category, source=name, file=path.name, action='fallback', reason='integrity-mismatch')
                    break
                if offset and (process.returncode == 33 or process.returncode == 22 and status == '416') and restart == 0:
                    if model or keep_partial:
                        event(network, category=category, source=name, file=path.name,
                              action='resume-refused', resume_bytes=offset, reason='source-does-not-support-range')
                        break
                    # A mirror lacking Range must start over, with previous bytes kept.
                    retain_partial(partial, 'resume-unavailable')
                    continue
                transient = stalled or process.returncode in (5, 6, 7, 18, 28, 35, 52, 55, 56, 92, 95) or status in ('429', '500', '502', '503', '504')
                if model and transient and restart < 2 and candidate_index + 1 == len(candidates) and process.returncode not in (5, 7):
                    retry_wait(network, restart, category=category, source=name, file=path.name,
                               resume_bytes=current_size, reason='no-progress' if stalled else 'connection-interrupted')
                    continue
                reason = 'no-progress' if stalled else 'slow-transfer' if slow else 'curl-' + str(process.returncode)
                alternatives = route_order(network, category, name, env)
                if alternatives.index(route) + 1 < len(alternatives):
                    event(network, category=category, source=name, route=alternatives[alternatives.index(route)+1],
                          file=path.name, action='route-retry', reason=reason, resume_bytes=current_size)
                else:
                    source_health(network, category, name, False)
                    event(network, category=category, source=name, route=route, file=path.name, action='fallback', reason=reason, http_status=status, resume_bytes=current_size)
                break
        if model and partial.exists() and partial.stat().st_size:
            if not network.get('allow_model_restart'):
                pause_download(network, name, path, partial.stat().st_size, 'sources could not continue the retained prefix')
            event(network, category=category, source=name, file=path.name, action='restart-approved',
                  retained_bytes=partial.stat().st_size, reason='resume-unavailable')
            retain_partial(partial, 'restart-approved')
    raise DownloadError('Every download source failed for %s. Check your network or choose another connection mode in Downloads. Partial files retained' % path.name)


def clone(target, url, commit, *, sparse=None, run=None, network=None, env=None):
    """Fetch only the pinned commit, atomically; never reset existing checkouts."""
    target = Path(target)
    network = installed_plan(env) if network is None else network
    env = proxy_environment(env)
    env.update(GIT_TERMINAL_PROMPT='0', GIT_HTTP_LOW_SPEED_LIMIT='1024', GIT_HTTP_LOW_SPEED_TIME='20')
    git = env.get('FREEVIDEO_GIT') or 'git'
    def execute(label, command):
        command = [git, *command[1:]]
        if run:
            return run(label, command, env=env)
        result = subprocess.run(list(map(str, command)), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **hidden_console())
        if result.returncode:
            raise RuntimeError(result.stdout[-2500:])
    if target.exists():
        head = subprocess.check_output([git, '-C', str(target), 'rev-parse', 'HEAD'], env=env, text=True, **hidden_console()).strip()
        dirty = subprocess.check_output([git, '-C', str(target), 'status', '--porcelain', '--untracked-files=no'], env=env, text=True, **hidden_console())
        if head != commit or dirty:
            raise ValueError('Existing dependency differs from the pinned clean source: ' + str(target))
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = urls(dict(network, sources=dict(network.get('sources', {}), github=network.get('sources', {}).get('git', [{'id': 'official'}]))), 'github', url, env)
    configured_env = env
    for name, endpoint, route in [(name, endpoint, route) for name, endpoint in candidates
                                 for route in route_order(network, 'git', name, configured_env)]:
        env = route_environment(network, 'git', name, configured_env, route)
        temporary = target.with_name(target.name + '.partial-' + str(time.time_ns()))
        event(network, category='git', source=name, route=route, file=target.name, action='attempt', commit=commit)
        try:
            execute(target.name + '-init-' + name, ['git', 'init', '--quiet', temporary])
            # Never depend on a user's global CRLF conversion or long-path
            # settings when constructing the exact official patched tree.
            execute(target.name + '-line-endings', ['git', '-C', temporary, 'config', 'core.autocrlf', 'false'])
            execute(target.name + '-long-paths', ['git', '-C', temporary, 'config', 'core.longpaths', 'true'])
            # URL via environment keeps credentials out of process arguments/logs.
            config_index = int(env.get('GIT_CONFIG_COUNT', 0))
            fetch_env = dict(env, GIT_CONFIG_COUNT=str(config_index + 1))
            fetch_env.update({'GIT_CONFIG_KEY_' + str(config_index): 'remote.origin.url',
                              'GIT_CONFIG_VALUE_' + str(config_index): endpoint})
            original = env
            env = fetch_env
            try:
                execute(target.name + '-fetch-' + name, ['git', '-C', temporary, '-c', 'fetch.fsckObjects=true', 'fetch', '--depth=1', '--no-tags', 'origin', commit])
                if sparse:
                    execute(target.name + '-sparse', ['git', '-C', temporary, 'sparse-checkout', 'set', '--no-cone', *sparse])
                execute(target.name + '-checkout', ['git', '-C', temporary, 'checkout', '--detach', commit])
            finally:
                env = original
            actual = subprocess.check_output([git, '-C', str(temporary), 'rev-parse', 'HEAD'], env=env, text=True, **hidden_console()).strip()
            if actual != commit:
                raise ValueError('Downloaded source did not match its pinned commit')
            temporary.rename(target)
            source_health(network, 'git', name, True)
            route_health(network, 'git', name, route)
            event(network, category='git', source=name, route=route, file=target.name, action='verified', commit=commit)
            return target
        except RuntimeError as error:
            if not retryable(error):
                raise
            source_health(network, 'git', name, False)
            event(network, category='git', source=name, route=route, file=target.name, action='fallback', reason='network-fetch-failed')
    raise RuntimeError('All Git sources failed. Check your network or connection mode in Downloads. Incomplete checkouts retained beside ' + str(target))
