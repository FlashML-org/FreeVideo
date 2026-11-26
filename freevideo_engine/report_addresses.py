"""Web addresses in report text: what of an address may stay.

A report (a problem report page, a download failure record, a diagnostic report) keeps no user, password,
query or fragment of an address. Its path names the file that failed, so on the public hosts FreeVideo
downloads from it stays; on hosts that keep anyone's repositories (Hugging Face, ModelScope, GitHub ...)
only FreeVideo's own repositories keep it, as the first part of a path there is usually its owner's name.
On any other host the caller decides (kept_path's other_hosts). One rule for every report.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

# A web address: scheme, authority (user, host, port) and the rest (path, query, fragment).
URL = re.compile(r'''\b(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<authority>[^\s/?#'"<>]*)(?P<rest>[^\s'"<>]*)''')
IP_HOST = re.compile(r'^(?:\[[^\]]*\]|\d{1,3}(?:\.\d{1,3}){3})(?=:\d+$|$)')
# The shipped files that name download addresses and repositories; their hosts are public_hosts.
DOWNLOAD_CONFIGS = ('bootstrap_versions.json', 'curl_windows.json', 'dependencies.json', 'depth_reference_model.json',
                    'frame_preview_weights.json', 'frame_upscale_model.json', 'gcc_linux.json', 'git_linux.json',
                    'git_macos.json', 'macos_reference_catalog.json', 'model_files.json', 'model_shares.json',
                    'prepared_models.json', 'rife_interp_model.json', 'subtitle_pack.json', 'swap_lora.json',
                    'torch_downloads.json', 'uv_downloads.json', 'windows_arm64.json')
# Hosts that keep anyone's repositories, shares or channels: the first part of a path there is usually
# its owner's name, so only the repositories FreeVideo itself uses (own_repositories: "owner/name", or the
# channel alone on conda.anaconda.org, after the parts the host puts before it; "x.git" is "x") keep
# their path. Package hosts (pypi.org, files.pythonhosted.org, download.pytorch.org ...) hold public
# packages only and keep any path.
REPOSITORY_HOSTS = {
    'huggingface.co': r'(?:api/)?(?:models/|datasets/|spaces/)?',
    'hf-mirror.com': r'(?:api/)?(?:models/|datasets/|spaces/)?',
    'modelscope.cn': r'(?:api/v1/)?(?:models/|datasets/)?',
    'www.modelscope.cn': r'(?:api/v1/)?(?:models/|datasets/)?',
    'github.com': '',
    'api.github.com': r'(?:repos/)?',
    'ghfast.top': r'(?:https?:/+github\.com/)?',
    'pan.quark.cn': '',
    'conda.anaconda.org': '',
}
ONE_PART_HOSTS = frozenset(('conda.anaconda.org',))


@lru_cache(maxsize=1)
def _shipped():
    """The download addresses FreeVideo names and the repository values ("owner/name") of its configuration."""
    addresses, repositories = [], []
    try:
        from . import network
        addresses += [url for table in network.SOURCES.values() for url in table.values()]
        addresses += list(network.PYTHON_MIRRORS.values())
    except Exception:
        pass
    try:
        from . import launcher_update
        addresses += [launcher_update.MIRROR_BASE, launcher_update.API, launcher_update.WEB]
        repositories.append(launcher_update.REPOSITORY)
    except Exception:
        pass

    def walk(value, key=''):
        if isinstance(value, dict):
            for name, item in value.items():
                walk(item, str(name))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, str) and 'repo' in key.lower() and re.fullmatch(r'[\w.-]+/[\w.-]+', value):
            repositories.append(value)
    for name in DOWNLOAD_CONFIGS:
        try:
            text = (Path(__file__).parent / name).read_text(encoding='utf-8')
            addresses += re.findall(r'''https?://[^\s"'<>\\]+''', text)
            walk(json.loads(text))
        except (OSError, ValueError, RecursionError):
            continue
    return tuple(addresses), tuple(repositories)


def _host(address):
    try:
        host = urlsplit(address).hostname
    except ValueError:
        return None
    return host.lower() if host and not IP_HOST.match(host) else None


@lru_cache(maxsize=1)
def public_hosts():
    """The public hosts FreeVideo downloads from: the download sources and mirrors, the release hosts and
    every host in the shipped download configuration. A host that cannot be read is simply not on it."""
    return frozenset(filter(None, map(_host, _shipped()[0])))


def _repository(host, path):
    """"owner/name" (a channel on a one-part host) of an address's path on a repository host, or None."""
    parts = r'([^/]+)' if host in ONE_PART_HOSTS else r'([^/]+/[^/]+)'
    found = re.match(r'/*' + REPOSITORY_HOSTS[host] + parts, path, flags=re.IGNORECASE)
    return _repository_name(found.group(1)) if found else None


def _repository_name(value):
    return re.sub(r'\.git$', '', value.lower())


@lru_cache(maxsize=1)
def own_repositories():
    """The repositories, shares and channels FreeVideo itself uses on the repository hosts."""
    addresses, repositories = _shipped()
    own = {_repository_name(value) for value in repositories}
    for address in addresses:
        host = _host(address)
        if host in REPOSITORY_HOSTS:
            own.add(_repository(host, urlsplit(address).path))
    own.discard(None)
    return frozenset(own)


def kept_path(host, rest, *, other_hosts=False):
    """What of an address's path, query and fragment may stay in a report: the path alone, or nothing.

    host is the host and port as written. On a host neither public nor a repository host (and not an IP
    address) the path stays only with other_hosts.
    """
    path = re.split(r'[?#]', rest, maxsplit=1)[0]
    name = re.sub(r':\d*$', '', host).lower()
    if IP_HOST.match(host):
        return ''
    if name in REPOSITORY_HOSTS:
        return path if _repository(name, path) in own_repositories() else ''
    return path if name in public_hosts() or other_hosts else ''


def site(match, *, other_hosts=False):
    """Replacement for a URL match: no user or password, an IP host as <ip>, the path kept_path keeps."""
    host = match.group('authority').rpartition('@')[2]
    if IP_HOST.match(host):
        return match.group('scheme') + IP_HOST.sub('<ip>', host)
    return match.group('scheme') + host + kept_path(host, match.group('rest'), other_hosts=other_hosts)


def record(match):
    """Replacement for a URL match where the caller removes users and passwords itself (a download failure
    record, a diagnostic report): the authority as written, then kept_path's path; any host keeps its path
    unless it is an IP address or a repository host's other repository."""
    host = match.group('authority').rpartition('@')[2]
    return match.group('scheme') + match.group('authority') + kept_path(host, match.group('rest'), other_hosts=True)
