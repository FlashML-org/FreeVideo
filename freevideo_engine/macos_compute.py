"""Per-stage compute partitions for the native Mac engine, sized by unified memory.

Every partition below changes only how independent heads, rows and windows are
grouped; reductions, precision, geometry, steps and seeds are unchanged.
Larger groups mean fewer Metal dispatches: on an M5 / 24 GiB the first-pass block
went from 4.84 s with four heads per group to 3.90 s with fourteen.

Activation bytes per token come from H3's widths: a 5376-channel residual, 7168
attention channels for Q/K/V and the softmax output, and the linear branch's
features for each head group. Memory left after the working set retains
streamed layers between steps, which matters once a larger GPU makes the
compute per step short.
"""
GiB = 2**30
HIDDEN, HEADS, HEAD_DIM, FF = 5376, 56, 128, 17920
LAYER_BYTES = 856_311_776          # one decoded BF16 transformer block
PREFETCH_BYTES = 512 << 20        # total bound for one layer, including LoRA sidecars
RESIDENT_LIMIT = 50
RETENTION_HEADROOM = 4 * GiB
GROUPS = (56, 28, 14, 8, 4)


def working_bytes(tokens, group, *, ff_chunk=4096, window_bytes=1 << 29, frames=72):
    """Estimated peak activations of one block at `tokens` rows and `group` heads.

    The linear branch also keeps FP32 [frames, heads, 128, 128] banks: A, B,
    transitions, injections, both scan directions, the gathered state and the
    inverse's iterates. A full 10 s first pass measured about 5.4 GiB of live
    working memory with all 56 heads; this estimate is 5.5 GiB there."""
    attention = HEADS * HEAD_DIM
    shared = tokens * (HIDDEN * 2 * 3          # residual, normed input and block output
                       + attention * 2 * 3     # raw Q/K/V
                       + attention * 2)        # softmax output before projection
    per_group = tokens * group * HEAD_DIM * (2 * 2   # normalized Q/K
                                             + 2 * 3  # MLX copies of Q/K/V (same pool)
                                             + 2 * 3  # linear features
                                             + 4 * 2)  # FP32 statistics operands
    banks = frames * group * HEAD_DIM * HEAD_DIM * 4 * 6
    ff = ff_chunk * FF * 2 * 2
    return shared + per_group + banks + ff + window_bytes


# Fastest first. The tail keeps the reference engine's small row chunks for the
# tightest allowances, where a 4096-row FF chunk alone needs 0.27 GiB.
CANDIDATES = tuple((group, 4096, 1 << 29) for group in GROUPS) + ((4, 1024, 1 << 28), (4, 256, 1 << 27))


def bounded_working_bytes(tokens, *, frames=72):
    """Conservative working estimate for the four-head/256-row owned profile.

    Unlike the original adapter, grouped projections retain two full attention
    outputs (softmax and linear), but only one group's raw Q/K/V. Full RMSNorm
    reductions remain: their full-size inputs/outputs must still be counted.
    This estimate includes MLX and host prefetch allowances through the planner;
    fitting a PyTorch allocator cap alone does not validate this unified budget.
    """
    if any(type(value) is not int or value <= 0 for value in (tokens, frames)):
        raise ValueError('Bounded planning needs positive integer row and frame counts')
    group, chunk, window = CANDIDATES[-1]
    hidden = tokens * HIDDEN * 2
    attention = tokens * HEADS * HEAD_DIM * 2
    # Residual and normalized input coexist with both full attention outputs.
    # Allow eight BF16 group planes for raw projections, features, contiguity
    # preparation and readout; softmax and linear-leg temporaries do not overlap.
    grouped = tokens * group * HEAD_DIM * 2 * 8
    banks = frames * group * HEAD_DIM * HEAD_DIM * 4 * 6
    # Frame-statistic casts and means are explicitly bounded in mps_linear.
    scratch = (16 + 32) << 20
    row_temporaries = chunk * (HIDDEN * 2 * 5 + FF * 2 * 2)
    return 2 * hidden + 2 * attention + grouped + banks + window + scratch + row_temporaries


def sequence_tokens(canvas, prompt_rows):
    """Count all packed rows with the pinned audio clock and validated references."""
    from diffusers.modular_pipelines.minimax_h3.modular_pipeline import (
        MINIMAX_H3_AUDIO_CHANNELS, audio_latent_num_frames)
    if type(prompt_rows) is not int or prompt_rows <= 0:
        raise ValueError('Compute planning needs the actual positive prompt row count')
    rows = dict(text=prompt_rows, video=canvas['video_tokens'],
                audio=MINIMAX_H3_AUDIO_CHANNELS * audio_latent_num_frames(canvas['frames']),
                reference_video=canvas.get('reference_video_tokens', 0),
                reference_audio=canvas.get('reference_audio_tokens', 0))
    if any(type(value) is not int or value < 0 for value in rows.values()):
        raise ValueError('Packed row counts must be nonnegative integers')
    return dict(rows, total=sum(rows.values()))


