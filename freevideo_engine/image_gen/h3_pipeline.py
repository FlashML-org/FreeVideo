"""In-process H3 Image pipeline: text encoding, residency planning, denoising, tiled decoding.

Text goes through the video model's text encoder in its short-lived worker
(``freevideo_engine encode``), so that encoder is gone from the card and the
host before the DiT is loaded. The DiT streams its int8 blocks through the
engine's ``StreamedStack``; how many stay resident comes from ``plan_image``
against the card's budget. Requests below the 4 MP floor are generated at 4 MP
with the same proportions and are not scaled back.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import torch

from . import h3_dit
from .h3_weights import load_h3_dit

MAX_REFERENCES = 16
# The released edit pipeline's reference sizes: the VAE copy keeps its size while the short edge is within
# [1024, 2048] and is scaled to the nearer bound otherwise; the vision copy has a short edge of exactly 1024. A
# resized side rounds to 32. References are never resized to the output size.
REFERENCE_SHORT_EDGE = (1024, 2048)
VISION_SHORT_EDGE = 1024
SIDE_MULTIPLE = 32
REFERENCE_CACHE_RECIPE = 'h3-image-reference-moments-v1'


def free_host_memory():
    """Return the request's weights to the host: the hooks make a stack a reference cycle, and torch keeps freed locked pages cached, so the next request in the same process would find them still charged."""
    import gc
    from ..torch_compat import empty_host_cache
    gc.collect()
    torch.cuda.empty_cache()
    empty_host_cache(torch)


class KeptModels:
    """The DiT stack and the VAE between requests of one worker.

    Keeping them saves loading the 21 GB of weights and the pinned planes again, so the next request starts
    sampling after its encoding. A model is kept only while the host can still hold it without squeezing the
    shared encoder and the video model: the cgroup (or the machine) must keep ``HOST_MARGIN`` free after the
    weights, and at least the floor the next picture asks for (``host_floor``), and a card below ``MIN_KEEP_VRAM`` gives its resident blocks back to the shared GPU (the host copy
    stays). Anything that does not fit is closed, which is what every request did before.
    """
    HOST_MARGIN = 10 * 2**30
    MIN_KEEP_VRAM = 12 * 2**30
    budget = None

    def __init__(self):
        self.items = {}

    def take(self, role, key, host_free=None):
        """The kept model of ``role`` for this request, or None (it is closed when it does not match).

        ``host_free`` is the host memory available now, with the kept planes still locked. When it no longer covers
        the working headroom and the staging reserve, the locked planes are given back and the caller loads again:
        that load re-decides how many layers to lock from what the host has now, not what it had at the first image.
        """
        entry = self.items.pop(role, None)
        if entry is None:
            return None
        if entry[0] != key:
            self.close_entry(entry)
            return None
        if role == 'dit' and host_free is not None and host_free < host_floor():
            self.close_entry(entry)
            return None
        return entry[1]

    @staticmethod
    def close_entry(entry):
        stack = entry[2]
        if stack is not None:
            stack.close()

    def host_room(self):
        from .image_stream import cgroup_pin_cap
        cap = cgroup_pin_cap(reserve=0)
        if cap is not None:
            return cap
        try:
            import psutil
            return psutil.virtual_memory().available
        except ImportError:
            return 0

    def keep(self, role, key, value, stack):
        """Hold ``value`` for the next request; False when it does not fit (the caller closes it)."""
        held = stack.pinned_bytes if stack is not None else 0
        total = torch.cuda.get_device_properties(0).total_memory
        # A card that is small, or has only a small part free to this request, keeps nothing on the device.
        small = min(total, self.budget or total) < self.MIN_KEEP_VRAM
        if role == 'vae' and small:
            return False
        # The same floor take() applies: a model the next picture would give back anyway is not kept.
        if role == 'dit' and self.host_room() < max(self.HOST_MARGIN, host_floor()):
            return False
        if role == 'dit':
            if small:
                stack.set_resident(0)
                # The embedders and tables are about 1 GiB on the card; the next encode needs that room.
                small_parts_to_host(value[0])
        old = self.items.pop(role, None)
        if old is not None:
            self.close_entry(old)
        self.items[role] = (key, value, stack)
        return True

    def give_back_device(self):
        """Hand the card back before a stage that was not planned beside the kept models (the reference encoder):
        the kept DiT stack keeps its host copy with no block on the card, and the kept decoder is let go."""
        dit = self.items.get('dit')
        if dit is not None:
            dit[2].set_resident(0)
        vae = self.items.pop('vae', None)
        if vae is not None:
            self.close_entry(vae)
        if dit is not None or vae is not None:
            torch.cuda.empty_cache()

    def close(self):
        for entry in list(self.items.values()):
            self.close_entry(entry)
        self.items.clear()


