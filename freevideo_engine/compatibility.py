"""User-controlled workload compatibility after an unsettled GPU request.

An interrupted owner/worker pair is evidence of an unfinished request, not a
diagnosis of power, temperature, a bluescreen or a defective kernel. This state
never bans execution and never treats OOM or a normal cancellation as a crash.
"""
from contextlib import contextmanager, closing
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import quote

from .long_paths import io_path, plain, PREFIX


LEVELS = (
    dict(level=0, en='Off · automatic performance', zh='关闭 · 自动性能'),
    dict(level=1, en='Light · the GPU handles a little less at a time', zh='轻度 · 显卡每次处理的数据减少一些', head=4),
    dict(level=2, en='Compatible · the GPU handles less at a time; slower', zh='兼容 · 显卡每次处理的数据更少，生成更慢', head=2),
    dict(level=3, en='Strong · the GPU handles the least at a time; slowest', zh='加强 · 显卡每次处理的数据最少，生成最慢', head=1),
)
RECOVERY_SUCCESSES = 3
# An interruption whose cause is unknown leaves no sign that the wider work grouping was at fault,
# so one completed generation restores the previous level; a reported GPU error keeps the full streak.
UNEXPLAINED_INTERRUPTIONS = ('unfinished_request_after_owner_and_worker_exit', 'unknown_worker_exit')
SUCCESS_RECEIPTS = 64


