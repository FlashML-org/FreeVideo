"""Presentation-only port of the 2026-10-10 fitted stage model v2.

Standard library only; coefficients and GPU table live in JSON. This is not a
resource planner. Unknown devices/flags retain the fit's broad fallback.
The six disjoint UI stages are STAGES; decode/video_decode/save_other remain
available for parity and diagnostics and must not also be summed with decode_save.

JSON v2 retains v1 keys and adds v2.selection (predictor names), v2.ratio
(log-ratio coefficients), v2.physical_cs (C and streaming coefficients),
v2.decode (free/log_prior/log_live fits), and v2.warmup (singleton corrections).
Selected ratio_offload uses (50 - pass2_resident_blocks) / 50; missing placement
keeps resident()'s documented coarse VRAM fallback. Decoder fits use the same
unknown-residency fallback as v1. No resource plan is inferred by this module.
"""
from functools import lru_cache
import json
import math
from pathlib import Path
import re
from statistics import median

TOKEN_UNIT = 72576.
# Every generated query attends to the reference rows, so a reference video
# row costs one token plus this many more per canvas-sized unit of tokens
# (B200: linear in the row count, 1.4x at 960x544 and 1.85x at 1664x960);
# canvases past the measured size keep the largest measured weight.
REFERENCE_VIDEO_PAIR_WEIGHT = 4.
# The B200 weights above were measured up to 8,696 reference rows on a 73,320-row
# canvas (12% of its rows). Past that share a row adds less: an 8 s reference video
# (50,928 rows) on a 5060 Ti, first pass 25,536 and second pass 57,456 generated
# rows, ran at 110 s and 173 s a step, which a constant 1.6 to 1.7 rows per
# reference row reproduces and the 2.4 and 4.2 above overshoot 1.6 and 2.6 times.
REFERENCE_CALIBRATED_SHARE = .12
REFERENCE_VIDEO_BEYOND_WEIGHT = 1.45
DEFAULT_INTERVAL = [.45, 2.2]
STAGES = ('encode', 'load', 'pass1', 'upscale', 'pass2', 'decode_save')
PARAMS_PATH = Path(__file__).with_name('stage_estimate_params.json')


@lru_cache(maxsize=2)
def _read_params(path, modified):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _params(params):
    if params is not None:
        return params
    return _read_params(str(PARAMS_PATH), PARAMS_PATH.stat().st_mtime_ns)


def _num(obj, key, default):
    value = obj.get(key)
    return default if value is None else value


def _dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def gpu_key(name):
    """Driver/marketing aliases, preserving mobile and power-limited variants."""
    key = ' '.join(str(name or 'unknown').replace('®', '').replace('™', '').split())
    key = re.sub(r'^(?:(?:NVIDIA(?: Corporation)?|GeForce|Quadro)\s+)+', '', key, flags=re.I)
    key = re.sub(r'\b(?:Server|Workstation)\s+Edition\b|\bEdition\b', '', key, flags=re.I)
    mobile = bool(re.search(r'\bLaptop(?:\s+GPU)?\b|\bMax[- ]?Q(?:\s+Design)?\b', key, re.I))
    # The PRO 6000 Max-Q is a 300 W desktop, not a laptop; retain its own prior.
    desktop_max_q = mobile and bool(re.search(r'\bRTX\s+PRO\s+6000\s+Blackwell\b', key, re.I)) and 'laptop' not in key.lower()
    key = re.sub(r'\bLaptop(?:\s+GPU)?\b|\bMax[- ]?Q(?:\s+Design)?\b', '', key, flags=re.I)
    key = ' '.join(key.split())
    return key + (' Max-Q' if desktop_max_q else ' Laptop GPU' if mobile else '')


def _gpu_item(h, p):
    key = _gpu(h)
    table = p['gpu_speed_table']
    if key in table:
        return table[key]
    normalized = key.casefold()
    return next((item for name, item in table.items() if gpu_key(name).casefold() == normalized), None)


