"""Join finished chain blocks end to end into one saved video.

The picture is copied packet by packet, except for a few closing frames when
needed to reach an exact requested length with matching codec parameters. The
sound is decoded and encoded once as a single stream: every block's sound
follows the previous block's sound, not its own first frame, which keeps a
long chain in step.
Original soundtracks instead bypass every block's sound and mux the selected
source range once, with a sample-identity receipt.
"""
from datetime import datetime
from fractions import Fraction
import json
import logging
import os
from pathlib import Path
import shutil
import uuid

FPS = 24
SEAM_TAIL = 12000
SEAM_HEAD = 240


class JoinCancelled(InterruptedError):
    pass


class _VoiceFailed(Exception):
    """The optional voice step failed partway: the join is done again without it."""


def _voice_step(call, *args):
    try:
        return call(*args)
    except JoinCancelled:
        raise
    except Exception as error:
        raise _VoiceFailed() from error


class _ExactEndFailed(Exception):
    pass


def _check(cancelled):
    if cancelled is not None and cancelled():
        raise JoinCancelled()


def _shape(stream):
    context = stream.codec_context
    return (context.name, context.width, context.height, context.pix_fmt, stream.time_base,
            stream.average_rate, bytes(context.extradata or b''))


def _last_kept(path, wanted):
    """The frame a file can end on at or just after `wanted` frames, or None to keep all of it.

    Packets are copied, not encoded, so a file may only end on a frame that later frames do not
    complete: one that is shown after everything decoded before it. That is at most a few frames on.
    """
    import av
    with av.open(str(path)) as container:
        video = container.streams.video[0]
        step = int(Fraction(1, video.average_rate) / video.time_base)
        start = latest = None
        for packet in container.demux(video):
            if packet.dts is None:
                continue
            if start is None:
                start = video.start_time if video.start_time is not None else packet.pts
            if latest is None or packet.pts > latest:
                latest = packet.pts
                if (latest - start) // step >= wanted - 1:
                    return (latest - start) // step
    return None


def _exact_tail(source, target, wanted, cancelled=None):
    """Prepare at most three closing frames, or leave the packet-copy ending alone."""
    import av
    temporary = target.with_name('.join-tail-' + uuid.uuid4().hex + '.mp4')
    try:
        with av.open(str(source)) as container:
            video = container.streams.video[0]
            shape = _shape(video)
            if shape[0] != 'h264':
                return None
            step = int(Fraction(1, video.average_rate) / video.time_base)
            begin = video.start_time
            prefix = count = 0
            latest = previous_dts = None
            for packet in container.demux(video):
                _check(cancelled)
                if packet.dts is None:
                    continue
                if begin is None:
                    begin = packet.pts
                if latest is None or packet.pts > latest:
                    latest = packet.pts
                    shown = (latest - begin) // step
                    if shown >= wanted - 1:
                        break
                    prefix = shown + 1
                count += 1
                previous_dts = packet.dts
            if count != prefix or not 1 <= wanted - prefix <= 3:
                return None
            # Seek to the preceding key frame; only the remaining shown frames are encoded.
            container.seek(begin + prefix * step, stream=video, backward=True)
            with av.open(str(temporary), 'w', format='mp4') as output:
                tail = output.add_stream('libx264', rate=FPS)
                tail.width, tail.height, tail.pix_fmt = shape[1:4]
                tail.time_base = shape[4]
                # decode_to_file's encode_video uses these libx264 defaults and yuv420p.
                encoded = 0
                for frame in container.decode(video):
                    _check(cancelled)
                    position = (frame.pts - begin) // step
                    if position < prefix:
                        continue
                    if position != prefix + encoded:
                        return None
                    frame.pts, frame.time_base = encoded, Fraction(1, FPS)
                    frame.pict_type = av.video.frame.PictureType.NONE
                    for packet in tail.encode(frame):
                        output.mux(packet)
                    encoded += 1
                    if encoded == wanted - prefix:
                        break
                if encoded != wanted - prefix:
                    return None
                for packet in tail.encode(None):
                    output.mux(packet)
        with av.open(str(temporary)) as container:
            tail = container.streams.video[0]
            if _shape(tail) != shape:
                return None
            packets = [packet for packet in container.demux(tail) if packet.dts is not None]
            if len(packets) != encoded or not packets[0].is_keyframe:
                return None
            if sorted(packet.pts for packet in packets) != [number * step for number in range(encoded)]:
                return None
            # A one- or two-frame encode has less delay than the block. Keep the
            # copied decoding cadence while leaving the new display timestamps unchanged.
            shift = previous_dts - begin + step - (packets[0].dts + prefix * step)
            for packet in packets:
                packet.dts += shift
                if packet.dts > packet.pts:
                    return None
            stamps = [previous_dts - begin] + [packet.dts + prefix * step for packet in packets]
            if any(later <= earlier for earlier, later in zip(stamps, stamps[1:])):
                return None
        _check(cancelled)
        return prefix, packets
    except JoinCancelled:
        raise
    except Exception:
        return None
    finally:
        temporary.unlink(missing_ok=True)


