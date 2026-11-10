"""Still keyframes between the first and last frame.

A keyframe block is a clean latent frame pinned at the fixed keyframe timestep; the native layout
only knows the 'first' and 'last' rotary times. A 'mid:<frame>' anchor is laid out like 'first' and
then its rotary time is moved to where the generated frame with that pixel index sits
(text length + 5/3 * frame, the native frame-to-time rescale used for guides and for 'last').
"""

FRAME_RESCALE = 5. / 3.
_installed = False


def split(anchors):
    """(anchors the native builder understands, {block index: pixel frame})"""
    mids = {i: int(a[4:]) for i, a in enumerate(anchors) if is_mid(a)}
    return tuple('first' if i in mids else a for i, a in enumerate(anchors)), mids


def is_mid(anchor):
    return isinstance(anchor, str) and anchor.startswith('mid:')


def native_anchors(anchors):
    """The first/last anchors of a keyframe set, without its middle frames."""
    return tuple(a for a in anchors if not is_mid(a))


def validate_order(anchors, task):
    """Middle anchors belong to a first+last request only, between the two, in strictly rising frame order."""
    mids = [a for a in anchors if is_mid(a)]
    if not mids:
        return
    try:
        frames = [int(a[4:]) for a in mids]
    except ValueError:
        raise ValueError('Invalid middle keyframe anchor') from None
    if (task != 'fl2va' or tuple(anchors[:1] + anchors[-1:]) != ('first', 'last')
            or tuple(anchors[1:-1]) != tuple(mids) or any(f < 1 for f in frames)
            or any(a >= b for a, b in zip(frames, frames[1:]))):
        raise ValueError('Middle keyframes need first and last frames and rising frame numbers between them')


def install_for(conditions):
    """Patch the layout builder once, and only when the conditions carry a middle keyframe."""
    if conditions and any(is_mid(a) for a in conditions[0]):
        install()


def install():
    global _installed
    if _installed:
        return
    from diffusers.modular_pipelines.minimax_h3.before_denoise import MiniMaxH3PrepareLayoutStep as Step
    original = Step.build_packed_sequence

    def build(text_token_tags, num_latent_frames, latent_height, latent_width, num_audio_latents, patch_size,
              audio_channels, audio_tag, video_tag, keyframe_anchors=()):
        native, mids = split(keyframe_anchors)
        layout = original(text_token_tags, num_latent_frames, latent_height, latent_width, num_audio_latents,
                          patch_size, audio_channels, audio_tag, video_tag, keyframe_anchors=native)
        if mids:
            positions = layout[0]
            rows = (latent_height // patch_size[1]) * (latent_width // patch_size[2])
            origin = int(text_token_tags.shape[0])
            for index, frame in mids.items():
                positions[origin + index * rows:origin + (index + 1) * rows, 0] = origin + FRAME_RESCALE * frame
        return layout

    Step.build_packed_sequence = staticmethod(build)
    _installed = True
