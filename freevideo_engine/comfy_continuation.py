"""ComfyUI continuation paths, capabilities and standalone audio export helpers."""
from pathlib import Path, PurePosixPath, PureWindowsPath

from .continuation import (INVALID_FILE, INVALID_FRAMES, LEAD_IN_SAMPLES,
                           LEAD_IN_FADE, LEAD_IN_RATE)


_EMPTY_TEXT = frozenset(('none', 'null', 'undefined', 'nan'))


def optional_text(value, default=''):
    """Return an optional text input, or default when it was not given.

    A missing optional widget can arrive as None, a blank string or the word a
    script or an older workflow wrote for "nothing" ("None", "null",
    "undefined", "NaN"). Other values are returned unchanged for the caller's
    own checks, so a real value is never reinterpreted.
    """
    if value is None or isinstance(value, str) and (
            not value.strip() or value.strip().lower() in _EMPTY_TEXT):
        return default
    return value


def node_frames(value):
    """The node's Total frames, or 0 (follow Duration) for anything that is not a count the model makes.

    Workflows saved by earlier versions and restored tabs can leave '', None, a word or another
    widget's number in this optional input. Such a value is not a request for that length, so it
    is read as "not given" instead of refusing the whole request.
    """
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return 0
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is not int or value < 39 or (value - 5) % 17:
        return 0
    return value


def part_steps(base_steps, refine_steps, step_cache):
    """(base_steps, refine_steps, step_cache, note) for a continued part or any part of a video made in parts.

    The engine continues a video only in two passes, and two passes run only with 8 first-pass steps
    (two_pass.TWO_PASS_BASE_STEPS). Other steps would let the first part finish and refuse the next,
    so every part samples 8 + 3, the same for the whole film. note is None when nothing changed.
    """
    from .two_pass import TWO_PASS_BASE_STEPS
    if base_steps == TWO_PASS_BASE_STEPS:
        return base_steps, refine_steps, step_cache, None
    return (TWO_PASS_BASE_STEPS, 3, 'none',
            'Parts of a long video and continued parts use %d + 3 steps with "Two-pass acceleration". '
            '%d first-pass steps apply only to a single video.' % (TWO_PASS_BASE_STEPS, base_steps))


def request_frames(value):
    """Translate the node's duration sentinel into the bridge's optional count."""
    if type(value) is not int or value != 0 and (value < 39 or (value - 5) % 17):
        raise ValueError(INVALID_FRAMES)
    return value or None


def resolve_continue_from(value, output_directory, input_directory):
    """Resolve an MP4 inside Comfy's roots, including output/FreeVideo creations.

    Relative history names always refer to output. An absolute input name is
    also accepted. Check both the original location and the resolved location
    so a link cannot grant access outside its allowed root.
    """
    if not isinstance(value, str) or len(value) > 4096 or '\x00' in value:
        raise ValueError(INVALID_FILE)
    if not value:
        return None
    path = Path(value)
    windows = PureWindowsPath(value)
    if ('..' in PurePosixPath(value).parts or '..' in windows.parts
            or windows.drive and not path.is_absolute()
            or '\\' in value and path.anchor != windows.anchor
            or any(':' in part for part in path.parts if part != path.anchor)
            or path.suffix.lower() != '.mp4'):
        raise ValueError(INVALID_FILE)
    roots = tuple(Path(root).resolve() for root in (output_directory, input_directory))
    candidate = path if path.is_absolute() else roots[0] / path
    allowed = [root for root in roots if candidate.is_relative_to(root)]
    if not allowed:
        raise ValueError(INVALID_FILE)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(INVALID_FILE) from None
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ValueError(INVALID_FILE)
    if not resolved.is_file():
        raise ValueError(INVALID_FILE)
    return str(resolved)


def request_options(continue_from='', frames=0):
    count = request_frames(frames)
    continue_from = optional_text(continue_from)
    if not isinstance(continue_from, str):
        raise ValueError(INVALID_FILE)
    previous = None
    if continue_from:
        import folder_paths
        previous = resolve_continue_from(continue_from, folder_paths.get_output_directory(),
                                         folder_paths.get_input_directory())
    # Ordinary requests retain the existing bridge call and duration behavior.
    return dict(continue_from=previous, frames=count) if previous or count else {}


def audio_lead_in(video, previous=None):
    """Read a new segment's seam audio, optionally checking its predecessor.

    Comfy paths follow continue_from's input/output root rules. Without Comfy,
    use absolute MP4 paths and the engine's selected FREEVIDEO_HOME. Missing,
    old or changed media/records return None.

    Export may use this only when the previous block's end and this block's
    start are untrimmed and the blocks are adjacent on the track. Apply before
    adjusting volume or resampling.
    """
    return audio_lead_in_checked(video, previous)[0]


