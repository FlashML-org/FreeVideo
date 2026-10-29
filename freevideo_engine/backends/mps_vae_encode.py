"""Bound the pending Metal work and the live tensors of the original FP32 input-video encoder."""
from contextlib import contextmanager


def _lean_conv_ok(conv):
    return (getattr(conv, 'spatial_padding', None) == 1 and getattr(conv, 'temporal_padding', None) in (0, 2)
            and getattr(conv, 'spatial_padding_mode', None) == 'reflect' and conv.groups == 1
            and tuple(conv.padding) == (0, 0, 0) and conv.padding_mode == 'zeros')


def _is_resnet(module):
    names = ('norm1', 'conv1', 'norm2', 'conv2', 'conv_shortcut')
    return (type(module).__name__ == 'MiniMaxH3VideoResnetBlock3d' and all(hasattr(module, n) for n in names)
            and _lean_conv_ok(module.conv1) and _lean_conv_ok(module.conv2)
            and type(module.norm1).__name__ == 'MiniMaxH3VideoGroupNorm'
            and type(module.norm2).__name__ == 'MiniMaxH3VideoGroupNorm')


class _Hold:
    """The only reference to a tile-sized activation between two encoder stages.

    A caller's own variable or argument would keep the previous activation alive
    through the whole stage; the stage takes it out of here and drops it.
    """
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value

    def take(self):
        value, self.value = self.value, None
        return value


def _norm_silu(norm, box):
    """silu(norm(x)) as a (B, T, C, H, W) tensor viewed back as (B, C, T, H, W).

    `box` is a one-element list holding x; its copy is released as soon as the
    folded input exists, unless the caller keeps another reference.

    The pinned group norm folds the frame axis into the batch; the same call is
    made on the same values, only the intermediate copies are released early and
    the elementwise SiLU runs in place.
    """
    import torch
    x = box.pop()
    batch, channels, frames, height, width = x.shape
    folded = x.permute(0, 2, 1, 3, 4).contiguous().view(batch * frames, channels, 1, height, width)
    del x
    normed = torch.nn.GroupNorm.forward(norm, folded)
    del folded
    normed = normed.view(batch, frames, channels, height, width)
    return torch.nn.functional.silu(normed, inplace=True).permute(0, 2, 1, 3, 4)


def _padded(source, temporal):
    """`source` with a 1-pixel reflect border and `temporal` leading zero frames in one buffer.

    Same values as reflect pad followed by a constant temporal pad, written
    without the intermediate spatially padded copy.
    """
    import torch
    batch, channels, frames, height, width = source.shape
    out = torch.empty((batch, channels, frames + temporal, height + 2, width + 2),
                      dtype=source.dtype, device=source.device)
    if temporal:
        out[:, :, :temporal] = 0
    body = out[:, :, temporal:]
    body[:, :, :, 1:-1, 1:-1] = source
    body[:, :, :, 0, 1:-1] = source[:, :, :, 1, :]
    body[:, :, :, -1, 1:-1] = source[:, :, :, -2, :]
    body[:, :, :, :, 0] = body[:, :, :, :, 2]
    body[:, :, :, :, -1] = body[:, :, :, :, -3]
    return out


def _downsample_ok(module):
    conv = getattr(module, 'conv', None)
    return (type(module).__name__ == 'MiniMaxH3VideoDownsample3d' and conv is not None
            and getattr(module, 'spatial_stride', None) == 2 and getattr(module, 'spatial_padding_mode', None) == 'reflect'
            and getattr(conv, 'spatial_padding', None) == 0 and getattr(conv, 'temporal_padding', None) == 2
            and conv.groups == 1 and tuple(conv.padding) == (0, 0, 0) and conv.padding_mode == 'zeros')


def _lean_downsample(module, fence):
    """Reflect pad of one bottom/right pixel and the causal temporal zero pad in one buffer."""
    import torch
    def forward(hidden_states):
        if isinstance(hidden_states, _Hold):
            hidden_states = hidden_states.take()
        batch, channels, frames, height, width = hidden_states.shape
        padded = torch.empty((batch, channels, frames + 2, height + 1, width + 1),
                             dtype=hidden_states.dtype, device=hidden_states.device)
        padded[:, :, :2] = 0
        body = padded[:, :, 2:]
        body[:, :, :, :-1, :-1] = hidden_states
        body[:, :, :, -1, :-1] = hidden_states[:, :, :, -2, :]
        del hidden_states
        body[:, :, :, :, -1] = body[:, :, :, :, -3]
        hidden = torch.nn.Conv3d.forward(module.conv, padded)
        del padded, body
        fence()
        return hidden
    return forward


def _lean_resnet(block, fence, stats):
    """The original resnet forward with at most three activation-sized tensors alive.

    Every convolution still receives a contiguous, identically padded tensor and
    the same weights; the residual sum is the same two operands. With
    `stats['residual_offload']` the block input waits in host memory while the
    block runs (an exact copy), which keeps one more tile-sized tensor off the
    device at the cost of a copy there and back.
    """
    import torch
    def forward(hidden_states):
        if isinstance(hidden_states, _Hold):
            hidden_states = hidden_states.take()
        device = hidden_states.device
        offload = bool(stats.get('residual_offload')) and block.conv_shortcut is None
        residual = hidden_states.to('cpu', copy=True) if offload else hidden_states
        box = [hidden_states]
        del hidden_states
        activated = _norm_silu(block.norm1, box)
        padded = _padded(activated, block.conv1.temporal_padding)
        del activated
        hidden = torch.nn.Conv3d.forward(block.conv1, padded)
        del padded
        fence()
        box = [hidden]
        del hidden
        activated = _norm_silu(block.norm2, box)
        padded = _padded(activated, block.conv2.temporal_padding)
        del activated
        hidden = torch.nn.Conv3d.forward(block.conv2, padded)
        del padded
        fence()
        if block.conv_shortcut is not None:
            residual = block.conv_shortcut(residual)
        return hidden.add_(residual.to(device) if offload else residual)
    return forward


