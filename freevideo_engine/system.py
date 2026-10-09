"""Small OS adapters usable by preflight before any third-party package exists."""
import os
from pathlib import Path
import platform
import shutil
import sys


def windows():
    return os.name == 'nt'


def curl_executable(env=None):
    """Find the same executable for setup checks and download subprocesses."""
    env = os.environ if env is None else env
    if windows():
        from .curl_windows import inspect
        return inspect(env)['selected']
    found = shutil.which('curl', path=env.get('PATH', os.defpath))
    return str(Path(found).absolute()) if found else None


def missing_curl_message(env=None):
    if windows():
        from .curl_windows import inspect, failure_message
        return failure_message(inspect(env))
    if platform.system() == 'Darwin':
        return 'macOS curl was not found. Check /usr/bin/curl and reopen FreeVideo with curl available in PATH.'
    return 'Missing curl for bounded downloads and HTTP/SOCKS proxy support. Run ./setup.sh to install it.'


def linux_tools_command(names, which=shutil.which, root=None):
    """The package command for missing build tools, as ./setup.sh would run it.

    The desktop launcher cannot ask for a password in a terminal, so its plan
    names the command; ./setup.sh installs the same packages itself.
    """
    manager = next((m for m in ('apt-get', 'dnf', 'yum', 'zypper') if which(m)), None)
    if manager is None:
        return None
    packages = []
    for name in names:
        package = {'g++': 'build-essential' if manager == 'apt-get' else 'gcc-c++',
                   'make': 'build-essential' if manager == 'apt-get' else 'make'}.get(name, name)
        if package not in packages:
            packages.append(package)
    install = {'apt-get': 'apt-get install -y', 'dnf': 'dnf install -y', 'yum': 'yum install -y',
               'zypper': 'zypper --non-interactive install'}[manager]
    privilege = '' if (root if root is not None else os.geteuid() == 0) else 'sudo '
    update = privilege + 'apt-get update && ' if manager == 'apt-get' else ''
    return update + privilege + install + ' ' + ' '.join(packages)


def source_root():
    """Find the checkout/ZIP by package location, never by the caller's cwd.

    A frozen desktop bundle and its versioned source cache are not user clones.
    Keep those on the desktop's saved/user-selected installation path.
    """
    if getattr(sys, 'frozen', False):
        return None
    root = Path(__file__).resolve().parents[1]
    if ((root / 'pyproject.toml').is_file()
            and any((root / name).is_file() for name in ('setup.sh', 'freevideo.ps1'))
            and not (root / 'launcher-source.json').is_file()):
        return root
    return None


def install_root():
    if os.environ.get('FREEVIDEO_HOME'):
        return Path(os.environ['FREEVIDEO_HOME']).expanduser()
    source = source_root()
    if source is not None:
        return source
    if windows():
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'FreeVideo'
    return Path.home() / '.local' / 'share' / 'freevideo'


def bootstrap_root(root=None):
    override = os.environ.get('FREEVIDEO_BOOTSTRAP_ROOT')
    selected = Path(override) if override else Path(root if root is not None else install_root()) / '.freevideo' / 'bootstrap'
    # Installation commands may run in the source/build directory instead of
    # the caller's cwd. Anchor a relative selection before passing it to uv.
    # Python 3.9 on Windows can return a relative path from resolve() when
    # none of the selected directory exists yet. Anchor before resolving.
    return Path(os.path.abspath(selected.expanduser())).resolve()


def venv_python(directory, system=None):
    return Path(directory) / ('Scripts/python.exe' if (system or platform.system()) == 'Windows' else 'bin/python')


def system_memory():
    if windows():
        from .win32 import memory_status
        return memory_status()
    if sys.platform == 'darwin':
        from .macos_memory import memory_status
        return memory_status()
    values = {k: int(v.split()[0]) * 1024 for k, v in
              (line.split(':', 1) for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines())
              if v.strip().endswith('kB')}
    return {'total_bytes': values['MemTotal'], 'available_bytes': values['MemAvailable'],
            'physical_available_bytes': values['MemAvailable'], 'commit_available_bytes': None,
            'swap_total_bytes': values.get('SwapTotal'), 'swap_free_bytes': values.get('SwapFree')}


