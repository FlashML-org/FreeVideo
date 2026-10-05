"""Run native generation stages with explicit sampling process lifetimes.

The controller imports no tensor runtime. Its existing parent supervises the
whole process tree and records one continuous engine log and memory observation.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback

from . import processes
from .locking import LOCK_ENV, runtime_lock
from .monitoring import save


def sampling_lifetime(resource):
    # New requests choose auto in macos_generate.policy. Preserve the historical
    # lifetime when replaying saved requests which predate this field.
    mode = resource.get('sampling_lifetime', 'phase')
    if mode not in ('phase', 'auto', 'request'):
        raise ValueError('Unknown native sampling lifetime: ' + str(mode))
    return mode


def joined_admission(engine, value):
    """Decide in the GPU worker, using its enforced cap and validated packed rows."""
    mode = sampling_lifetime(value['resources'])
    if mode == 'phase' or not value['sampling']['enabled']:
        return dict(selected=False, mode=mode, reason='separate-or-single-pass')
    task = value.get('task', 't2va')
    from .macos_runtime import conditioning_tokens, load_conditioning
    from .macos_compute import shared_sampling_plan
    if engine.policy.get('enforced') is not True:
        raise ValueError('Shared sampling requires an enforced MPS allocator policy')
    phase_tokens = None
    if task == 't2va':
        prompt, _, conditions = load_conditioning(value['conditioning'], 'cpu',
            task=task, canvas=value['canvas'])
        if conditions is not None:
            raise ValueError('Shared text sampling received unexpected media conditioning')
        prompt_rows = prompt.shape[0]
    else:
        from .geometry import geometry
        phase_tokens, prompt_rows = {}, None
        for phase, key in (('first-pass', 'first'), ('refinement', 'second')):
            canvas = geometry(**value['sampling'][key])
            prompt, _, conditions = load_conditioning(value['conditioning'], 'cpu',
                task=task, canvas=canvas, target=value['canvas'])
            if prompt_rows is not None and prompt.shape[0] != prompt_rows:
                raise ValueError('Prompt row count changed between sampling admissions')
            prompt_rows = prompt.shape[0]
            phase_tokens[phase] = conditioning_tokens(task, canvas, conditions)
            del prompt, conditions
    result = shared_sampling_plan(engine.policy['effective_allocator_limit_bytes'],
                                  value['sampling'], prompt_rows,
                                  phase_reference_tokens=phase_tokens)
    return dict(result, selected=mode == 'request' or result['eligible'], mode=mode)


def retain_joined_progress(result, observed):
    """Recover completed sampling when a joined child fails later in the request."""
    rows = observed.get('sampling_passes', [])
    expected = (result['sampling_plan']['base_steps'], result['sampling_plan']['refine_steps'])
    if (not isinstance(rows, list) or len(rows) > 2 or any(
            not isinstance(row, dict) or not isinstance(row.get('step_seconds'), list)
            or len(row['step_seconds']) != expected[index] for index, row in enumerate(rows))):
        raise ValueError('Invalid completed joined sampling report')
    result['sampling_passes'] = rows
    result['step_seconds'] = [t for row in rows for t in row['step_seconds']]
    result['load_seconds'] = observed.get('load_seconds', 0.)
    result['sample_seconds'] = observed.get('completed_work_seconds', 0.)
    result['latent_save_seconds'] = observed.get('latent_save_seconds', 0.)
    result['phase'] = observed.get('sampling_phase', result['phase'])


def run(value):
    output, artifacts = Path(value['output']), Path(value['artifacts'])
    sampling = value['sampling']
    path = output.with_suffix('.engine.json')
    result = dict(success=False, device_backend='mps', phase='load', geometry=value['canvas'],
                  sampling_plan=sampling, stages={}, sampling_passes=[])
    started = time.monotonic()
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Native stage interrupted by signal ' + str(signum))
    previous = processes.termination_handler(interrupted)
    try:
        lifetime = sampling_lifetime(value['resources'])
        with runtime_lock() as descriptor:
            stages = ['first-pass', 'upscale', 'refinement', 'decode'] if sampling['enabled'] else ['first-pass', 'decode']
            joined = False
            for stage in stages:
                if joined and stage in ('upscale', 'refinement'):
                    continue
                result['phase'] = stage
                save(path, result)
                request = artifacts / (stage + '-request.json')
                receipt = artifacts / (stage + '-result.json')
                save(request, dict(value, phase=stage, report=str(receipt)))
                tick = time.monotonic()
                try:
                    # Inherit stdout/stderr: progress and errors stay in the same
                    # engine log consumed by the existing UI and report exporter.
                    processes.run([sys.executable, '-m', __name__, '--request', str(request)],
                        env=dict(os.environ, **{LOCK_ENV: str(descriptor)}),
                        pass_fds=(descriptor,), check=True)
                finally:
                    if receipt.is_file():
                        try:
                            observed = json.loads(receipt.read_text(encoding='utf-8'))
                            if not isinstance(observed, dict):
                                raise ValueError('Native stage receipt is not an object')
                        except (OSError, ValueError) as error:
                            # A child killed while writing its receipt must not
                            # replace the original failure/cancellation with a
                            # secondary JSON error in this controller.
                            observed = dict(success=False, receipt_error=str(error))
                        result['stages'][stage] = dict(observed, process_seconds=time.monotonic() - tick)
                observed = result['stages'].get(stage, {})
                if observed.get('success') is not True:
                    raise RuntimeError('Native stage did not complete: ' + stage)
                if stage == 'first-pass' and observed.get('joined_sampling') is True:
                    if lifetime == 'phase':
                        raise ValueError('Separate sampling request reported a joined child')
                    if not sampling['enabled']:
                        raise ValueError('Single-pass request reported joined refinement')
                    retain_joined_progress(result, observed)
                    if len(result['sampling_passes']) != 2:
                        raise ValueError('Joined sampling omitted a requested pass')
                    result['latent_upscale'] = observed['latent_upscale']
                    result['shared_sampling_residency'] = observed['shared_sampling_residency']
                    result['sampling_admission'] = observed['sampling_admission']
                    joined = True
                elif stage in ('first-pass', 'refinement'):
                    result['sampling_passes'].append(observed['sampling'])
                    if stage == 'first-pass' and sampling['enabled']:
                        save(artifacts / 'first-pass.json', dict(sampling_plan=sampling,
                             sampling_passes=result['sampling_passes']))
                elif stage == 'upscale':
                    result['latent_upscale'] = observed['upscale']
                else:
                    result['decode'] = observed['decode']
                    result['decode_save_seconds'] = observed['work_seconds']
                # Keep completed work visible if a later stage fails or the
                # controller is interrupted. Failed/incomplete work is retained
                # separately in the stage receipt and process elapsed time.
                completed = {k: s for k, s in result['stages'].items() if s.get('success') is True}
                result.update(load_seconds=sum(s.get('load_seconds', 0.) for s in completed.values()),
                    sample_seconds=sum(s['work_seconds'] for k, s in completed.items() if k != 'decode'),
                    latent_save_seconds=sum(s.get('latent_save_seconds', 0.) for s in completed.values()),
                    step_seconds=[t for row in result['sampling_passes'] for t in row['step_seconds']])
            if (len(result['step_seconds']) != sampling['total_steps'] or
                    not output.is_file() or output.stat().st_size == 0):
                raise RuntimeError('Native stages did not produce the complete requested video')
            result.update(success=True, phase='complete')
    except BaseException as error:
        observed = result['stages'].get('first-pass', {})
        if observed.get('joined_sampling') is True and observed.get('success') is not True:
            try:
                retain_joined_progress(result, observed)
            except (ValueError, TypeError, KeyError) as receipt_error:
                observed['progress_receipt_error'] = str(receipt_error)
        result.update(error_type=type(error).__name__, error_message=str(error), exception=traceback.format_exc(),
                      cancelled=isinstance(error, KeyboardInterrupt))
        raise
    finally:
        processes.restore_handlers(previous)
        result['worker_seconds'] = time.monotonic() - started
        save(path, result)


def stage_compute(resource, canvas):
    """Partitions for this sampling stage from its live unified-memory allowance.

    The supervisor's policy fixes the reserve and allocator ceiling; the stage
    process sees its own live availability, which is larger after the encoder
    or a previous stage exited. Explicit `compute_plan: False` keeps the
    reference partitions."""
    if resource.get('compute_plan') is False:
        return {}
    from .geometry import geometry
    from .macos_compute import plan
    from .system import system_memory
    available = system_memory()['available_bytes'] - resource['reserve_bytes']
    budget = int(max(0, min(resource['allocator_capacity_bytes'], available)))
    shape = geometry(canvas['width'], canvas['height'], frames=canvas['frames'])
    tokens, frames = shape['video_tokens'] + 1024, shape['latent_frames']
    result = plan(budget, tokens, frames=frames)
    try:
        import mlx.core as mx
        mx.array([1.0])            # Fails early where Metal is unusable for MLX.
        result['attention_impl'] = 'mlx'
    except Exception:
        result['attention_impl'] = 'torch'
    result.update(fast_kernels=resource.get('fast_kernels', True), tokens=tokens, frames=frames)
    return result


def worker(value):
    from .macos_bootstrap import require_native
    require_native()
    import torch
    torch.set_num_threads(2)
    resource, sampling = value['resources'], value['sampling']
    artifacts, phase = Path(value['artifacts']), value['phase']
    result = dict(success=False, device_backend='mps', phase=phase)
    started = time.monotonic()
    try:
        sampling_lifetime(resource)
        if phase in ('first-pass', 'refinement'):
            from .macos_runtime import Engine
            refining = phase == 'refinement'
            if refining and not sampling['enabled']:
                raise ValueError('Refinement is absent from this sampling plan')
            initial = torch.load(artifacts / 'upscaled.pt', map_location='cpu', weights_only=True) if refining else None
            print(json.dumps(dict(event='model_load_phase', phase='Loading native MPS model')), flush=True)
            compute = stage_compute(resource, sampling['second' if refining else 'first'])
            result['compute'] = compute
            with Engine(value['cache'], base=value['base'], checkpoint=value['checkpoint'],
                        steps=sampling['base_steps'], budget_bytes=resource['allocator_capacity_bytes'],
                        budget_ceiling_bytes=resource['allocator_capacity_bytes'],
                        reserve_bytes=resource['reserve_bytes'], query_chunk=resource['query_chunk'],
                        ff_chunk=compute.get('ff_chunk', resource['ff_chunk']),
                        projection_chunk=resource['projection_chunk'],
                        attention_chunk=compute.get('attention_chunk', resource.get('attention_chunk')),
                        weight_decoder=resource['weight_decoder'],
                        head_chunk=compute.get('head_chunk', 4), window_batch=compute.get('window_batch', 1),
                        attention_impl=compute.get('attention_impl', 'torch'),
                        fast_kernels=compute.get('fast_kernels', False),
                        resident_bytes=compute.get('resident_bytes', 0),
                        attention_batch_bytes=compute.get('attention_batch_bytes', 1 << 29),
                        plan_compute=bool(compute),
                        task=value.get('task', 't2va'), canvas=value['canvas']) as engine:
                result['load_seconds'] = engine.load_seconds
                tick = time.monotonic()
                admission = joined_admission(engine, value) if not refining else dict(selected=False)
                result['sampling_admission'] = admission
                if admission['selected']:
                    result.update(joined_sampling=True, sampling_passes=[], latent_save_seconds=0.)
                    def changed(stage):
                        result['sampling_phase'] = stage
                        save(Path(value['report']), result)
                    def first_saved(video, audio, metrics):
                        save_started = time.monotonic()
                        torch.save(dict(video=video, audio=audio), artifacts / 'first-pass.pt')
                        save(artifacts / 'first-pass.json', metrics)
                        result['latent_save_seconds'] += time.monotonic() - save_started
                        result['sampling_passes'] = list(metrics['sampling_passes'])
                        result['completed_work_seconds'] = time.monotonic() - tick - result['latent_save_seconds']
                        save(Path(value['report']), result)
                    video, audio, measured = engine.generate(value['conditioning'], value['seed'],
                        width=value['canvas']['width'], height=value['canvas']['height'],
                        frames=value['canvas']['frames'], two_pass=True, refine_steps=sampling['refine_steps'],
                        reuse_weights=True, refinement_resident_bytes=admission['refinement_resident_bytes'],
                        first_pass_saved=first_saved, phase_changed=changed)
                    result.update(sampling_passes=measured['sampling_passes'],
                        shared_sampling_residency=measured['shared_sampling_residency'],
                        latent_upscale=measured['latent_upscale'], sampling_phase='latent-save')
                    result['work_seconds'] = time.monotonic() - tick - result['latent_save_seconds']
                    result['completed_work_seconds'] = result['work_seconds']
                else:
                    video, audio, measured = engine.sample(value['conditioning'],
                        value['seed'] + (sampling['restart_seed_offset'] if refining else 0),
                        **sampling['second' if refining else 'first'], progress_total=sampling['total_steps'],
                        progress_offset=sampling['base_steps'] if refining else 0,
                        **(dict(initial_latents=(initial['video'], initial['audio']), refine_steps=sampling['refine_steps'])
                           if refining else {}))
                    if refining and not torch.equal(audio, initial['audio']):
                        raise ValueError('MPS refinement changed first-pass audio')
                    if measured.get('compute') is not None:
                        result.update(initial_compute=compute, compute=measured['compute'])
                    result.update(sampling=measured, work_seconds=time.monotonic() - tick)
            name = ('first-pass.pt' if not refining and sampling['enabled']
                    and not result.get('joined_sampling') else 'latents.pt')
        elif phase == 'upscale':
            from .macos_vdn import activate
            activate()
            from .backends import get_backend
            from .macos_runtime import upscale_latents
            backend = get_backend('mps')
            result['policy'] = backend.configure_budget(resource['allocator_capacity_bytes'],
                                                        reserve_bytes=resource['reserve_bytes'])
            initial = torch.load(artifacts / 'first-pass.pt', map_location='cpu', weights_only=True)
            video, stats = upscale_latents(initial['video'], sampling, value['base'], backend)
            audio = initial['audio']
            result.update(upscale=stats, work_seconds=stats['stage_seconds'])
            name = 'upscaled.pt'
        elif phase == 'decode':
            from .macos_decode import decode_to_file
            initial = torch.load(artifacts / 'latents.pt', map_location='cpu', weights_only=True)
            tick = time.monotonic()
            result['decode'] = decode_to_file(initial['video'], initial['audio'], value['output'],
                base=value['base'], artifacts_dir=artifacts,
                budget_bytes=resource['allocator_capacity_bytes'], reserve_bytes=resource['reserve_bytes'])
            result.update(work_seconds=time.monotonic() - tick, success=True)
            return
        else:
            raise ValueError('Unknown native generation stage: ' + str(phase))
        if not bool(video.isfinite().all()) or not bool(audio.isfinite().all()):
            raise ValueError('Native stage produced nonfinite latents')
        tick = time.monotonic()
        torch.save(dict(video=video, audio=audio), artifacts / name)
        result.update(latent_save_seconds=result.get('latent_save_seconds', 0.) + time.monotonic() - tick,
                      success=True)
    except BaseException as error:
        result.update(error_type=type(error).__name__, error_message=str(error), exception=traceback.format_exc())
        raise
    finally:
        result['worker_seconds'] = time.monotonic() - started
        save(Path(value['report']), result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    worker(json.loads(parser.parse_args().request.read_text(encoding='utf-8')))
