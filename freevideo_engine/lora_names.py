"""Names a LoRA file uses for the FreeVideo model, shared by the engine and the pre-check."""
import re


# Per-block projections absent from the int8 package's linear index.
MODULATION = re.compile(r'transformer_blocks\.\d+\.adaln_proj\.')
INT8_UNMERGEABLE = ('This LoRA changes parts of the model that the int8 version cannot merge yet, '
                    'so it cannot be used for now. LoRAs that change only linear layers work.')

MIXED_RANK_ALPHA = ('This LoRA stores one alpha value for layers of different ranks, so its strength cannot be determined. '
                    'Use a copy that stores an alpha for every layer, or one with the same rank on every layer.')


# Factor names trainers write: PEFT lora_A/lora_B, Kohya and diffusers
# lora_down/lora_up, each with or without the trailing ".weight".
_FACTOR = re.compile(r'(.+)\.lora_(A|B|down|up)(?:\.weight)?')
_SIDE = {'A': 'A', 'down': 'A', 'B': 'B', 'up': 'B'}


def _canonical(key):
    return key.replace('.default.', '.')


def supported(key):
    """Whether adapter_keys can index this tensor name."""
    canonical = _canonical(key)
    return bool(_FACTOR.fullmatch(canonical)) or canonical.endswith(('.alpha', '.diff', '.diff_b'))


def adapter_keys(keys):
    """Index factor/difference names without reading any tensors."""
    pairs, full = {}, []
    for key in keys:
        canonical = _canonical(key)
        match = _FACTOR.fullmatch(canonical)
        if match:
            pair, side = pairs.setdefault(match[1], {}), _SIDE[match[2]]
            if side in pair:
                raise ValueError('Duplicate LoRA factor: ' + key)
            pair[side] = key
        elif canonical.endswith('.alpha'):
            pairs.setdefault(canonical[:-6], {})['alpha'] = key
        elif canonical.endswith(('.diff', '.diff_b')):
            bias = canonical.endswith('.diff_b')
            full.append((canonical[:-7] if bias else canonical[:-5], key, bias))
        else:
            raise ValueError('Unsupported LoRA tensor (no patches are ignored): ' + key)
    if not pairs and not full:
        raise ValueError('Adapter contains no LoRA factors')
    return pairs, full


def resolve_target(target, targets):
    if target not in targets and '.attn.' in target:
        target = target.replace('.attn.', '.attn.orig.', 1)
    return target


def int8_skip_reason(target, targets, *, full=False):
    """The online int8 patch policy, shared with the header-only check."""
    if full:
        if target.endswith('#bias'):
            return 'bias'
        return 'quantized_diff' if resolve_target(target, targets) in targets else 'other_layer'
    if target not in targets:
        return 'modulation' if MODULATION.match(target) else 'other_layer'
    return None


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
