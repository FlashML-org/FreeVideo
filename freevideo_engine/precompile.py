"""Compile the sampling kernels of recent requests after the engine or the GPU driver changed.

An engine update that changes a fused block kernel, an attention layout or an
int8 projection tile leaves the first request after it to compile them: on a
12 GB Windows laptop that request's first sampling step took 172 s instead of
65 s. The compiled kernels depend on the request's plan (the head group, the
frame layout, the projection tiles and the prompt's row count band), so this
plans the most recent distinct request shapes from the resource history -- or
the node's default request on a new installation -- exactly as a request would,
and runs each through precompile_worker until its second block. The kernels
land in the installation's Triton and inductor caches, where every later
process finds them.

Nothing waits for it: background_compile starts it while an interactive
session is idle and stops it when work arrives. The identity it records holds
the code, the packages and the GPU driver, so any of them changing compiles
again at the next idle start. It writes no measurement or compatibility
record; opening the resource history applies the schema upgrade a request's
opening would. A failure or an interruption only leaves the request to
compile what is missing, as it always did.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time

from .execution_paths import execution_environment
from .monitoring import save

# The node's default request: 1344x768, 10 s, two passes of 8 + 3 steps. The
# default prompt encodes to 460 tokens.
DEFAULT_SHAPE = dict(task='t2va', width=1344, height=768, frames=243, two_pass=True,
                     base_steps=8, refine_steps=3, text_tokens=(460,))
# Medium starts from a larger first pass, which is another canvas to compile. An
# installation with no request on record has not shown which level it uses.
MEDIUM_SHAPE = dict(DEFAULT_SHAPE, upscale_factor=1.5)
# A portrait canvas and a picture-referenced request are other compiled shapes
# (the frame count too, so portrait is compiled at 5.2 s, the length it is made
# at), and a first request of either compiled its own on the spot. The picture
# is one 1008-row reference with a prompt of 460 rows beside it.
REFERENCE_ROWS = 1008
PORTRAIT_SHAPE = dict(DEFAULT_SHAPE, width=768, height=1344, frames=124)
REFERENCE_SHAPE = dict(DEFAULT_SHAPE, task='ref2va', reference_video_tokens=REFERENCE_ROWS,
                       text_tokens=(460 + REFERENCE_ROWS,))
DEFAULT_SHAPES = [DEFAULT_SHAPE, MEDIUM_SHAPE, PORTRAIT_SHAPE, REFERENCE_SHAPE,
                  dict(REFERENCE_SHAPE, width=768, height=1344, frames=124)]
SHAPES = 2
LENGTHS = 2
TIMEOUT_SECONDS = 600
# Another process holds the GPU work lease: try again at the next idle start.
BUSY_EXIT = 75
# Set for the worker of a background run, which lowers its own disk priority.
BACKGROUND_ENV = 'FREEVIDEO_PRECOMPILE_LOW_PRIORITY'
# The interactive process that started a background run; its GPU use is not another program's.
OWNER_ENV = 'FREEVIDEO_BACKGROUND_OWNER'


class GiveWay(BlockingIOError):
    """The user's other programs need memory/GPU: leave the cache receipt intact."""


# Set when the interactive session stops a background run. A signal can land
# inside any handler that recovers from ordinary errors, so it only sets this;
# the run gives way at its next checkpoint.
CANCELLED = threading.Event()


def checkpoint(room=None, sample=None):
    """GiveWay when the session stopped this run or the room is needed now."""
    if CANCELLED.is_set():
        raise GiveWay('stopped by FreeVideo')
    reason = room.run_reason(sample) if room is not None else None
    if reason:
        raise GiveWay(reason)


