#!/usr/bin/env python3
"""Time a saved native-worker request, reusing its cached text conditioning."""
import argparse
import hashlib
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import torch
    from freevideo_engine.locking import runtime_lock, device_lock_path
    from freevideo_engine.runtime import Engine
    from freevideo_engine.worker import generate
    if not torch.version.hip:
        parser.error('This benchmark requires a ROCm PyTorch environment')
    props = torch.cuda.get_device_properties(0)
    if props.gcnArchName.split(':')[0] != 'gfx1201':
        parser.error('This benchmark is validated on gfx1201')
    request = json.loads(args.request.read_text())
    original_conditioning = hashlib.sha256(Path(request['conditioning']).read_bytes()).hexdigest()
    request.update(output=str(args.out/'video.mp4'), metrics=str(args.out/'engine.json'),
                   artifacts=str(args.out/'video.artifacts'), resource_attempt=args.out.name,
                   input_cache_dir=None)
    (args.out/'video.artifacts').mkdir()
    (args.out/'request.json').write_text(json.dumps(request, indent=2)+'\n')
    phases = []
    original_sample = Engine.sample
    def sample(engine, *a, **kw):
        start = time.perf_counter()
        result = original_sample(engine, *a, **kw)
        torch.cuda.synchronize()
        phases.append(dict(name='refine_3' if kw.get('initial_latents') is not None else 'base_8',
                           wall_seconds=time.perf_counter()-start))
        return result
    with ExitStack() as stack:
        stack.enter_context(runtime_lock(shared=True))
        device_id = os.environ.get('ROCR_VISIBLE_DEVICES', str(props.uuid))
        stack.enter_context(runtime_lock(device_lock_path(device_id), inherit=False))
        Engine.sample = sample
        stack.callback(setattr, Engine, 'sample', original_sample)
        torch.set_num_threads(8)
        torch.set_grad_enabled(False)
        start = time.perf_counter()
        generate(request)
        torch.cuda.synchronize()
        wall = time.perf_counter()-start
    assert hashlib.sha256(Path(request['conditioning']).read_bytes()).hexdigest() == original_conditioning
    record = dict(state='complete', video_worker_wall_seconds=wall, phases=phases,
                  scope='Model load through video/audio save, including final GPU sync; cached conditioning excludes text encoding',
                  request_sha256=hashlib.sha256(args.request.read_bytes()).hexdigest(),
                  conditioning_sha256=original_conditioning, hardware=str(props),
                  torch=torch.__version__, hip=torch.version.hip,
                  engine_metrics=json.loads((args.out/'engine.json').read_text()))
    (args.out/'native-validation.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(dict(video_worker_wall_seconds=wall, phases=phases), indent=2))


if __name__ == '__main__':
    main()
