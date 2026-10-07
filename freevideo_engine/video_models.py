"""The video models a user can install and generate with: MiniMax H3 and the Prism preview.

Standard library only: the installer, the launchers and the ComfyUI bridge read
this before any model environment exists. MiniMax H3 stays the default; Prism is
downloaded, offered and recorded only when it is chosen. H3 keeps its flat keys
in machine.json; Prism is recorded under ``models.prism``.
"""
from functools import lru_cache
import json
import math
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
H3, PRISM = 'h3', 'prism'
MODELS = (H3, PRISM)
DEFAULT = (H3,)
NAMES = {H3: ('MiniMax H3', 'MiniMax H3'), PRISM: ('Prism (preview)', 'Prism（预览）')}
# The ComfyUI combo shows names; workflows store the name, the engine gets the id.
COMFY_LABELS = {H3: 'MiniMax H3', PRISM: 'Prism (preview)'}
# Machine.json keys the H3 engine needs, beyond the shared environment.
H3_KEYS = ('cache', 'base', 'checkpoint', 'model_paths', 'encoder')
SHARED_KEYS = ('root', 'python', 'comfy_python', 'comfy_root', 'vdn_root', 'model_root')
# Prism generates at 24 fps; its video VAE needs 4n + 1 frames. The official
# setting is 720p for 205 frames (8.54 s); other sizes and lengths are experimental.
PRISM_FPS = 24
PRISM_OFFICIAL = dict(width=1280, height=720, frames=205)
PRISM_GPU = (8, 0)


def parse(value=None):
    """Normalise "h3,prism", a list or None to an ordered tuple of model ids."""
    if value is None:
        return DEFAULT
    if isinstance(value, str):
        items = [part.strip().lower() for part in value.split(',') if part.strip()]
    elif isinstance(value, (list, tuple)):
        if not all(isinstance(part, str) for part in value):
            raise ValueError('Video models must be names such as h3 or prism')
        items = [part.strip().lower() for part in value]
    else:
        raise ValueError('Video models must be a list such as h3,prism')
    unknown = sorted(set(items) - set(MODELS))
    if unknown:
        raise ValueError('Unknown video model: %s. Choose h3, prism or h3,prism.' % ', '.join(unknown))
    result = tuple(name for name in MODELS if name in items)
    if not result:
        raise ValueError('Choose at least one video model: h3, prism or h3,prism.')
    return result


def from_comfy(value):
    """The model id for a node or request value; absent means MiniMax H3."""
    if value in (None, ''):
        return H3
    for name, label in COMFY_LABELS.items():
        if value in (name, label):
            return name
    raise ValueError('Unknown video model: %s. Choose MiniMax H3 or Prism (preview).' % value)


def title(name, zh=False):
    return NAMES[name][1 if zh else 0]