def join_files(sources, target, cancelled=None, frames=None, leads=None, expected=None, speaking=None, *,
               original_audio=None):
    """Write `target` from `sources` in order; returns the joined frame and sample counts.

    `frames`: stop at this length when a compatible short tail can close it exactly,
    otherwise keep the packet-copy ending at or just after it.
    `leads`: optional `leads(following, previous)` returning a seam lead kept by the chain;
    the shared continuation record is read only when it has none.
    `expected`: how many seams should be repaired; only reported back, the caller decides.
    `original_audio`: mux the original selection and close the picture on its
    source-sample clock; a rounded completed prefix keeps its existing frames.
    """
    sources = list(sources)
    try:
        return _join_ending(sources, target, cancelled, frames, leads, expected, speaking, original_audio)
    except _VoiceFailed:
        logging.warning('[FreeVideo] The voice of the parts could not be evened out; they are joined as they are',
                        exc_info=True)
        return _join_ending(sources, target, cancelled, frames, leads, expected, None, original_audio)


def _join_ending(sources, target, cancelled, frames, leads, expected, speaking, original_audio=None):
    try:
        return _join_files(sources, target, cancelled, frames, leads=leads, expected=expected, speaking=speaking,
                           original_audio=original_audio)
    except _ExactEndFailed:
        # A late mux or validation error must also leave the original ending available.
        return _join_files(sources, target, cancelled, frames, exact=False, leads=leads, expected=expected,
                           speaking=speaking, original_audio=original_audio)


