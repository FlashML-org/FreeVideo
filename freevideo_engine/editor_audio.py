"""Pure timeline audio envelopes; volume is applied by the caller to base samples."""
import math
import re

from .editor_speed import changed, audio_changed, source_sample_start


def clip_fields(raw, kind):
    result = {}
    for name in ('fade_in', 'fade_out', 'crossfade'):
        if name in raw:
            if kind != 'audio' or type(raw[name]) is not int or not 0 <= raw[name] <= 120:
                raise ValueError('Invalid audio envelope')
            result[name] = raw[name]
    if 'detached_from' in raw:
        value = raw['detached_from']
        if (not isinstance(value, str) or not re.fullmatch(r'c[1-9]\d{0,8}', value)
                or value == raw['id'] or raw.get('link')):
            raise ValueError('Invalid detached audio')
        result['detached_from'] = value
    # A music piece's preset (prefilled when it is made again) and whether it is lowered under the first sound track.
    if 'music' in raw:
        value = raw['music']
        if (kind != 'audio' or not isinstance(value, dict) or set(value) != {'style', 'mood'}
                or not all(isinstance(value[k], str) and re.fullmatch(r'[a-z]{1,20}', value[k]) for k in ('style', 'mood'))):
            raise ValueError('Invalid music preset')
        result['music'] = {'style': value['style'], 'mood': value['mood']}
    if 'duck' in raw:
        if kind != 'audio' or type(raw['duck']) is not bool:
            raise ValueError('Invalid audio ducking')
        result['duck'] = raw['duck']
    return result


def track_fields(raw):
    result = {}
    for name in ('muted', 'solo', 'locked'):
        if name in raw:
            if type(raw[name]) is not bool:
                raise ValueError('Invalid audio track flag')
            result[name] = raw[name]
    return result


def track_audible(track, tracks):
    return not track.get('muted') and (not any(row.get('solo') for row in tracks) or bool(track.get('solo')))


def clip_audible(clip, track, tracks):
    return (track_audible(track, tracks) and not (clip.get('speed_audio') == 'mute' and changed(clip))
            and not any(row['kind'] == 'video' and row.get('muted')
                        and any(video['id'] == clip.get('link') for video in row['clips']) for row in tracks))


def _samples(frames, rate, fps):
    return math.floor(frames * rate / fps + .5)


def _source_start(clip, rate, fps):
    if audio_changed(clip):
        return math.floor(source_sample_start(clip) * rate / 48000 + .5)
    splice = clip.get('splice')
    return (math.floor((splice['audio_samples'] / 32000 + (clip['in'] - splice['frames']) / fps) * rate + .5)
            if splice else _samples(clip['in'], rate, fps))


def crossfade_reads(track, rate=48000, fps=24):
    """Raw extra read positions/counts at each contiguous audio junction.

    Changed speed or reverse forbids extension on that side. Odd sample counts
    place the extra sample after the junction. Source coordinates include splice.
    """
    result = []
    for left, right in zip(track['clips'], track['clips'][1:]):
        if (not left.get('source') or not right.get('source') or not right.get('crossfade')
                or left['start'] + left['frames'] != right['start']):
            continue
        count = _samples(min(right['crossfade'], 120, left['frames'], right['frames']), rate, fps)
        pre, post = count // 2, count - count // 2
        def extend(clip):
            return not changed(clip)
        tail = (min(post, max(0, _samples(left['length'], rate, fps) - _source_start(left, rate, fps)
                            - _samples(left['frames'], rate, fps))) if extend(left) else 0)
        head = min(pre, max(0, _source_start(right, rate, fps))) if extend(right) else 0
        result.append(dict(left=left['id'], right=right['id'], boundary=_samples(right['start'], rate, fps),
                           pre=pre, post=post, left_tail=tail, right_head=head,
                           left_read_start=_source_start(left, rate, fps) + _samples(left['frames'], rate, fps),
                           right_read_start=_source_start(right, rate, fps) - head))
    return result


def _ramp(position, length):
    import numpy as np
    return (position >= 0).astype(float) if length <= 1 else np.clip(position / (length - 1), 0, 1)


