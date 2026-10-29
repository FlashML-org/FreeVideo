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
import tempfile
from types import SimpleNamespace
from . import execution_paths
from . import subprocess_output
from .windows_ux import external_python, hidden_console
import threading
import time
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit
from . import proxy
from .long_paths import io_path, is_windows, path_chars, plain, plain_errors
from .system import curl_executable, curl_ca_arguments, missing_curl_message, windows


SOURCES = {
    'pypi': {'official': 'https://pypi.org/simple', 'tuna': 'https://pypi.tuna.tsinghua.edu.cn/simple'},
    'torch': {'official': 'https://download.pytorch.org/whl', 'nju': 'https://mirrors.nju.edu.cn/pytorch/whl'},
    'models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com'},
    'vdn-models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com',
                   'modelscope': 'https://www.modelscope.cn'},
    'edge-models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com',
                    'modelscope': 'https://www.modelscope.cn'},
    'image-models': {'official': 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com',
                     'modelscope': 'https://www.modelscope.cn'},
    'github': {'official': 'https://github.com', 'ghfast': 'https://ghfast.top/https://github.com'},
    'cuda': {'official': 'https://developer.download.nvidia.com/compute/cuda/redist',
             'nvidia-cn': 'https://developer.download.nvidia.cn/compute/cuda/redist'},
}
PROXY_KEYS = proxy.PROXY_KEYS
SAMPLE_BYTES = 256 * 1024
SPEED_SAMPLE_BYTES = 32 * 2**20
SPEED_SAMPLE_SECONDS = 3.
MIN_TRANSFER_LIMIT = 1024 * 1024
# A transfer that is moving but slowly is compared with the untried sources: after
# SWITCH_AFTER_SECONDS the others get one bounded speed sample and the transfer
# moves (resuming the same partial) when that saves a real amount of time.
SWITCH_AFTER_SECONDS = 15.
SWITCH_RECHECK_SECONDS = 180.
SWITCH_MAX_CHECKS = 3
SWITCH_MIN_REMAINING_SECONDS = 90.
SWITCH_MIN_SAVING_SECONDS = 60.
SWITCH_MIN_GAIN = 2.
# One quick pass over sources/routes, followed by at most one patient pass.
# These are inactivity timeouts, not deadlines for an entire installation.
DOWNLOAD_RETRY_PROFILES = (('fast', '20', '1'), ('patient', '120', '5'))
PATIENT_DOWNLOAD_NOTICE = 'The network is slow. Downloading again; this may take a while.'


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


@plain_errors
def model_spec(spec=None):
    return (spec if spec is not None else
            json.loads(io_path(Path(__file__).with_name('dependencies.json')).read_text(encoding='utf-8')))['models']


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
    if row.get('family') == 'image':
        return 'image-models'
    mirror = modelscope_mapping(row, spec)
    return mirror['family'] if mirror else 'models'


def model_url(row, name, env=None, *, spec=None):
    if row.get('family') == 'image':
        return next(s['url'] for s in row['sources'] if s['id'] == name)
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
    if family != 'image-models' and family not in network.get('sources', {}):
        family = 'models'
    names = ['official'] if network.get('mode') == 'official' or row.get('official_only') else ordered(network, family)
    if family == 'image-models' and network.get('mode') != 'official' and not row.get('official_only'):
        available = {s['id'] for s in row['sources']}
        names = [n for n in names if n in available]
        names += [s['id'] for s in row['sources'] if s['id'] not in names]
    return [(name, model_url(row, name, env, spec=spec)) for name in names]


def curl_config(url, headers=()):
    def quote(value):
        if any(c in str(value) for c in ('\n', '\r', '\x00')):
            raise ValueError('Invalid newline in network configuration')
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
    return ('url = ' + quote(url) + '\n' + ''.join('header = ' + quote(h) + '\n' for h in headers)).encode()


def curl_command(timeout, env=None):
    executable = curl_executable(env)
    if executable is None:
        raise RuntimeError(missing_curl_message(env))
    command = [executable, '--config', '-', '--silent', '--show-error', '--no-location-trusted', '--location',
            '--max-redirs', '5', '--fail', '--proto', '=https,http', '--proto-redir', '=https,http',
            '--connect-timeout', str(timeout), '--user-agent', 'FreeVideo-Setup/1']
    command += curl_ca_arguments(executable, env)
    if '--proxy-cacert' not in command and (env or {}).get('FREEVIDEO_CURL_PROXY_CA'):
        command += ['--proxy-cacert', env['FREEVIDEO_CURL_PROXY_CA']]
    if (env or {}).get('FREEVIDEO_PROXY_ROUTE') == 'direct':
        command += ['--proxy', '', '--noproxy', '*']
    return command


def curl_process(command, **kwargs):
    # Match discovery's environment: the frozen GUI's DLL directory can break
    # otherwise healthy external curl binaries (including Git's copy).
    try:
        with external_python():
            return execution_paths.popen(command, **kwargs, **hidden_console())
    except OSError as error:
        if not windows():
            raise
        code = 'CURL_START_DENIED' if isinstance(error, PermissionError) else 'CURL_START_FAILED'
        raise RuntimeError('[%s] curl could not start; exception=%s, winerror=%s, errno=%s. '
                           'Retry installation or export the report.' %
                           (code, type(error).__name__, getattr(error, 'winerror', None), error.errno)) from error