@lru_cache(maxsize=1)
def prism_manifest():
    return json.loads((PACKAGE / 'prism_models.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def prism_tiers():
    """The five Prism quality levels (light, standard, high, max, original), in order."""
    return json.loads((PACKAGE / 'prism_tiers.json').read_text(encoding='utf-8'))


def complete(row):
    """A row can be downloaded and verified: pinned revision, size and hash."""
    return (isinstance(row.get('revision'), str) and len(row['revision']) == 40
            and type(row.get('bytes')) is int and row['bytes'] > 0
            and isinstance(row.get('sha256'), str) and len(row['sha256']) == 64)


def prism_variant(capability):
    """The prepared weight format for a GPU, or None when it is too old.

    FP8 needs native FP8 tensor cores (SM 8.9+); RTX 30 cards use INT8.
    """
    try:
        capability = tuple(int(n) for n in capability)
    except (TypeError, ValueError):
        return None
    manifest = prism_manifest()
    variants = manifest['variants']
    # auto_variants (prism_models.json): the formats setup may pick, preferred first.
    order = manifest.get('auto_variants') or sorted(
        variants, key=lambda key: tuple(variants[key]['min_capability']), reverse=True)
    for name in order:
        if name in variants and capability >= tuple(variants[name]['min_capability']):
            return name
    return None


def prism_addons():
    """Optional Prism downloads (``addons`` in prism_models.json), e.g. the bf16
    weights that the original level samples at the original precision."""
    return dict(prism_manifest().get('addons') or {})


def prism_addon_choice(value):
    """Normalise a list of add-on ids (unknown ids are an error)."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [part.strip() for part in value.split(',') if part.strip()]
    if not isinstance(value, (list, tuple)) or not all(isinstance(part, str) for part in value):
        raise ValueError('Prism add-ons must be a list such as bf16')
    known = prism_addons()
    unknown = sorted(set(value) - set(known))
    if unknown:
        raise ValueError('Unknown Prism add-on: %s' % ', '.join(unknown))
    return tuple(name for name in known if name in value)


def prism_rows(variant, directory, addons=()):
    """Every file of one Prism variant (and the chosen add-ons), destined for ``directory``."""
    manifest = prism_manifest()
    if variant not in manifest['variants']:
        raise ValueError('Unknown Prism weight format: %s' % variant)
    rows = list(manifest['files']) + list(manifest['variants'][variant]['files'])
    for name in prism_addon_choice(addons):
        rows += [dict(row, addon=name) for row in manifest['addons'][name]['files']]
    return [dict(row, model=PRISM, directory=str(directory)) for row in rows]


def prism_bytes(variant=None, addons=()):
    """(known bytes, files without a published size) for a variant, or the larger one."""
    manifest = prism_manifest()
    names = [variant] if variant else list(manifest['variants'])
    extra = [row for name in prism_addon_choice(addons) for row in manifest['addons'][name]['files']]
    best = (0, 0)
    for name in names:
        rows = list(manifest['files']) + list(manifest['variants'][name]['files']) + extra
        value = (sum(row['bytes'] for row in rows if type(row.get('bytes')) is int),
                 sum(type(row.get('bytes')) is not int for row in rows))
        best = max(best, value)
    return best


def prism_addon_rows(zh=False):
    """What the setup screens offer for each add-on (name, levels, size)."""
    rows = []
    for name, value in prism_addons().items():
        files = value.get('files') or []
        rows.append(dict(id=name, title=value.get('zh' if zh else 'label') or value.get('label') or name,
                         levels=list(value.get('levels') or []), optional=value.get('optional', True),
                         bytes=sum(row['bytes'] for row in files if type(row.get('bytes')) is int),
                         size_known=all(type(row.get('bytes')) is int for row in files),
                         published=bool(files) and all(complete(row) for row in files)))
    return rows


def h3_bytes():
    """Approximate first download of MiniMax H3 with the prepared slim weights."""
    from .storage import conversion_source
    rows = json.loads((PACKAGE / 'model_files.json').read_text(encoding='utf-8'))
    prepared = json.loads((PACKAGE / 'prepared_models.json').read_text(encoding='utf-8'))
    weights = max(sum(row['bytes'] for row in value['files']) for value in prepared['variants'].values())
    try:
        from .sampling_assets import install_files
        tables = sum(row['bytes'] for row in install_files(False))
    except (OSError, ValueError, KeyError, TypeError):
        tables = 0
    return sum(row['bytes'] for row in rows if not conversion_source(row)) + weights + tables


# Shown wherever Prism can be chosen (launcher, ComfyUI setup, creative workspace, CLI review, docs),
# always next to a link to PRISM_SHOWCASE: the five levels side by side, so people look before installing.
PRISM_NOTICE = ('Read first: Prism is an early-access preview and may occasionally have audio problems, so choose it '
                'with care. Its current accelerated version may be slower and lower in quality than MiniMax H3, whose '
                'ecosystem is mature; we recommend MiniMax H3. Before trying Prism, watch the comparison first.',
                '必读须知：Prism 目前为尝鲜测试版，可能存在偶发的音频问题，请谨慎选择。现在支持的加速版本在速度和质量上'
                '可能不如生态成熟的 MiniMax H3，推荐使用成熟的 MiniMax H3。试用前请先看效果展示。')
PRISM_SHOWCASE = 'https://freevideo-community.pages.dev/prism/'


def choices(zh=False):
    """What the launchers show for each model, sizes taken from the manifests."""
    import sys
    known, unknown = prism_bytes()
    mac = sys.platform == 'darwin'  # Prism needs an NVIDIA GPU.
    return [dict(id=H3, title=title(H3, zh), tag=('Recommended', '推荐')[zh], preview=False, available=True,
                 bytes=h3_bytes(), size_known=True,
                 detail=('Text, first / last frame and reference inputs · LoRAs',
                         '文本、首尾帧与参考输入 · 支持 LoRA')[zh]),
            dict(id=PRISM, title='Prism', tag=('Preview', '预览')[zh], preview=True, available=not mac,
                 bytes=known, size_known=not unknown,
                 detail=('Needs an NVIDIA GPU; not available on Mac', '需要 NVIDIA 显卡，Mac 暂不支持')[zh] if mac else
                        ('Early-access preview · image to video + audio at 720p',
                         '尝鲜测试版 · 图生视频 + 音频 · 720p')[zh],
                 notice=PRISM_NOTICE[zh], addons=prism_addon_rows(zh))]


def prism_directory(root, saved=None):
    record = ((saved or {}).get('models') or {}).get(PRISM) or {}
    return Path(record.get('root') or Path(root) / 'models' / 'prism').expanduser().resolve()


def prism_saved_addons(saved):
    """Add-ons an existing installation recorded under ``models.prism``."""
    record = ((saved or {}).get('models') or {}).get(PRISM) or {}
    try:
        return prism_addon_choice(record.get('addons'))
    except ValueError:
        return ()


# Smallest machine Prism generates on (1280 x 720, 8.5 s): an RTX 4070 (12 GB, 11.0 GiB
# usable under Windows) peaked at 10.4 GiB of dedicated memory, 22.6 GiB of RAM.
PRISM_MIN_VRAM_BYTES = int(11.5 * 2**30)   # cards sold as 12 GB report about 11.99 GiB
PRISM_MIN_RAM_BYTES = int(19 * 2**30)      # machines sold with 20 GB


def prism_hardware_errors(vram_total=None, ram_total=None):
    """Why this machine cannot run Prism (setup refuses before the 47 GB download)."""
    errors = []
    if type(vram_total) is int and 0 < vram_total < PRISM_MIN_VRAM_BYTES:
        errors.append('Prism (preview) needs at least 12 GB of VRAM; this GPU has %.1f GiB. Deselect Prism; MiniMax H3 '
                      'runs on this GPU. · Prism（预览）至少需要 12 GB 显存，这块显卡只有 %.1f GiB。请取消选择 Prism；'
                      'MiniMax H3 可以在这块显卡上运行。' % (vram_total / 2**30, vram_total / 2**30))
    if type(ram_total) is int and 0 < ram_total < PRISM_MIN_RAM_BYTES:
        errors.append('Prism (preview) needs at least 20 GB of RAM (32 GB recommended); this computer has %.1f GiB. '
                      'Deselect Prism. · Prism（预览）至少需要 20 GB 内存（建议 32 GB），这台电脑只有 %.1f GiB。'
                      '请取消选择 Prism。' % (ram_total / 2**30, ram_total / 2**30))
    return errors


def prism_plan(capability, root, saved=None, system=None, addons=None, vram_total=None, ram_total=None):
    """What setup downloads for Prism on this machine, and why it cannot.
    ``addons`` None keeps the add-ons the installation already has; ``vram_total`` /
    ``ram_total`` (bytes, the selected GPU and the machine) below Prism's minimum refuse."""
    manifest = prism_manifest()
    addons = prism_saved_addons(saved) if addons is None else prism_addon_choice(addons)
    errors = []
    variant = None
    if system == 'Darwin':
        errors.append('Prism (preview) needs an NVIDIA GPU; on Mac choose MiniMax H3 only.')
    else:
        variant = prism_variant(capability or ())
        if variant is None:
            errors.append('Prism (preview) needs an NVIDIA RTX 30 series or newer GPU (SM 8.0+).')
        errors += prism_hardware_errors(vram_total, ram_total)
    directory = prism_directory(root, saved)
    rows = prism_rows(variant, directory) if variant else []
    pending = [row['file'] for row in rows if not complete(row)]
    if variant and pending:
        errors.append('Prism (preview) model files are not published yet (%d of %d files have no pinned size and hash). '
                      'Deselect Prism, or update FreeVideo and try again.' % (len(pending), len(rows)))
    extra = [row for row in prism_rows(variant, directory, addons) if row.get('addon')] if variant else []
    waiting = [row['file'] for row in extra if not complete(row)]
    if variant and not pending and waiting:
        errors.append('The optional Prism (preview) Original-level bf16 weights are not published yet; '
                      'turn that option off, or update FreeVideo and try again.')
    known, unknown = prism_bytes(variant, addons) if variant else (0, 0)
    addon_bytes = prism_bytes(variant, addons)[0] - prism_bytes(variant)[0] if variant else 0
    return dict(variant=variant, label=manifest['variants'][variant]['label'] if variant else None,
                directory=str(directory), repo=manifest['repo'], revision=manifest['revision'],
                total_bytes=known, unknown_files=unknown, published=bool(variant) and not pending and not waiting,
                addons=list(addons), addon_bytes=addon_bytes, errors=errors)


def _role_paths(directory, rows):
    """Where each role lives: a lone file by itself, a component (config +
    weights, blocks/ + root.safetensors, several config folders) by the folder
    that holds all of its files."""
    import os
    roles = {}
    for row in rows:
        roles.setdefault(row['role'], []).append(Path(row['file']))
    paths = {}
    for role, files in roles.items():
        if len(files) == 1:
            paths[role] = str(directory / files[0])
        else:
            common = Path(os.path.commonpath([str(path.parent) for path in files]))
            paths[role] = str(directory / common) if str(common) not in ('', '.') else str(directory)
    return paths


def prism_record(plan):
    """The ``models.prism`` entry setup writes once every file is verified."""
    directory = Path(plan['directory'])
    addons = prism_addon_choice(plan.get('addons'))
    rows = prism_rows(plan['variant'], directory, addons)
    paths = dict(root=str(directory), **_role_paths(directory, [row for row in rows if not row.get('addon')]))
    record = dict(ready=True, preview=True, variant=plan['variant'], root=str(directory),
                  repo=plan['repo'], revision=plan['revision'], paths=paths,
                  tiers='prism_tiers.json', schema_version=prism_manifest()['schema_version'],
                  addons=list(addons))
    if addons:
        record['addon_paths'] = {name: _role_paths(directory, [row for row in rows if row.get('addon') == name])
                                 for name in addons}
    return record


def h3_installed(machine):
    return isinstance(machine, dict) and all(machine.get(key) for key in H3_KEYS)


def prism_installed(machine):
    record = ((machine or {}).get('models') or {}).get(PRISM) if isinstance(machine, dict) else None
    return (isinstance(record, dict) and record.get('ready') is True and isinstance(record.get('root'), str)
            and Path(record['root']).is_dir())


def installed(machine):
    """Model ids this machine.json can generate with."""
    return tuple(name for name, ready in ((H3, h3_installed(machine)), (PRISM, prism_installed(machine))) if ready)


def saved_selection(saved, prior=None):
    """The models an earlier setup of this installation chose."""
    value = (prior or {}).get('selected_models')
    try:
        if value is not None:
            return parse(value)
    except ValueError:
        pass
    if not (saved or {}).get('ready'):
        return DEFAULT
    return installed(saved) or DEFAULT


def license_urls(selected):
    selected = parse(selected)
    urls = []
    if H3 in selected:
        urls += ['https://huggingface.co/OpenVDN/vdn-minimax-h3/blob/751739ee5b9e3ac802dca5d5111075fdaeb47885/LICENSE',
                 'https://huggingface.co/t8star/Vdn-Minimax-H3-Comfy']
    if PRISM in selected:
        urls += ['%s (%s): %s' % (row['name'], row['license'], row['url']) for row in prism_manifest()['licenses']]
    return urls


def license_names(selected, zh=False):
    """One readable line of model licenses for review and consent text."""
    selected = parse(selected)
    names = []
    if H3 in selected:
        names.append(('MiniMax H3, H3 text encoder', 'MiniMax H3、H3 文本编码器')[zh])
    if PRISM in selected:
        names.append(('Prism (MIT); MOVA, Wan2.2 and LightX2V distill models (Apache-2.0)',
                      'Prism（MIT）；MOVA、Wan2.2、LightX2V 蒸馏模型（Apache-2.0）')[zh])
    return ('; ' if not zh else '；').join(names)


def prism_geometry(width=PRISM_OFFICIAL['width'], height=PRISM_OFFICIAL['height'], frames=None, seconds=None):
    """Prism's canvas: 24 fps, 4n + 1 frames, sizes in multiples of 16.

    Durations round up to the next 4n + 1 frames, as H3 rounds to 17n + 5:
    8.5 s asks for 204 frames and becomes 205 (8.542 s).
    """
    if any(type(n) is not int or n < 256 or n > 4096 or n % 16 for n in (width, height)):
        raise ValueError('Prism (preview): width and height must be multiples of 16 from 256 to 4096 pixels.')
    if frames is not None and seconds is not None:
        raise ValueError('Specify frames or seconds, not both.')
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                                or not math.isfinite(seconds) or seconds <= 0):
        raise ValueError('Duration must be a positive number of seconds.')
    requested = math.ceil(seconds * PRISM_FPS) if seconds is not None else (
        PRISM_OFFICIAL['frames'] if frames is None else frames)
    if type(requested) is not int or requested < 1:
        raise ValueError('Frame count must be a positive integer.')
    aligned = requested + (1 - requested) % 4
    if aligned < 9:
        raise ValueError('Prism (preview) needs at least 9 frames (0.375 seconds).')
    official = (width, height, aligned) == tuple(PRISM_OFFICIAL[k] for k in ('width', 'height', 'frames'))
    return dict(width=width, height=height, fps=PRISM_FPS, requested_frames=requested,
                requested_seconds=seconds, frames=aligned, seconds=aligned / PRISM_FPS,
                latent_frames=(aligned - 1) // 4 + 1, model=PRISM, experimental=not official,
                alignment='Prism: round up to 4*n+1 frames at 24 fps; 1280 x 720 x 205 frames is the official setting.')


def prism_tier(tier_id=None, steps=None):
    """A Prism quality level: by id; else (requests saved before the levels had ids)
    the level those steps selected then (prism_tiers.json legacy_steps); else the
    default level. None for an unknown id."""
    tiers = prism_tiers()
    rows = tiers['tiers']
    if tier_id is not None:
        return next((tier for tier in rows if tier['id'] == tier_id), None)
    legacy = tiers.get('legacy_steps', {}).get(str(steps)) if steps is not None else None
    wanted = legacy or tiers['default']
    return next((tier for tier in rows if tier['id'] == wanted), None)


def prism_tier_ids():
    return [tier['id'] for tier in prism_tiers()['tiers']]


def validate_prism_tier(tier_id):
    if tier_id is not None and prism_tier(tier_id) is None:
        raise ValueError('Unknown Prism (preview) quality level: %s (choose one of %s)'
                         % (tier_id, ', '.join(prism_tier_ids())))


def prism_t2v(tier):
    """Whether this Prism level generates without a first frame (prism_tiers.json
    ``t2v``; undistilled levels only, as the engine checks)."""
    return bool(tier and tier.get('t2v') and not tier.get('distilled', True))


def prism_media_error(media, conditioning=None, tier=None):
    """Why Prism cannot use these inputs, or None. Prism animates one first frame,
    or (levels with ``t2v``) generates from the text alone."""
    media = media or {}
    if conditioning or media.get('conditioning_info'):
        return 'Prism (preview) encodes its own prompt; disconnect the H3 conditioning input or choose MiniMax H3.'
    if media.get('references'):
        return ('Prism (preview) does not use reference images, video or audio; disable them in Media '
                'or choose MiniMax H3.')
    if media.get('last'):
        return 'Prism (preview) starts from a first frame only; remove the last frame or choose MiniMax H3.'
    if media.get('loras'):
        return 'LoRAs are for MiniMax H3; disable them to generate with Prism (preview).'
    if not media.get('first') and not prism_t2v(tier):
        return ('This Prism (preview) quality level needs a first frame: add an image in Media and set it to '
                'First frame, or choose Max or Original for text to video, or MiniMax H3.')
    return None


PRISM_MAX_STEPS = 100


def validate_prism_steps(base_steps):
    if type(base_steps) is not int or not 1 <= base_steps <= PRISM_MAX_STEPS:
        raise ValueError('Prism (preview) steps must be an integer between 1 and %d' % PRISM_MAX_STEPS)


def prism_sampling_plan(canvas, tier):
    """The sampling plan recorded for a Prism request (single pass, one level)."""
    steps = tier['steps']
    shape = {key: canvas[key] for key in ('width', 'height', 'frames')}
    return dict(version=1, model=PRISM, requested=False, enabled=False, mode='prism', tier=tier['id'],
                base_steps=steps, refine_steps=0, total_steps=steps, first=shape, second=None,
                reason='Prism (preview) %s: %d steps at the target size.' % (tier['en'], steps))
