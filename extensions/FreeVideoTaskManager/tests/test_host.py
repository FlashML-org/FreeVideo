"""Run offline; use the real psutil with a mocked Comfy route registry."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

class HostTest(unittest.TestCase):
    def test_read_only_metrics(self):
        routes = {}
        def register(path):
            def decorate(fn):
                routes[path] = fn
                return fn
            return decorate
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(
            routes=types.SimpleNamespace(get=register)))
        original = sys.modules.get("server")
        sys.modules["server"] = server
        try:
            spec = importlib.util.spec_from_file_location("ftm_test", Path(__file__).resolve().parents[1] / "__init__.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertEqual(set(routes), {"/freevideo-task-manager/host"})
            self.assertEqual(mod.NODE_CLASS_MAPPINGS, {})
            result = asyncio.run(routes["/freevideo-task-manager/host"](None))
            data = json.loads(result.body)
            self.assertTrue(0 <= data["cpu"] <= 100)
            self.assertTrue(0 <= data["ram_used"] <= data["ram_total"])
            self.assertTrue(0 <= data["disk_used"] <= data["disk_total"])
            self.assertEqual(result.headers["Cache-Control"], "no-store")
        finally:
            if original is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = original

if __name__ == "__main__":
    unittest.main()
