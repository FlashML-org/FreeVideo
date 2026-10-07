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


LEVELS = (
    dict(level=0, en='Off · automatic performance', zh='关闭 · 自动性能'),
    dict(level=1, en='Light · heads ≤ 4, window 1', zh='轻度 · head ≤ 4，window 1', head=4),
    dict(level=2, en='Compatible · heads ≤ 2, window 1', zh='兼容 · head ≤ 2，window 1', head=2),
    dict(level=3, en='Strong · head 1, window 1, no prefetch', zh='加强 · head 1，window 1，关闭预取', head=1),
)


@lru_cache(maxsize=1)
def policy_revision():
    """Same identity in the frozen launcher and its deployed engine, without Torch.

    Build versions expire automatic settings on release upgrades; source hashes
    also cover a git checkout whose package version has not changed.
    """
    from . import __version__
    root = Path(__file__).parent
    if getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None):
        root = Path(sys._MEIPASS) / 'engine-source' / 'freevideo_engine'
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
    value.update(origin=origin, manual_level=manual_level, policy_revision=revision)
    return value


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


class Store:
    def __init__(self, root):
        self.path = Path(root) / 'compatibility.sqlite3'

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.path), timeout=10, isolation_level=None)) as db:
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
        if not self.path.exists():
            return dict(current_settings(dict(level=0, automatic=True, notice=None)), levels=LEVELS, available=True)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
            value = self.read_row(db, key)
        selected = current_settings(value)
        if persist and selected != value:
            with self.transaction() as db:
                # Re-read under the lock so a concurrent user edit always wins.
                selected = current_settings(self.read_row(db, key))
                self.write_row(db, key, selected)
        return dict(selected, levels=LEVELS, available=True)

    def set(self, identity, level, automatic):
        if type(level) is not int or not 0 <= level <= 3 or type(automatic) is not bool:
            raise ValueError('Compatibility level must be 0–3; automatic must be a boolean')
        key = device_key(identity)
        if key is None:
            return unavailable()
        with self.transaction() as db:
            value = dict(level=level, automatic=automatic, notice=None, origin='manual',
                         manual_level=level, policy_revision=policy_revision())
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

    def incident(self, row, reason):
        # Only a launched video worker can be a load-related interrupted attempt.
        if row.get('purpose') != 'generation' or not row.get('worker'):
            return
        # Prism has its own plans and retries; its crashes must not tighten
        # MiniMax H3's settings on the same card.
        if (row.get('config') or {}).get('model') == 'prism':
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
                policy_revision=revision, applicable=revision == policy_revision())
            db.execute('INSERT INTO incidents VALUES (?,?,?,?)', (row['id'], key, incident['time'], json.dumps(incident)))
            # Do not re-enable a user's explicit opt-out. One incident takes the
            # default to 2; another while compatible takes it to 3, at most once.
            if value['automatic'] and incident['applicable']:
                value['level'] = min(3, max(2, value['level'] + 1))
                value['origin'] = 'automatic'
                value.pop('reset', None)
                value['notice'] = dict(id=row['id'], level=value['level'], reason=reason,
                                      phase=row.get('phase'), diagnosis='unknown', policy_revision=revision)
                value['trigger'] = dict(value['notice'])
            self.write_row(db, key, value)
            db.execute('DELETE FROM incidents WHERE device=? AND id NOT IN '
                       '(SELECT id FROM incidents WHERE device=? ORDER BY time DESC LIMIT 64)', (key, key))


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
    for field in ('origin', 'policy_revision', 'reset', 'notice', 'trigger'):
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
        machine = json.loads((Path(root)/'machine.json').read_text(encoding='utf-8'))
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
    root = Path(root)
    identity = installed_identity(root)
    if device_key(identity) is None:
        return unavailable()
    history = root/'resource-history.sqlite3'
    if history.is_file():
        from .resource_history import ResourceHistory
        ResourceHistory(history).recover_pending()
    return Store(root).status(identity, persist=True)
