# Copyright (c) 2024 Alibaba Inc (authors: Xiang Lyu, Zhihao Du)
#               2025 Alibaba Inc (authors: Xiang Lyu, Yabin Li, Qihua, Shengqiang Li)
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
# Modified by FreeVideo: reduced to the CosyVoice3 inference path (no training, streaming
# or vLLM code); the Qwen2 backbone is built from its config and filled from llm.pt.
from typing import Callable, Generator, List

import torch
from torch import nn
from transformers import AutoConfig, Qwen2ForCausalLM


class Qwen2Encoder(torch.nn.Module):
    def __init__(self, config_dir):
        super().__init__()
        config = AutoConfig.from_pretrained(config_dir)
        config.dtype = torch.float32
        self.model = Qwen2ForCausalLM(config).to(torch.float32)

    def forward_one_step(self, xs, cache=None):
        # single sequence, no padding: the causal mask comes from the cache.
        # (a one-step mask of length 1 would be applied against the whole KV cache by transformers>=5)
        outs = self.model(
            inputs_embeds=xs,
            attention_mask=None,
            output_hidden_states=True,
            return_dict=True,
            use_cache=True,
            past_key_values=cache,
        )
        return outs.hidden_states[-1], outs.past_key_values


class CosyVoice3LM(torch.nn.Module):
    def __init__(
            self,
            llm_input_size: int,
            llm_output_size: int,
            speech_token_size: int,
            llm: torch.nn.Module,
            sampling: Callable,
            mix_ratio: List[int] = [5, 15],
    ):
        super().__init__()
        self.llm_input_size = llm_input_size
        self.llm_output_size = llm_output_size
        self.speech_token_size = speech_token_size
        self.sos = speech_token_size + 0
        self.eos_token = speech_token_size + 1
        self.task_id = speech_token_size + 2
        self.fill_token = speech_token_size + 3

        self.llm = llm
        self.llm_decoder = nn.Linear(llm_output_size, speech_token_size + 200, bias=False)
        self.speech_embedding = torch.nn.Embedding(speech_token_size + 200, llm_input_size)
        self.sampling = sampling
        self.mix_ratio = mix_ratio
        self.stop_token_ids = [speech_token_size + i for i in range(200)]

    def sampling_ids(self, weighted_scores: torch.Tensor, decoded_tokens: List, sampling: int, ignore_eos: bool = True):
        if ignore_eos is True:
            weighted_scores[self.speech_token_size] = -float('inf')
        return self.sampling(weighted_scores, decoded_tokens, sampling)

    @torch.inference_mode()
    def inference(
            self,
            text: torch.Tensor,
            text_len: torch.Tensor,
            prompt_text: torch.Tensor,
            prompt_text_len: torch.Tensor,
            prompt_speech_token: torch.Tensor,
            prompt_speech_token_len: torch.Tensor,
            embedding: torch.Tensor,
            sampling: int = 25,
            max_token_text_ratio: float = 20,
            min_token_text_ratio: float = 2,
            uuid: str = '',
    ) -> Generator[int, None, None]:
        device = text.device
        text = torch.concat([prompt_text, text], dim=1)
        text_len += prompt_text_len
        text_emb = self.llm.model.model.embed_tokens(text)
        # 151646 is the <|endofprompt|> token
        assert 151646 in text, '<|endofprompt|> not detected in CosyVoice3 text or prompt_text, check your input!'

        sos_emb = self.speech_embedding.weight[self.sos].reshape(1, 1, -1)
        task_id_emb = self.speech_embedding.weight[self.task_id].reshape(1, 1, -1)
        if prompt_speech_token_len != 0:
            prompt_speech_token_emb = self.speech_embedding(prompt_speech_token)
        else:
            prompt_speech_token_emb = torch.zeros(1, 0, self.llm_input_size, dtype=text_emb.dtype).to(device)
        lm_input = torch.concat([sos_emb, text_emb, task_id_emb, prompt_speech_token_emb], dim=1)

        min_len = int((text_len - prompt_text_len) * min_token_text_ratio)
        max_len = int((text_len - prompt_text_len) * max_token_text_ratio)

        out_tokens = []
        cache = None
        for i in range(max_len):
            y_pred, cache = self.llm.forward_one_step(lm_input, cache=cache)
            logp = self.llm_decoder(y_pred[:, -1]).log_softmax(dim=-1)
            top_ids = self.sampling_ids(logp.squeeze(dim=0), out_tokens, sampling, ignore_eos=True if i < min_len else False)
            if top_ids in self.stop_token_ids:
                break
            yield top_ids
            out_tokens.append(top_ids)
            lm_input = self.speech_embedding.weight[top_ids].reshape(1, 1, -1)
