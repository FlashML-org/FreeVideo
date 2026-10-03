"""Bounded loading of the H3 video and audio VAEs, one tensor at a time.

Diffusers' from_pretrained first builds a complete CPU state dictionary. With
disable_mmap it reads each 4.71 GiB video VAE shard whole; its default
copy-on-write mapping is charged to Windows commit. Conditioning also reads
the 9.0 GiB decoder only to discard it. These loaders build the same model,
read only the selected tensors and place each directly on its target device.
The FP32 loads keep from_pretrained's reader per platform: copy-on-write
mappings on Linux, pread on Windows, where a mapping is charged in full.

Values and dtypes match from_pretrained. The one explicit experiment is the
optional decoder Linear compute cache: Linear weights are stored as FP16 for
autocast, while RMSNorm, LayerNorm, residual scales, register tokens and
post-quantization stay FP32. Original checkpoint bytes are untouched.
"""
from contextlib import contextmanager
from pathlib import Path


# The initializers from_pretrained skips while constructing its skeleton.
_INITIALIZERS = ('uniform_', 'normal_', 'trunc_normal_', 'constant_', 'xavier_uniform_', 'xavier_normal_',
                 'kaiming_uniform_', 'kaiming_normal_', 'uniform', 'normal', 'xavier_uniform',
                 'xavier_normal', 'kaiming_uniform', 'kaiming_normal')


@contextmanager
def parameters_on_meta():
    """Construct a model as from_pretrained does: parameters on meta, buffers real.

    Each parameter moves to meta as it is registered, so neither storage nor
    initialization is spent on it. Buffers and other constructor-computed
    tensors keep their real CPU values: the decoder's RoPE frequencies are
    not stored in the checkpoint.
    """
    import torch
    register = torch.nn.Module.register_parameter
    initializers = {name: getattr(torch.nn.init, name) for name in _INITIALIZERS if hasattr(torch.nn.init, name)}

    def register_on_meta(module, name, param):
        register(module, name, param)
        if param is not None:
            value = module._parameters[name]
            options = dict(value.__dict__, requires_grad=param.requires_grad)
            module._parameters[name] = type(value)(value.to('meta'), **options)

    def skip(tensor, *args, **kwargs):
        return tensor

    torch.nn.Module.register_parameter = register_on_meta
    for name in initializers:
        setattr(torch.nn.init, name, skip)
    try:
        yield
    finally:
        torch.nn.Module.register_parameter = register
        for name, function in initializers.items():
            setattr(torch.nn.init, name, function)


def skeleton(cls, directory):
    config = cls.load_config(str(directory), local_files_only=True)
    with parameters_on_meta():
        return cls.from_config(config)


def _file_order(path):
    """Tensor names by data offset, so a seek-bound disk reads each shard forward."""
    import json
    import struct
    with Path(path).open('rb') as stream:
        length = struct.unpack('<Q', stream.read(8))[0]
        if not 2 <= length <= min(100_000_000, Path(path).stat().st_size - 8):
            raise ValueError('Invalid safetensors header size')
        header = json.loads(stream.read(length))
    header.pop('__metadata__', None)
    return sorted(header, key=lambda name: header[name]['data_offsets'][0])


def _owns_storage(tensor):
    return tensor.storage_offset() == 0 and tensor.untyped_storage().nbytes() == tensor.numel() * tensor.element_size()


