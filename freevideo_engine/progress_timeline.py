"""Bounded, text-free UI progress receipts. No ComfyUI, Torch or device imports.

The allowlists are deliberately checked in, rather than learned from messages.
Sources: engine phase/stage/timing_phase literals; comfy_bridge encoder/event
label tables and send_progress; comfy_nodes; sampling_progress; decode/media
phase labels; web/generation_progress.js encodingLabel/stageLabel translation
keys. Retry kinds originate in adaptive.py and warnings in engine event codes.
"""
from copy import deepcopy
import json
import math
import re
import time


PHASES = frozenset((
    'Decoding video tiles',
    'Encoding image/video references',
    'Encoding input media',
    'Encoding reference audio',
    'Load cached blocks and upload resident weights',
    'Loading and decoding audio',
    'Loading native MPS model',
    'Loading video VAE',
    'Preparing offloaded weight storage',
    'Saving MP4 and audio',
    'backend',
    'cancelled',
    'cancelling',
    'complete',
    'configuration',
    'connect',
    'decode',
    'dependencies',
    'download',
    'downloading',
    'encoder_import',
    'encoder_oom',
    'encoding',
    'engine',
    'failed',
    'finishing',
    'idle-encoder-load',
    'keyframe_vae',
    'load',
    'loading',
    'lora',
    'other',
    'paused',
    'plan',
    'preflight',
    'read_source_weights',
    'ready',
    'reconstruct',
    'recovery',
    'sample',
    'sample_finalize',
    'sampling',
    'starting',
    'switch',
    'transfer_complete',
    'unknown',
    'verify',
    'verify_cache',
    'verifying',
    'video',
    'waiting',
    'warmup',
))

STAGES = frozenset((
    'after-encoding',
    'before-encoding',
    'between_steps',
    'comfy',
    'complete',
    'encode',
    'encoder_compute',
    'encoder_load',
    'encoder_save',
    'encoder_tokenize',
    'encoding',
    'first_pass_reused',
    'gpu',
    'imports',
    'inputs',
    'installation',
    'latent_save',
    'latent_upscale',
    'latent_validation',
    'layers',
    'media_vae',
    'offload_release',
    'other',
    'other_or_unmeasured',
    'preset_download',
    'preview_mismatch',
    'reference_download',
    'sources_download',
    'starting',
    'transformer_release',
    'verify',
    'video',
))

TIMING_PHASES = frozenset((
    'decode',
    'encoding',
    'load',
    'other',
    'sampling',
))