def _record_file_exists(path, directory):
    """Whether the record file of this video is on disk, however it reads. Only asked after a failed read."""
    try:
        from .continuation_store import _path, video_id
        return _path(video_id(path), directory).is_file()
    except Exception:
        return False


def audio_lead_in_checked(video, previous=None):
    """(the seam audio, None) as audio_lead_in reads it, or (None, why it could not be read): no_video,
    no_record, unreadable_record, old_record, source_changed or read_failed:<error type>. Never a path or a text."""
    try:
        import numpy as np
        from av import FFmpegError
        from .continuation_store import load, video_id
    except ImportError:
        return None, 'read_failed:ImportError'
    try:
        try:
            import folder_paths
        except ModuleNotFoundError as error:
            if error.name != 'folder_paths':
                raise
            from .continuation import validate_path
            resolve = validate_path
            directory = None
        else:
            from .comfy_bridge import installation_root
            directory = installation_root() / 'continuations'
            def resolve(value):
                return resolve_continue_from(str(value), folder_paths.get_output_directory(),
                                             folder_paths.get_input_directory())
        def located(name):
            # A video that is gone raises ValueError from the resolvers rather than returning None.
            try:
                return resolve(name)
            except ValueError:
                return None
        path = located(video)
        if path is None:
            return None, 'no_video'
        value = load(path, directory=directory)
        if value is None:
            # load() also answers None for a record file that is there but fails to read or check.
            return None, 'unreadable_record' if _record_file_exists(path, directory) else 'no_record'
        if 'audio_lead_in' not in value:
            return None, 'old_record'
        if previous is not None:
            source = located(previous)
            if source is None or value['metadata'].get('source_id') != video_id(source):
                return None, 'source_changed'
        return dict(samples=value['audio_lead_in'].astype(np.float32), before=LEAD_IN_SAMPLES,
                    fade=LEAD_IN_FADE, sample_rate=LEAD_IN_RATE), None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError,
            ImportError, OverflowError, FFmpegError) as error:
        return None, 'read_failed:' + type(error).__name__


def apply_audio_lead_in(previous_samples, samples, lead):
    """Repair an adjacent seam in place, returning False without edits if invalid.

    Both blocks must be float32 [n, 2] at 32 kHz. The previous block's end and
    this block's start must be untrimmed, with the blocks adjacent on the track.
    Call before adjusting volume or resampling. Correlated audio uses linear
    fades, with endpoints included, to avoid an equal-power gain bump.
    Call once per seam on the original decoded blocks: repeated calls compound
    both fades and are not idempotent when the sources differ.
    """
    import numpy as np
    if (not isinstance(lead, dict)
            or any(type(lead.get(key)) is not int or lead[key] != expected for key, expected in
                   (('before', LEAD_IN_SAMPLES), ('fade', LEAD_IN_FADE), ('sample_rate', LEAD_IN_RATE)))):
        return False
    context = lead.get('samples')
    if any(not isinstance(value, np.ndarray) or value.dtype != np.float32
           or value.ndim != 2 or value.shape[1] != 2 for value in (previous_samples, samples, context)):
        return False
    if (context.shape[0] != LEAD_IN_SAMPLES + LEAD_IN_FADE
            or len(previous_samples) < LEAD_IN_SAMPLES or len(samples) < LEAD_IN_FADE
            or not previous_samples.flags.writeable or not samples.flags.writeable
            or np.shares_memory(previous_samples, samples)):
        return False
    # Distinct logical output samples must own distinct storage. Broadcast or
    # overlapping channel views could otherwise alter samples outside the seam.
    if any(abs(value.strides[0]) < value.itemsize
           or any(stride % value.itemsize for stride in value.strides)
           or np.shares_memory(value[:, 0], value[:, 1])
           for value in (previous_samples, samples)):
        return False
    old_tail = previous_samples[-LEAD_IN_SAMPLES:]
    if (not np.isfinite(context).all() or not np.isfinite(previous_samples).all()
            or not np.isfinite(samples).all()):
        return False
    weight = np.linspace(0., 1., LEAD_IN_FADE, dtype=np.float32)[:, None]
    # Compute both fades before the first write, even if context aliases a block.
    start = old_tail[:LEAD_IN_FADE] * (1. - weight) + context[:LEAD_IN_FADE] * weight
    end = context[LEAD_IN_SAMPLES:] * (1. - weight) + samples[:LEAD_IN_FADE] * weight
    old_tail[:] = context[:LEAD_IN_SAMPLES]
    old_tail[:LEAD_IN_FADE] = start
    samples[:LEAD_IN_FADE] = end
    return True


def continuation_supported():
    """Return the engine's (supported, reason_code) for the capabilities route.

    Import lazily: loading the Comfy extension does not probe an engine backend.
    The engine implementation is supplied alongside this server integration.
    """
    from .continuation import supported
    return supported()