def plan(budget_bytes, tokens, *, frames=72, reserve_bytes=GiB, layers=50,
         allow_retention=True, allow_bounded=False):
    """Head group, row chunks and retained-layer bytes for one sampling stage."""
    if type(budget_bytes) is not int or budget_bytes <= 0 or type(tokens) is not int or tokens <= 0:
        raise ValueError('Compute planning needs a positive byte budget and token count')
    if type(allow_bounded) is not bool:
        raise ValueError('Bounded planning must be explicitly enabled or disabled')
    available = budget_bytes - reserve_bytes - LAYER_BYTES - PREFETCH_BYTES
    # Keep a tenth of the allowance unplanned for allocator fragmentation.
    group, chunk, window = next(((g, c, w) for g, c, w in CANDIDATES
                                 if working_bytes(tokens, g, ff_chunk=c, window_bytes=w, frames=frames)
                                 <= .9 * available),
                                CANDIDATES[-1])
    working = working_bytes(tokens, group, ff_chunk=chunk, window_bytes=window, frames=frames)
    # The candidate is opt-in until native complete-product acceptance. Cover
    # only the smallest existing partition; do not increase head/FF groups or
    # spend the released buffers on additional retained weights.
    bounded = allow_bounded and (group, chunk, window) == CANDIDATES[-1]
    if bounded:
        working = bounded_working_bytes(tokens, frames=frames)
    # A retained layer saves one 0.12 s load per step on an M5, against 2.7 s
    # of compute: only spend memory the system will not miss. The first 4 GiB
    # beyond the working set stay free for other applications and the OS file
    # cache; on a 24 GiB Mac, retaining 14 layers pushed it into swap.
    spare = max(0, available - working - RETENTION_HEADROOM)
    retained = min(layers, RESIDENT_LIMIT, spare // LAYER_BYTES) if allow_retention and not bounded else 0
    result = dict(head_chunk=group, attention_chunk=chunk, ff_chunk=chunk, window_batch=64,
                attention_batch_bytes=window,
                resident_bytes=int(retained * LAYER_BYTES), retained_layers=int(retained),
                estimated_working_bytes=int(working), budget_bytes=int(budget_bytes),
                fits=working <= available,
                scope='Grouping of independent heads, rows and windows; reductions unchanged.')
    if bounded:
        result.update(bounded_buffers=True,
            scope='Grouped projections and owned intermediate buffers; unified working estimate is not a physical capacity result.')
    return result


def shared_sampling_plan(budget_bytes, sampling, prompt_rows, *, phase_reference_tokens=None):
    """Candidate cross-pass lifetime from an already enforced allocator cap.

    The caller validates conditioning and supplies its per-pass reference rows.
    Actual per-pass admission and
    pressure checks still run before sampling; this predicts whether retaining
    weights across the boundary can help, not physical workload capacity.
    """
    from .geometry import geometry
    if type(budget_bytes) is not int or budget_bytes <= 0:
        raise ValueError('Shared sampling needs an enforced positive byte budget')
    if type(prompt_rows) is not int or prompt_rows <= 0:
        raise ValueError('Shared sampling needs the actual positive prompt row count')
    if phase_reference_tokens is not None:
        if (not isinstance(phase_reference_tokens, dict)
                or set(phase_reference_tokens) != {'first-pass', 'refinement'}
                or any(not isinstance(row, dict)
                    or set(row) - {'reference_video_tokens', 'reference_audio_tokens'}
                    or any(type(n) is not int or n < 0 for n in row.values())
                    for row in phase_reference_tokens.values())):
            raise ValueError('Shared sampling needs both validated per-pass reference token counts')
    if not sampling['enabled']:
        return dict(eligible=False, reason='single-pass', budget_bytes=budget_bytes)
    phases = {}
    for phase, key in (('first-pass', 'first'), ('refinement', 'second')):
        canvas = geometry(**sampling[key])
        if phase_reference_tokens is not None:
            canvas.update(phase_reference_tokens[phase])
        rows = sequence_tokens(canvas, prompt_rows)
        phases[phase] = plan(budget_bytes, rows['total'], frames=canvas['latent_frames'])
        phases[phase]['packed_rows'] = rows
    eligible = all(p['fits'] and p['retained_layers'] > 0 for p in phases.values())
    return dict(eligible=eligible, reason='retained-weights-in-both-passes' if eligible else
                'no-cross-pass-retention-allowance', budget_bytes=budget_bytes, phases=phases,
                refinement_resident_bytes=phases['refinement']['resident_bytes'],
                scope='Enforced live cap and actual text rows; per-pass planning and pressure reclamation remain active.')
