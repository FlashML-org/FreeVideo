"""Whole-device NVML sampling; values include driver reservations."""
import bisect
import csv
import ctypes
import json
import os
from pathlib import Path
import threading
import time
import tempfile
from .system import windows
from .long_paths import io_path, plain_data


def load_ledger(path, *, retain=True):
    """Read a verification ledger; a missing, unreadable or non-object file counts as empty.

    A ledger only caches what was verified, so damage (for example a file zero-filled
    by a power loss) must lead to a fresh check, never to a failed install or run.
    A damaged file is renamed to ``<name>.corrupt-<date>`` so it stays available for
    diagnosis; ``retain=False`` leaves it in place for read-only callers."""
    path = io_path(path)
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    if retain and path.is_file():
        try:
            kept = path.with_name(path.name + '.corrupt-' + time.strftime('%Y%m%d'))
            if not kept.exists():
                path.replace(kept)
        except OSError:
            pass
    return {}


def save(path, value, *, staging=None, plain_paths=True, durable=False):
    path = io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique, closed staging file; Windows readers/antivirus may briefly hold
    # the destination. Keep the previous good report and the failed staging file.
    # ``staging`` names it for a caller whose folder cannot take a longer name
    # than the file itself.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
            prefix=path.name + '.' if staging is None else staging, suffix='.tmp',
            dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(plain_data(value) if plain_paths else value, ensure_ascii=False, indent=2) + '\n')
        if durable:
            # Without this, a power loss after the replace can leave a zero-filled file.
            stream.flush()
            os.fsync(stream.fileno())
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if not windows() or attempt == 7:
                raise
            time.sleep(.1 * (attempt + 1))


class Memory(ctypes.Structure):
    _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong),
               ('used', ctypes.c_ulonglong)]


class Utilization(ctypes.Structure):
    _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]


class Activity:
    """Constant-space driver observations; unsupported sensors remain unknown."""
    fields = ('gpu_util_percent', 'temperature_c', 'power_mw', 'sm_clock_mhz', 'memory_clock_mhz')
    clock_reasons = {'sw_power_cap': 0x04, 'hw_slowdown': 0x08,
                     'sw_thermal': 0x20, 'hw_thermal': 0x40, 'hw_power_brake': 0x80}

    def __init__(self):
        self.samples = 0
        self.values = {}
        self.reason_samples = 0
        self.reason_counts = {key: 0 for key in self.clock_reasons}
        self.reason_mask = 0

    def add(self, util, auxiliary, reasons):
        self.samples += 1
        for name, value in zip(self.fields, (util, *auxiliary)):
            if type(value) not in (int, float) or not 0 <= value < float('inf'):
                continue
            row = self.values.setdefault(name, dict(samples=0, total=0, minimum=value, maximum=value))
            row['samples'] += 1
            row['total'] += value
            row['minimum'] = min(row['minimum'], value)
            row['maximum'] = max(row['maximum'], value)
        if type(reasons) is int and reasons >= 0:
            self.reason_samples += 1
            self.reason_mask |= reasons
            for key, mask in self.clock_reasons.items():
                self.reason_counts[key] += bool(reasons & mask)

    def result(self):
        result = dict(samples=self.samples, clock_reason_samples=self.reason_samples)
        for name, row in self.values.items():
            result[name] = dict(samples=row['samples'], minimum=row['minimum'], maximum=row['maximum'],
                                mean=row['total']/row['samples'])
        if self.reason_samples:
            result['clock_reason_mask'] = self.reason_mask
            result['clock_reason_counts'] = dict(self.reason_counts)
        return result


