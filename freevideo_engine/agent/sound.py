"""Finishing the sound of a whole cut in one job, after the cut is made.

Five steps, each an ordinary edit that stays visible and changeable on the timeline:
1. the sound of each picture's clip is split into dialogue, music and ambience (a4-a6), once per source file;
2. the separated music (a5) of each block goes where it is a sustained score (score_like); separation also puts shots,
   explosions and crowds there, so a block whose music part is not a score keeps it;
3. one piece of music lies on a2 from the first frame to the last, at one volume and lowered during speech
   (a piece already on a2 stays, only its volume changes);
4. one loudness: the dialogue of each block moves towards the common dialogue level, the ambience follows it down
   but never up, the music sits MUSIC_UNDER_LU under the dialogue, every volume then moves by the same amount to
   TARGET_LUFS, and the export holds the true peak under PEAK_DB (the project's master_peak_db);
5. digital silence in the dialogue and ambience is filled with the block's own room tone (a new ambience file).

Levels are measured on the export's own audio-only render, so speed, fades, crossfades and ducking count as the
user will hear them. Without sound separation (Mac, AMD, small cards) steps 1, 2 and 5 are skipped."""
import copy
import json
import math
import secrets
import shutil
from pathlib import Path

from .. import comfy_editor
from .jobs import JobError
from ..app_control import scipy_module

FOLDER = 'freevideo-sound'
RATE = 48000
SAMPLES_PER_FRAME = RATE // comfy_editor.FPS
TARGET_LUFS = -18.
PEAK_DB = -1.
SWING_DB = 4.             # the most one block's dialogue moves towards the common level
LIMIT_TAKES_DB = 5.       # the most the export's limiter is left to take off a block's loudest peak
MUSIC_UNDER_LU = 16.
SPEECH_LUFS = -45.        # a block whose dialogue is quieter has no speech; it keeps its level
MAX_VOLUME = 2.
HOLE_DB, HOLE_SECONDS = -70., .04
CUT_GAP_SECONDS, CUT_SPAN_SECONDS = .01, .4
LEVEL_ROUNDS = 3
# score_like: a separated music part is a sustained score when it is this loud (RMS), sounds (0.5 s windows over that
# RMS) more than this share of the time, and more than this share of its sounding frames are tonal: at least
# TONAL_LINE of the 60 Hz-5 kHz energy on spectral lines 10 dB over their neighbourhood.
SCORE_RMS, SCORE_SOUNDING, SCORE_TONAL, TONAL_LINE = .005, .5, .5, .3
FADE_FRAMES = 72          # a given piece longer than the cut fades out over its last 3 s
FADE_IN_FRAMES = 48       # and, when it starts inside the piece, fades in over 2 s


def _cancelled(row):
    if row.get('_cancel'):
        raise JobError('cancelled', 'Cancelled')


def _db(value):
    return 20 * math.log10(value) if value > 0 else -math.inf


def _round(value, digits=2):
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _audio_tracks(project):
    return {track['id']: track for track in project['tracks'] if track['kind'] == 'audio'}


def blocks(project):
    """The picture's clips in order, each with its linked sound clip (None when it has none) and that clip's parts."""
    tracks = _audio_tracks(project)
    clips = {clip['id']: clip for track in tracks.values() for clip in track['clips']}
    parts = {}
    for track in tracks.values():
        if track.get('stem'):
            for clip in track['clips']:
                if clip.get('stem_of'):
                    parts.setdefault(clip['stem_of'], {})[track['stem']] = clip
    video = next(track for track in project['tracks'] if track['id'] == 'v1')
    result = []
    for clip in sorted(video['clips'], key=lambda clip: clip['start']):
        if not clip.get('source'):
            continue
        sound = clips.get(clip.get('link'))
        sound = sound if sound is not None and sound.get('source') else None
        result.append(dict(video=clip, sound=sound, parts=parts.get(sound['id'], {}) if sound else {}))
    return result


def cuts(found):
    """Timeline frames where one picture clip meets the next."""
    return [b['video']['start'] for a, b in zip(found, found[1:]) if a['video']['start'] + a['video']['frames'] == b['video']['start']]


