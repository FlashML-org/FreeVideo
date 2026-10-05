"""Sequential full text-to-video stability tests; retain every artifact locally."""
import argparse
import csv
import html
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import traceback
from urllib.parse import quote

from .bootstrap import inventory, digest, GiB, PACKAGE, DEFAULT_ROOT, existing_parent
from .geometry import geometry
from .locking import runtime_lock, LOCK_ENV
from .monitoring import Monitor, save
from .ram import ProcessMemory
from .terminal_ui import TerminalUI, LogProgress
from .validation import read_case_reports, validate_metrics, validate_artifacts
from . import processes
from .system import (windows, system_memory, cpu_info, nvidia_smi, memory_peak, memory_sample,
                     inference_headroom, inference_memory_sample)


def case_plan(suite='standard'):
    prompts = json.loads((PACKAGE / 'test_prompts.json').read_text(encoding='utf-8'))
    w, h, frames = (768, 448, 90) if suite == 'quick' else (1344, 768, 243)
    specs = [('01-first', 'first request; cache state observed, not forced cold', w, h, frames, prompts[0]),
             ('02-repeat', 'second request, identical prompt and seed', w, h, frames, prompts[0]),
             ('03-new-prompt', 'third request, changed prompt', w, h, frames, prompts[1]),
             ('04-resolution', 'changed resolution only, relative to first request',
              512 if suite == 'quick' else 1024, 288 if suite == 'quick' else 576, frames, prompts[0]),
             ('05-duration', 'changed duration only, relative to first request', w, h,
              56 if suite == 'quick' else 124, prompts[0])]
    if suite == 'stress':
        specs += [('06-higher-resolution', 'larger canvas stress case', 1536, 864, 243, prompts[0]),
                  ('07-longer', 'longer duration stress case', 1344, 768, 362, prompts[0])]
    return [dict(id=name, purpose=purpose, width=w, height=h, frames=f, prompt=p, seed=2026090901)
            for name, purpose, w, h, f, p in specs]


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise ValueError('Case file must contain a nonempty JSON list.')
    ids = set()
    for case in cases:
        name = case.get('id') if isinstance(case, dict) else None
        if not isinstance(name, str) or not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name) or name in ids:
            raise ValueError('Each case needs a unique path-safe id.')
        ids.add(name)
        if not isinstance(case.get('prompt'), str) or not case['prompt'].strip():
            raise ValueError('Every test case must include a nonempty raw text prompt.')
        geometry(case.get('width', 1344), case.get('height', 768),
                 frames=case.get('frames'), seconds=case.get('seconds'))
    return cases


def cgroup_snapshot():
    if windows():
        return {'unavailable': 'Linux cgroups do not apply to native Windows; no hard capacity limit was created.'}
    try:
        group = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text(encoding='utf-8').splitlines() if line.startswith('0::'))
        root = Path('/sys/fs/cgroup') / group.lstrip('/')
        result = {'path': str(root), 'scope': 'Existing shared cgroup; peaks are lifetime values, not case attribution.'}
        for name in ('memory.current', 'memory.peak', 'memory.max', 'memory.swap.current', 'memory.swap.max'):
            path = root / name
            raw = path.read_text(encoding='utf-8').strip() if path.is_file() else 'max'
            result[name] = None if raw == 'max' else int(raw)
        result['events'] = dict((k, int(v)) for k, v in (line.split() for line in (root / 'memory.events').read_text(encoding='utf-8').splitlines()))
        return result
    except (OSError, ValueError, StopIteration) as error:
        return {'unavailable': repr(error)}


def swap_snapshot():
    if windows():
        return {}  # Windows commit is captured in each RAM sample, not Linux swap counters.
    return {k: int(v) for k, v in (line.split() for line in Path('/proc/vmstat').read_text(encoding='utf-8').splitlines())
            if k in ('pswpin', 'pswpout', 'pgmajfault', 'oom_kill')}