def recent_shapes(history, limit=SHAPES):
    """Newest distinct text-to-video request shapes, each with its recent prompt lengths.

    A preview and its upscale count as their two-pass request. They keep no
    resource observation, so their prompt length is the one their forecast used.
    """
    shapes = {}
    for row in reversed(history.requests(limit=64)):
        geometry, observation = row.get('geometry') or {}, row.get('observation') or {}
        if row.get('purpose') != 'generation' or geometry.get('task', 't2va') != 't2va':
            continue
        plan = geometry.get('sampling_plan') or {}
        shape = dict(task='t2va', width=geometry.get('width'), height=geometry.get('height'),
                     frames=geometry.get('frames'), two_pass=bool(plan.get('enabled')),
                     base_steps=plan.get('base_steps', geometry.get('steps', 8)),
                     refine_steps=plan.get('refine_steps') or 3)
        if plan.get('enabled') and plan.get('scale', 2) != 2:
            # Only another factor adds the key, so the shapes of 2x requests stay as they were.
            shape['upscale_factor'] = plan['scale']
        if not all(type(shape[name]) is int and shape[name] > 0 for name in ('width', 'height', 'frames', 'base_steps')):
            continue
        key = json.dumps(shape, sort_keys=True)
        if key not in shapes and len(shapes) >= limit:
            continue
        lengths = shapes.setdefault(key, (shape, []))[1]
        tokens = observation.get('text_tokens')
        if tokens is None:
            evidence = (row.get('details') or {}).get('decision_evidence') or {}
            tokens = ((evidence.get('prediction') or {}).get('geometry') or {}).get('text_tokens')
        if type(tokens) is int and tokens > 0 and tokens not in lengths and len(lengths) < LENGTHS:
            lengths.append(tokens)
    return [dict(shape, text_tokens=tuple(lengths) or DEFAULT_SHAPE['text_tokens'])
            for shape, lengths in shapes.values()]


def with_other_defaults(recent):
    """The recent shapes, then the portrait and picture-referenced defaults they lack.

    A request of one of those shapes is as likely to come next as a repeat, and
    its first compile waits at the first step. With no recent shape there is
    nothing to add them to: the caller compiles every default.
    """
    if not recent:
        return recent
    def kind(shape):
        return {name: value for name, value in shape.items() if name != 'text_tokens'}
    seen = [kind(shape) for shape in recent]
    return recent + [shape for shape in DEFAULT_SHAPES[2:] if kind(shape) not in seen]


def plan_request(machine, shape, history, identity):
    """The worker request a generation of this shape would start with."""
    from .compatibility import Store, apply
    from .generate import automatic_profile
    from .geometry import geometry
    from .prediction_policy import select
    from .adaptive import recovered_compute
    from .two_pass import plan, first_pass_policy
    canvas = geometry(shape['width'], shape['height'], frames=shape['frames'])
    if shape['base_steps'] != 8:
        canvas['steps'] = shape['base_steps']
    if shape.get('reference_video_tokens'):
        # A request's canvas carries its pictures' packed rows, which the plan counts.
        canvas.update(reference_video_tokens=shape['reference_video_tokens'], reference_audio_tokens=0)
    from .resource_settings import read as read_resources
    # The installation's capacity caps and the user's saved reserve, as a
    # request's managed command line carries them.
    args = argparse.Namespace(vram_gib=machine.get('vram_gib'), ram_gib=machine.get('ram_gib'), attention='auto',
                              gpu_reserve_gib=None, ram_reserve_gib=None, task=shape['task'],
                              cache=Path(machine['cache']), prompt_file=None)
    for field, value in read_resources(machine['root'], backend=machine.get('device_backend') or 'cuda').items():
        setattr(args, field, value)
    hardware, _, profile = automatic_profile(args, canvas, stage='after-encoding', evidence=[])
    profile['engine'].update(steps=shape['base_steps'], task=shape['task'],
                             base=machine['base'], checkpoint=machine['checkpoint'])
    sampling_plan = plan(canvas, shape['two_pass'], shape['task'],
                         base_steps=shape['base_steps'], refine_steps=shape['refine_steps'],
                         scale=shape.get('upscale_factor', 2))
    if sampling_plan['enabled'] or sampling_plan['version'] == 2:
        canvas['sampling_plan'] = sampling_plan
    profile = tuned(profile, machine, hardware, canvas, sampling_plan, shape['text_tokens'][0])
    level = None
    if history is not None:
        # An update changes the code identity, so its first request finds no
        # matching placements; a request after a repair or on a fresh
        # installation does, and so does this plan.
        observations = history.observations(identity=identity, limit=512, include_details=False)
        profile, _ = select(profile, canvas, hardware, observations, identity, text_tokens=shape['text_tokens'][0])
        profile, _ = recovered_compute(profile, history, identity, canvas)
        level = Store(history.path.parent).status(identity)
        profile, _ = apply(profile, level)
    request = dict(cache=machine['cache'], gpu_budget_bytes=round(profile['gpu_budget_gb'] * 1e9),
                   gpu_reserve_bytes=profile.get('policy', {}).get('gpu_system_reserve_bytes', 0),
                   engine_options=profile['engine'], decoder_options=profile['decoder'], geometry=canvas,
                   sampling_plan=sampling_plan,
                   text_tokens=list(shape['text_tokens']))
    first = first_pass_policy(profile, canvas, sampling_plan)
    if first is not None:
        if level is not None:
            first['profile'], _ = apply(first['profile'], level)
        request['first_pass_policy'] = first
    return request, profile


