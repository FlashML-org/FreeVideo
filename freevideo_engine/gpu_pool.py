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


def eligible_devices(env=None):
    """Devices generation may use: FREEVIDEO_GPU_ALLOW lists indexes or UUIDs, absent means all."""
    env = os.environ if env is None else env
    devices = enumerate_devices(env)
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