def _lean_encoder_ok(model):
    encoder = getattr(model, 'encoder', None)
    names = ('conv_in', 'down_blocks', 'norm_out', 'conv_out')
    if encoder is None or not all(hasattr(encoder, n) for n in names) or getattr(model, 'quant_conv', None) is None:
        return False
    blocks = list(encoder.down_blocks)
    return bool(blocks) and all(hasattr(b, 'resnets') and hasattr(b, 'downsamplers') and len(b.resnets)
                                and all(_is_resnet(r) for r in b.resnets)
                and all(_downsample_ok(d) for d in (b.downsamplers or ())) for b in blocks)


def _lean_block(block):
    def forward(held):
        for resnet in block.resnets:
            held.value = resnet(held)
        if block.downsamplers is not None:
            for downsampler in block.downsamplers:
                held.value = downsampler(held)
        return held
    return forward


def _lean_encoder(encoder, torch):
    def forward(hidden_states):
        held = _Hold(encoder.conv_in(hidden_states))
        for block in encoder.down_blocks:
            block(held)
        hidden_states = held.take()
        hidden_states = torch.nn.functional.silu(encoder.norm_out(hidden_states))
        return encoder.conv_out(hidden_states)
    return forward


def _residency(model, device, torch):
    """Hooks that hold each encoder stage's weights on `device` only while it runs.

    The weights are the same FP32 values read from the checkpoint; only the
    stage being computed is resident on the device, so the large late-stage
    weights are not alive while the early stages hold the largest activations.
    Returns (hooks, host copies still on the device, uploaded byte counter).
    """
    encoder = model.encoder
    units = [encoder.conv_in, *encoder.down_blocks, encoder.norm_out, encoder.conv_out, model.quant_conv]
    covered = {id(p) for unit in units for p in unit.parameters()}
    if any(id(p) not in covered for p in model.parameters()):
        return None
    host, uploaded, handles = {}, [0], []
    def upload(unit, inputs):
        for parameter in unit.parameters():
            # A failed run leaves its stage here with the device copy in place;
            # the host copy kept from the first upload stays the source.
            host.setdefault(parameter, parameter.data)
            parameter.data = host[parameter].to(device)
            uploaded[0] += parameter.numel() * parameter.element_size()
    def release(unit, inputs, output):
        for parameter in unit.parameters():
            parameter.data = host.pop(parameter)
    for unit in units:
        handles.append(unit.register_forward_pre_hook(upload))
        handles.append(unit.register_forward_hook(release))
    return handles, host, uploaded


@contextmanager
def bounded_encoder(model, device=None):
    """Finish each convolution before submitting the next encoder operation.

    MPS can retain large temporary allocations for pending 3D convolutions even
    after their Python tensors are gone. Fencing the original operators bounds
    that lifetime without changing weights, precision, tiles or posterior noise.
    The residual blocks additionally build each padded convolution input in one
    buffer, run the SiLU in place, add the residual in place and let each stage
    release the previous activation: the same values, with fewer tile-sized
    tensors alive at the peak. With `device`, the model's weights stay on the
    host (the checkpoint mapping) and each stage uploads its own for its run.
    Hooks and instance forwards belong to this VAE instance and are always
    removed.
    """
    import torch
    if torch.is_grad_enabled():
        raise RuntimeError('Bounded MPS input encoding requires inference')
    modules = [module for module in model.modules() if isinstance(module, torch.nn.Conv3d)]
    if not modules:
        raise ValueError('Expected the H3 input encoder convolution modules')
    handles, patched, host, staged, uploaded = [], [], {}, None, [0]
    stats = dict(policy='Complete original FP32 convolutions and release unused MPS allocations',
                 convolution_completions=0, convolution_modules=len(modules), lean_resnet_blocks=0,
                 staged_weight_bytes=0)
    def fence():
        torch.mps.synchronize()
        torch.mps.empty_cache()
        stats['convolution_completions'] += 1
    def complete(module, inputs, output):
        fence()
    try:
        for module in modules:
            handles.append(module.register_forward_hook(complete))
        if _lean_encoder_ok(model):
            for block in model.encoder.down_blocks:
                for resnet in block.resnets:
                    resnet.forward = _lean_resnet(resnet, fence, stats)
                    patched.append(resnet)
                for downsampler in block.downsamplers or ():
                    downsampler.forward = _lean_downsample(downsampler, fence)
                    patched.append(downsampler)
                block.forward = _lean_block(block)
                patched.append(block)
            model.encoder.forward = _lean_encoder(model.encoder, torch)
            patched.append(model.encoder)
            stats['lean_resnet_blocks'] = sum(len(b.resnets) for b in model.encoder.down_blocks)
            if device is not None:
                staged = _residency(model, device, torch)
                if staged is not None:
                    hooks, host, uploaded = staged
                    handles.extend(hooks)
        if device is not None and staged is None:
            # Not the pinned structure: whole model on the device, fences only.
            model.to(device)
        yield stats
    finally:
        if staged is not None:
            stats['staged_weight_bytes'] = uploaded[0]
        for handle in handles:
            handle.remove()
        for parameter, data in host.items():
            parameter.data = data
        for module in patched:
            module.__dict__.pop('forward', None)
