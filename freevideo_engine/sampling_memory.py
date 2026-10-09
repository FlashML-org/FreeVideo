"""Bounded per-step allocator and WDDM observations; no policy changes."""
import copy
import os
import threading
import time

from .monitoring import save
from . import compilation_diagnostics as compilation


class SamplingMemory:
    def __init__(self, torch, path=None, *, reader_factory=None, interval=.5):
        self.torch, self.path, self.interval = torch, path, interval
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.thread = self.reader = None
        self.started = time.monotonic()
        self.index = 1
        self.rows, self.windows, self.errors = [], {}, []
        self.status = 'not-applicable'
        self.identity = None
        self.previous_process = None
        self.previous_compiler = compilation.snapshot()
        if reader_factory is None and os.name == 'nt':
            from .windows_gpu_memory import AdapterMemory
            reader_factory = AdapterMemory
        if reader_factory is not None:
            try:
                self.reader = reader_factory()
                self.identity = self.reader.identity
                self.status = 'observing'
            except Exception as error:
                self.status = 'unavailable'
                self.errors.append(str(error))

    def start(self):
        self.previous_process = self.process_counters()
        self.poll()
        if self.reader is not None:
            self.thread = threading.Thread(target=self.loop, name='FreeVideo WDDM diagnostics', daemon=True)
            self.thread.start()
        self.persist()
        return self

    def loop(self):
        try:
            while not self.stop_event.wait(self.interval):
                self.poll()
                self.persist()
        finally:
            self.release_reader()

    def release_reader(self):
        reader, self.reader = self.reader, None
        if reader is not None:
            try:
                reader.close()
            except Exception as error:
                with self.lock:
                    self.errors.append(str(error))

    def poll(self):
        if self.reader is None:
            return
        index = self.index
        try:
            value = self.reader.sample()
            for name in ('local', 'nonlocal'):
                sample = value[name]
                if any(type(sample.get(k)) is not int or sample[k] < 0 for k in ('usage_bytes', 'budget_bytes')):
                    raise ValueError('Invalid WDDM counters')
            if value['local']['budget_bytes'] == 0:
                raise ValueError('WDDM did not report a usable local video-memory budget')
        except Exception as error:
            with self.lock:
                if len(self.errors) < 8 and str(error) not in self.errors:
                    self.errors.append(str(error))
            return
        with self.lock:
            if index > 256:
                return
            row = self.windows.setdefault(index, dict(samples=0, first_elapsed_seconds=time.monotonic()-self.started))
            row['samples'] += 1
            row['last_elapsed_seconds'] = time.monotonic() - self.started
            for name in ('local', 'nonlocal'):
                sample = value[name]
                old = row.setdefault(name, dict(peak_usage_bytes=sample['usage_bytes'],
                    first_budget_bytes=sample['budget_bytes'], maximum_budget_bytes=sample['budget_bytes'],
                    last_budget_bytes=sample['budget_bytes'], budget_changes=0,
                    minimum_budget_bytes=sample['budget_bytes'], over_budget_samples=0))
                old['peak_usage_bytes'] = max(old['peak_usage_bytes'], sample['usage_bytes'])
                old['minimum_budget_bytes'] = min(old['minimum_budget_bytes'], sample['budget_bytes'])
                old['maximum_budget_bytes'] = max(old['maximum_budget_bytes'], sample['budget_bytes'])
                old['budget_changes'] += old['last_budget_bytes'] != sample['budget_bytes']
                old['last_budget_bytes'] = sample['budget_bytes']
                old['last_usage_bytes'] = sample['usage_bytes']
                old['over_budget_samples'] += sample['usage_bytes'] > sample['budget_bytes']

    def complete(self, seconds):
        # The absolute monotonic end lets the report place the GPU monitor's
        # samples (another process, same clock) inside each step.
        now = time.monotonic()
        row = dict(step=self.index, seconds=seconds, elapsed_seconds=now-self.started, monotonic_seconds=now)
        current_compiler = compilation.snapshot()
        row['compiler'] = compilation.difference(self.previous_compiler, current_compiler)
        self.previous_compiler = current_compiler
        row['host'] = self.host_counters()
        current = self.process_counters()
        row['process_delta'] = {key: value-self.previous_process[key] for key, value in current.items()
                                if self.previous_process is not None and key in self.previous_process
                                and value >= self.previous_process[key]}
        self.previous_process = current
        try:
            cuda = self.torch.cuda
            stats = cuda.memory_stats()
            row.update(allocated_bytes=cuda.memory_allocated(), reserved_bytes=cuda.memory_reserved(),
                cumulative_peak_allocated_bytes=cuda.max_memory_allocated(),
                cumulative_peak_reserved_bytes=cuda.max_memory_reserved(),
                inactive_split_bytes=stats.get('inactive_split_bytes.all.current'),
                allocation_retries=stats.get('num_alloc_retries'), allocator_ooms=stats.get('num_ooms'))
        except Exception as error:
            row['allocator_error'] = str(error)
        with self.lock:
            if len(self.rows) < 256:
                self.rows.append(row)
            self.index += 1
        self.persist()

    @staticmethod
    def process_counters():
        # Read only at step boundaries, not on the kernel/attention hot path.
        # CPU time can exceed elapsed time when several host threads work.
        result = {}
        try:
            import psutil
            process = psutil.Process(os.getpid())
            value = process.cpu_times()
            result.update(cpu_user_seconds=value.user, cpu_system_seconds=value.system)
            value = process.io_counters()
            result.update(read_bytes=value.read_bytes, write_bytes=value.write_bytes)
        except Exception:
            # Includes unavailable psutil counters; keep any earlier readings.
            pass
        return result

    def host_counters(self):
        result = {}
        try:
            stats = self.torch.cuda.memory.host_memory_stats()
            for target, source in (('pinned_allocated_bytes', 'allocated_bytes.current'),
                                   ('pinned_active_bytes', 'active_bytes.current')):
                if type(stats.get(source)) is int and stats[source] >= 0:
                    result[target] = stats[source]
            if ('pinned_allocated_bytes' in result and 'pinned_active_bytes' in result
                    and result['pinned_allocated_bytes'] >= result['pinned_active_bytes']):
                result['pinned_cached_bytes'] = result['pinned_allocated_bytes'] - result['pinned_active_bytes']
        except Exception:
            pass  # Optional on older/CPU wheels. Absence is not zero pins.
        try:
            from .system import system_memory
            memory = system_memory()
            for key in ('physical_available_bytes', 'commit_available_bytes'):
                if type(memory.get(key)) is int and memory[key] >= 0:
                    result[key] = memory[key]
        except Exception:
            pass
        for key, getter in (('torch_threads', 'get_num_threads'), ('torch_interop_threads', 'get_num_interop_threads')):
            try:
                result[key] = getattr(self.torch, getter)()
            except Exception:
                pass
        return result

    def result(self):
        with self.lock:
            rows = [dict(row, windows=copy.deepcopy(self.windows.get(row['step']))) for row in self.rows]
            return dict(steps=rows, current_step=self.index,
                incomplete_step_windows=copy.deepcopy(self.windows.get(self.index)),
                incomplete_step_compiler=compilation.difference(self.previous_compiler, compilation.snapshot()),
                compiler_scope=compilation.SCOPE,
                windows_status=self.status, windows_identity=self.identity, errors=list(self.errors),
                windows_sample_interval_seconds=self.interval, retained_step_limit=256,
                scope='Allocator peaks are cumulative since sampling reset; current counters are at completed step boundaries. '
                      'Allocation retry/OOM counters cover the process lifetime, not individual steps. '
                      'DXGI local/nonlocal values describe this CUDA process on its LUID-matched adapter. '
                      'Nonlocal usage alone is not proof of CUDA spill or PCIe paging traffic. '
                      'Host counters are step-boundary observations, not peaks. Pinned allocated bytes include '
                      'active and allocator-cached rounded blocks across this process, not just model weights. '
                      'Process I/O deltas are OS counters; they do not measure mmap hard faults or PCIe transfers. '
                      'These diagnostic observations do not change memory policy.')

    def persist(self):
        if self.path is not None:
            try:
                save(self.path, self.result())
            except Exception as error:
                with self.lock:
                    if len(self.errors) < 8 and str(error) not in self.errors:
                        self.errors.append(str(error))

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                result = self.result()
                result['windows_status'] = 'reader-still-stopping'
                result['errors'].append('WDDM query did not finish')
                return result
        if self.status == 'observing':
            self.release_reader()
            self.status = ('partial' if self.errors else 'complete') if self.windows else 'unavailable'
        self.persist()
        return self.result()
