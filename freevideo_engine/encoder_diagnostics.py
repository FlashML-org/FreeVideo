"""Encoder phase receipts, including startup before the tensor runtime loads.

Phase times are exclusive wall times, not GPU kernel timings. Native forward
includes streamed weight transfers; device placement is timed separately.
Only phase boundaries are sampled, with no CUDA synchronization or tensor hooks.
"""
import os
import json
from pathlib import Path
import sys
import time

from .monitoring import save


STAGES = ('worker_import', 'worker_cuda_setup', 'encoder_torch_import',
    'encoder_options', 'encoder_cuda_setup', 'encoder_path_config', 'encoder_native_import',
    'encoder_media_prepare', 'encoder_lookup', 'encoder_load', 'encoder_checkpoint_map',
    'encoder_construct', 'encoder_tokenize', 'encoder_device_load', 'encoder_page_release',
    'encoder_compute', 'encoder_oom', 'encoder_retry', 'encoder_conditioning_pack',
    'keyframe_vae', 'media_vae', 'encoder_save', 'encoder_idle_preload')


def cached_metrics(value):
    """Retain the original receipt as provenance, never current-request work."""
    timings = {key: value.pop(key) for key in tuple(value) if key.endswith('_seconds')}
    value['cache_source_timings'] = timings
    value.update({key: 0. for key in timings})
    value['cache_source_metrics'] = {key: value.pop(key) for key in ('timing', 'runtime',
        'load_stages', 'gpu', 'checkpoint', 'encoder_attempts', 'token_summary',
        'resident_encoder_cache_hit', 'resident_encoder_gpu_ready_at_start') if key in value}
    value.pop('phase', None)
    return value


def prewarm_receipt(path, *, gpu):
    if path is None:
        return {}
    from .diagnostic_resources import encoder_prewarm
    result = dict(gpu_preload=gpu, receipt_available=False)
    try:
        path = Path(path)
        result.update(encoder_prewarm(json.loads(path.read_text(encoding='utf-8'))),
                      receipt_available=True, age_seconds=max(0., time.time()-path.stat().st_mtime))
    except (OSError, ValueError):
        pass
    return result


class EncoderTrace:
    def __init__(self, request, *, resident=False):
        self.path = request.get('metrics')
        self.started = self.tick = time.perf_counter()
        self.current = None
        self.counters = {}
        self.finished = False
        self.publish_progress = not request.get('idle_preload', False)
        torch = sys.modules.get('torch')
        initialized = bool(torch is not None and torch.cuda.is_initialized())
        self.data = dict(success=False, load_stages=[],
            runtime=dict(resident_worker=resident, torch_imported_at_start=torch is not None,
                         cuda_initialized_at_start=initialized, native_imported_at_start='comfy.sd' in sys.modules),
            timing=dict(version=1, stage_seconds={}, diagnostic_seconds=0., complete=False))

    @staticmethod
    def process_counters():
        from .sampling_memory import SamplingMemory
        return SamplingMemory.process_counters()

    def _close_stage(self, now):
        if self.current is None:
            return
        duration = max(0., now - self.tick)
        timing = self.data['timing']['stage_seconds']
        timing[self.current] = timing.get(self.current, 0.) + duration
        row = self.data['load_stages'][-1]
        row['duration_seconds'] = duration
        counters = self.process_counters()
        row['process_delta'] = {key: value - self.counters[key] for key, value in counters.items()
                                if key in self.counters and value >= self.counters[key]}

    def persist(self):
        if self.path:
            save(self.path, self.data)

    def stage(self, name, **metrics):
        if name not in STAGES:
            raise ValueError('Unknown encoder diagnostic stage')
        if self.publish_progress:
            # Forward only a known stage, never request text, paths or metrics.
            print(json.dumps(dict(event='encoder_phase', stage=name)), flush=True)
        now = time.perf_counter()
        self._close_stage(now)
        self.current = name
        self.data.update(metrics, phase=name)
        row = dict(stage=name, seconds=now-self.started)
        # Early startup must not initialize CUDA merely to measure it.
        torch = sys.modules.get('torch')
        if torch is not None and torch.cuda.is_initialized():
            from .encoder_memory import snapshot
            row['gpu'] = metrics.get('gpu') or snapshot(torch)
        try:
            from .ram import ProcessMemory
            row['memory'] = ProcessMemory().sample(os.getpid())
        except (OSError, RuntimeError):
            pass
        self.data['load_stages'].append(row)
        if len(self.data['load_stages']) > 48:
            self.data['load_stages'][1:-47] = []
        self.data['timing']['elapsed_seconds'] = now-self.started
        self.persist()
        self.counters = self.process_counters()
        self.tick = time.perf_counter()
        self.data['timing']['diagnostic_seconds'] += self.tick-now

    def finish(self, success, metrics=None):
        if self.finished:
            return self.data
        now = time.perf_counter()
        self._close_stage(now)
        self.data.update(metrics or {})
        self.data['success'] = success
        self.data['timing'].update(elapsed_seconds=now-self.started, complete=success)
        self.persist()
        self.finished = True
        return self.data
