"""Keep the ComfyUI workflow inside saved videos.

ComfyUI's own video nodes store the prompt and the workflow as MP4 metadata
(QuickTime keys, moov first). The frontend reads those two keys when a video is
dropped on the canvas, so a FreeVideo result restores its graph the same way.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

BACKFILL_MARKER = '.workflow-backfill.json'
BACKUP_NAME = 'video.before-workflow.mp4'


def embed_comfy_metadata(path, *, prompt=None, workflow=None, backup=None):
    """Rewrite the MP4 at `path` with the graph in its metadata, copying the
    streams. The new file replaces the original only after every audio and video
    packet matches the original byte for byte and the graph reads back; with
    `backup`, the original is first kept under that name. Returns True when the
    file was rewritten; otherwise the video is left exactly as it was."""
    tags = {key: json.dumps(value) for key, value in (('prompt', prompt), ('workflow', workflow)) if value is not None}
    if not tags:
        return False
    path = Path(path)
    backup = Path(backup) if backup is not None else None
    temporary = path.with_name(f'{path.stem}.{uuid.uuid4().hex[:8]}.metadata{path.suffix}')
    made_backup = False
    try:
        import av
        size = path.stat().st_size
        if backup is not None and backup.exists():
            logging.info('FreeVideo left %s unchanged: %s already exists.', path, backup.name)
            return False
        if shutil.disk_usage(path.parent).free < 2 * size + 512 * 1024 * 1024:
            logging.info('FreeVideo left %s unchanged: not enough free disk space.', path)
            return False
        # faststart puts moov, and with it the metadata, before the media data.
        with av.open(str(path)) as source, av.open(str(temporary), 'w', format='mp4',
                                                   options={'movflags': 'use_metadata_tags+faststart'}) as target:
            if not source.streams or any(stream.type not in ('video', 'audio') or stream.codec_context is None
                                         for stream in source.streams):
                raise ValueError('Only videos with nothing but audio and video streams are rewritten')
            for key, value in tags.items():
                target.metadata[key] = value
            streams = {stream: _copy_stream(target, stream) for stream in source.streams}
            for packet in source.demux(*streams):
                if packet.dts is not None:
                    packet.stream = streams[packet.stream]
                    target.mux(packet)
        if _media(temporary) != _media(path):
            raise ValueError('The rewritten video does not match the original')
        with av.open(str(temporary)) as check:
            if any(check.metadata.get(key) != value for key, value in tags.items()):
                raise ValueError('The workflow did not read back')
        if backup is not None:
            made_backup = True
            try:
                os.link(path, backup)  # no copy: the original's data stays with the backup name
            except OSError:
                shutil.copy2(path, backup)
            if backup.stat().st_size != size:
                raise ValueError('The backup is incomplete')
        os.replace(temporary, path)
        return True
    except Exception:
        logging.warning('FreeVideo could not store the workflow in %s; the video is unchanged.', path, exc_info=True)
        temporary.unlink(missing_ok=True)
        if made_backup and backup.exists():
            backup.unlink()  # the original is still in place, so this copy is not needed
        return False


def _media(path):
    """Each stream's codec and shape, with the count and a digest of its packet bytes."""
    import av
    with av.open(str(path)) as container:
        streams = list(container.streams)
        layout = [(stream.type, stream.codec_context.name, getattr(stream.codec_context, 'width', None),
                   getattr(stream.codec_context, 'height', None), getattr(stream.codec_context, 'sample_rate', None))
                  for stream in streams]
        digests = {stream.index: hashlib.sha256() for stream in streams}
        counts = dict.fromkeys(digests, 0)
        for packet in container.demux():
            if packet.size:
                digests[packet.stream.index].update(bytes(packet))
                counts[packet.stream.index] += 1
    return layout, [(counts[index], digests[index].hexdigest()) for index in sorted(digests)]


def _copy_stream(container, template):
    try:
        return container.add_stream_from_template(template=template, opaque=True)
    except (AttributeError, TypeError):  # PyAV releases without add_stream_from_template(opaque=...)
        return container.add_stream(template=template)


