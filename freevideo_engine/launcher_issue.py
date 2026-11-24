"""The launcher's "Report a problem" page: the installation/launcher form with the failure already in it.

Only the sentence the failure card shows travels. It has gone through the launcher's redaction already;
paths and long tokens are removed once more here, and the end of a long log gives way to fit the address.
"""
import re
from urllib.parse import urlencode

from .feedback import scrub
from .long_paths import io_path

ISSUE_FORM = 'https://github.com/FlashML-org/FreeVideo/issues/new'
# GitHub refuses very long addresses.
MAX_ADDRESS = 7000
MAX_PROBLEM = 600
MAX_REPORT = 2500
# The update failure card: the report page carries the last lines of the per-source failure log.
UPDATE_TITLES = ('Update', '更新')
UPDATE_SOURCE_LINES = 5
UPDATE_SOURCE_LINE_CHARS = 240
# IPv4 first (so ::ffff:192.0.2.1 leaves no part of the address), then IPv6 in the full form (four groups or more,
# so a time such as 23:00:00 stays) and in the compressed form (::).
_IPV4 = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b')
_IPV6 = re.compile(r'(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{0,4}\b'
                   r'|(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{1,4})*::[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{1,4})*(?![\w:])')
# Below this many characters of error text the log lines give way first, oldest first.
MIN_REPORT_WITH_LOG = 500


def _lines(error):
    return [line for line in scrub(error).splitlines() if line.strip()]


def product_version(fallback=None):
    """The 0.4.x release of this launcher; the fallback (the build number) only when no release says."""
    try:
        from pathlib import Path
        from .release_notes import installed_details
        return installed_details(Path(__file__).parent, fallback).get('product_version') or fallback
    except Exception:
        return fallback


def machine_hardware(engine):
    """The hardware record of the installation at engine (machine.json), or {} when it is not readable."""
    try:
        import json
        from pathlib import Path
        from .long_paths import io_path
        hardware = json.loads(io_path(Path(engine) / 'machine.json').read_text(encoding='utf-8'))['hardware']
        return hardware if isinstance(hardware, dict) else {}
    except Exception:
        return {}


def environment_line(version=None, hardware=None):
    """One line about this computer: release, system, GPU, driver and memory. No paths, user names or build numbers."""
    hardware = hardware if isinstance(hardware, dict) else {}

    def gib(key):
        value = hardware.get(key)
        return '%.1f GiB' % (value / 2**30) if type(value) is int and value > 0 else ''

    gpu = ', '.join(part for part in (str(hardware.get('gpu_name') or ''), gib('vram_total') + ' VRAM' if gib('vram_total') else '') if part)
    parts = ['FreeVideo ' + str(version).lstrip('v') if version else '', str(hardware.get('system') or ''), gpu,
             'driver ' + str(hardware['driver_version']) if hardware.get('driver_version') else '',
             gib('ram_total') + ' RAM' if gib('ram_total') else '']
    return scrub(' · '.join(part for part in parts if part))[:300]


def adapter_line():
    """The display-adapter line the report ZIP carries: model names only, or that they could not be read."""
    try:
        from .display_adapters import adapters, report_line
        return scrub(report_line(adapters()))[:300]
    except Exception:
        return 'Display adapters: not available'


def update_source_lines(root, count=UPDATE_SOURCE_LINES):
    """A heading and the last lines of the update-source failure log, redacted once more; [] when there is no readable line."""
    try:
        from .launcher_update import SOURCE_LOG
        path = io_path(root) / 'updates' / SOURCE_LOG
        if not path.is_file() or path.is_symlink():
            return []
        with open(path, 'rb') as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 16384))
            text = handle.read().decode('utf-8', errors='replace')
        lines = [' '.join(_IPV6.sub('<ip>', _IPV4.sub('<ip>', scrub(line))).split())[:UPDATE_SOURCE_LINE_CHARS]
                 for line in text.splitlines() if line.strip()]
        # A heading and a list: the report page strips each line, so the entries start with "- ", not with spaces.
        return ['Update sources:'] + ['- ' + line for line in lines[-count:]] if lines else []
    except Exception:
        return []


def update_report_lines(title):
    """The diagnostic lines for the report page of an update failure; [] for any other failure or when anything goes wrong."""
    try:
        if str(title).strip() not in UPDATE_TITLES:
            return []
        from .desktop_runtime import launcher_root
        return update_source_lines(launcher_root())
    except Exception:
        return []


def issue_url(error, version=None, title=None, zh=False, environment='', diagnostics=()):
    lines = _lines(error)
    # A successful download-tool check can open the text (its Sysnative "not found" is normal); the cause is not that line.
    from .failure_details import _without_ready_curl_diagnostics
    causes = _lines(_without_ready_curl_diagnostics(str(error))) or lines
    first = (causes[-1] if causes and causes[0].startswith('Traceback') else (causes[0] if causes else '')).strip()
    place = '启动器' if zh else 'Launcher'
    problem = ('FreeVideo 启动器显示了一个错误。\n' if zh else 'The FreeVideo launcher showed an error.\n') + first[:300]
    report = '\n'.join(lines)
    # The environment line is for triage, so it stays English in either language and is kept whole;
    # only the error text gives way to fit the address.
    # The diagnostic lines (adapter model) come first and are kept whole, whatever the error is.
    notes = [scrub(line).strip() for line in diagnostics if str(line).strip()]

    def build(body):
        params = {'template': 'bug_launcher.yml', 'title': f'[{place}] ' + (str(title).strip() if title else place),
                  'problem': problem[:MAX_PROBLEM], 'report': body}
        if version:
            params['version'] = 'v' + str(version).lstrip('v')
        return ISSUE_FORM + '?' + urlencode(params)

    def attempt(lines, floor):
        head = '\n'.join(([('Environment: ' + scrub(environment).strip())] if str(environment).strip() else []) + lines)
        head += '\n\n' if head else ''

        def fit(limit):
            return head + (report if len(report) <= limit else report[:limit // 3] + '\n...\n' + report[-(limit - limit // 3 - 5):])

        limit = MAX_REPORT
        while len(build(fit(limit))) > MAX_ADDRESS and limit > floor:
            limit = int(limit * 0.8)
        return build(fit(limit))

    # The log lines (a heading, then "- " entries) are the first to give way: a long line, or one in Chinese, whose
    # address form is nine times as long, must not make the address too long for GitHub. Oldest entries go first.
    if 'Update sources:' in notes:
        heading = notes.index('Update sources:')
        count = 0
        while heading + 1 + count < len(notes) and notes[heading + 1 + count].startswith('- '):
            count += 1
        before, after = notes[:heading], notes[heading + 1 + count:]
        entries = notes[heading + 1:heading + 1 + count]
        for dropped in range(count + 1):
            kept = before + (['Update sources:'] + entries[dropped:] if dropped < count else []) + after
            url = attempt(kept, MIN_REPORT_WITH_LOG)
            if len(url) <= MAX_ADDRESS:
                return url
        notes = before + after
    return attempt(notes, 50)
