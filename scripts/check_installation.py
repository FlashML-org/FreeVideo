"""Check an installation source bundle without models, a GPU or network access."""
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NoModelImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('torch', 'triton', 'transformers', 'diffusers'):
            raise RuntimeError('Installation unexpectedly imports ' + fullname)


def main():
    sys.meta_path.insert(0, NoModelImports())
    from freevideo_engine import cli, comfy_launcher, comfy_launcher_runtime, modern_launcher
    from freevideo_engine.desktop_runtime import check_launcher_payload, materialize_source, source_files

    spec = importlib.util.spec_from_file_location('freevideo_install_check', ROOT / '__init__.py',
                                                submodule_search_locations=[str(ROOT)])
    entry = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = entry
    spec.loader.exec_module(entry)
    assert callable(entry.comfy_entrypoint)
    assert (ROOT / entry.WEB_DIRECTORY).is_dir()
    assert callable(cli.main) and callable(comfy_launcher.main)
    assert callable(modern_launcher.main)
    assert (ROOT / 'freevideo_engine/launcher/Main.qml').is_file()

    workflow = json.loads((ROOT / 'example_workflows' / comfy_launcher_runtime.TEMPLATE).read_text(encoding='utf-8'))
    assert {node['type'] for node in workflow['nodes']} == {'FreeVideoMedia', 'FreeVideoGenerate'}
    with tempfile.TemporaryDirectory(prefix='FreeVideo install 中文 ') as folder:
        root = Path(folder)
        source = materialize_source(ROOT, root / 'launcher')
        payload = check_launcher_payload(source, root)
        assert all((source / path.relative_to(ROOT)).read_bytes() == path.read_bytes()
                   for path in source_files(ROOT))
        assert not any((source / name).exists() for name in ('benchmarks', 'docs', 'tests', 'AGENTS.md'))
        for name in ('rewriter.py', 'rewriter_setup.py', 'rewriter_models.json'):
            assert not (source / 'freevideo_engine' / name).exists(), 'Retired feature in package: ' + name
        assert not (source / 'web/rewriter.js').exists(), 'Retired prompt model panel in package'
        from freevideo_engine import support_report
        report = dict(success=False, prompt='PRIVATE PROMPT',
                      error_message='PRIVATE PROMPT', token='PRIVATE TOKEN',
                      resources=dict(ram=dict(process_tree_peak_guard_bytes=123)))
        output = root / 'video.mp4'
        report_file = support_report.write(output, report)
        assert report_file and report_file.name == 'video.debug.json'
        saved = report_file.read_text(encoding='utf-8')
        assert 'PRIVATE' not in saved
        assert json.loads(saved)['memory']['video']['ram']['process_tree_peak_guard_bytes'] == 123
    print(json.dumps(dict(success=True, resources=len(payload['resources']),
                          checks=['CLI and launcher imports', 'ComfyUI entry and workflow',
                                  'durable launcher source', 'packaged installation manifests']), indent=2))


if __name__ == '__main__':
    main()
