"""Exercise real CLI media request reading without models or dependencies."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_engine.media_request import digest, read, task_for

class MediaRequestTest(unittest.TestCase):
    def test_utf8_and_windows_bom_preserve_non_ascii_relative_media(self):
        with tempfile.TemporaryDirectory(prefix='FreeVideo media 中文 ') as folder:
            root = Path(folder)
            media = root / '第一帧.png'; media.write_bytes(b'fixture image content')
            value = {'version': 1, 'first': media.name}
            for encoding in ('utf-8', 'utf-8-sig'):
                with self.subTest(encoding=encoding):
                    request = root / 'request.json'
                    request.write_text(json.dumps(value, ensure_ascii=False), encoding=encoding)
                    loaded = read(request)
                    self.assertEqual(task_for(loaded), 'i2va')
                    self.assertEqual(loaded['first']['path'], str(media.resolve()))
                    self.assertEqual(loaded['first']['sha256'], digest(media))
    def test_bom_does_not_bypass_request_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder) / 'request.json'
            for value in ({'version': 2}, {'version': 1, 'unknown': 'value'}):
                request.write_text(json.dumps(value), encoding='utf-8-sig')
                with self.subTest(value=value), self.assertRaises(ValueError): read(request)

if __name__ == '__main__': unittest.main()