def level_gains(dialogue, peaks, *, keep_dialogue=False):
    """Per-block dialogue and ambience gains (dB) towards the median dialogue level of the blocks with speech.

    dialogue: integrated dialogue loudness per block (-inf for none); peaks: true peak of dialogue+ambience per block."""
    speech = [value for value in dialogue if value > SPEECH_LUFS]
    if not speech:
        return None, [dict(dialogue_db=0., ambience_db=0.) for _ in dialogue]
    speech.sort()
    middle = len(speech) // 2
    target = speech[middle] if len(speech) % 2 else (speech[middle - 1] + speech[middle]) / 2
    gains = []
    for loudness, peak in zip(dialogue, peaks):
        gain = 0.
        if loudness > SPEECH_LUFS and not keep_dialogue:
            gain = max(-SWING_DB, min(SWING_DB, target - loudness))
            if math.isfinite(peak):
                gain = min(gain, PEAK_DB - peak + LIMIT_TAKES_DB)
        # Lifting a quiet voice must not lift the crash or the crowd next to it.
        gains.append(dict(dialogue_db=gain, ambience_db=min(gain, 0.)))
    return target, gains


def cut_steps(mix, music, cut_frames):
    """At each cut: the music's level change (RMS 0.4 s each side, 10 ms clear of the cut) and the mix's short-term
    loudness change (the 3 s window ending at the cut against the one ending 0.6 s later)."""
    import numpy as np
    from ..audio_loudness import short_term
    curve = short_term(mix, RATE) if mix.shape[1] >= 3 * RATE else np.zeros(0)
    gap, span = round(CUT_GAP_SECONDS * RATE), round(CUT_SPAN_SECONDS * RATE)

    def rms_db(x):
        return max(-70., _db(float(np.sqrt(np.mean(np.square(x))))) if x.size else -70.)

    rows = []
    for frame in cut_frames:
        at = frame * SAMPLES_PER_FRAME
        row = dict(frame=frame)
        if music is not None:
            row['music_step_db'] = round(rms_db(music[:, at + gap:at + gap + span]) - rms_db(music[:, max(0, at - gap - span):at - gap]), 2)
        ending = round(frame / comfy_editor.FPS * 10) - 30
        if curve.size and 0 <= ending and ending + 6 < curve.size:
            row['mix_step_lu'] = round(float(max(curve[ending + 6], -70.) - max(curve[ending], -70.)), 2)
        rows.append(row)
    return rows


def summary(rows, key):
    import numpy as np
    values = np.abs([row[key] for row in rows if key in row])
    return dict(median=round(float(np.median(values)), 2), max=round(float(values.max()), 2)) if values.size else None


def holes(audio, start, end):
    """[first, last) sample runs in [start, end) where the audio stays under HOLE_DB for HOLE_SECONDS or longer."""
    import numpy as np
    hop = RATE // 100
    count = (end - start) // hop
    if count <= 0:
        return []
    frames = audio[:, start:start + count * hop].reshape(audio.shape[0], count, hop)
    quiet = np.sqrt(np.mean(np.square(frames), axis=(0, 2))) < 10 ** (HOLE_DB / 20)
    runs, index = [], 0
    while index < count:
        if quiet[index]:
            stop = index
            while stop < count and quiet[stop]:
                stop += 1
            if (stop - index) * hop >= HOLE_SECONDS * RATE:
                runs.append((start + index * hop, start + stop * hop))
            index = stop
        else:
            index += 1
    return runs