def thermal_slowdown(measured, *, share=.5, clock_ratio=.7, minimum_samples=200):
    """The worker's busy time held back by driver thermal slowdown, or None.

    Counted only when thermal reasons cover at least `share` of the busy
    samples and the mean SM clock stays below `clock_ratio` of the highest
    one observed, so a brief hot spell or a normal power cap is not reported.
    """
    busy = measured.get('busy_activity') if isinstance(measured, dict) else None
    if not isinstance(busy, dict):
        return None
    samples = _count(busy.get('clock_reason_samples'))
    counts = busy.get('clock_reason_counts')
    counts = counts if isinstance(counts, dict) else {}
    clock = busy.get('sm_clock_mhz')
    clock = clock if isinstance(clock, dict) else {}
    mean, peak = _count(clock.get('mean')), _count(clock.get('maximum'))
    if samples < minimum_samples or not mean or not peak:
        return None
    thermal = min(samples, max(_count(counts.get('sw_thermal')), _count(counts.get('hw_thermal'))))
    if thermal < share * samples or mean > clock_ratio * peak:
        return None
    temperature = busy.get('temperature_c')
    temperature = temperature.get('mean') if isinstance(temperature, dict) else None
    return dict(share=round(thermal / samples, 3), sm_clock_mean_mhz=round(mean), sm_clock_max_mhz=round(peak),
                temperature_mean_c=round(temperature, 1) if type(temperature) in (int, float) and 0 <= temperature <= 1000 else None)


class OtherPrograms:
    """Other programs' share of the GPU's cores while a worker computes.

    A game, a browser tab or another generation on the same GPU slows every
    step, yet the device still reads fully busy at its full clock, exactly as
    when the worker has it alone. NVML's per-process samples tell the two
    apart where the driver reports them. Our processes are the one that
    monitors, the worker and their descendants. Constant space: counts and
    sums over the checks made while the worker was computing.
    """
    seconds = 1.
    refresh_seconds = 5.
    share_percent = 20
    # The worker's own share below which a check is not counted: while it
    # loads files, another program's work delays nothing of ours.
    own_percent = 15
    failures_allowed = 5

    def __init__(self, lib, handle, pid=None):
        self.lib, self.handle, self.pid = lib, handle, pid
        self.since = int(time.time() * 1e6)
        self.ours, self.seen = set(), set()
        self.checked = self.refreshed = 0.
        self.supported = None
        self.own_reported = False
        self.failures = 0
        self.checks = self.shared = 0
        self.other_total = self.own_total = self.other_peak = 0.

    def read(self):
        """{pid: mean % of cores} over the sample periods newer than the last ones read; None when unavailable.

        The driver leaves a process out of a period in which it did nothing,
        so the mean is over the periods the busiest reporter has, not over a
        process's own rows.
        """
        from .background_room import UtilizationSample, NOT_FOUND, INSUFFICIENT_SIZE
        function = getattr(self.lib, 'nvmlDeviceGetProcessUtilization', None)
        if function is None:
            return None
        count = ctypes.c_uint(0)
        rc = function(self.handle, None, ctypes.byref(count), ctypes.c_ulonglong(self.since))
        if rc == NOT_FOUND:
            return {}
        if rc != INSUFFICIENT_SIZE:
            return None
        count = ctypes.c_uint(count.value + 16)
        rows = (UtilizationSample * count.value)()
        rc = function(self.handle, rows, ctypes.byref(count), ctypes.c_ulonglong(self.since))
        if rc == NOT_FOUND:
            return {}
        if rc:
            return None
        totals, periods = {}, {}
        for row in rows[:count.value]:
            if row.sm <= 100:
                totals[row.pid] = totals.get(row.pid, 0) + row.sm
                periods[row.pid] = periods.get(row.pid, 0) + 1
            self.since = max(self.since, row.timestamp)
        number = max(periods.values(), default=1)
        return {pid: total / number for pid, total in totals.items()}

    def refresh(self):
        """A worker must never turn into another program for want of a process list: the last known ones stay ours."""
        from .background_room import family
        try:
            self.ours = family(os.getpid()) | (family(self.pid) if self.pid else set())
        except OSError:
            self.ours = self.ours or {pid for pid in (os.getpid(), self.pid) if pid}

    def check(self, util):
        """Read once a second; count the reading when the worker is computing on a busy device.

        Returns the other programs' share of the cores in percent for a reading that was counted, else None.
        """
        now = time.monotonic()
        if self.supported is False or now - self.checked < self.seconds:
            return None
        self.checked = now
        found = self.read()
        if found is None:
            self.failures += 1
            if self.supported is None and self.failures >= self.failures_allowed:
                self.supported = False
            return None
        self.supported = True
        if not found or util is None or util < 50:
            return None
        # A process we have not seen may be a child the worker just started.
        if now - self.refreshed >= self.refresh_seconds or any(pid not in self.seen for pid in found):
            self.refresh()
            self.refreshed, self.seen = now, set(found)
        other = min(100., sum(value for pid, value in found.items() if pid not in self.ours))
        own = min(100., sum(value for pid, value in found.items() if pid in self.ours))
        self.own_reported = self.own_reported or own > 0
        if not self.own_reported:
            # Some drivers report no samples for a compute process: what the
            # device did beyond the other programs was the worker's.
            own = max(0., util - other)
        if own < self.own_percent:
            return None
        self.checks += 1
        self.shared += other >= self.share_percent
        self.other_total += other
        self.own_total += own
        self.other_peak = max(self.other_peak, other)
        return other

    def result(self):
        result = dict(supported=bool(self.supported), own_reported=self.own_reported, checks=self.checks,
                      shared_checks=self.shared, check_seconds=self.seconds, share_percent=self.share_percent)
        if self.checks:
            result.update(other_mean_percent=self.other_total / self.checks, other_peak_percent=self.other_peak,
                          own_mean_percent=self.own_total / self.checks)
        return result


