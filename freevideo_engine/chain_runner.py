"""Durable chains, submitting ordinary host prompts by dependency."""
from copy import deepcopy
import json
import logging
import math
from pathlib import Path, PurePosixPath
import random
import re
import threading
import time
import uuid

from . import chain_leads, chain_plan
from . import chain_store as store

REPORT_ID = re.compile(r'[a-f0-9]{32}')


def _split_value(record, text):
    """What the prompt model sorts into parts: the whole description and each part's new seconds."""
    first = next(row for row in record['blocks'] if row['index'] == 0)
    return dict(text=text, continued=first['after'] is not None,
                seconds=[row['new_frames'] / chain_plan.FPS for row in record['blocks']])


def _ordered_texts(record, value):
    """Part texts when the description could not be split: its sentences in order, with the LoRA trigger
    words every part carries, or None for a description without sentences."""
    from .lora_triggers import apply_chain_triggers
    from .prompt_vlm.rules import order_split
    if not isinstance(value['text'], str):
        return None
    try:
        probe = dict(snapshot=deepcopy(record['snapshot']), node=record['node'],
                     blocks=[dict(inputs=dict(text=text)) for text in order_split(value)])
        apply_chain_triggers(probe)
    except Exception:
        return None
    return [row['inputs']['text'] for row in probe['blocks']]

class ChainBusy(ValueError):
    pass


class ChainTooLong(ValueError):
    """A chain that would continue a video through more parts than a video may have."""
    code = 'chain_too_long'

    def __init__(self, parts, limit):
        super().__init__('One continuous shot can have up to %d parts. Make it shorter.' % limit)
        self.parts, self.limit = parts, limit
        self.message_zh = '一个连续镜头最多 %d 段，请缩短。' % limit


class ChainShortPartsTooMany(ChainTooLong):
    """A video with a short continuing part, continued through more parts than such a video may have."""
    code = 'chain_short_parts'

    def __init__(self, parts, limit):
        full = chain_plan.FULL_PART_SECONDS
        ValueError.__init__(self, 'With a part shorter than %d seconds, one continuous shot can have up to %d parts. '
                            'Make every part at least %d seconds long, or use fewer parts.' % (full, limit, full))
        self.parts, self.limit = parts, limit
        self.message_zh = '有短于 %d 秒的段时，一个连续镜头最多 %d 段。请把每段加长到至少 %d 秒，或减少段数。' % (full, limit, full)


class ChainPartTooShort(ValueError):
    """A continuing part shorter than a continued video may have."""
    code = 'chain_part_too_short'

    def __init__(self, seconds, limit):
        super().__init__('Each continued part must be at least %d seconds. Make it longer.' % limit)
        self.seconds, self.limit = seconds, limit
        self.message_zh = '接着生成的每一段至少 %d 秒，请加长这一段。' % limit


def _work_key(path):
    return PurePosixPath(str(path).replace('\\', '/')).as_posix()


def _number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def node_prompt(prompt, node):
    """Copy one node and its upstream inputs, leaving validation to the host."""
    pending, selected = [node], set()
    while pending:
        current = pending.pop()
        if current in selected:
            continue
        selected.add(current)
        row = prompt[current]
        inputs = row.get('inputs') if isinstance(row, dict) else None
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if (isinstance(value, list) and len(value) == 2 and type(value[0]) in (str, int)
                    and value[0] in prompt and type(value[1]) is int):
                pending.append(value[0])
    return deepcopy({key: row for key, row in prompt.items() if key in selected})


