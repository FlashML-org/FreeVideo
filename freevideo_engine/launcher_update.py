"""Background launcher updates. Standard library only; no model installation.

Executables live in a versioned user cache. The original EXE stays intact and
forwards to a newer, explicitly approved download on subsequent starts.
"""
import errno
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import execution_paths
from .top_level_modules import load
from .monitoring import save
from .long_paths import io_path, resolved, plain, plain_errors
from . import disk_space, network
from .launcher_settings import default_language
from .system import windows

ed25519, keys = load('freevideo_ed25519'), load('freevideo_update_keys')

REPOSITORY = 'FlashML-org/FreeVideo'
# Mirror repository on the ModelScope domestic site; the release script uploads each release there.
MIRROR_BASE = 'https://modelscope.cn/models/BowenXue/FreeVideo-releases/resolve/master'
# ModelScope redirects large files to its regional CDN: cdn-lfs-cn-<n>.modelscope.cn.
MIRROR_CDN = re.compile(r'cdn-lfs-cn-[0-9]{1,2}\.modelscope\.cn')
UPDATE_STALL_SECONDS = 30
# A source this slow for this long gives way to the next one, keeping the bytes
# already fetched; if every source is slow, the fastest one finishes the file.
UPDATE_SLOW_SECONDS = 15
UPDATE_SLOW_BYTES_PER_SECOND = 256 * 1024
MANIFEST_CHECK_SECONDS = 20
_metadata_budget = threading.local()
log = logging.getLogger(__name__)


def mirror_url(relative):
    return MIRROR_BASE + '/' + urllib.parse.quote(relative, safe='/')


def source_order(language=None):
    language = (default_language() if language is None else language).lower()
    # Windows names its locales in words ('Chinese (Simplified)_China'), not 'zh_CN'.
    return ('mirror', 'github') if language.startswith(('zh', 'chinese')) else ('github', 'mirror')


# The channel names the platform; launchers from before combined releases read
# these tags, which the release workflow keeps up to date for them.
CHANNEL = 'windows-preview'
API = 'https://api.github.com/repos/' + REPOSITORY
WEB = 'https://github.com/' + REPOSITORY
RELEASE_PAGE = WEB + '/releases/latest'
MAX_EXE_BYTES = 512 * 2**20
MAX_DMG_BYTES = 2**30
MAC_CHANNEL = 'macos-preview'
# Linux has no release before combined releases, so no platform tag to fall back on.
LINUX_CHANNEL = 'linux-preview'
# Stable builds follow the latest vX.Y.Z release; nightly builds follow the
# rolling `nightly` prerelease. Both carry every platform in one release.
TRACKS = ('stable', 'nightly')
NIGHTLY_TAG = 'nightly'
RELEASE_ASSETS = {CHANNEL: ('FreeVideo.exe', 'update-windows.json'),
                  MAC_CHANNEL: ('FreeVideo-Mac-arm64.dmg', 'update-macos.json'),
                  LINUX_CHANNEL: ('FreeVideo-Linux-x86_64.AppImage', 'update-linux.json')}
CHANNELS = tuple(RELEASE_ASSETS)
MANIFEST_DOMAIN = b'FreeVideo update manifest v1\x00'
# Every platform's mirror manifest carries the release signature, which binds the
# package digest. The Windows launcher also checks a mirror-only download's
# Authenticode publisher. The Mac codesign check before use is an integrity check
# only: it does not require a particular developer identity.
MIRROR_CHANNELS = CHANNELS
RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 15 * 60
MAX_DOWNLOAD_RETRIES = 3
# No answer may postpone the next attempt beyond the service's own hourly window.
MAX_SERVER_WAIT_SECONDS = 3600
# Every released launcher stops reading update metadata at this size.
MAX_MANIFEST_BYTES = 65536
_api_retry_at = 0.


class UpdateUnavailable(OSError):
    def __init__(self, kind, retry_after=0):
        self.kind, self.retry_after = kind, retry_after
        super().__init__('Update service temporarily unavailable')