def _join_files(sources, target, cancelled, frames, exact=True, leads=None, expected=None, speaking=None,
                original_audio=None):
    import av
    from .comfy_metadata import _copy_stream
    audio_lead_in = apply_audio_lead_in = None
    if original_audio is None:
        try:
            from .comfy_continuation import audio_lead_in, apply_audio_lead_in
        except ImportError:
            audio_lead_in = apply_audio_lead_in = None
    if leads is not None and audio_lead_in is not None:
        stored = audio_lead_in
        def audio_lead_in(video, previous=None):
            if expected is not None:
                # The caller named the seams that must be repaired and keeps their sound itself;
                # a shared record for any other pair of videos must not stand in for them.
                return leads(video, previous)
            return leads(video, previous) or stored(video, previous=previous)
    sources, target = [Path(source) for source in sources], Path(target)
    if len(sources) < (2 if original_audio is None else 1):
        raise ValueError('Joining needs at least two videos')
    if target.exists() or target.is_symlink():
        raise ValueError('The joined video already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.join-' + uuid.uuid4().hex + '.mp4')
    wanted, frames, samples = frames, 0, 0
    used_tail = False
    try:
        with av.open(str(sources[0])) as first:
            if len(first.streams.video) != 1:
                raise ValueError('A block has no single picture stream')
            shape = _shape(first.streams.video[0])
            sound = first.streams.audio[0] if first.streams.audio and original_audio is None else None
            rate = sound.codec_context.sample_rate if sound is not None else None
            layout = sound.codec_context.layout.name if sound is not None else None
            bit_rate = max(sound.codec_context.bit_rate or 0, 192000) if sound is not None else None
        step = Fraction(1, shape[5]) / shape[4]
        if shape[5] != FPS or step.denominator != 1:
            raise ValueError('The blocks are not 24 frames per second videos')
        step = int(step)
        voice = _voice(sources, speaking, cancelled) if sound is not None else None
        with av.open(str(temporary), 'w', format='mp4',
                     options={'movflags': 'use_metadata_tags+faststart'}) as output:
            picture = audio = fifo = joined_audio = None
            for index, source in enumerate(sources):
                _check(cancelled)
                if wanted is not None and frames >= wanted and picture is not None:
                    break
                last = _last_kept(source, wanted - frames) if wanted is not None else None
                with av.open(str(source)) as container:
                    if len(container.streams.video) != 1 or _shape(container.streams.video[0]) != shape:
                        raise ValueError('The blocks differ in size or encoding')
                    if original_audio is None and bool(container.streams.audio) != (sound is not None):
                        raise ValueError('Only some of the blocks have sound')
                    video = container.streams.video[0]
                    tail = (_exact_tail(source, target, wanted - frames, cancelled)
                            if exact and last is not None and last + 1 > wanted - frames else None)
                    copied_last = tail[0] - 1 if tail is not None else last
                    if tail is not None:
                        used_tail = True
                        last = wanted - frames - 1
                    if picture is None:
                        picture = _copy_stream(output, video)
                        if sound is not None:
                            audio = output.add_stream('aac', rate=rate)
                            audio.layout = layout
                            audio.bit_rate = bit_rate
                            audio.codec_context.time_base = Fraction(1, rate)
                            fifo = av.AudioFifo()
                            joined_audio = _JoinedAudio(output, audio, fifo, rate, layout,
                                                        audio_lead_in, apply_audio_lead_in, cancelled, voice)
                    streams = [video] + ([container.streams.audio[0]] if sound is not None else [])
                    shift, count = None, 0
                    # A decoder returns the encoder's padding after the last sample; the stream's own
                    # length says where the sound ends. Left in, every block would end 20 ms late.
                    length = streams[-1].duration if sound is not None else None
                    limit = round(length * streams[-1].time_base * rate) if length is not None else None
                    if joined_audio is not None:
                        following = sources[index + 1] if last is None and index + 1 < len(sources) else None
                        # Selecting the final frame of a whole block leaves its existing sound limit alone.
                        total_frames = (frames + last + 1 if last is not None
                                        and (not video.frames or last + 1 < video.frames) else None)
                        joined_audio.start(source, streams[-1], limit, last, following, total_frames)
                    for packet in container.demux(*streams):
                        if packet.dts is None:
                            continue
                        if packet.stream is video:
                            if shift is None:
                                # The first packet of a file is its first shown frame's key frame.
                                begin = video.start_time if video.start_time is not None else packet.pts
                                shift = frames * step - begin
                            if copied_last is not None and (packet.pts - begin) // step > copied_last:
                                continue
                            packet.pts += shift
                            packet.dts += shift
                            packet.stream = picture
                            output.mux(packet)
                            count += 1
                            if not count % 48:
                                _check(cancelled)
                            continue
                        for decoded in packet.decode():
                            joined_audio.write(decoded)
                    if tail is not None:
                        for packet in tail[1]:
                            _check(cancelled)
                            packet.pts += (frames + tail[0]) * step
                            packet.dts += (frames + tail[0]) * step
                            packet.stream = picture
                            output.mux(packet)
                            count += 1
                    if joined_audio is not None:
                        joined_audio.end()
                    if not count:
                        raise ValueError('A block has no picture')
                    frames += count
            if audio is not None:
                samples = joined_audio.finish()
                for packet in audio.encode(None):
                    output.mux(packet)
        _check(cancelled)
        with av.open(str(temporary)) as check:
            if check.streams.video[0].frames != frames:
                raise ValueError('The joined video is incomplete')
        receipt = None
        if original_audio is not None:
            from .original_audio import mux
            required = ((original_audio['end_sample'] - original_audio['start_sample']) * FPS
                        + original_audio['sample_rate'] - 1) // original_audio['sample_rate']
            if frames < required:
                # Completed prefixes end on nearest-sample global boundaries.
                # Rounding up can put the endpoint half a sample past the
                # last frame; it does not require a further picture frame.
                count = original_audio['end_sample'] - original_audio['start_sample']
                if 2 * (count * FPS - frames * original_audio['sample_rate']) > FPS:
                    raise ValueError('Original audio is longer than the joined picture')
                required = frames
            receipt = mux(temporary, temporary, original_audio, video_frames=required)
            _check(cancelled)
            rate, samples = receipt['sample_rate'], receipt['samples_per_channel']
            with av.open(str(temporary)) as check:
                video = check.streams.video[0]
                frames = video.frames
                if (video.duration is None or
                        abs(video.duration * video.time_base - Fraction(samples, rate)) > Fraction(1, rate)):
                    raise ValueError('The joined picture did not end on the selected audio sample')
            if frames != required:
                raise ValueError('The joined picture did not reach the selected audio endpoint')
        os.replace(temporary, target)
        return dict(**({'original_audio': receipt} if receipt is not None else {}),
                    exact_end=wanted is not None and frames == wanted, frames=frames, width=shape[1], height=shape[2],
                    audio_samples=samples, sample_rate=rate,
                    seams_repaired=joined_audio.seams_repaired if joined_audio is not None else 0,
                    seams_expected=expected,
                    voice=[[round(setting[0], 1), round(setting[1], 1)] if setting is not None else None
                           for setting in voice] if voice is not None and audio is not None else None)
    except (JoinCancelled, _VoiceFailed):
        raise
    except Exception as error:
        if used_tail:
            raise _ExactEndFailed() from error
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _voice(sources, speaking, cancelled):
    """Each block's voice setting, or None: an optional step that never stops the join."""
    if speaking is None:
        return None
    try:
        from .chain_level import plan
        return plan(sources, speaking, lambda: cancelled is not None and cancelled())
    except Exception:
        logging.warning('[FreeVideo] The voice of the parts could not be measured; they are joined as they are')
        return None


