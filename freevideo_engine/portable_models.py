"""Download an offline installation's models with the automatic installer's downloader.

Runs with the installation's own Python after an Environment ZIP was imported
without model packages. It writes the folders model packages would have filled
and a model inventory the launcher then assembles, exactly as for packages.
"""
import argparse
import json
import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def selection(runtime, hardware):
    """The model this GPU runs, in the folders an offline installation reads."""
    from .prepared_model import catalog, preferred_format, select
    scale = ('int8_convrot' if preferred_format(hardware) == 'int8_convrot'
             else 'per_tensor' if hardware.capability[0] >= 10 else 'rowwise')
    edge = Path(runtime) / 'models' / 'edge'
    chosen = select(hardware.capability, runtime, scale_granularity=scale)
    if chosen is None:
        raise ValueError('No prepared model matches this GPU')
    return dict(chosen, directory=str(edge), cache=str(edge / catalog()['variants'][scale]['cache_prefix']))


def plan(runtime, hardware, sampling_caches):
    from . import network
    from .download_settings import read
    from .environments import bootstrap_versions
    runtime = Path(runtime)
    engine = runtime / 'engine'
    networking = network.plan(json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8')),
        bootstrap_versions(json.loads((PACKAGE / 'bootstrap_versions.json').read_text(encoding='utf-8')), hardware.system),
        'unified', model_only=True, env=dict(os.environ, FREEVIDEO_HOME=str(engine)),
        proxy_mode=read(engine / 'download-settings.json')['proxy_mode'])
    networking['download_settings_path'] = str(engine / 'download-settings.json')
    return dict(root=str(engine), model_dir=str(runtime / 'models' / 'base'), encoder_dir=str(runtime / 'models' / 'encoder'),
                prepared_model=selection(runtime, hardware), sampling_caches=bool(sampling_caches),
                network=networking, verification='auto')


def inventory(runtime, value):
    """What was downloaded, as a model package lists it: paths under the installation."""
    from .bootstrap import model_target
    from .install_tuning import required_models
    from .network import hash_file
    from .prepared_model import files
    from .sampling_assets import install_files
    runtime = Path(runtime).resolve()
    prepared = value['prepared_model']
    rows = list(required_models(json.loads((PACKAGE / 'model_files.json').read_text(encoding='utf-8')), prepared))
    rows += files(prepared) + install_files(value['sampling_caches'])
    result = []
    for row in rows:
        path = model_target(row, Path(value['model_dir']), Path(value['encoder_dir']), prepared['directory']).resolve()
        # Small files pinned by git blob carry no sha256; the inventory needs one.
        result.append(dict(path=path.relative_to(runtime).as_posix(), bytes=row['bytes'],
                           sha256=row.get('sha256') or hash_file(path)))
    return dict(variant=prepared['scale_granularity'], files=sorted(result, key=lambda r: r['path']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sampling-caches', action='store_true')
    args = parser.parse_args(argv)
    from .hardware import detect
    from .monitoring import save
    from .provision import models
    value = plan(args.root, detect(), args.sampling_caches)
    models(value)
    save(args.out, inventory(args.root, value))


if __name__ == '__main__':
    main()
