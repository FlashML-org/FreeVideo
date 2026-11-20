"""The SDK HTTP fallback uses the one route selected by the parent worker."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine import network
from freevideo_engine.provider_resume import hf_download


@unittest.skipUnless(shutil.which('curl'), 'curl is required by the real HTTP adapter')
class HttpRouteTests(unittest.TestCase):
    def test_worker_route_is_not_retried_directly(self):
        payload = b'Pinned model payload'
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        class FailedProxy(Handler):
            def do_GET(self):
                self.send_response(503)
                self.send_header('Content-Length', '0')
                self.end_headers()
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), FailedProxy)
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()
        try:
            proxy_url = 'http://127.0.0.1:%d' % proxy.server_port
            env = {key: proxy_url for key in ('http_proxy', 'https_proxy', 'all_proxy',
                'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
            env.update(NO_PROXY='', no_proxy='')
            try:
                with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env), patch.object(network, 'event'), patch.object(network, 'RETRY_DELAYS', (0, 0)):
                    stage = Path(folder)
                    row = dict(repo='test/model', revision='test-revision', file='model.bin',
                               bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                    request = dict(row=row, stage=str(stage), source='official', proxy_route='inherited')
                    kwargs = dict(incomplete_path=stage/'unused', destination_path=stage/'model.bin',
                        url_to_download='http://127.0.0.1:%d/model' % server.server_port,
                        headers={}, expected_size=len(payload), filename='model.bin', force_download=False,
                        etag='test', xet_file_data=None)
                    with self.assertRaises(network.DownloadError):
                        hf_download(request, SimpleNamespace(), lambda *args: None, **kwargs)
                    self.assertEqual(hits, [])
                    self.assertFalse((stage/'model.bin').exists())
                    request['proxy_route'] = 'direct'
                    hf_download(request, SimpleNamespace(), lambda *args: None, **kwargs)
                    self.assertEqual((stage/'model.bin').read_bytes(), payload)
                    self.assertEqual(hits, ['/model'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        finally:
            proxy.shutdown()
            proxy.server_close()
            proxy_thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