def stop_tree(process):
    if windows():
        return processes.stop(process, grace=20)
    import psutil
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    # generate catches SIGTERM and waits for its separate encoder/video group.
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    for child in children:
        try:
            if child.is_running():
                child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(children, timeout=5)


class CaseProgress:
    def __init__(self, directory, ui):
        self.directory, self.ui = directory, ui
        self.log = LogProgress(directory / 'video.engine.log')
        self.phase = None
        self.ram_peak = None

    def update(self, sample, monitor):
        phase = 'Starting request'
        for name, stages in [('encoding', {'encoder_import': 'Loading encoder libraries', 'encoder_load': 'Loading text encoder',
                                          'encode': 'Encoding prompt'}),
                             ('engine', {'load': 'Loading transformer', 'sample': 'Denoising', 'decode': 'Decoding video and audio',
                                         'complete': 'Writing request report'})]:
            try:
                value = json.loads((self.directory / ('video.' + name + '.json')).read_text(encoding='utf-8'))
                phase = stages.get(value.get('phase'), 'Releasing text encoder' if name == 'encoding' else phase)
            except (OSError, ValueError):
                pass
        if phase != self.phase:
            self.ui.update(self.directory.name, done=0, total=8 if phase == 'Denoising' else 0, detail=phase)
            self.phase = phase
        current_ram = memory_sample(sample)
        if current_ram is not None:
            self.ram_peak = max(self.ram_peak or 0, current_ram)
        now = '—' if current_ram is None else '%.2f' % (current_ram/GiB)
        peak = '—' if self.ram_peak is None else '%.2f' % (self.ram_peak/GiB)
        self.ui.update(self.directory.name, **self.log.read(), resource='GPU %.2f GiB · peak %.2f GiB · RAM now %s GiB · peak %s GiB · available %.2f GiB' %
            ((monitor.last if monitor else 0)/GiB, (monitor.peak if monitor else 0)/GiB,
             now, peak, inference_headroom(sample)/GiB))