def retry_after(error):
    """Respect GitHub's primary/secondary limit and HTTP Retry-After headers."""
    if isinstance(error, UpdateUnavailable):
        return error.retry_after
    headers = {str(k).lower(): v for k, v in (getattr(error, 'headers', None) or {}).items()}
    delays = [0]
    value = headers.get('retry-after', '')
    try:
        delays.append(float(value))
    except (ValueError, TypeError):
        try:
            delays.append(parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            pass
    if headers.get('x-ratelimit-remaining') == '0':
        try:
            delays.append(float(headers.get('x-ratelimit-reset', '')) - time.time() + 1)
        except (ValueError, TypeError):
            pass
    return min(MAX_SERVER_WAIT_SECONDS, max(d for d in delays if d == d))


def failure_kind(error):
    if isinstance(error, UpdateUnavailable):
        return error.kind
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return 'disk'
    if isinstance(error, urllib.error.HTTPError):
        headers = {str(k).lower(): v for k, v in (error.headers or {}).items()}
        if error.code == 429 or (error.code == 403 and
                (headers.get('x-ratelimit-remaining') == '0' or headers.get('retry-after'))):
            return 'rate_limited'
        if error.code == 404:
            return 'missing'
        if error.code in (401, 403):
            return 'blocked'
    return 'connection' if isinstance(error, OSError) else 'invalid'


def error_text(row, zh=False):
    """Product copy stays separate from raw exceptions, URLs and credentials."""
    messages = {
        'rate_limited': ('The update service is busy.', '更新服务繁忙。'),
        'missing': ('The files for the new version are still being published.', '新版本的文件还在发布中。'),
        'blocked': ('The update service could not be reached.', '无法访问更新服务。'),
        'connection': ('Could not connect to the download server.', '无法连接下载服务器。'),
        'invalid': ('The downloaded update files are incomplete or damaged.', '下载的更新文件不完整或已损坏。'),
        'disk': ('There is not enough disk space to download the update.', '磁盘空间不足，无法下载更新。'),
    }
    message = messages.get(row.get('error_code'))
    if message is None:
        return row.get('error', '')  # Other frontend errors retain their own copy.
    text = message[bool(zh)]
    if row.get('retry_at'):
        return text + ('稍后会自动重试，当前版本可以照常使用。' if zh else
                       ' FreeVideo will try again later. You can keep using the current version.')
    return text + ('请重试，或去发布页手动下载。' if zh else
                   ' Try again, or download it from the release page.')


def asset_url(tag, name):
    return WEB + '/releases/download/' + urllib.parse.quote(tag, safe='') + '/' + name


def _asset_url(candidate):
    return candidate['asset'].get('url') or (asset_url(NIGHTLY_TAG, RELEASE_ASSETS[candidate['channel']][0])
            if build_track(candidate) == 'nightly' else
            RELEASE_PAGE + '/download/' + RELEASE_ASSETS[candidate['channel']][0])


def build_target(value):
    """Legacy identities are Windows x64; never mix host update artifacts."""
    return value.get('target', 'windows-x86_64')


def build_track(value):
    return value.get('track', 'stable')


def manual_install(current, client=None):
    """Whether the user installs the update themselves from the release page."""
    if current.get('packaging') == 'app':
        if client is not None and client.install_failed:
            return True
        from . import mac_app_update
        return not mac_app_update.replaceable()
    return current.get('packaging') == 'onedir'


def release_page(current):
    if build_track(current) == 'nightly':
        return 'https://github.com/' + REPOSITORY + '/releases/tag/' + NIGHTLY_TAG
    return RELEASE_PAGE


def build_identity(value):
    if (not isinstance(value, dict) or value.get('repository') != REPOSITORY
            or value.get('channel') not in CHANNELS or value.get('schema') != 1
            or not re.fullmatch(r'[0-9a-f]{40,64}', str(value.get('revision', '')))
            or type(value.get('built_at')) is not int or value['built_at'] <= 0
            or not isinstance(value.get('version'), str) or len(value['version']) > 64):
        raise ValueError('Invalid launcher build identity')
    if value['channel'] == MAC_CHANNEL:
        if value.get('target') != 'macos-arm64' or value.get('packaging') != 'app':
            raise ValueError('Invalid Mac launcher build identity')
    elif value['channel'] == LINUX_CHANNEL:
        if value.get('target') != 'linux-x86_64' or value.get('packaging') != 'appimage':
            raise ValueError('Invalid Linux launcher build identity')
    elif build_target(value) != 'windows-x86_64':
        raise ValueError('Launcher update channel does not match its target')
    if build_track(value) not in TRACKS:
        raise ValueError('Unknown launcher release track')
    result = {k: value[k] for k in ('schema', 'repository', 'channel', 'revision', 'built_at', 'version')}
    if build_track(value) != 'stable':
        result['track'] = build_track(value)
    if value.get('packaging') == 'onedir':
        result['packaging'] = 'onedir'
    if value['channel'] == MAC_CHANNEL:
        result.update(target='macos-arm64', packaging='app')
    if value['channel'] == LINUX_CHANNEL:
        result.update(target='linux-x86_64', packaging='appimage')
    from .release_notes import optional_fields
    result.update(optional_fields(value))
    return result


def current_build():
    if not getattr(sys, 'frozen', False):
        return None
    try:
        import freevideo_source_bootstrap
        if freevideo_source_bootstrap.ACTIVE:
            return build_identity(freevideo_source_bootstrap.ACTIVE['manifest']['build'])
        return build_identity(json.loads((io_path(sys._MEIPASS) / 'launcher-build.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return None


def manifest(value):
    result = build_identity(value)
    asset = value.get('asset', {})
    if (not isinstance(asset, dict) or type(asset.get('id')) is not int or asset['id'] <= 0
            or type(asset.get('bytes')) is not int or not 2 <= asset['bytes'] <= (MAX_DMG_BYTES if result['channel'] == MAC_CHANNEL else MAX_EXE_BYTES)
            or not re.fullmatch(r'[0-9a-f]{64}', str(asset.get('sha256', '')))):
        raise ValueError('Invalid launcher update asset')
    result['asset'] = {k: asset[k] for k in ('id', 'bytes', 'sha256')}
    if download_address(asset.get('url'), result):
        result['asset']['url'] = asset['url']
    return result


def download_address(url, build):
    """Whether url is this platform's package on a tag of its own track.

    The address is optional. One this build does not recognise is left out
    rather than failing the update: size and SHA-256 still bind the package."""
    prefix = WEB + '/releases/download/'
    if not isinstance(url, str) or not url.startswith(prefix):
        return False
    parts = url[len(prefix):].split('/')
    tags = (NIGHTLY_TAG,) if build_track(build) == 'nightly' else (build['channel'],)
    return (len(parts) == 2 and parts[1] == RELEASE_ASSETS[build['channel']][0]
            and (parts[0] in tags or (build_track(build) == 'stable'
                                      and re.fullmatch(r'v\d+\.\d+\.\d+', parts[0]) is not None)))


def newer(candidate, current):
    from .release_notes import older
    return (build_target(candidate) == build_target(current)
            and candidate.get('channel', CHANNEL) == current.get('channel', CHANNEL)
            and build_track(candidate) == build_track(current)
            and not older({'product_version': candidate.get('product_version')},
                          {'product_version': current.get('product_version')})
            and candidate['revision'] != current['revision'] and candidate['built_at'] > current['built_at'])


def _trusted_url(url):
    if any(ord(character) <= 32 for character in url):
        raise ValueError('Invalid update URL')
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
            or not (parsed.hostname in ('api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                                        'objects.githubusercontent.com', 'github-releases.githubusercontent.com',
                                        'modelscope.cn', 'www.modelscope.cn')
                    or MIRROR_CDN.fullmatch(parsed.hostname or ''))):
        raise ValueError('Update redirect is not an approved HTTPS download')


def _github_url(url):
    _trusted_url(url)
    if urllib.parse.urlsplit(url).hostname not in (
            'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
            'objects.githubusercontent.com', 'github-releases.githubusercontent.com'):
        raise ValueError('Source trust requires GitHub HTTPS')


class UpdateRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, github_only=False):
        super().__init__()
        self.github_only = github_only

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        (_github_url if self.github_only else _trusted_url)(newurl)
        if time.monotonic() >= getattr(_metadata_budget, 'deadline', float('inf')):
            raise TimeoutError('Update metadata deadline exceeded')
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(newurl).hostname != 'api.github.com':
            redirected.remove_header('Authorization')
        return redirected


def open_download(url, token='', *, binary=False, github_only=False):
    # One inherited route, then one direct route. Do not mutate shell, Git or
    # registry proxies. Package bodies use network.download instead.
    global _api_retry_at
    is_api = url.startswith(API + '/')
    if not is_api and not (url.startswith(WEB + '/releases/download/')
                          or url.startswith(RELEASE_PAGE + '/download/')
                          or url.startswith(MIRROR_BASE + '/')):
        raise ValueError('Unexpected update download address')
    (_github_url if github_only else _trusted_url)(url)
    if is_api and time.time() < _api_retry_at:
        raise UpdateUnavailable('rate_limited', _api_retry_at - time.time())
    for direct in (False, True):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({} if direct else None), UpdateRedirect(github_only=github_only))
        headers = {'User-Agent': 'FreeVideo-launcher', 'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json'}
        if token and is_api:
            headers['Authorization'] = 'Bearer ' + token
        try:
            remaining = getattr(_metadata_budget, 'deadline', time.monotonic()+5) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Update metadata deadline exceeded')
            return opener.open(urllib.request.Request(url, headers=headers), timeout=min(5, remaining))
        except urllib.error.HTTPError as error:
            if failure_kind(error) == 'rate_limited':
                if is_api:
                    _api_retry_at = time.time() + max(RETRY_SECONDS, retry_after(error))
                raise
            if direct or error.code in (401, 404):
                raise  # Repeating a missing asset does not help; a blocked proxy may.
        except (OSError, urllib.error.URLError):
            if direct:
                raise


def _bytes(response, limit, cancel=None, seconds=15):
    deadline = min(time.monotonic() + seconds, getattr(_metadata_budget, 'deadline', float('inf')))
    done = 0
    read = getattr(response, 'read1', response.read)
    while True:
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Update download stopped; existing installation is unchanged')
        if time.monotonic() > deadline:
            raise TimeoutError('Update transfer exceeded its time limit; retry when the connection is available')
        part = read(min(256*1024, limit-done+1))
        if not part:
            return
        done += len(part)
        if done > limit:
            raise ValueError('Update response exceeds the expected size')
        yield part


def _json(url, token, *, binary=False, limit=2**20):
    with open_download(url, token, binary=binary) as response:
        return json.loads(b''.join(_bytes(response, limit)))


def _release(token, channel, track):
    """The release holding this platform's update, and its metadata asset name."""
    executable, metadata = RELEASE_ASSETS[channel]
    if track == 'nightly':
        release = _json(API + '/releases/tags/' + NIGHTLY_TAG, token)
        if not isinstance(release, dict) or release.get('draft') or release.get('tag_name') != NIGHTLY_TAG:
            raise UpdateUnavailable('missing')
        return release, executable, metadata
    try:
        release = _json(API + '/releases/latest', token)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = None
    names = {a.get('name') for a in (release or {}).get('assets', []) if isinstance(a, dict)}
    if isinstance(release, dict) and not release.get('draft') and not release.get('prerelease') and metadata in names:
        return release, executable, metadata
    if channel == LINUX_CHANNEL:
        raise UpdateUnavailable('missing')
    # Until the first combined release, updates come from the platform tag.
    release = _json(API + '/releases/tags/' + channel, token)
    if not isinstance(release, dict) or release.get('draft') or release.get('tag_name') != channel:
        raise UpdateUnavailable('missing')
    return release, executable, 'update.json'


def _api_latest_release(token, channel, track):
    """Compatibility fallback for releases without directly readable metadata."""
    release, executable_name, metadata_name = _release(token, channel, track)
    assets = {a.get('name'): a for a in release.get('assets', []) if isinstance(a, dict)}
    metadata, executable = assets.get(metadata_name, {}), assets.get(executable_name, {})
    if type(metadata.get('id')) is not int:
        raise UpdateUnavailable('missing')
    candidate = manifest(_json(API + '/releases/assets/' + str(metadata['id']), token, binary=True, limit=MAX_MANIFEST_BYTES))
    _selection(candidate, channel, track)
    if candidate['asset']['id'] != executable.get('id') or candidate['asset']['bytes'] != executable.get('size'):
        raise UpdateUnavailable('missing')
    candidate['asset'].setdefault('url', asset_url(release['tag_name'], executable_name))
    return manifest(candidate)


def _fallback(primary, call):
    """The API route after the direct one failed. The direct route needs no
    quota, so a limited API does not decide when it is tried again."""
    try:
        return call()
    except OSError as error:
        if failure_kind(error) == 'rate_limited':
            raise primary
        raise


def _selection(candidate, channel, track):
    if candidate['channel'] != channel:
        raise ValueError('Update metadata belongs to a different platform')
    if build_track(candidate) != track:
        raise ValueError('Update metadata belongs to a different release track')


def _github_release(token='', *, channel=CHANNEL, track='stable'):
    """Read and validate the published build without downloading an executable."""
    if channel not in CHANNELS:
        raise ValueError('Unknown launcher update channel')
    if track not in TRACKS:
        raise ValueError('Unknown launcher release track')
    metadata = RELEASE_ASSETS[channel][1]
    url = (asset_url(NIGHTLY_TAG, metadata) if track == 'nightly' else
           RELEASE_PAGE + '/download/' + metadata)
    try:
        candidate = manifest(_json(url, '', binary=True, limit=MAX_MANIFEST_BYTES))
    except urllib.error.HTTPError as error:
        if error.code == 404 and track == 'stable' and channel != LINUX_CHANNEL:
            try:
                candidate = manifest(_json(asset_url(channel, 'update.json'), '', binary=True, limit=MAX_MANIFEST_BYTES))
            except OSError as legacy:
                return _fallback(legacy, lambda: _api_latest_release(token, channel, track))
            _selection(candidate, channel, track)
            # Older manifests have no URL; keep their package on the same tag.
            candidate['asset'].setdefault('url', asset_url(channel, RELEASE_ASSETS[channel][0]))
            return candidate
        return _fallback(error, lambda: _api_latest_release(token, channel, track))
    except OSError as error:
        return _fallback(error, lambda: _api_latest_release(token, channel, track))
    _selection(candidate, channel, track)
    return candidate


def manifest_signature_ok(data, signature):
    """Whether `signature` (hex) is the release key's signature over the manifest bytes."""
    try:
        return (keys.ED25519_RELEASE_KEY_ENABLED and
                keys.ED25519_PUBLIC_KEY != keys.PLACEHOLDER_PUBLIC_KEY and
                ed25519.verify(keys.ED25519_PUBLIC_KEY, MANIFEST_DOMAIN + data,
                               bytes.fromhex(signature.decode('ascii').strip())))
    except (TypeError, ValueError, UnicodeDecodeError):
        return False


def _mirror_bytes(url, limit):
    with open_download(url, '', binary=True) as response:
        return b''.join(_bytes(response, limit))


def _mirror_release(channel, track):
    if channel not in MIRROR_CHANNELS:
        raise ValueError('No mirror for this platform')
    name = RELEASE_ASSETS[channel][1]
    data = _mirror_bytes(mirror_url('channels/stable/' + name), MAX_MANIFEST_BYTES)
    # A mirror is not the publisher: its manifest counts only with the release
    # signature, otherwise this source is skipped and GitHub still decides.
    if not manifest_signature_ok(data, _mirror_bytes(mirror_url('channels/stable/' + name + '.sig'), 256)):
        raise ValueError('Mirror manifest signature is not valid')
    candidate = manifest(json.loads(data))
    _selection(candidate, channel, track)
    if not candidate.get('product_version'):
        raise ValueError('Mirror updates require a product version')
    return candidate


def _choose_release(found, order):
    github, mirror = found.get('github'), found.get('mirror')
    conflict = (github is not None and mirror is not None
                and github.get('product_version') == mirror.get('product_version')
                and github['asset']['sha256'] != mirror['asset']['sha256'])
    if conflict:
        selected = github
    else:
        selected = next(found[name] for name in order if name in found)
        for candidate in found.values():
            if newer(candidate, selected):
                selected = candidate
    # Local provenance, never read from remote JSON. Persist it with the reminder
    # so a mirror-only check still requires the same verification after restart.
    result = dict(selected)
    if mirror is None:
        return result
    result['_update_sources'] = dict(github='github' in found, mirror='mirror' in found,
                                   mirror_allowed=not conflict)
    return result


def latest_release(token='', *, channel=CHANNEL, track='stable', language=None, on_candidate=None):
    """Race bounded metadata requests; reconcile both before explicit download.

    A first valid result can immediately display a reminder. The background
    check settles the other small request before allowing activation/download,
    so neither a stale mirror nor a digest conflict can win on latency alone.
    """
    if channel not in CHANNELS or track not in TRACKS:
        raise ValueError('Unknown launcher update channel or track')
    if track != 'stable':
        return _github_release(token, channel=channel, track=track)
    order = [name for name in source_order(language) if name == 'github' or channel in MIRROR_CHANNELS]
    calls = dict(github=lambda: _github_release(token, channel=channel, track=track),
                 mirror=lambda: _mirror_release(channel, track))
    def fetch(name):
        _metadata_budget.deadline = time.monotonic() + MANIFEST_CHECK_SECONDS
        try:
            return calls[name]()
        finally:
            del _metadata_budget.deadline
    found, failures = {}, {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='launcher-manifest') as pool:
        pending = {pool.submit(fetch, name): name for name in order}
        for future in as_completed(pending):
            name = pending[future]
            try:
                found[name] = future.result()
            except (OSError, ValueError, TypeError, AttributeError) as error:
                failures[name] = error
            if found and on_candidate:
                on_candidate(_choose_release(found, order))
    if not found:
        raise failures['github']
    return _choose_release(found, order)


def check(current, token='', *, language=None, on_candidate=None):
    current = build_identity(current)
    options = {}
    if language is not None:
        options['language'] = language
    if on_candidate is not None:
        options['on_candidate'] = on_candidate
    candidate = latest_release(token, channel=current['channel'], track=build_track(current), **options)
    return candidate if newer(candidate, current) else None


@plain_errors
def checksum(path):
    digest = hashlib.sha256()
    with io_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(256*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verified(path, asset):
    try:
        path = io_path(path)
        return (path.is_file() and not path.is_symlink() and path.stat().st_size == asset['bytes']
                and checksum(path) == asset['sha256'])
    except OSError:
        return False


@plain_errors
def executable_path(root, candidate):
    filename = RELEASE_ASSETS[candidate['channel']][0]
    return Path(plain(resolved(root))) / 'updates' / candidate['asset']['sha256'] / filename


def authenticode(path):
    """Use Windows PowerShell 5.1's WinTrust-backed check, with a literal path.

    Only an exit code leaves PowerShell; certificate/path/provider diagnostics
    and user data are never logged. The script contains no interpolated path.
    """
    from .windows_ux import external_python, hidden_console
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    executable = system / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = ("$ErrorActionPreference='Stop'; try {"
              "$s=Get-AuthenticodeSignature -LiteralPath $env:FREEVIDEO_SIGNATURE_PATH;"
              "if ($s.Status -eq 'Valid' -and $s.SignerCertificate -and "
              "$s.SignerCertificate.Subject -cmatch '(^|,\\s*)CN=FlashML LLC(,|$)' -and "
              "$s.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false) -ceq 'FlashML LLC') "
              "{exit 0}; exit 1} catch {exit 2}")
    env = dict(os.environ, FREEVIDEO_SIGNATURE_PATH=str(io_path(resolved(path))))
    try:
        with external_python():
            result = execution_paths.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive',
                                          '-Command', script], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, **hidden_console())
        return {0: True, 1: False}.get(result.returncode)  # None: the check itself could not be completed
    except (OSError, subprocess.SubprocessError):
        return None


