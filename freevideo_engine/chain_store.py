"""Versioned workflow snapshots and atomic receipts for background chains."""
from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
import time
import uuid

from . import chain_plan

ACTIVE = ('queued', 'running', 'cancelling')
CHAIN_STATES = (*ACTIVE, 'cancelled', 'failed', 'interrupted', 'done')
BLOCK_STATES = ('pending', 'queued', 'running', 'done', 'cancelled', 'failed', 'interrupted')
OUTPUT_STATES = ('none', 'pending', 'joining', 'done', 'failed')
# pending: split before the first part; given: the request gave one text per part; none: nothing to split.
SPLIT_STATES = ('pending', 'running', 'done', 'failed', 'given', 'none')
SPLIT_REASONS = {
    'model_missing': 'The prompt model is not installed, so every part used the whole description.',
    'busy': 'The prompt model was busy, so every part used the whole description.',
    'memory': 'There was not enough memory to split the description, so every part used the whole description.',
    'unusable': 'The description could not be split into parts, so every part used the whole description.',
    'unavailable': 'Splitting is not available here, so every part used the whole description.',
    'failed': 'Splitting the description failed, so every part used the whole description.',
}
# Parts one video may be continued through: the first part and seven continuations (fewer with a short part).
MAX_PARTS = chain_plan.MAX_PARTS


def reference(value, queue=False):
    if value is None or isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and set(value) == {'work'}:
        path = value['work']
        if isinstance(path, str) and path and '\x00' not in path:
            posix, windows = PurePosixPath(path), PureWindowsPath(path)
            if (not posix.is_absolute() and not windows.drive and not windows.root
                    and '..' not in posix.parts and '..' not in windows.parts
                    and posix.suffix.lower() == '.mp4'):
                return deepcopy(value)
    if queue and isinstance(value, dict) and set(value) == {'chain', 'block'}:
        if (isinstance(value['chain'], str) and re.fullmatch(r'c-[0-9a-f]{12}', value['chain'])
                and isinstance(value['block'], str) and value['block']):
            return deepcopy(value)
    if queue:
        raise ValueError('References must be block IDs, relative MP4 work paths, or queued chain outputs')
    raise ValueError('References must be block IDs or relative MP4 work paths without ..')


