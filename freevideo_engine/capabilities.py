"""Static, CPU-only admission and whole-request estimates from saved facts.

No hardware probing, model imports or report IO. Admission uses saved local
occupancy or a default baseline, with the planner's reserves; it is not a
guarantee of physical capacity.
"""
import hashlib
import json
import math
from statistics import median
from types import SimpleNamespace

from .effort_forecast import positive, stage_forecast
from .geometry import geometry
from .hardware import GiB, Hardware
from .media_encoding import reference_frames, reference_size
from .media_request import TASKS
from .policy import BLOCK_BYTES, ResourceBudgetError, choose
from .stage_estimate import STAGES, _params, gpu_key, predict_prior
from .two_pass import first_pass_policy, plan as sampling_plan
from . import tiers as quality_tiers

# Shared by all queries, including the default summary; parity with the editor
# is checked in test_capabilities (JS uses positive Math.round, not bankers' rounding).
SIZE_PRESETS = (.5, .75, .98, 1., 1.5, 2.)
TIERS = {name: (tier['steps'], tier['two_pass']) for name, tier in quality_tiers.TIERS.items()}
PLAN_NAMES = {'-1': 'refused', '0': 'normal', '1': 'slower_streaming',
              '2': 'admitted_tight_memory'}
# WholeVideoProgress.for_request starts an ordinary new-prompt request with
# these flags. Unobserved encoder/model residency stays cold; compilation is
# unknown (the prior's half-cold default), even later in the same session.
TYPICAL_FLAGS = dict(encode_cached=None)
IDLE_IN_USE = {'Windows': {'vram': int(1.2 * GiB), 'ram': 0},
               'Linux': {'vram': int(1.2 * GiB), 'ram': 0},
               'Darwin': {'vram': 0, 'ram': 0}}
UNAVAILABLE = {'version': 1, 'status': 'unavailable'}


def normalize_references(task, references=None):
    """Panel metadata, never tokens or file identities; immutable cache key.

    None means the smallest layout for the task, with canvas-sized stills
    and output-length clips. An explicit list describes every reference.
    """
    if task not in TASKS:
        raise ValueError('Unsupported task.')
    if references is None:
        return None
    if not isinstance(references, list) or len(references) > 32:
        raise ValueError('Use at most 32 reference items.')
    rows = []
    for row in references:
        if not isinstance(row, dict):
            raise ValueError('Invalid reference metadata.')
        kind = row.get('kind')
        allowed = {'kind', 'seconds', 'use'} if kind == 'audio' else {'kind', 'width', 'height'}
        if kind == 'video':
            allowed |= {'seconds', 'audio_seconds', 'guide'}
        if kind not in ('image', 'video', 'audio') or set(row) - allowed:
            raise ValueError('Unsupported reference metadata.')
        width = height = seconds = audio = None
        if kind != 'audio':
            width, height = row.get('width'), row.get('height')
            if any(type(n) is not int or not 1 <= n <= 65536 for n in (width, height)):
                raise ValueError('Specify the original reference width and height.')
        if kind != 'image':
            seconds = row.get('seconds')
            if not positive(seconds):
                raise ValueError('Specify positive finite reference seconds.')
            if kind == 'audio' and seconds < 1 / 32000:
                raise ValueError('Reference audio is empty after normalization.')
            if kind == 'video' and math.ceil(min(seconds, 15.) * 24) < 22:
                raise ValueError('The reference video needs at least 22 normalized frames.')
        if 'guide' in row:
            # A guide clip is packed as a silent video reference (same rows and
            # patches; only the time coordinates differ), so it keeps the plain
            # video tuple and history matching counts it as reference video tokens.
            if type(row['guide']) is not bool or 'audio_seconds' in row:
                raise ValueError('A guide clip is a silent video reference.')
        if 'audio_seconds' in row:
            audio = row['audio_seconds']
            if not positive(audio):
                raise ValueError('Specify positive finite soundtrack seconds, or omit it for silent video.')
            if audio < 1 / 32000:
                raise ValueError('Reference audio is empty after normalization.')
        from .original_audio import fields, AudioUseError, MAX_SECONDS
        use = fields(row)
        if use:
            if seconds < 1.625:
                raise AudioUseError('audio_original_short')
            if seconds > MAX_SECONDS:
                raise AudioUseError('audio_original_long')
        rows.append((kind, width, height, seconds, audio) + (('original',) if use else ()))
    if sum(len(row) == 6 for row in rows) > 1:
        raise AudioUseError('audio_original_multiple')
    visual = any(r[0] != 'audio' for r in rows)
    audio = any(r[0] == 'audio' or r[4] is not None for r in rows)
    expected = 'ref2va_av' if visual and audio else 'ref2va_audio' if audio else 'ref2va' if visual else None
    if (task.startswith('ref2va') and task != expected) or (not task.startswith('ref2va') and rows):
        raise ValueError('Reference modalities must match the task; keyframes and references cannot be mixed.')
    return tuple(rows)