def _signature_verifier(candidate, provenance):
    if not windows() or build_target(candidate) != 'windows-x86_64' or provenance.get('github', True):
        return None
    current = authenticode(sys.executable)
    enforced = current is not False  # A check that could not run is treated as signed: fail closed, switch source.
    def verify(path):
        valid = authenticode(path) is True
        (log.info if valid else log.warning)('Launcher Authenticode: current=%s enforced=%s valid=%s',
                                             current, enforced, valid)
        return valid or not enforced
    return verify


def download_sources(candidate, language=None):
    sources = {'github': _asset_url(candidate)}
    provenance = candidate.get('_update_sources', {})
    if (build_track(candidate) == 'stable' and candidate.get('product_version')
            and candidate.get('channel') in MIRROR_CHANNELS and provenance.get('mirror_allowed', True)):
        sources['mirror'] = mirror_url('releases/v' + candidate['product_version'] + '/' +
                                       RELEASE_ASSETS[candidate['channel']][0])
    return [(name, sources[name]) for name in source_order(language) if name in sources]


def download_fallbacks(candidate, token=''):
    if time.time() < _api_retry_at:
        return {}
    headers = ('Accept: application/octet-stream',)
    if token:
        headers += ('Authorization: Bearer ' + token,)
    return {'github': (API + '/releases/assets/' + str(candidate['asset']['id']), headers)}


