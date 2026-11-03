"""T2: load the Comfy Qwen3-VL ConvRot encoder without a BF16 model copy.

The DiT loader is supplied by T1. Encoder parameters start on meta and each
checkpoint tensor is installed directly. Only the vocabulary is dequantized,
in bounded row chunks; CUDA projections use the existing engine Int8Linear.
No downloads occur here. Supply a local Qwen3-VL config (or ``config=``) and
local processor/tokenizer assets separately. Vision weights stay unopened
until ``model.load_vision()`` is called before the image-conditioned text stage.
"""

import json
import weakref
from pathlib import Path

import torch
from safetensors import safe_open
from .load_progress import TensorProgress


_CONVROT = {"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": 256}


def _open(path):
    """The encoder file, opened like the DiT and VAE (streamed_weights._open_safetensors).

    Windows charges a copy-on-write mapping of a whole file to system commit, 9.35 GB for this
    one, however little of it is read; the pread backend reads only the requested tensors.
    """
    from ..system import windows
    return safe_open(str(path), framework="pt", device="cpu", **({"backend": "pread"} if windows() else {}))


def _quant_metadata(st, name):
    value = st.get_tensor(name + ".comfy_quant")
    if value.dtype != torch.uint8 or value.ndim != 1:
        raise ValueError(f"Invalid comfy_quant JSON tensor: {name}")
    try:
        meta = json.loads(bytes(value.tolist()).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"Invalid comfy_quant JSON: {name}") from error
    if meta != _CONVROT:
        raise ValueError(f"Unsupported ConvRot format for {name}: {meta}")


def _row_scales(st, name, rows):
    scales = st.get_tensor(name + ".weight_scale")
    if scales.dtype != torch.float32 or tuple(scales.shape) not in ((rows,), (rows, 1), (1, rows)):
        raise ValueError(f"Expected FP32 per-row scales for {name}")
    scales = scales.reshape(-1).contiguous()
    if not torch.isfinite(scales).all() or (scales <= 0).any():
        raise ValueError(f"Invalid per-row scales for {name}")
    return scales


