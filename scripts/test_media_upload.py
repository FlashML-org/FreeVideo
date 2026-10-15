"""Exercise streamed upload failures with owned temporary input folders."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_engine.comfy_upload import receive


class Part:
    name = 'file'
    filename = 'clip.mp4'

    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read_chunk(self, *, size):
        value = next(self.chunks, b'')
        if isinstance(value, BaseException):
            raise value
        return value


class Reader:
    def __init__(self, chunks, extra=False):
        self.parts = iter([Part(chunks)] + ([Part([])] if extra else []))

    async def next(self):
        return next(self.parts, None)


class UploadCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_streams_remove_only_their_staging_folder(self):
        for chunks, extra, error in (
            ([b'first', OSError('owned interrupted stream')], False, OSError),
            ([b'first', asyncio.CancelledError()], False, asyncio.CancelledError),
            ([], False, ValueError),
            ([b'done'], True, ValueError),
            ([b'too large'], False, ValueError),
        ):
            with self.subTest(error=error.__name__, extra=extra), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                unrelated = root / 'freevideo' / 'existing-upload' / 'keep.mp4'
                unrelated.parent.mkdir(parents=True)
                unrelated.write_bytes(b'keep')
                with patch('freevideo_engine.comfy_upload.MAX_BYTES', 5), self.assertRaises(error):
                    await receive(Reader(chunks, extra), root)
                self.assertEqual(list((root / 'freevideo').iterdir()), [unrelated.parent])
                self.assertEqual(unrelated.read_bytes(), b'keep')

    async def test_success_retains_the_published_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = await receive(Reader([b'abc', b'def']), root)
            self.assertEqual(result['bytes'], 6)
            destination = root / result['file']
            self.assertEqual(destination.read_bytes(), b'abcdef')
            self.assertEqual(list(destination.parent.iterdir()), [destination])


if __name__ == '__main__':
    unittest.main()
