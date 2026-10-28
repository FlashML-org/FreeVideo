"""Prepare file-backed BF16 weights without materializing the entire model.

LoRA deltas follow the upstream CPU FP32 merge order. Source files are read-only;
the derived per-block cache records its own identity, preparation time and hashes.
"""
import hashlib
import json
from pathlib import Path
import re
import time

from safetensors.torch import save_file
import torch

from .preparation import cache_location
from .tensor_io import open_tensors


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def skeleton(base, checkpoint):
    from .paths import add_vdn
    add_vdn()
    from diffusers import MiniMaxH3Transformer3DModel
    from diffusers.models.transformers.transformer_minimax_h3 import MiniMaxH3RotaryPosEmbed
    from src.models.hybrid_transform import apply_hybrid_attention_transform

    config = json.loads((base / 'transformer/config.json').read_text(encoding='utf-8'))
    spec = json.loads((checkpoint / 'model_spec.json').read_text(encoding='utf-8'))
    with torch.device('meta'):
        # Cast metadata only. Restore the official FP32 islands before reading
        # any weights; from_pretrained would unnecessarily load a full model.
        from .hardware import activation_dtype
        model = MiniMaxH3Transformer3DModel.from_config(config).to(dtype=activation_dtype())
        for name, module in model.named_modules():
            if any(key in name for key in model._keep_in_fp32_modules):
                module.float()
        apply_hybrid_attention_transform(model, spec['transforms'][0]['config'])
    # This nonpersistent buffer is computed, not stored in the checkpoint.
    model.rope = MiniMaxH3RotaryPosEmbed(config['rope_freq_dim'], config['rope_theta'])
    model.eval().requires_grad_(False)
    return model


class Sources:
    def __init__(self, base, checkpoint):
        self.weights = {}
        self.loras = {}
        self.paths = set()
        for path in sorted((base / 'transformer').glob('*.safetensors')):
            self.paths.add(path)
            with open_tensors(path, framework='np') as stream:
                for key in stream.keys():
                    target = re.sub(r'^(transformer_blocks\.\d+\.attn)\.', r'\1.orig.', key)
                    if target in self.weights:
                        raise ValueError('Duplicate base tensor: ' + target)
                    self.weights[target] = (path, key)
        branch = checkpoint / 'linear_branch/model.safetensors'
        self.paths.add(branch)
        with open_tensors(branch, framework='np') as stream:
            for key in stream.keys():
                if key in self.weights:
                    raise ValueError('Branch replaces a base tensor: ' + key)
                self.weights[key] = (branch, key)
        for path in sorted((checkpoint / 'adapters').glob('*/adapter_model.safetensors')):
            self.paths.add(path)
            with open_tensors(path, framework='np') as stream:
                keys = set(stream.keys())
                for key in sorted(keys):
                    if '.lora_A.' not in key:
                        continue
                    other = key.replace('.lora_A.', '.lora_B.')
                    if other not in keys:
                        raise ValueError('Missing LoRA B: ' + other)
                    target = key.split('.lora_A.')[0] + '.weight'
                    if target not in self.weights:
                        target = target.replace('.attn.', '.attn.orig.', 1)
                    if target not in self.weights:
                        raise ValueError('Unknown LoRA target: ' + target)
                    self.loras.setdefault(target, []).append((path, key, other))

    def tensor(self, name, assembly_dtype, final_dtype):
        path, key = self.weights[name]
        with open_tensors(path) as stream:
            value = stream.get_tensor(key)
        adapters = self.loras.get(name, [])
        if adapters:
            # Never modify a mapped source tensor, even with a private mapping.
            value = value.to(assembly_dtype, copy=True)
            for path, akey, bkey in adapters:
                with open_tensors(path) as stream:
                    a = stream.get_tensor(akey).float()
                    b = stream.get_tensor(bkey).float()
                    delta = (b @ a) * 1.0
                    value.add_(delta.to(value.dtype))
                    del a, b, delta
        return value.to(final_dtype).contiguous()


