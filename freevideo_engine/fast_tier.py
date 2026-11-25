"""The fastest sampling mode: pi-H3's two-step no-decode adapter on the installed int8 weights.

fast2 is a single pass of two steps whose pixels come from the adapter's own head on
the last step, so the video VAE is not loaded; the sound still goes through the audio
VAE. Nothing is merged into the installed weights: the adapter's low-rank factors go
through the online LoRA folder, and its output heads, 8-dimensional modulation and the
whole matrices it replaces in the last four blocks change the Engine's in memory only
(fast_tier_runtime). The pixel head runs after sampling (dwd_head).

The adapter was trained for sigmas 0.99377, 0.86534 and 0 on plain H3.
Using it on VDN is outside what its authors trained or support.

This module imports no Torch unless it runs as the adapter-preparation child.
"""
import argparse
import json
from pathlib import Path
import stat
import sys

from .long_paths import io_path

MODES = ('fast2',)
FOLDER = 'pi-h3'
ADAPTER = 'pi_h3_2step_dwd_adapter_int8_convrot.safetensors'
CONSTANTS = 'pi_h3_adaln_constants.safetensors'
FILES = {
    ADAPTER: (2192176240, 'ef98527409eee4eaec8183265f4ee22d265680524237fae7149dbc203e11e739'),
    CONSTANTS: (19435680, '7949a370323b2a15e13f2e9160cfb1325207cde21b2629643f390be3e32f690b'),
}
MISSING = 'FAST_MODE_FILES_MISSING: '
# The adapter's points before the video (12) or audio (3) shift: max_raw_t 0.93, one step
# of 0.93 / 1.6 and a last one to 0, as published with the adapter.
RAW_GRID = (.93, .93 - .93 / 1.6, 0.)
STEPS = len(RAW_GRID) - 1


def files(model_root):
    """{relative name: path ready for I/O} after checking each file's published size.

    Only a file that is not there, or has another size, counts as missing. Any other
    failure to read it is reported with its own reason.
    """
    root = io_path(Path(model_root) / FOLDER)
    found, absent = {}, []
    for name, (size, _) in FILES.items():
        path = root / name
        try:
            info = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            absent.append(name)
            continue
        except OSError as error:
            raise OSError(error.errno, 'Could not read the fastest mode file %s: %s'
                          % (name, error.strerror or error)) from error
        if stat.S_ISREG(info.st_mode) and info.st_size == size:
            found[name] = path
        else:
            absent.append(name)
    if absent:
        raise FileNotFoundError(MISSING + 'install the fastest mode files (missing or incomplete: '
                                + ', '.join(absent) + ')')
    return found


def request(args):
    """Check a generate command at its entry and set its steps; None without --fast-mode."""
    mode = getattr(args, 'fast_mode', None)
    if mode is None:
        return None
    if mode not in MODES:
        raise ValueError('Fast mode must be one of ' + ', '.join(MODES))
    if sys.platform == 'darwin':
        raise ValueError('The fastest mode is not available on Mac')
    for name, label in (('continue_from', 'a continued part'), ('continue_anchor', 'a continued part'),
                        ('preview', 'a preview'), ('refine_from', 'finishing a preview'),
                        ('upscale_from', 'an upscale'), ('redo_sound', 'redoing the sound'),
                        ('step_cache', 'a step cache')):
        if getattr(args, name, None) not in (None, False, 'none'):
            raise ValueError('The fastest mode cannot be used with ' + label)
    if getattr(args, 'upscale_factor', None) not in (None, 2):
        raise ValueError('The fastest mode uses the 2x upscale')
    from .paths import installed_model_root
    paths = files(installed_model_root())
    args.base_steps = STEPS
    args.two_pass = False
    args.refine_steps = 3
    return dict(mode=mode, adapter=str(paths[ADAPTER]), constants=str(paths[CONSTANTS]),
                adapter_sha256=FILES[ADAPTER][1], constants_sha256=FILES[CONSTANTS][1])


def bridge_steps(mode, *, preview=False, refine_from=None, continue_from=None, upscale_from=None, step_cache=None,
                 upscale_factor=2, parts=1):
    """(two_pass, base_steps, refine_steps) of a queued request, refused before anything is loaded."""
    if mode not in MODES:
        raise ValueError('Fast mode must be one of ' + ', '.join(MODES))
    for value, label in ((preview, 'a preview'), (refine_from, 'finishing a preview'),
                         (continue_from, 'a continued part'), (upscale_from, 'an upscale'),
                         (step_cache not in (None, 'none'), 'a step cache'), (upscale_factor != 2, 'another upscale factor'),
                         (parts > 1, 'a video made in parts')):
        if value:
            raise ValueError('The fastest mode cannot be used with ' + label)
    return False, STEPS, 3


def adapter_row(spec):
    """The adapter as the first row of the online LoRA request."""
    return dict(path=spec['adapter'], sha256=spec['adapter_sha256'], bytes=FILES[ADAPTER][0],
                strength=1., alpha=None, only_factors=True)


def factor_patches(row, targets):
    """The adapter's low-rank pairs. Its whole tensors are applied by fast_tier_runtime.

    The INT8 file names its layers the original way (blocks.N.mlp.fc1, ...); the shared
    LoRA name rules map them and swap fc1's [gate; value] halves, as for any such LoRA.
    """
    from .lora_names import _targets, resolve_target
    from .tensor_io import open_tensors
    suffix = '.lora_A.weight'
    result = []
    with open_tensors(row['path']) as source:
        for key in source.keys():
            if not key.endswith(suffix):
                continue
            other = key[:-len(suffix)] + '.lora_B.weight'
            ((name, split, swap),) = _targets(key[:-len(suffix)])
            target = resolve_target(name, targets)
            a, b = source.get_slice(key).get_shape(), source.get_slice(other).get_shape()
            if target not in targets or targets[target]['shape'] != [b[0], a[1]] or a[0] != b[1]:
                raise ValueError('The fastest mode adapter does not fit this model: ' + target)
            if split is not None:
                raise ValueError('The fastest mode adapter has a fused projection: ' + key)
            result.append(dict(A=key, B=other, target=target, split=None, swap=swap,
                               scale=float(row['strength']), file=row['path']))
    if not result:
        raise ValueError('The fastest mode adapter has no low-rank factors')
    return result


def main():
    """The LoRA preparation child for a fast request.

    The folder identity hashes lora_cache.py and lora_online_cache.py, so a change
    there would rebuild every installed LoRA folder. Only this process indexes the
    adapter row differently; user LoRA rows take the existing route.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args()
    value = json.loads(args.request.read_text(encoding='utf-8'))
    if value.get('mode') != 'online':
        raise ValueError('The fastest mode needs the online LoRA placement')
    import torch
    from . import lora_online_cache as online
    from .monitoring import save
    torch.set_num_threads(8)
    existing = online.index_adapter

    def index_adapter(row, targets, skipped=None):
        return factor_patches(row, targets) if row.get('only_factors') else existing(row, targets, skipped)
    online.index_adapter = index_adapter
    output, report = online.prepare(value['cache'], value['adapters'])
    save(value['result'], {'cache': str(output), 'report': report})


if __name__ == '__main__':
    main()
