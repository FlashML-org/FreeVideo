# Adapted from ACE-Step 1.5 constrained_logits_processor.py (MIT).
"""
Constrained Logits Processor for ACE-Step Language Model

This module implements a finite state machine (FSM) based logits processor that constrains
the language model's output to follow specific formats and value ranges during music generation.

Key Features:
- Enforces structured metadata generation (BPM, duration, keyscale, etc.)
- Validates numeric ranges (BPM: 30-300, Duration: 10-600s)
- Ensures proper formatting for musical metadata
- Prevents generation of invalid tokens or formats
- Supports constrained audio code generation (0-63999)

The FSM guides the model through different states to ensure outputs conform to expected
schema requirements without post-processing corrections.

Usage:
    processor = ConstrainedLogitsProcessor(tokenizer, mode="metadata")
    outputs = model.generate(inputs, logits_processor=[processor])
"""
from enum import Enum, auto
from typing import Optional, Dict, Any, Tuple, List, Callable, Set
import logging
logger = logging.getLogger(__name__)
from transformers import AutoTokenizer
from transformers.generation.logits_process import LogitsProcessor
import torch
from .lm_constants import VALID_LANGUAGES, KEYSCALE_NOTES, KEYSCALE_ACCIDENTALS, KEYSCALE_MODES, VALID_KEYSCALES, BPM_MIN, BPM_MAX, DURATION_MIN, DURATION_MAX, VALID_TIME_SIGNATURES
MAX_AUDIO_CODE = 63999

class FSMState(Enum):
    """Finite State Machine states for metadata generation"""
    THINK_TAG = auto()
    NEWLINE_AFTER_THINK = auto()
    BPM_NAME = auto()
    BPM_VALUE = auto()
    NEWLINE_AFTER_BPM = auto()
    CAPTION_NAME = auto()
    CAPTION_VALUE = auto()
    DURATION_NAME = auto()
    DURATION_VALUE = auto()
    NEWLINE_AFTER_DURATION = auto()
    GENRES_NAME = auto()
    GENRES_VALUE = auto()
    NEWLINE_AFTER_GENRES = auto()
    KEYSCALE_NAME = auto()
    KEYSCALE_VALUE = auto()
    NEWLINE_AFTER_KEYSCALE = auto()
    LANGUAGE_NAME = auto()
    LANGUAGE_VALUE = auto()
    TIMESIG_NAME = auto()
    TIMESIG_VALUE = auto()
    NEWLINE_AFTER_TIMESIG = auto()
    THINK_END_TAG = auto()
    CODES_GENERATION = auto()
    COMPLETED = auto()