def _decode_save(result):
    # Marginal endpoint sums, not a newly calibrated joint interval.
    stages = result['stages']
    stages['decode_save'] = {k: stages['decode'][k] + stages['save_other'][k]
                             for k in ('seconds', 'p20', 'p80')}
    return result


def ram_tier(h):
    ram = _num(h, 'effective_ram_gib', _num(h, 'ram_total_gib', 32.))
    # Consumer 64 GB installations often expose ~56-63.9 GiB. The middle
    # bucket also contains 24/48 GiB machines; 48 GiB boundary is explicit.
    return 0 if ram <= 16.5 else 1 if ram < 48 else 2


def _gpu(h):
    return gpu_key(h.get('physical_gpu') or h.get('real_gpu') or h.get('gpu_name'))


def _apple(h):
    return 'Apple' in _gpu(h) or h.get('os') == 'Darwin'


APPLE_TIERS = ('base', 'Pro', 'Max', 'Ultra')


def _apple_factors(h, p):
    """(step, decode) multipliers for the Apple chip; (1, 1) is the M4 Max fit.

    Only some chips have a report. A generation with no report takes the nearest
    older generation that has one (the nearest newer one if there is none older),
    so a guess errs long; the first measured step then corrects it. Inside the
    generation used, a tier with no report steps down to the next lower tier, and
    a tier lower than all of them is slower by a fixed ratio.
    """
    table = (p.get('apple') or {}).get('chip_factors')
    found = re.search(r'\bM(\d+)(?:\s+(Pro|Max|Ultra))?', _gpu(h))
    if not table or not found:
        return 1., 1.
    chips = {int(g): v for g, v in table['chips'].items()}
    generation, tier = int(found.group(1)), found.group(2) or 'base'
    if generation not in chips:
        older = [g for g in chips if g < generation]
        generation = max(older) if older else min(chips, key=lambda g: abs(g-generation))
    own = chips[generation]
    rank = APPLE_TIERS.index(tier)
    for r in range(rank, -1, -1):
        if APPLE_TIERS[r] in own:
            return tuple(own[APPLE_TIERS[r]])
    lowest = own[min(own, key=APPLE_TIERS.index)]
    return tuple(x*float(table['lower_tier_slowdown']) for x in lowest)


def speed(h, p, spec_only=False):
    key = _gpu(h)
    if _apple(h):
        return 1.
    item = _gpu_item(h, p)
    if item:
        return item.get('G_spec' if spec_only else 'G', item['G_spec'])
    if h.get('tensor_fp16_dense_tflops'):
        return max(.05, float(h['tensor_fp16_dense_tflops'])/94.8 * (.8 if _laptop(h) else 1.))
    if h.get('tensor_int8_dense_tops'):
        return max(.05, float(h['tensor_int8_dense_tops'])/189.6 * (.8 if _laptop(h) else 1.))
    return 1.  # Explicit unknown-device fallback; surfaced in warnings.


