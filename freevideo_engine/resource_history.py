"""Local performance and resource measurements, importable without Torch.

Only complete validated requests calibrate future predictions. Failed resource
attempts support OOM/RAM placement recovery; incomplete or external device
failures are excluded from calibration. Interrupted video workers can notify the
separate, user-controlled compatibility settings; history never bans a
configuration or grants permission to run one. The runtime lock owns execution.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

import psutil

from .execution_config import engine_options, decoder_options
from .system import windows


SCHEMA = 6
OUTCOMES = frozenset(('success', 'resource_failure', 'cancelled', 'code_failure',
                      'discarded'))
# Group arithmetic consistently while retaining placement and budget values in
# the original configuration for performance analysis.
INCIDENTAL_CONFIG_KEYS = frozenset(('resident_blocks', 'pin_host_gb', 'gpu_budget_bytes',
    'stream_weights', 'stream_output', 'residual_offload',
    'ram_budget_bytes', 'gpu_budget_gb', 'inference_ram_budget_gb', 'gpu_reserve_bytes',
    'ram_reserve_bytes', 'gpu_reserve_gib', 'ram_reserve_gib', 'vram_gib', 'ram_gib',
    'policy', 'evidence', 'base', 'checkpoint', 'cache', 'metrics', 'output', 'canvas',
    'source_sha256', 'driver_version', 'fa4_dependency'))


class ResourceHistoryError(RuntimeError):
    """The local performance database is unavailable or invalid."""




def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _computation(value):
    if isinstance(value, dict):
        return {key: _computation(item) for key, item in value.items()
                if key not in INCIDENTAL_CONFIG_KEYS}
    if isinstance(value, (list, tuple)):
        return [_computation(item) for item in value]
    return value


def _canonical_config(config, purpose):
    """Normalize an engine-only config or a complete legacy profile.

    A legacy profile's executable sections are engine, decoder and allocator.
    Its name, report, measured budgets and arbitrary descriptive top-level fields
    are not new computations. Unknown knobs *inside* the executable sections
    remain significant. Flat engine mappings remain a supported public API.
    """
    if not isinstance(config, dict):
        raise ValueError('Attempt config must be an engine mapping or legacy profile')
    if 'engine' in config or 'decoder' in config:
        engine = config.get('engine', {})
        decoder = config.get('decoder', {})
    else:
        engine = {key: value for key, value in config.items()
                  if key not in ('allocator_config', 'name', 'description')}
        decoder = {}
    if not isinstance(engine, dict) or not isinstance(decoder, dict):
        raise ValueError('Engine and decoder attempt options must be mappings')
    allocator = config.get('allocator_config', engine.get('allocator_config'))
    # Encoding has different executable options; VDN defaults do not apply.
    resolved_engine = engine_options(engine) if _purpose_phase(purpose) == 'video' else dict(engine)
    resolved_decoder = decoder_options(decoder) if _purpose_phase(purpose) == 'video' else dict(decoder)
    # task/steps also exist in public API geometry. Preserve their original
    # omission until computation_identity resolves that established contract.
    for name in ('task', 'steps'):
        if name not in engine:
            resolved_engine.pop(name, None)
    normalized_engine = _computation(resolved_engine)
    normalized_engine.pop('allocator_config', None)
    return {'engine': normalized_engine, 'decoder': _computation(resolved_decoder),
            'allocator_config': allocator}


def _purpose_phase(purpose):
    return 'encoding' if purpose in ('encoder', 'encoding') else 'video'


def computation_identity(identity, config, geometry, *, purpose='generation'):
    """Canonical computation grouping, independent of placement and free resources.

    UUID and OS are required: a device index/model name is not a local identity.
    Real canvas/steps/task are fixed; seed/prompt text are deliberately omitted.
    Placement is stored in each row for comparing performance of the same arithmetic.
    ``config`` may be engine-only or a legacy profile. Executable engine/decoder
    knobs and allocator settings are retained; profile labels are not identity.
    Derived tokens are omitted because dimensions/frames already encode them;
    text tokens remain measurements, since they are not always known at admission.
    """
    gpu = identity.get('gpu', {})
    gpu_uuid = gpu.get('uuid') if isinstance(gpu, dict) else None
    gpu_uuid = gpu_uuid or identity.get('gpu_uuid')
    system = identity.get('system')
    if not isinstance(gpu_uuid, str) or not gpu_uuid.strip() or not system:
        raise ValueError('Durable resource history requires GPU UUID and operating system')
    computation = _canonical_config(config, purpose)
    canvas = {name: geometry.get(name) for name in ('width', 'height', 'frames')}
    if geometry.get('sampling_plan', {}).get('enabled') or geometry.get('sampling_plan', {}).get('version') == 2:
        canvas['sampling_plan'] = geometry['sampling_plan']
    for key, default in (('steps', 8), ('task', 't2va')):
        configured = computation['engine'].pop(key, None)
        if key in geometry and configured is not None and geometry[key] != configured:
            raise ValueError('Request geometry and engine disagree on ' + key)
        canvas[key] = geometry.get(key, configured if configured is not None else default)
    if any(type(canvas[key]) is not int or canvas[key] <= 0
           for key in ('width', 'height', 'frames', 'steps')):
        raise ValueError('Durable resource history needs positive request geometry and steps')
    if _purpose_phase(purpose) == 'encoding' and computation['engine'].get('operation') == 'idle-preload':
        # Loading weights consumes no prompt or canvas. A different next video
        # does not describe a different weight-loading workload.
        # The original complete request geometry remains in the attempt row.
        canvas = {'scope': 'prompt-independent-weight-loading'}
    return {'gpu_uuid': gpu_uuid.strip(), 'system': str(system),
            'phase': _purpose_phase(purpose), 'config': computation, 'geometry': canvas}


def _boot_id():
    # boot_time is wall-clock based and may shift after clock correction. Only a
    # true boot UUID is used as conclusive restart evidence. On Windows the PID
    # plus process creation FILETIME still detects both absence and PID reuse.
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    except OSError:
        return None


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    try:
        return {'pid': pid, 'created': psutil.Process(pid).create_time(), 'boot_id': _boot_id()}
    except (psutil.Error, OSError) as error:
        raise ResourceHistoryError('Cannot identify the attempt owner: ' + str(error)) from error


def process_state(owner):
    """Return alive/dead/unknown; inability to inspect is never proof of death."""
    try:
        if (type(owner.get('pid')) is not int or owner['pid'] <= 0
                or not isinstance(owner.get('created'), (float, int))):
            return 'unknown'
        current_boot = _boot_id()
        if current_boot and owner.get('boot_id') and current_boot != owner['boot_id']:
            return 'dead'
        if windows():
            from .win32 import process_exited
            exited = process_exited(owner['pid'])
            if exited is True:
                return 'dead'
            if exited is None:
                return 'unknown'
            # An unsignaled handle still needs the creation-time check below:
            # the current PID may belong to a different, live process.
        process = psutil.Process(owner['pid'])
        if process.create_time() != owner['created']:
            return 'dead'
        if process.status() == psutil.STATUS_ZOMBIE:
            return 'dead'
        return 'alive'
    except psutil.NoSuchProcess:
        return 'dead'
    except (psutil.Error, OSError, KeyError, TypeError, ValueError):
        return 'unknown'


class ResourceHistory:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            if existed and self.path.is_file() and self.path.stat().st_size == 0:
                raise ResourceHistoryError('Existing resource history is empty; retain it for diagnosis rather than discarding measurements')
            with self._transaction() as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ResourceHistoryError('Resource history failed its integrity check')
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if existed and not {'metadata', 'attempts'}.issubset(tables):
                    raise ResourceHistoryError('Existing resource history has an incomplete schema; refusing to recreate missing measurements')
                db.execute('CREATE TABLE IF NOT EXISTS metadata (version INTEGER NOT NULL)')
                versions = db.execute('SELECT version FROM metadata').fetchall()
                if not versions:
                    if existed:
                        raise ResourceHistoryError('Existing resource history lost its schema version')
                    db.execute('INSERT INTO metadata VALUES (?)', (SCHEMA,))
                elif len(versions) != 1 or versions[0][0] not in (1, 2, 3, 4, 5, SCHEMA):
                    raise ResourceHistoryError('Unsupported resource history schema; retain it for diagnosis')
                db.execute('''CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, computation_key TEXT NOT NULL, evidence_key TEXT NOT NULL,
                    state TEXT NOT NULL, started REAL NOT NULL, settled REAL,
                    identity_json TEXT NOT NULL, config_json TEXT NOT NULL, geometry_json TEXT NOT NULL,
                    owner_json TEXT NOT NULL, worker_json TEXT, purpose TEXT NOT NULL,
                    phase TEXT, details_json TEXT NOT NULL, observation_json TEXT)''')
                columns={row[1] for row in db.execute('PRAGMA table_info(attempts)')}
                if 'hazard_key' in columns:
                    db.execute('ALTER TABLE attempts RENAME COLUMN hazard_key TO computation_key')
                db.execute('DROP INDEX IF EXISTS attempts_hazard')
                db.execute('CREATE INDEX IF NOT EXISTS attempts_computation ON attempts(computation_key,state)')
                db.execute('CREATE INDEX IF NOT EXISTS attempts_observations ON attempts(state, identity_json, started)')
                # Version 6 retires device-failure quarantine and its consent
                # table. Purge old blockers, preserving successful measurements
                # and useful RAM/OOM outcomes. Repeated opens are idempotent.
                db.execute("DELETE FROM attempts WHERE state IN ('device_lost','unacknowledged')")
                db.execute('DROP TABLE IF EXISTS retry_acknowledgments')
                if versions and versions[0][0] != SCHEMA:
                    for row in db.execute('SELECT id,details_json FROM attempts').fetchall():
                        details=json.loads(row['details_json'])
                        details.pop('hazard_key_migration',None)
                        details.pop('hazard_key_migrations',None)
                        db.execute('UPDATE attempts SET details_json=? WHERE id=?',(_json(details),row['id']))
                    db.execute('UPDATE metadata SET version=?',(SCHEMA,))
        except (OSError, sqlite3.Error) as error:
            raise ResourceHistoryError('Resource measurement history is unavailable: ' + str(error)) from error


    @contextmanager
    def _transaction(self):
        db = None
        try:
            db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA secure_delete=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except (sqlite3.Error, OSError) as error:
            if db is not None:
                db.rollback()
            raise ResourceHistoryError('Cannot record resource measurements: ' + str(error)) from error
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        result['outcome'] = result['state']
        for key in list(result):
            if key.endswith('_json'):
                value = result.pop(key)
                try:
                    result[key[:-5]] = json.loads(value) if value is not None else None
                except ValueError as error:
                    raise ResourceHistoryError('Invalid stored resource-history record; retain it for diagnosis') from error
        return result

    @staticmethod
    def _request_was_cancelled(row):
        # A Windows Job can exit before the CLI's exception handler settles its
        # attempt. The bridge's durable, request-local cancellation survives it.
        artifacts = (row.get('details') or {}).get('decision_evidence', {}).get('request_artifacts')
        if not isinstance(artifacts, str) or not artifacts:
            return False
        try:
            artifacts = Path(artifacts).resolve()
            with (artifacts.parent / 'comfy-request.json').open('rb') as stream:
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                return False
            request = json.loads(raw)
            return (isinstance(request, dict) and request.get('status') == 'cancelled'
                    and isinstance(request.get('output'), str) and bool(request['output'])
                    and Path(request['output']).with_suffix('.artifacts').resolve() == artifacts)
        except (OSError, ValueError, TypeError):
            return False

    def _recover(self, db):
        recovered=[]
        for row in db.execute("SELECT * FROM attempts WHERE state='pending'").fetchall():
            if process_state(json.loads(row['owner_json'])) != 'dead':
                continue
            if row['worker_json'] and process_state(json.loads(row['worker_json'])) != 'dead':
                continue
            record = self._row(row)
            recovered.append(row['id'])
            if self._request_was_cancelled(record):
                details = dict(record['details'], kind='cancelled',
                    reason='User cancelled this ComfyUI request; controller and worker have exited.')
                db.execute("UPDATE attempts SET state='cancelled',settled=?,details_json=? WHERE id=?",
                           (time.time(), _json(details), row['id']))
                continue
            from .compatibility import Store
            Store(self.path.parent).incident(record, 'unfinished_request_after_owner_and_worker_exit')
            db.execute('DELETE FROM attempts WHERE id=?',(row['id'],))
        return recovered

    def recover_pending(self):
        """Settle known cancellations and discard other stopped incomplete work."""
        with self._transaction() as db:
            return self._recover(db)

    def begin(self, identity, config, geometry, *, purpose='generation', owner=None,
              evidence=None):
        identifier=uuid.uuid4().hex
        owner=process_identity() if owner is None else owner
        if process_state(owner)!='alive':
            raise ResourceHistoryError('Cannot identify the live measurement owner')
        computation=_digest(computation_identity(identity,config,geometry,purpose=purpose))
        from .compatibility import policy_revision
        with self._transaction() as db:
            self._recover(db)
            db.execute("""INSERT INTO attempts (id,computation_key,evidence_key,state,started,
                identity_json,config_json,geometry_json,owner_json,purpose,details_json)
                VALUES (?,?,?,'pending',?,?,?,?,?,?,?)""",
                (identifier,computation,_digest({'identity':identity,'config':config,'geometry':geometry}),
                 time.time(),_json(identity),_json(config),_json(geometry),_json(owner),purpose,
                 _json({'decision_evidence':evidence or {}, 'compatibility_revision':policy_revision()})))
        return identifier

    def attach_worker(self, attempt_id, pid, *, phase=None):
        worker=process_identity(pid)
        with self._transaction() as db:
            changed=db.execute("UPDATE attempts SET worker_json=?,phase=COALESCE(?,phase) WHERE id=? AND state='pending'",
                (_json(worker),phase,attempt_id)).rowcount
            if changed!=1:raise ResourceHistoryError('Cannot attach a worker to an absent/settled attempt')

    def set_phase(self, attempt_id, phase):
        with self._transaction() as db:
            if db.execute("UPDATE attempts SET phase=? WHERE id=? AND state='pending'",(str(phase),attempt_id)).rowcount!=1:
                raise ResourceHistoryError('Cannot update an absent/settled attempt')

    @staticmethod
    def _observation(value, row):
        if value is None:
            return None
        geometry = json.loads(row['geometry_json'])
        from .two_pass import steps as sampling_steps
        steps = sampling_steps(geometry['sampling_plan']) if geometry.get('sampling_plan') else geometry.get('steps', 8)
        enriched_geometry = dict(geometry)
        provided_geometry = value.get('geometry', {})
        if not isinstance(provided_geometry, dict):
            raise ValueError('Observed geometry must be an object')
        for key in ('width', 'height', 'frames', 'steps', 'task', 'video_tokens', 'latent_frames', 'sampling_plan'):
            expected = geometry.get(key, 8 if key == 'steps' else None)
            if key in provided_geometry and expected is not None and provided_geometry[key] != expected:
                raise ValueError('Observed geometry differs from the durably recorded request: ' + key)
        enriched_geometry.update(provided_geometry)
        text_tokens = value.get('text_tokens', enriched_geometry.get('text_tokens'))
        if text_tokens is not None:
            if type(text_tokens) is not int or text_tokens < 0:
                raise ValueError('Observed text token count must be a nonnegative integer')
            if 'text_tokens' in geometry and geometry['text_tokens'] != text_tokens:
                raise ValueError('Observed text token count differs from the request')
            enriched_geometry['text_tokens'] = text_tokens
        if (row['purpose'] not in ('generation', 'validation', 'full_request')
                or value.get('full_request') is not True or value.get('validated') is not True
                or value.get('completed_steps') != steps
                or value.get('completed_frames') != geometry['frames']
                or value.get('media_verified') is not True or value.get('metrics_complete') is not True):
            raise ValueError('Only a verified complete requested video/audio request supplies resource calibration')
        times = value.get('step_seconds')
        if (not isinstance(times, list) or len(times) != steps
                or any(type(number) not in (int, float) or not math.isfinite(number) or number <= 0 for number in times)):
            raise ValueError('Resource calibration needs all actual step timings; no extrapolation')
        for key in ('peak_reserved_bytes', 'whole_gpu_peak_bytes', 'ram_peak_bytes'):
            if type(value.get(key)) is not int or value[key] <= 0:
                raise ValueError('Resource calibration needs a complete positive ' + key)
        stages = value.get('stage_seconds')
        if (not isinstance(stages, dict) or not stages
                or any(type(number) not in (int, float) or not math.isfinite(number) or number < 0 for number in stages.values())):
            raise ValueError('Resource calibration needs finite measured phase times')
        if not isinstance(value.get('ram_metric'), str) or not value['ram_metric']:
            raise ValueError('Resource calibration must name the platform RAM metric')
        value = dict(value, geometry=enriched_geometry,
                     scope='complete_request_measurement_not_numerical_equivalence')
        return _json(value)

    def finish(self, attempt_id, outcome, *, details=None, observation=None):
        """Keep measurements useful for optimization after the worker ends."""
        if outcome not in OUTCOMES:
            raise ValueError('Unknown attempt outcome: ' + str(outcome))
        with self._transaction() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
            if row is None or row['state'] != 'pending':
                raise ResourceHistoryError('Cannot settle an absent/already settled attempt')
            if observation is not None and outcome != 'success':
                raise ValueError('Failed attempts cannot certify resource calibration')
            if outcome == 'discarded':
                if (details or {}).get('kind') in ('cuda_error', 'unknown_worker_exit'):
                    from .compatibility import Store
                    Store(self.path.parent).incident(self._row(row), details['kind'])
                db.execute('DELETE FROM attempts WHERE id=?',(attempt_id,))
                return {'id':attempt_id,'outcome':'discarded'}
            observed = self._observation(observation, row)
            merged = json.loads(row['details_json'])
            merged.update(details or {})
            db.execute('UPDATE attempts SET state=?,settled=?,details_json=?,observation_json=? WHERE id=?',
                       (outcome, time.time(), _json(merged), observed, attempt_id))
            return self._row(db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone())



    def attempt(self, identifier):
        """Read one retained attempt without scanning a user's entire history."""
        with self._transaction() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=?', (identifier,)).fetchone()
            return self._row(row) if row is not None else None

    def recent(self, identity=None, geometry=None, *, purpose=None, limit=20):
        """The newest settled attempts (any outcome) of one identity and geometry,
        newest first: what recently failed on this device, without a full scan."""
        if type(limit) is not int or limit <= 0:
            raise ValueError('Attempt limit must be a positive integer')
        clauses, arguments = ["state!='pending'"], []
        for name, value in (('identity', identity), ('geometry', geometry)):
            if value is not None:
                clauses.append(name + '_json=?')
                arguments.append(_json(value))
        if purpose is not None:
            clauses.append('purpose=?')
            arguments.append(purpose)
        query = ('SELECT * FROM attempts WHERE ' + ' AND '.join(clauses)
                 + ' ORDER BY started DESC,id DESC LIMIT ?')
        with self._transaction() as db:
            return [self._row(row) for row in db.execute(query, arguments + [limit])]

    def attempts(self):
        with self._transaction() as db:
            return [self._row(row) for row in db.execute('SELECT * FROM attempts ORDER BY started')]

    def observations(self, identity=None, config=None, geometry=None, *, limit=None, include_details=True):
        """Measured complete requests only; filter exact software/config identity.

        Unfiltered queries are diagnostic, not permission to transfer knowledge
        to a different device or compute build. No probe extrapolation is stored.
        Filter before decoding JSON; long histories must not load every device's
        historical reports to predict one request. ``limit`` takes the newest
        matching rows, returned chronologically, without deleting any evidence.
        """
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError('Observation limit must be a positive integer')
        if type(include_details) is not bool:
            raise ValueError('include_details must be a boolean')
        clauses = ["state='success'", 'observation_json IS NOT NULL']
        arguments = []
        for name, value in (('identity', identity), ('config', config), ('geometry', geometry)):
            if value is not None:
                clauses.append(name + '_json=?')
                arguments.append(_json(value))
        columns = ('*' if include_details else 'id,state,started,settled,purpose,phase,'
                   'identity_json,config_json,geometry_json,observation_json')
        query = 'SELECT ' + columns + ' FROM attempts WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY started DESC,id DESC' if limit is not None else ' ORDER BY started,id'
        if limit is not None:
            query += ' LIMIT ?'
            arguments.append(limit)
        with self._transaction() as db:
            rows = [self._row(row) for row in db.execute(query, arguments)]
        return list(reversed(rows)) if limit is not None else rows