class ChainRunner:
    def __init__(self, store_dir, host, clock=time.monotonic, wall=time.time, estimate=None, poll=0.5, inputs=None,
                 max_parts=None, min_part_frames=None, seeds=None, full_part_frames=None):
        self.store_dir, self.host = store_dir, host
        self.seeds = seeds or random.SystemRandom()
        self.max_parts = max_parts or store.MAX_PARTS
        self.min_part_frames = chain_plan.MIN_PART_FRAMES if min_part_frames is None else min_part_frames
        # Continuing parts below this are short and hold their video to fewer parts. A runner given a lower
        # shortest part (a test host) lowers this with it unless it is given too.
        self.full_part_frames = (full_part_frames if full_part_frames is not None else chain_plan.FULL_PART_FRAMES
                                 if min_part_frames is None else min(min_part_frames, chain_plan.FULL_PART_FRAMES))
        self.clock, self.wall, self.estimate, self.poll = clock, wall, estimate, poll
        self.inputs = inputs or host.inputs
        self.lock = threading.RLock()
        self.step_lock = threading.Lock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.records = {row['id']: row for row in store.load_all(store_dir)}
        chain_leads.prune(store_dir, self.records)
        self.starts, self.success, self.cancel_sent, self.progress_values = {}, {}, set(), {}
        self.progress_messages = {}
        self.estimates, self.queue, self.active = {}, [], None
        self.concurrent = bool(getattr(host, 'concurrent', False))
        if self.concurrent:
            self.active = set()
        self.recover = set()
        for record in self.records.values():
            if record['state'] in store.ACTIVE:
                record['state'] = 'interrupted'
                if record['output_state'] == 'joining':
                    record['output_state'] = 'done' if record['output'] else 'pending'
                for block in record['blocks']:
                    if block['state'] in ('queued', 'running'):
                        block['state'] = 'interrupted'
                self._save(record)
            if record['state'] == 'interrupted' and record['join'] and record['output_state'] != 'failed':
                self.recover.add(record['id'])
        self.thread = threading.Thread(target=self._work, name='FreeVideo chain', daemon=True)
        self.thread.start()

    def _save(self, record):
        record['updated'] = self.wall()
        store.save(self.store_dir, record)

    def _current(self, record):
        executing = next((row for row in record['blocks'] if row['state'] in ('queued', 'running')), None)
        if executing is not None:
            return executing
        done = {row['id'] for row in record['blocks'] if row['state'] == 'done'}
        return next((row for row in record['blocks'] if row['state'] == 'pending'
                     and all(not isinstance(row[key], str) or row[key] in done
                             for key in ('after', 'before'))), None)

    def _sources(self, record, block):
        sources = {}
        for key in ('after', 'before'):
            value = block[key]
            if isinstance(value, str):
                source = next(row for row in record['blocks'] if row['id'] == value)
                sources[key] = dict(block=value, output=source['output'])
            elif isinstance(value, dict) and 'chain' in value:
                source = self._referenced_block(value)
                sources[key] = dict(work=source['output'], output=source['output'])
            elif value is not None:
                sources[key] = dict(work=value['work'], output=value['work'])
            else:
                sources[key] = None
        return sources

    def _referenced_block(self, value):
        source = self.records.get(value['chain'])
        if source is None:
            # A recent dependent may outlive the list's bounded startup window.
            try:
                self.load(value['chain'])
            except KeyError:
                raise ValueError('Referenced chain does not exist') from None
            source = self.records[value['chain']]
        block = next((row for row in source['blocks'] if row['id'] == value['block']), None)
        if block is None:
            raise ValueError('Referenced chain block does not exist')
        return block

    def _dependency_state(self, record):
        waiting = False
        for block in record['blocks']:
            for key in ('after', 'before'):
                value = block[key]
                if not isinstance(value, dict) or 'chain' not in value:
                    continue
                try:
                    source = self._referenced_block(value)
                except ValueError:
                    return 'interrupted'
                parent = self.records[value['chain']]
                if parent['state'] in ('cancelling', 'cancelled', 'failed', 'interrupted'):
                    return 'interrupted'
                if parent['state'] == 'done':
                    if source['state'] != 'done' or not source['output']:
                        return 'interrupted'
                else:
                    waiting = True
        return 'queued' if waiting else 'ready'

    def _interrupt_dependants(self):
        # Repeat so a stopped ancestor reaches descendants in any receipt order.
        changed = True
        while changed:
            changed = False
            for waiting in list(self.records.values()):
                if waiting['state'] == 'queued' and self._dependency_state(waiting) == 'interrupted':
                    waiting['state'] = 'interrupted'
                    self._save(waiting)
                    changed = True

    def _elapsed(self, block):
        start = self.starts.get(block['prompt_id'])
        return max(0., self.clock() - start) if start is not None else 0.

    def _view(self, record, detailed=True, client_id=None):
        value = store.detail(record) if detailed else store.summary(record)
        block = self._current(record)
        elapsed = value['elapsed_seconds']
        if block is not None and block['state'] == 'running':
            elapsed += self._elapsed(block)
        remembered = self.progress_values.get(record['id'], {})
        value['progress'] = deepcopy(remembered | self.progress_messages.get(record['id'], {}))
        value['last_heartbeat_at'] = record.get('last_heartbeat_at')
        value['elapsed_seconds'] = max(elapsed, remembered.get('elapsed_seconds', 0.))
        value['joining'] = record['output_state'] == 'joining'
        if client_id is not None:
            # The owner's ID stays in the record: a page only learns whether a chain is its own.
            value['mine'] = record['client_id'] == client_id
        if record['state'] in store.ACTIVE and block is not None:
            value['current'] = block['id']
        waiting = self._waiting(record)
        if waiting is not None:
            value['waiting'] = waiting
        return value

    def _waiting(self, record):
        """What a chain that has not started waits for: how many chains are ahead and, for a single
        one, the time it has left (None while that is unknown)."""
        if record['state'] != 'queued' or record['id'] in self.active_ids() or record['id'] not in self.queue:
            return None
        ahead = self.active_ids()
        for identity in self.queue[:self.queue.index(record['id'])]:
            row = self.records.get(identity)
            if row is not None and row['state'] == 'queued' and identity not in ahead:
                ahead.append(identity)
        if not ahead:
            return None
        remaining = _number(self.progress_values.get(ahead[0], {}).get('remaining_seconds')) if len(ahead) == 1 and ahead[0] in self.active_ids() else None
        return {'ahead': len(ahead), 'remaining_seconds': None if remaining is None or remaining < 0 else remaining}

    def _depths(self, record, seen=()):
        """Parts each block of `record` continues through, itself included (a block with no source is the
        first part). A source that is an existing video counts the parts that video was made of, as far
        as this runner's records know them; one they do not know counts as one part."""
        return {name: parts for name, (parts, _) in self._lineage(record, seen).items()}

    def _lineage(self, record, seen=()):
        """For each block of `record`: its parts as `_depths` counts them, and whether any continuing part
        among them is short."""
        rows = {row['id']: row for row in record['blocks']}
        lineage, pending = {}, list(rows)
        while pending:
            rest = []
            for identity in pending:
                row = rows[identity]
                sources = [row[key] for key in ('after', 'before') if row[key] is not None]
                if any(isinstance(value, str) and value not in lineage for value in sources):
                    rest.append(identity)
                    continue
                found = [lineage[value] if isinstance(value, str) else self._source_lineage(value, seen + (record['id'],))
                         for value in sources]
                short = bool(sources) and row['inputs']['frames'] < self.full_part_frames
                lineage[identity] = (1 + sum(parts for parts, _ in found), short or any(flag for _, flag in found))
            if len(rest) == len(pending):
                raise ValueError('Block references cannot contain a cycle or self reference')
            pending = rest
        return lineage

    def _source_parts(self, value, seen):
        return self._source_lineage(value, seen)[0]

    def _source_lineage(self, value, seen):
        if 'chain' not in value:
            return self._work_lineage(value['work'], seen)
        self._referenced_block(value)
        if value['chain'] in seen:
            raise ValueError('Chain references cannot contain a cycle')
        return self._lineage(self.records[value['chain']], seen)[value['block']]

    def _work_parts(self, work, seen):
        return self._work_lineage(work, seen)[0]

    def _work_lineage(self, work, seen):
        key = _work_key(work)
        for record in list(self.records.values()):
            if record['id'] in seen:
                continue
            if record.get('output') and _work_key(record['output']) == key:
                names = [row['id'] for row in store.leading_blocks(record)[:record.get('output_blocks') or None]]
            else:
                names = [row['id'] for row in record['blocks'] if row['output'] and _work_key(row['output']) == key]
            if names:
                lineage = self._lineage(record, seen)
                return max(lineage[name][0] for name in names), any(lineage[name][1] for name in names)
        return 1, False

    def _check_parts(self, record):
        """Refuses, before anything is written or queued, a chain with a continuing part too short or a video
        continued through too many parts."""
        short = [row['inputs']['frames'] for row in record['blocks']
                 if (row['after'] is not None or row['before'] is not None)
                 and row['inputs']['frames'] < self.min_part_frames]
        if short:
            raise ChainPartTooShort(min(short) / chain_plan.FPS, chain_plan.MIN_PART_SECONDS)
        lineage = self._lineage(record).values()
        parts = max(parts for parts, _ in lineage)
        if parts > self.max_parts:
            raise ChainTooLong(parts, self.max_parts)
        inputs = record['snapshot']['prompt'][record['node']].get('inputs') or {}
        limit = chain_plan.parts_limit(True, inputs.get('first_frame_reference') is True, self.max_parts)
        parts = max((parts for parts, flag in lineage if flag), default=0)
        if parts > limit:
            raise ChainShortPartsTooMany(parts, limit)

    def create(self, payload):
        from .lora_triggers import apply_chain_triggers
        record = store.validate(payload)
        apply_chain_triggers(record)
        with self.lock:
            # Before anything is written or queued: a refused chain leaves every record and file as it was.
            self._check_parts(record)
            record['created'] = self.wall()
            self._save(record)
            self.records[record['id']] = record
            self.queue.append(record['id'])
            self.wake.set()
            return self._view(record)

    def cancel(self, chain_id):
        with self.lock:
            record = self.records[chain_id]
            if record['state'] in store.ACTIVE and record['state'] != 'cancelling':
                record['state'] = 'cancelling'
                self._save(record)
                self._interrupt_dependants()
                self.wake.set()
            return self._view(record)

    def set_text(self, chain_id, block_id, text):
        """The description of a part that has not started; the user's own words, used as written."""
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError('Text must be a nonempty string of at most 20000 characters')
        with self.lock:
            record = self.records[chain_id]
            block = next((row for row in record['blocks'] if row['id'] == block_id), None)
            if block is None:
                raise KeyError(block_id)
            if (record.get('split') or {}).get('state') in ('pending', 'running'):
                raise ChainBusy('The descriptions of the parts are not written yet')
            if block['state'] != 'pending' or block['prompt_id'] is not None:
                raise ChainBusy('This part has already started')
            previous = block['inputs']['text']
            block['inputs']['text'] = text
            try:
                from .lora_triggers import apply_chain_triggers
                apply_chain_triggers(record)
            except Exception:
                block['inputs']['text'] = previous
                raise ValueError('Text must be at most 20000 characters with LoRA triggers') from None
            self._save(record)
            return self._view(record)

    def resume(self, chain_id, client_id=None):
        with self.lock:
            record = self.records[chain_id]
            if record['state'] in store.ACTIVE or record['output_state'] == 'joining':
                raise ChainBusy('This chain is already active')
            if (record['state'] not in ('cancelled', 'failed', 'interrupted')
                    and not (record['state'] == 'done' and record['output_state'] == 'failed')):
                raise ValueError('This chain cannot be resumed')
            if client_id is not None and not isinstance(client_id, str):
                raise ValueError('Client ID must be a string')
            for block in record['blocks']:
                if block['state'] != 'done':
                    self.starts.pop(block['prompt_id'], None)
                    self.success.pop(block['prompt_id'], None)
                    self.cancel_sent.discard(block['prompt_id'])
                    block.update(state='pending', prompt_id=None, started=None, finished=None,
                                 seconds=None, output=None, report_id=None, error=None,
                                 fraction=0., remaining_seconds=None, continuation=None,
                                 skip_frames=0, skip_audio_samples=0)
            split = record.get('split')
            whole = record['snapshot']['prompt'][record['node']]['inputs'].get('text')
            if (split is not None and split['state'] == 'failed' and split['reason'] in ('busy', 'memory')
                    and all(row['state'] != 'done' for row in record['blocks'])
                    and [row['inputs']['text'] for row in record['blocks']] in (
                        [whole] * len(record['blocks']), _ordered_texts(record, _split_value(record, whole)))):
                # Busy and memory pass; nothing has started and no part was rewritten, so split again.
                for row in record['blocks']:
                    row['inputs']['text'] = whole
                split.update(state='pending', reason=None)
            if client_id is not None:
                record['client_id'] = client_id
            record['state'] = 'queued'
            if record['join']:
                record['output_error'] = None
                if all(row['state'] == 'done' for row in record['blocks']):
                    self._finish_blocks(record)
                elif record['output'] is None:
                    record.update(output_blocks=0, output_state='pending')
            self.recover.discard(chain_id)
            self.progress_messages.pop(chain_id, None)
            self.queue = [identity for identity in self.queue if identity != chain_id]
            self.queue.append(chain_id)
            self._save(record)
            self.wake.set()
            return self._view(record)

    def redo(self, chain_id, block_id, text=None, client_id=None):
        """A new chain that keeps the parts before `block_id` and makes that part and every later one
        again with new seeds. Later parts continue the part before them, so redoing one part alone would
        break the next seam. The original chain, its parts and its joined video stay as they are."""
        if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 20000):
            raise ValueError('Text must be a nonempty string of at most 20000 characters')
        if client_id is not None and not isinstance(client_id, str):
            raise ValueError('Client ID must be a string')
        from .lora_triggers import apply_chain_triggers
        with self.lock:
            source = self.records[chain_id]
            start = next((row for row in source['blocks'] if row['id'] == block_id), None)
            if start is None:
                raise KeyError(block_id)
            if source['state'] in store.ACTIVE or source['output_state'] == 'joining':
                raise ChainBusy('This chain is already active')
            kept = [row for row in source['blocks'] if row['index'] < start['index']]
            if any(row['state'] != 'done' or not row['output'] for row in kept):
                raise ValueError('The parts before this one are not finished')
            names = {row['id'] for row in kept}
            if any(isinstance(row[key], str) and row[key] not in names for row in kept for key in ('after', 'before')):
                raise ValueError('A kept part depends on a part that is made again')
            record = deepcopy(source)
            identity = source['id']
            while identity in self.records or (Path(self.store_dir) / (identity + '.json')).exists():
                identity = 'c-' + uuid.uuid4().hex[:12]
            for key in ('last_report_id', 'last_heartbeat_at', 'output_seams', 'join_final_state'):
                record.pop(key, None)
            now = self.wall()
            record.update(id=identity, created=now, state='queued', redo_of=dict(chain=chain_id, block=block_id),
                          output=None, output_state='pending' if record['join'] else 'none',
                          output_error=None, output_blocks=0)
            if client_id is not None:
                record['client_id'] = client_id
            if record.get('split') is not None and record['split']['state'] == 'running':
                record['split']['state'] = 'pending'
            for block in record['blocks'][start['index']:]:
                seed = block['inputs']['seed']
                while seed == block['inputs']['seed']:
                    seed = self.seeds.randrange(2 ** 31)
                block['inputs']['seed'] = seed
                block.update(state='pending', prompt_id=None, attempts=0, started=None, finished=None,
                             seconds=None, output=None, report_id=None, error=None,
                             fraction=0., remaining_seconds=None, continuation=None,
                             skip_frames=0, skip_audio_samples=0)
            if text is not None:
                # Only the new words take the LoRA triggers; the other parts keep their texts as written.
                probe = dict(node=record['node'], snapshot=deepcopy(record['snapshot']),
                             blocks=[dict(inputs=dict(text=text))])
                try:
                    apply_chain_triggers(probe)
                except Exception:
                    raise ValueError('Text must be at most 20000 characters with LoRA triggers') from None
                record['blocks'][start['index']]['inputs']['text'] = probe['blocks'][0]['inputs']['text']
            self._check_parts(record)
            # The kept seams are repaired with the sound the original chain kept for them. Copies left by
            # a failed save have no record, so the next start prunes them.
            try:
                for row in kept[1:]:
                    chain_leads.copy(self.store_dir, chain_id, identity, row['index'])
                self._save(record)
            except OSError:
                raise ValueError('The kept parts could not be copied') from None
            self.records[identity] = record
            self.queue.append(identity)
            self.wake.set()
            return self._view(record)

    def list(self, client_id=None):
        with self.lock:
            records = [row for row in self.records.values()
                       if not (row.get('client_id') or '').startswith('agent:')
                       or client_id is not None and row.get('client_id') == client_id]
            records = sorted(records, key=lambda row: row['created'], reverse=True)[:50]
            return [self._view(row, detailed=False, client_id=client_id) for row in records]

    def get(self, chain_id, client_id=None):
        with self.lock:
            return self._view(self.records[chain_id], client_id=client_id)

    def load(self, chain_id):
        """Restore one referenced receipt through the store's normal validation."""
        with self.lock:
            if chain_id not in self.records:
                rows = store.load_all(self.store_dir, identity=chain_id)
                if not rows:
                    raise KeyError(chain_id)
                record = rows[0]
                # As at startup, an old active receipt cannot replay submitted work.
                if record['state'] in store.ACTIVE:
                    record['state'] = 'interrupted'
                    if record['output_state'] == 'joining':
                        record['output_state'] = 'done' if record['output'] else 'pending'
                    for block in record['blocks']:
                        if block['state'] in ('queued', 'running'):
                            block['state'] = 'interrupted'
                    self._save(record)
                self.records[chain_id] = record
                if record['state'] == 'interrupted' and record['join'] and record['output_state'] != 'failed':
                    self.recover.add(chain_id)
            return self._view(self.records[chain_id])

    def hidden(self, exists=None):
        with self.lock:
            return {row['output'] for record in self.records.values()
                    if record['join'] and record['output_state'] == 'done'
                    and (record['output_blocks'] > 1 or record.get('original_audio') is not None)
                    and record['output'] not in {block['output'] for block in record['blocks']}
                    and (exists is None or exists(record['output']))
                    for row in record['blocks'][:record['output_blocks']] if row['output'] is not None}

    def active_ids(self):
        """Stable chain order for both the legacy turn and the concurrent host."""
        if self.concurrent:
            return [identity for identity in self.records if identity in self.active]
        return [self.active] if self.active is not None else []

    def _release_active(self, identity):
        if self.concurrent:
            self.active.discard(identity)
        elif self.active == identity:
            self.active = None

    def _find(self, prompt_id, node):
        for identity in self.active_ids():
            record = self.records.get(identity)
            if record is not None and record['node'] == str(node) and record['state'] in store.ACTIVE:
                block = next((row for row in record['blocks'] if row['prompt_id'] == prompt_id
                              and row['state'] in ('queued', 'running')), None)
                if block is not None:
                    return record, block
        return None, None

    def owns(self, prompt_id):
        """True while a chain block submitted this host request; its order is the chain's."""
        with self.lock:
            return any(row['prompt_id'] == prompt_id and row['state'] in ('queued', 'running')
                       for record in self.records.values() for row in record['blocks'])

    def _start(self, block):
        if block['started'] is None:
            block['started'] = self.wall()
            self.starts[block['prompt_id']] = self.clock()
        block['state'] = 'running'

    def context(self, prompt_id, node):
        with self.lock:
            record, block = self._find(prompt_id, node)
            if block is None:
                return None
            record['last_heartbeat_at'] = self.wall()
            if block['state'] != 'running':
                self._start(block)
                self._save(record)
            return dict(id=record['id'], block=block['id'], part=block['part'], index=block['index'],
                        count=len(record['blocks']), after=deepcopy(block['after']), before=deepcopy(block['before']),
                        context=block['context'], sources=self._sources(record, block))

    def finished(self, prompt_id, node, output, report_id, continuation=None):
        with self.lock:
            record, block = self._find(prompt_id, node)
            if block is None:
                return
            record['last_heartbeat_at'] = self.wall()
            try:
                valid = continuation is None or type(continuation) is dict and store.json_value(continuation)
            except (ValueError, TypeError, RecursionError, OverflowError):
                valid = False
            if not valid:
                logging.warning('[FreeVideo] Discarding non-JSON chain continuation')
                continuation = None
            saved = deepcopy(continuation)
            # The engine trims the repeated opening itself. A skip applies only
            # when its receipt names one.
            skips = {}
            for key in ('skip_frames', 'skip_audio_samples'):
                value = (saved or {}).get(key)
                skips[key] = value if type(value) is int and value >= 0 else 0
            block.update(output=output, report_id=report_id)
            block.update(continuation=saved, **skips)
            if report_id is not None:
                record['last_report_id'] = report_id
            self.success[prompt_id] = (self.clock(), self.wall())
            self._save(record)
            self.wake.set()
            previous = self._sources(record, block)['after']
            # A kept original soundtrack replaces every seam's sound, so there is no lead to save.
            keep = (record['id'], block['index'], output, previous['output'] if previous is not None else None,
                    bool(saved) and record.get('original_audio') is None)
        self._keep_lead(*keep)

    def _keep_lead(self, identity, index, output, previous, continues):
        """Copy the block's seam sound out of the shared store before newer records push it out."""
        reader = getattr(self.host, 'lead', None)
        if not continues or reader is None or not getattr(self.host, 'repairs_seams', True) or not isinstance(output, str) or not isinstance(previous, str):
            return False
        try:
            lead = reader(output, previous)
            if lead is not None:
                chain_leads.save(self.store_dir, identity, index, lead, previous, output)
                return True
        except Exception:
            pass
        logging.warning('[FreeVideo] A chain block\'s seam sound could not be kept: %s', identity)
        return False

    @staticmethod
    def _continues(row):
        """A block generated from the one before it: its seam must be repaired, whatever its receipt promised."""
        return isinstance(row['after'], str) and bool(row['continuation'])

    def _seams_kept(self, snapshot):
        """(indexes of the seams that must be repaired, how many of them have their sound), keeping
        late any a shared record still has. A seam is the pair of neighbours in the joined video, so a
        block that continues from a block other than its neighbour cannot be repaired and counts as lost."""
        identity, blocks = snapshot['id'], store.leading_blocks(snapshot)
        wanted, kept = set(), 0
        if snapshot.get('original_audio') is not None:
            # The original selection replaces every block's sound, so no seam sound needs repair.
            return wanted, kept
        for position, row in enumerate(blocks[1:] if getattr(self.host, 'repairs_seams', True) else (), 1):
            if not self._continues(row):
                continue
            wanted.add(row['index'])
            neighbour = blocks[position - 1]
            if row['after'] != neighbour['id']:
                continue
            if chain_leads.load(self.store_dir, identity, row['index'], neighbour['output'], row['output']) is not None:
                kept += 1
            elif self._keep_lead(identity, row['index'], row['output'], neighbour['output'], True):
                kept += 1
        return wanted, kept

    def _finish_blocks(self, record, final_state='done'):
        if not record['join']:
            return final_state
        blocks = store.leading_blocks(record)
        if not blocks:
            record.update(output=None, output_blocks=0, output_state='pending', output_error=None)
            return final_state
        if len(blocks) == 1 and record.get('original_audio') is None:
            output = blocks[0]['output']
            record.update(output=output, output_blocks=1 if output else 0, output_state='done', output_error=None)
            return final_state
        if record['output_state'] == 'done' and record['output_blocks'] == len(blocks):
            return final_state
        record.update(output_state='joining', output_error=None, join_final_state=final_state)
        return 'cancelling' if final_state == 'cancelled' else 'running'

    def _complete(self, record, block, status):
        state = status['state']
        prompt_id = block['prompt_id']
        ended, finished = self.success.get(prompt_id, (self.clock(), self.wall()))
        block['finished'] = finished
        start = self.starts.get(prompt_id)
        block['seconds'] = max(0., ended - start) if start is not None else 0.
        if prompt_id in self.success or state == 'success':
            block['state'] = 'done'
            block.update(fraction=1., remaining_seconds=0.)
            if block['output'] is None:
                block['output'] = deepcopy(status.get('output'))
            next_state = 'running' if self._current(record) is not None else (
                'cancelled' if record['state'] == 'cancelling' else self._finish_blocks(record))
        elif state in ('missing', 'interrupted'):
            block['state'], next_state = 'cancelled', 'cancelled'
        else:
            block['state'], next_state = 'failed', 'failed'
            block['error'] = status.get('error') or 'Prompt execution failed'
        next_state = 'cancelled' if record['state'] == 'cancelling' else next_state
        record['state'] = self._finish_blocks(record, next_state) if next_state in ('cancelled', 'failed') else next_state
        if record['state'] not in store.ACTIVE:
            self._release_active(record['id'])
        self.starts.pop(prompt_id, None)
        self.success.pop(prompt_id, None)
        self.cancel_sent.discard(prompt_id)
        self._save(record)
        if record['state'] in ('cancelled', 'failed', 'interrupted'):
            self._interrupt_dependants()

    def step(self):
        """Run one scheduler pass; tests can drive the same loop without sleeping."""
        with self.step_lock:
            with self.lock:
                recovering = []
                for identity in self.recover:
                    record = self.records[identity]
                    self._finish_blocks(record, 'interrupted')
                    self._save(record)
                    if record['output_state'] == 'joining':
                        recovering.append((identity, deepcopy(record)))
                self.recover.clear()
                # Cancelling a waiting chain never interrupts the active chain.
                for record in self.records.values():
                    if record['state'] == 'cancelling' and record['output_state'] != 'joining' and not any(
                            row['state'] in ('queued', 'running') for row in record['blocks']):
                        record['state'] = self._finish_blocks(record, 'cancelled')
                        self._save(record)
                for active_id in self.active_ids():
                    if self.records[active_id]['state'] not in store.ACTIVE:
                        self._release_active(active_id)
                self._interrupt_dependants()
                for queued_id in list(self.queue):
                    waiting = self.records[queued_id]
                    if waiting['state'] not in store.ACTIVE:
                        self.queue.remove(queued_id)
                    elif (self.concurrent or self.active is None) and (waiting['state'] == 'queued'
                            and self._dependency_state(waiting) == 'ready'
                            or waiting['state'] == 'cancelling' and waiting['output_state'] == 'joining'):
                        self.queue.remove(queued_id)
                        if self.concurrent:
                            self.active.add(queued_id)
                        else:
                            self.active = queued_id
                identities = self.active_ids()
            for recovering_id, snapshot in recovering:
                if not self.closed.is_set():
                    self._join(recovering_id, snapshot)
            for identity in identities:
                if not self.closed.is_set():
                    self._step_chain(identity)

    def _step_chain(self, identity):
        with self.lock:
            record = self.records[identity]
            if record['state'] not in store.ACTIVE:
                return
            block = self._current(record)
            if record['output_state'] == 'joining':
                if record['state'] == 'cancelling':
                    record['join_final_state'] = 'cancelled'
                self._save(record)
                snapshot = deepcopy(record)
                action = 'join'
            elif block is None or record['state'] == 'cancelling' and block['prompt_id'] is None:
                record['state'] = self._finish_blocks(record, 'cancelled' if record['state'] == 'cancelling' else 'done')
                self._save(record)
                return
            else:
                prompt_id, node = block['prompt_id'], record['node']
                action = 'status'
            split = record.get('split')
            if action == 'join':
                pass
            elif (prompt_id is None and split is not None and split['state'] == 'pending'
                  and record['state'] != 'cancelling' and block['index'] == 0):
                # Before the first part: one description per part, so later parts carry on instead of replaying.
                split['state'] = 'running'
                record['state'] = 'running'
                self._save(record)
                value = _split_value(record, block['inputs']['text'])
                action = 'split'
            elif prompt_id is None:
                self.progress_messages.pop(identity, None)
                self.progress_values.get(identity, {}).pop('remaining_seconds', None)
                prompt_id = str(uuid.uuid4())
                block.update(prompt_id=prompt_id, state='queued', attempts=block['attempts'] + 1)
                record['state'] = 'running'
                # Receipt first: a crash after submission cannot replay this prompt.
                self._save(record)
                prompt = deepcopy(record['snapshot']['prompt'])
                block_inputs = dict(block['inputs'])
                block_inputs.update(index=block['index'], count=len(record['blocks']),
                                    original_audio=block.get('original_audio') is not None)
                sources = self._sources(record, block)
                extra = deepcopy(record['snapshot']['extra_data'])
                if record['client_id'] is not None:
                    extra['client_id'] = record['client_id']
                extra['freevideo_chain'] = dict(id=identity, block=block['id'], part=block['part'], index=block['index'],
                    count=len(record['blocks']), after=deepcopy(block['after']), before=deepcopy(block['before']))
                if self.concurrent:
                    extra['freevideo_chain']['node'] = node
                action = 'submit'
            elif record['state'] == 'cancelling' and prompt_id not in self.cancel_sent:
                action = 'cancel'
                self.cancel_sent.add(prompt_id)
            else:
                action = 'status'
        # Host IO must not hold the lock used by routes or node callbacks.
        if action == 'join':
            self._join(identity, snapshot)
            return
        if action == 'split':
            self._split(record, value)
            if record['split']['state'] != 'pending':
                self._step_chain(identity)  # The first part starts in the same pass.
            return
        if action == 'submit':
            try:
                for key, source in sources.items():
                    if source is not None:
                        if source['output'] is None and not getattr(self.host, 'fake', False):
                            raise ValueError('Referenced block has no output: %s' % source['block'])
                        if 'work' in source and not self.host.work_exists(source['work']):
                            raise ValueError('Referenced work video does not exist: %s' % source['work'])
                    block_inputs[key] = (source['output'] or '') if source is not None else ''
                for name in getattr(self.host, 'continuation_inputs', ()):
                    prompt[node]['inputs'].pop(name, None)
                # A part that continues footage takes no keyframes of its own: it starts from the footage.
                # The host's media inputs name what only a part that starts fresh keeps.
                if any(source is not None for source in sources.values()):
                    for name in getattr(self.host, 'media_inputs', ()):
                        prompt[node]['inputs'].pop(name, None)
                    if hasattr(self.host, 'continued'):
                        self.host.continued(prompt, node)
                prompt[node]['inputs'].update(self.inputs(block_inputs))
                if record.get('original_audio') is not None:
                    from .original_audio_chain import block_prompt
                    block_prompt(prompt, node, record['original_audio'], block['original_audio'])
                self.host.submit(prompt_id, node_prompt(prompt, node), extra)
            except Exception as error:
                # A response may be lost after the host has accepted the prompt.
                try:
                    status = self.host.status(prompt_id)
                except Exception:
                    status = dict(state='missing')
                with self.lock:
                    if prompt_id in self.success or status['state'] == 'success':
                        self._complete(record, block, status)
                    elif status['state'] == 'running':
                        record['last_heartbeat_at'] = self.wall()
                        self._start(block)
                        self._save(record)
                    elif status['state'] != 'pending':
                        self._complete(record, block, dict(state='error', error=str(error)))
            return
        if action == 'cancel':
            try:
                self.host.cancel(prompt_id, node)
            except Exception:
                logging.warning('[FreeVideo] Chain cancellation could not reach the host')
        with self.lock:
            succeeded = prompt_id in self.success
        if succeeded:
            status = dict(state='success')
        else:
            try:
                status = self.host.status(prompt_id)
            except Exception:
                logging.warning('[FreeVideo] Chain status could not reach the host')
                return
        with self.lock:
            state = status['state']
            if prompt_id in self.success or state in ('success', 'error', 'interrupted', 'missing'):
                if state != 'missing' or prompt_id in self.success:
                    record['last_heartbeat_at'] = self.wall()
                self._complete(record, block, status)
            elif state in ('pending', 'running'):
                desired = 'queued' if state == 'pending' else 'running'
                if block['state'] != desired:
                    if desired == 'running':
                        record['last_heartbeat_at'] = self.wall()
                        self._start(block)
                    else:
                        block['state'] = desired
                    self._save(record)

    def _split(self, record, value):
        def cancelled():
            return record['state'] == 'cancelling' or self.closed.is_set()
        split = getattr(self.host, 'split_texts', None)
        parts, reason = None, 'unavailable'
        if split is not None:
            try:
                parts, reason = split(value, cancelled)
            except Exception:
                logging.exception('[FreeVideo] Splitting the description failed')
                parts, reason = None, 'failed'
        with self.lock:
            if parts is None and cancelled():
                record['split']['state'] = 'pending'  # Split again when resumed.
            elif parts is not None and len(parts) == len(record['blocks']) and all(
                    isinstance(part, str) and part.strip() for part in parts):
                for row, part in zip(record['blocks'], parts):
                    row['inputs']['text'] = part
                try:
                    from .lora_triggers import apply_chain_triggers
                    apply_chain_triggers(record)
                    record['split'].update(state='done', reason=None)
                except Exception:
                    for row in record['blocks']:
                        row['inputs']['text'] = value['text']
                    record['split'].update(state='failed', reason='failed')
            else:
                # Without the prompt model the sentences go to the parts in order, so no part plays the whole
                # story again; the page says why. A description without sentences goes to every part.
                if parts is not None:
                    reason = 'unusable'
                texts = _ordered_texts(record, value)
                for row, text in zip(record['blocks'], texts or [value['text']] * len(record['blocks'])):
                    row['inputs']['text'] = text
                record['split'].update(state='failed', reason=reason if reason in store.SPLIT_REASONS else 'failed')
            self._save(record)

    def _join(self, identity, snapshot):
        final_state = snapshot.get('join_final_state', 'done')
        def cancelled():
            with self.lock:
                return self.closed.is_set() or (final_state != 'cancelled'
                                               and self.records[identity]['state'] == 'cancelling')
        index_by_output = {row['output']: row['index'] for row in snapshot['blocks'] if row['output']}
        wanted = set()
        def lead_reader(following, previous):
            # Only a seam that must be repaired gets a sound; another seam is never counted for it.
            index = index_by_output.get(following)
            if index is None or index not in wanted:
                return None
            return chain_leads.load(self.store_dir, identity, index, previous, following)
        texts = [row['inputs']['text'] for row in snapshot['blocks']]
        texts += [row['inputs'].get('text') for row in snapshot['snapshot']['prompt'].values()
                  if isinstance(row, dict) and isinstance(row.get('inputs'), dict)]
        failure = None
        promised = kept = 0
        try:
            found, kept = self._seams_kept(snapshot)
            wanted.update(found)
            promised = len(wanted)
            snapshot = dict(snapshot, lead_reader=lead_reader, expected_seams=promised)
            if kept < promised:
                # A sound seam that cannot be repaired is never published as a finished video.
                raise ValueError('%d of %d seam sounds were not kept' % (promised - kept, promised))
            output = self.host.join(snapshot, cancelled)
            if not isinstance(output, str) or not output:
                raise ValueError('Joining did not return a video path')
        except Exception as error:
            message = str(error)
            failure = message[:300]
            if 'workflow' in message.lower() or any(isinstance(value, str) and value and any(
                    part in message for part in (value, repr(value)[1:-1], json.dumps(value)[1:-1])) for value in texts):
                failure = 'Joining failed'
        with self.lock:
            record = self.records[identity]
            if self.closed.is_set():
                return
            elif cancelled():
                record['state'] = self._finish_blocks(record, 'cancelled')
            elif failure is not None:
                record.update(state=final_state, output_state='failed', output_error=failure)
                logging.warning('[FreeVideo] Chain joining failed: %s', identity)
            else:
                record.update(state=final_state, output=output, output_blocks=len(store.leading_blocks(snapshot)),
                              output_state='done', output_error=None, output_seams=dict(expected=promised, kept=kept))
            if record['state'] not in store.ACTIVE:
                self._release_active(identity)
            self._save(record)

    def _estimate(self, record, block):
        """The estimator receives live records once per block and must not modify them."""
        if self.estimate is None:
            return None
        key = record['id'], block['id']
        if key not in self.estimates:
            try:
                self.estimates[key] = _number(self.estimate(record, block))
            except Exception:
                logging.warning('[FreeVideo] Chain estimate is unavailable')
                self.estimates[key] = None
        return self.estimates[key]

    def progress(self, node, message, prompt_id=None):
        with self.lock:
            if self.concurrent:
                record, _ = self._find(prompt_id, node)
            else:
                record = self.records.get(self.active)
            if record is None or record['node'] != str(node) or record['state'] not in store.ACTIVE:
                return None
            block = self._current(record)
            if block is None or block['state'] != 'running':
                return None
            token = message.get('report_id')
            if isinstance(token, str) and REPORT_ID.fullmatch(token) and block['report_id'] != token:
                # A restart ends this block before `finished` stores its report, and the
                # report of the interrupted run is the one a failure needs.
                block['report_id'] = record['last_report_id'] = token
                self._save(record)
            overall = message.get('overall') or {}
            used = _number(overall.get('elapsed_seconds'))
            used = self._elapsed(block) if used is None else used
            remaining = _number(overall.get('remaining_seconds'))
            done = [row for row in record['blocks'] if row['state'] == 'done']
            rates = [row['seconds'] / row['inputs']['frames'] for row in done
                     if _number(row['seconds']) is not None and row['seconds'] > 0]
            rate = sum(rates) / len(rates) if rates else None
            fallback = ((used + remaining) / block['inputs']['frames']
                        if remaining is not None and used + remaining > 0 else None)
            estimates = []
            for row in record['blocks']:
                if row['state'] == 'done':
                    value = row['seconds'] or 0.
                elif rate is not None:
                    value = rate * row['inputs']['frames']
                else:
                    value = self._estimate(record, row)
                    if value is None and fallback is not None:
                        value = fallback * row['inputs']['frames']
                estimates.append(value)
            current_estimate = estimates[block['index']]
            fraction = _number(overall.get('fraction'))
            if overall.get('status') == 'complete' or message.get('phase') == 'complete':
                fraction = 1.
                remaining = 0.
            own_fraction = block['fraction'] if fraction is None else max(block['fraction'], min(1., fraction))
            # Kept in memory only: several messages arrive per second, and a
            # restart resets an unfinished block anyway.
            block.update(fraction=own_fraction, remaining_seconds=remaining)
            if fraction is None:
                fraction = min(.95, used / current_estimate) if current_estimate else 0.
            fraction = max(own_fraction, min(1., fraction))
            if remaining is None and current_estimate is not None:
                remaining = max(0., current_estimate - used)
            later = [estimates[row['index']] for row in record['blocks']
                     if row is not block and row['state'] != 'done']
            remaining = remaining + sum(later) if remaining is not None and all(e is not None for e in later) else None
            completed_seconds = sum(row['seconds'] or 0. for row in done)
            if len(done) + 1 == len(record['blocks']) and fraction == 1.:
                total_fraction = 1.
            elif all(e is not None for e in estimates) and sum(estimates) > 0:
                total_fraction = (completed_seconds + current_estimate * fraction) / sum(estimates)
            else:
                total_fraction = (len(done) + fraction) / len(record['blocks'])
            previous = self.progress_values.get(record['id'], {})
            value = dict(fraction=max(previous.get('fraction', 0.), total_fraction), remaining_seconds=remaining,
                         elapsed_seconds=max(previous.get('elapsed_seconds', 0.), completed_seconds + used))
            self.progress_messages[record['id']] = {key: deepcopy(message[key]) for key in
                          ('phase', 'timing_phase', 'label', 'detail', 'done', 'total', 'unit') if key in message}
            record['last_heartbeat_at'] = self.wall()
            if record['join'] and len(record['blocks']) > 1:
                value['fraction'] = min(.99, value['fraction'])
            self.progress_values[record['id']] = value
            return dict(id=record['id'], block=block['id'], part=block['part'], index=block['index'], count=len(record['blocks']),
                        after=deepcopy(block['after']), before=deepcopy(block['before']), overall=deepcopy(value))

    def _work(self):
        while not self.closed.is_set():
            self.wake.clear()
            try:
                self.step()
            except Exception:
                logging.exception('[FreeVideo] Chain scheduler pass failed')
            self.wake.wait(self.poll)

    def close(self):
        self.closed.set()
        self.wake.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=31.)
        close = getattr(self.host, 'close', None)
        if close is not None:
            close()
