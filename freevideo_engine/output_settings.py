"""Output choices: whether a finished video is also saved as a 10-bit copy (see video_10bit.py; off unless the user turns it
on) and whether a finished picture is also copied into the output folder (on unless the user turns it off)."""
import json
from pathlib import Path

from .monitoring import save

FILE = 'output-settings.json'
# One format for the first batch; chosen from playback tests on Windows (H.264 High 10 or HEVC Main 10).
CODEC = 'h264'


def available():
    """True when this installation's packaged encoder can write the 10-bit format."""
    try:
        import av
        from .video_10bit import CODECS
        return 'yuv420p10le' in {f.name for f in av.codec.Codec(CODECS[CODEC], 'w').video_formats or ()}
    except Exception:
        return False


def read(root):
    """{'ten_bit': bool, 'image_copy': bool}; a 10-bit copy that cannot be read is off, a picture copy is on."""
    try:
        value = json.loads((Path(root) / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(ten_bit=False, image_copy=True)
    value = value if isinstance(value, dict) else {}
    return dict(ten_bit=value.get('ten_bit') is True, image_copy=value.get('image_copy') is not False)


def write(root, ten_bit=None, image_copy=None):
    """Change the choices given (None leaves one as it is) and return both."""
    for name, value in (('The 10-bit copy', ten_bit), ('The picture copy in the output folder', image_copy)):
        if value is not None and not isinstance(value, bool):
            raise ValueError(name + ' is switched on or off')
    if ten_bit and not available():
        raise ValueError('This installation cannot write 10-bit video')
    result = read(root)
    if ten_bit is not None:
        result['ten_bit'] = ten_bit
    if image_copy is not None:
        result['image_copy'] = image_copy
    save(Path(root) / FILE, result)
    return result


def command_option(root):
    """['--ten-bit', codec] when the copy is on and can be written, else []."""
    return ['--ten-bit', CODEC] if read(root)['ten_bit'] and available() else []
