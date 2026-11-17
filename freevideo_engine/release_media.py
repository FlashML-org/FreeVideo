"""The optional picture or video shown above a version's release notes.

The update metadata names it in three fields: the file's address, a cover
picture and the file's SHA-256. Both files live beside the installers on the
download mirror. They never take part in deciding an update: a missing, slow or
altered file leaves the cover only, and without the cover nothing is shown.

Releases use an animated WebP: it moves in the app and in the launcher. A video (.mp4, .webm) plays in the app
only; the launcher shows its cover.
"""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.parse

from .long_paths import io_path

KINDS = {'.mp4': 'video', '.webm': 'video', '.gif': 'animated', '.webp': 'animated',
         '.png': 'image', '.jpg': 'image', '.jpeg': 'image'}
COVERS = ('.webp', '.png', '.jpg', '.jpeg')
TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.gif': 'image/gif', '.webp': 'image/webp',
         '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
MAX_ADDRESS = 512
MAX_BYTES = dict(media=48 * 2**20, poster=4 * 2**20)
TRANSFER_SECONDS = dict(media=180, poster=30)
# After a failed transfer the next attempt waits; an altered file waits longer.
RETRY_SECONDS, MISMATCH_RETRY_SECONDS = 60, 3600
KEEP_FILES = 8
PARTS = ('media', 'poster')


def suffix(url):
    return os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()


def _address(url, allowed):
    """A download address of this project with one of the allowed extensions."""
    from .launcher_update import MIRROR_BASE, WEB
    if (not isinstance(url, str) or not 0 < len(url) <= MAX_ADDRESS
            or any(ord(c) <= 32 or c in '"\'<>\\`' for c in url)):
        return False
    parts = urllib.parse.urlsplit(url)
    if parts.query or parts.fragment or '/../' in parts.path or parts.path.endswith('/..'):
        return False
    return (url.startswith((MIRROR_BASE + '/', WEB + '/releases/download/'))
            and suffix(url) in allowed)


def entry(value):
    """The validated media fields, or None; other keys are dropped."""
    if not isinstance(value, dict):
        return None
    url, poster, digest = value.get('url'), value.get('poster'), value.get('sha256')
    if not (_address(url, KINDS) and _address(poster, COVERS) and isinstance(digest, str)
            and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest.lower())):
        return None
    return dict(url=url, poster=poster, sha256=digest.lower())


def problem(value):
    """Why a media block is rejected; '' when it is usable."""
    if entry(value):
        return ''
    if not isinstance(value, dict):
        return 'media must be an object with url, poster and sha256'
    if not _address(value.get('url'), KINDS):
        return 'media url must be a release download of .mp4, .webm, .gif, .webp, .png or .jpg'
    if not _address(value.get('poster'), COVERS):
        return 'media poster must be a release download of .webp, .png or .jpg'
    return 'media sha256 must be 64 hexadecimal characters'


def kind(value):
    """'video', 'animated' or 'image', by the file's extension."""
    return KINDS.get(suffix(value['url']), 'image')


class Store:
    """Verified copies of media files in one folder, fetched once per file.

    A file is written under its final name only after its size and, for the
    media file, its SHA-256 matched, so a file present in the folder is usable.
    """
    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = threading.Lock()
        self.running = {}
        self.failed = {}

    def path(self, value, part):
        if part == 'media':
            return self.folder / (value['sha256'] + suffix(value['url']))
        return self.folder / ('cover-' + hashlib.sha256(value['poster'].encode('utf-8')).hexdigest()[:32]
                              + suffix(value['poster']))

    def ready(self, value, part):
        """The local file when it is already here, else None."""
        path = self.path(value, part)
        try:
            return path if io_path(path).stat().st_size > 0 else None
        except OSError:
            return None

    def state(self, value, part):
        """'ready', 'loading', 'failed' or 'idle'."""
        if self.ready(value, part):
            return 'ready'
        key = self.path(value, part).name
        with self.lock:
            if key in self.running:
                return 'loading'
            return 'failed' if key in self.failed else 'idle'

    def start(self, value, part):
        """Fetch in the background unless the file is here, on its way or failed a moment ago.

        Returns an event that is set when no fetch is running any more."""
        done = threading.Event()
        if self.ready(value, part):
            done.set()
            return done
        key = self.path(value, part).name
        with self.lock:
            if key in self.running:
                return self.running[key]
            failed = self.failed.get(key)
            if failed and time.monotonic() < failed:
                done.set()
                return done
            self.running[key] = done
        threading.Thread(target=self._fetch, args=(value, part, key, done), daemon=True,
                         name='FreeVideo-release-media').start()
        return done

    def get(self, value, part, seconds):
        """The local file, fetching it if needed and waiting at most `seconds`; None meanwhile or on failure."""
        self.start(value, part).wait(seconds)
        return self.ready(value, part)

    def _fetch(self, value, part, key, done):
        retry = RETRY_SECONDS
        try:
            data = download(value['url'] if part == 'media' else value['poster'], MAX_BYTES[part], TRANSFER_SECONDS[part])
            if part == 'media' and hashlib.sha256(data).hexdigest() != value['sha256']:
                retry = MISMATCH_RETRY_SECONDS
                raise ValueError('Release media does not match its SHA-256')
            self._write(self.path(value, part), data)
            with self.lock:
                self.failed.pop(key, None)
        except Exception:  # Any failure leaves the cover, or nothing; it never reaches the notes.
            with self.lock:
                self.failed[key] = time.monotonic() + retry
        finally:
            with self.lock:
                self.running.pop(key, None)
            done.set()

    def _write(self, path, data):
        folder = io_path(self.folder)
        folder.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=folder, prefix='.part-')
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
            os.replace(temporary, io_path(path))
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        # Earlier versions' files go; the newest few stay for the versions on screen.
        try:
            files = sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith('.part-')),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[KEEP_FILES:]:
                old.unlink()
        except OSError:
            pass


def view(store, release, *, fetch):
    """What a launcher window draws for a release: kind, and the cover and file as file URLs ('' while absent).

    The cover is fetched whenever the release has media; the file itself only when `fetch` (an update on offer, or a
    window that shows the notes). None when the release has no media."""
    value = entry((release or {}).get('media'))
    if not value:
        return None
    poster, media = store.ready(value, 'poster'), store.ready(value, 'media')
    if poster is None:
        store.start(value, 'poster')
    if media is None and fetch:
        store.start(value, 'media')
    return dict(kind=kind(value), poster=poster.resolve().as_uri() if poster else '',
                source=media.resolve().as_uri() if media else '', key=value['sha256'][:16])


def download(url, limit, seconds):
    """The file's bytes through the update downloader (approved hosts only, size-bounded)."""
    from . import launcher_update as update
    with update.open_download(url, '', binary=True) as response:
        return b''.join(update._bytes(response, limit, seconds=seconds))
