"""Who says each line: lines that sound like one person get one number.

A speaker embedding of the line (the model of the dubbing pack) is compared with the average of each person met so
far. Without the model or its runtime every line is speaker 0, as before.
"""
import logging
from collections import Counter

import numpy as np

_LOG = logging.getLogger(__name__)
SAME = .8            # cosine likeness from which two lines are one person (measured on film dialogue, see the card)
SHORTEST = .8        # a shorter line has too little voice to be told apart; it follows the line before it
RATE = 16000


def embedder(root=None):
    """A function from mono 16 kHz samples to a vector, or None when the model or its runtime is not here."""
    try:
        from ..dubbing import pack
        file = pack.directory(root) / 'campplus.onnx'
        if not file.is_file():
            return None
        try:
            import onnxruntime
        except ImportError:
            if not pack.activate(pack.runtime_path(root)):
                return None
            import onnxruntime
        import torch
        import torchaudio.compliance.kaldi as kaldi
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        session = onnxruntime.InferenceSession(str(file), sess_options=options, providers=['CPUExecutionProvider'])
    except Exception as error:      # a missing optional part must never fail the lines
        _LOG.info('Speakers are not told apart: %s', error)
        return None
    name = session.get_inputs()[0].name

    def embed(samples):
        feat = kaldi.fbank(torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))[None], num_mel_bins=80,
                           dither=0, sample_frequency=RATE)
        feat = feat - feat.mean(dim=0, keepdim=True)
        return np.asarray(session.run(None, {name: feat.unsqueeze(0).numpy()})[0]).flatten()
    return embed


def cluster(vectors, same=SAME):
    """Numbers for vectors ([array | None]) in order: a vector joins the person it is most like, if alike enough, else is a new one.

    None (a line too short to tell) takes the number of the line before it, or of the first one that has a vector."""
    centres, counts, numbers = [], [], []
    for vector in vectors:
        if vector is None:
            numbers.append(None)
            continue
        vector = np.asarray(vector, dtype=np.float64)
        vector = vector / (np.linalg.norm(vector) or 1.)
        best, likeness = None, same
        for index, centre in enumerate(centres):
            value = float(np.dot(vector, centre / (np.linalg.norm(centre) or 1.)))
            if value >= likeness:
                best, likeness = index, value
        if best is None:
            centres.append(vector.copy())
            counts.append(1)
            best = len(centres) - 1
        else:
            centres[best] = (centres[best] * counts[best] + vector) / (counts[best] + 1)
            counts[best] += 1
        numbers.append(best)
    known = [number for number in numbers if number is not None]
    fill = known[0] if known else 0
    for index, number in enumerate(numbers):
        if number is None:
            numbers[index] = numbers[index - 1] if index else fill
    return numbers


def label(slices, embed):
    """Speaker numbers for the mono 16 kHz `slices` of the lines, in order (all 0 without an embedder)."""
    if embed is None:
        return [0] * len(slices)
    vectors = []
    for samples in slices:
        try:
            vectors.append(embed(samples) if len(samples) >= SHORTEST * RATE else None)
        except Exception as error:
            _LOG.info('A line could not be told apart: %s', error)
            vectors.append(None)
    return cluster(vectors)


def by_role(numbers, roles):
    """Speaker numbers where the lines that match a script say who they are.

    `numbers`: the cluster of each line; `roles`: the role number of each line or None. A cluster takes the role most of
    its matched lines have; a line without a cluster match keeps the role of the nearest line that has one. Without any
    role the clusters stand as they are."""
    if all(role is None for role in roles):
        return list(numbers)
    votes = {}
    for number, role in zip(numbers, roles):
        if role is not None:
            votes.setdefault(number, Counter())[role] += 1
    result = []
    for number, role in zip(numbers, roles):
        if role is None and number in votes:
            role = votes[number].most_common(1)[0][0]
        result.append(role)
    known = [index for index, role in enumerate(result) if role is not None]
    for index, role in enumerate(result):
        if role is None:
            result[index] = result[min(known, key=lambda at: abs(at - index))] if known else 0
    return result