# Host availability kept free of pinned weights: the platform emergency floor
# plus room for the non-weight private memory a request still grows into.
# Measured on a 12/32 Ada laptop: non-pinned private working set peaked near
# 4.0 GiB whether 0.4 GiB or 12.1 GiB was pinned. Unpinned weights remain
# reclaimable, but a working set larger than RAM must reread them from disk.
# That cost needs the execution-path allowance below, rather than a universal
# assumption that keeping fewer pinned weights costs only H2D transfers.
HOST_WEIGHT_HEADROOM = 8 * 2**30


def residual_host_headroom(canvas=None):
    """Room for one packed residual, separate from the weight cache.

    The staging buffer is locked on CUDA when the platform allows, and this
    room is what keeps those pages out of the pinned weight budget.
    """
    rows = (sum(canvas.get(key, 0) for key in
                ('video_tokens', 'reference_video_tokens', 'reference_audio_tokens'))
            if canvas is not None else 72576)
    return int(2**30 * max(1., rows / 72576))


def weight_cache_headroom(*, system=None, streamed=False, cpu_outputs=True, canvas=None):
    """Separate host work from weight caching, without counting swap as RAM.

    Streaming with GPU attention outputs measured 2.56 GiB of non-weight
    working memory in a complete 768p/243-frame Linux request, and 2.63 GiB on
    Windows (RTX 4060 Ti 16 GiB: 10.69 GiB peak private working set holding
    8.06 GiB of pinned weights). 3.5 GiB leaves growth/staging room inside the
    already OS-reserved budget on both platforms; Windows previously kept 8 GiB
    here, sized for host attention outputs, and streamed weights it had room
    to retain. CPU readouts measured 4.56 GiB beyond retained weights on Linux.
    Keep 5 GiB at that geometry there; Windows page-locks the readouts on top
    of about 4.0 GiB of non-pinned private working set (12/32 Ada laptop) and
    keeps its 8 GiB. Larger requests additionally reserve 2.5 GiB per reference
    token volume for their growing host readouts.
    Unstreamed placements, platforms without a measurement and unknown
    geometry retain the original allowance. This is a starting estimate;
    complete local observations and runtime RAM guards still apply.
    """
    if system is None:
        system = 'Windows' if windows() else 'Linux'
    if system not in ('Linux', 'Windows') or not streamed:
        return HOST_WEIGHT_HEADROOM
    if not cpu_outputs:
        return int(3.5 * 2**30)
    if canvas is None:
        return HOST_WEIGHT_HEADROOM
    from .geometry import geometry
    checked = geometry(canvas['width'], canvas['height'], frames=canvas['frames'])
    if any(canvas.get(key) != checked[key] for key in ('width', 'height', 'frames', 'video_tokens')):
        raise ValueError('Host cache allowance requires consistent request geometry')
    growth = max(0., checked['video_tokens'] / (72 * 1008) - 1.)
    # Windows page-locks these readouts on top of the ~4.0 GiB measured there,
    # so it keeps its original 8 GiB at the measured geometry and grows with it.
    base = HOST_WEIGHT_HEADROOM / 2**30 if system == 'Windows' else 5.
    return int((base + 2.5 * growth) * 2**30)


def effective_available():
    """Availability including clean mapped pages this process could release.

    Linux MemAvailable already includes reclaimable page cache, so mapped
    weights cost nothing there. Windows GlobalMemoryStatusEx counts only free
    and standby pages, leaving resident clean mapped pages out: measured a
    21.85 GiB working set with 4.30 GiB private while availability read
    1.62 GiB. Credit only this process's own resident non-private pages, which
    are the mapped weight pages, and stay bounded by commit and total RAM.
    """
    memory = system_memory()
    if not windows():
        return memory['available_bytes']
    from .win32 import process_memory
    try:
        row = process_memory(os.getpid())
    except OSError:
        row = None
    reclaimable = max(0, row['rss_bytes'] - row['private_working_set_bytes']) if row else 0
    return min(memory['total_bytes'], memory['commit_available_bytes'],
               memory['physical_available_bytes'] + reclaimable)


