"""The diagnostic summary a user may add to a suggestion (it is put in the GitHub page).

Only the redacted fields the report form already uses: version, hardware,
settings and timings. No prompt, reference or picture is read here.
"""
import json
import re

from .exit_diagnostics import OWN_MEMORY_KEYS
from .long_paths import AUTHORIZATION, EMAIL, ends_at_user_folder
# What of a web address stays is one rule for every report (report_addresses); these names stay here too.
from .report_addresses import DOWNLOAD_CONFIGS, URL, own_repositories, public_hosts, site

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
TOKEN = re.compile(r'\b[A-Za-z0-9_\-]{32,}\b')
# One pass, so a path inside a kept address ("/-/binary/...") is not taken for a local one.
PLACE = re.compile(PATH.pattern + '|' + URL.pattern)
HARDWARE_KEYS = ('gpu_name', 'vram_total', 'ram_total', 'system', 'architecture', 'driver_version',
                 'torch_version', 'cuda_version')

# Free physical RAM and commit headroom stay apart: a refusal text is cut
# short, and the two numbers say which of them left too little.
MEMORY_KEYS = ('physical_available_bytes', 'commit_available_bytes', 'available_bytes', 'total_bytes')


def _place(match):
    value = match.group(0)
    last = re.split(r'[\\/]', value)[-1]
    # A user's own folder (under Users, /home, /var/home, a profiles share ...) goes as a whole: its name
    # may hold a dot and a space ("J.R Smith").
    name = None if ends_at_user_folder(value) else AFTER_FILE_NAME.search(last)
    return '<path>' + (value[len(value) - len(last) + name.end():] if name else '')


def _found(match):
    return site(match) if match.group('scheme') else _place(match)


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
