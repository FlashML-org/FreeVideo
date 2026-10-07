"""Prism placement from live VRAM / RAM (no Torch import).

Prism samples with one video expert at a time (high-noise steps, then
low-noise steps), so only the active expert's 40 units are placed:

* VRAM = CUDA context + roots (active expert + audio) + 2 streaming slots +
  the activation working set + as many *resident* units as fit; the remaining
  units stream through ``offload.LayerOffloader`` with prefetch.
* The activation working set at 720p x 205 frames (187,200 video tokens of
  5120 bf16 values, T = 1.92 GB per hidden-state tensor) measured 9.3 T in
  the research block path and 5.3 T in the lean path (chunked projections and
  FFN, in-place RoPE and residuals, attention written over q; bit-identical
  INT8 latents), so the lean path is selected whenever the research path
  cannot keep the whole expert resident. Both include the
  per-head-chunk attention workspace (``PRISM_BSA_HEAD_CHUNK``).
* The Wan VAE decodes with the research fast decoder (low-VRAM time slicing
  below ``VAE_FAST_DECODE_BYTES``) and encodes the first-frame condition with
  the template shortcut; both fall back to the official / tiled VAE on OOM.
* The UMT5 text encoder runs on the GPU when it fits, otherwise on the CPU.
* Streamed units are pinned in host RAM as the RAM budget allows.
The constants are measured upper bounds from the H200 calibration runs.
"""
import math

GIB = 2**30
CONTEXT_BYTES = int(1.5 * GIB)          # CUDA context + loaded kernels + workspaces (H200: NVML - torch = 1.46 GB)
MARGIN_FRACTION = 0.06                  # allocator fragmentation headroom on top of the estimate
VAE_UNTILED_BYTES = int(22 * GIB)       # untiled 720p x 205-frame decode: 18.1 GiB allocated (H200 measurement)
VAE_ENCODE_UNTILED_BYTES = int(12 * GIB)  # untiled first-frame condition encode: 8.8 GiB allocated
VAE_FAST_DECODE_BYTES = int(11.5 * GIB)   # fast_vae decoder at 720p x 205: +9.8 GiB (research); else low-VRAM (+5.5)
# Worker heap, torch + CUDA host state and the CPU roots beside the K/V cache, the
# pinned copies and the staging slots: measured 3.6 GiB for the floor tier at 16/32
# GiB. (It read 11.3 GiB while the K/V cache sat in power-of-two pinned blocks.)
HOST_WORKING_BYTES = int(5 * GIB)
TEXT_ENCODER_WORK = int(1.5 * GIB)      # UMT5-XXL activations for 512 tokens + tokenizer
TEXT_STREAM_BYTES = int(2 * GIB)        # one UMT5 block (bf16 + fp32 wo) in flight + activations
PIN_MARGIN_BYTES = int(1.5 * GIB)
# The speed-first plan (head chunk 2 with prefetch) is tried when the estimate
# overruns the budget by at most this much. At 1.5 GiB every speed-first attempt
# on an RTX 4070 (estimated 1.45 GiB over) ran out of memory in the first step and
# cost a retry, and that card is compute-bound: head chunk 2 did not make its steps
# faster (Light, 336.8 / 177.2 s against 336.6 / 176.4 s).
SPEED_FIRST_SLACK = int(0.5 * GIB)
# Windows plans keep this much of their resident room free. Consumer cards are
# compute-bound with INT8 + Sage, so residency buys little there: an RTX 3090 Light
# step took 158.5 s with 16 of 40 units resident per expert (22.06 GiB dedicated
# peak, 3 allocator flush-retries) and 157.6 s with none (19.57 GiB, no retries);
# an RTX 4070 spent ~4% of a step streaming. The headroom absorbs another program
# growing its VRAM (a browser, a game) and the allocator's fragmentation.
DESKTOP_RESIDENT_HEADROOM = int(3.0 * GIB)
SUBSTEP_STAGING_BYTES = int(0.5 * GIB)   # two pinned staging buffers of the sub-step audio parts
WINDOWS_GROWTH_RESERVE = int(0.25 * GIB)  # as H3's desktop GPU reserve
# Windows video workers hold more private RAM besides the planned buffers (CUDA,
# cuDNN and driver host allocations): an RTX 4070 run planned for --ram-gib 24 was
# stopped by the RAM guard at 25.4 GiB of private working set in its first step.
WINDOWS_HOST_WORKING_EXTRA = 4 * GIB
RECIPE = dict(steps=6, cfg_steps=1, cfg_scale=2.0, audio_cfg=5.0, audio_substeps=1, shift=5.0, audio_shift=7.0,
              sparsity=0.75, cdf=0.2)


