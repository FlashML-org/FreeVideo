"""Small measured trials; reuse compute state, reload only for placement changes."""
import argparse
import copy
from contextlib import contextmanager
import gc
import hashlib
import json
from pathlib import Path
import statistics
import time
import traceback
import os
import threading

import torch

from .decode import decode_to_file
from .locking import runtime_lock
from .monitoring import save, Monitor
from .ram import ProcessMemory
from .runtime import Engine
from .tuning import PATCH_KEYS, PLACEMENT_KEYS
from .processes import worker_signals
from .system import inference_headroom


def configure(engine, base, patch):
    if not set(patch).issubset(PATCH_KEYS):
        raise ValueError('Unsupported tuning parameter')
    options = dict(base, **patch)
    options.setdefault('head_parallelism', 1)
    from .compute_budget import valid_parallelism
    if not valid_parallelism(options):
        raise ValueError('Parallel head groups require full GPU residency and GPU outputs')
    if engine.closed or any(engine.config.get(name) != options[name] for name in PLACEMENT_KEYS):
        cache, inputs, canvas = engine.cache, engine.input_cache_dir, engine.canvas
        engine.close()
        # Do not keep old pinned weights while allocating the candidate. Model
        # files and AdaLN tables stay on disk and are reused by the next load.
        return Engine(cache, input_cache_dir=inputs, **dict(options, canvas=canvas))
    engine.prefetch = options['prefetch']
    engine.attention.window_batch = options['window_batch']
    engine.attention.batches = None
    from .head_chunk import install_head_chunks
    install_head_chunks(engine.transformer, engine.attention, options['head_chunk'],
                        cpu_outputs=options['attention_cpu_outputs'], projection_chunk=options['projection_chunk'],
                        grouped_outputs=options['grouped_attention_outputs'], parallelism=options['head_parallelism'])
    if options['linear_compute'] == 'native-fp8':
        from .fp8_ops import install_chunked_ff
        for block in engine.transformer.transformer_blocks:
            install_chunked_ff(block.ff, options['ff_chunk'], recompute=options['fp8_ff_recompute'])
    elif options['ff_chunk'] != base['ff_chunk']:
        raise ValueError('This bounded search keeps Ampere FF chunking fixed')
    from .packing import install_streamed_forward
    # The engine keeps its staged blocks, which take the residual stream as a
    # ResidualState; the packing forward must hand them one.
    install_streamed_forward(engine.transformer, options['projection_chunk'],
                             residual_offload=bool(engine.config.get('residual_offload')))
    engine.config.update({name: options[name] for name in PATCH_KEYS})
    return engine


def measured_probe(engine, request, label):
    """Whole-device VRAM plus the worker's physical/commit RAM during a probe."""
    folder = Path(request['out']) / 'probes'
    folder.mkdir(exist_ok=True)
    gpu = Monitor(folder / (label + '.csv')).start()
    ram, stop, errors = ProcessMemory(), threading.Event(), []
    def sample_ram():
        while not stop.is_set():
            try:
                ram.sample(os.getpid())
            except Exception as error:
                errors.append(str(error))
            stop.wait(.1)
    thread = threading.Thread(target=sample_ram, daemon=True)
    thread.start()
    try:
        measured = probe(engine, request['conditioning'], request['seed'], request['geometry'], fingerprint=True)
    finally:
        stop.set()
        thread.join()
        gpu_result = gpu.stop()
    result = {'gpu': gpu_result, 'ram': ram.result(), 'engine': {'sample_seconds': measured['seconds']},
              'prediction_fingerprint': measured['fingerprint']}
    from .system import memory_complete
    if (errors or gpu_result.get('sampling_errors') or not gpu_result.get('samples')
            or not memory_complete(result['ram']) or not result['ram'].get('ram_observation_samples')):
        raise RuntimeError('Incomplete optimization memory observations: ' + repr(errors))
    save(folder / (label + '.json'), result)
    return result


