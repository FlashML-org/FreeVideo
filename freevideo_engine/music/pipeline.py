"""Local instrumental ACE-Step XL turbo + 1.7B LM; no checkpoint Python.

Prompt formatting, conditioning and overlap-discard decode are adapted from
ACE-Step 1.5 (MIT, see licenses/ACE-Step-MIT.txt). The supported dependency set follows
upstream's no-pytorch_wavelets DCW fallback. All attention uses PyTorch SDPA.
"""
from contextlib import contextmanager
import gc
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import warnings
import wave

import numpy as np
import torch
from safetensors import safe_open

SAMPLE_RATE = 48000
# Exact checkpoint keys omitted from the instrumental LM-hint path. Never use
# prefix exemptions here: a renamed or newly added checkpoint tensor must fail.
UNUSED_DIT_KEYS = frozenset({
    'null_condition_emb',
    'tokenizer.attention_pooler.embed_tokens.bias',
    'tokenizer.attention_pooler.embed_tokens.weight',
    'tokenizer.attention_pooler.layers.0.input_layernorm.weight',
    'tokenizer.attention_pooler.layers.0.mlp.down_proj.weight',
    'tokenizer.attention_pooler.layers.0.mlp.gate_proj.weight',
    'tokenizer.attention_pooler.layers.0.mlp.up_proj.weight',
    'tokenizer.attention_pooler.layers.0.post_attention_layernorm.weight',
    'tokenizer.attention_pooler.layers.0.self_attn.k_norm.weight',
    'tokenizer.attention_pooler.layers.0.self_attn.k_proj.weight',
    'tokenizer.attention_pooler.layers.0.self_attn.o_proj.weight',
    'tokenizer.attention_pooler.layers.0.self_attn.q_norm.weight',
    'tokenizer.attention_pooler.layers.0.self_attn.q_proj.weight',
    'tokenizer.attention_pooler.layers.0.self_attn.v_proj.weight',
    'tokenizer.attention_pooler.layers.1.input_layernorm.weight',
    'tokenizer.attention_pooler.layers.1.mlp.down_proj.weight',
    'tokenizer.attention_pooler.layers.1.mlp.gate_proj.weight',
    'tokenizer.attention_pooler.layers.1.mlp.up_proj.weight',
    'tokenizer.attention_pooler.layers.1.post_attention_layernorm.weight',
    'tokenizer.attention_pooler.layers.1.self_attn.k_norm.weight',
    'tokenizer.attention_pooler.layers.1.self_attn.k_proj.weight',
    'tokenizer.attention_pooler.layers.1.self_attn.o_proj.weight',
    'tokenizer.attention_pooler.layers.1.self_attn.q_norm.weight',
    'tokenizer.attention_pooler.layers.1.self_attn.q_proj.weight',
    'tokenizer.attention_pooler.layers.1.self_attn.v_proj.weight',
    'tokenizer.attention_pooler.norm.weight',
    'tokenizer.attention_pooler.special_token',
    'tokenizer.audio_acoustic_proj.bias',
    'tokenizer.audio_acoustic_proj.weight',
    'tokenizer.quantizer.project_in.bias',
    'tokenizer.quantizer.project_in.weight',
    # The BF16 condition encoder file also carries silence_latent.pt's tensor.
    'silence_latent',
})
# Release folder: config, silence latent and the one F32 shard holding the
# detokenizer and FSQ. Decoder and encoder come from the official BF16 copy;
# every one of its tensors equals the F32 release cast to BF16.
DIT_NAME = "acestep-v15-xl-turbo"
DIT_BF16_NAME = "acestep-v15-xl-turbo-bf16"
DETOKENIZER_SHARD = "model-00004-of-00004.safetensors"
TRANSFORMER_SHARDS = ("diffusion_pytorch_model-00001-of-00002.safetensors",
                      "diffusion_pytorch_model-00002-of-00002.safetensors")
