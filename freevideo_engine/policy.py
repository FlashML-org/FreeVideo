"""Geometry-aware resource placement, with local history and measured tuning.

Capacity fields use bytes, CLI sizes use GiB. Predictions are not hard limits or
evidence of measured speed on an untested GPU. A process OOM remains a failure.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import math
from .hardware import Hardware, GiB
from .system import HOST_WEIGHT_HEADROOM, weight_cache_headroom, residual_host_headroom

# One prepared FP8 transformer block, as reserved on the host allocator.
BLOCK_BYTES = 432_500_000

# GPU bytes a request needs beyond resident block weights under the sub-10 GiB
# chunking profile: activations, the block-output cache, the transfer slot, root
# weights and allocator slack. From one complete 1344x768, 243-frame, 8-step run
# on a 12 GiB RTX 4080 Laptop at resident_blocks 9, where reserved VRAM peaked at
# 11.06 GiB; subtracting the nine resident blocks leaves 7.43 GiB. head_chunk 4
# then measured 0.73 GiB more than head_chunk 2 over three warm steps, and this
# keeps a small margin above the sum. window_batch stays at 1 in this band, so
# nothing is added for batched windows.
#
# Use the whole-run peak, not a slope. Reserved VRAM climbs 0.74 GiB per step
# for the first steps and then flattens, so extrapolating three probe steps
# predicted 11.82 GiB against the 11.06 GiB actually measured over eight.
# Compare against reserved rather than allocated: reserved is what occupies the
# device, and expandable segments are unavailable on Windows.
#
# Earlier interrupted runs reported a 5.15 GiB peak and are not evidence: the
# RAM guard stopped them inside the first sampling step, before the peak.
# Measured on Ada only; larger profiles keep their own estimates.
#
# Reference arithmetic for small-memory requests. Larger head groups are
# optimizer candidates; apply them after complete local output comparison.
# Head group and its activation allowance for the band under 10 GiB. Four
# heads cost exactly what two do. Measured on an H200 at three corners of the
# band with the card as the ceiling, changing nothing but the group:
#
#   8 GiB, 243 frames    45.41 -> 37.22 s/step   peak 7.34 GiB both
#   9 GiB, 345 frames    72.11 -> 58.26 s/step   peak 8.34 GiB both
#  10 GiB, 345 frames    56.11 -> 47.31 s/step   peak 9.39 GiB both
#
# Those corners span residual offload on and off, the host attention buffer on
# and off, and 0 to 2 resident blocks, and the peak did not move in any of
# them. The arithmetic bound agrees: a slice is tokens x heads x 128 x 2
# bytes, so doubling the group adds 151 MiB of QKV at 345 frames with all
# three tensors live, against 0.66 GiB of measured spare. That bound is what
# carries this to architectures with no measurement here -- every corner above
# is SM90. This needs no arithmetic of its own -- unlike eight heads, which
# cost 0.00, 0.41 and 0.84 GiB at the same three corners and overran a 10 GiB
# card at 345 frames. That interacts with where the attention outputs live and
# would need its own accounting and more corners than three.
# Eight heads in this band, where the budget covers them. Measured on an
# H200 at 1344x768x243 with the card as the ceiling, changing only the group:
#
#   9 GiB card, budget 8.50    13.03 -> 10.43 s/step   peak 8.34 -> 8.43
#  10 GiB card, budget 9.50    13.00 -> 10.37 s/step   peak 9.33 -> 9.33
#
# So 20% for 0.09 GiB at one corner and nothing at the other. Two corners
# refuse it, and 8.5 GiB is the reserve that reproduces all four: an 8 GiB
# card's 7.50 budget does not reach it, and the group peaked 8.02 there,
# over the card by 0.02 with every window and both chunk sizes. A 10 GiB
# card at 345 frames needs 10.42 after the token scaling below against a
# 9.50 budget, and the group peaked 10.30 there, over by 0.30.
#
# Where the outputs live interacts with this: a 9 GiB card at 345 frames
# stages residuals and outputs on the host, and eight heads did fit there
# at a 8.34 GiB peak. This reserve declines that corner, which costs the
# 15% it measured and keeps one rule for the band. The token axis is under
# measurement and is what would separate them.
#
# The entry is 8.2, not 8.5, because both peaks above were measured with the
# attention outputs on the GPU and GPU_ATTENTION_OUTPUT_BYTES adds that 0.3
# back after selection. Carrying 8.5 here charged it twice, which pushed a
# 9 GiB card's outputs to the host -- and eight heads with host outputs
# measured 102.88 s/step against 13.03, an eightfold loss. A reserve must
# not be able to buy the wider group by giving up the outputs.
SMALL_ACTIVATION_RESERVES = ((8, int(8.2 * GiB)), (4, int(7.6 * GiB)))
# The 8 GiB Windows path need not keep four heads at every token count.
# PRO 6000 full two-pass runs at 9720 first-pass rows measured head 4/8 at
# 11.78/9.81 s per warm step and 3.955/3.957 GiB reserved. At 18144 rows,
# the pair measured 7.72/6.13 s with both reserving 4.328 GiB. Native Windows
# reports 202/203 cover head-4 baselines through 18144 rows. Allow eight only
# in that small-canvas interval through the measured 243 frames, with its
# existing larger workspace allowance and GPU outputs fully funded. References
# count toward the same interval; equal token counts at longer durations do
# not prove that the linear-attention scan workspace remains the same.
# Larger groups can change floating-point rounding; bit identity is not a
# requirement for a measured chunking policy. Sixteen was no faster here.
WINDOWS_SMALL_HEAD8_TOKENS = 18144
WINDOWS_SMALL_HEAD8_FRAMES = 243
# Keeping the attention outputs on the GPU instead of in host memory costs this
# much extra peak. See benchmarks/2026-09-15-consumer-capacity-grid.json. Measured at four corners of the band that uses the host
# buffer: 0.15 GiB at 8 GiB/1344x768x243, 0.23 at 8 GiB/1024x1024x243, 0.22 at
# 9 GiB/1344x768x345 and 0.04 at 10 GiB/1344x768x345. Rounded up past the worst
# of those so residency, not the reserve's slack, pays for it.
GPU_ATTENTION_OUTPUT_BYTES = int(.3 * GiB)
# Whole-device peak of running the bottom of the band without residual storage:
# head 4, no resident weights, attention outputs on the GPU. Measured over
# complete eight-step requests on an 8 GiB card at 1344x768x243: 7.68 GiB at
# 32 GiB of RAM and 7.70 at 16 GiB with one window, and 7.69 with four on the
# same card. Rounded up past all three, because what it replaces -- moving the
# residual stream to the host -- measured 2.6 times slower.
SMALL_NO_WEIGHTS_PEAK = int(7.8 * GiB)
# The same path one card size down, with the attention outputs in host memory
# because the budget cannot also cover them on the card. Whole-device peak over
# complete eight-step requests on a 7 GiB card at 1344x768x243: 6.33 GiB at 8,
# 12, 16 and 32 GiB of RAM. The plan streams weights at the first three of
# those and holds them at the last, and the peak did not move, so this is the
# activation path rather than an allocator accident. Rounded up past it by the
# 0.30 GiB of margin the figure above keeps, which also keeps a 6 GiB card out:
# that card fits the same path at 5.90 of its 6.00, and 0.10 GiB is half the
# thinnest margin any Windows request in the field has survived.
SMALL_HOST_OUTPUT_PEAK = int(6.7 * GiB)
SMALL_ACTIVATION_RESERVE = SMALL_ACTIVATION_RESERVES[-1][1]

# Residency ceiling. Blocks held on the GPU do not buy time, and past a
# point they cost it. Measured at the same cap and geometry, changing only
# how many blocks are held:
#
#   24 GiB cap   29 blocks  9.24 s/step  peak 22.11 GiB
#                 8 blocks  9.20 s/step  peak 16.61 GiB   -0.4%, frees 5.50
#   32 GiB cap   48 blocks  9.28 s/step  peak 29.75 GiB
#                 8 blocks  9.15 s/step  peak 16.61 GiB   -1.3%, frees 13.14
#
# Holding all 50 and dropping prefetch to afford them was worse still: 9.206 s
# at 31.95 GiB against 9.047 s at 30.22 GiB for 44 plus prefetch. Streaming
# the remainder moved 19.3 GiB in 0.44 s of a 72 s request over a link
# measured at 47.7 to 54.1 GB/s, so the transfers are not the cost; the
# resident copies are. Hold the smallest number that was measured, and spend
# nothing further on memory that buys no time.
# See benchmarks/2026-09-15-consumer-capacity-grid.json.
# Blocks the GPU holds when host RAM does not need it to hold more. Zero is
# not measured, so keep the smallest count that is.
RESIDENT_TARGET = 8

# Above this the resident copies displace nothing useful, so it bounds the
# VRAM-side arithmetic before host RAM narrows it further.
RESIDENT_VRAM_CEILING = 44

# A streamed transformer uses one device transfer slot without prefetch.  The
# read-ahead slot adds one prepared block (BLOCK_BYTES) and lets host staging
# overlap the previous block's compute.  The old 14 GiB gate left the lower
# part of the 16 GiB Windows tier (for example a 13.7 GiB live budget) on the
# synchronous path even though that tier still has enough allocator headroom
# for the second slot.  Keep the 12 GiB tier unchanged; its measured budget
# is 11.5 GiB and remains below this threshold.
PREFETCH_GPU_BUDGET = int(13.5 * GiB)


def prefetch_for_budget(gpu_budget, *, ram_budget=None, twelve=False, ampere=False,
                        system='Linux', architecture=None):
    """Whether a second weight transfer slot fits the selected GPU budget.

    Native Windows Blackwell also benefits at 13.5--14 GiB with partial host
    retention. Residency below includes the second device slot in its complete
    token-scaled workspace; this never spends an unaccounted extra block.
    """
    # This extra lower-16-GiB rule was measured on Windows WDDM.  Linux has
    # different page-cache and allocator behaviour, so do not silently apply
    # a Windows-only transfer overlap threshold there.  The established
    # high-capacity and Ampere paths below remain cross-platform.
    low_host = (system == 'Windows' and ram_budget is not None
                and ram_budget < 8 * GiB)
    # Preserve the established high-capacity path.  The new lower-band rule
    # is an additional case for the 16 GiB tier; it must not turn prefetch off
    # for the already-validated 20/24/32 GiB profiles.
    return (gpu_budget >= 14 * GiB
            or (gpu_budget >= PREFETCH_GPU_BUDGET and low_host)
            or (gpu_budget >= PREFETCH_GPU_BUDGET and system == 'Windows'
                and architecture == 'blackwell-rtx')
            or (twelve and ampere))

# Whole-device bytes a request needs beside the resident blocks, by head
# group and token count, with the attention outputs on the GPU. A head-group
# slice is tokens x heads x 128 x 2 bytes, so this scales with the request
# and cannot be one number per group: the same 10 GiB card takes sixteen
# heads at 41472 tokens and not even eight at 102816.
#
# Read off complete eight-step runs as peak minus the blocks the run held,
# from the cases where residency had been reduced until the plan just fit --
# those are the ones at their true minimum. Four independent cards agreed on
# head 16 at 72576 tokens to a hundredth of a GiB:
#
#   11 GiB card, 2 blocks   peak 10.86  ->  10.05
#   12 GiB card, 4 blocks   peak 11.66  ->  10.05
#   13 GiB card, 7 blocks   peak 12.86  ->  10.04
#   13 GiB card, 6 blocks   peak 12.46  ->  10.05
#
# Cards with slack report more, because the allocator keeps a larger pool;
# those readings describe the allocator, not the requirement.
#
# Selecting the widest group whose requirement fits the budget reproduces
# every measured boundary: sixteen heads overran a 10 GiB card at 72576
# tokens by 1.09 GiB and fit an 11 GiB card by 0.14; eight overran an 8 GiB
# card by 0.02 and fit a 9 GiB card; at 102816 tokens eight overran 10 GiB
# by 0.44 and fit 12, and sixteen overran 12 by 0.87 and fit 16.
# See benchmarks/2026-09-15-consumer-capacity-grid.json.
ACTIVATION_BY_TOKENS = {
    4: ((41472, 6.11), (72576, 7.69), (102816, 9.97)),
    8: ((41472, 6.11), (72576, 8.03), (102816, 10.53)),
    16: ((41472, 6.75), (72576, 10.05), (102816, 14.21)),
}


def activation_bytes(head, tokens):
    """Measured GPU bytes a head group needs beside the resident blocks.

    Piecewise linear through the measured token counts, extrapolated with the
    nearest measured slope.
    """
    tokens = 72576 if tokens is None else tokens

    def interpolate(group):
        anchors = ACTIVATION_BY_TOKENS[group]
        for (low, at_low), (high, at_high) in zip(anchors, anchors[1:]):
            if tokens <= high or (high, at_high) == anchors[-1]:
                slope = (at_high - at_low) / (high - low)
                return int((at_low + slope * (tokens - low)) * GiB)
        raise AssertionError('unreachable: anchors are non-empty and ordered')

    # A wider group cannot need less. Below the measured range the two slopes
    # crossed, which is the extrapolation talking, not the requirement.
    return max(interpolate(group) for group in ACTIVATION_BY_TOKENS if group <= head)

# Host memory the text encoder holds while it runs, which happens before the
# transformer is placed at all and was not modelled here. Measured on an H200
# at three GPU budgets, after its weights reach the device and its consumed
# checkpoint pages are released:
#
#   GPU budget  6.45 GiB   2.33 GiB resident = 1.94 anonymous + 0.39 mapped
#   GPU budget  9.50 GiB   3.46 GiB          = 3.07 + 0.39
#   GPU budget   120 GiB   2.91 GiB          = 2.52 + 0.39
#
# Before that release the same measurements were 7.82, 6.10 and 9.44 GiB,
# almost all of it clean page cache the transfer walked through. A guard that
# credits clean pages sees past it; a hard cgroup or commit limit does not
# wait for reclaim, and a 10 GiB limit killed the encoder at 9.84 GiB with no
# disk read recorded, before a single sampling step. Rounded up past the
# largest of the three.
#
# The encoder and the transformer never run at once -- the encode child exits
# before sampling -- so this is a floor on admission, not an addition to the
# request's budget. Cached or preencoded conditioning skips the encoder
# entirely and this does not apply.
ENCODER_HOST_BYTES = int(3.6 * GiB)

# Residual placement keeps the same head/FF/projection kernels. Full-capacity
# qualification and its platform scope are recorded in docs/POLICY.md. This
# lower estimate applies only when the original small path cannot fit; spare
# memory on larger cards must not trigger additional residual transfers.
RESIDUAL_ACTIVATION_RESERVE = int(6.4 * GiB)

# What that path actually needed on the card that was refused for wanting 6.4.
# An RTX PRO 6000 request was refused at a 6.267 GiB budget, 0.133 short. Run
# there with the plan this branch builds -- staging on, attention outputs in
# host memory, no resident blocks -- it completed all eight steps at 36.86
# s/NFE with a 5.246 GiB sampling Torch reserved peak and a 6.712 GiB
# whole-device NVML peak, of which 0.533 is the MPS server's own allocation:
# 6.179 GiB of client memory against a 6.267 budget. So the refusal was a
# false negative by about 0.2 GiB, and admission is separated from the reserve
# the plan is then built with, which stays at 6.4 so no already-admitted card
# changes placement.
#
# Linux only, like every other measurement in this band. The same run has not
# been done on native Windows, where exceeding the card is a silent WDDM spill
# rather than an error, and 0.09 GiB of measured margin is not enough to
# assume it. benchmarks/pro6000-verification/results-pro6000.md.
RESIDUAL_ADMISSION_FLOOR = int(6.25 * GiB)
MIN_GPU_RESERVE_GIB = .2


def windows_gpu_output_workspace(video_tokens, *, prefetch=False):
    """Starting workspace for head-8 GPU outputs with the full per-tensor FF stash.

    Two complete Windows 5060 Ti requests constrain this estimate: 896-square
    / 243 frames used 11.14 GiB reserved with zero resident blocks; 1344x768
    / 243 used 13.75 GiB with eight blocks and FF recomputation. Subtracting
    those weights and adding the avoided 1.96 GiB stash gives 12.49 GiB.
    The 6.5 + 6 * token-ratio envelope covers both anchors. It is a cold-start
    estimate, not a fitted throughput model or proof of capacity elsewhere.
    The OS growth reserve has already been subtracted from the budget.
    """
    tokens = 72576 if video_tokens is None else video_tokens
    return int((6.5 + 6. * tokens / 72576) * GiB) + (BLOCK_BYTES if prefetch else 0)


class ResourceBudgetError(ValueError):
    """A rejected plan with the exact capacities, reserves and shortfalls."""
    def __init__(self, hardware, gpu_total, ram_total, free_gpu, free_ram, reserve_gpu, reserve_ram,
                 *, gpu_minimum_bytes=5*GiB, gpu_requirement=None, canvas=None,
                 ram_minimum_bytes=4*GiB, ram_requirement=None):
        self.details = dict(gpu=hardware.gpu_name, system=hardware.system,
            gpu_total_bytes=hardware.vram_total, gpu_capacity_bytes=gpu_total,
            gpu_available_bytes=free_gpu, gpu_reserve_bytes=int(reserve_gpu * GiB),
            gpu_budget_bytes=free_gpu-int(reserve_gpu * GiB), gpu_minimum_bytes=gpu_minimum_bytes,
            ram_total_bytes=hardware.ram_total, ram_capacity_bytes=ram_total,
            ram_available_bytes=free_ram, ram_reserve_bytes=int(reserve_ram * GiB),
            ram_budget_bytes=free_ram-int(reserve_ram * GiB), ram_minimum_bytes=ram_minimum_bytes,
            cgroup_ram_limit_bytes=hardware.cgroup_ram_limit)
        failed = [name for name in ('gpu', 'ram')
                  if self.details[name+'_budget_bytes'] < self.details[name+'_minimum_bytes']]
        self.details['insufficient'] = failed
        for name in ('gpu', 'ram'):
            self.details[name+'_shortfall_bytes'] = max(
                0, self.details[name+'_minimum_bytes'] - self.details[name+'_budget_bytes'])
        if gpu_requirement is not None:
            self.details['gpu_requirement'] = gpu_requirement
        if ram_requirement is not None:
            self.details['ram_requirement'] = ram_requirement
        # The geometry belongs to the refusal, not to whichever of the two
        # budgets happened to fail: a VRAM refusal is the one a user most
        # needs it for, because the way out is a smaller request.
        self.details['geometry'] = dict(canvas) if canvas is not None else None
        lines = ['FreeVideo resource planning could not admit this request: ' + ', '.join(name.upper() for name in failed),
                 'GPU: %s (%s)' % (hardware.gpu_name, hardware.system)]
        if ram_requirement == 'text_encoder_host':
            lines.append('The text encoder holds %.2f GiB of host memory while it runs, and this '
                         'machine offers %.2f GiB. It runs before sampling, so the request would '
                         'stop there. Reuse cached text conditioning, or supply it preencoded, '
                         'and the encoder is not loaded at all.'
                         % (self.details['ram_minimum_bytes'] / GiB,
                            self.details['ram_budget_bytes'] / GiB))
        if gpu_requirement == 'estimated_working_set':
            lines.append('The smallest-profile VRAM estimate includes activations, fixed weights and '
                         'transfer/work buffers, with CPU residual staging and no resident transformer blocks. '
                         'This is a planning estimate, not a measured OOM.')
            if canvas is not None:
                lines.append('Request: %d x %d, %d frames (%.3f s); %d video tokens, %d reference tokens.' %
                             (canvas['width'], canvas['height'], canvas['frames'], canvas['frames']/24,
                              canvas['video_tokens'], canvas.get('reference_video_tokens', 0) +
                              canvas.get('reference_audio_tokens', 0)))
        for name, label in (('gpu', 'VRAM'), ('ram', 'RAM')):
            lines.append('%s: %.2f GiB available, %.2f GiB reserved for system growth, '
                         '%.2f GiB usable; minimum %.2f GiB. Capacity %.2f / %.2f GiB.' %
                         (label, self.details[name+'_available_bytes']/GiB,
                          self.details[name+'_reserve_bytes']/GiB, self.details[name+'_budget_bytes']/GiB,
                          self.details[name+'_minimum_bytes']/GiB, self.details[name+'_capacity_bytes']/GiB,
                          self.details[name+'_total_bytes']/GiB))
            if self.details[name+'_shortfall_bytes']:
                lines.append('%s shortfall: %.2f GiB.' % (label, self.details[name+'_shortfall_bytes']/GiB))
        lines.append('Available memory includes validated idle-cache credit, if present. Resolution, frames and steps were not reduced.')
        super().__init__('\n'.join(lines))



def memory_fraction(budget_bytes, total_bytes):
    """Share of the device a budget represents, or None if it covers all of it.

    Pure arithmetic, so it lives with the budgets rather than in the worker:
    the CPU test job has no torch, and importing the worker there pulled it in.
    Pure planning arithmetic. The Windows worker applies a separate live
    allocator ceiling; Linux only enforces explicit benchmark ceilings.
    """
    if not budget_bytes or not total_bytes or budget_bytes <= 0:
        raise ValueError('A positive GPU budget and device capacity are required')
    if budget_bytes >= total_bytes:
        return None
    return budget_bytes / total_bytes


@dataclass
class Policy:
    schema_version: int
    hardware: dict
    gpu_budget_bytes: int
    ram_budget_bytes: int
    gpu_system_reserve_bytes: int
    ram_system_reserve_bytes: int
    allocator_config: str
    engine: dict
    decoder: dict
    evidence: str
    notes: list[str]
    capacity_trial: bool = False
    lora_max_block_bytes: int = 0
    lora_root_bytes: int = 0

    def to_dict(self):
        return asdict(self)

    def legacy_profile(self):
        return dict(name='Automatic VDN Engine policy', gpu_budget_gb=self.gpu_budget_bytes / 1e9,
                    inference_ram_budget_gb=self.ram_budget_bytes / 1e9,
                    allocator_config=self.allocator_config, engine=self.engine, decoder=self.decoder,
                    policy=self.to_dict())


def resource_budget(hardware: Hardware, *, vram_gib=None, ram_gib=None,
                    gpu_reserve_gib=None, ram_reserve_gib=None):
    """Live capacity and growth reserves, without admitting a generation request."""
    # Saved policies store whole bytes. Validate in that same unit so the
    # minimum reserve survives the bytes -> GiB round trip between passes.
    if gpu_reserve_gib is not None and (not math.isfinite(gpu_reserve_gib) or
                                      gpu_reserve_gib * GiB < int(MIN_GPU_RESERVE_GIB * GiB)):
        raise ValueError('Keep at least 0.2 GiB GPU reserve for the system.')
    for name, value in [('VRAM', vram_gib), ('RAM', ram_gib)]:
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(name + ' capacity must be positive')
    gpu_total = min(hardware.vram_total, int(vram_gib * GiB)) if vram_gib else hardware.vram_total
    ram_total = min(hardware.ram_total, int(ram_gib * GiB)) if ram_gib else hardware.ram_total
    desktop = hardware.system == 'Windows'
    # Available memory already excludes the OS and other applications. Reserve
    # modest growth headroom from that live remainder, not a second fixed share
    # of nominal capacity. Explicit capacities remain upper bounds, never extra
    # physical memory or pagefile capacity.
    free_gpu = max(0, min(gpu_total, hardware.vram_free))
    free_ram = max(0, min(ram_total, hardware.ram_available))
    if hardware.cgroup_ram_limit is not None:
        free_ram = min(free_ram, hardware.cgroup_ram_limit)
    reserve_gpu = (gpu_reserve_gib if gpu_reserve_gib is not None else
                   .25 if desktop else max(.5, min(1., free_gpu / GiB * .025)))
    reserve_ram = ram_reserve_gib if ram_reserve_gib is not None else max(2. if desktop else 1., min(4., free_ram / GiB * .05))
    if not math.isfinite(reserve_ram) or reserve_ram < 0:
        raise ValueError('System RAM headroom must be finite and nonnegative.')
    gpu_budget = free_gpu - int(reserve_gpu * GiB)
    ram_budget = free_ram - int(reserve_ram * GiB)
    return dict(gpu_capacity_bytes=gpu_total, ram_capacity_bytes=ram_total,
                gpu_available_bytes=free_gpu, ram_available_bytes=free_ram,
                gpu_system_reserve_bytes=int(reserve_gpu * GiB),
                ram_system_reserve_bytes=int(reserve_ram * GiB),
                gpu_budget_bytes=gpu_budget, ram_budget_bytes=ram_budget)


def decoder_workspace(canvas=None):
    """Existing offloaded decoder allowance; fixed weights do not shrink with video length."""
    area = canvas['width'] * canvas['height'] / (1344 * 768) if canvas is not None else 1.
    return int(3.25 * GiB * max(1., area))


def choose(hardware: Hardware, *, vram_gib=None, ram_gib=None, attention='auto',
           gpu_reserve_gib=None, ram_reserve_gib=None, available_backends=None, canvas=None,
           demonstrated_ram_bytes=None, allow_capacity_trial=False, stage='generation',
           lora_max_block_bytes=0, lora_root_bytes=0):
    from .geometry import geometry
    if stage not in ('encoding', 'generation'):
        raise ValueError('Resource planning stage must be encoding or generation')
    if type(lora_max_block_bytes) is not int or lora_max_block_bytes < 0:
        raise ValueError('LoRA block bytes must be a nonnegative integer')
    if type(lora_root_bytes) is not int or lora_root_bytes < 0:
        raise ValueError('LoRA root bytes must be a nonnegative integer')
    if stage == 'encoding':
        lora_max_block_bytes = lora_root_bytes = 0
    block_bytes = BLOCK_BYTES + lora_max_block_bytes
    # Two transfer slots plus bounded raw-SwiGLU/LoRA workspace. Resident and
    # retained-host blocks below use the enlarged per-block storage as well.
    lora_workspace = (128 * 2**20 + 2 * lora_max_block_bytes + lora_root_bytes
                      if lora_max_block_bytes or lora_root_bytes else 0)
    if canvas is not None:
        checked = geometry(canvas['width'], canvas['height'], frames=canvas['frames'])
        if any(canvas.get(k) != checked[k] for k in ('width', 'height', 'frames', 'video_tokens')):
            raise ValueError('Resource policy requires aligned, consistent request geometry')
    reference_rows = 0
    if canvas is not None:
        for name in ('reference_video_tokens', 'reference_audio_tokens'):
            count = canvas.get(name, 0)
            if type(count) is not int or count < 0:
                raise ValueError('Reference token counts must be nonnegative integers')
            reference_rows += count
    effective_tokens = canvas['video_tokens'] + reference_rows if canvas is not None else None
    if hardware.capability < (8, 0):
        raise ValueError('This engine targets Ampere and newer NVIDIA GPUs.')
    budget = resource_budget(hardware, vram_gib=vram_gib, ram_gib=ram_gib,
                             gpu_reserve_gib=gpu_reserve_gib, ram_reserve_gib=ram_reserve_gib)
    gpu_total, ram_total = budget['gpu_capacity_bytes'], budget['ram_capacity_bytes']
    free_gpu, free_ram = budget['gpu_available_bytes'], budget['ram_available_bytes']
    reserve_gpu, reserve_ram = budget['gpu_system_reserve_bytes']/GiB, budget['ram_system_reserve_bytes']/GiB
    gpu_budget, ram_budget = budget['gpu_budget_bytes'], budget['ram_budget_bytes']
    desktop = hardware.system == 'Windows'
    request_gpu_budget = gpu_budget
    gpu_budget -= lora_workspace
    # What this machine has actually held, for the weight cache only.
    #
    # Retaining weights is worth a 20 GB disk read per step, and only while
    # the retained pages stay physically resident: a retained block paged out
    # is a slower disk, not a saving. So the ceiling on retention is physical
    # RAM this machine can hold, which is not what an availability API
    # reports. GlobalMemoryStatusEx counts free and standby pages only, so a
    # tree's own resident mapped weights are missing from it -- a request
    # using 4.30 GiB was once refused against an 8.50 GiB budget while the
    # tree held a 21.85 GiB working set. The runtime guard already credits
    # those pages back; planning did not, so a machine that had just held the
    # model saw a small availability figure and retained nothing. On a 31.6
    # GiB laptop reporting 7.5 GiB available that meant no retention at all
    # and 21.6 GB read from disk every step.
    #
    # The credit is a measurement, not an assumption about other processes:
    # the caller passes the largest RAM peak a complete, verified run reached
    # on this machine under the same compute build and geometry, or None. A
    # first run gets no credit and behaves exactly as before. It cannot raise
    # the guard's budget or admission -- only what the weight cache may keep.
    retention_budget = ram_budget
    if demonstrated_ram_bytes is not None:
        if type(demonstrated_ram_bytes) is not int or demonstrated_ram_bytes < 0:
            raise ValueError('Demonstrated RAM must be a nonnegative byte count')
        retention_budget = max(ram_budget, min(demonstrated_ram_bytes,
                                               ram_total - int(reserve_ram * GiB)))
    small_adjustment = (int(4.6 * GiB * (effective_tokens / (72 * 1008) - 1.))
                        if canvas is not None else 0)
    # Scale the existing activation estimate with output and reference rows.
    # Keep the decoder's fixed workspace funded; neither weights nor its
    # temporal tile shrink in proportion to the number of output frames.
    fixed_workspace = decoder_workspace(canvas)
    residual_need = max(fixed_workspace, RESIDUAL_ACTIVATION_RESERVE + small_adjustment)
    residual_floor = (residual_need if desktop else
                      max(fixed_workspace, RESIDUAL_ADMISSION_FLOOR + small_adjustment))
    # Preserve the reference-canvas trial boundary while allowing smaller
    # requests to reach their geometry-aware placement below 5 GiB.
    gpu_minimum = max(fixed_workspace, min(5 * GiB, residual_floor))
    original_reserve_gpu = reserve_gpu
    # Let a foreground request spend growth headroom before rejecting it.
    # This is live free memory, not the card's nominal capacity: a busy larger
    # card benefits too. Keep admitted placements and explicit user reserves
    # unchanged. Below 10 GiB even the reduced reserve stays in the bounded
    # small-card path. Neither the working-set estimate nor RAM is relaxed.
    # The reduced floor is the same on Windows. A larger one there is
    # tempting, because over-allocating VRAM is backed by system memory
    # instead of failing and the result is a collapse in speed rather than a
    # recoverable error -- but the field does not support it and a refusal is
    # its own failure. Three complete Windows requests peaked at 10.50 GiB of
    # local usage against 10.70 GiB of live free VRAM, 0.20 GiB of margin,
    # with no over-budget sample in any of them. Raising the floor to 0.50
    # turned a 6.8 GiB card from admitted into refused and bought nothing the
    # field had measured. What protects Windows instead is the allocator
    # ceiling, which makes the caching allocator OOM rather than leaning on
    # WDDM, and the two bounds above: the head group may not exceed its
    # measured requirement, and the no-weights path stays on the platform
    # whose margin was measured.
    if not desktop and gpu_reserve_gib is None and free_gpu < 10 * GiB and gpu_budget < residual_need:
        reserve_gpu = MIN_GPU_RESERVE_GIB
        request_gpu_budget = free_gpu - int(reserve_gpu * GiB)
        gpu_budget = request_gpu_budget - lora_workspace
    # The encoding phase already has a measured host floor. Do not discard a
    # preloaded encoder because the later video stage needs a larger minimum;
    # automatic_profile replans generation after conditioning has been saved.
    ram_minimum = ENCODER_HOST_BYTES if stage == 'encoding' else 4*GiB
    if gpu_budget < gpu_minimum or ram_budget < ram_minimum:
        raise ResourceBudgetError(hardware, gpu_total, ram_total, free_gpu, free_ram, reserve_gpu, reserve_ram,
                                  gpu_minimum_bytes=gpu_minimum, ram_minimum_bytes=ram_minimum,
                                  canvas=canvas)
    if ram_budget < ENCODER_HOST_BYTES:
        # Say so here rather than let a request spend a minute loading a
        # 14.6 GiB encoder and die before the first sampling step, which is
        # what a 10 GiB host did. Cached conditioning skips the encoder, so
        # this is what the note below offers.
        raise ResourceBudgetError(hardware, gpu_total, ram_total, free_gpu, free_ram,
                                  reserve_gpu, reserve_ram, canvas=canvas,
                                  gpu_minimum_bytes=gpu_minimum, ram_minimum_bytes=ENCODER_HOST_BYTES,
                                  ram_requirement='text_encoder_host')
    # A foreground generation may try the existing bounded path below a
    # measured estimate after reclaiming owned idle caches. This does not add
    # capacity or spend the user's reserve; real allocator OOMs remain bounded
    # recoverable failures. Strict estimates remain the default for callers;
    # installation may preview this candidate without claiming it fits.
    capacity_trial = bool(allow_capacity_trial and gpu_budget < residual_floor)
    available = set(available_backends if available_backends is not None else {'cudnn', 'torch-flash'})
    if attention == 'auto':
        attention = next((name for name in ('sage2', 'torch-flash', 'cudnn', 'fa2', 'fa4') if name in available), '')
    legs = attention.split('/')
    if len(legs) == 1:
        legs *= 2
    if len(legs) != 2 or any(leg not in available for leg in legs):
        raise ValueError('Requested attention backend is unavailable or failed its kernel probe: ' + attention)
    # Small cards trade extra transfers for lower activation storage. The
    # 12 GiB native-FP8 preset comes from the matched 12/32 capacity experiments.
    small = gpu_budget < 10 * GiB
    # Lowering Windows headroom can lift a 12 GiB laptop across the old
    # 10 GiB boundary. Keep its affordable head-4 path in that narrow band
    # until the larger path's actual workspace fits; entering that path only
    # to fall back to CPU residuals would waste the newly available memory.
    if (desktop and hardware.architecture != 'ampere' and gpu_budget < 11 * GiB
            and gpu_budget < windows_gpu_output_workspace(effective_tokens, prefetch=False)):
        small = True
    # No gap between the small and twelve bands. A 0.25 GiB sliver used to fall
    # through both and take an unmeasured branch with three resident blocks,
    # which made residency drop as the budget grew: three blocks at 10.1 GiB
    # against one at 10.3 GiB.
    twelve = not small and gpu_budget < 14 * GiB
    ampere = hardware.architecture == 'ampere'
    prefetch = prefetch_for_budget(gpu_budget, ram_budget=ram_budget,
                                   twelve=twelve, ampere=ampere,
                                   system=hardware.system, architecture=hardware.architecture)
    # The larger head group is worth about 10% at no extra peak at all. Paying
    # for its wider activation allowance out of residency, measured on an H200
    # at 1344x768x243 with the card as the ceiling:
    #
    #   cap 15 GiB   head 8 -> 16   9.93 -> 9.08 s/step   peak 14.68 both
    #   cap 16 GiB   head 8 -> 16   9.96 -> 8.99 s/step   peak 15.83 both
    #   cap 18 GiB   head 8 -> 16  10.05 -> 9.01 s/step   peak 16.61 both
    #   cap 20 GiB   head 8 -> 16  10.13 -> 9.10 s/step   peak 17.72/17.70
    #
    # The peaks are equal to the hundredth of a GiB: five fewer resident blocks
    # is 2.01 GiB, which is what the wider allowance asks for. The old 20 GiB
    # floor cost every card between there and the twelve band about a tenth of
    # its sampling time for nothing.
    #
    # Windows keeps the old floor. Its GPU output workspace below is written
    # for head 8 and was added for a 15.9 GiB card, which is the band this
    # moves; no Windows measurement covers the wider group there. The floor is
    # the twelve band's own boundary rather than the 14.5 GiB budget measured
    # above, so that no half-GiB sliver falls between two bands the way a
    # 0.25 GiB sliver once did below.
    if desktop:
        head = 8 if twelve or gpu_budget < 20 * GiB else 16
        # The band alone never checked whether the group's activations fit,
        # and on Windows nothing downstream would tell us: exceeding the card
        # is backed by system memory instead of failing, which is a silent
        # collapse in speed rather than an error the controller can recover.
        # The measured requirement is SM90's, and Windows needs at least that
        # much -- no expandable segments, and a compositor taking VRAM during
        # the run -- so it is a lower bound here and may only narrow the group,
        # never widen it. A 11 GiB card at 345 frames took head 8 against a
        # 10.00 GiB budget for a group measuring 10.53.
        while head > 4 and activation_bytes(head, effective_tokens) > gpu_budget:
            head //= 2
        # The GPU-output workspace below adds the wider group's surcharge to
        # its head-8 estimate, and a miss there moves attention outputs and
        # residuals to host memory. Narrow the group first; the second
        # transfer slot is given up below before anything is staged. A 32 GB
        # RTX 4080 SUPER at 1344x768x736 fit sixteen heads by 0.10 GiB, missed
        # their workspace by 4.77 and staged both, holding 14.5 of its 32 GB at
        # 360-595 s/step; eight heads needed 25.07 of its 30.40 GiB budget.
        # Without this, 26 to 30 s at 1344x768 staged and 31 to 38 s did not.
        if not small and not ampere:
            while head > 8 and (windows_gpu_output_workspace(effective_tokens, prefetch=False)
                                + activation_bytes(head, effective_tokens)
                                - activation_bytes(8, effective_tokens)) > gpu_budget:
                head //= 2
    else:
        # The widest group the request can afford at its own token count.
        # A fixed budget threshold cannot express this: 41472 tokens take
        # sixteen heads on an 8 GiB card and 102816 do not take eight on a
        # 10 GiB one, and the group is worth 13 to 51% where it fits.
        head = next((group for group in sorted(ACTIVATION_BY_TOKENS, reverse=True)
                     if gpu_budget >= activation_bytes(group, effective_tokens)), 4)
    resident = (4 if ampere else 1) if twelve else 3
    residual_offload = False
    cpu_outputs = False
    no_weights = False
    if small:
        # Match the reference activation allowance to the actual geometry,
        # then spend remaining memory on useful residency.
        # Full-resolution Windows requests keep their existing conservative
        # path. Small canvases can use the wider measured group, paying its
        # larger allowance out of residency, never out of system reserves or
        # by moving attention outputs to the host just to widen the group.
        windows_small_head8 = (desktop and not ampere and legs == ['sage2', 'sage2']
                              and effective_tokens is not None
                              and canvas['frames'] <= WINDOWS_SMALL_HEAD8_FRAMES
                              and effective_tokens <= WINDOWS_SMALL_HEAD8_TOKENS)
        if not desktop and head >= 8:
            # The measured requirement for the group this request can afford,
            # already scaled by its token count, so the band's own reference
            # allowance and its linear correction do not apply. The figure
            # includes the GPU attention outputs; the allowance added after
            # selection puts them back, so take them off here rather than
            # charge them twice -- doing that once bought a wider group by
            # moving the outputs to the host, which measured eight times
            # slower.
            selected = (head, activation_bytes(head, effective_tokens) - GPU_ATTENTION_OUTPUT_BYTES)
        else:
            available = [(heads, need) for heads, need in SMALL_ACTIVATION_RESERVES
                         if heads <= 4 or not desktop or (windows_small_head8
                             and gpu_budget >= need + small_adjustment + GPU_ATTENTION_OUTPUT_BYTES)]
            selected = next(((heads, need + small_adjustment) for heads, need in available
                             if gpu_budget >= need + small_adjustment), None)
        if (selected is None and not desktop
                and free_gpu >= SMALL_NO_WEIGHTS_PEAK + small_adjustment):
            # Hold no weights at all rather than move the residual stream to
            # the host. The card can take the whole activation path; what it
            # cannot take is that path plus a resident block. On an 8 GiB card
            # at 1344x768x243, against the residual-storage plan this replaces:
            #
            #   32 GiB RAM   34.76 -> 13.49 s/step   peak 7.68 GiB of 8.00
            #   16 GiB RAM   34.17 -> 14.75 s/step   peak 7.70 GiB of 8.00
            #
            # One resident block does not fit beside it -- 8.04 and 8.02 GiB,
            # over the card at both RAM values and both chunk sizes -- and the
            # block is worth under 1% against this path's 2.6x. Eight
            # heads do not fit either, and the head group rather than the
            # batching is why: window 1, 2 and 4 and both chunk sizes all
            # peaked exactly 8.02 GiB.
            #
            # The gate is live free VRAM rather than the budget, because the
            # growth headroom is what pays: the peak leaves 0.30 GiB of an
            # 8 GiB card where the reserve would hold back 0.50. That is the
            # same trade the reserve reduction below makes, for 2.6x instead
            # of for admission.
            #
            # Linux only. This is a measured whole-device peak with 0.20 to
            # 0.32 GiB of margin, not an arithmetic bound, and three things
            # stop that margin transferring to Windows: PyTorch has no
            # expandable segments there, so a tight allocation fragments
            # instead of growing a segment; the desktop compositor takes VRAM
            # during the run, so the live figure this gate reads can shrink
            # under it; and exceeding the card does not fail there. WDDM backs
            # the excess with system memory silently, which is catastrophically
            # slow rather than an error the controller can recover. A platform
            # with no error to catch needs a margin it has measured.
            selected = (4, SMALL_ACTIVATION_RESERVE + small_adjustment)
            no_weights = True
        if (selected is None and not desktop
                and reserve_gpu == original_reserve_gpu
                and free_gpu >= SMALL_HOST_OUTPUT_PEAK + small_adjustment
                and gpu_budget < SMALL_HOST_OUTPUT_PEAK + GPU_ATTENTION_OUTPUT_BYTES):
            # The same no-weights path one card size down, where the budget
            # cannot also cover the attention outputs, so they stay in host
            # memory. Leaving the reserve below the budget keeps them there and
            # holds no blocks, which is the configuration measured.
            #
            # The second condition is what keeps this to that configuration. An
            # 8 GiB card with 7.5 GiB live has a 7.00 GiB budget, which does
            # cover the outputs on the card -- and then the peak to beat is the
            # 7.68-7.70 GiB the branch above measured, against 7.5 of live
            # VRAM. That card keeps staging, as its own test says.
            #
            # The reserve condition is the other half of that. This route
            # trades peak for speed, and a card whose growth reserve has
            # already been reduced to be admitted at all has nothing left to
            # trade: a 6.8 GiB free card reaches a 6.60 budget only by
            # spending 0.30 of its reserve, and stacking a higher-peak route
            # on top of that is two risks for one gain. Those keep staging.
            #
            # This is what the 7 GiB band should have been running. It reached
            # the residual-staging route below because that route's gate asks
            # for 6.4 GiB of budget while the no-weights gate above asks for
            # 7.8 GiB of live VRAM -- and 7.8 was measured with the outputs on
            # the card. With them in host memory the same path peaks 6.33 GiB
            # on a 7 GiB card, which the gate above never asked about. The
            # staging it replaces moves 158 GB over PCIe every step for
            # nothing: measured at 1344x768x243, whole-device peak 6.33 either
            # way, at 8, 12, 16 and 32 GiB of RAM.
            #
            #   staging on   39.07  38.37  --     38.25 s/NFE
            #   staging off  22.64  21.65  21.09  19.04
            #
            # Also measured at consumer compute, because this cluster's card
            # is not the target: at an MPS thread share of 6%, calibrated to
            # reproduce an RTX 4080 Laptop's measured 101.6 s/NFE, the pair is
            # 136.78 against 118.77 -- 1.15x rather than 2.05x, since the
            # staging is PCIe-bound and does not scale with the cards. A
            # consumer card's narrower bus makes 1.15x the floor, not the
            # estimate. Linux only, for the reasons the branch above gives.
            selected = (4, SMALL_HOST_OUTPUT_PEAK + small_adjustment)
        if selected is None and (gpu_budget >= residual_floor or capacity_trial):
            # Four heads here too: the 8 GiB corner above takes this path and
            # measured the same peak as two with 18% less sampling time.
            selected = (4, residual_need)
            # Staging stays on here, and the measurement that said otherwise
            # was reading an allocator rather than a requirement. On an H200
            # with the capacity simulated, complete eight-step requests at
            # three budgets peaked identically with staging on and off --
            # 6.267 -> 6.098 both ways, 35.03 against 19.12 s/NFE -- so
            # staging looked like pure cost. It is not. Both configurations
            # were then run at that budget on the RTX PRO 6000 the budget came
            # from, and with the outputs in host memory and no staging the
            # first sampling step died trying to allocate 112 MiB with 18.86
            # MiB free, at a 7.665 GiB whole-device peak. With staging the
            # same request completed at 6.712 GiB and 36.86 s/NFE. Staging is
            # worth 0.95 GiB of peak in this band; expandable_segments only
            # made the H200's peak track its budget because nothing there
            # enforced one.
            #
            # This mattered in production, not in theory: every Linux card
            # with 6.6 to 7.2 GiB of visible free memory -- the 8 GiB consumer
            # band -- was being handed exactly the configuration that died.
            # Windows always staged, so it was never exposed.
            # benchmarks/pro6000-verification/results-pro6000.md.
            residual_offload = True
        if selected is None:
            # Use the same recoverable admission error as the coarse floor.
            # A plain ValueError bypasses the controller's one idle-cache
            # release/remeasurement and loses the actual request shortfall.
            raise ResourceBudgetError(hardware, gpu_total, ram_total, free_gpu, free_ram,
                                      reserve_gpu, reserve_ram, gpu_minimum_bytes=residual_floor,
                                      gpu_requirement='estimated_working_set', canvas=canvas)
        head, reserve = selected
        # The host attention-output buffer was tied to this band with no
        # arithmetic behind it. It saves 0.04 to 0.23 GiB of peak and costs 19%
        # to 36% of sampling time -- 400 host round trips per request, 50
        # blocks by 8 steps -- measured at four corners of the band. Keep the
        # outputs on the GPU when the budget covers their peak, and let
        # residency pay for them: residency buys under 1% here, so one block is
        # a good price for a fifth to a third of the sampling time. When the
        # budget cannot cover them the host buffer stays, which is what the
        # 9 GiB/1344x768x345 corner needs.
        cpu_outputs = not no_weights and gpu_budget < reserve + GPU_ATTENTION_OUTPUT_BYTES
        if not cpu_outputs:
            reserve += GPU_ATTENTION_OUTPUT_BYTES
        # Outputs in host memory means the budget could not cover them on the
        # card, which is exactly the regime the PRO 6000 run measured: there,
        # not staging cost 0.95 GiB of peak and died in the first sampling
        # step. So every branch in this band that moves the outputs to the
        # host stages as well, not just the one that computes residual_need.
        # Without this the host-output branch alone still handed the dead
        # configuration to every Linux card with 6.7 to 7.2 GiB free.
        residual_offload = residual_offload or cpu_outputs
        if desktop and not residual_offload:
            # The old linear geometry discount may undercut the measured
            # token table at intermediate canvases. Residency must fund that
            # difference instead of spending the smaller OS reserve.
            reserve = max(reserve, activation_bytes(head, effective_tokens))
        # Residency comes last because it buys under 1% here: measured H2D
        # weight traffic is 6.78 s of 878 s sampling, and block 45 on mapped
        # weights ran no slower than block 5 on pinned weights. It still
        # relieves host RAM, so the leftover goes to it rather than staying idle.
        resident = max(0, min(RESIDENT_VRAM_CEILING, int((gpu_budget - reserve) / block_bytes)))
    if not small:
        # One measured requirement for the group in use, so the block count is
        # continuous across the band edges rather than restarting at each.
        resident = max(0, min(RESIDENT_VRAM_CEILING,
                              int((gpu_budget - activation_bytes(head, effective_tokens)) / block_bytes)))
    # Placement alone is numerically neutral. Scale the activation allowance
    # with the actual token count before spending memory on resident weights.
    # The small staging path is anchored to the complete 243-frame Ada run;
    # the larger path to the complete 345-frame Linux capacity run. This is
    # a starting estimate, not a peak extrapolated from incomplete steps.
    geometry_adjustment = 0
    if canvas is not None:
        reference_tokens = (72 if small else 102) * 1008
        variable_gib = 4.6 if small else (6. if head == 8 else 7.5)
        geometry_adjustment = int(variable_gib * GiB * (effective_tokens / reference_tokens - 1.))
        # No geometry credit on top of the measured requirement: that is
        # already a function of the token count, so crediting a shorter
        # request again counted the same discount twice. At 72576 tokens
        # against the old 102816-token reference it added five blocks to a
        # 15 GiB card, and the plan then peaked 16.65 GiB against a 14.50
        # budget. The small path keeps its own correction, which applies to
        # a reference figure rather than to a measurement.
    windows_workspace = None
    windows_activation_limited = False
    if desktop and not ampere and not small:
        # The twelve-band formula predates GPU attention outputs and the full
        # FF stash. In addition, crediting a shorter geometry against its old
        # 345-frame anchor added five blocks to a request that already OOMed.
        # Budget the current execution path before host placement is planned.
        windows_workspace = windows_gpu_output_workspace(effective_tokens, prefetch=prefetch)
        windows_workspace += max(0, activation_bytes(head, effective_tokens) - activation_bytes(8, effective_tokens))
        if (prefetch and gpu_budget < 14 * GiB
                and hardware.architecture == 'blackwell-rtx'):
            # Saving a transfer slot must not turn fully retained host weights
            # into per-step disk reads. Compare both placements, including the
            # full FF workspace, before spending that slot.
            with_slot = min(resident, max(0, int((gpu_budget - windows_workspace) / block_bytes)))
            without_slot = min(resident, max(0, int((gpu_budget - windows_workspace + BLOCK_BYTES) / block_bytes)))
            host_need = (50 - with_slot) * block_bytes + int(2.5 * GiB)
            if host_need > ram_budget >= (50 - without_slot) * block_bytes + int(2.5 * GiB):
                prefetch = False
                windows_workspace -= BLOCK_BYTES
        # The second transfer slot is worth about 2.5%; host attention outputs
        # and a host residual cost 15% to 2x. Give up the slot first.
        if prefetch and windows_workspace > gpu_budget >= windows_workspace - BLOCK_BYTES:
            prefetch = False
            windows_workspace -= BLOCK_BYTES
        resident = min(resident, max(0, int((gpu_budget - windows_workspace) / block_bytes)))
        # Do not enable FF recomputation merely to retain all host mappings.
        # Native 5060 Ti measurements show that doing so makes the same layer
        # 18% slower. Reopening an unpinned layer can hit the OS file cache and
        # overlap compute; it is not proof of physical disk traffic. Keep the
        # full FF workspace and bound host storage below. Recompute remains a
        # capacity/recovery option and an explicitly measurable tuning option.
        if windows_workspace > gpu_budget:
            # Zero resident weights is not a solution when activations alone
            # exceed the budget. Report 177 still admitted head 8 at 107856
            # tokens: 15.42 GiB workspace against 12.99 GiB, then OOMed with
            # 59 GiB of physical RAM and 303 GiB of commit still available.
            # Use the existing bounded path before the first sampling step.
            # In particular, do not credit FF recomputation here: Ada rowwise
            # FF already tiles its activations, so that flag saves nothing.
            if gpu_budget < residual_floor and not capacity_trial:
                raise ResourceBudgetError(hardware, gpu_total, ram_total, free_gpu, free_ram,
                    reserve_gpu, reserve_ram, gpu_minimum_bytes=residual_floor,
                    gpu_requirement='estimated_working_set', canvas=canvas)
            head = 4
            cpu_outputs = residual_offload = True
            prefetch = False
            # Do not immediately spend the saved activation space on weights.
            # A complete local request can supply measured placement later.
            resident = 0
            windows_workspace = residual_need
            windows_activation_limited = True
    if capacity_trial:
        head, resident, prefetch = 4, 0, False
        cpu_outputs = residual_offload = True
    pin = max(0, min(22_000_000_000, retention_budget - HOST_WEIGHT_HEADROOM)) / 1e9
    pin = min(pin, (50 - resident) * block_bytes / 1e9)
    if small:
        # Only locking every offloaded layer removes the offloader's own
        # staging buffers, so a partial pin pays full memory for both: a
        # 16.2 GB plan locked 14.51 GiB, left nothing for those buffers and
        # failed the request. All or nothing here.
        #
        # Locking all of them is worth about 3%. The figure this used to cite
        # -- 0.57%, from 4.97 s of host staging in 878 s of sampling -- was
        # measured when a step took 110 s. A step is now 12.6 s, and the same
        # comparison at that speed, changing only the lock:
        #
        #    8 GiB card   12.89 -> 12.53 s/step   peak 7.69 -> 7.72
        #    9 GiB card   13.03 -> 12.62          peak 8.34 -> 8.34
        #   10 GiB card   13.00 -> 12.61          peak 9.33 -> 9.33
        #
        # So 2.8 to 3.1% for at most 0.03 GiB of VRAM. Prefetching on top of
        # it took another 2.5% at 10 GiB but overran an 8 GiB card by 0.04,
        # so that stays on its own budget threshold.
        #
        # The streaming branch below recomputes the pin against its own host
        # headroom, so the all-or-nothing test is applied after it rather
        # than here.
        pass
    # Reopening a layer releases its mapping after the transfer slot is filled.
    # Use this only when keeping all offloaded mappings would exceed live RAM.
    # Larger machines keep the faster persistent mappings and optional pins.
    residual_host = residual_host_headroom(canvas) if residual_offload else 0
    host_activation = (2. if small or cpu_outputs else .5) * GiB + residual_host
    # Residency is not a way to spend spare VRAM: 8 blocks measured faster
    # than 29 or 48 at the same cap. What it is for is keeping the rest of the
    # model in RAM, because rereading weights from disk every step dominates a
    # request. So hold the smallest number RAM asks for, and no more -- which
    # makes the two capacities covariates rather than independent axes:
    #
    #   RAM 32 GiB   the host takes all 50   ->  hold 8
    #   RAM 24 GiB   the host takes all 50   ->  hold 8
    #   RAM 20 GiB   the host takes 40       ->  hold 10
    #   RAM 16 GiB   the host takes 31       ->  hold 19
    #
    # A card too small for what RAM asks still streams from disk; the retained
    # subset below is what bounds that case.
    host_room = retention_budget - host_activation - 2 * GiB
    ram_wants = max(0, 50 - max(0, int(host_room // block_bytes)))
    if not small:
        resident = min(resident, max(RESIDENT_TARGET, ram_wants))
    # Windows reads non-streamed offloaded blocks into private memory (pread;
    # a section view would be charged to commit in full). The RAM guard counts
    # it, it cannot be reclaimed, and pins planned after that load-all pass find
    # no room: after a 20 s request, 27.98 GiB RTX 5060 Ti, the next 768p plan
    # crossed this threshold and sampled with 21.2 GiB private, nothing pinned
    # and 2.5 GiB available. Streaming pins a bounded subset block by block and
    # keeps read-only views of the rest, which avoid rereads while RAM allows.
    streamed_weights = ((hardware.system == 'Windows' and resident < 50)
                        or (50 - resident) * block_bytes + host_activation + 2 * GiB > retention_budget)
    if streamed_weights:
        # When the remaining weights exceed RAM, a bounded retained subset
        # prevents rereading those bytes every step. This addresses disk I/O,
        # unlike pinning already-cached mappings (under 1% in the Ada trace).
        # Weight rereads dominate a disk-backed request. Reserve the working
        # memory of this execution path, not the unrelated Windows CPU-output
        # path's 8 GiB. Runtime still clamps against live physical/cgroup RAM.
        # Name the decision, not the band. `small` used to imply the host
        # attention buffer, so passing it here described the execution path;
        # now the buffer follows the budget, and a small-band request that
        # keeps its outputs on the GPU was still reserving the host-output
        # allowance -- 5.00 GiB instead of 3.50 at this geometry, so 1.5 GiB
        # of pinning it could have used, about four blocks it re-read instead.
        host_headroom = (weight_cache_headroom(system=hardware.system, streamed=True,
                                               cpu_outputs=cpu_outputs, canvas=canvas)
                         if ram_budget >= 8 * GiB else HOST_WEIGHT_HEADROOM)
        host_headroom += residual_host
        pin = max(0, min((50 - resident) * block_bytes, retention_budget - host_headroom)) / 1e9
    if small and not streamed_weights and pin * 1e9 < (50 - resident) * block_bytes:
        # All or nothing only when the weights already sit in RAM. On a
        # disk-backed request the partial pin is not a transfer optimisation
        # at all -- it is the bounded retained subset that stops those bytes
        # being reread every step, which is the larger cost there.
        pin = 0.
    engine = dict(attention='/'.join(legs), prefetch=prefetch,
                  adaln_cache=True, adaln_disk_cache=True, inference_kernels=True,
                  # One chunk size for every band, on neutrality rather than
                  # speed. The small band's 512 measured 13.3% slower at a
                  # 10 GiB cap -- 32.95 against 28.58 s/step -- but only while
                  # the attention outputs still went to host memory. With them
                  # kept on the GPU the host round trips are gone, the FF loop
                  # is off the critical path, and the same caps measure within
                  # 1.5%: 36.55, 20.35 and 20.51 s/step at 8, 9 and 10 GiB.
                  # So this claims no speed. It is kept because the peak is
                  # identical at all twelve verified caps, because a slice is
                  # chunk x width x 2 bytes and so costs a fixed 47 MiB at any
                  # geometry, and because it drops a conditional tied to a band
                  # whose other couplings proved mispriced. The head-dependent
                  # 1024 it replaces was unreachable: only the small band had a
                  # head group under 8, and it took the 512.
                  ff_chunk=2048, head_chunk=head,
                  head_parallelism=1,  # Increased only by a locally validated tuning profile.
                  projection_chunk=1024, offload_refiner=True,
                  attention_cpu_outputs=cpu_outputs, grouped_attention_outputs=cpu_outputs,
                  residual_offload=residual_offload,
                  # Do not recompute unconditionally. Earlier runs saved no
                  # peak VRAM: reserved
                  # matched the recomputing run to the byte across three probe
                  # steps (6.63, 7.37, 8.11 GiB) because those activations are
                  # freed within each block, while it cost 5% of sampling time.
                  fp8_ff_recompute=(capacity_trial and hardware.capability[0] >= 10), cache_refined_text=True,
                  resident_blocks=resident, pin_host_gb=round(pin, 3),
                  stream_weights=streamed_weights,
                  # Larger looped batches are optimization candidates; full
                  # request comparison validates numerical equivalence.
                  # Window batching is close to free at the bottom too. On an
                  # 8 GiB card, window 1 measured 13.75 s/step at a 7.68 GiB
                  # peak and window 4 measured 12.88 at 7.69, so 6.3% for
                  # 0.01 GiB. The allowance derivation above assumed window 1
                  # and added nothing for batched windows; 0.01 GiB is inside
                  # the margin it keeps. Ampere alone keeps the single window,
                  # as it does in the twelve band: no measurement here covers
                  # Ampere with the larger batch. Platform is not a gate; the
                  # twelve band already batches four on Windows.
                  # Four windows everywhere except Ampere, which has no
                  # measurement with the larger batch. Above the twelve band
                  # it measured 9.23 -> 9.10 s/step at a 16 GiB cap and
                  # 9.24 -> 9.11 at 20 GiB, with the whole-device peak
                  # unchanged at 15.83 and 18.09 GiB. Dropping to two at a
                  # 12 GiB cap cost 0.2%, so one rule covers every band.
                  window_batch=1 if ampere else 4,
                  linear_compute='bf16-weight-only' if ampere else 'native-fp8')
    notes = ['Request resolution, frame count, steps and attention backend are preserved.',
             'Budgets use current free VRAM and available physical/commit RAM, with growth headroom; OS usage is already excluded.',
             'Automatic execution uses matching GPU kernel probes; read-only plans may use package discovery. Small probes do not prove full-video capacity.',
             'Budgets guide placement; cgroup/MPS enforcement belongs to capacity benchmarks.']
    if canvas is not None:
        notes.append('Geometry: %dx%d, %d frames, %d video tokens; resident-weight allowance adjusted by %.3f GiB for activation storage.' %
                     (canvas['width'], canvas['height'], canvas['frames'], canvas['video_tokens'], geometry_adjustment / GiB))
    if desktop and small and head == 8:
        notes.append('Use eight-head groups for this small Windows canvas (%d generated and reference rows). '
                     'Its larger workspace allowance and GPU attention outputs fit inside the current budget; '
                     'weight residency pays for the wider group. Each pass is planned at its own total token count. '
                     'Measured speed benefit is from PRO 6000 Linux; native Windows throughput remains unmeasured.'
                     % effective_tokens)
    if capacity_trial:
        notes.append('The %.2f GiB live budget is below the %.2f GiB working-set estimate. '
                     'Attempt the bounded host-output/residual path with no resident blocks; '
                     'per-tensor FP8 recomputes FF tiles instead of retaining the full activation stash. '
                     'Keep resolution, duration, steps, backend and reserves unchanged. '
                     'This is a capacity attempt, not measured proof that this GPU fits.' %
                     (gpu_budget / GiB, residual_floor / GiB))
    if windows_activation_limited:
        notes.append('The Windows GPU-output activation estimate exceeds the live budget even with zero resident blocks. Start this token count with four-head groups, host attention outputs and one host residual buffer; estimated workspace %.2f GiB. Resolution, duration, steps, weights and attention backend are preserved. This is request-specific, not a persistent compatibility downgrade.' % (windows_workspace / GiB))
    elif windows_workspace is not None:
        notes.append('Windows head-8 GPU-output workspace estimate: %.2f GiB for the selected FF storage path, caches and transfer buffers; remaining live budget permits %d resident blocks. Based on complete Windows measurements, not a capacity guarantee. Head/window/FF tile shapes and system reserves are unchanged.' %
                     (windows_workspace / GiB, resident))
    if residual_offload:
        notes.append('The original small path exceeds the live VRAM budget: stage residuals in one reusable CPU buffer, preserving kernel shapes, dtype and every requested row. Host weight caching leaves an additional %.2f GiB for this buffer. Extra transfers cost time; this is a capacity route, not a claimed speedup.' % (residual_host / GiB))
    if streamed_weights:
        if hardware.system == 'Windows':
            notes.append('Windows streams offloaded layers: keep at most %.3f GB pinned and read-only views of the rest while RAM allows, reopening a layer only after its view is shed; no arithmetic or attention change.' % pin)
        else:
            notes.append('Live RAM cannot retain all offloaded mappings: keep at most %.3f GB pinned, reopen only remaining layers per transfer; no arithmetic or attention change.' % pin)
        notes.append('Host weight cache keeps %.2f GiB for this path\'s working memory, inside the separate OS growth reserve; the runtime rechecks available RAM.' % (host_headroom / GiB))
    if hardware.architecture == 'ampere':
        notes.append('Ampere keeps FP8 weight storage and uses bounded BF16 compute; no native FP8 tensor-core claim.')
    if desktop:
        notes.append('Windows keeps %.2f GiB beyond current GPU usage and %.2f GiB beyond current available RAM/commit; pagefile capacity is not added to RAM.' % (reserve_gpu, reserve_ram))
    if retention_budget > ram_budget:
        notes.append('Weight caching may keep %.2f GiB rather than the %.2f GiB currently reported available: a complete verified request on this machine, under this compute build and geometry, held that much. The request budget and admission still use the reported figure.'
                     % (retention_budget / GiB, ram_budget / GiB))
    if reserve_gpu < original_reserve_gpu:
        notes.append('Foreground VRAM growth reserve reduced from %.2f to %.2f GiB: %.2f GiB live free minus the original reserve could not cover the %.2f GiB smallest-profile estimate. The request can try the available capacity; this is not measured proof that it fits.' %
                     (original_reserve_gpu, reserve_gpu, free_gpu / GiB, residual_need / GiB))
    # The transformer is released before decode. Full-GPU VAE decode measured
    # below 14 GiB on SM120; keep additional margin for other devices/backends.
    if lora_workspace:
        notes.append('Online LoRA reserves %d bytes per block plus %d bytes for transfer slots and bounded workspace.'
                     % (lora_max_block_bytes, lora_workspace))
    gpu_budget = request_gpu_budget
    decoder_offload = gpu_budget < 20 * GiB
    if reference_rows:
        notes.append('Reference conditioning adds %d packed rows; activation placement scales with total rows. This is a starting estimate, not a verified reference-capacity result.' % reference_rows)
    # A full floating video plus its RGB copy grows with output geometry.
    # The temporal streaming path uses the original decoder tiles and blend;
    # completed uint8 frames go to the retained disk artifact immediately.
    output_bytes = (canvas['width'] * canvas['height'] * canvas['frames'] * 3
                    if canvas is not None else 1344 * 768 * 243 * 3)
    stream_output = decoder_offload or ram_budget < 8 * GiB + 3 * output_bytes
    decoder_stream_weights = decoder_offload and ram_budget < 14 * GiB
    # A decoder block contains 268,574,720 bytes of original FP32 weights.
    # Temporal streaming bounds the live clip workspace, so use spare VRAM for
    # these repeatedly-read weights before spending RAM on host copies. At the
    # reference canvas a streamed/offloaded clip measured 3.07 GiB whole-GPU;
    # 3.25 GiB includes the transfer slot and a small workspace allowance.
    vae_workspace = decoder_workspace(canvas)
    vae_resident = max(0, min(36, int((gpu_budget - vae_workspace) / 268574720))) if decoder_offload else 0
    # The bounded decoder loader stores Linear weights in the FP16 dtype that
    # autocast would use anyway. This reduces streamed transfer volume and
    # avoids repeated conversion in a fully resident decoder. Both paths are
    # bit-identical on Linux RTX PRO 6000 Blackwell and native Windows
    # RTX 4060 Ti / RTX 5060 Ti. The Windows Blackwell cache also avoids
    # materializing a full FP32 checkpoint on the host before decode.
    # Windows also avoids the whole-checkpoint CPU load before uploading the
    # resident prefix. Keep residency/headroom unchanged: this spends less
    # memory on the same placement, rather than filling the reclaimed space.
    decoder_linear_cache = ((hardware.architecture == 'blackwell-rtx'
                             and hardware.system == 'Linux')
                            or (hardware.architecture in ('ada', 'blackwell-rtx')
                                and hardware.system == 'Windows'))
    if decoder_offload:
        weight_format = 'FP16 Linear / FP32 other' if decoder_linear_cache else 'FP32'
        notes.append('Decoder keeps %d/36 %s blocks on GPU using live free VRAM; streams only the remainder, with original tiles and blending.' % (vae_resident, weight_format))
    if stream_output:
        notes.append('Decode streams original temporal clips into rgb.npy; full floating video and duplicate RGB storage are avoided.')
    return Policy(2, hardware.to_dict(), gpu_budget, ram_budget, int(reserve_gpu * GiB),
                  int(reserve_ram * GiB), 'backend:native,pinned_max_round_threshold_mb:1,expandable_segments:True',
                  # 504 serialized VAE weight transfers fill 124.8 s of a
                  # 140.4 s decode here, with one slot and no overlap, so a
                  # second slot is worth measuring. It stays off until a
                  # complete request verifies it: nothing ships on probe
                  # evidence alone in this band.
                  engine, dict(offload=decoder_offload, prefetch=decoder_offload and not small,
                               tile_group=decoder_offload, preload=False,
                               pin_weights=decoder_offload and ram_budget >= 16 * GiB,
                               stream_output=stream_output, stream_weights=decoder_stream_weights,
                               resident_blocks=vae_resident,
                               linear_compute_cache=decoder_linear_cache),
                  'Architecture-specific candidate; validate this exact policy on the target GPU.', notes,
                  capacity_trial=capacity_trial, lora_max_block_bytes=lora_max_block_bytes,
                  lora_root_bytes=lora_root_bytes)
