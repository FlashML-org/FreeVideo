"""Build the prepared Prism weights that FreeVideo streams block by block.

Inputs are the original downloads: the Prism checkpoint
(``preview_alpha/diffusion_pytorch_model.safetensors``), the MOVA-360p
component directory (configs, video/audio VAE, UMT5 text encoder, tokenizer,
scheduler) and the full-rank LightX2V Wan2.2-I2V distill deltas
(``high_260412.safetensors`` / ``low_260412.safetensors``).

Output (one directory per variant)::

    manifest.json                  layout, quantization, sha256 + bytes per file
    configs/                       MOVA model configs (+ scheduler, model_index)
    root.safetensors               embeddings, heads, time projections (bf16)
    expert_high/blocks/NN.safetensors   high-noise video block NN (00-39)
    expert_low/blocks/NN.safetensors    low-noise video block NN (00-39)
    audio/blocks/NN.safetensors    audio DiT block NN (00-29)
    bridge/NN.safetensors          a2v + v2a conditioners of fused layer NN
    text_encoder/                  UMT5-XXL: bf16 shards (default) or W8A16 INT8 (qlinear)
    tokenizer/, vae/, audio_vae/   tokenizer, Wan2.1 VAE, DAC decoder (bf16)
    lora_high/, lora_low/          (``--lora-*`` builds) unmerged distill LoRA per
                                   block + root; the experts then hold the base weights

Block tensors are named ``NN.<role>.<module path>``. The role is ``video``,
``audio``, ``a2v`` or ``v2a``, so the expert, audio and bridge files of one
fused layer together form one streamed unit (prefix ``NN.``). Quantized
Linears store the ``QLinear`` buffers (``qweight``, ``w_scale``, ``bias``,
``act_mult``) and their layout in the safetensors metadata key
``prism_qlinear`` (the format of qlinear.save_quantized).

The distill delta is added to the bf16 weights in FP32 and each Linear is
quantized from that FP32 sum (rounding the merge to bf16 first loses about a
third of the delta). Variants: ``int8`` = W8A8 INT8 per-channel weights with a
128-wide randomized Hadamard rotation (RTX 30/40/50); ``fp8`` = W8A8 E4M3
rowwise (RTX 40/50). Same math as the Prism research runner
(fastrun/worker.py: lora merge hook -> QLinear.from_linear).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

FORMAT = 'freevideo-prism-prepared'
VERSION = 1
VARIANTS = {'int8': dict(mode='w8a8_int8', rot='had128'), 'fp8': dict(mode='w8a8_fp8', rot=None),
            'bf16': dict(mode=None, rot=None)}  # bf16: plain Linear weights (the closest to the official model)
VIDEO_LINEARS = ('self_attn.q', 'self_attn.k', 'self_attn.v', 'self_attn.o',
                 'cross_attn.q', 'cross_attn.k', 'cross_attn.v', 'cross_attn.o', 'ffn.0', 'ffn.2')
BRIDGE_LINEARS = ('inner.q', 'inner.k', 'inner.v', 'inner.o')
META_KEY = 'prism_qlinear'


def sha256(path, chunk=16 * 2**20):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            data = stream.read(chunk)
            if not data:
                return digest.hexdigest()
            digest.update(data)


def source_name(expert, index, fused):
    if expert == 'high':
        return ('fusion_blocks.%d.video_block.' % index) if index < fused else (
            'remaining_video_blocks.%d.' % (index - fused))
    return 'video_dit_2.blocks.%d.' % index


class Sources:
    def __init__(self, checkpoint, deltas):
        from safetensors import safe_open
        self.checkpoint = safe_open(str(checkpoint), framework='pt', device='cpu')
        self.keys = set(self.checkpoint.keys())
        self.deltas = {}
        for expert, path in deltas.items():
            if path is None:
                self.deltas[expert] = None
                continue
            handle = safe_open(str(path), framework='pt', device='cpu')
            self.deltas[expert] = (handle, set(handle.keys()))

    def get(self, key):
        return self.checkpoint.get_tensor(key)

    def with_prefix(self, prefix):
        return sorted(k for k in self.keys if k.startswith(prefix))

    def delta(self, expert, block, name, suffix):
        entry = self.deltas.get(expert)
        if entry is None:
            return None
        key = 'diffusion_model.blocks.%d.%s.%s' % (block, name, suffix)
        return entry[0].get_tensor(key) if key in entry[1] else None


def quantize(weight, bias, variant, device, delta=None, strength=1.0):
    """QLinear buffers from bf16 ``weight`` (+ fp32 ``delta`` merge), on ``device``;
    for the bf16 variant the (merged, bf16-rounded) Linear weight and bias."""
    import torch
    from .prism_model import qlinear
    if VARIANTS[variant]['mode'] is None:
        w = weight if delta is None else (weight.to(device, torch.float32)
                                          + delta.to(device, torch.float32) * strength).to(weight.dtype)
        tensors = {'weight': w.detach().contiguous().cpu()}
        if bias is not None:
            tensors['bias'] = bias.detach().contiguous().cpu()
        return tensors, None
    if delta is not None:
        src = torch.nn.Linear(weight.shape[1], weight.shape[0], bias=bias is not None, device=device,
                              dtype=torch.float32)
        with torch.no_grad():
            src.weight.copy_(weight.to(device, torch.float32) + delta.to(device, torch.float32) * strength)
            if bias is not None:
                src.bias.copy_(bias.to(device).float())
    else:
        src = torch.nn.Linear(weight.shape[1], weight.shape[0], bias=bias is not None, device=device,
                              dtype=weight.dtype)
        with torch.no_grad():
            src.weight.copy_(weight.to(device))
            if bias is not None:
                src.bias.copy_(bias.to(device))
    q = qlinear.QLinear.from_linear(src, VARIANTS[variant]['mode'], rot=VARIANTS[variant]['rot'])
    meta = dict(mode=q.mode, in_features=q.in_features, out_features=q.out_features, rot_block=q.rot_block,
                has_mult=q.act_mult is not None, act_asym=q.act_asym, bias=q.bias is not None,
                bias_dtype=str(q.bias.dtype).replace('torch.', '') if q.bias is not None else None)
    tensors = {b: getattr(q, b).detach().contiguous().cpu() for b in ('qweight', 'w_scale', 'bias', 'act_mult', 'w_sum')
               if getattr(q, b) is not None}
    del src, q
    return tensors, meta


def build_unit_file(sources, prefix, role, linears, out_paths, variants, device, unit, expert=None, block=None,
                    strength=1.0):
    """One role of one fused layer: quantize its Linears, copy the rest (bf16)."""
    from safetensors.torch import save_file
    keys = sources.with_prefix(prefix)
    if not keys:
        raise KeyError('Missing checkpoint tensors under ' + prefix)
    tensors = {v: {} for v in variants}
    metas = {v: {} for v in variants}
    linear_keys = set()
    merged = 0
    for name in linears:
        weight_key, bias_key = prefix + name + '.weight', prefix + name + '.bias'
        weight = sources.get(weight_key)
        bias = sources.get(bias_key) if bias_key in sources.keys else None
        linear_keys.update((weight_key, bias_key))
        delta = sources.delta(expert, block, name, 'diff') if expert else None
        diff_b = sources.delta(expert, block, name, 'diff_b') if expert else None
        if diff_b is not None and bias is not None:
            bias = (bias.float() + diff_b.float() * strength).to(bias.dtype)
        merged += delta is not None
        for variant in variants:
            q, meta = quantize(weight, bias, variant, device, delta, strength)
            module = '%s.%s.%s' % (unit, role, name)
            if meta is not None:
                metas[variant][module] = meta
            for buffer, value in q.items():
                tensors[variant][module + '.' + buffer] = value
        del weight, bias, delta
    for key in keys:
        if key in linear_keys:
            continue
        value = sources.get(key)
        rest = key[len(prefix):]
        if expert and rest == 'modulation':
            diff_m = sources.delta(expert, block, '', 'diff_m')
            if diff_m is not None:
                import torch
                value = value.clone().add_(diff_m.to(torch.float32).mul(strength).to(value.dtype))
        for variant in variants:
            tensors[variant]['%s.%s.%s' % (unit, role, rest)] = value.contiguous()
    for variant in variants:
        out_paths[variant].parent.mkdir(parents=True, exist_ok=True)
        save_file(tensors[variant], str(out_paths[variant]), metadata={META_KEY: json.dumps(metas[variant])})
    return merged


LORA_PREFIX = 'diffusion_model.'


def lora_groups(path, strength=1.0):
    """Kijai/LightX2V LoRA file -> {owner path: {'down','up','diff_b','diff','diff_m'}} with
    alpha / rank and ``strength`` folded into ``up`` (merge_wan_lora semantics)."""
    import torch
    from safetensors import safe_open
    groups = {}
    with safe_open(str(path), framework='pt', device='cpu') as handle:
        raw = {}
        for key in handle.keys():
            if not key.startswith(LORA_PREFIX):
                continue
            body = key[len(LORA_PREFIX):]
            for suffix, name in (('.lora_down.weight', 'down'), ('.lora_up.weight', 'up'), ('.alpha', 'alpha'),
                                 ('.diff_b', 'diff_b'), ('.diff_m', 'diff_m'), ('.diff', 'diff')):
                if body.endswith(suffix):
                    raw.setdefault(body[:-len(suffix)], {})[name] = handle.get_tensor(key)
                    break
    for owner, entry in raw.items():
        row = {}
        if 'down' in entry:
            rank = entry['down'].shape[0]
            scale = strength * (float(entry['alpha']) / rank if 'alpha' in entry else 1.0)
            row['down'] = entry['down'].to(torch.bfloat16).contiguous()
            row['up'] = (entry['up'].float() * scale).to(torch.bfloat16).contiguous()
        for name in ('diff_b', 'diff', 'diff_m'):
            if name in entry:
                row[name] = (entry[name].float() * strength).to(torch.bfloat16).contiguous()
        groups[owner] = row
    return groups


def lora_block_tensors(groups, index, unit):
    """Unmerged distill LoRA of video block ``index`` as extra unit tensors:
    ``NN.video.<linear>.lora_down/lora_up/lora_diff_b`` and ``NN.video.<norm>.lora_diff(_b)``."""
    prefix = 'blocks.%d.' % index
    out = {}
    for owner, row in groups.items():
        if not owner.startswith(prefix):
            continue
        path = owner[len(prefix):]
        base = '%s.video.%s.' % (unit, path) if path else '%s.video.' % unit
        if 'down' in row:
            out[base + 'lora_down'] = row['down']
            out[base + 'lora_up'] = row['up']
        if 'diff_b' in row:
            out[base + 'lora_diff_b'] = row['diff_b']
        if 'diff' in row:
            out[base + 'lora_diff'] = row['diff']
        if 'diff_m' in row:
            out[base + 'lora_diff_m'] = row['diff_m']
    return out


def lora_root_tensors(groups):
    """Root (embeddings, time projection, head, patch embedding) LoRA entries."""
    out = {}
    for owner, row in groups.items():
        if owner.startswith('blocks.'):
            continue
        for name, value in row.items():
            out['%s.%s' % (owner, {'down': 'lora_down', 'up': 'lora_up', 'diff_b': 'lora_diff_b',
                                   'diff': 'lora_diff', 'diff_m': 'lora_diff_m'}[name])] = value
    return out


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def prepare_text_encoder(mova, outputs, mode, device):
    """UMT5-XXL: bf16 (the original shards, as the research runs load it) or
    W8A16 INT8 (qlinear.quantize_t5 from the HF bf16 load; half the size, but
    it moves the sampled trajectory measurably)."""
    if mode == 'bf16':
        for out in outputs:
            directory = out / 'text_encoder'
            if directory.exists():
                shutil.rmtree(directory)
            for item in sorted((mova / 'text_encoder').iterdir()):
                if item.is_file():
                    copy_file(item, directory / item.name)
        return
    import torch
    from safetensors.torch import save_file
    from transformers import UMT5EncoderModel
    from .prism_model import qlinear
    model = UMT5EncoderModel.from_pretrained(str(mova / 'text_encoder'), torch_dtype=torch.bfloat16)
    meta = {}
    if mode == 'int8':
        model.to(device)
        done = qlinear.quantize_t5(model, 'w8a16_int8', embeddings=True)
        for name, module in model.named_modules():
            if isinstance(module, qlinear.QLinear):
                meta[name] = dict(kind='qlinear', mode=module.mode, in_features=module.in_features,
                                  out_features=module.out_features, rot_block=module.rot_block,
                                  has_mult=module.act_mult is not None, act_asym=module.act_asym,
                                  bias=module.bias is not None,
                                  bias_dtype=str(module.bias.dtype).replace('torch.', '') if module.bias is not None else None)
            elif isinstance(module, qlinear.QEmbedding):
                meta[name] = dict(kind='qembedding', num_embeddings=module.num_embeddings,
                                  embedding_dim=module.embedding_dim, padding_idx=module.padding_idx,
                                  out_dtype=str(module.out_dtype).replace('torch.', ''))
        print(json.dumps(dict(event='prism_prepare', stage='text_encoder', quantized=len(done))), flush=True)
    state = {}
    seen = {}
    for key, value in model.state_dict().items():
        pointer = (value.data_ptr(), value.dtype, tuple(value.shape))
        if pointer in seen:  # tied (encoder.embed_tokens is shared)
            continue
        seen[pointer] = key
        state[key] = value.detach().contiguous().cpu()
    for out in outputs:
        directory = out / 'text_encoder'
        directory.mkdir(parents=True, exist_ok=True)
        copy_file(mova / 'text_encoder' / 'config.json', directory / 'config.json')
        save_file(state, str(directory / 'model.safetensors'),
                  metadata={META_KEY: json.dumps(meta), 'precision': 'w8a16_int8' if mode == 'int8' else 'bf16'})
    del model, state
    if device != 'cpu':
        torch.cuda.empty_cache()


def prepare_audio_vae(mova, outputs):
    import torch
    from safetensors.torch import load_file, save_file
    state = {k: v.to(torch.bfloat16).contiguous() for k, v in
             load_file(str(mova / 'audio_vae' / 'diffusion_pytorch_model.safetensors')).items()
             if k.startswith(('decoder.', 'post_quant_conv.'))}
    for out in outputs:
        copy_file(mova / 'audio_vae' / 'config.json', out / 'audio_vae' / 'config.json')
        save_file(state, str(out / 'audio_vae' / 'diffusion_pytorch_model.safetensors'))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m freevideo_engine.prism_prepare', description=__doc__.split('\n\n')[0])
    parser.add_argument('--checkpoint', type=Path, required=True,
                        help='Prism preview_alpha/diffusion_pytorch_model.safetensors')
    parser.add_argument('--mova', type=Path, required=True, help='MOVA-360p component directory')
    parser.add_argument('--delta-high', type=Path, help='High-noise distill delta (high_260412.safetensors)')
    parser.add_argument('--delta-low', type=Path, help='Low-noise distill delta (low_260412.safetensors)')
    parser.add_argument('--delta-strength', type=float, default=1.0)
    parser.add_argument('--lora-high', type=Path,
                        help='Unmerged distill LoRA for the high-noise expert (Kijai rank-256 260412); '
                             'experts are then stored as the base Prism weights')
    parser.add_argument('--lora-low', type=Path, help='Unmerged distill LoRA for the low-noise expert')
    parser.add_argument('--link-shared', type=Path,
                        help='Hard-link (or copy) text_encoder/, tokenizer/, vae/ and audio_vae/ from this prepared '
                             'variant instead of rebuilding them')
    parser.add_argument('--weights-only', action='store_true',
                        help='Write only the variant\'s own weights and configs (an add-on such as bf16 for the max '
                             'level); text_encoder/, tokenizer/, vae/ and audio_vae/ are read from the installed '
                             'sibling variant at run time')
    parser.add_argument('--out', type=Path, required=True,
                        help='Output root; each variant is written to OUT/<variant>')
    parser.add_argument('--variants', default='int8,fp8')
    parser.add_argument('--text-encoder', choices=('int8', 'bf16'), default='bf16')
    parser.add_argument('--refresh-text-encoder', action='store_true',
                        help='Only rewrite text_encoder/ (and the manifest) of existing variants')
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--blocks', help='Debug: comma list of block indices to build (default all)')
    args = parser.parse_args(argv)
    variants = [v.strip() for v in args.variants.split(',') if v.strip()]
    for variant in variants:
        if variant not in VARIANTS:
            parser.error('Unknown variant ' + variant)
    if bool(args.delta_high) != bool(args.delta_low):
        parser.error('Pass both distill deltas or neither')
    if bool(args.lora_high) != bool(args.lora_low):
        parser.error('Pass both distill LoRAs or neither')
    if args.lora_high and args.delta_high:
        parser.error('Choose merged deltas or unmerged LoRAs, not both')
    import torch
    torch.set_grad_enabled(False)
    started = time.perf_counter()
    mova = args.mova.resolve()
    outputs = {v: (args.out / v).resolve() for v in variants}
    if args.refresh_text_encoder:
        for out in outputs.values():
            if not (out / 'manifest.json').is_file():
                parser.error('Not prepared yet: %s' % out)
        prepare_text_encoder(mova, list(outputs.values()), args.text_encoder, args.device)
        for variant, out in outputs.items():
            manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
            write_manifest(out, dict(manifest, text_encoder='w8a16_int8' if args.text_encoder == 'int8' else 'bf16'))
        return 0
    for out in outputs.values():
        if (out / 'manifest.json').exists():
            parser.error('Already prepared: %s (remove it to rebuild)' % out)
        out.mkdir(parents=True, exist_ok=True)
    configs = {name: json.loads((mova / name / 'config.json').read_text(encoding='utf-8'))
               for name in ('video_dit', 'video_dit_2', 'audio_dit', 'dual_tower_bridge')}
    layers = configs['video_dit']['num_layers']
    fused = min(layers, configs['audio_dit']['num_layers'])
    if configs['video_dit_2']['num_layers'] != layers:
        raise ValueError('Both video experts must have the same number of blocks')
    sources = Sources(args.checkpoint, {'high': args.delta_high, 'low': args.delta_low})
    selected = set(range(layers)) if not args.blocks else {int(x) for x in args.blocks.split(',')}
    merged = {'high': 0, 'low': 0}
    loras = {e: lora_groups(p, args.delta_strength) for e, p in (('high', args.lora_high), ('low', args.lora_low))
             if p} if args.lora_high else {}
    from safetensors.torch import save_file

    def paths(relative):
        return {v: outputs[v] / relative for v in variants}

    for index in sorted(selected):
        tick = time.perf_counter()
        unit = '%02d' % index
        for expert, folder in (('high', 'expert_high'), ('low', 'expert_low')):
            merged[expert] += build_unit_file(
                sources, source_name(expert, index, fused), 'video', VIDEO_LINEARS,
                paths('%s/blocks/%s.safetensors' % (folder, unit)), variants, args.device, unit,
                expert=expert if sources.deltas[expert] else None, block=index, strength=args.delta_strength)
        for expert in loras:
            extra = lora_block_tensors(loras[expert], index, unit)
            for path in paths('lora_%s/blocks/%s.safetensors' % (expert, unit)).values():
                path.parent.mkdir(parents=True, exist_ok=True)
                save_file(extra, str(path))
        if index < fused:
            build_unit_file(sources, 'fusion_blocks.%d.audio_block.' % index, 'audio', VIDEO_LINEARS,
                            paths('audio/blocks/%s.safetensors' % unit), variants, args.device, unit)
            build_bridge_file(sources, index, paths('bridge/%s.safetensors' % unit), variants, args.device, unit)
        torch.cuda.empty_cache()
        print(json.dumps(dict(event='prism_prepare', stage='block', block=index, seconds=round(time.perf_counter() - tick, 1))),
              flush=True)
    if args.blocks:
        print(json.dumps(dict(event='prism_prepare', stage='partial', note='debug build; no manifest')), flush=True)
        return 0
    # Root: every non-block tensor (embeddings, heads, time projections), unchanged bf16.
    from safetensors.torch import save_file
    block_prefixes = ('fusion_blocks.', 'remaining_video_blocks.', 'video_dit_2.blocks.')
    root = {k: sources.get(k).contiguous() for k in sorted(sources.keys) if not k.startswith(block_prefixes)}
    for out in outputs.values():
        save_file(root, str(out / 'root.safetensors'))
        for expert in loras:
            save_file(lora_root_tensors(loras[expert]), str(out / ('lora_%s' % expert) / 'root.safetensors'))
        for name in ('video_dit', 'video_dit_2', 'audio_dit', 'dual_tower_bridge'):
            copy_file(mova / name / 'config.json', out / 'configs' / name / 'config.json')
        copy_file(mova / 'scheduler' / 'scheduler_config.json', out / 'configs' / 'scheduler' / 'scheduler_config.json')
        copy_file(mova / 'model_index.json', out / 'configs' / 'model_index.json')
        if args.link_shared or args.weights_only:
            continue
        for item in sorted((mova / 'tokenizer').iterdir()):
            if item.is_file():
                copy_file(item, out / 'tokenizer' / item.name)
        for item in ('config.json', 'diffusion_pytorch_model.safetensors'):
            copy_file(mova / 'video_vae' / item, out / 'vae' / item)
    del root
    if args.weights_only:
        pass
    elif args.link_shared:
        shared = args.link_shared.resolve()
        for out in outputs.values():
            for folder in ('text_encoder', 'tokenizer', 'vae', 'audio_vae'):
                for item in sorted((shared / folder).iterdir()):
                    target = out / folder / item.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(item, target)
                    except OSError:
                        shutil.copyfile(item, target)
    else:
        prepare_audio_vae(mova, list(outputs.values()))
        prepare_text_encoder(mova, list(outputs.values()), args.text_encoder, args.device)
    for variant, out in outputs.items():
        manifest = dict(
            format=FORMAT, version=VERSION, variant=variant, quantization=dict(VARIANTS[variant], weight_clip='mse'),
            text_encoder='w8a16_int8' if args.text_encoder == 'int8' else 'bf16',
            layout=dict(video_blocks=layers, fused_blocks=fused, unit_prefix='NN.',
                        roles=dict(video='expert_high|expert_low/blocks/NN', audio='audio/blocks/NN',
                                   a2v='bridge/NN', v2a='bridge/NN')),
            distill=(dict(kind='lora', strength=args.delta_strength, high=args.lora_high.name, low=args.lora_low.name,
                          rank=int(next(iter(r['down'] for r in loras['high'].values() if 'down' in r)).shape[0]),
                          layout='lora_high|lora_low/blocks/NN (NN.video.<linear>.lora_down/lora_up/lora_diff_b, '
                                 '<norm>.lora_diff), lora_*/root.safetensors',
                          note='Experts store the base Prism weights; the student adds the LoRA at run time.')
                     if loras else
                     dict(kind='none', note='Base Prism weights only (no distillation)')
                     if not args.delta_high else
                     dict(kind='merged', merged_linears=merged, strength=args.delta_strength,
                          high=args.delta_high.name if args.delta_high else None,
                          low=args.delta_low.name if args.delta_low else None,
                          merge='fp32 sum, quantized from fp32')),
            source=dict(checkpoint=Path(args.checkpoint).name, mova=Path(mova).name),  # names only: the manifest ships
            requirements=dict(min_compute_capability=[8, 9] if variant == 'fp8' else [8, 0]),
            linear_weights='bf16' if VARIANTS[variant]['mode'] is None else VARIANTS[variant]['mode'],
            created=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        if args.weights_only:
            manifest['shared'] = dict(folders=['text_encoder', 'tokenizer', 'vae', 'audio_vae'],
                                      source='the installed int8 or fp8 variant beside this folder')
        manifest = write_manifest(out, manifest)
        print(json.dumps(dict(event='prism_prepare', stage='complete', variant=variant, out=str(out),
                              bytes=manifest['bytes'], seconds=round(time.perf_counter() - started, 1))), flush=True)
    return 0


def write_manifest(out, manifest):
    """Record bytes and sha256 of every file beside the manifest."""
    files = {}
    for path in sorted(p for p in out.rglob('*') if p.is_file() and p.name != 'manifest.json'):
        files[path.relative_to(out).as_posix()] = dict(bytes=path.stat().st_size, sha256=sha256(path))
    manifest = dict(manifest, files=files, bytes=sum(row['bytes'] for row in files.values()))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    return manifest


def build_bridge_file(sources, index, out_paths, variants, device, unit):
    """a2v + v2a conditioners of one fused layer in one file."""
    from safetensors.torch import save_file
    tensors = {v: {} for v in variants}
    metas = {v: {} for v in variants}
    for role, attr in (('a2v', 'a2v_conditioner'), ('v2a', 'v2a_conditioner')):
        prefix = 'fusion_blocks.%d.%s.' % (index, attr)
        keys = sources.with_prefix(prefix)
        if not keys:
            continue
        linear_keys = set()
        for name in BRIDGE_LINEARS:
            weight_key, bias_key = prefix + name + '.weight', prefix + name + '.bias'
            weight = sources.get(weight_key)
            bias = sources.get(bias_key) if bias_key in sources.keys else None
            linear_keys.update((weight_key, bias_key))
            for variant in variants:
                q, meta = quantize(weight, bias, variant, device)
                module = '%s.%s.%s' % (unit, role, name)
                if meta is not None:
                    metas[variant][module] = meta
                for buffer, value in q.items():
                    tensors[variant][module + '.' + buffer] = value
        for key in keys:
            if key not in linear_keys:
                for variant in variants:
                    tensors[variant]['%s.%s.%s' % (unit, role, key[len(prefix):])] = sources.get(key).contiguous()
    for variant in variants:
        out_paths[variant].parent.mkdir(parents=True, exist_ok=True)
        save_file(tensors[variant], str(out_paths[variant]), metadata={META_KEY: json.dumps(metas[variant])})


if __name__ == '__main__':
    sys.exit(main())
