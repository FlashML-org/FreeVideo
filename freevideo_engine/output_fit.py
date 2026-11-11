"""Scale decoded frames to the requested size when the model rendered a larger one."""


def fit_frames(frames, width, height, chunk=16, dtype='uint8'):
    """Return uint8 (or uint16, for the 10-bit file) frames of exactly height x width; audio is not involved.

    The rendered frames are centre-cropped to the requested shape, then reduced
    with an area filter, so every output pixel averages the rendered pixels it
    covers.
    """
    import numpy as np
    import torch
    import torch.nn.functional as functional
    count, source_height, source_width = frames.shape[0], frames.shape[1], frames.shape[2]
    if (source_width, source_height) == (width, height):
        return frames
    if source_width < width or source_height < height:
        raise ValueError('Rendered frames are smaller than the requested size')
    crop_width, crop_height = source_width, source_height
    if source_width * height > source_height * width:
        crop_width = min(source_width, max(width, round(source_height * width / height)))
    else:
        crop_height = min(source_height, max(height, round(source_width * height / width)))
    left, top = (source_width - crop_width) // 2, (source_height - crop_height) // 2
    wide = dtype == 'uint16'
    maximum = 65535 if wide else 255
    result = np.empty((count, height, width, 3), dtype=np.uint16) if wide else torch.empty((count, height, width, 3), dtype=torch.uint8)
    for start in range(0, count, chunk):
        block = frames[start:start + chunk, top:top + crop_height, left:left + crop_width]
        if wide and not torch.is_tensor(block):
            block = torch.from_numpy(np.ascontiguousarray(block).astype(np.float32))
        else:
            block = torch.from_numpy(np.ascontiguousarray(block)) if not torch.is_tensor(block) else block
        scaled = functional.interpolate(block.permute(0, 3, 1, 2).float(), size=(height, width), mode='area')
        scaled = scaled.round_().clamp_(0, maximum).permute(0, 2, 3, 1)
        if wide:
            result[start:start + block.shape[0]] = scaled.to(torch.int32).numpy().astype(np.uint16)
        else:
            result[start:start + block.shape[0]] = scaled.to(torch.uint8)
    return result