class MetadataConstrainedLogitsProcessor(LogitsProcessor):
    """
    FSM-driven LogitsProcessor that constrains generation to produce valid metadata.

    This processor enforces the following format:
    <think>
    bpm: [30-300]
    caption: [text without code blocks, ends with period + newline]
    duration: [10-600]
    keyscale: [A-G][#/♭]? [major/minor]
    language: [en/zh/ja/ko/es/fr/de/uk/ru/...]
    timesignature: [2/3/4/6]
    </think>

    It uses token masking (setting invalid token logits to -inf) to enforce constraints.
    For numeric fields, it uses early-blocking to prevent out-of-range values.
    For field transitions (e.g., end of numeric value), it compares P(newline) vs P(digit).
    For caption field, it blocks code blocks and newlines, and only transitions when
    the previous token was a period and newline has the highest probability.
    """

    def __init__(self, tokenizer: AutoTokenizer, enabled: bool=True, debug: bool=False, genres_vocab_path: Optional[str]=None, skip_genres: bool=True, max_duration: Optional[int]=None):
        self.tokenizer = tokenizer
        self.enabled = enabled
        self.debug = debug
        assert skip_genres, "genre generation is outside instrumental inference"
        self.skip_genres = True
        self.max_duration = max_duration if max_duration is not None else DURATION_MAX
        self.skip_caption = False
        self.skip_language = False
        self.caption: Optional[str] = None
        self.user_provided_metadata: Dict[str, Optional[str]] = {'bpm': None, 'caption': None, 'duration': None, 'keyscale': None, 'language': None, 'timesignature': None, 'genres': None}
        self.metadata_temperature: Optional[float] = None
        self.codes_temperature: Optional[float] = None
        self.target_duration: Optional[float] = None
        self.target_codes: Optional[int] = None
        self.codes_count: int = 0
        self.stop_at_reasoning: bool = False
        self.generation_phase: str = 'cot'
        self.state = FSMState.THINK_TAG
        self.position_in_state = 0
        self.accumulated_value = ''
        self.accumulated_token_ids: List[int] = []
        self.caption_after_newline = False
        self.caption_token_count = 0
        self.caption_ending = False
        self.pending_field_name = ''
        self.user_field_token_queue: List[int] = []
        self.current_user_field: Optional[str] = None
        self._precompute_tokens()
        self._char_to_tokens: Dict[str, set] = {}
        self._precompute_char_token_mapping()
        self.field_specs = {'bpm': {'min': BPM_MIN, 'max': BPM_MAX}, 'duration': {'min': DURATION_MIN, 'max': self.max_duration}, 'timesignature': {'valid_values': VALID_TIME_SIGNATURES}}
        self.valid_bpm_values = [str(v) for v in range(self.field_specs['bpm']['min'], self.field_specs['bpm']['max'] + 1)]
        self.valid_duration_values = [str(v) for v in range(self.field_specs['duration']['min'], self.field_specs['duration']['max'] + 1)]
        self.valid_timesig_values = [str(v) for v in self.field_specs['timesignature']['valid_values']]
        self.keyscale_prefix_tree = self._build_keyscale_prefix_tree()
        self.bpm_prefix_tree = self._build_numeric_prefix_tree(self.valid_bpm_values, context_prefix_for_matching='bpm:', context_prefix_for_tokenization='bpm: ')
        self.duration_prefix_tree = self._build_numeric_prefix_tree(self.valid_duration_values, context_prefix_for_matching='duration:', context_prefix_for_tokenization='duration: ')
        self.timesig_prefix_tree = self._build_numeric_prefix_tree(self.valid_timesig_values, context_prefix_for_matching='timesignature:', context_prefix_for_tokenization='timesignature: ')
        self.language_prefix_tree = self._build_language_prefix_tree()
        self.fixed_strings = {FSMState.THINK_TAG: '<think>', FSMState.NEWLINE_AFTER_THINK: '\n', FSMState.BPM_NAME: 'bpm:', FSMState.CAPTION_NAME: 'caption:', FSMState.DURATION_NAME: 'duration:', FSMState.GENRES_NAME: 'genres:', FSMState.KEYSCALE_NAME: 'keyscale:', FSMState.LANGUAGE_NAME: 'language:', FSMState.TIMESIG_NAME: 'timesignature:', FSMState.THINK_END_TAG: '</think>'}
        self._build_state_transitions()

    def _get_next_field_state(self, current_field: str) -> Optional[FSMState]:
        field_order = ['bpm', 'caption', 'duration', 'genres', 'keyscale', 'language', 'timesignature']
        field_to_state = {'bpm': FSMState.BPM_NAME, 'caption': FSMState.CAPTION_NAME, 'duration': FSMState.DURATION_NAME, 'genres': FSMState.GENRES_NAME, 'keyscale': FSMState.KEYSCALE_NAME, 'language': FSMState.LANGUAGE_NAME, 'timesignature': FSMState.TIMESIG_NAME}
        try:
            current_idx = field_order.index(current_field)
        except ValueError:
            return FSMState.THINK_END_TAG
        for i in range(current_idx + 1, len(field_order)):
            field = field_order[i]
            if field == 'genres' and self.skip_genres:
                continue
            if field == 'caption' and self.skip_caption:
                continue
            if field == 'language' and self.skip_language:
                continue
            return field_to_state[field]
        return FSMState.THINK_END_TAG

    def _build_state_transitions(self):
        self.next_state = {FSMState.THINK_TAG: FSMState.NEWLINE_AFTER_THINK, FSMState.NEWLINE_AFTER_THINK: FSMState.BPM_NAME, FSMState.THINK_END_TAG: FSMState.CODES_GENERATION, FSMState.CODES_GENERATION: FSMState.COMPLETED}
        self.next_state[FSMState.BPM_NAME] = FSMState.BPM_VALUE
        self.next_state[FSMState.BPM_VALUE] = self._get_next_field_state('bpm')
        if not self.skip_caption:
            self.next_state[FSMState.CAPTION_NAME] = FSMState.CAPTION_VALUE
            self.next_state[FSMState.CAPTION_VALUE] = self._get_next_field_state('caption')
        self.next_state[FSMState.DURATION_NAME] = FSMState.DURATION_VALUE
        self.next_state[FSMState.DURATION_VALUE] = self._get_next_field_state('duration')
        if not self.skip_genres:
            self.next_state[FSMState.GENRES_NAME] = FSMState.GENRES_VALUE
            self.next_state[FSMState.GENRES_VALUE] = self._get_next_field_state('genres')
        self.next_state[FSMState.KEYSCALE_NAME] = FSMState.KEYSCALE_VALUE
        self.next_state[FSMState.KEYSCALE_VALUE] = self._get_next_field_state('keyscale')
        if not self.skip_language:
            self.next_state[FSMState.LANGUAGE_NAME] = FSMState.LANGUAGE_VALUE
            self.next_state[FSMState.LANGUAGE_VALUE] = self._get_next_field_state('language')
        self.next_state[FSMState.TIMESIG_NAME] = FSMState.TIMESIG_VALUE
        self.next_state[FSMState.TIMESIG_VALUE] = FSMState.THINK_END_TAG

    def set_skip_genres(self, skip: bool):
        self.skip_genres = skip
        self._build_state_transitions()

    def set_skip_caption(self, skip: bool):
        self.skip_caption = skip
        self._build_state_transitions()

    def set_skip_language(self, skip: bool):
        self.skip_language = skip
        self._build_state_transitions()

    @staticmethod
    def postprocess_caption(caption: str) -> str:
        if not caption:
            return caption
        lines = caption.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                cleaned_lines.append(stripped)
        return ' '.join(cleaned_lines)

    def set_stop_at_reasoning(self, stop: bool):
        self.stop_at_reasoning = stop

    def set_generation_phase(self, phase: str):
        if phase not in ('cot', 'codes'):
            raise ValueError(f"Invalid generation phase: {phase!r}. Must be 'cot' or 'codes'")
        self.generation_phase = phase

    def set_user_metadata(self, metadata: Optional[Dict[str, Optional[str]]]=None):
        if metadata is None:
            metadata = {}
        for field in ['bpm', 'caption', 'duration', 'keyscale', 'language', 'timesignature', 'genres']:
            if field in metadata:
                self.user_provided_metadata[field] = metadata[field]
            else:
                self.user_provided_metadata[field] = None
        self._build_state_transitions()
        if self.debug:
            provided_fields = [k for (k, v) in self.user_provided_metadata.items() if v is not None]
            if provided_fields:
                logger.debug(f'User provided metadata fields: {provided_fields}')
            else:
                logger.debug('No user-provided metadata, all fields will be generated')

    def _precompute_tokens(self):
        self.digit_tokens = {}
        for d in range(10):
            tokens = self.tokenizer.encode(str(d), add_special_tokens=False)
            if tokens:
                self.digit_tokens[d] = tokens[-1]
        newline_tokens = self.tokenizer.encode('\n', add_special_tokens=False)
        self.newline_token = newline_tokens[-1] if newline_tokens else None
        self.note_tokens = {}
        for note in KEYSCALE_NOTES:
            tokens = self.tokenizer.encode(note, add_special_tokens=False)
            if tokens:
                self.note_tokens[note] = tokens[-1]
        self.sharp_tokens = []
        for s in ['#', '♯']:
            tokens = self.tokenizer.encode(s, add_special_tokens=False)
            if tokens:
                self.sharp_tokens.append(tokens[-1])
        self.flat_tokens = []
        for f in ['b', '♭']:
            tokens = self.tokenizer.encode(f, add_special_tokens=False)
            if tokens:
                self.flat_tokens.append(tokens[-1])
        space_tokens = self.tokenizer.encode(' ', add_special_tokens=False)
        self.space_token = space_tokens[-1] if space_tokens else None
        self.major_start_tokens = []
        self.minor_start_tokens = []
        for prefix in ['m', 'M']:
            tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
            if tokens:
                if prefix.lower() == 'm':
                    self.minor_start_tokens.append(tokens[-1])
                    self.major_start_tokens.append(tokens[-1])
        self.vocab_size = len(self.tokenizer)
        comma_tokens = self.tokenizer.encode(',', add_special_tokens=False)
        self.comma_token = comma_tokens[-1] if comma_tokens else None
        self.eos_token_id = self.tokenizer.eos_token_id
        period_tokens = self.tokenizer.encode('.', add_special_tokens=False)
        self.period_token = period_tokens[-1] if period_tokens else None
        backtick_tokens = self.tokenizer.encode('`', add_special_tokens=False)
        self.backtick_token = backtick_tokens[-1] if backtick_tokens else None
        self.valid_languages = VALID_LANGUAGES
        self.audio_code_token_ids: Set[int] = set()
        self._precompute_audio_code_tokens()
        self.audio_code_mask: Optional[torch.Tensor] = None
        self.non_audio_code_mask: Optional[torch.Tensor] = None
        self._build_audio_code_mask()
        self.valid_keyscales = VALID_KEYSCALES.copy()

    def _precompute_audio_code_tokens(self):
        import re
        audio_code_pattern = re.compile('^<\\|audio_code_(\\d+)\\|>$')
        invalid_tokens_count = 0
        for token_id in range(self.vocab_size):
            try:
                token_text = self.tokenizer.decode([token_id])
                match = audio_code_pattern.match(token_text)
                if match:
                    code_value = int(match.group(1))
                    if 0 <= code_value <= MAX_AUDIO_CODE:
                        self.audio_code_token_ids.add(token_id)
                    else:
                        invalid_tokens_count += 1
                        if self.debug:
                            logger.debug(f'Skipping audio code token {token_id} with invalid code value {code_value} (max: {MAX_AUDIO_CODE})')
            except Exception:
                continue
        if invalid_tokens_count > 0:
            logger.debug(f'Found {invalid_tokens_count} audio code tokens with values outside valid range [0, {MAX_AUDIO_CODE}]')
        if len(self.audio_code_token_ids) == 0:
            logger.warning(f'No valid audio code tokens found in vocabulary (range [0, {MAX_AUDIO_CODE}]). Code generation may fail.')
        elif self.debug:
            logger.debug(f'Found {len(self.audio_code_token_ids)} valid audio code tokens (range [0, {MAX_AUDIO_CODE}])')

    def _extract_code_from_token(self, token_id: int) -> Optional[int]:
        import re
        audio_code_pattern = re.compile('^<\\|audio_code_(\\d+)\\|>$')
        try:
            token_text = self.tokenizer.decode([token_id])
            match = audio_code_pattern.match(token_text)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return None

    def _build_audio_code_mask(self):
        if not self.audio_code_token_ids:
            self.audio_code_mask = None
            self.non_audio_code_mask = None
            return
        mask = torch.zeros(1, self.vocab_size, dtype=torch.float32)
        audio_code_indices = list(self.audio_code_token_ids)
        mask[0, audio_code_indices] = float('-inf')
        self.audio_code_mask = mask
        inverse_mask = torch.full((1, self.vocab_size), float('-inf'), dtype=torch.float32)
        inverse_mask[0, audio_code_indices] = 0
        if self.eos_token_id is not None:
            inverse_mask[0, self.eos_token_id] = 0
        self.non_audio_code_mask = inverse_mask
        if self.debug:
            logger.debug(f'Built audio code masks for {len(self.audio_code_token_ids)} tokens')

    def _apply_whitelist_inplace(self, scores: torch.Tensor, allowed_tokens: List[int]) -> None:
        if not allowed_tokens:
            scores.fill_(float('-inf'))
            return
        allowed_indices = torch.tensor(allowed_tokens, device=scores.device, dtype=torch.long)
        saved_values = scores[0, allowed_indices].clone()
        scores.fill_(float('-inf'))
        scores[0, allowed_indices] = saved_values

    def _build_keyscale_prefix_tree(self) -> Dict[Tuple[int, ...], Set[int]]:
        prefix_to_tokens: Dict[Tuple[int, ...], Set[int]] = {}
        context_prefix_for_matching = 'keyscale:'
        context_prefix_for_tokenization = 'keyscale: '
        context_token_ids = self.tokenizer.encode(context_prefix_for_matching, add_special_tokens=False)
        if self.debug:
            context_tokens_str = [self.tokenizer.decode([t]) for t in context_token_ids]
            logger.debug(f"Context for matching 'keyscale:' tokenizes to {context_token_ids} -> {context_tokens_str}")
        for keyscale in self.valid_keyscales:
            full_text = context_prefix_for_tokenization + keyscale
            full_token_ids = self.tokenizer.encode(full_text, add_special_tokens=False)
            context_end_idx = None
            if len(full_token_ids) >= len(context_token_ids):
                if full_token_ids[:len(context_token_ids)] == context_token_ids:
                    context_end_idx = len(context_token_ids)
            if context_end_idx is None:
                if self.debug:
                    logger.warning(f"Could not find context prefix in full tokenization of '{full_text}', skipping")
                continue
            keyscale_token_ids = full_token_ids[context_end_idx:]
            if not keyscale_token_ids:
                if self.debug:
                    logger.warning(f"No tokens extracted for keyscale '{keyscale}', skipping")
                continue
            first_token_id = keyscale_token_ids[0]
            first_token_str = self.tokenizer.decode([first_token_id])
            first_char = first_token_str.lstrip()[0].upper() if first_token_str.lstrip() else ''
            if first_char not in 'ABCDEFG':
                if self.debug:
                    logger.debug(f"Skipping keyscale '{keyscale}': first token is '{first_token_str}' (id={first_token_id}), not a note")
                continue
            for i in range(len(keyscale_token_ids) + 1):
                token_prefix = tuple(keyscale_token_ids[:i])
                if token_prefix not in prefix_to_tokens:
                    prefix_to_tokens[token_prefix] = set()
                if i < len(keyscale_token_ids):
                    next_token_id = keyscale_token_ids[i]
                    prefix_to_tokens[token_prefix].add(next_token_id)
                elif self.newline_token:
                    prefix_to_tokens[token_prefix].add(self.newline_token)
        if self.debug:
            logger.debug(f'Built keyscale prefix tree with {len(prefix_to_tokens)} token sequence prefixes')
            empty_prefix = tuple()
            if empty_prefix in prefix_to_tokens:
                first_tokens = prefix_to_tokens[empty_prefix]
                decoded_first = [(t, repr(self.tokenizer.decode([t]))) for t in sorted(first_tokens)]
                logger.debug(f'First tokens allowed (empty prefix): {decoded_first}')
        return prefix_to_tokens

    def _build_numeric_prefix_tree(self, valid_values: List[str], context_prefix_for_matching: str='', context_prefix_for_tokenization: str='') -> Dict[Tuple[int, ...], Set[int]]:
        prefix_to_tokens: Dict[Tuple[int, ...], Set[int]] = {}
        context_token_ids = self.tokenizer.encode(context_prefix_for_matching, add_special_tokens=False) if context_prefix_for_matching else []
        for value_str in valid_values:
            full_text = context_prefix_for_tokenization + value_str
            token_ids = self.tokenizer.encode(full_text, add_special_tokens=False)
            context_end_idx = None
            if len(token_ids) >= len(context_token_ids):
                if token_ids[:len(context_token_ids)] == context_token_ids:
                    context_end_idx = len(context_token_ids)
            if context_end_idx is None:
                if self.debug:
                    logger.warning(f"Could not find context prefix in full tokenization of '{full_text}', skipping")
                continue
            value_token_ids = token_ids[context_end_idx:]
            for i in range(len(value_token_ids) + 1):
                token_prefix = tuple(value_token_ids[:i])
                if token_prefix not in prefix_to_tokens:
                    prefix_to_tokens[token_prefix] = set()
                if i < len(value_token_ids):
                    next_token_id = value_token_ids[i]
                    prefix_to_tokens[token_prefix].add(next_token_id)
                elif self.newline_token:
                    prefix_to_tokens[token_prefix].add(self.newline_token)
        return prefix_to_tokens

    def _build_language_prefix_tree(self) -> Dict[Tuple[int, ...], Set[int]]:
        prefix_to_tokens: Dict[Tuple[int, ...], Set[int]] = {}
        context_prefix_for_matching = 'language:'
        context_prefix_for_tokenization = 'language: '
        context_token_ids = self.tokenizer.encode(context_prefix_for_matching, add_special_tokens=False)
        if self.debug:
            context_tokens_str = [self.tokenizer.decode([t]) for t in context_token_ids]
            logger.debug(f"Context for matching 'language:' tokenizes to {context_token_ids} -> {context_tokens_str}")
        for lang in self.valid_languages:
            full_text = context_prefix_for_tokenization + lang
            full_token_ids = self.tokenizer.encode(full_text, add_special_tokens=False)
            context_end_idx = None
            if len(full_token_ids) >= len(context_token_ids):
                if full_token_ids[:len(context_token_ids)] == context_token_ids:
                    context_end_idx = len(context_token_ids)
            if context_end_idx is None:
                if self.debug:
                    logger.warning(f"Could not find context prefix in full tokenization of '{full_text}', skipping")
                continue
            lang_token_ids = full_token_ids[context_end_idx:]
            if not lang_token_ids:
                if self.debug:
                    logger.warning(f"No tokens extracted for language '{lang}', skipping")
                continue
            for i in range(len(lang_token_ids) + 1):
                token_prefix = tuple(lang_token_ids[:i])
                if token_prefix not in prefix_to_tokens:
                    prefix_to_tokens[token_prefix] = set()
                if i < len(lang_token_ids):
                    next_token_id = lang_token_ids[i]
                    prefix_to_tokens[token_prefix].add(next_token_id)
                elif self.newline_token:
                    prefix_to_tokens[token_prefix].add(self.newline_token)
        if self.debug:
            logger.debug(f'Built language prefix tree with {len(prefix_to_tokens)} token sequence prefixes')
            empty_prefix = tuple()
            if empty_prefix in prefix_to_tokens:
                first_tokens = prefix_to_tokens[empty_prefix]
                decoded_first = [(t, repr(self.tokenizer.decode([t]))) for t in sorted(first_tokens)]
                logger.debug(f'First tokens allowed for language (empty prefix): {decoded_first}')
        return prefix_to_tokens

    def _precompute_char_token_mapping(self):
        self._char_to_tokens: Dict[str, set] = {}
        self._token_to_text: Dict[int, str] = {}
        for token_id in range(self.vocab_size):
            try:
                text = self.tokenizer.decode([token_id])
                if not text:
                    continue
                text_lower = text.lower()
                if text_lower.strip():
                    normalized_text = text_lower.rstrip()
                else:
                    normalized_text = ' '
                self._token_to_text[token_id] = normalized_text
                first_char = text[0].lower()
                if first_char not in self._char_to_tokens:
                    self._char_to_tokens[first_char] = set()
                self._char_to_tokens[first_char].add(token_id)
                stripped_text = text.lstrip()
                if stripped_text and stripped_text != text:
                    first_nonspace_char = stripped_text[0].lower()
                    if first_nonspace_char not in self._char_to_tokens:
                        self._char_to_tokens[first_nonspace_char] = set()
                    self._char_to_tokens[first_nonspace_char].add(token_id)
            except Exception:
                continue
        if self.debug:
            logger.debug(f'Precomputed char->token mapping for {len(self._char_to_tokens)} unique characters')

    def reset(self):
        self.state = FSMState.THINK_TAG
        self.position_in_state = 0
        self.accumulated_value = ''
        self.accumulated_token_ids = []
        self.codes_count = 0
        self.user_field_token_queue = []
        self.current_user_field = None
        self.caption_after_newline = False
        self.caption_token_count = 0
        self.caption_ending = False
        self.pending_field_name = ''

    def set_target_duration(self, duration: Optional[float]):
        self.target_duration = duration
        if duration is not None and duration > 0:
            self.target_codes = int(duration * 5)
            if self.debug:
                logger.debug(f'Set target duration: {duration}s -> {self.target_codes} codes')
        else:
            self.target_codes = None
            if self.debug:
                logger.debug('Target duration cleared, no duration constraint')

    def set_max_duration(self, max_duration: int):
        if max_duration == self.max_duration:
            return
        old_max = self.max_duration
        self.max_duration = max_duration
        self.field_specs['duration']['max'] = max_duration
        self.valid_duration_values = [str(v) for v in range(self.field_specs['duration']['min'], self.field_specs['duration']['max'] + 1)]
        self.duration_prefix_tree = self._build_numeric_prefix_tree(self.valid_duration_values, context_prefix_for_matching='duration:', context_prefix_for_tokenization='duration: ')
        if self.debug:
            logger.debug(f'Updated max duration: {old_max}s -> {max_duration}s, rebuilt prefix tree with {len(self.valid_duration_values)} values')

    def _get_allowed_tokens_for_fixed_string(self, fixed_str: str) -> List[int]:
        remaining = fixed_str[self.position_in_state:]
        if not remaining:
            return []
        if self.debug:
            logger.debug(f'_get_allowed_tokens_for_fixed_string: fixed_str={repr(fixed_str)}, position_in_state={self.position_in_state}, remaining={repr(remaining)}')
        best_token = None
        best_prefix_len = 0
        for end in range(len(remaining), 0, -1):
            prefix = remaining[:end]
            tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
            if tokens and len(tokens) == 1:
                best_token = tokens[0]
                best_prefix_len = end
                if self.debug:
                    logger.debug(f'Found single-token match: prefix={repr(prefix)}, token_id={best_token}, token_text={repr(self.tokenizer.decode([best_token]))}')
                break
        if best_token is not None:
            return [best_token]
        allowed_tokens = {}
        for end in range(1, min(len(remaining) + 1, 20)):
            prefix = remaining[:end]
            tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
            if tokens:
                first_token = tokens[0]
                decoded_token = self.tokenizer.decode([first_token])
                normalized_prefix = prefix.lstrip().lower()
                normalized_decoded = decoded_token.lstrip().lower()
                if normalized_decoded.startswith(normalized_prefix) or normalized_prefix.startswith(normalized_decoded):
                    if first_token not in allowed_tokens or end > allowed_tokens[first_token]:
                        allowed_tokens[first_token] = end
        sorted_tokens = sorted(allowed_tokens.items(), key=lambda x: x[1], reverse=True)
        result = [token for (token, _) in sorted_tokens] if sorted_tokens else []
        if self.debug:
            logger.debug(f'Fallback: returning {len(result)} tokens: {[(t, repr(self.tokenizer.decode([t]))) for t in result[:5]]}')
            if result:
                logger.debug(f'Fixed string: {repr(fixed_str)}, position: {self.position_in_state}, remaining: {repr(remaining)}')
        return result

    def _get_allowed_digit_tokens(self, min_val: int, max_val: int) -> List[int]:
        if not self.accumulated_value:
            allowed_digits = set()
            for v in range(min_val, max_val + 1):
                allowed_digits.add(int(str(v)[0]))
            return [self.digit_tokens[d] for d in allowed_digits if d in self.digit_tokens]
        current = int(self.accumulated_value)
        allowed = []
        for d in range(10):
            new_value = int(self.accumulated_value + str(d))
            if new_value > max_val:
                continue
            if new_value >= min_val:
                allowed.append(d)
            elif new_value * 10 <= max_val:
                allowed.append(d)
        return [self.digit_tokens[d] for d in allowed if d in self.digit_tokens]

    def _get_allowed_numeric_tokens(self, prefix_tree: Dict[Tuple[int, ...], Set[int]]) -> List[int]:
        token_prefix = tuple(self.accumulated_token_ids)
        if token_prefix in prefix_tree:
            return list(prefix_tree[token_prefix])
        return []

    def _should_end_numeric_field(self, logits: torch.Tensor, min_val: int, max_val: int) -> bool:
        if not self.accumulated_value:
            return False
        current = int(self.accumulated_value)
        if current < min_val or current > max_val:
            return False
        probs = torch.softmax(logits, dim=-1)
        newline_prob = probs[0, self.newline_token].item() if self.newline_token else 0
        allowed_digits = self._get_allowed_digit_tokens(min_val, max_val)
        if not allowed_digits:
            return True
        max_digit_prob = max((probs[0, t].item() for t in allowed_digits))
        if self.debug:
            logger.debug(f'Numeric field decision: newline_prob={newline_prob:.4f}, max_digit_prob={max_digit_prob:.4f}')
        return newline_prob > max_digit_prob

    def _should_end_text_field(self, logits: torch.Tensor) -> bool:
        if not self.accumulated_value.strip():
            return False
        probs = torch.softmax(logits, dim=-1)
        newline_prob = probs[0, self.newline_token].item() if self.newline_token else 0
        masked_probs = probs.clone()
        if self.newline_token:
            masked_probs[0, self.newline_token] = 0
        max_other_prob = masked_probs[0].max().item()
        return newline_prob > max_other_prob

    def _get_allowed_keyscale_tokens(self) -> List[int]:
        token_prefix = tuple(self.accumulated_token_ids)
        if token_prefix in self.keyscale_prefix_tree:
            return list(self.keyscale_prefix_tree[token_prefix])
        return []

    def _is_keyscale_complete(self) -> bool:
        token_prefix = tuple(self.accumulated_token_ids)
        if token_prefix in self.keyscale_prefix_tree:
            return self.newline_token in self.keyscale_prefix_tree[token_prefix]
        return False

    def _get_allowed_language_tokens(self) -> List[int]:
        token_prefix = tuple(self.accumulated_token_ids)
        if token_prefix in self.language_prefix_tree:
            return list(self.language_prefix_tree[token_prefix])
        return []

    def _get_allowed_timesig_tokens(self) -> List[int]:
        token_prefix = tuple(self.accumulated_token_ids)
        if token_prefix in self.timesig_prefix_tree:
            return list(self.timesig_prefix_tree[token_prefix])
        return []

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        if not self.enabled:
            return self._apply_temperature_scaling(scores)
        if self.state == FSMState.COMPLETED:
            return self._apply_temperature_scaling(scores)
        if self.generation_phase == 'codes' and self.state == FSMState.THINK_TAG:
            if self._input_contains_think_end_tag(input_ids):
                self.state = FSMState.CODES_GENERATION
                self.codes_count = 0
                if self.debug:
                    logger.debug('Codes phase: detected </think> in input, skipping to CODES_GENERATION')
        if self.state == FSMState.CODES_GENERATION:
            if self.non_audio_code_mask is not None:
                if self.non_audio_code_mask.device != scores.device or self.non_audio_code_mask.dtype != scores.dtype:
                    self.non_audio_code_mask = self.non_audio_code_mask.to(device=scores.device, dtype=scores.dtype)
                scores = scores + self.non_audio_code_mask
            if self.target_codes is not None and self.eos_token_id is not None:
                if self.codes_count < self.target_codes:
                    scores[:, self.eos_token_id] = float('-inf')
                    if self.debug:
                        logger.debug(f'Codes generation: {self.codes_count}/{self.target_codes}, blocking EOS')
                else:
                    eos_scores = scores[:, self.eos_token_id].clone()
                    scores.fill_(float('-inf'))
                    scores[:, self.eos_token_id] = eos_scores
                    if self.debug:
                        logger.debug(f'Codes generation: {self.codes_count}/{self.target_codes}, forcing EOS')
            return self._apply_temperature_scaling(scores)
        batch_size = scores.shape[0]
        for b in range(batch_size):
            result = self._process_single_sequence(input_ids[b], scores[b:b + 1])
            scores[b] = result[0]
        return self._apply_temperature_scaling(scores)

    def _input_contains_think_end_tag(self, input_ids: torch.LongTensor) -> bool:
        think_end_tokens = self.tokenizer.encode('</think>', add_special_tokens=False)
        if not think_end_tokens:
            return False
        for b in range(input_ids.shape[0]):
            seq = input_ids[b].tolist()
            for i in range(len(seq) - len(think_end_tokens) + 1):
                if seq[i:i + len(think_end_tokens)] == think_end_tokens:
                    return True
        return False

    def _apply_temperature_scaling(self, scores: torch.FloatTensor) -> torch.FloatTensor:
        if self.state == FSMState.CODES_GENERATION or self.state == FSMState.COMPLETED:
            temperature = self.codes_temperature
        else:
            temperature = self.metadata_temperature
        if temperature is None:
            return scores
        if temperature <= 0:
            temperature = 1e-06
        return scores / temperature

    def _get_user_provided_field_tokens(self, field_name: str) -> Optional[List[int]]:
        value = self.user_provided_metadata.get(field_name)
        if value is None:
            return None
        field_to_prefix = {'bpm': 'bpm: ', 'caption': 'caption: ', 'duration': 'duration: ', 'keyscale': 'keyscale: ', 'language': 'language: ', 'timesignature': 'timesignature: ', 'genres': 'genres: '}
        prefix = field_to_prefix[field_name]
        full_text = f'{prefix}{value}\n'
        tokens = self.tokenizer.encode(full_text, add_special_tokens=False)
        prefix_for_matching = field_name + ':'
        prefix_tokens = self.tokenizer.encode(prefix_for_matching, add_special_tokens=False)
        if len(tokens) >= len(prefix_tokens) and tokens[:len(prefix_tokens)] == prefix_tokens:
            return tokens[len(prefix_tokens):]
        else:
            if self.debug:
                logger.warning(f'Could not match prefix tokens for field {field_name}, using all tokens')
            return tokens

    def _process_single_sequence(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        if self.user_field_token_queue:
            next_token = self.user_field_token_queue[0]
            self._apply_whitelist_inplace(scores, [next_token])
            return scores
        if self.state in self.fixed_strings:
            fixed_str = self.fixed_strings[self.state]
            allowed = self._get_allowed_tokens_for_fixed_string(fixed_str)
            if allowed:
                if self.state == FSMState.THINK_END_TAG and self.stop_at_reasoning:
                    remaining_chars = len(fixed_str) - self.position_in_state
                    if remaining_chars <= 10:
                        if self.eos_token_id is not None:
                            self._apply_whitelist_inplace(scores, [self.eos_token_id])
                            if self.debug:
                                logger.debug(f'stop_at_reasoning=True: forcing EOS near end of </think> tag (remaining: {remaining_chars} chars)')
                            return scores
                self._apply_whitelist_inplace(scores, allowed)
            else:
                if self.state == FSMState.THINK_END_TAG and self.stop_at_reasoning:
                    if self.eos_token_id is not None:
                        self._apply_whitelist_inplace(scores, [self.eos_token_id])
                        if self.debug:
                            logger.debug(f'stop_at_reasoning=True: forcing EOS after completing </think> tag')
                        return scores
                old_state = self.state
                self._transition_to_next_state()
                if self.state in self.fixed_strings:
                    if self.debug:
                        logger.warning(f'State transition from {old_state.name} to {self.state.name} still in fixed_strings, avoiding recursion')
                    return scores
                scores.zero_()
                return self._process_single_sequence(input_ids, scores)
        elif self.state == FSMState.BPM_VALUE:
            if self.user_provided_metadata['bpm'] is not None and (not self.user_field_token_queue) and (not self.accumulated_token_ids):
                value = self.user_provided_metadata['bpm']
                value_text = f' {value}\n'
                value_tokens = self.tokenizer.encode(value_text, add_special_tokens=False)
                if value_tokens:
                    self.user_field_token_queue = value_tokens
                    self.current_user_field = 'bpm'
                    self._apply_whitelist_inplace(scores, [value_tokens[0]])
                    return scores
            allowed = self._get_allowed_numeric_tokens(self.bpm_prefix_tree)
            token_prefix = tuple(self.accumulated_token_ids)
            if token_prefix in self.bpm_prefix_tree and self.newline_token in self.bpm_prefix_tree[token_prefix]:
                allowed = allowed + [self.newline_token]
            self._apply_whitelist_inplace(scores, allowed)
        elif self.state == FSMState.CAPTION_VALUE:
            if self.user_provided_metadata['caption'] is not None and (not self.user_field_token_queue) and (not self.accumulated_value):
                value = self.user_provided_metadata['caption']
                value_text = f' {value}\n'
                value_tokens = self.tokenizer.encode(value_text, add_special_tokens=False)
                if value_tokens:
                    self.user_field_token_queue = value_tokens
                    self.current_user_field = 'caption'
                    self._apply_whitelist_inplace(scores, [value_tokens[0]])
                    return scores
            if self.caption_after_newline:
                top_token_id = torch.argmax(scores[0]).item()
                top_token_text = self.tokenizer.decode([top_token_id])
                if len(top_token_text) > 0 and top_token_text[0] not in ' \t':
                    self.caption_after_newline = False
                    self.caption_ending = True
                    self.pending_field_name = ''
                    return scores
                else:
                    self.caption_after_newline = False
            if self.caption_ending:
                return scores
            if self.backtick_token is not None:
                scores[0, self.backtick_token] = float('-inf')
            if self.audio_code_mask is not None:
                if self.audio_code_mask.device != scores.device or self.audio_code_mask.dtype != scores.dtype:
                    self.audio_code_mask = self.audio_code_mask.to(device=scores.device, dtype=scores.dtype)
                scores = scores + self.audio_code_mask
            if self.caption_token_count >= 512:
                if self.newline_token is not None:
                    self._apply_whitelist_inplace(scores, [self.newline_token])
                    return scores
            return scores
        elif self.state == FSMState.DURATION_VALUE:
            if self.user_provided_metadata['duration'] is not None and (not self.user_field_token_queue) and (not self.accumulated_token_ids):
                value = self.user_provided_metadata['duration']
                value_text = f' {value}\n'
                value_tokens = self.tokenizer.encode(value_text, add_special_tokens=False)
                if value_tokens:
                    self.user_field_token_queue = value_tokens
                    self.current_user_field = 'duration'
                    self._apply_whitelist_inplace(scores, [value_tokens[0]])
                    return scores
            if self.target_duration is not None:
                target_str = str(int(self.target_duration))
                current_pos = len(self.accumulated_value)
                if current_pos < len(target_str):
                    next_digit = int(target_str[current_pos])
                    if next_digit in self.digit_tokens:
                        self._apply_whitelist_inplace(scores, [self.digit_tokens[next_digit]])
                elif self.newline_token:
                    self._apply_whitelist_inplace(scores, [self.newline_token])
            else:
                allowed = self._get_allowed_numeric_tokens(self.duration_prefix_tree)
                token_prefix = tuple(self.accumulated_token_ids)
                if token_prefix in self.duration_prefix_tree and self.newline_token in self.duration_prefix_tree[token_prefix]:
                    allowed = allowed + [self.newline_token]
                self._apply_whitelist_inplace(scores, allowed)
        elif self.state == FSMState.KEYSCALE_VALUE:
            if self.user_provided_metadata['keyscale'] is not None and (not self.user_field_token_queue) and (not self.accumulated_token_ids):
                value = self.user_provided_metadata['keyscale']
                value_text = f' {value}\n'
                value_tokens = self.tokenizer.encode(value_text, add_special_tokens=False)
                if value_tokens:
                    self.user_field_token_queue = value_tokens
                    self.current_user_field = 'keyscale'
                    self._apply_whitelist_inplace(scores, [value_tokens[0]])
                    return scores
            token_prefix = tuple(self.accumulated_token_ids)
            if token_prefix in self.keyscale_prefix_tree and self.newline_token in self.keyscale_prefix_tree[token_prefix]:
                if self.newline_token:
                    self._apply_whitelist_inplace(scores, [self.newline_token])
            else:
                allowed = self._get_allowed_keyscale_tokens()
                if allowed:
                    self._apply_whitelist_inplace(scores, allowed)
                elif self.newline_token:
                    self._apply_whitelist_inplace(scores, [self.newline_token])
        elif self.state == FSMState.LANGUAGE_VALUE:
            if self.user_provided_metadata['language'] is not None and (not self.user_field_token_queue) and (not self.accumulated_token_ids):
                value = self.user_provided_metadata['language']
                value_text = f' {value}\n'
                value_tokens = self.tokenizer.encode(value_text, add_special_tokens=False)
                if value_tokens:
                    self.user_field_token_queue = value_tokens
                    self.current_user_field = 'language'
                    self._apply_whitelist_inplace(scores, [value_tokens[0]])
                    return scores
            if not self.accumulated_token_ids:
                empty_prefix = tuple()
                if empty_prefix in self.language_prefix_tree:
                    candidate_tokens = list(self.language_prefix_tree[empty_prefix])
                    if candidate_tokens:
                        candidate_indices = torch.tensor(candidate_tokens, device=scores.device, dtype=torch.long)
                        candidate_scores = scores[0, candidate_indices]
                        best_idx = torch.argmax(candidate_scores).item()
                        top_token_id = candidate_tokens[best_idx]
                        self._apply_whitelist_inplace(scores, [top_token_id])
                        if self.debug:
                            top_token_text = self.tokenizer.decode([top_token_id])
                            logger.debug(f'Language field: selected top-1 token {top_token_id} ({repr(top_token_text)}) from {len(candidate_tokens)} candidates')
                    elif self.newline_token:
                        self._apply_whitelist_inplace(scores, [self.newline_token])
                elif self.newline_token:
                    self._apply_whitelist_inplace(scores, [self.newline_token])
            else:
                token_prefix = tuple(self.accumulated_token_ids)
                if token_prefix in self.language_prefix_tree and self.newline_token in self.language_prefix_tree[token_prefix]:
                    if self.newline_token:
                        self._apply_whitelist_inplace(scores, [self.newline_token])
                else:
                    allowed = self._get_allowed_language_tokens()
                    if allowed:
                        self._apply_whitelist_inplace(scores, allowed)
                    elif self.newline_token:
                        self._apply_whitelist_inplace(scores, [self.newline_token])
        elif self.state == FSMState.TIMESIG_VALUE:
            if self.user_provided_metadata['timesignature'] is not None and (not self.user_field_token_queue) and (not self.accumulated_token_ids):
                value = self.user_provided_metadata['timesignature']
                value_text = f' {value}\n'
                value_tokens = self.tokenizer.encode(value_text, add_special_tokens=False)
                if value_tokens:
                    self.user_field_token_queue = value_tokens
                    self.current_user_field = 'timesignature'
                    self._apply_whitelist_inplace(scores, [value_tokens[0]])
                    return scores
            token_prefix = tuple(self.accumulated_token_ids)
            if token_prefix in self.timesig_prefix_tree and self.newline_token in self.timesig_prefix_tree[token_prefix]:
                if self.newline_token:
                    self._apply_whitelist_inplace(scores, [self.newline_token])
            else:
                allowed = self._get_allowed_timesig_tokens()
                self._apply_whitelist_inplace(scores, allowed)
        return scores

    def _transition_to_next_state(self):
        if self.state in self.next_state:
            old_state = self.state
            next_state = self.next_state[self.state]
            self.state = next_state
            self.position_in_state = 0
            self.accumulated_value = ''
            self.accumulated_token_ids = []
            self.caption_after_newline = False
            self.caption_token_count = 0
            self.caption_ending = False
            self.pending_field_name = ''
            if self.debug:
                logger.debug(f'FSM transition: {old_state.name} -> {self.state.name}')

    def update_state(self, generated_token_id: int):
        if not self.enabled:
            return
        if self.state == FSMState.COMPLETED:
            return
        if self.state == FSMState.CODES_GENERATION:
            self.codes_count += 1
            if self.debug and self.target_codes is not None:
                logger.debug(f'Codes count: {self.codes_count}/{self.target_codes}')
            return
        if self.user_field_token_queue:
            expected_token = self.user_field_token_queue[0]
            if generated_token_id != expected_token:
                if self.debug:
                    logger.warning(f'Expected token {expected_token} but got {generated_token_id} for user-provided field {self.current_user_field}')
            self.user_field_token_queue.pop(0)
            if not self.user_field_token_queue:
                if self.debug:
                    logger.debug(f'Completed injection of user-provided field: {self.current_user_field}')
                field_name = self.current_user_field
                self.current_user_field = None
                next_state = self._get_next_field_state(field_name)
                if next_state:
                    old_state = self.state
                    self.state = next_state
                    self.position_in_state = 0
                    self.accumulated_value = ''
                    self.accumulated_token_ids = []
                    if self.debug:
                        logger.debug(f'FSM transition (after user field injection): {old_state.name} -> {self.state.name}')
                else:
                    self._transition_to_next_state()
            return
        token_str = self.tokenizer.decode([generated_token_id])
        if self.debug:
            logger.debug(f'Generated token: {repr(token_str)} (id={generated_token_id}), state={self.state.name}')
        if self.state in self.fixed_strings:
            fixed_str = self.fixed_strings[self.state]
            self.position_in_state += len(token_str)
            if self.position_in_state >= len(fixed_str):
                if self.state == FSMState.THINK_END_TAG and self.stop_at_reasoning:
                    old_state = self.state
                    self.state = FSMState.COMPLETED
                    self.position_in_state = 0
                    self.accumulated_value = ''
                    self.accumulated_token_ids = []
                    if self.debug:
                        logger.debug(f'FSM transition (stop_at_reasoning): {old_state.name} -> {self.state.name}')
                else:
                    self._transition_to_next_state()
        elif self.state in [FSMState.BPM_VALUE, FSMState.DURATION_VALUE, FSMState.TIMESIG_VALUE]:
            if generated_token_id == self.newline_token:
                old_state = self.state
                self._transition_to_next_state()
                if self.state in self.fixed_strings:
                    return
            else:
                self.accumulated_token_ids.append(generated_token_id)
                if token_str.strip().isdigit():
                    self.accumulated_value += token_str.strip()
        elif self.state == FSMState.CAPTION_VALUE:
            self.caption_token_count += 1
            self.accumulated_value += token_str
            if '\n' in token_str:
                self.caption_after_newline = True
            else:
                self.caption_after_newline = False
            if self.caption_ending:
                self.pending_field_name += token_str
                if ':' in token_str or token_str.strip() == ':':
                    field_name_full = self.pending_field_name.strip()
                    field_name = field_name_full.rstrip(':').strip().lower()
                    if self.debug:
                        logger.debug(f'Detected field name after caption: {repr(field_name)}')
                    field_name_to_value_state = {'duration': FSMState.DURATION_VALUE, 'genres': FSMState.GENRES_VALUE, 'keyscale': FSMState.KEYSCALE_VALUE, 'language': FSMState.LANGUAGE_VALUE, 'timesignature': FSMState.TIMESIG_VALUE}
                    if field_name in field_name_to_value_state:
                        old_state = self.state
                        self.state = field_name_to_value_state[field_name]
                        self.position_in_state = 0
                        self.accumulated_value = ''
                        self.accumulated_token_ids = []
                        self.caption_ending = False
                        self.pending_field_name = ''
                        if self.debug:
                            logger.debug(f'FSM transition (caption ending): {old_state.name} -> {self.state.name}')
                    else:
                        if self.debug:
                            logger.warning(f'Unknown field name after caption: {repr(field_name)}, forcing transition')
                        self.caption_ending = False
                        self.pending_field_name = ''
                        self._transition_to_next_state()
        elif self.state == FSMState.KEYSCALE_VALUE:
            if generated_token_id == self.newline_token:
                self._transition_to_next_state()
                if self.state in self.fixed_strings:
                    return
            else:
                self.accumulated_token_ids.append(generated_token_id)
                self.accumulated_value += token_str
        elif self.state == FSMState.LANGUAGE_VALUE:
            if generated_token_id == self.newline_token:
                self._transition_to_next_state()
                if self.state in self.fixed_strings:
                    return
            else:
                self.accumulated_token_ids.append(generated_token_id)
                self.accumulated_value += token_str
