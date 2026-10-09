"""Prepare immutable FP8 adapter variants without per-NFE LoRA matmuls.

Adapters modify the *prepared FP8 model*: dequantize affected matrices, add
FP32 B@A deltas in the input weight dtype, and requantize with the same scale
granularity. This is not a claim of equivalence to merging a BF16 checkpoint.
Unchanged files are hardlinked. Original H3 QKV splitting and SwiGLU half order
follow OpenVDN src/inference/utils/lora.py; foreign architectures fail closed.
"""
import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import time

from safetensors.torch import save_file
import torch

from .tensor_io import open_tensors
from .media_request import digest, verify_files
from .monitoring import save
from .storage import fingerprint
from . import disk_space


def _targets(name):
    prefixes = ('base_model.model.', 'diffusion_model.', 'model.diffusion_model.', 'transformer.')
    while any(name.startswith(prefix) for prefix in prefixes):
        prefix = next(prefix for prefix in prefixes if name.startswith(prefix))
        name = name[len(prefix):]
    # Community H3 trainers also export Kohya-style flattened module names.
    # Only the known H3 block grammar is expanded; underscores inside qkv_proj
    # and out_proj are meaningful and must not become dots.
    name = re.sub(r'^lora_unet_blocks_(\d+)_(attn|mlp)_(out_proj|qkv_proj|fc1|fc2)(?=\.|$)',
                  r'blocks.\1.\2.\3', name)
    name = re.sub(r'^blocks\.', 'transformer_blocks.', name)
    name = name.replace('token_refiner.blocks.', 'token_refiner.refiner_blocks.')
    name = name.replace('final_layer.adaln_proj.linear', 'norm_out.linear')
    # Original H3 fc1 uses [gate; value], including token-refiner MLPs.
    # Diffusers adapters already name ff.net.0.proj and need no swap.
    swap = '.mlp.fc1' in name
    for before, after in (('.mlp.fc1', '.ff.net.0.proj'), ('.mlp.fc2', '.ff.net.2'), ('.attn.out_proj', '.attn.to_out.0')):
        name = name.replace(before, after)
    if '.attn.qkv_proj' in name:
        return [(name.replace('.attn.qkv_proj', '.attn.' + suffix), i, False) for i, suffix in enumerate(('to_q', 'to_k', 'to_v'))]
    return [(name, None, swap)]


def index_adapter(row, targets):
    pairs = {}
    full = []
    with open_tensors(row['path']) as source:
        metadata = source.metadata() or {}
        keys = source.keys()
        for key in keys:
            canonical = key.replace('.default.', '.').replace('.lora_down.weight', '.lora_A.weight').replace('.lora_up.weight', '.lora_B.weight')
            match = re.fullmatch(r'(.+)\.lora_([AB])\.weight', canonical)
            if match:
                pair = pairs.setdefault(match[1], {})
                if match[2] in pair:
                    raise ValueError('Duplicate LoRA factor: ' + key)
                pair[match[2]] = key
            elif canonical.endswith('.alpha'):
                pairs.setdefault(canonical[:-6], {})['alpha'] = key
            elif canonical.endswith(('.diff', '.diff_b')):
                bias = canonical.endswith('.diff_b')
                full.append((canonical[:-7] if bias else canonical[:-5], key, bias))
            else:
                raise ValueError('Unsupported LoRA tensor (no patches are ignored): ' + key)
        if not pairs and not full:
            raise ValueError('Adapter contains no LoRA factors')
        global_alpha = row.get('alpha') is not None or 'alpha' in metadata
        if global_alpha:
            ranks = {source.get_slice(pair['A']).get_shape()[0] for pair in pairs.values() if 'A' in pair
                     and (row.get('alpha') is not None or 'alpha' not in pair)}
            if len(ranks) > 1:
                raise ValueError('A global alpha cannot identify mixed-rank H3 scaling; provide per-target alpha tensors')
        results = []
        for name, pair in pairs.items():
            if not {'A', 'B'} <= pair.keys():
                raise ValueError('Incomplete LoRA pair: ' + name)
            ashape, bshape = (source.get_slice(pair[k]).get_shape() for k in ('A', 'B'))
            if len(ashape) != 2 or len(bshape) != 2 or ashape[0] != bshape[1] or ashape[0] <= 0:
                raise ValueError('Expected matching 2-D LoRA factors: ' + name)
            alpha = row.get('alpha')
            if alpha is None:
                alpha = source.get_tensor(pair['alpha']).item() if 'alpha' in pair else float(metadata.get('alpha', ashape[0]))
            if not math.isfinite(alpha):
                raise ValueError('Nonfinite LoRA alpha')
            for target, split, swap in _targets(name):
                if target not in targets and '.attn.' in target:
                    target = target.replace('.attn.', '.attn.orig.', 1)
                expected = targets.get(target)
                rows = bshape[0] if split is None else bshape[0] // 3
                if (expected is None or (split is not None and bshape[0] % 3)
                        or (swap and rows % 2) or expected['shape'] != [rows, ashape[1]]):
                    raise ValueError('LoRA target/shape incompatible with this VDN model (including pruned AdaLN): ' + target)
                results.append(dict(pair, target=target, split=split, swap=swap,
                                    scale=float(row['strength']) * alpha / ashape[0], file=row['path']))
        for name, key, bias in full:
            shape = source.get_slice(key).get_shape()
            for target, split, swap in _targets(name):
                target += '#bias' if bias else ''
                if target not in targets and '.attn.' in target:
                    target = target.replace('.attn.', '.attn.orig.', 1)
                expected = targets.get(target)
                actual = list(shape)
                if split is not None:
                    if not actual or actual[0] % 3:
                        raise ValueError('Fused QKV difference must have three equal row groups')
                    actual[0] //= 3
                if not expected or actual != expected['shape'] or (swap and (not actual or actual[0] % 2)):
                    raise ValueError('Full-difference LoRA target/shape incompatible with this VDN model: ' + target)
                results.append(dict(full=key, target=target, split=split, swap=swap,
                                    scale=float(row['strength']), file=row['path']))
        if len({item['target'] for item in results}) != len(results):
            raise ValueError('Adapter has overlapping patches for the same target; merge intent is ambiguous')
    return results


