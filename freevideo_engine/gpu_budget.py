"""Windows allocator admission; no Torch import or device probing at import time."""
import math
import threading
import time
from .system import windows

_trial_allocator_owner = None


def owned_pool_capacity(torch):
    """Read the idle owner's pool capacity before policy subtracts its reserve.

    A fresh Windows CUDA context's free-memory reading cannot tell us how much
    of the resident worker's pool has already been included by the driver.
    Use the same owner/context and bounds as allocator admission instead.
    """
    total = torch.cuda.get_device_properties(0).total_memory
    free, _ = torch.cuda.mem_get_info()
    reserved = torch.cuda.memory_reserved()
    local, reader = None, None
    try:
        from .windows_gpu_memory import AdapterMemory
        reader = AdapterMemory()
        local = reader.sample().get('local')
    except (OSError, RuntimeError, AttributeError, ValueError):
        pass
    finally:
        if reader is not None:
            try:
                reader.close()
            except (OSError, RuntimeError):
                pass  # A failed optional reader close does not invalidate CUDA's own measurement.
    return allocator_ceiling(total, total, free, reserved, 0, local)


def allocator_ceiling(budget, total, free, reserved, reserve, local=None):
    """Bound the *whole process allocator*, crediting its own pool exactly once.

    The policy budget already excludes growth headroom. Live free memory and
    the WDDM process budget are independent bounds, each with that same reserve,
    not deductions from the already reduced policy budget. WDDM is not a PCIe
    traffic counter and allocations outside Torch remain outside this ceiling.
    """
    for value in (budget, total, free, reserved, reserve):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('GPU allocator admission requires finite nonnegative byte counts')
    if not budget or not total:
        raise ValueError('GPU allocator admission requires positive capacity and budget')
    bounds = dict(planning_budget_bytes=int(budget), physical_capacity_bytes=int(total),
                  live_pool_capacity_bytes=max(0, int(free + reserved - reserve)))
    native = None
    if isinstance(local, dict):
        driver_budget, usage = local.get('budget_bytes'), local.get('usage_bytes')
        if (type(driver_budget) is int and driver_budget > 0 and
                type(usage) is int and usage >= 0):
            native = max(0, usage - reserved)
            bounds['wddm_pool_capacity_bytes'] = max(0, int(driver_budget - native - reserve))
    return dict(allocator_limit_bytes=min(bounds.values()), bounds=bounds,
                observed_non_torch_local_bytes=native,
                owned_allocator_reserved_bytes=int(reserved), live_free_bytes=int(free),
                growth_reserve_bytes=int(reserve))


def configure(torch, budget_bytes, explicit_limit=None, *, reserve_bytes=0, system=None, capacity_trial=False):
    """Bound Windows, a low-memory trial, or an explicit benchmark allocation.

    Reclaimed pool blocks can still be reused by the caching allocator. Its own
    OOM is recoverable by the controller, unlike silently relying on WDDM to
    back excessive GPU allocation with system memory. This is not a whole-device
    limit and cannot prevent every instance of driver paging.
    """
    global _trial_allocator_owner
    from .policy import memory_fraction
    total = torch.cuda.get_device_properties(0).total_memory
    planned = memory_fraction(budget_bytes, total)
    if explicit_limit is not None and (type(explicit_limit) is not int or not 0 < explicit_limit <= total):
        raise ValueError('Benchmark allocator limit must be positive and no larger than physical VRAM')
    desktop = windows() if system is None else system == 'Windows'
    report = dict(device_total_bytes=total, budget_bytes=budget_bytes, planned_fraction=planned,
                  enforced=False, benchmark_allocator_limit_bytes=explicit_limit,
                  benchmark_allocator_limit_enforced=explicit_limit is not None,
                  capacity_trial_limit_enforced=False,
                  windows_allocator_limit_enforced=False,
                  scope='PyTorch caching allocator only; other CUDA allocations and whole-device usage are not capped.')
    limit = explicit_limit
    if desktop:
        # Initialize CUDA before asking the driver for a context-matched LUID.
        torch.cuda.init()
        free, _ = torch.cuda.mem_get_info()
        reserved = torch.cuda.memory_reserved()
        reader, local = None, None
        try:
            from .windows_gpu_memory import AdapterMemory
            reader = AdapterMemory()
            observed = reader.sample()
            local = observed.get('local')
            if not isinstance(local, dict) or type(local.get('budget_bytes')) is not int or local['budget_bytes'] <= 0:
                raise ValueError('WDDM returned no usable local GPU budget')
            report['windows_memory'] = dict(status='available', **observed)
        except (OSError, RuntimeError, AttributeError, ValueError) as error:
            report['windows_memory'] = dict(status='unavailable', reason=str(error))
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception as error:
                    report['windows_memory_cleanup_error'] = str(error)
    elif capacity_trial:
        free, _ = torch.cuda.mem_get_info()
        reserved = torch.cuda.memory_reserved()
        local = None
    if desktop or capacity_trial:
        decision = allocator_ceiling(budget_bytes, total, free, reserved, reserve_bytes, local)
        report['admission'] = decision
        limit = min(decision['allocator_limit_bytes'], limit if limit is not None else total)
        if limit <= 0:
            raise torch.cuda.OutOfMemoryError('CUDA out of memory: no local GPU allocator capacity remains after live budgets')
        report.update(enforced=True, windows_allocator_limit_enforced=desktop,
                      capacity_trial_limit_enforced=bool(capacity_trial),
                      reason='Keep the CUDA allocator within the live local-memory budget; '
                             'recover with controlled weight offload instead of permitting allocator oversubscription.')
        # A resident process may already have a larger *unused* pool. A new
        # fraction affects future allocation, so release that pool once now.
        if reserved > limit:
            torch.cuda.empty_cache()
            report['released_cached_pool_before_request'] = True
    if limit is not None:
        torch.cuda.set_per_process_memory_fraction(limit / total)
        _trial_allocator_owner = torch if capacity_trial and not desktop else None
    elif _trial_allocator_owner is torch:
        # A later Linux request can regain ordinary capacity in the same
        # resident worker. Do not leave yesterday's temporary trial cap active.
        torch.cuda.set_per_process_memory_fraction(1.)
        _trial_allocator_owner = None
        report['released_capacity_trial_limit'] = True
    report['effective_allocator_limit_bytes'] = limit
    return report


