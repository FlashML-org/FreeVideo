"""Whole-device NVML sampling; values include driver reservations."""
import csv
import ctypes
import json
import os
from pathlib import Path
import threading
import time
import tempfile
from .system import windows


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique, closed staging file; Windows readers/antivirus may briefly hold
    # the destination. Keep the previous good report and the failed staging file.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
            prefix=path.name + '.', suffix='.tmp', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
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
    samples = busy.get('clock_reason_samples') or 0
    counts = busy.get('clock_reason_counts') or {}
    clock = busy.get('sm_clock_mhz') or {}
    mean, peak = clock.get('mean'), clock.get('maximum')
    if samples < minimum_samples or not mean or not peak:
        return None
    thermal = min(samples, max(counts.get('sw_thermal') or 0, counts.get('hw_thermal') or 0))
    if thermal < share * samples or mean > clock_ratio * peak:
        return None
    temperature = (busy.get('temperature_c') or {}).get('mean')
    return dict(share=round(thermal / samples, 3), sm_clock_mean_mhz=round(mean), sm_clock_max_mhz=round(peak),
                temperature_mean_c=round(temperature, 1) if isinstance(temperature, (int, float)) else None)


class Monitor:
    def __init__(self, path, interval=0.05, device=None):
        if windows():
            candidates = [Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/nvml.dll',
                          Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NVIDIA Corporation/NVSMI/nvml.dll']
            library = next((p for p in candidates if p.is_file()), None)
            if library is None:
                raise RuntimeError('NVML DLL is missing from the NVIDIA driver installation; repair the driver and rerun test.')
            self.lib = ctypes.CDLL(str(library))
        else:
            self.lib = ctypes.CDLL('libnvidia-ml.so.1')
        rc = self.lib.nvmlInit_v2()
        if rc:
            raise RuntimeError(f'NVML initialization returned {rc}')
        self.handle = ctypes.c_void_p()
        device = str(device if device is not None else os.environ.get('CUDA_VISIBLE_DEVICES', '0').split(',')[0])
        if device.startswith('GPU-'):
            rc = self.lib.nvmlDeviceGetHandleByUUID(device.encode(), ctypes.byref(self.handle))
        else:
            rc = self.lib.nvmlDeviceGetHandleByIndex_v2(int(device), ctypes.byref(self.handle))
        if rc:
            self.lib.nvmlShutdown()
            raise RuntimeError(f'NVML could not select {device}: {rc}')
        self.device = device
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
                             'temperature_c', 'power_mw', 'sm_clock_mhz', 'memory_clock_mhz', 'clock_reason_mask'])
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
                    writer.writerow([time.time(), time.monotonic(), used, total, util, *auxiliary, reasons])
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
                'sampling_interval_seconds': self.interval, 'samples': self.count,
                'sampling_errors': self.errors, 'sample_csv': str(self.path)}