def load_state(model, paths, *, device_for_name, skip=(), linear_dtype=None, mapped=False):
    """Read one tensor at a time, with no retained full state dictionary.

    Each parameter and persistent buffer of `model` comes from exactly one
    checkpoint tensor; checkpoint names under `skip` belong to removed modules
    and are not read. All keys and shapes are checked before reading payloads,
    and a file that changes while it is read fails closed. Values take the
    skeleton's dtype first, as from_pretrained does, so a non-FP32 checkpoint
    keeps that rounding step. `linear_dtype` additionally stores Linear
    parameters in that dtype. A 'meta' placement records only metadata.

    By default every tensor is read with pread: no file mapping exists, which
    Windows would charge to commit in full, and a CPU tensor owns exactly its
    bytes. `mapped` reads through copy-on-write mappings as from_pretrained
    does on Linux, without its extra copy: CPU tensors stay views of one
    long-lived mapping (clean page cache the kernel can reclaim and the RAM
    guard credits), and uploads use a short-lived mapping per shard, so their
    pages do not stay mapped.
    """
    import torch
    from safetensors import safe_open
    from .tensor_io import open_tensors
    expected = dict(model.state_dict(keep_vars=True))
    skip = tuple(skip)
    files, sources = {}, {}
    for path in sorted(Path(p) for p in paths):
        stamp = path.stat()
        files[path] = (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
        with open_tensors(str(path), framework='pt') as handle:
            for name in handle.keys():
                if name.startswith(skip):
                    continue
                if name not in expected or name in sources:
                    raise ValueError('Unexpected or duplicate VAE weight: ' + name)
                metadata = handle.get_slice(name)
                if tuple(metadata.get_shape()) != tuple(expected[name].shape):
                    raise ValueError('VAE weight shape mismatch: ' + name)
                if not (metadata.get_dtype().startswith('F') or metadata.get_dtype() == 'BF16'):
                    raise ValueError('VAE weight is not floating point: ' + name)
                sources[name] = path
    if set(sources) != set(expected):
        raise ValueError('Missing VAE weights: ' + ', '.join(sorted(set(expected) - set(sources))))
    converted, loaded, mapped_bytes, streamed_count, streamed_bytes = 0, 0, 0, 0, 0
    for path, stamp in files.items():
        names = [name for name in _file_order(path) if sources.get(name) == path]
        if not names:
            continue
        # A pread reader owns only each requested tensor's bytes and keeps no
        # file mapping, so one handle per shard retains nothing consumed.
        with (safe_open(str(path), framework='pt', device='cpu') if mapped
              else open_tensors(str(path), framework='pt')) as handle:
            views = None
            for name in names:
                parent, _, leaf = name.rpartition('.')
                owner = model.get_submodule(parent) if parent else model
                reference = expected[name]
                parameter = leaf in owner._parameters
                dtype = (linear_dtype if linear_dtype is not None and parameter
                         and isinstance(owner, torch.nn.Linear) else reference.dtype)
                device = torch.device(device_for_name(name))
                if device.type == 'meta':
                    # Only metadata is needed until a streamed layer executes.
                    # Reading the whole CPU remainder here defeats streaming even
                    # if its storage is discarded by the later offloader.
                    target = torch.empty(reference.shape, dtype=dtype, device='meta')
                    streamed_count += 1
                    streamed_bytes += target.numel() * target.element_size()
                else:
                    view = mapped and device.type == 'cpu'
                    if view and views is None:
                        views = safe_open(str(path), framework='pt', device='cpu')
                    source = (views if view else handle).get_tensor(name)
                    if not source.is_floating_point():
                        raise ValueError('VAE weight is not floating point: ' + name)
                    # from_pretrained converts to the skeleton's dtype, including
                    # the rounding of a non-FP32 checkpoint tensor, before any cache.
                    value = source.to(dtype=reference.dtype)
                    # Otherwise a CPU result is copied unless it owns exactly its
                    # own bytes, so a small tensor never retains a read buffer.
                    target = value.to(device=device, dtype=dtype, copy=device.type == 'cpu' and not view
                                      and not _owns_storage(value))
                    converted += int(dtype != reference.dtype)
                    loaded += target.numel() * target.element_size()
                    mapped_bytes += target.numel() * target.element_size() if view and target is source else 0
                    del source, value
                if parameter:
                    owner._parameters[leaf] = torch.nn.Parameter(target, requires_grad=False)
                else:
                    owner._buffers[leaf] = target
                del target
            del views
        current = path.stat()
        if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != stamp:
            raise ValueError('VAE checkpoint changed during preparation: ' + str(path))
    cached = linear_dtype is not None
    return dict(linear_parameter_count=converted, prepared_parameter_bytes=loaded,
                mapped_parameter_bytes=mapped_bytes,
                streamed_parameter_count=streamed_count, streamed_parameter_bytes=streamed_bytes,
                source_policy='Original FP32 checkpoint, one tensor at a time',
                compute_policy=('FP16 Linear weights; FP32 normalization, residual scales and other parameters'
                                if cached else 'Original FP32 parameters'))


def load_parameters(model, paths, *, device_for_name, linear_fp16=True, mapped=False):
    """The decoder side of the shared video VAE shards; encoder tensors are not read."""
    import torch
    return load_state(model, paths, device_for_name=device_for_name, skip=('encoder.', 'quant_conv.'),
                      linear_dtype=torch.float16 if linear_fp16 else None, mapped=mapped)


def _mapped():
    """from_pretrained's reader on this platform: mapped on Linux, never on Windows."""
    from .system import windows
    return not windows()


def load_video_decoder(base, *, resident_blocks, weight_source=None, linear_fp16=True, device='cuda'):
    """Place the selected decoder prefix directly on CUDA without a CPU copy.

    Later blocks stay on the CPU, or bind to `weight_source` without reading.
    Without the Linear cache every parameter keeps from_pretrained's FP32.
    """
    from diffusers import AutoencoderKLMiniMaxH3
    directory = Path(base) / 'vae'
    model = skeleton(AutoencoderKLMiniMaxH3, directory)
    if type(resident_blocks) is not int or not 0 <= resident_blocks <= len(model.decoder.transformer_blocks):
        raise ValueError('Invalid VAE resident block count')
    if weight_source is not None and weight_source.prefixes != tuple(
            f'decoder.transformer_blocks.{index}.' for index in range(
                resident_blocks, len(model.decoder.transformer_blocks))):
        raise ValueError('Streamed VAE source does not match the offloaded layer order')
    model.encoder = None
    model.quant_conv = None
    if any(value.is_meta for value in model.buffers()):
        raise ValueError('Unexpected unmaterialized VAE buffer')
    def placement(name):
        prefix = 'decoder.transformer_blocks.'
        if name.startswith(prefix) and int(name[len(prefix):].split('.', 1)[0]) >= resident_blocks:
            return 'meta' if weight_source is not None else 'cpu'
        return device
    # The FP32 decoder keeps from_pretrained's storage and reads on each
    # platform. On Linux offloaded CPU blocks stay clean mapped page cache,
    # which the RAM guard credits and the kernel can reclaim; Windows would
    # charge a mapping whole to commit, so there they are private copies, as
    # disable_mmap made them. The Linear cache converts every tensor it reads.
    metrics = load_parameters(model, directory.glob('*.safetensors'), device_for_name=placement,
                              linear_fp16=linear_fp16, mapped=not linear_fp16 and _mapped())
    if weight_source is not None:
        from .offload import initialize_streamed_layer
        for index, layer in enumerate(list(model.decoder.transformer_blocks)[resident_blocks:]):
            initialize_streamed_layer(layer, weight_source, index)
    model.eval().requires_grad_(False)
    return model, metrics


def load_video_encoder(base, *, before_upload=None, device='cuda'):
    """The conditioning encoder and quant_conv in FP32; decoder tensors are not read.

    `before_upload(model)` runs on the meta-parameter model, whose tensor sizes
    are final, before any weight reaches the device.
    """
    from diffusers import AutoencoderKLMiniMaxH3
    directory = Path(base) / 'vae'
    model = skeleton(AutoencoderKLMiniMaxH3, directory)
    model.decoder = None
    model.post_quant_conv = None
    if before_upload is not None:
        before_upload(model)
    metrics = load_state(model, directory.glob('*.safetensors'), device_for_name=lambda name: device,
                         skip=('decoder.', 'post_quant_conv.'), mapped=_mapped())
    model.to(device).eval().requires_grad_(False)
    return model, metrics


def load_audio_vae(base, *, before_upload=None, device='cuda'):
    """The complete audio VAE in FP32, including the buffers stored in its checkpoint."""
    from diffusers import AutoencoderKLMiniMaxH3Audio
    directory = Path(base) / 'audio_vae'
    model = skeleton(AutoencoderKLMiniMaxH3Audio, directory)
    if before_upload is not None:
        before_upload(model)
    metrics = load_state(model, directory.glob('*.safetensors'), device_for_name=lambda name: device,
                         mapped=_mapped())
    model.to(device).eval().requires_grad_(False)
    return model, metrics
