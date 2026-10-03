"""Collect the current installation's evidence into a local, redacted report."""
import json
from pathlib import Path

from . import diagnostic_resources as d


def _json(path, limit=2*1024*1024):
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    return d.mapping(json.loads(raw)) if len(raw) <= limit else {}


def collect(root, since):
    """Never pick a different/old installation merely because it is the latest."""
    try:
        root = Path(root).resolve()
        run = Path(_json(root/'machine.json')['setup_run']).resolve()
        if run.parent != (root/'setup-runs').resolve() or (run/'plan.json').stat().st_mtime < since-1:
            return {}
        state = _json(run/'status.json')
        rows = []
        try:
            with (run/'ram.jsonl').open('rb') as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size-65536))
                lines = stream.read(65536).splitlines()
            if size > 65536:
                lines = lines[1:]
            for line in lines[-32:]:
                try:
                    rows.append(d.mapping(json.loads(line)))
                except (ValueError, UnicodeError):
                    pass
        except OSError:
            pass
        # ComfyUI dependency preparation runs in launcher/host-runs rather
        # than setup-runs. Keep only structured package failure events; the
        # command output remains available in the local manual bundle.
        package_attempts = []
        host_root = root / 'launcher' / 'host-runs'
        if host_root.is_dir():
            candidates = sorted((path for path in host_root.iterdir() if path.is_dir()),
                                key=lambda path: path.name)[-3:]
            for host in candidates:
                try:
                    if host.stat().st_mtime < since - 1:
                        continue
                    with (host / 'network.jsonl').open('rb') as stream:
                        lines = stream.read(256 * 1024).splitlines()[-64:]
                    for line in lines:
                        try:
                            item = d.mapping(json.loads(line))
                        except (ValueError, UnicodeError):
                            continue
                        if item.get('action') != 'package-failed':
                            continue
                        package_attempts.append({key: item[key] for key in
                            ('source', 'route', 'error_type', 'returncode', 'summary') if key in item})
                except OSError:
                    continue
        return dict(state=state, samples=rows, package_attempts=package_attempts[-16:])
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _errors(rows):
    result = []
    for row in d.sequence(rows)[:16]:
        row = d.mapping(row)
        item = d.numbers(row, ('pid', 'parent_pid', 'winerror', 'errno'))
        if type(row.get('exited')) is bool:
            item['exited'] = row['exited']
        # Keep stable error codes separate from the redacted error details.
        item['function'] = ('ProcessVmCounters' if 'ProcessVmCounters' in str(row.get('error', ''))
                            else 'process_memory')
        result.append(item)
    return result


def resources(value):
    state = d.mapping(d.mapping(value).get('state'))
    memory = d.mapping(state.get('memory'))
    result = d.resources(dict(ram=memory))
    result['ram'].update(d.numbers(memory, ('monitor_unreadable_samples', 'monitor_recoveries',
                                           'monitor_consecutive_unreadable')))
    result['memory_read_errors'] = _errors(memory.get('process_tree_memory_errors'))
    samples = [d.mapping(row) for row in d.sequence(d.mapping(value).get('samples'))]
    # Preserve the failure endpoint plus a few preceding read failures.
    selected = [row for row in samples[:-1] if row.get('guard_bytes', row.get('pss_bytes')) is None][-7:]
    if samples:
        selected.append(samples[-1])
        result['ram_last'] = d.numbers(samples[-1], d.RAM_SAMPLE)
    result['ram_samples'] = []
    for row in selected:
        item = d.numbers(row, d.RAM_SAMPLE + ('elapsed_seconds', 'consecutive_unreadable'))
        item['sample_complete'] = row.get('guard_bytes', row.get('pss_bytes')) is not None
        item['unreadable_memory_pids'] = [pid for pid in d.sequence(row.get('unreadable_memory_pids'))[:16]
                                           if type(pid) is int and pid >= 0]
        item['memory_read_errors'] = _errors(row.get('memory_read_errors'))
        result['ram_samples'].append(item)
    return result


def enrich(result, value):
    """Summarize installation resources, stages and failures."""
    value = d.mapping(value)
    if not value:
        result['coverage']['installation_memory'] = False
        return result
    state = d.mapping(value.get('state'))
    result['worker_resources'] = resources(value)
    result['request_memory_peaks'] = {key: amount for key, amount in result['worker_resources']['ram'].items()
                                      if key.startswith('process_tree_peak_') and type(amount) in (int, float)}
    result['hardware_stage'] = 'installation_start'
    result['coverage']['installation_memory'] = bool(result['worker_resources']['ram'] or
                                                     result['worker_resources']['ram_samples'])
    result['coverage']['worker_memory'] = bool(result['worker_resources']['ram'])
    plan = d.mapping(state.get('plan'))
    estimate = d.mapping(plan.get('installation_resources') or plan.get('policy_estimate'))
    result['budgets'].update(d.numbers({'ram': estimate.get('ram_budget_bytes')}, ('ram',)))
    for row in d.sequence(state.get('steps'))[-32:]:
        row = d.mapping(row)
        # Step labels are implementation-owned, but still use an explicit list.
        stage = row.get('label') if row.get('label') in (
            'models', 'prepare', 'kernels', 'storage', 'torch', 'packages', 'unified-packages',
            'encoder-packages', 'engine-packages', 'dependency-check', 'freeze') else 'installation'
        item = dict(stage=stage, **d.numbers(row, ('seconds',)))
        item['resources'] = d.resources(dict(ram=row.get('memory')))
        item['state'] = row.get('status') if row.get('status') in ('running', 'complete', 'failed') else 'unknown'
        result['stages'].append(item)
        if item['state'] == 'failed':
            result['failure_stage'] = stage
    reason = str(state.get('resource_guard', ''))
    if 'RAM monitoring failed' in reason:
        result['errors'].append(dict(kind='memory_monitor_unavailable', phase=result.get('failure_stage', 'installation')))
    elif 'crossed its RAM budget' in reason:
        result['errors'].append(dict(kind='memory_pressure', phase=result.get('failure_stage', 'installation')))
    for row in d.sequence(value.get('package_attempts'))[-16:]:
        row = d.mapping(row)
        item = dict(kind='package_source_failure')
        for key in ('source', 'route', 'error_type', 'returncode', 'summary'):
            if key in row and (key == 'returncode' or isinstance(row[key], (str, int, float, bool))):
                item[key] = row[key]
        result['errors'].append(item)
    result['diagnostic_revision'] = 6
    return result


def write(root, since, report):
    """Retain complete structured evidence without changing installation status."""
    try:
        from .diagnostics import Redactor
        from .diagnostic_summary import summary_report
        from .monitoring import save
        root = Path(root)
        evidence = collect(root, since)
        payload = dict(report, schema_version=1, kind='installation', installation=evidence)
        redactor = Redactor([(str(root), '<INSTALL>')])
        cleaned = json.loads(redactor.data(json.dumps(payload).encode('utf-8'), '.json'))
        cleaned['analysis'] = enrich(summary_report(cleaned), cleaned['installation'])
        cleaned['analysis']['kind'] = 'installation'
        target = root / 'launcher' / 'installation.debug.json'
        save(target, cleaned)
        return target
    except Exception:
        return None
