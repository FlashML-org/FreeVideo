"""Guide layout: a guide clip shares the generated clip's time origin.

The native H3 AddGuide node packs [text | guide rows | references | target] with the guide rows
at time origin cursor + 5/3 * frame_index, the same origin as the target rows. The Ref2VA layout
builder gives every video reference its own time slot instead. This wrapper builds the ordinary
Ref2VA layout with the guides as the LAST references, then rewinds the target rows' time
coordinate by the slot the first guide occupied so that guides and target share one origin.
Guides after the first are moved onto the same origin, and a guide with a frame index is offset
by 5/3 per frame (the native frame-to-time rescale).

Requests without a guide return the builder's layout object unchanged.
"""

FRAME_RESCALE = 5. / 3.


def build(builder, text_token_tags, references, condition_latents, audio_conditions, *args):
    layout = builder(text_token_tags, references, condition_latents, audio_conditions, *args)
    guides = [i for i, ref in enumerate(references) if getattr(ref, 'guide', False)]
    if not guides:
        return layout
    count = len(guides)
    if guides != list(range(len(references) - count, len(references))):
        raise ValueError('Guide clips must come after all other references')
    positions, tags, video_ids, audio_ids, text_ids, rows, audio_rows = layout
    patch_h, patch_w = args[-4][1], args[-4][2]
    # Guides are silent, so their latents are the last `count` entries of condition_latents.
    sizes = []
    for latent in condition_latents[-count:]:
        sizes.append(latent.shape[2] * (latent.shape[3] // patch_h) * (latent.shape[4] // patch_w))
    end = rows
    spans = []
    for n in reversed(sizes):
        spans.append(video_ids[:end][-n:])
        end -= n
    spans.reverse()
    audio_start = int(audio_ids[audio_rows:].min())
    first = float(positions[int(spans[0][0]), 0])
    shift = float(positions[int(video_ids[rows]), 0]) - first
    positions[audio_start:, 0] -= shift
    for span, reference in zip(spans, references[-count:]):
        offset = first - float(positions[int(span[0]), 0])
        index = getattr(reference, 'guide_index', 0)
        if offset or index:
            positions[span, 0] += offset + FRAME_RESCALE * index
    return positions, tags, video_ids, audio_ids, text_ids, rows, audio_rows
