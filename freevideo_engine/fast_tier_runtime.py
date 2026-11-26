"""The fastest mode's output heads, modulation and replaced matrices, installed on one Engine's model.

Only that Engine's in-memory modules change; the weight files and every other
Engine are untouched. Modulation follows the 8-dimensional form of the pruned H3:
a time coordinate interpolated from a 1025-row table, a projection and a bias.
The adapter also replaces three whole matrices in each of the last four blocks, and
on the last step its features are kept for the pixel head (dwd_head), which runs
after the transformer is released.
"""
import hashlib
import re
import types

import torch

from .fast_tier import ADAPTER, CONSTANTS, FILES, files
from .paths import installed_model_root
from .tensor_io import open_tensors


class Clock(torch.nn.Module):
    """The time coordinates of the current forward, shared by every block."""

    def __init__(self, table):
        super().__init__()
        self.register_buffer('table', table)
        self.coordinates = None
        self.output = None

    def set(self, timestep):
        table = self.table
        position = timestep.to(table.device).float().clamp(0., 1.) * (len(table) - 1)
        lower = position.floor().long().clamp(max=len(table) - 2)
        self.coordinates = torch.lerp(table[lower], table[lower + 1], (position - lower).unsqueeze(1))
        self.output = None


class Modulation(torch.nn.Module):
    """One block's six modulation vectors. Buffers are named like the table steps they replace."""

    def __init__(self, clock, weight, bias):
        super().__init__()
        object.__setattr__(self, 'clock', clock)  # shared: owned by the model, not by each block
        self.register_buffer('step_weight', weight)
        self.register_buffer('step_bias', bias)

    def forward(self, temb):
        values = (self.clock.coordinates @ self.step_weight.t() + self.step_bias).to(torch.bfloat16)
        return values.view(-1, values.shape[-1] // 3).chunk(6, dim=-1)


def _rotate256(x):
    """The group-256 Hadamard of the ConvRot files (its own inverse): kron(H4, H4) / 4 on each 16x16 group."""
    h4 = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1], [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=torch.float32)
    h = torch.kron(h4, h4) / 4
    groups = x.reshape(x.shape[0], -1, 16, 16)
    return (h @ groups @ h).reshape(x.shape)


def _diffusers_name(key):
    key = re.sub(r'^blocks\.(\d+)\.attn\.out_proj\.', r'transformer_blocks.\1.attn.to_out.0.', key)
    key = re.sub(r'^blocks\.(\d+)\.mlp\.fc1\.', r'transformer_blocks.\1.ff.net.0.proj.', key)
    key = re.sub(r'^blocks\.(\d+)\.mlp\.fc2\.', r'transformer_blocks.\1.ff.net.2.', key)
    key = re.sub(r'^blocks\.(\d+)\.adaln_proj\.', r'transformer_blocks.\1.adaln_proj.', key)
    return (key.replace('final_layer.adaln_proj.linear.', 'norm_out.linear.').replace('final_layer.norm.weight', 'norm_out.norm.weight')
               .replace('final_layer.video_out.', 'proj_out.').replace('final_layer.audio_out.', 'audio_proj_out.'))


def adapter_tensors(path, wanted):
    """{diffusers name: tensor} of the adapter file for the names wanted(name) accepts.

    The INT8 ConvRot file names layers the original way and stores 44 matrices as int8
    with a per-row scale after the group-256 rotation; they come back as float16 (rows
    rotated back, fc1's halves in the diffusers [value; gate] order). Every other tensor
    in it is the BF16 file's, byte for byte. The low-rank factors are not read here.
    """
    out = {}
    with open_tensors(path) as source:
        for key in source.keys():
            if key.endswith('.comfy_quant') or key.endswith('.weight_scale') or '.lora_' in key:
                continue
            name = _diffusers_name(key)
            if not wanted(name):
                continue
            value = source.get_tensor(key)
            if value.dtype == torch.int8:
                value = _rotate256(value.float() * source.get_tensor(key + '_scale').float().reshape(-1, 1)).to(torch.float16)
                if '.mlp.fc1.' in key:
                    half = value.shape[0] // 2
                    value = torch.cat((value[half:], value[:half]))
            out[name] = value
    return out


