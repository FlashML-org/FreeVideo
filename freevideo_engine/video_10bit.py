"""Optional 10-bit picture output, written from the decoder's own float frames.

The ordinary 8-bit file is unchanged. When 10-bit output is requested, the
decoded frames are also kept as 16-bit RGB (value * 65535, rounded once) and a
second file is encoded from them as 10-bit 4:2:0. Nothing here widens an 8-bit
picture: the 8-bit plane is never an input.
"""
from pathlib import Path

MAXIMUM = 65535
CODECS = {'h264': 'libx264', 'hevc': 'libx265'}
# CRF 18 rather than the 8-bit file's 23: the file exists for grading and
# gradients, so it is not tuned for size.
CRF = '18'


def open_store(path, count, height, width):
    """A writable 16-bit RGB frame store (memory-mapped, so RAM stays bounded)."""
    import numpy as np
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return np.lib.format.open_memmap(path, mode='w+', dtype=np.uint16, shape=(count, height, width, 3))


def discard_store(mapping, path):
    """Close the frame store and delete its file (Windows cannot delete a mapped file)."""
    import gc
    gc.collect()
    try:
        if mapping is not None:
            mapping.flush() if getattr(mapping, 'mode', 'r') != 'r' else None
            mapping._mmap.close()
    except (BufferError, ValueError, OSError):
        pass
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def to_rgb16(chunk):
    """Float RGB in [0, 1], shape (frames, height, width, 3) -> uint16 numpy."""
    import torch
    return (chunk.clamp(0, 1) * MAXIMUM).round().to(torch.int32).numpy().astype('uint16')


def fit_frames16(frames, width, height, chunk=16):
    """The same centre crop and area reduction as the 8-bit fit, on 16-bit frames."""
    from .output_fit import fit_frames
    return fit_frames(frames, width, height, chunk=chunk, dtype='uint16')


def write(frames, output_path, *, fps, codec='h264', audio=None, audio_sample_rate=None):
    """Encode (frames, height, width, 3) uint16 RGB as 10-bit; audio as in the 8-bit file."""
    import numpy as np
    from diffusers.utils.export_utils import _import_av, _prepare_audio_stream, _write_audio
    if codec not in CODECS:
        raise ValueError('Unknown 10-bit codec')
    if audio is not None and audio_sample_rate is None:
        raise ValueError('audio_sample_rate is required when audio is provided')
    av = _import_av()
    count, height, width = frames.shape[0], frames.shape[1], frames.shape[2]
    if count < 1 or frames.dtype != np.uint16 or frames.shape[3] != 3:
        raise ValueError('10-bit output needs 16-bit RGB frames')
    container = av.open(str(output_path), mode='w', format='mp4')
    try:
        stream = container.add_stream(CODECS[codec], rate=int(fps))
        stream.width, stream.height, stream.pix_fmt = width, height, 'yuv420p10le'
        stream.options = {'crf': CRF}
        if codec == 'hevc':
            # Without this tag Safari and some MP4 players do not recognise the stream.
            stream.codec_context.codec_tag = 'hvc1'
        audio_stream = _prepare_audio_stream(container, audio_sample_rate) if audio is not None else None
        for index in range(count):
            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(frames[index]), format='rgb48le')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if audio is not None:
            _write_audio(container, audio_stream, audio, audio_sample_rate, av)
    finally:
        container.close()
