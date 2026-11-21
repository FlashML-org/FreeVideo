"""MOSS-Image sampling for the image worker: prompt -> text encoder -> DiT (50 steps, guided) -> VAE -> picture.

The model code is the file the model's repository ships (``pipeline_moss_image.py``, pinned by hash in the
install pack); this module only places the weights. Placement follows the card:

* DiT: kept on the card when it fits (``DIT_RESIDENT_GIB``), else its 42 layers are read from the files per use
  (``layer_stream.DiskLayerStream``) and each layer serves the positive and the negative branch at once, so a
  guided step reads the weights once. Card use stays near 4 GiB, host memory near 4 GiB.
* Text encoder: loaded to the card for the prompt and dropped after it; read from disk per layer below
  ``ENCODER_RESIDENT_GIB``. (Keeping it between requests on a very large card is not done yet.)
* VAE: float32, tiled decode.

Both placements give the same picture (layer streaming only changes where a layer lives while it runs).
"""
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

DIT_RESIDENT_GIB = 16.
ENCODER_RESIDENT_GIB = 24.
STEPS = 50
GUIDANCE = 3.
SKIP_LAYERS = (22,)
LAYERS = 42
LAYER_GIB = 0.225   # one bf16 DiT layer
KEEP_MARGIN_GIB = 1.5
SIDE_MULTIPLE = 16
MAX_SIDE = 2048
MIN_SIDE = 256


class Cancelled(Exception):
    pass


def fit_size(width, height):
    """The nearest sizes MOSS can draw: multiples of 16, each side within [MIN_SIDE, MAX_SIDE]."""
    def one(value):
        return min(MAX_SIDE, max(MIN_SIDE, round(value / SIDE_MULTIPLE) * SIDE_MULTIPLE))
    return one(width), one(height)