def _merge(weight, patches):
    for patch in patches:
        if patch['scale'] == 0:
            continue
        with open_tensors(patch['file']) as source:
            if 'full' in patch:
                value = source.get_tensor(patch['full'])
                if patch['split'] is not None:
                    value = value.chunk(3, dim=0)[patch['split']]
                if patch['swap']:
                    value = torch.cat(value.chunk(2, dim=0)[::-1], dim=0)
                if not bool(torch.isfinite(value).all()):
                    raise ValueError('LoRA contains a nonfinite full difference')
                # Native Comfy diff/diff_b convention: strength * delta in the
                # destination dtype. Alpha scales low-rank factors only.
                weight.add_(value.to(weight.dtype) * patch['scale'])
                del value
                continue
            a, b = (source.get_tensor(patch[k]).float() for k in ('A', 'B'))
            if patch['split'] is not None:
                b = b.chunk(3, dim=0)[patch['split']]
            if patch['swap']:
                b = torch.cat(b.chunk(2, dim=0)[::-1], dim=0)
            if not bool(torch.isfinite(a).all() and torch.isfinite(b).all()):
                raise ValueError('LoRA contains nonfinite factors')
            # A complete delta matrix can be hundreds of MiB. Bound it to rows.
            for begin in range(0, weight.shape[0], 128):
                delta = (b[begin:begin + 128] @ a) * patch['scale']
                weight[begin:begin + 128].add_(delta.to(weight.dtype))
                del delta
        del a, b
    if not bool(torch.isfinite(weight).all()):
        raise ValueError('LoRA merge overflowed the model weight dtype')
    return weight


def _quantize(weight, rowwise):
    # Fp8Linear's weight convention, including the division in weight dtype.
    scale = (weight.abs().amax(dim=1, keepdim=True).float() if rowwise else weight.abs().amax().float())
    scale = (scale / 448.).clamp_min(1e-12)
    result = (weight / scale.to(weight.dtype)).to(torch.float8_e4m3fn)
    if not bool(torch.isfinite(result.float()).all()):
        raise ValueError('LoRA FP8 conversion produced nonfinite weights')
    return result, scale.reshape(1, -1).contiguous()