def json_value(value):
    """Only ordinary, finite JSON values may enter durable continuation receipts."""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and json_value(item) for key, item in value.items())
    return False


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('A chain object is required')
    node, prompt = payload.get('node'), payload.get('prompt')
    if not isinstance(node, str) or not node or not isinstance(prompt, dict) or node not in prompt:
        raise ValueError('Node must exist in the prompt')
    if not isinstance(prompt[node], dict) or not isinstance(prompt[node].get('inputs'), dict):
        raise ValueError('Node inputs are required')
    if prompt[node].get('class_type') != 'FreeVideoGenerate':
        raise ValueError('Node must be FreeVideoGenerate')
    extra = payload.get('extra_data', {})
    client = payload.get('client_id')
    if not isinstance(extra, dict) or client is not None and not isinstance(client, str):
        raise ValueError('Invalid extra data or client ID')
    if ('blocks' in payload) == ('seconds' in payload):
        raise ValueError('Specify blocks or seconds, not both')
    queue = payload.get('queue', False)
    if type(queue) is not bool:
        raise ValueError('Queue must be a bool')
    planned = split = None
    join = payload.get('join', 'seconds' in payload)
    if type(join) is not bool:
        raise ValueError('Join must be a bool')
    if 'seconds' in payload:
        # 'after' continues a saved video: the plan then counts the first block as a continuation too.
        source = payload.get('after')
        if source is not None and not isinstance(source, dict):
            raise ValueError('A planned video can only continue a saved work')
        planned = chain_plan.plan(payload['seconds'], payload.get('single', False), payload.get('max_frames'),
                                  payload.get('single_limit_seconds'), payload.get('block_seconds'),
                                  continued=source is not None)
        inputs = prompt[node]['inputs']
        text, seed = payload.get('text', inputs.get('text')), payload.get('seed', inputs.get('seed'))
        if not isinstance(text, str):
            raise ValueError('Text must be a string')
        if type(seed) is not int or seed < 0:
            raise ValueError('A nonnegative integer seed is required')
        # Each part plays its own share of the description; one text for all parts replays the story
        # from the start in every part. Without texts the runner splits it before the first part.
        texts = payload.get('texts')
        if texts is not None and (not isinstance(texts, list) or len(texts) != len(planned['blocks'])
                                  or any(not isinstance(row, str) or not row.strip() for row in texts)):
            raise ValueError('Texts must give one description for each planned part')
        rows = [dict(id='b%s' % (index + 1), after='b%s' % index if index else source,
                     frames=row['frames'], text=texts[index] if texts else text, seed=seed + index)
                for index, row in enumerate(planned['blocks'])]
        if len(rows) > 1:
            split = dict(state='given' if texts else 'pending' if text.strip() else 'none', reason=None)
    elif 'texts' in payload:
        raise ValueError('Texts are given with seconds; blocks carry their own text')
    else:
        rows = payload['blocks']
    if not isinstance(rows, list) or not 1 <= len(rows) <= chain_plan.MAX_BLOCKS:
        raise ValueError('A chain requires 1 to %s blocks' % chain_plan.MAX_BLOCKS)
    blocks, ids = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('A block object is required')
        identity = row.get('id')
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError('Block IDs must be unique strings')
        part = row.get('part', identity)
        if not isinstance(part, str):
            raise ValueError('Block part must be a string')
        after, before = reference(row.get('after'), queue), reference(row.get('before'), queue)
        text, frames = row.get('text'), row.get('frames')
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError('Block text must be a string of at most 20000 characters')
        if type(frames) is not int or frames < 39 or (frames - 5) % 17:
            raise ValueError('Block frames must be an integer of 5 + 17n, at least 39')
        if 'seconds' in row:
            raise ValueError('Use frames for block length')
        seed = row.get('seed') if 'seed' in row else prompt[node]['inputs'].get('seed')
        if type(seed) is not int or seed < 0:
            raise ValueError('A nonnegative integer seed is required')
        if 'seed' not in row:
            seed += index
        for key, supported in (('fill', 'both'), ('context', 'saved')):
            if key in row and row[key] != supported:
                raise ValueError('This version does not support %s other than %s' % (key, supported))
        blocks.append(dict(id=identity, part=part, index=index, after=after, before=before,
                           fill='both', context='saved', inputs=dict(text=text, frames=frames, seed=seed),
                           new_frames=frames - chain_plan.OVERLAP if after or before else frames,
                           state='pending', prompt_id=None, attempts=0, started=None,
                           finished=None, seconds=None, output=None, report_id=None, error=None,
                           fraction=0., remaining_seconds=None, continuation=None,
                           skip_frames=0, skip_audio_samples=0))
        ids.add(identity)
    dependencies = {row['id']: [row[key] for key in ('after', 'before')
                               if isinstance(row[key], str)] for row in blocks}
    visiting, visited = set(), set()
    def visit(identity):
        if identity not in ids:
            raise ValueError('Referenced block must belong to this chain')
        if identity in visiting:
            raise ValueError('Block references cannot contain a cycle or self reference')
        if identity not in visited:
            visiting.add(identity)
            for source in dependencies[identity]:
                visit(source)
            visiting.remove(identity)
            visited.add(identity)
    for identity in ids:
        visit(identity)
    now = time.time()
    record = dict(version=1, id='c-' + uuid.uuid4().hex[:12], node=node, created=now,
                  updated=now, state='queued', client_id=client,
                  snapshot=dict(prompt=deepcopy(prompt), extra_data=deepcopy(extra)), blocks=blocks,
                  plan=planned, join=join, output=None, output_state='pending' if join else 'none',
                  output_error=None, output_blocks=0, split=split)
    if queue:
        record['queue'] = True
    try:
        json.dumps(record, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('Chain data must be valid JSON') from error
    return record


def leading_blocks(record):
    blocks = []
    for row in record['blocks']:
        if row['state'] != 'done':
            break
        blocks.append(row)
    return blocks


def summary(record):
    blocks = record['blocks']
    current = next((row['id'] for row in blocks if row['state'] in ('queued', 'running')), None)
    return dict({key: deepcopy(record[key]) for key in
                 ('version', 'id', 'node', 'created', 'updated', 'state',
                  'join', 'output', 'output_state', 'output_error', 'output_blocks')},
                output_seams=deepcopy(record.get('output_seams')), split=deepcopy(record.get('split')),
                redo_of=deepcopy(record.get('redo_of')),
                count=len(blocks), done=sum(row['state'] == 'done' for row in blocks),
                current=current, elapsed_seconds=sum(row['seconds'] or 0 for row in blocks if row['state'] == 'done'),
                last_report_id=record.get('last_report_id') or next((row['report_id'] for row in reversed(blocks)
                                     if row['report_id'] is not None), None))


def detail(record):
    value = dict(summary(record), blocks=deepcopy(record['blocks']), plan=deepcopy(record['plan']),
                 generation_size={key: record['snapshot']['prompt'][record['node']]['inputs'].get(key)
                                  for key in ('width', 'height')})
    if (record.get('split') or {}).get('state') == 'done':
        # Split parts repeat words put there by code; the page lists and edits only each part's own.
        from .prompt_vlm.rules import own_words
        value['part_words'] = own_words([str(row['inputs'].get('text', '')) for row in record['blocks']])
    return value


def save(directory, record):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (record['id'] + '.json')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix=record['id'] + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_all(directory, limit=200, *, identity=None):
    if identity is not None and (not isinstance(identity, str) or not re.fullmatch(r'c-[0-9a-f]{12}', identity)):
        return []
    records = []
    paths = []
    for path in Path(directory).glob(identity + '.json' if identity is not None else '*.json'):
        try:
            paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            logging.warning('[FreeVideo] Skipping inaccessible chain file: %s', path.name)
    paths.sort(key=lambda row: (row[0], row[1].name), reverse=True)
    for _, path in paths[:limit]:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or record.get('version') != 1:
                raise ValueError('Unsupported chain version')
            identity = record.get('id')
            if not isinstance(identity, str) or not re.fullmatch(r'c-[0-9a-f]{12}', identity) or path.stem != identity:
                raise ValueError('Invalid chain ID')
            for key, value in dict(plan=None, join=False, output=None, output_state='none', output_error=None).items():
                record.setdefault(key, value)
            if type(record['join']) is not bool or record['output_state'] not in OUTPUT_STATES:
                raise ValueError('Invalid chain output state')
            if any(record[key] is not None and not isinstance(record[key], str) for key in ('output', 'output_error')):
                raise ValueError('Invalid chain output')
            record.setdefault('output_blocks', len(record['blocks']) if record['output_state'] == 'done' else 0)
            if (type(record['output_blocks']) is not int or not 0 <= record['output_blocks'] <= len(record['blocks'])
                    or record['output'] is None and record['output_blocks'] != 0):
                raise ValueError('Invalid chain output block count')
            split = record.setdefault('split', None)
            if split is not None and (not isinstance(split, dict) or set(split) != {'state', 'reason'}
                                      or split['state'] not in SPLIT_STATES
                                      or split['reason'] is not None and split['reason'] not in SPLIT_REASONS):
                raise ValueError('Invalid chain split')
            if split is not None and split['state'] == 'running':
                split['state'] = 'pending'  # Stopped while splitting: split again when resumed.
            seams = record.get('output_seams')
            if seams is not None and (not isinstance(seams, dict) or set(seams) != {'expected', 'kept'}
                                      or any(type(value) is not int or value < 0 for value in seams.values())):
                raise ValueError('Invalid chain seam count')
            redo = record.get('redo_of')
            if redo is not None and (not isinstance(redo, dict) or set(redo) != {'chain', 'block'}
                                     or not isinstance(redo['chain'], str)
                                     or not re.fullmatch(r'c-[0-9a-f]{12}', redo['chain'])
                                     or not isinstance(redo['block'], str) or not redo['block']):
                raise ValueError('Invalid chain redo source')
            if record.get('join_final_state', 'done') not in ('done', 'cancelled', 'failed', 'interrupted'):
                raise ValueError('Invalid chain join final state')
            if record['plan'] is not None:
                planned = record['plan']
                # The plan was made for a device limit that is not kept; only its shape is checked.
                if (not isinstance(planned, dict) or not json_value(planned) or type(planned.get('single')) is not bool
                        or any(type(planned.get(key)) is not int or planned[key] < 0 for key in ('frames', 'hide_frames'))):
                    raise ValueError('Invalid chain plan')
            for row in record['blocks']:
                if 'frames' not in row['inputs'] and 'seconds' in row['inputs']:
                    # Step-one receipts used seconds. Preserve their effective
                    # geometry and results while exposing the new block contract.
                    from .geometry import geometry
                    context = row.get('context', {})
                    if not isinstance(context, dict) or context.get('source', 'tail') not in ('tail', 'frames'):
                        raise ValueError('Invalid legacy block context')
                    row['inputs']['frames'] = geometry(seconds=row['inputs'].pop('seconds'))['frames']
                    row.update(part=row['id'], before=None, fill='both', context='saved',
                               fraction=1. if row['state'] == 'done' else 0., remaining_seconds=None,
                               continuation=None, skip_frames=0, skip_audio_samples=0)
            snapshot = record['snapshot']
            normalized = validate(dict(node=record['node'], client_id=record['client_id'], **snapshot,
                queue=record.get('queue', False), blocks=[{key: row[key] for key in ('id', 'part', 'after', 'before', 'fill', 'context')}
                        | row['inputs']
                        for row in record['blocks']], join=record['join']))
            if record['state'] not in CHAIN_STATES:
                raise ValueError('Invalid chain state')
            for row, clean in zip(record['blocks'], normalized['blocks']):
                row.setdefault('new_frames', clean['new_frames'])
                if type(row['new_frames']) is not int or row['new_frames'] != clean['new_frames']:
                    raise ValueError('Invalid block new frame count')
                if row['state'] not in BLOCK_STATES or row['index'] != clean['index']:
                    raise ValueError('Invalid block state or index')
                if type(row['attempts']) is not int or row['attempts'] < 0:
                    raise ValueError('Invalid attempt count')
                for key in ('started', 'finished', 'seconds'):
                    value = row[key]
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                        raise ValueError('Invalid block timing')
                for key in ('prompt_id', 'output', 'report_id', 'error'):
                    if row[key] is not None and not isinstance(row[key], str):
                        raise ValueError('Invalid block result')
                if (type(row['fraction']) not in (int, float) or not 0 <= row['fraction'] <= 1
                        or row['remaining_seconds'] is not None and
                        (type(row['remaining_seconds']) not in (int, float)
                         or not math.isfinite(row['remaining_seconds']) or row['remaining_seconds'] < 0)):
                    raise ValueError('Invalid block progress')
                if not json_value(row['continuation']) or any(
                        type(row[key]) not in (int, float) or not math.isfinite(row[key]) or row[key] < 0
                        for key in ('skip_frames', 'skip_audio_samples')):
                    raise ValueError('Invalid continuation receipt')
                if row['state'] != 'pending' and (not row['prompt_id'] or row['attempts'] == 0):
                    raise ValueError('Submitted blocks require a prompt ID and attempt')
                if row['state'] == 'done' and (row['seconds'] is None or row['finished'] is None):
                    raise ValueError('Completed blocks require timing')
            for key in ('created', 'updated'):
                if type(record[key]) not in (int, float) or not math.isfinite(record[key]):
                    raise ValueError('Invalid chain timestamp')
            records.append(record)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            # Keep the file for inspection; never log its workflow or sentences.
            logging.warning('[FreeVideo] Skipping invalid chain file: %s', path.name)
    return records