def video_tokens(width, height, frames):
    latent_frames = (frames - 1) // 4 + 1
    return latent_frames * (height // 16) * (width // 16)


def activation_bytes(tokens, *, lean, head_chunk=8, heads=40, dim=5120, exact=False, plain=False, park=False):
    """Peak transformer working set beside the weights (bytes). ``exact``: the
    original bf16 BSA kernel gathers q, k, v and its output for a head chunk; bf16
    (``plain``) blocks skip the INT8 activation copies, which about offsets it:
    bf16 + exact at 16 GiB peaked at 14.2 GiB against 14.5 planned, but 12
    resident units at 24 GiB ran out of memory by 0.35 GiB."""
    hidden = tokens * dim * 2
    attention = hidden * 2.0 * head_chunk / heads + 256 * 2**20
    if exact:
        attention += hidden * 0.25 * head_chunk / 8
    extra = 0
    if lean:
        # park: the residual stream waits in host memory during self-attention
        # (int8 paths park it from the QKV GEMMs on: their peak, x + xq + q, k, v)
        return int((3.5 if park else 4.5) * hidden + attention + 0.4 * GIB) + extra
    return int(8.8 * hidden + attention + 0.6 * GIB) + extra


def unit_sizes(manifest, expert):
    """Bytes per unit of one expert from the prepared manifest."""
    files = manifest['files']
    layers = manifest['layout']['video_blocks']
    fused = manifest['layout']['fused_blocks']
    folder = 'expert_high' if expert == 'high' else 'expert_low'
    sizes = []
    for index in range(layers):
        tag = '%02d' % index
        size = files['%s/blocks/%s.safetensors' % (folder, tag)]['bytes']
        size += files.get('lora_%s/blocks/%s.safetensors' % (expert, tag), {}).get('bytes', 0)
        if index < fused:
            size += files['audio/blocks/%s.safetensors' % tag]['bytes']
            size += files.get('bridge/%s.safetensors' % tag, {}).get('bytes', 0)
        sizes.append(size)
    return sizes


def root_bytes(manifest):
    # root.safetensors holds both experts' roots and the audio root; one expert is on the GPU.
    total = manifest['files']['root.safetensors']['bytes']
    return int(total * 0.52)


def spread(count, total):
    """``count`` resident indices spread over ``total`` layers (prefetch hides the rest)."""
    if count >= total:
        return list(range(total))
    if count <= 0:
        return []
    return sorted({min(total - 1, int(math.floor((i + 0.5) * total / count))) for i in range(count)})


def kv_cache_bytes(manifest, tokens, audio_dim=1536):
    """Audio-teacher v2a K/V cache: per fused layer FP8 keys and values over the video tokens."""
    return manifest['layout']['fused_blocks'] * 2 * tokens * audio_dim


def choose(manifest, *, vram_total, vram_free, ram_available, width, height, frames, vram_budget=None,
           ram_budget=None, force_lean=None, head_chunk=None, chunk=None, vae_tiling=None, audio_teacher=False,
           kv_placement=None, attention=None, fbcache=False, speed_first=False, pinned_limit=None, desktop=False,
           teacher_start=0, audio_maps=None):
    """Placement for one request. Budgets are bytes; ``vram_budget`` caps the
    usable VRAM (``--vram-gib``), otherwise live free VRAM is used. ``teacher_start``:
    the partial teacher's first fused layer (the student's hidden states wait for
    it); ``audio_maps``: (all, one step's) bytes of Light's K/V calibration maps,
    kept in pageable RAM with one step's bucket on the GPU during the student pass."""
    usable = min(vram_free, vram_budget) if vram_budget else vram_free
    if desktop:
        usable -= WINDOWS_GROWTH_RESERVE  # no expandable segments on Windows: fragmentation headroom
    capacity = min(vram_total, vram_budget) if vram_budget else vram_total
    tokens = video_tokens(width, height, frames)
    sizes = {expert: unit_sizes(manifest, expert) for expert in ('high', 'low')}
    fused_layers = manifest['layout']['fused_blocks']
    largest = max(max(s[:fused_layers]) for s in sizes.values())
    largest_tail = max((max(s[fused_layers:]) for s in sizes.values() if len(s) > fused_layers), default=0)
    roots = root_bytes(manifest)
    # Fused and tail units stream through separate offloaders, two CUDA slots each.
    fixed = CONTEXT_BYTES + roots + 2 * largest + 2 * largest_tail
    notes = []
    hc = head_chunk or 8
    expert_bytes = sum(sizes['high'])

    exact = attention == 'exact'
    plain = manifest.get('linear_weights') == 'bf16' or manifest.get('variant') == 'bf16'

    park = False

    def room_for(lean, chunk_heads):
        return usable - fixed - int(activation_bytes(tokens, lean=lean, head_chunk=chunk_heads, exact=exact,
                                                     plain=plain, park=park) * (1 + MARGIN_FRACTION))
    # The research block path when the whole expert stays resident beside it;
    # otherwise the lean path, which computes bit-identical INT8 latents
    # (measured) with about half the working set, so more units stay resident.
    lora = (manifest.get('distill') or {}).get('kind') == 'lora'
    if lora:
        # the student root (LoRA merged) sits beside the base root; the LoRA
        # branch adds bf16 chunk temporaries (LN rows, low-rank products, updates)
        fixed += roots + int(0.3 * GIB)
    lean = room_for(False, hc) < max(expert_bytes, sum(sizes['low'])) if force_lean is None else bool(force_lean)
    if lora and force_lean is None:
        lean = True  # the student LoRA runs in the lean block path
    room = room_for(lean, hc)
    prefetch = True
    rope_chunk = None
    overrun = 0
    if room < 0 and head_chunk is None and lean:
        # first: park the residual stream in host memory during self-attention
        park = True
        room = room_for(lean, hc)
        notes.append('Residual stream parked in host memory during attention')
    if room < 0 and head_chunk is None:
        # Small GPUs (measured: the floor tier fits a 12 GiB cap this way at
        # 1.15x the step time): smaller attention head chunks, one streaming
        # slot per offloader (no prefetch), 4096-row chunks, smaller RoPE chunks.
        # ``speed_first`` (the first attempt of a request) stops at head chunk 2 with
        # prefetch when the estimate overruns by at most SPEED_FIRST_SLACK: the
        # estimate is conservative, and the allocator ceiling and the shared-memory
        # guard turn a real overrun into a retry with this conservative plan.
        for candidate in (4, 2, 1):
            if speed_first and candidate < 2:
                break
            hc = candidate
            room = room_for(lean, hc)
            if room >= 0:
                break
        if room < 0 and speed_first and room >= -SPEED_FIRST_SLACK:
            chunk = chunk or 4096
            rope_chunk = 1 << 24
            overrun = -room
            room = 0
            notes.append('Speed-first plan: estimated %.2f GiB over the budget; retried with the conservative '
                         'plan if it runs out of memory or spills' % (overrun / GIB))
        if room < 0:
            prefetch = False
            fixed -= largest + largest_tail
            if lora:
                fixed -= roots  # swap_roots: one root variant on the GPU
            chunk = chunk or 4096
            rope_chunk = 1 << 24
            room = room_for(lean, hc) + int(0.4 * GIB)
        notes.append('Small-GPU settings: head chunk %d%s' % (hc, '' if prefetch else ', no prefetch, 4096-row chunks'))
    kv = kv_cache_bytes(manifest, tokens) if audio_teacher else 0
    maps_all, maps_step = audio_maps or (0, 0)
    room -= int(maps_step)  # on the device through the conditional student pass
    states = tokens * 5120 * 2 if teacher_start else 0  # student hidden states for the partial teacher
    placement = None
    if audio_teacher:
        # Keep the cache on the GPU only when a useful share of the expert stays
        # resident beside it; otherwise pinned host memory, streamed per layer.
        placement = kv_placement or ('gpu' if room - kv >= 0.25 * expert_bytes else 'host')
        if placement == 'gpu':
            room -= kv + states  # alive through the teacher pass's video blocks
        # 'host': one layer in flight (FP8 upload + bf16 pair, ~2.3 GB at 720p) is
        # below the video blocks' working set, so it does not raise the peak.
    # First-block cache: two video residuals per CFG branch plus a snapshot and a
    # spare (FBCache.BUFFERS hidden states, 1.9 GB each at 720p x 205), on the GPU
    # when that leaves a useful share of the expert resident, else in host memory.
    fb_bytes = 6 * tokens * 5120 * 2 if fbcache else 0
    fb_placement = None
    if fbcache:
        fb_placement = 'gpu' if room - fb_bytes >= 0.25 * expert_bytes else 'host'
        if fb_placement == 'gpu':
            room -= fb_bytes
    feasible = room >= 0
    if not feasible:
        notes.append('Estimated working set exceeds the VRAM budget by %.2f GiB; expect out-of-memory'
                     % (-room / GIB))
    if desktop and room > 0:
        # Windows has no expandable segments and shares the card with the desktop: leave
        # part of the resident room free (DESKTOP_RESIDENT_HEADROOM). An RTX 3090 with 16
        # resident units per expert peaked at 22.58 of its 23.0 GiB dedicated budget.
        room = max(0, room - DESKTOP_RESIDENT_HEADROOM)
    resident = {}
    for expert in ('high', 'low'):
        # Resident units are spread over the expert, so count the units actually
        # picked (fused units are larger than tail units: a count sized from the
        # smallest ones overcommitted a 24 GiB bf16 plan by 1.7 GiB).
        cost = (lambda i: int(sizes[expert][i] * 1.05)) if plain else (lambda i: sizes[expert][i])
        count = len(sizes[expert])
        while count and sum(cost(i) for i in spread(count, len(sizes[expert]))) > max(0, room):
            count -= 1
        resident[expert] = spread(count, len(sizes[expert]))
    streamed = max(sum(s for i, s in enumerate(sizes[e]) if i not in set(resident[e])) for e in ('high', 'low'))
    host = ram_budget if ram_budget else ram_available
    host_kv = kv if placement == 'host' else 0
    if fb_placement == 'host':
        host -= fb_bytes  # registered host buffers, held for the whole request
    # The pinned copies take what the working set, the K/V cache and the staging
    # slots leave, less a margin; the remaining streamed units are read from the
    # prepared files every pass (overlapped with compute, one unit ahead).
    free = int(host) - HOST_WORKING_BYTES - PIN_MARGIN_BYTES - (WINDOWS_HOST_WORKING_EXTRA if desktop else 0)
    free -= int(maps_all) + (states if placement == 'host' else 0)  # pageable
    staging = 0
    if free - host_kv < streamed:
        # Units read from disk pass through pinned staging slots (two per offloader,
        # rounded up by the pinned allocator).
        staging = 2 * int(1.25 * (largest + largest_tail))
    kv_disk = 0
    if host_kv and free - staging < host_kv:
        # The first K/V layers go to a file beside the output (two pinned slabs
        # write and read them); the audio sub-steps hold the first layers on the
        # GPU, so these are read back once per step where VRAM allows.
        layer = host_kv // fused_layers
        in_ram = max(0, min(fused_layers, (free - staging - 2 * layer) // layer))
        kv_disk = fused_layers - in_ram
        host_kv = in_ram * layer + 2 * layer
        notes.append('%d of the %d teacher K/V layers (%.1f GB) are kept in a file beside the output'
                     % (kv_disk, fused_layers, kv_disk * layer / 1e9))
        if free - staging < host_kv:
            notes.append('The RAM allowance is below the minimum working set (%.1f GiB needed)'
                         % ((HOST_WORKING_BYTES + PIN_MARGIN_BYTES + staging + host_kv) / GIB))
    pin = max(0, min(streamed, free - host_kv - staging))
    if pinned_limit is not None:
        # Windows: page-locked host memory (pinned units, registered K/V slabs,
        # staging slots, host step-cache buffers) is charged to the WDDM non-local
        # ("shared GPU memory") budget, about half of RAM. Past it, registration
        # fails with a CUDA out-of-memory error. Fewer pinned units first (they are
        # read from disk instead), then teacher K/V layers to the spill file.
        fb_host = (fb_bytes if fb_placement == 'host' else 0) + (tokens * 5120 * 2 if park else 0) \
            + SUBSTEP_STAGING_BYTES
        if pin < streamed or pin + host_kv + fb_host > pinned_limit:
            staging = max(staging, 2 * int(1.25 * (largest + largest_tail)))
        room_locked = max(0, int(pinned_limit) - staging - fb_host)
        if pin + host_kv > room_locked:
            before = pin
            pin = max(0, min(pin, room_locked - host_kv))
            notes.append('Pinned units cut from %.1f to %.1f GB by the Windows shared-GPU-memory budget (%.1f GB)'
                         % (before / 1e9, pin / 1e9, pinned_limit / 1e9))
            if host_kv > room_locked:
                layer = kv // fused_layers
                in_lock = max(0, min(fused_layers, (room_locked - 2 * layer) // layer))
                more = fused_layers - in_lock
                if more > kv_disk:
                    kv_disk = more
                    host_kv = in_lock * layer + 2 * layer
                    notes.append('%d of the %d teacher K/V layers go to the spill file to stay inside the '
                                 'Windows shared-GPU-memory budget' % (kv_disk, fused_layers))
    if pin < streamed:
        notes.append('%.1f GB of the streamed units are read from disk every pass' % ((streamed - pin) / 1e9))
    text_bytes = sum(row['bytes'] for name, row in manifest['files'].items()
                     if name.startswith('text_encoder/') and name.endswith('.safetensors'))
    # Resident on the GPU when it fits, else streamed to it one block at a time
    # (~0.5 GB; same embeddings as resident), else on the CPU.
    text_device = ('cuda' if usable - CONTEXT_BYTES >= text_bytes + TEXT_ENCODER_WORK else
                   'stream' if usable - CONTEXT_BYTES >= TEXT_STREAM_BYTES else 'cpu')
    # What the ComfyUI resident worker may keep between requests (prism_resident):
    # both experts only when every unit is GPU-resident and both resident sets fit
    # at once; the text encoder on the GPU beside them when that fits too, else in
    # the RAM the plan leaves over; the Wan VAE on the GPU with the experts, else in RAM.
    both = sum(sizes['high'][i] for i in resident['high']) + sum(sizes['low'][i] for i in resident['low'])
    keep_experts = streamed == 0 and room >= both
    leftover_ram = free - host_kv - staging - pin
    keep_text = ('gpu' if keep_experts and text_device == 'cuda' and room - both >= text_bytes + TEXT_ENCODER_WORK
                 else 'cpu' if leftover_ram >= text_bytes + PIN_MARGIN_BYTES else None)
    resident_keep = dict(experts=bool(keep_experts), text_encoder=keep_text, vae='gpu' if keep_experts else 'cpu',
                         leftover_ram_bytes=int(leftover_ram))
    scale = tokens / video_tokens(1280, 720, 205)  # VAE working sets grow with the canvas
    tiling = vae_tiling if vae_tiling is not None else usable < VAE_UNTILED_BYTES * max(1.0, scale)
    decode = 'fast' if usable - CONTEXT_BYTES >= VAE_FAST_DECODE_BYTES * max(1.0, scale) else 'fast_low_vram'
    encode_tiling = vae_tiling if vae_tiling is not None else usable < VAE_ENCODE_UNTILED_BYTES * max(1.0, scale)
    return dict(
        variant=manifest.get('variant'), lean=bool(lean), chunk=int(chunk or 16384), head_chunk=hc,
        prefetch=prefetch, rope_chunk_elems=rope_chunk,
        swap_roots=bool(lora and (room < 2 * GIB or not prefetch)),
        resident_units=resident, pin_bytes=int(pin), kv_placement=placement, kv_cache_bytes=int(kv),
        kv_disk_layers=int(kv_disk), fbcache_placement=fb_placement, fbcache_bytes=int(fb_bytes),
        resident=resident_keep, speed_first=bool(speed_first), estimated_overrun_bytes=int(overrun),
        pinned_limit_bytes=None if pinned_limit is None else int(pinned_limit),
        locked_host_bytes=int(pin + (host_kv if placement == 'host' else 0) + staging
                              + (fb_bytes if fb_placement == 'host' else 0)),
        # page-locked bytes besides the weight pins (K/V slabs, host step cache,
        # staging slots, sub-step staging): the worker reserves them in Windows'
        # non-local budget before pinning weights
        locked_other_bytes=int((host_kv if placement == 'host' else 0) + staging
                               + (fb_bytes if fb_placement == 'host' else 0) + SUBSTEP_STAGING_BYTES
                               + (tokens * 5120 * 2 if park else 0)),
        park_residual=bool(park),
        vae_tiling=bool(tiling), vae_encode_tiling=bool(encode_tiling), vae_decode=decode, vae_encode='template',
        text_encoder_device=text_device,
        allocator_limit_bytes=int(capacity - CONTEXT_BYTES) if vram_budget else None,
        feasible=feasible, notes=notes,
        estimate=dict(video_tokens=tokens, usable_vram_bytes=int(usable), fixed_bytes=int(fixed),
                      activation_bytes=activation_bytes(tokens, lean=lean, head_chunk=hc, exact=exact, plain=plain,
                                                        park=park),
                      resident_room_bytes=int(room), largest_unit_bytes=int(largest),
                      expert_bytes={e: sum(s) for e, s in sizes.items()},
                      resident_bytes={e: sum(sizes[e][i] for i in resident[e]) for e in resident},
                      streamed_bytes=int(streamed), disk_bytes=int(max(0, streamed - pin)),
                      host_staging_bytes=int(staging), host_working_bytes=HOST_WORKING_BYTES))
