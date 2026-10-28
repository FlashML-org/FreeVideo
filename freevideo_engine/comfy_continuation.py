"""ComfyUI continuation paths, capabilities and standalone audio export helpers."""
from pathlib import Path, PurePosixPath, PureWindowsPath

from .continuation import (INVALID_FILE, INVALID_FRAMES, LEAD_IN_SAMPLES,
                           LEAD_IN_FADE, LEAD_IN_RATE)


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
    if not isinstance(continue_from, str):
        raise ValueError(INVALID_FILE)
    previous = None
    if continue_from:
        import folder_paths
        previous = resolve_continue_from(continue_from, folder_paths.get_output_directory(),
                                         folder_paths.get_input_directory())
    # Ordinary requests retain the existing bridge call and duration behavior.
    return dict(continue_from=previous, frames=count) if previous or count else {}


def continuation_notice(path):
    """Return a user message when `path` has no saved continuation record, else None.

    A cheap presence check run before the node stops background work or touches
    the GPU, so an unusable source video becomes a notice instead of an
    execution error. A record that exists but is damaged is still reported by
    the bridge's full check, exactly as before.
    """
    if not path:
        return None
    try:
        from av import FFmpegError
        from .comfy_bridge import installation_root
        from .continuation import missing_message
        from .continuation_store import _path, video_id
        directory = installation_root() / 'continuations'
    except (OSError, ValueError, KeyError, ImportError):
        return None  # Installation problems keep their existing error path.
    try:
        if _path(video_id(path), directory).is_file():
            return None
    except FFmpegError:
        pass  # Unreadable media cannot select a record.
    except (OSError, ValueError):
        return None  # Keep the bridge's existing message for other file problems.
    return missing_message(path)


def audio_lead_in(video, previous=None):
    """Read a new segment's seam audio, optionally checking its predecessor.

    Comfy paths follow continue_from's input/output root rules. Without Comfy,
    use absolute MP4 paths and the engine's selected FREEVIDEO_HOME. Missing,
    old or changed media/records return None.

    Export may use this only when the previous block's end and this block's
    start are untrimmed and the blocks are adjacent on the track. Apply before
    adjusting volume or resampling.
    """
    try:
        import numpy as np
        from av import FFmpegError
        from .continuation_store import load, video_id
    except ImportError:
        return None
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
        path = resolve(video)
        if path is None:
            return None
        value = load(path, directory=directory)
        if value is None or 'audio_lead_in' not in value:
            return None
        if previous is not None:
            source = resolve(previous)
            if source is None or value['metadata'].get('source_id') != video_id(source):
                return None
        return dict(samples=value['audio_lead_in'].astype(np.float32), before=LEAD_IN_SAMPLES,
                    fade=LEAD_IN_FADE, sample_rate=LEAD_IN_RATE)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError,
            ImportError, OverflowError, FFmpegError):
        return None


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