@plain_errors
def download(candidate, root, token='', *, progress=None, cancel=None, language=None):
    provenance = candidate.get('_update_sources', {})
    candidate = dict(manifest(candidate), _update_sources=provenance)
    from .source_update import try_download
    overlay = try_download(candidate, root, token, progress=progress, cancel=cancel, language=language)
    if overlay is not None:
        return overlay
    asset = candidate['asset']
    target = executable_path(root, candidate)
    def cancelled():
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Update stopped before activation')
    cancelled()
    verify_signature = _signature_verifier(candidate, provenance)
    if verified(target, asset) and (verify_signature is None or verify_signature(target)):
        cancelled()
        return target
    io_path(target.parent).mkdir(parents=True, exist_ok=True)
    # Only the selected cache artifact is ours. Other update versions/attempts
    # and the original launcher remain untouched.
    io_path(target).unlink(missing_ok=True)
    partial = io_path(str(target) + '.partial')
    retained = min(asset['bytes'], partial.stat().st_size) if partial.is_file() else 0
    if disk_space.free_bytes(target.parent) < asset['bytes'] - retained + 16*2**20:
        raise UpdateUnavailable('disk')
    def report(done, total, speed):
        cancelled()
        if progress:
            progress(dict(done=done, total=total, bytes_per_second=speed,
                          remaining_seconds=(total-done)/speed if speed > 0 else 0))
    try:
        network.download(download_sources(candidate, language), io_path(target), asset['sha256'], report,
            network=dict(quiet=True, resource_check=cancelled), size=asset['bytes'],
            stall_seconds=UPDATE_STALL_SECONDS, slow_seconds=UPDATE_SLOW_SECONDS,
            low_speed_limit=UPDATE_SLOW_BYTES_PER_SECOND, cycles=2,
            category='launcher-update', validate_url=_trusted_url, verify=verify_signature,
            fallbacks=download_fallbacks(candidate, token))
    except network.DownloadError as error:
        raise UpdateUnavailable('invalid' if getattr(error, 'integrity', False) else 'connection') from error
    cancelled()
    if build_target(candidate) == 'linux-x86_64':
        io_path(target).chmod(0o755)
    return target


