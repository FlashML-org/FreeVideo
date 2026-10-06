"""Run the H3 text encoder's language model in BF16 instead of FP32.

ComfyUI's text-encoder base builds the input embeddings in FP32 and calls the
language model with dtype=float32, so every NVFP4 weight is dequantized to
FP32 and every matmul runs in FP32, without tensor cores. The official MiniMax
H3 pipeline runs this encoder in BF16, the model's own dtype, and conditions
the transformer on BF16 hidden states; FreeVideo saves them in BF16 too. BF16
halves the working memory per token and uses tensor cores.
"""
from contextlib import contextmanager
import types

import torch

from .encoder_lowmem import language_model


def vision_model(model):
    """The Qwen3-VL wrapper that prepares image positions and DeepStack features."""
    for _, module in model.named_modules():
        if callable(getattr(type(module), 'build_image_inputs', None)):
            return module
    return None


def single_concat_image_inputs(wrapper):
    """build_image_inputs that joins each DeepStack layer once, in BF16.

    The native version grows every layer with one torch.cat per image or video
    block, a full copy each time, and keeps every block's own FP32 features. For
    two 15 s reference videos and three images (33 blocks) on Windows, where
    PyTorch has no expandable segments, those growing copies fragmented the
    allocator past 16 GiB before the language model started. The features are
    cast to the hidden-state dtype where they are added, so BF16 here is exact.
    """
    from comfy.text_encoders.qwen_vl import qwen2vl_mrope_position_ids

    def build_image_inputs(self, embeds, embeds_info):
        images = sorted([e for e in embeds_info if e.get("type") == "image"], key=lambda e: e["index"])
        if not images:
            return None, None, None
        device, length = embeds.device, embeds.shape[1]
        position_ids = qwen2vl_mrope_position_ids(embeds_info, length, device)
        visual_pos_masks = torch.zeros((1, length), dtype=torch.bool, device=device)
        for row in images:
            visual_pos_masks[0, row["index"]:row["index"] + row["size"]] = True
        for row in images:
            row["extra"]["deepstack"] = list(row["extra"]["deepstack"])
        deepstack = []
        for layer in range(len(images[0]["extra"]["deepstack"])):
            parts = [row["extra"]["deepstack"][layer] for row in images]
            joined = torch.empty((sum(part.shape[0] for part in parts),) + tuple(parts[0].shape[1:]),
                                 device=device, dtype=torch.bfloat16)
            start = 0
            for index, part in enumerate(parts):
                joined[start:start + part.shape[0]] = part
                start += part.shape[0]
                images[index]["extra"]["deepstack"][layer] = None  # each block's own copy is no longer needed
            deepstack.append(joined)
            del parts
        return position_ids, visual_pos_masks, deepstack
    return types.MethodType(build_image_inputs, wrapper)


def compact_vision_outputs(wrapper):
    """preprocess_embed that returns each block's cached memory before the next block.

    The vision tower runs once per image or video block. Without expandable
    segments (Windows), PyTorch kept every block's freed activation memory
    instead of reusing it: 33 blocks of two 15 s references grew reserved memory
    from 12.7 to 26.5 GiB while live tensors grew by 2.7 GiB, and on a 16 GB
    card the encode overflowed into shared memory before the language model
    started. Returning the cache after each block kept the whole encode at 18.2
    GiB including 12.3 GiB of weights. DeepStack features are kept in BF16, the
    dtype the language model casts them to anyway (exact).
    """
    original = wrapper.preprocess_embed

    def preprocess_embed(self, embed, device):
        merged, extra = original(embed, device)
        if merged is None or not merged.is_cuda:
            return merged, extra
        if extra and extra.get('deepstack') is not None:
            extra = dict(extra, deepstack=[value.to(torch.bfloat16) for value in extra['deepstack']])
        torch.cuda.empty_cache()
        return merged, extra
    return types.MethodType(preprocess_embed, wrapper)


@contextmanager
def bf16_language_model(model):
    llama = language_model(model)
    wrapper = vision_model(model)
    wrapper_previous = wrapper.__dict__.get('build_image_inputs') if wrapper is not None else None
    embed_previous = wrapper.__dict__.get('preprocess_embed') if wrapper is not None else None
    if wrapper is not None:
        wrapper.build_image_inputs = single_concat_image_inputs(wrapper)
        wrapper.preprocess_embed = compact_vision_outputs(wrapper)
    previous = llama.__dict__.get('forward')
    current = llama.forward

    def forward(self, x, attention_mask=None, embeds=None, num_tokens=None, intermediate_output=None,
                final_layer_norm_intermediate=True, dtype=None, position_ids=None, embeds_info=[],
                past_key_values=None, input_ids=None, deepstack_embeds=None, visual_pos_masks=None):
        if embeds is not None:
            embeds = embeds.to(torch.bfloat16)
        if deepstack_embeds is not None:
            deepstack_embeds = [value.to(torch.bfloat16) for value in deepstack_embeds]
        if attention_mask is not None and attention_mask.is_floating_point():
            attention_mask = attention_mask.to(torch.bfloat16)
        result = current(x, attention_mask=attention_mask, embeds=embeds, num_tokens=num_tokens,
                         intermediate_output=intermediate_output,
                         final_layer_norm_intermediate=final_layer_norm_intermediate, dtype=torch.bfloat16,
                         position_ids=position_ids, embeds_info=embeds_info, past_key_values=past_key_values,
                         input_ids=input_ids, deepstack_embeds=deepstack_embeds, visual_pos_masks=visual_pos_masks)
        return result

    llama.forward = types.MethodType(forward, llama)
    try:
        yield
    finally:
        if previous is None:
            del llama.forward
        else:
            llama.forward = previous
        if wrapper is not None:
            if wrapper_previous is None:
                del wrapper.build_image_inputs
            else:
                wrapper.build_image_inputs = wrapper_previous
            if embed_previous is None:
                del wrapper.preprocess_embed
            else:
                wrapper.preprocess_embed = embed_previous
