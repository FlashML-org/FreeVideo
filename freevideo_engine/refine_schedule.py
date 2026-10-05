"""Exact independent LBH three-step clocks and pass-local modulation tables."""
from contextlib import contextmanager
import json
from pathlib import Path
import time

COMMUNITY = 'community-sigma3-v1'
VIDEO_SIGMAS = (0.9035, 0.6316, 0.3158, 0.)


def schedulers(device):
    import torch
    from diffusers import MiniMaxH3Scheduler
    # Keep the leading 1.0 used by the verified community workflow. It is
    # initialization only: the three executed calls start at index one.
    sigma = torch.tensor((1.,) + VIDEO_SIGMAS, device=device, dtype=torch.float32)
    raw = sigma / (12. + (1. - 12.) * sigma)
    audio_sigma = 3. * raw / (1. + 2. * raw)
    video, audio = MiniMaxH3Scheduler(shift=12.), MiniMaxH3Scheduler(shift=3.)
    video.set_timesteps(device=device, sigmas=sigma)
    audio.set_timesteps(device=device, sigmas=audio_sigma)
    return video, audio


def timesteps(device, task):
    from .adaln import modality_timesteps
    video, audio = schedulers(device)
    return modality_timesteps(video.timesteps[1:], audio.timesteps[1:], task)


@contextmanager
def modulation(engine, schedule):
    """Swap only small constants; always restore the resident first-pass state."""
    if schedule is None:
        yield None
        return
    if schedule != COMMUNITY:
        raise ValueError('Unsupported refinement schedule')
    import torch
    from .adaln import CachedModulation, ScheduleCursor, TableCache
    from .adaln_assets import restore_projections
    model = getattr(engine, 'transformer', None) or engine.model
    backend = getattr(engine, 'device_backend', None) or engine.backend
    device = backend.device
    previous_cursor = engine.cursor
    # Uncached modulation evaluates the same exact clocks directly.
    if previous_cursor is None:
        yield dict(schedule=schedule, table_cache_hits=0)
        return
    manifest = json.loads((Path(engine.cache) / 'manifest.json').read_text(encoding='utf-8'))
    times = timesteps(device, engine.task)
    cursor = ScheduleCursor(times)
    cache = TableCache(engine.cache, manifest['source_id'], 3, task=engine.task,
        manifest=manifest, timesteps=times, channels=model.config.hidden_size, device='cpu')
    originals = []
    started, hits = time.perf_counter(), 0
    try:
        with torch.no_grad():
            embeddings = None
            for index, block in enumerate(model.transformer_blocks):
                # Preserve the original tensors without keeping two schedules
                # resident on the GPU. Offloaders are outside this boundary.
                originals.append((block, block.adaln_proj.cpu()))
                values = cache.load(index, 3)
                if values is None:
                    from diffusers.models.transformers.transformer_minimax_h3 import MiniMaxH3AdaLayerNormModulation
                    if embeddings is None:
                        embeddings = [model.time_embedder(model.time_proj(t).to(
                            model.time_embedder.linear_1.weight.dtype)) for t in times]
                    path = Path(engine.cache) / f'adaln/{index:02d}.safetensors'
                    if not path.is_file():
                        restore_projections(engine.cache, manifest)
                    prefix = f'transformer_blocks.{index}.adaln_proj.'
                    from .runtime import _load_safetensors
                    state = {k.removeprefix(prefix): v.to(device) for k, v in _load_safetensors(path).items()}
                    with torch.device('meta'):
                        projection = MiniMaxH3AdaLayerNormModulation(state['linear.weight'].shape[1], model.config.hidden_size)
                    projection.load_state_dict(state, strict=True, assign=True)
                    values = [projection(e) for e in embeddings]
                    cache.save(index, CachedModulation(values, cursor), producer_device=str(device))
                    del projection, state
                else:
                    hits += 1
                block.adaln_proj = CachedModulation(values, cursor).to(device)
                del values
            del embeddings
        engine.schedule_hook.remove()
        engine.cursor = cursor
        engine.schedule_hook = model.register_forward_pre_hook(cursor.before, with_kwargs=True)
        yield dict(schedule=schedule, table_cache_hits=hits, seconds=time.perf_counter()-started,
                   table_identity=cache.identity)
    finally:
        # Clear all new tables before restoration, bounding GPU storage by the
        # larger of the two schedules rather than their combined size.
        for block, _ in originals:
            block.adaln_proj.cpu()
        for block, original in originals:
            block.adaln_proj = original.to(device)
        engine.schedule_hook.remove()
        engine.cursor = previous_cursor
        engine.schedule_hook = model.register_forward_pre_hook(previous_cursor.before, with_kwargs=True)
