"""Background launcher updates. Standard library only; no model installation.

Executables live in a versioned user cache. The original EXE stays intact and
forwards to a newer, explicitly approved download on subsequent starts.
"""
import hashlib
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

from .diagnostics import Redactor
from .monitoring import save

REPOSITORY = 'FlashML-org/FreeVideo'
CHANNEL = 'windows-preview'
API = 'https://api.github.com/repos/' + REPOSITORY
RELEASE_PAGE = 'https://github.com/' + REPOSITORY + '/releases/tag/' + CHANNEL
MAX_EXE_BYTES = 512 * 2**20
MAC_CHANNEL = 'macos-preview'


def build_target(value):
    """Legacy identities are Windows x64; never mix host update artifacts."""
    return value.get('target', 'windows-x86_64')


def release_page(current):
    return 'https://github.com/' + REPOSITORY + '/releases/tag/' + build_identity(current)['channel']


def build_identity(value):
    if (not isinstance(value, dict) or value.get('repository') != REPOSITORY
            or value.get('channel') not in (CHANNEL, MAC_CHANNEL) or value.get('schema') != 1
            or not re.fullmatch(r'[0-9a-f]{40,64}', str(value.get('revision', '')))
            or type(value.get('built_at')) is not int or value['built_at'] <= 0
            or not isinstance(value.get('version'), str) or len(value['version']) > 64):
        raise ValueError('Invalid launcher build identity')
    if value['channel'] == MAC_CHANNEL:
        if value.get('target') != 'macos-arm64' or value.get('packaging') != 'app':
            raise ValueError('Invalid Mac launcher build identity')
    elif build_target(value) != 'windows-x86_64':
        raise ValueError('Launcher update channel does not match its target')
    result = {k: value[k] for k in ('schema', 'repository', 'channel', 'revision', 'built_at', 'version')}
    if value.get('packaging') == 'onedir':
        result['packaging'] = 'onedir'
    if value['channel'] == MAC_CHANNEL:
        result.update(target='macos-arm64', packaging='app')
    from .release_notes import optional_fields
    result.update(optional_fields(value))
    return result