def fade_gain(clip, local, length, rate=48000, fps=24):
    import numpy as np
    local = np.asarray(local)
    gain = np.ones(local.shape, dtype=np.float64)
    if clip.get('fade_in'):
        gain *= _ramp(local, _samples(min(120, clip['frames'] // 2, clip['fade_in']), rate, fps))
    if clip.get('fade_out'):
        gain *= _ramp(length - 1 - local, _samples(min(120, clip['frames'] // 2, clip['fade_out']), rate, fps))
    return gain


def apply_track_audio(block, block_start, track, tracks, *, rate=48000, fps=24, extras=None):
    """Return a new planar C×N block, with the same shape and timeline origin.

    Base block already has volume. extras[right_id] contains raw planar
    left_tail/right_head arrays. Missing extras use a fade out/in at the cut.
    Work depends on block size; no decoding, file access or whole-edit buffer.
    """
    import numpy as np
    if type(block_start) is not int or block_start < 0 or not math.isfinite(rate) or rate <= 0 or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Invalid timeline audio coordinates')
    output = np.array(block, dtype=np.float64, copy=True)
    if output.ndim != 2:
        raise ValueError('Expected planar audio')
    count = output.shape[1]
    if not track_audible(track, tracks):
        output.fill(0)
        return output
    added = np.zeros_like(output)
    plans = []
    for raw in crossfade_reads(track, rate, fps):
        extra = (extras or {}).get(raw['right'], {})
        data = {}
        for name in ('left_tail', 'right_head'):
            value = np.asarray(extra.get(name, np.zeros((output.shape[0], 0))), dtype=np.float64)
            if value.ndim != 2 or value.shape[0] != output.shape[0]:
                raise ValueError('Extra read channel mismatch')
            data[name] = value
        plans.append(dict(raw, tail=min(raw['left_tail'], data['left_tail'].shape[1]),
                          head=min(raw['right_head'], data['right_head'].shape[1]), extra=data))
    for clip in track['clips']:
        start = _samples(clip['start'], rate, fps)
        stop = _samples(clip['start'] + clip['frames'], rate, fps)
        incoming = next((p for p in plans if p['right'] == clip['id']), None)
        outgoing = next((p for p in plans if p['left'] == clip['id']), None)
        lo = start - (incoming['head'] if incoming else 0)
        hi = stop + (outgoing['tail'] if outgoing else 0)
        audible = clip_audible(clip, track, tracks)
        def gain(positions):
            value = fade_gain(clip, positions - lo, hi - lo, rate, fps) if audible else np.zeros(positions.shape)
            for plan, right in ((incoming, True), (outgoing, False)):
                if plan:
                    if plan['head'] + plan['tail']:
                        a, b = plan['boundary'] - plan['head'], plan['boundary'] + plan['tail']
                        weight = _ramp(positions - a, b - a)
                        value *= weight if right else 1 - weight
                    else:
                        value *= (_ramp(positions - start, plan['post']) if right
                                  else _ramp(stop - 1 - positions, plan['pre']))
            return value
        a, b = max(block_start, start), min(block_start + count, stop)
        if b > a:
            output[:, a - block_start:b - block_start] *= gain(np.arange(a, b))[None, :]
        for plan, name, begin, size in ((incoming, 'right_head', lo, incoming['head'] if incoming else 0),
                                        (outgoing, 'left_tail', stop, outgoing['tail'] if outgoing else 0)):
            if not size:
                continue
            a, b = max(block_start, begin), min(block_start + count, begin + size)
            if b > a:
                data = plan['extra'][name]
                offset = data.shape[1] - size if name == 'right_head' else 0
                added[:, a - block_start:b - block_start] += (data[:, offset + a - begin:offset + b - begin]
                    * gain(np.arange(a, b))[None, :] * clip.get('volume', 1))
    return output + added


# Lower during speech: a clip with duck=True sits DUCK_DB lower wherever another audible clip has speech,
# with DUCK_RAMP_SECONDS ramps just outside each stretch. web/editor_audio_model.js has the same rules.
DUCK_DB = -8.
DUCK_RAMP_SECONDS = .3


def _speed(clip):
    return clip.get('speed', 1) if changed(clip) else 1


def duck_ranges(tracks, spans, rate=48000, fps=24):
    """Timeline sample ranges [first, last) with speech in a clip that is not itself lowered.

    spans maps 'kind:id' source keys to [start, end] seconds of that source; a source without an entry
    has no known speech. Ranges closer than two ramps are joined so the level does not bounce between them.
    """
    found = []
    for track in tracks:
        if track.get('kind') != 'audio' or not track_audible(track, tracks):
            continue
        for clip in track['clips']:
            source = clip.get('source')
            if not source or clip.get('duck') or clip.get('volume', 1) == 0 or not clip_audible(clip, track, tracks):
                continue
            stretches = spans.get(source['kind'] + ':' + source['id']) or ()
            first, last = _samples(clip['start'], rate, fps), _samples(clip['start'] + clip['frames'], rate, fps)
            origin = (source_sample_start(clip) * rate / 48000 if audio_changed(clip)
                      else _source_start(clip, rate, fps))
            speed = _speed(clip)
            for begin, end in stretches:
                a, b = (begin * rate - origin) / speed, (end * rate - origin) / speed
                if clip.get('reverse'):
                    a, b = (last - first) - b, (last - first) - a
                a, b = max(first, math.floor(first + a + .5)), min(last, math.floor(first + b + .5))
                if b > a:
                    found.append([a, b])
    found.sort()
    ramp = 2 * max(1, math.floor(DUCK_RAMP_SECONDS * rate + .5))
    joined = []
    for a, b in found:
        if joined and a <= joined[-1][1] + ramp:
            joined[-1][1] = max(joined[-1][1], b)
        else:
            joined.append([a, b])
    return joined


def duck_gain(ranges, block_start, count, rate=48000):
    """Gain per sample for a lowered clip over [block_start, block_start + count)."""
    import numpy as np
    gain = np.ones(count, dtype=np.float64)
    ramp = max(1, math.floor(DUCK_RAMP_SECONDS * rate + .5))
    low = 10. ** (DUCK_DB / 20.)
    position = np.arange(block_start, block_start + count, dtype=np.float64)
    for first, last in ranges:
        if last + ramp <= block_start or first - ramp >= block_start + count:
            continue
        into = np.clip((position - (first - ramp)) / ramp, 0, 1)
        out_of = np.clip(((last + ramp) - position) / ramp, 0, 1)
        np.minimum(gain, 1 - (1 - low) * np.minimum(into, out_of), out=gain)
    return gain


def duck_sources(tracks):
    """Source keys whose speech spans a lowered clip needs (none when nothing is lowered)."""
    if not any(clip.get('duck') and clip.get('source') for track in tracks for clip in track['clips']):
        return []
    keys = []
    for track in tracks:
        if track.get('kind') != 'audio':
            continue
        for clip in track['clips']:
            source = clip.get('source')
            key = source and source['kind'] + ':' + source['id']
            if key and not clip.get('duck') and key not in keys:
                keys.append(key)
    return keys
