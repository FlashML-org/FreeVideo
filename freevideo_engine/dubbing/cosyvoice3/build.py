# Copyright (c) 2024 Alibaba Inc (authors: Xiang Lyu)
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
"""Build Fun-CosyVoice3-0.5B from its published weights without hyperpyyaml.

The parameters below are those of the model's cosyvoice3.yaml.
"""
import os
from functools import partial

import torch
from omegaconf import DictConfig

from freevideo_engine.dubbing.cosyvoice3.cli.frontend import CosyVoiceFrontEnd
from freevideo_engine.dubbing.cosyvoice3.cli.model import CosyVoice3Model
from freevideo_engine.dubbing.cosyvoice3.flow.DiT.dit import DiT
from freevideo_engine.dubbing.cosyvoice3.flow.flow import CausalMaskedDiffWithDiT
from freevideo_engine.dubbing.cosyvoice3.flow.flow_matching import CausalConditionalCFM
from freevideo_engine.dubbing.cosyvoice3.hifigan.f0_predictor import CausalConvRNNF0Predictor
from freevideo_engine.dubbing.cosyvoice3.hifigan.generator import CausalHiFTGenerator
from freevideo_engine.dubbing.cosyvoice3.llm.llm import CosyVoice3LM, Qwen2Encoder
from freevideo_engine.dubbing.cosyvoice3.tokenizer.tokenizer import get_qwen_tokenizer
from freevideo_engine.dubbing.cosyvoice3.transformer.upsample_encoder import PreLookaheadLayer
from freevideo_engine.dubbing.cosyvoice3.utils.common import ras_sampling
from freevideo_engine.dubbing.cosyvoice3.utils.mel import mel_spectrogram

SAMPLE_RATE = 24000
QWEN_DIR = 'CosyVoice-BlankEN'
PROMPT_PREFIX = 'You are a helpful assistant.<|endofprompt|>'


def build_networks(qwen_dir):
    llm = CosyVoice3LM(
        llm_input_size=896, llm_output_size=896, speech_token_size=6561,
        llm=Qwen2Encoder(qwen_dir),
        sampling=partial(ras_sampling, top_p=0.8, top_k=25, win_size=10, tau_r=0.1),
        mix_ratio=[5, 15])
    flow = CausalMaskedDiffWithDiT(
        input_size=80, output_size=80, spk_embed_dim=192, output_type='mel', vocab_size=6561,
        input_frame_rate=25, only_mask_loss=True, token_mel_ratio=2, pre_lookahead_len=3,
        pre_lookahead_layer=PreLookaheadLayer(in_channels=80, channels=1024, pre_lookahead_len=3),
        decoder=CausalConditionalCFM(
            in_channels=240, n_spks=1, spk_emb_dim=80,
            cfm_params=DictConfig({'sigma_min': 1e-06, 'solver': 'euler', 't_scheduler': 'cosine',
                                   'training_cfg_rate': 0.2, 'inference_cfg_rate': 0.7, 'reg_loss_type': 'l1'}),
            estimator=DiT(dim=1024, depth=22, heads=16, dim_head=64, ff_mult=2, mel_dim=80, mu_dim=80,
                          spk_dim=80, out_channels=80, static_chunk_size=50, num_decoding_left_chunks=-1)))
    hift = CausalHiFTGenerator(
        in_channels=80, base_channels=512, nb_harmonics=8, sampling_rate=SAMPLE_RATE,
        nsf_alpha=0.1, nsf_sigma=0.003, nsf_voiced_threshold=10,
        upsample_rates=[8, 5, 3], upsample_kernel_sizes=[16, 11, 7],
        istft_params={'n_fft': 16, 'hop_len': 4},
        resblock_kernel_sizes=[3, 7, 11], resblock_dilation_sizes=[[1, 3, 5]] * 3,
        source_resblock_kernel_sizes=[7, 7, 11], source_resblock_dilation_sizes=[[1, 3, 5]] * 3,
        lrelu_slope=0.1, audio_limit=0.99, conv_pre_look_right=4,
        f0_predictor=CausalConvRNNF0Predictor(num_class=1, in_channels=80, cond_channels=512))
    return llm, flow, hift


def _feat_extractor():
    return partial(mel_spectrogram, n_fft=1920, num_mels=80, sampling_rate=SAMPLE_RATE, hop_size=480,
                   win_size=1920, fmin=0, fmax=None, center=False)


class CosyVoice3:
    """Zero-shot voice cloning with Fun-CosyVoice3-0.5B; every call returns one float32 numpy waveform."""

    sample_rate = SAMPLE_RATE

    def __init__(self, model_dir, device='cuda', fp16=False):
        qwen_dir = os.path.join(model_dir, QWEN_DIR)
        self.frontend = CosyVoiceFrontEnd(
            lambda: get_qwen_tokenizer(token_path=qwen_dir, skip_special_tokens=True, version='cosyvoice3'),
            _feat_extractor(),
            os.path.join(model_dir, 'campplus.onnx'),
            os.path.join(model_dir, 'speech_tokenizer_v3.onnx'),
            device=device)
        llm, flow, hift = build_networks(qwen_dir)
        self.model = CosyVoice3Model(llm, flow, hift, device, fp16)
        self.model.load(os.path.join(model_dir, 'llm.pt'), os.path.join(model_dir, 'flow.pt'),
                        os.path.join(model_dir, 'hift.pt'))

    def _run(self, texts, make_input, speed):
        import numpy as np
        parts = []
        for piece in self.frontend.text_normalize(texts, split=True, text_frontend=True):
            inp = make_input(piece)
            speech = self.model.tts(
                text=inp['text'], flow_embedding=inp['flow_embedding'], llm_embedding=inp['llm_embedding'],
                prompt_text=inp['prompt_text'],
                llm_prompt_speech_token=inp.get('llm_prompt_speech_token', torch.zeros(1, 0, dtype=torch.int32)),
                flow_prompt_speech_token=inp['flow_prompt_speech_token'],
                prompt_speech_feat=inp['prompt_speech_feat'], speed=speed)
            parts.append(speech.squeeze(0).float().cpu().numpy())
        return np.concatenate(parts).astype('float32')

    def zero_shot(self, text, prompt_text, prompt_wav, speed=1.0):
        """Say `text` in the voice of `prompt_wav`, whose transcript is `prompt_text`."""
        prompt = PROMPT_PREFIX + prompt_text
        return self._run(text, lambda t: self.frontend.frontend_zero_shot(t, prompt, prompt_wav, SAMPLE_RATE), speed)

    def instruct(self, text, instruction, prompt_wav, speed=1.0):
        """Say `text` in the voice of `prompt_wav` in the manner `instruction` (a Chinese sentence) describes."""
        prompt = f'You are a helpful assistant. {instruction}<|endofprompt|>'
        return self._run(text, lambda t: self.frontend.frontend_instruct2(t, prompt, prompt_wav, SAMPLE_RATE), speed)
