"""Measure a complete local baseline when needed, then run a bounded search."""
import argparse
import copy
from contextlib import nullcontext
import html
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import traceback

from .bootstrap import DEFAULT_ROOT
from .diagnostics import latest
from .hardware import detect, GiB
from .kernel_capabilities import available_backends
from .locking import runtime_lock, LOCK_ENV
from . import processes
from .system import memory_complete, memory_peak, windows
from .monitoring import save
from .policy import choose
from .terminal_ui import TerminalUI
from . import tuning


def capacity_flags(command):
    result = {}
    for index, argument in enumerate(command):
        for name in ('vram_gib', 'ram_gib'):
            flag = '--' + name.replace('_', '-')
            if argument == flag:
                result[name] = float(command[index + 1])
            elif argument.startswith(flag + '='):
                result[name] = float(argument.split('=', 1)[1])
    return result


class NoCompleteBaseline(ValueError):
    """Inputs may still be reusable; failed measurements never are a baseline."""


def artifact_entries(manifest):
    """Read portable paths and native Windows paths from earlier reports."""
    files = manifest.get('files') if isinstance(manifest, dict) else manifest
    if not isinstance(files, list):
        raise ValueError('Invalid reference artifact manifest')
    entries = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ValueError('Invalid reference artifact path')
        name = item['path'].replace('\\', '/')
        if name in entries:
            raise ValueError('Duplicate reference artifact path: ' + name)
        entries[name] = item
    return entries


def read_baseline(path, expected):
    directory = path.parent if path.is_file() else path
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    if report.get('baseline_identity') is not None:
        errors = [] if report['baseline_identity'] == expected else ['Bootstrap baseline identity changed']
    else:
        inventory = json.loads((directory / 'inventory.json').read_text(encoding='utf-8'))
        errors = tuning.report_identity_errors(inventory, expected)
    if errors:
        raise ValueError('Run test again on this installation before optimizing: ' + '; '.join(errors))
    from .validation import validate_metrics
    usable = []
    for row in report.get('cases', []):
        if row.get('status') != 'complete':
            continue
        preencoded = row.get('input_mode') == 'preencoded'
        validate_metrics(row, row['geometry'], preencoded=preencoded)
        if (row['engine'].get('sampling_plan', {}).get('enabled')
                or row['engine'].get('config', {}).get('steps') != 8):
            continue  # Optimizer comparisons require the unchanged eight-step sampler.
        if not isinstance(row['id'], str) or Path(row['id']).name != row['id'] or row['id'] in ('.', '..'):
            raise ValueError('Invalid baseline case identifier')
        case = directory / row['id']
        manifest = json.loads((case / 'artifacts.json').read_text(encoding='utf-8'))
        # The test's hashes bind the exact prompt/conditioning/latent reference.
        entries = artifact_entries(manifest)
        names = ['video.artifacts/conditioning.pt', 'video.artifacts/latents.pt']
        if not preencoded:
            names.append('prompt.txt')
        for name in names:
            if name not in entries or tuning.digest(case / name) != entries[name]['sha256']:
                raise ValueError('Reference artifact is missing or changed: ' + str(case / name))
        if not preencoded and (case / 'prompt.txt').read_text(encoding='utf-8') != row['prompt']:
            raise ValueError('Test prompt changed')
        from .validation import validate_artifacts
        validate_artifacts(case, row['geometry'], preencoded=preencoded)
        usable.append(row)
    if not usable:
        raise NoCompleteBaseline('No complete baseline; a fresh full request is required, not a passing probe')
    return directory, report, usable



def capacity_attention(command):
    for index, argument in enumerate(command):
        if argument == '--attention':
            return command[index + 1]
        if argument.startswith('--attention='):
            return argument.split('=', 1)[1]
    raise ValueError('Recovery input has no recorded attention choice')


def recovery_input(path, expected):
    """Reuse only an intact input of a recoverable failure, never its metrics."""
    from .adaptive import classify_failure
    directory = path.parent if path.is_file() else path
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    if report.get('baseline_identity') is not None:
        errors = [] if report['baseline_identity'] == expected else ['Bootstrap baseline identity changed']
    else:
        inventory = json.loads((directory / 'inventory.json').read_text(encoding='utf-8'))
        errors = tuning.report_identity_errors(inventory, expected)
    if errors:
        raise ValueError('Failed-run input identity changed: ' + '; '.join(errors))
    for row in reversed(report.get('cases', [])):
        status = row.get('status')
        failure = classify_failure(row.get('error', ''), row.get('engine'))
        two_pass_input = status == 'complete' and row.get('engine', {}).get('sampling_plan', {}).get('enabled')
        if not two_pass_input and status not in ('ram_guard', 'trial_memory_guard', 'timeout') and not failure['retryable']:
            continue
        reason = ('Verified input from two-pass generation; measuring a separate single-pass optimization baseline'
                  if two_pass_input else 'Verified input from a recoverable failed request')
        name = row.get('id')
        if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid failed-run case identifier')
        case = directory / name
        manifest = json.loads((case / 'artifacts.json').read_text(encoding='utf-8'))
        entries = artifact_entries(manifest)
        if row.get('input_mode') == 'preencoded':
            copied = case / 'input-conditioning.pt'
            if (copied.is_file() and tuning.digest(copied) == row.get('input_sha256')
                    and tuning.digest(copied) == entries.get('input-conditioning.pt', {}).get('sha256')):
                return dict(conditioning=copied, geometry=row['geometry'], seed=row['seed'],
                            attention=capacity_attention(row['command']), source=str(case),
                            reason=reason)
        # Prefer encoding already paid for, but only if a successful encoder
        # receipt and its original artifact digest both survive.
        encoding = row.get('encoding', {})
        condition = case / 'video.artifacts/conditioning.pt'
        entry = entries.get('video.artifacts/conditioning.pt', {})
        if (encoding.get('success') is True and encoding.get('conditioning_shape', [])[1:] == [5120]
                and condition.is_file() and tuning.digest(condition) == entry.get('sha256')):
            return dict(conditioning=condition, geometry=row['geometry'], seed=row['seed'],
                        attention=row['request']['profile']['engine']['attention'],
                        source=str(case), reason=reason)
        prompt = case / 'prompt.txt'
        if (prompt.is_file() and tuning.digest(prompt) == entries.get('prompt.txt', {}).get('sha256')
                and prompt.read_text(encoding='utf-8') == row.get('prompt')):
            return dict(prompt_file=prompt, geometry=row['geometry'], seed=row['seed'],
                        attention=row['request']['profile']['engine']['attention'],
                        source=str(case), reason=reason)
    raise ValueError('No recoverable failed request has verified inputs. Pass --prompt-file or --conditioning for a new baseline.')


