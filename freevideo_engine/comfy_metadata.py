"""Keep the ComfyUI workflow inside saved videos.

ComfyUI's own video nodes store the prompt and the workflow as MP4 metadata
(QuickTime keys, moov first). The frontend reads those two keys when a video is
dropped on the canvas, so a FreeVideo result restores its graph the same way.
"""
import json
import logging
from pathlib import Path
import uuid


def embed_comfy_metadata(path, *, prompt=None, workflow=None):
    """Rewrite the MP4 at `path` with the graph in its metadata, copying the
    streams unchanged. Returns True when the file was rewritten; on any failure
    the original video is left as it was."""
    tags = {key: json.dumps(value) for key, value in (('prompt', prompt), ('workflow', workflow)) if value is not None}
    if not tags:
        return False
    path = Path(path)
    temporary = path.with_name(f'{path.stem}.{uuid.uuid4().hex[:8]}.metadata{path.suffix}')
    try:
        import av
        # faststart puts moov, and with it the metadata, before the media data.
        with av.open(str(path)) as source, av.open(str(temporary), 'w', format='mp4',
                                                   options={'movflags': 'use_metadata_tags+faststart'}) as target:
            for key, value in tags.items():
                target.metadata[key] = value
            streams = {stream: _copy_stream(target, stream) for stream in source.streams
                       if stream.type in ('video', 'audio') and stream.codec_context is not None}
            if not streams:
                raise ValueError('The video has no audio or video stream to copy')
            for packet in source.demux(*streams):
                if packet.dts is not None:
                    packet.stream = streams[packet.stream]
                    target.mux(packet)
        temporary.replace(path)
        return True
    except Exception:
        logging.warning('FreeVideo could not store the workflow in %s; the video is unchanged.', path, exc_info=True)
        temporary.unlink(missing_ok=True)
        return False


def _copy_stream(container, template):
    try:
        return container.add_stream_from_template(template=template, opaque=True)
    except (AttributeError, TypeError):  # PyAV releases without add_stream_from_template(opaque=...)
        return container.add_stream(template=template)