def saved_workflow(path):
    """The canvas workflow in a run's workflow.json, or None. Requests queued
    without the canvas (the API, scripts) leave an empty one."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if isinstance(value, dict) and 'nodes' not in value and isinstance(value.get('workflow'), dict):
        value = value['workflow']
    return value if isinstance(value, dict) and value.get('nodes') else None


def has_workflow(path):
    import av
    with av.open(str(path)) as reader:
        return bool(reader.metadata.get('workflow'))


def backfill(output_directory, *, stop=None):
    """Store the workflow in videos saved before it was embedded, from the
    workflow.json kept beside each one. Each original is kept next to its video
    as video.before-workflow.mp4. Runs once: a marker records a pass with nothing
    left to do. Returns the counts, or None when there was nothing to run."""
    root = Path(output_directory).resolve() / 'FreeVideo'
    marker = root / BACKFILL_MARKER
    if marker.is_file() or not root.is_dir():
        return None
    from .result_cache import ResultCache, _file, save
    cache = ResultCache(output_directory)
    rows = {}
    for row in cache.index.glob('*.json'):
        try:
            rows.setdefault(json.loads(row.read_text(encoding='utf-8'))['id'], []).append(row)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    counts = dict(embedded=0, skipped=0, pending=0, failed=0)
    for video in sorted(root.glob('*/*/video.mp4')):
        if stop is not None and stop.is_set():
            return counts
        run = video.parent
        try:
            state = json.loads((run / 'comfy-request.json').read_text(encoding='utf-8')).get('status')
        except (OSError, ValueError, AttributeError):
            state = None
        if state in ('starting', 'running'):
            counts['pending'] += 1  # Its own request embeds the workflow when it completes.
            continue
        try:
            workflow = saved_workflow(run / 'workflow.json')
            if workflow is None or has_workflow(video):
                counts['skipped'] += 1
                continue
        except Exception:
            counts['failed'] += 1
            continue
        if (run / BACKUP_NAME).exists():
            counts['skipped'] += 1  # An earlier attempt left its backup; do not touch this video again.
            continue
        if not embed_comfy_metadata(video, workflow=workflow, backup=run / BACKUP_NAME):
            counts['failed'] += 1
            continue
        counts['embedded'] += 1
        # Keep saved results reusable: the index records each output's size and hash.
        for row in rows.get(run.relative_to(root).as_posix(), []):
            try:
                value = json.loads(row.read_text(encoding='utf-8'))
                value['files']['.mp4'] = dict(bytes=video.stat().st_size, sha256=_file(video, content=True))
                save(row, value)
            except (OSError, ValueError, KeyError, TypeError):
                pass
    if not counts['pending'] and not counts['failed']:
        save(marker, dict(schema=1, completed_at=time.time(), embedded=counts['embedded']))
    return counts


DISMISSED = '.workflow-backfill-dismissed.json'
_job = {'state': 'idle', 'result': None}
_job_lock = threading.Lock()


def candidates(output_directory):
    """Earlier videos the backfill would rewrite, and their total size (the backups take as much)."""
    root = Path(output_directory).resolve() / 'FreeVideo'
    found = dict(count=0, bytes=0)
    if not root.is_dir() or (root / BACKFILL_MARKER).is_file():
        return found
    for video in root.glob('*/*/video.mp4'):
        run = video.parent
        try:
            state = json.loads((run / 'comfy-request.json').read_text(encoding='utf-8')).get('status')
        except (OSError, ValueError, AttributeError):
            state = None
        try:
            if (state in ('starting', 'running') or (run / BACKUP_NAME).exists()
                    or saved_workflow(run / 'workflow.json') is None or has_workflow(video)):
                continue
            found['count'] += 1
            found['bytes'] += video.stat().st_size
        except Exception:
            continue
    return found


def register():
    """Earlier videos get their workflow only when the user confirms it in Creations."""
    import asyncio
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    from .monitoring import save
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_workflow_backfill', False):
        return
    server._freevideo_workflow_backfill = True
    try:
        from comfy.cli_args import args
        disabled = bool(args.disable_metadata)
    except (ImportError, AttributeError):
        disabled = False

    def run(output):
        try:
            result = backfill(output) or dict(embedded=0, skipped=0, pending=0, failed=0)
            if result['embedded']:
                logging.info('FreeVideo stored the workflow in %d earlier videos.', result['embedded'])
        except Exception:
            logging.warning('FreeVideo could not store the workflow in earlier videos.', exc_info=True)
            result = dict(embedded=0, skipped=0, pending=0, failed=1)
        with _job_lock:
            _job.update(state='done', result=result)

    @server.routes.get('/freevideo/workflow-backfill')
    async def status(request):
        output = folder_paths.get_output_directory()
        dismissed = (Path(output).resolve() / 'FreeVideo' / DISMISSED).is_file()
        found = dict(count=0, bytes=0)
        if not disabled and not dismissed and _job['state'] != 'running':
            found = await asyncio.to_thread(candidates, output)
        with _job_lock:
            job = dict(_job)
        return web.json_response(dict(found, disabled=disabled, dismissed=dismissed, **job), headers={'Cache-Control': 'no-store'})

    @server.routes.post('/freevideo/workflow-backfill')
    async def act(request):
        try:
            action = (await request.json()).get('action')
        except (ValueError, AttributeError):
            action = None
        output = folder_paths.get_output_directory()
        if action == 'dismiss':
            save(Path(output).resolve() / 'FreeVideo' / DISMISSED, dict(schema=1, dismissed_at=time.time()))
            return web.json_response(dict(dismissed=True))
        if action != 'run' or disabled:
            raise web.HTTPBadRequest(text='Unknown action')
        with _job_lock:
            if _job['state'] != 'running':
                _job.update(state='running', result=None)
                threading.Thread(target=run, args=(output,), name='freevideo-workflow-backfill', daemon=True).start()
            return web.json_response(dict(state=_job['state']))
