"""Construction boundary for linear layers and their weight-only int8 replacement.

Handwritten DiT/FSQ/MLP projections all use linear(); native Transformers model
creation is kept in build_qwen(). Int8Linear is applied to decoder linears only
(quantizable), the same layers upstream quantizes for small cards.
"""
import torch
from torch import nn
from transformers.models.qwen3.modeling_qwen3 import Qwen3MLP

LINEAR = nn.Linear
_FP32_EPS = torch.finfo(torch.float32).eps


def linear(*args, **kwargs):
    return LINEAR(*args, **kwargs)


def quantizable(name, module):
    """Upstream's int8 filter: decoder linears, never the audio tokenizer or detokenizer."""
    parts = name.split(".")
    return isinstance(module, nn.Linear) and parts[0] == "decoder" and not {"tokenizer", "detokenizer"} & set(parts)


class Int8Linear(nn.Module):
    """Weight-only int8 linear with one symmetric scale per output row.

    Rounding and the matmul follow torchao Int8WeightOnlyConfig (the upstream
    int8 option) step for step, so qweight, scale and outputs are bit-identical
    to it without the extra package.
    """

    def __init__(self, qweight, scale, bias=None):
        super().__init__()
        self.out_features, self.in_features = qweight.shape
        self.register_buffer("qweight", qweight)
        self.register_buffer("scale", scale)
        self.bias = None if bias is None else nn.Parameter(bias, requires_grad=False)

    @classmethod
    def from_weight(cls, weight, bias=None):
        # The scale is computed in the weight dtype, then kept as float32.
        max_abs = torch.maximum(-weight.amin(dim=1, keepdim=True).clamp(max=0),
                                weight.amax(dim=1, keepdim=True).clamp(min=0))
        scale = (max_abs / 127.5).clamp(min=_FP32_EPS).to(torch.float32)
        qweight = torch.clamp(torch.round(weight * (1.0 / scale)), -128, 127).to(torch.int8)
        return cls(qweight, scale, bias)

    def forward(self, x):
        dtype = x.dtype
        out = torch.mm(x.reshape(-1, x.shape[-1]), self.qweight.t().to(dtype))
        out = out * self.scale.to(out.dtype).flatten()
        out = out.reshape(*x.shape[:-1], self.out_features)
        if self.bias is not None:
            out += self.bias
        return out.to(dtype)


class MusicMLP(Qwen3MLP):
    def __init__(self, config):
        nn.Module.__init__(self)
        from transformers.activations import ACT2FN
        self.config = config
        self.hidden_size = config.hidden_size
        self.intermediate_size = config.intermediate_size
        self.gate_proj = linear(self.hidden_size, self.intermediate_size, bias=False)
        self.up_proj = linear(self.hidden_size, self.intermediate_size, bias=False)
        self.down_proj = linear(self.intermediate_size, self.hidden_size, bias=False)
        self.act_fn = ACT2FN[config.hidden_act]


def build_qwen(config=None, *, path=None, device, dtype):
    """Only boundary that constructs native Transformers projections.

    An int8 port can replace native linears here before returning the model;
    the sampling code does not need to know about their implementation.
    """
    from transformers import Qwen3ForCausalLM, Qwen3Model
    if path is not None:
        model, info = Qwen3ForCausalLM.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False,
            dtype=dtype, attn_implementation="sdpa", output_loading_info=True,
        )
        if any(info.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
            raise RuntimeError(f"checkpoint_keys: LM: {info}")
        # Upstream loads F32, then .to(device).to(dtype), which also casts
        # the nonpersistent RoPE buffer. from_pretrained(dtype=...) alone
        # intentionally leaves that buffer F32 in Transformers 5.
        return model.to(device=device, dtype=dtype).eval().requires_grad_(False)
    return Qwen3Model(config)
