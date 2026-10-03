"""Prepare small, streamable adapter factors beside unchanged FP8 weights."""
import hashlib
import json
import os
from pathlib import Path
import re
import time

import torch
from safetensors.torch import save_file

from .lora_cache import _targets, index_adapter, prepare as prepare_fused
from .media_request import digest, verify_files
from .monitoring import save
from .storage import fingerprint
from .tensor_io import open_tensors


def _supported(adapters, linears):
    for adapter in adapters:
        with open_tensors(adapter['path']) as source:
            for key in source.keys():
                name = re.sub(r'\.(?:lora_[AB]\.weight|lora_(?:down|up)\.weight|alpha)$',
                              '', key.replace('.default.', '.'))
                for target, _, _ in _targets(name):
                    if target not in linears and '.attn.' in target:
                        target = target.replace('.attn.', '.attn.orig.', 1)
                    if target not in linears or not target.startswith(('transformer_blocks.', 'token_refiner.refiner_blocks.')):
                        return False
    return True


@torch.no_grad()
def prepare(cache, adapters, output_root=None):
    cache = Path(cache).resolve()
    adapters = [row for row in adapters if row['strength'] != 0]
    if not adapters:
        return cache, dict(enabled=False)
    verify_files({'loras': adapters})
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('online_lora'):
        raise ValueError('Select the original model when changing the LoRA combination')
    if manifest.get('precision') != 'fp8' or not _supported(adapters, manifest.get('linears', {})):
        # Full-difference, bias and modulation patches retain the existing
        # exact preparation path. Never silently omit an unsupported patch.
        path, report = prepare_fused(cache, adapters, output_root)
        return path, dict(report, mode='fused', reason='Adapter includes non-linear or modulation patches')
    identity = dict(source_manifest_sha256=digest(cache / 'manifest.json'),
        implementation_sha256=digest(__file__), index_sha256=digest(Path(__file__).with_name('lora_cache.py')),
        adapters=[{k: v for k, v in row.items() if k != 'path'} for row in adapters],
        arithmetic='FP8 base plus independent BF16 low-rank branches')
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(output_root or cache.parent) / ('vdn-online-lora-' + key[:24])
    marker = output / 'manifest.json'
    if marker.is_file():
        saved = json.loads(marker.read_text(encoding='utf-8'))
        if saved.get('lora_identity') != identity:
            raise ValueError('Adapter cache identity mismatch')
        refreshed = False
        for group in saved['groups']:
            path = output / group['file']
            stamp = fingerprint(path)
            if stamp != saved['lora_file_stamps'].get(group['file']):
                if digest(path) != group['sha256']:
                    raise ValueError('Adapter cache changed: ' + str(path))
                saved['lora_file_stamps'][group['file']] = stamp
                refreshed = True
        if refreshed:
            # Another combination can change a shared inode's link count/ctime.
            # Recheck it once, rather than rehashing the model on every use.
            save(marker, saved)
        return output, dict(enabled=True, mode='online', cache_hit=True, **saved['online_lora']['summary'])
    targets = {name: dict(shape=spec['weight_shape']) for name, spec in manifest['linears'].items()}
    groups = {}
    for adapter in adapters:
        for patch in index_adapter(adapter, targets):
            block = (patch['target'].split('.')[1] if patch['target'].startswith('transformer_blocks.') else 'root')
            groups.setdefault(block, []).append(patch)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    records, specs, block_bytes = [], {}, {}
    for block, patches in sorted(groups.items()):
        values, modules = {}, {}
        for patch in patches:
            name = patch['target']
            branches = modules.setdefault(name, [])
            prefix = name + '._freevideo_lora.' + str(len(branches))
            dtype = getattr(torch, manifest['linears'][name].get('input_dtype', 'bfloat16'))
            with open_tensors(patch['file']) as source:
                a = source.get_tensor(patch['A']).to(dtype=dtype).clone()
                b = source.get_tensor(patch['B'])
                if patch['split'] is not None:
                    b = b.chunk(3, dim=0)[patch['split']]
                if patch['swap']:
                    b = torch.cat(b.chunk(2, dim=0)[::-1], dim=0)
                b = b.to(dtype=dtype).contiguous().clone()
            if not bool(torch.isfinite(a).all() and torch.isfinite(b).all()):
                raise ValueError('LoRA contains nonfinite or overflowing factors')
            values[prefix + '.a'], values[prefix + '.b'] = a, b
            branches.append(dict(scale=patch['scale'], rank=a.shape[0]))
        label = f'{int(block):02d}' if block != 'root' else 'root'
        relative = f'lora/{label}.safetensors'
        destination = output / relative
        destination.parent.mkdir(exist_ok=True)
        temporary = destination.with_suffix('.partial')
        save_file(values, temporary)
        temporary.replace(destination)
        block_bytes[str(block)] = sum(t.numel() * t.element_size() for t in values.values())
        records.append(dict(group=f'lora/{label}', file=relative,
                            bytes=destination.stat().st_size, sha256=digest(destination)))
        specs[str(block)] = dict(file=relative, modules=modules)
        del values, a, b
        print(json.dumps(dict(event='lora_prepare', done=len(records), total=len(groups))), flush=True)
    from .export_slim import share
    for group in manifest['groups']:
        source, destination = cache / group['file'], output / group['file']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_size != group['bytes']:
            raise ValueError('Prepared source group is incomplete')
        if destination.exists():
            if not os.path.samefile(source, destination):
                raise ValueError('Adapter base weights changed: ' + str(destination))
        else:
            try:
                os.link(source, destination)
            except OSError as error:
                raise RuntimeError('Store LoRA preparation beside the model on a volume supporting hardlinks') from error
    for table in manifest.get('adaln_tables', []):
        for row in table['files']:
            share(cache / row['file'], output / row['file'], row['sha256'])
    verify_files({'loras': adapters})
    root_bytes = block_bytes.get('root', 0)
    summary = dict(targets=sum(len(spec['modules']) for spec in specs.values()),
                   adapter_count=len(adapters), factor_bytes=sum(block_bytes.values()),
                   root_bytes=root_bytes,
                   max_block_bytes=max((v for k, v in block_bytes.items() if k != 'root'), default=0))
    online = dict(version=1, blocks=specs, block_bytes=block_bytes, summary=summary)
    records = list(manifest['groups']) + records
    save(marker, dict(manifest, source_id=key, groups=records, online_lora=online,
        lora_identity=identity, lora_file_stamps={r['file']: fingerprint(output / r['file']) for r in records},
        total_bytes=manifest['total_bytes'] + sum(r['bytes'] for r in records if r['group'].startswith('lora/'))))
    return output, dict(enabled=True, mode='online', cache_hit=False,
                        prepare_seconds=time.perf_counter() - started, **summary)