def run_process(command, directory, env, gpu, timeout, minimum_ram, *, sample_gpu=True, pass_fds=(), ui=None,
                maximum_gpu=None, maximum_working_ram=None):
    memory = ProcessMemory()
    monitor = None
    gpu_error = None
    if sample_gpu:
        try:
            monitor = Monitor(directory / 'gpu.csv', device=gpu).start()
        except Exception as error:
            gpu_error = repr(error)
    initial_cgroup, initial_swap = cgroup_snapshot(), swap_snapshot()
    tick = time.perf_counter()
    status = 'failed'
    reason = None
    process = None
    progress = CaseProgress(directory, ui) if ui else None
    try:
        with (directory / 'run.log').open('w', encoding='utf-8') as log, (directory / 'ram.jsonl').open('w', buffering=1, encoding='utf-8') as samples:
            process = processes.popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                                       start_new_session=True, pass_fds=pass_fds)
            heartbeat = time.monotonic()
            while process.poll() is None:
                sample = memory.sample(process.pid)
                samples.write(json.dumps(dict(sample, epoch_seconds=time.time(), elapsed_seconds=time.perf_counter()-tick)) + '\n')
                if progress:
                    progress.update(sample, monitor)
                if ((maximum_gpu is not None and monitor and monitor.peak > maximum_gpu)
                        or (maximum_working_ram is not None and inference_memory_sample(sample) is not None
                            and inference_memory_sample(sample) > maximum_working_ram)):
                    reason, status = 'Optimization trial exceeded its observed memory budget.', 'trial_memory_guard'
                    stop_tree(process)
                    break
                if memory_sample(sample) is None:
                    reason, status = 'Process-tree memory monitoring became incomplete; details retained in ram.jsonl.', 'ram_monitor_failed'
                    stop_tree(process)
                    break
                if inference_headroom(sample) <= 0 or inference_headroom(sample) < minimum_ram:
                    reason, status = 'Available system RAM crossed the configured emergency floor.', 'ram_guard'
                    stop_tree(process)
                    break
                if time.perf_counter() - tick > timeout:
                    reason, status = 'Case exceeded timeout.', 'timeout'
                    stop_tree(process)
                    break
                try:
                    # Short model-load peaks can disappear between 500 ms
                    # samples. Keep this bounded; it is observed, not exact, RAM.
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    pass
                if not ui and time.monotonic() - heartbeat >= 30:
                    print('[%s] %.0fs; RAM now %.2f GiB; peak %.2f GiB; available %.2f GiB' %
                          (directory.name, time.perf_counter()-tick, (memory_sample(sample) or 0)/GiB,
                           (memory_peak(memory.result()) or 0)/GiB,
                           inference_headroom(sample)/GiB), flush=True)
                    heartbeat = time.monotonic()
            if reason is None:
                status = 'complete' if process.returncode == 0 else 'failed'
    except BaseException as error:
        reason, status = repr(error), 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
        if process is not None:
            stop_tree(process)
    finally:
        measured = monitor.stop() if monitor else {'sampling_errors': [gpu_error or 'GPU sampling disabled'], 'gpu_peak_bytes': None}
    elapsed = time.perf_counter() - tick
    final_cgroup, final_swap = cgroup_snapshot(), swap_snapshot()
    return {'status': status, 'error': reason, 'returncode': process.returncode if process else None,
            'wall_seconds': elapsed, 'gpu': measured,
            'ram': dict(memory.result(), ram_poll_interval_seconds=.2),
            'cgroup_before': initial_cgroup, 'cgroup_after': final_cgroup,
            'cgroup_event_delta': {k: v - initial_cgroup.get('events', {}).get(k, 0)
                                   for k, v in final_cgroup.get('events', {}).items()},
            'system_vmstat_delta': {k: final_swap[k] - initial_swap[k] for k in final_swap},
            'hard_memory_limits_created': False,
            'timing_scope': 'Fresh request process: text encoding, model loads, sampling, decode, all artifact writes and process startup/exit. Media validation and artifact hashing excluded.'}


def environment(machine):
    """The installation's defaults, not an override of the caller's choices.

    The recorded GPU UUID keeps the host and its children on the device that
    passed initialization and survives a Windows display-adapter reorder, so it
    is the right default. Forcing it also pins the installation to one physical
    GPU: a caller that selected another device silently lost it, and on any
    other machine that UUID does not exist, so CUDA reported no device at all.
    The compiler caches behave the same way; several measurements on one host
    need their own.
    """
    inherited = lambda name, fallback: os.environ.get(name) or fallback
    env = dict(os.environ, FREEVIDEO_HOME=machine['root'], FREEVIDEO_VDN_ROOT=machine['vdn_root'],
                PATH=(str(Path(machine['git']).parent) + os.pathsep if machine.get('git') else '') + os.environ.get('PATH', ''),
                FREEVIDEO_MODEL_ROOT=machine['model_root'], FREEVIDEO_COMFY_ROOT=machine['comfy_root'],
                FREEVIDEO_COMFY_PYTHON=machine['comfy_python'],
                PYTHONUNBUFFERED='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                FREEVIDEO_LOCK_PATH=inherited('FREEVIDEO_LOCK_PATH', str(Path(machine['root']) / 'engine.lock')),
                OMP_NUM_THREADS='8', MKL_NUM_THREADS='8')
    if machine.get('device_backend') == 'mps':
        from .macos_bootstrap import environment as native_environment
        return native_environment(machine['root'], dict(env, OMP_NUM_THREADS='2', MKL_NUM_THREADS='2'))
    env['CUDA_VISIBLE_DEVICES'] = inherited('CUDA_VISIBLE_DEVICES', machine['gpu_uuid'])
    from .triton_compat import environment as compiler_environment
    return compiler_environment(machine['root'], env)