class DownloadedLauncherUnavailable(ValueError):
    """The cached executable needs downloading again before it can be started."""


@plain_errors
def launch_download(candidate, root, *, token='', popen=None):
    candidate = manifest(candidate)
    from . import source_update
    overlay = source_update.prepared(candidate, root)
    if overlay is not None:
        return source_update.launch(candidate, root, overlay, token=token, popen=popen)
    path = executable_path(root, candidate)
    if not verified(path, candidate['asset']):
        raise DownloadedLauncherUnavailable('Downloaded launcher is missing or changed; download again')
    if build_target(candidate) == 'macos-arm64':
        from . import mac_app_update
        staged = Path(path.parent) / 'staged.txt'
        try:
            location = Path(staged.read_text(encoding='utf-8').strip())
            process = mac_app_update.activate(location, mac_app_update.current_app(), path.parent, popen=popen or subprocess.Popen)
        except (OSError, ValueError, AttributeError):
            raise DownloadedLauncherUnavailable('The prepared app is missing; download again')
        save(Path(root) / 'updates' / 'active.json', dict(approved=True, candidate=candidate))
        return process
    from .linux_bundle import child_environment
    env = dict(child_environment(), FREEVIDEO_LAUNCHER_HOME=plain(resolved(root)))
    if token:
        env['GITHUB_TOKEN'] = token  # Session only; never stored in a receipt or command.
    kwargs = dict(cwd=path.parent, env=env, close_fds=True)
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    process = (popen or execution_paths.popen)([str(path)], **kwargs)
    # Start first: a spawn error must not replace the approved startup receipt.
    save(Path(root) / 'updates' / 'active.json', dict(approved=True, candidate=candidate))
    return process