def canvases(r):
    w, h = int(_num(r, 'width', 1344)), int(_num(r, 'height', 768))
    f = int(_num(r, 'frames', math.ceil(_num(r, 'seconds', 10.)*24)))
    f += (5-f) % 17
    if w <= 0 or h <= 0 or w % 32 or h % 32 or f < 39:
        raise ValueError('Use positive 32-aligned canvases and at least 39 H3 frames.')
    full = dict(width=w, height=h, frames=f)
    sg = r.get('sampling_geometry') or {}
    two = bool(_num(r, 'two_pass', True))
    if sg.get('first'):
        first = dict(full, **sg['first'])
    elif two and min(w, h) >= 512:
        first = dict(full, width=((w+63)//64)*32, height=((h+63)//64)*32)
    elif two:
        d = math.gcd(w//32, h//32)
        uw, uh = w//32//d, h//32//d
        m = max((d+1)//2, (8+uw-1)//uw, (8+uh-1)//uh)
        first = dict(full, width=32*uw*m, height=32*uh*m)
    else:
        first = dict(full)
    # two_pass.plan() samples a first pass under SMALL_FIRST_PASS_PIXELS (138,240) at the target
    # size, except when the first pass itself is wanted (a preview or the upscale of one).
    if (not sg.get('first') and two and r.get('flow') not in ('preview', 'upscale')
            and first != full and first['width']*first['height'] < 138240):
        return full, full, full
    if not sg.get('first') and two and min(w, h) >= 512 and r.get('upscale_factor', 2) != 2:
        # A smaller factor starts larger; the size check above is on the 2x first pass, as in plan().
        from .two_pass import first_canvas
        first = first_canvas(full, r['upscale_factor'])[0]
    second = dict(full, **(sg.get('second') or {}))
    decode = first if r.get('flow') == 'preview' else full
    return first, second, decode


def token_count(canvas):
    f = int(canvas['frames'])
    # Verified in engine/geometry.py and refine.py: H3 is 17 frames -> 5,
    # with 2 latent frames for the initial 5. H/32*W/32 is patch-token grid.
    if (f-5) % 17:
        f += (5-f) % 17
    return ((f-5)//17*5+2)*(canvas['width']//32)*(canvas['height']//32)


def step_counts(r):
    n1 = int(_num(r, 'base_steps', 8))
    n2 = int(_num(r, 'refine_steps', 3)) if _num(r, 'two_pass', True) else 0
    if r.get('flow') == 'preview':
        n2 = 0
    if r.get('flow') == 'upscale':
        n1 = 0
    return max(0, n1), max(0, n2)


def resident(h, r, i):
    value = r.get(f'pass{i}_resident_blocks')
    if value is not None:
        return float(min(50, max(0, value)))
    if _apple(h):
        return 50.
    # Deliberately coarse missing-plan fallback, not a copied product planner.
    v = float(_num(h, 'vram_free_gib_start', _num(h, 'vram_total_gib', 8.)))
    return float(min(44, max(0, int((v-6.)*2.48))))


def stream_features(h, r, i):
    if _apple(h):
        return [1., 0., 0., 0., 0.]
    f = (50-resident(h, r, i))/50.
    # PCIe and OS effects lack independent identification in this corpus.
    # Keep weak fixed priors; measured link bandwidth may override.
    os_factor = .85 if h.get('os') == 'Linux' else 1.
    link = float(_num(h, 'pcie_bandwidth_gbs', 16.))  # decimal GB/s, not Gb/s
    f *= os_factor * 16./max(1., link)
    tiers = [0., 0., 0.]
    tiers[ram_tier(h)] = f
    laptop = float('Laptop' in _gpu(h) or bool(h.get('laptop')))
    return [1.] + tiers + [f*laptop]


def _reference_rows(h, r, x):
    # Measured on NVIDIA only; the Apple fit keeps one row per reference token.
    video, audio = float(_num(r, 'reference_video_tokens', 0.)), float(_num(r, 'reference_audio_tokens', 0.))
    if _apple(h):
        return video + audio
    weight = 1. + REFERENCE_VIDEO_PAIR_WEIGHT*min(x, 1.)
    # Rows inside the measured share keep the measured weight; the rest never weigh more.
    measured = min(video, REFERENCE_CALIBRATED_SHARE*x*TOKEN_UNIT)
    return weight*measured + min(weight, REFERENCE_VIDEO_BEYOND_WEIGHT)*(video-measured) + audio


def step_parts(h, r, i, p):
    canvas = canvases(r)[i-1]
    x = token_count(canvas)/TOKEN_UNIT
    # Reference tokens affect the actual attention sequence (policy.py:697).
    # No guessed duration is substituted when unavailable.
    x += _reference_rows(h, r, x)/TOKEN_UNIT
    if _apple(h):
        b, c = p['apple']['step_bc']
        return p['apple']['step_fixed'], x*(b+c*x)*_apple_factors(h, p)[0]
    b, c = p['step']['bc_normalized']
    s = _dot(stream_features(h, r, i), p['step']['s'])
    return max(0., s), x*(b+c*x)/speed(h, p)


def _cold_level(flags, i):
    specific = flags.get(f'pass{i}_compile_cold')
    if specific is not None:
        return float(bool(specific))
    if flags.get(f'pass{i}_canvas_compiled') is True:
        return 0.
    pre = str(flags.get('precompile_state') or '').lower()
    if 'disable_caches=1' in pre:
        return 1.
    if ('after precompile' in pre or 'repeat after precompile' in pre or pre == 'warm'):
        return 0.
    c = flags.get('compile_cold')
    return .5 if c is None else float(bool(c))


def first_extra(step, flags, i, p):
    a, alpha, cold = p['first'][str(i)]
    return max(0., a+alpha*step+cold*_cold_level(flags, i))


def _decoder_stream(h, r):
    off = r.get('decoder_offload')
    if off is False or _apple(h):
        return 0.
    blocks = r.get('decoder_resident_blocks')
    if blocks is None:
        v = _num(h, 'vram_free_gib_start', _num(h, 'vram_total_gib', 8.))
        blocks = max(0., min(36., (v-3.25)*2**30/268574720))
    return max(0., (36-float(blocks))/36)


def _media_units(r):
    if not any(str(r.get('task') or '').startswith(t) for t in ['ref2va', 'i2va', 'l2va', 'fl2va']):
        return 0.
    duration = r.get('reference_seconds')
    return max(.1, float(duration)/5.) if duration is not None else 1.


def _encode_features(h, r, flags):
    if flags.get('encode_cached') is True:
        return [0.] * 4
    a = [0., 0., 0.]
    if flags.get('encoder_cold') is not False:
        a[ram_tier(h)] = 1.
    return a+[_media_units(r)]


def _load_features(h, r, flags):
    a = [0., 0., 0.]
    cold = flags.get('model_cold') is not False
    if cold:
        a[ram_tier(h)] = 1.
    return a+[float(not cold), float(_num(r, 'lora_count', 0))]


def _raw_base(h, r, flags, p, factor=1., mode='compute', factor_pass=1):
    stages, diagnostics = {}, {}
    n1, n2 = step_counts(r)
    for i, n in [(1, n1), (2, n2)]:
        s, comp = step_parts(h, r, i, p)
        warm = (s+comp)*factor if mode == 'whole' else s+comp*factor
        extra = first_extra(warm, flags, i, p)
        stages[f'pass{i}'] = n*warm+extra+p['pass_wall_overhead'][str(i)] if n else 0.
        diagnostics[f'pass{i}_warm_step'] = warm
        diagnostics[f'pass{i}_first_extra'] = extra
        diagnostics[f'pass{i}_fixed'] = s
        diagnostics[f'pass{i}_compute'] = comp
    _, full, dec = canvases(r)
    mp = full['width']*full['height']/1e6
    up = _dot([1., mp, float(ram_tier(h)==0)], p['upscale']) if n2 else 0.
    # Upscale scales with output pixels, but observed streaming factor from the
    # transformer does not identify it. Leave unchanged in compute mode.
    stages['upscale'] = up*factor if mode == 'whole' else up
    m = dec['width']*dec['height']*dec['frames']/1e6
    for name in ['video_decode', 'decode']:
        if _apple(h):
            a, d = p['apple'][name]
            value = a+m*d*factor*_apple_factors(h, p)[1]
        else:
            a, d, stream = p[name]['coef']
            ram = p[name]['ram16_multiplier'] if ram_tier(h)==0 else 1.
            value = a+m*(d/speed(h,p)*factor+stream*_decoder_stream(h,r))*ram
            if mode == 'whole':
                value = (a+m*(d/speed(h,p)+stream*_decoder_stream(h,r))*ram)*factor
        stages[name] = max(0., value)
    stages['decode'] = max(stages['decode'], stages['video_decode'])
    stages['encode'] = _dot(_encode_features(h,r,flags), p['encode'])
    stages['load'] = _dot(_load_features(h,r,flags), p['load'])
    stages['save_other'] = p['save_other']
    stages['total'] = sum(stages[k] for k in ['encode','load','pass1','upscale','pass2','decode','save_other'])
    diagnostics['G'] = speed(h,p)
    diagnostics['machine_factor'] = factor
    return stages, diagnostics


def _clip(value, low, high):
    return min(high, max(low, value))


def _laptop(h):
    return float('Laptop' in _gpu(h) or bool(h.get('laptop')))


def _ratio_features(h, r, offload=False):
    x2 = token_count(canvases(r)[1]) / TOKEN_UNIT
    v = [1., math.log(max(x2, 1e-6)), float(ram_tier(h) == 0), _laptop(h)]
    if offload:
        v.append((50-resident(h, r, 2))/50.)
    return v


def _area(r):
    a, b, _ = canvases(r)
    return b['width']*b['height']/(a['width']*a['height'])


def _physical_transfer(h, r, p, measured, coef):
    b = p['step']['bc_normalized'][0]
    c, ss = coef[0], coef[1:]
    x = [token_count(g)/TOKEN_UNIT for g in canvases(r)[:2]]
    x = [v+_reference_rows(h, r, v)/TOKEN_UNIT for v in x]
    s = [_dot(stream_features(h, r, i), ss) for i in [1, 2]]
    if _apple(h):
        s1, comp1 = step_parts(h, r, 1, p)
        s2, comp2 = step_parts(h, r, 2, p)
        return s2+comp2*float(_clip(max(.02, measured-s1)/max(comp1, .01), .1, 10.))
    return max(.01, s[1]+max(.02, measured-s[0])*x[1]*(b+c*x[1])/max(x[0]*(b+c*x[0]), .01))


def _decode_features(h, r, p, slowdown=None):
    d = canvases(r)[2]
    m = d['width']*d['height']*d['frames']/1e6
    v = [1., math.log(max(m,.01)), -math.log(speed(h,p)),
         float(ram_tier(h)==0), _decoder_stream(h,r), _laptop(h)]
    if slowdown is not None:
        v.append(math.log(max(.05,min(20.,slowdown))))
    return v


def _pass2(h,r,p,warm1,method):
    if method == 'original':
        s1,c1 = step_parts(h,r,1,p); s2,c2 = step_parts(h,r,2,p)
        return s2+c2*float(_clip(max(.02,warm1-s1)/max(c1,.01),.1,10.))
    if method == 'physical_cs':
        return _physical_transfer(h,r,p,warm1,p['v2']['physical_cs'])
    if method == 'area':
        return warm1*_area(r)
    name = 'ratio_offload' if method == 'ratio_offload' else 'ratio'
    coef = p['v2']['ratio'][name]
    ratio = float(math.exp(_clip(_dot(_ratio_features(h,r,name=='ratio_offload'),coef),math.log(.25),math.log(32.))))
    if method.startswith('blend'):
        alpha = float(method[5:])/100.
        ratio = math.exp(alpha*math.log(ratio)+(1-alpha)*math.log(_area(r)))
    return warm1*ratio


def _decode_value(h,r,p,name,method,factor=1.,slowdown=1.,warm2=None,live=False):
    if method == 'product':
        return 1.9*warm2
    if _apple(h):
        d=canvases(r)[2]; m=d['width']*d['height']*d['frames']/1e6
        a,b=p['apple'][name]
        return a+m*b*factor*_apple_factors(h,p)[1]
    spec = p['v2']['decode'][name]
    if method in ['log_prior','log_live']:
        use_live = method=='log_live' and live
        feat = _decode_features(h,r,p,slowdown if use_live else None)
        return float(math.exp(_clip(_dot(feat,spec['log_live' if use_live else 'log_prior']),-10.,20.)))
    d=canvases(r)[2]; m=d['width']*d['height']*d['frames']/1e6
    dec = spec['free'] if method in ['free_compute','free_static'] else p[name]
    a,b,stream = dec['coef']
    mult = dec['ram16_multiplier'] if ram_tier(h)==0 else 1.
    f = 1. if method=='free_static' else factor
    return max(0.,a+m*(b/speed(h,p)*f+stream*_decoder_stream(h,r))*mult)


def _set_pass2(v,d,h,r,flags,p,warm):
    d['pass2_warm_step'] = warm
    d['pass2_first_extra'] = first_extra(warm,flags,2,p)
    n = step_counts(r)[1]
    v['pass2'] = n*warm+d['pass2_first_extra']+p['pass_wall_overhead']['2'] if n else 0.


def _sum_total(v):
    v['decode'] = max(v['decode'], v['video_decode'])
    v['total'] = sum(v[k] for k in ['encode','load','pass1','upscale','pass2','decode','save_other'])


def _raw(h,r,flags,p,factor=1.,mode='compute',factor_pass=1):
    v,d = _raw_base(h,r,flags,p,factor,mode,factor_pass)
    if 'v2' not in p:
        return v,d
    select = p['v2']['selection']
    if select['prior_pass2']!='original':
        _set_pass2(v,d,h,r,flags,p,_pass2(h,r,p,d['pass1_warm_step'],select['prior_pass2']))
    decode_step=d['pass2_warm_step'] if step_counts(r)[1] else d['pass1_warm_step']
    for name in ['video_decode','decode']:
        v[name] = _decode_value(h,r,p,name,select['prior_'+name],warm2=decode_step)
    _sum_total(v)
    return v,d


def _warm(steps,h,r,i,p,method):
    if len(steps)<2:
        s,c=step_parts(h,r,i,p)
        return s+c,0
    if method=='median':
        return float(median(steps[1:])),len(steps)-1
    if len(steps)>=3:
        return float(median(steps[2:])),len(steps)-2
    measured=float(steps[1])
    if method=='skip_corrected':
        return measured*p['v2']['warmup'][str(i)],1
    alpha = .5 if method=='skip_shrink50' else .75
    s,c=step_parts(h,r,i,p)
    return math.exp(alpha*math.log(measured)+(1-alpha)*math.log(max(s+c,.01))),1


def _interval(name,value,p,kind='prior',wide=False):
    bounds=p.get('uncertainty',{}).get(kind,{}).get(name,DEFAULT_INTERVAL)
    lo,hi=bounds[:2]
    if wide:lo,hi=min(lo,.2),max(hi,5.)
    return {'seconds':float(max(0,value)), 'p20':float(max(0,value*lo)),
        'p80':float(max(0,value*hi))}


def _warnings(h,r,p):
    out=[]
    if _apple(h):out.append('Apple prior rests on a few physical-machine reports; chips without one borrow the nearest generation.')
    if _gpu_item(h,p) is None:out.append('Unseen GPU: supplied dense tensor rate or G=1 fallback; wide uncertainty.')
    if not (r.get('sampling_geometry') or {}).get('first'):out.append('Sampling geometry inferred from current H3 engine rules.')
    if any(r.get(f'pass{i}_resident_blocks') is None for i in [1,2]):out.append('Missing planned transformer residency: coarse VRAM fallback.')
    if r.get('reference_seconds') is None and _media_units(r):out.append('Reference duration unavailable; pooled one-reference encoding cost.')
    return out


def predict_prior(hardware, request, cold_flags, params=None):
    """Per-stage {seconds,p20,p80}, total, diagnostics and warnings.

    video_decode is a diagnostic SUBSET of decode: never add it to total.
    Input hardware RAM/VRAM use GiB. pcie_bandwidth_gbs is decimal GB/s.
    request uses schema names plus optional pass1_resident_blocks, reference_seconds,
    step_cache (the effective first-pass cache).
    Cold flags may include pass1/2_canvas_compiled and pass1/2_compile_cold.
    Unknown flags must be None, never inferred from future observed first steps.
    """
    p=_params(params)
    v,d=_raw(hardware,request,cold_flags or {},p)
    if request.get('step_cache'):
        # The fit has only plain passes; a step cache shortens the first pass alone.
        from .tiers import sampling_share
        d['step_cache_share']=sampling_share(request['step_cache'])
        v['pass1']*=d['step_cache_share']
        _sum_total(v)
    wide=_apple(hardware) or _gpu_item(hardware,p) is None
    return _decode_save({'stages':{k:_interval(k,x,p,wide=wide) for k,x in v.items() if k!='total'},
        'total':_interval('total',v['total'],p,wide=wide), 'diagnostics':d,
        'warnings':_warnings(hardware,request,p)})


def predict_live(state, params=None):
    """Same v1 state/output contract; completed steps only, no future timings.

    v2.selection selects each stage; no extra state keys are required. mode is
    retained for the v1 whole/compute comparison. v2 ratio and log-decode models
    have their own calibrated speed transfer, independent of that ablation.
    """
    p = _params(params)
    h,r = state['hardware'],state['request']
    flags = state.get('cold_flags') or {}
    mode = state.get('mode',p.get('live_mode','compute'))
    if mode not in ['compute','whole']:
        raise ValueError('mode must be compute or whole')
    a = {i:list(state.get(f'pass{i}_steps') or []) for i in [1,2]}
    if any(not math.isfinite(float(t)) or t<=0 for steps in a.values() for t in steps):
        raise ValueError('Completed step times must be positive finite seconds')
    select = p.get('v2',{}).get('selection',{})
    warmup = select.get('warmup','median')
    i = 2 if len(a[2])>=2 else 1
    measured,used = _warm(a[i],h,r,i,p,warmup)
    s,c = step_parts(h,r,i,p)
    slowdown = measured/max(s+c,.01)
    factor = measured/max(s+c,.01) if mode=='whole' else max(.02,measured-s)/max(c,.01)
    factor = float(_clip(factor,.1,10.)) if used else 1.
    v,d = _raw_base(h,r,flags,p,factor,mode,i)
    if 'v2' in p:
        p2method = select['live_pass2'] if used else select['prior_pass2']
        if i==1 and p2method!='original':
            _set_pass2(v,d,h,r,flags,p,_pass2(h,r,p,measured,p2method))
        elif i==2:
            _set_pass2(v,d,h,r,flags,p,measured)
        decode_step=d['pass2_warm_step'] if step_counts(r)[1] else measured
        for name in ['video_decode','decode']:
            method = select[('live_' if used else 'prior_')+name]
            if method=='original' and mode=='whole':
                continue
            v[name] = _decode_value(h,r,p,name,method,factor,slowdown,decode_step,bool(used))
    counts = step_counts(r)
    done = set(state.get('completed_stages') or [])
    if 'decode_save' in done:
        done.update(['decode', 'save_other'])
    if a[1] or a[2]:
        done.update(['encode','load'])
    if a[2]:
        done.update(['pass1','upscale'])
    for j in [1,2]:
        key=f'pass{j}'; n=counts[j-1]; seen=len(a[j])
        warm = _warm(a[j],h,r,j,p,warmup)[0] if seen>=2 else d[key+'_warm_step']
        if seen>=2:
            d[key+'_warm_step'] = warm
        rem = max(0,n-seen)*warm
        if n and not seen:
            rem += d[key+'_first_extra']
        if n>seen and key not in (state.get('pass_wall_overhead_done') or []):
            rem += p['pass_wall_overhead'][str(j)]
        if state.get('current_stage')==key:
            rem = max(0.,rem-float(state.get('step_elapsed_seconds') or 0))
        v[key]=rem
    _sum_total(v)
    for key in done:
        if key in v:
            v[key]=0.
    if 'decode' in done:
        v['video_decode']=0.
    for key,elapsed in (state.get('stage_elapsed_seconds') or {}).items():
        if key in v and key not in ['pass1','pass2','total']:
            v[key]=max(0.,v[key]-float(elapsed))
    v['total']=sum(v[k] for k in ['encode','load','pass1','upscale','pass2','decode','save_other'])
    kind='live_pass2' if i==2 else 'live_pass1'
    wide=_apple(h) or _gpu_item(h,p) is None
    result={'stages':{k:_interval(k,x,p,kind,wide) for k,x in v.items() if k!='total'},
        'total_remaining':_interval('total',v['total'],p,kind,wide),
        'diagnostics':dict(d,calibration_pass=i,warm_steps_used=used,warmup_method=warmup),
        'warnings':_warnings(h,r,p)}
    if state.get('elapsed_seconds') is not None:
        elapsed=float(state['elapsed_seconds'])
        result['total_request']={k:x+elapsed for k,x in result['total_remaining'].items()}
    return _decode_save(result)
