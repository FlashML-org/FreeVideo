"""Schedule-specific AdaLN tables, evaluated with the original per-step GEMMs.

The optimization is inspired by NVIDIA's Sol-H3 AdaLN precompute: timesteps are
known before sampling, so retain the small modulation outputs instead of 26 GB
of immutable projection weights. FL2VA has a separate keyframe timestep schedule.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from safetensors.torch import load_file, save_file
import torch


@contextlib.contextmanager
def serial_ops():
    """Run the small CPU tensor work of a table on this thread alone.

    A table is a few hundred kilobytes. Spread over the full intra-op pool each
    operation waits for its workers, and on a machine whose cores are busy that
    wait is hundreds of times the work (about 0.3 s per block for a cat that
    takes 0.2 ms alone). The thread count is a setting of the whole process, so
    it is restored on exit and other threads running torch ops meanwhile see 1.
    """
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        yield
    finally:
        torch.set_num_threads(previous)


def all_finite(tensor):
    """Whether every element is finite; reads half-precision bits instead of calling isfinite.

    A bfloat16 or float16 table is finite exactly when its exponent field is not
    all ones, so one integer mask answers the question about ten times faster on
    the CPU than isfinite() on those dtypes.
    """
    mask = {torch.bfloat16: 0x7F80, torch.float16: 0x7C00}.get(tensor.dtype)
    if mask is None:
        return bool(torch.isfinite(tensor).all())
    bits = tensor.reshape(-1).view(torch.int16)
    return not bool((bits & mask).eq(mask).any())


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
        with serial_ops():
            for index, values_at_step in enumerate(values):
                self.register_buffer(f'step_{index}', torch.cat(values_at_step, dim=-1))
        # Steps differ in how many distinct timesteps they carry: the first H3
        # step has video and audio at the same t, so its rows are fewer. The
        # fused pre/post kernels compile for static shapes and recompiled in the
        # second step, mid-request and beside a full step's activations. Rows
        # past a step's own timesteps are never indexed, so the narrower steps
        # repeat their last row up to the widest step; stored tables are unchanged.
        self.rows = max(len(getattr(self, f'step_{index}')) for index in range(self.steps))

    def forward(self, temb):
        if self.cursor.index < 0:
            raise RuntimeError('AdaLN schedule has not started')
        values = getattr(self, f'step_{self.cursor.index}')
        if len(values) < self.rows:
            values = torch.cat((values, values[-1:].expand(self.rows - len(values), -1)))
        return values.chunk(6, dim=-1)


class TableCache:
    """Persist fixed modulation constants by weights, schedule and layout."""
    def __init__(self, root, source_id, steps, task='t2va', *, manifest=None,
                 timesteps=None, channels=5376, device='cuda', fetch=True):
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
        self.fetch = fetch
        self.placeholders = set()
        self.root = Path(root) / assets.directory(self.identity)
        self.producer = None
        self.shared = None
        if self.asset is None:
            # Published tables installed by setup or the request preflight.
            from .paths import installed_model_root
            from .sampling_assets import cache_root
            self.shared = cache_root(installed_model_root()) / assets.directory(self.identity)
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
                    # A legacy table follows the grid this machine's own linspace
                    # gave the version that wrote it. Where that is not the
                    # engine's grid the table belongs to other timesteps: keep
                    # it on disk and do not relabel it.
                    legacy_times = [t.tolist() for t in schedule_timesteps(steps, device='cpu', task=old['task'],
                                                                          upstream_grid=True)]
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
        if not fetch:
            # Compiling ahead of a request reads the tables on disk and writes
            # nothing; a table that is not there is not fetched (see load).
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.drop_stale_staging()
        marker = self.root / 'identity.json'
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8')) != self.identity:
                raise ValueError('AdaLN table cache identity mismatch')
        else:
            save(marker, self.identity)
        if manifest is not None:
            self.optional_asset = assets.optional_table(self.identity, manifest)
            # An installation with the original projections computed the upscale clock's table from them, offline, before
            # that table was published; it keeps doing so rather than now needing the network for it.
            from .sampling_assets import UPSCALE_PREFIX
            if (self.optional_asset is not None and self.optional_asset['download']['prefix'].startswith(UPSCALE_PREFIX)
                    and assets.sources_present(root, manifest)):
                self.optional_asset = None

    def load(self, index, steps):
        from . import adaln_assets as assets
        if steps != len(self.identity['timesteps']):
            raise ValueError('AdaLN cache schedule length mismatch')
        path = self.root / f'{index:02d}.safetensors'
        marker = path.with_suffix('.json')
        checked = False
        if self.asset is None and not (path.is_file() and marker.is_file()) and self.shared is not None:
            path = self.shared / path.name
            marker = path.with_suffix('.json')
        if self.asset is not None:
            row = next((r for r in self.asset['files'] if r['index'] == index), None)
            if row is None:
                raise ValueError('Incomplete AdaLN model asset')
        elif not path.is_file() or not marker.is_file():
            if not self.fetch:
                return self.placeholder(index)
            if self.optional_asset is None:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            row = assets.download_table(path.parent, self.optional_asset, index)
            self.optional_downloaded.add(index)
            self.optional_download_bytes += row['bytes']
        else:
            # Only a table this cache computed itself; published tables setup installed beside it keep failing loudly.
            local = path.parent == self.root
            try:
                row = json.loads(marker.read_text(encoding='utf-8'))
                assets.check_table(path, row, self.identity)
            except (OSError, ValueError):
                if not local:
                    raise
                # Another process can be between renaming this table into place and writing its record. Read the
                # record once more; a table that still does not match is computed again rather than failing.
                try:
                    row = json.loads(marker.read_text(encoding='utf-8'))
                    assets.check_table(path, row, self.identity)
                except (OSError, ValueError):
                    return self.placeholder(index) if not self.fetch else None
            checked = True
        if not checked:
            assets.check_table(path, row, self.identity)
        if self.optional_asset is not None:
            published = next(r for r in self.optional_asset['files'] if r['index'] == index)
            if row['sha256'] == published['sha256']:
                self.optional_loaded.add(index)
                self.producer = self.optional_asset.get('producer')
        # These are small immutable constants; their producer's GPU/runtime is
        # deliberately irrelevant. Validate before transferring to this device.
        state = load_file(path, device=self.device)
        with serial_ops():
            finite = all(all_finite(tensor) for tensor in state.values())
        if not finite:
            raise ValueError('Nonfinite AdaLN model asset')
        return [state[f'step_{i}'].chunk(6, dim=-1) for i in range(steps)]

    def placeholder(self, index):
        """Zeros in a missing table's shape and dtype, for compiling only.

        The fused block kernels compile for these shapes; no value reaches one.
        A request installs or computes the real table before it samples.
        """
        if self.identity['dtype'] != 'BF16':
            raise ValueError('Unsupported AdaLN table dtype')
        self.placeholders.add(index)
        width = self.identity['channels'] * self.identity['components']
        return [torch.zeros(len(times) * self.identity['modality_rows'], width, dtype=torch.bfloat16,
                            device=self.device).chunk(6, dim=-1) for times in self.identity['timesteps']]

    def drop_stale_staging(self, age_seconds=3600):
        """Remove staging files a killed writer left here. A table takes seconds to write, so a staging file older
        than an hour belongs to no running writer; this is decided by age, not by process number, since a process
        number can be reused (and probing one on Windows signals it)."""
        cutoff = time.time() - age_seconds
        for staging in self.root.glob('*.partial'):
            try:
                if staging.stat().st_mtime < cutoff:
                    staging.unlink()
            except OSError:
                pass

    def save(self, index, module, *, producer_device=None):
        from . import adaln_assets as assets
        from .monitoring import save
        if self.asset is not None:
            raise ValueError('Published AdaLN model assets are immutable')
        path = self.root / f'{index:02d}.safetensors'
        # Two processes can compute the same table at the same moment (two engines or installations sharing one model
        # folder). Each writes its own staging file and renames it into place whole; a shared staging name let one
        # overwrite the other's half-written file.
        temporary = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex}.partial')
        state = {name: tensor.cpu() for name, tensor in module.state_dict().items()}
        if any(not all_finite(tensor) for tensor in state.values()):
            raise ValueError('Cannot save nonfinite AdaLN constants')
        try:
            save_file(state, temporary)
            row = {'bytes': temporary.stat().st_size, 'sha256': assets.file_hash(temporary)}
            assets.check_table(temporary, row, self.identity, verify_hash=False)
            # Windows refuses the rename while another process has the table open (hashing or loading it); wait a
            # little as monitoring.save does. A table this request computed is already in use in memory, so a cache
            # that cannot take it now never fails the request: the table in place (or none) stays as it is.
            for attempt in range(8):
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    from .system import windows
                    if attempt == 7 or not windows():
                        return  # elsewhere a refusal is the folder's permissions, which waiting does not change
                    time.sleep(.1 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)
        # The table and its record are two files: another writer's table can land between this rename and this record.
        # The record always describes the table that is there, so a later load never finds a mismatched pair.
        marker = path.with_suffix('.json')
        try:
            for _ in range(8):
                save(marker, row)
                there = {'bytes': path.stat().st_size, 'sha256': assets.file_hash(path)}
                if there == row:
                    break
                assets.check_table(path, there, self.identity, verify_hash=False)
                row = there
            else:
                # Still changing under other writers: leave no record rather than one that may not match, so the next
                # load treats the table as missing and computes it again.
                marker.unlink(missing_ok=True)
        except PermissionError:
            # Held open on Windows past monitoring.save's own retries: a load that finds the pair mismatched reads the
            # record again and otherwise computes the table again.
            return
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
def schedule_timesteps(steps, video_shift=12., audio_shift=3., device='cuda', task='t2va', upstream_grid=False):
    from .media_request import TASKS
    if task not in TASKS:
        raise ValueError('Unsupported AdaLN task')
    if upstream_grid:
        from diffusers import MiniMaxH3Scheduler as scheduler
    else:
        from .step_grid import scheduler
    video, audio = scheduler(shift=video_shift), scheduler(shift=audio_shift)
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
            # Reference audio rows are clean latents: t = 1 in H3's x_t = t*x0 + (1-t)*noise.
            values.append(video_t.new_tensor(1., dtype=torch.float32))
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