def forward_approved(current, root):
    """Opening the original shortcut starts the last approved newer EXE."""
    if current.get('packaging') in ('onedir', 'app'):
        return False  # Keep the user's folder distribution; do not switch to onefile.
    try:
        receipt = json.loads((io_path(root)/'updates/active.json').read_text(encoding='utf-8'))
        if receipt.get('approved') is not True:
            return False
        candidate = manifest(receipt.get('candidate'))
        if not newer(candidate, current):
            return False
        if resolved(executable_path(root, candidate)) == resolved(sys.executable):
            return False  # A bad release identity must never recursively launch itself.
        launch_download(candidate, root)
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False  # A missing/corrupt cached update leaves the original app usable.


def handoff(candidate, root, source, *, pages=False):
    """Record which explicit action restarted into a newer launcher, and
    whether FreeVideo pages may be waiting to reconnect."""
    save(Path(root) / 'updates' / 'handoff.json',
         dict(revision=manifest(candidate)['revision'], source=source, pages=bool(pages), at=time.time()))


def resumed_update(current, root, *, seconds=600):
    """Return the explicit update that just started this build, at most once.

    The approved receipt must name this exact build and be recent. Launchers
    without a handoff record (older releases) still count as an update. The
    original shortcut later forwards here again, so a marker prevents a second
    automatic engine update for the same build.
    """
    updates = io_path(root) / 'updates'
    try:
        receipt = updates / 'active.json'
        value = json.loads(receipt.read_text(encoding='utf-8'))
        candidate = manifest(value.get('candidate'))
        age = time.time() - receipt.stat().st_mtime
        marker = updates / 'resumed.json'
        if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('revision') == current['revision']:
            return None
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if (value.get('approved') is not True or candidate['revision'] != current['revision']
            or candidate['built_at'] != current['built_at'] or not -60 <= age <= seconds):
        return None
    result = dict(source='launcher', pages=False)
    try:
        record = json.loads((updates / 'handoff.json').read_text(encoding='utf-8'))
        if record.get('revision') == current['revision'] and -60 <= time.time() - record.get('at', 0) <= seconds:
            source = record.get('source') if record.get('source') in ('launcher', 'browser') else 'launcher'
            result = dict(source=source, pages=record.get('pages') is True or source == 'browser')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    save(updates / 'resumed.json', dict(revision=current['revision'], at=time.time()))
    return result


