"""The diagnostic summary a user may add to a suggestion (it is put in the GitHub page).

Only the redacted fields the report form already uses: version, hardware,
settings and timings. No prompt, reference or picture is read here.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .exit_diagnostics import OWN_MEMORY_KEYS
from .long_paths import AUTHORIZATION, EMAIL

# The summary travels in a GitHub page address, which has to stay short.
MAX_DIAGNOSTICS = 3000
# Anything that looks like a file location or a long secret-like token is replaced.
# A location may contain spaces, so it is removed to the end of the line or quote, except what follows
# a file name in its last part: "C:\\Users\\...\\x.exe rename denied" keeps "rename denied". A drive
# letter is not the end of a word ("http://..." is no drive "p:").
# A file URL first (file:///home/..., file://localhost/C:/..., %-escaped): its slashes would not start a path below.
PATH = re.compile(r'''(?<![\w/])(?i:file):(?://[^/\s"'<>]*)?(?:/+|(?=[A-Za-z]:[\\/]))[^'"<>|\r\n]*'''
                  r'|(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\|~[\\/])[^\'"<>|\r\n]+|(?<![\w/.])/[\w.\-]+(?:/[^\'"<>|\r\n]+)+')
AFTER_FILE_NAME = re.compile(r'\.[A-Za-z0-9]{1,8}(?=[\s:,;)])')
USER_FOLDERS = ('users', 'home', 'documents and settings')
TOKEN = re.compile(r'\b[A-Za-z0-9_\-]{32,}\b')
# A web address loses its user, password, query and fragment. On a public download host FreeVideo uses
# (public_hosts) it keeps its path, which names the file that failed; on any other host, or an IP
# address (replaced too), only the scheme, host and port stay. The reason after it stays as well.
URL = re.compile(r'''\b(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<authority>[^\s/?#'"<>]*)(?P<rest>[^\s'"<>]*)''')
IP_HOST = re.compile(r'^(?:\[[^\]]*\]|\d{1,3}(?:\.\d{1,3}){3})(?=:\d+$|$)')
# One pass, so a path inside a kept address ("/-/binary/...") is not taken for a local one.
PLACE = re.compile(PATH.pattern + '|' + URL.pattern)
# The shipped files that name download addresses and repositories; their hosts are public_hosts.
DOWNLOAD_CONFIGS = ('bootstrap_versions.json', 'curl_windows.json', 'dependencies.json', 'depth_reference_model.json',
                    'frame_preview_weights.json', 'frame_upscale_model.json', 'gcc_linux.json', 'git_linux.json',
                    'git_macos.json', 'macos_reference_catalog.json', 'model_files.json', 'model_shares.json',
                    'prepared_models.json', 'rife_interp_model.json', 'subtitle_pack.json', 'swap_lora.json',
                    'torch_downloads.json', 'uv_downloads.json', 'windows_arm64.json')
# Hosts that keep anyone's repositories, shares or channels: the first part of a path there is usually
# its owner's name, so only the repositories FreeVideo itself uses (own_repositories: "owner/name", or the
# channel alone on conda.anaconda.org, after the parts the host puts before it; "x.git" is "x") keep
# their path. Package hosts (pypi.org, files.pythonhosted.org,
# download.pytorch.org ...) hold public packages only and keep any path.
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
HARDWARE_KEYS = ('gpu_name', 'vram_total', 'ram_total', 'system', 'architecture', 'driver_version',
                 'torch_version', 'cuda_version')

# Free physical RAM and commit headroom stay apart: a refusal text is cut
# short, and the two numbers say which of them left too little.
MEMORY_KEYS = ('physical_available_bytes', 'commit_available_bytes', 'available_bytes', 'total_bytes')


def _place(match):
    value = match.group(0)
    parts = re.split(r'[\\/]', value)
    last = parts[-1]
    # The last part right under the users' folders is a user's own folder: its name may hold a dot and a
    # space ("J.R Smith") and goes as a whole.
    name = None if len(parts) > 1 and parts[-2].lower() in USER_FOLDERS else AFTER_FILE_NAME.search(last)
    return '<path>' + (value[len(value) - len(last) + name.end():] if name else '')


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


def _site(match):
    host = match.group('authority').rpartition('@')[2]
    if IP_HOST.match(host):
        return match.group('scheme') + IP_HOST.sub('<ip>', host)
    rest = re.split(r'[?#]', match.group('rest'), maxsplit=1)[0]
    name = re.sub(r':\d*$', '', host).lower()
    if name not in public_hosts() or (name in REPOSITORY_HOSTS and _repository(name, rest) not in own_repositories()):
        rest = ''
    return match.group('scheme') + host + rest


def _found(match):
    return _site(match) if match.group('scheme') else _place(match)


def scrub(value):
    value = AUTHORIZATION.sub(r'\1\2\3<redacted>', str(value))
    value = PLACE.sub(_found, value)
    return TOKEN.sub('<token>', EMAIL.sub('<email>', value))


def summary(report, machine=None):
    """The diagnostic summary: the same redacted fields the report form uses, no prompt or picture.

    report is the parsed diagnostic report of the latest generation (or None);
    machine is the installation's machine record (or None).
    """
    out = {}
    hardware = {}
    if isinstance(machine, dict) and isinstance(machine.get('hardware'), dict):
        hardware.update({k: v for k, v in machine['hardware'].items() if k in HARDWARE_KEYS})
    analysis = report.get('analysis') if isinstance(report, dict) else None
    if isinstance(analysis, dict):
        if isinstance(analysis.get('hardware'), dict):
            hardware.update({k: v for k, v in analysis['hardware'].items() if k in HARDWARE_KEYS})
        for key in ('version', 'status', 'request_seconds', 'geometry', 'config', 'stages', 'budgets'):
            if key in analysis:
                out[key] = analysis[key]
        planning = analysis.get('resource_planning')
        memory = planning[-1].get('system_memory') if isinstance(planning, list) and planning and isinstance(planning[-1], dict) else None
        if isinstance(memory, dict):
            memory = {k: memory[k] for k in MEMORY_KEYS if type(memory.get(k)) is int}
            own = planning[-1].get('own_memory')
            if isinstance(own, dict):
                memory.update({k: own[k] for k in OWN_MEMORY_KEYS if type(own.get(k)) is int})
            if memory:
                out['memory'] = memory
        errors = analysis.get('errors')
        if isinstance(errors, list):
            out['errors'] = [scrub(e)[:300] for e in errors[:3]]
    if hardware:
        out['hardware'] = hardware
    # Bound the whole summary; drop the largest optional parts first.
    for key in ('stages', 'config', 'errors', 'budgets', 'geometry', 'memory'):
        if len(json.dumps(out, separators=(',', ':'), ensure_ascii=False)) <= MAX_DIAGNOSTICS:
            break
        out.pop(key, None)
    return out


def setup_hardware(machine):
    """The hardware the setup run recorded for this machine's device, or {}.

    machine.json carries only the device identity; the totals are in the plan of
    the setup run it names, the same place the progress forecast reads them from.
    """
    try:
        from pathlib import Path
        from .effort_forecast import same_device
        device = machine.get('device_identity') if machine.get('device_backend') == 'mps' else machine.get('gpu_uuid')
        with (Path(machine['setup_run']) / 'plan.json').open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        found = json.loads(raw).get('inventory', {}).get('hardware', {}) if len(raw) <= 1024 * 1024 else {}
        return found if isinstance(found, dict) and same_device(found, device) else {}
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return {}


def collect(report_id=None):
    """The diagnostic summary for the latest generation, or for this machine alone before any."""
    report = machine = None
    try:
        from .comfy_progress import REPORTS
        with REPORTS.lock:
            token = report_id if report_id in REPORTS.runs else next(reversed(REPORTS.runs), None)
        if token:
            report = json.loads(REPORTS.snapshot(token))
    except (KeyError, OSError, RuntimeError, ValueError, TypeError):
        report = None
    try:
        from .comfy_bridge import installation
        machine = installation()[1]
        if not isinstance(machine.get('hardware'), dict):
            machine = dict(machine, hardware=setup_hardware(machine))
    except Exception:  # Setup problems must not stop a suggestion from being sent.
        machine = None
    return summary(report, machine)
