"""Best-effort, anonymous occupancy and its effect on this request's placement.

No process names, model loads or CUDA initialization. Call readings off the
event loop; an incomplete reading never becomes a claim about other programs.
"""
import os
import platform
import sys

from .background_room import Gpu, available_memory, family
from .capabilities import IDLE_IN_USE, _admit, _machine
from .hardware import GiB
from .system import system_memory

THRESHOLD = GiB
CONTEXT_BYTES = GiB // 2
# What the system and desktop use with no other program running. Readings are
# whole-machine use minus this program's own, so they include it; only the part
# above it counts as other programs. Host memory gets a generous allowance so
# the notice stays quiet unless something else is clearly running.
SYSTEM_RAM = {'Windows': 6 * GiB, 'Linux': 2 * GiB}
MEMORY_ERRORS = frozenset(('no_memory', 'small_memory', 'insufficient_memory',
                           'out_of_memory', 'ram_space'))


def _mac(machine):
    return (platform.system() == 'Darwin' or machine.get('device_backend') == 'mps'
            or (machine.get('device_identity') or {}).get('backend') == 'mps'
            or (machine.get('hardware') or {}).get('system') == 'Darwin')


def _running():
    """Unknown activity is unsafe for the WDDM allocator approximation."""
    from .resident_process import OWNER
    from .background_compile import COMPILE
    if OWNER.active or COMPILE.process is not None and COMPILE.process.poll() is None:
        return True
    module = sys.modules.get('server')
    server = module.PromptServer.instance if module is not None else None
    if server is not None:
        service = getattr(server, '_freevideo_image_gen', None)
        if service is not None and service.busy():
            return True
        queue = getattr(server, 'prompt_queue', None)
        if queue is not None and queue.get_current_queue()[0]:
            return True
    return False


def _allocator_credit(pids):
    """WDDM has no per-process bytes. Only trust idle, already loaded allocators.

    A child with a persistent pool cannot be accounted for by the parent's
    torch allocator. Suppress the approximation while such a worker lives.
    """
    if _running():
        return None
    from .resident_process import OWNER
    module = sys.modules.get('server')
    server = module.PromptServer.instance if module is not None else None
    service = getattr(server, '_freevideo_image_gen', None)
    for process in (OWNER.process, getattr(service, 'process', None)):
        if process is not None and process.poll() is None:
            return None
    torch = sys.modules.get('torch')
    if torch is None or not torch.cuda.is_initialized():
        return None
    return int(torch.cuda.memory_reserved()) + CONTEXT_BYTES


def _rss(pids):
    import psutil
    total = 0
    for pid in pids:
        try:
            total += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            continue  # A child that exited holds no memory now.
    return total


def read(machine=None):
    """{'vram': bytes, 'ram': bytes}, or None if either cannot be established."""
    gpu = None
    try:
        if platform.system() == 'Darwin':
            return None
        if machine is None:
            from .comfy_bridge import installation
            _, machine = installation()
        if (_mac(machine) or machine.get('device_backend', 'cuda') != 'cuda'
                or machine.get('gpu_uuid') is None):
            return None
        pids = family(os.getpid())
        gpu = Gpu(machine.get('gpu_uuid'))
        memory = gpu.memory()
        if memory is None:
            return None
        used, total = memory
        if not total or not 0 <= used <= total:
            return None
        rows = gpu.processes()
        if rows is None or any(size is None for size in rows.values()):
            if platform.system() != 'Windows':
                return None
            own = _allocator_credit(pids)
            if own is None:
                return None
        else:
            own = sum(size for pid, size in rows.items() if pid in pids)
        host_total, available = system_memory()['total_bytes'], available_memory()
        if host_total <= 0 or not 0 <= available <= host_total:
            return None
        return dict(vram=max(0, used - own), ram=max(0, host_total - available - _rss(pids)))
    except Exception:
        return None
    finally:
        if gpu is not None:
            try:
                gpu.close()
            except Exception:
                pass


def net(usage, machine):
    """The reading above what the system and desktop use by themselves."""
    if not isinstance(usage, dict) or not isinstance(machine, dict):
        return None
    hardware = machine.get('hardware') or {}
    system = hardware.get('system') or machine.get('system')
    if system not in SYSTEM_RAM or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('vram', 'ram')):
        return None
    return dict(vram=max(0, usage['vram'] - IDLE_IN_USE[system]['vram']),
                ram=max(0, usage['ram'] - SYSTEM_RAM[system]))


