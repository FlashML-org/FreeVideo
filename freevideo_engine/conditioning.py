"""The shared native H3 text-conditioning contract; no encoder implementation."""
import torch


def to_cache(conditioning, prompt=None, task='t2va'):
    from .media_request import TASKS
    if task not in TASKS:
        raise ValueError('Unsupported H3 conditioning task')
    if len(conditioning) != 1:
        raise ValueError('FreeVideo expects one H3 text conditioning sequence')
    embeds, extra = conditioning[0]
    tags = extra.get('minimax_token_tags')
    if embeds.ndim != 3 or embeds.shape[0] != 1 or embeds.shape[-1] != 5120 or not embeds.shape[1]:
        raise ValueError('Use H3 layer-50 text conditioning [1,L,5120]')
    if not embeds.is_floating_point() or not bool(torch.isfinite(embeds).all()):
        raise ValueError('H3 conditioning must contain finite embeddings')
    if tags is None or tags.ndim != 1 or len(tags) != embeds.shape[1]:
        raise ValueError('H3 conditioning requires one token tag per embedding')
    if not bool(((tags == 0) | (tags == 1)).all()) or (task == 't2va' and not bool((tags == 1).all())):
        raise ValueError('Unexpected H3 modality tags for ' + task)
    if task in ('i2va', 'l2va', 'fl2va') and not bool((tags == 0).any()):
        raise ValueError('Keyframe tasks require vision conditioning')
    from .hardware import activation_dtype
    result = {'prompt_embeds': embeds[0].detach().to('cpu', activation_dtype()),
              'text_token_tags': tags.detach().to('cpu', torch.long)}
    if prompt is not None:
        result['prompt'] = prompt
    return result
