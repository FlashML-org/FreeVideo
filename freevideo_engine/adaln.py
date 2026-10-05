"""Schedule-specific AdaLN tables, evaluated with the original per-step GEMMs.

The optimization is inspired by NVIDIA's Sol-H3 AdaLN precompute: timesteps are
known before sampling, so retain the small modulation outputs instead of 26 GB
of immutable projection weights. FL2VA has a separate keyframe timestep schedule.
"""
import hashlib
import json
from pathlib import Path

from safetensors.torch import load_file, save_file
import torch


class ScheduleCursor:
    def __init__(self, timesteps):
        self.timesteps = timesteps
        self.index = -1

    def before(self, model, args, kwargs):
        index = self.index + 1
        if index >= len(self.timesteps):
            raise ValueError('Reset the AdaLN schedule before each request')
        if not torch.equal(kwargs['timestep'], self.timesteps[index]):
            raise ValueError('The request changed the precomputed AdaLN schedule')
        self.index = index

    def reset(self, start_index=0):
        if type(start_index) is not int or not 0 <= start_index < len(self.timesteps):
            raise ValueError('AdaLN start index must select an existing schedule row')
        self.index = start_index - 1


class CachedModulation(torch.nn.Module):
    def __init__(self, values, cursor):
        super().__init__()
        self.cursor = cursor
        self.steps = len(values)
        for index, values_at_step in enumerate(values):
            self.register_buffer(f'step_{index}', torch.cat(values_at_step, dim=-1))

    def forward(self, temb):
        if self.cursor.index < 0:
            raise RuntimeError('AdaLN schedule has not started')
        return getattr(self, f'step_{self.cursor.index}').chunk(6, dim=-1)


class TableCache:
    """Persist fixed modulation constants by weights, schedule and layout."""
    def __init__(self, root, source_id, steps, task='t2va', *, manifest=None,
                 timesteps=None, channels=5376, device='cuda'):
        from . import adaln_assets as assets
        from .monitoring import save
        times = schedule_timesteps(steps, device='cpu', task=task) if timesteps is None else timesteps
        rows = [value.detach().float().cpu().tolist() for value in times]
        self.identity = assets.identity(assets.weight_identity(manifest) if manifest is not None else source_id,
                                        rows, channels)
        self.device = device
        self.asset = next((table for table in (manifest or {}).get('adaln_tables', [])
                           if table['identity'] == self.identity), None)
        self.optional_asset = None
        self.optional_loaded = set()
        self.optional_downloaded = set()
        self.optional_download_bytes = 0
        self.root = Path(root) / assets.directory(self.identity)
        self.producer = None
        self.shared = None
        if self.asset is None:
            # Published tables installed by setup or the request preflight.
            from .paths import model_root
            from .sampling_assets import cache_root
            self.shared = cache_root(model_root()) / assets.directory(self.identity)
        if self.asset is not None:
            if self.asset['directory'] != self.root.name:
                raise ValueError('AdaLN asset identity/path mismatch')
            self.producer = self.asset.get('producer')
            return
        # Upgrade known legacy tables without rereading 26 GB of projections.
        # This is a one-time semantic-format migration; future v2 reuse does
        # not depend on these implementation hashes or the producer machine.
        if manifest is not None and not (self.root / 'identity.json').exists():
            from .export_slim import LEGACY_CONTRACTS, LEGACY_MODEL_CONTRACTS
            count = len(assets.projection_groups(manifest))
            for candidate in sorted(Path(root).glob('adaln-tables-*')):
                marker = candidate / 'identity.json'
                try:
                    old = json.loads(marker.read_text(encoding='utf-8'))
                    if (old.get('implementation') not in LEGACY_CONTRACTS or
                            old.get('original_implementation') not in LEGACY_MODEL_CONTRACTS or
                            old.get('source_id') != source_id or old.get('steps') != steps or
                            old.get('video_shift') != 12. or old.get('audio_shift') != 3.):
                        continue
                    if old.get('task') != 't2va':
                        from src.inference.render import KEYFRAME_NOISE_AUG
                        if old.get('keyframe_noise_aug') != KEYFRAME_NOISE_AUG:
                            continue
                    legacy_times = [t.tolist() for t in schedule_timesteps(steps, device='cpu', task=old['task'])]
                    if legacy_times != rows:
                        continue
                    files = []
                    for index in range(count):
                        path = candidate / f'{index:02d}.safetensors'
                        record = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
                        files.append(dict(index=index, file=path.name, bytes=path.stat().st_size, sha256=record['sha256']))
                    if not files:
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                self.root = candidate
                self.producer = dict(old, migrated_from='verified-legacy-contract-v1')
                self.asset = dict(identity=self.identity, directory=candidate.name, files=files, producer=self.producer)
                return
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / 'identity.json'
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8')) != self.identity:
                raise ValueError('AdaLN table cache identity mismatch')
        else:
            save(marker, self.identity)
        if manifest is not None:
            self.optional_asset = assets.optional_table(self.identity, manifest)

    def load(self, index, steps):
        from . import adaln_assets as assets
        if steps != len(self.identity['timesteps']):
            raise ValueError('AdaLN cache schedule length mismatch')
        path = self.root / f'{index:02d}.safetensors'
        marker = path.with_suffix('.json')
        if self.asset is None and not (path.is_file() and marker.is_file()) and self.shared is not None:
            path = self.shared / path.name
            marker = path.with_suffix('.json')
        if self.asset is not None:
            row = next((r for r in self.asset['files'] if r['index'] == index), None)
            if row is None:
                raise ValueError('Incomplete AdaLN model asset')
        elif not path.is_file() or not marker.is_file():
            if self.optional_asset is None:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            row = assets.download_table(path.parent, self.optional_asset, index)
            self.optional_downloaded.add(index)
            self.optional_download_bytes += row['bytes']
        else:
            row = json.loads(marker.read_text(encoding='utf-8'))
        assets.check_table(path, row, self.identity)
        if self.optional_asset is not None:
            published = next(r for r in self.optional_asset['files'] if r['index'] == index)
            if row['sha256'] == published['sha256']:
                self.optional_loaded.add(index)
                self.producer = self.optional_asset.get('producer')
        # These are small immutable constants; their producer's GPU/runtime is
        # deliberately irrelevant. Validate before transferring to this device.
        state = load_file(path, device=self.device)
        if any(not bool(torch.isfinite(tensor).all()) for tensor in state.values()):
            raise ValueError('Nonfinite AdaLN model asset')
        return [state[f'step_{i}'].chunk(6, dim=-1) for i in range(steps)]

    def save(self, index, module, *, producer_device=None):
        from . import adaln_assets as assets
        from .monitoring import save
        if self.asset is not None:
            raise ValueError('Published AdaLN model assets are immutable')
        path = self.root / f'{index:02d}.safetensors'
        temporary = path.with_suffix('.partial')
        state = {name: tensor.cpu() for name, tensor in module.state_dict().items()}
        if any(not bool(torch.isfinite(tensor).all()) for tensor in state.values()):
            raise ValueError('Cannot save nonfinite AdaLN constants')
        save_file(state, temporary)
        row = {'bytes': temporary.stat().st_size, 'sha256': assets.file_hash(temporary)}
        assets.check_table(temporary, row, self.identity, verify_hash=False)
        temporary.replace(path)
        save(path.with_suffix('.json'), row)
        provenance = self.root / 'producer.json'
        if not provenance.exists():
            from diffusers.models.transformers import transformer_minimax_h3
            producer = {'torch': str(torch.__version__), 'cuda': torch.version.cuda,
                'gpu': torch.cuda.get_device_name() if str(self.device).startswith('cuda') else 'cpu',
                'implementation': assets.file_hash(__file__),
                'original_implementation': assets.file_hash(transformer_minimax_h3.__file__),
                'matmul_precision': torch.get_float32_matmul_precision(),
                'tf32': torch.backends.cuda.matmul.allow_tf32,
                'bf16_reduced_reduction': torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction}
            if producer_device is not None:
                # Storage/loading can be CPU even when constants were computed
                # on Metal. Existing CUDA callers keep their original receipt.
                producer.update(device_backend=producer_device, gpu=producer_device)
            save(provenance, producer)


