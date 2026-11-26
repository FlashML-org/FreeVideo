"""Prepared storage for the official VDN FP8 arithmetic and scale granularity.

Quantization calls the upstream Fp8Linear constructor on one CUDA matrix at a
time. Loading binds those exact bytes to the original Fp8Linear class. The BF16
backup is omitted, as in the separately verified compact-storage wrapper.
"""
import argparse
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import time

from safetensors.torch import save_file
import torch

from .weights import sha256, skeleton, Sources, group_name
from .preparation import cache_location, expected_groups, reusable_group, source_paths
from .tensor_io import open_tensors


def retain_corrupt_group(destination, progress):
    """A prepared group that failed its check is kept (the newest only) while it is rebuilt."""
    from .network import retain_partial
    retain_partial(destination, 'hash-rejected')
    retain_partial(progress, 'hash-rejected')


def forget_corrupt_group(destination, progress):
    """The group is in place and verified: the corrupt ones kept for it go."""
    from .network import forget_failed
    forget_failed(destination)
    forget_failed(progress)


def input_dtype(module):
    """Input dtype of an ordinary Linear or a cached official FP8 Linear."""
    return module.input_dtype if hasattr(module, 'input_dtype') else module.weight.dtype


def quantize_group(values, eligible, official):
    """Same official quantization for both retained-BF16 and streamed setup."""
    converted, linears = {}, {}
    items = values.items() if hasattr(values, 'items') else values
    for name, value in items:
        prefix, _, suffix = name.rpartition('.')
        if prefix not in eligible:
            converted[name] = value
        elif suffix == 'bias':
            converted[prefix + '.original.bias'] = value
        elif suffix == 'weight':
            module = torch.nn.Linear(value.shape[1], value.shape[0], bias=False, device='meta', dtype=value.dtype)
            module.weight = torch.nn.Parameter(value.to('cuda'), requires_grad=False)
            quantized = official.Fp8Linear(module)
            converted[prefix + '.weight_fp8'] = quantized.weight_fp8.cpu()
            converted[prefix + '.weight_scale'] = quantized.weight_scale.cpu()
            linears[prefix] = {'weight_shape': list(value.shape),
                              'scale_shape': list(quantized.weight_scale.shape),
                              'input_dtype': str(value.dtype).removeprefix('torch.')}
            del quantized, module
        else:
            raise ValueError('Unknown eligible Linear state: ' + name)
        # Do not keep the preceding matrix while the iterator merges/loads the
        # next one. Only this group's converted output survives until saving.
        del value
    return converted, linears


def _merged_tensors(sources, entries):
    for name, parameter in entries:
        final_dtype = torch.bfloat16 if '.attn.' in name and name.startswith('transformer_blocks.') else parameter.dtype
        value = sources.tensor(name, parameter.dtype, final_dtype)
        try:
            if value.shape != parameter.shape:
                raise ValueError('Weight shape mismatch: ' + name)
            yield name, value
        finally:
            del value


def _file_tensors(path):
    with open_tensors(path) as stream:
        for name in stream.keys():
            value = stream.get_tensor(name)
            try:
                yield name, value
            finally:
                del value


