"""Read-only whole-host admission and conservative standard-prompt envelopes.

No CUDA context, model import, profile change or client-supplied resource claim
is involved. Unsupported graphs use the scheduler's exclusive lane.
"""
import json
import math
from pathlib import Path
import struct

from .admission import Envelope, HostMemory
from .hardware import Hardware, cgroup_memory
from .policy import (BLOCK_BYTES, ENCODER_HOST_BYTES, adaln_extra_bytes, decoder_workspace,
                     resource_budget)
from .preparation import expected_groups
from .system import (HOST_WEIGHT_HEADROOM, residual_host_headroom,
                     system_memory, windows)


def probe_host_memory():
    """Live physical/cgroup headroom and independent Windows commit, less reserves.

    A cgroup is credited with its clean filesystem pages (cgroup_memory's
    reclaimable_available_bytes: dirty, shared and locked pages earn nothing);
    a shared parent cgroup full of reclaimable file cache must not hold a
    request back. Without a cgroup limit, and on Windows and Mac, nothing
    changes. The ledger retains full future peaks against these fresh counters.
    """
    memory = system_memory()
    physical = memory['physical_available_bytes']
    total = memory['total_bytes']
    commit = memory.get('commit_available_bytes')
    if any(type(value) is not int or value < 0 for value in (physical, total)):
        raise ValueError('Invalid physical memory counters')
    if windows() and (type(commit) is not int or commit < 0):
        raise ValueError('Missing Windows commit counter')
    group = cgroup_memory()
    if not group['complete']:
        raise ValueError('Incomplete cgroup memory counters')
    if group['limit_bytes'] is not None:
        total = min(total, group['limit_bytes'])
        physical = min(physical, group.get('reclaimable_available_bytes', group['available_bytes']))
    available = min(physical, commit) if commit is not None else physical
    hardware = Hardware('', (), 0, 0, total, available,
                        system='Windows' if windows() else 'Linux')
    reserve = resource_budget(hardware)['ram_system_reserve_bytes']
    return HostMemory(max(0, physical - reserve),
                      max(0, (commit if commit is not None else physical) - reserve))


def _checkpoint_storage(path, expected):
    """Bound mapped input and a rounded storage copy from safetensors metadata.

    Prepared inference weights and the native quantized encoder preserve their
    checkpoint storage dtype; VAE FP16 conversion only shrinks FP32 storage.
    Cold weight preparation/dequantization is outside this model. Round every
    storage to twice its payload: power-of-two allocation rounding is strictly
    below that bound for any partition into tensors or packed dtype planes
    (offload._pin_reservation). This needs no Torch import.
    """
    if path.stat().st_size != expected:
        raise ValueError('Unrecognized model size')
    with path.open('rb') as stream:
        length = struct.unpack('<Q', stream.read(8))[0]
        if not 2 <= length <= min(100_000_000, expected - 8):
            raise ValueError('Invalid safetensors header')
        header = json.loads(stream.read(length))
    expanded = 0
    spans = []
    for name, tensor in header.items():
        if name == '__metadata__':
            continue
        shape = tensor['shape']
        if any(type(n) is not int or n < 0 for n in shape):
            raise ValueError('Invalid tensor shape')
        sizes = dict(F64=8, I64=8, F32=4, I32=4, F16=2, BF16=2, I16=2,
                     F8_E4M3=1, F8_E4M3FN=1, F8_E5M2=1, I8=1, U8=1, BOOL=1)
        if tensor['dtype'] not in sizes:
            raise ValueError('Unsupported model dtype')
        size = math.prod(shape) * sizes[tensor['dtype']]
        begin, end = tensor['data_offsets']
        if (type(begin) is not int or type(end) is not int or begin < 0
                or end - begin != size or end > expected - length - 8):
            raise ValueError('Invalid tensor storage')
        spans.append((begin, end))
        expanded += 2 * size
    cursor = 0
    for begin, end in sorted(spans):
        if begin != cursor:
            raise ValueError('Non-contiguous tensor storage')
        cursor = end
    if cursor != expected - length - 8:
        raise ValueError('Incomplete tensor storage')
    if not expanded:
        raise ValueError('Empty model')
    return expected, expanded