# A falling driver budget can be Windows answering a full device: once this
# process frees its optional cache planes the budget returns within seconds.
# Before lowering the cap below the next step's measured workspace (a certain
# OOM and a full restart), wait this long for it to come back.
SETTLE_SECONDS = 15.
SETTLE_INTERVAL = .5
# NVML answers in milliseconds. It is opened before the model loads and before
# any progress event, where a driver call that does not return would hold the
# request at "Loading video model" for good. Past this, the budget goes without
# the whole-device reading, as it did before that reading existed.
DEVICE_OPEN_SECONDS = 2.


def open_bounded(factory, seconds):
    """`factory()`, or TimeoutError after `seconds`; a late result is closed."""
    lock, state = threading.Lock(), {}

    def work():
        try:
            value = factory()
        except BaseException as error:
            with lock:
                state['error'] = error
            return
        with lock:
            late = state.get('abandoned', False)
            if not late:
                state['value'] = value
        if late:
            try:
                value.close()
            except (OSError, RuntimeError, AttributeError):
                pass
    thread = threading.Thread(target=work, name='freevideo-device-memory', daemon=True)
    thread.start()
    thread.join(seconds)
    with lock:
        if 'value' in state:
            return state['value']
        if 'error' in state:
            raise state['error']
        state['abandoned'] = True
    raise TimeoutError('NVML did not answer within %g s; device-wide memory is not read for this request' % seconds)