# What a resident encode needs free on the card when the request starts: the encoder's own minimum, the CUDA context
# the resident worker keeps there, and what the picture process needs beside them.
ENCODER_MIN_FREE = 5 * 2**30
RESIDENT_CONTEXT = int(0.8 * 2**30)
PICTURE_CONTEXT = int(0.75 * 2**30)
PICTURE_SHARE = int(2.2 * 2**30)
RESIDENT_ENCODE_MIN_FREE = ENCODER_MIN_FREE + RESIDENT_CONTEXT + PICTURE_SHARE


def resident_encode_decision(free_now, placed=0):
    """(use the resident encoder?, reason) from the free memory the driver reports, plus ``placed``: the bytes of the
    encoder the resident worker already holds on the card (kept there after the last picture, or put there while the
    page was being set up), which the encoding uses where they are."""
    if free_now is None:
        return True, dict(reason='free memory unreadable; resident kept')
    parts = dict(free_gib=round(free_now / 2**30, 2), need_gib=round(RESIDENT_ENCODE_MIN_FREE / 2**30, 2),
                 encoder_gib=round(ENCODER_MIN_FREE / 2**30, 2), context_gib=round(RESIDENT_CONTEXT / 2**30, 2),
                 picture_gib=round(PICTURE_SHARE / 2**30, 2))
    if placed:
        parts['placed_gib'] = round(placed / 2**30, 2)
    if free_now + placed < RESIDENT_ENCODE_MIN_FREE:
        return False, dict(parts, reason='free memory below the resident encoder, its context and the picture share')
    return True, dict(parts, reason='free memory covers the resident encoder, its context and the picture share')


# The text encoder's bytes on the card when all of it is there (15.2 GiB measured for the 32B encoder, rounded up):
# what a keep decision counts for an encoder that is not on the card yet.
ENCODER_CARD_ESTIMATE = 16 * 2**30
# Room a kept encoder leaves beyond the picture's plan, for free memory that moves between the decision and the plan.
ENCODER_KEEP_MARGIN = 2**30
# The DiT's shape from the last load in this process, by file: the keep decision is made before the DiT loads.
_SHAPES = {}


def keep_encoder_decision(*, free_now, placed, shape, budget_cap, reserved, vae_held, width, height, sequence_rows,
                          context=0, host_free=None):
    """(keep the text encoder on the card after this picture's encoding?, facts).

    The encoder stays only when the picture's planned placement (layers on the card, host mode, decoder tiling) is
    the same with the encoder on the card as without it, so the picture is made exactly as it would be after the
    encoder left; a saved conditioning is then used and the next encoding needs no load. ``free_now`` is the card's
    free memory now, ``placed`` the encoder bytes on the card now, ``reserved`` what this process holds,
    ``context`` the CUDA context this process has yet to create; ``budget_cap`` is the request's card limit."""
    if free_now is None or shape is None:
        return False, dict(reason='free memory or the picture model\'s shape unknown')
    from .image_policy import plan_image
    canvas_h, canvas_w = h3_dit.canvas_size(height, width)
    encoder_bytes = placed or ENCODER_CARD_ESTIMATE

    def placement(free):
        budget = max(0, min(budget_cap, free + reserved) - vae_held)
        plan = plan_image(gpu_budget_bytes=budget, host_available_bytes=host_free, width=canvas_w, height=canvas_h,
                          shape=shape, sequence_rows=sequence_rows)
        # What this picture runs with. Not part of it: the plan's own text-encoder layers (H3 encodes in the
        # resident worker, never from this plan) and the DiT blocks left on the card after the picture
        # (dit_kept_layers: a block fewer costs the next picture well under a second, against seconds of encoder load).
        return (plan.dit_resident_layers, plan.vae_tiling, plan.vae_tile_size, plan.host_mode,
                plan.feasible), plan.dit_kept_layers

    released, released_after = placement(free_now + placed - context)
    kept, kept_after = placement(free_now - (0 if placed else encoder_bytes) - context - ENCODER_KEEP_MARGIN)
    facts = dict(free_gib=round(free_now / 2**30, 2), encoder_gib=round(encoder_bytes / 2**30, 2),
                 placed=bool(placed), resident_layers_kept=kept[0], resident_layers_released=released[0],
                 kept_after_kept=kept_after, kept_after_released=released_after)
    if kept != released:
        return False, dict(facts, reason='the picture would be placed differently with the encoder on the card')
    return True, dict(facts, reason='the picture is placed the same with the encoder on the card')


def placed_encoder_bytes(endpoint):
    """Card bytes of the text encoder in the resident worker at ``endpoint``; 0 when it holds none there or cannot say."""
    from ..resident_process import ENV, status
    held = status({ENV: endpoint})
    try:
        return sum(int(row.get('gpu_bytes') or 0) for row in held.get('models') or [] if row.get('role') == 'encoder')
    except (AttributeError, TypeError, ValueError):
        return 0


def _driver_free_bytes():
    """Free memory of the first card from the driver's tool; reading it with CUDA would put this process's context on the card
    before the encoder asks for its 5 GiB. None when it cannot be read."""
    import subprocess
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=10, check=True).stdout
        return int(float(out.split()[0]) * 2**20)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class Cancelled(Exception):
    pass