class _JoinedAudio:
    """Keep native seam windows out of the encoder until the next block's head is ready."""
    def __init__(self, output, stream, fifo, rate, layout, read_lead, apply_lead, cancelled=None, voice=None):
        self.output, self.stream, self.fifo = output, stream, fifo
        self.shaper = None
        if voice is not None:
            from .chain_level import Shaper
            self.shaper = _voice_step(Shaper, voice, rate, stream.codec_context.layout.nb_channels)
        self.rate, self.layout = rate, layout
        self.read_lead, self.apply_lead = read_lead, apply_lead
        self.cancelled, self.next_head = cancelled, None
        self.warned = False
        self.written = self.seams_repaired = 0

    def start(self, source, stream, limit, last=None, following=None, total_frames=None):
        import av
        context = stream.codec_context
        self.source, self.following = str(source), str(following) if following is not None else None
        self.limit, self.heard, self.kept = limit, 0, 0
        # end() has released the previous tail and flushed its resampler. Count
        # both encoded samples and those still waiting in the encoder queue.
        joined = self.written + self.fifo.samples + (self.shaper.pending if self.shaper is not None else 0) \
            if total_frames is not None else 0
        if self.shaper is not None:
            _voice_step(self.shaper.mark)
        if last is not None:
            cut = (max(0, round(total_frames * self.rate / FPS) - joined) if total_frames is not None
                   else round((last + 1) * self.rate / FPS))
            self.limit = min(self.limit, cut) if self.limit is not None else cut
        self.tail, self.released = None, False
        self.head = self.next_head[1] if self.next_head is not None and self.next_head[0] == self.source else None
        self.next_head, self.head_position = None, 0
        self.resampler = av.AudioResampler(format='fltp', layout=self.layout, rate=self.rate)
        self.native = (av.AudioResampler(format='fltp', layout='stereo', rate=32000)
                       if context.sample_rate == 32000 and context.layout.name == 'stereo' else None)
        self.native_limit = round(stream.duration * stream.time_base * 32000) if stream.duration is not None else None
        if last is not None:
            cut = (max(0, round(total_frames * 32000 / FPS) - round(joined * 32000 / self.rate))
                   if total_frames is not None else round((last + 1) * 32000 / FPS))
            self.native_limit = min(self.native_limit, cut) if self.native_limit is not None else cut

    def write(self, decoded):
        if self.native is None:
            self._encode(self.resampler.resample(decoded))
        else:
            for frame in self.native.resample(decoded):
                self._native(frame)

    def _native(self, frame):
        import numpy as np
        data = np.array(frame.to_ndarray().T, dtype=np.float32, order='C', copy=True)
        if self.head is not None:
            count = min(SEAM_HEAD - self.head_position, len(data))
            data[:count] = self.head[self.head_position:self.head_position + count]
            self.head_position += count
            if self.head_position == SEAM_HEAD:
                self.head = None
        if self.released:
            self._queue(data)
            return
        keep = len(data) if self.native_limit is None else max(0, min(len(data), self.native_limit - self.kept))
        self.kept += keep
        self._hold(data[:keep])
        if self.native_limit is not None and self.kept >= self.native_limit:
            self._release()
        if keep < len(data):
            # Keep decoder padding available to the existing resampler; the output limit still removes it.
            self._queue(data[keep:])

    def _hold(self, data):
        import numpy as np
        if not len(data):
            return
        if self.tail is not None:
            data = np.concatenate((self.tail, data))
        if len(data) > SEAM_TAIL:
            self._queue(data[:-SEAM_TAIL])
        self.tail = data[-SEAM_TAIL:].copy()

    def _release(self):
        tail = self.tail
        if tail is not None and len(tail) == SEAM_TAIL and self.following is not None:
            try:
                head = _audio_head(self.following, self.cancelled)
                if head is not None:
                    lead = self.read_lead(self.following, previous=self.source) if self.read_lead is not None else None
                    if lead is not None:
                        repaired_tail, repaired_head = tail.copy(), head.copy()
                        if self.apply_lead(repaired_tail, repaired_head, lead):
                            tail = repaired_tail
                            self.next_head = self.following, repaired_head
                            self.seams_repaired += 1
                        else:
                            self._warn()
            except JoinCancelled:
                raise
            except Exception:
                self._warn()
        if tail is not None:
            self._queue(tail)
        self.tail, self.released = None, True

    def _warn(self):
        if not self.warned:
            logging.warning('[FreeVideo] A chain sound seam could not be repaired; the blocks are joined as they are')
            self.warned = True

    def _queue(self, data):
        import av
        import numpy as np
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data.T), format='fltp', layout='stereo')
        frame.sample_rate = 32000
        self._encode(self.resampler.resample(frame))

    def _encode(self, frames):
        self.heard, self.written = _encode(self.output, self.stream, self.fifo, frames, self.heard, self.limit,
                                           self.written, self.rate, self.layout, shaper=self.shaper)

    def end(self):
        if self.native is not None:
            for frame in self.native.resample(None):
                self._native(frame)
            if not self.released:
                self._release()
        self._encode(self.resampler.resample(None))

    def finish(self):
        return _encode(self.output, self.stream, self.fifo, [], 0, None,
                       self.written, self.rate, self.layout, final=True, shaper=self.shaper)[1]


