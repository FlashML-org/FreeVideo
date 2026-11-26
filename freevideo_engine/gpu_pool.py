"""Which GPUs may run generation at the same time, and the leases that keep them apart.

A host with two or more eligible NVIDIA devices can run one generation per
device. A host with fewer keeps the original single-device path: callers ask
`pool_enabled()` first and never touch the leases here when it is False.
"""
from contextlib import contextmanager
import csv
from dataclasses import dataclass
import os
import subprocess

from . import subprocess_output
from .locking import device_lock_path, runtime_lock
from .system import nvidia_smi, windows

QUERY = 'index,uuid,name,memory.total,compute_cap'
OFF = ('0', 'off', 'false', 'no')


@dataclass(frozen=True)
class Device:
    index: int
    uuid: str
    name: str
    total_bytes: int
    capability: tuple


def enumerate_devices(env=None):
    """Every physical NVIDIA device nvidia-smi lists; empty when none can be read."""
    try:
        result = subprocess_output.run_external(
            [nvidia_smi(), '--query-gpu=' + QUERY, '--format=csv,noheader,nounits'],
            check=True, capture_output=True, timeout=20, env=env)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return []
    devices = []
    for row in csv.reader(result.stdout.splitlines()):
        try:
            index, uuid, name, total, capability = (item.strip() for item in row)
            major, minor = (int(part) for part in capability.split('.'))
            # MIG instances and unreadable rows are not whole devices.
            if not uuid.startswith('GPU-'):
                continue
            devices.append(Device(int(index), uuid, name, int(float(total) * 2**20), (major, minor)))
        except ValueError:
            continue
    return devices


# A launch that pins CUDA_VISIBLE_DEVICES to the card setup checked (the environment package's host) names the
# value it set in OWN_PIN and keeps what the user had set, if anything, in USER_VISIBLE, with the user's
# CUDA_DEVICE_ORDER (the pin sets PCI_BUS_ID) in USER_ORDER: the order the user's indexes were written for.
OWN_PIN = 'FREEVIDEO_OWN_VISIBLE_DEVICES'
USER_VISIBLE = 'FREEVIDEO_USER_VISIBLE_DEVICES'
USER_ORDER = 'FREEVIDEO_USER_DEVICE_ORDER'


def pin_visible_device(environ, uuid):
    """Show only `uuid` to this process and its children, in bus order, keeping the user's own
    CUDA_VISIBLE_DEVICES and CUDA_DEVICE_ORDER for the pool.

    A second pin (another start attempt) keeps the user's values from the first.
    """
    current = environ.get('CUDA_VISIBLE_DEVICES')
    if environ.get(OWN_PIN) is None or current != environ.get(OWN_PIN):
        for name, value in ((USER_VISIBLE, current), (USER_ORDER, environ.get('CUDA_DEVICE_ORDER'))):
            if value is None:
                environ.pop(name, None)
            else:
                environ[name] = value
    environ['CUDA_VISIBLE_DEVICES'] = environ[OWN_PIN] = uuid
    environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'


def _pinned(env):
    """Whether CUDA_VISIBLE_DEVICES is FreeVideo's own pin, not the user's value."""
    value = env.get('CUDA_VISIBLE_DEVICES')
    return value is not None and env.get(OWN_PIN) is not None and value == env.get(OWN_PIN)


def requested_visibility(env):
    """The CUDA_VISIBLE_DEVICES the user chose, None when it is not set (every device).

    FreeVideo's own pin stands for the value it replaced.
    """
    return env.get(USER_VISIBLE) if _pinned(env) else env.get('CUDA_VISIBLE_DEVICES')


def requested_order(env):
    """The CUDA_DEVICE_ORDER the user's CUDA_VISIBLE_DEVICES was written for (under FreeVideo's pin, the user's own)."""
    return env.get(USER_ORDER) if _pinned(env) else env.get('CUDA_DEVICE_ORDER')


def visible_devices(devices, value, order=None):
    """The listed devices CUDA shows for this CUDA_VISIBLE_DEVICES value, or None when that cannot be told.

    As CUDA reads it: entries are indexes, whole-GPU UUIDs or a unique prefix of one (with or without
    "GPU-"); the list ends at the first entry that names no device, names one twice or is ambiguous, so an
    empty value or -1 shows none. An index counts in CUDA's order: nvidia-smi's (PCI bus) order when
    CUDA_DEVICE_ORDER is PCI_BUS_ID or when every device is the same model (the default fastest-first
    order then keeps the bus order); otherwise, and for a MIG instance, the devices CUDA shows are not known.
    """
    if value is None:
        return list(devices)
    by_bus = len({(d.name, d.capability) for d in devices}) <= 1 or (order or '').strip().upper() == 'PCI_BUS_ID'
    chosen = []
    for entry in value.split(','):
        entry = entry.strip()
        if entry.upper().startswith('MIG-'):
            return None
        if entry.isdigit():
            if not by_bus:
                return None
            matches = [d for d in devices if d.index == int(entry)]
        elif entry:
            wanted = entry.lower()
            matches = [d for d in devices if d.uuid.lower() == wanted] or [
                d for d in devices if any(d.uuid.lower().startswith(p) for p in (wanted, 'gpu-' + wanted))]
        else:
            matches = []
        if len(matches) != 1 or matches[0] in chosen:
            break
        chosen.append(matches[0])
    return [d for d in devices if d in chosen]


def eligible_devices(env=None):
    """Devices generation may use: those CUDA_VISIBLE_DEVICES shows (all when it is not set), and of them those
    FREEVIDEO_GPU_ALLOW lists by index or UUID (absent means all). None of them when the visible devices cannot be
    told."""
    env = os.environ if env is None else env
    devices = visible_devices(enumerate_devices(env), requested_visibility(env), requested_order(env))
    if devices is None:
        return []  # no pool: the single-device path runs on whatever CUDA shows this process
    allowed = {item.strip() for item in env.get('FREEVIDEO_GPU_ALLOW', '').split(',') if item.strip()}
    if not allowed:
        return devices
    return [d for d in devices if str(d.index) in allowed or d.uuid in allowed]


def pool_enabled(env=None):
    env = os.environ if env is None else env
    if env.get('FREEVIDEO_MULTI_GPU', '').strip().lower() in OFF:
        return False
    if not (windows() or os.name == 'posix'):
        return False
    return len(eligible_devices(env)) >= 2


def device_environment(device):
    return {'CUDA_VISIBLE_DEVICES': device.uuid}


def child_lease_environment(device):
    """Environment for a child that inherits the device lease descriptor.

    The child validates its inherited descriptor against FREEVIDEO_LOCK_PATH,
    so it must name the device lease. The parent keeps the shared machine
    lease for as long as the child runs, so the child needs only one.
    """
    return {**device_environment(device), 'FREEVIDEO_LOCK_PATH': str(device_lock_path(device.uuid))}


@contextmanager
def generation_lease(device, *, wait_seconds=0):
    """The machine lease shared and this device's lease exclusively.

    Setup takes the machine lease exclusively, so it still excludes every
    device; two generations on different devices do not exclude each other.
    The descriptor yielded is the device lease, the one children inherit.
    """
    lease = device_lock_path(device.uuid)
    with runtime_lock(shared=True, inherit=False, wait_seconds=wait_seconds), \
            runtime_lock(lease, inherit=False, wait_seconds=wait_seconds) as descriptor:
        yield descriptor