def reference_parameters(references):
    """JSON form of a normalized cache key, also used in notifications."""
    if references is None:
        return None
    return [{k: v for k, v in zip(('kind', 'width', 'height', 'seconds', 'audio_seconds', 'use'), row)
             if v is not None} for row in references]


def valid_reference_rows(value):
    return (isinstance(value, dict) and set(value) == {'video', 'audio'}
            and all(type(value[k]) is int and value[k] >= 0 for k in ('video', 'audio')))


def _material(canvas, task, references):
    """Mirror raw-media normalization with scalar facts only.

    Still encoding is one legacy media unit (= 5 reference seconds). Clip
    units use the actual retained duration, including a soundtrack encoder.
    Metadata duration assumes a continuous 24 fps normalized timeline.
    """
    patch = (canvas['height'] // 32) * (canvas['width'] // 32)
    if task in ('t2va', 'i2va', 'l2va', 'fl2va'):
        count = {'t2va': 0, 'i2va': 1, 'l2va': 1, 'fl2va': 2}[task]
        return dict(video=count * patch, audio=0), 5. * count
    if references is None:
        seconds = canvas['frames'] / 24.
        references = (('image', canvas['width'], canvas['height'], None, None),) if task == 'ref2va' else (
            ('audio', None, None, seconds, None),) if task == 'ref2va_audio' else (
            ('video', canvas['width'], canvas['height'], seconds, seconds),)
    video_rows = audio_rows = 0
    duration = 0.
    limit = reference_frames(canvas)
    for row in references:
        kind, width, height, seconds, soundtrack = row[:5]
        if len(row) == 6:
            # Original audio occupies the existing target rows. Keep all
            # ordinary model/decoder budgets, without duplicating the track.
            duration += seconds
            continue
        if kind != 'audio':
            width, height = reference_size(width, height, canvas)
            patches = (width // 32) * (height // 32)
            if kind == 'image':
                video_rows += patches
                duration += 5.
            else:
                frames = min(limit, math.ceil(min(seconds, 15.) * 24))
                frames -= (frames - 5) % 17
                video_rows += ((frames - 5) // 17 * 5 + 2) * patches
                duration += frames / 24.
        audio_seconds = seconds if kind == 'audio' else soundtrack
        if audio_seconds is not None:
            used = min(audio_seconds, limit / 24.)
            samples = int(used * 32000)
            if samples < 1:
                raise ValueError('Reference audio is empty after normalization.')
            audio_rows += 2 * ((samples + 799) // 800)
            duration += samples / 32000.
    return dict(video=video_rows, audio=audio_rows), duration


def content_revision(response):
    content = {k: v for k, v in response.items() if k != 'revision'}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()[:12]


def sizes_for(aspect='16:9', size=None):
    if size is not None:
        width, height = size
        if any(type(n) is not int or n > 4096 for n in size):
            raise ValueError('Use integer dimensions up to 4096.')
        geometry(width, height)
        return [{'pixels': width * height / 1024**2, 'width': width, 'height': height}]
    try:
        a, b = (float(n) for n in aspect.split(':'))
        if not positive(a) or not positive(b):
            raise ValueError
        ratio = a / b
        result, seen = [], set()
        for pixels in SIZE_PRESETS:
            width, height = (32 * math.floor(math.sqrt(pixels * 1024**2 * r) / 32 + .5)
                             for r in (ratio, 1 / ratio))
            if not 256 <= width <= 4096 or not 256 <= height <= 4096:
                raise ValueError
            if (width, height) not in seen:
                result.append(dict(pixels=pixels, width=width, height=height))
                seen.add((width, height))
        return result
    except (ValueError, TypeError, AttributeError, OverflowError, ZeroDivisionError) as error:
        raise ValueError('Use a positive width:height aspect with supported dimensions.') from error


def _machine(machine, in_use=None):
    """Normalize physical totals; configured budget caps are not device totals."""
    if not isinstance(machine, dict):
        return None
    observed = machine.get('hardware') or {}
    device = machine.get('device_identity') or {}
    if not isinstance(observed, dict) or not isinstance(device, dict):
        return None
    name = observed.get('gpu_name') or machine.get('gpu_name') or device.get('name')
    system = observed.get('system') or machine.get('system')
    mac = machine.get('device_backend') == 'mps' or device.get('backend') == 'mps'
    ram = observed.get('ram_total') or device.get('unified_ram_bytes')
    vram = ram if mac else observed.get('vram_total')
    cap = observed.get('capability')
    if (not isinstance(name, str) or not name.strip() or system not in ('Windows', 'Linux', 'Darwin')
            or not positive(ram) or not positive(vram)):
        return None
    if mac:
        if system != 'Darwin' or not name.startswith('Apple '):
            return None
    elif (not isinstance(cap, (list, tuple)) or len(cap) != 2
          or any(type(n) is not int for n in cap) or cap[0] < 8 or not 0 <= cap[1] <= 9):
        return None
    # Setup records the prepared variant (for example 'int8_convrot'); planning takes its family.
    saved = machine.get('prepared_format', 'int8')
    precision = next((p for p in ('int8', 'fp8') if isinstance(saved, str) and saved.startswith(p)), None)
    if precision is None:
        return None
    # Only use an already saved Metal observation, never create a Metal device.
    metal = observed.get('metal_recommended_working_set_bytes')
    if metal is not None and not positive(metal):
        return None
    idle = IDLE_IN_USE[system] if in_use is None else in_use
    if not isinstance(idle, dict) or any(type(idle.get(k)) is not int or idle[k] < 0 for k in ('vram', 'ram')):
        raise ValueError('Use nonnegative byte counts for vram and ram in use.')
    idle = {k: idle[k] for k in ('vram', 'ram')}
    if mac:
        idle = dict(IDLE_IN_USE['Darwin'])  # Preserve the unified-memory path.
    return dict(name=name, system=system, mac=mac, ram=int(ram), vram=int(vram),
                vram_free=max(0, int(vram) - idle['vram']),
                ram_available=max(0, int(ram) - idle['ram']), idle_in_use=idle,
                cap=tuple(cap) if not mac else None, precision=precision,
                metal=int(metal) if metal is not None else None,
                compile_owed=machine.get('compile_owed') is True and not mac)


def _request(width, height, frames, steps, two_pass, task='t2va', refine=3,
             references=None, reference_rows=None, upscale=2):
    """Scalar request shared by admission and offline, recorded-row reviews."""
    from .two_pass import render_canvas
    canvas = dict(render_canvas(geometry(width, height, frames=frames))[0], task=task, steps=steps)
    if reference_rows is not None:
        if not valid_reference_rows(reference_rows):
            raise ValueError('Invalid recorded reference rows.')
        rows, duration = reference_rows, None  # History cannot describe media encoding units.
    else:
        rows, duration = _material(canvas, task, references)
    if task != 't2va' or rows['video'] or rows['audio']:
        canvas.update(reference_video_tokens=rows['video'], reference_audio_tokens=rows['audio'])
        if duration is not None:
            canvas['reference_seconds'] = duration
    sampling = sampling_plan(canvas, two_pass, task, base_steps=steps, refine_steps=refine, scale=upscale)
    # A small canvas runs in one pass even with two-pass on; follow the plan.
    request = dict(canvas, base_steps=steps, refine_steps=sampling['refine_steps'],
                   two_pass=sampling['enabled'], flow='full',
                   sampling_geometry={k: sampling[k] for k in ('first', 'second') if sampling[k]})
    if sampling.get('scale', 2) != 2:
        request['upscale_factor'] = sampling['scale']
    return canvas, sampling, request


def _admit(machine, width, height, frames, steps, two_pass, task='t2va', refine=3,
           references=None, reference_rows=None, step_cache=None, upscale=2):
    """Admission of one request; `step_cache` asks the planner for a first-pass cache.

    The request carries the cache the planner keeps (on Mac only the audio-video EasyCache), so its
    estimate and history follow what the engine will actually run.
    """
    canvas, sampling, request = _request(width, height, frames, steps, two_pass, task, refine,
                                         references, reference_rows, upscale)
    rows = dict(video=canvas.get('reference_video_tokens', 0), audio=canvas.get('reference_audio_tokens', 0))
    try:
        if machine['mac']:
            from .macos_generate import policy as mac_policy
            from .macos_compute import request_admission, plan as mac_plan
            resource = mac_policy(SimpleNamespace(), dict(total_bytes=machine['ram'],
                                                         available_bytes=machine['ram']))
            admission = request_admission(resource, sampling, working_set_bytes=machine['metal'])
            if admission['state'] == 'refuse':
                return None, -1, 'memory'
            plans = [mac_plan(min(resource['allocator_bytes'], machine['metal'] or resource['allocator_bytes']),
                              geometry(**g)['video_tokens'] + 1024 + rows['video'] + rows['audio'],
                              frames=geometry(**g)['latent_frames'], allow_bounded=True)
                     for g in (sampling['first'], sampling['second'] or sampling['first'])]
            request.update(pass1_resident_blocks=plans[0]['retained_layers'],
                           pass2_resident_blocks=plans[1]['retained_layers'])
            if step_cache == 'easycache-av':
                request['step_cache'] = step_cache
            code = 0 if all(p['retained_layers'] == 50 for p in plans) else 1
            if admission['state'] == 'tight':
                code = 2
            return request, code, None
        hardware = Hardware(machine['name'], machine['cap'], machine['vram'], machine['vram_free'],
                            machine['ram'], machine['ram_available'], system=machine['system'])
        # SageAttention 2 has no sm100 kernels (data-centre Blackwell); those cards run torch-flash.
        backend = 'torch-flash' if machine['cap'][0] == 10 else 'sage2'
        options = dict(precision=machine['precision'], attention=backend + '/' + backend, available_backends={backend})
        choose(hardware, canvas=canvas, stage='encoding', **options)
        final = choose(hardware, canvas=dict(canvas, step_cache=step_cache) if step_cache else canvas, **options)
        if final.engine.get('step_cache'):
            request['step_cache'] = final.engine['step_cache']
        first = first_pass_policy(final.legacy_profile(), canvas, sampling)
        engines = [first['profile']['engine'] if first else final.engine, final.engine]
        request.update(pass1_resident_blocks=engines[0]['resident_blocks'],
                       pass2_resident_blocks=final.engine['resident_blocks'],
                       decoder_resident_blocks=final.decoder['resident_blocks'],
                       decoder_offload=final.decoder['offload'])
        disk_risk = any(e['stream_weights'] and e['pin_host_gb'] * 1e9 + 1e6
                        < (50 - e['resident_blocks']) * BLOCK_BYTES for e in engines)
        transfers = any(e['residual_offload'] or e['attention_cpu_outputs'] for e in engines)
        # The third value names what made a slow plan slow: host RAM (weights stream from disk) or GPU memory.
        return request, int(disk_risk or transfers), 'memory' if disk_risk else 'gpu_memory' if transfers else None
    except ResourceBudgetError as error:
        # One field cannot encode both failures: prefer the system RAM limit.
        return None, -1, 'memory' if 'ram' in error.details['insufficient'] else 'gpu_memory'
    except MemoryError:
        return None, -1, 'memory'


def _forecast_hardware(machine):
    return dict(gpu_name=machine['name'], os=machine['system'],
                ram_total_gib=machine['ram'] / GiB, vram_total_gib=machine['vram'] / GiB,
                vram_free_gib_start=machine['vram_free'] / GiB)


def _recent(records):
    rows = [r for r in records[:48] if isinstance(r, dict)]
    if rows and all(positive(r.get('recorded_epoch')) for r in rows):
        rows.sort(key=lambda r: r['recorded_epoch'], reverse=True)
    return rows


def _local_in_use(machine, records, version):
    """Use the calibration's ordered, version-preferred window of saved bytes."""
    def values(rows):
        return [r['in_use'] for r in rows if isinstance(r.get('in_use'), dict)
                and all(type(r['in_use'].get(k)) is int for k in ('vram', 'ram'))][:5]
    rows = values([r for r in records if version and r.get('version') == version]) or values(records)
    return {k: max(0, min(machine[k], int(median(r[k] for r in rows))))
            for k in ('vram', 'ram')} if rows else None


def _calibration(machine, hardware, records, params, version):
    def ratios(rows):
        found = []
        for row in rows:
            try:
                if row.get('flow', 'full') != 'full' or not positive(row.get('request_seconds')) or row['request_seconds'] > 1e6:
                    continue
                shape = row['shape']
                canvas = geometry(**{k: shape[k] for k in ('width', 'height', 'frames')})
                if canvas['frames'] != shape['frames'] or type(row['two_pass']) is not bool:
                    continue
                refs = row.get('reference_rows')
                if row['task'] != 't2va' and not valid_reference_rows(refs):
                    continue  # Legacy material records do not describe their rows.
                request, _, _ = _admit(machine, shape['width'], shape['height'], shape['frames'],
                                       row['base_steps'], row['two_pass'], row['task'],
                                       row['refine_steps'] if row['two_pass'] else 3,
                                       reference_rows=refs, step_cache=row.get('step_cache'),
                                       upscale=row.get('upscale_factor', 2))
                if request is None:
                    continue
                request['lora_count'] = int(bool(row.get('adapters')))
                flags = dict(TYPICAL_FLAGS, encode_cached=bool(row.get('conditioning_cache_hit')),
                             model_cold=not bool(row.get('model_resident')))
                prior = predict_prior(hardware, request, flags, params)['total']['seconds']
                if positive(prior):
                    found.append(row['request_seconds'] / prior)
                    if len(found) == 5:
                        break
            except (LookupError, TypeError, ValueError, ArithmeticError, AttributeError):
                continue
        return found
    values = ratios([r for r in records if version and r.get('version') == version]) or ratios(records)
    return {'ratio': max(.5, min(2., median(values))), 'runs': len(values)} if values else None


def _whole_records(records, request, adapters):
    # stage_forecast can extrapolate nearby shapes/step counts for live ETA.
    # A heat-map point uses local seconds only for its own complete settings;
    # every other point follows the machine-wide calibration ratio.
    shape = {k: request[k] for k in ('width', 'height', 'frames')}
    rows = dict(video=request.get('reference_video_tokens', 0), audio=request.get('reference_audio_tokens', 0))
    return [r for r in records if r.get('shape') == shape
            and r.get('flow', 'full') == 'full' and r.get('task') == request['task']
            and r.get('adapters', False) == adapters and r.get('two_pass') == request['two_pass']
            and r.get('base_steps') == request['base_steps'] and r.get('refine_steps') == request['refine_steps']
            and r.get('step_cache') == request.get('step_cache')
            and r.get('upscale_factor', 2) == request.get('upscale_factor', 2)
            and ((request['task'] == 't2va' and r.get('reference_rows') is None)
                 or (valid_reference_rows(r.get('reference_rows')) and r['reference_rows'] == rows))]


def build(machine, records, *, aspect='16:9', size=None, task='t2va', adapters=False, version=None,
          in_use=None, longest_wait_seconds=None, references=None):
    """Return selectable whole seconds and the separate legal-frame boundary.

    Enumerating every legal frame count also finds boundaries between whole
    seconds. A panel-limited row ends at 1450 frames (60 s input).
    """
    machine = _machine(machine, in_use)
    if machine is None:
        return dict(UNAVAILABLE)
    records = _recent(records)
    idle_basis = 'local' if in_use is not None and not machine['mac'] else 'default'
    if in_use is None and machine['system'] in ('Windows', 'Linux') and not machine['mac']:
        idle = _local_in_use(machine, records, version)
        if idle is not None:
            machine.update(idle_in_use=idle, vram_free=machine['vram'] - idle['vram'],
                           ram_available=machine['ram'] - idle['ram'])
            idle_basis = 'local'
    if task not in TASKS or type(adapters) is not bool:
        raise ValueError('Unsupported task or adapters flag.')
    references = normalize_references(task, references)
    if longest_wait_seconds is not None and not positive(longest_wait_seconds):
        raise ValueError('Use a positive longest wait in seconds.')
    sizes = sizes_for(aspect, size)
    lengths = [dict(seconds=s, frames=geometry(seconds=s)['frames']) for s in range(2, 61)]
    length_frames = {l['frames'] for l in lengths}
    params = _params(None)
    hardware = _forecast_hardware(machine)
    calibration = _calibration(machine, hardware, records, params, version)
    ratio = calibration['ratio'] if calibration else 1.
    item = next((v for k, v in params['gpu_speed_table'].items()
                 if gpu_key(k).casefold() == gpu_key(machine['name']).casefold()), {})
    fitted = item.get('training_requests', 0) > 0 and 'G' in item
    # The shipped fit counts requests, not distinct community reports. Do not
    # relabel 77 training requests as 77 reports; unknown support stays null.
    reports = item.get('model_reports')
    basis = 'local' if calibration else 'same_model' if fitted else 'near_model'

    def estimate(request):
        request['lora_count'] = int(adapters)
        prior = predict_prior(hardware, request, TYPICAL_FLAGS, params)
        weights = {k: prior['stages'][k]['seconds'] for k in STAGES}
        matches = _whole_records(records, request, adapters)
        local = (stage_forecast(matches, request, TYPICAL_FLAGS, weights, version=version)
                 if matches else {})
        return local.get('seconds', prior['total']['seconds'] * ratio)

    tiers = {}
    for name, tier in quality_tiers.table(machine['mac']).items():
        steps, two_pass, step_cache, upscale = tier['steps'], tier['two_pass'], tier['step_cache'], tier['upscale']
        times, codes, slow_by, maxima, frame_maxima, limits = [], [], [], [], [], []
        for size_row in sizes:
            admitted = {f: _admit(machine, size_row['width'], size_row['height'], f, steps, two_pass, task,
                                  references=references, step_cache=step_cache, upscale=upscale)
                        for f in range(39, lengths[-1]['frames'] + 1, 17)}
            estimates = {}
            for f, (request, _, _) in admitted.items():
                if request is not None and (longest_wait_seconds is not None
                                            or f in length_frames):
                    estimates[f] = estimate(request)
                    if longest_wait_seconds is not None and estimates[f] > longest_wait_seconds:
                        admitted[f] = (None, -1, 'time')
            frames = [f for f, (request, _, _) in admitted.items() if request is not None]
            maximum = max(frames) if frames else None
            frame_maxima.append(maximum)
            maxima.append(max((l['seconds'] for l in lengths if admitted[l['frames']][0] is not None),
                              default=None))
            next_frame = maximum + 17 if maximum is not None else 39
            limits.append('panel' if next_frame > lengths[-1]['frames']
                          else admitted[next_frame][2] if admitted[next_frame][0] is None else None)
            row_times, row_codes, row_slow = [], [], []
            for length in lengths:
                request, code, why = admitted[length['frames']]
                seconds = None
                if request is not None:
                    seconds = max(1, math.floor(estimates[length['frames']] + .5))
                row_times.append(seconds)
                row_codes.append(code)
                row_slow.append(why if code > 0 else None)
            times.append(row_times)
            codes.append(row_codes)
            slow_by.append(row_slow)
        tiers[name] = dict(steps=steps, two_pass=two_pass, step_cache=step_cache, upscale=upscale, estimate_seconds=times,
                           max_seconds=maxima, max_frames=frame_maxima, limit=limits, plan=codes, slow_by=slow_by)
    default = next((i for i, s in enumerate(sizes) if s['pixels'] == .98), 0)
    result = dict(version=1, status='ready',
                machine=dict(gpu_name=machine['name'], vram_gib=machine['vram'] / GiB,
                             ram_gib=machine['ram'] / GiB, system=machine['system'],
                             compile_owed=machine['compile_owed']),
                aspect=aspect, sizes=sizes, lengths=lengths, tiers=tiers,
                summary=dict(width=sizes[default]['width'], height=sizes[default]['height'], tier='light',
                             max_seconds=tiers['light']['max_seconds'][default],
                             max_frames=tiers['light']['max_frames'][default]),
                diagnostics=dict(basis=basis, model_reports=reports, calibration=calibration,
                                 idle_basis=idle_basis,
                                 idle_in_use_gib={k: round(v / GiB, 3) for k, v in machine['idle_in_use'].items()},
                                 plan_names=dict(PLAN_NAMES)))
    result['revision'] = content_revision(result)
    return result