def _window_db(x, size, hop):
    import numpy as np
    count = max(0, (x.shape[1] - size) // hop + 1)
    if not count:
        return np.zeros(0)
    total = np.concatenate([[0.], np.cumsum(np.mean(np.square(x.astype(np.float64)), axis=0))])
    starts = np.arange(count) * hop
    return 10 * np.log10(np.maximum((total[starts + size] - total[starts]) / size, 1e-20))


def room_tone(ambience, dialogue, rate, start, end, quiet):
    """A steady stretch of ambience with no speech in [start, end), at the block's usual background level
    (25th percentile of the candidates), longest first: 1 s, 0.5 s, 0.25 s. None when the block has none.

    quiet: a boolean per 10 ms of the range that is digital silence, which a candidate must not touch."""
    import numpy as np
    hop, hop_10 = round(.05 * rate), rate // 100
    for seconds in (1., .5, .25):
        size = round(seconds * rate)
        if end - start < size:
            continue
        level = _window_db(ambience[:, start:end], size, hop)
        speech = _window_db(dialogue[:, start:end], size, hop)
        found = []
        for index in np.nonzero((speech < -55) & (level > -68) & (level < -35))[0]:
            at = start + index * hop
            if np.any(quiet[(at - start) // hop_10:(at - start + size) // hop_10]):
                continue
            if np.std(_window_db(ambience[:, at:at + size], hop, hop)) <= 3.:  # steady: room tone, not an effect hit
                found.append((at, level[index]))
        if found:
            usual = np.percentile([level for _, level in found], 25)
            at, _ = min(found, key=lambda item: abs(item[1] - usual))
            return ambience[:, at:at + size].copy()
    return None


def tile(tone, count, rate):
    """`count` samples of `tone` laid end to end, every other copy reversed, joined and faded with 15 ms
    equal-power ramps."""
    import numpy as np
    fade = min(round(.015 * rate), tone.shape[1] // 2)
    rise = np.sin(np.linspace(0, math.pi / 2, fade))[None, :]
    fall = np.cos(np.linspace(0, math.pi / 2, fade))[None, :]
    out = np.zeros((tone.shape[0], count + tone.shape[1] + fade))
    position, flip = 0, False
    while position < count + fade:
        piece = (tone[:, ::-1] if flip else tone).astype(np.float64)
        if position:
            piece = piece.copy()
            piece[:, :fade] *= rise
            out[:, position:position + fade] *= fall
            out[:, position + fade:position + piece.shape[1]] = 0
        out[:, position:position + piece.shape[1]] += piece
        position += piece.shape[1] - fade
        flip = not flip
    out = out[:, :count]
    edge = min(fade, count // 2)
    if edge:
        out[:, :edge] *= rise[:, :edge]
        out[:, count - edge:] *= fall[:, fade - edge:]
    return out


class _Renders:
    """Audio-only renders of the project through the export, as float [2, samples] at 48 kHz."""

    def __init__(self, jobs, row, folder):
        self.jobs, self.row, self.folder, self.count = jobs, row, folder, 0

    def __call__(self, project, *, solo=None, muted=(), master=False):
        import numpy as np
        from .. import editor_export
        value = copy.deepcopy(project)
        if not master:
            value.pop('master_peak_db', None)
        for track in value['tracks']:
            if track['kind'] != 'audio':
                continue
            if solo is not None:
                track['solo'] = track['id'] in solo
            if track['id'] in muted:
                track['muted'] = True
        self.count += 1
        target = self.folder / ('render-%d.wav' % self.count)
        try:
            editor_export.render(value, editor_export.resolve_source, target,
                                 options=dict(only='sound', sound_format='wav'),
                                 cancelled=lambda: bool(self.row.get('_cancel')))
        except editor_export.ExportCancelled:
            raise JobError('cancelled', 'Cancelled') from None
        except editor_export.ExportError as error:
            raise JobError('sound_failed', 'The sound of the cut cannot be rendered (%s)' % error, kind='sound') from None
        try:
            import av
            with av.open(str(target)) as container:
                chunks = [frame.to_ndarray() for frame in container.decode(audio=0)]
        finally:
            target.unlink(missing_ok=True)
        samples = np.concatenate(chunks, axis=1) if chunks else np.zeros((1, 0), np.int16)
        samples = samples.reshape(-1, 2).T if samples.shape[0] == 1 else samples
        return samples.astype(np.float64) / 32768


def run_finish_sound(jobs, row, *, poll=.5):
    spec, port = row['spec'], jobs.port
    identity = spec['editor_project']
    try:
        project = jobs.projects.get(identity)['project']
    except FileNotFoundError as error:
        raise JobError('not_found', 'The project does not exist') from error
    if not any(found['sound'] for found in blocks(project)):
        raise JobError('invalid_request', 'The cut has no picture clips with sound')
    folder = '%s/%s-%s' % (FOLDER, row['id'], secrets.token_hex(4))
    work = port.output_path(folder + '/work/.keep', create=True).parent
    made = []

    def phase(name, fraction=None):
        row['_phase'] = name
        row['_detail'] = json.dumps(dict(fraction=round(fraction, 3))) if fraction is not None else None

    result = dict(editor_project=identity, skipped={})
    try:
        render = _Renders(jobs, row, work)
        # Run again on a finished cut (it carries the export ceiling), only the blocks split this time are judged and
        # levelled; the rest keep what the first run gave them, so gains do not add up.
        finished = jobs.projects.get(identity)['project'].get('master_peak_db') is not None
        separated = _split(jobs, row, identity, phase, poll, result, made)
        fresh = set(result.get('split', {}).get('clips', ())) if finished else None
        _cancelled(row)
        if separated:
            scores = _separated_music_levels(jobs.projects.get(identity)['project'], render, result, fresh)
            _cancelled(row)
            _set_volumes(jobs, identity, {identity_: 0. for identity_ in scores})
        _cancelled(row)
        _music(jobs, row, spec, identity, phase, poll, result, made)
        _cancelled(row)
        phase('balancing loudness')
        _levels(jobs, row, identity, render, result, keep_dialogue=bool(spec.get('keep_dialogue_volume')), fresh=fresh)
        _cancelled(row)
        if separated:
            phase('filling silence')
            _fill(jobs, row, identity, render, port, folder, result, made)
        else:
            result['skipped'].setdefault('room_tone', 'stems_unavailable')
        _cancelled(row)
        phase('measuring loudness')
        project = jobs.projects.get(identity)['project']
        _measure(render, project, result)
    except BaseException:
        # Parts already placed stay with the project (they are ordinary edits); only unplaced files go.
        try:
            used = {clip['source']['id'] for track in jobs.projects.get(identity)['project']['tracks']
                    for clip in track['clips'] if clip.get('source') and clip['source']['kind'] == 'generated'}
        except Exception:  # noqa: BLE001 - unreadable: keep every file rather than break the project
            used = None
        for relative in made:
            if used is None or any(name == relative or name.startswith(relative + '/') for name in used):
                continue
            path = port.output_path(relative)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result['revision'] = jobs.projects.get(identity)['project']['revision']
    return result


def _split(jobs, row, identity, phase, poll, result, made):
    """Step 1; False when separation is not offered here, so nothing separated exists to work with."""
    from ..comfy_bridge import installation
    from ..editor_export import resolve_source
    from ..stems import service
    from . import stems
    from .routes import stems_supported
    try:
        machine = installation()[1]
    except (OSError, ValueError):
        machine = {}
    project = jobs.projects.get(identity)['project']
    found = [block for block in blocks(project) if block['sound']]
    if not stems_supported(machine):
        result['skipped'].update(split='stems_unavailable', music_removed='stems_unavailable')
        return any(block['parts'] for block in found)
    waiting = [block['sound'] for block in found if not block['parts'] and block['sound']['volume'] > 0]
    by_source = {}
    for clip in waiting:
        by_source.setdefault(_key(clip), []).append(clip)
    laid = []
    for index, (key, clips) in enumerate(by_source.items()):
        _cancelled(row)
        try:
            source = resolve_source(key)['audio']
        except (OSError, ValueError, KeyError) as error:
            raise JobError('stems_failed', 'The clip media is not readable', kind='stems') from error
        if source is None:
            continue
        name = '%s/%s-%d-%s' % (stems.FOLDER, row['id'], index, secrets.token_hex(4))
        out = jobs.port.output_path(name + '/dialogue.mp4', create=True).parent
        made.append(name)
        stems._split(jobs, row, service, source, out, lambda step, fraction=None: phase(
            'separating %d/%d' % (index + 1, len(by_source)) if step not in ('waiting', 'downloading') else step, fraction),
            poll)
        sources = {part: 'generated:%s/%s.mp4' % (name, part) for part in stems.NAMES}
        for clip in clips:
            phase('placing')
            jobs.projects.lay_stems(identity, clip['id'], sources)
            laid.append(clip['id'])
    result['split'] = dict(clips=laid, already=[block['sound']['id'] for block in found if block['parts']])
    return True


def score_like(samples, rate):
    """Whether a separated music part ([2, n] floats) is a sustained score, with the numbers behind it. Separation puts
    some effects with the music; shots, explosions and crowds are broadband, so they fail the tonal test, and short
    stings fail the sounding one."""
    import numpy as np
    median_filter = scipy_module('scipy.ndimage').median_filter
    mono = np.asarray(samples, np.float64).mean(axis=0)
    rms = float(np.sqrt(np.mean(mono ** 2))) if mono.size else 0.
    half = rate // 2
    windows = mono[:mono.size // half * half].reshape(-1, half)
    sounding = float(np.mean(np.sqrt(np.mean(windows ** 2, axis=1)) > SCORE_RMS)) if windows.size else 0.
    size, hop = 4096, 2048
    tonal = 0.
    if mono.size >= size:
        window = np.hanning(size)
        frames = np.lib.stride_tricks.sliding_window_view(mono, size)[::hop] * window
        power = np.abs(np.fft.rfft(frames, axis=1)) ** 2 + 1e-14
        frequency = np.fft.rfftfreq(size, 1 / rate)
        band = power[:, (frequency > 60) & (frequency < 5000)]
        lines = (band * (band > 10 * median_filter(band, size=(1, 15)))).sum(axis=1) / band.sum(axis=1)
        loud = np.sqrt(np.mean(frames ** 2, axis=1) / np.mean(window ** 2)) > SCORE_RMS
        tonal = float(np.mean(lines[loud] >= TONAL_LINE)) if loud.any() else 0.
    found = rms >= SCORE_RMS and sounding > SCORE_SOUNDING and tonal > SCORE_TONAL
    return found, dict(music_rms=round(rms, 4), sounding=round(sounding, 2), tonal=round(tonal, 2))


def _separated_music_levels(project, render, result, fresh=None):
    """Before step 2: for each split block, whether its music part is a score (it goes) or effects (it stays), and the
    block's level with and without that part and of the part alone. Levels are the K-weighted mean over the block,
    ungated, so taking a part away can only lower them; the loudest values are the highest 400 ms momentary loudness.
    Returns the ids of the music parts to silence. fresh: when given, only blocks whose sound clip is in it."""
    import numpy as np
    from ..audio_loudness import _block_powers, _lufs
    tracks = _audio_tracks(project)
    music = tuple(identity for identity, track in tracks.items() if track.get('stem') == 'music')
    rest = tuple(identity for identity, track in tracks.items() if track.get('stem') != 'music' and identity != 'a2')
    found = [block for block in blocks(project) if block['sound'] and block['parts'].get('music')
             and block['parts']['music']['volume'] > 0 and (fresh is None or block['sound']['id'] in fresh)]
    if not music or not found:
        return []
    before, after, alone = render(project, solo=rest + music), render(project, solo=rest), render(project, solo=music)

    def level(samples):
        powers = _block_powers(samples, RATE, samples.shape[1] / RATE) if samples.shape[1] else np.zeros(0)
        return float(_lufs(powers[0])) if powers.size and powers[0] > 0 else -math.inf

    def loudest(samples):
        powers = _block_powers(samples, RATE, .4)
        return float(_lufs(powers.max())) if powers.size and powers.max() > 0 else -math.inf

    rows, scores = [], []
    for block in found:
        a, b = _span(block)
        score, numbers = score_like(alone[:, a:b], RATE)
        if score:
            scores.append(block['parts']['music']['id'])
        with_music, without = level(before[:, a:b]), level(after[:, a:b])
        rows.append(dict(clip_id=block['video']['id'], removed=score, **numbers, with_music_db=_round(with_music),
                         without_music_db=_round(without),
                         drop_db=_round(with_music - without) if math.isfinite(with_music + without) else None,
                         music_db=_round(level(alone[:, a:b])), music_loudest_lufs=_round(loudest(alone[:, a:b])),
                         without_music_loudest_lufs=_round(loudest(after[:, a:b]))))
    result['music_removed'] = dict(blocks=rows, removed=len(scores), kept=len(rows) - len(scores))
    return scores


def _music(jobs, row, spec, identity, phase, poll, result, made):
    """Step 3: keep the music on a2, or lay the given piece, or compose one as long as the cut."""
    from ..music import service
    from . import music
    project = jobs.projects.get(identity)['project']
    track = _audio_tracks(project).get('a2')
    if track is not None and any(clip.get('source') for clip in track['clips']):
        result['music'] = dict(kept=[clip['id'] for clip in track['clips'] if clip.get('source')])
        return
    asked = spec.get('music') or {}
    frames = comfy_editor.duration(project)
    if not asked:
        result['skipped']['music'] = 'not_asked'
        return
    if 'source' in asked:
        source = asked['source']
    else:
        if frames / comfy_editor.FPS > service.MAX_SECONDS:
            result['skipped']['music'] = 'too_long'
            return
        relative = '%s/%s-%s.mp4' % (music.FOLDER, row['id'], secrets.token_hex(4))
        out = jobs.port.output_path(relative, create=True)
        made.append(relative)
        seed = asked.get('seed', secrets.randbelow(2**32))
        music._compose(jobs, row, service, out, frames, seed, [], phase, poll, caption=asked['caption'])
        source = 'generated:' + relative
        result['music_seed'] = seed
    phase('placing')
    try:
        saved, clip_ids = jobs.projects.lay_music(identity, source)
    except (ValueError, KeyError) as error:
        raise JobError('invalid_request', 'The music source is not a sound or video file of this installation') from error

    laid = [clip for track in saved['tracks'] for clip in track['clips'] if clip['id'] in clip_ids]
    skip = 0
    if len(laid) == 1 and laid[0]['frames'] > frames:
        # A given piece longer than the cut: start it where its own swells and breaks fall clear of the cuts.
        try:
            power = _music_power(source)
        except Exception:  # noqa: BLE001 - unreadable here: start at its beginning, as placed
            power = None
        if power is not None:
            seconds = music_offset(power, [frame / comfy_editor.FPS for frame in cuts(blocks(project))],
                                   frames / comfy_editor.FPS)
            skip = min(round(seconds * comfy_editor.FPS), laid[0]['frames'] - frames)

    def lowered(project):
        # As on the page: placed music is lowered during speech. A longer piece ends with the cut, faded in where it
        # starts inside the piece and faded out where it stops inside it, so the cut does not grow.
        value = copy.deepcopy(project)
        for track in value['tracks']:
            for clip in track['clips']:
                if clip['id'] in clip_ids:
                    clip['duck'] = True
                    if clip['start'] + clip['frames'] > frames:
                        clip['in'] += skip
                        clip['frames'] = max(1, frames - clip['start'])
                        if skip:
                            clip['fade_in'] = min(FADE_IN_FRAMES, clip['frames'] // 2)
                        if clip['in'] + clip['frames'] < clip['length']:
                            clip['fade_out'] = min(FADE_FRAMES, clip['frames'] // 2)
        return dict(ok=True, project=value)
    jobs.projects._update(identity, lowered)
    result['music'] = dict(laid=clip_ids, source=source)
    if skip:
        result['music']['starts_at_seconds'] = round(skip / comfy_editor.FPS, 2)


def _music_power(source):
    """Mean square of the piece every 10 ms, at 48 kHz, both channels together."""
    import av
    import numpy as np
    from ..editor_export import resolve_source
    path = resolve_source(source)['audio']
    if path is None:
        return None
    chunks = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format='flt', layout='mono', rate=RATE)
        for frame in container.decode(audio=0):
            chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
        chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None) or ())
    samples = np.concatenate(chunks).astype(np.float64) if chunks else np.zeros(0)
    hop = RATE // 100
    count = samples.size // hop
    return np.mean(np.square(samples[:count * hop].reshape(count, hop)), axis=1)


def music_offset(power, cut_seconds, film_seconds, *, hop=.01):
    """Where in a piece longer than the cut to start it (seconds): the start whose own level changes least across
    the cuts (RMS 0.4 s each side, 10 ms clear, as cut_steps measures), and among the starts within 1 dB of that, the
    latest, so the piece's own ending comes as close to the film's end as it can."""
    import numpy as np
    span = int(round(film_seconds / hop))
    room = power.size - span
    if room <= 0 or not cut_seconds:
        return max(0, room) * hop
    total = np.concatenate([[0.], np.cumsum(power)])
    offsets = np.arange(room + 1)
    gap, size = round(CUT_GAP_SECONDS / hop), round(CUT_SPAN_SECONDS / hop)
    floor = 10 ** (-70 / 10)
    worst = np.zeros(offsets.size)
    for cut in cut_seconds:
        at = offsets + int(round(cut / hop))
        low, high = np.clip(at - gap - size, 0, power.size), np.clip(at - gap, 0, power.size)
        before = (total[high] - total[low]) / size
        low, high = np.clip(at + gap, 0, power.size), np.clip(at + gap + size, 0, power.size)
        after = (total[high] - total[low]) / size
        worst = np.maximum(worst, np.abs(10 * np.log10(np.maximum(after, floor) / np.maximum(before, floor))))
    return float(offsets[np.nonzero(worst <= worst.min() + 1.)[0][-1]] * hop)


def _set_volumes(jobs, identity, factors):
    """Multiply clip volumes (clip id -> linear factor), capped at MAX_VOLUME; returns the ids that hit the cap."""
    capped = set()

    def change(project):
        value = copy.deepcopy(project)
        for track in value['tracks']:
            for clip in track['clips']:
                if clip['id'] in factors and track['kind'] == 'audio':
                    wanted = clip['volume'] * factors[clip['id']]
                    if wanted > MAX_VOLUME:
                        capped.add(clip['id'])
                    clip['volume'] = round(min(MAX_VOLUME, wanted), 4)
        return dict(ok=True, project=value)
    jobs.projects._update(identity, change)
    return capped


def _key(clip):
    return '%s:%s' % (clip['source']['kind'], clip['source']['id'])


def _span(block):
    clip = block['video']
    return clip['start'] * SAMPLES_PER_FRAME, (clip['start'] + clip['frames']) * SAMPLES_PER_FRAME


def _levels(jobs, row, identity, render, result, *, keep_dialogue, fresh=None):
    """Step 4. fresh: when given, only blocks whose sound clip is in it get their own gain; all count for the target."""
    from ..audio_loudness import integrated, true_peak
    project = jobs.projects.get(identity)['project']
    found = [block for block in blocks(project) if block['sound']]
    # A split clip is silent on a1 and an unsplit one has nothing on a4, so together they are the dialogue of each
    # block (with the whole original sound where it was not split).
    dialogue = render(project, solo=('a1', 'a4'))
    # The peak of everything but the score: music parts that stayed are effects and move with the ambience.
    both = render(project, solo=('a1', 'a4', 'a5', 'a6'))
    _cancelled(row)
    loudness, peaks = [], []
    for block in found:
        a, b = _span(block)
        loudness.append(integrated(dialogue[:, a:b], RATE) if b - a >= RATE // 2 else -math.inf)
        peaks.append(true_peak(both[:, a:b], RATE) if b > a else -math.inf)
    target, gains = level_gains(loudness, peaks, keep_dialogue=keep_dialogue)
    factors = {}
    if fresh is not None:
        gains = [gain if block['sound']['id'] in fresh else dict(dialogue_db=0., ambience_db=0.)
                 for block, gain in zip(found, gains)]
    for block, gain in zip(found, gains):
        speech = block['parts'].get('dialogue') or block['sound']
        ambience = block['parts'].get('ambience')
        if speech is not None:
            factors[speech['id']] = 10 ** (gain['dialogue_db'] / 20)
        if ambience is not None:
            factors[ambience['id']] = 10 ** (gain['ambience_db'] / 20)
        kept = block['parts'].get('music')
        if kept is not None and kept['volume'] > 0:
            factors[kept['id']] = 10 ** (gain['ambience_db'] / 20)
    capped = _set_volumes(jobs, identity, factors)
    result['blocks'] = [dict(clip_id=block['video']['id'], dialogue_lufs=_round(value), gain_db=round(gain['dialogue_db'], 2),
                             ambience_gain_db=round(gain['ambience_db'], 2))
                        for block, value, gain in zip(found, loudness, gains)]
    result['dialogue_lufs'] = _round(target)
    project = jobs.projects.get(identity)['project']
    music_clips = [clip for clip in _audio_tracks(project).get('a2', {}).get('clips', ()) if clip.get('source')]
    if music_clips and target is not None:
        bed = render(project, solo=('a2',))
        level = integrated(bed, RATE)
        if math.isfinite(level):
            gain = target - MUSIC_UNDER_LU - level
            capped |= _set_volumes(jobs, identity, {clip['id']: 10 ** (gain / 20) for clip in music_clips})
            result['music_gain_db'] = round(gain, 2)
    # One factor for every clip, until the whole cut sits at TARGET_LUFS.
    for _ in range(LEVEL_ROUNDS):
        _cancelled(row)
        project = jobs.projects.get(identity)['project']
        level = integrated(render(project), RATE)
        if not math.isfinite(level) or abs(level - TARGET_LUFS) <= .2:
            break
        factor = 10 ** ((TARGET_LUFS - level) / 20)
        everything = {clip['id']: factor for track in project['tracks'] if track['kind'] == 'audio'
                      for clip in track['clips'] if clip.get('source') and clip['volume'] > 0}
        capped |= _set_volumes(jobs, identity, everything)

    def ceiling(project):
        return dict(ok=True, project=dict(copy.deepcopy(project), master_peak_db=PEAK_DB))
    jobs.projects._update(identity, ceiling)
    if capped:
        result['volume_capped'] = sorted(capped)


def _fill(jobs, row, identity, render, port, folder, result, made):
    """Step 5: room tone into each block's ambience file wherever dialogue and ambience fall to digital silence.

    The tone is the block's own; a block with no steady stretch of it borrows the nearest block's."""
    import numpy as np
    from ..editor_export import resolve_source
    from ..editor_speed import changed as speed_changed
    from ..stems.separate import SAMPLE_RATE, read_audio, write_mp4
    project = jobs.projects.get(identity)['project']
    found = [block for block in blocks(project) if block['parts'].get('ambience') and block['parts'].get('dialogue')]
    both = render(project, solo=('a4', 'a6'))
    runs_of = [holes(both, *_span(block)) for block in found]
    read, tones = {}, {}

    def material(index):
        """The block's ambience and dialogue sources, its range in them and the timeline-to-source mapping."""
        if index not in read:
            block = found[index]
            ambience, dialogue = block['parts']['ambience'], block['parts']['dialogue']
            read[index] = None
            if speed_changed(ambience) or ambience.get('reverse'):
                return None
            try:
                tone_source, _ = read_audio(resolve_source(_key(ambience))['audio'])
                speech_source, _ = read_audio(resolve_source(_key(dialogue))['audio'])
            except Exception:  # noqa: BLE001 - an unreadable part keeps its silence; the report counts it
                return None

            def source_at(sample, ambience=ambience):
                # Timeline sample -> source sample at the separation rate.
                return round((ambience['in'] * SAMPLES_PER_FRAME + sample - ambience['start'] * SAMPLES_PER_FRAME)
                             * SAMPLE_RATE / RATE)
            a, b = _span(block)
            first = source_at(max(a, ambience['start'] * SAMPLES_PER_FRAME))
            last = min(source_at(min(b, (ambience['start'] + ambience['frames']) * SAMPLES_PER_FRAME)),
                       tone_source.shape[1], speech_source.shape[1])
            read[index] = dict(tone=tone_source, speech=speech_source, first=first, last=last, at=source_at)
        return read[index]

    def tone(index):
        if index not in tones:
            tones[index] = None
            found_ = material(index)
            if found_ is not None and found_['last'] > found_['first']:
                hop = SAMPLE_RATE // 100
                quiet = np.zeros((found_['last'] - found_['first']) // hop + 1, bool)
                for x, y in runs_of[index]:
                    quiet[max(0, (found_['at'](x) - found_['first']) // hop):max(0, (found_['at'](y) - found_['first']) // hop) + 1] = True
                tones[index] = room_tone(found_['tone'], found_['speech'], SAMPLE_RATE, found_['first'], found_['last'], quiet)
        return tones[index]

    filled, seconds, unfilled = [], 0., []
    for index, (block, runs) in enumerate(zip(found, runs_of)):
        _cancelled(row)
        if not runs:
            continue
        own = material(index)
        donor = index if own is not None and tone(index) is not None else None
        if own is not None and donor is None:
            for distance in range(1, len(found)):
                donor = next((j for j in (index - distance, index + distance) if 0 <= j < len(found) and tone(j) is not None), None)
                if donor is not None:
                    break
        if donor is None:
            unfilled.append(dict(clip_id=block['video']['id'], holes=len(runs)))
            continue
        patched = own['tone'].astype(np.float64)
        for x, y in runs:
            p, q = max(own['first'], own['at'](x)), min(own['last'], own['at'](y))
            if q > p:
                patched[:, p:q] += tile(tones[donor], q - p, SAMPLE_RATE)
                seconds += (y - x) / RATE
        ambience = block['parts']['ambience']
        relative = '%s/%s.mp4' % (folder, ambience['id'])
        made.append(relative)
        write_mp4(port.output_path(relative, create=True), patched.astype(np.float32))
        jobs.projects.replace_voice_source(identity, ambience['id'], 'generated:' + relative)
        filled.append(dict(clip_id=block['video']['id'], holes=len(runs), **(
            {} if donor == index else dict(tone_from=found[donor]['video']['id']))))
    after = render(jobs.projects.get(identity)['project'], solo=('a4', 'a6'))
    left = sum(len(holes(after, *_span(block))) for block in found)
    result['room_tone'] = dict(blocks=filled, seconds=round(seconds, 2), holes_left=left, not_filled=unfilled)


def _measure(render, project, result):
    """The finished cut as exported: loudness, true peak and the steps at each cut."""
    from ..audio_loudness import integrated, true_peak
    finished = render(project, master=True)
    result['loudness_lufs'] = _round(integrated(finished, RATE))
    result['true_peak_db'] = _round(true_peak(finished, RATE))
    music = None
    if any(clip.get('source') for clip in _audio_tracks(project).get('a2', {}).get('clips', ())):
        # The music as it sounds in the cut, lowered during speech: the mix less the mix without it.
        music = render(project) - render(project, muted=('a2',))
    rows = cut_steps(finished, music, cuts(blocks(project)))
    result['cuts'] = rows
    result['cut_steps'] = dict(mix_lu=summary(rows, 'mix_step_lu'), music_db=summary(rows, 'music_step_db'))