@torch.no_grad()
def probe(engine, conditioning, seed, canvas, *, fingerprint=False):
    """One complete model forward from the original eight-step schedule.

    Probe timings exclude text refinement/loading and are never reported as a
    generated eight-step video. The best candidate still needs full validation.
    """
    class Finished(Exception):
        pass
    times, started, fingerprints = [], [], []
    def before(module, arguments):
        if times:
            raise Finished()
        torch.cuda.synchronize()
        started.append(time.perf_counter())
    def after(module, arguments, output):
        torch.cuda.synchronize()
        times.append(time.perf_counter() - started[-1])
        values = (output.sample, output.audio_sample) if hasattr(output, 'sample') else output
        if not all(bool(torch.isfinite(value).all()) for value in values):
            raise RuntimeError('Non-finite trial prediction')
        if fingerprint:
            # Reject a changed prediction cheaply; a matching first step still
            # cannot certify full-video equivalence or memory capacity. Hash
            # after timing, retain no reference tensors or GPU allocations.
            for value in values:
                host = value.detach().cpu().contiguous()
                raw = memoryview(host.view(torch.uint8).numpy()).cast('B')
                fingerprints.append(dict(shape=list(value.shape), dtype=str(value.dtype),
                                         sha256=hashlib.sha256(raw).hexdigest()))
                del raw, host
    handles = [engine.transformer.register_forward_pre_hook(before), engine.transformer.register_forward_hook(after)]
    original_error = None
    try:
        engine.sample(conditioning, seed, **{k: canvas[k] for k in ('frames', 'width', 'height')})
        raise RuntimeError('Trial did not stop at its documented step boundary')
    except Finished:
        if len(times) != 1:
            raise RuntimeError('Expected exactly one measured model step')
        return dict(seconds=times[0], fingerprint=fingerprints) if fingerprint else times[0]
    except BaseException as error:
        original_error = error
        raise
    finally:
        cleanup_errors = []
        for operation in [handle.remove for handle in handles] + [gc.collect, torch.cuda.empty_cache]:
            try:
                operation()
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            if original_error is None:
                raise cleanup_errors[0]
            original_error.cleanup_errors = [repr(error) for error in cleanup_errors]


