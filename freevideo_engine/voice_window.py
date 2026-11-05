"""How much of a voice reference's first seconds is speech.

The engine reads the first WINDOW seconds of a reference sound. A window with little speech, or one that opens with
music, gives a weak voice, so the caller can ask for another clip before spending a generation on it.
"""
from __future__ import annotations

WINDOW = 6.
MIN_SPEECH = 4.


def measure(spans, duration, window=WINDOW):
    """Speech seconds inside [0, window] from VAD spans, and when speech first starts (None without speech)."""
    end = min(window, duration)
    speech = sum(max(0., min(b, end) - max(a, 0.)) for a, b in spans)
    first = next((max(a, 0.) for a, b in spans if b > 0 and a < end), None)
    return round(speech, 2), None if first is None else round(first, 2), round(end, 2)


def check(spans, duration):
    """{'window', 'speech_seconds', 'first_speech', 'usable', 'reason'}; reason is None when usable."""
    speech, first, window = measure(spans, duration)
    need = min(MIN_SPEECH, window)
    # A clip that opens with music has the same effect as a sparse one: the window holds too little speech.
    reason = 'too_little_speech' if speech < need else None
    return dict(window=window, speech_seconds=speech, first_speech=first, usable=reason is None, reason=reason)


def inspect(path, duration, model_path, cache):
    """check() for a sound or video file, or None when the speech model is not installed or the file cannot be read."""
    from .speech import SpeechError, speech_spans
    try:
        spans = speech_spans(path, model_path, cache)
        return check(spans, duration)
    except SpeechError:
        return None
