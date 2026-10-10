"""Existing ComfyUI environments keep the interpreter's virtualenv prefix."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine.comfy_launcher_runtime import layout, probe_host


@unittest.skipIf(os.name == 'nt', 'Windows venv executables are not symlinks')
class ComfyVenvTests(unittest.TestCase):
    def test_layout_probes_the_selected_venv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve() / 'ComfyUI'
            root.mkdir()
            (root / 'main.py').write_text('')
            (root / 'folder_paths.py').write_text('def get_folder_paths(name): return []\n')
            (root / 'comfy_api/latest').mkdir(parents=True)
            (root / 'utils').mkdir()
            (root / 'utils/extra_config.py').write_text('def load_extra_path_config(path): pass\n')
            environment = root / '.venv'
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            python = environment / 'bin/python'
            site = Path(subprocess.check_output([str(python), '-I', '-c',
                'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
            for name in ('aiohttp', 'yaml', 'numpy', 'torch', 'safetensors', 'comfyui_frontend_package'):
                (site / (name + '.py')).write_text('')
            descriptor = layout(root)
            result = probe_host(descriptor)
            self.assertTrue(result['ready'], result)
            self.assertEqual(descriptor['python'], str(python))
            self.assertEqual(Path(result['python']), python)


if __name__ == '__main__':
    unittest.main()