@torch.no_grad()
def run(request):
    torch.set_num_threads(8)
    root = Path(request['out'])
    result = {'success': False, 'status': 'running', 'trials': [], 'selected_patch': None,
              'scope': 'Single-step trials at the full requested geometry and eight-step schedule; '
                       'only a full eight-step, bitwise-latent and media-validated winner may be activated.'}
    base, canvas = request['profile']['engine'], request['geometry']
    engine = None
    original_error = None
    started = time.perf_counter()
    from .resource_history import ResourceHistory
    from .adaptive import classify_failure
    history = ResourceHistory(request['resource_history'])
    identity = request['resource_identity']

    @contextmanager
    def attempt(phase, patch=None, full=False):
        profile = dict(request['profile'], engine=dict(base, **(patch or {})))
        identifier = history.begin(identity, profile, canvas,
            purpose='validation' if full else 'probe', owner=request.get('resource_owner'),
            evidence={'phase': phase, 'report': str(root / 'optimization.json'), 'patch': patch or {}})
        history.attach_worker(identifier, os.getpid(), phase=phase)
        result['active_attempt'] = identifier
        result.setdefault('attempt_ids', []).append(identifier)
        save(root / 'optimization.json', result)
        try:
            yield
        except BaseException as error:
            failure = classify_failure(error, dict(result, cleanup_errors=result.get('cleanup_errors', []) + getattr(error, 'cleanup_errors', [])))
            history.finish(identifier, failure['outcome'], details={'phase': phase, 'failure': failure, 'error': repr(error),
                'cleanup_errors': getattr(error, 'cleanup_errors', [])})
            result['active_attempt'] = None
            raise
        else:
            if full:
                # The controller owns media/raw-array/memory validation after
                # this worker exits. A passing probe cannot write calibration.
                result['full_attempt'] = identifier
            else:
                history.finish(identifier, 'success', details={'phase': phase, 'full_request': False})
                result['active_attempt'] = None
        finally:
            save(root / 'optimization.json', result)

    def record(phase, **extra):
        result.update(phase=phase, **extra)
        save(root / 'optimization.json', result)
        save(root / 'video.engine.json', {'phase': 'load' if phase == 'load' else 'sample', 'optimization_phase': phase})
        print(json.dumps({'event': 'optimization', 'phase': phase, **extra}), flush=True)
    try:
        with attempt('baseline-load-and-warmup'):
            record('load')
            # Placement is a plan, not an allocator ceiling; use the same
            # allocator behavior as a normal complete request.
            engine = Engine(request['cache'], input_cache_dir=request.get('input_cache_dir'),
                            **dict(base, canvas=canvas))
            result['load_seconds'] = engine.load_seconds
            record('baseline-warmup')
            probe(engine, request['conditioning'], request['seed'], canvas)
        with attempt('baseline-measurement'):
            record('baseline-measurement')
            observed = measured_probe(engine, request, 'baseline')
        baseline = [observed['engine']['sample_seconds']]
        baseline_fingerprint = observed['prediction_fingerprint']
        result['baseline_prediction_fingerprint'] = baseline_fingerprint
        combined = {}
        placement_trials = 0
        for index in range(len(request['candidates'])):
            if request.get('adaptive_candidates'):
                from .tuning import candidate_plan
                memory = ProcessMemory().sample(os.getpid())
                available = inference_headroom(memory)
                current = dict(request['profile'], engine=dict(base, **combined))
                # Keep the complete baseline for memory admission until the
                # arithmetic changes. A later probe cannot replace its peak or
                # certify headroom for a combined, unmeasured configuration.
                memory_row = request.get('complete_baseline', observed) if not combined else observed
                planned = candidate_plan(memory_row, current, maximum=20,
                    live_gpu_free=torch.cuda.mem_get_info()[0], live_ram_available=available,
                    live_ram_sample=memory)
                result.setdefault('candidate_plans', []).append(planned)
                choices = [item['patch'] for item in planned['candidates']]
                patches = [dict(combined, **choice) for choice in choices]
                if placement_trials >= request.get('max_placement_trials', len(request['candidates'])):
                    patches = [p for p in patches if all(engine.config[k] == p.get(k, base[k]) for k in PLACEMENT_KEYS)]
                patch = next((p for p in patches if not any(t['patch'] == p for t in result['trials'])), None)
                if patch is None:
                    break
            else:
                patch = request['candidates'][index]
            try:
                with attempt('candidate-%d' % index, patch):
                    record('candidate-warmup', candidate=index, patch=patch)
                    if any(engine.config[k] != patch.get(k, base[k]) for k in PLACEMENT_KEYS):
                        placement_trials += 1
                    engine = configure(engine, base, patch)
                    probe(engine, request['conditioning'], request['seed'], canvas)
                    record('candidate-measurement', candidate=index, patch=patch)
                    measured = measured_probe(engine, request, 'candidate-%d' % index)
                seconds = measured['engine']['sample_seconds']
                equal = measured['prediction_fingerprint'] == baseline_fingerprint
                result['trials'].append({'patch': patch, 'seconds': seconds, 'finite_prediction': True,
                                         'status': 'measured' if equal else 'numerical-change-rejected-probe',
                                         'first_prediction_equal': equal,
                                         'prediction_fingerprint': measured['prediction_fingerprint'],
                                         'gpu': measured['gpu'], 'ram': measured['ram']})
                if equal and seconds < observed['engine']['sample_seconds']:
                    combined, observed = patch, measured
            except (torch.cuda.OutOfMemoryError, MemoryError) as error:
                result['trials'].append({'patch': patch, 'status': 'memory-rejected', 'error': str(error)})
                # End this CUDA worker after exhaustion. Normal generation
                # retries placement in fresh workers; a probe failure cannot
                # authorize a configuration or retain a poisoned partial state.
                result.update(success=True, status='resource-rejected', error=repr(error),
                              failure=classify_failure(error, result))
                return
            finally:
                save(root / 'optimization.json', result)
        with attempt('baseline-recheck'):
            engine = configure(engine, base, {})
            record('baseline-recheck')
            baseline.append(measured_probe(engine, request, 'baseline-recheck')['engine']['sample_seconds'])
        result['baseline_step_seconds'] = baseline
        eligible = [row for row in result['trials'] if row.get('status') == 'measured']
        if not eligible:
            result.update(success=True, status='no-safe-candidates')
            return
        winner = min(eligible, key=lambda row: row['seconds'])
        if winner['seconds'] >= min(baseline) * .95:
            result.update(success=True, status='no-stable-sampling-gain')
            return
        with attempt('winner-recheck', winner['patch']):
            engine = configure(engine, base, winner['patch'])
            record('winner-recheck', patch=winner['patch'])
            verified = probe(engine, request['conditioning'], request['seed'], canvas)
        winner['recheck_seconds'] = verified
        from .optimize import repeated_gain
        if not repeated_gain(baseline, winner['seconds'], verified):
            result.update(success=True, status='gain-did-not-repeat')
            return
        result['selected_patch'] = winner['patch']
        result['measured_step_gain_fraction'] = 1 - statistics.mean([verified, winner['seconds']]) / statistics.mean(baseline)
        with attempt('full-eight-step-validation', winner['patch'], full=True):
            record('full-eight-step-validation', patch=winner['patch'])
            video, audio, metrics = engine.sample(request['conditioning'], request['seed'], **{k: canvas[k] for k in ('frames', 'width', 'height')})
            if len(metrics.get('step_seconds', [])) != 8 or engine.config.get('steps') != 8:
                raise RuntimeError('Full validation did not complete the requested eight steps')
            if not bool(torch.isfinite(video).all() and torch.isfinite(audio).all()):
                raise RuntimeError('Non-finite full video/audio latents')
            actual = {'video': video.cpu(), 'audio': audio.cpu(), 'seed': request['seed'], 'geometry': canvas}
            reference = torch.load(request['reference_latents'], map_location='cpu', weights_only=True)
            equality = {name: actual[name].shape == reference[name].shape and actual[name].dtype == reference[name].dtype
                        and bool(torch.equal(actual[name].contiguous().view(torch.uint8), reference[name].contiguous().view(torch.uint8)))
                        for name in ('video', 'audio')}
            result['validation'] = {'bitwise_latents': all(equality.values()), 'equality': equality,
                                    'reference': request['reference_latents']}
            del reference
            artifacts = root / 'video.artifacts'
            artifacts.mkdir(exist_ok=True)
            torch.save(actual, artifacts / 'latents.pt')
            config, base_path = copy.deepcopy(engine.config), engine.base
            engine.close()
            engine = None
            metrics.update(config=config, load_seconds=result['load_seconds'], finite_latents=True,
                           transformer_released_before_decode=True, phase='decode')
            save(root / 'video.engine.json', metrics)
            metrics.update(decode_to_file(video, audio, root / 'video.mp4', base=base_path,
                                         artifacts_dir=artifacts, **request['profile']['decoder']))
            metrics.update(success=True, phase='complete', work_seconds=time.perf_counter() - started)
            save(root / 'video.engine.json', metrics)
            result.update(success=True, status='validated-candidate' if all(equality.values()) else 'numerical-change-rejected',
                          full_sample_seconds=metrics['sample_seconds'])
    except BaseException as error:
        original_error = error
        result.update(success=False, status='failed', error=repr(error), failure=classify_failure(error, result), traceback=traceback.format_exc())
        save(root / 'optimization.json', result)
        raise
    finally:
        cleanup_error = None
        if engine is not None and not engine.closed:
            if result.get('failure', {}).get('kind') == 'cuda_error':
                # End the failed CUDA context; future requests remain eligible.
                result['cleanup_skipped'] = 'Known unsafe CUDA failure; no additional GPU cleanup calls'
            else:
                # The last completed probe is not the end of the GPU lifecycle.
                # A crash/free failure here must retain a pending attempt for
                # the actual last configuration, not the original baseline.
                cleanup_patch = {key: engine.config[key] for key in base
                                 if key in engine.config and engine.config[key] != base[key]}
                try:
                    with attempt('cleanup', cleanup_patch):
                        engine.close()
                except BaseException as error:
                    cleanup_error = error
                    result.setdefault('cleanup_errors', []).append(repr(error))
        if cleanup_error is not None:
            primary = original_error or cleanup_error
            result.update(success=False, status='failed',
                          error=result.get('error', repr(primary)), failure=classify_failure(primary, result))
        result['wall_seconds'] = time.perf_counter() - started
        save(root / 'optimization.json', result)
        if cleanup_error is not None and original_error is None:
            raise cleanup_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    with worker_signals(), runtime_lock():
        run(json.loads(args.request.read_text(encoding='utf-8')))


if __name__ == '__main__':
    main()