def load_module(folder):
    """The model's own pipeline file, imported once under its repository name."""
    name = 'pipeline_moss_image'
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(Path(folder) / 'pipeline_moss_image.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def folder_key(folder):
    """Identity of the installed files: names, sizes and times of the weight files."""
    parts = []
    for path in sorted(Path(folder).rglob('*.safetensors')):
        stat = path.stat()
        parts.append((str(path.relative_to(folder)), stat.st_size, int(stat.st_mtime)))
    return tuple(parts)


def keep_layers(vram_gib, width, height, layers=LAYERS, layer_gib=LAYER_GIB):
    """How many DiT layers a streamed run keeps on the card: what the budget leaves after the picture's own working
    memory (measured about 0.3 GiB plus 2.4 GiB per megapixel, streaming alone) and a margin for the driver."""
    working = 0.3 + 2.4 * width * height / 1e6
    spare = float(vram_gib or 0) - working - KEEP_MARGIN_GIB
    return max(0, min(layers - 1, int(spare / layer_gib)))


def kept_indices(count, layers=LAYERS):
    """``count`` layer indices spread evenly, so the next streamed layer is read while kept ones run."""
    if count <= 0:
        return ()
    return tuple(sorted({int((k + 0.5) * layers / count) for k in range(count)}))


def plan(vram_gib, width=1024, height=1024):
    """Where each part goes for a card budget in GiB: {'dit': 'resident'|'stream', 'encoder': ..., 'keep': n}."""
    vram = float(vram_gib or 0)
    dit = 'resident' if vram >= DIT_RESIDENT_GIB else 'stream'
    return dict(dit=dit, encoder='resident' if vram >= ENCODER_RESIDENT_GIB else 'stream',
                keep=0 if dit == 'resident' else keep_layers(vram, width, height))


class PairedForward:
    """Replace the DiT's forward by one that runs the positive and the negative branch layer by layer, so a
    streamed layer is read once for both. Returns the positive prediction and holds the negative one for the
    pipeline's second call of the step."""

    def __init__(self, transformer, negative, negative_mask, skip, device):
        self.tr, self.neg, self.nmask, self.skip, self.device = transformer, negative, negative_mask, skip, device
        self.original = transformer.forward
        self.pending = None
        transformer.forward = self.forward

    def _prepare(self, hidden, timestep, encoder, mask):
        import torch
        tr = self.tr
        batch, _, h, w = hidden.shape
        times = torch.as_tensor(timestep, device=hidden.device, dtype=torch.float32).reshape(-1).expand(batch)
        half = tr.config.freq_dim // 2
        freq = 10000 ** (-torch.arange(half, device=times.device, dtype=torch.float64) / half)
        phase = times.double()[:, None] * freq
        emb = torch.cat((phase.cos(), phase.sin()), -1).float()
        time_emb = tr.time_embedding(emb.to(tr.time_embedding[0].weight.dtype)).float()
        mod = tr.shared_block_modulation(time_emb.to(tr.shared_block_modulation.weight.dtype)).view(batch, 6, -1)
        head = tr.video_head_modulation(time_emb.to(tr.video_head_modulation.weight.dtype)).view(batch, 2, -1)
        context = encoder[0:1, mask[0].bool()]
        video = hidden[0:1].flatten(2).transpose(1, 2)
        video = tr.video_embedder(video.to(tr.video_embedder.weight.dtype).contiguous())
        rope = tr.rope(h, w, context.shape[1], hidden.device).to(video.dtype)
        text = tr.text_embedder(context, rope[:, h * w:])
        return torch.cat((video, text), 1), rope, mod[0:1], head[0:1], h, w

    def _finish(self, x, head, h, w):
        tr = self.tr
        x = tr.final_norm_video(x[:, :h * w].to(tr.final_norm_video.weight.dtype))
        x = x * (1 + head[:, 1, None]) + head[:, 0, None]
        x = tr.final_linear_video(x.to(tr.final_linear_video.weight.dtype))
        return x.transpose(1, 2).reshape(1, -1, h, w)

    def forward(self, hidden_states, timestep, encoder_hidden_states, encoder_attention_mask=None,
                return_dict=True, skip_layers=None):
        from diffusers.models.modeling_outputs import Transformer2DModelOutput
        from .layer_stream import run_layers_paired
        if self.pending is not None:
            out, self.pending = self.pending, None
            return Transformer2DModelOutput(sample=out)
        xp, rp, mp, hp, h, w = self._prepare(hidden_states, timestep, encoder_hidden_states, encoder_attention_mask)
        xn, rn, mn, hn, _, _ = self._prepare(hidden_states, timestep, self.neg, self.nmask)
        xp, xn = run_layers_paired(self.tr.layers, (xp, rp, mp), (xn, rn, mn), skip_second=self.skip)
        self.pending = self._finish(xn, hn, h, w)
        return Transformer2DModelOutput(sample=self._finish(xp, hp, h, w))

    def close(self):
        self.tr.forward = self.original


class Kept:
    """What stays loaded between requests of one worker."""

    def __init__(self):
        self.key = None
        self.dit = None          # transformer on the card (resident placement only)
        self.vae = None
        self.negative = None     # (embeds, mask) of the empty prompt, cached on the card

    def close(self):
        self.dit = self.vae = self.negative = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


def _encode(module, folder, prompts, device, placement, stats):
    """Encode ``prompts`` with the text encoder; returns a list of (embeds, mask) on the CPU."""
    import torch
    from transformers import AutoConfig, AutoModel, AutoTokenizer
    from .layer_stream import DiskLayerStream
    from .staged_weights import layer_key, open_index
    folder = Path(folder)
    encoder_dir = folder / 'text_encoder'
    tokenizer = AutoTokenizer.from_pretrained(str(folder / 'tokenizer'), local_files_only=True)
    stream = None
    if placement == 'resident':
        text_encoder = AutoModel.from_pretrained(str(encoder_dir), dtype=torch.bfloat16, local_files_only=True)
        text_encoder = text_encoder.eval().to(device)
    else:
        config = AutoConfig.from_pretrained(str(encoder_dir), local_files_only=True)
        with torch.device('meta'):
            text_encoder = AutoModel.from_config(config, dtype=torch.bfloat16).eval()
        language = text_encoder.language_model if hasattr(text_encoder, 'language_model') else text_encoder.model.language_model
        index = open_index(encoder_dir)
        language.embed_tokens.weight = torch.nn.Parameter(
            _one(index, 'language_model.embed_tokens.weight', device), requires_grad=False)
        language.norm.weight = torch.nn.Parameter(_one(index, 'language_model.norm.weight', device), requires_grad=False)
        language.rotary_emb = type(language.rotary_emb)(config=language.config, device=device)
        stream = DiskLayerStream(language.layers, index, layer_key('language_model.'), device)
    pipe = module.MOSSImagePipeline(None, module.MOSSImageScheduler(), text_encoder, tokenizer, None)
    pipe.__class__._execution_device = property(lambda self: torch.device(device))
    out = []
    started = time.time()
    with torch.no_grad():
        for prompt in prompts:
            embeds, mask = pipe.encode_prompt(prompt)
            out.append((embeds.cpu(), mask.cpu()))
    stats['encode_s'] = round(time.time() - started, 2)
    if stream is not None:
        stats['encoder_read_gib'] = round(stream.bytes_read / 2**30, 2)
        stats['encoder_pinned'] = stream.pinned
        stream.close()
    return out


def _one(index, key, device):
    from safetensors import safe_open
    with safe_open(str(index.folder / index.weight_map[key]), 'pt', device='cpu') as handle:
        return handle.get_tensor(key).to(device)


def _build_dit(module, folder, device, placement, keep=0):
    """The DiT with its non-layer weights on the card; returns (transformer, stream or None)."""
    import torch
    from .layer_stream import DiskLayerStream
    from .staged_weights import layer_key, open_index
    folder = Path(folder) / 'transformer'
    if placement == 'resident':
        transformer = module.MOSSImageTransformer.from_pretrained(
            str(folder), torch_dtype=torch.bfloat16, local_files_only=True).eval().to(device)
        return transformer, None
    config = module.MOSSImageTransformer.load_config(str(folder))
    with torch.device('meta'):
        transformer = module.MOSSImageTransformer.from_config(config).to(torch.bfloat16).eval()
    index = open_index(folder, 'diffusion_pytorch_model.safetensors.index.json')
    small = {key: _one(index, key, device) for key in index.weight_map if not key.startswith('layers.')}
    transformer.load_state_dict(small, strict=False, assign=True)
    for name, buffer in transformer.named_buffers():
        if buffer.is_meta:
            raise RuntimeError('MOSS-Image: buffer %s was not loaded from the files' % name)
    return transformer, DiskLayerStream(transformer.layers, index, layer_key(''), device,
                                         keep=kept_indices(keep, len(transformer.layers)))


def generate(prompt, width, height, seed, progress=None, cancel=None, *, folder, vram_gib=None, kept=None,
             stats=None, steps=STEPS, guidance=GUIDANCE, enter=None):
    """Return a PIL image. ``progress(stage, done, total)`` stages are encode, denoise, decode."""
    import torch
    progress = progress or (lambda *a: None)
    stats = stats if stats is not None else {}
    kept = kept if kept is not None else Kept()
    device = torch.device('cuda')
    width, height = fit_size(width, height)
    stats['canvas'] = [width, height]

    def check():
        if cancel is not None and cancel():
            raise Cancelled()

    module = load_module(folder)
    key = folder_key(folder)
    if kept.key != key:
        kept.close()
        kept.key = key
    free_gib = torch.cuda.mem_get_info()[0] / 2**30 + (torch.cuda.memory_reserved() / 2**30)
    budget = min(float(vram_gib), free_gib) if vram_gib else free_gib
    placement = plan(budget, width, height)
    stats['placement'] = placement
    check()
    progress('encode', 0, 1)
    if enter is not None:
        enter('encode')
    prompts = [prompt] + ([] if kept.negative is not None else [''])
    encoded = _encode(module, folder, prompts, device, placement['encoder'], stats)
    embeds, mask = (e.to(device) for e in encoded[0])
    if kept.negative is None:
        kept.negative = tuple(t.to(device) for t in encoded[1])
    negative, negative_mask = kept.negative
    torch.cuda.empty_cache()
    progress('encode', 1, 1)
    check()
    if enter is not None:
        enter('denoise')
    started = time.time()
    resident = placement['dit'] == 'resident'
    if resident and kept.dit is not None:
        transformer, stream = kept.dit, None
    else:
        transformer, stream = _build_dit(module, folder, device, placement['dit'], placement.get('keep', 0))
        if resident:
            kept.dit = transformer
    stats['dit_load_s'] = round(time.time() - started, 2)
    if kept.vae is None:
        from diffusers import AutoencoderKLFlux2
        kept.vae = AutoencoderKLFlux2.from_pretrained(str(Path(folder) / 'vae'), torch_dtype=torch.float32,
                                                      local_files_only=True).eval().to(device)
        kept.vae.enable_tiling()
    scheduler = module.MOSSImageScheduler()  # the repository's scheduler_config.json holds exactly these defaults
    pipe = module.MOSSImagePipeline(transformer, scheduler, None, None, kept.vae)
    pipe.__class__._execution_device = property(lambda self: device)
    pipe.set_progress_bar_config(disable=True)
    paired = None
    if stream is not None and guidance != 1.0:
        paired = PairedForward(transformer, negative, negative_mask, SKIP_LAYERS, device)
    done = [0]
    original_step = scheduler.step

    def step(*args, **kwargs):
        result = original_step(*args, **kwargs)
        done[0] += 1
        progress('denoise', done[0], steps)
        check()
        return result
    scheduler.step = step
    progress('denoise', 0, steps)
    try:
        generator = torch.Generator(device=device).manual_seed(int(seed))
        started = time.time()
        torch.cuda.reset_peak_memory_stats()
        result = pipe(prompt_embeds=embeds, prompt_attention_mask=mask, negative_prompt_embeds=negative,
                      negative_prompt_attention_mask=negative_mask, height=height, width=width,
                      num_inference_steps=steps, generator=generator, output_type='pil', guidance_scale=guidance)
        stats['denoise_decode_s'] = round(time.time() - started, 1)
        stats['peak_gib'] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    finally:
        if paired is not None:
            paired.close()
        if stream is not None:
            stats['dit_read_gib'] = round(stream.bytes_read / 2**30, 2)
            stats['dit_pinned'] = stream.pinned
            stream.close()
        scheduler.step = original_step
        torch.cuda.empty_cache()
    progress('decode', 1, 1)
    return result.images[0]