# Whole matrices the adapter replaces: (its name, the module in a hybrid block).
REPLACED = (('attn.to_out.0', 'attn.orig.to_out.0'), ('ff.net.0.proj', 'ff.net.0.proj'), ('ff.net.2', 'ff.net.2'))


class Features:
    """The last step's features for the pixel head: the final block's video rows, layer-normed and modulated."""

    def __init__(self, clock, weight, bias, steps):
        self.clock, self.weight, self.bias, self.steps = clock, weight, bias, steps
        self.grid, self.count, self.hidden, self.value = None, 0, None, None

    def begin(self, grid):
        self.grid, self.count, self.hidden, self.value = grid, 0, None, None

    def keep_hidden(self, _module, _args, output):
        self.hidden = output

    def after_forward(self, _module, _args, kwargs, _output):
        self.count += 1
        hidden, self.hidden = self.hidden, None
        if self.count < self.steps or self.grid is None:
            return
        hidden = hidden if torch.is_tensor(hidden) else hidden[0]
        hidden = hidden[0] if hidden.dim() == 3 else hidden
        rows = kwargs['video_indices']
        rows = rows[-self.grid[0] * self.grid[1] * self.grid[2]:]  # conditioning rows come first
        # The DWD norm takes the SiLU of the time coordinate, as in the measured runs.
        scale, shift = (torch.nn.functional.silu(self.clock.coordinates) @ self.weight.t() + self.bias).to(
            torch.bfloat16).chunk(2, dim=-1)
        steps = kwargs['timestep_indices'][rows]
        picked = hidden[rows]
        normed = torch.nn.functional.layer_norm(picked.float(), (picked.shape[-1],), None, None, 1e-5).to(torch.bfloat16)
        value = normed * (1. + scale[steps]) + shift[steps]
        # Off the GPU, so the transformer can be released before the head runs.
        self.value = value.to('cpu')
        self.count = 0

    def take(self):
        value, grid, self.value = self.value, self.grid, None
        if value is None:
            raise RuntimeError('The fastest mode kept no features for its pixel head')
        return value, grid


class ReplacedLinear(torch.nn.Module):
    """One of the adapter's whole matrices, held outside the module's parameters and buffers.

    The block's own weights may be pinned, uploaded or streamed from the prepared files;
    these matrices are not in those files, so the offloader must not see them. On a
    resident block the weight lives on the GPU; on a streamed block it stays in pinned
    host memory and goes up for each call, one matrix at a time.
    """

    def __init__(self, weight):
        super().__init__()
        self.in_features, self.out_features = weight.shape[1], weight.shape[0]
        object.__setattr__(self, 'held', weight)

    def forward(self, x):
        weight = self.held if self.held.device == x.device else self.held.to(x.device, non_blocking=True)
        return torch.nn.functional.linear(x, weight)


