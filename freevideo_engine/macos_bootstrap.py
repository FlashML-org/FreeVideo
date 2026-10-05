"""Apple Silicon bootstrap data and tool acquisition, separate from CUDA pins.

The caller supplies the shared installer's fetch/progress/cancellation service.
This prepares one tool; it does not declare a model installation ready.
"""
from pathlib import Path
import json
import math
import os
import platform
import shutil
import subprocess
import sys

from . import network
from .uv_bootstrap import extract, verified, wheel_url

PYTHON_VERSION = '3.12.3'
UV = dict(version='0.9.0', filename='uv-0.9.0-py3-none-macosx_11_0_arm64.whl',
    sha256='b900e84d992a657e16371426dbb030ab031c0322a604b632dada34401ebe7145',
    bytes=18207114, executable='uv-0.9.0.data/scripts/uv', executable_bytes=41368656,
    sources=dict(official='https://files.pythonhosted.org/packages/8e/06/f5e38314e318bfaa20ccce966f6d0a69b093854648d31085b2d8b2097aab/uv-0.9.0-py3-none-macosx_11_0_arm64.whl'))


def require_native():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('The Mac runtime requires native Apple Silicon; use the platform installer')


def constraints():
    return Path(__file__).resolve().parents[1] / 'constraints' / 'macos-runtime.txt'


def versions(value):
    # Keep the pinned GitHub route probe independent of native uv delivery.
    # The native executable is always obtained from the verified PyPI wheel.
    return dict(value, target_system='Darwin', python=PYTHON_VERSION,
        uv=dict(UV, url=UV['sources']['official']), github_probe_url=value['uv']['url'])