class UpdateClient:
    @plain_errors
    def __init__(self, current, root, token=None, *, language=None):
        # Python 3.9 on Windows can leave a nonexistent relative path relative
        # in resolve(). Updates must survive changing cwd to the EXE directory.
        self.current, self.root = build_identity(current), Path(plain(resolved(root)))
        self.token = token if token is not None else os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN', '')
        self.language = language
        self.state = dict(status='idle')
        self.available = None
        # Older launchers saved this as a mandatory update. Read it only as a
        # cached reminder; a known newer version never prevents using this one.
        for name in ('available.json', 'required.json'):
            try:
                cached = json.loads((io_path(self.root) / 'updates' / name).read_text(encoding='utf-8'))
                candidate = manifest(cached)
                if isinstance(cached.get('_update_sources'), dict):
                    candidate['_update_sources'] = cached['_update_sources']
                if newer(candidate, self.current) and (self.available is None or newer(candidate, self.available)):
                    self.available = candidate
            except (OSError, ValueError, TypeError):
                pass
        self.thread = None
        self.cancelled = threading.Event()
        self.retry = None
        self.failures = 0
        # The swap of an earlier run put the old app back: say so once, and
        # leave the release page as the way to update this time.
        self.install_failed = False
        if self.current.get('packaging') == 'app':
            marker = io_path(self.root) / 'updates' / 'mac-update-failed.json'
            try:
                self.install_failed = marker.is_file()
                marker.unlink(missing_ok=True)
            except OSError:
                pass

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.cancelled.set()
        self.retry = None
        self.state = {k: v for k, v in self.state.items() if k not in ('retry_at', 'retry_operation')}
        if self.state.get('error_code'):
            self.state['error'] = error_text(self.state)

    def poll(self, *, allow_download=True):
        """UI ticks dispatch due retries; no sleeping worker blocks launch or close."""
        if self.cancelled.is_set():
            self.retry = None
        if self.busy or not self.retry:
            return
        operation, candidate, at = self.retry
        if time.time() >= at and (operation != 'download' or allow_download):
            self.run(operation, candidate, background=operation == 'check', _retry=True)

    def run(self, operation, candidate=None, *, background=False, _retry=False):
        """Only an explicit download, or its bounded retry, fetches a package."""
        if self.busy:
            return
        if operation not in ('check', 'download'):
            raise ValueError('Unknown update operation')
        if operation == 'download' and manual_install(self.current):
            kind = 'application' if self.current.get('packaging') == 'app' else 'folder'
            raise ValueError('Download and extract the updated %s ZIP from the release page' % kind)
        self.cancelled.clear()
        self.retry = None
        if not _retry:
            self.failures = 0
        quiet = background and operation == 'check'
        if not quiet:
            self.state = dict(status='checking' if operation == 'check' else 'downloading', candidate=candidate)
        def work():
            selected = candidate
            try:
                if operation == 'check':
                    def noticed(value):
                        if not self.cancelled.is_set() and newer(value, self.current):
                            shown = self.available
                            if shown is None or newer(value, shown):
                                self.state = dict(status='available', candidate=value)
                    found = check(self.current, self.token, language=self.language, on_candidate=noticed)
                    if self.cancelled.is_set():
                        raise InterruptedError('Update check cancelled')
                    if found and (self.available is None or newer(found, self.available)
                                  or (found['revision'] == self.available['revision']
                                      and found['built_at'] == self.available['built_at']
                                      and found.get('product_version') == self.available.get('product_version'))
                                  or (found.get('product_version') == self.available.get('product_version')
                                      and found.get('_update_sources', {}).get('mirror_allowed') is False)):
                        self.available = found
                        save(self.root / 'updates/available.json', found)
                    self.state = dict(status='available' if self.available else 'current', candidate=self.available)
                else:
                    if _retry and (not candidate['asset'].get('url') or build_track(candidate) == 'nightly'
                                   or candidate['asset']['url'] == asset_url(candidate['channel'], RELEASE_ASSETS[candidate['channel']][0])):
                        # A rolling release or a legacy latest URL may have moved.
                        found = check(self.current, self.token, language=self.language)
                        if found and newer(found, candidate):
                            selected = found
                    def prepare_app(dmg):
                        from . import mac_app_update
                        app = mac_app_update.current_app()
                        if app is None:
                            raise ValueError('Not running from an app bundle')
                        staged = mac_app_update.prepare(dmg, app, dmg.parent)
                        (dmg.parent / 'staged.txt').write_text(str(staged), encoding='utf-8')
                    def progress(value):
                        self.state = dict(status='downloading', candidate=selected, progress=value)
                    target = download(selected, self.root, self.token, progress=progress, cancel=self.cancelled, language=self.language)
                    # An incremental overlay comes back instead of the DMG; only the DMG is mounted.
                    if self.current.get('packaging') == 'app' and Path(target) == executable_path(self.root, selected):
                        prepare_app(target)
                    self.state = dict(status='ready', candidate=selected)
                self.failures = 0
            except Exception as error:
                self.failures += 1
                stopped = self.cancelled.is_set()
                row = dict(status='cancelled' if stopped else 'error', candidate=selected or self.available)
                if not stopped:
                    row['error_code'] = failure_kind(error)
                if (not stopped and row['error_code'] not in ('invalid', 'disk')
                        and (operation == 'check' or self.failures <= MAX_DOWNLOAD_RETRIES)):
                    delay = max(min(MAX_RETRY_SECONDS, RETRY_SECONDS * 2**min(self.failures - 1, 4)),
                                retry_after(error))
                    row['retry_at'] = time.time() + delay
                    row['retry_operation'] = operation
                    self.retry = (operation, selected, row['retry_at'])
                # A background check nobody asked for leaves what is shown alone
                # and only retries; the failure of a check that is shown stays current.
                if quiet and self.state.get('retry_operation') != 'check':
                    return
                row['error'] = error_text(row)
                self.state = row
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
