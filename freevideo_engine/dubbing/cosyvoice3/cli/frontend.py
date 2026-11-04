# Copyright (c) 2024 Alibaba Inc (authors: Xiang Lyu)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Modified by FreeVideo: reduced to the zero-shot and instruct2 inputs; onnxruntime, wetext and
# inflect are optional so the package imports without them.
import logging
import re
from functools import partial
from typing import Callable

import numpy as np
import torch
import torchaudio.compliance.kaldi as kaldi

from freevideo_engine.dubbing.cosyvoice3.utils.file_utils import load_wav
from freevideo_engine.dubbing.cosyvoice3.utils.frontend_utils import (
    contains_chinese, is_only_punctuation, remove_bracket, replace_blank, replace_corner_mark,
    spell_out_number, split_paragraph)
from freevideo_engine.dubbing.cosyvoice3.utils.mel import log_mel_spectrogram

logger = logging.getLogger(__name__)


class CosyVoiceFrontEnd:

    def __init__(self,
                 get_tokenizer: Callable,
                 feat_extractor: Callable,
                 campplus_model: str,
                 speech_tokenizer_model: str,
                 device='cpu'):
        import onnxruntime
        self.tokenizer = get_tokenizer()
        self.feat_extractor = feat_extractor
        self.device = torch.device(device)
        option = onnxruntime.SessionOptions()
        option.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        option.intra_op_num_threads = 1
        # both small networks run on the CPU so they take no video memory next to the three generators
        self.campplus_session = onnxruntime.InferenceSession(
            campplus_model, sess_options=option, providers=['CPUExecutionProvider'])
        self.speech_tokenizer_session = onnxruntime.InferenceSession(
            speech_tokenizer_model, sess_options=option, providers=['CPUExecutionProvider'])
        self.inflect_parser = None
        try:
            import inflect
            self.inflect_parser = inflect.engine()
        except Exception:
            logger.info('inflect is not available, digits in English text are kept as written')
        self.zh_tn_model = self.en_tn_model = None
        try:
            from wetext import Normalizer
            self.zh_tn_model = Normalizer(remove_erhua=False)
            self.en_tn_model = Normalizer()
        except Exception:
            logger.info('wetext is not available, text is not normalized')

    def _extract_text_token(self, text):
        text_token = self.tokenizer.encode(text)
        text_token = torch.tensor([text_token], dtype=torch.int32).to(self.device)
        text_token_len = torch.tensor([text_token.shape[1]], dtype=torch.int32).to(self.device)
        return text_token, text_token_len

    def _extract_speech_token(self, prompt_wav):
        speech = load_wav(prompt_wav, 16000)
        assert speech.shape[1] / 16000 <= 30, 'do not support extract speech token for audio longer than 30s'
        feat = log_mel_spectrogram(speech, n_mels=128)
        speech_token = self.speech_tokenizer_session.run(
            None, {self.speech_tokenizer_session.get_inputs()[0].name: feat.detach().cpu().numpy(),
                   self.speech_tokenizer_session.get_inputs()[1].name: np.array([feat.shape[2]], dtype=np.int32)}
        )[0].flatten().tolist()
        speech_token = torch.tensor([speech_token], dtype=torch.int32).to(self.device)
        speech_token_len = torch.tensor([speech_token.shape[1]], dtype=torch.int32).to(self.device)
        return speech_token, speech_token_len

    def _extract_spk_embedding(self, prompt_wav):
        speech = load_wav(prompt_wav, 16000)
        feat = kaldi.fbank(speech, num_mel_bins=80, dither=0, sample_frequency=16000)
        feat = feat - feat.mean(dim=0, keepdim=True)
        embedding = self.campplus_session.run(
            None, {self.campplus_session.get_inputs()[0].name: feat.unsqueeze(dim=0).cpu().numpy()}
        )[0].flatten().tolist()
        return torch.tensor([embedding]).to(self.device)

    def _extract_speech_feat(self, prompt_wav):
        speech = load_wav(prompt_wav, 24000)
        speech_feat = self.feat_extractor(speech).squeeze(dim=0).transpose(0, 1).to(self.device)
        speech_feat = speech_feat.unsqueeze(dim=0)
        speech_feat_len = torch.tensor([speech_feat.shape[1]], dtype=torch.int32).to(self.device)
        return speech_feat, speech_feat_len

    def text_normalize(self, text, split=True, text_frontend=True):
        # NOTE skip the text frontend when a control symbol is in the text
        if '<|' in text and '|>' in text:
            text_frontend = False
        if text_frontend is False or text == '':
            return [text] if split is True else text
        text = text.strip()
        if contains_chinese(text):
            if self.zh_tn_model is not None:
                text = self.zh_tn_model.normalize(text)
            text = text.replace("\n", "")
            text = replace_blank(text)
            text = replace_corner_mark(text)
            text = text.replace(".", "。")
            text = text.replace(" - ", "，")
            text = remove_bracket(text)
            text = re.sub(r'[，,、]+$', '。', text)
            lang = 'zh'
        else:
            if self.en_tn_model is not None:
                text = self.en_tn_model.normalize(text)
            if self.inflect_parser is not None:
                text = spell_out_number(text, self.inflect_parser)
            lang = 'en'
        texts = list(split_paragraph(text, self.tokenizer.encode, lang, token_max_n=80,
                                     token_min_n=60, merge_len=20, comma_split=False))
        texts = [i for i in texts if not is_only_punctuation(i)]
        return texts if split is True else text

    def frontend_zero_shot(self, tts_text, prompt_text, prompt_wav, resample_rate=24000):
        tts_text_token, tts_text_token_len = self._extract_text_token(tts_text)
        prompt_text_token, prompt_text_token_len = self._extract_text_token(prompt_text)
        speech_feat, speech_feat_len = self._extract_speech_feat(prompt_wav)
        speech_token, speech_token_len = self._extract_speech_token(prompt_wav)
        if resample_rate == 24000:
            # force speech_feat % speech_token = 2
            token_len = min(int(speech_feat.shape[1] / 2), speech_token.shape[1])
            speech_feat, speech_feat_len[:] = speech_feat[:, :2 * token_len], 2 * token_len
            speech_token, speech_token_len[:] = speech_token[:, :token_len], token_len
        embedding = self._extract_spk_embedding(prompt_wav)
        return {'prompt_text': prompt_text_token, 'prompt_text_len': prompt_text_token_len,
                'llm_prompt_speech_token': speech_token, 'llm_prompt_speech_token_len': speech_token_len,
                'flow_prompt_speech_token': speech_token, 'flow_prompt_speech_token_len': speech_token_len,
                'prompt_speech_feat': speech_feat, 'prompt_speech_feat_len': speech_feat_len,
                'llm_embedding': embedding, 'flow_embedding': embedding,
                'text': tts_text_token, 'text_len': tts_text_token_len}

    def frontend_instruct2(self, tts_text, instruct_text, prompt_wav, resample_rate=24000):
        model_input = self.frontend_zero_shot(tts_text, instruct_text, prompt_wav, resample_rate)
        del model_input['llm_prompt_speech_token']
        del model_input['llm_prompt_speech_token_len']
        return model_input
