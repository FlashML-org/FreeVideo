"""Small local, torch-free forecasts from verified complete requests.

Predictions are measurements generalized to another input, not permission to
change arithmetic or evidence that a workload fits. Identity and actual placement
must match. Repeated requests at one geometry count as one geometry for model
selection, including its leave-one-geometry-out validation.
"""
import hashlib
import json
import math
from statistics import median

from .geometry import geometry as resolve_geometry
from .execution_config import engine_options, decoder_options


STAGES = ('load_seconds', 'sample_seconds', 'vae_load_seconds', 'video_decode_seconds',
          'audio_load_decode_seconds', 'encode_seconds', 'decode_save_seconds', 'work_seconds',
          'text_encode_seconds')
RESOURCES = ('peak_reserved_bytes', 'whole_gpu_peak_bytes', 'ram_peak_bytes')
_ENGINE_METADATA = frozenset(('base', 'checkpoint', 'cache', 'source_id', 'fa4_dependency',
    'resident_weight_bytes', 'pinned_model_bytes', 'pinned_host_allocated_bytes', 'fp8_linears',
    'adaln_table_cache_hits', 'reuse_block_outputs', 'name', 'description'))
_BUDGETS = frozenset(('gpu_budget_bytes', 'ram_budget_bytes', 'gpu_budget_gb',
    'inference_ram_budget_gb', 'gpu_reserve_gib', 'ram_reserve_gib', 'policy', 'evidence'))
_CACHE_SENSITIVE_STAGES = ('load_seconds', 'work_seconds')


def _allocator_limit(value):
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError('Benchmark allocator limit must be positive integer bytes, or None')
    return value


def _cache_state(context):
    if not isinstance(context, dict):
        return 'unknown'
    explicit = context.get('adaln_cache_state')
    if explicit in ('cold', 'warm', 'partial'):
        return explicit
    hits = context.get('adaln_table_cache_hits')
    if type(hits) is int and 0 <= hits <= 50:
        return 'cold' if hits == 0 else 'warm' if hits == 50 else 'partial'
    return 'unknown'


def profile_options(profile):
    """Executable profile identity, retaining placement and unknown compute knobs."""
    if not isinstance(profile, dict):
        raise ValueError('Forecast profile must be a mapping')
    wrapped = 'engine' in profile or 'decoder' in profile
    engine = profile.get('engine', {}) if wrapped else profile
    decoder = profile.get('decoder', {}) if wrapped else {}
    if not isinstance(engine, dict) or not isinstance(decoder, dict):
        raise ValueError('Forecast engine/decoder must be mappings')
    engine = {key: value for key, value in engine_options(engine).items()
              if key not in _ENGINE_METADATA and key not in _BUDGETS
              and key not in ('allocator_config', 'benchmark_allocator_limit_bytes')}
    return {'engine': engine, 'decoder': decoder_options(decoder),
            'benchmark_allocator_limit_bytes': _allocator_limit(profile.get('benchmark_allocator_limit_bytes')),
            'allocator_config': profile.get('allocator_config',
                profile.get('engine', {}).get('allocator_config') if wrapped else None)}


