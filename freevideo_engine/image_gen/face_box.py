"""The faces in a picture: box, five landmarks and score of each, found on the CPU by the YuNet face detector.

Used by the page before a character is made from a photo: where the face is small, the page cuts it out and enlarges
it for the face close-up. Nothing is queued, no graphics card is used and the picture is left where it is.

The network is kornia's YuNet (kornia 0.9.1, kornia/models/yunet/model.py and processors.py, and the prior boxes,
decoding and non-maximum suppression of kornia/contrib/face_detection.py), Apache-2.0, (c) 2018 Kornia Team, itself
based on ShiqiYu/libfacedetection.train@74f3aa77c63234dd954d21286e9a60703b8d0868 tasks/task1/yufacedetectnet.py.
Changes: inference only (the "test" phase, no training initialisation and no download), the prior boxes built with
tensor operations instead of loops, and a plain non-maximum suppression, so nothing beyond torch is needed.

The weights are kornia's yunet_final.pth (huggingface.co/kornia/yunet, MIT, trained by libfacedetection.train),
405,729 bytes, carried unchanged as base64 in face_box_weights.json because the engine's source payload holds no other
binary file; they are checked against the SHA-256 below and read with weights_only=True. See
freevideo_engine/licenses/YuNet-Kornia-Apache-2.0.txt and freevideo_engine/licenses/YuNet-libfacedetection-MIT.txt.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import threading

WEIGHTS = Path(__file__).with_name('face_box_weights.json')
WEIGHTS_BYTES = 405729
WEIGHTS_SHA256 = 'ee0a3035c5cbfc9484a31332223c9fd9c0ddcdb7c46b6a19e558ed393b6f2c67'
# The picture is searched at most this long on its long side (smaller ones as they are): about 0.1 s on four cores.
SEARCH_SIDE = 800
# The network needs at least this many pixels on each side.
MIN_SIDE = 64
MAX_PIXELS = 4096 * 4096
CONFIDENCE = 0.6
NMS_IOU = 0.3
MAX_FACES = 16
MIN_SIZES = ((10, 16, 24), (32, 48), (64, 96), (128, 192, 256))
STEPS = (8, 16, 32, 64)
VARIANCE = (0.1, 0.2)

_lock = threading.Lock()
_model = None


class WeightsError(RuntimeError):
    """The bundled weights are missing or are not the expected file."""


def weights_bytes(path=WEIGHTS):
    """The yunet_final.pth bytes, only when they are exactly the pinned file."""
    try:
        record = json.loads(Path(path).read_text(encoding='ascii'))
        data = base64.b64decode(''.join(record['base64']), validate=True)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        raise WeightsError('face detector weights unreadable') from error
    if len(data) != WEIGHTS_BYTES or hashlib.sha256(data).hexdigest() != WEIGHTS_SHA256:
        raise WeightsError('face detector weights do not match')
    return data


def build():
    import torch
    from torch import nn
    import torch.nn.functional as F

    class ConvDPUnit(nn.Sequential):
        def __init__(self, in_channels, out_channels, with_bn_relu=True):
            super().__init__()
            self.add_module('conv1', nn.Conv2d(in_channels, out_channels, 1, 1, 0, bias=True, groups=1))
            self.add_module('conv2', nn.Conv2d(out_channels, out_channels, 3, 1, 1, bias=True, groups=out_channels))
            if with_bn_relu:
                self.add_module('bn', nn.BatchNorm2d(out_channels))
                self.add_module('relu', nn.ReLU(inplace=True))

    class ConvHead(nn.Sequential):
        def __init__(self, in_channels, mid_channels, out_channels):
            super().__init__()
            self.add_module('conv1', nn.Conv2d(in_channels, mid_channels, 3, 2, 1, bias=True, groups=1))
            self.add_module('bn1', nn.BatchNorm2d(mid_channels))
            self.add_module('relu', nn.ReLU(inplace=True))
            self.add_module('conv2', ConvDPUnit(mid_channels, out_channels))

    class Conv4LayerBlock(nn.Sequential):
        def __init__(self, in_channels, out_channels, with_bn_relu=True):
            super().__init__()
            self.add_module('conv1', ConvDPUnit(in_channels, in_channels, True))
            self.add_module('conv2', ConvDPUnit(in_channels, out_channels, with_bn_relu))

    class YuNet(nn.Module):
        """BGR input in [0, 255], no mean or scale; per prior: 14 offsets (box and five landmarks), 2 classes, 1 IoU."""

        def __init__(self):
            super().__init__()
            self.model0 = ConvHead(3, 16, 16)
            self.model1 = Conv4LayerBlock(16, 64)
            self.model2 = Conv4LayerBlock(64, 64)
            self.model3 = Conv4LayerBlock(64, 64)
            self.model4 = Conv4LayerBlock(64, 64)
            self.model5 = Conv4LayerBlock(64, 64)
            self.model6 = Conv4LayerBlock(64, 64)
            self.head = nn.Sequential(
                Conv4LayerBlock(64, 3 * (14 + 2 + 1), False),
                Conv4LayerBlock(64, 2 * (14 + 2 + 1), False),
                Conv4LayerBlock(64, 2 * (14 + 2 + 1), False),
                Conv4LayerBlock(64, 3 * (14 + 2 + 1), False),
            )

        def forward(self, x):
            sources = []
            x = self.model0(x)
            x = F.max_pool2d(x, 2)
            x = self.model1(x)
            x = self.model2(x)
            x = F.max_pool2d(x, 2)
            x = self.model3(x)
            sources.append(x)
            for block in (self.model4, self.model5, self.model6):
                x = block(F.max_pool2d(x, 2))
                sources.append(x)
            heads = [head(source).permute(0, 2, 3, 1).contiguous() for head, source in zip(self.head, sources)]
            data = torch.cat([o.flatten(1) for o in heads], 1)
            data = data.view(data.shape[0], data.shape[1] // 17, 17)
            loc, conf, iou = data.split((14, 2, 1), dim=-1)
            return loc, conf.softmax(dim=-1), iou

    return YuNet()


def load(path=WEIGHTS):
    """The network in evaluation mode on the CPU; raises WeightsError for weights that are not the pinned file."""
    import torch
    data = weights_bytes(path)
    model = build()
    model.load_state_dict(torch.load(io.BytesIO(data), map_location='cpu', weights_only=True), strict=True)
    return model.eval()


def priors(height, width):
    """(N, 4) prior boxes, centre and size relative to the picture, in the order of the network's outputs."""
    import torch
    first = (height + 1) // 2 // 2, (width + 1) // 2 // 2
    maps, size = [], first
    for _ in range(4):
        size = size[0] // 2, size[1] // 2
        maps.append(size)
    rows = []
    for (rows_n, cols_n), step, sizes in zip(maps, STEPS, MIN_SIZES):
        i, j = torch.meshgrid(torch.arange(rows_n, dtype=torch.float32), torch.arange(cols_n, dtype=torch.float32),
                              indexing='ij')
        cx = ((j + .5) * step / width)[..., None].expand(rows_n, cols_n, len(sizes))
        cy = ((i + .5) * step / height)[..., None].expand(rows_n, cols_n, len(sizes))
        k = torch.tensor(sizes, dtype=torch.float32)
        sx, sy = (k / width).expand(rows_n, cols_n, -1), (k / height).expand(rows_n, cols_n, -1)
        rows.append(torch.stack((cx, cy, sx, sy), -1).reshape(-1, 4))
    return torch.cat(rows)