def notice(usage):
    """Only anonymous numbers cross the UI boundary; sub-GiB noise stays quiet."""
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                         for k in ('vram', 'ram')):
        return None
    kinds = [kind for key, kind in (('vram', 'gpu'), ('ram', 'ram')) if usage[key] >= THRESHOLD]
    if not kinds:
        return None
    return dict(vram_gib=usage['vram'] / GiB, ram_gib=usage['ram'] / GiB,
                kinds=kinds, signature='%d:%d' % (usage['vram'] // GiB, usage['ram'] // GiB))


def _worse(current, idle):
    request, code, _ = current
    baseline, base_code, _ = idle
    if baseline is None:
        return False  # Closing other programs cannot admit this request either.
    if request is None:
        return False  # A refused request is reported by the failure card, not as slower.
    return (code > base_code
            or any(request[key] < baseline[key] for key in (
                'pass1_resident_blocks', 'pass2_resident_blocks', 'decoder_resident_blocks'))
            or request['decoder_offload'] != baseline['decoder_offload'])


def placements(machine, usage, *, kind='video', width=1344, height=768, frames=243,
               steps=8, two_pass=True, task='t2va', refine=3, references=None,
               step_cache=None, upscale=2, reference_pixels=0):
    """Identical request and reserves, with and without the measured other usage."""
    idle = _machine(machine)
    usage = net(usage, machine)
    if idle is None or idle['mac'] or _mac(machine) or notice(usage) is None:
        return None
    current = dict(idle, vram_free=max(0, idle['vram_free'] - usage['vram']),
                   ram_available=max(0, idle['ram_available'] - usage['ram']))
    if kind == 'image':
        from .image_gen.image_policy import plan_image
        def plan(value):
            return plan_image(gpu_budget_bytes=value['vram_free'], host_available_bytes=value['ram_available'],
                              width=width, height=height, reference_pixels=reference_pixels).to_dict()
        base, occupied = plan(idle), plan(current)
        worse = base['feasible'] and occupied['feasible'] and (any(
            occupied[key] < base[key] for key in ('dit_resident_layers', 'te_resident_layers',
                                                 'dit_kept_layers', 'vae_tile_size'))
            or occupied['vae_tiling'] != base['vae_tiling']
            or occupied['host_mode'] != base['host_mode'])
    else:
        params = dict(width=width, height=height, frames=frames, steps=steps, two_pass=two_pass,
                      task=task, refine=refine, references=references, step_cache=step_cache, upscale=upscale)
        base, occupied = _admit(idle, **params), _admit(current, **params)
        worse = _worse(occupied, base)
    return dict(idle=base, occupied=occupied, worse=bool(worse))


def for_request(machine, usage, **request):
    try:
        comparison = placements(machine, usage, **request)
        return notice(net(usage, machine)) if comparison and comparison['worse'] else None
    except Exception:
        return None


def at_failure(code, machine=None):
    """Fresh reading at failure, independent of session deduplication/admission."""
    if code not in MEMORY_ERRORS:
        return None
    try:
        if machine is None:
            from .comfy_bridge import installation
            _, machine = installation()
        return notice(net(read(machine), machine))
    except Exception:
        return None


FACTS = {
    'gpu': ('Other programs are using {vram_gib} GB of GPU memory.', '其他程序占用了 {vram_gib} GB 显存。'),
    'ram': ('Other programs are using {ram_gib} GB of RAM.', '其他程序占用了 {ram_gib} GB 内存。'),
    'both': ('Other programs are using {vram_gib} GB of GPU memory and {ram_gib} GB of RAM.',
             '其他程序占用了 {vram_gib} GB 显存和 {ram_gib} GB 内存。'),
}


def join(text, suffix, zh=False):
    """Append the fact as its own sentence: no space in Chinese, one in English."""
    text = (text or '').rstrip()
    if not text:
        return suffix
    if text[-1] not in ('。！？' if zh else '.!?'):
        text += '。' if zh else '.'
    return text + suffix if zh else text + ' ' + suffix


def fact(value, zh=False):
    if not isinstance(value, dict):
        return ''
    kinds = value.get('kinds')
    key = 'both' if kinds == ['gpu', 'ram'] else 'gpu' if kinds == ['gpu'] else 'ram' if kinds == ['ram'] else None
    try:
        return FACTS[key][int(zh)].format(**{k: format(value[k], '.1f') for k in ('vram_gib', 'ram_gib')}) if key else ''
    except (KeyError, TypeError, ValueError):
        return ''
