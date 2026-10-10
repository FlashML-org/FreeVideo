"""Isolated interactive worker; keeps compatible idle models until memory is needed."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import gc
import json
import os
from pathlib import Path
import sys
import time
import traceback
import types

from .resident_process import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=Path, required=True)
    args = parser.parse_args()
    bank = None
    allocator = None

    def snapshot():
        result = bank.snapshot() if bank is not None else dict(reclaimable_gpu_bytes=0, reclaimable_ram_bytes=0, gpu_uuid=None, models=[])
        return dict(result, allocator_config=allocator, capabilities=['encoder-weight-preload'])

    def run(message):
        nonlocal bank, allocator
        started = time.perf_counter()
        command = message['argv']
        module = command[command.index('-m')+1]
        if module not in ('freevideo_engine.worker', 'freevideo_engine.encode_worker'):
            raise ValueError('Only the engine and native encoder use the resident worker')
        request = json.loads(Path(command[command.index('--request')+1]).read_text(encoding='utf-8'))
        with Path(message['log']).open('w', encoding='utf-8', buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
            if module == 'freevideo_engine.worker':
                # As a worker started for one request logs before torch loads
                # (see worker.py): the session has taken this request.
                print(json.dumps({'event': 'worker_start', 'pid': os.getpid(), 'epoch': time.time(),
                                  'resident': True}), flush=True)
            metrics = None
            diagnostics = None
            try:
                if module.endswith('.encode_worker'):
                    from .encoder_diagnostics import EncoderTrace
                    diagnostics = EncoderTrace(request, resident=True)
                    diagnostics.stage('worker_import')
                selected = os.environ.get('PYTORCH_ALLOC_CONF', os.environ.get('PYTORCH_CUDA_ALLOC_CONF', ''))
                if allocator is not None and allocator != selected:
                    # Allocation strategy is process initialization state. Do
                    # not pretend environment changes reconfigure a live pool.
                    raise RuntimeError('Resident allocator configuration changed; restart the interactive session')
                allocator = selected
                if bank is None:
                    from .resident_models import ModelBank
                    bank = ModelBank()
                import torch
                torch.set_grad_enabled(False)
                if diagnostics is not None:
                    diagnostics.stage('worker_cuda_setup')
                # An explicit benchmark cap belongs to its request. A cached
                # process must not silently carry that cap into a later job.
                torch.cuda.set_per_process_memory_fraction(1.)
                torch.cuda.reset_peak_memory_stats()
                from .processes import worker_signals
                with worker_signals():
                    if module.endswith('.worker'):
                        from .worker import generate
                        bank.configure(request['gpu_budget_bytes'], request.get('ram_budget_bytes'))
                        generate(request, resident=bank)
                    else:
                        from .encode_worker import encode
                        bank.configure(request['gpu_budget_gb']*1e9, request.get('ram_budget_bytes'))
                        encode(types.SimpleNamespace(comfy_root=None, check_library=False), request,
                               resident=bank, diagnostics=diagnostics)
                print(json.dumps(dict(event='resident_models', **bank.snapshot())), flush=True)
                return 0
            except BaseException as error:
                if diagnostics is not None:
                    diagnostics.finish(False)
                traceback.print_exc()
                from .adaptive import classify_failure, cuda_runtime_error
                failure = classify_failure(error)
                if request.get('metrics'):
                    from .monitoring import save
                    path = Path(request['metrics'])
                    try:
                        metrics = json.loads(path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        metrics = {}
                    failure = classify_failure(error, metrics)
                    from .runtime_libraries import mismatch, diagnose
                    if mismatch(error) and 'runtime_libraries' not in failure:
                        failure['runtime_libraries'] = diagnose()
                    metrics.update(success=False, error=repr(error), failure=failure,
                                   work_seconds=time.perf_counter()-started)
                    save(path, metrics)
                    if 'torch' in sys.modules and sys.modules['torch'].cuda.is_initialized():
                        from .encoder_memory import failure_resources
                        # The video worker already captured the endpoint before
                        # its cleanup. Do not overwrite it with a later reading.
                        retained = metrics.get('failure', {})
                        if not retained.get('gpu'):
                            retained.update(failure_resources(sys.modules['torch'], query_cuda=failure['kind'] != 'cuda_error'))
                        metrics['gpu'] = retained['gpu']
                    save(path, metrics)
                # Any CUDA runtime error except an allocation failure can leave
                # this context unusable, not only the known sticky ones. After
                # 1080p failed with "CUDA error: invalid argument" on an H2D
                # copy, the next request's text encoder in this same process
                # failed with the same error. A fresh worker costs a reload.
                if failure['kind'] == 'cuda_error' or cuda_runtime_error(error):
                    log.flush()
                    os._exit(74)
                failed_error = error

            # ModelBank cannot own tensors held by an exception's traceback.
            # Leave the handler first, save diagnostics above, then destroy
            # failed frames before clearing models and allocator caches. Doing
            # this inside except left the old weights alive for the next job.
            from .failure_cleanup import release_exception_frames
            cleanup = {}
            try:
                cleanup['frames_cleared'] = release_exception_frames(failed_error)
                del failed_error
                if bank is not None:
                    bank.clear()
                gc.collect()
                torch = sys.modules.get('torch')
                if torch is not None and torch.cuda.is_initialized():
                    from .torch_compat import empty_host_cache
                    from .encoder_memory import snapshot as memory_snapshot
                    # Finish any pending transfers before recycling their
                    # pinned host storage for the next request.
                    torch.cuda.synchronize()
                    torch.cuda.empty_cache()
                    empty_host_cache(torch)
                    cleanup['after'] = memory_snapshot(torch, windows_memory=True)
                cleanup['complete'] = True
            except Exception as cleanup_error:
                from .diagnostic_resources import exception_details
                cleanup.update(complete=False, errors=exception_details(cleanup_error))
            if metrics is not None:
                cleanup['before'] = metrics.get('failure', {}).get('gpu', {})
                metrics['failure_cleanup'] = cleanup
                save(path, metrics)
            print(json.dumps(dict(event='resident_failure_cleanup', **cleanup)), flush=True)
            if not cleanup['complete']:
                # A failed cleanup must not admit another model over whatever
                # remains. The controller may start a fresh worker on retry.
                log.flush()
                os._exit(75)
            return 1
    serve(args.endpoint, run, snapshot)


if __name__ == '__main__':
    main()
