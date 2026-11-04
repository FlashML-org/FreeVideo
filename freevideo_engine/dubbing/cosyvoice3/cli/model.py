# Copyright (c) 2024 Alibaba Inc (authors: Xiang Lyu)
#               2025 Alibaba Inc (authors: Xiang Lyu, Bofan Zhou)
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
#
# Modified by FreeVideo: reduced to the non-streaming CosyVoice3 inference path
# (no streaming, TensorRT, vLLM or JIT variants); the language model runs on the
# calling thread so errors propagate.
import torch
from torch.nn import functional as F


class CosyVoice3Model:

    def __init__(self, llm: torch.nn.Module, flow: torch.nn.Module, hift: torch.nn.Module, device, fp16: bool = False):
        self.device = torch.device(device)
        self.llm = llm
        self.flow = flow
        self.hift = hift
        self.fp16 = fp16
        # FSQ silent and breath token
        self.silent_tokens = [1, 2, 28, 29, 55, 248, 494, 2241, 2242, 2322, 2323]

    def load(self, llm_model, flow_model, hift_model):
        """Load the three state dicts onto the CPU first and then move each part to the device."""
        for part, path in ((self.llm, llm_model), (self.flow, flow_model)):
            part.load_state_dict(torch.load(path, map_location='cpu', weights_only=True), strict=True)
        hift_state = torch.load(hift_model, map_location='cpu', weights_only=True)
        self.hift.load_state_dict({k.replace('generator.', ''): v for k, v in hift_state.items()}, strict=True)
        for part in (self.llm, self.flow, self.hift):
            part.to(self.device).eval()

    def speech_tokens(self, text, prompt_text, llm_prompt_speech_token, llm_embedding):
        """Run the language model and return the generated speech tokens as a list."""
        device = self.device
        tokens, silent_run, max_silent_run = [], 0, 5
        generator = self.llm.inference(
            text=text.to(device),
            text_len=torch.tensor([text.shape[1]], dtype=torch.int32).to(device),
            prompt_text=prompt_text.to(device),
            prompt_text_len=torch.tensor([prompt_text.shape[1]], dtype=torch.int32).to(device),
            prompt_speech_token=llm_prompt_speech_token.to(device),
            prompt_speech_token_len=torch.tensor([llm_prompt_speech_token.shape[1]], dtype=torch.int32).to(device),
            embedding=llm_embedding.to(device),
            uuid='freevideo')
        for token in generator:
            if token in self.silent_tokens:
                silent_run += 1
                if silent_run > max_silent_run:
                    continue
            else:
                silent_run = 0
            tokens.append(token)
        return tokens

    def token2wav(self, token, prompt_token, prompt_feat, embedding, speed=1.0):
        device = self.device
        with torch.autocast(device_type=device.type, enabled=bool(self.fp16) and device.type == 'cuda'):
            mel, _ = self.flow.inference(token=token.to(device, dtype=torch.int32),
                                         token_len=torch.tensor([token.shape[1]], dtype=torch.int32).to(device),
                                         prompt_token=prompt_token.to(device),
                                         prompt_token_len=torch.tensor([prompt_token.shape[1]], dtype=torch.int32).to(device),
                                         prompt_feat=prompt_feat.to(device),
                                         prompt_feat_len=torch.tensor([prompt_feat.shape[1]], dtype=torch.int32).to(device),
                                         embedding=embedding.to(device),
                                         streaming=False,
                                         finalize=True)
            if speed != 1.0:
                mel = F.interpolate(mel, size=int(mel.shape[2] / speed), mode='linear')
            speech, _ = self.hift.inference(speech_feat=mel, finalize=True)
        return speech

    def tts(self, text, flow_embedding, llm_embedding, prompt_text, llm_prompt_speech_token,
            flow_prompt_speech_token, prompt_speech_feat, speed=1.0):
        tokens = self.speech_tokens(text, prompt_text, llm_prompt_speech_token, llm_embedding)
        speech = self.token2wav(torch.tensor(tokens).unsqueeze(dim=0), flow_prompt_speech_token,
                                prompt_speech_feat, flow_embedding, speed=speed)
        return speech.cpu()