def decode(loc, prior):
    import torch
    centre, size = prior[:, :2], prior[:, 2:]
    box_centre = centre + loc[:, 0:2] * VARIANCE[0] * size
    box_size = size * torch.exp(loc[:, 2:4] * VARIANCE[1])
    points = [centre + loc[:, n:n + 2] * VARIANCE[0] * size for n in range(4, 14, 2)]
    corner = box_centre - box_size / 2
    return torch.cat([corner, corner + box_size, *points], 1)


def suppress(boxes, scores, threshold=NMS_IOU):
    """Indices kept by non-maximum suppression, best score first; equal scores (common near 1) in the network's order."""
    import torch
    order = scores.sort(descending=True, stable=True)[1]
    keep = []
    area = (boxes[:, 2] - boxes[:, 0]).clamp(min=0) * (boxes[:, 3] - boxes[:, 1]).clamp(min=0)
    while order.numel():
        best = int(order[0])
        keep.append(best)
        rest = order[1:]
        top_left = torch.maximum(boxes[best, :2], boxes[rest, :2])
        bottom_right = torch.minimum(boxes[best, 2:4], boxes[rest, 2:4])
        inter = (bottom_right - top_left).clamp(min=0).prod(1)
        iou = inter / (area[best] + area[rest] - inter).clamp(min=1e-9)
        order = rest[iou <= threshold]
    return keep


def find(model, image):
    """The faces in an RGB PIL picture, in its pixels: [{'box', 'score', 'landmarks'}], largest box first."""
    import numpy as np
    import torch
    from PIL import Image
    width, height = image.size
    k = min(1.0, SEARCH_SIDE / max(width, height))
    small = image.resize((max(1, round(width * k)), max(1, round(height * k))), Image.Resampling.LANCZOS) if k < 1 else image
    if min(small.size) < MIN_SIDE:
        return []
    sx, sy = width / small.width, height / small.height
    # RGB to BGR, the order the weights were trained on.
    x = torch.from_numpy(np.asarray(small, dtype=np.float32)[:, :, ::-1].copy()).permute(2, 0, 1)[None]
    with torch.inference_mode():
        loc, conf, iou = model(x)
        boxes = decode(loc[0], priors(small.height, small.width))
        scores = (conf[0, :, 1] * iou[0, :, 0].clamp(0, 1)).sqrt()
        chosen = scores > CONFIDENCE
        boxes, scores = boxes[chosen], scores[chosen]
        scale = torch.tensor([small.width, small.height] * 7, dtype=boxes.dtype)
        boxes = boxes * scale * torch.tensor([sx, sy] * 7, dtype=boxes.dtype)
        keep = suppress(boxes, scores)
    faces = []
    for index in keep:
        row = [float(v) for v in boxes[index]]
        faces.append(dict(box=[round(v, 1) for v in row[:4]], score=round(float(scores[index]), 3),
                          landmarks=[[round(row[n], 1), round(row[n + 1], 1)] for n in range(4, 14, 2)]))
    faces.sort(key=lambda face: -(face['box'][2] - face['box'][0]) * (face['box'][3] - face['box'][1]))
    return faces[:MAX_FACES]


def detect(path):
    """{'width', 'height', 'faces'} of the picture at `path` as it is shown (its orientation tag applied).

    Boxes are [left, top, right, bottom] and may reach past the picture's edge; landmarks are the eyes, the nose and
    the mouth corners, left to right as seen. Raises ValueError('invalid_media') for a picture that cannot be read,
    WeightsError when the bundled weights are not the expected file.
    """
    global _model
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError('invalid_media')
            image = ImageOps.exif_transpose(opened).convert('RGB')
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError('invalid_media') from error
    with _lock:
        if _model is None:
            _model = load()
        faces = find(_model, image)
    return dict(width=image.width, height=image.height, faces=faces)