def shared_gpu(measured, *, share=.5, minimum_checks=30):
    """Other programs holding a real part of the GPU for most of the time the worker computed, or None.

    Counted only when they took at least `share_percent` of the cores in at
    least `share` of the checks, so a desktop that draws now and then or a
    short video is not reported.
    """
    programs = measured.get('other_programs') if isinstance(measured, dict) else None
    if not isinstance(programs, dict):
        return None
    checks, shared, mean = (programs.get(key) for key in ('checks', 'shared_checks', 'other_mean_percent'))
    if (any(type(value) not in (int, float) for value in (checks, shared, mean))
            or not minimum_checks <= checks <= 1e9 or not 0 <= shared <= checks or not 0 <= mean <= 100
            or shared < share * checks):
        return None
    return dict(share=round(shared / checks, 3), other_mean_percent=round(mean))


# Other programs' mean share of the GPU, in percent, from which the result says so:
# the run took at least about a quarter longer than with the GPU to itself.
SHARED_NOTE_PERCENT = 20


def shared_gpu_note(measured):
    """{other_mean_percent} for the note under a result, or None.

    Shown when other programs averaged at least SHARED_NOTE_PERCENT of the
    GPU, and shared_gpu holds: at least 30 checks, with other programs using
    at least 20% in at least half of them.
    """
    if shared_gpu(measured) is None:
        return None
    mean = measured['other_programs']['other_mean_percent']
    return dict(other_mean_percent=mean) if mean >= SHARED_NOTE_PERCENT else None


def _count(value):
    return value if type(value) in (int, float) and 0 <= value <= 1e12 else 0