def profile_key(profile):
    return hashlib.sha256(json.dumps(profile_options(profile), sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _finite(value, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except (OverflowError, ValueError):
        return False


def _canvas(value, options, text_tokens=None):
    width, height = value.get('width', 1344), value.get('height', 768)
    canvas = resolve_geometry(width, height, frames=value.get('frames'),
                              seconds=value.get('seconds') if value.get('frames') is None else None)
    canvas['steps'] = options['engine']['steps']
    canvas['task'] = options['engine']['task']
    if value.get('sampling_plan', {}).get('enabled') or value.get('sampling_plan', {}).get('version') == 2:
        canvas['sampling_plan'] = value['sampling_plan']
    if type(canvas['steps']) is not int or canvas['steps'] <= 0:
        raise ValueError('Forecast steps must be a positive integer')
    from .media_request import TASKS
    if canvas['task'] not in TASKS:
        raise ValueError('Unknown forecast task')
    for key in ('steps', 'task'):
        if key in value and value[key] != canvas[key]:
            raise ValueError('Forecast geometry and engine disagree on ' + key)
    text = text_tokens if text_tokens is not None else value.get('text_tokens')
    if text is not None and (type(text) is not int or text < 0):
        raise ValueError('Forecast text token count must be a nonnegative integer')
    canvas['text_tokens'] = text
    for name in ('reference_video_tokens', 'reference_audio_tokens'):
        count = value.get(name, 0)
        if type(count) is not int or count < 0:
            raise ValueError('Invalid reference token count')
        canvas[name] = count
    return canvas


def _features(canvas, assumed_text):
    p = (canvas['height'] // 32) * (canvas['width'] // 32)
    l = canvas['latent_frames']
    n = p * l
    text = canvas['text_tokens'] if canvas['text_tokens'] is not None else assumed_text
    # These are regression features, not claimed exact kernel FLOP counts.
    # Video windows are linear in sequence length at fixed spatial/window size;
    # their per-frame work grows with spatial tokens. Global-query work can be
    # quadratic; text length and audio duration are represented independently.
    global_queries = text + canvas['frames'] + canvas.get('reference_video_tokens', 0) + canvas.get('reference_audio_tokens', 0)
    return {'constant': 1., 'tokens': n / 72576., 'quadratic': (n / 72576.) ** 2,
            'window': n * p * min(l, 15) / (72576. * 1008 * 15),
            'global': global_queries * (n + global_queries) / (610. * (72576 + 610)),
            'pixels': canvas['width'] * canvas['height'] * canvas['frames'] / (1344. * 768 * 243),
            'spatial': p / 1008., 'length': canvas['frames'] / 243.,
            'text': ((text + 1) / 368.) ** 2,
            'text_length': text + 1., 'latent_length': l,
            'reference_rows': 1. + canvas.get('reference_video_tokens', 0) + canvas.get('reference_audio_tokens', 0)}


def _geometry_key(canvas, assumed_text):
    return (canvas['width'], canvas['height'], canvas['frames'],
            canvas['text_tokens'] if canvas['text_tokens'] is not None else assumed_text,
            canvas.get('reference_video_tokens', 0), canvas.get('reference_audio_tokens', 0))


def _distance(a, b):
    return sum(abs(math.log(max(a[key], 1e-9) / max(b[key], 1e-9)))
               for key in ('spatial', 'latent_length', 'text_length', 'reference_rows'))


def _solve(matrix, target):
    size = len(target)
    rows = [list(row) + [target[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda index: abs(rows[index][col]))
        if abs(rows[pivot][col]) < 1e-10:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [value / scale for value in rows[col]]
        for index in range(size):
            if index == col:
                continue
            scale = rows[index][col]
            rows[index] = [a - scale * b for a, b in zip(rows[index], rows[col])]
    answer = [row[-1] for row in rows]
    return answer if all(math.isfinite(value) for value in answer) else None


def _fit(points, names):
    # Three small Huber reweighting rounds reduce one unusual cold/thermal run's
    # influence. Group medians already prevent identical repeats dominating fit.
    weights = [1.] * len(points)
    coefficients = None
    for _ in range(3):
        matrix = [[sum(weight * point['features'][a] * point['features'][b]
                       for point, weight in zip(points, weights))
                   for b in names] for a in names]
        target = [sum(weight * point['features'][a] * point['value']
                      for point, weight in zip(points, weights)) for a in names]
        coefficients = _solve(matrix, target)
        if coefficients is None:
            return None
        residuals = [abs(point['value'] - _evaluate(coefficients, names, point['features'])) for point in points]
        scale = max(median(residuals) * 1.5, median(point['value'] for point in points) * .005, 1e-9)
        weights = [min(1., scale / max(error, 1e-9)) for error in residuals]
    return coefficients


def _evaluate(coefficients, names, features):
    return sum(coefficient * features[name] for coefficient, name in zip(coefficients, names))


def _bases(name):
    if name in ('sample_seconds', 'work_seconds'):
        return [('constant', 'tokens'), ('constant', 'tokens', 'window'),
                ('constant', 'tokens', 'quadratic'), ('constant', 'tokens', 'window', 'global'),
                ('constant', 'tokens', 'quadratic', 'global')]
    if name in RESOURCES:
        return [('constant', 'tokens'), ('constant', 'spatial'),
                ('constant', 'tokens', 'spatial'), ('constant', 'tokens', 'length'),
                ('constant', 'tokens', 'global')]
    if name == 'text_encode_seconds':
        return [('constant', 'text'), ('constant', 'text', 'spatial')]
    if name in ('load_seconds', 'vae_load_seconds'):
        return [('constant',)]
    if name == 'audio_load_decode_seconds':
        return [('constant', 'length')]
    return [('constant', 'pixels'), ('constant', 'pixels', 'spatial')]


def _validated_model(points, name):
    if len(points) < 6:
        return None
    candidates = []
    for names in _bases(name):
        if len(points) < len(names) + 3:
            continue
        errors = []
        for index, held_out in enumerate(points):
            trained = _fit(points[:index] + points[index + 1:], names)
            if trained is None:
                break
            forecast = _evaluate(trained, names, held_out['features'])
            if not _finite(forecast):
                break
            errors.append(abs(forecast - held_out['value']) / max(held_out['value'], 1e-9))
        if len(errors) != len(points):
            continue
        middle, worst = median(errors), max(errors)
        if middle > .3 or worst > .75:
            continue
        coefficients = _fit(points, names)
        if coefficients is not None:
            candidates.append({'names': names, 'coefficients': coefficients,
                               'median_error': middle, 'max_error': worst,
                               'score': middle + .15 * worst + .003 * len(names)})
    return min(candidates, key=lambda item: item['score']) if candidates else None


def _sparse_scale(name, source, target):
    ratio = lambda key: target[key] / max(source[key], 1e-9)
    if name in ('load_seconds', 'vae_load_seconds'):
        choices = [1.]
    elif name in RESOURCES:
        choices = [1., ratio('tokens'), ratio('spatial'), ratio('global'), ratio('text_length')]
        return .6 + .3 * ratio('tokens') + .1 * ratio('text_length'), min(choices), max(choices)
    elif name in ('sample_seconds', 'work_seconds'):
        choices = [ratio('tokens'), ratio('window'), ratio('quadratic'), ratio('global')]
        center = .45 * choices[0] + .4 * choices[1] + .15 * choices[3]
        return center, min(choices), max(choices)
    elif name == 'text_encode_seconds':
        choices = [1., ratio('text')]
    elif name == 'audio_load_decode_seconds':
        choices = [1., ratio('length')]
    elif name == 'unattributed_seconds':
        choices = [1., ratio('pixels')]
    else:
        choices = [ratio('pixels')]
    return sum(choices) / len(choices), min(choices), max(choices)


def _unknown(unit, reason):
    return {'estimate': None, 'lower': None, 'upper': None, 'unit': unit,
            'confidence': 'unknown', 'method': 'unavailable', 'evidence_count': 0,
            'geometry_count': 0, 'reason': reason}


def _predict_metric(records, target, name, assumed_text, unknown_text):
    unit = 'bytes' if name in RESOURCES else 'seconds'
    grouped = {}
    for record in records:
        observation = record['observation']
        stages = observation.get('stage_seconds')
        value = observation.get(name) if name in RESOURCES else (stages.get(name) if isinstance(stages, dict) else None)
        if not _finite(value, positive=name in RESOURCES):
            continue
        key = _geometry_key(record['canvas'], assumed_text)
        point = grouped.setdefault(key, {'features': _features(record['canvas'], assumed_text),
                                         'values': [], 'ids': []})
        point['values'].append(float(value))
        point['ids'].append(record['id'])
    points = list(grouped.values())
    if not points:
        return _unknown(unit, 'No matching complete local request supplies this metric')
    for point in points:
        point['value'] = median(point['values'])
    features = _features(target, assumed_text)
    exact = grouped.get(_geometry_key(target, assumed_text))
    nearest = min(points, key=lambda point: _distance(point['features'], features))
    distance = _distance(nearest['features'], features)
    outside = any(features[key] < min(point['features'][key] for point in points) - 1e-9
                  or features[key] > max(point['features'][key] for point in points) + 1e-9
                  for key in ('spatial', 'latent_length', 'text_length', 'reference_rows'))
    extrapolation = outside or distance > .6
    model_points = sorted(points, key=lambda point: _distance(point['features'], features))[:24]
    model = None if exact else _validated_model(model_points, name)
    validation = None
    weak_fit = None
    ids = []
    if exact:
        center = exact['value']
        spread = max(abs(value - center) for value in exact['values']) / max(center, 1e-9)
        fraction = max(.15 if len(exact['values']) >= 3 else .35, spread)
        low, high = center * (1 - fraction), center * (1 + fraction)
        method = 'same_geometry_observations'
        confidence = 'moderate' if len(exact['values']) >= 3 and spread <= .25 else 'weak'
        ids = exact['ids']
    elif model:
        center = _evaluate(model['coefficients'], model['names'], features)
        if not _finite(center):
            model = None
        else:
            scaled, _, _ = _sparse_scale(name, nearest['features'], features)
            # Blend only well-supported interpolation. Large extrapolation is
            # left explicitly weak; it never becomes capacity certification.
            center = .9 * center + .1 * nearest['value'] * scaled
            observed_spread = max(abs(value - point['value']) / max(point['value'], 1e-9)
                                  for point in model_points for value in point['values'])
            fraction = max(.2, 1.5 * model['max_error'], observed_spread) + (.5 + .3 * distance if extrapolation else 0)
            low, high = center * (1 - fraction), center * (1 + fraction)
            method = 'geometry_model_with_neighbor_blend'
            confidence = 'weak' if extrapolation or observed_spread > .3 else ('strong' if len(model_points) >= 8 and model['max_error'] < .1 else 'moderate')
            validation = {'method': 'leave_one_geometry_out', 'geometry_count': len(model_points),
                          'median_relative_error': model['median_error'], 'max_relative_error': model['max_error'],
                          'features': list(model['names'])}
            ids = [identifier for point in model_points for identifier in point['ids']]
    if not exact and model is None:
        scaled, least, most = _sparse_scale(name, nearest['features'], features)
        center = nearest['value'] * scaled
        uncertainty = .4 + min(1.5, distance * .5)
        low = min(nearest['values']) * least * max(0., 1 - uncertainty)
        high = max(nearest['values']) * most * (1 + uncertainty)
        method, confidence, ids = 'sparse_geometry_scaling', 'weak', nearest['ids']
        # Two complete geometries can expose a fixed sampling cost that a
        # zero-intercept FLOP ratio misses. This is an unvalidated hypothesis,
        # not a substitute for the held-out model above. Keep both the broad
        # geometry-feature envelope and empirical variation in its interval.
        if name == 'sample_seconds' and len({point['features']['tokens'] for point in model_points}) >= 2:
            names = ('constant', 'tokens')
            fitted = _fit(model_points, names)
            if fitted is not None and fitted[0] >= 0 and fitted[1] > 0:
                empirical = _evaluate(fitted, names, features)
                if _finite(empirical):
                    spread = max(abs(value - _evaluate(fitted, names, point['features'])) /
                                 max(point['value'], 1e-9)
                                 for point in model_points for value in point['values'])
                    fraction = max(.5, spread) + (.25 * distance if extrapolation else 0.)
                    low, high = min(low, empirical * max(0., 1 - fraction)), max(high, empirical * (1 + fraction))
                    center, method = empirical, 'weak_affine_token_model'
                    ids = [identifier for point in model_points for identifier in point['ids']]
                    weak_fit = {'features': list(names), 'coefficients': fitted,
                                'geometry_count': len(model_points), 'held_out_validated': False,
                                'assumption': 'Nonnegative fixed sampling cost plus positive cost per video token; spatial/window/input ambiguity remains in the wide range'}
    if unknown_text and name in ('sample_seconds', 'work_seconds', 'text_encode_seconds') + RESOURCES:
        confidence = 'weak'
        low, high = min(low, center * .5), max(high, center * 1.5)
    result = {'estimate': max(0., center), 'lower': max(0., min(low, center)),
              'upper': max(high, center), 'unit': unit, 'method': method,
              'confidence': confidence, 'evidence_count': sum(len(point['values']) for point in points),
              'geometry_count': len(points), 'extrapolation': extrapolation,
              'evidence_ids': ids, 'validation': validation}
    if weak_fit is not None:
        result['weak_fit'] = weak_fit
    if unit == 'bytes':
        for key in ('estimate', 'lower', 'upper'):
            result[key] = round(result[key])
    return result


def _work_from_phases(records, target, assumed_text, unknown_text):
    """A sparse full-worker estimate must not scale fixed loading by geometry.

    Use only rows with all disjoint phases and a nonnegative measured remainder.
    The remainder includes retained-artifact I/O and cleanup; it is not secretly
    treated as sampling. Summed bounds are empirical uncertainty, not a coverage
    guarantee, and preserve cold/thermal variation in the component models.
    """
    names = ('load_seconds', 'sample_seconds', 'decode_save_seconds')
    complete = []
    for record in records:
        stages = record['observation'].get('stage_seconds')
        if not isinstance(stages, dict) or not all(_finite(stages.get(name)) for name in names + ('work_seconds',)):
            continue
        accounted = sum(stages[name] for name in names)
        residual = stages['work_seconds'] - accounted
        if residual < -max(1e-6, accounted * 1e-8):
            continue
        observation = dict(record['observation'], stage_seconds=dict(stages, unattributed_seconds=max(0., residual)))
        complete.append(dict(record, observation=observation))
    if not complete:
        return _unknown('seconds', 'Sparse worker forecast needs measured load, sampling, decode/save and total time from the same complete requests')
    components = {name: _predict_metric(complete, target, name, assumed_text, unknown_text)
                  for name in names + ('unattributed_seconds',)}
    if any(value['estimate'] is None for value in components.values()):
        return _unknown('seconds', 'A required worker phase cannot be estimated')
    rank = {'unknown': 0, 'weak': 1, 'moderate': 2, 'strong': 3}
    result = {key: sum(value[key] for value in components.values()) for key in ('estimate', 'lower', 'upper')}
    result.update(unit='seconds', method='phase_sum_with_observed_overhead',
                  confidence=min((value['confidence'] for value in components.values()), key=rank.__getitem__),
                  evidence_count=len(complete), geometry_count=len({_geometry_key(row['canvas'], assumed_text) for row in complete}),
                  evidence_ids=list(dict.fromkeys(identifier for value in components.values() for identifier in value['evidence_ids'])),
                  extrapolation=any(value['extrapolation'] for value in components.values()), validation=None,
                  components=components,
                  reason='Sum independently forecast load, sampling, decode/save and measured residual; fixed model loading is not scaled by video size')
    return result


def predict(observations, identity, profile, geometry, *, text_tokens=None, execution_context=None):
    """Forecast this exact configuration on an unseen canvas/input using local history.

    Timings are the named stored phases: encode_seconds is media encoding, not
    text encoding. work_seconds is the video worker, not the complete text-to-MP4
    request. Missing text-encoder data stays unknown. Returned ranges describe
    empirical/model uncertainty, not statistical coverage guarantees.
    """
    options = profile_options(profile)
    target = _canvas(geometry, options, text_tokens)
    cache_state = _cache_state(execution_context)
    if execution_context is not None and not isinstance(execution_context, dict):
        raise ValueError('Forecast execution context must be a mapping')
    if execution_context and 'allocator_limit_bytes' in execution_context:
        if _allocator_limit(execution_context['allocator_limit_bytes']) != options['benchmark_allocator_limit_bytes']:
            raise ValueError('Forecast execution context and profile disagree on allocator limit')
    key = profile_key(profile)
    records, rejected = [], {}
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    for index, row in enumerate(observations):
        try:
            if row.get('identity') != identity:
                reject('different_local_hardware_or_software')
                continue
            observation = row.get('observation')
            observed_context = observation.get('execution_context', {}) if isinstance(observation, dict) else {}
            if not isinstance(observed_context, dict):
                reject('malformed_execution_context')
                continue
            recorded_profile = dict(row.get('config', {}))
            if 'allocator_limit_bytes' in observed_context:
                cap = _allocator_limit(observed_context['allocator_limit_bytes'])
                if recorded_profile.get('benchmark_allocator_limit_bytes') not in (None, cap):
                    reject('inconsistent_allocator_cap_evidence')
                    continue
                recorded_profile['benchmark_allocator_limit_bytes'] = cap
            if profile_key(recorded_profile) != key:
                reject('different_execution_configuration')
                continue
            if (row.get('outcome', row.get('state')) != 'success' or not isinstance(observation, dict)
                    or row.get('purpose') not in ('generation', 'validation', 'full_request')
                    or any(observation.get(flag) is not True for flag in
                           ('full_request', 'validated', 'media_verified', 'metrics_complete'))):
                reject('not_a_verified_complete_request')
                continue
            original = row.get('geometry', {})
            enriched = dict(original)
            enriched.update(observation.get('geometry', {}))
            if any(type(enriched.get(key)) is not int or enriched[key] <= 0 for key in ('width', 'height', 'frames')):
                reject('missing_observed_geometry')
                continue
            if any(key in original and key in enriched and original[key] != enriched[key]
                   for key in ('width', 'height', 'frames', 'steps', 'task', 'reference_video_tokens', 'reference_audio_tokens')):
                reject('inconsistent_observed_geometry')
                continue
            canvas = _canvas(enriched, options, observation.get('text_tokens'))
            from .two_pass import same_strategy, steps as sampling_steps
            if not same_strategy(canvas, target):
                reject('different_sampling_plan')
                continue
            total_steps = sampling_steps(canvas['sampling_plan']) if canvas.get('sampling_plan') else canvas['steps']
            times = observation.get('step_seconds')
            if (observation.get('completed_frames') != canvas['frames']
                    or observation.get('completed_steps') != total_steps
                    or not isinstance(times, list) or len(times) != total_steps
                    or not all(_finite(value, positive=True) for value in times)):
                reject('incomplete_requested_steps_or_frames')
                continue
            records.append({'id': row.get('id', 'record-' + str(index)), 'canvas': canvas,
                            'observation': observation, 'cache_state': _cache_state(observed_context),
                            'host_threads': observed_context.get('host_threads')})
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
            reject('malformed_record')
    known_text = [record['canvas']['text_tokens'] for record in records if record['canvas']['text_tokens'] is not None]
    assumed_text = round(median(known_text)) if known_text else 367
    unknown_text = target['text_tokens'] is None
    stages = {}
    regimes = {}
    for record in records:
        regimes[record['cache_state']] = regimes.get(record['cache_state'], 0) + 1
    thread_contexts = []
    for record in records:
        context = record['host_threads']
        if context not in thread_contexts:
            thread_contexts.append(context)
    for name in STAGES:
        same_context = (name in _CACHE_SENSITIVE_STAGES and cache_state != 'unknown')
        relevant = [record for record in records if record['cache_state'] == cache_state] if same_context else records
        value = _predict_metric(relevant, target, name, assumed_text, unknown_text)
        if name == 'work_seconds' and value['method'] == 'sparse_geometry_scaling':
            value = _work_from_phases(relevant, target, assumed_text, unknown_text)
        if name in _CACHE_SENSITIVE_STAGES:
            value['cache_context_matched'] = same_context and value['estimate'] is not None
            value['cache_state'] = cache_state
            if same_context and value['estimate'] is None:
                value['reason'] = 'No complete local observation matches the requested %s AdaLN cache regime' % cache_state
        stages[name] = value
    # Different platform counters must never form one RAM response variable.
    ram_metrics = {record['observation'].get('ram_metric')
                   if isinstance(record['observation'].get('ram_metric'), str) else None
                   for record in records if _finite(record['observation'].get('ram_peak_bytes'), positive=True)}
    resources = {name: _predict_metric(records, target, name, assumed_text, unknown_text) for name in RESOURCES}
    if len(ram_metrics) != 1 or next(iter(ram_metrics), None) in (None, '', 'unknown'):
        resources['ram_peak_bytes'] = _unknown('bytes', 'No single explicit platform RAM metric is available')
    else:
        resources['ram_peak_bytes']['ram_metric'] = next(iter(ram_metrics))
    evidence = [item for item in list(stages.values()) + list(resources.values()) if item['estimate'] is not None]
    ranks = {'unknown': 0, 'weak': 1, 'moderate': 2, 'strong': 3}
    confidence = min((item['confidence'] for item in evidence), key=lambda level: ranks[level]) if evidence else 'unknown'
    reasons = ['Only verified complete requests with the same local hardware/software, backend, task, arithmetic and placement are used.',
               'Timing ranges include historical cache/thermal variability; cache warmth is not certified.',
               'Ranges are forecasts, not allocator limits, quality validation or a proof of capacity.',
               'work_seconds excludes text encoding; encode_seconds is media encoding. Missing text-encoder time remains unknown.']
    if cache_state == 'unknown':
        reasons.append('AdaLN cache state was not supplied: load/work forecasts retain the observed cache-regime mixture and do not establish a warm-start estimate.')
    else:
        reasons.append('Load/work timings use only the explicitly requested %s AdaLN cache regime; absence remains unknown.' % cache_state)
    reasons.append('Host CPU thread/environment settings are measured context, not established cache effects; unknown or mixed thread settings limit load/work comparisons.')
    if options['benchmark_allocator_limit_bytes'] is not None:
        reasons.append('Only observations with this explicit benchmark allocator cap are eligible; uncapped or unrecorded-cap data are excluded.')
    if unknown_text:
        reasons.append('Input token length is not known yet; timing features assume %d text tokens from %s.' %
                       (assumed_text, 'matching local history' if known_text else 'an unmeasured reference shape'))
    if not records:
        reasons.append('No compatible complete local observations; run a full request to create the first data point.')
    return {'status': 'estimated' if evidence else 'unknown', 'confidence': confidence,
            'evidence_count': len(records), 'geometry': target, 'profile_key': key,
            'extrapolation': any(item.get('extrapolation', False) for item in evidence),
            'input_token_assumption': assumed_text if unknown_text else None,
            'execution_context': {'adaln_cache_state': cache_state,
                                  'allocator_limit_bytes': options['benchmark_allocator_limit_bytes'],
                                  'observed_cache_regimes': regimes,
                                  'observed_host_threads': thread_contexts},
            'stages': stages, 'resources': resources, 'rejected_records': rejected,
            'reasons': reasons, 'capacity_certified': False, 'numerical_equivalence_certified': False,
            'scope': 'local_complete_video_worker_forecast'}