def bound_short_edge(width, height, low, high, multiple=SIDE_MULTIPLE):
    """(width, height) a reference is resized to for one consumer (the released pipeline's rule)."""
    short = min(width, height)
    if low <= short <= high:
        return width, height
    scale = (low if short < low else high) / short
    return (max(multiple, round(width * scale / multiple) * multiple),
            max(multiple, round(height * scale / multiple) * multiple))


def reference_copies(references):
    """[(vae copy, vision copy)] of PIL references: read upright, RGB, each copy sized for its consumer."""
    from PIL import Image, ImageOps
    copies = []
    for image in references:
        image = ImageOps.exif_transpose(image).convert('RGB')
        if min(image.size) < SIDE_MULTIPLE:
            raise ValueError(f'A reference image needs at least {SIDE_MULTIPLE} pixels on each side, got {image.size}.')

        def fit(low, high):
            size = bound_short_edge(*image.size, low, high)
            return image if image.size == size else image.resize(size, Image.LANCZOS)
        copies.append((fit(*REFERENCE_SHORT_EDGE), fit(VISION_SHORT_EDGE, VISION_SHORT_EDGE)))
    return copies


def reference_sequence_rows(copies):
    """Rows the references add to the transformer's sequence: their latent patches and their vision tokens."""
    rows = 0
    for index, (vae_copy, vision_copy) in enumerate(copies):
        width, height = vae_copy.size
        rows += (height // 16 // 2) * (width // 16 // 2)
        width, height = vision_copy.size
        # The vision block with its two markers, and the label ("Picture 1: " is five tokens, "Picture 10: " six).
        rows += round(height / 32) * round(width / 32) + 2 + 4 + len(str(index + 1))
    return rows


# What the streamed-only floor must leave free, beyond this process's CUDA context, for the request to be tried at
# all. Under an 8 GiB cap 16 photos (floor 7.99 GiB) ran out in the first step with 0.84 GiB held in pieces; with the
# context taken off the cap (7.25 GiB usable) 10 photos (floor 6.93 GiB, 0.32 GiB to spare) finished.
REFUSE_ALLOWANCE = 0.25 * 2**30


class ReferencesTooLarge(MemoryError):
    """The references cannot fit on this card even with every transformer block streamed."""


def plan_facts(plan, stack, *, budget, total, shared_card, capped, canvas, sequence_rows):
    """Numbers and fixed words of the placement this request was planned with, for the worker's ``plan`` row.

    The row reaches the service before the first step, so a report keeps it when the worker ends without
    answering (a native abort leaves nothing else).
    """
    facts = dict(canvas=[int(canvas[0]), int(canvas[1])], resident_layers=int(plan.dit_resident_layers),
                 kept_layers=None if plan.dit_kept_layers is None else int(plan.dit_kept_layers),
                 vae_tiling=bool(plan.vae_tiling), vae_tile_size=int(plan.vae_tile_size), host_mode=plan.host_mode,
                 feasible=bool(plan.feasible), planned_peak_bytes=int(plan.peak_allocated_bytes),
                 budget_bytes=int(budget), card_total_bytes=int(total), shared_card=bool(shared_card),
                 capped=bool(capped), sequence_rows=int(sequence_rows))
    try:
        facts['card_free_bytes'] = int(torch.cuda.mem_get_info()[0])
    except Exception:
        pass
    try:
        facts.update(pinned_layers=int(stack.pinned_layers), pinned_bytes=int(stack.pinned_bytes))
    except Exception:
        pass
    return facts


def card_budget(vram_gib):
    """Bytes of the card this request may use: the caller's limit less this process's CUDA context (which the
    allocator limit does not count), else the whole card."""
    total = torch.cuda.get_device_properties(0).total_memory
    return int(min(vram_gib * 2**30, total)) - PICTURE_CONTEXT if vram_gib else total


def usable_bytes(vram_gib):
    """What the allocator can really have: the capped budget, or the whole card less this process's CUDA context."""
    return card_budget(vram_gib) if vram_gib else card_budget(None) - PICTURE_CONTEXT


def denoise_floor_bytes(copies, width, height):
    """The denoising stage's peak with nothing resident: the least any plan for these references needs."""
    from .image_policy import ImageShape, stage_peaks
    canvas_h, canvas_w = h3_dit.canvas_size(height, width)
    return stage_peaks(ImageShape(), 0, 0, width=canvas_w, height=canvas_h,
                       sequence_rows=reference_sequence_rows(copies))['denoise']




# Host memory the request takes beyond the locked weights: the read-ahead staging, the unlocked layers' direct-read
# buffers and the encoder worker. Measured on a 31.7 GiB Windows machine under an 8 GiB card limit at 3.9-4.8 GiB
# outside the locked weights, on top of the 8 GiB working headroom the lock capacity already keeps.
HOST_STAGING_RESERVE = 5 * 2**30


def host_free_bytes():
    """Physical host memory available now, or None when it cannot be read."""
    try:
        from ..system import system_memory
        memory = system_memory()
        return int(memory.get('physical_available_bytes') or memory['available_bytes'])
    except Exception:
        return None


def host_floor():
    """Host memory that must stay available beside a kept DiT: the working headroom plus the staging reserve."""
    from ..system import HOST_WEIGHT_HEADROOM
    return HOST_WEIGHT_HEADROOM + HOST_STAGING_RESERVE


def host_pin_budget(free_bytes=None, reserve=HOST_STAGING_RESERVE):
    """Most bytes of weights to lock for this load, or None when host memory cannot be read.

    The lock capacity is read once, at load, and nothing allocated afterwards is charged to it; the staging
    reserve is charged here so a tight machine locks fewer layers instead of running out of memory later in
    the request. Locking changes transfer speed only, never the numbers.
    """
    if free_bytes is None:
        try:
            from ..system import system_memory
            memory = system_memory()
            free_bytes = memory.get('physical_available_bytes') or memory['available_bytes']
        except Exception:
            return None
    from ..system import HOST_WEIGHT_HEADROOM
    return max(0, int(free_bytes) - HOST_WEIGHT_HEADROOM - reserve)


def _tensor_bytes(value):
    """Bytes of the tensors held by a module, or by nested tuples, lists and dicts of them (a decoder object)."""
    if isinstance(value, torch.Tensor):
        return value.numel() * value.element_size() if value.is_cuda else 0
    if hasattr(value, 'parameters'):
        return sum(_tensor_bytes(t) for t in value.parameters())
    if isinstance(value, dict):
        value = value.values()
    elif hasattr(value, '__dict__') and not isinstance(value, (list, tuple)):
        value = vars(value).values()
    if isinstance(value, (list, tuple)) or hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
        return sum(_tensor_bytes(v) for v in value)
    return 0


def _encode(prompt, *, encoder, comfy_root, comfy_python, model_paths, vram_gib, ram_gib, vision_images=(),
            resident_endpoint=None, release_gpu=True):
    """Run the shared text encoder in its own process and return the saved conditioning."""
    import os
    import sys
    from ..resident_process import ENV
    # With the video worker alive the encoder is taken from (and left in) its ModelBank; a worker that is gone
    # makes ``child`` fall back to the short-lived process by itself.
    previous = os.environ.get(ENV)
    if resident_endpoint:
        os.environ[ENV] = resident_endpoint
    try:
        return _encode_once(prompt, encoder, comfy_root, comfy_python, model_paths, vram_gib, ram_gib, vision_images,
                            release_gpu=release_gpu)
    except BaseException as error:
        # The caller's classifier keeps only a code; the text of the failure goes to the worker's log.
        print('H3 encode failed: %s: %s' % (type(error).__name__, str(error)[-2000:]), file=sys.stderr, flush=True)
        raise
    finally:
        if resident_endpoint:
            if previous is None:
                os.environ.pop(ENV, None)
            else:
                os.environ[ENV] = previous


def _encode_once(prompt, encoder, comfy_root, comfy_python, model_paths, vram_gib, ram_gib, vision_images=(),
                 release_gpu=True):
    from .. import encode as encode_command
    import sys
    with tempfile.TemporaryDirectory(prefix='freevideo-h3img-') as directory:
        prompt_file = Path(directory) / 'prompt.txt'
        prompt_file.write_text(prompt, encoding='utf-8')
        out = Path(directory) / 'cond.pt'
        pictures = []
        for index, image in enumerate(vision_images):
            pictures.append(Path(directory) / f'picture-{index + 1}.png')
            image.save(pictures[-1], format='PNG', compress_level=1)
        encode_command.run(argparse.Namespace(
            prompt_file=prompt_file, out=out, encoder=encoder,
            comfy_root=Path(comfy_root) if comfy_root else None, model_paths=Path(model_paths) if model_paths else None,
            comfy_python=comfy_python or sys.executable, vram_gib=vram_gib, ram_gib=ram_gib, attention='auto',
            gpu_reserve_gib=None, ram_reserve_gib=None, release_gpu=release_gpu,
            # A kept encoder gives back only the encoding's working memory, which the picture plans with.
            empty_cache=not release_gpu, h3_image_references=pictures))
        return torch.load(out, map_location='cpu', weights_only=False)


def reference_moments(copies, vae_path, device, *, cache=True, cache_root=None, stats=None, before_load=None):
    """Posterior moments ([1, 48, h, w] float32 on the CPU) of each reference's VAE copy, read from or kept in the
    input cache the video references use; the encoder loads only when one is missing, after ``before_load()``."""
    import hashlib
    import numpy as np
    from .h3_vae import H3ImageEncoder, PIXEL_MEAN, PIXEL_STD
    store = None
    if cache:
        try:
            from ..media_encoding import _latent_cache
            store = _latent_cache(cache_root, Path(vae_path).parent.parent, device, recipe=REFERENCE_CACHE_RECIPE)
        except Exception:
            store = None
    out, keys, encoder, hits = [], [], None, 0
    work = Path(tempfile.mkdtemp(prefix='freevideo-h3ref-'))
    try:
        for vae_copy, _ in copies:
            pixels = np.asarray(vae_copy, dtype=np.uint8)
            pixels = pixels[:pixels.shape[0] // 16 * 16, :pixels.shape[1] // 16 * 16]
            key = 'h3-image-reference:%s:%dx%d' % (hashlib.sha256(np.ascontiguousarray(pixels).data).hexdigest(),
                                                   pixels.shape[1], pixels.shape[0])
            moments = None
            if store is not None:
                from ..media_encoding import _cached_latent
                moments = _cached_latent(store, key, work)
            if moments is None:
                if encoder is None:
                    if before_load is not None:
                        before_load()
                    encoder = H3ImageEncoder.load(str(vae_path), device)
                tensor = torch.from_numpy(np.ascontiguousarray(pixels)).to(device).permute(2, 0, 1)[None].float()
                mean = torch.tensor(PIXEL_MEAN, device=device).view(1, 3, 1, 1)
                std = torch.tensor(PIXEL_STD, device=device).view(1, 3, 1, 1)
                moments = encoder.moments((tensor / 255.0 - mean) / std).cpu()
                if store is not None:
                    from ..media_encoding import _remember_latent
                    _remember_latent(store, key, moments, work)
            else:
                hits += 1
            out.append(moments)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        del encoder
    if stats is not None:
        stats['reference_cache_hits'] = hits
    return out


def load_dit(dit_path, device, host_free, *, mapped=True):
    """(model, stack): the DiT with its small parts on the card and its blocks in a streamed stack that locks as
    much of the host as ``host_free`` allows; no block is on the card yet."""
    from .image_stream import StreamedStack, checkpoint_layer_source
    model = load_h3_dit(str(dit_path), 'cpu', mapped=mapped)
    for name, child in model.named_children():
        if name != 'blocks':
            child.to(device)
    model.adaln_t_table = model.adaln_t_table.to(device)
    source = checkpoint_layer_source(str(dit_path), list(model.blocks)) if mapped else None
    # Layers the host cannot keep locked are read from the file ahead of their
    # use through the staging buffer, one device slot (see LayerOffloader).
    pin_cap = host_pin_budget(host_free)
    stack = (StreamedStack(model.blocks, name='h3', device=device, weight_source=source,
                           prefetch=os.environ.get('FREEVIDEO_H3_DEVICE_PREFETCH') == '1',
                           host_prefetch=True, max_pin_bytes=pin_cap) if source is not None
             else StreamedStack(model.blocks, name='h3', device=device, max_pin_bytes=pin_cap))
    return model, stack


def picture_shape(model, stack):
    from .image_policy import ImageShape
    shape = ImageShape.measure(list(model.blocks), None)
    # Blocks the host did not keep read from the checkpoint file when used and hold no bytes between calls, so a
    # measure of the modules alone gives 0 for them and the plan puts every block on the card. The stack's layout
    # keeps their real sizes.
    return ImageShape(**{**shape.__dict__, 'steps': 6,
                         'dit_block_bytes': max(shape.dit_block_bytes, int(getattr(stack, 'layer_bytes', 0) or 0))})


def small_parts_to_host(model):
    """The embedders and tables (about 1 GiB) leave the card; the next picture puts them back."""
    for name, child in model.named_children():
        if name != 'blocks':
            child.to('cpu')
    model.adaln_t_table = model.adaln_t_table.to('cpu')


# The picture a warm-up prepares for: the default size, no references.
PREPARE_SIZE = (1024, 1024)


def prepare(dit_path, vae_path, *, kept, resident_endpoint=None, should_stop=lambda: False, stats=None):
    """Leave the DiT and the decoder in ``kept`` as a picture of the default size leaves them, before any prompt is
    known, so the next picture starts sampling right after its encoding. Returns what happened, as a word.

    The card keeps only what leaves the resident text encoder the room its encoding needs
    (``resident_encode_decision``), so the next encoding is never moved to a short process by this; with too little
    room the blocks stay in host memory. ``KeptModels.keep`` decides whether the host can hold them at all.
    ``should_stop()`` ends it between steps; what is already loaded stays kept, as the next picture would load it."""
    stats = stats if stats is not None else {}
    if kept is None:
        return 'no_kept'
    from .h3_vae import H3ImageDecoder
    from .image_policy import plan_image
    torch.set_grad_enabled(False)
    device = torch.device('cuda')
    placed = placed_encoder_bytes(resident_endpoint) if resident_endpoint else 0
    encoder_room = max(0, RESIDENT_ENCODE_MIN_FREE - placed) if resident_endpoint else 0
    host_free = host_free_bytes()
    canvas_h, canvas_w = h3_dit.canvas_size(*PREPARE_SIZE)
    tick = time.perf_counter()
    held = kept.items.get('dit')
    if held is not None and held[0] == str(dit_path):
        # Already kept: only blocks that were moved to the host for room go back to the card, from their locked
        # host copy; nothing is read from the file again.
        model, stack = held[1]
        free, _ = torch.cuda.mem_get_info()
        budget = max(0, min(card_budget(None), int(free + torch.cuda.memory_reserved())) - encoder_room)
        plan = plan_image(gpu_budget_bytes=budget, host_available_bytes=None, width=canvas_w, height=canvas_h,
                          shape=picture_shape(model, stack), sequence_rows=0)
        before = getattr(stack, 'resident', 0)
        if not should_stop() and (plan.dit_kept_layers or 0) > before:
            for name, child in model.named_children():
                if name != 'blocks':
                    child.to(device)
            model.adaln_t_table = model.adaln_t_table.to(device)
            stack.set_resident(plan.dit_kept_layers)
        stats.update(prepare_kept_before=before)
        state = 'kept' if getattr(stack, 'resident', 0) == before else 'returned'
    else:
        if host_free is not None and host_free < host_floor():
            return 'host_memory'
        free, _ = torch.cuda.mem_get_info()
        budget = max(0, min(card_budget(None), int(free + torch.cuda.memory_reserved())) - encoder_room)
        model, stack = load_dit(dit_path, device, host_free)
        try:
            plan = plan_image(gpu_budget_bytes=budget, host_available_bytes=host_free, width=canvas_w, height=canvas_h,
                              shape=picture_shape(model, stack), sequence_rows=0)
            if not should_stop():
                stack.set_resident(plan.dit_kept_layers or 0)
            if not kept.keep('dit', str(dit_path), (model, stack), stack):
                stack.close()
                free_host_memory()
                return 'host_memory'
        except BaseException:
            stack.close()
            raise
        state = 'prepared'
    kept.budget = budget
    del model, stack
    if not should_stop() and 'vae' not in kept.items:
        decoder = H3ImageDecoder.load(str(vae_path), device)
        if not kept.keep('vae', str(vae_path), decoder, None):
            del decoder
    torch.cuda.empty_cache()

    def short():
        return torch.cuda.mem_get_info()[0] + placed < RESIDENT_ENCODE_MIN_FREE

    # The plan is an estimate: what the card really has left decides, the decoder first, then the blocks.
    if resident_endpoint and short() and 'vae' in kept.items:
        kept.close_entry(kept.items.pop('vae'))
        torch.cuda.empty_cache()
    if resident_endpoint and short():
        model, stack = kept.items['dit'][1]
        stack.set_resident(0)
        small_parts_to_host(model)
        torch.cuda.empty_cache()
    stack = kept.items['dit'][2]
    stats.update(prepare_s=round(time.perf_counter() - tick, 2), prepare_budget_gib=round(budget / 2**30, 2),
                 prepare_resident=getattr(stack, 'resident', None), prepare_vae='vae' in kept.items)
    return 'stopped' if should_stop() else state


def generate(prompt, width, height, seed, references=None, progress_cb=None, cancel=None, *, dit_path, vae_path,
             encoder='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', comfy_root=None, comfy_python=None,
             model_paths=None, vram_gib=None, ram_gib=None, resident=None, cpu_noise=False, stats=None, mapped=True,
             preview=None, enter=None, reference_cache=True, reference_cache_root=None, resident_endpoint=None, kept=None,
             note=None):
    """Return a PIL image for the prompt.

    ``progress_cb(stage, done, total)`` is called with stage in encode, denoise, decode;
    ``cancel()`` returning true stops at the next step. ``mapped`` leaves the checkpoint's tensors as views of the mapped file, so a host
    with less memory than the 21 GB of weights loads them and pins what live memory allows.
    ``resident`` overrides the planned
    layer count. ``stats`` (a dict) receives timings, the plan and the peak memory.
    ``preview`` is the sampler's ``preview_callback`` (see ``step_preview``); it never changes the picture.
    ``enter(stage)`` names the stage a failure belongs to without moving the progress: the DiT loads after the
    text, so a load error would otherwise be filed under the text stage.
    ``resident_endpoint`` is the video worker whose resident encoder does the text encoding. ``kept`` (a
    ``KeptModels``) holds the DiT stack and the decoder between requests. ``note(facts)`` receives the planned
    placement once, before the first step (see ``plan_facts``); it never changes the picture.
    """
    references = list(references or [])
    if len(references) > MAX_REFERENCES:
        raise ValueError('too_many_images')
    stats = stats if stats is not None else {}
    report = progress_cb or (lambda *a: None)

    def check():
        if cancel is not None and cancel():
            raise Cancelled()

    check()
    report('encode', 0, 1)
    copies = reference_copies(references)
    if copies and denoise_floor_bytes(copies, width, height) + REFUSE_ALLOWANCE > usable_bytes(vram_gib):
        # Refused before the text encoder starts: otherwise the request spends a minute or more there and runs out
        # in the first step. Filed under the reference stage, where the page asks for fewer references.
        if enter is not None:
            enter('encode')
        raise ReferencesTooLarge('the references need more memory than this card has, even with every block streamed')
    shared_card = bool(resident_endpoint)
    placed = 0
    if resident_endpoint:
        # The resident encoder worker keeps its own CUDA context (about 0.8 GiB) on the card for good and encoding needs
        # 5 GiB: a card with less free than that plus the picture process's share encodes in a short process, as before.
        free_now = _driver_free_bytes()
        placed = placed_encoder_bytes(resident_endpoint)
        use_resident, why = resident_encode_decision(free_now, placed)
        stats['encode_resident_decision'] = why
        if not use_resident:
            stats['encode_resident'] = False
            resident_endpoint = None
    # A card with room for both keeps the encoder: the planner places the picture the same either way.
    keep_encoder = False
    if resident_endpoint:
        initialized = torch.cuda.is_initialized()
        held_dit = kept.items.get('dit') if kept is not None else None
        same_dit = held_dit is not None and held_dit[0] == str(dit_path)
        shape = picture_shape(*held_dit[1]) if same_dit else _SHAPES.get(str(dit_path))
        vae_held = _tensor_bytes(kept.items['vae'][1]) if kept is not None and 'vae' in kept.items else 0
        keep_encoder, why = keep_encoder_decision(
            free_now=free_now, placed=placed, shape=shape,
            budget_cap=vram_gib * 2**30 - PICTURE_CONTEXT if vram_gib else float('inf'),
            reserved=torch.cuda.memory_reserved() if initialized else 0, vae_held=vae_held,
            host_free=None if same_dit else host_free_bytes(), width=width, height=height,
            sequence_rows=reference_sequence_rows(copies), context=0 if initialized else PICTURE_CONTEXT)
        stats['encoder_keep'] = why
    # An encoder left on the card that this picture does not keep (placed while the page was set up, after a video,
    # or after the last picture) leaves it only through an encoding: a saved conditioning would keep it there beside
    # the picture model for the whole picture. With the encoder already on the card the encoding itself is short,
    # and the model gets the card it would have had.
    encoder_on_card = bool(resident_endpoint) and placed > 0 and not keep_encoder
    tick = time.perf_counter()
    from . import prompt_cache
    cond = prompt_cache.encode(
        prompt, [vision for _, vision in copies], stats=stats, encoder=encoder, use_saved=not encoder_on_card,
        cache=prompt_cache.open_cache(encoder=encoder, comfy_root=comfy_root, comfy_python=comfy_python or sys.executable,
                                      model_paths=model_paths),
        compute=lambda: _encode(prompt, encoder=encoder, comfy_root=comfy_root, model_paths=model_paths,
                                comfy_python=comfy_python, vram_gib=vram_gib, ram_gib=ram_gib,
                                vision_images=[vision for _, vision in copies], resident_endpoint=resident_endpoint,
                                release_gpu=not keep_encoder))
    stats['encode_s'] = round(time.perf_counter() - tick, 2)
    check()
    moments = []
    if copies:
        # Encoded before the DiT loads, so the card holds only the encoder and one batch of tiles.
        if enter is not None:
            enter('encode')
        tick = time.perf_counter()
        if vram_gib:
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(min(1.0, card_budget(vram_gib) / total))
        torch.cuda.reset_peak_memory_stats()
        # The encoder's peak (about 3 GiB) was never planned beside the blocks and decoder kept from the last picture.
        moments = reference_moments(copies, vae_path, torch.device('cuda'), cache=reference_cache,
                                    cache_root=reference_cache_root, stats=stats,
                                    before_load=kept.give_back_device if kept is not None else None)
        free_host_memory()
        stats.update(reference_encode_s=round(time.perf_counter() - tick, 2),
                     reference_encode_peak_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2),
                     reference_sizes=[list(vae.size) for vae, _ in copies],
                     text_tokens=int(cond['prompt_embeds'].shape[-2]))
    report('encode', 1, 1)
    check()

    from .h3_vae import H3ImageDecoder, to_unit
    from .image_policy import plan_image
    from PIL import Image
    torch.set_grad_enabled(False)
    device = torch.device('cuda')
    total = torch.cuda.get_device_properties(0).total_memory
    # Plan against what the card really has free plus what this process already holds, never against its whole size
    # or the caller's limit alone: a resident video worker's text encoder, another program, or this process's own
    # CUDA context may already hold part of it, and the planner fills whatever it is given with resident blocks.
    free, _ = torch.cuda.mem_get_info()
    reserved = torch.cuda.memory_reserved()
    budget = min(card_budget(vram_gib), int(free + reserved))
    # A decoder kept from the previous picture is not part of the next denoise plan.
    vae_held = 0
    if kept is not None and 'vae' in kept.items:
        vae_held = _tensor_bytes(kept.items['vae'][1])
        budget -= vae_held
    # budget = min(limit, free at plan time + this process's cached memory (reserved)) - the kept decoder
    stats['budget_parts_gib'] = dict(free=round(free / 2**30, 2), reserved=round(reserved / 2**30, 2),
                                     kept_vae=round(vae_held / 2**30, 2),
                                     allocated=round(torch.cuda.memory_allocated() / 2**30, 2))
    stats['budget_gib'] = round(budget / 2**30, 2)
    if kept is not None:
        kept.budget = budget
    if vram_gib:
        # The allocator limit is the caller's limit; the plan above already keeps inside the smaller budget, and a
        # limit that also left out the kept decoder would count it twice.
        torch.cuda.set_per_process_memory_fraction(min(1.0, card_budget(vram_gib) / total))
    canvas_h, canvas_w = h3_dit.canvas_size(height, width)
    if enter is not None:
        enter('load')
    tick = time.perf_counter()
    # Read before the weights are loaded. A kept model is given back when the host no longer covers its working
    # room, and the load below then locks only what the host has now.
    host_free = host_free_bytes()
    held = kept.take('dit', str(dit_path), host_free) if kept is not None else None
    if held is not None:
        host_free = None  # the kept model already holds its locked copy
    elif kept is not None and host_free is not None:
        host_free = host_free_bytes()  # the give-back above returned its locked planes
    if held is not None:
        model, stack = held
        stats['dit_kept'] = True
        # A small card gave the non-block parts back to the host after the last picture.
        for name, child in model.named_children():
            if name != 'blocks':
                child.to(device)
        model.adaln_t_table = model.adaln_t_table.to(device)
    else:
        model, stack = load_dit(dit_path, device, host_free, mapped=mapped)
    shape = picture_shape(model, stack)
    _SHAPES[str(dit_path)] = shape
    plan = plan_image(gpu_budget_bytes=budget, host_available_bytes=host_free, width=canvas_w, height=canvas_h,
                      shape=shape, sequence_rows=reference_sequence_rows(copies))
    stack.set_resident(plan.dit_resident_layers if resident is None else resident)
    stats.update(load_s=round(time.perf_counter() - tick, 2), canvas=[canvas_h, canvas_w],
                 resident=plan.dit_resident_layers if resident is None else resident,
                 planned_peak_gib=round(plan.peak_allocated_bytes / 2**30, 2), host_mode=plan.host_mode)
    if note is not None:
        try:
            note(plan_facts(plan, stack, budget=budget, total=total, shared_card=shared_card, capped=bool(vram_gib),
                            canvas=(canvas_h, canvas_w), sequence_rows=reference_sequence_rows(copies)))
        except Exception:
            pass
    generator = torch.Generator('cpu' if cpu_noise else device).manual_seed(seed)
    # The released order: each reference's posterior sample, then the image's noise, then each reference's noise.
    from .h3_vae import reference_latents
    ref_latents = [reference_latents(m.to(device), generator, 'cpu' if cpu_noise else None) for m in moments]

    def step(i, n):
        check()
        report('denoise', i, n)

    tick = time.perf_counter()
    try:
        latents = h3_dit.sample(model, cond['prompt_embeds'], cond['text_token_tags'], canvas_h // 16, canvas_w // 16,
                                generator, noise_device='cpu' if cpu_noise else None, step_callback=step,
                                preview_callback=preview, references=ref_latents)
        torch.cuda.synchronize()
        stats['sample_s'] = round(time.perf_counter() - tick, 2)
        stats['denoise_peak_gib'] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    finally:
        if kept is not None and resident is None and plan.dit_kept_layers is not None and stack.offloader.at_boundary:
            # The decode stage was planned with this many blocks on the card.
            stack.set_resident(plan.dit_kept_layers)
        if (kept is not None and stack.offloader.at_boundary
                and kept.keep('dit', str(dit_path), (model, stack), stack)):
            stats['dit_kept_after'] = True
        else:
            stack.close()
        del model, stack
        # Nothing is cleared while the weights stay: ModelBank-style room checks (KeptModels.keep) decided that.
        if kept is None or 'dit' not in kept.items:
            free_host_memory()
    report('denoise', 6, 6)
    check()
    report('decode', 0, 1)
    torch.cuda.reset_peak_memory_stats()
    decoder = kept.take('vae', str(vae_path)) if kept is not None else None
    stats['vae_kept'] = decoder is not None
    if decoder is None:
        decoder = H3ImageDecoder.load(str(vae_path), device)
    pixels = to_unit(decoder.decode(latents.to(device)[None], normalized=True))
    image = Image.fromarray((pixels[0].clamp(0, 1) * 255).round().byte().permute(1, 2, 0).cpu().numpy())
    stats['decode_peak_gib'] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    if kept is None or not kept.keep('vae', str(vae_path), decoder, None):
        del decoder
        free_host_memory()
    report('decode', 1, 1)
    if shared_card:
        # The next picture's text encoding runs in another process on this card: give back the allocator's cache.
        torch.cuda.empty_cache()
        stats['held_after_gib'] = dict(allocated=round(torch.cuda.memory_allocated() / 2**30, 2),
                                       reserved=round(torch.cuda.memory_reserved() / 2**30, 2))
    return image
