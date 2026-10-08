"""Observed capacity, including live usage and enclosing Linux RAM limits."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import importlib.util
import platform
import re
from pathlib import Path

GiB = 1 << 30


@dataclass(frozen=True)
class Hardware:
    gpu_name: str
    capability: tuple[int, int]
    vram_total: int
    vram_free: int
    ram_total: int
    ram_available: int
    system: str = 'Linux'
    torch_version: str = ''
    cuda_version: str = ''
    cgroup_ram_limit: int | None = None
    gpu_uuid: str = ''
    driver_version: str = ''
    hip_version: str = ''
    gcn_arch: str = ''

    @property
    def architecture(self):
        if self.hip_version:
            arch = self.gcn_arch.split(':')[0]
            return 'rdna4' if arch in ('gfx1200', 'gfx1201') else 'amd-' + (arch or 'unknown')
        if len(self.capability) != 2 or any(type(v) is not int for v in self.capability):
            return 'other'
        if (8, 0) <= self.capability < (8, 9):
            return 'ampere'
        if self.capability == (8, 9):
            return 'ada'
        if self.capability[0] == 9:
            return 'hopper'
        if self.capability in ((10, 0), (10, 3)):
            return 'blackwell-datacenter'
        if self.capability == (11, 0):
            return 'blackwell-tegra'
        if self.capability == (12, 1):
            return 'blackwell-unified'
        if self.capability[0] == 12:
            return 'blackwell-rtx'
        return 'other'

    def cuda_compatibility(self):
        """The engine's arithmetic floor, not a GPU model or performance allowlist.

        Passing this check permits setup. The installed Torch, Triton and
        attention kernels must still execute successfully on the selected GPU.
        CPU architecture/platform and driver requirements are checked separately.
        """
        if self.hip_version:
            return dict(status='probe-required', admitted=True, gpu_name=self.gpu_name,
                        capability=list(self.capability), minimum_capability=None,
                        driver_version=self.driver_version, architecture=self.architecture,
                        hip_version=self.hip_version, gcn_arch=self.gcn_arch,
                        linear_compute='native-fp8' if self.architecture == 'rdna4' else 'bf16-weight-only',
                        validation='ROCm linear and attention execution probes are required before readiness.', error=None)
        capability = self.capability
        known = (len(capability) == 2 and all(type(v) is int for v in capability)
                 and capability[0] > 0 and 0 <= capability[1] <= 9)
        sm = '%d%d' % capability if known else 'unknown'
        admitted = known and capability >= (8, 0)
        status = ('probe-required' if admitted else
                  'CUDA_ARCH_UNSUPPORTED' if known else 'CUDA_CAPABILITY_UNKNOWN')
        detail = '%s (SM%s; compute capability %s; driver %s)' % (
            self.gpu_name, sm, '.'.join(map(str, capability)) if known else 'unknown',
            self.driver_version or 'unknown')
        error = None
        if not admitted:
            if known:
                error = ('[CUDA_ARCH_UNSUPPORTED] ' + detail + '. FreeVideo requires '
                         'native BF16 GPU computation (compute capability 8.0 / SM80 or newer). '
                         'This GPU is below that hardware requirement; a driver update cannot add it.')
            else:
                error = ('[CUDA_CAPABILITY_UNKNOWN] Could not determine the CUDA architecture of '
                         + detail + '. GPU detection did not return a valid compute capability. '
                         'Retry detection or export the report for the driver query result.')
        return dict(status=status, admitted=admitted, gpu_name=self.gpu_name,
                    capability=list(capability), minimum_capability=[8, 0],
                    driver_version=self.driver_version, architecture=self.architecture,
                    linear_compute=('bf16-weight-only' if capability < (8, 9) else 'native-fp8') if admitted else None,
                    validation='Actual linear and attention kernel probes are required before readiness.', error=error)

    def to_dict(self):
        return dict(asdict(self), architecture=self.architecture)

    @classmethod
    def from_dict(cls, value):
        fields = {k: v for k, v in value.items() if k != 'architecture'}
        fields['capability'] = tuple(fields['capability'])
        return cls(**fields)


def cgroup_memory(proc=Path('/proc')):
    """Live v1/v2 usage, including file cache and every visible ancestor cap."""
    result = dict(limit_bytes=None, available_bytes=None, reclaimable_available_bytes=None,
                  groups=[], complete=True)
    if platform.system() != 'Linux':
        return result
    try:
        groups = {}
        for line in (proc / 'self/cgroup').read_text(encoding='utf-8').splitlines():
            hierarchy, controllers, path = line.split(':', 2)
            if hierarchy == '0' and not controllers:
                groups[2] = Path(path)
            elif 'memory' in controllers.split(','):
                groups[1] = Path(path)
        mounts = (proc / 'self/mountinfo').read_text(encoding='utf-8').splitlines()
        seen = set()
        for line in mounts:
            before, after = line.split(' - ', 1)
            fields, filesystem = before.split(), after.split()
            version = 2 if filesystem[0] == 'cgroup2' else 1 if filesystem[0] == 'cgroup' and 'memory' in filesystem[2].split(',') else None
            if version not in groups:
                continue
            decode = lambda value: Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value))
            mount_root, root = decode(fields[3]), decode(fields[4])
            group = groups[version]
            relative = group.relative_to(mount_root) if group == mount_root or mount_root in group.parents else Path(str(group).lstrip('/'))
            current = root / relative
            for directory in (current, *current.parents):
                if (directory != root and root not in directory.parents) or directory in seen:
                    continue
                seen.add(directory)
                limit_file = directory / ('memory.max' if version == 2 else 'memory.limit_in_bytes')
                if not limit_file.is_file():
                    continue
                raw = limit_file.read_text(encoding='utf-8').strip()
                limit = None if raw == 'max' or int(raw) >= 1 << 60 else int(raw)
                usage_file = directory / ('memory.current' if version == 2 else 'memory.usage_in_bytes')
                try:
                    used = int(usage_file.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    used = None
                    result['complete'] = False
                try:
                    stats = dict((key, int(value)) for key, value in
                                 (line.split() for line in (directory / 'memory.stat').read_text(encoding='utf-8').splitlines()))
                except (OSError, ValueError):
                    stats = {}
                def metric(v2, v1):
                    return stats.get(v2) if version == 2 else stats.get('total_' + v1, stats.get(v1))
                entry = dict(version=version, limit_bytes=limit, current_bytes=used,
                    available_bytes=max(0, limit-used) if limit is not None and used is not None else 0 if limit is not None else None,
                    file_cache_bytes=metric('file', 'cache'), anonymous_bytes=metric('anon', 'rss'),
                    shmem_bytes=metric('shmem', 'shmem'), dirty_bytes=metric('file_dirty', 'dirty'),
                    writeback_bytes=metric('file_writeback', 'writeback'),
                    unevictable_bytes=metric('unevictable', 'unevictable'))
                # A memory.max charge is not all irreclaimable working memory:
                # immutable mapped model pages can be reclaimed and read again.
                # Keep raw headroom for download throttling and hard accounting.
                # Inference may credit only clean filesystem pages; missing
                # counters, tmpfs/shared pages, dirty data and locked pages earn
                # no credit. This estimate never changes the kernel's limit.
                names = ('file_cache_bytes', 'shmem_bytes', 'dirty_bytes',
                         'writeback_bytes', 'unevictable_bytes')
                complete = used is not None and all(type(entry[key]) is int and entry[key] >= 0 for key in names)
                clean = max(0, min(used, entry['file_cache_bytes']) - sum(entry[key] for key in names[1:])) if complete else 0
                entry['reclaimable_file_bytes'] = clean
                entry['reclaimable_available_bytes'] = (min(limit, entry['available_bytes'] + clean)
                    if limit is not None else None)
                result['groups'].append(entry)
    except (OSError, ValueError, IndexError):
        result['complete'] = False
    limited = [group for group in result['groups'] if group['limit_bytes'] is not None]
    if limited:
        result.update(limit_bytes=min(group['limit_bytes'] for group in limited),
                      available_bytes=min(group['available_bytes'] for group in limited),
                      reclaimable_available_bytes=min(group['reclaimable_available_bytes'] for group in limited))
    return result


def cgroup_capacity(*, include_reclaimable=False):
    state = cgroup_memory()
    return state['limit_bytes'], state.get('reclaimable_available_bytes', state['available_bytes']) if include_reclaimable else state['available_bytes']


def detect():
    # End the detector's CUDA context before starting the model worker. The
    # lightweight parent must not keep another GPU context or Torch heap alive.
    import json
    import subprocess
    import sys
    code = 'import json; from freevideo_engine.hardware import _detect_local; print(json.dumps(_detect_local().to_dict()))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Hardware detection failed: ' + result.stderr[-2000:])
    observed = Hardware.from_dict(json.loads(result.stdout.strip().splitlines()[-1]))
    # The isolated torch probe has exited. Its temporary Python/CUDA host heap
    # must not be subtracted again from the future worker's whole-process RAM
    # allowance. Refresh RAM in the lightweight controller after that exit.
    from dataclasses import replace
    from .system import system_memory
    memory = system_memory()
    limit, available = cgroup_capacity(include_reclaimable=True)
    return replace(observed, ram_total=memory['total_bytes'],
                   ram_available=min(memory['available_bytes'], available) if available is not None else memory['available_bytes'],
                   cgroup_ram_limit=limit)


def _detect_local():
    from .system import system_memory, nvidia_smi
    import subprocess
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('A CUDA GPU is required for VDN generation; plan --hardware-json can inspect presets offline.')
    index = torch.cuda.current_device()
    free, total = torch.cuda.mem_get_info(index)
    ram = system_memory()
    cap, available = cgroup_capacity(include_reclaimable=True)
    uuid = str(getattr(torch.cuda.get_device_properties(index), 'uuid', ''))
    if torch.version.hip and uuid:
        # Some HIP wheels wrap the 16 ASCII KFD hex digits as a CUDA UUID.
        # Recover the native unique_id rather than publishing that byte wrapper.
        try:
            from uuid import UUID
            native = UUID(uuid).bytes.decode('ascii')
            if re.fullmatch(r'[0-9a-fA-F]{16}', native):
                uuid = native.lower()
        except (ValueError, UnicodeDecodeError):
            pass
    if uuid and not uuid.startswith(('GPU-', 'MIG-')):
        uuid = 'GPU-' + uuid
    driver = ''
    try:
        if torch.version.hip:
            raise RuntimeError('HIP devices do not use NVIDIA driver queries')
        query = [nvidia_smi(), '--query-gpu=driver_version', '--format=csv,noheader']
        if uuid:
            query += ['--id=' + uuid]
        driver = subprocess.check_output(query, text=True, stderr=subprocess.DEVNULL, timeout=5).strip().splitlines()[0]
    except (OSError, RuntimeError, subprocess.SubprocessError, IndexError):
        pass
    return Hardware(torch.cuda.get_device_name(index), torch.cuda.get_device_capability(index),
                    total, free, ram['total_bytes'], min(ram['available_bytes'], available) if available is not None else ram['available_bytes'],
                    platform.system(), str(torch.__version__), str(torch.version.cuda or ''), cap, uuid, driver,
                    str(torch.version.hip or ''), str(getattr(torch.cuda.get_device_properties(index), 'gcnArchName', '')))


def installed_backends():
    import importlib.metadata
    try:
        if 'rocm' in importlib.metadata.version('torch').lower():
            return {'torch-flash'}
    except importlib.metadata.PackageNotFoundError:
        pass
    result = {'cudnn', 'torch-flash'}
    for name, module, distribution in [('sage2', 'sageattention', 'sageattention'), ('fa2', 'flash_attn_2_cuda', 'flash-attn')]:
        try:
            if importlib.metadata.version(distribution).startswith('2.') and importlib.util.find_spec(module) is not None:
                result.add(name)
        except (ImportError, ModuleNotFoundError, ValueError):
            pass
    spec = importlib.util.find_spec('flash_attn')
    if spec and any((Path(p) / 'cute' / 'interface.py').is_file() for p in spec.submodule_search_locations or []):
        result.add('fa4')
    try:
        distribution = importlib.metadata.distribution('flash-attn-4')
        if Path(distribution.locate_file('flash_attn/cute/interface.py')).is_file():
            result.add('fa4')
    except importlib.metadata.PackageNotFoundError:
        pass
    return result
