"""Optional local body-orientation check for cast backs; never downloads weights.

The installation includes torchvision, but not pose weights. Without a local
checkpoint this check is skipped. Face visibility is deliberately not a proxy
for torso direction: a backwards head on a frontal body is still rejected.
"""
from dataclasses import dataclass
import math
from pathlib import Path
import secrets
import time

MAX_RETRIES = 2
_POSE_FILE = 'keypointrcnn_resnet50_fpn_coco-fc266e95.pth'


@dataclass(frozen=True)
class Assessment:
    body: str
    back_score: float

    def __post_init__(self):
        if (self.body not in ('front', 'back', 'side', 'unknown')
                or not math.isfinite(self.back_score) or not 0 <= self.back_score <= 1):
            raise ValueError('invalid body assessment')


def assess_keypoints(points, scores, box):
    """COCO anatomical left/right shoulders and hips, independent of the head.

    Low-confidence, occluded, overlapping or conflicting joints abstain. This
    cannot judge garment fronts or guarantee that a hallucinated pose is sound.
    """
    width = box[2] - box[0]
    joints = (5, 6, 11, 12)  # left/right shoulder, left/right hip
    if (not math.isfinite(width) or width <= 0 or len(points) < 17 or len(scores) < 17
            or any(points[i][2] <= 0 or scores[i] < 2. for i in joints)):
        return Assessment('unknown', .5)
    shoulders = (points[6][0] - points[5][0]) / width
    hips = (points[12][0] - points[11][0]) / width
    if not all(math.isfinite(v) for v in (shoulders, hips)):
        return Assessment('unknown', .5)
    score = .5 + .5 * max(-1., min(1., (shoulders + hips) / 2))
    if shoulders > .1 and hips > .1:
        return Assessment('back', score)
    if shoulders < -.1 and hips < -.1:
        return Assessment('front', score)
    return Assessment('side', .5)


def load_detector(pack_dir):
    """Use an already present torchvision checkpoint, on CPU; absent means skip.

    A pack may supply detectors/<filename>, or an existing Torch Hub cache may
    supply it. No package installation, network access, GPU residency or eager
    torch import on installations without that checkpoint.
    """
    import os
    cache = Path(os.environ.get('TORCH_HOME') or
                 Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'torch')
    try:
        checkpoint = next((p for p in (Path(pack_dir) / 'detectors' / _POSE_FILE,
                                      cache / 'hub' / 'checkpoints' / _POSE_FILE) if p.is_file()), None)
    except OSError:
        return None
    if checkpoint is None:
        return None
    try:
        import torch
        from torchvision.models.detection import keypointrcnn_resnet50_fpn
        from torchvision.transforms.functional import pil_to_tensor
        model = keypointrcnn_resnet50_fpn(weights=None, weights_backbone=None,
                                          min_size=512, max_size=768)
        model.load_state_dict(torch.load(checkpoint, map_location='cpu', weights_only=True))
        model.eval()
    except Exception:
        # An optional cache/dependency failure must not fail image generation.
        return None

    def detect(image):
        with torch.inference_mode():
            prediction = model([pil_to_tensor(image.convert('RGB')).float() / 255])[0]
        people = [i for i, score in enumerate(prediction['scores'].tolist()) if score >= .9]
        if len(people) != 1:
            return Assessment('unknown', .5)
        i = people[0]
        return assess_keypoints(prediction['keypoints'][i].tolist(),
                                prediction['keypoints_scores'][i].tolist(), prediction['boxes'][i].tolist())
    return detect


def generate_back(generate, seed, detector, *, cancel=None):
    """At most three candidates, retaining the highest BODY back score.

    ``generate(seed)`` uses the unchanged front reference, dimensions and steps.
    A detector failure/abstention returns the best existing image. Diagnostics
    contain fixed labels, seeds and timings, never prompts or user paths.
    """
    cancelled = cancel or (lambda: False)

    def check_cancel():
        if cancelled():
            raise InterruptedError('cancelled')

    started = time.monotonic()
    check_cancel()
    initial = seed if seed is not None or detector is None else secrets.randbits(53)
    best = generate(initial)
    diagnostics = dict(retries=0, selected_seed=initial, checks=[], extra_generation_seconds=0.,
                       check_seconds=0., skipped='detector_unavailable' if detector is None else '')
    if detector is None:
        return best, diagnostics
    best_score = -1.
    image = best
    current_seed = initial
    for attempt in range(MAX_RETRIES + 1):
        check_cancel()
        check_started = time.monotonic()
        try:
            assessment = detector(image)
            if not isinstance(assessment, Assessment):
                raise ValueError('invalid body assessment')
        except InterruptedError:
            raise
        except Exception:
            diagnostics['skipped'] = 'detector_failed'
            # Retain the earlier assessed best; the first image if no check worked.
            diagnostics['check_seconds'] += time.monotonic() - check_started
            break
        diagnostics['check_seconds'] += time.monotonic() - check_started
        check_cancel()
        diagnostics['checks'].append(dict(seed=current_seed, body=assessment.body,
                                          back_score=assessment.back_score))
        if assessment.back_score > best_score:
            best, best_score = image, assessment.back_score
            diagnostics['selected_seed'] = current_seed
        if assessment.body != 'front' or attempt == MAX_RETRIES:
            break
        current_seed = (initial + (attempt + 1) * 104729) % 2**53
        check_cancel()
        retry_started = time.monotonic()
        image = generate(current_seed)
        diagnostics['extra_generation_seconds'] += time.monotonic() - retry_started
        diagnostics['retries'] += 1
    diagnostics['seconds'] = time.monotonic() - started
    return best, diagnostics
