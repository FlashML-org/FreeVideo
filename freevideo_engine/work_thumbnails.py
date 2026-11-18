"""Small copies of saved pictures for grids and lists, made once on this computer and kept beside the pictures.

A grid shows these; the picture itself loads only where it is shown large, so a page opened over a slow link moves a
few hundred kilobytes for a screen of works instead of the whole pictures. A small copy lives in the pictures' own
folder (``freevideo-image/.thumbnails``) under the picture's name.

Deleted is gone for good: the place that deletes a work calls ``release(owner)`` with the owner name the private store
uses (``image:<file name>``), together with its release of the work's inputs, and the small copy goes at once. A
picture that leaves some other way (removed by hand, by a cleanup) takes its small copy with it the next time the
engine looks at the folder: every listing of the history, every request for it, and each start. Reads follow ComfyUI's
/view rules and are stricter: only a picture of the history by its own name (an older unnamed picture only when it is
a colour picture, never a mask), never a link or anything outside the folder.
"""
import io
import logging
import os
import re
import stat
import time
from pathlib import Path

# The history's own names (image_gen.service.HISTORY_NAME and LEGACY_NAME).
PICTURE = re.compile(r'image-\d{8}-\d{6}-\d{2}\.(?:png|jpg)|[0-9a-f]{32}\.png')
FOLDER = 'freevideo-image'
CACHE = '.thumbnails'
# The long edge of a small copy: about two grid columns' width on a dense display.
EDGE = 480
QUALITY = 80
# A picture larger than this is not decoded for a small copy (the grid then shows the picture itself).
MOST_PIXELS = 64 * 1024 * 1024
_REPARSE_POINT = 0x400
_LIMIT = 2 * 1024 * 1024


def _plain(path, kind):
    """A regular file or folder of ours: never a link, junction or other reparse point."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT:
        raise ValueError('A thumbnail path is a link')
    if not (stat.S_ISDIR if kind == 'folder' else stat.S_ISREG)(info.st_mode):
        raise ValueError('A thumbnail path is not a %s' % kind)
    return info


def _folder(input_directory):
    root = Path(input_directory).resolve()
    folder = root / FOLDER
    _plain(folder, 'folder')
    if folder.resolve() != folder or folder.parent != root:
        raise ValueError('The pictures folder is outside the input folder')
    return folder


LEGACY = re.compile(r'[0-9a-f]{32}\.png')


def _colour_png(path):
    """An older unnamed picture is a work only when it is a colour PNG (its masks are grey), as the history decides."""
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)), 'rb') as stream:
        head = stream.read(26)
    return head[:8] == b'\x89PNG\r\n\x1a\n' and len(head) == 26 and head[25] in (2, 6)


def picture_path(input_directory, file):
    """The saved picture a history name stands for (``freevideo-image/<name>``) and its file facts."""
    leaf = file.rpartition('/')[2] if isinstance(file, str) else ''
    if not PICTURE.fullmatch(leaf) or file != FOLDER + '/' + leaf:
        raise ValueError('Invalid picture')
    path = _folder(input_directory) / leaf
    info = _plain(path, 'file')
    if path.resolve().parent != path.parent:
        raise ValueError('The picture is outside the pictures folder')
    if LEGACY.fullmatch(leaf) and not _colour_png(path):
        raise ValueError('Not a picture of the history')
    return path, info


def tag(info):
    """The validator of a small copy: the picture's size and time, so a replaced picture gets a new one."""
    return '"p%d-%x-%x"' % (EDGE, info.st_size, info.st_mtime_ns)


def _cache(folder, leaf, create=False):
    cache = folder / CACHE
    if create and not os.path.lexists(cache):
        try:
            cache.mkdir()
        except FileExistsError:
            pass
    _plain(cache, 'folder')
    return cache / (leaf + '.webp')


def _read(path):
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)), 'rb') as stream:
        return stream.read(_LIMIT + 1)