class Runtime:
    def __init__(self, device):
        # Resolved here so that requests and reports carry the mode and no local path.
        paths = files(installed_model_root())
        self.device, self.adapter = device, paths[ADAPTER]
        constants = paths[CONSTANTS]
        if hashlib.sha256(constants.read_bytes()).hexdigest() != FILES[CONSTANTS][1]:
            raise ValueError('The fastest mode constants file changed')
        with open_tensors(constants) as source:
            self.bias = {key: source.get_tensor(key).float() for key in source.keys() if key.endswith('folded_bias')}
            table = source.get_tensor('time_embedder.table').float()
        self.clock = Clock(table.to(device))
        # Read once: opening the file for each of the fifty blocks is slow on Windows.
        names = {f'transformer_blocks.{index}.{name}.weight' for index in range(50) for name, _ in REPLACED}
        root = {'proj_out.weight', 'proj_out.bias', 'audio_proj_out.weight', 'audio_proj_out.bias', 'norm_out.norm.weight',
                'norm_out.linear.weight', 'norm_out.linear.bias', 'dwd_norm_out.linear.weight', 'dwd_norm_out.linear.bias'}
        tensors = adapter_tensors(self.adapter, lambda name: name in names or name in root or (
            name.startswith('transformer_blocks.') and '.adaln_proj.linear.' in name))
        self.weights = {key: value.float().to(device) for key, value in tensors.items() if key.endswith('.adaln_proj.linear.weight')}
        # The two-step adapter brings its own modulation bias for its last four blocks; it wins over the folded one.
        self.own_bias = {key: value.float() for key, value in tensors.items()
                         if key.startswith('transformer_blocks.') and key.endswith('.adaln_proj.linear.bias')}
        self.matrices = {key: value for key, value in tensors.items() if key in names}
        self.root = {key: value for key, value in tensors.items() if key in root}
        self.features = Features(self.clock, self.root['dwd_norm_out.linear.weight'].float().to(device),
                                 self.root['dwd_norm_out.linear.bias'].float().to(device), None)

    def modulation(self, index):
        prefix = f'transformer_blocks.{index}.adaln_proj.'
        folded = self.bias.pop(prefix + 'folded_bias')
        bias = self.own_bias.pop(prefix + 'linear.bias', folded)
        return Modulation(self.clock, self.weights.pop(prefix + 'linear.weight'), bias.to(self.device))

    def replace_matrices(self, index, block, resident):
        """This block's whole matrices from the adapter (BF16, no bias, as in H3). None for most blocks.

        resident: the block is uploaded to the GPU (the matrices go there too); otherwise
        they wait in pinned host memory.
        """
        replaced = 0
        for name, module_name in REPLACED:
            weight = self.matrices.pop(f'transformer_blocks.{index}.{name}.weight', None)
            if weight is None:
                continue
            parent, _, leaf = module_name.rpartition('.')
            weight = weight.to(torch.bfloat16)
            weight = weight.to(self.device) if resident else (weight.pin_memory() if torch.cuda.is_available() else weight)
            layer = ReplacedLinear(weight)
            owner = block.get_submodule(parent)
            if leaf.isdigit():
                owner[int(leaf)] = layer
            else:
                setattr(owner, leaf, layer)
            replaced += 1
        return replaced

    def install_root(self, model):
        device = self.device
        source = self.root
        for name in ('proj_out', 'audio_proj_out'):
            module = getattr(model, name)
            for part in ('weight', 'bias'):
                parameter = getattr(module, part)
                parameter.data = source[f'{name}.{part}'].to(device, parameter.dtype)
        norm = model.norm_out
        norm.norm.weight.data = source['norm_out.norm.weight'].to(device, norm.norm.weight.dtype)
        norm.register_buffer('fast_weight', source['norm_out.linear.weight'].float().to(device))
        norm.register_buffer('fast_bias', source['norm_out.linear.bias'].float().to(device))
        self.root = None
        norm.linear = torch.nn.Identity()  # the 2688-dimensional projection this replaces
        clock = self.clock

        def forward(module, hidden, temb, indices):
            if clock.output is None:
                clock.output = (clock.coordinates @ module.fast_weight.t() + module.fast_bias).to(
                    torch.bfloat16).chunk(2, dim=-1)
            shift, scale = clock.output
            hidden = module.norm(hidden)
            return hidden * (1. + scale.index_select(0, indices)) + shift.index_select(0, indices)
        norm.forward = types.MethodType(forward, norm)
        model.add_module('fast_clock', clock)
        model.register_forward_pre_hook(lambda _, args, kwargs: clock.set(kwargs['timestep']), with_kwargs=True)

    def install_features(self, model, steps):
        """Keep the last step's features; registered after the root so the clock is set first."""
        self.features.steps = steps
        model.transformer_blocks[-1].register_forward_hook(self.features.keep_hidden)
        model.register_forward_hook(self.features.after_forward, with_kwargs=True)