def combined(rows):
    """Several attempts' busy-time clock reasons and other programs' checks as one observation.

    Only what thermal_slowdown and shared_gpu read: counts add up, means are
    weighted by their samples and peaks are the highest.
    """
    result = {}
    busy = [row['busy_activity'] for row in rows if isinstance(row.get('busy_activity'), dict)]
    if busy:
        merged = dict(clock_reason_samples=sum(_count(item.get('clock_reason_samples')) for item in busy),
                      clock_reason_counts={key: sum(_count(item['clock_reason_counts'].get(key)) for item in busy
                                                    if isinstance(item.get('clock_reason_counts'), dict))
                                           for key in Activity.clock_reasons})
        for field in ('sm_clock_mhz', 'temperature_c'):
            parts = [(item[field], _count(item[field].get('samples')) or _count(item.get('clock_reason_samples')))
                     for item in busy if isinstance(item.get(field), dict) and _count(item[field].get('mean'))]
            weight = sum(samples for _, samples in parts)
            if weight:
                merged[field] = dict(mean=sum(_count(part.get('mean')) * samples for part, samples in parts) / weight,
                                     maximum=max(_count(part.get('maximum')) for part, _ in parts))
        result['busy_activity'] = merged
    programs = [row['other_programs'] for row in rows if isinstance(row.get('other_programs'), dict)]
    if programs:
        counted = [(item, _count(item.get('checks'))) for item in programs]
        checks = sum(number for _, number in counted)
        merged = dict(supported=any(item.get('supported') is True for item in programs),
                      own_reported=any(item.get('own_reported') is True for item in programs),
                      checks=checks, shared_checks=sum(_count(item.get('shared_checks')) for item in programs))
        if checks:
            merged.update(other_mean_percent=sum(_count(item.get('other_mean_percent')) * number for item, number in counted) / checks,
                          other_peak_percent=max(_count(item.get('other_peak_percent')) for item in programs),
                          own_mean_percent=sum(_count(item.get('own_mean_percent')) * number for item, number in counted) / checks)
        result['other_programs'] = merged
    return result


def worker_gpu(report):
    """The video worker's GPU telemetry for a whole request, or None.

    A request's report keeps it in `resources.gpu` and a written diagnostic
    report in `memory.video.gpu`, but that is the last attempt's alone: after
    a retry that only decodes, the attempt that sampled is in
    `resource_attempts`. Several attempts are combined, so a share of the
    request's busy time is a share of all of it.
    """
    report = report if isinstance(report, dict) else {}
    attempts = report.get('resource_attempts')
    rows = [row['resources']['gpu'] for row in attempts if isinstance(row, dict) and isinstance(row.get('resources'), dict)
            and isinstance(row['resources'].get('gpu'), dict) and row['resources']['gpu']] if isinstance(attempts, list) else []
    if len(rows) > 1:
        return combined(rows)
    video = report.get('memory').get('video') if isinstance(report.get('memory'), dict) else None
    for section in (report.get('resources'), video):
        if isinstance(section, dict) and isinstance(section.get('gpu'), dict) and section['gpu']:
            return section['gpu']
    return None