def nvidia_smi():
    found = shutil.which('nvidia-smi')
    if found:
        return found
    if windows():
        for path in (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/nvidia-smi.exe',
                     Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NVIDIA Corporation/NVSMI/nvidia-smi.exe'):
            if path.is_file():
                return str(path)
    raise RuntimeError('NVIDIA driver/nvidia-smi is missing. Install NVIDIA driver 580+ and restart before setup.')


def cpu_info():
    return platform.processor() if windows() else Path('/proc/cpuinfo').read_text(encoding='utf-8').split('\n\n')[0]


def memory_peak(record):
    return record.get('process_tree_peak_guard_bytes', record.get('process_tree_peak_pss_bytes'))


def memory_complete(record):
    return record.get('process_tree_guard_complete', record.get('process_tree_pss_complete')) is True


def memory_sample(record):
    return record.get('guard_bytes', record.get('pss_bytes'))


def inference_memory_sample(record):
    """Inference can reread clean file pages; keep Windows' existing metric."""
    return record.get('inference_guard_bytes', memory_sample(record))


def inference_environment(environment, ram_budget_bytes):
    """Avoid a second compiler-process heap on small-RAM inference workers."""
    selected = dict(environment)
    if ram_budget_bytes < 8 * 2**30:
        selected.setdefault('TORCHINDUCTOR_COMPILE_THREADS', '1')
    return selected


def inference_headroom(record):
    """Available RAM including reclaimable clean model cache, never swap."""
    return record.get('inference_available_bytes',
                      record.get('effective_available_bytes', record['system_available_bytes']))


def inference_emergency_floor(budget_bytes, reserve_gib=None):
    """Separate planned OS growth headroom from the runtime exhaustion tripwire.

    Planning already subtracts its reserve before assigning the worker budget.
    Applying that same 1/2 GiB reserve as an immediate kill threshold rejects
    valid small-memory requests. Keep a bounded 5% tripwire, at most 256 MiB.
    An explicit zero reserve also requests a zero tripwire; actual exhaustion,
    commit pressure and the measured working-memory budget still apply.
    """
    import math
    if reserve_gib is not None and (not math.isfinite(reserve_gib) or reserve_gib < 0):
        raise ValueError('RAM reserve must be finite and nonnegative')
    floor = min(256 * 2**20, int(budget_bytes * .05))
    return min(floor, int(reserve_gib * 2**30)) if reserve_gib is not None else floor


def ram_budget_is_estimate(args):
    """Automatic placement is an estimate; explicit capacity tests stay strict."""
    return getattr(args, 'ram_gib', None) is None and not getattr(args, 'profile', None)


def inference_pressure(record, budget_bytes, minimum_available_bytes=None, *, budget_is_estimate=False):
    used = inference_memory_sample(record)
    if used is None:
        raise RuntimeError('Worker memory monitoring is incomplete; see the retained diagnostics.')
    floor = inference_emergency_floor(budget_bytes) if minimum_available_bytes is None else minimum_available_bytes
    if type(floor) is not int or floor < 0:
        raise ValueError('Emergency RAM floor must be a nonnegative byte count')
    available = inference_headroom(record)
    reasons = []
    if used > budget_bytes:
        reasons.append('working_memory_budget')
    if available <= 0 or available < floor:
        reasons.append('system_or_commit_pressure')
    return dict(reasons=reasons, enforced_reasons=[r for r in reasons
                    if r != 'working_memory_budget' or not budget_is_estimate],
                budget_is_estimate=bool(budget_is_estimate), working_bytes=used, budget_bytes=budget_bytes,
                available_bytes=available, emergency_floor_bytes=floor,
                physical_available_bytes=record.get('system_physical_available_bytes'),
                commit_available_bytes=record.get('system_commit_available_bytes'))


def enable_terminal(stream):
    if not windows():
        return True
    try:
        import ctypes
        import msvcrt
        from .win32 import kernel32
        mode = ctypes.c_uint32()
        handle = msvcrt.get_osfhandle(stream.fileno())
        return bool(kernel32().GetConsoleMode(handle, ctypes.byref(mode)) and
                    kernel32().SetConsoleMode(handle, mode.value | 4))
    except (OSError, ValueError, AttributeError):
        return False