def settle_bootstrap_attempts(machine, case, measured):
    """Known controller stops must not masquerade as an abandoned GPU crash."""
    from .resource_history import ResourceHistory
    path = Path(machine['root']) / 'resource-history.sqlite3'
    if not path.is_file() or measured.get('status') == 'complete':
        return []
    try:
        request = json.loads((case / 'video.request.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        request = {}
    identifiers = {item.get('id') for item in request.get('resource_attempts', []) if isinstance(item, dict)}
    identifiers.add(request.get('encoding_attempt'))
    artifacts = str(case / 'video.artifacts')
    history = ResourceHistory(path)
    settled = []
    for attempt in history.attempts():
        evidence = attempt.get('details', {}).get('decision_evidence', {})
        if (attempt['outcome'] != 'pending' or
                (attempt['id'] not in identifiers and evidence.get('request_artifacts') != artifacts)):
            continue
        status = measured.get('status')
        if status in ('timeout', 'interrupted'):
            outcome = 'cancelled'
        elif status in ('ram_guard', 'trial_memory_guard'):
            outcome = 'resource_failure'
        else:
            outcome = 'discarded'
        settled.append(history.finish(attempt['id'], outcome, details={
            'reason': 'Bootstrap controller stopped: ' + str(status),
            'controlled_worker_stop': outcome != 'discarded', 'bootstrap_case': str(case)}))
    return settled


def bootstrap_baseline(spec, machine, expected, destination, seconds, env, ui):
    """A new full request, with its own artifacts, within the total GPU budget."""
    from .testing import run_process, artifact_manifest, artifact_estimate
    from .validation import validate_metrics, validate_artifacts
    root = destination / 'baseline'
    case = root / '01-bootstrap'
    case.mkdir(parents=True, exist_ok=False)
    canvas = spec['geometry']
    if shutil.disk_usage(case).free < artifact_estimate([{k: canvas[k] for k in ('width', 'height', 'frames')}]):
        raise ValueError('Insufficient disk space for a retained full baseline request')
    preencoded = bool(spec.get('conditioning'))
    input_path = Path(spec['conditioning'] if preencoded else spec['prompt_file']).resolve()
    source_hash = tuning.digest(input_path)
    copied = case / ('input-conditioning.pt' if preencoded else 'prompt.txt')
    shutil.copyfile(input_path, copied)
    if tuning.digest(copied) != source_hash or tuning.digest(input_path) != source_hash:
        raise ValueError('Baseline input changed while it was copied')
    command = [machine['python'], '-m', 'freevideo_engine', 'generate',
        '--conditioning' if preencoded else '--prompt-file', str(copied),
        '--cache', machine['cache'], '--base', machine['base'], '--checkpoint', machine['checkpoint'],
        '--encoder-python', machine['comfy_python'], '--encoder-root', machine['comfy_root'],
        '--model-paths', machine['model_paths'], '--encoder', machine['encoder'],
        '--out', str(case / 'video.mp4'), '--seed', str(spec['seed']),
        '--width', str(canvas['width']), '--height', str(canvas['height']), '--frames', str(canvas['frames']),
        '--attention', spec['attention'], '--no-tuning', '--no-two-pass']
    for key in ('vram_gib', 'ram_gib'):
        if machine.get(key) is not None:
            command += ['--' + key.replace('_', '-'), str(machine[key])]
    row = dict(id=case.name, geometry=canvas, seed=spec['seed'], command=command,
               input_mode='preencoded' if preencoded else 'prompt', status='running',
               input_sha256=source_hash, input_source=str(input_path))
    if not preencoded:
        row['prompt'] = copied.read_text(encoding='utf-8')
    result = dict(schema_version=1, status='running', baseline_identity=expected, cases=[row],
                  scope='New complete baseline, not failed/probe metrics promoted to success',
                  max_seconds=seconds, input_reason=spec.get('reason', 'Explicit input'))
    save(root / 'report.json', result)
    descriptors = (int(os.environ[LOCK_ENV]),)
    environment = dict(env, **{LOCK_ENV: os.environ[LOCK_ENV],
        'PYTHONPATH': os.pathsep.join((str(Path(__file__).resolve().parents[1]), env.get('PYTHONPATH', '')))})
    started = time.perf_counter()
    try:
        ui.begin(case.name, 'Measure a complete baseline', detail='Full request; retained artifacts; shared total time budget')
        row.update(run_process(command, case, env=environment, gpu=machine['gpu_uuid'], timeout=seconds,
                   minimum_ram=(2 if windows() else 1)*GiB, pass_fds=descriptors, ui=ui))
        result['attempt_settlements'] = settle_bootstrap_attempts(machine, case, row)
        if row['status'] != 'complete':
            result['status'] = 'baseline-budget-exhausted' if row['status'] == 'timeout' else 'baseline-failed'
            return root, result, [], time.perf_counter() - started
        for key in ('request', 'engine') + (() if preencoded else ('encoding',)):
            row[key] = json.loads((case / ('video.' + key + '.json')).read_text(encoding='utf-8'))
        validate_metrics(row, canvas, preencoded=preencoded)
        row['artifacts'] = validate_artifacts(case, canvas, preencoded=preencoded)
        from .media import inspect
        row['media'] = inspect(case / 'video.mp4', **{k: canvas[k] for k in ('width', 'height', 'frames', 'fps')})
        shape = row['engine'].get('conditioning_shape') if preencoded else row['encoding'].get('conditioning_shape')
        if not isinstance(shape, list) or len(shape) != 2 or type(shape[0]) is not int or shape[0] < 1 or shape[1] != 5120:
            raise ValueError('A completed baseline must report its actual conditioning shape')
        row['text_tokens'] = shape[0]
        result['status'] = 'complete'
        return root, result, [row], time.perf_counter() - started
    except BaseException as error:
        row.update(status='validation_failed', error=repr(error))
        result.update(status='baseline-failed', error=repr(error))
        raise
    finally:
        result['wall_seconds'] = time.perf_counter() - started
        save(root / 'report.json', result)
        save(case / 'case.json', row)
        artifact_manifest(case)
        ui.end(case.name, success=result['status'] == 'complete', detail=result['status'])


def text_tokens(row):
    return row.get('text_tokens') or row.get('encoding', {}).get('conditioning_shape', [None])[0]



def settle_search_attempts(history, work, measured, trial, *, validation=None, error=None):
    """Settle only this stopped worker's active attempt, including hard exits.

    The journal write can precede optimization.json, so match its retained report
    path rather than depending on the worker having emitted another message.
    """
    from .adaptive import classify_failure
    settled = []
    report = str(work / 'optimization.json')
    for attempt in history.attempts():
        if attempt['outcome'] != 'pending' or attempt.get('details', {}).get('decision_evidence', {}).get('report') != report:
            continue
        status = measured.get('status')
        if status in ('timeout', 'interrupted'):
            outcome, details = 'cancelled', {'reason': status, 'controlled_worker_stop': True}
        elif status in ('ram_guard', 'trial_memory_guard'):
            outcome, details = 'resource_failure', {'reason': status, 'controlled_worker_stop': True}
        elif validation is not None:
            outcome, details = 'success', dict(validation)
        elif error is not None:
            failure = classify_failure(error)
            outcome, details = failure['outcome'], {'failure': failure, 'error': repr(error)}
        elif status != 'complete' or not trial.get('success'):
            failure = trial.get('failure')
            if isinstance(failure, dict) and failure.get('outcome'):
                outcome, details = failure['outcome'], {'failure': failure}
            else:
                outcome, details = 'discarded', {'reason': 'Worker exited without a settled attempt or structured failure'}
        else:
            # A completed full worker still awaits the parent's media checks.
            continue
        settled.append(history.finish(attempt['id'], outcome, details=details))
    return settled


def compare_arrays(first, second):
    import numpy as np
    results = {}
    for name in ('rgb.npy', 'audio.npy'):
        a, b = [np.load(root / name, mmap_mode='r', allow_pickle=False) for root in (first, second)]
        same = a.shape == b.shape and a.dtype == b.dtype
        if same:
            for start in range(len(a)):
                if not np.array_equal(a[start:start + 1].view(np.uint8), b[start:start + 1].view(np.uint8)):
                    same = False
                    break
        results[name] = bool(same)
    return results


def write_report(directory, result):
    save(directory / 'report.json', result)
    search = result.get('search', {})
    def number(value, divisor=1):
        return '—' if value is None else '%.3f' % (value / divisor)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' %
        (html.escape(json.dumps(row['patch'])), html.escape(row.get('status', 'measured')),
         number(row.get('seconds')), number(row.get('recheck_seconds')),
         number(row.get('gpu', {}).get('gpu_peak_bytes'), GiB),
         number(memory_peak(row.get('ram', {})), GiB)) for row in search.get('trials', []))
    selected_directory = result.get('search_directory', 'search')
    if (not isinstance(selected_directory, str) or selected_directory in ('.', '..')
            or Path(selected_directory).name != selected_directory):
        selected_directory = 'search'
    media_path = selected_directory + '/video.mp4'
    media = '<video controls src="%s"></video>' % html.escape(media_path, quote=True) if (directory / media_path).is_file() else ''
    attempts = []
    for item in result.get('search_attempts', []):
        recovery = item.get('recovery') or {}
        cells = [Path(item['directory']).name, item.get('status', 'unknown'),
                 json.dumps(recovery.get('next_patch', {})), recovery.get('reason', ''),
                 number(item.get('wall_seconds'))]
        attempts.append('<tr>' + ''.join('<td>' + html.escape(str(cell)) + '</td>' for cell in cells) + '</tr>')
    attempt_table = ('<h2>Retained search attempts</h2><table><tr><th>Directory</th><th>Status</th>'
                     '<th>Retry configuration</th><th>Reason</th><th>Total elapsed seconds</th></tr>'
                     + ''.join(attempts) + '</table>') if attempts else ''
    plans = [('Initial search', result.get('candidate_plan', {}))]
    plans += [('Replanned search %d' % (index + 1), plan)
              for index, plan in enumerate(search.get('candidate_plans', []))]
    reasons = []
    for label, plan in plans:
        for row in plan.get('candidates', []) + plan.get('rejected', []):
            cells = [label, json.dumps(row.get('patch', {}), ensure_ascii=False),
                     row.get('reason', ''), json.dumps(row.get('observation', {}), ensure_ascii=False),
                     row.get('risk', ''), row.get('rejected_reason', 'Candidate; full local validation required')]
            reasons.append('<tr>' + ''.join('<td>' + html.escape(str(cell)) + '</td>' for cell in cells) + '</tr>')
    explanations = ('<details open><summary>Why these candidates</summary><table><tr><th>Plan</th><th>Change</th>'
        '<th>Reason</th><th>Measured evidence</th><th>Risk</th><th>Admission</th></tr>' + ''.join(reasons) +
        '</table><p>Other-machine evidence ranks a hypothesis; only a complete local request can validate it.</p></details>') if reasons else ''
    bootstrap = result.get('bootstrap') or {}
    remaining = result.get('remaining_search_seconds')
    if remaining is None and result.get('max_seconds') is not None:
        remaining = max(0., result['max_seconds'] - bootstrap.get('wall_seconds', 0.))
    budget = ('<p>Total worker budget: ' + number(result.get('max_seconds')) + ' s. New baseline used: ' +
              number(bootstrap.get('wall_seconds', 0.)) + ' s. Remaining search budget: ' +
              number(remaining) + ' s.</p>')
    if bootstrap:
        budget += '<p>New full baseline: ' + html.escape(str(bootstrap.get('status', 'unknown'))) + '</p>'
    (directory / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>FreeVideo optimization</title>'
        '<style>body{font:16px system-ui;background:#111827;color:#e5e7eb;margin:32px}a{color:#93c5fd}'
        'td,th{padding:10px;border:1px solid #4b5563}table{border-collapse:collapse}video{max-width:800px;width:100%}</style>'
        '<h1>FreeVideo local optimization</h1><p>Status: ' + html.escape(result['status']) + '</p>'
        '<p>Validated prompts cached: ' + str(result.get('cached_prompts', 0)) + '. Sampling profile activated: ' +
        html.escape(str(result.get('activated_profile') or 'none')) + '</p>'
        + budget + explanations + attempt_table +
        '<p>Single-step probes below preserve the eight-step schedule and full geometry; they are not complete video times.</p>'
        '<p>Baseline step measurements: ' + html.escape(str(search.get('baseline_step_seconds', []))) + '</p>'
        '<table><tr><th>Candidate</th><th>Status</th><th>Step seconds</th><th>Recheck seconds</th><th>Peak VRAM GiB</th><th>Peak RAM GiB</th></tr>' + rows + '</table>'
        '<p>Run test again for complete request time, text-cache hits, sampling, VRAM/RAM and matched baseline comparisons. '
        'OS/JIT and file cache warmth can affect results. Every original artifact is retained.</p>'
        '<p><a href="report.json">Full report</a> · <a href="baseline.json">Baseline metrics</a></p>' + media, encoding='utf-8')


def repeated_gain(baseline, candidate, recheck):
    values = [*baseline, candidate, recheck]
    return (len(baseline) >= 2 and all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values)
            and max(candidate, recheck) < min(baseline) * .95)