def available_git():
    executable = shutil.which('git')
    if not executable:
        return None
    try:
        # /usr/bin/git can be Apple's tool launcher before the developer tools
        # are installed. Check selection first so inventory cannot open its
        # installation dialog. Independently installed Git needs no Apple tools.
        if Path(executable).resolve() == Path('/usr/bin/git'):
            selected = subprocess.run(['/usr/bin/xcode-select', '--print-path'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
            if selected.returncode or not selected.stdout.strip():
                return None
        version = subprocess.run([executable, '--version'], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=5)
        if version.returncode == 0 and version.stdout.startswith('git version '):
            return executable
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def inventory():
    require_native()
    from .macos_memory import chip_name, memory_status
    memory = memory_status()
    hardware = dict(gpu_name=chip_name(), system='Darwin', device_backend='mps',
        memory_model='unified', ram_total=memory['total_bytes'], ram_available=memory['available_bytes'])
    return dict(hardware=hardware, selected_device=dict(backend='mps', name=hardware['gpu_name'],
        unified_ram_bytes=hardware['ram_total']), machine=platform.machine(),
        platform=platform.platform(), kernel=platform.release(), cpu_threads=os.cpu_count(),
        system_memory=memory, git=available_git())


def preflight(args, snapshot=None, *, ram_gib=None, vram_gib=None):
    snapshot = inventory() if snapshot is None else snapshot
    hardware = snapshot['hardware']
    errors = []
    if hardware.get('system') != 'Darwin' or snapshot.get('machine') != 'arm64':
        errors.append('Mac setup requires native Apple Silicon (arm64).')
    if getattr(args, 'environment', None) not in (None, 'unified'):
        errors.append('Mac uses one native environment; select unified.')
    if getattr(args, 'model_source', 'prepared') != 'prepared' or getattr(args, 'rebuild_cache', False):
        errors.append('Mac setup uses the verified prepared model. Local source conversion is not supported.')
    if getattr(args, 'rebuild_sage', False) or getattr(args, 'gpu', None) is not None:
        errors.append('CUDA device and SageAttention build options do not apply to Mac.')
    if vram_gib is not None:
        errors.append('Mac has one unified memory pool; use --ram-gib instead of a separate VRAM limit.')
    if not snapshot.get('git'):
        errors.append('Git is unavailable. Install or repair Git (Apple Command Line Tools), then retry.')
    total, available = hardware['ram_total'], hardware['ram_available']
    if any(type(n) is not int or n < 0 for n in (total, available)) or not 0 < total or available > total:
        raise ValueError('Invalid unified-memory observation')
    if ram_gib is not None and (not math.isfinite(ram_gib) or ram_gib <= 0):
        raise ValueError('RAM limit must be positive and finite')
    reserve = 2 * 2**30
    capacity = min(total, int(ram_gib * 2**30)) if ram_gib is not None else total
    resources = dict(memory_model='unified', ram_system_reserve_bytes=reserve,
        ram_budget_bytes=max(0, min(capacity, available) - reserve))
    # This is installation supervision, not a video-capacity admission decision.
    if resources['ram_budget_bytes'] < 2**30:
        errors.append('Setup needs at least 3 GiB available unified memory, including its 2 GiB system reserve. Close memory-heavy applications and retry.')
    return snapshot, resources, errors


def dependency_status(root):
    from . import __version__
    required = set()
    def requirements(path):
        for text in path.read_text(encoding='utf-8').splitlines():
            text = text.strip()
            if text.startswith('-r '):
                requirements(path.parent / text[3:])
            elif text and not text.startswith('#'):
                required.add(text.split('[', 1)[0].lower().replace('_', '-'))
    requirements(constraints().with_name('macos-runtime-requirements.txt'))
    required.update(('diffusers', 'freevideo-engine'))
    expected = dict(line.strip().split('==', 1) for line in constraints().read_text().splitlines() if '==' in line)
    expected['freevideo-engine'] = __version__
    python = Path(root) / 'envs/unified/bin/python'
    row = dict(python=str(python), exists=python.is_file(), installed={}, missing=sorted(required), mismatched={})
    if python.is_file():
        result = subprocess.run([str(python), '-I', '-B', '-c',
            'import importlib.metadata as m,json; print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))'],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            row['error'] = result.stderr[-1000:]
        else:
            installed = json.loads(result.stdout)
            row.update(installed={p: installed.get(p) for p in sorted(required)},
                missing=sorted(required - installed.keys()),
                mismatched={p: dict(installed=installed[p], required=expected[p]) for p in required
                    if p in installed and p in expected and installed[p] != expected[p]})
    return {'unified': row}


def environment(root, env):
    result = dict(env)
    for name in ('CUDA_VISIBLE_DEVICES', 'CUDA_CACHE_PATH', 'TRITON_CACHE_DIR',
                 'TORCHINDUCTOR_CACHE_DIR', 'FREEVIDEO_FA4_OVERLAY'):
        result.pop(name, None)
    result.update(FREEVIDEO_DEVICE_BACKEND='mps', PYTORCH_ENABLE_MPS_FALLBACK='0',
                  PYTORCH_MPS_FAST_MATH='0', UV_NO_CONFIG='1')
    return result


def describe_plan(value):
    value.update(device_backend='mps', model_scale_granularity='int8_convrot',
        kernel_install='Native Apple MPS kernels; actual execution checked before readiness',
        model_source_reason='Verified compact ConvRot int8 storage: int8 products on Apple M5 and newer, BF16 execution on earlier Macs',
        first_generation_note='Unified memory is shared by macOS and the GPU. Generation uses current available memory and preserves the requested geometry and steps.',
        steps=['Prepare isolated native Python and one shared environment',
               'Install pinned model code and native H3 encoder library',
               'Download and verify models, reusing matching local files',
               'Verify prepared model storage',
               'Execute native GPU checks and save installation configuration'])
    value['licenses'] = value['licenses'][:2]


def prepare_uv(root, networking, env, fetch):
    require_native()
    root = Path(root)
    archive = root / 'downloads' / UV['filename']
    destination = root / 'tools' / 'uv'
    for cached in (archive, archive.with_name(archive.name + '.partial')):
        if verified(cached, UV['sha256'], UV['bytes']):
            archive = cached
            break
    else:
        sources = []
        for name in network.ordered(networking, 'pypi'):
            for route in network.route_order(networking, 'pypi', name, env):
                try:
                    address = wheel_url(UV, name, networking.get('timeout_seconds', 5),
                        network.route_environment(networking, 'pypi', name, env, route))
                    sources.append((name, address))
                    break
                except (OSError, ValueError, RuntimeError):
                    network.event(networking, category='pypi', source=name, route=route,
                        file=UV['filename'], action='fallback', reason='wheel-index-unavailable')
        if not sources:
            raise network.DownloadError('No Mac uv source is available; cached files retained')
        if archive.exists():
            network.retain_partial(archive, 'hash-rejected')
        fetch(sources[0][1], archive, UV['sha256'], size=UV['bytes'],
            candidates=sources, category='pypi', low_speed_limit=32 * 1024, max_seconds=180, cycles=1)
    # Always revalidate before extraction, including the fetch callback's output.
    if not verified(archive, UV['sha256'], UV['bytes']):
        raise ValueError('Mac uv archive failed integrity verification')
    extract(archive, destination, UV['executable'], UV['executable_bytes'])
    destination.chmod(0o755)
    return destination