def _audio_head(source, cancelled):
    """Decode just the next block's kept native head, without changing its files."""
    import av
    import numpy as np
    _check(cancelled)
    with av.open(source) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        if stream.codec_context.sample_rate != 32000 or stream.codec_context.layout.name != 'stereo':
            return None
        if stream.duration is not None and round(stream.duration * stream.time_base * 32000) < SEAM_HEAD:
            return None
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=32000)
        chunks, count = [], 0
        for packet in container.demux(stream):
            _check(cancelled)
            if packet.dts is None:
                continue
            for decoded in packet.decode():
                for frame in resampler.resample(decoded):
                    keep = min(frame.samples, SEAM_HEAD - count)
                    chunks.append(frame.to_ndarray()[:, :keep].T.copy())
                    count += keep
                    if count == SEAM_HEAD:
                        return np.ascontiguousarray(np.concatenate(chunks), dtype=np.float32)
    return None


def _encode(output, stream, fifo, resampled, heard, limit, written, rate, layout, final=False, shaper=None):
    """Queue one block's resampled sound up to its length and encode whole encoder frames; `final` writes the rest."""
    import av
    import numpy as np

    def queue(data):
        if data is not None:
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data), format='fltp', layout=layout)
            frame.sample_rate = rate
            fifo.write(frame)
    for frame in resampled:
        keep = frame.samples if limit is None else min(frame.samples, limit - heard)
        if keep <= 0:
            continue
        if keep < frame.samples:
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(frame.to_ndarray()[:, :keep]),
                                               format='fltp', layout=layout)
            frame.sample_rate = rate
        heard += keep
        frame.pts = None  # the queue counts samples itself: blocks follow one another without gaps
        if shaper is not None:
            queue(_voice_step(shaper.push, frame.to_ndarray()))
        else:
            fifo.write(frame)
    if final and shaper is not None:
        queue(_voice_step(shaper.flush))
    size = stream.codec_context.frame_size or 1024
    while fifo.samples >= size or final and fifo.samples:
        frame = fifo.read(min(size, fifo.samples))
        frame.pts, frame.sample_rate, frame.time_base = written, rate, Fraction(1, rate)
        written += frame.samples
        for packet in stream.encode(frame):
            output.mux(packet)
    return heard, written


