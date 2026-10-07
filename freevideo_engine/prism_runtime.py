"""Load prepared Prism weights and run one expert at a time with bounded VRAM.

The prepared directory (``prism_prepare``) stores one safetensors file per
block role. A *unit* is one layer of the active expert: its video block plus,
for the fused layers, the audio block and the a2v / v2a conditioners. The
policy keeps some units resident on the GPU and streams the rest through
FreeVideo's ``offload.LayerOffloader`` (two CUDA slots, prefetch, optional
pinned host copies, ``streamed_weights.SafetensorLayers`` as the source).
Only one expert exists at a time: at the high -> low boundary the high-noise
expert is released before the low-noise one is loaded. The roots (embeddings,
heads) of the active expert and of the audio DiT stay on the GPU.
"""
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import os
import time

import torch

from .prism_model import sampling


ROLES = (('video', None), ('audio', 'audio/blocks/%s.safetensors'), ('a2v', 'bridge/%s.safetensors'),
         ('v2a', 'bridge/%s.safetensors'))
EXPERT_DIRS = {'high': 'expert_high', 'low': 'expert_low'}
META_KEY = 'prism_qlinear'


def emit(**event):
    print(json.dumps(event), flush=True)


def read_manifest(root):
    manifest = json.loads((Path(root) / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 'freevideo-prism-prepared':
        raise ValueError('Not a prepared Prism directory: ' + str(root))
    return manifest


def audio_calibration(root, expert):
    """Per-expert v2a K/V maps for the calibrated audio mode (tier audio_calibrated):
    <variant>/audio_calibration/<expert>.safetensors beside the expert folders, a few
    MB each, listed in the variant's manifest like every other file. None when the
    bundle has none (the mode then refuses to start)."""
    path = Path(root) / 'audio_calibration' / ('%s.safetensors' % expert)
    return path if path.is_file() else None


def read_configs(root):
    root = Path(root) / 'configs'
    configs = {name: json.loads((root / name / 'config.json').read_text(encoding='utf-8'))
               for name in ('video_dit', 'video_dit_2', 'audio_dit', 'dual_tower_bridge')}
    configs['scheduler'] = json.loads((root / 'scheduler' / 'scheduler_config.json').read_text(encoding='utf-8'))
    configs['model_index'] = json.loads((root / 'model_index.json').read_text(encoding='utf-8'))
    return configs


def unit_files(root, expert, index, fused):
    """Files of unit ``index`` of ``expert``: video file first, then shared ones."""
    tag = '%02d' % index
    paths = [Path(root) / EXPERT_DIRS[expert] / 'blocks' / (tag + '.safetensors')]
    lora = Path(root) / ('lora_' + expert) / 'blocks' / (tag + '.safetensors')
    if lora.is_file():  # unmerged distill LoRA (student only), streamed with the block
        paths.append(lora)
    if index < fused:
        paths.append(Path(root) / 'audio' / 'blocks' / (tag + '.safetensors'))
        bridge = Path(root) / 'bridge' / (tag + '.safetensors')
        if bridge.is_file():
            paths.append(bridge)
    return paths


def file_metadata(path):
    """(qlinear layout, {tensor name: (torch dtype, shape)}) from a safetensors header."""
    from safetensors import safe_open
    from .streamed_weights import _TORCH_DTYPES
    with safe_open(str(path), framework='pt') as handle:
        meta = handle.metadata() or {}
        keys = {}
        for key in handle.keys():
            view = handle.get_slice(key)
            keys[key] = (getattr(torch, _TORCH_DTYPES[view.get_dtype()]), tuple(view.get_shape()))
        return json.loads(meta.get(META_KEY, '{}')), keys


def add_lora_buffers(unit, stored):
    """Register the LoRA tensors (``<module>.lora_*``) named in a unit's files as
    meta buffers, so they load, pin and stream with the block."""
    for name, (dtype, shape) in stored.items():
        parent, _, leaf = name.rpartition('.')
        if not leaf.startswith('lora_'):
            continue
        owner = unit.get_submodule(parent) if parent else unit
        if leaf not in owner._buffers:
            owner.register_buffer(leaf, torch.empty(shape, dtype=dtype, device='meta'))


@torch.no_grad()
def merged_root(module, path):
    """A copy of an expert root with its distill LoRA merged (FP32 sum -> bf16)."""
    import copy
    from safetensors import safe_open
    merged = copy.deepcopy(module)
    entries = {}
    with safe_open(str(path), framework='pt', device='cpu') as handle:
        for key in handle.keys():
            owner, _, kind = key.rpartition('.')
            entries.setdefault(owner, {})[kind] = handle.get_tensor(key)
    for owner, row in entries.items():
        mod = merged.get_submodule(owner)
        if 'lora_down' in row:
            w = mod.weight
            delta = row['lora_up'].float() @ row['lora_down'].float()
            w.copy_((w.float() + delta.reshape(w.shape)).to(w.dtype))
        if 'lora_diff' in row:
            mod.weight.copy_((mod.weight.float() + row['lora_diff'].float().reshape(mod.weight.shape)).to(mod.weight.dtype))
        if 'lora_diff_b' in row and getattr(mod, 'bias', None) is not None:
            mod.bias.copy_((mod.bias.float() + row['lora_diff_b'].float()).to(mod.bias.dtype))
    return merged


@torch.no_grad()
def retype_meta(unit, stored):
    """Give the meta tensors the stored dtypes (the module tree is built in fp32):
    a streamed unit's layout, and hence its GPU slot, follows these tensors."""
    for name, (dtype, shape) in stored.items():
        parent, _, leaf = name.rpartition('.')
        owner = unit.get_submodule(parent) if parent else unit
        if leaf in owner._parameters:
            old = owner._parameters[leaf]
            if old.dtype != dtype or tuple(old.shape) != shape:
                owner._parameters[leaf] = torch.nn.Parameter(torch.empty(shape, dtype=dtype, device='meta'),
                                                             requires_grad=False)
        elif leaf in owner._buffers:
            old = owner._buffers[leaf]
            if old is not None and (old.dtype != dtype or tuple(old.shape) != shape):
                owner._buffers[leaf] = torch.empty(shape, dtype=dtype, device='meta')


def unit_bytes(manifest, root, expert, index, fused):
    files = manifest['files']
    return sum(files[p.relative_to(root).as_posix()]['bytes'] for p in unit_files(root, expert, index, fused))


@torch.no_grad()
def load_root(module, prefix, path, device):
    """Bind root tensors (``prefix`` + name in root.safetensors) to ``module`` on ``device``."""
    from safetensors import safe_open
    state = {}
    with safe_open(str(path), framework='pt', device='cpu') as handle:
        for key in handle.keys():
            if key.startswith(prefix):
                state[key[len(prefix):]] = handle.get_tensor(key).to(device)
    missing, unexpected = module.load_state_dict(state, strict=False, assign=True)
    if unexpected or missing:
        raise ValueError('Root weights do not match %s: missing %s, unexpected %s' % (prefix, missing[:4], unexpected[:4]))
    # Non-persistent tables (RoPE) were built on the CPU; move them too.
    for name, buffer in list(module.named_buffers()):
        if buffer.device.type == 'meta':
            raise ValueError('Unbound buffer ' + name)
    return module.to(device)


def _disk_layers_class():
    from .streamed_weights import SafetensorLayers

    class _DiskLayers(SafetensorLayers):
        """SafetensorLayers that counts its file reads; FREEVIDEO_PRISM_COLD_READS=1
        (measurements) drops each unit's file pages after use, so every pass reads
        the drive as on a machine whose RAM cannot cache the streamed weights."""
        cold = os.environ.get('FREEVIDEO_PRISM_COLD_READS') == '1'

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.read_bytes = 0
            self.read_seconds = 0.0

        def stage_into(self, index, layout, planes):
            tick = time.perf_counter()
            done = super().stage_into(index, layout, planes)
            if done:
                self.read_seconds += time.perf_counter() - tick
                prefix = self.prefixes[index]
                paths = {self.keys[prefix + name] for _, name, *_ in layout}
                self.read_bytes += sum(self.byte_ranges[prefix + name][1] for _, name, *_ in layout)
                if self.cold:
                    from .streamed_weights import release_file_pages
                    release_file_pages(paths)
            return done
    return _DiskLayers


def DiskLayers(*args, **kwargs):
    global _DISK_LAYERS
    if _DISK_LAYERS is None:
        _DISK_LAYERS = _disk_layers_class()
    return _DISK_LAYERS(*args, **kwargs)


_DISK_LAYERS = None


class ExpertPhase:
    """Units of one expert: some resident on the GPU, the rest streamed."""

    def __init__(self, root, manifest, configs, expert, device, *, resident, bsa_params, pin_bytes=0,
                 prefetch=True, progress=None, nonlocal_reserve=None):
        from .offload import LayerOffloader, initialize_streamed_layer, prepare_streamed_layer
        from .streamed_weights import SafetensorLayers
        from .system import HOST_WEIGHT_HEADROOM
        self.expert = expert
        self.device = torch.device(device)
        # Windows: page-locked bytes the weight pins must leave in the non-local
        # budget (the K/V cache, step cache, staging slots), as H3's runtime does.
        self.nonlocal_reserve = nonlocal_reserve
        started = time.perf_counter()
        layers = configs['video_dit']['num_layers']
        _, _, fused = sampling.interaction(configs)
        self.units = []
        self.resident = set()
        streamed, paths, prefixes = [], [], []
        self.resident_bytes = self.streamed_bytes = self.pinned_bytes = 0
        for index in range(layers):
            unit = sampling.build_unit(configs, index, bsa_params)
            files = unit_files(root, expert, index, fused)
            stored = {}
            tag = '%02d.' % index
            for path in files:
                meta, keys = file_metadata(path)
                sampling.swap_qlinears(unit, meta, prefix=tag)
                stored.update((k[len(tag):], v) for k, v in keys.items())
            add_lora_buffers(unit, stored)
            expected = {n for n, _ in unit.named_parameters()} | {n for n, _ in unit.named_buffers()}
            if set(stored) != expected:
                raise ValueError('Prepared unit %s/%d does not match the model: %s' % (
                    expert, index, sorted(set(stored) ^ expected)[:6]))
            retype_meta(unit, stored)
            sampling.fuse_norms(unit)
            size = unit_bytes(manifest, root, expert, index, fused)
            if index in resident:
                state = {}
                from safetensors import safe_open
                for path in files:
                    with safe_open(str(path), framework='pt', device='cpu') as handle:
                        for key in handle.keys():
                            state[key[len(tag):]] = handle.get_tensor(key).to(self.device, non_blocking=True)
                unit.load_state_dict(state, strict=True, assign=True)
                del state
                self.resident.add(index)
                self.resident_bytes += size
            else:
                streamed.append((index, unit, size))
                paths.extend(files)
                prefixes.append(tag)
            self.units.append(unit)
            if progress is not None:
                progress(index + 1, layers)
        self.source = None
        self.offloader = None
        self.offloaders = []
        self.slot = {}
        budget = max(0, int(pin_bytes))
        # Fused and tail units stream through separate offloaders: the audio
        # teacher and audio sub-steps visit the fused units alone, and an
        # offloader requires its layers in a fixed cyclic order.
        for group in ([row for row in streamed if row[0] < fused], [row for row in streamed if row[0] >= fused]):
            if not group:
                continue
            group_paths, group_prefixes = [], []
            for index, _, _ in group:
                group_paths.extend(unit_files(root, expert, index, fused))
                group_prefixes.append('%02d.' % index)
            # Units that stay unpinned are read from the prepared files every pass:
            # readinto the pinned staging slot (no file mapping, no tensor copy), with
            # the next unit staged on a host thread when there is no second GPU slot.
            source = DiskLayers(group_paths, group_prefixes, direct_read=True)
            budget = self._stream(group, source, budget, root, expert, fused)
            offloader = LayerOffloader([u for _, u, _ in group], device=self.device, prefetch=prefetch,
                                       manage_hooks=False, weight_source=source, host_prefetch=not prefetch)
            self.offloaders.append((offloader, source))
            for k, (index, _, _) in enumerate(group):
                self.slot[index] = (offloader, k)
        torch.cuda.synchronize(self.device)
        self.load_seconds = time.perf_counter() - started

    def _stream(self, group, source, budget, root, expert, fused):
        from .offload import initialize_streamed_layer, prepare_streamed_layer
        from .system import HOST_WEIGHT_HEADROOM
        for k, (index, unit, size) in enumerate(group):
            if budget >= size:
                # Read into host memory, then pin it (or fall back to placeholders).
                state = {}
                from safetensors import safe_open
                tag = '%02d.' % index
                for path in unit_files(root, expert, index, fused):
                    with safe_open(str(path), framework='pt', device='cpu') as handle:
                        for key in handle.keys():
                            state[key[len(tag):]] = handle.get_tensor(key)
                unit.load_state_dict(state, strict=True, assign=True)
                del state
                pinned, reserved = prepare_streamed_layer(unit, source, k, pin_budget_bytes=budget,
                                                          headroom_bytes=HOST_WEIGHT_HEADROOM,
                                                          nonlocal_reserve_bytes=self.nonlocal_reserve()
                                                          if callable(self.nonlocal_reserve) else self.nonlocal_reserve)
                budget -= reserved
                self.pinned_bytes += pinned
            else:
                initialize_streamed_layer(unit, source, k)
            self.streamed_bytes += size
        return budget

    def residency(self, index):
        entry = self.slot.get(index)
        if entry is None:
            return nullcontext()
        return entry[0].layer(entry[1])

    def rewind(self):
        """Return every offloader to its first unit, as after a complete pass. The
        prefetch already queued for the next unit is waited for and dropped; no
        later unit is read or uploaded."""
        for offloader, _ in self.offloaders:
            for future in list(offloader.pending.values()):
                future.result()
            offloader.pending.clear()
            for _, future in list(offloader.host_pending.values()):
                future.result()
            offloader.host_pending.clear()
            offloader.expected = 0

    def seek(self, index):
        """The next pass starts at unit ``index`` (partial audio teacher): as rewind(),
        then each offloader expects its first streamed unit at or after ``index``."""
        self.rewind()
        for offloader, _ in self.offloaders:
            later = [k for unit, (owner, k) in self.slot.items() if owner is offloader and unit >= index]
            offloader.expected = min(later) if later else 0

    def residency_map(self):
        """Callable index -> context; ``.streamed`` names the streamed units (the
        audio sub-steps capture a CUDA graph only when none of theirs streams)."""
        return _Residency(self)

    def stats(self):
        out = dict(expert=self.expert, resident_units=sorted(self.resident), resident_bytes=self.resident_bytes,
                   streamed_units=sorted(self.slot), streamed_bytes=self.streamed_bytes,
                   pinned_bytes=self.pinned_bytes, load_seconds=self.load_seconds)
        out['offload'] = []
        for offloader, source in self.offloaders:
            stats = offloader.stats()
            row = {k: stats[k] for k in ('slots', 'cuda_buffer_bytes', 'pinned_layer_count', 'transfers',
                                         'h2d_bytes', 'h2d_seconds', 'prefetch_wait_seconds', 'host_stage_seconds',
                                         'host_prefetch', 'host_prefetch_wait_seconds', 'host_buffer_wait_seconds',
                                         'host_prefetch_disabled_reason', 'direct_read_layers', 'direct_read_bytes',
                                         'pinned_buffer_bytes', 'host_prefetch_buffer_bytes', 'disk_read_ahead')
                   if k in stats}
            row.update(disk_read_bytes=getattr(source, 'read_bytes', 0), disk_read_seconds=getattr(source, 'read_seconds', 0.0))
            out['offload'].append(row)
        return out

    def close(self):
        for offloader, _ in self.offloaders:
            offloader.close()
        self.offloaders = []
        self.slot = {}
        # Release resident GPU tensors and pinned host copies now, even if a
        # caller still holds a reference to a unit module.
        with torch.no_grad():
            for unit in self.units:
                for tensor in list(unit.parameters()) + list(unit.buffers()):
                    tensor.data = torch.empty(0, dtype=tensor.dtype)
        self.units = []
        self.source = None
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        from .torch_compat import empty_host_cache
        empty_host_cache(torch)


class _Residency:
    def __init__(self, phase):
        self.phase = phase
        self.streamed = set(phase.slot)

    def __call__(self, index):
        return self.phase.residency(index)

    def rewind(self):
        """A pass ended early (first-block cache): the next pass starts at unit 0."""
        self.phase.rewind()

    def seek(self, index):
        """The next pass starts at unit ``index`` (partial audio teacher)."""
        self.phase.seek(index)


class SwappedRoots(dict):
    """Root mapping that keeps only one variant of an expert root (base or
    student) on the GPU, moving the requested one in on access (policy
    ``swap_roots``: saves one root, ~0.46 GB, on small GPUs)."""

    def __init__(self, device):
        super().__init__()
        self.device = device

    def __getitem__(self, key):
        module = dict.__getitem__(self, key)
        sibling = key[:-len('/student')] if key.endswith('/student') else key + '/student'
        if dict.__contains__(self, sibling):
            other = dict.__getitem__(self, sibling)
            if next(other.parameters()).device.type != 'cpu':
                other.to('cpu')
            if next(module.parameters()).device != self.device:
                module.to(self.device)
        return module


class PrismModel:
    """Roots on the GPU + the active expert phase."""

    def __init__(self, root, device, *, policy, progress=None):
        self.root = Path(root)
        self.device = torch.device(device)
        self.manifest = read_manifest(self.root)
        self.configs = read_configs(self.root)
        self.policy = policy
        self.progress = progress
        bsa = dict(sparsity=policy.get('sparsity', 0.75), cdf_threshold=policy.get('cdf', 0.2),
                   # Sol correction for the unselected tiles (Sage kernel; tier field sol, sol_beta)
                   sol=policy.get('sol'), sol_beta=policy.get('sol_beta'),
                   chunk_3d_shape_q=[4, 4, 4], chunk_3d_shape_k=[4, 4, 4],
                   dynamic_block_lambda_a=0.5, dynamic_block_tau_128=0.25, dynamic_block_lambda_128=1.0)
        self.bsa_params = bsa
        tick = time.perf_counter()
        roots = sampling.build_roots(self.configs)
        prefixes = {'high': 'video_dit.', 'low': 'video_dit_2.', 'audio': 'audio_dit.'}
        self.root_modules = SwappedRoots(self.device) if policy.get('swap_roots') else {}
        self.root_modules['bridge'] = roots['bridge']
        self.root_modules['audio'] = load_root(roots['audio'], prefixes['audio'], self.root / 'root.safetensors',
                                               self.device)
        self.root_modules['bridge'].rotary.to(self.device)
        self.cpu_roots = {}
        for expert in ('high', 'low'):
            # Expert roots stay in host memory while that expert is inactive.
            self.cpu_roots[expert] = load_root(roots[expert], prefixes[expert], self.root / 'root.safetensors', 'cpu')
            lora_root = self.root / ('lora_' + expert) / 'root.safetensors'
            if lora_root.is_file():
                # Student root: the distill LoRA merged into the small bf16 root layers.
                self.cpu_roots[expert + '/student'] = merged_root(self.cpu_roots[expert], lora_root)
        self.lora = (self.root / 'lora_high').is_dir()
        self.root_seconds = time.perf_counter() - tick
        self.phase = None
        self.phases = []
        # Resident worker (prism_resident): with ``keep_phases`` an expert switch
        # parks the previous expert (GPU units and roots stay) instead of closing it,
        # so a later request reuses both without loading.
        self.keep_phases = False
        self.parked = {}

    def loaded_experts(self):
        return set(self.parked) | ({self.phase.expert} if self.phase is not None else set())

    def host_bytes(self):
        total = sum(t.numel() * t.element_size() for module in self.cpu_roots.values()
                    for t in list(module.parameters()) + list(module.buffers()) if t.device.type == 'cpu')
        for phase in list(self.parked.values()) + ([self.phase] if self.phase is not None else []):
            total += phase.pinned_bytes
        return total

    def _close_phase(self, phase):
        self.phases.append(phase.stats())
        phase.close()
        for key in (phase.expert, phase.expert + '/student'):
            if key in self.root_modules:
                dict.__getitem__(self.root_modules, key).to('cpu')
                del self.root_modules[key]

    def release_phases(self):
        """Close every loaded expert (GPU units, pinned copies); the CPU roots stay."""
        for phase in list(self.parked.values()) + ([self.phase] if self.phase is not None else []):
            self._close_phase(phase)
        self.parked = {}
        self.phase = None
        audio = dict.get(self.root_modules, 'audio') if isinstance(self.root_modules, dict) else None
        if audio is not None:
            audio.to('cpu')
        self.root_modules['bridge'].rotary.to('cpu')
        torch.cuda.empty_cache()

    def activate(self, expert):
        if self.phase is not None and self.phase.expert == expert:
            return self.phase.units, self.root_modules, self.phase.residency_map()
        dict.__getitem__(self.root_modules, 'audio').to(self.device)
        self.root_modules['bridge'].rotary.to(self.device)
        if self.phase is not None:
            if self.keep_phases:
                self.parked[self.phase.expert] = self.phase
                self.phase = None
            else:
                self._close_phase(self.phase)
                self.phase = None
                torch.cuda.empty_cache()
        if expert in self.parked:
            self.phase = self.parked.pop(expert)
            emit(event='prism_expert_reused', expert=expert)
            return self.phase.units, self.root_modules, self.phase.residency_map()
        emit(event='model_load_phase', phase='Loading %s-noise video expert' % expert,
             during_sampling=bool(getattr(self, 'sampling_started', False)))
        for key in (expert, expert + '/student'):
            if key in self.cpu_roots:
                swap = isinstance(self.root_modules, SwappedRoots) and key == expert and \
                    expert + '/student' in self.cpu_roots
                # with swapped roots the base root stays on the CPU until a base pass needs it
                self.root_modules[key] = self.cpu_roots[key] if swap else self.cpu_roots[key].to(self.device)
        resident = set(self.policy['resident_units'][expert])

        def loaded(done, total):
            if self.progress is not None:
                self.progress(expert, done, total)
        reserve = None
        if os.name == 'nt':
            # Locked buffers the plan still has to allocate after these weights; the
            # pinning already sees the registered ones (e.g. the teacher K/V) in the
            # live non-local usage, and counting them again left 0 bytes pinned.
            from .windows_gpu_memory import NONLOCAL_PIN_RESERVE
            # (Evaluated per unit: the K/V slabs register in the background meanwhile.)
            other = int(self.policy.get('locked_other_bytes') or 0)
            reserve = lambda: NONLOCAL_PIN_RESERVE + max(0, other - sampling.registered_bytes())  # noqa: E731
        self.phase = ExpertPhase(self.root, self.manifest, self.configs, expert, self.device, resident=resident,
                                 bsa_params=self.bsa_params, pin_bytes=self.policy.get('pin_bytes', 0),
                                 prefetch=self.policy.get('prefetch', True), progress=loaded,
                                 nonlocal_reserve=reserve)
        emit(event='prism_expert_loaded', expert=expert, **{k: v for k, v in self.phase.stats().items()
                                                           if k in ('resident_bytes', 'streamed_bytes', 'pinned_bytes',
                                                                    'load_seconds')})
        return self.phase.units, self.root_modules, self.phase.residency_map()

    def close(self):
        for phase in list(self.parked.values()) + ([self.phase] if self.phase is not None else []):
            self.phases.append(phase.stats())
            phase.close()
        self.parked = {}
        self.phase = None
        self.root_modules = {}
        self.cpu_roots = {}
        torch.cuda.empty_cache()
        return


# ---------------------------------------------------------------------------
# Text encoder, tokenizer and VAEs
# ---------------------------------------------------------------------------
from .prism_layout import SHARED_FOLDERS, VARIANT_NAMES, shared, sibling, with_shared  # noqa: E402,F401


def load_tokenizer(root):
    try:
        from transformers import T5TokenizerFast as Tokenizer
    except ImportError:  # transformers 5 consolidated the fast tokenizers
        from transformers import T5Tokenizer as Tokenizer
    return Tokenizer.from_pretrained(str(shared(root, 'tokenizer')))


def stream_text_encoder(model, device):
    """Run a CPU-resident UMT5 encoder on ``device`` one block at a time (small
    GPUs): the embedding lookup stays on the CPU (an exact row copy), the blocks
    stream through the GPU. The result is the GPU-resident encoder's (prompt
    embeddings computed on the CPU differed by 7.5%)."""
    from .offload import LayerOffloader
    device = torch.device(device)
    encoder = model.encoder
    for name, child in encoder.named_children():
        if name not in ('embed_tokens', 'block'):
            child.to(device)
    embed = encoder.embed_tokens
    model._freevideo_stream_hooks = [
        embed.register_forward_pre_hook(lambda module, args: tuple(a.to('cpu') if torch.is_tensor(a) else a
                                                                   for a in args)),
        embed.register_forward_hook(lambda module, args, output: output.to(device))]
    # Blocks stream through two GPU slots: the next block is staged in pinned
    # memory and uploaded on a copy stream while the current one runs.
    model._freevideo_offloader = LayerOffloader(list(encoder.block), device=device, prefetch=True, manage_hooks=True)
    return model


def release_text_encoder(model):
    """Undo stream_text_encoder: the model is a plain CPU module again (reusable)."""
    offloader = getattr(model, '_freevideo_offloader', None)
    if offloader is not None:
        offloader.close()
        model._freevideo_offloader = None
    for hook in getattr(model, '_freevideo_stream_hooks', None) or []:
        hook.remove()
    model._freevideo_stream_hooks = []
    if offloader is not None:
        for name, child in model.encoder.named_children():
            if name not in ('embed_tokens', 'block'):
                child.to('cpu')


@torch.no_grad()
def load_text_encoder(root, device):
    """UMT5-XXL encoder from the prepared directory (W8A16 INT8 or bf16)."""
    from safetensors import safe_open
    from transformers import UMT5Config, UMT5EncoderModel
    from .prism_model import qlinear
    directory = shared(root, 'text_encoder')
    path = directory / 'model.safetensors'
    meta = {}
    if path.is_file():
        with safe_open(str(path), framework='pt', device='cpu') as handle:
            meta = json.loads((handle.metadata() or {}).get(META_KEY, '{}'))
    if not meta:
        # bf16 shards: the research load path (HF keeps ``wo`` in fp32).
        model = UMT5EncoderModel.from_pretrained(str(directory), torch_dtype=torch.bfloat16)
        return model.to(device).eval().requires_grad_(False)
    config = UMT5Config.from_pretrained(str(directory))
    with torch.device('meta'):
        model = UMT5EncoderModel(config)
    for name, md in meta.items():
        parent_name, _, leaf = name.rpartition('.')
        parent = model.get_submodule(parent_name) if parent_name else model
        if md['kind'] == 'qlinear':
            module = qlinear.QLinear(md['in_features'], md['out_features'], md['mode'], bias=md['bias'],
                                     rot_block=md['rot_block'], has_mult=md['has_mult'], act_asym=md['act_asym'],
                                     bias_dtype=getattr(torch, md['bias_dtype']) if md['bias_dtype'] else torch.bfloat16,
                                     device='meta')
        else:
            module = qlinear.QEmbedding(md['num_embeddings'], md['embedding_dim'], dtype=getattr(torch, md['out_dtype']),
                                        padding_idx=md['padding_idx'], device='meta')
        setattr(parent, leaf, module)
    target = 'cpu' if torch.device(device).type == 'cpu' else str(torch.device(device))
    state = {}
    with safe_open(str(path), framework='pt', device=target) as handle:
        for key in handle.keys():
            state[key] = handle.get_tensor(key)
    if 'encoder.embed_tokens.qweight' not in state and 'encoder.embed_tokens.weight' not in state:
        model.encoder.embed_tokens = model.shared
    missing, unexpected = model.load_state_dict(state, strict=False, assign=True)
    missing = [m for m in missing if not m.startswith('encoder.embed_tokens.')]
    if missing or unexpected:
        raise ValueError('Text encoder weights do not match: missing %s unexpected %s' % (missing[:4], unexpected[:4]))
    model.encoder.embed_tokens = model.shared
    del state
    # Non-persistent buffers (none expected) would still be meta.
    for name, value in list(model.named_buffers()) + list(model.named_parameters()):
        if value.is_meta:
            raise ValueError('Unbound text encoder tensor ' + name)
    return model.eval().requires_grad_(False)


def load_vae(root, device, dtype=torch.bfloat16):
    from diffusers.models.autoencoders import AutoencoderKLWan
    vae = AutoencoderKLWan.from_pretrained(str(shared(root, 'vae')), torch_dtype=dtype)
    config = json.loads((shared(root, 'vae') / 'config.json').read_text(encoding='utf-8'))
    return vae.to(device).eval().requires_grad_(False), config


def load_audio_vae(root, device):
    from .prism_model.dac_vae import DACDecoder
    return DACDecoder.load(shared(root, 'audio_vae'), device=device, dtype=torch.bfloat16)


def scheduler(root):
    from .prism_model.flow_match_pair import FlowMatchPairScheduler
    return FlowMatchPairScheduler.from_config_file(Path(root) / 'configs' / 'scheduler' / 'scheduler_config.json')