LABEL_KEYS = frozenset((
    '#',
    'Adjusting memory placement and retrying',
    'Check local models',
    'Checking completed sampling',
    'Checking saved video',
    'Checking text encoder cache',
    'ComfyUI',
    'Compatibility level # · smaller work groups',
    'Continuing from the preview',
    'Copy local model',
    'Decoding video and audio',
    'Decoding video tiles',
    'Downloading extra model weights (one-time download)',
    'Encoding image/video references',
    'Encoding input media',
    'Encoding long references in smaller blocks',
    'Encoding prompt',
    'Encoding reference audio',
    'Encoding reference media',
    'Encoding text and images',
    'Existing models checked',
    'Find existing models',
    'First-pass preview · # × # · # steps',
    'GPU peak (GiB)',
    'Generating video',
    'Generation cancelled',
    'Generation stopped',
    'History forecast',
    'HF Mirror',
    'Hugging Face',
    'Importing offline packages',
    'Install FreeVideo workflow',
    'Installing sampling cache',
    'Leaving more GPU memory for text encoding',
    'Load cached blocks and upload resident weights',
    'Loading LoRAs',
    'Loading LoRAs · # / #',
    'Loading and decoding audio',
    'Loading cached video model',
    'Loading cached video model · # / # blocks',
    'Loading native MPS model',
    'Loading native MPS model · # / # blocks',
    'Loading text encoder',
    'Loading text encoder onto GPU',
    'Loading video VAE',
    'Loading video model',
    'Loading video model · # / # blocks',
    'Low-memory mode for this GPU',
    'ModelScope',
    'Open ComfyUI',
    'Paused; existing data kept',
    'Peak disk space',
    'Prepare ComfyUI',
    'Prepare ComfyUI environment',
    'Prepare FreeVideo',
    'Prepare download tools',
    'Preparing # s video + audio · # × #',
    'Preparing offloaded weight storage',
    'Preparing offloaded weight storage · # / # blocks',
    'Preparing prompt data',
    'Preparing reference media',
    'Preparing reference media resources',
    'Preparing sampling preset',
    'Preparing text and image tokens',
    'Preparing text encoder GPU',
    'Preparing text encoder model',
    'Preparing text encoding',
    'Preparing two-pass upscaler',
    'Preparing video',
    'Preparing video decoding',
    'Preview saved',
    'Prompt ready',
    'RAM measurement',
    'RAM peak (GiB)',
    'Reading text encoder weights',
    'Ready',
    'Releasing encoder weights after insufficient GPU memory',
    'Releasing idle models and checking available memory again',
    'Releasing sampling buffers',
    'Restart ComfyUI',
    'Restart explicitly allowed',
    'Resource forecast ready',
    'Resume unavailable; checking another source',
    'Retaining and checking input media',
    'Retrying same source',
    'Retrying same source with configured proxy',
    'Retrying same source with direct connection',
    'Retrying text encoding with more GPU workspace',
    'Reused previous result',
    'Reuse local model',
    'Reusing completed sampling · retrying video and audio decoding',
    'Reusing prompt cache',
    'Reusing text encoder',
    'Reusing the completed first pass',
    'Sampling',
    'Sampling # / #',
    'Sampling # / # · #%',
    'Sampling # / # · ~#%',
    'Saving MP# and audio',
    'Saving completed sampling',
    'Saving prompt cache',
    'Single-pass · # steps',
    'Start ComfyUI',
    'Starting ComfyUI',
    'Starting text encoder',
    'System available minimum (GiB)',
    'Test GPU acceleration',
    'The preview no longer matches; sampling its first pass again',
    'Two-pass · # × # → # × # · # + # steps',
    'Update ComfyUI packages',
    'Upscaling before the second pass',
    'Using text encoder already on GPU',
    'Verify / reuse local models',
    'Verify bundled files',
    'Verify local model',
    'Video + audio saved',
    'Video model ready',
    'Video saved',
    'compute_device',
    'other',
    'root',
    'your Hub endpoint',
    'Checking configuration',
    'Checking packages',
    'Downloading models',
    '下载模型',
    '导入离线包',
    '检查配套包',
    '正在检查配置',
    'Decoding the video',
    'Encoding long references',
    'Loading the video model',
    'Preparing the prompt and images',
    'Starting generation process',
))

UNITS = frozenset((
    'bytes',
    'frames',
    'items',
    'other',
    'seconds',
    'steps',
))

RETRY_KINDS = frozenset((
    'cancelled',
    'code_error',
    'cuda_error',
    'gpu_oom',
    'memory_monitor_unavailable',
    'memory_pressure',
    'other',
    'ram_pressure',
    'shared_memory_spill',
    'timeout',
    'unknown_worker_exit',
    'worker_stop_unconfirmed',
))

STATUSES = frozenset((
    'cancelled',
    'complete',
    'failed',
    'other',
    'running',
))

WARNINGS = frozenset((
    'other',
    'ram_budget_warning',
    'unified_memory_tight',
))

SEGMENT_KEYS = ('phase', 'stage', 'timing_phase', 'label_key')
ENUMS = dict(phase=PHASES, stage=STAGES, timing_phase=TIMING_PHASES,
             label_key=LABEL_KEYS, unit=UNITS, warning=WARNINGS)
OVERALL_ENUMS = dict(status=STATUSES)
RETRY_ENUMS = dict(kind=RETRY_KINDS, failed_phase=PHASES)
COUNTERS = frozenset(('done', 'total', 'block', 'blocks', 'reference_trimmed'))
FLAGS = frozenset(('low_memory', 'kernel_cache_note', 'estimated', 'new_request', 'reset', 'result'))
ONE_SHOT = frozenset(('new_request', 'reset', 'result', 'warning'))
SECONDS = frozenset(('step_elapsed_seconds', 'estimated_step_seconds', 'measured_age_seconds'))
FRACTIONS = frozenset(('display_fraction',))
OVERALL_SECONDS = frozenset(('remaining_seconds', 'phase_seconds', 'remaining_floor_seconds',
                             'elapsed_seconds', 'phase_elapsed_seconds'))
OVERALL_FRACTIONS = frozenset(('fraction', 'phase_start_fraction', 'phase_weight'))
SCOPE = ('Events are sanitized actual UI rows after publish applies retry carry-over and warning overlays; '
         'low_memory and reference_trimmed use the UI row when present, otherwise the current input; '
         'delta fields and overall subkeys inherit, null clears a field, retry is replaced as a whole, '
         'and new_request/reset/result/warning belong only to their event; '
         'the first event and every segment start contain full states; '
         't is seconds since the first recorded event, and measured_age_seconds is how old a warning overlay\'s inherited row was; heartbeats are counted but omitted without changing sampling; '
         'truncated means mandatory events exhausted the capacity, later events were dropped except terminal events, which may replace retained events; '
         'the report is written before the node final event, '
         'so use bridge.bridge_seconds for the end time.')
