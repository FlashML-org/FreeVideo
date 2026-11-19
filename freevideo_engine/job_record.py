"""Where a generation ends, write its line in the finished-job record (jobs_journal.py).

Three things run generations outside the Agent's job store, and each ends here once: a video the creation panel
queued in ComfyUI (the FreeVideo node, or the multi-GPU front when the node runs in a worker), a long video's chain,
and a picture. The Agent's own jobs keep their own durable records and are not written twice; a long video the Agent
asked for is still recorded once, as its chain.

Only ids, short codes, times and a saved video's library id are passed on, and jobs_journal drops anything else.
Nothing here ever raises: a record that cannot be written never touches the job.
"""
import functools
import logging
import re
import time
from pathlib import Path, PurePosixPath

_LOG = logging.getLogger(__name__)
LIBRARY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


def output_root():
    """The output folder the /freevideo/jobs route reads the record from, or None outside ComfyUI."""
    try:
        import folder_paths
        return Path(folder_paths.get_output_directory())
    except Exception:
        return None


def library_id(relative):
    """'YYYY-MM-DD/<run>' for a saved video under FreeVideo/<day>/<run>/..., else None."""
    try:
        parts = PurePosixPath(str(relative).replace('\\', '/')).parts
    except (TypeError, ValueError):
        return None
    if len(parts) >= 3 and parts[0] == 'FreeVideo' and LIBRARY.fullmatch(parts[1]):
        return parts[1] + '/' + parts[2]
    return None


def ended(identity, kind, status, *, origin='page', created_at=None, started_at=None, result=None, failure=None,
          root=None):
    """Record that a job ended. status: done, failed, cancelled or interrupted."""
    try:
        root = root if root is not None else output_root()
        if root is None or not identity:
            return
        from . import jobs_journal
        jobs_journal.append(dict(id=str(identity), kind=kind, origin=origin, status=status, created_at=created_at,
                                 started_at=started_at, finished_at=time.time(), result=result, failure=failure),
                            root=Path(root))
    except Exception:
        _LOG.warning('A finished job could not be recorded.', exc_info=True)


def failure_code(error):
    """A short code for an exception: its class name, never its message (which may hold a prompt or a path)."""
    name = type(error).__name__
    return name if re.fullmatch(r'[A-Za-z0-9_]{1,64}', name) else 'error'


def interrupted(error):
    """ComfyUI's stop button raises this; it is a cancellation, not a failure."""
    return type(error).__name__ in ('InterruptProcessingException', 'KeyboardInterrupt')


def _summary_video(output):
    """The saved video's path from the node's result, read defensively."""
    try:
        ui = getattr(output, 'ui', None) or {}
        summary = (ui.get('freevideo_summary') or [None])[0] or {}
        return summary.get('video')
    except (AttributeError, TypeError, IndexError):
        return None


def video_node(execute):
    """Wrap the FreeVideo node's execute: one record per creation-panel video, written by the server process.

    Not written: a part of a long video (its chain is recorded once), and a run in a multi-GPU worker (the front in
    the control process records it)."""
    @functools.wraps(execute)
    def run(cls, *args, **kwargs):
        context = _video_start(cls)
        try:
            output = execute(cls, *args, **kwargs)
        except BaseException as error:
            if context is not None:
                ended(context['id'], 'video', 'cancelled' if interrupted(error) else 'failed', origin=context['origin'],
                      started_at=context['started_at'], failure=None if interrupted(error) else failure_code(error))
            raise
        if context is not None:
            ended(context['id'], 'video', 'done', origin=context['origin'], started_at=context['started_at'],
                  result=library_id(_summary_video(output)))
        return output
    return run


def _video_start(cls):
    try:
        from .comfy_environment import executor_device
        if executor_device():
            return None
        from server import PromptServer
        server = PromptServer.instance
        prompt_id = getattr(server, 'last_prompt_id', None)
        if not prompt_id:
            return None
        from .comfy_chain import block_context
        if block_context(server, cls.hidden.unique_id) is not None:
            return None
        agent = str(getattr(server, 'client_id', '') or '').startswith('agent:')
        return dict(id=str(prompt_id), origin='agent' if agent else 'page', started_at=time.time())
    except Exception:
        return None


CHAIN_STATUS = dict(done='done', failed='failed', cancelled='cancelled', interrupted='interrupted')


def chain(record, store_dir):
    """A chain that reached its end. Its parts are not recorded on their own."""
    status = CHAIN_STATUS.get(record.get('state'))
    if status is None or record.get('output_state') == 'joining' or status == 'done' and record.get('output_state') == 'pending':
        return
    if status == 'done' and record.get('output_state') == 'failed':
        status = 'failed'
    client = str(record.get('client_id') or '')
    try:
        root = Path(store_dir).parents[1]   # <output>/FreeVideo/.chains
    except IndexError:
        return
    created = record.get('created')
    ended(record.get('id'), 'chain', status, origin='agent' if client.startswith('agent:') else 'page',
          created_at=created if isinstance(created, (int, float)) else None,
          result=library_id(record.get('output')) if status == 'done' else None,
          failure='join_failed' if status == 'failed' and record.get('output_error') else None,
          root=root)


IMAGE_STATUS = dict(complete='done', failed='failed', cancelled='cancelled')


def image(key, state, started_at=None, root=None):
    """A picture job that ended: complete, failed or cancelled."""
    status = IMAGE_STATUS.get((state or {}).get('phase'))
    if status is None:
        return
    error = state.get('error')
    code = error if isinstance(error, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', error) else 'error'
    ended(key, 'image', status, started_at=started_at, failure=code if status == 'failed' else None, root=root)