@torch.no_grad()
def schedule_timesteps(steps, video_shift=12., audio_shift=3., device='cuda', task='t2va'):
    from .media_request import TASKS
    if task not in TASKS:
        raise ValueError('Unsupported AdaLN task')
    from diffusers import MiniMaxH3Scheduler
    video, audio = MiniMaxH3Scheduler(shift=video_shift), MiniMaxH3Scheduler(shift=audio_shift)
    video.set_timesteps(steps, device=device)
    audio.set_timesteps(steps, device=device)
    return modality_timesteps(video.timesteps, audio.timesteps, task)


def modality_timesteps(video, audio, task):
    timesteps = []
    for video_t, audio_t in zip(video, audio):
        values = [video_t.float(), audio_t.float()]
        if task in ('i2va', 'l2va', 'fl2va', 'ref2va', 'ref2va_av'):
            from src.inference.render import KEYFRAME_NOISE_AUG
            values.append(video_t.new_tensor(max(float(video_t), KEYFRAME_NOISE_AUG), dtype=torch.float32))
        if task in ('ref2va_audio', 'ref2va_av'):
            values.append(video_t.new_tensor(0., dtype=torch.float32))
        timesteps.append(torch.unique(torch.stack(values), sorted=True))
    return timesteps


@torch.no_grad()
def schedule_embeddings(model, steps, video_shift=12., audio_shift=3., device='cuda', task='t2va'):
    timesteps = schedule_timesteps(steps, video_shift, audio_shift, device, task)
    embeddings = []
    for times in timesteps:
        projected = model.time_proj(times).to(model.time_embedder.linear_1.weight.dtype)
        embeddings.append(model.time_embedder(projected))
    return ScheduleCursor(timesteps), embeddings