MAX_EVENTS = 5000
MAX_BYTES = 160_000
# Everything but the events, as monitoring.save writes it (indent=2, nested in
# the report), with the widest counters, plus the summary and analysis counts.
METADATA_BYTES = len(json.dumps(dict(progress_timeline=dict(
    version=1, encoding='delta', events=[], dropped=10**12, heartbeats=10**12, errors=10**12,
    truncated=False, scope=SCOPE)), indent=2)) + 512
_NUMBERS = re.compile(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?')


def label_key(value):
    """Never retain unmatched text, including device names in known prefixes."""
    if not isinstance(value, str):
        return 'other'
    if value.startswith('Compute device ·') or value == 'Compute device':
        return 'compute_device'
    key = _NUMBERS.sub('#', value)
    return key if key in LABEL_KEYS else 'other'


def _number(value, digits=1, significant=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid progress number')
    result = float(format(value, '.3g')) if significant else round(value, digits)
    if not math.isfinite(result):
        raise ValueError('Invalid rounded progress number')
    return result


def _fields(value, *, strict=False, nested=None):
    """One schema for ingestion and the final report boundary; no arbitrary keys."""
    if not isinstance(value, dict):
        return {}, 1
    result, errors = {}, 0
    enums = ENUMS if nested is None else OVERALL_ENUMS if nested == 'overall' else RETRY_ENUMS
    for key, raw in value.items():
        known = (key in enums or
                 (nested is None and (key in COUNTERS or key in FLAGS or key in SECONDS or key in FRACTIONS
                                      or key in ('t', 'bytes_per_second', 'overall', 'retry'))) or
                 (nested == 'overall' and (key in OVERALL_SECONDS or key in OVERALL_FRACTIONS
                                          or key in ('estimated', 'sampling_time_weighted'))) or
                 (nested == 'retry' and key in ('attempt', 'max_attempts', 'reuse_sampling')))
        if not known:
            # Heartbeat and the bridge's extra overall fields are intentionally omitted.
            if strict:
                errors += 1
            continue
        try:
            if raw is None:
                result[key] = None
            elif key in enums:
                if not isinstance(raw, str):
                    raise ValueError('Invalid progress code')
                if strict and raw not in enums[key]:
                    raise ValueError('Unknown progress code')
                result[key] = raw if raw in enums[key] else 'other'
            elif key in ('overall', 'retry') and nested is None:
                if not isinstance(raw, dict):
                    raise ValueError('Invalid progress mapping')
                result[key], count = _fields(raw, strict=strict, nested=key)
                errors += count
            elif (nested is None and key in FLAGS) or key in ('estimated', 'reuse_sampling', 'sampling_time_weighted'):
                if (strict or key == 'sampling_time_weighted') and type(raw) is not bool:
                    raise ValueError('Invalid progress flag')
                result[key] = bool(raw)
            elif (nested is None and key in COUNTERS) or key in ('attempt', 'max_attempts'):
                if type(raw) is not int or raw < 0:
                    raise ValueError('Invalid progress counter')
                result[key] = raw
            else:
                result[key] = _number(raw, 4 if key in OVERALL_FRACTIONS or key in FRACTIONS else 1,
                                      significant=key == 'bytes_per_second')
        except (ValueError, TypeError, OverflowError):
            errors += 1
    return result, errors


def sanitize(message):
    """Clean supplied presentation fields without inheriting any prior state."""
    supplied = {key: message[key] for key in (*ENUMS, *COUNTERS, *FLAGS, *SECONDS, *FRACTIONS,
                                             'overall', 'retry', 'bytes_per_second')
                if key in message}
    if 'label' in message:
        supplied['label_key'] = label_key(message['label'])
    for key in FLAGS & supplied.keys():
        if supplied[key] is not None:
            supplied[key] = bool(supplied[key])
    # A single bridge reference_trimmed mapping describes one reference, not four fields.
    if 'reference_trimmed' in supplied:
        raw = supplied['reference_trimmed']
        supplied['reference_trimmed'] = (raw if type(raw) is int and raw >= 0 else
                                        len(raw) if isinstance(raw, (list, tuple)) else
                                        1 if raw is not None else 0)
    clean, errors = _fields(supplied)
    # Null is the wire's removal operation, not a retained state value.
    clean = {key: ({subkey: item for subkey, item in value.items() if item is not None}
                   if isinstance(value, dict) else value)
             for key, value in clean.items() if value is not None}
    return clean, errors


def _segment(event):
    return tuple(event.get(key) for key in SEGMENT_KEYS)


def _delta(event, previous):
    if previous is None:
        return dict(event)
    full = _segment(event) != _segment(previous)
    delta = {key: None for key in previous if key not in event and key not in ONE_SHOT}
    for key, value in event.items():
        if full or key == 't' or key in ONE_SHOT or key not in previous:
            delta[key] = value
        elif key == 'overall' and isinstance(previous[key], dict):
            changed = {subkey: None for subkey in previous[key] if subkey not in value}
            changed.update((subkey, item) for subkey, item in value.items()
                           if subkey not in previous[key] or item != previous[key][subkey])
            if changed:
                delta[key] = changed
        elif value != previous[key]:
            delta[key] = value
    return delta


def decode(events):
    """Restore complete states; a segment's first event can be read independently."""
    result, state = [], {}
    for event in events:
        # Segment keys are omitted within a segment. A changed (including
        # cleared) key identifies a full row, whose overall is also complete.
        if any(key in event and event[key] != state.get(key) for key in SEGMENT_KEYS):
            state = {}
        for key in ONE_SHOT:
            state.pop(key, None)
        for key, value in event.items():
            if value is None:
                state.pop(key, None)
            elif key == 'overall':
                overall = state.setdefault(key, {})
                for subkey, item in value.items():
                    if item is None:
                        overall.pop(subkey, None)
                    else:
                        overall[subkey] = deepcopy(item)
            else:
                state[key] = deepcopy(value)
        result.append(deepcopy(state))
    return result


def _event_bytes(event, previous):
    # monitoring.save uses indent=2; payload.progress_timeline.events adds six
    # spaces to every event line. Default ASCII escaping is conservative for
    # the fixed Unicode labels saved as UTF-8. Include a comma and newline.
    encoded = json.dumps(_delta(event, previous), indent=2)
    return len(encoded) + 6 * (encoded.count('\n') + 1) + 2


def validate(payload):
    """Revalidate the receipt before report redaction, discarding invalid values."""
    if not isinstance(payload, dict):
        return None
    errors = 0
    counters = {}
    for key in ('dropped', 'heartbeats', 'errors'):
        value = payload.get(key, 0)
        if type(value) is int and value >= 0:
            counters[key] = value
        else:
            counters[key] = 0
            errors += 1
    events = []
    raw = payload.get('events', [])
    if not isinstance(raw, list):
        raw = []
        errors += 1
    for event in raw:
        clean, count = _fields(event, strict=True)
        errors += count
        if type(clean.get('t')) not in (int, float):
            counters['dropped'] += 1
            errors += 1
            continue
        events.append(clean)
    truncated = payload.get('truncated', False)
    if type(truncated) is not bool:
        truncated = False
        errors += 1
    counters['errors'] += errors
    return dict(version=1, encoding='delta', events=events, **counters, truncated=truncated, scope=SCOPE)


def _terminal(event):
    overall = event.get('overall')
    return 'result' in event or (isinstance(overall, dict)
        and overall.get('status') in ('failed', 'cancelled', 'complete'))


class Timeline:
    """Full states internally, online sampling, delta only at the output boundary.

    Rows carry a preservation flag and the unrounded sampling clock. Compaction
    costs O(retained rows), paid for by the rows it discards; normal records touch
    a bounded schema and the last row only. The caller supplies synchronization.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = None
        self.state = {}
        self.rows = []
        self.pending = None
        self.interval = .5
        self.dropped = self.heartbeats = self.errors = 0
        self.report_id = None
        self._bytes = 0
        self._counted = False
        self._last_count = None
        self.truncated = False
        self._saturated = False

    def record(self, value, now=None, *, message=None, measured=None):
        """Record the actual publish row, falling back to input-only metadata.

        Direct callers supply an already resolved UI row as value. The original
        message controls heartbeat detection and one-shot marker presence: a
        warning can inherit a heartbeat's UI row without itself being one.
        measured is when the row's values were measured: a warning overlay keeps
        the earlier row's time, and the page ages its remaining time from it.
        """
        message = value if message is None else message
        overall = message.get('overall')
        if message.get('heartbeat') is True or isinstance(overall, dict) and overall.get('heartbeat') is True:
            self.heartbeats += 1
            return
        if self._saturated and not _terminal(value):
            self.dropped += 1
            return
        now = self.clock() if now is None else now
        if self.started is None:
            self.started = now
        supplied = dict(value)
        for key in ONE_SHOT:
            if key not in message:
                supplied.pop(key, None)
        for key in ('low_memory', 'reference_trimmed'):
            if key not in supplied and key in message:
                supplied[key] = message[key]
        clean, errors = sanitize(supplied)
        self.errors += errors
        event = dict(clean, t=_number(max(0., now - self.started)))
        age = round(now - measured, 1) if type(measured) in (int, float) and math.isfinite(measured) else 0.
        if age > 0:
            event['measured_age_seconds'] = _number(age)
        changed = not self.rows or _segment(event) != _segment(self.state)
        if changed:
            if self._last_count is not None:
                self._last_count[1] = True
            self._last_count = None
            self._counted = False
        counted = any(type(event.get(key)) is int for key in ('done', 'total', 'block', 'blocks'))
        first_count = counted and not self._counted
        self._counted |= counted
        mandatory = changed or first_count or any(key in message for key in ('retry', 'new_request', 'reset', 'result', 'warning'))
        mandatory |= event.get('phase') in ('failed', 'cancelled', 'complete') or (isinstance(event.get('overall'), dict)
                        and event['overall'].get('status') in ('failed', 'cancelled', 'complete'))
        self.state = event
        row = [event, mandatory, now]
        if counted:
            self._last_count = row
        elif self.pending is not None and self.pending is self._last_count:
            # An uncounted update must not supersede the segment's last count.
            self._flush()
        if mandatory:
            self._flush(last=changed)
            if changed and self.rows:
                self.rows[-1][1] = True
            self._append(row)
        elif now - self.rows[-1][2] >= self.interval:
            # The pending update is superseded by this sample at the interval boundary.
            if self.pending is not None:
                self.dropped += 1
                self.pending = None
            self._append(row)
        else:
            if self.pending is not None:
                self.dropped += 1
            self.pending = row

    def _flush(self, last=False):
        if self.pending is not None:
            row = self.pending
            self.pending = None
            if last:
                promoted = [row[0], True, row[2]]
                if self._last_count is row:
                    self._last_count = promoted
                row = promoted
            self._append(row)

    def _append(self, row):
        if self._saturated and not _terminal(row[0]):
            self.dropped += 1
            return
        previous = self.rows[-1][0] if self.rows else None
        self._bytes += _event_bytes(row[0], previous)
        self.rows.append(row)
        if self._over_capacity():
            self._compact()
            # Mandatory-only receipts cannot be thinned. Stop accepting ordinary
            # events, but always make room for the newest terminal event.
            terminal = _terminal(row[0])
            while self._over_capacity():
                index = len(self.rows) - 1
                if terminal:
                    index = next((i for i in range(len(self.rows) - 2, -1, -1)
                                  if not _terminal(self.rows[i][0])), len(self.rows) - 2)
                self._remove(index)

    def _over_capacity(self):
        return len(self.rows) > MAX_EVENTS or self._bytes > MAX_BYTES - METADATA_BYTES

    def _remove(self, index):
        previous = self.rows[index - 1][0] if index else None
        event = self.rows[index][0]
        self._bytes -= _event_bytes(event, previous)
        if index + 1 < len(self.rows):
            following = self.rows[index + 1][0]
            self._bytes += _event_bytes(following, previous) - _event_bytes(following, event)
        self.rows.pop(index)
        self.dropped += 1

    def _compact(self):
        if self._saturated:
            return
        while self._over_capacity():
            optional = [index for index, row in enumerate(self.rows[:-1])
                        if not row[1] and row is not self._last_count]
            if not optional:
                self.truncated = self._saturated = True
                break
            remove = set(optional[::2])
            self.rows = [row for index, row in enumerate(self.rows) if index not in remove]
            self.dropped += len(remove)
            self.interval *= 2
            self._bytes = 0
            previous = None
            for event, _, _ in self.rows:
                self._bytes += _event_bytes(event, previous)
                previous = event

    def snapshot(self):
        # Flush on a copy: a live download must not influence future sampling.
        view = Timeline(self.clock)
        view.rows = list(self.rows)
        view.pending = self.pending
        view.interval = self.interval
        view.dropped, view.heartbeats, view.errors = self.dropped, self.heartbeats, self.errors
        view._bytes = self._bytes
        view._last_count = self._last_count
        view.truncated, view._saturated = self.truncated, self._saturated
        view._flush(last=True)
        view._compact()
        events, previous = [], None
        for event, _, _ in view.rows:
            events.append(deepcopy(_delta(event, previous)))
            previous = event
        return dict(version=1, encoding='delta', events=events, dropped=view.dropped,
                    heartbeats=view.heartbeats, errors=view.errors, truncated=view.truncated, scope=SCOPE)
