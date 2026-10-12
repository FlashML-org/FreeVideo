"""Exercise native request-file handoff; no launcher, network or user files."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_engine import launcher_bridge as bridge

class LauncherBridgeTest(unittest.TestCase):
    def test_cancel_arriving_during_update_read_is_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); bridge.request(root, 'update')
            read, resume = threading.Event(), threading.Event()
            original = json.loads; consumed = []; failures = []
            def paused_parse(value):
                parsed = original(value)
                read.set()
                if not resume.wait(5): raise TimeoutError('reader not resumed')
                return parsed
            def consume():
                try: consumed.append(bridge.take_request(root))
                except BaseException as error: failures.append(error)
            # Only the scheduling point is controlled. Both producers and the
            # consumer use their real atomic JSON writes and native filesystem.
            with patch.object(bridge.json, 'loads', paused_parse):
                thread = threading.Thread(target=consume); thread.start()
                try:
                    self.assertTrue(read.wait(5)); bridge.request(root, 'cancel')
                finally:
                    resume.set(); thread.join(5)
            self.assertFalse(thread.is_alive()); self.assertEqual(failures, [])
            self.assertEqual(consumed[0]['action'], 'update')
            self.assertEqual(bridge.take_request(root)['action'], 'cancel')
            self.assertIsNone(bridge.take_request(root))
            self.assertEqual(list(root.iterdir()), [])
    def test_missing_invalid_and_ordinary_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.assertIsNone(bridge.take_request(root))
            for raw in ('not JSON', json.dumps({'action': 'unexpected'}), '[]'):
                (root / 'request.json').write_text(raw, encoding='utf-8')
                self.assertIsNone(bridge.take_request(root)); self.assertEqual(list(root.iterdir()), [])
            for action in ('update', 'cancel'):
                bridge.request(root, action); self.assertEqual(bridge.take_request(root)['action'], action)
                self.assertIsNone(bridge.take_request(root)); self.assertEqual(list(root.iterdir()), [])

if __name__ == '__main__': unittest.main()