def command_for(machine, case, destination, args):
    command = [machine['python'], '-m', 'freevideo_engine', 'generate', '--prompt-file', str(destination / 'prompt.txt'),
        '--cache', machine['cache'], '--base', machine['base'], '--checkpoint', machine['checkpoint'],
        '--encoder-python', machine['comfy_python'], '--encoder-root', machine['comfy_root'],
        '--model-paths', machine['model_paths'], '--encoder', machine['encoder'],
        '--out', str(destination / 'video.mp4'), '--seed', str(case.get('seed', 2026090901)),
        '--width', str(case.get('width', 1344)), '--height', str(case.get('height', 768)),
        '--attention', args.attention]
    if getattr(args, 'no_tuning', False):
        command.append('--no-tuning')
    if case.get('seconds') is not None:
        command += ['--seconds', str(case['seconds'])]
    else:
        command += ['--frames', str(case.get('frames', 243))]
    for key in ('vram_gib', 'ram_gib'):
        value = getattr(args, key) if getattr(args, key) is not None else machine.get(key)
        if value is not None:
            command += ['--' + key.replace('_', '-'), str(value)]
    return command


def artifact_manifest(directory):
    files = []
    for path in sorted(directory.rglob('*')):
        if path.is_file() and path.name != 'artifacts.json':
            files.append({'path': path.relative_to(directory).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    save(directory / 'artifacts.json', {'files': files, 'total_bytes': sum(f['bytes'] for f in files)})


def artifact_estimate(cases):
    canvases = [geometry(c.get('width', 1344), c.get('height', 768), frames=c.get('frames'), seconds=c.get('seconds')) for c in cases]
    raw_bytes = sum(c['width'] * c['height'] * c['frames'] * 3 for c in canvases)
    # RGB is the largest retained output; include latents, audio, MP4 and logs.
    return int(raw_bytes * 1.5 + len(cases) * 128 * 2**20 + GiB)


def flatten(row):
    g, request = row['geometry'], row.get('request', {})
    video, encoding = row.get('engine', {}), row.get('encoding', {})
    gpu, ram = row.get('gpu', {}), row.get('ram', {})
    profile = request.get('profile', {})
    return dict(case=row['id'], status=row['status'], width=g['width'], height=g['height'],
        frames=g['frames'], duration_seconds=g['seconds'], wall_seconds=row.get('wall_seconds'),
        text_encode_seconds=encoding.get('work_seconds'), conditioning_cache_hit=bool(encoding.get('cache_hit')),
        tuning_profile=request.get('tuning', {}).get('profile_id'),
        baseline_wall_seconds=(row.get('comparison') or {}).get('baseline_wall_seconds'),
        wall_gain_percent=(row.get('comparison') or {}).get('wall_gain_percent'),
        sampling_gain_percent=(row.get('comparison') or {}).get('sampling_gain_percent'),
        model_load_seconds=video.get('load_seconds'),
        sample_seconds=video.get('sample_seconds'), vae_load_seconds=video.get('vae_load_seconds'),
        video_decode_seconds=video.get('video_decode_seconds'), audio_decode_seconds=video.get('audio_load_decode_seconds'),
        mp4_save_seconds=video.get('encode_seconds'), latent_save_seconds=video.get('latent_save_seconds'),
        rgb_audio_save_seconds=video.get('decoded_artifact_save_seconds'),
        gpu_baseline_gib=gpu.get('gpu_baseline_bytes', 0)/GiB,
        gpu_peak_gib=gpu['gpu_peak_bytes']/GiB if gpu.get('gpu_peak_bytes') is not None else None,
        process_peak_rss_gib=ram.get('process_tree_peak_rss_bytes', 0)/GiB,
        process_peak_pss_gib=ram['process_tree_peak_pss_bytes']/GiB if ram.get('process_tree_peak_pss_bytes') is not None else None,
        process_peak_private_commit_gib=ram['process_tree_peak_private_commit_bytes']/GiB if ram.get('process_tree_peak_private_commit_bytes') is not None else None,
        process_peak_private_working_set_gib=ram['process_tree_peak_private_working_set_bytes']/GiB if ram.get('process_tree_peak_private_working_set_bytes') is not None else None,
        process_peak_guard_gib=(memory_peak(ram) or 0)/GiB, ram_guard_metric=ram.get('ram_guard_metric', 'PSS'),
        system_min_commit_available_gib=ram['system_min_commit_available_bytes']/GiB if ram.get('system_min_commit_available_bytes') is not None else None,
        system_min_available_gib=(ram.get('system_min_available_bytes') or 0)/GiB,
        attention=profile.get('engine', {}).get('attention'), error=row.get('error'))


def report(directory, value):
    save(directory / 'report.json', value)
    flat = [flatten(row) for row in value['cases']]
    if flat:
        with (directory / 'report.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
    columns = ('case', 'status', 'width', 'height', 'frames', 'wall_seconds', 'text_encode_seconds',
               'conditioning_cache_hit', 'sample_seconds', 'wall_gain_percent', 'sampling_gain_percent',
               'gpu_peak_gib', 'process_peak_guard_gib', 'ram_guard_metric', 'system_min_available_gib')
    def cell(v):
        return '—' if v is None else ('%.2f' % v if isinstance(v, float) else html.escape(str(v)))
    labels = {'gpu_peak_gib': 'GPU peak (GiB)', 'process_peak_guard_gib': 'RAM peak (GiB)',
              'ram_guard_metric': 'RAM measurement', 'system_min_available_gib': 'System available minimum (GiB)'}
    table = '<tr>' + ''.join('<th>' + html.escape(labels.get(k, k)) + '</th>' for k in columns) + '</tr>'
    table += ''.join('<tr>' + ''.join('<td>' + cell(row.get(k)) + '</td>' for k in columns) + '</tr>' for row in flat)
    media = ''
    for row in value['cases']:
        path = directory / row['id'] / 'video.mp4'
        media += '<h2>%s — %s</h2><p>%s</p>' % (html.escape(row['id']), html.escape(row['status']), html.escape(row.get('purpose', '')))
        if path.is_file():
            relative = quote(path.relative_to(directory).as_posix())
            media += '<video controls preload="metadata" src="%s"></video>' % relative
        media += '<p><a href="%s/case.json">Metrics</a> · <a href="%s/run.log">Log</a> · <a href="%s/artifacts.json">All artifacts</a></p>' % ((quote(row['id']),) * 3)
        if row.get('error'):
            media += '<pre>' + html.escape(row['error']) + '</pre>'
    (directory / 'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>FreeVideo test report</title>'
        '<style>body{font:16px system-ui;margin:32px;background:#111827;color:#e5e7eb}a{color:#93c5fd}'
        'table{border-collapse:collapse;font-size:13px}td,th{padding:8px;border:1px solid #4b5563}video{max-width:800px;width:100%}'
        'pre{white-space:pre-wrap}section{overflow:auto}</style><h1>FreeVideo test report</h1>'
        '<p>Status: ' + html.escape(value['status']) + '. All videos, audio, raw RGB, latents, conditions and logs are retained locally.</p>'
        '<p>Whole GPU usage includes other applications. Linux uses PSS; Windows separately records working set and private commit, and guards their maximum. '
        'Neither includes all system file cache. Windows available memory also checks commit headroom. RAM/VRAM budgets are placement guidance; '
        'these runs create no hard capacity limit. Each request reloads models; validated conditioning reuse is shown separately. '
        'Gains compare matched prompts, seeds, geometry, base placement and dependencies from the optimization baseline; OS/JIT cache and storage warmth can affect wall time.</p>'
        '<p><a href="report.json">Full JSON</a> · <a href="report.csv">CSV</a> · <a href="inventory.json">Machine and versions</a></p>'
        '<section><table>' + table + '</table></section>' + media + '</html>', encoding='utf-8')


def provenance(machine, env):
    result = inventory(machine['gpu_uuid'])
    result['machine_configuration'] = machine
    result['source_files'] = {p.relative_to(PACKAGE.parent).as_posix(): digest(p)
                              for p in sorted([*PACKAGE.glob('*.py'), *PACKAGE.glob('*.json')])}
    result['cpu_info'] = cpu_info()
    result['dependency_revisions'] = json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8'))
    result['fp8_cache_manifest_sha256'] = digest(Path(machine['cache']) / 'manifest.json')
    for name, command in [('git_head', ['git', '-C', str(PACKAGE.parent), 'rev-parse', 'HEAD']),
                          ('git_status', ['git', '-C', str(PACKAGE.parent), 'status', '--porcelain']),
                          ('nvidia_smi', [nvidia_smi(), '-q']),
                          ('engine_packages', [machine['python'], '-m', 'pip', 'freeze']),
                          ('encoder_packages', [machine['comfy_python'], '-m', 'pip', 'freeze'])]:
        p = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30, encoding='utf-8', errors='replace')
        result[name] = {'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    return result


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('FREEVIDEO_HOME', DEFAULT_ROOT)), help='Setup installation directory')
    parser.add_argument('--config', type=Path, help='Override root/machine.json')
    parser.add_argument('--suite', choices=('standard', 'quick', 'stress'), default='standard')
    parser.add_argument('--cases', type=Path, help='Custom JSON list of raw-text cases')
    parser.add_argument('--out', type=Path, help='New report directory; existing paths are refused')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--json', action='store_true', help='Print --plan as JSON')
    parser.add_argument('--plain', action='store_true', help='Disable live terminal rendering')
    parser.add_argument('--no-color', action='store_true', help='Disable colors (also respects NO_COLOR)')
    parser.add_argument('--timeout-seconds', type=float, default=3600)
    parser.add_argument('--min-available-ram-gib', type=float, default=.25)
    parser.add_argument('--vram-gib', type=float)
    parser.add_argument('--ram-gib', type=float)
    parser.add_argument('--attention', default='auto')
    parser.add_argument('--no-tuning', action='store_true', help='Bypass saved optimization and text reuse for a baseline run')
    args = parser.parse_args(argv)
    if args.json and not args.plan:
        parser.error('--json requires --plan')
    if not all(math.isfinite(v) for v in (args.timeout_seconds, args.min_available_ram_gib)) or args.timeout_seconds <= 0 or args.min_available_ram_gib < 0:
        parser.error('Use a positive timeout and a nonnegative emergency RAM floor.')
    config = (args.config or args.root / 'machine.json').expanduser().resolve()
    machine = json.loads(config.read_text(encoding='utf-8'))
    if not machine.get('ready'):
        raise ValueError('Setup is incomplete.')
    cases = validate_cases(json.loads(args.cases.read_text(encoding='utf-8')) if args.cases else case_plan(args.suite))
    destination = (args.out or Path(machine['root']) / 'test-runs' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))).resolve()
    disk_free, disk_needed = shutil.disk_usage(existing_parent(destination)).free, artifact_estimate(cases)
    ui = TerminalUI('Engine test / ' + (args.suite if not args.cases else 'custom'), plain=args.plain, no_color=args.no_color)
    test_plan = {'cases': cases, 'geometry': [geometry(c.get('width', 1344), c.get('height', 768), frames=c.get('frames'), seconds=c.get('seconds')) for c in cases],
                 'estimated_artifact_bytes': disk_needed, 'disk_free_bytes': disk_free, 'output': str(destination),
                 'artifacts': 'All retained', 'scope': 'Native text conditioning (validated reuse when enabled), complete default 8 + 3 video/audio generation.'}
    if args.json:
        print(json.dumps(test_plan, indent=2))
    else:
        ui.panel('FreeVideo / Engine test plan', [('GPU', machine['gpu_uuid']),
            ('Cases', '%d · text encode → 8 + 3 denoising steps → video + audio → validation' % len(cases)),
            ('Cache', 'Fresh request processes; persistent compatible inputs ' + ('bypassed' if args.no_tuning else 'reused automatically; no eviction')),
            ('Disk', '~%.2f GiB for retained artifacts · %.2f GiB free' % (disk_needed/GiB, disk_free/GiB)),
            ('Outputs', destination)] + [(c['id'], '%d×%d · %d frames · %.2fs' % (g['width'], g['height'], g['frames'], g['seconds']))
                                       for c, g in zip(cases, test_plan['geometry'])])
    if disk_free < disk_needed:
        print('Insufficient free disk space for all retained test artifacts.', file=sys.stderr)
        return 1
    if args.plan:
        return 0
    env = environment(machine)
    with runtime_lock(path=Path(env['FREEVIDEO_LOCK_PATH'])) as fd:
        if json.loads(config.read_text(encoding='utf-8')) != machine:
            raise ValueError('Installation changed during preflight. Rerun the test command.')
        return run_suite(args, machine, cases, destination, ui, env, fd)


