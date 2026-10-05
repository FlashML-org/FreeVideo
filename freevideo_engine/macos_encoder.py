"""Native Comfy H3 conditioning with bounded weight decoding for MPS.

The native tokenizer, layer-50 model, AWQ input smoothing and output contract
remain authoritative. Quantized checkpoint storage stays on the host; MPS
executes FP32 model operations, as the native H3 text forward requests. This
entry point is isolated from the existing CUDA encoder worker.
"""
import gc
import json
import os
from pathlib import Path
import sys
import time
import types


def nvfp4_weight(weight, dtype, device, *, rows=128):
    """Decode original E2M1 storage in bounded rows using FP32 scale arithmetic."""
    import torch
    from comfy_kitchen.float_utils import from_blocked
    if rows < 1 or weight._qdata.device.type != 'cpu':
        raise ValueError('MPS NVFP4 reader requires host storage and a positive row chunk')
    params, packed = weight._params, weight._qdata
    if getattr(params, 'transposed', False) or packed.dtype != torch.uint8 or packed.ndim != 2:
        raise ValueError('Unsupported NVFP4 storage layout')
    count, columns = packed.shape[0], packed.shape[1] * 2
    if columns % 16 or params.scale.numel() != 1:
        raise ValueError('Invalid NVFP4 scale geometry')
    scales = from_blocked(params.block_scale, num_rows=count, num_cols=columns // 16).float()
    lut = torch.tensor([0., .5, 1., 1.5, 2., 3., 4., 6., -0., -.5, -1., -1.5, -2., -3., -4., -6.])
    result = torch.empty((count, columns), dtype=dtype, device=device)
    for start in range(0, count, rows):
        part = packed[start:start + rows]
        indices = torch.stack((part >> 4, part & 15), dim=-1).long()
        decoded = lut[indices].reshape(len(part), -1, 16)
        # CUDA's native dequantizer multiplies block scale before tensor scale.
        decoded.mul_(scales[start:start + rows, :, None]).mul_(params.scale.float())
        result[start:start + rows].copy_(decoded.reshape(len(part), columns).to(dtype))
        del part, indices, decoded
    return result[:params.orig_shape[0], :params.orig_shape[1]]


def install_weight_reader(clip, backend, checkpoint, *, decoder=nvfp4_weight):
    """Adapt only this encoder instance; never patch the shared Comfy library."""
    import torch
    from comfy.quant_ops import QuantizedTensor, get_layout_class
    from .encoder_checkpoint import release_mapped_pages
    from .system import system_memory
    handles, restores = [], []
    stats = dict(linear_calls=0, embedding_calls=0, largest_weight_bytes=0,
                 weight_decode_seconds=0., host_storage='original read-only checkpoint',
                 weight_decode_seconds_scope='Host read/decode/submit time; Metal completion is included in encoding elapsed time')
    def guard():
        if system_memory()['available_bytes'] < 2**30:
            raise MemoryError('MPS encoding reached the 1 GiB physical RAM emergency floor')
    def linear(module, input, *args, **kwargs):
        if input.device.type != 'mps' or input.dtype != torch.float32:
            raise ValueError('Native H3 encoder must execute FP32 operations on MPS')
        guard()
        tick = time.monotonic()
        source = module.weight
        if isinstance(source, QuantizedTensor):
            if module.quant_format != 'nvfp4':
                raise NotImplementedError('Unsupported MPS encoder projection storage: ' + module.quant_format)
            weight = decoder(source, input.dtype, input.device)
        else:
            weight = source.to(device=input.device, dtype=input.dtype)
        bias = module.bias.to(device=input.device, dtype=input.dtype) if module.bias is not None else None
        stats['weight_decode_seconds'] += time.monotonic() - tick
        stats['largest_weight_bytes'] = max(stats['largest_weight_bytes'], weight.numel() * weight.element_size())
        try:
            output = torch.nn.functional.linear(input, weight, bias)
            backend.synchronize()
            stats['linear_calls'] += 1
            return output
        finally:
            del weight, bias
            backend.empty_cache()
            release_mapped_pages([checkpoint])
    def embedding(module, input, out_dtype=None):
        guard()
        weight = module.weight
        indices = input.cpu()
        if isinstance(weight, QuantizedTensor):
            if module.quant_format != 'int8_tensorwise':
                raise NotImplementedError('Unsupported MPS encoder embedding storage')
            value = get_layout_class(module.layout_type).dequantize_embedding(weight._qdata, weight._params, indices)
        else:
            value = torch.nn.functional.embedding(indices, weight)
        stats['embedding_calls'] += 1
        return value.to(device=input.device, dtype=out_dtype or value.dtype)
    model = clip.cond_stage_model.qwen3vl_32b.transformer.model
    if len(model.layers) != 50:
        raise ValueError('Expected the native H3 encoder truncated to layer 50')
    selected = []
    for module in clip.cond_stage_model.modules():
        if isinstance(module, torch.nn.Embedding):
            function = embedding
        elif hasattr(module, 'forward_comfy_cast_weights') and hasattr(module, 'in_features'):
            function = linear
        else:
            continue
        if getattr(module, 'weight_function', ()) or getattr(module, 'bias_function', ()):
            raise ValueError('MPS encoder reader does not support patched weights')
        selected.append((module, function))
    for module, function in selected:
        restores.append((module, module.__dict__.get('forward_comfy_cast_weights')))
        module.forward_comfy_cast_weights = types.MethodType(function, module)
    for index, layer in enumerate(model.layers):
        def progress(module, inputs, output, index=index):
            print(json.dumps(dict(event='encoder_layer', completed=index + 1, total=50)), flush=True)
        handles.append(layer.register_forward_hook(progress))
    return stats, restores, handles


def encode(prompt, output, *, root, budget_bytes=3 * 2**30, reserve_bytes=2 * 2**30,
           library=None, checkpoint=None, weight_decoder='cpu', media=None, canvas=None, base=None,
           media_budget_bytes=None):
    if sys.platform != 'darwin' or os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK', '0') not in ('', '0'):
        raise RuntimeError('This encoder requires native Mac with CPU fallback disabled')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('A nonempty prompt is required')
    if media_budget_bytes is not None and (type(media_budget_bytes) is not int or media_budget_bytes <= 0):
        raise ValueError('Native media encoding requires a positive byte allowance')
    from .media_request import task_for, TASKS
    if (media or {}).get('conditioning_info'):
        raise ValueError('Native encoding expects raw media, not preencoded conditioning information')
    task = task_for(media or {})
    if task not in TASKS:
        raise ValueError('Unsupported native conditioning task')
    if task != 't2va' and (canvas is None or base is None):
        raise ValueError('Native media encoding requires the target canvas and H3 base')
    if weight_decoder == 'metal':
        from .backends.mps_nvfp4 import decode_weight as decoder
    elif weight_decoder == 'cpu':
        decoder = nvfp4_weight
    else:
        raise ValueError('Unknown native encoder weight decoder')
    root, output = Path(root), Path(output)
    library = Path(library) if library else root / 'vendor/h3-text-encoder'
    sys.path.insert(0, str(library))
    import torch
    torch.set_num_threads(2)
    from .backends import get_backend
    backend = get_backend('mps')
    policy = backend.configure_budget(budget_bytes, reserve_bytes=reserve_bytes)
    import comfy.options
    comfy.options.enable_args_parsing()
    old_args = sys.argv
    sys.argv = [str(library / 'main.py'), '--disable-all-custom-nodes', '--cache-none',
                '--disable-dynamic-vram', '--disable-comfy-compiler', '--disable-cuda-graphs']
    started = time.monotonic()
    clip = None
    handles, restores = [], []
    def release_encoder():
        nonlocal clip
        for handle in handles:
            handle.remove()
        for module, original in restores:
            if original is None:
                module.__dict__.pop('forward_comfy_cast_weights', None)
            else:
                module.forward_comfy_cast_weights = original
        handles.clear()
        restores.clear()
        clip = None
        gc.collect()
        backend.empty_cache()
    try:
        import comfy.sd
        from .encoder_checkpoint import load_clip, release_mapped_pages
        from .conditioning import to_cache
        if checkpoint is not None and not Path(checkpoint).is_file():
            raise FileNotFoundError('Selected encoder checkpoint is missing: ' + str(checkpoint))
        checkpoint = Path(checkpoint) if checkpoint else root / 'models/encoder/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
        if not checkpoint.is_file():
            candidates = list((root / 'models/encoder').rglob(checkpoint.name))
            if len(candidates) != 1:
                raise ValueError('Expected one installed native H3 encoder checkpoint')
            checkpoint = candidates[0]
        with torch.no_grad():
            normalized, vision_kwargs = None, {}
            if task != 't2va':
                from .media_encoding import prepare
                normalized, vision_kwargs = prepare(media, canvas, output.parent / 'media')
            print(json.dumps(dict(event='encoder_phase', stage='encoder_load')), flush=True)
            clip = load_clip([checkpoint], clip_type=comfy.sd.CLIPType.MINIMAX,
                model_options=dict(initial_device=torch.device('cpu'), load_device=torch.device('mps'),
                    offload_device=torch.device('cpu'), dtype=torch.bfloat16), disable_dynamic=True)
            stats, restores, handles = install_weight_reader(clip, backend, checkpoint, decoder=decoder)
            # Placement is handled by the instance reader; native tokenization,
            # forward and output packing still execute through the CLIP API.
            clip.load_model = lambda tokens: clip.patcher
            print(json.dumps(dict(event='encoder_phase', stage='encoder_tokenize')), flush=True)
            tokens = clip.tokenize(prompt, **vision_kwargs)
            release_mapped_pages([checkpoint])
            print(json.dumps(dict(event='encoder_phase', stage='encoder_compute')), flush=True)
            encoded = clip.encode_from_tokens_scheduled(tokens, show_pbar=False)
            backend.synchronize()
            value = to_cache(encoded, prompt, task=task)
            media_metrics = {}
            if normalized is not None:
                # Keep the same native vision/text contract, then release every
                # encoder reference before the independent input VAE is loaded.
                del encoded, tokens, vision_kwargs
                release_encoder()
                # Text streaming deliberately uses a small allocation ceiling.
                # Input VAEs have separate workspaces: re-admit after releasing
                # text weights, against the request's original capacity bound.
                # configure_budget also enforces live availability, the reserve
                # and Metal's recommendation; this never selects unlimited MPS.
                media_policy = backend.configure_budget(
                    budget_bytes if media_budget_bytes is None else media_budget_bytes,
                    reserve_bytes=reserve_bytes)
                from .macos_vdn import activate
                activate()
                from .media_encoding import encode_latents
                tick = time.monotonic()
                value.update(task=task, width=canvas['width'], height=canvas['height'])
                print(json.dumps(dict(event='encoder_phase', stage='media_vae', budget=media_policy)), flush=True)
                info = encode_latents(normalized, value, base, canvas, device='mps')
                backend.synchronize()
                media_metrics.update(conditioning_info=info, media_vae_seconds=time.monotonic() - tick,
                                     media_budget=media_policy)
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix('.partial')
            print(json.dumps(dict(event='encoder_phase', stage='encoder_save')), flush=True)
            torch.save(value, temporary)
            temporary.replace(output)
            return dict(success=True, shape=list(value['prompt_embeds'].shape), output=str(output),
                elapsed_seconds=time.monotonic() - started, budget=policy, reader=stats,
                device_backend='mps', precision='NVFP4/AWQ storage; FP32 decoder and native FP32 H3 forward',
                weight_decoder=weight_decoder, **media_metrics,
                cpu_work=(('tokenization, selected embedding rows and scale layout' if weight_decoder == 'metal'
                           else 'tokenization, selected embedding rows, and explicit weight decoding')
                          + ('; media normalization and native vision position preparation' if normalized is not None else '')),
                cpu_operator_fallback=False)
    finally:
        sys.argv = old_args
        release_encoder()
