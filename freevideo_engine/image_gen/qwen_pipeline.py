# Copyright 2026 Qwen-Image Team, The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Qwen-Image 2.1 Turbo orchestration, with no diffusers runtime dependency.

Placement protocol: ``enter(stage)`` makes that stage's model executable on the
generator's device; ``leave(stage)`` parks/releases it, including on errors or
cancellation. Stages are ``text`` (QwenTextEncoder.model), ``encode`` (reference
VAE encoding, only for edits), ``denoise`` (transformer), and ``decode`` (VAE).
Models are available on the generator before enter is called; a lazy vision
tower is materialized on CPU before enter('text'). The default placement does
nothing, so callers using CUDA must explicitly arrange model placement.
Progress uses only the public text/denoise/decode labels, in that order.

Instances are sequential and must not be shared between concurrent jobs.
The pack needs the three Comfy weight files plus local Qwen3-VL config and
processor assets (text_encoder/ and processor/, or text_encoders/ for both).
T1 supplies qwen_weights.load_dit and qwen_weights.load_vae; injected components
allow orchestration to be tested independently of those implementations.
"""

import json
import math
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import numpy as np
import torch
from PIL import Image

from .qwen_text import QwenTextEncoder


# Free card memory that must remain after a request's LoRA branches are placed on the card; below it they
# stay in pinned host memory and move to the card per use.
LORA_GPU_MARGIN = 2 << 30
TURBO_SIGMAS = (1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0)


class ImagePlacement(Protocol):
    def enter(self, stage: str) -> None: ...
    def leave(self, stage: str) -> None: ...


class NoopPlacement:
    def enter(self, stage):
        pass

    def leave(self, stage):
        pass


class ImageGenerationCancelled(RuntimeError):
    """The caller cancelled the job; no partial image is decoded or returned."""


def normalize_dimensions(width, height):
    """Floor to 32 pixels: 16x VAE compression plus the DiT's 2x2 grouping.

    This is a multiple of the contract's 16-pixel grid, with the stronger
    divisibility requirement imposed by the reference DiT and pipeline.
    """
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 32 for v in (width, height)):
        raise ValueError("width and height must be integers of at least 32 pixels")
    return width // 32 * 32, height // 32 * 32


def _validated_sigmas(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError("sample_sigmas must be a list")
    sigmas = tuple(float(value) for value in values)
    if len(sigmas) == 8:
        sigmas += (0.0,)
    if (len(sigmas) != 9 or sigmas[0] != 1.0 or sigmas[-1] != 0.0
            or not all(math.isfinite(v) for v in sigmas)
            or not all(a > b >= 0 for a, b in zip(sigmas, sigmas[1:]))):
        raise ValueError("Turbo requires eight strictly decreasing sigmas from 1, then terminal 0")
    return sigmas


def load_sample_sigmas(path):
    """Read sample_sigmas from checkpoint metadata; absence uses the Turbo grid.

    Recognized metadata keys are sample_sigmas and config (a JSON object with
    sample_sigmas). A present but invalid schedule is an error, never a fallback.
    """
    from safetensors import safe_open

    with safe_open(str(path), framework="pt", device="cpu") as checkpoint:
        metadata = checkpoint.metadata() or {}
    if "sample_sigmas" in metadata:
        return _validated_sigmas(json.loads(metadata["sample_sigmas"]))
    if "config" in metadata:
        config = json.loads(metadata["config"])
        if "sample_sigmas" in config:
            return _validated_sigmas(config["sample_sigmas"])
    return TURBO_SIGMAS


def euler_step(latents, prediction, sigma, next_sigma):
    # Match FlowMatchEulerDiscreteScheduler: FP32 sample, native prediction
    # multiplication, and final cast to the prediction's dtype.
    return (latents.float() + (next_sigma - sigma) * prediction).to(prediction.dtype)


def _config(model, name):
    config = model.config
    return config[name] if isinstance(config, Mapping) else getattr(config, name)


def _dtype(model):
    for value in model.parameters():
        if value.is_floating_point():
            return value.dtype
    return torch.float32


def _encoded_latents(output):
    if isinstance(output, torch.Tensor):
        return output
    if hasattr(output, "latent_dist"):
        return output.latent_dist.mode()
    if hasattr(output, "latents"):
        return output.latents
    raise TypeError("VAE.encode must return a Tensor, latent_dist, or latents")


def _sample(output):
    if isinstance(output, torch.Tensor):
        return output
    if isinstance(output, tuple):
        return output[0]
    return output.sample


class QwenImageGenerator:
    vae_scale_factor = 16

    def __init__(self, pack_dir: Path | None, *, placement: ImagePlacement | None = None, device="cuda",
                 transformer=None, vae=None, text_encoder=None, processor=None, sample_sigmas=None,
                 reference_resolution=1024, vae_tile=False):
        self.device = torch.device(device)
        self.placement = placement if placement is not None else NoopPlacement()
        self.pack_dir = Path(pack_dir) if pack_dir is not None else None
        self.transformer, self.vae = transformer, vae
        if text_encoder is not None and processor is not None:
            text_encoder = QwenTextEncoder(text_encoder, processor)
        self.text_encoder = text_encoder
        self._closed = False
        self.vae_tile = vae_tile
        self.vae_tile_size = None   # None: the decoder's own profile
        if not isinstance(reference_resolution, int) or reference_resolution < 32:
            raise ValueError("reference_resolution must be at least 32")
        self.reference_resolution = reference_resolution
        if any(model is None for model in (self.transformer, self.vae, self.text_encoder)):
            if self.pack_dir is None:
                raise ValueError("pack_dir is required when components are not supplied")
            self._load_components()
        if sample_sigmas is None and self.pack_dir is not None:
            dit_path = self._weight_path("diffusion_models", "qwen_image_2.1_turbo_int8_convrot.safetensors")
            if dit_path.exists():
                sample_sigmas = load_sample_sigmas(dit_path)
            elif (self.pack_dir / "model_index.json").exists():
                sample_sigmas = json.loads((self.pack_dir / "model_index.json").read_text()).get("sample_sigmas")
        self.sample_sigmas = _validated_sigmas(sample_sigmas if sample_sigmas is not None else TURBO_SIGMAS)

    def _weight_path(self, directory, filename):
        nested = self.pack_dir / directory / filename
        return nested if nested.exists() else self.pack_dir / filename

    def _load_components(self):
        if self.transformer is None:
            from .qwen_weights import load_dit

            self.transformer = load_dit(
                self._weight_path("diffusion_models", "qwen_image_2.1_turbo_int8_convrot.safetensors"), device="cpu"
            )
        if self.vae is None:
            from .qwen_weights import load_vae

            self.vae = load_vae(self._weight_path("vae", "qwen_image_2.1_vae_bf16.safetensors"), device="cpu")
        if self.text_encoder is None:
            from transformers import Qwen3VLProcessor
            from .qwen_te_weights import load_text_encoder

            config_dir = self.pack_dir / "text_encoder"
            if not config_dir.exists():
                config_dir = self.pack_dir / "text_encoders"
            processor_dir = self.pack_dir / "processor"
            if not processor_dir.exists():
                processor_dir = config_dir
            model = load_text_encoder(
                self._weight_path("text_encoders", "qwen3vl_8b_int8_convrot.safetensors"),
                config_path=config_dir, device="cpu",
            )
            processor = Qwen3VLProcessor.from_pretrained(str(processor_dir), local_files_only=True)
            self.text_encoder = QwenTextEncoder(model, processor)

    @contextmanager
    def _stage(self, stage):
        self.placement.enter(stage)
        try:
            yield
        finally:
            self.placement.leave(stage)

    @staticmethod
    def _pack_latents(latents, batch_size, num_channels_latents, height, width):
        return latents.reshape(batch_size, num_channels_latents, height * width).transpose(1, 2)

    @staticmethod
    def _unpack_latents(latents, height, width, vae_scale_factor=16):
        batch_size, _, channels = latents.shape
        height = 2 * (int(height) // (vae_scale_factor * 2))
        width = 2 * (int(width) // (vae_scale_factor * 2))
        return latents.transpose(1, 2).reshape(batch_size, channels, 1, height, width)

    def _stats(self, latents):
        channels = _config(self.vae, "z_dim")
        mean = torch.as_tensor(_config(self.vae, "latents_mean"), device=latents.device, dtype=latents.dtype)
        std = torch.as_tensor(_config(self.vae, "latents_std"), device=latents.device, dtype=latents.dtype)
        return mean.reshape(1, channels, 1, 1, 1), std.reshape(1, channels, 1, 1, 1)

    def _reference_inputs(self, images):
        pil_images, vae_images, sizes = [], [], []
        for image in images or []:
            if not isinstance(image, Image.Image):
                raise ValueError("reference_images must be a flat list of PIL images")
            image = image.convert("RGBA")
            ratio = image.width / image.height
            width = math.sqrt(self.reference_resolution**2 * ratio)
            height = width / ratio
            width, height = round(width / 32) * 32, round(height / 32) * 32
            if min(width, height) < 32:
                raise ValueError("Reference image aspect ratio produces an empty latent grid")
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
            pil_images.append(resized)
            # Preserve alpha for the four-channel VAE; only the encoder's copy
            # is composited over white in QwenTextEncoder.
            # Batch axis first, then channels: the layout the reference pipeline feeds the VAE
            # (same values as a per-image permute, a different memory order, and with it a
            # different convolution path).
            array = np.stack([np.asarray(resized)], axis=0).astype(np.float32) / 255.0
            vae_images.append(torch.from_numpy(array).permute(0, 3, 1, 2).unsqueeze(2) * 2 - 1)
            sizes.append((width, height))
        return pil_images or None, vae_images, sizes

    @torch.no_grad()
    def generate(self, prompt: str, *, width: int, height: int, seed: int | None = None, steps: int = 8,
                 reference_images=None, edit_mask=None, loras=None, progress=None, cancel=None) -> Image.Image:
        if self._closed:
            raise RuntimeError("Generator is closed")
        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")
        if isinstance(steps, bool) or steps != 8:
            raise ValueError("Qwen-Image Turbo supports the checkpoint's fixed eight-step profile")
        if reference_images is not None and not isinstance(reference_images, list):
            raise ValueError("reference_images must be a flat list of PIL images")
        width, height = normalize_dimensions(width, height)
        if edit_mask is not None and not reference_images:
            raise ValueError("edit_mask needs a reference image: the first one is the picture being edited")
        if loras:
            from .qwen_lora import UNUSABLE, inspect_file
            if not all(inspect_file(item["path"])["ok"] for item in loras):
                raise ValueError(UNUSABLE)

        def check_cancel():
            if cancel is not None and cancel():
                raise ImageGenerationCancelled("Image generation cancelled")

        def report(stage, fraction):
            if progress is not None:
                progress(stage, fraction)
            check_cancel()

        check_cancel()
        images, vae_images, sizes = self._reference_inputs(reference_images)
        if hasattr(self.text_encoder, "prepare_images"):
            self.text_encoder.prepare_images(images)
        check_cancel()
        with self._stage("text"):
            report("text", 0.0)
            if hasattr(self.text_encoder, "encode_prompt_with_image_mask"):
                embeds, mask, image_mask = self.text_encoder.encode_prompt_with_image_mask(
                    prompt, images, device=self.device
                )
            else:
                if images:
                    raise TypeError("Image conditioning needs encode_prompt_with_image_mask")
                embeds, mask = self.text_encoder.encode_prompt(prompt)
                image_mask = torch.zeros(embeds.shape[:2], dtype=torch.bool, device=embeds.device)
            embeds = embeds.to(self.device)
            mask = mask.to(self.device) if mask is not None else None
            image_mask = image_mask.to(device=self.device, dtype=torch.bool)
            report("text", 1.0)

        channels = _config(self.transformer, "in_channels")
        if channels != _config(self.vae, "z_dim"):
            raise ValueError("DiT in_channels must equal VAE z_dim (2.1 latents are unpatched)")
        ref_latents = []
        if vae_images:
            with self._stage("encode"):
                for index, image in enumerate(vae_images):
                    report("encode", index / len(vae_images))
                    # Always tiled, whatever the memory plan: the latents (and so the picture)
                    # must not depend on the card, and the tiled pass needs far less memory.
                    encoded = _encoded_latents(
                        self.vae.encode(image.to(device=self.device, dtype=_dtype(self.vae)), tile=True))
                    mean, std = self._stats(encoded)
                    encoded = (encoded - mean) / std
                    ref_latents.append(self._pack_latents(encoded, 1, channels, *encoded.shape[-2:]).to(embeds.dtype))
                report("encode", 1.0)
        keep_latents = cell_mask = None
        if edit_mask is not None:
            from . import region_edit

            original = reference_images[0].convert("RGBA")
            with self._stage("encode"):
                check_cancel()
                resized = original.resize((width, height), Image.Resampling.LANCZOS)
                array = np.asarray(resized, dtype=np.float32)[None] / 255.0
                tensor = torch.from_numpy(array).permute(0, 3, 1, 2).unsqueeze(2) * 2 - 1
                encoded = _encoded_latents(self.vae.encode(tensor.to(device=self.device, dtype=_dtype(self.vae)), tile=True))
                mean, std = self._stats(encoded)
                keep_latents = self._pack_latents((encoded - mean) / std, 1, channels, *encoded.shape[-2:]).to(embeds.dtype)
            cell_mask = torch.from_numpy(region_edit.latent_mask(edit_mask, (width, height))).to(self.device)
            cell_mask = cell_mask.reshape(1, -1, 1)
        ref_latents = torch.cat(ref_latents, dim=1) if ref_latents else None
        ref_tokens = 0 if ref_latents is None else ref_latents.shape[1]
        if image_mask.sum().item() * 4 != ref_tokens:
            raise ValueError("Vision image slots and reference VAE latent grid differ")

        generator = torch.Generator(device=self.device)
        generator.manual_seed(seed) if seed is not None else generator.seed()
        latent_height, latent_width = height // 16, width // 16
        latents = torch.randn((1, 1, channels, latent_height, latent_width), generator=generator,
                              device=self.device, dtype=embeds.dtype)
        latents = self._pack_latents(latents, 1, channels, latent_height, latent_width)
        start_noise = latents.clone() if keep_latents is not None else None
        img_shapes = [[*[(1, h // 16, w // 16) for w, h in sizes], (1, latent_height, latent_width)]]
        image_mask = torch.cat([image_mask, image_mask.new_ones(1, latents.shape[1] // 4)], dim=1)
        sigmas = torch.tensor(self.sample_sigmas, device=self.device, dtype=torch.float32)
        timesteps = sigmas[:-1] * 1000
        with self._stage("denoise"), self._lora_scope(loras):
            report("denoise", 0.0)
            for i, timestep in enumerate(timesteps):
                check_cancel()
                model_input = torch.cat([ref_latents, latents], dim=1) if ref_latents is not None else latents
                prediction = _sample(self.transformer(
                    hidden_states=model_input,
                    timestep=timestep.expand(1).to(latents.dtype) / 1000,
                    encoder_hidden_states=embeds,
                    encoder_hidden_states_mask=mask,
                    img_shapes=img_shapes,
                    img_mask=image_mask,
                    attention_kwargs=None,
                    kv_cache=None,
                    kv_cache_mode=None,
                    return_dict=False,
                ))[:, -latents.shape[1]:]
                check_cancel()
                latents = euler_step(latents, prediction, sigmas[i], sigmas[i + 1])
                if keep_latents is not None:
                    # Outside the selection the latent stays on the original's own path to this sigma.
                    s = sigmas[i + 1]
                    kept = (1 - s) * keep_latents.float() + s * start_noise.float()
                    latents = (cell_mask * latents.float() + (1 - cell_mask) * kept).to(prediction.dtype)
                report("denoise", (i + 1) / steps)

        check_cancel()
        with self._stage("decode"):
            report("decode", 0.0)
            latents = self._unpack_latents(latents, height, width).to(dtype=_dtype(self.vae))
            mean, std = self._stats(latents)
            latents = latents * std + mean
            # Tiles are most of a large decode; the blend and the copy to the host take the rest.
            kwargs = {"return_dict": False, "progress": lambda done, total: report("decode", .9 * done / total)}
            if self.vae_tile:
                kwargs["tile"] = True
                if self.vae_tile_size:
                    kwargs["tile_size"] = int(self.vae_tile_size)
                    kwargs["tile_stride"] = int(self.vae_tile_size) // 2
            pixels = _sample(self.vae.decode(latents, **kwargs))[:, :, 0]
            check_cancel()
            array = (pixels / 2 + 0.5).clamp(0, 1).float().cpu().permute(0, 2, 3, 1).numpy()[0]
            image = Image.fromarray((array * 255).round().astype(np.uint8))
            if edit_mask is not None:
                image = region_edit.composite(reference_images[0], image,
                                              region_edit.blend_alpha(edit_mask, reference_images[0].size))
            report("decode", 1.0)
        return image

    @contextmanager
    def _lora_scope(self, loras):
        """Attach this request's LoRAs to the DiT for the denoising loop only; no LoRAs, no change."""
        if not loras:
            yield
            return
        from .qwen_lora import LoraSet
        on_card = False
        if torch.device(self.device).type == "cuda":
            floor = LORA_GPU_MARGIN + sum(Path(item["path"]).stat().st_size for item in loras)
            on_card = torch.cuda.mem_get_info(self.device)[0] > floor
        branches = LoraSet(loras, device=self.device if on_card else "cpu")
        try:
            yield branches.attach(self.transformer)
        finally:
            branches.detach()

    def close(self) -> None:
        """Release owned model references; all active stages have already left."""
        self.text_encoder = self.transformer = self.vae = None
        self._closed = True
        # Int8Linear keeps the last activation alive for Q/K/V sharing.
        for name in ("freevideo_engine.int8_ops", "freevideo_engine.image_gen.int8_linear"):
            module = sys.modules.get(name)
            if module is not None:
                module.Int8Linear._last.clear()