def _dequantize_vocabulary(st, name, shape, dtype, chunk_rows, reads=None):
    weight = st.get_slice(name + ".weight")
    if list(weight.get_shape()) != list(shape) or weight.get_dtype() != "I8" or shape[1] % 256:
        raise ValueError(f"Expected group-256 int8 vocabulary for {name}")
    scales = _row_scales(st, name, shape[0])
    if reads is not None:
        reads.tensors(name + ".weight_scale")
    # Exactly int8_ops.hadamard16: the regular symmetric, orthogonal H256,
    # rather than the Sylvester Hadamard convention used by some quantizers.
    h4 = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1], [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=torch.float32)
    h16 = torch.kron(h4, h4) / 4
    full = torch.empty(shape, dtype=dtype)
    for start in range(0, shape[0], chunk_rows):
        end = min(start + chunk_rows, shape[0])
        rotated = (weight[start:end].float() * scales[start:end, None]).reshape(-1, shape[1] // 256, 16, 16)
        full[start:end] = (h16 @ rotated @ h16).reshape(-1, shape[1]).to(dtype)
        if reads is not None:
            reads.advance((end - start) * shape[1])
    return full


def _text_name(model, name):
    candidates = [name]
    if name.startswith("model.") and not name.startswith("model.language_model."):
        candidates.append(name.replace("model.", "model.language_model.", 1))
    for candidate in candidates:
        try:
            model.get_submodule(candidate)
            return candidate
        except AttributeError:
            pass
    raise ValueError(f"Unknown Qwen3-VL checkpoint module: {name}")


def _assign_tensor(model, name, value, dtype):
    prefix, _, leaf = name.rpartition(".")
    try:
        module = model.get_submodule(prefix) if prefix else model
    except AttributeError as error:
        raise ValueError(f"Unknown checkpoint tensor: {name}") from error
    target = module._parameters.get(leaf)
    is_parameter = target is not None
    if not is_parameter:
        target = module._buffers.get(leaf)
    if target is None or target.shape != value.shape:
        raise ValueError(f"Unknown tensor or shape mismatch: {name}, {tuple(value.shape)}")
    if not value.is_floating_point():
        raise ValueError(f"Unexpected non-floating unquantized tensor: {name}")
    value = value.to(dtype=dtype)
    if is_parameter:
        module._parameters[leaf] = torch.nn.Parameter(value, requires_grad=False)
    else:
        module._buffers[leaf] = value


def _install_text_weights(model, st, *, dtype, chunk_rows, reads=None):
    keys = set(st.keys())
    handled = set()
    for key in sorted(k for k in keys if k.endswith(".comfy_quant")):
        name = key[:-len(".comfy_quant")]
        if name.startswith("model.visual.") or name == "lm_head":
            continue
        _quant_metadata(st, name)
        if reads is not None:
            reads.tensors(key)
        full_name = _text_name(model, name)
        module = model.get_submodule(full_name)
        shape = module.weight.shape if isinstance(module, (torch.nn.Linear, torch.nn.Embedding)) else ()
        if not shape:
            raise ValueError(f"Quantized target is not Linear/Embedding: {name}")
        if isinstance(module, torch.nn.Embedding):
            module.weight = torch.nn.Parameter(
                _dequantize_vocabulary(st, name, shape, dtype, chunk_rows, reads), requires_grad=False
            )
        else:
            from .int8_linear import ops
            Int8Linear = ops().Int8Linear

            weight = st.get_tensor(name + ".weight")
            if weight.dtype != torch.int8 or weight.shape != shape or shape[1] % 256:
                raise ValueError(f"Expected group-256 int8 matrix for {name}")
            scales = _row_scales(st, name, shape[0])
            replacement = Int8Linear.__new__(Int8Linear)
            torch.nn.Module.__init__(replacement)
            replacement.register_buffer("weight_int8", weight.contiguous())
            replacement.register_buffer("weight_scale", scales)
            replacement.original = torch.nn.Module()
            replacement.original.register_parameter("bias", module.bias)
            replacement.in_features, replacement.out_features = module.in_features, module.out_features
            replacement.input_dtype = dtype
            parent, _, child = full_name.rpartition(".")
            setattr(model.get_submodule(parent), child, replacement)
            if reads is not None:
                reads.tensors(name + ".weight", name + ".weight_scale")
        handled.update({key, name + ".weight", name + ".weight_scale"})

    for key in sorted(keys - handled):
        if key.startswith(("model.visual.", "lm_head.")):
            continue
        if key.endswith((".comfy_quant", ".weight_scale")):
            raise ValueError(f"Unpaired quantization tensor: {key}")
        name, _, leaf = key.rpartition(".")
        full_name = _text_name(model, name)
        module = model.get_submodule(full_name)
        if leaf == "weight" and hasattr(module, "weight_int8"):
            raise ValueError(f"Duplicate quantized weight: {key}")
        if leaf == "bias" and hasattr(module, "weight_int8"):
            full_name += ".original"
        _assign_tensor(model, full_name + "." + leaf, st.get_tensor(key), dtype)
        if reads is not None:
            reads.tensors(key)
    missing = [name for name, value in model.state_dict().items() if value.is_meta]
    if missing:
        raise ValueError("Missing text encoder tensors: " + ", ".join(missing))


def load_text_encoder(path, *, config=None, config_path=None, device="cpu", dtype=torch.bfloat16,
                      chunk_rows=8192, progress=None, mapped=False):
    """Return a Qwen3VLForConditionalGeneration with int8 language projections.

    ``config`` accepts a transformers Qwen3VLConfig or its dictionary. Otherwise
    ``config_path`` (default: checkpoint sibling config.json) must be local.
    ``lm_head`` is Identity because only hidden states are consumed. The lazy
    ``load_vision(device='cpu')`` method installs only BF16 visual tensors and
    returns that module, allowing the caller's placement policy to own moves.
    CPU supports loading/inspection; Int8Linear execution requires CUDA.
    ``mapped`` leaves tensors stored as they are on disk as views of a read-only mapping of the file.
    ``progress(done_bytes, total_bytes)`` counts language source bytes, including
    vocabulary chunks, and excludes the deferred vision tensors and logits head.
    """
    from accelerate import init_empty_weights
    from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration

    path = Path(path)
    if not isinstance(chunk_rows, int) or chunk_rows <= 0:
        raise ValueError("chunk_rows must be a positive integer")
    if dtype not in (torch.float32, torch.float16, torch.bfloat16):
        raise ValueError("Unsupported text encoder floating dtype")
    if config is None:
        config_file = Path(config_path) if config_path else path.with_name("config.json")
        if not config_file.exists():
            raise FileNotFoundError(f"Local Qwen3-VL config required: {config_file}")
        config = Qwen3VLConfig.from_pretrained(str(config_file), local_files_only=True)
    elif isinstance(config, dict):
        config = Qwen3VLConfig.from_dict(config)
    config._attn_implementation = "sdpa"
    config.text_config.use_cache = False
    with init_empty_weights():
        model = Qwen3VLForConditionalGeneration(config)
    # Retain the constructor, not the meta vision module or unused logits head.
    vision_type = type(model.model.visual)
    model.model.visual = None
    model.lm_head = torch.nn.Identity()
    with _open(path) as st:
        reads = (TensorProgress(st, progress, (key for key in st.keys()
                 if not key.startswith(("model.visual.", "lm_head.")))) if progress is not None else None)
        if mapped:   # CPU only: tensors stored as they are on disk stay views of the mapped file
            from .mapped import MappedCheckpoint
            st = MappedCheckpoint(st, path)
        _install_text_weights(model, st, dtype=dtype, chunk_rows=chunk_rows, reads=reads)
    model.eval().requires_grad_(False).to(device=device)
    model_ref = weakref.ref(model)

    def load_vision(device="cpu"):
        owner = model_ref()
        if owner is None:
            raise RuntimeError("Text encoder has been released")
        if owner.model.visual is not None:
            return owner.model.visual
        with init_empty_weights():
            visual = vision_type(config.vision_config)
        with _open(path) as st:
            visual_keys = [key for key in st.keys() if key.startswith("model.visual.")]
            for key in sorted(visual_keys):
                value = st.get_tensor(key)
                if value.dtype != torch.bfloat16:
                    raise ValueError(f"Expected BF16 visual tensor: {key}")
                _assign_tensor(visual, key[len("model.visual."):], value, torch.bfloat16)
            missing = [name for name, value in visual.state_dict().items() if value.is_meta]
            if missing:
                raise ValueError("Missing vision tensors: " + ", ".join(missing))
        visual.eval().requires_grad_(False).to(device=device)
        owner.model.visual = visual
        return visual

    model.load_vision = load_vision
    return model
