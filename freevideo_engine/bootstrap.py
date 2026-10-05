"""Linux/Windows bootstrap. Python preflight uses stdlib and curl, without installation writes."""
from __future__ import annotations
import argparse
import csv
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import threading
import urllib.request
import zipfile
from urllib.parse import unquote, urlsplit

from .hardware import Hardware, GiB, cgroup_capacity
from . import __version__
from .policy import resource_budget
from .monitoring import save
from .install_tuning import build_parallelism, required_models, cache_compatible, wheel_key
from .terminal_ui import TerminalUI, LogProgress
from .locking import runtime_lock, LOCK_ENV
from .environments import ENVIRONMENTS, environment_names, select_layout, role_pythons, constraints_file, bootstrap_versions
from . import network
from . import processes
from .system import install_root, venv_python, system_memory, nvidia_smi, memory_sample

PACKAGE = Path(__file__).resolve().parent
SOURCE = PACKAGE.parent
DEFAULT_ROOT = install_root()


def _permission_limited_memory_sample(row):
    """Whether only descendant process queries were denied by Windows.

    Windows can deny PROCESS_QUERY_LIMITED_INFORMATION for a child owned by a
    different security context even though the install process and the system
    memory counters remain readable.  In that case the process-tree number is
    incomplete, but the global emergency floor is still a valid safety guard.
    Require a real sampled process count so synthetic/no-sample rows and a
    failed process snapshot continue to fail closed.
    """
    if type(row.get('processes')) is not int or row['processes'] <= 0:
        return False
    pids = row.get('unreadable_memory_pids', row.get('unreadable_pss_pids', []))
    errors = row.get('memory_read_errors', [])
    if not pids or not errors:
        return False
    if not all(type(pid) is int and pid > 0 for pid in pids):
        return False
    return all(type(error) is dict and error.get('winerror') == 5 for error in errors)


def setup_memory_status(row, verbose=False):
    available = row.get('effective_available_bytes', row['system_available_bytes'])
    if memory_sample(row) is None:
        if row.get('monitor_status') == 'degraded':
            return 'Process RAM partially unavailable · %.1f GiB available · continuing with system floor' % (available/GiB)
        return 'Process RAM reading unavailable · %.1f GiB available · retrying' % (available/GiB)
    if row.get('private_commit_bytes') is not None:
        # Commit includes nonresident reservations; displaying it as RAM hid
        # the cause of Windows file-mapping failures while physical RAM was free.
        return ('RAM %.1f GiB · Commit %.1f GiB · Free RAM %.1f GiB · Free Commit %.1f GiB' %
                (row['rss_bytes']/GiB, row['private_commit_bytes']/GiB,
                 row['system_physical_available_bytes']/GiB, row['system_commit_available_bytes']/GiB))
    result = ('RAM %s %.2f GiB · system available %.2f GiB' %
              (row.get('guard_metric', 'PSS'), (memory_sample(row) or 0)/GiB, available/GiB)
              if verbose else 'Setup processes %.1f GiB · %.1f GiB available' %
              ((memory_sample(row) or 0)/GiB, available/GiB))
    if (row.get('cgroup_memory') or {}).get('limit_bytes') is not None:
        result += ' within container limit'
    return result


def setup_memory_pressure(row, budget, system):
    """Keep Windows planning headroom separate from an exhaustion stop.

    The budget already excludes the OS reserve. Model SDKs additionally
    switch to bounded streaming below 2 GiB; stopping the whole installer at
    that same threshold prevented the low-memory path from continuing.
    Linux retains its existing container/cache floor.
    """
    reserve = (2 if system == 'Windows' else 1) * GiB
    floor = min(256 * 2**20, int(budget * .05)) if system == 'Windows' else reserve
    available = row.get('effective_available_bytes', row['system_available_bytes'])
    commit = row.get('system_commit_available_bytes')
    used = memory_sample(row)
    reasons = []
    if commit is not None and commit <= floor:
        reasons.append('commit_headroom')
    elif available <= floor:
        reasons.append('system_headroom')
    if used is not None and used > budget:
        reasons.append('process_budget')
    return dict(reasons=reasons, working_bytes=used, budget_bytes=budget,
                guard_metric=row.get('guard_metric', 'process working memory'),
                available_bytes=available, physical_available_bytes=row.get('system_physical_available_bytes'),
                commit_available_bytes=commit, emergency_floor_bytes=floor,
                planning_reserve_bytes=reserve)


def setup_memory_failure(pressure):
    reason = pressure['reasons'][0]
    if reason == 'commit_headroom':
        detail = 'Installation paused: Windows commit headroom is nearly exhausted (%.2f GiB available; %.2f GiB minimum).' % (
            pressure['commit_available_bytes'] / GiB, pressure['emergency_floor_bytes'] / GiB)
    elif reason == 'system_headroom':
        detail = 'Installation paused: system RAM is nearly exhausted (%.2f GiB available; %.2f GiB minimum).' % (
            pressure['available_bytes'] / GiB, pressure['emergency_floor_bytes'] / GiB)
    else:
        detail = 'Installation crossed its RAM budget (%.2f GiB used; %.2f GiB budget; %s).' % (
            pressure['working_bytes'] / GiB, pressure['budget_bytes'] / GiB, pressure['guard_metric'])
    return detail + ' Downloaded files are retained.'


def dependency_status(root, layout='unified', system=None):
    """Inspect distribution metadata only: no tensor imports or downloads."""
    result = {}
    system = system or platform.system()
    if system == 'Darwin':
        from .macos_bootstrap import dependency_status as native_status
        return native_status(root)
    packages = {
        'engine': ['torch', 'torchvision', 'triton', 'transformers', 'accelerate', 'peft', 'safetensors',
                   'huggingface-hub', 'omegaconf', 'pyyaml', 'einops', 'numpy', 'pillow', 'av',
                   'psutil', 'tqdm', 'importlib-metadata', 'requests', 'httpx', 'socksio', 'pysocks',
                   'modelscope-hub', 'hf-xet', 'sageattention', 'diffusers', 'freevideo-engine'],
        'encoder': ['torch', 'torchvision', 'torchaudio', 'freevideo-engine'] +
                   [s.strip() for s in (SOURCE / 'constraints/encoder-runtime.txt').read_text(encoding='utf-8').splitlines()
                    if s.strip() and not s.startswith('#')]}
    packages['unified'] = sorted(set(packages['engine'] + packages['encoder']))
    if system == 'Windows':
        for name in packages:
            packages[name] = ['triton-windows' if p == 'triton' else p for p in packages[name]]
    for name in environment_names(layout):
        names = packages[name]
        expected = dict(line.strip().lower().split('==', 1) for line in
                        constraints_file(name, system).read_text(encoding='utf-8').splitlines() if '==' in line)
        expected = {name.replace('_', '-'): version for name, version in expected.items()}
        expected.update({'freevideo-engine': __version__, 'sageattention': '2.2.0'})
        if system == 'Windows':
            expected['sageattention'] = json.loads((PACKAGE / 'bootstrap_versions.json').read_text(encoding='utf-8'))['windows']['sageattention']['version']
        python = venv_python(root / 'envs' / name, system)
        row = {'python': str(python), 'exists': python.exists(), 'installed': {}, 'missing': sorted(set(names)), 'mismatched': {}}
        if python.exists():
            code = ('import importlib.metadata as m,json; '
                    'print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))')
            check = subprocess.run([str(python), '-B', '-c', code], capture_output=True, text=True)
            if check.returncode:
                row['error'] = check.stderr[-1000:]
            else:
                installed = json.loads(check.stdout)
                row['installed'] = {p: installed.get(p) for p in sorted(set(names))}
                row['missing'] = [p for p in sorted(set(names)) if p not in installed]
                row['mismatched'] = {p: {'installed': installed[p], 'required': expected[p]}
                    for p in names if p in installed and p in expected and installed[p] != expected[p]}
        result[name] = row
    return result