def retry_search_request(request, trial):
    """Retain an OOM trial, then nominate one alternative in a fresh worker.

    A failed probe neither certifies capacity nor blacklists a configuration.
    Exclusions live only in this bounded search. Production quality gates and
    the original complete reference remain in force.
    """
    if trial.get('status') != 'resource-rejected':
        return None
    failure = trial.get('failure', {})
    if failure.get('kind') not in ('gpu_oom', 'ram_pressure'):
        return None
    failed = next((item.get('patch') for item in reversed(trial.get('trials', []))
                   if item.get('status') == 'memory-rejected'), None)
    if not isinstance(failed, dict) or not failed or not set(failed).issubset(tuning.PATCH_KEYS):
        return None
    rejected = list(request.get('rejected_candidates', []))
    for item in trial.get('trials', []):
        patch = item.get('patch')
        if isinstance(patch, dict) and set(patch).issubset(tuning.PATCH_KEYS) and patch not in rejected:
            rejected.append(patch)
    base = request['profile']
    row = request.get('complete_baseline', {})
    engine = dict(base['engine'], **failed)
    rescue = None
    if failure['kind'] == 'gpu_oom' and engine.get('resident_blocks', 0) > 0:
        # Spend existing weight residency before shrinking a worthwhile head
        # group. Only nominate this when the complete baseline's RAM accounting
        # covers every extra offloaded weight; no unbudgeted host copy is implied.
        ram = row.get('ram', {})
        peak = memory_peak(ram)
        extra = engine['resident_blocks'] * tuning.BLOCK_BYTES
        if (tuning._complete_peak(row, base)[0] is not None and memory_complete(ram) and tuning._positive(peak)
                and peak + extra <= base['inference_ram_budget_gb'] * 1e9):
            rescue = dict(failed, resident_blocks=0, pin_host_gb=engine.get('pin_host_gb', 0.))
    elif failure['kind'] == 'ram_pressure' and engine.get('pin_host_gb', 0) > 0:
        rescue = dict(failed, pin_host_gb=0., resident_blocks=engine['resident_blocks'])
    choices = ([rescue] if rescue and rescue not in rejected else [])
    choices += tuning.candidates(row, base, maximum=20,
                                 reject=lambda patch: 'Resource failure in this search' if patch in rejected else None)
    if not choices:
        return None
    result = copy.deepcopy(request)
    result.update(candidates=[choices[0]], adaptive_candidates=False,
                  rejected_candidates=rejected, max_placement_trials=1,
                  recovery={'reason': 'Fresh worker after recoverable memory failure; try another configuration within the same time budget',
                            'failure': failure, 'failed_patch': failed, 'next_patch': choices[0]})
    return result