def run_suite(args, machine, cases, destination, ui, env, fd):
    destination.mkdir(parents=True, exist_ok=False)
    value = {'schema_version': 1, 'suite': args.suite if not args.cases else 'custom', 'status': 'running',
             'created_epoch': time.time(), 'cases': [], 'plan': cases,
             'artifacts_retained': True, 'full_model_residency_between_requests': False,
             'cache_policy': 'Natural OS file and persistent JIT caches; no global cache flushing; first is not claimed storage-cold.',
             'physical_gpu_scope': 'Measured on the GPU in inventory.json. Nominal capacity overrides do not emulate another GPU.'}
    save(destination / 'plan.json', value)
    report(destination, value)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Test interrupted by signal %s' % signum)
    previous = processes.termination_handler(interrupted)
    try:
        ui.start(destination)
        ui.phase('Capture hardware, source and dependency versions', 0, len(cases))
        save(destination / 'inventory.json', provenance(machine, env))
        for index, case in enumerate(cases):
            directory = destination / case['id']
            directory.mkdir()
            (directory / 'prompt.txt').write_text(case['prompt'], encoding='utf-8')
            canvas = geometry(case.get('width', 1344), case.get('height', 768), frames=case.get('frames'), seconds=case.get('seconds'))
            command = command_for(machine, case, directory, args)
            row = dict(case, geometry=canvas, command=command, status='running')
            value['cases'].append(row)
            save(directory / 'case.json', row)
            report(destination, value)
            ui.phase('Case %d of %d' % (index + 1, len(cases)), index, len(cases))
            ui.begin(case['id'], '%s · %d×%d · %d frames' % (case['id'], canvas['width'], canvas['height'], canvas['frames']), detail='Starting exclusive Engine request')
            row['before'] = inventory(machine['gpu_uuid'])
            child_env = dict(env, **{LOCK_ENV: str(fd)})
            row.update(run_process(command, directory, child_env, machine['gpu_uuid'], args.timeout_seconds,
                                   args.min_available_ram_gib * GiB, pass_fds=(fd,), ui=ui))
            read_case_reports(directory, row)
            if row['status'] == 'complete':
                ui.update(case['id'], done=0, total=0, detail='Validate every video frame, audio and retained artifacts')
                try:
                    from .media import inspect
                    validate_metrics(row, canvas)
                    row['artifact_validation'] = validate_artifacts(directory, canvas)
                    row['media'] = inspect(directory / 'video.mp4', **{k: canvas[k] for k in ('width', 'height', 'frames', 'fps')})
                except Exception as error:
                    row.update(status='validation_failed', error=repr(error))
            elif not row.get('error'):
                row['error'] = row.get('request', {}).get('error') or (directory / 'run.log').read_text(errors='replace', encoding='utf-8')[-2500:]
            if row['status'] != 'complete' and row.get('request', {}).get('tuning', {}).get('profile_id'):
                from .tuning import disable_profile
                # The child may have been killed before its own finally block.
                old_root = os.environ.get('FREEVIDEO_HOME')
                os.environ['FREEVIDEO_HOME'] = machine['root']
                try:
                    disable_profile(row['request']['tuning']['profile_id'], row.get('error', row['status']))
                finally:
                    if old_root is None:
                        os.environ.pop('FREEVIDEO_HOME', None)
                    else:
                        os.environ['FREEVIDEO_HOME'] = old_root
            if row['status'] == 'complete':
                row['comparison'] = baseline_comparison(row)
            save(directory / 'case.json', row)
            ui.update(case['id'], done=0, total=0, detail='Hash retained artifacts and write reports')
            artifact_manifest(directory)
            report(destination, value)
            ui.end(case['id'], success=row['status'] == 'complete', detail='%.2fs request · GPU peak %.2f GiB · RAM peak %.2f GiB' %
                   (row['wall_seconds'], (row.get('gpu', {}).get('gpu_peak_bytes') or 0)/GiB, (memory_peak(row['ram']) or 0)/GiB))
            if row['status'] == 'interrupted':
                break
        value['status'] = 'complete' if len(value['cases']) == len(cases) and all(r['status'] == 'complete' for r in value['cases']) else 'failed'
    except BaseException as error:
        value.update(status='failed', error=repr(error), traceback=traceback.format_exc())
        if value['cases'] and value['cases'][-1]['status'] == 'running':
            last = value['cases'][-1]
            last.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed', error=repr(error))
            ui.end(last['id'], success=False, detail=str(error))
            save(destination / last['id'] / 'case.json', last)
            artifact_manifest(destination / last['id'])
    finally:
        try:
            value['finished_epoch'] = time.time()
            report(destination, value)
        except OSError as error:
            value.update(status='failed', report_error=repr(error))
            print('Could not save final test report: ' + str(error), file=sys.stderr)
        finally:
            processes.restore_handlers(previous)
            try:
                ui.phase('Tests ' + value['status'], len([r for r in value['cases'] if r['status'] != 'running']), len(cases))
            finally:
                ui.close()
    print('\nReport: %s\nStatus: %s' % (destination / 'report.html', value['status']), flush=True)
    return 0 if value['status'] == 'complete' else 1