def group_name(name):
    parts = name.split('.')
    if parts[0] != 'transformer_blocks':
        return 'root'
    return ('adaln/' if parts[2] == 'adaln_proj' else 'blocks/') + f'{int(parts[1]):02d}'


def prepare(base, checkpoint, cache_root):
    started = time.monotonic()
    base, checkpoint, cache_root = map(Path, (base, checkpoint, cache_root))
    model = skeleton(base, checkpoint)
    sources = Sources(base, checkpoint)
    params = dict(model.named_parameters())
    if params.keys() != sources.weights.keys():
        raise ValueError({'missing': sorted(params.keys() - sources.weights.keys()),
                          'unused': sorted(sources.weights.keys() - params.keys())})
    identity = {'implementation_sha256': sha256(__file__),
                'base_config_sha256': sha256(base / 'transformer/config.json'),
                'model_spec_sha256': sha256(checkpoint / 'model_spec.json'),
                'torch': torch.__version__, 'merge_policy': 'upstream_cpu_fp32_then_weight_dtype_add',
                'sources': [{'path': str(p), 'bytes': p.stat().st_size,
                             'mtime_ns': p.stat().st_mtime_ns} for p in sorted(sources.paths)]}
    output, identity, key = cache_location(cache_root, 'vdn-bf16-', identity)
    output.mkdir(parents=True, exist_ok=True)
    marker = output / 'source.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != identity:
        raise ValueError('Prepared weight identity conflict: ' + str(output))
    marker.write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    manifest = output / 'manifest.json'
    if manifest.exists():
        record = json.loads(manifest.read_text(encoding='utf-8'))
        for item in record['groups']:
            path = output / item['file']
            if not path.is_file() or path.stat().st_size != item['bytes']:
                raise ValueError('Prepared weight cache is incomplete: ' + str(path))
        print(json.dumps({'cache': str(output), 'reused': True}), flush=True)
        return output
    groups = {}
    for name, parameter in params.items():
        groups.setdefault(group_name(name), []).append((name, parameter))
    records = []
    torch.set_grad_enabled(False)
    for group, entries in sorted(groups.items()):
        destination = output / (group + '.safetensors')
        destination.parent.mkdir(parents=True, exist_ok=True)
        progress = destination.with_suffix('.json')
        if destination.exists() and progress.exists():
            row = json.loads(progress.read_text(encoding='utf-8'))
            if destination.stat().st_size == row['bytes'] and sha256(destination) == row['sha256']:
                records.append(row)
                continue
        tensors = {}
        for name, parameter in entries:
            # Upstream converts the new hybrid parameters to BF16 after merging.
            final_dtype = torch.bfloat16 if '.attn.' in name and name.startswith('transformer_blocks.') else parameter.dtype
            value = sources.tensor(name, parameter.dtype, final_dtype)
            if value.shape != parameter.shape:
                raise ValueError(f'Weight shape mismatch: {name}: {value.shape} != {parameter.shape}')
            tensors[name] = value
        temporary = destination.with_suffix('.partial')
        save_file(tensors, temporary)
        del tensors, value
        temporary.replace(destination)
        row = {'group': group, 'file': str(destination.relative_to(output)),
               'bytes': destination.stat().st_size, 'sha256': sha256(destination),
               'tensors': len(entries)}
        progress.write_text(json.dumps(row, indent=2) + '\n', encoding='utf-8')
        records.append(row)
        print(json.dumps({'prepared': group, 'bytes': row['bytes'],
                          'elapsed_seconds': time.monotonic() - started}), flush=True)
    record = {'source_id': key, 'groups': records,
              'preparation_seconds': time.monotonic() - started,
              'tensor_count': len(params), 'merged_lora_pairs': sum(map(len, sources.loras.values())),
              'arithmetic': 'BF16 weights with original FP32 modules and original CPU LoRA merge',
              'cuda_used': torch.cuda.is_initialized(), 'original_weights_modified': False}
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    temporary.replace(manifest)
    print(json.dumps({'cache': str(output), **record}, indent=2), flush=True)
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--cache-root', required=True)
    args = parser.parse_args()
    torch.set_num_threads(8)
    prepare(args.base, args.checkpoint, args.cache_root)
