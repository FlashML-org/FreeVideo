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


def model_rows(value):
    from .bootstrap import installation_model_files
    return installation_model_files(value['prepared_model'], value.get('reuse_cache'), value['sampling_caches'])


def plan(runtime, hardware, sampling_caches, *, model_dirs=(), local_only=False):
    from . import network
    from .download_settings import read
    from .environments import bootstrap_versions
    runtime = Path(runtime)
    engine = runtime / 'engine'
    from .bootstrap import model_target, existing_parent
    from .local_models import scan_many, key, can_link, progress_output
    value = dict(root=str(engine), model_dir=str(runtime / 'models' / 'base'), encoder_dir=str(runtime / 'models' / 'encoder'),
                 prepared_model=selection(runtime, hardware), sampling_caches=bool(sampling_caches), verification='auto')
    def target(row):
        return model_target(row, Path(value['model_dir']), Path(value['encoder_dir']), value['prepared_model']['directory'])
    needed = [row for row in model_rows(value) if not target(row).exists()]
    local = scan_many(list(model_dirs), needed, callback=progress_output) if model_dirs and needed else dict(matches={})
    for row in needed:
        match = local['matches'].get(key(row))
        if match:
            match['method'] = 'hardlink' if can_link(match['source'], existing_parent(target(row).parent)) else 'copy'
    value['local_models'] = local
    missing = [row for row in needed if key(row) not in local['matches']]
    if local_only and missing:
        from .offline_packages import pack_name
        name = target(missing[0]).relative_to(runtime).as_posix()
        raise ValueError('还需导入「%s」包，缺少文件：%s' % (pack_name(name), name))
    networking = network.plan(json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8')),
        bootstrap_versions(json.loads((PACKAGE / 'bootstrap_versions.json').read_text(encoding='utf-8')), hardware.system),
        'unified', model_only=True, offline=local_only or not missing, env=dict(os.environ, FREEVIDEO_HOME=str(engine)),
        proxy_mode=read(engine / 'download-settings.json')['proxy_mode'])
    networking['download_settings_path'] = str(engine / 'download-settings.json')
    return dict(value, network=networking)


def inventory(runtime, value):
    """What was downloaded, as a model package lists it: paths under the installation."""
    from .bootstrap import model_target
    from .network import hash_file
    runtime = Path(runtime).resolve()
    prepared = value['prepared_model']
    rows = model_rows(value)
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
    parser.add_argument('--reuse-models-manifest', type=Path)
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args(argv)
    from .hardware import detect
    from .monitoring import save
    from .provision import models
    directories = []
    if args.reuse_models_manifest:
        from .local_models import library_roots
        if args.reuse_models_manifest.stat().st_size > 512 * 1024:
            raise ValueError('Model library manifest is too large')
        libraries = json.loads(args.reuse_models_manifest.read_text(encoding='utf-8'))
        if not isinstance(libraries, dict) or libraries.get('version') != 1:
            raise ValueError('Invalid model library manifest')
        directories = library_roots(libraries.get('roots'))
    value = plan(args.root, detect(), args.sampling_caches, model_dirs=directories, local_only=args.local_only)
    models(value)
    save(args.out, inventory(args.root, value))


if __name__ == '__main__':
    main()