def sample(url, timeout=5, limit=SAMPLE_BYTES, *, ranged=True, env=None, headers=()):
    """Read at most limit bytes; a hard curl deadline bounds DNS and slow reads."""
    command = curl_command(timeout, env) + ['--max-time', str(timeout)]
    if url.startswith('https://'):
        command += ['--proto-redir', '=https']
    if ranged:
        command += ['--range', '0-' + str(limit - 1)]
    config = curl_config(url, headers)
    started = time.monotonic()
    process = curl_process(command, env=proxy_environment(env), stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
    process = curl_process(command, env=proxy_environment(env), stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
        elif family == 'image-models':
            from .image_gen.image_pack import rows
            url = model_url(rows()[0], name, env)
        elif family in ('vdn-models', 'edge-models'):
            settings = spec['models']
            mirror = settings[family.split('-')[0] + '_modelscope']
            url = model_url({'repo': mirror['repo'], 'revision': mirror['hf_revision'],
                             'file': mirror['probe_file']}, name, env, spec=spec)
        elif family == 'github':
            url = versions.get('github_probe_url', versions['uv']['url']).replace(SOURCES['github']['official'], base, 1)
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
            origin_proxy = execution_paths.run(['git', 'config', '--get', 'remote.origin.proxy'], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, **hidden_console())
            if origin_proxy.returncode == 0:
                settings['remote.' + remote + '.proxy'] = subprocess_output.decode_output(origin_proxy.stdout).rstrip('\r\n')
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
        if (family in ('models', 'vdn-models', 'edge-models', 'image-models') and name == 'official'
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
         model_only=False, measure_speed=True, progress=None, proxy_mode='auto', git_available=True):
    from .environments import ENVIRONMENTS, environment_names
    env = proxy_environment(env)
    started = time.monotonic()
    if proxy_mode not in proxy.MODES:
        raise ValueError('Unknown download connection mode')
    proxy_configured = proxy.configured(env)
    git_keys = [] if offline or model_only or not git_available else proxy.git_proxy_keys(env)
    families = ['pypi', 'models', 'vdn-models', 'image-models', 'github', 'git', 'cuda']
    if spec['models'].get('edge_modelscope'):
        families.insert(3, 'edge-models')
    if versions.get('target_system') in ('Windows', 'Darwin'):
        families.remove('cuda')
    for name in (() if versions.get('target_system') == 'Darwin' else environment_names(layout)):
        role = ENVIRONMENTS[name]
        family = 'torch-' + role['cuda'] + '-' + role['torch'][0].split('==')[1]
        if family not in families:
            families.append(family)
    if model_only:
        families=[family for family in families if family in ('models','vdn-models','edge-models','image-models')]
    selections, jobs = {}, []
    for family in families:
        group = 'torch' if family.startswith('torch-') else 'github' if family == 'git' else family
        names = ['official'] if mode == 'official' else list(SOURCES[group])
        if mode != 'official' and configured_source(group, env):
            names.insert(0, 'user')
        selections[family] = []
        routes = proxy.routes(env, git=family == 'git' and git_available, mode=proxy_mode)
        jobs += [(family, name, route) for name in names for route in routes]
    # Without a usable Git, any `git` on PATH may be Apple's tool launcher,
    # which opens an installation dialog when run. Leave those sources unmeasured.
    skipped = [job for job in jobs if job[0] == 'git' and not git_available and not offline]
    if skipped:
        jobs = [job for job in jobs if job not in skipped]
        for family, name, route in skipped:
            selections[family].append({'id': name, 'route': route, 'ok': None, 'score': 0, 'scope': 'Git not available yet; not measured'})
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
    value = re.sub(r'(?<![A-Za-z0-9_:/])(?:(?P<relative>\.{1,2}/[^\s"\'<>;,)\]}]*)|'
                   r'(?:[A-Za-z]:[\\/]|/)([^\s"\'<>;,)\]}]*))',
                   lambda match: match.group('relative') if match.group('relative') is not None
                   and not re.search(r'[\\/]', match.group('relative').split('/', 1)[1]) else
                   '<PATH>/' + re.split(r'[\\/]', match.group(0).rstrip('\\/'))[-1], value)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    detail = '\n'.join(lines[-12:])[-4096:] if lines else 'no package-manager output'
    summary = next((line for line in lines if line), detail).strip()[:320]
    return detail, summary


def _package_attempt(error, source, route):
    detail, summary = _package_failure(error)
    return dict(source=source, route=route, error_type=type(error).__name__,
                returncode=getattr(error, 'returncode', None), detail=detail, summary=summary)


def download_environment(environ, retry_mode='fast'):
    """Apply shared uv/pip defaults without replacing any user setting."""
    _, timeout, retries = next(row for row in DOWNLOAD_RETRY_PROFILES if row[0] == retry_mode)
    env = dict(environ)
    for key, value in (('UV_HTTP_TIMEOUT', timeout), ('UV_HTTP_RETRIES', retries),
                       ('PIP_DEFAULT_TIMEOUT', timeout), ('PIP_RETRIES', retries)):
        env.setdefault(key, value)
    return env


# Only a validation failure counts: a package or service that merely has
# "checksum" in its name must stay eligible for the slow-network retry. Keep in
# sync with fv_slow_download_failure in scripts/bootstrap_linux.sh.
_INTEGRITY_FAILURE = re.compile(
    r"\b(?:invalid|bad|wrong|incorrect|mismatched?)\s+checksums?\b"
    r"|\bchecksums?\s+(?:mismatch|error|failed|failure)\b"
    r"|\bchecksums?\s+(?:verification|validation)\s+(?:failed|failure|error)\b"
    r"|\bchecksums?\s+(?:does\s+not|doesn't|did\s+not|didn't|do\s+not|don't)\s+match\b"
    r"|\b(?:does\s+not|doesn't|did\s+not|didn't|do\s+not|don't)\s+match\s+(?:the\s+|its\s+|expected\s+)?(?:checksums?|hash(?:es)?)\b"
    r"|\bhash\s+(?:mismatch|check\s+failed|does\s+not\s+match)\b|\bhashes\s+(?:do\s+not|didn't)\s+match\b"
    r"|\bsha-?256\s+mismatch\b|\bdigest\s+mismatch\b"
    r"|\bintegrity\s+(?:check|verification)\s+failed\b")


def _download_integrity_failure(detail):
    # URL paths may contain validation words without a validation failure.
    detail = re.sub(r'https?://\S+', '', detail.lower())
    return bool(_INTEGRITY_FAILURE.search(detail))


def slow_download_failure(detail):
    """Mirror fallback is broader than permission to retry a whole slow pass."""
    return (connection_failure(detail) and not _download_integrity_failure(detail)
            and not re.search(r'\b(?:401|403|404|410)\b', detail)
            and not any(word in detail.lower() for word in
                        ('certificate verify failed', 'certificate_verify_failed',
                         'invalid peer certificate', 'unknown issuer', 'not found',
                         'resolutionimpossible', 'failed to build', 'invalid wheel',
                         'permission denied', 'no space left on device', 'disk full')))


def package_environment(network, family, name, environ=None, *, route=None, retry_mode='fast'):
    env = route_environment(network, family, name, environ, route)
    env = download_environment(env, retry_mode)
    endpoint = source_url(family, name, environ)
    if family.startswith('torch-'):
        endpoint += '/' + family.split('-')[1]
    env.update(UV_DEFAULT_INDEX=endpoint, UV_INDEX_URL=endpoint, PIP_INDEX_URL=endpoint, UV_NO_CONFIG='1')
    for key in ('UV_INDEX', 'UV_EXTRA_INDEX_URL', 'PIP_EXTRA_INDEX_URL'):
        env.pop(key, None)
    return env


def download_command(network, family, run, env=None, *, category=None, extra_sources=(), notice=None):
    """Run a source/route callback with the same bounded policy for uv and pip.

    Callbacks receive (source, route, retry_mode); each builds its child env from
    the original environment, so our fast defaults never become user overrides.
    """
    category = category or family
    operation = 'python' if category == 'python' else 'package'
    attempts = []
    for retry_mode, timeout, retries in DOWNLOAD_RETRY_PROFILES:
        if retry_mode == 'patient':
            if not attempts or not all(slow_download_failure(row['detail']) for row in attempts):
                break
            event(network, category=category, action='download-round-result', retry_mode='fast',
                  result='failed', attempts=len(attempts))
            event(network, category=category, action='download-patient-retry', retry_mode=retry_mode,
                  default_timeout_seconds=int(timeout), default_retries=int(retries))
            if notice is None:
                print(PATIENT_DOWNLOAD_NOTICE, flush=True)
            else:
                notice(PATIENT_DOWNLOAD_NOTICE)
        round_start = len(attempts)
        retry_details = {'retry_mode': retry_mode} if retry_mode == 'patient' else {}
        if retry_mode == 'patient':
            event(network, category=category, action='download-round-start', retry_mode=retry_mode)
        # Re-read health and allowed routes after the fast pass. Equal failures
        # retain the existing probe order; no new connectivity probes are made.
        for name in [*extra_sources, *ordered(network, family)]:
            for route in route_order(network, family, name, env):
                if operation == 'package' or retry_mode == 'patient':
                    event(network, category=category, source=name, route=route,
                          action=operation + '-attempt', **retry_details)
                try:
                    result = run(name, route, retry_mode)
                    source_health(network, family, name, True)
                    route_health(network, family, name, route)
                    if retry_mode == 'patient':
                        event(network, category=category, action='download-round-result', retry_mode=retry_mode,
                              result='success', attempts=len(attempts)-round_start+1)
                    return result
                except (RuntimeError, subprocess.CalledProcessError) as error:
                    attempt = dict(_package_attempt(error, name, route), retry_mode=retry_mode)
                    attempts.append(attempt)
                    event(network, category=category, source=name, route=route, action=operation + '-failed',
                          error_type=attempt['error_type'], returncode=attempt['returncode'],
                          detail=attempt['detail'], summary=attempt['summary'], **retry_details)
                    detail = attempt['detail']
                    if (not retryable(detail)
                            or (retry_mode == 'patient' and not slow_download_failure(detail))):
                        if retry_mode == 'patient':
                            event(network, category=category, action='download-round-result', retry_mode=retry_mode,
                                  result='failed', attempts=len(attempts)-round_start, reason='non-network-error')
                        # Retain original build/configuration/integrity errors.
                        try:
                            error.package_attempts = attempts
                        except Exception:
                            pass
                        raise
                    event(network, category=category, source=name, route=route, action=operation + '-fallback',
                          reason='network-or-unavailable-pinned-package', **retry_details)
                    if not connection_failure(detail):
                        break
            source_health(network, family, name, False)
        if retry_mode == 'patient':
            event(network, category=category, action='download-round-result', retry_mode=retry_mode,
                  result='failed', attempts=len(attempts)-round_start)
    raise PackageSourcesError(category, attempts)


def package_command(network, family, run, env=None, *, notice=None):
    def attempt(name, route, retry_mode):
        attempt_env = package_environment(network, family, name, env, route=route, retry_mode=retry_mode)
        # Private markers retain per-source/route logs; no package configuration.
        attempt_env['FREEVIDEO_PACKAGE_SOURCE'] = name
        attempt_env['FREEVIDEO_PACKAGE_ROUTE'] = route
        return run(attempt_env, name)
    return download_command(network, family, attempt, env, notice=notice)


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
        'Retry setup to continue the same source. If resume is unavailable, run setup '
        'with --allow-model-restart to download the file again from the start.' %
        (Path(path).name, reason, explanation))


class DownloadError(RuntimeError):
    """Every permitted source failed; an alternate artifact may be tried."""


def _faster_source(compare, now, started, offset, done, size, alternatives, timeout, env, network, category, headers_for,
                   full_restart=False):
    """Return the untried candidate worth moving to, or None. Never blocks the transfer loop."""
    thread = compare['thread']
    if thread is None:
        if now < compare['at']:
            return None
        rate = (done - offset) / max(.001, now - started)
        remaining = size - done
        if rate <= 0 or remaining / rate < SWITCH_MIN_REMAINING_SECONDS:
            compare['at'] = now + SWITCH_RECHECK_SECONDS
            return None
        compare['current'], compare['remaining'], compare['rates'] = rate, remaining, {}
        compare['checks'] += 1
        names = {}
        for row in alternatives:
            names.setdefault(row[0], row)

        def measure(row):
            name, url, route = row
            try:
                headers = headers_for(name) if headers_for else ()
                _, result = speed_sample(url, timeout, env=route_environment(network, category, name, env, route),
                                         headers=headers)
                value = result.get('bytes_per_second') if result.get('speed_measured') else None
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                value = None
            if value:
                compare['rates'][row[0], row[2]] = value
        compare['thread'] = threading.Thread(target=lambda: [measure(r) for r in list(names.values())[:3]], daemon=True)
        compare['thread'].start()
        return None
    if thread.is_alive():
        return None
    compare['thread'] = None
    compare['at'] = now + SWITCH_RECHECK_SECONDS
    best = max(compare['rates'].items(), key=lambda item: item[1], default=None)
    if best is None:
        return None
    rate, remaining = compare['current'], compare['remaining']
    if best[1] >= SWITCH_MIN_GAIN * rate and remaining / rate - (size if full_restart else remaining) / best[1] >= SWITCH_MIN_SAVING_SECONDS:
        return next((row for row in alternatives if (row[0], row[2]) == best[0]), None)
    return None



def _redirect_fields(text):
    """curl's http_code and redirect_url output as (status, url); Windows curl ends its lines with CRLF."""
    status, _, destination = text.partition('\n')
    return status.strip(), destination.strip()


DOWNLOAD_SIBLING_CHARS = 21  # '.partial.source.json' beside the file


def move_file(source, destination):
    """Move one file by the long-path entry; across volumes copy it and remove the source."""
    source, destination = io_path(source), io_path(destination)
    try:
        source.replace(destination)
    except OSError:
        shutil.copy2(source, destination)
        source.unlink()


def download(candidates, path, expected, *args, **options):
    """Fetch into `path`; where curl cannot name it (Windows, beyond 259 characters), in a short folder.

    The file and what resumes it (partial bytes, source record) go to the short
    folder first, and whatever is left there comes back, also after a failure.
    """
    path = Path(path)
    scratch = None
    if is_windows() and path_chars(plain(path)) + DOWNLOAD_SIBLING_CHARS > GIT_PATH_LIMIT:
        scratch = git_scratch_folder()
    if scratch is None:
        return download_here(candidates, path, expected, *args, **options)
    try:
        io_path(path.parent).mkdir(parents=True, exist_ok=True)
        for entry in io_path(path.parent).iterdir():
            if entry.name == path.name or entry.name.startswith(path.name + '.partial'):
                move_file(entry, scratch / entry.name)
        return download_here(candidates, scratch / path.name, expected, *args, **options)
    finally:
        for entry in io_path(scratch).iterdir():
            move_file(entry, path.parent / entry.name)
        remove_tree(scratch)


def download_here(candidates, path, expected, progress=None, *, network=None, env=None, size=None,
             algorithm='sha256', git_blob=False, headers_for=None, stall_seconds=None, category='download',
             low_speed_limit=1024, max_seconds=None, cycles=2, slow_seconds=None, keep_partial=False,
             validate_url=None, verify=None, fallbacks=None):
    """Resume ranked sources; defaults retain rejected bytes.

    validate_url checks each redirect before curl can follow it; verify runs
    after the digest and before publication. fallbacks maps a source name to
    an alternate (url, headers) within the same source/route retry round.
    """
    network = network or {}
    path = Path(path)
    updating = category == 'launcher-update'
    integrity_failed = False
    attempts = {}

    def note_failure(source, route, reason, status=''):
        # The last reason each source and route gave, for the final error.
        attempts.pop((source, route), None)  # a repeat moves to the end, so the last 8 are the latest
        attempts[source, route] = reason + (' (HTTP %s)' % status if str(status).isdigit() and status != '000' else '')

    failed_sources = set()
    transferred, high_water, last_rate = 0, 0, 0.
    transfer_samples = deque([(time.monotonic(), 0)])

    def update_progress(done, added):
        nonlocal transferred, high_water, last_rate
        now = time.monotonic()
        transferred += added
        transfer_samples.append((now, transferred))
        while len(transfer_samples) > 2 and transfer_samples[1][0] < now - 5:
            transfer_samples.popleft()
        seconds = now - transfer_samples[0][0]
        last_rate = (transferred-transfer_samples[0][1]) / max(.001, seconds)
        # A clean retry keeps the visible bar. Completion is reported only
        # after the hash and optional caller verification have both succeeded.
        high_water = max(high_water, min(done, size-1) if size else done)
        if progress:
            progress(high_water, size or 0, last_rate)

    def reject(candidate, reason):
        if updating:
            candidate.unlink(missing_ok=True)
        else:
            retain_partial(candidate, reason)

    def publish(candidate):
        check = network.get('resource_check')
        if updating and check:
            check()
        from .file_ops import publish as move
        move(candidate, path)
        if updating and progress:
            progress(path.stat().st_size, size or 0, last_rate)

    model = category in ('models', 'vdn-models', 'edge-models', 'image-models')
    stall_seconds = stall_seconds if stall_seconds is not None else 120 if model else 20

    def matches(candidate, source='local'):
        if size is not None and candidate.stat().st_size != size:
            return False
        event(network, category=category, source=source, file=path.name, action='verifying', algorithm=algorithm)
        check = network.get('resource_check') if keep_partial or updating else None
        from .install_activity import byte_progress
        report = byte_progress('verify_progress', path.name, report=lambda **row:
            event(network, category=category, source=source, action='verify-progress', **row))
        def verification(done, total):
            if check:
                check()
            if model:
                report(done, total)
        matched = hash_file(candidate, algorithm, git_blob, discard_cache=category in ('models', 'vdn-models', 'image-models'),
                            progress=verification if model or check else None) == expected
        if matched and verify is not None:
            matched = verify(candidate)
        return matched
    if path.is_file():
        if matches(path):
            return
        raise ValueError('Existing file failed integrity check; retained without overwrite: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.partial')
    if partial.is_file() and (size is None or partial.stat().st_size == size):
        if matches(partial):
            publish(partial)
            return
        if size is not None:
            # A complete-but-corrupt file is not a resumable prefix.
            reject(partial, 'hash-rejected')
    timeout = network.get('timeout_seconds', 5)
    affinity = partial.with_name(partial.name + '.source.json')
    candidates = [(name, address, route, headers, (fallback, rank)) for name, url in candidates
                  for fallback, (address, headers) in enumerate([(url, ())] +
                      ([(fallbacks[name][0], tuple(fallbacks[name][1]))] if fallbacks and name in fallbacks else []))
                  for rank, route in enumerate(route_order(network, category, name, env))]
    if updating:
        # Every source's own address on its first route comes before any other
        # route or alternate address, so a slow source is left after one attempt.
        candidates.sort(key=lambda row: row[4])
    candidates = [row[:4] for row in candidates]
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
    left_slow = set()
    for cycle in range(cycles):
        # Once every route has been tried, keep the fastest observed route even
        # if the user's entire connection is slow. Do not oscillate forever.
        if cycle and updating and not left_slow:
            break  # No source was left for being slow, so none is worth resuming.
        if cycle and measured_rates:
            candidates.sort(key=lambda row: -measured_rates.get((row[0], row[2]), 0))
        for candidate_index, (name, original_url, route, extra_headers) in enumerate(candidates):
            if name in failed_sources:
                continue
            url, redirects, restart = original_url, 0, 0
            # Redirects are individual curl requests when a caller supplies a
            # policy. Never let curl contact an unchecked redirect destination.
            for attempt in range(16 if validate_url else (3 if model else 2)):
                if updating and network.get('resource_check'):
                    network['resource_check']()
                if validate_url:
                    try:
                        validate_url(url)
                    except ValueError:
                        failed_sources.add(name)
                        break
                offset = partial.stat().st_size if partial.exists() else 0
                if keep_partial and size is not None and offset == size:
                    if matches(partial, name):
                        publish(partial)
                        return
                    reject(partial, 'hash-rejected')
                    offset = 0
                if size and offset > size:
                    reject(partial, 'oversized')
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
                if validate_url:
                    command += ['--disable', '--max-redirs', '0', '--write-out', '%{http_code}\n%{redirect_url}']
                    # --disable must be the first option to suppress .curlrc.
                    command.insert(1, command.pop(command.index('--disable')))
                if not model and slow_seconds is None and not updating:
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
                process = curl_process(command, env=attempt_env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                started = time.monotonic()
                oversized = None
                stalled = False
                slow = False
                faster = None
                compare = dict(thread=None, rates={}, checks=0, at=started + SWITCH_AFTER_SECONDS, current=0.)
                alternatives_ahead = [row[:3] for row in candidates[candidate_index+1:] if row[0] != name]
                last_body, previous_done = started, offset
                reported_done = offset
                recent = deque([(started, offset)])
                try:
                    process.stdin.write(curl_config(url,
                        tuple(headers_for(name) if headers_for else ()) + extra_headers
                        if urlsplit(url).netloc == urlsplit(original_url).netloc else ()))
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
                        if updating and done == offset:
                            # An update's speed is measured from its first body byte;
                            # a source that sends nothing is left by the stall rule.
                            recent = deque([(now, done)])
                        else:
                            recent.append((now, done))
                        window = max(5, slow_seconds or 0)
                        while len(recent) > 2 and recent[1][0] < now - window:
                            recent.popleft()
                        if (slow_seconds is not None and cycle == 0 and len(candidates) > 1
                                and now - recent[0][0] >= slow_seconds
                                and (done - recent[0][1]) / (now - recent[0][0]) < low_speed_limit):
                            slow = True
                            left_slow.add((name, route))
                            break
                        # Speed samples follow redirects themselves; guarded updates
                        # must keep every request inside the caller's URL policy.
                        if (not validate_url and cycle == 0 and size and alternatives_ahead and compare['checks'] < SWITCH_MAX_CHECKS
                                and not network.get('manual_source_switch')):
                            faster = _faster_source(compare, now, started, offset, done, size, alternatives_ahead,
                                                    timeout, env, network, category, headers_for)
                            if faster is not None:
                                break
                        if progress:
                            if updating:
                                update_progress(done, max(0, done-reported_done))
                                reported_done = done
                            else:
                                sample = next((row for row in recent if row[0] >= now - 5), recent[0])
                                progress(done, size or 0, max(0, done - sample[1]) / max(.001, now - sample[0]))
                        try:
                            process.wait(timeout=.25)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    thread = compare['thread']
                    if thread is not None and thread.is_alive():
                        thread.join(timeout=timeout + SPEED_SAMPLE_SECONDS + 3)
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    if not process.stdin.closed:
                        process.stdin.close()
                    status = process.stdout.read(65536 if validate_url else 16).decode(errors='replace')
                    process.stdout.close()
                    # --silent --show-error keeps this to a line or two.
                    curl_message = _package_failure(SimpleNamespace(stderr=process.stderr.read(2048)))[1] if process.returncode else ''
                    process.stderr.close()
                if validate_url:
                    status, destination = _redirect_fields(status)
                    if process.returncode == 47 and status in ('301', '302', '303', '307', '308'):
                        redirects += 1
                        if not destination or redirects > 5:
                            failed_sources.add(name)
                            break
                        url = urljoin(url, destination)
                        continue
                current_size = partial.stat().st_size if partial.exists() else 0
                if slow_seconds is not None:
                    # A later failure at another address of the same source and
                    # route must not erase the rate it was measured at.
                    measured_rates[name, route] = max(measured_rates.get((name, route), 0), (
                        max(0, current_size - offset) / max(.001, time.monotonic() - started)) if slow else 0)
                if faster is not None:
                    measured_rates[name, route] = compare['current']
                    measured_rates.update(compare['rates'])
                    # The faster source is tried next, with the retained prefix.
                    position = next(i for i, row in enumerate(candidates) if row[:3] == faster)
                    candidates.insert(candidate_index + 1, candidates.pop(position))
                if model and current_size > offset:
                    remember_source()
                if oversized is not None or (updating and process.returncode == 63):
                    if partial.exists():
                        reject(partial, 'oversized-response')
                    if updating:
                        integrity_failed = True
                        failed_sources.add(name)
                    source_health(network, category, name, False)
                    note_failure(name, route, 'response-size-exceeded')
                    event(network, category=category, source=name, file=path.name, action='fallback',
                          method='curl', reason='response-size-exceeded', observed_bytes=oversized,
                          expected_bytes=size, resume_bytes=offset)
                    break
                if updating and (size is None or current_size <= size):
                    update_progress(current_size, max(0, current_size-reported_done))
                if process.returncode == 0:
                    if matches(partial, name):
                        publish(partial)
                        if network.get('download_settings_path'):
                            from .download_settings import record_speed
                            record_speed(Path(network['download_settings_path']).parent,
                                         max(0, current_size - offset), time.monotonic() - started)
                        source_health(network, category, name, True)
                        route_health(network, category, name, route)
                        event(network, category=category, source=name, route=route, file=path.name, action='verified', method='curl', bytes=path.stat().st_size)
                        return
                    reject(partial, 'hash-rejected')
                    if updating:
                        integrity_failed = True
                        # A corrupt retained prefix must not poison the good
                        # source. Give this source one clean fetch, then move on.
                        if offset and restart == 0:
                            restart += 1
                            continue
                        failed_sources.add(name)
                    source_health(network, category, name, False)
                    note_failure(name, route, 'integrity-mismatch')
                    event(network, category=category, source=name, file=path.name, action='fallback', reason='integrity-mismatch')
                    break
                if offset and (process.returncode == 33 or process.returncode == 22 and status == '416') and restart == 0:
                    if model or keep_partial:
                        note_failure(name, route, 'source-does-not-support-range')
                        event(network, category=category, source=name, file=path.name,
                              action='resume-refused', resume_bytes=offset, reason='source-does-not-support-range')
                        break
                    # A mirror lacking Range must start over, with previous bytes kept.
                    reject(partial, 'resume-unavailable')
                    restart += 1
                    continue
                # A connection that was moving bytes and then ended with an
                # unclassified exit is an interruption, not a refusal.
                interrupted = process.returncode not in (0, 22, 33, 63) and current_size > offset
                transient = (stalled or interrupted or process.returncode in (5, 6, 7, 18, 28, 35, 52, 55, 56, 92, 95)
                             or status in ('429', '500', '502', '503', '504'))
                if model and transient and restart < 2 and candidate_index + 1 == len(candidates) and process.returncode not in (5, 7):
                    retry_wait(network, restart, category=category, source=name, file=path.name,
                               resume_bytes=current_size, reason='no-progress' if stalled else 'connection-interrupted')
                    restart += 1
                    continue
                if updating and stalled:
                    failed_sources.add(name)
                reason = 'no-progress' if stalled else 'slow-transfer' if slow else 'faster-source' if faster else 'curl-' + str(process.returncode)
                note_failure(name, route, reason, status)
                alternatives = route_order(network, category, name, env)
                if alternatives.index(route) + 1 < len(alternatives):
                    event(network, category=category, source=name, route=alternatives[alternatives.index(route)+1],
                          file=path.name, action='route-retry', reason=reason, resume_bytes=current_size)
                else:
                    source_health(network, category, name, False)
                    event(network, category=category, source=name, route=route, file=path.name, action='fallback', reason=reason, http_status=status, resume_bytes=current_size,
                          **(dict(detail=curl_message) if curl_message else {}))
                break
        if model and partial.exists() and partial.stat().st_size:
            if not network.get('allow_model_restart'):
                pause_download(network, name, path, partial.stat().st_size, 'sources could not continue the retained prefix')
            event(network, category=category, source=name, file=path.name, action='restart-approved',
                  retained_bytes=partial.stat().st_size, reason='resume-unavailable')
            retain_partial(partial, 'restart-approved')
    message = 'Every download source failed for %s. Check your network or choose another connection mode in Downloads. Partial files retained' % path.name
    if attempts:
        message += '. Attempts: ' + '; '.join('%s/%s: %s' % (source, route, reason)
                                              for (source, route), reason in list(attempts.items())[-8:])
    error = DownloadError(message)
    error.attempts = [dict(source=source, route=route, reason=reason) for (source, route), reason in attempts.items()]
    error.integrity = integrity_failed
    raise error


# clone() passes the remote through GIT_CONFIG_PARAMETERS and writes the sparse
# patterns itself instead of using GIT_CONFIG_COUNT (Git 2.31) or
# `sparse-checkout set --no-cone` (Git 2.35), so the Git that current Linux
# distributions ship works. MIN_GIT is the oldest Git this sequence is tested on.
MIN_GIT = (2, 17)


def git_version(executable, env=None):
    """(major, minor) of a Git executable, or None when it cannot be run or read."""
    try:
        output = subprocess_output.run_external([str(executable), '--version'], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=30, **hidden_console()).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    found = re.search(r'git version (\d+)\.(\d+)', output or '')
    return (int(found[1]), int(found[2])) if found else None


def old_git_message(version):
    return ('Git %d.%d is too old to download the pinned sources; FreeVideo needs Git %d.%d or newer. '
            'Click Retry installation to use FreeVideo\'s own Git.' % (*version, *MIN_GIT))


def git_parameter(value):
    """A `-c` setting quoted as Git quotes it in GIT_CONFIG_PARAMETERS."""
    return "'" + value.replace("'", "'\\''").replace('!', "'\\!'") + "'"


# Git for Windows cannot find a repository from a working folder deeper than
# the ordinary path limit, with or without core.longpaths, and a junction does
# not help: the folder it reports is the real one. Git therefore works in a short
# real scratch folder and the finished checkout is moved into place.
GIT_FOLDER_RESERVE = 140
GIT_SCRATCH_BASE_CHARS = 80
GIT_PATH_LIMIT = 259


def git_folder_too_deep(path):
    return is_windows() and path_chars(plain(path)) + GIT_FOLDER_RESERVE > GIT_PATH_LIMIT


def git_scratch_folder():
    """A new short real folder for Git to work in, or None where none can be made."""
    bases = [tempfile.gettempdir()]
    for key in ('ProgramData', 'LOCALAPPDATA'):
        if os.environ.get(key):
            bases.append(os.path.join(os.environ[key], 'FV'))
    for base in bases:
        if path_chars(plain(base)) > GIT_SCRATCH_BASE_CHARS:
            continue
        try:
            os.makedirs(base, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix='fvgit-', dir=base))
        except OSError:
            continue
    return None


def remove_tree(path):
    def writable(function, name, _):
        os.chmod(name, 0o700)
        function(name)
    shutil.rmtree(io_path(path), onerror=writable)


def relocate(source, destination):
    """Move a finished folder; where a rename cannot (another volume) copy it."""
    source, destination = io_path(source), io_path(destination)
    try:
        source.rename(destination)
        return
    except OSError:
        pass
    try:
        shutil.copytree(source, destination, symlinks=True)
    except BaseException:
        if destination.exists():
            remove_tree(destination)
        raise
    remove_tree(source)


# Checks compare content only: a copy, restore or drive that changes permission
# bits, or a global line-ending setting, must not make a pinned checkout look
# edited. These settings apply to that one Git command and are never stored.
CONTENT_ONLY = ('-c', 'core.filemode=false', '-c', 'core.autocrlf=false')


def checkout_environment(path, env):
    """Git environment that reads the repository at `path` itself, never one around it."""
    env = dict(os.environ if env is None else env)
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY'):
        env.pop(name, None)
    # Without its own .git/refs (a backup that drops empty folders), Git would
    # otherwise climb to an enclosing repository and report that one's HEAD.
    env['GIT_CEILING_DIRECTORIES'] = os.path.dirname(os.path.abspath(str(path)))
    return env


def sparse_pathspecs(patterns):
    """Pathspecs equal to anchored, literal sparse patterns, or None when they cannot be expressed."""
    specs = []
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern or pattern.startswith('#'):
            continue
        if not pattern.startswith('/') or any(c in pattern for c in '!*?[\\'):
            return None
        name = pattern.strip('/')
        if not name:
            return None
        specs.append(':(top,literal)' + name)
    return specs or None


def sparse_enabled(path, git, env):
    value = config_value(path, 'core.sparseCheckout', git, env) or ''
    return value.lower() in ('true', 'yes', 'on', '1')


def sparse_patterns(path, git, env):
    """The sparse checkout patterns the checkout at `path` uses, or None."""
    if not sparse_enabled(path, git, env):
        return None
    try:
        text = io_path(Path(path) / '.git' / 'info' / 'sparse-checkout').read_text(encoding='utf-8')
    except OSError:
        return None
    return [line.strip() for line in text.splitlines() if line.strip()]


def checkout_status(path, git, env, hidden, *, filemode=False):
    """`git status` lines for tracked files, limited to the sparse checkout when there is one.

    Git 2.37 and newer report a file that appears at a path outside the sparse
    patterns; such a file is not part of what FreeVideo uses.
    """
    specs = sparse_pathspecs(sparse_patterns(path, git, env) or [])
    settings = CONTENT_ONLY if not filemode else ('-c', 'core.filemode=true', '-c', 'core.autocrlf=false')
    return subprocess_output.output_external([git, '-c', 'core.longpaths=true', '-C', str(path), *settings,
                                              'status', '--porcelain', '--untracked-files=no',
                                              *(['--', *specs] if specs else [])], env=env, **hidden)


def repository_state(path, git, env, revision='HEAD', **options):
    """What `revision` is and what content changes the checkout at `path` has."""
    path = Path(path)
    prefix = [git, '-c', 'core.longpaths=true']
    hidden = dict(hidden_console(), **options)
    if not git_folder_too_deep(path):
        env = checkout_environment(path, env)
        # A folder Git cannot read is reported by the caller, not as Git's own message in the log.
        hidden.setdefault('stderr', subprocess.PIPE)
        head = subprocess_output.output_external([*prefix, '-C', str(path), *CONTENT_ONLY, 'rev-parse', revision],
                                                 env=env, **hidden).strip()
        dirty = checkout_status(path, git, env, hidden)
        return head, dirty
    # Name the repository instead of entering it. If Git cannot read it this
    # way, HEAD is read from the file and local changes cannot be checked.
    git_dir = '--git-dir=' + str(path / '.git')
    folder = tempfile.gettempdir()
    try:
        head = subprocess_output.output_external([*prefix, git_dir, *CONTENT_ONLY, 'rev-parse', revision],
                                                 cwd=folder, env=env, **hidden).strip()
        dirty = subprocess_output.output_external([*prefix, git_dir, *CONTENT_ONLY, 'diff-index', '--cached',
                                                   '--name-only', 'HEAD'], cwd=folder, env=env, **hidden)
    except (subprocess.CalledProcessError, OSError):
        # Git also refuses a --git-dir this long, so read what is asked from the files.
        head = stored_head(path)
        if revision == 'HEAD':
            return head, ''
        tree = stored_tree(path, head) if revision == 'HEAD^{tree}' else None
        if tree is None:
            raise
        return tree, ''
    return head, dirty


def stored_reference(path, name):
    """The commit a reference such as refs/heads/main names, from its file or packed-refs, or None."""
    try:
        return io_path(path / '.git' / name).read_text(encoding='utf-8').strip()
    except OSError:
        pass
    try:
        packed = io_path(path / '.git' / 'packed-refs').read_text(encoding='utf-8')
    except OSError:
        return None
    for line in packed.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == name and not line.startswith(('#', '^')):
            return parts[0]
    return None


def stored_head(path):
    """The commit .git/HEAD names, following a symbolic reference such as `ref: refs/heads/main`."""
    head = io_path(path / '.git' / 'HEAD').read_text(encoding='utf-8').strip()
    if head.startswith('ref:'):
        return stored_reference(path, head.split(None, 1)[1].strip()) or head
    return head


def stored_tree(path, commit):
    """The tree named by a loose commit object, or None (packed or missing objects)."""
    import zlib
    try:
        if commit.startswith('ref:'):
            commit = stored_reference(path, commit.split(None, 1)[1].strip())
        raw = zlib.decompress(io_path(path / '.git' / 'objects' / commit[:2] / commit[2:]).read_bytes())
    except (OSError, ValueError, IndexError, TypeError, zlib.error):
        return None
    header, _, body = raw.partition(b'\0')
    first = body.split(b'\n', 1)[0]
    if header.startswith(b'commit ') and first.startswith(b'tree ') and len(first) == 45:
        return first[5:].decode('ascii')
    return None


def status_paths(lines):
    return [line[3:].split(' -> ')[-1] for line in (lines or '').splitlines() if len(line) > 3]


def config_value(path, key, git, env):
    """A value from the checkout's own config file (readable even where Git cannot open the repository)."""
    try:
        result = subprocess_output.run_external(
            [git, 'config', '--file', str(Path(path) / '.git' / 'config'), '--get', key], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, **hidden_console())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def same_source(first, second):
    def normal(url):
        url = url.strip().rstrip('/')
        return url[:-4] if url.endswith('.git') else url
    return normal(first) == normal(second)


def owned_checkout(target, url, sparse, git, env):
    """Whether `target` is a checkout FreeVideo made itself and may set aside.

    Only dependency folders directly in a FreeVideo `vendor` folder qualify, and
    only when the checkout is set up as clone() leaves it: the same sparse
    patterns, or (without them) no origin or the same origin. A ComfyUI the user
    works in is never replaced: its custom nodes and models live inside it.
    """
    target = Path(target)
    if target.parent.name != 'vendor' or not io_path(target / '.git').is_dir():
        return False
    enabled = sparse_enabled(target, git, env)
    if sparse:
        try:
            patterns = io_path(target / '.git' / 'info' / 'sparse-checkout').read_text(encoding='utf-8').split()
        except OSError:
            return False
        return enabled and patterns == [p.strip() for p in sparse]
    origin = config_value(target, 'remote.origin.url', git, env)
    return not enabled and (origin is None or same_source(origin, url))


def mismatch_message(target, git, env, head, commit, dirty):
    """A specific description of how an existing checkout differs, naming only its folder and files."""
    details = []
    if head is None:
        details.append('Its version could not be read.')
    elif head != commit:
        details.append('It holds a different version.')
    changed = status_paths(dirty)
    if changed:
        shown = ', '.join(changed[:8]) + (' and %d more' % (len(changed) - 8) if len(changed) > 8 else '')
        details.append('Files with changed content (%d): %s.' % (len(changed), shown))
    if head is not None:
        try:
            with_modes = status_paths(checkout_status(target, git, checkout_environment(target, env),
                                                      dict(hidden_console(), timeout=60), filemode=True))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            with_modes = []
        modes = [name for name in with_modes if name not in set(changed)]
        if modes:
            details.append('1 other file differs only in permissions, which does not block installation.'
                           if len(modes) == 1 else
                           '%d other files differ only in permissions, which do not block installation.' % len(modes))
    return ('A dependency folder differs from the version FreeVideo needs: %s. %s '
            'Rename or move this folder, then retry installation. FreeVideo will download a fresh copy.'
            % (target, ' '.join(details)))


# Git reads a checkout's settings from .git\config by its ordinary path, even in
# the deep-folder branch above. Past the path limit it cannot tell whether
# FreeVideo made the folder, and a new copy in the same place could not be read
# either, so such a folder is reported before anything is renamed.
GIT_SETTINGS_SUFFIX = '\\.git\\config'


def git_settings_out_of_reach(path):
    return is_windows() and path_chars(plain(path)) + len(GIT_SETTINGS_SUFFIX) > GIT_PATH_LIMIT


def path_too_long_message(target):
    return ('The path of the dependency folder %s is too long for Git to read. Install FreeVideo in a folder '
            'with a shorter path, such as D:\\FreeVideo, then click "Retry installation". '
            'The folder was left unchanged.' % plain(target))


REPLACED_MARKER = 'freevideo-replaced'


def set_aside(target):
    """Rename an outdated checkout to `<name>.replaced-<time>` beside it; return the new path."""
    old = target.with_name('%s.replaced-%s' % (target.name, time.strftime('%Y%m%d-%H%M%S')))
    if io_path(old).exists():
        old = old.with_name('%s-%d' % (old.name, time.time_ns()))
    try:
        io_path(target).rename(io_path(old))
    except OSError as error:
        raise ValueError('FreeVideo could not rename the dependency folder %s to replace it. Close programs that '
                         'use this folder, then retry installation. The folder was left unchanged.' % target) from error
    try:
        io_path(old / '.git' / REPLACED_MARKER).write_text(target.name + '\n', encoding='utf-8')
    except OSError:
        pass  # Without the marker the folder is never removed automatically.
    return old


def remove_earlier_replaced(target, keep):
    """Remove older folders set_aside() made for `target`; never another folder."""
    pattern = re.compile(re.escape(target.name) + r'\.replaced-\d{8}-\d{6}(-\d+)?')
    try:
        candidates = list(io_path(target.parent).glob(target.name + '.replaced-*'))
    except OSError:
        return  # The folder cannot be listed now; a later replacement tries again.
    for path in candidates:
        if (path.name == keep.name or not pattern.fullmatch(path.name) or path.is_symlink()
                or not (path / '.git' / REPLACED_MARKER).is_file()):
            continue
        try:
            remove_tree(path)
        except OSError:
            pass  # Held by another program; a later replacement tries again.


def clone(target, url, commit, *, sparse=None, run=None, network=None, env=None):
    """Fetch only the pinned commit, atomically; never reset existing checkouts.

    An existing checkout at the pinned commit with unchanged content is used
    as it is. One FreeVideo made itself in its `vendor` folder that holds
    another version or changed content is replaced by a fresh verified
    download; the old folder is kept beside it as `<name>.replaced-<time>`.
    Any other differing folder is reported, never changed.
    """
    target = Path(target)
    network = installed_plan(env) if network is None else network
    env = proxy_environment(env)
    env.update(GIT_TERMINAL_PROMPT='0', GIT_HTTP_LOW_SPEED_LIMIT='1024', GIT_HTTP_LOW_SPEED_TIME='20')
    git = env.get('FREEVIDEO_GIT') or 'git'
    replacing = False
    if io_path(target).exists():
        if not io_path(target / '.git').exists():
            raise ValueError('Existing dependency is not a Git checkout of the pinned source: ' + str(target))
        try:
            head, dirty = repository_state(target, git, env)
        except (subprocess.CalledProcessError, OSError):
            head, dirty = None, ''  # Git cannot read it as a repository of its own.
        if head == commit and dirty and all(line[:2] == ' D' for line in dirty.splitlines()):
            # Files deleted after installation: the same commit's objects are still local, nothing is downloaded.
            from . import encoder_source
            if encoder_source.managed(target, env) and encoder_source.restore(target, encoder_source.deleted(target, env), env):
                head, dirty = repository_state(target, git, env)
        if head == commit and not dirty:
            return target
        if target.parent.name == 'vendor' and git_settings_out_of_reach(target):
            raise ValueError(path_too_long_message(target))
        if not owned_checkout(target, url, sparse, git, env):
            raise ValueError(mismatch_message(target, git, env, head, commit, dirty))
        replacing = True
        event(network, category='git', file=target.name, action='replace-planned', commit=commit,
              reason='unreadable' if head is None else 'other-version' if head != commit else 'changed-content',
              changed_files=len(status_paths(dirty)))
    version = git_version(git, env) if replacing or not io_path(target).exists() else None
    if version and version < MIN_GIT:
        raise RuntimeError(old_git_message(version))
    def execute(label, command):
        command = [git, '-c', 'core.longpaths=true', *command[1:]]
        if run:
            return run(label, command, env=env)
        result = subprocess_output.run_external(list(map(str, command)), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **hidden_console())
        if result.returncode:
            raise RuntimeError(result.stdout[-2500:])
    io_path(target.parent).mkdir(parents=True, exist_ok=True)
    candidates = urls(dict(network, sources=dict(network.get('sources', {}), github=network.get('sources', {}).get('git', [{'id': 'official'}]))), 'github', url, env)
    configured_env = env
    # A depth-1 fetch cannot continue an earlier attempt, so what a past run left beside the target is only waste.
    for stale in io_path(target.parent).glob(target.name + '.partial-*'):
        try:
            remove_tree(stale)
        except OSError:
            pass  # Held by another program; the download goes on and a later run tries again.
    for name, endpoint, route in [(name, endpoint, route) for name, endpoint in candidates
                                 for route in route_order(network, 'git', name, configured_env)]:
        env = route_environment(network, 'git', name, configured_env, route)
        temporary = target.with_name(target.name + '.partial-' + str(time.time_ns()))
        event(network, category='git', source=name, route=route, file=target.name, action='attempt', commit=commit)
        scratch = git_scratch_folder() if git_folder_too_deep(temporary) else None
        work = scratch / 'repo' if scratch else temporary
        try:
            execute(target.name + '-init-' + name, ['git', 'init', '--quiet', work])
            # Never depend on a user's global CRLF conversion or long-path
            # settings when constructing the exact official patched tree.
            execute(target.name + '-line-endings', ['git', '-C', work, 'config', 'core.autocrlf', 'false'])
            execute(target.name + '-long-paths', ['git', '-C', work, 'config', 'core.longpaths', 'true'])
            if sparse:
                # The patterns file with cone mode off is what `sparse-checkout
                # set --no-cone` writes; every Git reads it at checkout.
                execute(target.name + '-sparse', ['git', '-C', work, 'config', 'core.sparseCheckout', 'true'])
                execute(target.name + '-sparse-cone', ['git', '-C', work, 'config', 'core.sparseCheckoutCone', 'false'])
                io_path(work / '.git' / 'info').mkdir(exist_ok=True)
                io_path(work / '.git' / 'info' / 'sparse-checkout').write_text(''.join(p + '\n' for p in sparse), encoding='utf-8')
            # The URL reaches Git the way `git -c` hands settings to its own
            # children, in every Git version: never in process arguments, logs
            # or the repository's config, even if the fetch is killed.
            original = env
            env = dict(env, GIT_CONFIG_PARAMETERS=' '.join(filter(None, (
                env.get('GIT_CONFIG_PARAMETERS', '').strip(), git_parameter('remote.origin.url=' + endpoint)))))
            try:
                execute(target.name + '-fetch-' + name, ['git', '-C', work, '-c', 'fetch.fsckObjects=true', 'fetch', '--depth=1', '--no-tags', 'origin', commit])
            finally:
                env = original
            execute(target.name + '-checkout', ['git', '-C', work, 'checkout', '--detach', commit])
            actual = subprocess_output.output_external([git, '-c', 'core.longpaths=true', '-C', str(work), 'rev-parse', 'HEAD'], env=env, **hidden_console()).strip()
            if actual != commit:
                raise ValueError('Downloaded source did not match its pinned commit')
            if scratch:
                relocate(work, temporary)
            old = set_aside(target) if replacing else None
            try:
                io_path(temporary).rename(io_path(target))
            except OSError as error:
                if old is None:
                    raise
                try:
                    io_path(old).rename(io_path(target))
                except OSError:
                    raise ValueError('FreeVideo could not put the new copy of %s in place, and the previous files '
                                     'are now in %s. Close programs that use these folders, then retry '
                                     'installation.' % (target, old)) from error
                try:
                    io_path(target / '.git' / REPLACED_MARKER).unlink()
                except OSError:
                    pass
                raise ValueError('FreeVideo could not put the new copy of %s in place. Close programs that use '
                                 'this folder, then retry installation. The folder was left unchanged.' % target) from error
            if old is not None:
                event(network, category='git', file=target.name, action='replaced', commit=commit, kept=old.name)
                remove_earlier_replaced(target, old)
            source_health(network, 'git', name, True)
            route_health(network, 'git', name, route)
            event(network, category='git', source=name, route=route, file=target.name, action='verified', commit=commit)
            return target
        except RuntimeError as error:
            if not retryable(error):
                raise
            source_health(network, 'git', name, False)
            event(network, category='git', source=name, route=route, file=target.name, action='fallback', reason='network-fetch-failed')
        finally:
            # Best effort: a file still held by antivirus or git.exe must not replace the
            # error being handled or stop the fallback to the next source.
            for leftover in (scratch, io_path(temporary)):
                try:
                    if leftover and leftover.exists():
                        remove_tree(leftover)
                except OSError:
                    pass
    raise RuntimeError('All Git sources failed. Check your network or connection mode in Downloads.')