class LiveGPUBudget:
    """Refresh a Windows allocator ceiling at idle CUDA stage/step boundaries.

    The 256 MiB reserve is growth headroom, in addition to observed allocations
    outside Torch. A larger driver budget must survive two observations before
    use. Pressure takes effect immediately, reclaiming optional weights first;
    a cap that would fall below the next step's workspace first waits briefly
    for the budget to recover. No background thread changes CUDA limits or
    frees tensors in active kernels.
    """
    def __init__(self, torch, report, maximum_bytes, reserve_bytes, *, reader_factory=None,
                 device_factory=None, settle_seconds=SETTLE_SECONDS, sleep=time.sleep, clock=time.monotonic,
                 device_open_seconds=DEVICE_OPEN_SECONDS):
        self.torch, self.report = torch, report
        self.total = report['device_total_bytes']
        self.limit = report['effective_allocator_limit_bytes']
        self.maximum = min(maximum_bytes, self.total,
                           report.get('benchmark_allocator_limit_bytes') or self.total)
        self.reserve = reserve_bytes
        self.pending = None
        self.settle_seconds, self.sleep, self.clock = settle_seconds, sleep, clock
        self.started = clock()
        self.reader = None
        self.device = None
        self.device_memory = None
        self.receipt = report['dynamic_budget'] = dict(enabled=True,
            base_reserve_bytes=reserve_bytes, maximum_allocator_bytes=self.maximum,
            minimum_limit_bytes=self.limit, maximum_limit_bytes=self.limit,
            observations=0, transitions=[], errors=[])
        try:
            if reader_factory is None:
                from .windows_gpu_memory import AdapterMemory
                reader_factory = AdapterMemory
            self.reader = reader_factory()
        except (OSError, RuntimeError, AttributeError, ValueError) as error:
            self._error(error)
        try:
            if device_factory is None and windows():
                from .monitoring import DeviceMemory
                uuid = str(getattr(torch.cuda.get_device_properties(torch.cuda.current_device()), 'uuid', ''))
                if uuid and not uuid.startswith(('GPU-', 'MIG-')):
                    uuid = 'GPU-' + uuid
                device_factory = lambda: DeviceMemory(uuid or None)
            if device_factory is not None:
                self.device = open_bounded(device_factory, device_open_seconds)
        except (OSError, RuntimeError, AttributeError, ValueError) as error:
            self._error(error)

    def _error(self, error):
        errors = self.receipt['errors']
        if len(errors) < 8 and str(error) not in errors:
            errors.append(str(error))

    def _observe(self):
        cuda = self.torch.cuda
        free, _ = cuda.mem_get_info()
        reserved = cuda.memory_reserved()
        local = None
        if self.reader is not None:
            try:
                local = self.reader.sample().get('local')
            except (OSError, RuntimeError, AttributeError, ValueError) as error:
                self._error(error)
        decision = allocator_ceiling(self.maximum, self.total, free, reserved, self.reserve, local)
        candidate = decision['allocator_limit_bytes']
        # An unavailable driver reading never grants extra residency. CUDA can
        # still report less live capacity and trigger reclamation.
        known = 'wddm_pool_capacity_bytes' in decision['bounds']
        if not known:
            candidate = min(candidate, self.limit)
        self.device_memory = None
        if self.device is not None:
            try:
                self.device_memory = self.device.sample()
            except (OSError, RuntimeError, AttributeError, ValueError) as error:
                self._error(error)
        return free, reserved, local, decision, known, candidate

    def device_headroom(self):
        """Physical device memory no process holds at the last observation, or None if unread."""
        memory = self.device_memory or {}
        used, total = memory.get('used_bytes'), memory.get('total_bytes')
        if type(used) is not int or type(total) is not int:
            return None
        return max(0, total - used)

    def _settle(self, workspace):
        """Wait for a driver budget that again covers `workspace` twice in a row."""
        deadline = self.clock() + self.settle_seconds
        seen, samples = [], 0
        while self.clock() < deadline:
            self.sleep(SETTLE_INTERVAL)
            *_, known, candidate = self._observe()
            samples += 1
            seen = seen + [candidate] if known and candidate >= workspace else []
            if len(seen) == 2:
                return min(seen), samples
        return None, samples

    def refresh(self, stage, reclaim=None, workspace=None):
        cuda = self.torch.cuda
        free, reserved, local, decision, known, candidate = self._observe()
        target, action = self.limit, 'hold'
        if candidate < self.limit:
            target, action, self.pending = candidate, 'shrink', None
        elif candidate >= self.limit + 64 * 2**20:
            if self.pending is not None:
                target, action = min(candidate, self.pending), 'grow'
                self.pending = None
            else:
                self.pending, action = candidate, 'pending'
        else:
            self.pending = None
        row = dict(stage=stage, elapsed_seconds=self.clock()-self.started,
                   previous_limit_bytes=self.limit, candidate_limit_bytes=candidate,
                   allocator_limit_bytes=target, action=action, driver_available=known,
                   live_free_bytes=free, reserved_bytes=reserved,
                   non_torch_local_bytes=decision['observed_non_torch_local_bytes'],
                   driver_budget_bytes=local.get('budget_bytes') if known else None,
                   device_used_bytes=(self.device_memory or {}).get('used_bytes'))
        self.receipt['observations'] += 1
        self.receipt['last'] = row
        if action in ('shrink', 'grow'):
            if len(self.receipt['transitions']) < 64:
                self.receipt['transitions'].append(row)
            if action == 'shrink':
                if reclaim is not None:
                    reclaim(target)
                if cuda.memory_reserved() > target:
                    cuda.empty_cache()
                if known and workspace is not None and target < workspace:
                    started = self.clock()
                    settled, samples = self._settle(workspace)
                    row.update(settle_seconds=self.clock() - started, settle_samples=samples,
                               workspace_bytes=int(workspace))
                    if settled is not None:
                        target = min(settled, self.limit)
                        row.update(action='settled', allocator_limit_bytes=target, settled_candidate_bytes=settled)
            # The setter only governs future allocations. If active tensors
            # cannot fit after reclamation, use the existing controlled OOM
            # recovery rather than assume that setting a fraction evicts them.
            cuda.set_per_process_memory_fraction(max(0, target) / self.total)
            self.limit = target
            self.report['effective_allocator_limit_bytes'] = target
            self.receipt['minimum_limit_bytes'] = min(self.receipt['minimum_limit_bytes'], target)
            self.receipt['maximum_limit_bytes'] = max(self.receipt['maximum_limit_bytes'], target)
            if target <= 0 or cuda.memory_allocated() > target:
                raise cuda.OutOfMemoryError('CUDA out of memory: live local GPU budget decreased below active allocations')
        return self.limit

    def close(self):
        reader, self.reader = self.reader, None
        if reader is not None:
            try:
                reader.close()
            except (OSError, RuntimeError) as error:
                self._error(error)
        device, self.device = self.device, None
        if device is not None:
            try:
                device.close()
            except (OSError, RuntimeError) as error:
                self._error(error)