@torch.no_grad()
def prepare_streamed(base, checkpoint, cache_root):
    """Merge and quantize one group at a time, without a second BF16 disk cache.

    Sources.tensor retains the original CPU LoRA merge order and dtypes. The
    final group is hashed and checkpointed before advancing; retries reuse only
    verified complete groups. Original source files remain read-only here.
    """
    from . import weights
    from .paths import add_vdn
    add_vdn()
    from src.models.ops import fp8_linear as official
    base, checkpoint, cache_root = map(Path, (base, checkpoint, cache_root))
    started = time.monotonic()
    identity = {'preparation': 'streamed_cpu_merge_official_cuda_fp8',
                'implementation_sha256': sha256(__file__), 'merge_implementation_sha256': sha256(weights.__file__),
                'official_fp8_sha256': sha256(official.__file__),
                'base_config_sha256': sha256(base / 'transformer/config.json'),
                'model_spec_sha256': sha256(checkpoint / 'model_spec.json'),
                'torch': torch.__version__, 'scale_granularity': 'per_tensor' if official.per_tensor_gemm() else 'rowwise',
                'min_width': official.MIN_WIDTH, 'skip_end_blocks': 0,
                'sources': [{'path': str(p), 'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
                            for p in source_paths(base, checkpoint)]}
    output, identity, key = cache_location(cache_root, 'vdn-fp8-streamed-', identity)
    output.mkdir(parents=True, exist_ok=True)
    marker = output / 'source.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != identity:
        raise ValueError('Streamed FP8 cache identity conflict')
    if not marker.exists():
        marker.write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    group_names = expected_groups(base)
    # Even the meta model and source-key index are unnecessary on a full hit.
    groups = sources = eligible = None
    records, linears = [], {}
    reused_groups = source_tensors_loaded = 0
    for index, group in enumerate(group_names, 1):
        destination = output / (group + '.safetensors')
        destination.parent.mkdir(parents=True, exist_ok=True)
        progress = destination.with_suffix('.json')
        print(json.dumps({'event': 'prepare_fp8_phase', 'phase': 'verify_cache', 'group': group,
                          'index': index, 'total': len(group_names)}), flush=True)
        saved = reusable_group(output, group)
        if saved is not None:
            forget_corrupt_group(destination, progress)
            reused_groups += 1
            records.append(saved)
            linears.update(saved['linears'])
            print(json.dumps({'event': 'prepared_fp8_group', 'preparation': 'streamed', 'group': group,
                              'index': index, 'total': len(group_names), 'reused': True}), flush=True)
            continue
        if destination.is_file() and progress.is_file():
            retain_corrupt_group(destination, progress)
            print(json.dumps({'event': 'rebuild_fp8_group', 'group': group,
                              'reason': 'Corrupt group retained before rebuilding'}), flush=True)
        print(json.dumps({'event': 'prepare_fp8_phase', 'phase': 'read_source_weights', 'group': group,
                          'index': index, 'total': len(group_names)}), flush=True)
        if groups is None:
            model, sources = skeleton(base, checkpoint), Sources(base, checkpoint)
            params = dict(model.named_parameters())
            if params.keys() != sources.weights.keys():
                raise ValueError({'missing': sorted(params.keys() - sources.weights.keys()),
                                  'unused': sorted(sources.weights.keys() - params.keys())})
            eligible = {name for name, module in model.named_modules()
                        if isinstance(module, torch.nn.Linear)
                        and min(module.in_features, module.out_features) >= official.MIN_WIDTH}
            groups = {}
            for name, parameter in params.items():
                if not parameter.is_meta:
                    raise ValueError('Preparation skeleton materialized a parameter: ' + name)
                groups.setdefault(group_name(name), []).append((name, parameter))
            if sorted(groups) != group_names:
                raise ValueError('Unexpected preparation group layout')
            del model, params, parameter
        with closing(_merged_tensors(sources, groups[group])) as tensors:
            converted, local_linears = quantize_group(tensors, eligible, official)
        source_tensors_loaded += len(groups[group])
        temporary = destination.with_suffix('.partial')
        if temporary.exists():
            # A previous interrupted group's partial is evidence, not a cache hit.
            temporary.rename(temporary.with_name(temporary.name + '.retained-' + str(time.time_ns())))
        save_file(converted, temporary)
        tensor_count = len(converted)
        del tensors, converted
        gc.collect()
        if destination.exists():
            # Interrupted between final rename and receipt commit: keep the
            # orphaned file while rebuilding this one group from pinned sources.
            destination.rename(destination.with_name(destination.name + '.orphaned-' + str(time.time_ns())))
        temporary.replace(destination)
        row = {'group': group, 'file': group + '.safetensors', 'bytes': destination.stat().st_size,
               'sha256': sha256(destination), 'tensors': tensor_count, 'linears': local_linears}
        pending = progress.with_suffix('.json.partial')
        pending.write_text(json.dumps(row, indent=2) + '\n', encoding='utf-8')
        pending.replace(progress)
        records.append(row)
        linears.update(local_linears)
        torch.cuda.empty_cache()
        print(json.dumps({'event': 'prepared_fp8_group', 'preparation': 'streamed', 'group': group,
                          'index': index, 'total': len(group_names), 'reused': False,
                          'bytes': row['bytes'], 'linears': len(local_linears),
                          'seconds': time.monotonic() - started}), flush=True)
    if not linears or (eligible is not None and set(linears) != eligible):
        raise ValueError('Streamed FP8 cache omitted eligible Linears')
    record = {'source_id': key, 'precision': 'fp8', 'scale_granularity': identity['scale_granularity'],
              'groups': records, 'linears': linears, 'identity': identity,
              'preparation_implementation_sha256': sha256(__file__),
              'merge_preparation_implementation_sha256': sha256(weights.__file__),
              'reused_groups': reused_groups, 'source_tensors_loaded': source_tensors_loaded,
              'preparation_seconds': time.monotonic() - started,
              'total_bytes': sum(row['bytes'] for row in records), 'bf16_intermediate_bytes': 0}
    pending = output / 'manifest.partial'
    pending.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    pending.replace(output / 'manifest.json')
    print(json.dumps({'event': 'fp8_cache_ready', 'cache': str(output), 'bytes': record['total_bytes'],
                      'bf16_intermediate_bytes': 0, 'reused_groups': reused_groups,
                      'source_tensors_loaded': source_tensors_loaded}), flush=True)
    return output


def install_cached_linears(model, linears, *, weight_only=False):
    from src.models.ops.fp8_linear import Fp8Linear, FP8_DTYPE
    from .weight_only import WeightOnlyLinear
    for name, spec in linears.items():
        original = model.get_submodule(name)
        if not isinstance(original, torch.nn.Linear):
            raise ValueError('FP8 cache target is not a Linear: ' + name)
        if list(original.weight.shape) != spec['weight_shape']:
            raise ValueError('FP8 cache shape differs from the official model: ' + name)
        kind = WeightOnlyLinear if weight_only else Fp8Linear
        replacement = kind.__new__(kind)
        torch.nn.Module.__init__(replacement)
        replacement.register_buffer('weight_fp8', torch.empty(spec['weight_shape'], dtype=FP8_DTYPE, device='meta'))
        replacement.register_buffer('weight_scale', torch.empty(spec['scale_shape'], dtype=torch.float32, device='meta'))
        replacement.original = torch.nn.Module()
        replacement.original.register_parameter('bias', original.bias)
        replacement.in_features = original.in_features
        replacement.out_features = original.out_features
        # Newly injected hybrid modules start as FP32 in the meta skeleton;
        # official assembly casts them to BF16 before FP8 conversion.
        input_name = spec.get('input_dtype', str(original.weight.dtype).removeprefix('torch.'))
        if input_name not in ('bfloat16', 'float16', 'float32'):
            raise ValueError('Unexpected FP8 input dtype in cache: ' + input_name)
        replacement.input_dtype = getattr(torch, input_name)
        prefix, _, child = name.rpartition('.')
        setattr(model.get_submodule(prefix) if prefix else model, child, replacement)


@torch.no_grad()
def export_compact_model(model, cache_root, *, base, checkpoint):
    """Save the already assembled official FP8 model, one group at a time.

    This avoids loading and quantizing the same 70 GB again after a reference
    render. No arithmetic or weight conversion is performed during export.
    """
    from src.models.ops import fp8_linear as official
    if model.training or any(p.requires_grad for p in model.parameters()):
        raise ValueError('Export requires a frozen inference model')
    state = model.state_dict()
    if any(name.endswith('.original.weight') for name in state):
        raise ValueError('Export requires compact FP8 without the BF16 backups')
    linears = {name: {'weight_shape': list(module.weight_fp8.shape),
                      'scale_shape': list(module.weight_scale.shape), 'input_dtype': 'bfloat16'}
               for name, module in model.named_modules() if isinstance(module, official.Fp8Linear)}
    if not linears:
        raise ValueError('No official FP8 matrices to export')
    sources = Sources(Path(base), Path(checkpoint))
    identity = {'origin': 'official assembled FP8; exact registered tensors',
                'implementation_sha256': sha256(__file__),
                'official_fp8_sha256': sha256(official.__file__),
                'base_config_sha256': sha256(Path(base) / 'transformer/config.json'),
                'model_spec_sha256': sha256(Path(checkpoint) / 'model_spec.json'),
                'torch': torch.__version__, 'scale_granularity': 'per_tensor' if official.per_tensor_gemm() else 'rowwise',
                'linears': linears,
                'sources': [{'path': str(p), 'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
                            for p in sorted(sources.paths)]}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(cache_root) / ('vdn-fp8-official-' + key[:16])
    output.mkdir(parents=True, exist_ok=True)
    marker = output / 'source.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != identity:
        raise ValueError('Export cache identity mismatch')
    marker.write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    groups = {}
    for name, tensor in state.items():
        groups.setdefault(group_name(name), {})[name] = tensor
    records = []
    started = time.perf_counter()
    for group, tensors in sorted(groups.items()):
        destination = output / (group + '.safetensors')
        destination.parent.mkdir(parents=True, exist_ok=True)
        progress = destination.with_suffix('.json')
        record = json.loads(progress.read_text(encoding='utf-8')) if progress.exists() else None
        if record and destination.is_file() and destination.stat().st_size == record['bytes'] and sha256(destination) == record['sha256']:
            records.append(record)
            continue
        cpu = {name: tensor.detach().cpu().contiguous() for name, tensor in tensors.items()}
        temporary = destination.with_suffix('.partial')
        save_file(cpu, temporary)
        temporary.replace(destination)
        del cpu
        record = {'group': group, 'file': group + '.safetensors', 'bytes': destination.stat().st_size,
                  'sha256': sha256(destination), 'tensors': len(tensors)}
        progress.write_text(json.dumps(record) + '\n', encoding='utf-8')
        records.append(record)
        print(json.dumps({'event': 'exported_official_fp8_group', 'group': group,
                          'seconds': time.perf_counter() - started}), flush=True)
    manifest = {'source_id': key, 'precision': 'fp8', 'scale_granularity': identity['scale_granularity'],
                'groups': records, 'linears': linears, 'total_bytes': sum(r['bytes'] for r in records),
                'preparation_seconds': time.perf_counter() - started, 'identity': identity}
    temporary = output / 'manifest.partial'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    temporary.replace(output / 'manifest.json')
    return output


@torch.no_grad()
def prepare(source, cache_root, *, base, checkpoint):
    from .paths import add_vdn
    add_vdn()
    from src.models.ops import fp8_linear as official
    source, cache_root = Path(source), Path(cache_root)
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('precision', 'bf16') != 'bf16':
        raise ValueError('FP8 preparation requires the verified BF16 merged cache')
    per_tensor = official.per_tensor_gemm()
    identity = {'source_id': manifest['source_id'],
                'source_manifest_sha256': sha256(source / 'manifest.json'),
                'implementation_sha256': sha256(__file__),
                'official_fp8_sha256': sha256(official.__file__),
                'torch': torch.__version__, 'scale_granularity': 'per_tensor' if per_tensor else 'rowwise',
                'min_width': official.MIN_WIDTH, 'skip_end_blocks': 0,
                'memory_policy': 'discard BF16 backup after official CUDA quantization; immutable per-group files'}
    output, identity, key = cache_location(cache_root, 'vdn-fp8-', identity)
    output.mkdir(parents=True, exist_ok=True)
    marker = output / 'source.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != identity:
        raise ValueError('FP8 cache identity conflict')
    marker.write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    eligible = None
    linears, groups = {}, []
    started = time.perf_counter()
    if sorted(g['group'] for g in manifest['groups']) != expected_groups(Path(base)):
        raise ValueError('Unexpected BF16 preparation group layout')
    for index, group in enumerate(manifest['groups'], 1):
        if group['file'] != group['group'] + '.safetensors':
            raise ValueError('Unexpected source group path')
        destination = output / group['file']
        destination.parent.mkdir(parents=True, exist_ok=True)
        progress = destination.with_suffix('.json')
        saved = reusable_group(output, group['group'])
        if saved is not None:
            forget_corrupt_group(destination, progress)
            groups.append(saved)
            linears.update(saved['linears'])
            print(json.dumps({'event': 'prepared_fp8_group', 'group': group['group'], 'index': index,
                              'total': len(manifest['groups']), 'reused': True}), flush=True)
            continue
        if destination.is_file() and progress.is_file():
            retain_corrupt_group(destination, progress)
        path = source / group['file']
        if path.stat().st_size != group['bytes'] or sha256(path) != group['sha256']:
            raise ValueError('Source cache failed integrity check: ' + str(path))
        if eligible is None:
            model = skeleton(Path(base), Path(checkpoint))
            if any(not parameter.is_meta for parameter in model.parameters()):
                raise ValueError('Preparation skeleton materialized parameters')
            eligible = {name for name, module in model.named_modules()
                        if isinstance(module, torch.nn.Linear)
                        and min(module.in_features, module.out_features) >= official.MIN_WIDTH}
            del model
        with closing(_file_tensors(path)) as values:
            converted, local_linears = quantize_group(values, eligible, official)
        temporary = destination.with_suffix('.partial')
        if temporary.exists():
            temporary.rename(temporary.with_name(temporary.name + '.retained-' + str(time.time_ns())))
        if not local_linears:
            # AdaLN's input is below the official FP8 threshold. Preserve its
            # immutable BF16 files without making another 26 GB disk copy.
            os.link(path, temporary)
        else:
            save_file(converted, temporary)
        if destination.exists():
            destination.rename(destination.with_name(destination.name + '.orphaned-' + str(time.time_ns())))
        temporary.replace(destination)
        saved = {'group': group['group'], 'file': group['file'],
                 'bytes': destination.stat().st_size, 'sha256': sha256(destination),
                 'tensors': len(converted), 'linears': local_linears}
        progress.write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
        groups.append(saved)
        linears.update(local_linears)
        del values, converted
        gc.collect()
        torch.cuda.empty_cache()
        print(json.dumps({'event': 'prepared_fp8_group', 'group': group['group'],
                          'bytes': saved['bytes'], 'linears': len(local_linears),
                          'seconds': time.perf_counter() - started}), flush=True)
    if not linears or eligible is not None and set(linears) != eligible:
        raise ValueError('FP8 cache omitted eligible Linears')
    record = {'source_id': key, 'bf16_source_id': manifest['source_id'], 'precision': 'fp8',
              'scale_granularity': identity['scale_granularity'], 'groups': groups, 'linears': linears,
              'preparation_implementation_sha256': sha256(__file__),
              'preparation_seconds': time.perf_counter() - started,
              'total_bytes': sum(group['bytes'] for group in groups), 'identity': identity}
    (output / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'event': 'fp8_cache_ready', 'cache': str(output), 'linears': len(linears),
                      'bytes': record['total_bytes']}), flush=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--cache-root', required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--checkpoint', required=True)
    args = parser.parse_args()
    torch.set_num_threads(8)
    prepare(args.source, args.cache_root, base=args.base, checkpoint=args.checkpoint)


if __name__ == '__main__':
    main()