class PromptEnvelope:
    """Estimate literal, text-only FreeVideo requests up to the policy reference.

    Count prepared weights, encoder and VAEs' mapped input AND rounded native
    storage. Cold preparation has no whole-task model and stays exclusive.
    The upscaler uses its existing host allowance. Also retain a complete
    prepared transformer and its transfer slots, even when weights are on GPU.
    Sum stage allowances rather than crediting sequential release or shared
    files: resident workers may retain earlier allocations. Startup workers and
    old caches are already charged in the live probe, without attribution credit.
    This intentionally loose upper estimate is not a capacity guarantee.
    """
    def __init__(self, root, devices):
        self.root, self.devices = Path(root), tuple(devices)

    def __call__(self, prompt, extra):
        try:
            return self._estimate(prompt)
        except (OSError, ValueError, KeyError, TypeError, struct.error):
            return None

    def _estimate(self, prompt):
        generators = [node for node in prompt.values() if node.get('class_type') == 'FreeVideoGenerate']
        if len(generators) != 1:
            return None
        node = generators[0]
        empty_media = set()
        for identity, other in prompt.items():
            if other is node:
                continue
            media = other.get('inputs', {})
            if (other.get('class_type') != 'FreeVideoMedia' or set(media) - {'assets'}
                    or not isinstance(media.get('assets', '[]'), str)
                    or json.loads(media.get('assets', '[]')) != []):
                return None
            empty_media.add(identity)
        inputs = dict(node['inputs'])
        link = inputs.get('media')
        if isinstance(link, list) and len(link) == 2 and link[0] in empty_media and link[1] == 0:
            del inputs['media']
        allowed = {'text', 'width', 'height', 'seconds', 'seed', 'two_pass', 'base_steps',
                   'refine_steps', 'force_regenerate', 'preview', 'frames', 'step_cache',
                   'upscale_factor', 'refine_from', 'continue_from', 'first_frame_reference',
                   'voice_reference', 'hide_tail'}
        if (set(inputs) - allowed or any(isinstance(v, (list, dict)) for v in inputs.values())
                or inputs.get('refine_from') or inputs.get('continue_from')
                or inputs.get('hide_tail') or inputs.get('first_frame_reference')
                or inputs.get('voice_reference') or inputs.get('step_cache', 'none') != 'none'):
            return None
        from .geometry import geometry
        canvas = geometry(inputs.get('width', 1344), inputs.get('height', 768),
                          frames=inputs['frames'] if inputs.get('frames') else None,
                          seconds=None if inputs.get('frames') else inputs.get('seconds', 10.))
        if canvas['width'] > 1344 or canvas['height'] > 768 or canvas['frames'] > 243:
            return None
        machine = json.loads((self.root / 'machine.json').read_text(encoding='utf-8'))
        manifest = json.loads(Path(__file__).with_name('model_files.json').read_text(encoding='utf-8'))
        encoder = 'text_encoders/' + machine['encoder']
        if encoder not in {row['file'] for row in manifest} or not self.devices:
            return None
        cache = Path(machine['cache'])
        prepared_manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
        if prepared_manifest.get('online_lora'):
            return None
        groups = list(prepared_manifest['groups'])
        from .adaln_assets import SLIM_FORMATS, projection_groups, validate_catalog
        slim = prepared_manifest.get('format') in SLIM_FORMATS
        catalog = groups + projection_groups(prepared_manifest) if slim else groups
        if (prepared_manifest.get('precision', 'bf16') not in ('fp8', 'int8', 'bf16')
                or sorted(row['group'] for row in catalog) != expected_groups(Path(machine['base']))):
            return None
        if slim:
            # Other schedules may restore omitted projections or build tables;
            # their preparation peak is not described by this adapter.
            if (not inputs.get('two_pass', True) or inputs.get('base_steps', 8) != 8
                    or inputs.get('refine_steps', 3) != 3):
                return None
            groups += [row for row, _ in validate_catalog(prepared_manifest,
                       len(projection_groups(prepared_manifest)))]
        files = [(cache / row['file'], row['bytes']) for row in groups]
        if any(not path.resolve().is_relative_to(cache.resolve()) for path, _ in files):
            return None
        for row in manifest:
            name = Path(row['file'])
            if row['file'] == encoder:
                path = Path(machine.get('encoder_model_root') or machine['model_root']) / name
            elif name.suffix == '.safetensors' and name.parts[:2] in (
                    ('h3-base', 'vae'), ('h3-base', 'audio_vae')):
                path = Path(machine['base']).joinpath(*name.parts[1:])
            else:
                continue
            files.append((path, row['bytes']))
        mapped = expanded = 0
        for path, size in files:
            raw, copy = _checkpoint_storage(path, size)
            mapped += raw
            expanded += copy
        # The retained model and its pinning copy are counted above. Two extra
        # transfer slots follow offload's maximum prefetch depth, at policy's
        # complete-block size even when the actual offloaded layer is smaller.
        slot = max(BLOCK_BYTES, max(row['bytes'] for row in prepared_manifest['groups']
                                   if row['group'].startswith('blocks/')))
        prepared = 2 * (1 << (slot - 1).bit_length())
        residual = residual_host_headroom(canvas)
        output = canvas['width'] * canvas['height'] * canvas['frames'] * 3
        from .two_pass import UPSCALER_HOST_BYTES, render_size
        if render_size(canvas['width'], canvas['height']) is not None:
            return None
        workspace = (HOST_WEIGHT_HEADROOM + ENCODER_HOST_BYTES + residual
                     + decoder_workspace(canvas) + 3 * output
                     + UPSCALER_HOST_BYTES + adaln_extra_bytes(canvas))
        # Charge every private/workspace byte as potentially pinned, including
        # attention readouts and decoder/encoder transfer buffers.
        pinned = expanded + prepared + workspace
        host = mapped + expanded + prepared + workspace
        # Commit excludes pinned storage, which AdmissionLedger charges once.
        # Reserve the full device capacity for future WDDM allocation, following
        # runtime.gpu_commit_reserve, with no credit for already reserved VRAM.
        gpu_commit = max(device.total_bytes for device in self.devices) if windows() else 0
        return Envelope(host, host - pinned + gpu_commit, pinned)
