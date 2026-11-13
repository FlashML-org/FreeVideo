"""The launcher's "Report a problem" page: the installation/launcher form with the failure already in it.

Only the sentence the failure card shows travels. It has gone through the launcher's redaction already;
paths and long tokens are removed once more here, and the end of a long log gives way to fit the address.
"""
from urllib.parse import urlencode

from .feedback import scrub

ISSUE_FORM = 'https://github.com/FlashML-org/FreeVideo/issues/new'
# GitHub refuses very long addresses.
MAX_ADDRESS = 7000
MAX_PROBLEM = 600
MAX_REPORT = 2500


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


def issue_url(error, version=None, title=None, zh=False, environment=''):
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
    head = (('Environment: ' + scrub(environment).strip() + '\n\n') if str(environment).strip() else '')

    def fit(limit):
        return head + (report if len(report) <= limit else report[:limit // 3] + '\n...\n' + report[-(limit - limit // 3 - 5):])

    def build(body):
        params = {'template': 'bug_launcher.yml', 'title': f'[{place}] ' + (str(title).strip() if title else place),
                  'problem': problem[:MAX_PROBLEM], 'report': body}
        if version:
            params['version'] = 'v' + str(version).lstrip('v')
        return ISSUE_FORM + '?' + urlencode(params)

    limit = MAX_REPORT
    while len(build(fit(limit))) > MAX_ADDRESS and limit > 50:
        limit = int(limit * 0.8)
    return build(fit(limit))
