"""Read real retained reports and check the browser JSON boundary."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_engine.comfy_library import list_videos

class LibraryGeometryTest(unittest.TestCase):
    def page(self, root, geometry, **measurements):
        folder = root / 'FreeVideo' / '2026-10-05' / ('a' * 32)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'video.mp4').write_bytes(b'retained-video-fixture')
        (folder / 'video.request.json').write_text(json.dumps({'success': True, 'geometry': geometry, **measurements}), encoding='utf-8')
        return list_videos(root)
    def test_nonfinite_fields_cannot_poison_browser_page(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in (float('nan'), float('inf'), float('-inf')):
                with self.subTest(value=value):
                    page = self.page(Path(folder), {'width': value, 'height': 768, 'frames': 243, 'fps': 24, 'seconds': value})
                    payload = json.dumps(page)
                    result = subprocess.run(['node', '-e', 'JSON.parse(require("fs").readFileSync(0,"utf8"))'], input=payload, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    json.dumps(page, allow_nan=False)
                    self.assertEqual(len(page['items']), 1)
                    self.assertEqual(page['items'][0]['geometry'], {'height': 768, 'frames': 243, 'fps': 24})
    def test_nonfinite_measurements_and_nested_plan_remain_parseable(self):
        with tempfile.TemporaryDirectory() as folder:
            page = self.page(Path(folder), {'width': 1344, 'height': 768},
                request_seconds=float('inf'), video={'sample_seconds': float('nan'), 'torch_peak_reserved_bytes': float('inf')},
                sampling_plan={'enabled': True, 'nested': [{'seconds': float('-inf')}, 8]})
            payload = json.dumps(page)
            result = subprocess.run(['node', '-e', 'JSON.parse(require("fs").readFileSync(0,"utf8"))'], input=payload, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            json.dumps(page, allow_nan=False)
            row = page['items'][0]
            self.assertIsNone(row['request_seconds']); self.assertIsNone(row['sample_seconds']); self.assertIsNone(row['vram_peak_bytes'])
            self.assertEqual(row['sampling_plan'], {'enabled': True, 'nested': [{'seconds': None}, 8]})

    def test_finite_numbers_preserved_and_metadata_still_filtered(self):
        with tempfile.TemporaryDirectory() as folder:
            geometry = {'width': 1344, 'height': 768, 'frames': 243, 'fps': 24, 'seconds': 10.125,
                        'private': 'not geometry', 'latent_frames': 72}
            page = self.page(Path(folder), geometry)
            self.assertEqual(page['items'][0]['geometry'], {key: geometry[key] for key in ('width','height','frames','fps','seconds')})

if __name__ == '__main__': unittest.main()
