"""Stdlib-only setup fallback when the NVML command cannot report GPU details.

Run in a child: its temporary CUDA context must exit before installation starts.
CUDA ordinals are not nvidia-smi indices; only UUIDs identify multi-GPU selections.
"""
import ctypes as C
import json
import os
import sys
import uuid

from . import processes, subprocess_output


WARNING = ('NVIDIA GPU information could not be read with nvidia-smi. CUDA detected the GPU; '
           'you can continue. Setup will test it before marking it ready.')


# cuDriverGetVersion of the oldest driver setup accepts (CUDA 13, NVIDIA driver 580).
OLDEST_DRIVER_API = 13000


def probe(selector=None):
    env = dict(os.environ)
    # Enumerate physical devices, then select by stable UUID in the worker too.
    env.pop('CUDA_VISIBLE_DEVICES', None)
    result = subprocess_output.run_python(processes.module_command(__name__, '0' if selector is None else selector),
        env=env, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'CUDA GPU detection exited with code %d' % result.returncode)
    return json.loads(result.stdout)


def local_probe(selector=None):
    driver = C.WinDLL('nvcuda.dll') if sys.platform == 'win32' else C.CDLL('libcuda.so.1')

    def call(name, types, *args):
        function = getattr(driver, name)
        function.argtypes, function.restype = types, C.c_int
        status = function(*args)
        if status:
            raise RuntimeError('%s failed (CUDA error %d)' % (name, status))

    integer = C.POINTER(C.c_int)
    call('cuInit', [C.c_uint], 0)
    version, count = C.c_int(), C.c_int()
    call('cuDriverGetVersion', [integer], C.byref(version))
    call('cuDeviceGetCount', [integer], C.byref(count))
    rows = []
    for ordinal in range(count.value):
        device, major, minor = C.c_int(), C.c_int(), C.c_int()
        call('cuDeviceGet', [integer, C.c_int], C.byref(device), ordinal)
        name, pci = C.create_string_buffer(256), C.create_string_buffer(32)
        identity = C.create_string_buffer(16)
        call('cuDeviceGetName', [C.c_void_p, C.c_int, C.c_int], name, len(name), device)
        call('cuDeviceGetPCIBusId', [C.c_void_p, C.c_int, C.c_int], pci, len(pci), device)
        # Drivers before CUDA 9.2 have no UUID call. Setup refuses them as too old, so the
        # card can still name the graphics card instead of ending on a missing function.
        has_uuid = hasattr(driver, 'cuDeviceGetUuid')
        if has_uuid:
            call('cuDeviceGetUuid', [C.c_void_p, C.c_int], identity, device)
        if has_uuid and hasattr(driver, 'cuDeviceGetUuid_v2'):
            instance = C.create_string_buffer(16)
            call('cuDeviceGetUuid_v2', [C.c_void_p, C.c_int], instance, device)
            if instance.raw != identity.raw:
                raise RuntimeError('MIG is not supported by setup. Select a physical GPU.')
        call('cuDeviceGetAttribute', [integer, C.c_int, C.c_int], C.byref(major), 75, device)
        call('cuDeviceGetAttribute', [integer, C.c_int, C.c_int], C.byref(minor), 76, device)
        rows.append({'index': '', 'cuda_ordinal': ordinal,
            'uuid': 'GPU-' + str(uuid.UUID(bytes=identity.raw)) if has_uuid else '',
            'name': name.value.decode('utf-8', errors='replace'), 'pci.bus_id': pci.value.decode('ascii'),
            'compute_cap': '%d.%d' % (major.value, minor.value), 'driver_version': ''})
    if not rows:
        raise RuntimeError('CUDA found no accessible GPUs. Check the NVIDIA driver and device access, then retry setup.')
    if selector and selector.startswith('MIG-'):
        raise ValueError('MIG is not supported by setup. Select a physical GPU.')
    if version.value < OLDEST_DRIVER_API and not all(row['uuid'] for row in rows):
        # Too old to install on whichever card is chosen; the first one names the problem.
        matches = rows[:1]
    elif selector and selector.startswith('GPU-'):
        matches = [row for row in rows if row['uuid'] == selector]
    elif len(rows) == 1 and selector in (None, '0'):
        matches = rows
    else:
        raise ValueError('nvidia-smi indices are unavailable. Select a full GPU UUID with --gpu: '
                         + ', '.join(row['uuid'] + ' (' + row['name'] + ')' for row in rows))
    if len(matches) != 1:
        raise ValueError('The selected GPU UUID was not found by CUDA: ' + str(selector))
    selected = matches[0]
    if version.value < OLDEST_DRIVER_API:
        # Setup stops on the driver version; the context and memory calls of old drivers
        # differ and nothing downstream needs them.
        selected.update({'memory.total': '0', 'memory.free': '0'})
        return dict(selected_gpu=selected, gpus=rows, cuda_driver_api_version=version.value)
    device = C.c_int()
    call('cuDeviceGet', [integer, C.c_int], C.byref(device), selected['cuda_ordinal'])
    context = C.c_void_p()
    call('cuDevicePrimaryCtxRetain', [C.POINTER(C.c_void_p), C.c_int], C.byref(context), device)
    try:
        call('cuCtxSetCurrent', [C.c_void_p], context)
        free, total = C.c_size_t(), C.c_size_t()
        call('cuMemGetInfo_v2', [C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)], C.byref(free), C.byref(total))
        selected.update({'memory.total': str(total.value / 2**20), 'memory.free': str(free.value / 2**20)})
    finally:
        call('cuDevicePrimaryCtxRelease_v2', [C.c_int], device)
    return dict(selected_gpu=selected, gpus=rows, cuda_driver_api_version=version.value)


if __name__ == '__main__':
    try:
        print(json.dumps(local_probe(sys.argv[1] if len(sys.argv) > 1 else None)))
    except (OSError, RuntimeError, ValueError, AttributeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