def nvml_device(device=None):
    """Initialize NVML and select one GPU by UUID or index; returns (library, handle, device)."""
    if windows():
        candidates = [Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/nvml.dll',
                      Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NVIDIA Corporation/NVSMI/nvml.dll']
        library = next((p for p in candidates if p.is_file()), None)
        if library is None:
            raise RuntimeError('NVML DLL is missing from the NVIDIA driver installation; repair the driver and rerun test.')
        lib = ctypes.CDLL(str(library))
    else:
        lib = ctypes.CDLL('libnvidia-ml.so.1')
    rc = lib.nvmlInit_v2()
    if rc:
        raise RuntimeError(f'NVML initialization returned {rc}')
    handle = ctypes.c_void_p()
    device = str(device if device is not None else os.environ.get('CUDA_VISIBLE_DEVICES', '0').split(',')[0])
    if device.startswith('GPU-'):
        rc = lib.nvmlDeviceGetHandleByUUID(device.encode(), ctypes.byref(handle))
    else:
        rc = lib.nvmlDeviceGetHandleByIndex_v2(int(device), ctypes.byref(handle))
    if rc:
        lib.nvmlShutdown()
        raise RuntimeError(f'NVML could not select {device}: {rc}')
    return lib, handle, device


class DeviceMemory:
    """Whole-device memory in use by every process, read on demand.

    Windows' per-process budget and CUDA's free memory leave out what other
    applications hold until the device is already full; NVML counts them.
    """
    def __init__(self, device=None):
        self.lib, self.handle, self.device = nvml_device(device)

    def sample(self):
        m = Memory()
        rc = self.lib.nvmlDeviceGetMemoryInfo(self.handle, ctypes.byref(m))
        if rc:
            raise RuntimeError(f'NVML memory read returned {rc}')
        if m.used > m.total or not m.total:
            raise RuntimeError('Driver returned unavailable NVML device memory')
        return dict(used_bytes=m.used, total_bytes=m.total)

    def close(self):
        lib, self.lib = self.lib, None
        if lib is not None:
            lib.nvmlShutdown()


class Monitor:
    def __init__(self, path, interval=0.05, device=None, pid=None):
        self.lib, self.handle, device = nvml_device(device)
        self.device = device
        self.programs = OtherPrograms(self.lib, self.handle, pid)
        self.path, self.interval = Path(path), interval
        self.stop_event = threading.Event()
        self.peak = 0
        self.baseline = 0
        self.last = 0
        self.count = 0
        self.errors = []
        self.max_temperature = None
        self.max_power_mw = None
        self.activity, self.busy_activity = Activity(), Activity()

    def sample(self):
        m, u = Memory(), Utilization()
        rc = self.lib.nvmlDeviceGetMemoryInfo(self.handle, ctypes.byref(m))
        if rc:
            raise RuntimeError(f'NVML memory read returned {rc}')
        if m.used > m.total or not m.total:
            raise RuntimeError('Driver returned unavailable NVML device memory; no zero/estimated peak will be reported.')
        try:
            rc = self.lib.nvmlDeviceGetUtilizationRates(self.handle, ctypes.byref(u))
        except AttributeError:
            rc = -1
        return m.used, m.total, u.gpu if rc == 0 and u.gpu <= 100 else None

    def start(self):
        self.baseline = self.sample()[0]
        self.peak = self.baseline
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        return self

    def auxiliary(self):
        values = []
        for name, extra in [('nvmlDeviceGetTemperature', (0,)), ('nvmlDeviceGetPowerUsage', ()),
                            ('nvmlDeviceGetClockInfo', (1,)), ('nvmlDeviceGetClockInfo', (2,))]:
            value = ctypes.c_uint()
            try:
                rc = getattr(self.lib, name)(self.handle, *extra, ctypes.byref(value))
                values.append(value.value if rc == 0 else None)
            except AttributeError:
                values.append(None)
        return values

    def clock_reasons(self):
        for name in ('nvmlDeviceGetCurrentClocksEventReasons', 'nvmlDeviceGetCurrentClocksThrottleReasons'):
            try:
                value = ctypes.c_ulonglong()
                if getattr(self.lib, name)(self.handle, ctypes.byref(value)) == 0:
                    return value.value
            except AttributeError:
                pass
        return None

    def loop(self):
        with self.path.open('w', buffering=1, encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['epoch_seconds', 'monotonic_seconds', 'used_bytes', 'total_bytes', 'gpu_util_percent',
                             'temperature_c', 'power_mw', 'sm_clock_mhz', 'memory_clock_mhz', 'clock_reason_mask',
                             'other_programs_percent'])
            while not self.stop_event.is_set():
                try:
                    used, total, util = self.sample()
                    self.peak = max(self.peak, used)
                    self.last = used
                    self.count += 1
                    auxiliary = self.auxiliary()
                    reasons = self.clock_reasons()
                    self.activity.add(util, auxiliary, reasons)
                    if util is not None and util >= 50:
                        self.busy_activity.add(util, auxiliary, reasons)
                    if auxiliary[0] is not None:
                        self.max_temperature = max(self.max_temperature or 0, auxiliary[0])
                    if auxiliary[1] is not None:
                        self.max_power_mw = max(self.max_power_mw or 0, auxiliary[1])
                    try:
                        other = self.programs.check(util)
                    except Exception:
                        other = None
                    writer.writerow([time.time(), time.monotonic(), used, total, util, *auxiliary, reasons,
                                     '' if other is None else '%.1f' % other])
                except Exception as e:
                    if str(e) not in self.errors and len(self.errors) < 20:
                        self.errors.append(str(e))
                self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        self.lib.nvmlShutdown()
        return {'device': self.device, 'gpu_peak_bytes': self.peak, 'gpu_peak_mib': self.peak / 2**20,
                'gpu_baseline_bytes': self.baseline, 'gpu_last_bytes': self.last,
                'max_temperature_c': self.max_temperature, 'max_power_mw': self.max_power_mw,
                'activity': self.activity.result(), 'busy_activity': self.busy_activity.result(),
                'busy_util_threshold_percent': 50,
                'activity_scope': 'Whole device during this worker request, including load and decode. '
                                  'Sample means, not duration-weighted measurements. Busy subset has GPU utilization >= 50%; '
                                  'clock reasons are driver observations, not attribution of elapsed time.',
                'other_programs': self.programs.result(),
                'other_programs_scope': 'Per-process driver samples read once a second while the worker was computing on a '
                                        'busy device: the share of its cores used by processes outside this worker and its '
                                        'monitor. shared_checks counts readings at or above share_percent. Without own_reported '
                                        "the worker's share is the device's utilization beyond the other programs. A driver "
                                        'without per-process samples reports no checks.',
                'sampling_interval_seconds': self.interval, 'samples': self.count,
                'sampling_errors': self.errors, 'sample_csv': str(self.path)}



def step_activity(csv_path, steps, *, limit_bytes=64 * 2**20):
    """Add each sampling step's whole-device NVML samples to it as `device_activity`.

    A request-wide mean cannot show one slow step: whether the device was full,
    another process kept it busy or clocks fell during that step. Steps carry
    the monotonic time they ended; the monitor's CSV, written by the parent
    process, carries the same clock. Returns how many steps were annotated.
    Missing, oversized or partial files add nothing.
    """
    windows = sorted((row['monotonic_seconds'] - row['seconds'], row['monotonic_seconds'], index)
                     for index, row in enumerate(steps if isinstance(steps, list) else [])
                     if isinstance(row, dict) and all(type(row.get(key)) in (int, float) and row[key] >= 0
                                                      for key in ('monotonic_seconds', 'seconds')))
    if not windows:
        return 0
    try:
        if Path(csv_path).stat().st_size > limit_bytes:
            return 0
        with open(csv_path, newline='', encoding='utf-8') as handle:
            samples = list(csv.DictReader(handle))
    except (OSError, ValueError, csv.Error):
        return 0
    def number(sample, key):
        try:
            value = float(sample.get(key) or 'nan')
        except ValueError:
            return None
        return value if 0 <= value < float('inf') else None
    starts = [start for start, _, _ in windows]
    found = {}
    for sample in samples:
        moment = number(sample, 'monotonic_seconds')
        if moment is None:
            continue
        position = bisect.bisect_right(starts, moment) - 1
        if position < 0 or moment > windows[position][1]:
            continue
        item = found.setdefault(windows[position][2], dict(samples=0, busy_samples=0, sums={}))
        item['samples'] += 1
        util = number(sample, 'gpu_util_percent')
        item['busy_samples'] += util is not None and util >= 50
        for key, value in (('gpu_util_mean_percent', util), ('sm_clock_mean_mhz', number(sample, 'sm_clock_mhz')),
                           ('power_mean_mw', number(sample, 'power_mw'))):
            if value is not None:
                total = item['sums'].setdefault(key, [0., 0])
                total[0] += value
                total[1] += 1
        other = number(sample, 'other_programs_percent')
        if other is not None and other <= 100:
            item['other_programs_checks'] = item.get('other_programs_checks', 0) + 1
            item['other_programs_percent_peak'] = max(item.get('other_programs_percent_peak', 0.), other)
            total = item['sums'].setdefault('other_programs_percent_mean', [0., 0])
            total[0] += other
            total[1] += 1
        used, size = number(sample, 'used_bytes'), number(sample, 'total_bytes')
        if used is not None:
            item['device_used_peak_bytes'] = max(item.get('device_used_peak_bytes', 0), int(used))
            item['device_used_minimum_bytes'] = min(item.get('device_used_minimum_bytes', int(used)), int(used))
        if size is not None:
            item['device_total_bytes'] = int(size)
    for index, item in found.items():
        for key, (total, count) in item.pop('sums').items():
            item[key] = total / count
        steps[index]['device_activity'] = item
    return len(found)
