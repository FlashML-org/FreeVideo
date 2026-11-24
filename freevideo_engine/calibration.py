"""Measure the placement questions this machine can settle, and report them.

Some placement choices are gated to one platform because the measurement
behind them is from one platform. The engine cannot guess the others: on
Windows PyTorch has no expandable segments, so the same allocations can need
more *reserved* memory, and the desktop compositor takes VRAM while a request
runs. So the gates stay closed until a machine of that kind reports back.

This runs the pair that would open a gate. It plans for the live card with no
simulated cap, keeps everything identical except the named fields, and records
what each variant reserved and how long its steps took. Nothing is decided
here; the report is the evidence for deciding.

Only questions that apply to this machine's own plan run, so a machine whose
plan has no open question does nothing and says so.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import traceback
import uuid

from .hardware import detect
from .geometry import geometry
from .kernel_capabilities import available_backends
from .policy import choose
from .monitoring import save
from .system import windows
from . import processes

# Each question names the fields it changes and why the answer is not already
# known. `applies` reads the plan this machine would run, so a question only
# costs time on a machine that can answer it.
# Every placement question worth a consumer machine's time, in the order the
# answers matter. Each names one field to change against the machine's own
# plan, so a question costs one run rather than a pair, and a question that
# does not apply to this plan costs nothing.
QUESTIONS = (
    # No residual_staging question. It asked whether staging lowers the
    # whole-device peak or only costs time, and the RTX PRO 6000 answered it:
    # 0.95 GiB of peak, and the unstaged configuration dies in the first
    # sampling step. Asking a user's card again would spend half an hour
    # reproducing an OOM, and on Windows the unstaged run can spill silently
    # and look merely slow -- which is the reading that turned staging off in
    # the first place. benchmarks/pro6000-verification/results-pro6000.md.
    dict(name='attention_outputs', flip='attention_cpu_outputs',
         applies=lambda engine: 'attention_cpu_outputs' in engine,
         asks='What the full-length attention output buffer costs on the card instead of '
              'in host memory.',
         decides='Four Linux corners put it at 0.04 to 0.23 GiB of peak for 19 to 36% of '
                 'sampling time. If this platform agrees, the band can buy it back with '
                 'residency, which measured under 1%.'),
    dict(name='ff_recompute', flip='fp8_ff_recompute',
         applies=lambda engine: 'fp8_ff_recompute' in engine,
         asks='Whether recomputing the feed-forward intermediates is worth the compute it '
              'costs on a card this size.',
         decides='One RTX 4080 Laptop measured recompute off as 1.071x faster with a lower '
                 'reserved peak -- the opposite of what a datacentre card prefers, where '
                 'compute is nearly free. One machine is not a rule.'),
    dict(name='pinned_host_cache', override=dict(pin_host_gb=0.),
         applies=lambda engine: (engine.get('pin_host_gb') or 0) > 0,
         asks='Whether the pinned host weight cache earns its locked pages.',
         decides='The same 4080 record measured 12.75 GB pinned as slower than none. '
                 'Locked pages are scarcer on a desktop than on a server.'),
    dict(name='window_batch', override=dict(window_batch=1),
         applies=lambda engine: (engine.get('window_batch') or 1) > 1,
         asks='What batching the attention windows costs in peak and buys in time.',
         decides='Ampere already runs one. Elsewhere four is planned on a Linux '
                 'measurement, and in this band it changed no peak at three budgets while '
                 'costing up to 18%, which would make one the better default here too.'),
    dict(name='resident_blocks', override=dict(resident_blocks=0),
         applies=lambda engine: (engine.get('resident_blocks') or 0) > 0,
         asks='Whether blocks held on the card buy any time on this platform.',
         decides='Linux measured residency at under 1%, and the 4080 record measured five '
                 'resident blocks as faster than nine. If it buys nothing here either, '
                 'that VRAM is better spent on the activation path.'),
)


def host_peak_bytes():
    """This process's own high-water host memory, or None where unreadable.

    Two of the questions -- the pinned host cache and residency -- are trades
    against host memory, so a report that only carries device peaks answers
    half of each. Linux keeps a true high-water mark in VmHWM; Windows keeps a
    peak working set, and both are read rather than sampled so a peak between
    samples cannot be missed.
    """
    try:
        if os.name == 'nt':
            from .win32 import process_memory
            row = process_memory(os.getpid())
            return int(row['peak_rss_bytes']) if row and row.get('peak_rss_bytes') else (
                int(row['rss_bytes']) if row else None)
        for line in Path('/proc/self/status').read_text(encoding='utf-8').splitlines():
            if line.startswith('VmHWM:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, KeyError, ImportError, AttributeError):
        return None
    return None


def override_for(question, engine):
    """What this question changes, given the plan it is changing.

    A `flip` question reads the plan's own value and inverts it, so one entry
    serves a machine that turns the field on and one that turns it off. Without
    that, a card whose plan already declines the host attention buffer had
    nothing to say about what the buffer costs.
    """
    if 'flip' in question:
        field = question['flip']
        return {field: not bool(engine.get(field))}
    return dict(question['override'])


def open_questions(engine):
    """Every question this plan can answer, in the order their answers matter."""
    return [question for question in QUESTIONS if question['applies'](engine)]


def plan_variants(hardware, canvas, available, **caps):
    """This machine's plan, and one run per open question plus a repeated baseline.

    Every question compares against the same baseline, so N questions cost N+1
    runs rather than 2N. The baseline runs first and again last: a sweep can
    take hours, and a machine whose thermals or desktop load drift would
    otherwise hand its later questions a quietly different comparison.
    """
    policy = choose(hardware, available_backends=available, canvas=canvas, **caps)
    questions = open_questions(policy.engine)
    if not questions:
        return policy, []
    baseline = dict(question='baseline', variant='as_planned', overrides={},
                    engine=copy.deepcopy(policy.engine))
    rows = [baseline]
    for question in questions:
        override = override_for(question, policy.engine)
        rows.append(dict(question=question['name'], variant='changed', overrides=override,
                         engine=dict(copy.deepcopy(policy.engine), **override)))
    rows.append(dict(baseline, variant='as_planned_again'))
    return policy, rows


def _sequence(rows, repeats):
    """The sweep, repeated, with the order reversed on alternate passes.

    The sweep already carries its own baseline at both ends, so a repeat is
    another whole pass rather than an interleaving; reversing it means no
    question is always measured on a warmer or busier machine than another.
    """
    order = []
    for index in range(repeats):
        order.extend(rows if index % 2 == 0 else list(reversed(rows)))
    return order


def run(cache, conditioning, policy, rows, out, *, seed, repeats, warmup_steps, canvas):
    import torch
    from .runtime import Engine
    from .locking import runtime_lock
    from .monitoring import Monitor
    report = dict(calibration_id=uuid.uuid4().hex, schema_version=1)
    results = []
    monitor = Monitor(Path(out) / 'gpu.csv').start()
    try:
        with runtime_lock():
            for attempt, row in enumerate(_sequence(rows, repeats)):
                engine = None
                torch.cuda.reset_peak_memory_stats()
                monitor.peak = monitor.sample()[0]
                record = dict(question=row['question'], variant=row['variant'],
                              overrides=row['overrides'], attempt=attempt)
                started = time.perf_counter()
                try:
                    engine = Engine(cache, **dict(row['engine'], canvas=canvas))
                    record['load_seconds'] = round(engine.load_seconds, 3)
                    for _ in range(warmup_steps):
                        engine.sample(conditioning, seed)
                    torch.cuda.reset_peak_memory_stats()
                    video, audio, metrics = engine.sample(conditioning, seed)
                    finite = bool(torch.isfinite(video).all() and torch.isfinite(audio).all())
                    del video, audio
                    steps = [round(float(value), 4) for value in metrics['step_seconds']]
                    record.update(status='complete', finite_latents=finite,
                                  sample_seconds=round(metrics['sample_seconds'], 3),
                                  step_seconds=steps,
                                  steady_seconds_per_step=(round(statistics.median(steps[2:]), 4)
                                                           if len(steps) > 2 else None),
                                  residual_staged_steps=len(metrics.get('residual_offload_steps') or []),
                                  torch_peak_reserved_bytes=int(torch.cuda.max_memory_reserved()),
                                  torch_peak_allocated_bytes=int(torch.cuda.max_memory_allocated()),
                                  whole_gpu_peak_bytes=int(monitor.peak),
                                  host_peak_bytes=host_peak_bytes(),
                                  actual_config={key: dict(engine.config).get(key) for key in row['engine']})
                except Exception as error:
                    record.update(status='error', error='%s: %s' % (type(error).__name__, error))
                finally:
                    if engine is not None:
                        engine.close()
                    record['elapsed_seconds'] = round(time.perf_counter() - started, 3)
                results.append(record)
                save(Path(out) / 'calibration.json', dict(report, results=results))
    finally:
        report['monitor'] = monitor.stop()
    report.update(results=results, budget_bytes=policy.gpu_budget_bytes,
                  gpu_reserve_bytes=policy.gpu_system_reserve_bytes)
    return report


def summarise(report):
    """Each question against the shared baseline, and how far the baseline moved.

    The comparison is only worth as much as the baseline is stable, so the
    drift between the sweep's first and last baseline runs is reported beside
    the answers rather than left for a reader to notice.
    """
    complete = [row for row in report['results'] if row.get('status') == 'complete']
    baselines = [row for row in complete if row['question'] == 'baseline']
    def steady(rows):
        values = [row['steady_seconds_per_step'] for row in rows if row.get('steady_seconds_per_step')]
        return round(statistics.median(values), 4) if values else None
    def peak(rows, key):
        values = [row[key] for row in rows if row.get(key)]
        return max(values) if values else None
    base_steady = steady(baselines)
    summary = dict(baseline=dict(runs=len(baselines), steady_seconds_per_step=base_steady,
                                 torch_peak_reserved_bytes=peak(baselines, 'torch_peak_reserved_bytes'),
                                 whole_gpu_peak_bytes=peak(baselines, 'whole_gpu_peak_bytes'),
                                 host_peak_bytes=peak(baselines, 'host_peak_bytes')))
    first_last = [steady([row]) for row in baselines if steady([row])]
    summary['baseline']['drift'] = (round(max(first_last) / min(first_last) - 1, 4)
                                    if len(first_last) > 1 else None)
    questions = {}
    for question in [row['question'] for row in complete if row['question'] != 'baseline']:
        if question in questions:
            continue
        rows = [row for row in complete if row['question'] == question]
        value, reserved = steady(rows), peak(rows, 'torch_peak_reserved_bytes')
        whole = peak(rows, 'whole_gpu_peak_bytes')
        base_reserved = summary['baseline']['torch_peak_reserved_bytes']
        base_whole = summary['baseline']['whole_gpu_peak_bytes']
        host = peak(rows, 'host_peak_bytes')
        base_host = summary['baseline']['host_peak_bytes']
        questions[question] = dict(
            runs=len(rows), overrides=rows[0].get('overrides') or {},
            steady_seconds_per_step=value, torch_peak_reserved_bytes=reserved,
            whole_gpu_peak_bytes=whole, host_peak_bytes=host,
            host_delta_bytes=(host - base_host if host and base_host else None),
            faster_than_baseline=(round(base_steady / value, 4)
                                  if value and base_steady else None),
            reserved_delta_bytes=(reserved - base_reserved
                                  if reserved and base_reserved else None),
            whole_gpu_delta_bytes=(whole - base_whole if whole and base_whole else None),
            over_budget=bool(whole and whole > report['budget_bytes']))
    failed = [dict(question=row['question'], variant=row['variant'], error=row.get('error'))
              for row in report['results'] if row.get('status') != 'complete']
    return dict(summary, questions=questions, failed=failed)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='freevideo calibrate',
        description='Measure the placement questions this machine can settle, and report them')
    parser.add_argument('--cache', type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--conditioning', type=Path,
                        help='Preencoded conditioning, so text encoding is not in the comparison')
    source.add_argument('--prompt-file', type=Path,
                        help='Encode this prompt once first, then measure with it')
    for name in ('--encoder-python', '--encoder-root', '--model-paths', '--encoder'):
        parser.add_argument(name)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--warmup-steps', type=int, default=1)
    parser.add_argument('--frames', type=int, default=243)
    parser.add_argument('--width', type=int, default=1344)
    parser.add_argument('--height', type=int, default=768)
    parser.add_argument('--seed', type=int, default=2026090901)
    args = parser.parse_args(argv)
    if args.repeats < 1 or not 0 <= args.warmup_steps <= 2:
        parser.error('Use at least one repeat and at most two warmup steps')
    canvas = geometry(args.width, args.height, frames=args.frames)
    # One directory for the whole run, made once. The encode below writes
    # into it, so a second exclusive mkdir after it raised FileExistsError
    # on every run that encodes its own prompt, which is every run the
    # panel starts. Reusing a directory is deliberate: the encode above is
    # skipped when its output is already there, so a retry keeps it.
    args.out.mkdir(parents=True, exist_ok=True)
    hardware = detect()

    def deliver(report):
        """Keep measurements and failures in the local report."""
        from .diagnostics import Redactor
        cleaned = Redactor([(str(args.out), '<RUN>')]).data(json.dumps(report).encode('utf-8'), '.json')
        save(args.out / 'calibration.json', json.loads(cleaned))

    if args.prompt_file is not None:
        # One encode, before any measurement, so every variant reads one input
        # and none of them pays for text encoding. A separate process because
        # the encoder is a separate runtime, exactly as generation runs it.
        args.conditioning = args.out / 'conditioning.pt'
        command = [sys.executable, '-B', '-m', 'freevideo_engine', 'encode',
                   '--prompt-file', str(args.prompt_file), '--out', str(args.conditioning)]
        for name in ('encoder_python', 'encoder_root', 'model_paths', 'encoder'):
            value = getattr(args, name, None)
            if value:
                command += ['--' + name.replace('_', '-'), str(value)]
        log = args.out / 'encode.log'
        print(json.dumps({'event': 'calibration_encode', 'out': str(args.conditioning),
                          'log': str(log)}), flush=True)
        if not args.conditioning.exists():
            # Hand the child this lease instead of hiding it from it. The
            # managed CLI takes the machine lease exclusively and holds it for
            # the whole command, so a child that opens the lock file itself
            # blocks on its own parent: the first Windows placement test died
            # in msvcrt.locking as 'Another process holds the installation
            # lease' with nothing else running, and no restart could clear it.
            # Removing the two variables, which is what stopped the EBADF
            # before that, was the wrong half of the fix -- a Windows handle
            # number is only valid in the process it was made inheritable for,
            # so the handle has to be duplicated into the child, which popen
            # does for pass_fds.
            lease = processes.descriptors()
            environment = dict(os.environ)
            if windows() and lease:
                # popen gives the child its own handle; a descriptor number
                # left beside it belongs to this process and only reproduces
                # the EBADF above.
                environment.pop('FREEVIDEO_RUNTIME_LOCK_FD', None)
            with log.open('wb') as stream:
                with processes.popen(command, env=environment, pass_fds=lease,
                                     cwd=str(Path(__file__).resolve().parent.parent),
                                     stdin=subprocess.DEVNULL, stdout=stream,
                                     stderr=subprocess.STDOUT) as child:
                    returncode = child.wait()
            if returncode:
                # Print it and report it. This failure is upstream of every
                # measurement, so the report carries no results; its value is
                # the engine frames a reader can parse out of the child's
                # traceback, which is how this bug was found -- by hand, from a
                # console the machine's owner had to copy out.
                output = log.read_text(encoding='utf-8', errors='replace')
                print(output[-8000:], flush=True)
                deliver(dict(schema_version=1, calibration_id=uuid.uuid4().hex,
                             hardware=hardware.to_dict(), geometry=dict(canvas),
                             status='error', results=[], traceback=output[-4000:],
                             error='encode: freevideo encode exited with code %d' % returncode))
                # Not parser.error: a usage dump buries the child's traceback,
                # which is the only thing here that says what went wrong.
                failure = {'event': 'calibration_failed', 'stage': 'encode',
                           'returncode': returncode, 'log': str(log)}
                # A memory stop is not a held lease: say which it was, and do
                # not tell a machine that ran out of memory to look for another
                # FreeVideo process.
                shortage = next((line for line in reversed(output.splitlines())
                                 if line.startswith('MemoryError: RAM guard')), None)
                if shortage:
                    failure.update(reason='memory', detail=shortage)
                else:
                    failure['if_lease_held'] = ('A held installation lease means another FreeVideo '
                                                'process is using the card. Finish or cancel it, then '
                                                'run the placement test again.')
                raise SystemExit(json.dumps(failure))
    policy, rows = plan_variants(hardware, canvas, available_backends(hardware))
    if not rows:
        result = dict(schema_version=1, questions=[], hardware=hardware.to_dict(),
                      note='This machine\'s plan has no open placement question; nothing to measure.')
        save(args.out / 'calibration.json', result)
        print(json.dumps(result, indent=1))
        return 0
    os.environ['PYTORCH_ALLOC_CONF'] = policy.allocator_config
    os.environ['PYTORCH_CUDA_ALLOC_CONF'] = policy.allocator_config
    context = dict(schema_version=1, calibration_id=uuid.uuid4().hex,
                   hardware=hardware.to_dict(), geometry=dict(canvas), seed=args.seed,
                   plan=dict(policy.engine), budget_bytes=policy.gpu_budget_bytes,
                   gpu_reserve_bytes=policy.gpu_system_reserve_bytes,
                   questions=[dict(name=q['name'], asks=q['asks'], decides=q['decides'])
                              for q in open_questions(policy.engine)])
    try:
        report = run(args.cache, args.conditioning, policy, rows, args.out,
                     seed=args.seed, repeats=args.repeats,
                     warmup_steps=args.warmup_steps, canvas=canvas)
    except BaseException as error:
        deliver(dict(context, status='error', results=[],
                     error='%s: %s' % (type(error).__name__, error),
                     traceback=traceback.format_exc()[-4000:]))
        raise
    report.update(
        context, status='complete',
        conditioning_sha256=hashlib.sha256(args.conditioning.read_bytes()).hexdigest(),
        summary=summarise(report),
        scope=('Live card with no simulated cap. Identical model, conditioning, seed and '
               'geometry; only the named fields differ. Sampling only, so loading and '
               'decode are excluded.'))
    print(json.dumps(dict(calibration_id=report['calibration_id'],
                          summary=report['summary']), indent=1))
    deliver(report)
    return 0