@lru_cache(maxsize=1)
def policy_revision():
    """Same identity in the frozen launcher and its deployed engine, without Torch.

    Build versions expire automatic settings on release upgrades; source hashes
    also cover a git checkout whose package version has not changed.
    """
    from . import __version__
    root = io_path(__file__).parent
    if getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None):
        root = io_path(Path(sys._MEIPASS) / 'engine-source' / 'freevideo_engine')
    digest = hashlib.sha256(__version__.encode('utf-8'))
    for name in ('compatibility.py', 'policy.py', 'resident_models.py', 'worker.py',
                 'runtime.py', 'attention.py', 'adaptive.py', 'resource_history.py'):
        digest.update(name.encode('utf-8'))
        # An incomplete install must not reuse another build's automatic cap.
        try:
            content = (root / name).read_bytes()
        except OSError:
            content = b'missing'
        digest.update(content.replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def recovery_required(state):
    if state.get('recovery_required') in (1, RECOVERY_SUCCESSES):
        return state['recovery_required']
    reason = (state.get('trigger') or state.get('notice') or {}).get('reason')
    return 1 if reason in UNEXPLAINED_INTERRUPTIONS else RECOVERY_SUCCESSES


def current_settings(value):
    value = copy.deepcopy(value)
    revision = policy_revision()
    # Older releases only recorded the automatic checkbox. Preserve their
    # explicit opt-outs; new rows track manual choices independently of it.
    origin = value.get('origin', 'automatic' if value.get('automatic', True) else 'manual')
    manual_level = value.get('manual_level', value['level'] if origin == 'manual' else 0)
    if value.get('policy_revision') != revision:
        if origin == 'automatic' and value['level']:
            value['reset'] = dict(reason='policy_updated', previous_level=value['level'],
                                  previous_revision=value.get('policy_revision'), revision=revision,
                                  previous_trigger=value.get('trigger') or value.get('notice'))
            value['level'] = manual_level
            origin = 'manual' if manual_level else 'automatic'
        value['notice'] = None
        value.pop('trigger', None)
        for field in ('unknown_interruptions', 'consecutive_successes', 'recovery_disabled',
                      'last_counted_interruption', 'last_counted_batch', 'last_escalation', 'recovery',
                      'recovery_required'):
            value.pop(field, None)
    value.setdefault('unknown_interruptions', 0)
    value.setdefault('consecutive_successes', 0)
    value.setdefault('recovery_disabled', False)
    value.update(origin=origin, manual_level=manual_level, policy_revision=revision)
    return value


def failure_evidence(details):
    # Bookkeeping must never turn a settled failure into another error.
    try:
        return _failure_evidence(details)
    except Exception:
        return None


def _failure_evidence(details):
    """What a discarded GPU failure leaves for diagnosis: fixed fields and numbers only.

    The attempt row is deleted when it is discarded, so a report taken after a
    later successful retry would otherwise show no more than the reason.
    """
    if not isinstance(details, dict):
        return None
    evidence = {}
    for key in ('failed_phase', 'completed_steps'):
        value = details.get(key)
        if isinstance(value, str) and len(value) <= 64 or type(value) is int:
            evidence[key] = value
    chain = []
    for item in (details.get('exception') or [])[:3]:
        if not isinstance(item, dict):
            continue
        entry = {key: item[key] for key in ('exception_type', 'category') if isinstance(item.get(key), str)
                 and len(item[key]) <= 64}
        if type(item.get('error_code')) is int:
            entry['error_code'] = item['error_code']
        entry['frames'] = [{key: (str(frame[key]).replace('\\', '/').rsplit('/', 1)[-1] if key == 'file' else frame[key])
                            for key in ('file', 'function', 'line')
                            if isinstance(frame.get(key), (str, int)) and len(str(frame[key])) <= 96}
                           for frame in (item.get('frames') or [])[-4:] if isinstance(frame, dict)]
        chain.append(entry)
    if chain:
        evidence['exception'] = chain
    memory = details.get('memory')
    if isinstance(memory, dict):
        numbers = {key: value for key, value in memory.items() if type(value) is int}
        if numbers:
            evidence['memory'] = dict(list(numbers.items())[:16])
    return evidence or None


def device_key(identity):
    """Missing optional metadata disables compatibility, never invents a device."""
    if not isinstance(identity, dict):
        return None
    gpu = identity.get('gpu')
    uuid = (gpu.get('uuid') if isinstance(gpu, dict) else None) or identity.get('gpu_uuid')
    system = identity.get('system')
    if any(not isinstance(value, str) or not value.strip() for value in (uuid, system)):
        return None
    return hashlib.sha256(json.dumps([uuid, system]).encode()).hexdigest()


def unavailable():
    return dict(available=False, reason='device_identity_unavailable',
                level=0, automatic=False, notice=None, levels=LEVELS)


def _read_uri(path, *, windows=None):
    path = io_path(path, windows=windows)
    if str(path).startswith(PREFIX):
        # WindowsPath.as_uri treats the prefix as a UNC authority. Encode the
        # filename itself so SQLite receives it unchanged, still with mode=ro.
        return 'file:' + quote(str(path), safe='/:') + '?mode=ro'
    return path.resolve().as_uri() + '?mode=ro'


class Store:
    def __init__(self, root):
        self.path = Path(plain(root)) / 'compatibility.sqlite3'

    @contextmanager
    def transaction(self):
        path = io_path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(path), timeout=10, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS settings (device TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, device TEXT NOT NULL, time REAL, value TEXT NOT NULL)')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def read_row(db, key):
        row = db.execute('SELECT value FROM settings WHERE device=?', (key,)).fetchone()
        return json.loads(row[0]) if row else dict(level=0, automatic=True, notice=None)

    @staticmethod
    def write_row(db, key, value):
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def status(self, identity, *, persist=False):
        key = device_key(identity)
        if key is None:
            return unavailable()
        if not io_path(self.path).exists():
            return dict(current_settings(dict(level=0, automatic=True, notice=None)), levels=LEVELS, available=True,
                        recovery_successes_required=RECOVERY_SUCCESSES)
        with closing(sqlite3.connect(_read_uri(self.path), uri=True, timeout=10)) as db:
            value = self.read_row(db, key)
        selected = current_settings(value)
        if persist and selected != value:
            with self.transaction() as db:
                # Re-read under the lock so a concurrent user edit always wins.
                selected = current_settings(self.read_row(db, key))
                self.write_row(db, key, selected)
        return dict(selected, levels=LEVELS, available=True, recovery_successes_required=recovery_required(selected))

    def set(self, identity, level, automatic):
        if type(level) is not int or not 0 <= level <= 3 or type(automatic) is not bool:
            raise ValueError('Compatibility level must be 0–3; automatic must be a boolean')
        key = device_key(identity)
        if key is None:
            return unavailable()
        with self.transaction() as db:
            value = dict(level=level, automatic=automatic, notice=None, origin='manual',
                         manual_level=level, policy_revision=policy_revision(),
                         unknown_interruptions=0, consecutive_successes=0, recovery_disabled=False)
            self.write_row(db, key, value)
        return dict(value, levels=LEVELS, available=True)

    def acknowledge(self, identity, notice_id):
        key = device_key(identity)
        if key is None:
            return unavailable()
        with self.transaction() as db:
            value = current_settings(self.read_row(db, key))
            if value.get('notice') and value['notice']['id'] == notice_id:
                value['notice'] = None
            self.write_row(db, key, value)
        return dict(value, levels=LEVELS, available=True)

    def incident(self, row, reason, batch=None, evidence=None):
        # Only a launched video worker can be a load-related interrupted attempt.
        if row.get('purpose') != 'generation' or not row.get('worker'):
            return
        identity = row.get('identity')
        key = device_key(identity)
        if key is None:
            return
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM incidents WHERE id=?', (row['id'],)).fetchone():
                return
            value = current_settings(self.read_row(db, key))
            revision = (row.get('details') or {}).get('compatibility_revision')
            incident = dict(id=row['id'], reason=reason, phase=row.get('phase'),
                config=row['config'], geometry=row['geometry'], diagnosis='unknown', time=time.time(),
                policy_revision=revision, applicable=revision == policy_revision(),
                counted=False, level_changed=False)
            evidence = failure_evidence(evidence)
            if evidence:
                incident['evidence'] = evidence
            escalate = False
            if value['automatic'] and incident['applicable']:
                if reason == 'cuda_error':
                    escalate = True
                    incident['counted'] = True
                    value['unknown_interruptions'] = 0
                # One event may leave several unsettled requests: those recovered together count
                # once, and so does one that was already running at the last counted interruption.
                # A request is registered after recovery in the same call, often within one tick
                # of a coarse system clock, so an equal time counts.
                elif (reason in UNEXPLAINED_INTERRUPTIONS
                        and row['started'] >= value.get('last_counted_interruption', 0)
                        and (batch is None or batch != value.get('last_counted_batch'))):
                    incident['counted'] = True
                    value['unknown_interruptions'] += 1
                    escalate = value['unknown_interruptions'] >= 2
                    if escalate:
                        value['unknown_interruptions'] = 0
                if incident['counted']:
                    value['last_counted_interruption'] = incident['time']
                    value['last_counted_batch'] = batch
                    value['consecutive_successes'] = 0
            if escalate:
                previous = value['level']
                # A level raised by a GPU error keeps its full streak when unexplained events raise it further.
                carried = previous > 0 and value['origin'] == 'automatic' and recovery_required(value) == RECOVERY_SUCCESSES
                value['recovery_required'] = (1 if reason in UNEXPLAINED_INTERRUPTIONS and not carried
                                              else RECOVERY_SUCCESSES)
                value['level'] = min(3, max(2, value['level'] + 1))
                incident['level_changed'] = value['level'] != previous
                value['origin'] = 'automatic'
                value['last_escalation'] = incident['time']
                if value.get('recovery'):
                    value['recovery_disabled'] = True
                value.pop('reset', None)
                value['notice'] = dict(id=row['id'], level=value['level'], reason=reason,
                                      phase=row.get('phase'), diagnosis='unknown', policy_revision=revision)
                value['trigger'] = dict(value['notice'])
                if evidence:
                    value['trigger']['evidence'] = evidence
            db.execute('INSERT INTO incidents VALUES (?,?,?,?)', (row['id'], key, incident['time'], json.dumps(incident)))
            self.write_row(db, key, value)
            db.execute('DELETE FROM incidents WHERE device=? AND id NOT IN '
                       '(SELECT id FROM incidents WHERE device=? ORDER BY time DESC LIMIT 64)', (key, key))

    def success(self, row):
        """Account for one settled generation; the caller validates its outcome."""
        if row.get('purpose') != 'generation' or not row.get('worker'):
            return
        key = device_key(row.get('identity'))
        if key is None or not self.path.exists():
            # No state was ever stored here: no streak to clear, no level to restore.
            return
        with self.transaction() as db:
            # History and settings are separate databases. Retain a receipt so
            # retrying after a history commit failure cannot count success twice.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='successes'").fetchone():
                receipt = db.execute('SELECT value FROM successes WHERE id=?', (row['id'],)).fetchone()
                if receipt:
                    return json.loads(receipt[0])
            value = current_settings(self.read_row(db, key))
            revision = (row.get('details') or {}).get('compatibility_revision')
            if (not value['automatic'] or revision != policy_revision()
                    or row['started'] < value.get('last_escalation', 0)):
                return
            restoring = (value['origin'] == 'automatic' and value['level'] > 0
                         and not value['recovery_disabled'])
            if not value['unknown_interruptions'] and not restoring:
                # The usual case: a success changes nothing, so nothing is written.
                return
            value['unknown_interruptions'] = 0
            recovery = None
            if restoring:
                value['consecutive_successes'] += 1
                required = recovery_required(value)
                if value['consecutive_successes'] >= required:
                    previous = value['level']
                    value['level'] = max(value['manual_level'], 2 if previous == 3 else 0)
                    if value['level'] < previous:
                        recovery = dict(id=row['id'], reason='consecutive_successes', previous_level=previous,
                                        level=value['level'], successes=required,
                                        policy_revision=revision, time=time.time())
                        value['recovery'] = recovery
                    if value['manual_level'] and value['level'] == value['manual_level']:
                        # Back on the level the user chose: it is their setting again.
                        value['origin'] = 'manual'
                    value['consecutive_successes'] = 0
                    if not value['level']:
                        value['notice'] = None
                    elif value.get('notice'):
                        value['notice']['level'] = value['level']
            result = dict(unknown_interruptions=value['unknown_interruptions'],
                          consecutive_successes=value['consecutive_successes'],
                          recovery_successes_required=recovery_required(value),
                          recovery_disabled=value['recovery_disabled'])
            if recovery:
                result['recovery'] = recovery
            self.write_row(db, key, value)
            db.execute('CREATE TABLE IF NOT EXISTS successes (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT INTO successes VALUES (?,?)', (row['id'], json.dumps(result)))
            db.execute('DELETE FROM successes WHERE rowid NOT IN '
                       '(SELECT rowid FROM successes ORDER BY rowid DESC LIMIT ?)', (SUCCESS_RECEIPTS,))
            return result


def apply(profile, state):
    """Cap work grouping, never the request or backend; settings are explicit consent."""
    value = copy.deepcopy(profile)
    level = state['level']
    if type(level) is not int or not 0 <= level <= 3:
        raise ValueError('Invalid compatibility level')
    changes = {}
    if level:
        engine = value['engine']
        for name, cap in (('head_chunk', LEVELS[level]['head']), ('window_batch', 1), ('head_parallelism', 1)):
            prior = engine.get(name, 0 if name == 'head_chunk' else 1)
            selected = cap if prior == 0 else min(prior, cap)
            if selected != prior:
                engine[name] = selected
                changes['engine.'+name] = dict(before=prior, after=selected)
        if level == 3:
            for section in ('engine', 'decoder'):
                if value.get(section, {}).get('prefetch', True):
                    value.setdefault(section, {})['prefetch'] = False
                    changes[section+'.prefetch'] = dict(before=True, after=False)
        # Never let a policy copy or a saved profile silently undo the cap.
        if isinstance(value.get('policy'), dict):
            value['policy'].update(engine=copy.deepcopy(value['engine']), decoder=copy.deepcopy(value.get('decoder', {})))
    decision = dict(level=level, automatic=state['automatic'], changes=changes,
        reason='User compatibility setting; smaller work groups and optional serialized transfers. '
               'No power-spike or crash-prevention guarantee; geometry, steps and backend unchanged.',
        numerical_class='chunking-may-change-floating-point-results' if changes else 'unchanged')
    decision.update(unknown_interruptions=state.get('unknown_interruptions', 0),
                    consecutive_successes=state.get('consecutive_successes', 0),
                    recovery_successes_required=recovery_required(state),
                    recovery_disabled=state.get('recovery_disabled', False))
    for field in ('origin', 'policy_revision', 'reset', 'notice', 'trigger', 'recovery'):
        if field in state:
            decision[field] = copy.deepcopy(state[field])
    if state.get('origin') == 'automatic' and level:
        decision['reason'] = ('Automatic compatibility after an unfinished request or reported GPU error '
                              'on this policy revision; the cause is unknown. Geometry, steps and backend unchanged.')
    if state.get('available') is False:
        decision.update(available=False, reason='Compatibility unavailable: device identity is missing; profile unchanged.')
    return value, decision


def installed_identity(root):
    try:
        machine = json.loads((io_path(root)/'machine.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        # Setup may not yet have written this optional settings metadata.
        return {}
    if not isinstance(machine, dict):
        return {}
    gpu_uuid = machine.get('gpu_uuid')
    # OS is an installation property here, never a GPU probe.
    import platform
    return dict(gpu_uuid=gpu_uuid, system=platform.system())


def check_installation(root):
    """Called at app startup, without querying or initializing a GPU."""
    root = io_path(root)
    identity = installed_identity(root)
    if device_key(identity) is None:
        return unavailable()
    history = root/'resource-history.sqlite3'
    if history.is_file():
        from .resource_history import ResourceHistory
        ResourceHistory(history).recover_pending()
    return Store(root).status(identity, persist=True)