def digest(path, algorithm='sha256', git_blob=False):
    h = hashlib.new(algorithm)
    if git_blob:
        h.update(('blob %d\0' % path.stat().st_size).encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def inventory(gpu=None):
    smi = nvidia_smi()
    query = 'index,uuid,name,compute_cap,memory.total,memory.free,driver_version,pci.bus_id'
    result = subprocess.run([smi, '--query-gpu=' + query, '--format=csv,noheader,nounits'],
                            check=True, capture_output=True, text=True, timeout=20)
    rows = [dict(zip(query.split(','), map(str.strip, row))) for row in csv.reader(result.stdout.splitlines())]
    selector = gpu if gpu is not None else os.environ.get('CUDA_VISIBLE_DEVICES', '0').split(',')[0]
    matches = [r for r in rows if r['index'] == selector or r['uuid'] == selector]
    if len(matches) != 1:
        raise ValueError('Select one physical GPU by nvidia-smi index or full UUID with --gpu. MIG is not supported.')
    selected = matches[0]
    try:
        capability = tuple(map(int, selected['compute_cap'].split('.')))
        if len(capability) != 2:
            capability = (0, 0)
    except (TypeError, ValueError):
        # Keep the raw driver result in inventory for the exported report.
        # Unknown must not be presented as an unsupported old GPU architecture.
        capability = (0, 0)
    ram = system_memory()
    limit, available = cgroup_capacity()
    hardware = Hardware(selected['name'], capability,
        int(float(selected['memory.total']) * 2**20), int(float(selected['memory.free']) * 2**20),
        ram['total_bytes'], min(ram['available_bytes'], available) if available is not None else ram['available_bytes'],
        platform.system(), cgroup_ram_limit=limit, gpu_uuid=selected['uuid'],
        driver_version=selected['driver_version'])
    return {'hardware': hardware.to_dict(), 'selected_gpu': selected, 'gpus': rows,
            'kernel': platform.release(), 'platform': platform.platform(), 'machine': platform.machine(),
            'cpu_threads': os.cpu_count(), 'swap_total_bytes': ram.get('swap_total_bytes'),
            'swap_free_bytes': ram.get('swap_free_bytes'), 'system_memory': ram,
            'compiler': shutil.which('cl' if platform.system() == 'Windows' else 'g++'), 'git': shutil.which('git')}


def existing_parent(path):
    while not path.exists():
        path = path.parent
    return path


def prepared_precision(prepared):
    """Display name of a prepared model's weight format (Macs use the ConvRot int8 export)."""
    return 'ConvRot int8' if (prepared or {}).get('scale_granularity') == 'int8_convrot' else 'FP8'


def model_target(row, model_dir, encoder_dir, prepared_dir=None):
    if row.get('sampling_file'):
        from .adaln_assets import asset_path
        from .sampling_assets import cache_root
        return asset_path(cache_root(model_dir), row['sampling_file'])
    if row.get('role') == 'latent_upscaler':
        return model_dir / 'latent_upscaler' / Path(row['file']).name
    if row.get('prepared'):
        if prepared_dir is None:
            raise ValueError('Prepared model download has no destination directory')
        from .adaln_assets import asset_path
        return asset_path(prepared_dir, row['file'])
    return (model_dir if row['repo'].startswith('OpenVDN/') else encoder_dir) / row['file']


def plan(args, *, local_progress=None):
    root = args.root.expanduser().resolve()
    saved = json.loads((root / 'machine.json').read_text(encoding='utf-8')) if (root / 'machine.json').is_file() else {}
    storage = getattr(args, 'storage', None) or saved.get('storage', 'compact')
    layout = select_layout(getattr(args, 'environment', None), saved)
    # v0.2.0 stored the encoder directory only in its retained setup plan.
    prior_path = Path(saved['setup_run']) / 'plan.json' if saved.get('setup_run') else None
    prior = json.loads(prior_path.read_text(encoding='utf-8')) if prior_path and prior_path.is_file() else {}
    # Hardware detection is the default for fresh installs, updates and retries.
    # Saved benchmark caps must never silently follow a user onto a larger GPU.
    keep_limits = getattr(args, 'keep_resource_limits', False)
    vram_gib = args.vram_gib if args.vram_gib is not None else saved.get('vram_gib') if keep_limits else None
    ram_gib = args.ram_gib if args.ram_gib is not None else saved.get('ram_gib') if keep_limits else None
    fixture = json.loads(args.hardware_json.read_text(encoding='utf-8')) if args.hardware_json else None
    mac_target = (fixture or {}).get('hardware', {}).get('system', platform.system()) == 'Darwin'
    if mac_target:
        from .macos_bootstrap import preflight
        snapshot, resources, errors = preflight(args, fixture, ram_gib=ram_gib, vram_gib=vram_gib)
        system, windows_target, layout = 'Darwin', False, 'unified'
        cache_format = dict(capability=None, scale_granularity='int8_convrot')
    else:
        if not args.hardware_json and (platform.system() not in ('Linux', 'Windows') or platform.machine().lower() not in ('x86_64', 'amd64')):
            raise RuntimeError('One-click setup supports Linux x86_64 and native Windows x64.')
        snapshot = json.loads(args.hardware_json.read_text(encoding='utf-8')) if args.hardware_json else inventory(args.gpu)
        hardware = Hardware.from_dict(snapshot['hardware'])
        errors = []
        if snapshot.get('machine', '').lower() not in ('x86_64', 'amd64') or hardware.system not in ('Linux', 'Windows'):
            errors.append('This installer supports Linux x86_64 and native Windows x64.')
        windows_target = hardware.system == 'Windows'
        if windows_target and layout != 'unified':
            errors.append('Native Windows uses the unified environment. Rerun with --environment unified.')
            layout = 'unified'
        if windows_target and getattr(args, 'rebuild_sage', False):
            errors.append('Windows uses the pinned, verified Sage2 wheel; --rebuild-sage is a Linux source-build option.')
        if int(snapshot['selected_gpu']['driver_version'].split('.')[0]) < 580:
            errors.append('NVIDIA driver 580 or newer is required by the pinned CUDA 13 encoder; update the driver first.')
        compatibility = hardware.cuda_compatibility()
        if compatibility['error']:
            errors.append(compatibility['error'])
        snapshot['cuda_compatibility'] = compatibility
        for name in () if windows_target else ('git', 'compiler'):
            if not snapshot.get(name):
                errors.append('Missing %s. Run ./setup.sh interactively to install basic tools, then review the engine plan.' % name)
        if not args.hardware_json and not shutil.which('curl'):
            errors.append('Missing curl for bounded downloads and HTTP/SOCKS proxy support. Run ./setup.sh to install it.')
        resources = None
        try:
            resources = resource_budget(hardware, vram_gib=vram_gib, ram_gib=ram_gib)
            resources['ram_budget_bytes'] = max(0, resources['ram_budget_bytes'])
        except ValueError as error:
            errors.append(str(error))
        system = hardware.system
        cache_format = dict(capability=hardware.capability)
    model_dir = Path(args.models or saved.get('model_root') or root / 'models' / 'vdn').expanduser().resolve()
    encoder_dir = Path(args.encoder_models or saved.get('encoder_model_root') or prior.get('encoder_dir') or root / 'models' / 'encoder').expanduser().resolve()
    reuse_cache = args.cache.expanduser().resolve() if args.cache else None
    if reuse_cache is None and not getattr(args, 'rebuild_cache', False):
        revision = json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8'))['models']['vdn_revision']
        for name in ('machine.json', 'prepared-cache.json'):
            cache_record = root / name
            if cache_record.is_file():
                record = json.loads(cache_record.read_text(encoding='utf-8'))
                candidate = record.get('cache')
                if record.get('model_revision') == revision and candidate and cache_compatible(candidate, **cache_format):
                    reuse_cache = Path(candidate)
                    break
    if reuse_cache is None and not getattr(args, 'rebuild_cache', False):
        from .install_tuning import discover_prepared
        reuse_cache = discover_prepared(getattr(args, 'reuse_models', None), **cache_format)
    from . import prepared_model
    prepared = None
    model_source = getattr(args, 'model_source', 'prepared')
    if reuse_cache is None and not getattr(args, 'rebuild_cache', False) and model_source == 'prepared':
        prepared = prepared_model.select(root=root, **cache_format)
    prepared_dir = prepared['directory'] if prepared else None
    if mac_target and reuse_cache is None and prepared is None:
        errors.append('No verified native-compatible prepared model is available. Source conversion is not supported on Mac.')
    files = required_models(json.loads((PACKAGE / 'model_files.json').read_text(encoding='utf-8')), reuse_cache or prepared)
    files += prepared_model.files(prepared)
    sampling_caches = bool(getattr(args, 'sampling_caches', False))
    if sampling_caches:
        from .sampling_assets import files as sampling_files
        files += sampling_files()
    local_reuse = None
    local_folder = getattr(args, 'reuse_models', None)
    local_manifest = getattr(args, 'reuse_models_manifest', None)
    if local_folder or local_manifest:
        from .local_models import scan, scan_many, can_link, key as local_key
        previous_reuse = None
        approved = getattr(args, 'approved_plan', None)
        if approved:
            previous_reuse = json.loads(Path(approved).read_text(encoding='utf-8')).get('local_models')
        needed = [row for row in files if not model_target(row, model_dir, encoder_dir, prepared_dir).exists()]
        if local_manifest:
            if Path(local_manifest).stat().st_size > 512 * 1024:
                raise ValueError('Model library manifest is too large')
            libraries = json.loads(Path(local_manifest).read_text(encoding='utf-8'))
            if not isinstance(libraries, dict) or libraries.get('version') != 1 or not isinstance(libraries.get('roots'), list):
                raise ValueError('Invalid model library manifest')
            directories = ([str(local_folder)] if local_folder else []) + libraries['roots']
            local_reuse = scan_many(directories, needed, prior=previous_reuse, callback=local_progress)
        else:
            local_reuse = scan(local_folder, needed, prior=previous_reuse, callback=local_progress)
        local_reuse['copy_mode'] = bool(getattr(args, 'copy_existing_models', False))
        local_reuse['copy_bytes'] = local_reuse['linked_bytes'] = 0
        for row in needed:
            record = local_reuse['matches'].get(local_key(row))
            if record:
                parent = existing_parent(model_target(row, model_dir, encoder_dir, prepared_dir).parent)
                record['method'] = 'hardlink' if not local_reuse['copy_mode'] and can_link(record['source'], parent) else 'copy'
                local_reuse['linked_bytes' if record['method'] == 'hardlink' else 'copy_bytes'] += row['bytes']
    groups = {}
    present = 0
    model_entries = []
    for row in files:
        path = model_target(row, model_dir, encoder_dir, prepared_dir)
        size_matches = path.is_file() and path.stat().st_size == row['bytes']
        present += row['bytes'] if size_matches else 0
        device = existing_parent(path.parent)
        entry = groups.setdefault(str(device), {'path': str(device), 'needed_bytes': 0,
                                                'free_bytes': shutil.disk_usage(device).free})
        local = (local_reuse or {}).get('matches', {}).get(row['repo'] + '/' + row['file'])
        model_entries.append((row, 'found' if size_matches else 'verified' if local else 'download'))
        entry['needed_bytes'] += 0 if size_matches or local and local['method'] == 'hardlink' else row['bytes']
        partial = path.with_suffix(path.suffix + '.partial')
        if not size_matches and not local and partial.is_file() and partial.stat().st_size <= row['bytes']:
            # Only the streaming strategy can reuse this contiguous prefix.
            # Its completed content must still pass the pinned hash check.
            entry['resume_bytes'] = entry.get('resume_bytes', 0) + partial.stat().st_size
    # Stream directly into final FP8 groups. The pinned model occupies ~45.3
    # GiB; allow 52 GiB including the largest group in progress. Never credit
    # future source deletion toward the space needed to complete conversion.
    dependencies = dependency_status(root, layout, system)
    environments_ready = all(r['exists'] and not r['missing'] and not r['mismatched'] for r in dependencies.values())
    extra = (0 if reuse_cache or prepared else 52) + (5 if environments_ready else 30 if layout == 'unified' else 45) + 10
    frontend = None
    if getattr(args, 'frontend_root', None):
        frontend = dict(root=str(args.frontend_root.expanduser().resolve()),
                        separate=args.frontend_separate, download=args.frontend_download)
    from .install_disk import budget as disk_budget
    reviewed = (json.loads(Path(args.approved_plan).read_text(encoding='utf-8'))
                if getattr(args, 'approved_plan', None) else saved)
    disk_plan = disk_budget(groups, root, extra,
        eligible=bool(windows_target and layout == 'unified' and (reuse_cache or prepared)
                      and getattr(args, 'model_downloader', 'auto') != 'xet'),
        environments_ready=environments_ready, frontend=frontend,
        keep_extreme=reviewed.get('disk_mode') == 'extreme')
    extra = disk_plan['environment_cache_safety_gib']
    errors.extend(disk_plan['errors'])
    if reuse_cache and not cache_compatible(reuse_cache, **cache_format):
        errors.append('--cache must contain a prepared FP8 cache compatible with this GPU scale format.')
    from .download_settings import read as download_preferences
    networking = network.plan(json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8')),
        bootstrap_versions(json.loads((PACKAGE / 'bootstrap_versions.json').read_text(encoding='utf-8')), system), layout,
        mode=getattr(args, 'network', 'auto'), timeout=getattr(args, 'network_timeout', 5),
        offline=bool(args.hardware_json or errors),
        proxy_mode=download_preferences(root / 'download-settings.json')['proxy_mode'])
    networking['download_settings_path'] = str(root / 'download-settings.json')
    prepared_missing = prepared and any(
        not model_target(row, model_dir, encoder_dir, prepared_dir).is_file()
        for row in files if row.get('prepared') and not
        (local_reuse or {}).get('matches', {}).get(row['repo'] + '/' + row['file']))
    if prepared_missing and not args.hardware_json and not errors:
        error = prepared_model.access_error(prepared, networking)
        if error:
            errors.append(error)
    from .model_transfer import policy as transfer_policy
    build = build_parallelism(resources['ram_budget_bytes'], snapshot.get('cpu_threads') or 1) if resources and not windows_target and not mac_target else None
    transfers = transfer_policy(resources['ram_budget_bytes'], build['estimated_peak_bytes'] if build else 0) if resources else None
    if disk_plan['mode'] == 'extreme':
        transfers = dict(transfers or {}, file_workers=1, overlap_build=False, mode='streaming-space-saver')
    from .model_status import inventory as model_inventory
    value = {'schema_version': 1, 'engine_version': __version__, 'root': str(root), 'inventory': snapshot, 'policy_estimate': None,
            'installation_resources': resources,
            'storage': storage,
            'disk_mode': disk_plan['mode'], 'disk_policy': disk_plan, 'frontend': frontend,
            'storage_preparation': ('Download verified slim %s weights and fixed AdaLN tables; no original transformer or local conversion'
                                    % prepared_precision(prepared)
                                    if prepared else 'Stream CPU merge directly to FP8 groups; no complete BF16 intermediate cache'),
            'storage_cleanup': ('After cache verification and GPU probes, remove only unchanged conversion-only weights downloaded/copied into this installation; borrowed originals and outputs are retained'
                                if storage == 'compact' else 'Retain original conversion weights for future re-quantization'),
            'network': networking,
            'model_transfer': transfers,
            'model_downloader': getattr(args, 'model_downloader', 'auto'),
            'allow_model_restart': getattr(args, 'allow_model_restart', False),
            'local_models': local_reuse,
            'model_groups': model_inventory(model_entries),
            'environment_layout': layout,
            'environment_count': len(environment_names(layout)),
            'vram_gib': vram_gib, 'ram_gib': ram_gib,
            'resource_mode': 'auto' if vram_gib is None and ram_gib is None else 'capacity-limits',
            'dependencies': dependencies,
            'model_dir': str(model_dir), 'encoder_dir': str(encoder_dir),
            'reuse_cache': str(reuse_cache) if reuse_cache else None,
            'prepared_model': prepared, 'sampling_caches': sampling_caches,
            'model_source': 'prepared' if prepared else 'existing' if reuse_cache else 'source',
            'model_source_reason': ('Pinned slim model, matching the existing GPU precision policy' if prepared else
                                    'Reuse existing compatible cache' if reuse_cache else
                                    'Original model explicitly selected' if model_source == 'source' or getattr(args, 'rebuild_cache', False) else
                                    'No verified prebuilt artifact for this GPU scale format; preserve its original precision policy'),
            'verification': getattr(args, 'verify', 'auto'),
            'build': build,
            'kernel_install': 'Pinned Windows Triton / Sage2 wheels; actual kernels checked before readiness' if windows_target else 'Local Sage2 source build / ABI-keyed wheel cache',
            'git_install': 'Reuse detected Git' if snapshot.get('git') else 'Install verified portable MinGit locally after confirmation' if windows_target else 'Git required',
            'wheel_cache': str(Path(getattr(args, 'wheel_cache', None) or saved.get('wheel_cache') or root / 'wheels').expanduser().resolve()),
            'rebuild_sage': getattr(args, 'rebuild_sage', False),
            'model_download_bytes': sum(r['bytes'] for r in files) - present - (local_reuse or {}).get('reused_bytes', 0),
            'existing_model_bytes_size_matched': present, 'disks': disk_plan['disks'],
            'additional_environment_cache_safety_gib': extra,
            'preparation_ram_estimate_gib': ('Bounded hash/header verification; no transformer loaded or converted' if prepared else
                                           '4–8 GiB working set plus reclaimable source file cache; measured by setup'),
            'test_artifacts_estimate_gib': '5–8 GiB for the standard five-case suite; all outputs retained',
            'first_generation_note': 'Portable AdaLN tables follow model weights and the exact schedule, across GPUs and Torch/CUDA versions. New schedules or modulation-changing LoRAs require preparation. Existing environments stay on disk when switching.',
            'estimates_are_not_capacity_guarantees': True, 'errors': errors,
            'steps': ['Install isolated uv/Python and ' + ('one shared CUDA environment' if layout == 'unified' else 'two pinned CUDA environments'),
                      'Clone pinned VDN, patched Diffusers and the native H3 text-encoder library',
                      'Install verified Windows Triton / Sage2 wheels' if windows_target else 'Install a local CUDA compiler and build Sage2 for the detected GPU only',
                      'Download and verify pinned base, eight-step checkpoint and encoder weights',
                      'Prepare or verify the architecture-compatible FP8 cache',
                      'Execute small attention/linear kernel probes and save machine configuration'],
            'licenses': ['https://huggingface.co/OpenVDN/vdn-minimax-h3/blob/751739ee5b9e3ac802dca5d5111075fdaeb47885/LICENSE',
                         'https://huggingface.co/t8star/Vdn-Minimax-H3-Comfy',
                         'https://docs.nvidia.com/cuda/eula/index.html']}
    if mac_target:
        from .macos_bootstrap import describe_plan
        describe_plan(value)
    else:
        value['cuda_compatibility'] = compatibility
    return value



def display(value, ui=None, *, verbose=False):
    ui = ui or TerminalUI('Setup')
    if verbose:
        return display_details(value, ui)
    h = value['inventory']['hardware']
    usable_ram = min(h['ram_total'], h.get('cgroup_ram_limit') or h['ram_total'])
    rows = ([('GPU', h['gpu_name'] + ' · MPS'),
             ('Unified memory', '%.1f GiB / %.1f GiB available' % (h['ram_total']/GiB, h['ram_available']/GiB))]
            if value.get('device_backend') == 'mps' else
            [('GPU', '%s · %.1f GiB / %.1f GiB free' % (h['gpu_name'], h['vram_total']/GiB, h['vram_free']/GiB)),
             ('RAM', '%.1f GiB / %.1f GiB available%s' % (usable_ram/GiB, min(usable_ram, h['ram_available'])/GiB,
                                                       ' · container limit' if usable_ram < h['ram_total'] else ''))])
    rows += [('Install in', value['root']),
            ('Environment', 'One shared Python environment · missing dependencies installed automatically'
             if value['environment_layout'] == 'unified' else 'Two separate Python environments · keeping your selected layout')]
    for key, default, label in (('model_dir', 'vdn', 'Model directory'), ('encoder_dir', 'encoder', 'Encoder directory')):
        if Path(value[key]) != Path(value['root']) / 'models' / default:
            rows.append((label, value[key]))
    rows.append(('Model download', '~%.1f GiB; Python / GPU packages are additional' % (value['model_download_bytes']/GiB)
                 if value['model_download_bytes'] else 'No new model files expected · verify existing files'))
    if value.get('disk_mode') == 'extreme':
        rows.append(('Disk mode', 'Automatic space saver · sequential downloads · temporary package cache removed before models'))
    if value.get('local_models'):
        local = value['local_models']
        rows.append(('Reuse models', '%.1f GiB verified · %.1f GiB copied locally · source files kept' %
                     (local['reused_bytes']/GiB, local['copy_bytes']/GiB)))
    rows.append(('Download recovery', 'May restart incomplete files if resume fails; earlier data retained' if value.get('allow_model_restart')
                 else 'Retry the same source; preserve progress; pause if a restart would be required'))
    for disk in value['disks']:
        label = 'Peak disk space' if len(value['disks']) == 1 else 'Disk ' + disk['paths'][0]
        rows.append((label, '~%.1f GiB additional needed · %.1f GiB free' % (disk['needed_bytes']/GiB, disk['free_bytes']/GiB)))
    prepared = value.get('prepared_model')
    rows.append(('Storage', 'Download slim %s model · no local conversion' % prepared_precision(prepared) if prepared else
                 'Reuse prepared FP8 cache' if value['reuse_cache'] else 'Prepare compact FP8 model'))
    if prepared:
        rows.append(('Prepared model', prepared['repo'] + ' · ' + prepared['scale_granularity'] +
                     (' · private, authorized HF token required' if prepared.get('private') else '')))
    elif value.get('model_source') == 'source':
        rows.append(('Model source', value['model_source_reason']))
    rows.append(('Original weights', 'Not downloaded; fixed AdaLN tables included' if prepared else
                 'Remove verified conversion inputs downloaded/copied here; keep borrowed originals'
                 if value.get('storage', 'compact') == 'compact' else 'Keep original weights for future conversion'))
    setup_ram = 'Bounded model verification; no local conversion' if prepared else 'About 4–8 GiB for model preparation'
    if value.get('build'):
        setup_ram += ' · up to ~%.1f GiB for compilation' % (value['build']['estimated_peak_bytes']/GiB)
    rows.append(('Setup RAM', setup_ram))
    if value.get('model_transfer'):
        rows.append(('Model downloads', 'Native parallel · %.1f GiB process budget + disk cache managed separately' % (value['model_transfer']['ram_guard_bytes']/GiB)))
        if value.get('model_downloader') == 'xet':
            rows.append(('Download mode', 'Require HF Xet for large weights · stop on failure · curl partials retained separately'))
    limits = [value.get(key) for key in ('vram_gib', 'ram_gib')]
    rows.append(('Optimization', 'Automatic · reserves GPU and system memory' if all(v is None for v in limits)
                 else 'Capacity limits · GPU %s / RAM %s' % tuple('auto' if v is None else '%g GiB' % v for v in limits)))
    networking = value.get('network', {})
    route = ('Models via HF Xet only · package mirrors remain automatic' if value.get('model_downloader') == 'xet' and networking.get('mode') != 'official' else
             'Official sources only' if networking.get('mode') == 'official' else
             'VDN via ModelScope · retry alternatives' if network.ordered(networking, 'vdn-models')[0] == 'modelscope' else
             'Fastest available mirrors · retry alternatives')
    if prepared and prepared.get('private'):
        route = 'Prepared model via authenticated Hugging Face · other models use ranked sources'
    elif prepared and value.get('model_downloader') != 'xet' and networking.get('mode') != 'official':
        family = network.model_family(dict(repo=prepared['repo'], revision=prepared['revision']))
        source = network.ordered(networking, family)[0]
        label = {'modelscope': 'ModelScope', 'official': 'Hugging Face', 'hf-mirror': 'HF Mirror',
                 'user': 'your Hub endpoint'}.get(source, source)
        route = 'Public Edge via ' + label + ' · verified mirrors · resume retained files'
    rows.append(('Network', route
                 + (' · compare proxy / direct' if networking.get('proxy_configured') or networking.get('git_proxy_configured') else '')))
    ui.panel('FreeVideo / ' + h['system'] + ' setup', rows)
    ui.write('Space and memory are estimates. Setup checks your GPU before marking it ready.\n')
    ui.write('Model licenses: MiniMax H3, H3 text encoder.\n' if value.get('device_backend') == 'mps' else
             'Model / toolkit licenses: MiniMax H3, H3 text encoder, NVIDIA CUDA.\n')
    ui.write('Use --verbose for full details and license links, or enter d at confirmation.\n')
    for error in value['errors']:
        ui.write('BLOCKED: ' + str(error) + '\n')


def display_details(value, ui=None):
    ui = ui or TerminalUI('Setup')
    h = value['inventory']['hardware']
    rows = ([('GPU', h['gpu_name'] + ' · MPS'),
             ('Unified memory', '%.2f GiB total / %.2f GiB available' % (h['ram_total']/GiB, h['ram_available']/GiB))]
            if value.get('device_backend') == 'mps' else
            [('GPU', '%s · SM %s' % (h['gpu_name'], '.'.join(map(str, h['capability'])))),
             ('VRAM', '%.2f GiB total / %.2f GiB free' % (h['vram_total']/GiB, h['vram_free']/GiB)),
             ('RAM', '%.2f GiB total / %.2f GiB available' % (h['ram_total']/GiB, h['ram_available']/GiB))])
    limits = [value.get(key) for key in ('vram_gib', 'ram_gib')]
    rows.append(('Resources', 'Automatic · current available VRAM/RAM' if all(v is None for v in limits)
                 else 'Capacity limits · VRAM %s / RAM %s' % tuple('auto' if v is None else '%g GiB' % v for v in limits)))
    resources = value.get('installation_resources') or value.get('policy_estimate')
    if resources:
        rows.append(('Installation RAM budget', '%.2f GiB · system growth reserve %.2f GiB' %
                     (resources['ram_budget_bytes']/GiB, resources['ram_system_reserve_bytes']/GiB)))
    rows.append(('Install root', value['root']))
    rows.append(('Environment', value['environment_layout'] + ' · text encoder exits before video models load'))
    rows.append(('Kernel installation', value.get('kernel_install', 'Local Sage2 build')))
    rows.append(('Git', value.get('git_install', 'Use detected Git')))
    networking = value.get('network', {})
    rows.append(('Connection', {'auto': 'Auto', 'proxy': 'Proxy only', 'direct': 'Direct only'}[
        networking.get('proxy_mode', 'auto')]))
    for name, entries in networking.get('sources', {}).items():
        selected = entries[0]
        detail = (' · %.2fs Git availability' % selected['seconds'] if name == 'git' else
                  ' · %.2f MiB/s sample' % (selected['bytes_per_second']/2**20) if selected.get('bytes_per_second')
                  else ' · connected') if selected.get('ok') else ' · unverified, downloads will retry alternatives'
        rows.append((name, selected['id'] + detail))
    for name, row in value['dependencies'].items():
        if not row['exists']:
            rows.append((name.capitalize(), 'Create isolated environment and install dependencies'))
        else:
            rows.append((name.capitalize(), 'Missing: %s · version mismatches: %s' %
                        (', '.join(row['missing']) or 'none', ', '.join(row['mismatched']) or 'none')))
    rows.append(('Model download', '%.2f GiB · existing files verified after confirmation' % (value['model_download_bytes']/GiB)))
    rows.append(('Storage', value.get('storage', 'compact') + ' · ' + value.get('storage_preparation', 'streamed FP8 preparation without a BF16 disk copy')))
    if value.get('prepared_model'):
        prepared = value['prepared_model']
        rows.append(('Prepared model', prepared['repo'] + '@' + prepared['revision'] + ' · ' + prepared['scale_granularity']))
        rows.append(('Access', 'Private repository; HF_TOKEN or hf auth login with an authorized account' if prepared.get('private') else 'Public repository'))
    rows.append(('Source weights', value.get('storage_cleanup', 'Retain source weights')))
    if value['reuse_cache']:
        rows.append(('Prepared cache', 'Reuse FP8 · skip original BF16/LoRA downloads and scans'))
    rows.append(('Verification', value['verification'] + ' · auto reuses unchanged pinned receipts; full rereads tensors'))
    if value['build']:
        rows.append(('Sage2 build', 'Up to %d compiler jobs · ~%.1f GiB RAM estimate · reusable ABI/GPU wheel cache' %
                    (value['build']['jobs'], value['build']['estimated_peak_bytes']/GiB)))
    for disk in value['disks']:
        rows.append(('Disk', '~%.1f GiB additional / %.1f GiB free · %s' %
                    (disk['needed_bytes']/GiB, disk['free_bytes']/GiB, disk['paths'][0])))
    rows += [('Preparation RAM', value['preparation_ram_estimate_gib']), ('Test outputs', value['test_artifacts_estimate_gib'])]
    rows.append(('First generation', value['first_generation_note']))
    ui.panel('FreeVideo / ' + h['system'] + ' setup plan', rows)
    print('Resource numbers are conservative estimates. The test suite measures actual usage.')
    for i, step in enumerate(value['steps'], 1):
        print('%d. %s' % (i, step))
    print('Licenses:\n' + '\n'.join(value['licenses']))
    for error in value['errors']:
        print('BLOCKED: ' + error)


def confirmed(args, value, ask=input, ui=None):
    reviewed_path = getattr(args, 'approved_plan', None)
    if reviewed_path:
        if not args.yes or not args.accept_model_license:
            raise ValueError('--approved-plan requires explicit plan/license acceptance.')
        reviewed = json.loads(Path(reviewed_path).read_text(encoding='utf-8'))
        keys = ('root', 'engine_version', 'environment_layout', 'model_dir', 'encoder_dir', 'vram_gib', 'ram_gib', 'reuse_cache', 'storage', 'storage_cleanup', 'model_downloader', 'prepared_model', 'model_source', 'disk_mode', 'frontend', 'sampling_caches')
        changed = any(reviewed.get(key) != value.get(key) for key in keys)
        changed |= reviewed.get('device_backend') != value.get('device_backend')
        if value.get('device_backend') == 'mps':
            changed |= reviewed.get('inventory', {}).get('selected_device') != value['inventory']['selected_device']
        else:
            changed |= reviewed.get('inventory', {}).get('selected_gpu', {}).get('uuid') != value['inventory']['selected_gpu']['uuid']
        changed |= value['model_download_bytes'] > reviewed.get('model_download_bytes', -1)
        changed |= bool(reviewed.get('allow_model_restart')) != bool(value.get('allow_model_restart'))
        for key in ('root', 'roots', 'copy_mode', 'copy_bytes'):
            changed |= (reviewed.get('local_models') or {}).get(key) != (value.get('local_models') or {}).get(key)
        changed |= sum(disk['needed_bytes'] for disk in value['disks']) > sum(disk['needed_bytes'] for disk in reviewed.get('disks', []))
        if changed or reviewed.get('errors'):
            raise ValueError('The installation plan changed. Detect configuration again and review the new plan before installing.')
    if args.yes:
        if not args.accept_model_license:
            raise ValueError('Unattended installation requires --yes --accept-model-license.')
        return True
    if not sys.stdin.isatty():
        raise ValueError('No interactive input. Review --plan, then use --yes --accept-model-license.')
    while True:
        try:
            reply = ask('Install and accept the model/toolkit licenses? [Y/n/d] (Enter = yes, d = details): ').strip().lower()
        except EOFError:
            return False
        if reply in ('', 'y', 'yes'):
            return True
        if reply in ('n', 'no'):
            return False
        if reply in ('d', 'details'):
            display(value, ui, verbose=True)
        else:
            print('Press Enter to install, n to cancel, or d to review details.')


def download(url, path, sha, progress=None, *, networking=None, env=None):
    networking = networking or {}
    family = 'github' if url.startswith(network.SOURCES['github']['official'] + '/') else 'cuda'
    return network.download(network.urls(networking, family, url, env), path, sha, progress, network=networking, env=env, category=family)


def unpack(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                name = member.filename.replace('\\', '/')
                path = (destination / name).resolve()
                if (not path.is_relative_to(destination.resolve()) or ':' in name or
                        (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError('Unsafe ZIP member: ' + member.filename)
            zipped.extractall(destination)
        return
    with tarfile.open(archive) as tar:
        # Python 3.9-compatible containment checks, including link targets.
        for member in tar.getmembers():
            path = (destination / member.name).resolve()
            if not path.is_relative_to(destination.resolve()) or member.isdev() or member.isfifo():
                raise ValueError('Unsafe archive member: ' + member.name)
            if member.issym() or member.islnk():
                target = (path.parent if member.issym() else destination) / member.linkname
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Unsafe archive link: ' + member.name)
        tar.extractall(destination)


def setup_task(label):
    """Describe the work independently of internal command IDs and log paths."""
    routes = {'official': 'official source', 'nju': 'Nanjing University mirror',
              'tuna': 'Tsinghua mirror', 'ghfast': 'GitHub mirror',
              'user-python': 'your Python mirror', 'user': 'your configured source'}
    source = next((name for name in routes if label.endswith('-' + name)), None)
    key = label[:-(len(source) + 1)] if source else label
    for prefix in ('unified-', 'engine-', 'encoder-'):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    tasks = {
        'release-package-cache': ('Release installation cache', 'Installed components and models are kept'),
        'space-saving-comfy': ('Prepare ComfyUI', 'Finish the environment before downloading models'),
        'python': ('Install Python', 'Download and prepare the private Python runtime'),
        'python-bootstrap-reuse': ('Use existing Python', 'Check the Python already prepared by the launcher'),
        'venv': ('Create Python environment', 'Prepare an isolated environment for FreeVideo'),
        'torch': ('Install PyTorch + CUDA', 'Download and install the GPU runtime packages'),
        'packages': ('Install engine dependencies', 'Download and install video and text encoding libraries'),
        'engine': ('Install encoder support', 'Connect the encoder to FreeVideo'),
        'vdn': ('Install model support', 'Install the verified model code'),
        'vdn-source': ('Download model code', 'Prepare model source files while runtime packages download'),
        'library-check': ('Check text encoder', 'Verify that the text encoding library loads'),
        'models': ('Download model weights', 'Download and verify the video model and text encoder'),
        'prepare': ('Optimize model storage', 'Prepare compact FP8 weights for your GPU'),
        'kernels': ('Test GPU acceleration', 'Run small checks on your GPU'),
        'storage': ('Finish model storage', 'Verify prepared weights and apply your storage choice'),
        'dependency-check': ('Check installed packages', 'Verify dependency compatibility'),
        'freeze': ('Save installed versions', 'Record versions for future diagnostics'),
        'sage2-toolchain-check': ('Check build tools', 'Verify the compiler before building GPU acceleration'),
        'sage2-build': ('Build SageAttention 2', 'Compile acceleration for your GPU · the first build takes longer'),
        'sage2-wheel-install': ('Install SageAttention 2', 'Install the prepared GPU acceleration package'),
        'sage2-windows-wheel-install': ('Install SageAttention 2', 'Install the verified Windows acceleration package'),
        'windows-runtime-check': ('Check GPU runtime', 'Verify CUDA and the Windows runtime libraries'),
    }
    title, detail = tasks.get(key, ('Prepare dependencies', 'Prepare verified source files'))
    for prefix, name in (('h3-text-encoder-', 'text encoder'), ('SageAttention-', 'SageAttention 2')):
        if key.startswith(prefix):
            title = 'Download ' + name if '-fetch' in key else 'Prepare ' + name
            detail = 'Download verified source code' if '-fetch' in key else 'Prepare source files for installation'
    if source:
        detail += ' · ' + routes[source]
    return title, detail


def reusable_kernel_receipt(path, hardware_data):
    """Return a matching successful GPU probe receipt, or ``None``.

    The receipt is deliberately tied to the same identity used by runtime
    backend selection.  A changed driver, Torch/CUDA build, installed backend,
    or engine source invalidates it and forces a fresh isolated probe.
    """
    from .hardware import Hardware, installed_backends
    from .kernel_capabilities import identity, readiness
    try:
        receipt = json.loads(Path(path).read_text(encoding='utf-8'))
        expected = identity(Hardware.from_dict(hardware_data))
        probes = receipt.get('kernel_probes')
        if (receipt.get('identity') != expected
                or sorted(receipt.get('installed_attention_packages', ())) != sorted(installed_backends())
                or not isinstance(probes, list) or not readiness(probes).get('ready')):
            return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return None


class Installer:
    def __init__(self, value, ui=None):
        self.locks = ExitStack()
        try:
            self.initialize(value, ui)
        except BaseException:
            self.locks.close()
            raise

    def initialize(self, value, ui):
        self.plan = value
        self.resources = value.get('installation_resources') or value.get('policy_estimate') or {}
        self.root = Path(value['root'])
        self.layout = value.get('environment_layout', 'unified')
        self.system = value['inventory'].get('hardware', {}).get('system', platform.system())
        self.pythons = role_pythons(self.root, self.layout, self.system)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = self.locks.enter_context(runtime_lock(self.root / 'setup.lock', inherit=False))
        lock_path = os.environ.get('FREEVIDEO_LOCK_PATH', str(self.root / 'engine.lock'))
        self.runtime_fd = self.locks.enter_context(runtime_lock(lock_path))
        self.run_dir = self.root / 'setup-runs' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
        self.run_dir.mkdir(parents=True, exist_ok=False)
        save(self.run_dir / 'plan.json', value)
        previous = self.root / 'machine.json'
        saved = {}
        if previous.is_file():
            shutil.copyfile(previous, self.run_dir / 'machine.before.json')
            saved = json.loads(previous.read_text(encoding='utf-8'))
        # Persist a confirmed choice for interrupted first installs and upgrades.
        # The former complete configuration is retained in machine.before.json.
        save(previous, dict(saved, root=str(self.root), ready=False, setup_run=str(self.run_dir),
            storage=value.get('storage', 'compact'),
            disk_mode=value.get('disk_mode', 'normal'),
            pending_environment_layout=self.layout, model_root=value['model_dir'],
            encoder_model_root=value.get('encoder_dir', saved.get('encoder_model_root')),
            wheel_cache=value.get('wheel_cache', saved.get('wheel_cache')),
            vram_gib=value.get('vram_gib', saved.get('vram_gib')),
            ram_gib=value.get('ram_gib', saved.get('ram_gib'))))
        self.env = dict(os.environ, FREEVIDEO_HOME=str(self.root),
            FREEVIDEO_VDN_ROOT=str(self.root / 'vendor' / 'vdn'),
            FREEVIDEO_MODEL_ROOT=value['model_dir'],
            FREEVIDEO_COMFY_ROOT=str(self.root / 'vendor' / 'h3-text-encoder'),
            FREEVIDEO_COMFY_PYTHON=str(self.pythons['encoder']),
            FREEVIDEO_LOCK_PATH=lock_path,
            PATH=str(self.pythons['engine'].parent) + os.pathsep + os.environ.get('PATH', ''),
            UV_CACHE_DIR=str(self.root / 'downloads' / 'uv-cache'),
            UV_PYTHON_INSTALL_DIR=str(self.root / 'python'), UV_LINK_MODE='hardlink',
            HF_HOME=str(self.root / 'downloads' / 'huggingface'), HF_HUB_DISABLE_TELEMETRY='1',
            HF_HUB_OFFLINE='0', TRANSFORMERS_OFFLINE='0',
            PIP_DISABLE_PIP_VERSION_CHECK='1',
            PYTHONUNBUFFERED='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONPATH=str(SOURCE), OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
        self.env = self.device_environment(self.env)
        self.env[LOCK_ENV] = str(self.runtime_fd)
        self.env.update(FREEVIDEO_NETWORK_PLAN=str(self.run_dir / 'plan.json'),
                        FREEVIDEO_NETWORK_EVENTS=str(self.run_dir / 'network.jsonl'))
        self.env = network.proxy_environment(self.env)
        if value.get('disk_mode') == 'extreme':
            from .install_disk import environment
            self.env = environment(self.root, self.env)
        self.network = dict(value.get('network', {}), events_path=str(self.run_dir / 'network.jsonl'), quiet=True)
        self.state = {'status': 'running', 'steps': [], 'plan': value}
        self.state_lock = threading.Lock()
        self.cancel = threading.Event()
        self.versions = bootstrap_versions(json.loads((PACKAGE / 'bootstrap_versions.json').read_text(encoding='utf-8')), self.system)
        self.spec = json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8'))
        self.ui = ui or TerminalUI('Setup', plain=True)
        self.monitor_stop = threading.Event()
        self.monitor_thread = None
        self.started = time.monotonic()

    def device_environment(self, env):
        from .triton_compat import environment
        return environment(self.root, dict(env,
            CUDA_VISIBLE_DEVICES=self.plan['inventory']['selected_gpu']['uuid'],
            TRITON_CACHE_DIR=str(self.root / 'kernel-cache' / 'triton'),
            CUDA_CACHE_PATH=str(self.root / 'kernel-cache' / 'cuda'),
            TORCHINDUCTOR_CACHE_DIR=str(self.root / 'kernel-cache' / 'inductor')))

    def start_monitor(self):
        from .ram import ProcessMemory
        memory = ProcessMemory()
        def sample():
            unreadable = failures = recoveries = 0
            try:
                with (self.run_dir / 'ram.jsonl').open('w', buffering=1, encoding='utf-8') as stream:
                    while not self.monitor_stop.is_set():
                        if self.plan.get('disk_mode') == 'extreme':
                            from .install_disk import check_floor
                            try:
                                check_floor([p for disk in self.plan['disks'] for p in disk['paths']])
                            except RuntimeError as error:
                                self.state['resource_guard'] = str(error)
                                self.cancel.set()
                                break
                        try:
                            row = memory.sample(os.getpid())
                        except OSError as error:
                            if self.system != 'Windows':
                                raise
                            # The process snapshot itself can fail, too. Still
                            # obtain fresh physical/commit headroom; never reuse
                            # the previous reading or invent process usage.
                            system = system_memory()
                            row = dict(guard_bytes=None,
                                system_available_bytes=system['available_bytes'],
                                system_physical_available_bytes=system['physical_available_bytes'],
                                system_commit_available_bytes=system['commit_available_bytes'],
                                memory_read_errors=[dict(error=str(error), winerror=getattr(error, 'winerror', None))])
                        used = memory_sample(row)
                        if used is None:
                            unreadable += 1
                            failures += 1
                            # A live worker can have descendants for which
                            # Windows returns ERROR_ACCESS_DENIED (5). Keep
                            # the sample and continue under the global RAM/
                            # commit floor; abort only when the whole process
                            # snapshot is unavailable or the root cannot be
                            # sampled. This avoids killing installs merely
                            # because a helper has a stricter token.
                            degraded = (self.system == 'Windows' and
                                        _permission_limited_memory_sample(row))
                            if degraded:
                                self.state['resource_warning'] = (
                                    'Some Windows child processes denied memory queries (WinError 5); '
                                    'continuing with the global RAM/commit floor.')
                            row = dict(row, monitor_status='degraded' if degraded else 'retrying',
                                       consecutive_unreadable=unreadable)
                        else:
                            if unreadable:
                                recoveries += 1
                            row = dict(row, monitor_status='recovered' if unreadable else 'complete',
                                       consecutive_unreadable=0)
                            unreadable = 0
                        pressure = setup_memory_pressure(row, self.resources['ram_budget_bytes'], self.system)
                        row['installation_guard'] = pressure
                        stream.write(json.dumps(dict(row, elapsed_seconds=time.monotonic()-self.started, epoch_seconds=time.time())) + '\n')
                        available = pressure['available_bytes']
                        self.ui.resource = setup_memory_status(row, self.ui.verbose)
                        # Windows helpers can exit/change while their tree is
                        # sampled. Allow two resamples, while checking the live
                        # exhaustion floor on every attempt. The larger planning
                        # reserve is not a reason to kill a bounded download.
                        if pressure['reasons']:
                            self.state['installation_guard'] = pressure
                            self.state['resource_guard'] = setup_memory_failure(pressure)
                            self.cancel.set()
                            break
                        if available < pressure['planning_reserve_bytes']:
                            self.state['memory_headroom_warning'] = pressure
                        if (used is None and (self.system != 'Windows' or unreadable >= 3)
                                and row.get('monitor_status') != 'degraded'):
                            details = json.dumps(dict(
                                unreadable_memory_pids=row.get('unreadable_memory_pids', row.get('unreadable_pss_pids', [])),
                                memory_read_errors=row.get('memory_read_errors', []),
                                available_bytes=available), ensure_ascii=False)
                            raise RuntimeError('Cannot read process-tree memory after %d consecutive samples. '
                                'Download progress is retained; retry installation to resume. '
                                'RAM sample: %s; full samples: %s' % (unreadable, details, self.run_dir / 'ram.jsonl'))
                        self.monitor_stop.wait(1)
            except BaseException as error:
                self.state['resource_guard'] = 'Installation RAM monitoring failed: ' + repr(error)
                self.cancel.set()
            finally:
                self.state['memory'] = memory.result()
                self.state['memory'].update(monitor_unreadable_samples=failures,
                    monitor_recoveries=recoveries, monitor_consecutive_unreadable=unreadable)
                if failures:
                    self.state['memory']['process_tree_guard_complete'] = False
        self.monitor_thread = threading.Thread(target=sample, name='setup-memory', daemon=True)
        self.monitor_thread.start()

    def fetch(self, url, path, sha, *, size=None, candidates=None, category=None,
              low_speed_limit=1024, max_seconds=None, cycles=2):
        key = 'download-' + path.name
        title = ('Prepare download tools' if path.name.startswith('uv') else
                 'Download GPU acceleration' if path.suffix == '.whl' else
                 'Prepare Git' if 'git' in path.name.lower() else 'Download CUDA build tools')
        self.ui.begin(key, title, detail=path.name if self.ui.verbose or candidates is not None else 'Download and verify required files')
        transfer = {}
        def source_detail():
            names = {'pypi': {'official': 'PyPI', 'tuna': 'Tsinghua mirror'},
                     'github': {'official': 'GitHub', 'ghfast': 'GitHub mirror'}}
            source = names.get(category, {}).get(transfer.get('source'), transfer.get('source', ''))
            return ' · Source: ' + source if source else ''
        def progress(done, total, speed):
            if self.cancel.is_set():
                raise RuntimeError('Installation cancelled; partial download retained.')
            rate_text = ('%.1f KiB/s' % (speed/1024) if speed < 2**20 else '%.1f MiB/s' % (speed/2**20))
            self.ui.update(key, done=done, total=total, rate=speed, unit='bytes', scope=path.name,
                detail=(path.name + ' · ' if candidates is not None else '') +
                    ('%.1f MiB / %.1f MiB' % (done/2**20, total/2**20) if total else '%.1f MiB downloaded' % (done/2**20)) +
                    ' · ' + rate_text + source_detail())
        try:
            def feedback(row):
                transfer.update(row)
                self.ui.update(key, detail=path.name + source_detail() + ' · ' + row['action'] +
                               (' · ' + row['reason'] if row.get('reason') else ''))
            networking = dict(self.network, event_callback=feedback)
            if candidates is None:
                download(url, path, sha, progress, networking=networking, env=self.env)
            else:
                network.download(candidates, path, sha, progress, network=networking, env=self.env,
                                 size=size, category=category or 'download',
                                 low_speed_limit=low_speed_limit, max_seconds=max_seconds, cycles=cycles)
        except BaseException:
            self.ui.end(key, success=False)
            raise
        self.ui.end(key, detail='Verified and ready')

    def command(self, label, args, env=None, cwd=None):
        from .ram import ProcessMemory
        from .package_progress import PackageOutput
        if self.cancel.is_set():
            raise RuntimeError(self.state.get('resource_guard', 'Installation cancelled.'))
        with self.state_lock:
            index = len(self.state['steps'])
            log = self.run_dir / ('%02d-%s.log' % (index, label))
            row = {'label': label, 'command': list(map(str, args)), 'log': str(log), 'status': 'running'}
            self.state['steps'].append(row)
            save(self.run_dir / 'status.json', self.state)
        key = str(index) + '-' + label
        title, detail = setup_task(label)
        if label == 'models' and self.plan.get('local_models'):
            title, detail = 'Reuse models / download missing files', 'Import verified local models; source files stay in place'
        self.ui.begin(key, title, detail=str(log) if self.ui.verbose else detail)
        progress = LogProgress(log)
        parallel_tasks = set()
        def update_progress(*, final=False):
            self.ui.update(key, **progress.read(final=final))
            if progress.model_groups is not None:
                self.ui.event('models', groups=progress.model_groups)
                progress.model_groups = None
            for name, details in progress.take_tasks().items():
                subkey = key + '-' + name
                label, state = details.pop('label'), details.pop('state')
                if subkey not in parallel_tasks:
                    self.ui.begin(subkey, label)
                    parallel_tasks.add(subkey)
                self.ui.update(subkey, **details)
                if state != 'running':
                    self.ui.end(subkey, success=state == 'complete', detail=details['detail'])
        tick = time.monotonic()
        memory = ProcessMemory()
        child = None
        try:
            with log.open('w', encoding='utf-8') as stream:
                with PackageOutput(row['command'], stream, env or self.env) as output:
                    child = processes.popen(row['command'], env=output.env, cwd=cwd, stdout=output.stdout, stderr=subprocess.STDOUT,
                                             start_new_session=True, pass_fds=(self.runtime_fd,), supervise=True)
                    output.spawned()
                    try:
                        while child.poll() is None:
                            if output.error is not None:
                                raise RuntimeError('Could not retain package installation output') from output.error
                            if self.cancel.is_set():
                                raise RuntimeError('Another parallel installation step failed; cancelling this step.')
                            memory.sample(child.pid)
                            try:
                                child.wait(timeout=.5)
                            except subprocess.TimeoutExpired:
                                pass
                            update_progress()
                    except BaseException:
                        if child.poll() is None:
                            processes.stop(child)
                        raise
        except BaseException as error:
            row['error'] = repr(error)
            if child is not None and child.poll() is None:
                processes.stop(child)
            raise
        finally:
            update_progress(final=True)
            success = child is not None and child.poll() == 0 and 'error' not in row
            for subkey in parallel_tasks:
                if self.ui.tasks[subkey]['state'] == 'running':
                    self.ui.end(subkey, success=success, detail='Ready' if success else 'Stopped; files retained')
            try:
                with self.state_lock:
                    row.update(seconds=time.monotonic()-tick, returncode=child.poll() if child else None, memory=memory.result(),
                               status='complete' if success else 'failed')
                    save(self.run_dir / 'status.json', self.state)
            finally:
                self.ui.end(key, success=success, detail=(getattr(progress, 'last_notice', None) or 'Log: ' + str(log))
                            if self.ui.verbose or not success else 'Ready')
        if child.returncode:
            from .failure_details import setup_command_failure
            raise RuntimeError(setup_command_failure(label, log, child.returncode))
        return log

    def component_tasks(self, uv):
        """Dependencies, not serial UI stages, determine when work can start."""
        tasks = {}
        def task(name, run, after=(), writes=()):
            tasks[name] = dict(run=run, after=tuple(after), writes=tuple(writes))
        task('python', lambda: self.python_install(uv))
        task('git', self.install_git_windows if self.system == 'Windows' else lambda: None)
        # This entry imports only stdlib engine code from the bootstrap Python.
        # It can fetch the exact source trees while large Torch wheels download.
        task('vdn-source', lambda: self.command('vdn-source', [sys.executable, '-c',
            'from freevideo_engine.install import install; install()']), after=('git',))
        task('encoder-source', lambda: self.clone('h3-text-encoder',
            self.spec['encoder']['comfy_url'], self.spec['encoder']['comfy_commit'],
            sparse=['/comfy/', '/utils/', '/folder_paths.py', '/node_helpers.py', '/LICENSE']), after=('git',))
        pythons = {}
        for name in environment_names(self.layout):
            spec = ENVIRONMENTS[name]
            venv = self.root / 'envs' / name
            python = venv_python(venv, self.system)
            pythons[name] = python
            writer = ('environment:' + name,)
            def environment(folder=venv, executable=python, label=name):
                if not executable.exists():
                    return self.packages(label + '-venv', [uv, 'venv', '--seed', '--python', self.versions['python'], folder])
            task(name + '-venv', environment, ('python',), writer)
            command = [uv, 'pip', 'install', '--python', python, *spec['torch'], '-c', constraints_file(name, self.system)]
            family = 'torch-' + spec['cuda'] + '-' + spec['torch'][0].split('==')[1]
            def torch_packages(label=name, argv=command, group=family, role=spec, executable=python):
                if self.system == 'Windows':
                    from .torch_download import install
                    def check():
                        if self.cancel.is_set():
                            raise RuntimeError('Installation cancelled; partial download retained.')
                        if self.plan.get('disk_mode') == 'extreme':
                            from .install_disk import check_floor
                            check_floor((self.root,))
                    if install(uv, executable, role['torch'], role['cuda'], root=self.root,
                               networking=self.network, env=self.env, ui=self.ui,
                               run=lambda args, env: self.command(label + '-torch', args, env=env),
                               constraints=['-c', constraints_file(label, self.system)], check=check):
                        return
                return self.packages(label + '-torch', argv, group)
            task(name + '-torch', torch_packages,
                 (name + '-venv',), writer)
        name = 'unified' if self.layout == 'unified' else 'engine'
        python, encoder_python = self.pythons['engine'], self.pythons['encoder']
        command = [uv, 'pip', 'install', '--python', python, '-e', str(SOURCE) + '[runtime]',
                   '-c', constraints_file(name, self.system), 'pip', 'ninja', 'packaging', 'wheel', 'setuptools']
        if self.layout == 'unified':
            command += ['-r', SOURCE / 'constraints/encoder-runtime.txt']
        task('runtime', lambda: self.packages(name + '-packages', command), (name + '-torch',), ('environment:' + name,))
        # Enqueue downloads as soon as their SDKs exist, before waiting for
        # source setup, encoder checks or acceleration compilation.
        task('models', lambda: self.command('models', [python, '-m', 'freevideo_engine.provision',
            '--plan', self.run_dir / 'plan.json']), ('runtime',))
        task('vdn-install', lambda: self.command('vdn', [python, '-m', 'freevideo_engine', 'setup', '--install-packages']),
             ('runtime', 'vdn-source'), ('environment:' + name,))
        encoder_after = 'runtime'
        if self.layout == 'dual':
            def encoder_packages():
                self.packages('encoder-packages', [uv, 'pip', 'install', '--python', encoder_python,
                    '-r', SOURCE / 'constraints/encoder-runtime.txt', '-c', constraints_file('encoder', self.system)])
                self.packages('encoder-engine', [uv, 'pip', 'install', '--python', encoder_python, '--no-deps', '-e', SOURCE])
            task('encoder-runtime', encoder_packages, ('encoder-torch',), ('environment:encoder',))
            encoder_after = 'encoder-runtime'
        comfy = self.root / 'vendor' / 'h3-text-encoder'
        task('encoder-check', lambda: self.command('encoder-library-check', [encoder_python, '-m',
            'freevideo_engine.encode_worker', '--comfy-root', comfy, '--check-library']),
             (encoder_after, 'encoder-source'), ('environment:unified' if self.layout == 'unified' else 'environment:encoder',))
        if self.system == 'Windows':
            task('windows-check', lambda: self.command('windows-runtime-check', [python, '-c',
                'import torch,triton,safetensors,comfy_kitchen,comfy_aimdo; '
                'assert torch.cuda.is_available(), "CUDA unavailable; check NVIDIA driver and Windows DLL errors above"; '
                'print("Windows runtime imports passed; GPU kernel probes follow")']), ('runtime',), ('environment:' + name,))
        overlap = (self.plan.get('model_transfer') or {}).get('overlap_build', True)
        task('sage', lambda: self.install_sage(uv, python), ('runtime',) if overlap else ('runtime', 'models'),
             ('environment:' + name,))
        if self.plan.get('disk_mode') == 'extreme':
            # Finish all environments before models occupy most of the disk.
            # In particular retain the uv cache until ComfyUI has reused it.
            tasks['sage']['after'] = ('runtime',)
            prerequisites = tuple(key for key in tasks if key != 'models')
            frontend = self.plan.get('frontend') or {}
            if frontend.get('separate'):
                task('frontend', lambda: self.command('space-saving-comfy',
                    [python, '-m', 'freevideo_engine.comfy_host', '--root', self.root,
                     '--comfy', frontend['root'], '--setup-plan', self.run_dir / 'plan.json']), prerequisites)
                prerequisites = ('frontend',)
            task('package-cache', lambda: self.release_package_cache(uv), prerequisites)
            tasks['models']['after'] = ('package-cache',)
        return tasks, pythons, comfy

    def release_package_cache(self, uv):
        cache = self.root / 'downloads' / 'uv-cache'
        if cache.is_symlink() or not cache.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('The installation cache points outside this FreeVideo folder; '
                             'space-saving cleanup stopped without removing it.')
        # Use uv's lock-aware cleanup; never unlink its internal files ourselves.
        result = self.command('release-package-cache', [uv, 'cache', 'clean', '--cache-dir', cache])
        from .torch_download import release_cache
        release_cache(self.root)
        return result

    def clone(self, name, url, commit, sparse=None):
        return network.clone(self.root / 'vendor' / name, url, commit, sparse=sparse, run=self.command,
                             network=self.network, env=self.env)

    def packages(self, label, args, family='pypi'):
        return network.package_command(self.network, family,
            lambda env, source: self.command(label if source == 'official' else label + '-' + source, args, env=env), self.env)

    def python_install(self, uv):
        from .system import bootstrap_root
        prepared = bootstrap_root(self.root) / 'python'
        current = Path(self.env['UV_PYTHON_INSTALL_DIR'])
        # A from-zero launcher already installed this exact Python. Reuse its
        # base interpreter when creating the single compute venv; do not download
        # and unpack another copy. Existing installations keep their own base.
        if prepared.is_dir() and not any(current.glob('cpython-' + self.versions['python'] + '-*')):
            env = dict(self.env, UV_PYTHON_INSTALL_DIR=str(prepared))
            found = subprocess.run([str(uv), '--no-config', 'python', 'find', '--managed-python',
                                    '--no-python-downloads', self.versions['python']],
                                   env=env, capture_output=True, text=True, timeout=30)
            if found.returncode == 0:
                candidate = Path(found.stdout.strip())
                if candidate.is_file() and candidate.resolve().is_relative_to(prepared.resolve()):
                    check = subprocess.run([str(candidate), '-I', '-B', '-c',
                        'import platform; print(platform.python_version())'], capture_output=True, text=True, timeout=30)
                    if check.returncode == 0 and check.stdout.strip() == self.versions['python']:
                        self.env['UV_PYTHON_INSTALL_DIR'] = str(prepared)
                        self.command('python-bootstrap-reuse', [candidate, '-I', '-B', '-c',
                                     'import sys; print("Reusing verified bootstrap Python: " + sys.executable)'])
                        return
        custom = self.env.get('UV_PYTHON_INSTALL_MIRROR') if self.network.get('mode') != 'official' else None
        sources = (['user-python'] if custom else []) + network.ordered(self.network, 'github')
        isolation = ['--no-bin', '--no-registry']
        for source in sources:
            for route in network.route_order(self.network, 'github', source, self.env):
                env = network.route_environment(self.network, 'github', source, self.env, route)
                env.update(UV_HTTP_TIMEOUT='20', UV_HTTP_RETRIES='1')
                env['UV_PYTHON_INSTALL_MIRROR'] = custom if source == 'user-python' else network.source_url('github', source, self.env) + '/astral-sh/python-build-standalone/releases/download'
                try:
                    result = self.command('python-' + source + '-' + route, [uv, 'python', 'install', *isolation, self.versions['python']], env=env)
                    network.route_health(self.network, 'github', source, route)
                    return result
                except RuntimeError as error:
                    if not network.retryable(error):
                        raise
                    network.event(self.network, category='python', source=source, route=route, action='fallback', reason='network-download-failed')
                    if not network.connection_failure(error):
                        break
        raise RuntimeError('Python download failed on every route; see retained logs')

    def install_sage(self, uv, python):
        if self.system == 'Windows':
            return self.install_sage_windows(uv, python)
        code = ('import json,platform,sys,torch; print(json.dumps(dict('
                'python="cp"+str(sys.version_info.major)+str(sys.version_info.minor), '
                'torch=str(torch.__version__),cuda=torch.version.cuda,cxx11_abi=torch.compiled_with_cxx11_abi(), '
                'machine=platform.machine(),glibc=platform.libc_ver())))')
        identity = json.loads(subprocess.check_output([str(python), '-c', code], env=self.env, text=True))
        identity.update(arch='.'.join(map(str, self.plan['inventory']['hardware']['capability'])),
                        source=self.spec['sageattention']['commit'], compiler=subprocess.check_output(
                            ['g++', '--version'], text=True).splitlines()[0])
        wheel_dir = Path(self.plan['wheel_cache']) / wheel_key(identity)
        manifest = wheel_dir / 'manifest.json'
        record = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else None
        cached = False
        if record and not self.plan['rebuild_sage']:
            filename = record['filename']
            if Path(filename).name != filename or not filename.endswith('.whl'):
                raise ValueError('Invalid wheel cache filename')
            wheel = wheel_dir / filename
            cached = record['identity'] == identity and wheel.is_file() and digest(wheel) == record['sha256']
        if not cached:
            # Keep old wheels/build objects when forcing a fresh measured build.
            if wheel_dir.exists():
                wheel_dir.rename(wheel_dir.with_name(wheel_dir.name + '.previous-' + self.run_dir.name))
            wheel_dir.mkdir(parents=True)
            toolkit = self.install_toolkit(identity['cuda'])
            sage = self.clone('SageAttention', self.spec['sageattention']['url'], self.spec['sageattention']['commit'])
            stamp = sage / '.freevideo-build.json'
            previous_identity = json.loads(stamp.read_text(encoding='utf-8')) if stamp.is_file() else None
            if (self.plan['rebuild_sage'] or previous_identity != identity) and (sage / 'build').exists():
                (sage / 'build').rename(sage / ('build.previous-' + self.run_dir.name))
            save(stamp, identity)
            header_code = ('from pathlib import Path; import site; '
                          'print(":".join(str(p) for s in site.getsitepackages() for p in (Path(s)/"nvidia").glob("*/include")))')
            headers = subprocess.check_output([str(python), '-c', header_code], env=self.env, text=True).strip()
            build = self.plan['build']
            build_env = dict(self.env, CUDA_HOME=str(toolkit), PATH=str(toolkit / 'bin') + os.pathsep + self.env['PATH'],
                CPATH=headers, TORCH_CUDA_ARCH_LIST=identity['arch'], MAX_JOBS=str(build['jobs']),
                EXT_PARALLEL='1', NVCC_APPEND_FLAGS='--threads=' + str(build['nvcc_threads']))
            self.command('sage2-toolchain-check', [python, '-c',
                'from torch.utils.cpp_extension import is_ninja_available; '
                'assert is_ninja_available(), "Ninja must be visible in the build PATH; serial fallback is refused"'], env=build_env)
            self.command('sage2-build', [python, '-m', 'pip', 'wheel', '--verbose', '--no-build-isolation', '--no-deps',
                         '--wheel-dir', wheel_dir, sage], env=build_env)
            wheels = list(wheel_dir.glob('*.whl'))
            if len(wheels) != 1:
                raise ValueError('Expected one locally built Sage2 wheel')
            wheel = wheels[0]
            record = {'identity': identity, 'filename': wheel.name, 'sha256': digest(wheel),
                      'bytes': wheel.stat().st_size, 'build_run': str(self.run_dir), 'kernel_validation': 'pending'}
            save(manifest, record)
        # Install from the verified wheel path, avoiding architecture-blind source caches.
        self.command('sage2-wheel-install', [uv, 'pip', 'install', '--python', python, '--no-deps',
                                           '--reinstall-package', 'sageattention', wheel])
        save(self.run_dir / 'sage-wheel.json', dict(record, reused=cached, cache=str(wheel_dir)))
        return manifest

    def install_sage_windows(self, uv, python):
        spec = self.versions['windows']['sageattention']
        directory = Path(self.plan['wheel_cache']) / 'windows-sage2' / spec['sha256'][:16]
        wheel = directory / unquote(urlsplit(spec['url']).path.rsplit('/', 1)[-1])
        reused = wheel.is_file()
        self.fetch(spec['url'], wheel, spec['sha256'])
        self.command('sage2-windows-wheel-install', [uv, 'pip', 'install', '--python', python,
                     '--no-deps', '--reinstall-package', 'sageattention', wheel])
        record = dict(spec, filename=wheel.name, reused=reused, cache=str(directory),
                      build_run=str(self.run_dir), kernel_validation='pending',
                      architecture=self.plan['inventory']['hardware']['capability'])
        manifest = directory / 'manifest.json'
        save(manifest, record)
        save(self.run_dir / 'sage-wheel.json', record)
        return manifest

    def install_git_windows(self):
        if shutil.which('git', path=self.env['PATH']):
            return
        spec = self.versions['windows']['git']
        archive = self.root / 'downloads' / ('MinGit-' + spec['version'] + '.zip')
        self.fetch(spec['url'], archive, spec['sha256'])
        directory = self.root / 'tools' / ('git-' + spec['version'])
        unpack(archive, directory)
        executable = directory / spec['executable']
        if not executable.is_file():
            raise RuntimeError('Portable Git archive is incomplete: ' + str(executable))
        self.env['PATH'] = str(executable.parent) + os.pathsep + self.env['PATH']
        self.command('portable-git-check', [executable, '--version'])
        self.env['FREEVIDEO_GIT'] = str(executable)

    def install_toolkit(self, cuda):
        spec = self.versions['cuda_toolkits'][cuda]
        toolkit = self.root / 'tools' / ('cuda-' + cuda)
        receipt = toolkit / '.freevideo-toolkit.json'
        complete = lambda: all((toolkit / name).is_file() and (toolkit / name).stat().st_size
                               for name in spec['required_files'])
        if receipt.is_file() and json.loads(receipt.read_text(encoding='utf-8')) == spec and complete():
            return toolkit
        # CUDA 13 splits CRT and NVVM into additional archives. An nvcc binary
        # alone is not a usable compiler, especially after interrupted setup.
        toolkit.mkdir(parents=True, exist_ok=True)
        for component in spec['components'].values():
            archive = self.root / 'downloads' / Path(component['relative_path']).name
            self.fetch(self.versions['cuda_redist_base'] + component['relative_path'], archive, component['sha256'])
            unpack(archive, self.root / 'tools' / 'cuda-components')
            unpacked = self.root / 'tools' / 'cuda-components' / archive.name.removesuffix('.tar.xz')
            shutil.copytree(unpacked, toolkit, dirs_exist_ok=True, symlinks=True)
        if not (toolkit / 'lib64').exists() and not (toolkit / 'lib64').is_symlink():
            (toolkit / 'lib64').symlink_to('lib', target_is_directory=True)
        if not complete():
            raise RuntimeError('CUDA toolkit is incomplete: ' + str(toolkit))
        save(receipt, spec)
        return toolkit

    def device_configuration(self):
        return {'gpu_uuid': self.plan['inventory']['selected_gpu']['uuid']}

    def check_kernels(self, python, kernel_report):
        # Kernel probes execute real CUDA work and take roughly a minute on a
        # consumer GPU.  A completed receipt is reusable only when every
        # input that can change the result still matches: GPU identity,
        # driver, Torch/CUDA, installed attention packages, and the engine
        # source hashes.  Keep the run-local copy so diagnostics retain the
        # exact receipt used by this installation.
        cached_kernel = self.root / 'kernel-capabilities.json'
        hardware_data = self.plan.get('inventory', {}).get('hardware')
        receipt = (reusable_kernel_receipt(cached_kernel, hardware_data)
                   if isinstance(hardware_data, dict) else None)
        if receipt is None:
            self.command('kernels', [python, '-m', 'freevideo_engine', 'doctor', '--probe', '--require-paths', '--out', kernel_report])
        else:
            save(kernel_report, receipt)
            self.ui.event('setup_cache', key='kernels', detail='Reused matching GPU kernel checks')

    def record_kernel_validation(self, results, kernel_report):
        sage_manifest = results['sage']
        sage_record = json.loads(sage_manifest.read_text(encoding='utf-8'))
        kernels = json.loads(kernel_report.read_text(encoding='utf-8'))
        sage_record['kernel_validation'] = ('Small Sage2 kernels passed on ' + self.plan['inventory']['selected_gpu']['uuid']
            if 'sage2' in kernels['usable_attention_backends'] else 'Sage2 probe failed; see ' + str(kernel_report))
        save(sage_manifest, sage_record)

    def prepare_tools(self):
        uv_spec = self.versions['uv']
        if self.system == 'Windows':
            from .uv_bootstrap import prepare_windows
            uv = prepare_windows(self.root, uv_spec, self.network, self.env, self.fetch)
        else:
            archive = self.root / 'downloads' / 'uv.tar.gz'
            from .system import bootstrap_root
            previous = bootstrap_root(self.root) / ('uv-' + uv_spec['version'] + '.tar.gz')
            if not archive.exists() and previous.is_file() and digest(previous) == uv_spec['sha256']:
                archive.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(previous, archive)
                except OSError:
                    shutil.copyfile(previous, archive)
            self.fetch(uv_spec['url'], archive, uv_spec['sha256'])
            unpack(archive, self.root / 'tools')
            uv = self.root / 'tools' / 'uv-x86_64-unknown-linux-gnu' / 'uv'
        return uv

    def execute(self):
        self.ui.phase('Prepare download tools', 0, 8)
        uv = self.prepare_tools()
        self.env['FREEVIDEO_UV'] = str(uv)
        from .install_schedule import run
        tasks, pythons, comfy = self.component_tasks(uv)
        total = len(tasks) + 4  # Bootstrap, preparation, checks and final readiness.
        phase = 'Install with space saver' if self.plan.get('disk_mode') == 'extreme' else 'Install components in parallel'
        def scheduling(name, status, completed, count):
            with self.state_lock:
                self.state.setdefault('schedule', {})[name] = dict(status=status, after=tasks[name]['after'],
                    exclusive_writers=tasks[name]['writes'], epoch=time.time())
                save(self.run_dir / 'status.json', self.state)
            self.ui.phase(phase, 1 + completed, total)
        self.ui.phase(phase, 1, total)
        results = run(tasks, self.cancel, progress=scheduling,
                      workers=1 if self.plan.get('disk_mode') == 'extreme' else 3)
        python, encoder_python = self.pythons['engine'], self.pythons['encoder']
        prepared = self.run_dir / 'prepared.json'
        self.ui.phase('Optimize model storage', total - 3, total)
        self.command('prepare', [python, '-m', 'freevideo_engine.provision', '--plan', self.run_dir / 'plan.json',
                                '--prepare', '--out', prepared])
        self.ui.phase('Verify installation on your GPU', total - 2, total)
        for name, executable in pythons.items():
            self.command(name + '-dependency-check', [uv, 'pip', 'check', '--python', executable])
            self.command(name + '-freeze', [uv, 'pip', 'freeze', '--python', executable])
        kernel_report = self.run_dir / 'kernel-capabilities.json'
        self.check_kernels(python, kernel_report)
        self.command('storage', [python, '-m', 'freevideo_engine.provision', '--plan', self.run_dir / 'plan.json',
                                 '--cleanup', '--out', self.run_dir / 'storage.json'])
        self.record_kernel_validation(results, kernel_report)
        self.ui.phase('Finish setup', total - 1, total)
        configuration = {'schema_version': 1, 'root': str(self.root), 'source': str(SOURCE),
            'storage': self.plan.get('storage', 'compact'),
            'disk_mode': self.plan.get('disk_mode', 'normal'),
            'environment_layout': self.layout,
            'system': self.system, 'engine_version': __version__,
            'git': self.env.get('FREEVIDEO_GIT') or shutil.which('git', path=self.env['PATH']),
            'platform_validation': 'Local dependency and small GPU probes passed; full consumer-GPU validation comes from user test reports.',
            'kernel_capabilities': str(self.root / 'kernel-capabilities.json'),
            'python': str(python), 'comfy_python': str(encoder_python), 'comfy_root': str(comfy),
            'vdn_root': str(self.root / 'vendor' / 'vdn'), 'model_root': self.plan['model_dir'],
            'encoder_model_root': self.plan['encoder_dir'], 'wheel_cache': self.plan['wheel_cache'],
            'base': str(Path(self.plan['model_dir']) / 'h3-base'),
            'checkpoint': str(Path(self.plan['model_dir']) / 'stage-dmd-step-250'),
            'cache': json.loads(prepared.read_text(encoding='utf-8'))['cache'],
            'encoder': self.spec['models']['encoder_file'].split('/')[-1],
            'model_paths': str(self.root / 'encoder-paths.yaml'),
            **self.device_configuration(),
            'vram_gib': self.plan.get('vram_gib'), 'ram_gib': self.plan.get('ram_gib'),
            'model_revision': self.spec['models']['vdn_revision'],
            'setup_run': str(self.run_dir), 'ready': True}
        # Commit readiness only after all steps pass. Failed/rerun setup files
        # remain available, and no model/output cleanup runs automatically.
        self.monitor_stop.set()
        self.monitor_thread.join(timeout=5)
        if self.monitor_thread.is_alive() or self.cancel.is_set():
            raise RuntimeError(self.state.get('resource_guard', 'Installation monitoring did not stop cleanly.'))
        save(self.root / 'machine.json', configuration)
        self.ui.phase('Setup complete', total, total)
        return configuration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('FREEVIDEO_HOME', DEFAULT_ROOT)))
    parser.add_argument('--gpu', help='Physical nvidia-smi index or full GPU UUID')
    parser.add_argument('--environment', choices=('unified', 'dual'),
                        help='New installs default to unified; updates retain their saved layout. Existing environments are kept when switching.')
    parser.add_argument('--models', type=Path, help='Reuse/download official model files in this directory')
    parser.add_argument('--sampling-caches', action='store_true', help='Install all four quality levels and reference-mode sampling caches in advance')
    parser.add_argument('--model-source', choices=('prepared', 'source'), default='prepared',
                        help='Default: download a pinned slim model matching the GPU format. source: explicitly download original weights and convert locally')
    parser.add_argument('--encoder-models', type=Path, help='Directory containing text_encoders/')
    parser.add_argument('--reuse-models', type=Path, help='Scan an existing model library, verify pinned content and import matching files; only missing models are downloaded')
    parser.add_argument('--reuse-models-manifest', type=Path, help='Versioned JSON containing model library roots, including ComfyUI extra model paths')
    parser.add_argument('--copy-existing-models', action='store_true', help='Copy verified local models instead of using same-disk hardlinks; source files are kept')
    parser.add_argument('--allow-model-restart', action='store_true', help='Permit a fresh download when an incomplete model cannot resume; retains old data and may use extra download/disk space')
    cache_options = parser.add_mutually_exclusive_group()
    cache_options.add_argument('--cache', type=Path, help='Verify and reuse an existing FP8 cache')
    cache_options.add_argument('--rebuild-cache', action='store_true', help='Recreate FP8 from pinned source weights, downloading missing sources; preserve failed caches')
    parser.add_argument('--storage', choices=('compact', 'retain'), help='Default compact: stream FP8 and remove installer-owned conversion sources after verification; retain keeps sources for re-quantization')
    parser.add_argument('--frontend-root', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--frontend-separate', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--frontend-download', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--verify', choices=('auto', 'full'), default='auto', help='auto reuses unchanged pinned receipts; full rereads every required tensor')
    parser.add_argument('--wheel-cache', type=Path, help='Reusable local Sage2 wheel cache, keyed by architecture and ABI')
    parser.add_argument('--rebuild-sage', action='store_true', help='Measure a fresh source build, retaining previous build artifacts')
    parser.add_argument('--vram-gib', type=float, help='Save an explicit nominal VRAM cap; default uses detected resources')
    parser.add_argument('--ram-gib', type=float, help='Save an explicit nominal RAM cap; default uses detected resources')
    resources = parser.add_mutually_exclusive_group()
    resources.add_argument('--keep-resource-limits', action='store_true', help='Reuse saved VRAM/RAM caps on update or retry; explicit capacity options override them')
    resources.add_argument('--auto-resources', action='store_true', help='Compatibility alias for the default: detect resources without reusing saved capacity caps')
    parser.add_argument('--network', choices=('auto', 'official'), default='auto', help='Auto: rank sources; official: original upstream sources only. Compare configured proxy and temporary direct routes')
    parser.add_argument('--network-timeout', type=float, default=5, help='Per-connection/probe timeout in seconds (1–30); stalled transfers retry other sources')
    parser.add_argument('--model-downloader', choices=['auto', 'xet'], default='auto',
                        help='xet: require official HF Xet for large weights; no ModelScope/HTTP fallback. Small configs use HTTP; existing files/partials retained')
    parser.add_argument('--plan', '--check', action='store_true', help='Read-only preflight with small network samples; no installation files are written')
    parser.add_argument('--json', action='store_true', help='Print preflight as JSON')
    parser.add_argument('--plain', action='store_true', help='Disable live terminal rendering; append readable log lines')
    parser.add_argument('--verbose', action='store_true', help='Show full setup details, license links and per-step log paths')
    parser.add_argument('--no-color', action='store_true', help='Disable colors (also respects NO_COLOR)')
    parser.add_argument('--hardware-json', type=Path, help='Offline inventory fixture, only with --plan')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--accept-model-license', action='store_true')
    parser.add_argument('--approved-plan', type=Path, help='GUI confirmation receipt; changed paths, models or larger disk/download requirements require a new review')
    args = parser.parse_args(argv)
    if not 1 <= args.network_timeout <= 30:
        parser.error('--network-timeout must be between 1 and 30 seconds')
    if args.hardware_json and not args.plan:
        parser.error('--hardware-json is only permitted with --plan')
    if args.json and not args.plan:
        parser.error('--json requires --plan')
    if args.auto_resources and (args.vram_gib is not None or args.ram_gib is not None):
        parser.error('--auto-resources cannot be combined with --vram-gib or --ram-gib')
    if args.copy_existing_models and not (args.reuse_models or args.reuse_models_manifest):
        parser.error('--copy-existing-models requires --reuse-models or --reuse-models-manifest')
    ui = TerminalUI(platform.system() + ' setup', plain=args.plain, no_color=args.no_color,
                    verbose=args.verbose, show_location=args.verbose)
    try:
        from .local_models import progress_output
        value = plan(args, local_progress=progress_output if os.environ.get('FREEVIDEO_UI_EVENTS') == '1' or not args.json else None)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print('Preflight failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(value, indent=2)) if args.json else display(value, ui, verbose=args.verbose)
    if args.plan:
        return 1 if value['errors'] else 0
    if value['errors']:
        return 1
    try:
        accepted = confirmed(args, value, ui=ui)
    except KeyboardInterrupt:
        print('\nCancelled. No engine or model installation was started.')
        return 130
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if not accepted:
        print('Cancelled. No engine or model installation was started.')
        return 0
    try:
        installer_class = Installer
        if value.get('device_backend') == 'mps':
            from .macos_setup import Installer as installer_class
        installer = installer_class(value, ui)
    except BlockingIOError:
        print('Setup, testing or generation is using this installation/GPU lock. Retry after it finishes.', file=sys.stderr)
        from .locking import lock_holders
        owners = lock_holders([Path(value['root']) / 'setup.lock', os.environ.get('FREEVIDEO_LOCK_PATH', str(Path(value['root']) / 'engine.lock'))])
        if owners:
            print('Processes with open lock handles: %s. Stop the old task before retrying; do not delete lock files.' % ', '.join(map(str, owners)), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print('Setup could not start: ' + str(error), file=sys.stderr)
        return 1
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Setup interrupted by signal %s' % signum)
    previous = processes.termination_handler(interrupted)
    try:
        ui.start(installer.run_dir)
        installer.start_monitor()
        installer.execute()
        installer.state['status'] = 'complete'
    except BaseException as error:
        installer.state.update(status='failed', error='%s: %s' % (type(error).__name__, error))
        ui.phase('Setup interrupted' if isinstance(error, KeyboardInterrupt) else 'Setup failed', ui.done, ui.total)
    finally:
        try:
            installer.monitor_stop.set()
            if installer.monitor_thread:
                installer.monitor_thread.join(timeout=5)
            installer.state['wall_seconds'] = time.monotonic() - installer.started
            save(installer.run_dir / 'status.json', installer.state)
        except OSError as error:
            installer.state.update(status='failed', error='Could not save final setup status: ' + repr(error))
        finally:
            processes.restore_handlers(previous)
            installer.locks.close()
            ui.close()
    if installer.state['status'] == 'complete':
        entry = '.\\test.ps1' if platform.system() == 'Windows' else './test.sh'
        print('\nSetup complete. Next: %s --root "%s"' % (entry, installer.root))
        if args.verbose:
            print('Configuration: %s' % (installer.root / 'machine.json'))
        return 0
    ui.event('failure', error=installer.state.get('resource_guard') or installer.state['error'])
    print('\nSetup failed: %s\nAll files retained. Logs: %s\nRerun the same setup command to resume.' %
          (installer.state.get('resource_guard') or installer.state['error'], installer.run_dir), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
