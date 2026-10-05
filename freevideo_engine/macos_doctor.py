"""Native Mac readiness checks, independent of CUDA packages and receipts."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import traceback


def doctor(*, probe=False):
    from .macos_bootstrap import require_native, inventory
    from .paths import data_root, model_root, vdn_root
    require_native()
    root = data_root()
    cached = root / 'prepared-cache.json'
    cache = json.loads(cached.read_text()).get('cache') if cached.is_file() else None
    locations = dict(vdn=vdn_root(), base=model_root() / 'h3-base',
        checkpoint=model_root() / 'stage-dmd-step-250', comfy=root / 'vendor/h3-text-encoder', cache=cache)
    result = dict(schema_version=1, device_backend='mps', hardware=inventory()['hardware'],
        paths={k: dict(path=str(p) if p else None, exists=bool(p and Path(p).is_dir()))
               for k, p in locations.items()}, ready=False, kernel_probes=[],
        usable_attention_backends=[], failed_optional_backends=[],
        validation='Small native operations only; not full-video capacity, performance or quality validation')
    result['versions'] = {name: importlib.metadata.version(name) for name in
        ('torch', 'torchvision', 'torchaudio', 'safetensors', 'diffusers')}
    if not probe:
        return result
    try:
        if os.environ.get('PYTORCH_MPS_FAST_MATH', '0') != '0':
            raise ValueError('Native readiness checks require default precision (MPS fast math disabled)')
        from .macos_vdn import activate
        activate()
        sys.path.insert(0, str(locations['comfy']))
        from comfy.text_encoders import minimax  # Same native encoder contract used by generation.
        import torch
        from .backends import get_backend
        backend = get_backend('mps')
        if not backend.is_available() or torch.version.cuda is not None:
            raise RuntimeError('The installed runtime is not a native Apple MPS build')
        result['budget'] = backend.configure_budget(2 * 2**30, reserve_bytes=2**30)
        generator = torch.Generator().manual_seed(20261004)
        kernels = backend.attention_kernels('mps', 'mps')
        with torch.inference_mode():
            for dtype in (torch.float32, torch.bfloat16):
                q, k, v = [torch.randn(1, 31, 4, 128, generator=generator).to(dtype) for _ in range(3)]
                actual = kernels.batched('mps', q.to('mps'), k.to('mps'), v.to('mps'), 128**-.5)
                reference = torch.nn.functional.scaled_dot_product_attention(
                    *[t.double().transpose(1, 2) for t in (q, k, v)], scale=128**-.5).transpose(1, 2)
                backend.synchronize()
                relative = ((actual.cpu().double() - reference).square().mean().sqrt() /
                            reference.square().mean().sqrt().clamp_min(1e-12)).item()
                limit = .01 if dtype == torch.bfloat16 else .0001
                if not bool(actual.isfinite().all()) or relative > limit:
                    raise ValueError('Native attention comparison failed: ' + str(relative))
                result['kernel_probes'].append(dict(backend='mps', dtype=str(dtype), status='complete',
                    relative_rmse=relative, relative_rmse_limit=limit))
            from .backends.mps_fp8 import decode_weight
            from .backends.mps_weights import decode_weight as reference_decode
            weight = torch.randn(64, 128, generator=generator).to(torch.float8_e4m3fn)
            scale = torch.full((1, 64), .125)
            decoded = decode_weight(weight, scale)
            if not torch.equal(decoded.cpu(), reference_decode(weight, scale)):
                raise ValueError('Native weight storage decoding differs from its BF16 reference')
            x = torch.randn(7, 128, generator=generator).bfloat16().to('mps')
            projected = torch.nn.functional.linear(x, decoded)
            if not bool(projected.isfinite().all()):
                raise ValueError('Native BF16 projection produced nonfinite output')
            result['kernel_probes'].append(dict(backend='linear', status='complete', storage_decode_exact=True))
            from types import SimpleNamespace
            from comfy_kitchen.float_utils import to_blocked
            from .macos_encoder import nvfp4_weight
            from .backends.mps_nvfp4 import decode_weight as decode_nvfp4
            packed = torch.arange(128 * 32).remainder(256).to(torch.uint8).reshape(128, 32)
            scales = torch.arange(128 * 4).remainder(127).to(torch.uint8).view(
                torch.float8_e4m3fn).reshape(128, 4)
            weight = SimpleNamespace(_qdata=packed, _params=SimpleNamespace(
                block_scale=to_blocked(scales), scale=torch.tensor(.0379), orig_shape=(127, 61),
                transposed=False))
            decoded = decode_nvfp4(weight, torch.float32, 'mps').cpu().contiguous()
            reference = nvfp4_weight(weight, torch.float32, 'cpu').contiguous()
            if not torch.equal(decoded.view(torch.uint8), reference.view(torch.uint8)):
                raise ValueError('Native NVFP4 decoding differs from its FP32 reference')
            result['kernel_probes'].append(dict(backend='encoder-nvfp4', status='complete',
                storage_decode_exact=True))
        result.update(ready=all(row['exists'] for row in result['paths'].values()),
            usable_attention_backends=['mps'], identity=backend.arithmetic_identity())
    except Exception:
        result['kernel_probes'].append(dict(backend='mps', status='error', error=traceback.format_exc()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--require-paths', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = doctor(probe=args.probe)
    if args.out:
        from .monitoring import save
        save(args.out, result)
    print(json.dumps(result, indent=2))
    return int((args.probe and not result['ready']) or
               (args.require_paths and not all(row['exists'] for row in result['paths'].values())))


if __name__ == '__main__':
    raise SystemExit(main())