def run_search_attempts(command, work, request, *, seconds, env, gpu, ui, maximum_working_ram, history=None):
    """At most two fresh-worker retries, all inside the original search budget."""
    from .testing import run_process, artifact_manifest
    if not tuning._positive(seconds):
        raise ValueError('A positive finite search time budget is required')
    started = time.monotonic()
    attempts = []
    first = work
    measured, trial = {'status': 'timeout', 'wall_seconds': 0.}, {}
    for index in range(3):
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        if index:
            work = first.with_name(first.name + '-retry-%02d' % index)
            work.mkdir()
            artifacts = work / 'video.artifacts'
            artifacts.mkdir()
            shutil.copyfile(request['conditioning'], artifacts / 'conditioning.pt')
            request.update(out=str(work), conditioning=str(artifacts / 'conditioning.pt'))
            save(artifacts / 'video.json', request)
            save(work / 'request.json', request)
            ui.begin('search', 'Retry with another configuration', detail=request['recovery']['reason'])
        argv = list(command)
        argv[-1] = str(work / 'request.json')
        # Preparing a retry also spends wall time; never reset or extend the
        # worker deadline while copying retained inputs and writing receipts.
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        measured, trial = {}, {}
        try:
            measured = run_process(argv, work, env=env, gpu=gpu, timeout=remaining,
                minimum_ram=(2 if windows() else 1)*GiB, pass_fds=(int(env[LOCK_ENV]),), ui=ui,
                maximum_working_ram=maximum_working_ram)
            trial_path = work / 'optimization.json'
            trial = json.loads(trial_path.read_text(encoding='utf-8')) if trial_path.is_file() else {}
        except BaseException as error:
            settlements = settle_search_attempts(history, work, measured, trial, error=error) if history is not None else []
            attempts.append(dict(directory=str(work), measurement=measured, status='failed', error=repr(error),
                                 recovery=request.get('recovery'), settlements=settlements,
                                 wall_seconds=time.monotonic() - started))
            save(first.parent / 'search-attempts.json', attempts)
            raise
        settlements = settle_search_attempts(history, work, measured, trial) if history is not None else []
        attempts.append(dict(directory=str(work), measurement=measured,
                             status=trial.get('status'), recovery=request.get('recovery'),
                             settlements=settlements,
                             wall_seconds=time.monotonic() - started))
        save(first.parent / 'search-attempts.json', attempts)
        artifact_manifest(work)
        if measured['status'] != 'complete' or index == 2:
            break
        next_request = retry_search_request(request, trial)
        if next_request is None:
            break
        request = next_request
    return work, measured, trial, attempts


