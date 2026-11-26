"""The quality levels, defined once.

Every entry (editor, Agent API, ComfyUI capabilities, estimates, docs) reads
its level -> plan mapping from here; tests/test_tiers.py checks the entries
that cannot import it (web JS, prose docs) against this table.

- light: two-pass, 8 + 3 steps, first pass at half size (2x learned upscale).
- medium: two-pass, 8 + 3 steps, a larger first pass (`upscale`, below 2).
- high: one pass of 16 steps with the guarded audio-video EasyCache.
- max: one pass of 20 steps with the same guarded cache.

Each level uses the step count in the table below. Only a two-pass level
samples a second time. Single-pass levels carry REFINE_STEPS only as the neutral value the `refine_steps` field
needs; it is never run for them, and their `upscale` is 2 (no factor).
`step_cache` is the engine's cache key: None, 'easycache-av' (audio-video
EasyCache) or 'fbcache' (first-block cache, no level uses it; a request may
still ask for it). `upscale` is the two-pass learned upscale factor
(two_pass.SCALES): change medium's number here, and everything follows.

Mac (MPS) runs the same levels (TIERS_MAC). Results made before the
audio-video EasyCache was ported there, one pass of 16 steps without a cache,
still count as high (LEGACY_MAC_HIGH). Earlier one-pass 20-step results
without a cache still count as max on every platform (LEGACY_MAX).
"""
import sys

REFINE_STEPS = 3
STEP_CACHES = (None, 'easycache-av', 'fbcache')

TIERS = {
    'light': dict(steps=8, two_pass=True, step_cache=None, upscale=2),
    'medium': dict(steps=8, two_pass=True, step_cache=None, upscale=1.5),
    'high': dict(steps=16, two_pass=False, step_cache='easycache-av', upscale=2),
    'max': dict(steps=20, two_pass=False, step_cache='easycache-av', upscale=2),
}
TIERS_MAC = TIERS
LEGACY_MAX = dict(steps=20, two_pass=False, step_cache=None, upscale=2)
LEGACY_MAC_HIGH = dict(steps=16, two_pass=False, step_cache=None, upscale=2)

# Sampling time of a cached pass as a share of the same pass without a cache,
# for estimates that have no run with that cache yet. B200, 20 s clips:
# EasyCache-AV sampled 1.8-1.9x and the first-block cache 1.4-1.5x faster than
# plain 20 steps. 10 s clips with the GPU held to 8-48 GiB: EasyCache-AV
# 0.48-0.75 of plain 20 steps (median about 0.6); a first-block cache kept at
# 32-48 GiB 0.71-0.82. Under the current keep rule, on a B200 held to 8-24 GiB,
# it is kept at 12 and 24 GiB, 0.81-1.28 (median 0.87), and dropped at 8 and
# 16 GiB. The shares are not fitted to the median: EasyCache-AV is slightly
# conservative, the first-block cache near the 8-24 GiB median. A first-block
# cache the planner did not keep is plain 20 steps.
# EasyCache-AV, guarded (at most 40% of steps reused, never twice in a row): measured on the RTX PRO 6000,
# 864x1184, 5 s, 20 steps, 2 reused steps: 212 s against 238 s for the plain 20 steps (about .88 of the sampling).
# At 16 steps the guarded cache reused 1–4 steps (about 14% on average),
# so the same conservative .88 share also applies at 16 steps.
SAMPLING_SHARE = {None: 1., 'easycache-av': .88, 'fbcache': .9}

# Sampling time (both passes) of a two-pass request with this upscale factor,
# as a share of the same request at 2x, for estimates without a run of that
# factor. 1.5: H200, 1344x768 20 s, 221.5 s against 177.8 s; the stage model
# gives 1.25-1.30 from 10 to 20 s on 8-141 GiB cards. 1.2: the stage model
# only (1.62-1.78), not yet measured.
UPSCALE_SHARE = {2: 1., 1.5: 1.27, 1.2: 1.72}


def on_mac():
    """FreeVideo runs on Apple's MPS backend on every Mac."""
    return sys.platform == 'darwin'


def table(mac=False):
    return TIERS_MAC if mac else TIERS


def plan(name, mac=False):
    """(two_pass, base_steps, refine_steps) of a level."""
    tier = table(mac)[name]
    return tier['two_pass'], tier['steps'], REFINE_STEPS


def step_cache(name, mac=False):
    return table(mac)[name]['step_cache']


def upscale_factor(name, mac=False):
    """The learned upscale factor of a two-pass level; 2 for every other level."""
    return table(mac)[name]['upscale']


def name_for(steps, two_pass, refine_steps=REFINE_STEPS, step_cache=None, mac=False, upscale=2):
    """The level with exactly this plan, or None for custom sampling.

    `upscale` tells the two-pass levels apart; a single pass has no factor."""
    upscale = upscale if two_pass else 2
    found = next((name for name, tier in table(mac).items()
                  if tier['steps'] == steps and tier['two_pass'] == two_pass
                  and (not two_pass or refine_steps == REFINE_STEPS) and tier['step_cache'] == step_cache
                  and tier['upscale'] == upscale), None)
    if found is None and (steps, two_pass, step_cache, upscale) == tuple(LEGACY_MAX.values()):
        return 'max'
    if found is None and mac and (steps, two_pass, step_cache, upscale) == tuple(LEGACY_MAC_HIGH.values()):
        return 'high'
    return found


def table_level(steps, two_pass, refine_steps=REFINE_STEPS, step_cache=None, mac=False, upscale=2):
    """The level whose sampling table a plan needs, for naming a missing download.

    Tables follow the step count only: 16 steps names high, 20 names max.
    Prefer the exact level or a legacy alias, then the other platform's table,
    then the first level with that step count (light for 8 + 3, whatever the
    upscale factor).
    """
    found = name_for(steps, two_pass, refine_steps, step_cache, mac, upscale) or name_for(
        steps, two_pass, refine_steps, step_cache, not mac, upscale)
    return found or next((name for name, tier in TIERS.items()
                          if tier['steps'] == steps and tier['two_pass'] == two_pass
                          and (not two_pass or refine_steps == REFINE_STEPS)), None)


def sampling_share(cache):
    """Estimated sampling time with this effective cache, as a share of the plain pass."""
    return SAMPLING_SHARE.get(cache, 1.)


def upscale_share(factor):
    """Estimated two-pass sampling time with this upscale factor, as a share of 2x."""
    return UPSCALE_SHARE.get(factor, 1.)