CONDITION_ENCODER_FILE = "condition_encoder/diffusion_pytorch_model.safetensors"
# Diffusers names of the BF16 copy -> release checkpoint names.
_BF16_RENAMES = ((r"\.to_q\.", ".q_proj."), (r"\.to_k\.", ".k_proj."), (r"\.to_v\.", ".v_proj."),
                 (r"\.to_out\.0\.", ".o_proj."), (r"\.norm_q\.", ".q_norm."), (r"\.norm_k\.", ".k_norm."),
                 (r"^proj_in_conv\.", "proj_in.1."), (r"^proj_out_conv\.", "proj_out.1."))
LM_NAME = "acestep-5Hz-lm-1.7B"
# resident: everything stays on the GPU. staged: one component on the GPU at a
# time. int8: staged, with weight-only int8 decoder linears. stream: staged,
# with DiT blocks read from the files right before their forward.
MODES = ("resident", "staged", "int8", "stream")


class BadRequest(ValueError):
    """Invalid music generation parameters."""


def validate_request(caption, seconds, seed):
    if not isinstance(caption, str) or not caption.strip():
        raise BadRequest("caption must be a nonempty string")
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or not 10 <= seconds <= 600):
        raise BadRequest("seconds must be between 10 and 600")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**64 - 1:
        raise BadRequest("seed must be an integer between 0 and 2**64-1")


def validate_keys(expected, actual, unused=frozenset()):
    """Reject every missing active key and every unexpected non-allowlisted key."""
    expected, actual = set(expected), set(actual)
    missing = expected - actual
    unexpected = actual - expected - unused
    if missing or unexpected:
        raise RuntimeError(f"checkpoint_keys: missing={sorted(missing)}, unexpected={sorted(unexpected)}")


