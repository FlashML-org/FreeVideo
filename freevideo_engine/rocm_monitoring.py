"""Read AMDGPU kernel counters for the UUID selected by the HIP worker."""
import os
from pathlib import Path
import threading

from .monitoring import Activity, Monitor


class ROCmMonitor(Monitor):
    def __init__(self, path, interval=0.05, device=None):
        self.device = str(device or os.environ.get('ROCR_VISIBLE_DEVICES', ''))
        if not self.device.startswith('GPU-') or ',' in self.device:
            raise ValueError('AMDGPU monitoring requires one explicit ROCR GPU UUID')
        unique = int(self.device.removeprefix('GPU-'), 16)
        matched = []
        for node in Path('/sys/class/kfd/kfd/topology/nodes').glob('*/properties'):
            try:
                props = dict(line.split() for line in node.read_text().splitlines())
            except PermissionError:
                # The device cgroup intentionally hides the other GPUs.
                continue
            if int(props.get('unique_id', '0')) == unique:
                matched.append(Path('/sys/class/drm') / ('renderD'+props['drm_render_minor']) / 'device')
        if len(matched) != 1:
            raise RuntimeError('Cannot map ROCR UUID to exactly one AMDGPU device: '+self.device)
        self.root = matched[0].resolve()
        self.source = 'AMDGPU sysfs: '+str(self.root)
        self.path, self.interval = Path(path), interval
        self.stop_event = threading.Event()
        self.peak = self.baseline = self.last = self.count = 0
        self.errors = []
        self.max_temperature = self.max_power_mw = None
        self.activity, self.busy_activity = Activity(), Activity()
        self.hwmon = next((self.root / 'hwmon').glob('hwmon*'), None)

    def sample(self):
        used = int((self.root / 'mem_info_vram_used').read_text())
        total = int((self.root / 'mem_info_vram_total').read_text())
        if not 0 <= used <= total or not total:
            raise RuntimeError('AMDGPU returned invalid VRAM counters')
        try:
            util = int((self.root / 'gpu_busy_percent').read_text())
            util = util if 0 <= util <= 100 else None
        except (OSError, ValueError):
            util = None
        return used, total, util

    def auxiliary(self):
        def sensor(name, divisor):
            try:
                value = int((self.hwmon / name).read_text()) / divisor
                return value if value >= 0 else None
            except (OSError, ValueError, TypeError):
                return None
        def clock(name):
            try:
                for row in (self.root / name).read_text().splitlines():
                    if '*' in row:
                        return int(row.split()[1].removesuffix('Mhz').removesuffix('MHz'))
            except (OSError, ValueError, IndexError):
                pass
            return None
        return [sensor('temp1_input', 1000), sensor('power1_average', 1000),
                clock('pp_dpm_sclk'), clock('pp_dpm_mclk')]

    def clock_reasons(self):
        return None

    def shutdown(self):
        pass