def baseline_comparison(row):
    reference = row.get('request', {}).get('tuning', {}).get('baseline_report')
    if not reference:
        return None
    try:
        baseline = json.loads(Path(reference).read_text(encoding='utf-8'))
        for previous in baseline.get('cases', []):
            if previous.get('status') != 'complete' or any(row.get(k) != previous.get(k) for k in ('prompt', 'seed', 'geometry')):
                continue
            before, after = previous['request']['profile'], row['request']['profile']
            from .tuning import PATCH_KEYS
            if (before['decoder'] != after['decoder'] or any(before.get(k) != after.get(k) for k in ('gpu_budget_gb', 'inference_ram_budget_gb'))
                    or {k: v for k, v in before['engine'].items() if k not in PATCH_KEYS} !=
                       {k: v for k, v in after['engine'].items() if k not in PATCH_KEYS}):
                continue
            return {'baseline_report': reference, 'case': previous['id'], 'baseline_wall_seconds': previous['wall_seconds'],
                    'wall_gain_percent': 100 * (1 - row['wall_seconds'] / previous['wall_seconds']),
                    'sampling_gain_percent': 100 * (1 - row['engine']['sample_seconds'] / previous['engine']['sample_seconds']),
                    'conditioning_reused': bool(row['encoding'].get('cache_hit')),
                    'scope': 'Same input, seed, geometry and base resource placement. Storage/JIT warmth can affect timings.'}
    except (OSError, ValueError, KeyError, ZeroDivisionError, TypeError):
        return None
    return None


def main(argv=None):
    try:
        return _main(argv)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('Test could not start: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Test cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