def search_plan(row, base, maximum, seconds, *, reject=None):
    """Count reloads only for placement trials; keep cheap trials on slow disks."""
    pool = tuning.candidates(row, base, maximum=20, reject=reject)
    while True:
        selected = pool[:maximum]
        placements = sum(bool(set(patch) & tuning.PLACEMENT_KEYS) for patch in selected)
        # A placement can require returning to the best previous state. Reserve
        # baseline/winner reloads too; chunk-only trials keep the same model.
        loads = 1 if not placements else 3 + 2 * placements
        estimate = (row['engine']['load_seconds'] * loads + row['engine']['decode_save_seconds']
                    + row['engine']['sample_seconds'] * (12 + 2 * len(selected)) / 8)
        if not selected or estimate <= seconds:
            return selected, estimate, placements
        expensive = next((p for p in selected if set(p) & tuning.PLACEMENT_KEYS), selected[-1])
        pool.remove(expensive)


def run(args, machine, ui):
    from .testing import environment, run_process, artifact_manifest, artifact_estimate
    env = environment(machine)
    os.environ.update(env)
    if args.disable:
        state, reason = tuning.load_state()
        if state:
            state.update(enabled=False, disabled_epoch=time.time())
            tuning.save_state(state)
        print('Local optimization disabled. Saved reports and conditioning files are retained.')
        return 0
    hardware = detect()
    expected = tuning.identity(machine, hardware)
    from .adaptive import local_identity
    resource_identity = local_identity(hardware, machine['cache'])
    selected = args.report or latest(Path(machine['root']), 'test-runs')
    destination = (args.out or Path(machine['root']) / 'optimization-runs' /
                   (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))).resolve()
    bootstrap_seconds, bootstrap = 0., None
    spec = None
    if getattr(args, 'conditioning', None) or getattr(args, 'prompt_file', None):
        from .geometry import geometry
        spec = dict(geometry=geometry(args.width, args.height, frames=args.frames), seed=args.seed,
                    attention=args.attention, conditioning=args.conditioning, prompt_file=args.prompt_file)
    elif selected:
        try:
            source, baseline, usable = read_baseline(selected.resolve(), expected)
        except NoCompleteBaseline:
            spec = recovery_input(selected.resolve(), expected)
    else:
        raise ValueError('No baseline found. Pass --prompt-file PATH or --conditioning PATH to measure a full baseline within --max-seconds.')
    if spec is not None:
        ui.panel('FreeVideo / establish a complete baseline', [
            ('Input', spec.get('conditioning') or spec.get('prompt_file')),
            ('Request', '%dx%d, %d frames, 8 steps, attention %s' %
                (spec['geometry']['width'], spec['geometry']['height'], spec['geometry']['frames'], spec['attention'])),
            ('Budget', 'At most %.0fs for baseline AND search; complete-request time is not yet known' % args.max_seconds),
            ('Validation', 'A new complete request is required. Failed runs and probes are not a baseline.'),
            ('Outputs', destination)])
        if args.plan:
            return 0
        destination.mkdir(parents=True, exist_ok=False)
        ui.start(destination)
        try:
            source, baseline, usable, bootstrap_seconds = bootstrap_baseline(
                spec, machine, expected, destination, args.max_seconds, env, ui)
        finally:
            ui.close()
        bootstrap = dict(report=str(source / 'report.json'), wall_seconds=bootstrap_seconds, status=baseline['status'])
        if not usable:
            write_report(destination, dict(status=baseline['status'], bootstrap=bootstrap,
                max_seconds=args.max_seconds, search_note='No failed metrics were accepted. Retained inputs may be retried with an explicit larger budget.'))
            return 1
    remaining_seconds = max(0., args.max_seconds - bootstrap_seconds)
    if bootstrap is not None:
        hardware = detect()  # Re-read live memory after the complete child exits.
    # Prefer the most frequently tested geometry, then a later warm request.
    geometry_key = lambda r: tuple(r['geometry'][k] for k in ('width', 'height', 'frames'))
    counts = {geometry_key(r): sum(geometry_key(s) == geometry_key(r) for s in usable) for r in usable}
    row = max(enumerate(usable), key=lambda pair: (counts[geometry_key(pair[1])], pair[0]))[1]
    resources = {k: machine.get(k) for k in ('vram_gib', 'ram_gib')}
    resources.update(capacity_flags(row['command']))
    base = choose(hardware, **resources, attention=row['request']['profile']['engine']['attention'],
                  available_backends=available_backends(hardware, probe_missing=False), canvas=row['geometry']).legacy_profile()
    base['engine'].update(base=str(Path(machine['base']).resolve()), checkpoint=str(Path(machine['checkpoint']).resolve()))
    automatic = copy.deepcopy(base)
    if baseline.get('baseline_identity') is not None:
        # A recovered request is the actual validated baseline. Reintroducing
        # the original failed placement here would recreate the cold-start loop.
        completed = row['request']['profile']
        base['engine'] = copy.deepcopy(completed['engine'])
        base['decoder'] = copy.deepcopy(completed['decoder'])
        for budget in ('gpu_budget_gb', 'inference_ram_budget_gb'):
            base[budget] = min(base[budget], completed[budget])
    previous, _ = tuning.load_state(expected)
    base, starting_profile = tuning.apply_profile(previous or {}, base, row['geometry'], text_tokens(row))
    placement_baseline = None
    if starting_profile is None:
        base, placement_baseline = tuning.completed_placement_baseline(row, base)
    same_placement = base['engine'] == row['request']['profile']['engine'] and base['decoder'] == row['request']['profile']['decoder']
    from .resource_history import ResourceHistory
    history_path = Path(machine['root']) / 'resource-history.sqlite3'
    # Read-only planning must not create a database. Execution requires a
    # durable journal before any worker starts.
    history = ResourceHistory(history_path) if not args.plan or history_path.exists() else None
    plan = tuning.candidate_plan(row, base, maximum=20)
    candidates, estimated, placement_trials = search_plan(row, base, args.max_trials, remaining_seconds)
    ui.panel('FreeVideo / local optimization', [('Baseline', source), ('GPU', hardware.gpu_name),
        ('Text reuse', 'Import %d validated requests; cache later prompts after successful encoding' % len(usable)),
        ('Sampling search', '%d candidates · at most %.0fs · identical backend, precision, resolution and steps' % (len(candidates), args.max_seconds)),
        ('Validation', 'Repeat speed gain, memory budgets, full eight steps, identical video/audio latents and decoded output'),
        ('Outputs', destination)])
    if args.plan:
        return 0
    if bootstrap is None:
        destination.mkdir(parents=True, exist_ok=False)
    required_disk = artifact_estimate([{k: row['geometry'][k] for k in ('width', 'height', 'frames')}]) if candidates else 128 * 2**20
    if shutil.disk_usage(destination).free < required_disk:
        raise ValueError('Insufficient free disk space for retained optimization artifacts')
    state = copy.deepcopy(previous) if previous else {'schema': tuning.SCHEMA, 'enabled': True, 'identity': expected,
        'conditioning': {}, 'profiles': []}
    state.update(enabled=True, baseline_report=str(source / 'report.json'), optimization_report=str(destination / 'report.json'))
    result = {'schema_version': 1, 'status': 'running', 'baseline_report': str(source / 'report.json'),
              'identity': expected, 'selected_case': row['id'], 'candidates': candidates, 'candidate_plan': plan,
              'scope': 'Persistent inputs plus bounded, measured placement/chunk/prefetch search. No model/precision/backend or quality reduction.',
              'starting_profile': starting_profile, 'fresh_placement_measurement': not same_placement,
              'placement_baseline': placement_baseline,
              'max_seconds': args.max_seconds, 'remaining_search_seconds': remaining_seconds, 'bootstrap': bootstrap,
              'capacity_scope': 'Placement budgets and sampled guards; no kernel hard limits created.'}
    save(destination / 'baseline.json', baseline)
    save(destination / 'report.json', result)
    ui.start(destination)
    started = time.perf_counter()
    work, measured, trial = None, {}, {}
    try:
        ui.phase('Reuse validated text conditioning', 0, 2)
        for item in usable:
            if item.get('input_mode') == 'preencoded':
                continue  # No invented prompt/encoder receipt for an opaque input.
            tuning.remember_conditioning(state, item['prompt'], source / item['id'] / 'video.artifacts/conditioning.pt', item['encoding'])
        tuning.save_state(state)
        result['cached_prompts'] = len(state['conditioning'])
        result['status'] = 'conditioning-cache-ready'
        if not candidates:
            result['search_note'] = 'No candidate fits the bounded search plan or max-trials is zero; previous settings retained.'
            return 0
        ui.phase('Measure candidates and validate a winner', 1, 2)
        work = destination / 'search'
        work.mkdir()
        artifacts = work / 'video.artifacts'
        artifacts.mkdir()
        condition = artifacts / 'conditioning.pt'
        shutil.copyfile(source / row['id'] / 'video.artifacts/conditioning.pt', condition)
        (artifacts / 'prompt.txt').write_text(row.get('prompt', ''), encoding='utf-8')
        from .resource_history import process_identity
        request = {'out': str(work), 'cache': machine['cache'], 'profile': base, 'geometry': row['geometry'],
                   'input_cache_dir': str(Path(machine['root']) / 'input-cache'),
                   'conditioning': str(condition), 'reference_latents': str(source / row['id'] / 'video.artifacts/latents.pt'),
                   'seed': row.get('seed', 2026090901), 'candidates': candidates, 'adaptive_candidates': True,
                   'complete_baseline': row,
                   'max_placement_trials': placement_trials,
                   'resource_history': str(Path(machine['root']) / 'resource-history.sqlite3'),
                   'resource_identity': resource_identity, 'resource_owner': process_identity()}
        save(work / 'request.json', request)
        save(artifacts / 'video.json', request)
        ui.begin('search', 'Bounded sampling search', detail='Measure live headroom; reload only for placement trials; validate one full winner')
        from .system import inference_environment
        search_env = inference_environment(dict(env, **{LOCK_ENV: os.environ[LOCK_ENV],
            'PYTORCH_ALLOC_CONF': base['allocator_config'], 'PYTORCH_CUDA_ALLOC_CONF': base['allocator_config']}),
            base['inference_ram_budget_gb'] * 1e9)
        work, measured, trial, result['search_attempts'] = run_search_attempts(
            [machine['python'], '-m', 'freevideo_engine.optimize_worker', '--request', str(work / 'request.json')],
            work, request, env=search_env, gpu=machine['gpu_uuid'], seconds=remaining_seconds, ui=ui,
            maximum_working_ram=base['inference_ram_budget_gb'] * 1e9, history=history)
        result['search_directory'] = work.name
        # GPU placement budget is not an allocator/telemetry cutoff. A complete
        # request can reserve slightly more; actual OOM and full-run margins
        # determine rejection without terminating a working request mid-step.
        ui.end('search', success=measured['status'] == 'complete', detail=measured['status'])
        result['measurement'] = measured
        result['search'] = trial
        result['attempt_settlements'] = settle_search_attempts(history, work, measured, trial)
        artifact_manifest(work)
        if measured['status'] != 'complete' or not trial.get('success'):
            result['status'] = 'conditioning-cache-ready-search-failed'
            return 1
        if trial['status'] not in ('validated-candidate', 'numerical-change-rejected'):
            result['status'] = 'conditioning-cache-ready-no-sampling-change'
            return 0
        from .media import inspect
        from .validation import validate_metrics, validate_artifacts
        engine_metrics = json.loads((work / 'video.engine.json').read_text(encoding='utf-8'))
        full = dict(request=dict(success=True, geometry=row['geometry'], request_seconds=measured['wall_seconds']),
                    engine=engine_metrics, gpu=measured['gpu'], ram=measured['ram'])
        validate_metrics(full, row['geometry'], preencoded=True)
        validate_artifacts(work, row['geometry'], preencoded=True)
        media = inspect(work / 'video.mp4', **{k: row['geometry'][k] for k in ('width', 'height', 'frames', 'fps')})
        output_equal = compare_arrays(work / 'video.artifacts', source / row['id'] / 'video.artifacts')
        result['media'] = media
        result['decoded_output_equal'] = output_equal
        full_validation = dict(full_request=True, completed_steps=8, media_verified=True,
            bitwise_latents=trial.get('validation', {}).get('bitwise_latents') is True,
            decoded_output_equal=output_equal, memory_checked=True,
            scope='Full local request; successful completion is separate from numerical equivalence')
        result['attempt_settlements'] += settle_search_attempts(history, work, measured, trial, validation=full_validation)
        gpu, ram = measured['gpu'], measured['ram']
        good_memory = not gpu.get('sampling_errors') and gpu.get('samples') and memory_complete(ram) and ram.get('ram_observation_samples')
        if trial['status'] != 'validated-candidate' or not all(output_equal.values()) or not good_memory or trial['full_sample_seconds'] >= row['engine']['sample_seconds']:
            result['status'] = 'conditioning-cache-ready-full-validation-rejected'
            return 0
        selected_engine = dict(base['engine'], **trial['selected_patch'])
        selected_patch = {k: selected_engine[k] for k in tuning.PATCH_KEYS
                          if k in tuning.PLACEMENT_KEYS or selected_engine[k] != automatic['engine'][k]}
        winner = next(item for item in trial['trials'] if item.get('patch') == trial['selected_patch'])
        entry = {'id': tuning.key({'identity': expected, 'run': str(destination), 'patch': selected_patch}),
                 'enabled': True, 'base_engine': automatic['engine'], 'decoder': base['decoder'],
                 'geometry': {k: row['geometry'][k] for k in ('width', 'height', 'frames')},
                 'text_tokens': text_tokens(row), 'patch': selected_patch,
                 'required_gpu_bytes': gpu['gpu_peak_bytes'] + max(64 * 2**20, int(gpu['gpu_peak_bytes'] * .01)),
                 'required_ram_bytes': memory_peak(ram) + (2 if windows() else 1)*GiB,
                 'performance': {'baseline_step_seconds': trial['baseline_step_seconds'],
                                 'candidate_step_seconds': [winner['seconds'], winner['recheck_seconds']],
                                 'baseline_full_sample_seconds': row['engine']['sample_seconds'],
                                 'candidate_full_sample_seconds': trial['full_sample_seconds']},
                 'validation': dict(trial['validation'], decoded_output_equal=output_equal, media=media,
                                    memory_checked=True, full_request=True, completed_steps=8),
                 'report': str(destination / 'report.json')}
        if not tuning.compatible_profile(entry, automatic, row['geometry'], entry['text_tokens']):
            result['status'] = 'conditioning-cache-ready-insufficient-margin'
            return 0
        state['profiles'].append(entry)
        tuning.save_state(state)
        result.update(status='optimized', activated_profile=entry['id'])
        return 0
    except BaseException as error:
        result.update(status='failed', error=repr(error), traceback=traceback.format_exc())
        if work is not None:
            result.setdefault('attempt_settlements', []).extend(
                settle_search_attempts(history, work, measured, trial, error=error))
        raise
    finally:
        result['wall_seconds'] = bootstrap_seconds + time.perf_counter() - started
        try:
            write_report(destination, result)
            ui.phase(result['status'], 2, 2)
        finally:
            ui.close()
        print('Optimization report: %s\nStatus: %s\nRun ./test.sh again to measure the result.' % (destination / 'report.html', result['status']))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='./freevideo optimize', description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('FREEVIDEO_HOME', DEFAULT_ROOT)))
    parser.add_argument('--report', type=Path, help='Existing test report.json or its directory; default latest test')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--conditioning', type=Path, help='Measure a new full baseline from existing conditioning')
    source.add_argument('--prompt-file', type=Path, help='Measure a new full baseline from a UTF-8 prompt')
    parser.add_argument('--width', type=int, default=1344)
    parser.add_argument('--height', type=int, default=768)
    parser.add_argument('--frames', type=int, default=243)
    parser.add_argument('--seed', type=int, default=2026090901)
    parser.add_argument('--attention', default='auto')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--max-trials', type=int, default=2, help='0–4 neighboring configurations; default 2')
    parser.add_argument('--max-seconds', type=float, default=600, help='Maximum total GPU worker time: new baseline (if needed), loading, probes and full winner generation')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--disable', action='store_true', help='Disable automatic reuse; retain every saved file')
    parser.add_argument('--plain', action='store_true')
    parser.add_argument('--no-color', action='store_true')
    args = parser.parse_args(argv)
    if not 0 <= args.max_trials <= 4 or not math.isfinite(args.max_seconds) or args.max_seconds < 1:
        parser.error('Use 0–4 candidates and a positive finite time budget')
    if args.plan and args.disable:
        parser.error('--plan and --disable cannot be combined')
    config = args.config or args.root / 'machine.json'
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Optimization interrupted')
    previous_signal = processes.termination_handler(interrupted)
    try:
        machine = json.loads(config.read_text(encoding='utf-8'))
        if not machine.get('ready'):
            raise ValueError('Setup is incomplete')
        from .testing import environment
        os.environ.update(environment(machine))
        with nullcontext() if args.plan else runtime_lock() as descriptor:
            previous = os.environ.get(LOCK_ENV)
            if descriptor is not None:
                os.environ[LOCK_ENV] = str(descriptor)
            try:
                return run(args, machine, TerminalUI('Local optimization', plain=args.plain, no_color=args.no_color))
            finally:
                if previous is None:
                    os.environ.pop(LOCK_ENV, None)
                else:
                    os.environ[LOCK_ENV] = previous
    except (OSError, ValueError, RuntimeError) as error:
        print('Optimization could not complete: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Optimization cancelled; reports and artifacts retained.', file=sys.stderr)
        return 130
    finally:
        processes.restore_handlers(previous_signal)


if __name__ == '__main__':
    raise SystemExit(main())
