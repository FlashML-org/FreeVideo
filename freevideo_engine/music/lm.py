"""Instrumental two-phase ACE-Step pt LM inference (MIT).

Sampling order, CFG, YAML roundtrip and FSM are ported from llm_inference.py.
The model is the built-in Transformers Qwen3 causal LM, loaded locally.
"""
from contextlib import nullcontext
import logging
from typing import Any, Dict, List, Optional, Tuple
import time
import torch
import yaml
from transformers.generation.logits_process import LogitsProcessorList, RepetitionPenaltyLogitsProcessor
from transformers.generation.streamers import BaseStreamer
from . import words
from .constrained_logits import MetadataConstrainedLogitsProcessor, FSMState
from .lm_constants import DEFAULT_LM_INSTRUCTION
logger = logging.getLogger(__name__)

def tqdm(items, **kwargs):
    return items

class InstrumentalLM:

    def __init__(self, model, tokenizer, processor, device):
        self.llm = model
        self.llm_tokenizer = tokenizer
        self.constrained_processor = processor
        self.device = torch.device(device)
        self.disable_tqdm = True
        self.use_legacy_cfg_prompt = False
        self.tokens = {}

    def _load_model_context(self):
        return nullcontext()

    def _compute_max_new_tokens(self, target_duration, generation_phase, fallback_max=4096):
        value = int(target_duration * 5) + 10 if target_duration else min(fallback_max, 3500)
        return min(value, 4096 - 64)

    def generate(self, caption, seconds, avoid=()):
        """The single-item llm_dit route with all GenerationParams defaults.

        avoid: things the music should leave out ("drums"); the codes step steers away from them and the
        planner's own description loses its clauses naming them. Empty: the upstream route unchanged."""
        lyrics = "[Instrumental]"
        defaults = dict(
            temperature=0.85, top_k=None,
            top_p=0.9, repetition_penalty=1.0, use_constrained_decoding=True,
            constrained_decoding_debug=False, caption=caption, lyrics=lyrics,
        )
        start = time.perf_counter()
        cot = self._run_pt_single(
            formatted_prompt=self.build_formatted_prompt(caption, lyrics),
            cfg_scale=1.0, target_duration=None,
            user_metadata={"duration": int(seconds)}, stop_at_reasoning=True,
            skip_genres=True, skip_caption=False, skip_language=False,
            generation_phase="cot", cot_text="", negative_prompt="NO USER INPUT", **defaults,
        )
        metadata, _ = self.parse_lm_output(cot)
        if avoid and metadata.get("caption"):
            metadata["caption"] = words.drop_clauses(metadata["caption"], avoid)
        cot_s = time.perf_counter() - start
        cot_text = self._format_metadata_as_cot(metadata)
        start = time.perf_counter()
        codes = self._run_pt_single(
            formatted_prompt=self.build_formatted_prompt_with_cot(caption, lyrics, cot_text),
            cfg_scale=2.0, target_duration=seconds, user_metadata=None,
            stop_at_reasoning=False, skip_genres=False,
            skip_caption=True, skip_language=True,
            generation_phase="codes", cot_text=cot_text,
            negative_prompt=words.negative(avoid) if avoid else "NO USER INPUT", **defaults,
        )
        _, codes = self.parse_lm_output(codes)
        if len(codes.split("<|audio_code_")) - 1 != int(seconds * 5):
            raise RuntimeError("LM did not generate the duration-constrained audio codes")
        return metadata, codes, {"cot_s": cot_s, "codes_s": time.perf_counter() - start}

    def _has_meaningful_negative_prompt(self, negative_prompt: str) -> bool:
        return negative_prompt and negative_prompt.strip() and (negative_prompt.strip() != 'NO USER INPUT')

    def _build_logits_processor(self, repetition_penalty: float) -> LogitsProcessorList:
        logits_processor = LogitsProcessorList()
        if repetition_penalty != 1.0:
            logits_processor.append(RepetitionPenaltyLogitsProcessor(penalty=repetition_penalty))
        return logits_processor

    def _setup_constrained_processor(self, use_constrained_decoding: bool, constrained_decoding_debug: bool, target_duration: Optional[float], user_metadata: Optional[Dict[str, Optional[str]]], stop_at_reasoning: bool, skip_genres: bool, skip_caption: bool, skip_language: bool, generation_phase: str, is_batch: bool=False, metadata_temperature: Optional[float]=None, codes_temperature: Optional[float]=None) -> Optional[MetadataConstrainedLogitsProcessor]:
        use_phase_temperatures = not is_batch and (metadata_temperature is not None or codes_temperature is not None)
        if not use_constrained_decoding and (not use_phase_temperatures):
            return None
        self.constrained_processor.reset()
        self.constrained_processor.enabled = use_constrained_decoding
        self.constrained_processor.debug = constrained_decoding_debug
        if use_phase_temperatures:
            self.constrained_processor.metadata_temperature = metadata_temperature
            self.constrained_processor.codes_temperature = codes_temperature
        else:
            self.constrained_processor.metadata_temperature = None
            self.constrained_processor.codes_temperature = None
        self.constrained_processor.set_target_duration(target_duration)
        if is_batch:
            self.constrained_processor.set_user_metadata(None)
            self.constrained_processor.set_stop_at_reasoning(False)
            self.constrained_processor.set_skip_genres(True)
            self.constrained_processor.set_skip_caption(True)
            self.constrained_processor.set_skip_language(True)
        else:
            self.constrained_processor.set_user_metadata(user_metadata)
            self.constrained_processor.set_stop_at_reasoning(stop_at_reasoning)
            self.constrained_processor.set_skip_genres(skip_genres)
            self.constrained_processor.set_skip_caption(skip_caption)
            self.constrained_processor.set_skip_language(skip_language)
        self.constrained_processor.set_generation_phase(generation_phase)
        return self.constrained_processor

    def _build_unconditional_prompt(self, caption: str, lyrics: str, cot_text: str, negative_prompt: str, generation_phase: str, is_batch: bool=False) -> str:
        if is_batch or generation_phase == 'codes':
            prompt = self.build_formatted_prompt_with_cot(caption, lyrics, cot_text, is_negative_prompt=True, negative_prompt=negative_prompt)
        else:
            prompt = self.build_formatted_prompt(caption, lyrics, is_negative_prompt=True, generation_phase='cot', negative_prompt=negative_prompt)
        logger.debug('Built CFG unconditional prompt')
        return prompt

    def _apply_top_k_filter(self, logits: torch.Tensor, top_k: Optional[int]) -> torch.Tensor:
        if top_k is not None and top_k > 0:
            indices_to_remove = logits < torch.topk(logits, top_k)[0][..., -1, None]
            logits[indices_to_remove] = float('-inf')
        return logits

    def _apply_top_p_filter(self, logits: torch.Tensor, top_p: Optional[float]) -> torch.Tensor:
        if top_p is not None and 0.0 < top_p < 1.0:
            (sorted_logits, sorted_indices) = torch.sort(logits, descending=True)
            cumulative_probs = torch.cumsum(torch.softmax(sorted_logits.float(), dim=-1), dim=-1)
            sorted_indices_to_remove = cumulative_probs > top_p
            sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[..., :-1].clone()
            sorted_indices_to_remove[..., 0] = 0
            indices_to_remove = sorted_indices_to_remove.scatter(1, sorted_indices, sorted_indices_to_remove)
            logits[indices_to_remove] = float('-inf')
        return logits

    def _sample_tokens(self, logits: torch.Tensor, temperature: float) -> torch.Tensor:
        if temperature > 0:
            logits = logits.float() / temperature
            probs = torch.softmax(logits, dim=-1)
            return torch.multinomial(probs, num_samples=1).squeeze(1)
        else:
            return torch.argmax(logits, dim=-1)

    def _check_eos_token(self, tokens: torch.Tensor, eos_token_id: int, pad_token_id: Optional[int]) -> bool:
        if torch.any(tokens == eos_token_id):
            return True
        if pad_token_id is not None and pad_token_id != eos_token_id:
            if torch.any(tokens == pad_token_id):
                return True
        return False

    def _update_constrained_processor_state(self, constrained_processor: Optional[MetadataConstrainedLogitsProcessor], tokens: torch.Tensor):
        if constrained_processor is not None:
            for b in range(tokens.shape[0]):
                constrained_processor.update_state(tokens[b].item())

    def _forward_pass(self, model: Any, generated_ids: torch.Tensor, model_kwargs: Dict[str, Any], past_key_values: Optional[Any], use_cache: bool) -> Any:
        if past_key_values is None:
            outputs = model(input_ids=generated_ids, **model_kwargs, use_cache=use_cache)
        else:
            outputs = model(input_ids=generated_ids[:, -1:], past_key_values=past_key_values, **model_kwargs, use_cache=use_cache)
        return outputs

    def _run_pt_single(self, formatted_prompt: str, temperature: float, cfg_scale: float, negative_prompt: str, top_k: Optional[int], top_p: Optional[float], repetition_penalty: float, use_constrained_decoding: bool, constrained_decoding_debug: bool, target_duration: Optional[float], user_metadata: Optional[Dict[str, Optional[str]]], stop_at_reasoning: bool, skip_genres: bool, skip_caption: bool, skip_language: bool, generation_phase: str, caption: str, lyrics: str, cot_text: str) -> str:
        inputs = self.llm_tokenizer(formatted_prompt, return_tensors='pt', padding=False, truncation=True)
        constrained_processor = self._setup_constrained_processor(use_constrained_decoding=use_constrained_decoding, constrained_decoding_debug=constrained_decoding_debug, target_duration=target_duration, user_metadata=user_metadata, stop_at_reasoning=stop_at_reasoning, skip_genres=skip_genres, skip_caption=skip_caption, skip_language=skip_language, generation_phase=generation_phase, is_batch=False)
        with self._load_model_context():
            inputs = {k: v.to(self.device) for (k, v) in inputs.items()}
            max_new_tokens = self._compute_max_new_tokens(target_duration=target_duration, generation_phase=generation_phase, fallback_max=getattr(self.llm.config, 'max_new_tokens', 4096))
            logits_processor = self._build_logits_processor(repetition_penalty)
            if cfg_scale > 1.0:
                formatted_unconditional_prompt = self._build_unconditional_prompt(caption=caption, lyrics=lyrics, cot_text=cot_text, negative_prompt=negative_prompt, generation_phase=generation_phase, is_batch=False)
                batch_texts = [formatted_prompt, formatted_unconditional_prompt]
                original_padding_side = self.llm_tokenizer.padding_side
                self.llm_tokenizer.padding_side = 'left'
                batch_inputs_tokenized = self.llm_tokenizer(batch_texts, return_tensors='pt', padding=True, truncation=True)
                self.llm_tokenizer.padding_side = original_padding_side
                batch_inputs_tokenized = {k: v.to(self.device) for (k, v) in batch_inputs_tokenized.items()}
                batch_input_ids = batch_inputs_tokenized['input_ids']
                batch_attention_mask = batch_inputs_tokenized.get('attention_mask', None)
                outputs = self._generate_with_cfg_custom(batch_input_ids=batch_input_ids, batch_attention_mask=batch_attention_mask, max_new_tokens=max_new_tokens, temperature=temperature, cfg_scale=cfg_scale, top_k=top_k, top_p=top_p, repetition_penalty=repetition_penalty, pad_token_id=self.llm_tokenizer.pad_token_id or self.llm_tokenizer.eos_token_id, streamer=None, constrained_processor=constrained_processor)
                outputs = outputs[0:1]
            else:
                outputs = self._generate_with_constrained_decoding(input_ids=inputs['input_ids'], attention_mask=inputs.get('attention_mask'), max_new_tokens=max_new_tokens, temperature=temperature, top_k=top_k, top_p=top_p, repetition_penalty=repetition_penalty, pad_token_id=self.llm_tokenizer.pad_token_id or self.llm_tokenizer.eos_token_id, streamer=None, constrained_processor=constrained_processor)
        if isinstance(outputs, torch.Tensor):
            if outputs.dim() == 2:
                generated_ids = outputs[0]
            else:
                generated_ids = outputs
        else:
            generated_ids = outputs[0]
        if cfg_scale > 1.0:
            input_length = batch_inputs_tokenized['input_ids'].shape[1]
        else:
            input_length = inputs['input_ids'].shape[1]
        generated_ids = generated_ids[input_length:]
        self.tokens[generation_phase] = generated_ids.detach().cpu().clone()
        if generated_ids.device.type != 'cpu':
            generated_ids = generated_ids.cpu()
        output_text = self.llm_tokenizer.decode(generated_ids, skip_special_tokens=False)
        return output_text

    def _format_metadata_as_cot(self, metadata: Dict[str, Any]) -> str:
        cot_items = {}
        for key in ['bpm', 'caption', 'duration', 'keyscale', 'language', 'timesignature']:
            if key in metadata and metadata[key] is not None:
                value = metadata[key]
                if key == 'timesignature' and value.endswith('/4'):
                    value = value.split('/')[0]
                if isinstance(value, str) and value.isdigit():
                    value = int(value)
                cot_items[key] = value
        if len(cot_items) > 0:
            cot_yaml = yaml.dump(cot_items, allow_unicode=True, sort_keys=True).strip()
        else:
            cot_yaml = ''
        return f'<think>\n{cot_yaml}\n</think>'

    def build_formatted_prompt(self, caption: str, lyrics: str='', is_negative_prompt: bool=False, generation_phase: str='cot', negative_prompt: str='NO USER INPUT') -> str:
        if self.llm_tokenizer is None:
            raise ValueError('LLM tokenizer is not initialized. Call initialize() first.')
        if is_negative_prompt:
            has_negative_prompt = self._has_meaningful_negative_prompt(negative_prompt)
            if generation_phase == 'cot':
                if has_negative_prompt:
                    prompt = f'# Caption\n{negative_prompt}\n\n# Lyric\n{lyrics}\n'
                else:
                    prompt = f'# Lyric\n{lyrics}\n'
            else:
                prompt = caption
        else:
            prompt = f'# Caption\n{caption}\n\n# Lyric\n{lyrics}\n'
        return self.llm_tokenizer.apply_chat_template([{'role': 'system', 'content': f'# Instruction\n{DEFAULT_LM_INSTRUCTION}\n\n'}, {'role': 'user', 'content': prompt}], tokenize=False, add_generation_prompt=True)

    def build_formatted_prompt_with_cot(self, caption: str, lyrics: str, cot_text: str, is_negative_prompt: bool=False, negative_prompt: str='NO USER INPUT') -> str:
        if self.llm_tokenizer is None:
            raise ValueError('LLM tokenizer is not initialized. Call initialize() first.')
        if is_negative_prompt:
            has_negative_prompt = self._has_meaningful_negative_prompt(negative_prompt)
            cot_for_prompt = '<think>\n\n</think>'
            user_prompt = negative_prompt if has_negative_prompt else 'NO USER INPUT'
        else:
            cot_for_prompt = cot_text
            user_prompt = f'# Caption\n{caption}\n\n# Lyric\n{lyrics}\n'
        formatted = self.llm_tokenizer.apply_chat_template([{'role': 'system', 'content': f'# Instruction\n{DEFAULT_LM_INSTRUCTION}\n\n'}, {'role': 'user', 'content': user_prompt}], tokenize=False, add_generation_prompt=True)
        formatted += cot_for_prompt + '\n\n'
        return formatted

    def _generate_with_constrained_decoding(self, input_ids: torch.Tensor, attention_mask: Optional[torch.Tensor], max_new_tokens: int, temperature: float, top_k: Optional[int], top_p: Optional[float], repetition_penalty: float, pad_token_id: int, streamer: Optional[BaseStreamer], constrained_processor: Optional[MetadataConstrainedLogitsProcessor]=None) -> torch.Tensor:
        model = self.llm
        device = self.device
        generated_ids = input_ids.clone()
        if attention_mask is not None:
            attn_mask = attention_mask.clone()
        else:
            attn_mask = torch.ones_like(input_ids)
        model_kwargs = {'attention_mask': attn_mask}
        past_key_values = None
        use_cache = hasattr(model, 'generation_config') and getattr(model.generation_config, 'use_cache', True)
        eos_token_id = self.llm_tokenizer.eos_token_id
        if eos_token_id is None:
            eos_token_id = pad_token_id
        logits_processor = self._build_logits_processor(repetition_penalty)
        with torch.inference_mode():
            for step in tqdm(range(max_new_tokens), desc='LLM Constrained Decoding', unit='token', disable=self.disable_tqdm):
                outputs = self._forward_pass(model, generated_ids, model_kwargs, past_key_values, use_cache)
                next_token_logits = outputs.logits[:, -1, :]
                if constrained_processor is not None:
                    next_token_logits = constrained_processor(generated_ids, next_token_logits)
                for processor in logits_processor:
                    next_token_logits = processor(generated_ids, next_token_logits)
                next_token_logits = self._apply_top_k_filter(next_token_logits, top_k)
                next_token_logits = self._apply_top_p_filter(next_token_logits, top_p)
                next_tokens = self._sample_tokens(next_token_logits, temperature)
                self._update_constrained_processor_state(constrained_processor, next_tokens)
                should_stop = self._check_eos_token(next_tokens, eos_token_id, pad_token_id)
                next_tokens_unsqueezed = next_tokens.unsqueeze(1)
                generated_ids = torch.cat([generated_ids, next_tokens_unsqueezed], dim=1)
                attn_mask = torch.cat([attn_mask, torch.ones((input_ids.shape[0], 1), device=device, dtype=attn_mask.dtype)], dim=1)
                model_kwargs['attention_mask'] = attn_mask
                if use_cache and hasattr(outputs, 'past_key_values'):
                    past_key_values = outputs.past_key_values
                if streamer is not None:
                    streamer.put(next_tokens_unsqueezed)
                if should_stop:
                    break
        if streamer is not None:
            streamer.end()
        del past_key_values
        return generated_ids

    def _generate_with_cfg_custom(self, batch_input_ids: torch.Tensor, batch_attention_mask: Optional[torch.Tensor], max_new_tokens: int, temperature: float, cfg_scale: float, top_k: Optional[int], top_p: Optional[float], repetition_penalty: float, pad_token_id: int, streamer: Optional[BaseStreamer], constrained_processor: Optional[MetadataConstrainedLogitsProcessor]=None) -> torch.Tensor:
        from .constrained_logits import FSMState
        model = self.llm
        device = self.device
        batch_size = batch_input_ids.shape[0] // 2
        cond_start_idx = 0
        uncond_start_idx = batch_size
        generated_ids = batch_input_ids.clone()
        if batch_attention_mask is not None:
            attention_mask = batch_attention_mask.clone()
        else:
            attention_mask = torch.ones_like(batch_input_ids)
        model_kwargs = {}
        if batch_attention_mask is not None:
            model_kwargs['attention_mask'] = attention_mask
        past_key_values = None
        use_cache = hasattr(model, 'generation_config') and getattr(model.generation_config, 'use_cache', True)
        eos_token_id = self.llm_tokenizer.eos_token_id
        if eos_token_id is None:
            eos_token_id = pad_token_id
        logits_processor = self._build_logits_processor(repetition_penalty)
        seq_finished = torch.zeros(batch_size, dtype=torch.bool, device=device)
        with torch.inference_mode():
            for step in tqdm(range(max_new_tokens), desc='LLM CFG Generation', unit='token', disable=self.disable_tqdm):
                outputs = self._forward_pass(model, generated_ids, model_kwargs, past_key_values, use_cache)
                next_token_logits = outputs.logits[:, -1, :]
                cond_logits = next_token_logits[cond_start_idx:cond_start_idx + batch_size]
                uncond_logits = next_token_logits[uncond_start_idx:uncond_start_idx + batch_size]
                if constrained_processor is not None and constrained_processor.state == FSMState.CODES_GENERATION and (constrained_processor.non_audio_code_mask is not None):
                    non_audio_mask = constrained_processor.non_audio_code_mask
                    if non_audio_mask.device != device or non_audio_mask.dtype != torch.float32:
                        non_audio_mask = non_audio_mask.to(device=device, dtype=torch.float32)
                    valid_mask = non_audio_mask[0] == 0
                    valid_indices = valid_mask.nonzero(as_tuple=False).squeeze(1)
                    cond_valid = cond_logits[:, valid_indices].float()
                    uncond_valid = uncond_logits[:, valid_indices].float()
                    cfg_valid = uncond_valid + cfg_scale * (cond_valid - uncond_valid)
                    cfg_logits = torch.full((batch_size, cond_logits.shape[1]), float('-inf'), device=device, dtype=torch.float32)
                    cfg_logits[:, valid_indices] = cfg_valid
                else:
                    cfg_logits = uncond_logits.float() + cfg_scale * (cond_logits.float() - uncond_logits.float())
                    cfg_logits = torch.nan_to_num(cfg_logits, nan=float('-inf'))
                if constrained_processor is not None:
                    current_input_ids = generated_ids[cond_start_idx:cond_start_idx + batch_size]
                    cfg_logits = constrained_processor(current_input_ids, cfg_logits)
                current_input_ids = generated_ids[cond_start_idx:cond_start_idx + batch_size]
                for processor in logits_processor:
                    cfg_logits = processor(current_input_ids, cfg_logits)
                cfg_logits = self._apply_top_k_filter(cfg_logits, top_k)
                cfg_logits = self._apply_top_p_filter(cfg_logits, top_p)
                if seq_finished.any():
                    for b in range(batch_size):
                        if seq_finished[b]:
                            cfg_logits[b, :] = float('-inf')
                            cfg_logits[b, eos_token_id] = 0.0
                next_tokens = self._sample_tokens(cfg_logits, temperature)
                self._update_constrained_processor_state(constrained_processor, next_tokens)
                is_eos = next_tokens == eos_token_id
                if pad_token_id is not None and pad_token_id != eos_token_id:
                    is_eos = is_eos | (next_tokens == pad_token_id)
                seq_finished = seq_finished | is_eos
                next_tokens_unsqueezed = next_tokens.unsqueeze(1)
                generated_ids = torch.cat([generated_ids, next_tokens_unsqueezed.repeat(2, 1)], dim=1)
                attention_mask = torch.cat([attention_mask, torch.ones((batch_size * 2, 1), device=device, dtype=attention_mask.dtype)], dim=1)
                model_kwargs['attention_mask'] = attention_mask
                if use_cache and hasattr(outputs, 'past_key_values'):
                    past_key_values = outputs.past_key_values
                if streamer is not None:
                    streamer.put(next_tokens_unsqueezed)
                if seq_finished.all():
                    break
        if streamer is not None:
            streamer.end()
        del past_key_values
        return generated_ids

    def parse_lm_output(self, output_text: str) -> Tuple[Dict[str, Any], str]:
        debug_output_text = output_text.split('</think>')[0]
        logger.debug('Parsing LM metadata')
        metadata = {}
        audio_codes = ''
        import re
        code_pattern = '<\\|audio_code_\\d+\\|>'
        code_matches = re.findall(code_pattern, output_text)
        if code_matches:
            audio_codes = ''.join(code_matches)
        reasoning_patterns = ['<think>(.*?)</think>', '<think>(.*?)</think>', '<reasoning>(.*?)</reasoning>']
        reasoning_text = None
        for pattern in reasoning_patterns:
            match = re.search(pattern, output_text, re.DOTALL)
            if match:
                reasoning_text = match.group(1).strip()
                break
        if not reasoning_text:
            lines_before_codes = output_text.split('<|audio_code_')[0] if '<|audio_code_' in output_text else output_text
            reasoning_text = lines_before_codes.strip()
        if reasoning_text:
            lines = reasoning_text.split('\n')
            current_key = None
            current_value_lines = []

            def save_current_field():
                nonlocal current_key, current_value_lines
                if current_key and current_value_lines:
                    value = '\n'.join(current_value_lines)
                    if current_key == 'bpm':
                        try:
                            metadata['bpm'] = int(value.strip())
                        except:
                            metadata['bpm'] = value.strip()
                    elif current_key == 'caption':
                        metadata['caption'] = MetadataConstrainedLogitsProcessor.postprocess_caption(value)
                    elif current_key == 'duration':
                        try:
                            metadata['duration'] = int(value.strip())
                        except:
                            metadata['duration'] = value.strip()
                    elif current_key == 'genres':
                        metadata['genres'] = value.strip()
                    elif current_key == 'keyscale':
                        metadata['keyscale'] = value.strip()
                    elif current_key == 'language':
                        metadata['language'] = value.strip()
                    elif current_key == 'timesignature':
                        metadata['timesignature'] = value.strip()
                current_key = None
                current_value_lines = []
            for line in lines:
                if line.strip().startswith('<'):
                    continue
                if line and (not line[0].isspace()) and (':' in line):
                    save_current_field()
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        current_key = parts[0].strip().lower()
                        first_value = parts[1]
                        if first_value.strip():
                            current_value_lines.append(first_value)
                elif line.startswith(' ') or line.startswith('\t'):
                    if current_key:
                        current_value_lines.append(line)
            save_current_field()
        return (metadata, audio_codes)