def tuned(profile, machine, hardware, canvas, sampling_plan, tokens):
    """The validated local optimization a request of this canvas would apply, as generate does."""
    from . import tuning
    try:
        if sampling_plan['version'] not in (1, 3) or not tuning.state_path().is_file():
            return profile
        state, _ = tuning.load_state(tuning.identity(machine, hardware))
        return tuning.apply_profile(state, profile, canvas, tokens)[0] if state else profile
    except Exception:
        return profile  # The request finds no usable optimization either.


def run(machine, *, environment=None, timeout=TIMEOUT_SECONDS, background=False, owner=None):
    """Compile each shape in its own worker; the receipt lists what ran and how long.

    In the background it starts only with room beside the user's other
    programs and gives way when they need it (background_room): GiveWay.
    """
    from .adaptive import history_path, local_identity
    from .hardware import detect
    from .resource_history import ResourceHistory
    environment = dict(os.environ if environment is None else environment)
    root = Path(machine['root'])
    from .triton_compat import own_temporary_environment, temporary_environment
    own_temporary_environment(root)
    environment = temporary_environment(root, environment)
    receipt = dict(success=False, started=time.time(), shapes=[])
    from .precompile_state import record
    record(root, 'running', started_at=receipt['started'], shapes=[])
    if machine.get('gpu_vendor') == 'amd':
        # Not measured on AMD GPUs yet; their first request compiles as before.
        return dict(receipt, skipped=True, reason='AMD GPU')
    try:
        hardware = detect()
        history = ResourceHistory(history_path())
        identity = local_identity(hardware, Path(machine['cache']))
        shapes = with_other_defaults(recent_shapes(history)) or DEFAULT_SHAPES
    except Exception as error:
        hardware, history, identity, shapes = None, None, None, DEFAULT_SHAPES
        receipt['history_error'] = repr(error)
    receipt.update(identity=identity, requested=shapes)
    try:
        previous = json.loads((root / 'precompile.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    receipt['caches'] = compiler_caches(environment)
    receipt['inputs'] = plan_inputs(machine, identity, history)
    if (identity is not None and previous.get('success') and previous.get('identity') == identity
            and previous.get('requested') == json.loads(json.dumps(shapes))
            and previous.get('inputs') == receipt['inputs']
            and previous.get('caches') == receipt['caches'] and marked(receipt['caches'], previous.get('started'))):
        # Same code, packages, GPU, request shapes, plan inputs and compiler
        # caches, which still hold what that run wrote: nothing to compile.
        record(root, 'complete', shapes=previous.get('shapes'))
        return dict(previous, skipped=True)
    checkpoint()
    room = None
    if background:
        from .background_room import Room, compile_need
        room = Room(machine.get('gpu_uuid'), owner=owner,
                    need_bytes=compile_need(previous, identity=identity,
                        requested=json.loads(json.dumps(shapes)), inputs=receipt['inputs']))
        reason = room.start_reason()
        if reason:
            room.close()
            raise GiveWay(reason)
    try:
        checkpoint(room)
        if hardware is not None:
            # After a driver or package change the first request re-runs the GPU
            # kernel checks (about 36 s on Windows) before it plans; do it here,
            # and plan with the backends it leaves, as that request will.
            try:
                check_kernels(hardware, machine, environment, room)
            except BlockingIOError:
                raise
            except Exception as error:
                receipt['probe_error'] = repr(error)
        return compile_shapes(machine, shapes, history, identity, receipt, environment, timeout, room)
    finally:
        if room is not None:
            samples = room.samples()
            if samples:
                print(json.dumps(dict(event='gpu_samples', samples=samples)), flush=True)
            room.close()


def plan_inputs(machine, identity, history):
    """What decides a request's plan besides code and shape: the caps, the saved
    reserve, validated tuning and the compatibility level."""
    import hashlib
    from . import tuning
    from .resource_settings import read as read_resources
    value = dict(caps={name: machine.get(name) for name in ('vram_gib', 'ram_gib')})
    try:
        value['resources'] = read_resources(machine['root'], backend=machine.get('device_backend') or 'cuda')
    except Exception as error:
        value['resources'] = repr(error)
    try:
        path = tuning.state_path()
        value['tuning'] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError as error:
        value['tuning'] = repr(error)
    try:
        from .compatibility import Store
        level = Store(history.path.parent).status(identity) if history is not None and identity is not None else {}
        value['compatibility'] = {name: level.get(name) for name in ('level', 'automatic')}
    except Exception as error:
        value['compatibility'] = repr(error)
    return json.loads(json.dumps(value, default=str))


def plan_lengths(machine, shape, history, identity):
    """[(prompt lengths, request, profile)]: one worker request per distinct plan.

    History placement and validated tuning both select by prompt length, so
    two lengths can plan different kernels; lengths that plan alike share one.
    """
    groups = {}
    for tokens in shape['text_tokens']:
        request, profile = plan_request(machine, dict(shape, text_tokens=(tokens,)), history, identity)
        first = (request.get('first_pass_policy') or {}).get('profile', {}).get('engine')
        key = json.dumps([request['engine_options'], request['decoder_options'], first], sort_keys=True, default=str)
        if key in groups:
            groups[key][0].append(tokens)
        else:
            groups[key] = ([tokens], request, profile)
    for tokens, request, _ in groups.values():
        request['text_tokens'] = list(tokens)
    return list(groups.values())


def check_kernels(hardware, machine, environment, room):
    """Run the GPU kernel checks when theirs no longer match this GPU, driver and packages.

    In the background they run in a child this process watches like a worker
    and stops when the room is needed; an interrupted check publishes no receipt.
    """
    from .kernel_capabilities import available_backends, matching_receipt, needs_path_recheck
    from .triton_compat import own_temporary_environment, temporary_environment
    own_temporary_environment(machine['root'])
    environment = temporary_environment(machine['root'], environment)
    if room is None:
        available_backends(hardware)
        return
    receipt = matching_receipt(hardware)
    if receipt is not None and not needs_path_recheck(receipt, environment):
        return
    # A forced stop skips the cleanup below; an hour-old folder is no live check.
    for stale in Path(tempfile.gettempdir()).glob('freevideo-kernel-checks-*'):
        try:
            if stale.is_dir() and time.time() - stale.stat().st_mtime > 3600:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass
    # Not a precompile-* folder: those belong to the lease holder's cleanup.
    with tempfile.TemporaryDirectory(prefix='freevideo-kernel-checks-', ignore_cleanup_errors=True) as folder:
        log = Path(folder) / 'kernel-checks.log'
        returncode = watch_worker([sys.executable, '-B', '-m', 'freevideo_engine.managed', '--root', str(machine['root']),
                                   'doctor', '--probe'], environment, None, log, TIMEOUT_SECONDS, room)
        if returncode:
            raise RuntimeError('Kernel checks exited with %d: %s' % (
                returncode, log.read_bytes()[-2000:].decode('utf-8', errors='replace')))


def compile_shapes(machine, shapes, history, identity, receipt, environment, timeout, room):
    from . import launch_view
    from .locking import LOCK_ENV, runtime_lock
    from .long_paths import io_path
    root = Path(machine['root'])
    view = launch_view.current() or launch_view.for_engine(root)
    # torch and the worker must see a short path; keep the machine and final
    # receipt rooted at the real installation. Never resolve the temporary view.
    work_root = Path(launch_view.value(root, view)) if view.used else root
    with runtime_lock() as descriptor, tempfile.TemporaryDirectory(prefix='precompile-', dir=work_root,
                                                                   ignore_cleanup_errors=True) as folder:
        environment[LOCK_ENV] = str(descriptor)
        # An interrupted run leaves its folder behind; only this lease writes them.
        # Found and removed under the real folder: nothing is deleted through a short
        # folder, which may by now lead somewhere else.
        for stale in io_path(root).glob('precompile-*'):
            if (stale.is_dir() and stale.name != Path(folder).name
                    and re.fullmatch(r'precompile-[a-z0-9_]{8}', stale.name)):
                shutil.rmtree(stale, ignore_errors=True)
        index = 0
        for shape in shapes:
            checkpoint(room)
            tick = time.perf_counter()
            try:
                planned = plan_lengths(machine, shape, history, identity)
            except BlockingIOError:
                raise
            except Exception as error:
                receipt['shapes'].append(dict(shape=shape, error=repr(error), seconds=time.perf_counter() - tick))
                continue
            for tokens, request, profile in planned:
                checkpoint(room)
                index += 1
                compile_plan(receipt, dict(shape, text_tokens=tuple(tokens)), request, profile, index,
                             Path(folder), environment, descriptor, timeout, room)
                if view.used:
                    # Only the temporary execution paths and their diagnostics
                    # return from the worker; persisted identities stay real.
                    row = receipt['shapes'][-1]
                    for name in ('error', 'stderr_tail'):
                        if name in row:
                            row[name] = launch_view.text_back(row[name], view)
                    back = launch_view.View(view.root, view.real, view.rules)
                    for sample in row.get('worker', {}).get('passes', ()):
                        if 'conditioning' in sample:
                            sample['conditioning'] = launch_view.value(sample['conditioning'], back)
                from .precompile_state import record
                record(root, 'running', started_at=receipt['started'], shapes=receipt['shapes'])
    # A stop arriving after the last worker finished finds the work done; the
    # receipt records it, so the next start does not compile it again.
    receipt['success'] = all(row.get('returncode') == 0 for row in receipt['shapes'])
    receipt['seconds'] = time.time() - receipt['started']
    if room is not None:
        receipt['host_memory'] = room.memory_result()
    if receipt['success']:
        mark(receipt['caches'], receipt['started'])
    save(root / 'precompile.json', receipt)
    from .precompile_state import record
    record(root, 'complete' if receipt['success'] else 'unknown', shapes=receipt['shapes'])
    return receipt


def compile_plan(receipt, shape, request, profile, index, folder, environment, descriptor, timeout, room):
    """Run one planned worker request and add its row to the receipt."""
    from .system import inference_environment
    row = dict(shape=shape)
    receipt['shapes'].append(row)
    tick = time.perf_counter()
    try:
        request['conditioning'] = [str(zero_conditioning(folder, tokens, shape.get('reference_video_tokens', 0)))
                                   for tokens in shape['text_tokens']]
        request['receipt'] = str(folder / ('shape-%d.json' % index))
        path = folder / ('request-%d.json' % index)
        save(path, request)
        env = inference_environment(environment, profile['inference_ram_budget_gb'] * 1e9)
        if room is not None:
            # Background work must not grow a pool of compiler heaps even
            # when the generation profile has a generous RAM budget.
            env['TORCHINDUCTOR_COMPILE_THREADS'] = '1'
        env.update(PYTORCH_ALLOC_CONF=profile['allocator_config'], PYTORCH_CUDA_ALLOC_CONF=profile['allocator_config'])
        log = folder / ('worker-%d.log' % index)
        returncode = watch_worker([sys.executable, '-m', 'freevideo_engine.precompile_worker', '--request', str(path)],
                                  env, descriptor, log, timeout, room)
        row.update(returncode=returncode, plan=dict(
            {name: request['engine_options'].get(name) for name in ('head_chunk', 'prefetch', 'resident_blocks', 'linear_compute')},
            gpu_budget_gb=profile['gpu_budget_gb']))
        try:
            row.update(worker=json.loads(Path(request['receipt']).read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        if returncode:
            row['stderr_tail'] = log.read_bytes()[-4000:].decode('utf-8', errors='replace')
    except BlockingIOError:
        raise
    except Exception as error:
        row['error'] = repr(error)
    row['seconds'] = time.perf_counter() - tick


# Compiler caches can be moved or emptied (a repair, a user, a new location).
# A marker beside the compiled files says this run's kernels are still there:
# in Triton's cache and in inductor's graph cache, which inductor clears on
# its own. The driver's cache is only compared by location.
CACHE_VARIABLES = ('TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR', 'CUDA_CACHE_PATH')
MARKED = (('TRITON_CACHE_DIR', ''), ('TORCHINDUCTOR_CACHE_DIR', 'fxgraph'))
MARKER = 'freevideo-precompile.json'


def compiler_caches(environment):
    return {name: environment.get(name) for name in CACHE_VARIABLES}


def markers(caches):
    """The marker files, or None when a marked cache has no location."""
    if not all(caches.get(name) for name, _ in MARKED):
        return None
    return [Path(caches[name]) / folder / MARKER for name, folder in MARKED]


def mark(caches, started):
    for path in markers(caches) or ():
        try:
            save(path, dict(started=started))
        except OSError:
            pass  # Unmarked: the next run compiles again.


def marked(caches, started):
    try:
        paths = markers(caches)
        return paths is not None and all(
            json.loads(path.read_text(encoding='utf-8')).get('started') == started for path in paths)
    except (OSError, ValueError, AttributeError):
        return False


def watch_worker(command, env, descriptor, log, timeout, room):
    """Run one worker to its end; stop it when it times out or the room is needed."""
    import subprocess
    from . import processes
    from .ram import ProcessMemory
    memory = ProcessMemory() if room is not None else None
    checkpoint(room)  # Never start a child after a stop or with the room needed.
    from .triton_compat import temporary_environment, temp_root
    from .system import install_root
    managed_temp = temp_root(env)
    root = env.get('FREEVIDEO_HOME') or (Path(managed_temp).parent if managed_temp else install_root())
    env = temporary_environment(root, env)
    with log.open('wb') as stream:
        process = processes.popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                  pass_fds=(descriptor,) if descriptor is not None else (), start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            # Include this supervisor's torch/conditioning heap and probe children,
            # not just the active worker. This also credits only our mapped pages.
            checkpoint(room, memory.sample(os.getpid()) if room is not None else None)
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        processes.stop(process, grace=1)
        raise
    return process.returncode


def worker_options(options):
    """No host weight retention for blocks that a compile never executes.

    Keep every compute shape, GPU placement and arithmetic option. Streaming
    profiles can then bind offloaded weights without a load-all/pinning pass.
    """
    return dict(options, pin_host_gb=0., pin_host_weights=False, preload_host=False)


def zero_conditioning(folder, tokens, reference_rows=0):
    """A prompt of `tokens` rows; only its length reaches a kernel.

    With reference rows the last of them are the picture's vision tokens, and the
    picture is a zero latent that packs into as many rows before the video.
    """
    import torch
    path = folder / ('zero-%d-%d.pt' % (tokens, reference_rows) if reference_rows else 'zero-%d.pt' % tokens)
    if not path.exists():
        tags = torch.ones(tokens, dtype=torch.long)
        value = {'prompt_embeds': torch.zeros(tokens, 5120, dtype=torch.bfloat16), 'text_token_tags': tags, 'task': 't2va'}
        if reference_rows:
            tags[tokens - reference_rows:] = 0
            # Even sides, as the H3 patches need: rows = (height / 2) x (width / 2).
            high = next(n for n in range(math.isqrt(reference_rows), 0, -1) if reference_rows % n == 0)
            value.update(task='ref2va', references=[dict(kind='image', latent=torch.zeros(
                1, 24, 1, 2 * high, 2 * reference_rows // high))])
        torch.save(value, path)
    return path


def lower_priority():
    """Yield the CPU and the disk to anything the user is doing."""
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        process = kernel32.GetCurrentProcess()
        # Idle class, which the worker and its compile processes inherit, and
        # background mode for this process's disk and memory priority.
        kernel32.SetPriorityClass(process, 0x40)
        kernel32.SetPriorityClass(process, 0x00100000)
    else:
        os.nice(19)


def reclaim(machine):
    """Free the AdaLN sources a table made unused; disk only, so the GPU may be busy (adaln_reclaim)."""
    from .adaln_reclaim import run as reclaim_sources
    result = reclaim_sources(machine, should_stop=CANCELLED.is_set)
    if result['action'] == 'deleted' or result['reason'] not in ('full_install', 'no_manifest',
                                                              'no_old_grid_table', 'unexpected_manifest'):
        print(json.dumps(dict(event='adaln_reclaim', **result)), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='The installation machine.json')
    parser.add_argument('--background', action='store_true', help='Lowest priority; exit if the GPU is in use')
    args = parser.parse_args(argv)
    machine = json.loads(args.config.read_text(encoding='utf-8'))
    if args.background:
        lower_priority()
        os.environ[BACKGROUND_ENV] = '1'
        from .processes import termination_handler
        def stopped(signum, frame):
            # Stopped by the interactive session: give way at the next
            # checkpoint, stopping the children and removing temporary folders
            # on the way out, leaving the cache receipt intact.
            CANCELLED.set()
        termination_handler(stopped)
    try:
        from .testing import environment
        # A request's own variables: the installation, its caches and its GPU.
        from .triton_compat import own_temporary_environment
        own_temporary_environment(machine.get('root'))
        os.environ.update(execution_environment(environment(machine)))
        owner = os.environ.get(OWNER_ENV)
        reclaim(machine)
        receipt = run(machine, background=args.background, owner=int(owner) if owner else None)
    except Exception as error:
        if args.background and isinstance(error, BlockingIOError):
            # Only diagnostic status changes; the next idle start still tries again.
            from .precompile_state import flush, record, reason_code
            record(machine['root'], 'not_started', reason=reason_code(error)
                   if isinstance(error, GiveWay) else 'lease_busy')
            flush(cancelled=CANCELLED)  # A stop must not wait for diagnostic I/O.
            print(json.dumps(dict(event='give_way', reason=str(error), at=time.time())), flush=True)
            return BUSY_EXIT
        # FreeVideo works either way; record why.
        receipt = dict(success=False, error=repr(error))
        save(Path(machine['root']) / 'precompile.json', receipt)
    from .precompile_state import flush, record
    record(machine['root'], 'complete' if receipt.get('success') else 'unknown', shapes=receipt.get('shapes', []))
    flush(cancelled=CANCELLED)
    print(json.dumps({key: receipt.get(key) for key in ('success', 'skipped', 'seconds')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
