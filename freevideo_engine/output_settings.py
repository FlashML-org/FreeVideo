"""Whether a finished video is also saved as a 10-bit copy (see video_10bit.py). Off unless the user turns it on."""
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
    """{'ten_bit': bool}; anything unreadable is off."""
    try:
        value = json.loads((Path(root) / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(ten_bit=False)
    return dict(ten_bit=isinstance(value, dict) and value.get('ten_bit') is True)


def write(root, ten_bit):
    if not isinstance(ten_bit, bool):
        raise ValueError('The 10-bit copy is switched on or off')
    if ten_bit and not available():
        raise ValueError('This installation cannot write 10-bit video')
    result = dict(ten_bit=ten_bit)
    save(Path(root) / FILE, result)
    return result


def command_option(root):
    """['--ten-bit', codec] when the copy is on and can be written, else []."""
    return ['--ten-bit', CODEC] if read(root)['ten_bit'] and available() else []