def current_build():
    if not getattr(sys, 'frozen', False):
        return None
    try:
        return build_identity(json.loads((Path(sys._MEIPASS) / 'launcher-build.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return None


def manifest(value):
    result = build_identity(value)
    asset = value.get('asset', {})
    if (not isinstance(asset, dict) or type(asset.get('id')) is not int or asset['id'] <= 0
            or type(asset.get('bytes')) is not int or not 2 <= asset['bytes'] <= MAX_EXE_BYTES
            or not re.fullmatch(r'[0-9a-f]{64}', str(asset.get('sha256', '')))):
        raise ValueError('Invalid launcher update asset')
    result['asset'] = {k: asset[k] for k in ('id', 'bytes', 'sha256')}
    return result


def newer(candidate, current):
    return (build_target(candidate) == build_target(current)
            and candidate['channel'] == current['channel']
            and candidate['revision'] != current['revision'] and candidate['built_at'] > current['built_at'])


def _trusted_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.hostname not in ('api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                                       'objects.githubusercontent.com', 'github-releases.githubusercontent.com')):
        raise ValueError('Update redirect is not an approved HTTPS GitHub download')


class UpdateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _trusted_url(newurl)
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(newurl).hostname != 'api.github.com':
            redirected.remove_header('Authorization')
        return redirected


def open_download(url, token='', *, binary=False):
    # One inherited route, then one direct route. Do not mutate shell, Git or
    # registry proxies. Never retry a slow-but-active binary on another source.
    if not url.startswith(API + '/'):
        raise ValueError('Unexpected update API address')
    for direct in (False, True):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({} if direct else None), UpdateRedirect())
        headers = {'User-Agent': 'FreeVideo-launcher', 'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            return opener.open(urllib.request.Request(url, headers=headers), timeout=5)
        except (OSError, urllib.error.URLError):
            if direct:
                raise


def _bytes(response, limit, cancel=None, seconds=15):
    deadline = time.monotonic() + seconds
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


def latest_release(token='', *, channel=CHANNEL):
    """Read and validate the published build without downloading an executable."""
    if channel not in (CHANNEL, MAC_CHANNEL):
        raise ValueError('Unknown launcher update channel')
    release = _json(API + '/releases/tags/' + channel, token)
    if not isinstance(release, dict) or release.get('draft') or release.get('tag_name') != channel:
        raise ValueError('No published update is available for this platform')
    assets = {a.get('name'): a for a in release.get('assets', []) if isinstance(a, dict)}
    metadata, executable = assets.get('update.json', {}), assets.get(
        'FreeVideo-Mac-arm64.zip' if channel == MAC_CHANNEL else 'FreeVideo.exe', {})
    if type(metadata.get('id')) is not int:
        raise ValueError('Update metadata is not published yet; check the release page')
    candidate = manifest(_json(API + '/releases/assets/' + str(metadata['id']), token, binary=True, limit=65536))
    if candidate['channel'] != channel:
        raise ValueError('Update metadata belongs to a different platform')
    if candidate['asset']['id'] != executable.get('id') or candidate['asset']['bytes'] != executable.get('size'):
        raise ValueError('A new release is being published; check again shortly')
    return candidate


def check(current, token=''):
    build_identity(current)
    candidate = latest_release(token) if current['channel'] == CHANNEL else latest_release(token, channel=current['channel'])
    return candidate if newer(candidate, current) else None


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(256*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verified(path, asset):
    try:
        return (path.is_file() and not path.is_symlink() and path.stat().st_size == asset['bytes']
                and checksum(path) == asset['sha256'])
    except OSError:
        return False


def executable_path(root, candidate):
    filename = 'FreeVideo-Mac-arm64.zip' if build_target(candidate) == 'macos-arm64' else 'FreeVideo.exe'
    return Path(root).absolute().resolve() / 'updates' / candidate['asset']['sha256'] / filename


def download(candidate, root, token='', *, progress=None, cancel=None):
    candidate = manifest(candidate)
    asset = candidate['asset']
    target = executable_path(root, candidate)
    if verified(target, asset):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Unique staging keeps a cancelled/failed transfer and concurrent downloads
    # separate. The original launcher and models are never overwritten.
    import tempfile
    import shutil
    if shutil.disk_usage(target.parent).free < asset['bytes'] + 16*2**20:
        raise OSError('Not enough disk space to download the launcher update')
    start, done = time.monotonic(), 0
    with open_download(API + '/releases/assets/' + str(asset['id']), token, binary=True) as response:
        with tempfile.NamedTemporaryFile(prefix='download-', suffix='.partial', dir=target.parent, delete=False) as stream:
            stage = Path(stream.name)
            digest = hashlib.sha256()
            for block in _bytes(response, asset['bytes'], cancel, seconds=1800):
                stream.write(block); digest.update(block); done += len(block)
                if progress:
                    elapsed = time.monotonic()-start
                    speed = done/max(.001, elapsed)
                    progress(dict(done=done, total=asset['bytes'], bytes_per_second=speed,
                                  remaining_seconds=(asset['bytes']-done)/speed))
            stream.flush(); os.fsync(stream.fileno())
    if done != asset['bytes'] or digest.hexdigest() != asset['sha256']:
        raise ValueError('Launcher update failed size/content verification; original EXE retained')
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Update stopped before activation')
    stage.replace(target)
    return target


class DownloadedLauncherUnavailable(ValueError):
    """The cached executable needs downloading again before it can be started."""


def launch_download(candidate, root, *, token='', popen=None):
    candidate = manifest(candidate)
    if build_target(candidate) == 'macos-arm64':
        raise ValueError('Mac update activation is not available in this development build')
    path = executable_path(root, candidate)
    if not verified(path, candidate['asset']):
        raise DownloadedLauncherUnavailable('Downloaded launcher is missing or changed; download again')
    env = dict(os.environ, FREEVIDEO_LAUNCHER_HOME=str(Path(root).absolute().resolve()))
    if token:
        env['GITHUB_TOKEN'] = token  # Session only; never stored in a receipt or command.
    kwargs = dict(cwd=path.parent, env=env, close_fds=True)
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    process = (popen or subprocess.Popen)([str(path)], **kwargs)
    # Start first: a spawn error must not replace the approved startup receipt.
    save(Path(root) / 'updates' / 'active.json', dict(approved=True, candidate=candidate))
    return process


def forward_approved(current, root):
    """Opening the original shortcut starts the last approved newer EXE."""
    if current.get('packaging') in ('onedir', 'app'):
        return False  # Keep the user's folder distribution; do not switch to onefile.
    try:
        receipt = json.loads((Path(root)/'updates/active.json').read_text(encoding='utf-8'))
        if receipt.get('approved') is not True:
            return False
        candidate = manifest(receipt.get('candidate'))
        if not newer(candidate, current):
            return False
        if executable_path(root, candidate).resolve() == Path(sys.executable).resolve():
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
    updates = Path(root) / 'updates'
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
    def __init__(self, current, root, token=None):
        # Python 3.9 on Windows can leave a nonexistent relative path relative
        # in resolve(). Updates must survive changing cwd to the EXE directory.
        self.current, self.root = build_identity(current), Path(root).absolute().resolve()
        self.token = token if token is not None else os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN', '')
        self.state = dict(status='idle')
        self.available = None
        # Older launchers saved this as a mandatory update. Read it only as a
        # cached reminder; a known newer version never prevents using this one.
        for name in ('available.json', 'required.json'):
            try:
                candidate = manifest(json.loads((self.root / 'updates' / name).read_text(encoding='utf-8')))
                if newer(candidate, self.current) and (self.available is None or newer(candidate, self.available)):
                    self.available = candidate
            except (OSError, ValueError, TypeError):
                pass
        self.thread = None
        self.cancelled = threading.Event()

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def run(self, operation, candidate=None, *, background=False):
        """A background check keeps the shown state and ignores offline failures."""
        if self.busy:
            return
        if operation not in ('check', 'download'):
            raise ValueError('Unknown update operation')
        if operation == 'download' and self.current.get('packaging') in ('onedir', 'app'):
            kind = 'application' if self.current.get('packaging') == 'app' else 'folder'
            raise ValueError('Download and extract the updated %s ZIP from the release page' % kind)
        self.cancelled.clear()
        quiet = background and operation == 'check'
        if not quiet:
            self.state = dict(status='checking' if operation == 'check' else 'downloading', candidate=candidate)
        def work():
            try:
                if operation == 'check':
                    found = check(self.current, self.token)
                    if found and (self.available is None or newer(found, self.available)):
                        self.available = found
                        save(self.root / 'updates/available.json', found)
                    self.state = dict(status='available' if self.available else 'current', candidate=self.available)
                else:
                    def progress(value):
                        self.state = dict(status='downloading', candidate=candidate, progress=value)
                    download(candidate, self.root, self.token, progress=progress, cancel=self.cancelled)
                    self.state = dict(status='ready', candidate=candidate)
            except Exception as error:
                if quiet:
                    return  # The next periodic check retries without a reminder.
                message = Redactor([(self.token, '<REDACTED>')] if self.token else []).text(str(error))
                if isinstance(error, urllib.error.HTTPError) and error.code in (401, 403, 404):
                    message = 'GitHub update access unavailable. A private repository needs a GitHub token with read access; also check API rate limits.'
                self.state = dict(status='cancelled' if self.cancelled.is_set() else 'error', error=message,
                                  candidate=candidate or self.available)
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