@torch.no_grad()
def prepare(cache, adapters, output_root=None):
    cache = Path(cache).resolve()
    adapters = [row for row in adapters if row['strength'] != 0]
    if not adapters:
        return cache, {'enabled': False, 'reason': 'No nonzero adapters; original FP8 bytes unchanged'}
    verify_files({'loras': adapters})
    manifest_path = cache / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('precision') == 'int8':
        raise ValueError('This LoRA also changes non-linear or modulation weights, which cannot be merged '
                         'into the int8 model yet. LoRAs that only adapt linear layers work.')
    if manifest.get('precision') != 'fp8':
        raise ValueError('LoRA variants require the prepared FP8 model')
    from .adaln_assets import SLIM_FORMATS, restore_projections
    from .export_slim import embedding_hash, share
    changes_modulation = False
    changes_embedding = False
    for adapter in adapters:
        with open_tensors(adapter['path']) as stream:
            for name in stream.keys():
                for target, _, _ in _targets(name):
                    changes_embedding |= target.startswith(('time_embedder.', 'time_proj.'))
                    changes_modulation |= bool(re.match(r'transformer_blocks\.\d+\.adaln_proj\.', target))
    identity = {'source_manifest_sha256': digest(manifest_path), 'implementation_sha256': digest(__file__),
                'torch': str(torch.__version__), 'adapters': [{k: v for k, v in row.items() if k != 'path'} for row in adapters],
                'arithmetic': 'prepared FP8 dequantization; FP32 delta, weight-dtype merge; original FP8 granularity'}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(output_root or cache.parent) / ('vdn-fp8-lora-' + key[:24])
    marker = output / 'manifest.json'
    if marker.is_file():
        saved = json.loads(marker.read_text(encoding='utf-8'))
        if saved.get('lora_identity') != identity:
            raise ValueError('Adapter cache identity mismatch')
        for group in saved['groups']:
            path = output / group['file']
            stamp = saved['lora_file_stamps'].get(group['file'])
            current = fingerprint(path)
            reliable = current.get('change_time_ns', current['ctime_ns']) not in (None, current['mtime_ns'])
            if (stamp != current or not reliable) and digest(path) != group['sha256']:
                raise ValueError('Adapter cache changed: ' + str(path))
        return output, {'enabled': True, 'cache_hit': True, 'cache': str(output), 'identity': identity}
    if manifest.get('format') in SLIM_FORMATS and (changes_modulation or changes_embedding):
        # Ordinary attention/FF LoRAs keep the fixed tables. Only adapters that
        # actually change modulation need the optional original projection data.
        originals = restore_projections(cache, manifest)
        manifest = dict(manifest, groups=list(manifest['groups']) + originals)
        manifest.pop('format', None)
        manifest.pop('adaln_tables', None)
    elif changes_modulation or changes_embedding:
        manifest = dict(manifest)
        manifest.pop('adaln_tables', None)
    # Header-only indexing; no full model state dict or persistent mmap.
    targets = {}
    for group in manifest['groups']:
        if group['file'] != group['group'] + '.safetensors' or Path(group['file']).is_absolute() or '..' in Path(group['file']).parts:
            raise ValueError('Invalid prepared group path')
        with open_tensors(cache / group['file']) as stream:
            for name in stream.keys():
                if name.endswith('.weight'):
                    shape = stream.get_slice(name).get_shape()
                    targets[name[:-7]] = {'shape': shape, 'group': group['file'], 'key': name}
                elif name.endswith('.bias'):
                    target = name[:-14] if name.endswith('.original.bias') else name[:-5]
                    targets[target + '#bias'] = {'shape': stream.get_slice(name).get_shape(), 'group': group['file'], 'key': name}
    for name, spec in manifest['linears'].items():
        group = next((g for g in manifest['groups'] if name in g.get('linears', {})), None)
        if group is None:
            # Older official export receipts store the linear index globally.
            from .weights import group_name
            file = group_name(name + '.weight') + '.safetensors'
        else:
            file = group['file']
        targets[name] = {'shape': spec['weight_shape'], 'group': file, 'key': name + '.weight_fp8'}
    patches = {}
    for adapter in adapters:
        for patch in index_adapter(adapter, targets):
            patches.setdefault(targets[patch['target']]['group'], []).append(patch)
    output.mkdir(parents=True, exist_ok=True)
    required = sum(row['bytes'] for row in manifest['groups'] if row['file'] in patches)
    if disk_space.free_bytes(output) < required + 1024**3:
        raise ValueError('Insufficient disk for the separate LoRA variant; original model retained')
    records = []
    started = time.perf_counter()
    for index, group in enumerate(manifest['groups']):
        path, destination = cache / group['file'], output / group['file']
        destination.parent.mkdir(parents=True, exist_ok=True)
        progress = destination.with_suffix('.lora.json')
        if destination.is_file() and progress.is_file():
            record = json.loads(progress.read_text(encoding='utf-8'))
            if destination.stat().st_size == record['bytes'] and digest(destination) == record['sha256']:
                records.append(record)
                continue
            raise ValueError('Interrupted adapter group changed; files retained: ' + str(destination))
        if destination.exists():
            destination.rename(destination.with_name(destination.name + '.orphaned-' + str(time.time_ns())))
        if path.stat().st_size != group['bytes']:
            raise ValueError('Prepared source group is incomplete')
        if group['file'] not in patches:
            try:
                os.link(path, destination)
            except OSError as error:
                raise RuntimeError('Adapter variants require hardlinks on the model volume; no unbudgeted full-model copy is made') from error
            record = dict(group)
        else:
            if digest(path) != group['sha256']:
                raise ValueError('Prepared source group failed hash verification')
            values = {}
            by_target = {}
            for patch in patches[group['file']]:
                by_target.setdefault(patch['target'], []).append(patch)
            with open_tensors(path) as stream:
                for name in stream.keys():
                    if name.endswith('.weight_scale') and name[:-13] in by_target:
                        continue
                    target = name[:-11] if name.endswith('.weight_fp8') else name[:-7] if name.endswith('.weight') else None
                    if name.endswith('.bias'):
                        target = (name[:-14] if name.endswith('.original.bias') else name[:-5]) + '#bias'
                    value = stream.get_tensor(name)
                    if target in by_target:
                        if name.endswith('.weight_fp8'):
                            spec = manifest['linears'][target]
                            scale = stream.get_tensor(target + '.weight_scale')
                            weight = (value.float() * scale.reshape(-1, 1)).to(getattr(torch, spec.get('input_dtype', 'bfloat16')))
                            del value, scale
                            weight = _merge(weight, by_target[target])
                            value, scale = _quantize(weight, manifest['scale_granularity'] == 'rowwise')
                            values[target + '.weight_scale'] = scale
                            del weight
                        else:
                            value = _merge(value.clone(), by_target[target])
                    values[name] = value
                    del value
            temporary = destination.with_suffix('.partial')
            if temporary.exists():
                temporary.rename(temporary.with_name(temporary.name + '.retained-' + str(time.time_ns())))
            save_file(values, temporary)
            del values
            gc.collect()
            temporary.replace(destination)
            record = dict(group, bytes=destination.stat().st_size, sha256=digest(destination))
        save(progress, record)
        records.append(record)
        print(json.dumps({'event': 'lora_prepare', 'done': index + 1, 'total': len(manifest['groups']),
                          'group': group['group'], 'modified': group['file'] in patches}), flush=True)
    verify_files({'loras': adapters})
    stamps = {}
    for record in records:
        stamps[record['file']] = fingerprint(output / record['file'])
    if manifest.get('adaln_tables'):
        for table in manifest['adaln_tables']:
            for row in table['files']:
                share(cache / row['file'], output / row['file'], row['sha256'])
    if manifest.get('adaln_sources'):
        manifest = dict(manifest, adaln_sources=dict(manifest['adaln_sources']))
        if changes_modulation:
            manifest['adaln_sources']['groups'] = [r for r in records if r['group'].startswith('adaln/')]
        if changes_embedding:
            manifest['adaln_sources']['embedding_sha256'] = embedding_hash(output / 'root.safetensors')
        # Modified projection files are local variant data, never recover them
        # from the unmodified upstream's optional-download revision.
        if changes_embedding or changes_modulation:
            manifest['adaln_sources'].pop('download', None)
    save(marker, dict(manifest, source_id=key, groups=records, lora_identity=identity, lora_file_stamps=stamps,
                      total_bytes=sum(row['bytes'] for row in records) +
                      sum(row['bytes'] for table in manifest.get('adaln_tables', []) for row in table['files'])))
    return output, {'enabled': True, 'cache_hit': False, 'cache': str(output), 'identity': identity,
                    'modified_groups': sorted(patches), 'prepare_seconds': time.perf_counter() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding='utf-8'))
    torch.set_num_threads(8)
    from .lora_online_cache import prepare as prepare_online
    output, report = (prepare if request.get('mode') == 'fused' else prepare_online)(
        request['cache'], request['adapters'])
    save(request['result'], {'cache': str(output), 'report': report})


if __name__ == '__main__':
    main()