def _size(path):
    """The picture's own width and height, from its header only."""
    from PIL import Image
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def cached_picture(input_directory, file):
    """(bytes, tag, picture size) of a small copy already made for this picture, or None; no decoding here."""
    path, info = picture_path(input_directory, file)
    try:
        saved = _cache(path.parent, path.name)
        if _plain(saved, 'file').st_mtime_ns < info.st_mtime_ns:
            return None
        data = _read(saved)
    except (OSError, ValueError):
        return None
    if data[:4] != b'RIFF' or data[8:12] != b'WEBP' or len(data) > _LIMIT:
        return None
    return data, tag(info), _size(path)


def picture_thumbnail(input_directory, file):
    """(bytes, tag, picture size) of a picture's small copy (WebP, long edge 480 px), made on first use and kept."""
    found = cached_picture(input_directory, file)
    if found:
        return found
    path, info = picture_path(input_directory, file)
    from PIL import Image
    with Image.open(path) as image:
        size = image.size
        if image.width * image.height > MOST_PIXELS:
            raise ValueError('The picture is too large for a small copy')
        # A JPEG decodes straight at a reduced scale: a fraction of the work of the whole picture.
        image.draft('RGB', (EDGE * 2, EDGE * 2))
        image.load()
        keep_alpha = image.mode in ('RGBA', 'LA', 'PA') or 'transparency' in image.info
        small = image.convert('RGBA' if keep_alpha else 'RGB')
    small.thumbnail((EDGE, EDGE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    small.save(buffer, format='WEBP', quality=QUALITY, method=4)
    data = buffer.getvalue()
    try:
        saved = _cache(path.parent, path.name, create=True)
        temporary = saved.with_name('.%s.%s.partial' % (saved.name, os.urandom(4).hex()))
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
                                   | getattr(os, 'O_BINARY', 0), 0o600), 'wb') as stream:
                stream.write(data)
            os.replace(temporary, saved)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        # Deleted meanwhile: its small copy does not stay behind.
        if not os.path.lexists(path):
            forget_pictures(input_directory, [file])
    except (OSError, ValueError):
        pass  # A read-only folder still shows its pictures; the copy is made again next time.
    return data, tag(info), size


def release(owner_id, input_directory):
    """A work deleted for good: remove what this module made of it. ``owner_id`` is the private store's owner name,
    ``image:<file name>``; other kinds own nothing here (a video's thumbnail lives in its run folder, which goes with
    it). Returns how many files went."""
    kind, _, leaf = owner_id.partition(':') if isinstance(owner_id, str) else ('', '', '')
    return forget_pictures(input_directory, [FOLDER + '/' + leaf]) if kind == 'image' and PICTURE.fullmatch(leaf) else 0


def forget_pictures(input_directory, files):
    """Remove the small copies of pictures deleted for good."""
    try:
        cache = _folder(input_directory) / CACHE
        _plain(cache, 'folder')
    except (OSError, ValueError):
        return 0
    removed = 0
    for file in files:
        leaf = file.rpartition('/')[2] if isinstance(file, str) else ''
        if not PICTURE.fullmatch(leaf):
            continue
        try:
            os.unlink(cache / (leaf + '.webp'))
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            logging.warning('[FreeVideo] Could not remove the small copy of a deleted picture.', exc_info=True)
    return removed


def prune_pictures(input_directory, present=None):
    """Remove small copies whose picture is no longer in the folder (deleted some other way), and stray partial files.
    ``present``: the picture names a listing of the folder just found (saves looking at each one again)."""
    try:
        folder = _folder(input_directory)
        cache = folder / CACHE
        _plain(cache, 'folder')
        names = os.listdir(cache)
    except (OSError, ValueError):
        return 0
    removed = 0
    for name in names:
        leaf = name[:-len('.webp')] if name.endswith('.webp') else ''
        if present is not None:
            keep = PICTURE.fullmatch(leaf) and leaf in present
        else:
            keep = PICTURE.fullmatch(leaf) and os.path.isfile(folder / leaf) and not os.path.islink(folder / leaf)
        if keep:
            continue
        try:
            entry = cache / name
            # A copy being written right now is left to finish (it checks its picture once written).
            if name.endswith('.partial') and time.time() - os.lstat(entry).st_mtime < 60:
                continue
            if os.path.islink(entry) or os.path.isfile(entry):
                os.unlink(entry)
                removed += 1
        except OSError:
            pass
    return removed