def _files(root):
    """Return {component: {checkpoint key: (file, key in file)}} after checking every file exists."""
    paths = {
        "lm": root / LM_NAME / "model.safetensors",
        "text": root / "Qwen3-Embedding-0.6B/model.safetensors",
        "vae": root / "vae/diffusion_pytorch_model.safetensors",
    }
    dit_files = [root / DIT_BF16_NAME / "transformer" / name for name in TRANSFORMER_SHARDS] + [
        root / DIT_BF16_NAME / CONDITION_ENCODER_FILE, root / DIT_NAME / DETOKENIZER_SHARD]
    required = list(paths.values()) + dit_files + [
        root / DIT_NAME / "config.json", root / DIT_NAME / "silence_latent.pt",
        root / LM_NAME / "config.json", root / LM_NAME / "tokenizer.json",
        root / LM_NAME / "tokenizer_config.json",
        root / "Qwen3-Embedding-0.6B/config.json",
        root / "Qwen3-Embedding-0.6B/tokenizer.json",
        root / "Qwen3-Embedding-0.6B/tokenizer_config.json",
        root / "vae/config.json",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing model files: " + ", ".join(missing))
    # Files are data only: no model code is imported from the model folder.
    weights = {kind: _weight_map(path) for kind, path in paths.items()}
    weights["dit"] = _dit_weight_map(root)
    return weights


def _weight_map(path):
    with safe_open(str(path), framework="pt", device="cpu") as source:
        return {k: (path, k) for k in source.keys()}


def _bf16_key(part, key):
    for pattern, replacement in _BF16_RENAMES:
        key = re.sub(pattern, replacement, key)
    if part == "transformer":
        return "decoder." + key
    return key if key in ("null_condition_emb", "silence_latent") else "encoder." + key


def _dit_weight_map(root):
    """BF16 decoder/encoder from the official BF16 copy; detokenizer and FSQ from the F32 shard.

    The F32 shard also holds some decoder/encoder tensors; those duplicate the
    BF16 copy and are never read.
    """
    weights = {}
    sources = [("transformer", root / DIT_BF16_NAME / "transformer" / name) for name in TRANSFORMER_SHARDS]
    sources += [("condition_encoder", root / DIT_BF16_NAME / CONDITION_ENCODER_FILE)]
    for part, path in sources:
        for key in _weight_map(path):
            name = _bf16_key(part, key)
            if name in weights:
                raise RuntimeError(f"checkpoint_keys: duplicated key {name}")
            weights[name] = (path, key)
    for key, value in _weight_map(root / DIT_NAME / DETOKENIZER_SHARD).items():
        if key.split(".")[0] in ("detokenizer", "tokenizer"):
            if key in weights:
                raise RuntimeError(f"checkpoint_keys: duplicated key {key}")
            weights[key] = value
    return weights


def _read(weights, name, device, dtype):
    path, key = weights[name]
    with safe_open(str(path), framework="pt", device="cpu") as source:
        return source.get_tensor(key).to(device=device, dtype=dtype)


def _read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _load_weights(model, weight_map, unused=frozenset()):
    state = model.state_dict()
    validate_keys(state, weight_map, unused)
    if all(t.device.type == "meta" for t in state.values()):
        # Shape-only DiT: validate every shape without materializing all 9 GiB.
        for shard in {path for path, _ in weight_map.values()}:
            with safe_open(str(shard), framework="pt", device="cpu") as source:
                for key, target in state.items():
                    path, name = weight_map[key]
                    if path == shard and tuple(source.get_slice(name).get_shape()) != tuple(target.shape):
                        raise RuntimeError(f"checkpoint_shape: {key}")
        return model.eval().requires_grad_(False)
    # Close the mapping after each tensor to bound resident host memory.
    with torch.no_grad():
        for key, target in state.items():
            path, name = weight_map[key]
            with safe_open(str(path), framework="pt", device="cpu") as source:
                value = source.get_tensor(name)
                if value.shape != target.shape:
                    raise RuntimeError(f"checkpoint_shape: {key}: {value.shape} != {target.shape}")
                target.copy_(value)
                del value
    return model.eval().requires_grad_(False)


def _materialize(model, weight_map, device, dtype, quantize):
    """Fill a shape-only (meta) model from local files, one tensor on the host at a time.

    With quantize, each decoder linear becomes Int8Linear as soon as its BF16
    weight arrives, so the BF16 decoder (7.77 GiB) never exists on the device.
    Constructor buffers that are not in the files are real CPU data and move.
    """
    from .layers import Int8Linear, quantizable
    with torch.no_grad():
        for name, module in list(model.named_modules()):
            prefix = name + "." if name else ""
            if quantize and quantizable(name, module):
                bias = None if module.bias is None else _read(weight_map, prefix + "bias", device, dtype)
                layer = Int8Linear.from_weight(_read(weight_map, prefix + "weight", device, dtype), bias)
                parent, _, leaf = name.rpartition(".")
                setattr(model.get_submodule(parent), leaf, layer)
                continue
            for leaf, parameter in list(module.named_parameters(recurse=False)):
                value = _read(weight_map, prefix + leaf, device, parameter.dtype)
                module._parameters[leaf] = torch.nn.Parameter(value, requires_grad=False)
            for leaf, buffer in list(module.named_buffers(recurse=False)):
                if prefix + leaf in weight_map:
                    module._buffers[leaf] = _read(weight_map, prefix + leaf, device, buffer.dtype)
                elif buffer is not None:
                    module._buffers[leaf] = buffer.to(device)
    if any(t.device.type == "meta" for t in list(model.parameters()) + list(model.buffers())):
        raise RuntimeError("checkpoint_keys: tensor left unmaterialized")
    return model


def _construct(kind, root, weights, device, dtype):
    from diffusers import AutoencoderOobleck
    from transformers import Qwen3Config
    from .layers import build_qwen
    from transformers.initialization import no_init_weights
    from transformers.models.qwen3.modeling_qwen3 import Qwen3RotaryEmbedding
    from .acestep_model import AceStepConfig, AceStepConditionGenerationModel, AudioCodeQuantizer

    if kind == "lm":
        return build_qwen(path=root / LM_NAME, device=device, dtype=dtype)

    # Ordinary components allocate directly on the target. Sequential DiT uses
    # meta parameters with real CPU constructor buffers; blocks materialize
    # directly from local shards immediately before their CUDA forward.
    previous_dtype = torch.get_default_dtype()
    try:
        torch.set_default_dtype(dtype)
        with torch.device(device), no_init_weights():
            if kind == "dit":
                config = AceStepConfig(**_read_json(root / DIT_NAME / "config.json"))
                config._attn_implementation = "sdpa"
                model = AceStepConditionGenerationModel(config)
            elif kind == "text":
                config = Qwen3Config(**_read_json(root / "Qwen3-Embedding-0.6B/config.json"))
                config._attn_implementation = "sdpa"
                model = build_qwen(config, device=device, dtype=dtype)
            else:
                model = AutoencoderOobleck.from_config(_read_json(root / "vae/config.json"))
    finally:
        torch.set_default_dtype(previous_dtype)
    # Upstream constructs RoPE on CPU before moving to CUDA. Computing pow on
    # CUDA can round differently; reproduce that small constructor buffer on CPU.
    for module in model.modules():
        if isinstance(module, Qwen3RotaryEmbedding):
            with torch.device("cpu"):
                inv_freq, scale = module.compute_default_rope_parameters(module.config)
            buffer_device = "cpu" if torch.device(device).type == "meta" else device
            module.inv_freq = inv_freq.to(device=buffer_device, dtype=dtype)
            module.original_inv_freq = module.inv_freq.clone()
            module.attention_scaling = scale
    for module in model.modules():
        if isinstance(module, AudioCodeQuantizer):
            buffer_device = "cpu" if torch.device(device).type == "meta" else device
            for name, value in module.named_buffers():
                module._buffers[name] = value.to(device=buffer_device, dtype=dtype)
    return _load_weights(model, weights, UNUSED_DIT_KEYS if kind == "dit" else frozenset())


def _prompt(caption, seconds, metadata):
    # handler._resolve_generate_music_task uses this instruction whenever LM
    # audio codes are supplied, including ordinary instrumental text2music.
    metas = (f"- bpm: {metadata.get('bpm') or 'N/A'}\n"
             f"- timesignature: {metadata.get('timesignature') or 'N/A'}\n"
             f"- keyscale: {metadata.get('keyscale') or 'N/A'}\n"
             f"- duration: {int(seconds)} seconds\n")
    return ("# Instruction\nGenerate audio semantic tokens based on the given conditions:\n\n"
            f"# Caption\n{caption}\n\n# Metas\n{metas}<|endoftext|>\n")


class MusicPipeline:
    """One request at a time; progress callbacks receive (fraction, desc=...).

    Sequential mode releases each component completely, then reopens local
    safetensors for the next stage. This bounds host memory as well as VRAM;
    stage weight reads are included in generate time. load preflights local
    file headers; full key/shape validation happens when a stage is opened.
    """

    def __init__(self, root, device, mode):
        from transformers import PreTrainedTokenizerFast
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.root = Path(root)
        self.weights = _files(self.root)
        self.device = torch.device(device)
        self.dtype = torch.bfloat16 if self.device.type == "cuda" else torch.float32
        self.mode = mode
        self.sequential = sequential = mode != "resident"
        self.components = {}
        self.component_bytes = {}
        self.tokenizer = PreTrainedTokenizerFast.from_pretrained(
            str(self.root / "Qwen3-Embedding-0.6B"), local_files_only=True,
        )
        # weights_only disallows pickle globals; this is a tensor, not code.
        self.silence = torch.load(self.root / DIT_NAME / "silence_latent.pt",
                                  map_location="cpu", weights_only=True)
        if not isinstance(self.silence, torch.Tensor) or self.silence.ndim != 3:
            raise RuntimeError("checkpoint_shape: silence_latent")
        # Official loader transposes [1,64,T] -> [1,T,64].
        self.silence = self.silence.transpose(1, 2).to(self.dtype)
        if self.silence.shape[0] != 1 or self.silence.shape[2] != 64 or self.silence.shape[1] < 750:
            raise RuntimeError("checkpoint_shape: silence_latent")
        from transformers import AutoTokenizer
        from .constrained_logits import MetadataConstrainedLogitsProcessor
        self.lm_tokenizer = AutoTokenizer.from_pretrained(
            str(self.root / LM_NAME), local_files_only=True, trust_remote_code=False, use_fast=True,
        )
        self.lm_processor = MetadataConstrainedLogitsProcessor(self.lm_tokenizer, max_duration=600)
        self.stage_times = {}
        self.last_lm_tokens = {}
        self.last_metadata = {}
        # Sequential load validates file headers/keys without loading 10 GiB of
        # DiT twice. Shape validation takes place when each component is opened.
        for kind in ("lm", "dit", "vae", "text"):
            if sequential:
                size = 0
                element = torch.tensor([], dtype=self.dtype).element_size()
                for shard in {path for path, _ in self.weights[kind].values()}:
                    with safe_open(str(shard), framework="pt", device="cpu") as source:
                        for key, (path, name) in self.weights[kind].items():
                            if path == shard:
                                size += math.prod(source.get_slice(name).get_shape()) * element
                self.component_bytes[kind] = size
            else:
                component = _construct(kind, self.root, self.weights[kind], self.device, self.dtype)
                self.component_bytes[kind] = sum(t.numel() * t.element_size() for t in component.parameters())
                self.components[kind] = component
        self.decode_chunk_size = self._chunk_size()
        warnings.warn("ACE-Step default DCW: enabled/double/0.05/0.02/haar; "
                      "using the official no-pytorch_wavelets no-op path.", RuntimeWarning)

    def _clear(self):
        gc.collect()
        if self.device.type == "cuda":
            torch.cuda.empty_cache()

    @contextmanager
    def _component(self, kind):
        if kind not in self.components:
            # XL decoder weights alone occupy 7.77 GiB. Stream and int8 modes
            # build the DiT shape-only and fill it from the files: stream one
            # CUDA block per forward, int8 once with int8 decoder linears.
            shape_only = kind == "dit" and self.mode in ("stream", "int8")
            target = "meta" if shape_only else self.device
            started = time.perf_counter()
            self.components[kind] = _construct(kind, self.root, self.weights[kind], target, self.dtype)
            if shape_only and self.mode == "stream":
                self._stream_dit(self.components[kind])
            elif shape_only:
                _materialize(self.components[kind], self.weights[kind], self.device, self.dtype, quantize=True)
            self._synchronize()
            self.stage_times[kind + "_load_s"] = time.perf_counter() - started
        try:
            yield self.components[kind]
        finally:
            if self.sequential:
                del self.components[kind]
                self._clear()

    def _stream_dit(self, dit):
        """Materialize exact BF16 weights from local shards, one block at a time.

        Shape-only parameters carry the full validated model structure. Every
        nonpersistent constructor buffer is real CPU data, never uninitialized
        to_empty storage. At most one block's mapped weights are resident on
        the host; all shards are read-only and closed after each block.
        """
        weight_map = self.weights["dit"]

        def layout(block, prefix, exclude_layers=False):
            entries = []
            for name, parameter in block.named_parameters():
                if exclude_layers and name.startswith("layers."):
                    continue
                parent_name, _, leaf = name.rpartition(".")
                parent = block.get_submodule(parent_name) if parent_name else block
                entries.append((parent, leaf, parameter, prefix + "." + name))
            return entries

        def materialize(entries):
            started = time.perf_counter()
            for shard in dict.fromkeys(weight_map[key][0] for _, _, _, key in entries):
                with safe_open(str(shard), framework="pt", device="cpu") as source:
                    for parent, leaf, _, key in entries:
                        path, name = weight_map[key]
                        if path != shard:
                            continue
                        value = source.get_tensor(name)
                        tensor = value.to(device=self.device, dtype=self.dtype)
                        parent._parameters[leaf] = torch.nn.Parameter(tensor, requires_grad=False)
                        del value, tensor
            self.stage_times["dit_weight_load_s"] = (
                self.stage_times.get("dit_weight_load_s", 0.0) + time.perf_counter() - started)

        blocks = [(dit.encoder, "encoder"), (dit.detokenizer, "detokenizer")]
        blocks += [(layer, f"decoder.layers.{i}") for i, layer in enumerate(dit.decoder.layers)]
        for block, prefix in blocks:
            entries = layout(block, prefix)
            buffers = [(child, name, value) for child in block.modules()
                       for name, value in child._buffers.items() if value is not None]
            def before(module, args, entries=entries, buffers=buffers):
                materialize(entries)
                for child, name, value in buffers:
                    child._buffers[name] = value.to(self.device)
            def after(module, args, outputs, entries=entries, buffers=buffers):
                for parent, leaf, shape_only, _ in entries:
                    parent._parameters[leaf] = shape_only
                for child, name, value in buffers:
                    child._buffers[name] = value
            block.register_forward_pre_hook(before)
            block.register_forward_hook(after, always_call=True)
        materialize(layout(dit.tokenizer.quantizer, "tokenizer.quantizer"))
        dit.tokenizer.quantizer.to(self.device)
        # Decoder projections/timestep embeddings are small and remain resident.
        materialize(layout(dit.decoder, "decoder", exclude_layers=True))
        dit.decoder.rotary_emb.to(self.device)

    @contextmanager
    def _stage(self, name):
        self._synchronize()
        start = time.perf_counter()
        try:
            yield
        finally:
            self._synchronize()
            self.stage_times[name + "_s"] = time.perf_counter() - start

    def _synchronize(self):
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)

    def _chunk_size(self):
        # Same thresholds and 512-frame maximum as official tiled_decode.
        # Fix the chosen size per instance: offloading must not change the audio.
        if hasattr(self, "decode_chunk_size"):
            return self.decode_chunk_size
        if self.device.type == "cuda":
            free, _ = torch.cuda.mem_get_info(self.device)
            free += torch.cuda.memory_reserved(self.device) - torch.cuda.memory_allocated(self.device)
            # Choose the official chunk for the equivalent fully resident model.
            # Keep it across offload modes and allocator caps: changing temporal
            # windows changes VAE numerics. Only one VAE window is live at a time.
            free -= sum(size for kind, size in self.component_bytes.items() if kind not in self.components)
            gib = free / 2**30
            return 512 if gib >= 24 else 384 if gib >= 16 else 256 if gib >= 12 else 128
        return 256

    def _decode(self, vae, latents):
        chunk_size, overlap = self._chunk_size(), 64
        while chunk_size - 2 * overlap <= 0 and overlap > 4:
            overlap //= 2
        if latents.shape[-1] <= chunk_size:
            return vae.decode(latents).sample
        stride = chunk_size - 2 * overlap
        pieces = []
        factor = None
        for core_start in range(0, latents.shape[-1], stride):
            core_end = min(core_start + stride, latents.shape[-1])
            start, end = max(0, core_start - overlap), min(latents.shape[-1], core_end + overlap)
            chunk = vae.decode(latents[:, :, start:end]).sample
            if factor is None:
                factor = chunk.shape[-1] / (end - start)
            trim_start = int(round((core_start - start) * factor))
            trim_end = int(round((end - core_end) * factor))
            pieces.append(chunk[:, :, trim_start:chunk.shape[-1] - trim_end].cpu())
            del chunk
        audio = torch.cat(pieces, dim=-1)
        # Official >=24 GiB decode keeps waveforms on CUDA for the peak limiter.
        return audio.to(self.device) if chunk_size == 512 else audio

    @torch.inference_mode()
    def generate(self, caption, seconds, seed, progress=None):
        validate_request(caption, seconds, seed)
        self.stage_times = {}
        if progress:
            progress(0.0, desc="Planning instrumental music")
        from .lm import InstrumentalLM
        # Official single-item pt path uses the global torch RNG; seeds=[...]
        # is only honored for batches >1 upstream. Seed that RNG explicitly on
        # both sides of the reference comparison. Restore caller RNG afterwards.
        devices = [self.device.index if self.device.index is not None else torch.cuda.current_device()] if self.device.type == "cuda" else []
        with self._stage("lm"), self._component("lm") as model:
            planner = InstrumentalLM(model, self.lm_tokenizer, self.lm_processor, self.device)
            with torch.random.fork_rng(devices=devices):
                torch.manual_seed(seed)
                metadata, codes, times = planner.generate(caption, seconds)
            self.last_lm_tokens = planner.tokens
            self.last_metadata = metadata.copy()
            self.stage_times.update(times)
            del planner, model
            if self.sequential:
                # FSM masks are the only GPU tensors retained by the planner's
                # shared processor; return them to CPU before the LM unload.
                for name, value in vars(self.lm_processor).items():
                    if isinstance(value, torch.Tensor):
                        setattr(self.lm_processor, name, value.cpu())
        # Match _update_metadata_from_lm + use_cot_caption/language defaults.
        # parse_lm_output stores "language", while the updater reads
        # "vocal_language". Keep the official effective unknown language.
        dit_caption = metadata.get("caption", caption)
        language = metadata.get("vocal_language", "unknown")
        text = self.tokenizer(_prompt(dit_caption, seconds, metadata), padding="longest", truncation=True,
                              max_length=256, return_tensors="pt")
        lyrics = self.tokenizer(f"# Languages\n{language}\n\n# Lyric\n[Instrumental]<|endoftext|>",
                                padding="longest", truncation=True, max_length=2048, return_tensors="pt")
        with self._stage("text"), self._component("text") as encoder:
            hidden = encoder(input_ids=text.input_ids.to(self.device), lyric_attention_mask=None).last_hidden_state
            lyric_hidden = encoder.embed_tokens(lyrics.input_ids.to(self.device))
            del encoder
        silence = self.silence.to(self.device)
        with self._stage("dit"), self._component("dit") as dit:
            code_ids = [int(v) for v in re.findall(r"<\|audio_code_(\d+)\|>", codes)]
            indices = torch.tensor(code_ids, device=self.device, dtype=torch.long).unsqueeze(0).unsqueeze(-1)
            quantized = dit.tokenizer.quantizer.get_output_from_indices(indices)
            hints = dit.detokenizer(quantized.to(self.dtype))
            frames = max(128, hints.shape[1])
            if hints.shape[1] < frames:
                hints = torch.cat([hints, silence[:, :frames-hints.shape[1]]], dim=1)
            src = hints
            pred = dit.generate_audio(
                text_hidden_states=hidden, text_attention_mask=text.attention_mask.to(self.device).bool(),
                lyric_hidden_states=lyric_hidden, lyric_attention_mask=lyrics.attention_mask.to(self.device).bool(),
                refer_audio_acoustic_hidden_states_packed=silence[:, :750].contiguous(),
                refer_audio_order_mask=torch.tensor([0], device=self.device, dtype=torch.long),
                src_latents=src, chunk_masks=torch.ones_like(src), seed=seed, progress=progress,
            )
            del dit, quantized, hints, indices
        del hidden, lyric_hidden, silence, src
        if not torch.isfinite(pred).all() or pred.abs().sum() == 0:
            raise RuntimeError("invalid_latents: nonfinite or zero DiT output")
        if progress:
            progress(0.8, desc="Decoding music")
        with self._stage("vae"), self._component("vae") as vae:
            audio = self._decode(vae, pred.transpose(1, 2).contiguous()).float()
            del vae
        peak = audio.abs().amax(dim=[1, 2], keepdim=True)
        if torch.any(peak > 1.0):
            audio = audio / peak.clamp(min=1.0)
        audio = audio[0].cpu()
        # generate_music defaults to peak normalization at -1 dB, on CPU,
        # after the handler's peak limiter and before AudioSaver writes files.
        peak = torch.max(torch.abs(audio))
        if peak >= 1e-6:
            audio = audio.clone() * (10 ** (-1.0 / 20.0) / peak)
        if progress:
            progress(1.0, desc="Music ready")
        return audio


def load(root, device="cuda", mode="resident"):
    return MusicPipeline(root, device, mode)


def write_wav(path, audio, rate=SAMPLE_RATE):
    """Atomically write stereo [2, samples] audio as little-endian PCM16."""
    path = Path(path)
    if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
        raise BadRequest("sample rate must be a positive integer")
    if isinstance(audio, torch.Tensor):
        audio = audio.detach().cpu().numpy()
    audio = np.asarray(audio)
    if audio.ndim != 2 or audio.shape[0] != 2 or audio.shape[1] == 0 or not np.isfinite(audio).all():
        raise BadRequest("audio must be finite stereo [2, samples]")
    pcm = np.rint(np.clip(audio.T, -1, 1) * 32767).astype("<i2")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".partial", delete=False) as stream:
            temporary = stream.name
            with wave.open(stream, "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(pcm.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