def _request_seconds(sources):
    total = 0.
    for source in sources:
        try:
            value = json.loads(source.with_suffix('.request.json').read_text(encoding='utf-8')).get('request_seconds')
        except (OSError, ValueError, AttributeError):
            return None
        if type(value) not in (int, float) or not 0 <= value < float('inf'):
            return None
        total += value
    return total


def _replace_target(root, replace, identity, sources):
    from .comfy_library import _video, _report
    try:
        if not isinstance(replace, str) or not replace.startswith('FreeVideo/') or not replace.endswith('/video.mp4'):
            return None
        target = _video(root, replace[len('FreeVideo/'):-len('/video.mp4')])
        if target != root / replace:
            return None
        report = _report(target)
        if target.is_file() and target not in sources and report.get('chain_join', {}).get('chain') == identity:
            return target
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def join_outputs(outputs, identity, cancelled=None, frames=None, replace=None, leads=None, expected_seams=None,
                 speaking=None, *, original_audio=None):
    """Join relative block outputs into a saved video, replacing only this chain's earlier join."""
    import folder_paths
    root = Path(folder_paths.get_output_directory()).resolve()
    sources = []
    for output in outputs:
        if not isinstance(output, str) or not output:
            raise ValueError('A block has no video')
        source = (root / output).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError('A block video is missing')
        sources.append(source)
    if leads is not None and len(set(sources)) != len(sources):
        # The chain's seam sounds are looked up per video; one video in two places would take the wrong one.
        raise ValueError('Two blocks share one video')
    # A saved video is a folder the library lists once its report says it is complete.
    target = _replace_target(root, replace, identity, sources)
    replacing = target is not None
    folder = target.parent if replacing else root / 'FreeVideo' / datetime.now().strftime('%Y-%m-%d') / uuid.uuid4().hex
    target = folder / 'video.mp4'
    staged = target.with_name('.join-result-' + uuid.uuid4().hex + '.mp4') if replacing else target
    path = target.with_suffix('.request.json')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
    backup = target.with_name('.join-backup-' + uuid.uuid4().hex + '.mp4') if replacing else None
    try:
        kept = None
        if leads is not None:
            # The chain asks for its own output strings, never a path rebuilt from the disk.
            originals = {str(source): output for source, output in zip(sources, outputs)}
            def kept(following, previous):
                return leads(originals.get(str(following)), originals.get(str(previous)) if previous is not None else None)
        joined = join_files(sources, staged, cancelled, frames, leads=kept, expected=expected_seams, speaking=speaking,
                            **({'original_audio': original_audio} if original_audio is not None else {}))
        # The original selection replaces every block's sound, so there are no seam sounds to repair.
        if original_audio is None and expected_seams is not None and joined['seams_repaired'] < expected_seams:
            # A seam sound left unrepaired is never reported as a finished video.
            raise ValueError('Only %d of %d seam sounds could be repaired' % (joined['seams_repaired'], expected_seams))
        report = dict(success=True, geometry=dict(width=joined['width'], height=joined['height'], frames=joined['frames'],
                                                  fps=FPS, seconds=joined['frames'] / FPS),
                      request_seconds=_request_seconds(sources), video={}, encoding={},
                      chain_join=dict(chain=identity, blocks=len(sources), seams_repaired=joined['seams_repaired'],
                                      exact_end=joined['exact_end']))
        if expected_seams is not None:
            report['chain_join']['seams_expected'] = expected_seams
        if joined.get('voice') is not None:
            report['chain_join']['voice'] = joined['voice']
        if original_audio is not None:
            report['video']['original_audio'] = joined['original_audio']
            report['geometry']['seconds'] = joined['original_audio']['duration_seconds']
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        _check(cancelled)
        if replacing:
            try:
                os.link(target, backup)
            except OSError:
                shutil.copyfile(target, backup)
            _check(cancelled)
            os.replace(staged, target)
        try:
            os.replace(temporary, path)
        except BaseException:
            if replacing:
                os.replace(backup, target)
            raise
        if replacing:
            try:
                target.with_suffix('.thumbnail.jpg').unlink(missing_ok=True)
            except OSError:
                pass
    except BaseException:
        if not replacing:
            for name in ('video.mp4', 'video.request.json'):
                (folder / name).unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if replacing:
            staged.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
    return target.relative_to(root).as_posix()
