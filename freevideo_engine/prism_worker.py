"""Isolated Prism worker: ``--stage encode`` (UMT5 prompt + first-frame VAE
condition) or ``--stage video`` (sampling, decoding and the MP4 with audio).

The supervisor (prism_generate) runs the two stages as separate processes so
the text encoder's memory is returned before the video model loads. Progress
uses the same JSON events as the H3 worker (comfy_bridge tails them).
"""
import argparse
import json
import os
from pathlib import Path
import time

import torch

from .locking import runtime_lock
from .monitoring import save
from .processes import worker_signals


def emit(**event):
    print(json.dumps(event), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--stage', choices=('encode', 'video'), required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding='utf-8'))
    with worker_signals(), runtime_lock():
        run_stage(request, args.stage)


def run_stage(request, stage, resident=None):
    """One stage of a Prism request; ``resident`` (prism_resident.PrismResident)
    when the ComfyUI resident worker runs it, keeping models between requests."""
    # Per-installation Sage attention tune cache (read when sage_bsa is imported).
    if request.get('sage_tune_cache'):
        os.environ['PRISM_SAGE_TUNE_CACHE'] = request['sage_tune_cache']
        os.environ.setdefault('PRISM_AUDIO_TUNE_CACHE',
                              str(Path(request['sage_tune_cache']).with_name('prism-audio-tune.json')))
    if resident is None:
        return (encode if stage == 'encode' else video)(request)
    # The resident worker also runs MiniMax H3: process-wide switches made for Prism
    # (static dynamo shapes, CPU threads) are restored after the stage.
    import torch._dynamo
    dynamic = torch._dynamo.config.automatic_dynamic_shapes
    threads = torch.get_num_threads()
    with resident.lock:
        resident.touch()
        try:
            return (encode if stage == 'encode' else video)(request, resident=resident)
        finally:
            torch._dynamo.config.automatic_dynamic_shapes = dynamic
            torch.set_num_threads(threads)
            resident.touch()


GPU_GROWTH_RESERVE = 256 * 2**20  # as H3: headroom kept below the live WDDM budget


def configure_memory(request, metrics):
    """Bound the PyTorch allocator (gpu_budget.configure, as MiniMax H3): on Windows
    by the live WDDM local budget minus the process's non-Torch usage, so growing
    past dedicated VRAM raises a recoverable out-of-memory error instead of running
    from shared system memory; elsewhere by an explicit capacity cap only."""
    torch.cuda.init()
    from .gpu_budget import configure
    total = torch.cuda.get_device_properties(0).total_memory
    budget = request.get('gpu_budget_bytes') or total
    report = configure(torch, min(int(budget), total), request.get('allocator_limit_bytes'),
                       reserve_bytes=GPU_GROWTH_RESERVE)
    free, _ = torch.cuda.mem_get_info()
    report.update(free_bytes_at_start=free, allocator_limit_bytes=report.get('effective_allocator_limit_bytes'),
                  name=torch.cuda.get_device_name(0), capability=list(torch.cuda.get_device_capability(0)),
                  windows_memory_at_start=wddm_sample())
    metrics['device_memory'] = report
    return report


def wddm_sample():
    """WDDM local / non-local budget and usage of this process (Windows), else None."""
    if os.name != 'nt':
        return None
    from .windows_gpu_memory import sample_once
    return sample_once()


class MemoryGuard:
    """Windows guards around a stage (as MiniMax H3's engine and encoder): a
    LiveGPUBudget refreshed at step boundaries (a shrinking driver budget lowers the
    allocator ceiling, below the live allocations it raises out-of-memory), and a
    SpillGuard comparing the allocator's growth with the dedicated room free when
    the stage began (past it, the next module call raises SharedMemorySpill). Both
    end the worker with a retryable failure; prism_generate retries with the next
    plan. Inactive elsewhere."""

    PRESSURE_INTERVAL = 2.0  # seconds between WDDM readings in the per-layer check

    def __init__(self, report):
        self.live = self.spill = None
        self.report = report
        self.reader = None
        self.pressure = None      # the open over-budget episode, or None
        self.pressure_seen = 0
        self.pressure_clear = 0
        self.pressure_checked = 0.0
        if os.name != 'nt' or not report.get('windows_allocator_limit_enforced'):
            return
        from .gpu_budget import LiveGPUBudget
        self.live = LiveGPUBudget(torch, report, report['device_total_bytes'], GPU_GROWTH_RESERVE)

    def arm(self):
        if os.name != 'nt':
            return
        try:
            from .windows_gpu_memory import AdapterMemory
            from .encoder_workspace import SpillGuard
            self.spill = SpillGuard(torch, AdapterMemory())
            self.spill.arm()
            self.report['spill_guard'] = dict(armed=self.spill.room is not None, room_bytes=self.spill.room)
        except (OSError, RuntimeError, AttributeError, ValueError) as error:
            self.report['spill_guard'] = dict(armed=False, reason=str(error))
            self.spill = None
        try:
            from .windows_gpu_memory import AdapterMemory
            self.reader = AdapterMemory()
        except (OSError, RuntimeError, AttributeError, ValueError):
            self.reader = None

    def check(self):
        """Cheap spill check for the per-layer callback (the lean paths call few nn.Modules)."""
        if self.spill is not None and self.spill.spill is not None:
            from .encoder_workspace import SharedMemorySpill
            raise SharedMemorySpill(*self.spill.spill)
        if self.reader is not None and time.monotonic() - self.pressure_checked >= self.PRESSURE_INTERVAL:
            self.pressure_checked = time.monotonic()
            self.watch_pressure()

    def watch_pressure(self):
        """Another program growing its VRAM shrinks this process's WDDM budget: the
        driver then demotes part of our allocations to system memory without any
        allocation of ours (an RTX 3090 swung between 13.7 and 21.9 GiB dedicated for
        90 s while another process loaded a 32B model), which SpillGuard cannot see.
        Over budget: give cached blocks back first; if that does not end it, report
        the episode (progress warning, metrics) and its end. Sampling continues,
        slower, rather than restarting a long request."""
        try:
            local = self.reader.sample()['local']
        except (OSError, RuntimeError, KeyError, ValueError):
            return
        over = local['usage_bytes'] - local['budget_bytes']
        if over > 0:
            # Cached blocks back to the driver: often enough to get under the budget
            # until the allocator grows again (an RTX 4070 next to a 3 GiB allocation
            # of another process: budget 11.8 -> 8.5 GB, over it about half the time).
            torch.cuda.empty_cache()
        if over > 0:
            self.pressure_clear = 0
            self.pressure_seen += 1
            if self.pressure is None and self.pressure_seen >= 2:
                self.pressure = dict(started=time.time(), over_bytes=int(over), budget_bytes=int(local['budget_bytes']))
                emit(event='vram_pressure', active=True, over_bytes=int(over), budget_bytes=int(local['budget_bytes']),
                     usage_bytes=int(local['usage_bytes']))
            elif self.pressure is not None:
                self.pressure['over_bytes'] = max(self.pressure['over_bytes'], int(over))
        else:
            self.pressure_seen = 0
            if self.pressure is not None:
                self.pressure_clear += 1
                if self.pressure_clear >= 2:
                    episode = dict(self.pressure, seconds=time.time() - self.pressure['started'])
                    self.report.setdefault('vram_pressure', []).append(episode)
                    emit(event='vram_pressure', active=False, seconds=episode['seconds'],
                         over_bytes=episode['over_bytes'])
                    self.pressure = None

    def step(self, stage):
        self.check()
        if self.live is not None:
            self.live.refresh(stage)

    def close(self):
        if self.spill is not None:
            self.spill.disarm()
            if self.spill.spill is not None:
                self.report['spill_guard'].update(spilled_bytes=self.spill.spill[1])
            reader = self.spill.reader
            self.spill = None
            try:
                reader.close()
            except OSError:
                pass
        if self.live is not None:
            self.live.close()
            self.live = None
        if self.pressure is not None:
            self.report.setdefault('vram_pressure', []).append(
                dict(self.pressure, seconds=time.time() - self.pressure['started'], open_at_end=True))
            self.pressure = None
        if self.reader is not None:
            try:
                self.reader.close()
            except OSError:
                pass
            self.reader = None
        self.report['windows_memory_at_end'] = wddm_sample()


def clamp_locked(policy, metrics):
    """Windows: clamp the plan's page-locked host memory to this process's live
    non-local budget (windows_gpu_memory.nonlocal_pin_capacity; the planner read it
    in a probe, a resident worker may still hold page-locked memory): fewer pinned
    units first, then teacher K/V layers to the spill file. The rest of the plan is
    unchanged."""
    from .windows_gpu_memory import nonlocal_pin_capacity
    sample = (metrics.get('device_memory') or {}).get('windows_memory_at_start')
    room = nonlocal_pin_capacity(sample=sample) if sample else None
    if room is None:
        return
    other = int(policy.get('locked_other_bytes') or 0)  # K/V, step cache, staging
    pin = int(policy.get('pin_bytes') or 0)
    record = dict(nonlocal_room_bytes=room, planned_pin_bytes=pin, planned_other_locked_bytes=other)
    if pin + other > room:
        policy['pin_bytes'] = max(0, room - other)
        record['pin_bytes'] = policy['pin_bytes']
        if other > room and policy.get('kv_placement') == 'host' and policy.get('kv_cache_bytes'):
            layers = int(policy.get('kv_layers') or 30)
            layer = int(policy['kv_cache_bytes']) // layers
            kv_now = max(0, layers - int(policy.get('kv_disk_layers') or 0)) * layer
            more = min(layers, -(-(other - room) // layer))
            policy['kv_disk_layers'] = min(layers, int(policy.get('kv_disk_layers') or 0) + more)
            record['kv_disk_layers'] = policy['kv_disk_layers']
            record['kv_bytes_before'] = kv_now
    metrics['locked_clamp'] = record


def failure_kind(error, metrics):
    """adaptive.classify_failure, with this worker's evidence: a SharedMemorySpill
    and any allocation failure (torch's OutOfMemoryError, a refused host
    registration) are retryable resource failures."""
    from .adaptive import classify_failure
    from .encoder_workspace import SharedMemorySpill
    kind = 'shared_memory_spill' if isinstance(error, SharedMemorySpill) else \
        'gpu_oom' if isinstance(error, torch.cuda.OutOfMemoryError) or 'out of memory' in str(error).lower() else None
    metrics['failure'] = classify_failure(error, dict(failure=dict(kind=kind)) if kind else None)
    if kind == 'shared_memory_spill':
        metrics['failure']['spilled_bytes'] = error.spilled_bytes


def peaks(metrics, stage):
    metrics.setdefault('stage_peaks', {})[stage] = dict(
        allocated_bytes=torch.cuda.max_memory_allocated(), reserved_bytes=torch.cuda.max_memory_reserved())
    torch.cuda.reset_peak_memory_stats()


def sage_tuning(policy, geometry, metrics, budget=180.0):
    """Tune the Sage BSA attention kernel once per GPU / Triton / PV mode / canvas
    class (sage_tune, a few minutes; cached in the installation's tune file) before
    the weights load, and record the plan used."""
    from .prism_model import sage_bsa, sage_tune
    from .prism_model.block_sparse_attention.dynamic_block_shape import ZONE_SIZE
    device = torch.device('cuda')
    pv = sage_bsa.resolve_pv(policy.get('sage', 'auto'), device)
    if pv is None or policy.get('attention') == 'exact':
        metrics['sage_tune'] = dict(status='not used', pv=pv)
        return
    latent_frames = (geometry['frames'] - 1) // 4 + 1
    padded = [-(-n // ZONE_SIZE) * ZONE_SIZE for n in (latent_frames, geometry['height'] // 16, geometry['width'] // 16)]
    rows = padded[0] * padded[1] * padded[2]
    cls = sage_bsa.shape_class(rows)
    key = sage_bsa.tune_key(device, pv, cls)
    cached = sage_bsa._tune_disk().get(key)
    status = 'cached' if cached else 'default'
    if cached is None and cls in sage_tune.CLASS_GRIDS and policy.get('sage_tune', True):
        # The search stops at the budget; measured 51-90 s on an H200 and 72 s on an RTX 4070.
        emit(event='model_load_phase', phase='Tuning attention kernels for this GPU (one time, usually 1 to 2 minutes, '
                                             'at most %d)' % round(budget / 60))
        tick = time.perf_counter()
        try:
            cached = sage_tune.tune_class(cls, pv, device, budget_s=budget, verbose=False)
            status = 'tuned now (%.0f s)' % (time.perf_counter() - tick)
        except Exception as error:  # a tuning failure only costs speed
            status = 'tuning failed: %s' % str(error)[:200]
        torch.cuda.empty_cache()
    metrics['sage_tune'] = dict(status=status, key=key, cache=sage_bsa.TUNE_PATH, entry=cached,
                                plan=sage_bsa.launch_plan(device, pv, rows))


def kernel_setup(policy):
    """Kernel switches of the validated recipe."""
    os.environ['PRISM_BSA_HEAD_CHUNK'] = str(policy.get('head_chunk', 8))
    from .prism_model import sampling as _sampling
    _sampling.PARK_RESIDUAL = bool(policy.get('park_residual'))
    try:
        # Static shapes for the compiled helpers: automatic dynamic shapes, fed across
        # processes by torch's profile-guided state in the inductor cache, switched
        # them to dynamic-shape kernels after one request with another head chunk,
        # and later requests then computed other latents (9.6% apart).
        import torch._dynamo
        torch._dynamo.config.automatic_dynamic_shapes = False
    except (ImportError, AttributeError):
        pass
    if policy.get('rope_chunk_elems'):
        from .prism_model import wan_video_dit
        wan_video_dit._ROPE_CHUNK_ELEMS = int(policy['rope_chunk_elems'])
    from .prism_model import sage_bsa, block_sparse_attention  # noqa: F401
    from .prism_model.block_sparse_attention import dynamic_block_attention as dba
    if policy.get('attention') == 'exact':
        os.environ['PRISM_SAGE_BSA'] = 'exact'  # the original bf16 BSA kernel (ivpq_fast pv=None)
    else:
        sage_bsa.patch_prism(pv=policy.get('sage', 'auto'), head_chunk=policy.get('head_chunk', 8))
    if not getattr(dba._dyn_bsa_kernel, '_skip_pad', False):
        orig = dba._dyn_bsa_kernel

        class _SkipPad:  # fully padded query tiles get an empty block list (research skip_pad_q)
            _skip_pad = True
            _orig = orig

            @staticmethod
            def apply(q, k, v, sm_scale, idx, lens, cq, ck, sp, valid):
                if valid is not None:
                    qv = valid.view(-1, cq).any(dim=1)
                    lens = lens * qv.view(1, 1, -1).to(lens.dtype)
                return orig.apply(q, k, v, sm_scale, idx, lens, cq, ck, sp, valid)
        dba._dyn_bsa_kernel = _SkipPad


@torch.no_grad()
def encode(request, resident=None):
    from . import prism_runtime as runtime
    from .prism_model import sampling
    started = time.perf_counter()
    metrics = dict(success=False, stage='encode', phase='load', resident=resident is not None)
    save(request['metrics'], metrics)
    guard = None
    try:
        guard = MemoryGuard(configure_memory(request, metrics))
        guard.arm()
        policy = request['policy']
        keep = policy.get('resident') or {}
        root = Path(request['prepared'])
        device = torch.device('cuda')
        placement = policy.get('text_encoder_device', 'cuda')
        streamed_text = placement == 'stream'
        text_device = torch.device('cuda' if streamed_text else placement)
        emit(event='encoder_phase', stage='encoder_load')
        tick = time.perf_counter()
        text_key = (str(runtime.shared(root, 'text_encoder')), request.get('text_encoder_dir'))
        cached = resident.take_text(text_key) if resident is not None else None
        if cached is not None:
            tokenizer, text_encoder, where = cached
            if streamed_text:
                if where != 'cpu':
                    text_encoder.to('cpu')
                text_encoder = runtime.stream_text_encoder(text_encoder, text_device)
            elif where != text_device.type:
                text_encoder.to(text_device)
            metrics['text_encoder_reused'] = where
        else:
            tokenizer = runtime.load_tokenizer(root)
            if request.get('text_encoder_dir'):
                # Diagnostics: the original bf16 UMT5 (MOVA text_encoder/) instead of the prepared one.
                from transformers import UMT5EncoderModel
                text_encoder = UMT5EncoderModel.from_pretrained(request['text_encoder_dir'], torch_dtype=torch.bfloat16)
                text_encoder = text_encoder.to(text_device).eval().requires_grad_(False)
            elif streamed_text:
                text_encoder = runtime.stream_text_encoder(runtime.load_text_encoder(root, torch.device('cpu')),
                                                           text_device)
            else:
                text_encoder = runtime.load_text_encoder(root, text_device)
        metrics['text_encoder_load_seconds'] = time.perf_counter() - tick
        emit(event='encoder_phase', stage='encoder_compute')
        tick = time.perf_counter()
        prompt, negative = request['prompt'], request['negative_prompt']
        prompt_embeds, tokens = sampling.t5_prompt_embeds(tokenizer, text_encoder, prompt, text_device)
        guard.step('text')
        negative_embeds, negative_tokens = sampling.t5_prompt_embeds(tokenizer, text_encoder, negative, text_device)
        guard.step('text')
        metrics.update(text_seconds=time.perf_counter() - tick, text_tokens=tokens, negative_tokens=negative_tokens,
                       text_encoder_device='cuda (streamed by block)' if streamed_text else str(text_device))
        runtime.release_text_encoder(text_encoder)
        if resident is not None:
            resident.keep_text(text_key, tokenizer, text_encoder, 'cpu' if streamed_text else text_device.type,
                               keep.get('text_encoder'))
        del text_encoder
        torch.cuda.empty_cache()
        peaks(metrics, 'text')
        emit(event='encoder_phase', stage='keyframe_vae')
        tick = time.perf_counter()
        vae_key = str(runtime.shared(root, 'vae'))
        cached = resident.take_vae(vae_key, device) if resident is not None else None
        vae, vae_config = cached or runtime.load_vae(root, device)
        geometry = request['geometry']
        image = None
        if request.get('image'):
            from PIL import Image
            image = sampling.image_tensor(sampling.crop_and_resize(
                Image.open(request['image']).convert('RGB'), geometry['height'], geometry['width']))
        # Ladder: research fast I2V encode (``template``: the zero tail from a cached
        # gray-reference encode) -> official encode -> tiled official encode.
        # Where the planner expects the untiled encode not to fit (12 GB), the tiled
        # template encode first: an RTX 4070 ran out of memory in template and official
        # and took 81 s for the tiled full-video encode.
        modes = [] if policy.get('vae_encode_tiling') else [policy.get('vae_encode', 'template'), 'official']
        if hasattr(vae, 'enable_tiling'):
            modes += ['tiled_template', 'tiled']
        modes = list(dict.fromkeys(m for m in modes if m))
        condition = None
        cache_dir = Path(request['sage_tune_cache']).parent if request.get('sage_tune_cache') else None
        template = template_path(cache_dir, root, geometry, vae.dtype)
        for attempt, mode in enumerate(modes):
            failed = False
            try:
                if attempt:
                    vae.clear_cache()
                    fast_vae_caches_clear()
                condition, latent_frames = vae_encode(vae, vae_config, image, geometry, device, mode, template)
            except torch.cuda.OutOfMemoryError:
                if attempt + 1 == len(modes):
                    raise
                failed = True  # retry outside the handler so the failed attempt's tensors are freed
            if not failed:
                metrics['vae_encode'] = mode
                break
            import gc
            gc.collect()
            torch.cuda.empty_cache()
            metrics.setdefault('vae_encode_oom', []).append(mode)
        metrics['vae_encode_seconds'] = time.perf_counter() - tick
        guard.step('vae_encode')
        if resident is not None:
            resident.keep_vae(vae_key, vae, vae_config, keep.get('vae'))
            metrics['resident_events'] = list(resident.events[-6:])
        del vae
        torch.cuda.empty_cache()
        peaks(metrics, 'vae_encode')
        emit(event='encoder_phase', stage='encoder_save')
        torch.save(dict(prompt_embeds=prompt_embeds.to('cpu'), negative_embeds=negative_embeds.to('cpu'),
                        condition=condition.to('cpu'), text_tokens=tokens, negative_tokens=negative_tokens,
                        geometry=geometry, image=request.get('image'), task='i2va' if image is not None else 't2va'),
                   request['output'])
        metrics.update(success=True, phase='complete', conditioning_shape=list(condition.shape),
                       latent_frames=latent_frames, work_seconds=time.perf_counter() - started)
        emit(event='encoder_complete')
    except BaseException as error:
        metrics.update(error=repr(error), work_seconds=time.perf_counter() - started)
        failure_kind(error, metrics)
        raise
    finally:
        if guard is not None:
            guard.close()
        save(request['metrics'], metrics)


def fast_vae_caches_clear():
    import gc
    from .prism_model import fast_vae
    fast_vae._FAST_ENCODERS.clear()
    gc.collect()
    torch.cuda.empty_cache()


def template_path(cache_dir, prepared, geometry, dtype):
    """Disk copy of fast_vae's zero-reference I2V template for this VAE and canvas.
    The research worker keeps it in memory across requests; a FreeVideo request is
    a fresh process, where computing it (a zero-video encode until its latents stop
    changing, often the whole tail) made ``template`` slower than the plain encode."""
    if not cache_dir:
        return None
    try:
        from . import prism_runtime as runtime
        manifest = runtime.with_shared(prepared, json.loads((Path(prepared) / 'manifest.json').read_text(encoding='utf-8')))
        digest = manifest['files']['vae/diffusion_pytorch_model.safetensors']['sha256'][:16]
    except (OSError, ValueError, KeyError):
        return None
    return Path(cache_dir) / ('prism-i2v-template-%s-%dx%dx%d-%s.pt' % (
        digest, geometry['width'], geometry['height'], geometry['frames'], str(dtype).replace('torch.', '')))


@torch.no_grad()
def vae_encode(vae, vae_config, image, geometry, device, mode, template=None):
    """First-frame I2V condition with the official weights: ``template`` / ``exact``
    (fast_vae.fast_i2v_encode shortcuts), ``official`` or ``tiled``. ``template``:
    file for the zero-reference template (loaded when present, else saved)."""
    from contextlib import nullcontext
    from .prism_model import fast_vae, sampling
    if getattr(vae, 'use_tiling', False):
        vae.use_tiling = False
    if mode == 'tiled':
        vae.enable_tiling()
    if mode == 'tiled_template':
        # 12 GB: the untiled first-frame encode does not fit; the VAE's tiles with the
        # zero tail from a per-tile-shape template (disk copy beside the untiled one)
        vae.enable_tiling()
        tiles = Path(str(template) + '.tiles') if template is not None else None
        templates = {}
        if tiles is not None and tiles.is_file():
            try:
                templates = {key: value.to(device) for key, value in
                             torch.load(tiles, map_location='cpu', weights_only=False).items()}
            except (OSError, RuntimeError, ValueError, AttributeError):
                templates = {}
        known = set(templates)
        dev = torch.device(device)
        if dev.type == 'cuda' and dev.index is None:
            dev = torch.device('cuda', torch.cuda.current_device())
        templates = {(k[0], k[1], k[2], k[3], dev): v for k, v in templates.items()}
        encoder = lambda frame: fast_vae.tiled_i2v_condition(vae, frame, geometry['frames'], templates=templates)
        out = sampling.image_condition(vae, vae_config, image, geometry['frames'], geometry['height'],
                                       geometry['width'], device, first_frame_encoder=encoder)
        if tiles is not None and set(k[:4] for k in templates) != set(k[:4] for k in known):
            try:
                tiles.parent.mkdir(parents=True, exist_ok=True)
                partial = Path(str(tiles) + '.partial')
                torch.save({k[:4] + ('cpu',): v.cpu() for k, v in templates.items()}, partial)
                partial.replace(tiles)
            except OSError:
                pass
        return out
    use = mode in ('template', 'exact')
    key = None
    if use and mode == 'template' and template is not None:
        frames = 1 + (geometry['frames'] - 1) // 4
        dev = torch.device(device)
        if dev.type == 'cuda' and dev.index is None:
            dev = torch.device('cuda', torch.cuda.current_device())
        key = (id(vae), 1, geometry['height'], geometry['width'], frames, vae.dtype, dev, False)
        if key not in fast_vae._I2V_TEMPLATES and Path(template).is_file():
            try:
                fast_vae._I2V_TEMPLATES[key] = torch.load(template, map_location=device, weights_only=True)
            except (OSError, RuntimeError, ValueError):
                pass
        loaded = key in fast_vae._I2V_TEMPLATES
    # template / exact encode only the first frame's prefix (the zero tail from the
    # template or the encoder's fixed point): no full-length video on the GPU
    encoder = (lambda frame: fast_vae.encode_i2v_condition(vae, frame, geometry['frames'], mode)) if use else None
    out = sampling.image_condition(vae, vae_config, image, geometry['frames'], geometry['height'],
                                   geometry['width'], device, first_frame_encoder=encoder)
    if key is not None and not loaded and key in fast_vae._I2V_TEMPLATES:
        try:
            Path(template).parent.mkdir(parents=True, exist_ok=True)
            partial = Path(str(template) + '.partial')
            torch.save(fast_vae._I2V_TEMPLATES[key].cpu(), partial)
            partial.replace(template)
        except OSError:
            pass
    return out


@torch.no_grad()
def vae_decode(vae, vae_config, latents, mode):
    """[F, H, W, 3] uint8 frames on the CPU (VideoProcessor.postprocess_video rounding)."""
    from .prism_model import fast_vae, sampling
    if mode in ('fast', 'fast_low_vram'):
        mean, std = sampling.latent_stats(vae_config, vae_config['z_dim'], latents.device, latents.dtype)
        frames = fast_vae.fast_decode_frames(vae, latents * std + mean, low_vram=mode == 'fast_low_vram', pil=False)
        return torch.from_numpy(frames[0])
    video = sampling.decode_video(vae, vae_config, latents, tiling=mode == 'tiled')
    frames = sampling.frames_uint8(video)
    del video
    return frames


def write_mp4(frames, audio, sample_rate, fps, path):
    """The H3 output path (diffusers ``encode_video``: PyAV libx264 yuv420p +
    AAC). Mono Prism audio is written as two identical channels, as that writer
    requires stereo. Falls back to the same PyAV calls when the installed
    diffusers has no audio-capable ``encode_video``."""
    import inspect
    stereo = audio.reshape(1, -1).float().cpu().expand(2, -1).contiguous()
    try:
        from diffusers.utils.export_utils import encode_video
        if 'audio' in inspect.signature(encode_video).parameters:
            encode_video(frames, fps=fps, output_path=str(path), audio=stereo, audio_sample_rate=sample_rate)
            return 'diffusers.encode_video'
    except ImportError:
        pass
    import av
    container = av.open(str(path), mode='w')
    try:
        stream = container.add_stream('libx264', rate=int(fps))
        stream.width, stream.height, stream.pix_fmt = frames.shape[2], frames.shape[1], 'yuv420p'
        audio_stream = container.add_stream('aac', rate=sample_rate)
        audio_stream.codec_context.sample_rate = sample_rate
        audio_stream.codec_context.layout = 'stereo'
        audio_stream.codec_context.time_base = __import__('fractions').Fraction(1, sample_rate)
        for frame in frames.numpy():
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format='rgb24')):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        samples = (torch.clip(stereo.t(), -1.0, 1.0) * 32767.0).to(torch.int16)
        frame_in = av.AudioFrame.from_ndarray(samples.contiguous().reshape(1, -1).numpy(), format='s16', layout='stereo')
        frame_in.sample_rate = sample_rate
        cc = audio_stream.codec_context
        resampler = av.audio.resampler.AudioResampler(format=cc.format or 'fltp', layout=cc.layout or 'stereo',
                                                      rate=cc.sample_rate or sample_rate)
        pts = 0
        for rframe in resampler.resample(frame_in):
            if rframe.pts is None:
                rframe.pts = pts
            pts += rframe.samples
            rframe.sample_rate = sample_rate
            container.mux(audio_stream.encode(rframe))
        for packet in audio_stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return 'pyav'


@torch.no_grad()
def kv_spill_path(request):
    """Where spilled teacher K/V layers go: beside the request's artifacts (the
    output drive), else the temporary directory. Removed when sampling ends (and
    with the process: sampling._KVSpill); files an older build left are removed here."""
    import tempfile
    base = Path(request['artifacts']) if request.get('artifacts') else Path(tempfile.gettempdir())
    for stale in base.glob('prism-kv-spill-*.bin'):
        try:
            stale.unlink()  # an open one (a live worker, Windows) cannot be removed
        except OSError:
            pass
    return str(base / ('prism-kv-spill-%d.bin' % os.getpid()))


class _Preload:
    """CPU-side work the video worker would otherwise do in line, started in a
    thread while the experts load: torch.compile's source hash (torch_key, ~3 s on
    the first compile of every process), the diffusers import (~12-18 s: its
    autoencoders package imports every model family, transformers and peft) and the
    Wan VAE weights on the CPU, moved to the GPU at decode."""

    def __init__(self, prepared):
        import threading
        self.prepared = prepared
        self.vae = None
        self.error = None
        self.seconds = {}
        self.thread = threading.Thread(target=self._run, name='prism-preload', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            tick = time.perf_counter()
            try:
                from torch._inductor.codecache import torch_key
                torch_key()
            except Exception:  # noqa: BLE001 - an optimisation only
                pass
            self.seconds['torch_key'] = time.perf_counter() - tick
            tick = time.perf_counter()
            from . import prism_runtime as runtime
            self.vae = runtime.load_vae(self.prepared, torch.device('cpu'))
            self.seconds['vae_cpu_load'] = time.perf_counter() - tick
        except Exception as error:  # noqa: BLE001 - decode falls back to loading in line
            self.error = repr(error)

    def take_vae(self, device):
        self.thread.join()
        vae, self.vae = self.vae, None
        if vae is None:
            return None
        module, config = vae
        return module.to(device), config


def model_key(request):
    """What a cached PrismModel must share with a request to be reused."""
    policy = request['policy']
    manifest = Path(request['prepared']) / 'manifest.json'
    stat = manifest.stat()
    fields = ('variant', 'resident_units', 'pin_bytes', 'prefetch', 'swap_roots', 'sparsity', 'cdf', 'lean', 'sol',
              'sol_beta')
    return json.dumps(dict(prepared=str(Path(request['prepared']).resolve()), manifest=[stat.st_size, stat.st_mtime_ns],
                           **{k: policy.get(k) for k in fields}), sort_keys=True)


def video(request, resident=None):
    from . import prism_runtime as runtime
    from .prism_model import sampling
    from .sampling_progress import SamplingProgress
    torch.set_num_threads(8)
    started = time.perf_counter()
    metrics = dict(success=False, stage='video', phase='load', geometry=request.get('geometry'),
                   recipe=request['recipe'], policy=request['policy'], resident=resident is not None)
    save(request['metrics'], metrics)
    model = None
    keep = request['policy'].get('resident') or {}
    vae_key = str(runtime.shared(request['prepared'], 'vae'))
    cached_vae = resident is not None and resident.vae is not None and resident.vae['key'] == vae_key
    request = dict(request, _preload=None if cached_vae else _Preload(request['prepared']), _resident=resident,
                   _vae_key=vae_key)
    guard = None
    memory_log = None
    try:
        guard = MemoryGuard(configure_memory(request, metrics))
        policy, recipe = request['policy'], request['recipe']
        clamp_locked(policy, metrics)
        kernel_setup(policy)
        sage_tuning(policy, request['geometry'], metrics)
        save(request['metrics'], metrics)
        device = torch.device('cuda')
        conditioning = torch.load(request['conditioning'], map_location='cpu', weights_only=True)
        condition = conditioning['condition'].to(device)
        pe = conditioning['prompt_embeds'].to(device)
        ne = conditioning['negative_embeds'].to(device)
        geometry = request['geometry']
        steps = recipe['steps']
        layers = None

        def loading(expert, done, total):
            # an expert switch during sampling is part of sampling for the progress bar
            emit(event='model_load_phase', phase='Loading %s-noise video expert' % expert, done=done, total=total,
                 during_sampling=bool(getattr(model, 'sampling_started', False)) if model is not None else False)
        resume = request.get('resume_latents')
        if resume:
            # A completed sampling of this request (retry after a decode failure).
            saved = torch.load(resume, map_location='cpu', weights_only=True)
            if saved.get('seed') != request['seed'] or saved.get('geometry') != geometry or saved.get('recipe') != recipe:
                raise ValueError('Saved latents belong to a different request')
            return decode_and_write(request, metrics, saved['video'].to(device), saved['audio'].to(device), started,
                                    resumed=True)
        tick = time.perf_counter()
        key = model_key(request) if resident is not None else None
        model = resident.take_model(key) if resident is not None else None
        if model is not None:
            model.policy, model.progress, model.phases = policy, loading, []
            metrics['model_reused'] = sorted(model.loaded_experts())
        else:
            model = runtime.PrismModel(request['prepared'], device, policy=policy, progress=loading)
        model.sampling_started = False
        if resident is not None:
            model.keep_phases = bool(keep.get('experts'))
        layers = model.configs['video_dit']['num_layers']
        metrics['root_load_seconds'] = time.perf_counter() - tick
        progress = SamplingProgress(steps, layers)
        load_seconds = {}

        def phase(expert):
            tick = time.perf_counter()
            result = model.activate(expert)
            preload = request.get('_preload')
            if preload is not None:
                # diffusers' from_pretrained switches torch's default dtype while it
                # loads; a torch.compile frame built meanwhile fails its global-state
                # guard (seen once with cold reads). The load overlapped the expert's.
                preload.thread.join()
            load_seconds[expert] = time.perf_counter() - tick
            metrics['load_seconds'] = load_seconds
            save(request['metrics'], metrics)
            if expert == 'high' or not progress.completed:
                emit(event='loaded', seconds=time.perf_counter() - started, resident_cache_hit=False)
            return result

        def layer(step, branch, block, total):
            guard.check()
            # A full-CFG step runs two passes; show them as one step's 40 layers.
            cfg_steps = recipe['cfg_steps']
            if recipe['cfg_scale'] != 1.0 and (cfg_steps in ('all', None) or step < cfg_steps):
                block = (block + 1) // 2 + (total // 2 if branch == 'uncond' else 0)
            progress.layer(max(1, min(total, block)))

        step_seconds = []

        def step_done(index, seconds):
            if memory_log is not None:
                memory_log.complete(seconds)
            guard.step('step %d' % index)
            step_seconds.append(seconds)
            progress.complete(seconds)
            metrics.update(phase='sample', completed_steps=len(step_seconds), step_seconds=list(step_seconds))
            save(request['metrics'], metrics)

        sched = runtime.scheduler(request['prepared'])
        audio_cfg_dir = runtime.shared(model.root, 'audio_vae') / 'config.json'
        audio_config = json.loads(audio_cfg_dir.read_text(encoding='utf-8'))
        import math as _math
        hop = int(_math.prod(audio_config['encoder_rates']))
        rate = audio_config['sample_rate']
        audio_samples = int(rate * geometry['frames'] / 24.0)
        torch.cuda.reset_peak_memory_stats()
        metrics['phase'] = 'sample'
        guard.arm()
        # Per-step allocator and WDDM local / non-local budget and usage (as MiniMax
        # H3): tells page-locked host memory apart from a real spill.
        audio_maps = None
        if (recipe.get('audio_teacher') or {}).get('calibrated') and recipe.get('audio_maps'):
            # Light: the student's K/V through the fitted maps (kv_calib), held in RAM
            from .prism_model import kv_calib
            tick = time.perf_counter()
            audio_maps = kv_calib.load_maps(recipe['audio_maps'])
            metrics['audio_maps'] = dict(path=recipe['audio_maps'], load_seconds=time.perf_counter() - tick,
                                         buckets=len({s['bucket'] for s in audio_maps['steps']}),
                                         meta={k: audio_maps['meta'].get(k) for k in ('form', 'bucket', 'lam', 'train')})
        from .sampling_memory import SamplingMemory
        memory_log = SamplingMemory(torch, Path(request['output']).with_suffix('.sampling-memory.json')).start()
        progress.start()
        model.sampling_started = True
        latents, audio_latents, log = sampling.sample(
            scheduler=sched, phase=phase, condition=condition, prompt_embeds=pe, negative_embeds=ne,
            seed=request['seed'], frames=geometry['frames'], height=geometry['height'], width=geometry['width'],
            audio_latent_dim=audio_config['latent_dim'], audio_samples=audio_samples, audio_hop=hop,
            boundary_ratio=model.configs['model_index'].get('boundary_ratio', 0.9), steps=steps,
            video_shift=recipe['shift'], audio_shift=recipe['audio_shift'], cfg_scale=recipe['cfg_scale'],
            cfg_steps=recipe['cfg_steps'], audio_cfg=recipe['audio_cfg'], device=device, lean=policy['lean'],
            chunk=policy.get('chunk', 16384), step_callback=step_done, layer_callback=layer,
            distilled=recipe.get('distilled', True), audio_teacher=recipe.get('audio_teacher'),
            kv_placement=policy.get('kv_placement') or 'gpu',
            vram_limit=(metrics.get('device_memory') or {}).get('effective_allocator_limit_bytes')
            or request.get('allocator_limit_bytes'),
            substep_cache=os.environ.get('FREEVIDEO_PRISM_SUBSTEP_CACHE', '1') != '0',
            kv_disk_layers=policy.get('kv_disk_layers', 0), kv_spill_path=kv_spill_path(request),
            fbcache=recipe.get('fbcache'), fbcache_max_skip=recipe.get('fbcache_max_skip') or 3,
            fbcache_placement=policy.get('fbcache_placement') or 'gpu',
            kv_width=model.configs['audio_dit'].get('dim'), kv_layers=sampling.interaction(model.configs)[2],
            audio_maps=audio_maps, audio_callback=progress.audio)
        del audio_maps
        metrics['weights'] = dict(distill=(model.manifest.get('distill') or {}).get('kind', 'merged'),
                                  student_lora=model.lora,
                                  teacher_weights='base' if model.lora else 'merged distilled (no base weights in this bundle)')
        metrics['sampling_log'] = log
        metrics['sampling_peak_allocated_bytes'] = max(row['peak_allocated_bytes'] for row in log)
        metrics['sampling_peak_reserved_bytes'] = max(row['peak_reserved_bytes'] for row in log)
        peaks(metrics, 'sample')
        emit(event='sample_finalize', phase='latent_validation')
        if not bool(torch.isfinite(latents).all() and torch.isfinite(audio_latents).all()):
            raise RuntimeError('Non-finite video/audio latents; refusing to save a corrupted result')
        artifacts = Path(request['artifacts']) if request.get('artifacts') else None
        if artifacts:
            emit(event='sample_finalize', phase='latent_save')
            # atomic: a retry after a decode failure resumes from a complete file only
            partial = artifacts / 'latents.pt.partial'
            torch.save(dict(video=latents.cpu(), audio=audio_latents.cpu(), seed=request['seed'], geometry=geometry,
                            recipe=recipe), partial)
            os.replace(partial, artifacts / 'latents.pt')
            metrics['latents_saved'] = str(artifacts / 'latents.pt')
            save(request['metrics'], metrics)
        emit(event='sample_finalize', phase='transformer_release')
        metrics['phases'] = list(model.phases)
        if resident is not None:
            resident.keep_model(key, model, bool(keep.get('experts')))
        else:
            model.close()
        metrics['phases'] = list(model.phases)
        model = None
        del condition, pe, ne
        torch.cuda.empty_cache()
        metrics.update(sampling_seconds=sum(step_seconds))
        memory_log.close()
        metrics['sampling_memory'] = memory_log.result()
        memory_log = None
        guard.close()  # decode has its own out-of-memory ladder under the allocator ceiling
        decode_and_write(request, metrics, latents, audio_latents, started)
    except BaseException as error:
        metrics.update(error=repr(error), work_seconds=time.perf_counter() - started)
        failure_kind(error, metrics)
        try:
            metrics['failure_peak_allocated_bytes'] = torch.cuda.max_memory_allocated()
        except BaseException:
            pass
        raise
    finally:
        if memory_log is not None:
            memory_log.close()
            metrics['sampling_memory'] = memory_log.result()
        if guard is not None:
            guard.close()
        if model is not None:
            try:
                metrics['phases'] = list(model.phases) + ([model.phase.stats()] if model.phase else [])
                model.close()
            except BaseException as error:
                metrics.setdefault('cleanup_errors', []).append(repr(error))
        save(request['metrics'], metrics)


@torch.no_grad()
def decode_and_write(request, metrics, latents, audio_latents, started, resumed=False):
    """Official decoders (Wan VAE, tiled when planned; DAC) and the MP4."""
    from . import prism_runtime as runtime
    from .prism_model import sampling
    policy, geometry = request['policy'], request['geometry']
    device = latents.device
    artifacts = Path(request['artifacts']) if request.get('artifacts') else None
    if True:  # (kept indented with the sampling stage it follows)
        metrics.update(phase='decode', decode_resumed=resumed)
        save(request['metrics'], metrics)
        emit(event='decode_phase', phase='Loading video VAE')
        tick = time.perf_counter()
        preload = request.get('_preload')
        resident = request.get('_resident')
        loaded = resident.take_vae(request['_vae_key'], device) if resident is not None else None
        if loaded is None and preload is not None:
            loaded = preload.take_vae(device)
        vae, vae_config = loaded or runtime.load_vae(request['prepared'], device)
        metrics['vae_load_seconds'] = time.perf_counter() - tick
        if preload is not None:
            metrics['preload'] = dict(preload.seconds, error=preload.error, used=loaded is not None)
        emit(event='decode_phase', phase='Decoding video')
        # Ladder: research fast decoder (``fast``; ``fast_low_vram`` time-slices every
        # stage, ~5.5 GiB at 720p x 205) -> official decode -> spatially tiled official.
        modes = list(dict.fromkeys([policy.get('vae_decode', 'fast'), 'fast_low_vram', 'tiled']))
        frames = None
        for attempt, mode in enumerate(modes):
            failed = False
            try:
                frames = vae_decode(vae, vae_config, latents, mode)
            except torch.cuda.OutOfMemoryError:
                if attempt + 1 == len(modes):
                    raise
                failed = True
            if not failed:
                metrics['vae_decode'] = mode
                break
            import gc
            gc.collect()
            torch.cuda.empty_cache()
            metrics.setdefault('vae_decode_oom', []).append(mode)
            emit(event='decode_phase', phase='Decoding video with less memory')
        if resident is not None:
            resident.keep_vae(request['_vae_key'], vae, vae_config, (request['policy'].get('resident') or {}).get('vae'))
        del vae
        torch.cuda.empty_cache()
        metrics['video_decode_seconds'] = time.perf_counter() - tick
        peaks(metrics, 'video_decode')
        emit(event='decode_phase', phase='Loading and decoding audio')
        tick = time.perf_counter()
        audio_vae = runtime.load_audio_vae(request['prepared'], device)
        rate = audio_vae.sample_rate
        audio = sampling.decode_audio(audio_vae, audio_latents)
        # The decoder emits whole hops; keep the samples that cover the video (research length).
        audio = audio[0].float().cpu().squeeze()[:int(rate * geometry['frames'] / 24.0)]
        del audio_vae
        torch.cuda.empty_cache()
        metrics['audio_decode_seconds'] = time.perf_counter() - tick
        peaks(metrics, 'audio_decode')
        if artifacts:
            import numpy as np
            np.save(artifacts / 'audio.npy', audio.numpy(), allow_pickle=False)
        emit(event='decode_phase', phase='Saving MP4 and audio')
        tick = time.perf_counter()
        destination = Path(request['output'])
        temporary = destination.with_name(destination.stem + '.partial' + destination.suffix)
        metrics['mp4_writer'] = write_mp4(frames, audio, rate, 24, temporary)
        temporary.replace(destination)
        metrics.update(encode_seconds=time.perf_counter() - tick, frames=int(frames.shape[0]),
                       audio_samples=int(audio.numel()), audio_sample_rate=rate, output=str(destination),
                       success=True, phase='complete', work_seconds=time.perf_counter() - started)


if __name__ == '__main__':
    main()
