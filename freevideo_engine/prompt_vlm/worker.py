"""One rewrite or ask job per process: model, CUDA context and private commit die together."""
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

PRESTART_ENV = 'FREEVIDEO_PROMPT_PRESTART_WORKER'
PRESTART_TIMEOUT_ENV = 'FREEVIDEO_PROMPT_PRESTART_TIMEOUT_SECONDS'


def process_memory(pid=None):
    """Import footprint only; Windows residency and commit are distinct."""
    import psutil
    pid = os.getpid() if pid is None else pid
    if os.name == 'nt':
        from ..win32 import process_memory as counters
        row = counters(pid)
        return dict(rss_bytes=row['rss_bytes'], private_bytes=row['private_commit_bytes'],
                    private_working_set_bytes=row['private_working_set_bytes'],
                    private_commit_bytes=row['private_commit_bytes'], metric='windows_private')
    process = psutil.Process(pid)
    rss = process.memory_info().rss
    try:
        private = process.memory_full_info().uss
        metric = 'uss'
    except (psutil.Error, OSError, AttributeError):
        private, metric = rss, 'rss_fallback'
    return dict(rss_bytes=rss, private_bytes=private, metric=metric)


def request_memory(stats):
    """Credit this waiting worker, rather than its stale launch-time headroom."""
    import psutil
    own = process_memory()
    physical = psutil.virtual_memory().available
    stats.update(request_memory_metric=own['metric'], request_memory_physical_available_bytes=physical,
                 request_memory_rss_bytes=own['rss_bytes'], request_memory_private_bytes=own['private_bytes'])
    if os.name == 'nt':
        from ..win32 import memory_status
        commit = memory_status()['commit_available_bytes']
        physical_credit = physical + own['private_working_set_bytes']
        commit_credit = commit + own['private_commit_bytes']
        stats.update(request_memory_commit_available_bytes=commit,
                     request_memory_private_working_set_bytes=own['private_working_set_bytes'],
                     request_memory_private_commit_bytes=own['private_commit_bytes'],
                     request_memory_physical_with_worker_bytes=physical_credit,
                     request_memory_commit_with_worker_bytes=commit_credit)
        available = min(physical_credit, commit_credit)
    else:
        available = physical + own['private_bytes']
    stats['request_memory_with_worker_bytes'] = available
    return available


def import_dependencies():
    # Resolve lazy Transformers classes here, without loading config or weights.
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    import transformers
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
    return torch, transformers


def wait_request(timeout):
    """A pipe read in a daemon thread works on Windows as well as POSIX.

    On timeout the process exits, dropping the blocked reader and its pipe.
    EOF with no request is an ordinary idle exit.
    """
    import queue
    import threading
    received = queue.Queue()
    def read():
        try:
            chunks, remaining = [], 1024 * 1024
            while remaining:
                chunk = os.read(sys.stdin.fileno(), min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            received.put(b''.join(chunks))
        except Exception as error:
            received.put(error)
    threading.Thread(target=read, daemon=True).start()
    try:
        value = received.get(timeout=timeout)
    except queue.Empty:
        return None
    if isinstance(value, Exception):
        raise value
    return value or None


def available_memory():
    import psutil
    available = psutil.virtual_memory().available
    if os.name == 'nt':
        from ..win32 import memory_status
        available = min(available, memory_status()['commit_available_bytes'])
    return available


class Cancelled(BaseException):
    """Raised by a stop signal between Python operations, never inside a GPU call."""


def stop_on_signal():
    """Stop like a finished run: no hard exit while weights or tokens are on their way to the GPU."""
    import signal
    names = [signal.SIGTERM, signal.SIGINT] + ([signal.SIGBREAK] if os.name == 'nt' else [])
    def stop(*_):
        for name in names:
            signal.signal(name, signal.SIG_IGN)
        raise Cancelled()
    for name in names:
        signal.signal(name, stop)


def settle():
    torch = sys.modules.get('torch')
    if torch is not None and torch.cuda.is_initialized():
        try:
            torch.cuda.synchronize()
        except (RuntimeError, OSError):
            pass


def emit(**value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def gpu_snapshot(stats):
    torch = sys.modules.get('torch')
    if torch is not None and torch.cuda.is_initialized():
        try:
            stats.update(gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                         gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                         gpu_free_bytes=torch.cuda.mem_get_info()[0],
                         gpu_allocator_ooms=torch.cuda.memory_stats().get('num_ooms'))
        except (RuntimeError, OSError):
            stats['gpu_sample_unavailable'] = True


def failure(error, mode='rewrite'):
    """Keep stack locations and typed causes, never arbitrary exception text.

    Tokenizers may quote only a fragment of either draft: replacing the full
    prompt in an exception is not sufficient to make that exception private.
    """
    allowed = {'ram_space', 'image_size', 'context_length', 'invalid_output', 'out_of_memory'}
    message = str(error)
    fallback = 'ask_failed' if mode == 'ask' else 'rewrite_failed'
    code = message if message in allowed else 'out_of_memory' if 'out of memory' in message.lower() else fallback
    frames = [dict(module=Path(f.filename).name, function=f.name, line=f.lineno)
              for f in traceback.extract_tb(error.__traceback__)]
    result = dict(error=code, exception=type(error).__name__, error_details=frames)
    from .rules import InvalidOutput
    if isinstance(error, InvalidOutput):
        result['output_rejection'] = error.details
    return result


def begin(stats):
    """Imports and the available-RAM readings shared by every mode; returns checkpoint()."""
    started = time.monotonic()
    if 'available_memory_at_start_bytes' not in stats:
        stats['available_memory_at_start_bytes'] = available_memory()
    import hashlib
    stats['code_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                            for name in ('core.py', 'worker.py', 'rules.py')}
    emit(phase='loading', diagnostics=dict(stats))
    torch, transformers = import_dependencies()
    if 'prewarm_import_seconds' not in stats:
        stats['import_seconds'] = time.monotonic() - started
    stats.update(torch_version=torch.__version__,
                 transformers_version=transformers.__version__, cuda_version=torch.version.cuda,
                 platform=sys.platform, python_version=sys.version.split()[0])
    def checkpoint(stage, phase='loading', **extra):
        stats['stage'] = stage
        stats['worker_seconds'] = time.monotonic() - started
        gpu_snapshot(stats)
        emit(phase=phase, diagnostics=dict(stats), **extra)
    stats['available_memory_before_load_bytes'] = available_memory()
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    return torch, checkpoint


def open_image(path):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        if source.width * source.height > 50_000_000:
            raise ValueError('image_size')
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((768, 768))
    return image


def tokenize(processor, messages, max_pixels=512 * 32 * 32):
    return processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
        return_dict=True, return_tensors='pt', processor_kwargs={'images_kwargs': {'max_pixels': max_pixels}})


# Available RAM each placement needs. A GPU-resident load reads one layer at a
# time into a small buffer; its figure covers the whole worker and is checked
# against the reading taken when the worker starts (on Windows the imports and
# CUDA context alone take about 2.5 GB of commit). Other placements map and
# merge the whole model in RAM; theirs is checked after the imports.
RESIDENT_RAM = 4 * 2**30
STREAMED_RAM = 6 * 2**30   # download-card figure for 6 GB cards: resident part plus the streamed layers' host copy
MAPPED_RAM = 11 * 2**30


# GPU space beside the weights and the KV cache: prefill activations, a
# widened int8 weight, the per-layer merge while loading and allocator slack.
WORKING_VRAM = 3 * 2**28
OUTPUT_TOKENS = 1800


def kv_positions(input_tokens):
    """Cache positions reserved for a request: the input and its rewrite, then a
    correction retry that resends both with a short instruction and writes another.
    A retry that would need more is skipped when the model is held on the GPU."""
    return input_tokens + 2 * OUTPUT_TOKENS + 256


def reserved_tokens(stats):
    """Prompt length to reserve KV space for; a batch of questions runs one at a time."""
    tokens = stats.get('input_tokens', 6500)
    if isinstance(tokens, list):
        tokens = max((t for t in tokens if t <= 6500), default=6500)
    return tokens


def kv_bytes(stored, input_tokens):
    keys = [row for name, row in stored.items() if name.endswith('self_attn.k_proj.weight')]
    per_token = 2 * len(keys) * (keys[0]['shape'][0] if keys else 0) * 2  # K and V, bf16
    return per_token * kv_positions(input_tokens)


def retry_allowed(tokens, stats):
    """A correction retry must fit the context limit and, on the GPU, the reserved cache."""
    if tokens + OUTPUT_TOKENS > stats.get('kv_positions', tokens + OUTPUT_TOKENS):
        stats['dialogue_retry_skipped'] = 'gpu_space'
        return False
    return tokens <= 6500


def placement(weights, int8_saving, kv, allocator_budget):
    """'bf16' or 'int8' when weights, KV and working space fit the GPU, else None
    (map in RAM and run the layers that do not fit from there).

    Cards that cannot hold bf16 weights (8-10 GB) would otherwise run part of
    every token from system RAM, about 1 token/s on Windows.
    """
    room = allocator_budget - kv - WORKING_VRAM
    return 'bf16' if weights <= room else 'int8' if weights - int8_saving <= room else None


def load_model(folder, torch, stats, checkpoint):
    """Weights, FreeToken operators and GPU placement; returns (model, device)."""
    from . import core, int8
    from .load import stored
    checkpoint('placement')
    placing = time.monotonic()
    shapes = stored(folder)
    weights = sum(row['bytes'] for row in shapes.values())
    resident = None
    keep = None
    host_bytes = 0
    weight_budget = 0
    if torch.cuda.is_available():
        from .. import gpu_budget
        free, _ = torch.cuda.mem_get_info()
        stats.update(gpu_name=torch.cuda.get_device_name(), gpu_free_before_load_bytes=free,
                     gpu_compute_capability=list(torch.cuda.get_device_capability()))
        admission = gpu_budget.configure(torch, free, reserve_bytes=256 * 2**20)
        free = min(free, admission.get('effective_allocator_limit_bytes') or free)
        # When the layers do not all fit, keep activation/KV space and the
        # desktop outside the budget for the layers that stay on the GPU.
        weight_budget = min(9 * 2**30, max(0, free - 2 * 2**30))
        kv = kv_bytes(shapes, reserved_tokens(stats))
        stats.update(gpu_allocator_budget_bytes=free, gpu_weight_budget_bytes=weight_budget, gpu_kv_reserve_bytes=kv)
        resident = placement(weights, int8.saved_bytes(shapes), kv, free)
        if not resident:
            # Too big even as int8: keep what fits on the GPU and stream the other
            # layers' int8 rows from host memory, a layer ahead of use.
            plan = int8.resident_layers(weights, shapes, free - kv - WORKING_VRAM)
            if plan:
                resident, (keep, host_bytes) = 'int8', plan
        stats.update(streamed_layers=len(int8.layer_bytes(shapes)) - keep if keep is not None else 0,
                     streamed_host_bytes=host_bytes)
        if not resident and weight_budget <= 2 * 2**30:
            # Not even the streaming tier fits: the model would run on the CPU at
            # about 0.2 tokens/s and hit the request deadline, so say so at once.
            raise ValueError('out_of_memory')
    stats['ram_required_bytes'] = (RESIDENT_RAM + host_bytes) if resident else MAPPED_RAM
    available = stats['available_memory_at_start_bytes' if resident else 'available_memory_before_load_bytes']
    if available < stats['ram_required_bytes']:
        raise ValueError('ram_space')
    from ..device_index import active_index
    card = active_index(torch)
    checkpoint('load_weights')
    loading = time.monotonic()
    if resident:
        from .load import resident as load_resident
        model = load_resident(folder, torch, 'cuda:%d' % card, resident, keep)
        mapping, device = {'': card}, 'cuda:%d' % card
        stats.update(weights=resident, kv_positions=kv_positions(reserved_tokens(stats)))
    else:
        import psutil
        from transformers import Qwen3VLForConditionalGeneration
        model = Qwen3VLForConditionalGeneration.from_pretrained(folder, dtype=torch.bfloat16,
            device_map='cpu', local_files_only=True, trust_remote_code=False, attn_implementation='sdpa').eval()
        core.prepare(model)
        mapping, device = {'': 'cpu'}, 'cpu'
        if weight_budget > 2 * 2**30:
            from accelerate import dispatch_model, infer_auto_device_map
            mapping = infer_auto_device_map(model, max_memory={card: weight_budget,
                'cpu': min(psutil.virtual_memory().total - 2 * 2**30, weights + available_memory() - 2 * 2**30)},
                no_split_module_classes=['Qwen3VLTextDecoderLayer', 'Qwen3VLVisionBlock'], dtype=torch.bfloat16)
            if 'disk' in mapping.values():
                raise ValueError('ram_space')
            model = dispatch_model(model, mapping)
            device = 'cuda:%d' % card if any(v == card for v in mapping.values()) else 'cpu'
    stats.update(load_seconds=time.monotonic() - loading, placement_seconds=time.monotonic() - placing,
                 accelerations=['merged_gate_up', 'per_image_sdpa', 'chunked_vision_mlp', 'merged_qkv'],
                 backend='freetoken-portable-ops+transformers',
                 cpu_offload=any(v == 'cpu' for v in mapping.values()) and any(v == card for v in mapping.values()),
                 execution_device=device, device_map=mapping)
    return model, device


def generate(torch, model, inputs, max_new_tokens, started, on_token, stop=None):
    """Greedy decoding; on_token(timing) sees first-output and decode speed, and stop(ids) may end it early."""
    from transformers import StoppingCriteria, StoppingCriteriaList
    prompt = inputs.input_ids.shape[-1]
    timing = {}
    class Progress(StoppingCriteria):
        first = None
        def __call__(self, input_ids, scores, **kwargs):
            now = time.monotonic()
            tokens = int(input_ids.shape[-1] - prompt)
            if self.first is None:
                if inputs.input_ids.is_cuda:
                    torch.cuda.synchronize()
                    now = time.monotonic()
                self.first = now
                timing['first_output_seconds'] = now - started
                timing['first_output_epoch'] = time.time()
            timing.update(output_tokens=tokens, decode_seconds=now - self.first,
                          decode_tokens_per_second=(tokens - 1) / (now - self.first) if now > self.first else None)
            on_token(timing)
            return bool(stop is not None and tokens % 16 == 0 and stop(input_ids[0, prompt:]))
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, use_cache=True,
                                stopping_criteria=StoppingCriteriaList([Progress()]))
    return output[0, prompt:], timing


def run(folder, value, *, stats=None):
    stats = {} if stats is None else stats
    if value.get('mode') == 'ask':
        return ask(folder, value, stats)
    if value.get('mode') == 'split':
        return split(folder, value, stats)
    return rewrite(folder, value, stats)


def validate_rewrite(text, value, stats, retried=False):
    from .rules import InvalidOutput, validate_output
    try:
        return validate_output(text, value)
    except InvalidOutput as error:
        error.details['retried'] = retried
        stats.setdefault('output_rejections', []).append(error.details)
        raise


def rewrite(folder, value, stats):
    stats.update(stage='imports', pid=os.getpid(), image_count=len(value['media']),
                 mode=value['mode'], dtype='bfloat16', attention='sdpa', kv_cache='dynamic',
                 backend='transformers', accelerations=[],
                 persistent_model=False)
    torch, checkpoint = begin(stats)
    from transformers import AutoProcessor
    from .rules import DIALOGUE_RETRY, InvalidOutput, instruction, label, language, missing_lines, user_text
    checkpoint('preprocess')
    preparing = time.monotonic()
    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    messages = [{'role': 'system', 'content': instruction(value)}]
    content = []
    for row in value['media']:
        content.extend([{'type': 'text', 'text': f'{label(row)} ({row["role"]})'},
                        {'type': 'image', 'image': open_image(row['path'])}])
    content.append({'type': 'text', 'text': user_text(value)})
    messages.append({'role': 'user', 'content': content})
    inputs = tokenize(processor, messages)
    if inputs.input_ids.shape[-1] > 6500:
        raise ValueError('context_length')
    stats.update(input_tokens=int(inputs.input_ids.shape[-1]), preprocess_seconds=time.monotonic() - preparing)
    model, device = load_model(folder, torch, stats, checkpoint)
    inputs = inputs.to(device)
    loaded = time.monotonic()
    checkpoint('prefill', 'rewriting')
    shown = [0.]
    def on_token(timing):
        stats.update(output_tokens=timing['output_tokens'], decode_seconds=timing['decode_seconds'],
                     decode_tokens_per_second=timing['decode_tokens_per_second'])
        stats['time_to_first_output_seconds'] = timing['first_output_seconds']
        stats.setdefault('first_output_epoch', timing['first_output_epoch'])
        if time.monotonic() - shown[0] > .5:
            checkpoint('decode', 'rewriting')
            shown[0] = time.monotonic()
    ids, _ = generate(torch, model, inputs, 1800, loaded, on_token)
    stats.update(rewrite_seconds=time.monotonic() - loaded, output_tokens=len(ids))
    checkpoint('validate', 'rewriting')
    if len(ids) >= 1800:
        raise ValueError('context_length')
    text = processor.decode(ids, skip_special_tokens=True)
    missing = missing_lines(text, value['text'])
    rewritten, rejected = None, None
    references = []
    try:
        rewritten = validate_rewrite(text, value, stats)
    except InvalidOutput as error:
        rejected = error
        if error.details['condition'] == 'missing_picture_reference':
            references = error.details['missing_references']
    if missing or references:
        # Both corrections share one turn and the existing context/cache allowance.
        correction = ''
        if references:
            labels = ', '.join(references)
            correction = (f'Your rewrite omitted these required media references: {labels}. '
                          'Preserve all existing content and add each missing reference where it belongs. '
                          'Return the complete prompt in the same required section format.\n')
        if missing:
            correction += DIALOGUE_RETRY + '\n'.join('- [%s] %s' % (language(line), line) for line in missing)
        retry = messages + [{'role': 'assistant', 'content': text}, {'role': 'user', 'content': correction}]
        retry_inputs = tokenize(processor, retry)
        if retry_allowed(retry_inputs.input_ids.shape[-1], stats):
            again, _ = generate(torch, model, retry_inputs.to(device), 1800, time.monotonic(), lambda timing: None)
            second = processor.decode(again, skip_special_tokens=True)
            stats.update(retry_output_tokens=len(again), rewrite_seconds=time.monotonic() - loaded)
            if missing:
                stats['dialogue_retry'] = True
            if references:
                stats['media_retry'] = True
                if any(each.startswith('<Picture ') for each in references):
                    stats['picture_retry'] = True  # Retain the existing image diagnostic.
            try:
                candidate = validate_rewrite(second, value, stats, retried=True)
                if references and len(again) >= 1800:
                    error = InvalidOutput('output_token_limit', second, value)
                    error.details['retried'] = True
                    stats['output_rejections'].append(error.details)
                    raise error
                if len(again) < 1800 and (references or len(missing_lines(second, value['text'])) < len(missing)):
                    rewritten = candidate
            except InvalidOutput:
                if references:
                    raise
        elif references:
            stats['media_retry_skipped'] = stats.get('dialogue_retry_skipped', 'context_length')
            if any(each.startswith('<Picture ') for each in references):
                stats['picture_retry_skipped'] = stats['media_retry_skipped']
    if rewritten is None:
        rejected.details = dict(rejected.details, retried=bool(stats.get('media_retry') or stats.get('dialogue_retry')))
        raise rejected
    stats['stage'] = 'complete'
    emit(phase='complete', text=rewritten, diagnostics=dict(stats))


def split(folder, value, stats):
    """One description per part of a long video; text leaves only via stdout."""
    from .rules import SPLIT_RETRY, split_instruction, split_user_text, validate_split
    count = len(value['seconds'])
    limit = min(2 * OUTPUT_TOKENS - 200, 600 + 300 * count)  # Within the cache reserved for a rewrite and its retry.
    stats.update(stage='imports', pid=os.getpid(), mode='split', part_count=count, dtype='bfloat16',
                 attention='sdpa', kv_cache='dynamic', backend='transformers', accelerations=[],
                 persistent_model=False)
    torch, checkpoint = begin(stats)
    from transformers import AutoProcessor
    checkpoint('preprocess')
    preparing = time.monotonic()
    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    messages = [{'role': 'system', 'content': split_instruction(value)},
                {'role': 'user', 'content': [{'type': 'text', 'text': split_user_text(value)}]}]
    inputs = tokenize(processor, messages)
    if inputs.input_ids.shape[-1] > 6500:
        raise ValueError('context_length')
    stats.update(input_tokens=int(inputs.input_ids.shape[-1]), preprocess_seconds=time.monotonic() - preparing)
    model, device = load_model(folder, torch, stats, checkpoint)
    loaded = time.monotonic()
    checkpoint('prefill', 'splitting')
    shown = [0.]
    def on_token(timing):
        stats.update(output_tokens=timing['output_tokens'], decode_seconds=timing['decode_seconds'])
        if time.monotonic() - shown[0] > .5:
            checkpoint('decode', 'splitting')
            shown[0] = time.monotonic()
    def ended(ids):
        # A model that starts the answer over after the last part would repeat it until the limit.
        text = processor.decode(ids, skip_special_tokens=True)
        last = re.search(r'(?m)^Part %d:' % count, text)
        again = last and re.search(r'(?m)^(?:Part \d+:|Shared:)', text[last.end():])
        return text[:last.end() + again.start()].rstrip() if again else None
    def answer(ids):
        text = ended(ids)
        return [text] if text is not None else [processor.decode(ids, skip_special_tokens=True)] if len(ids) < limit else []
    ids, _ = generate(torch, model, inputs.to(device), limit, loaded, on_token, ended)
    answers = answer(ids)
    stats.update(split_seconds=time.monotonic() - loaded, output_tokens=len(ids))
    parts = None
    try:
        parts = validate_split(answers[0], value, strict=True)
    except (ValueError, IndexError):
        # One more try with the rules restated; resending the wrong answer would not fit the cache.
        retry = [messages[0], {'role': 'user', 'content': [{'type': 'text', 'text': (
            split_user_text(value) + '\n\n' + SPLIT_RETRY)}]}]
        retry_inputs = tokenize(processor, retry)
        if retry_inputs.input_ids.shape[-1] + limit <= stats.get('kv_positions', 10 ** 9):
            ids, _ = generate(torch, model, retry_inputs.to(device), limit, time.monotonic(), lambda timing: None, ended)
            stats.update(split_retry=True, retry_output_tokens=len(ids), split_seconds=time.monotonic() - loaded)
            answers[:0] = answer(ids)
        for strict in (True, False):
            for answer in answers:
                try:
                    parts = parts or validate_split(answer, value, strict=strict)
                except ValueError:
                    pass
        if parts is None:
            raise ValueError('split_failed')
    stats['stage'] = 'complete'
    emit(phase='complete', parts=parts, diagnostics=dict(stats))


def ask(folder, value, stats):
    """Answer each question with one loaded model; answers leave only via stdout."""
    from .rules import ask_instruction, image_tokens, parse_json, ASK_JSON_RETRY
    questions = value['questions']
    stats.update(stage='imports', pid=os.getpid(), mode='ask', question_count=len(questions),
                 image_count=sum(len(q['images']) for q in questions), format=value['format'],
                 max_tokens=value['max_tokens'], custom_system=bool(value.get('system')),
                 dtype='bfloat16', attention='sdpa', kv_cache='dynamic', backend='transformers',
                 accelerations=[], persistent_model=False)
    torch, checkpoint = begin(stats)
    from transformers import AutoProcessor
    checkpoint('preprocess')
    preparing = time.monotonic()
    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    system = ask_instruction(value)
    images = {}
    prepared = []
    for question in questions:
        content = []
        for index, row in enumerate(question['images'], 1):
            if row['path'] not in images:
                images[row['path']] = open_image(row['path'])
            label = f' ({row["label"]})' if row.get('label') else ''
            content.extend([{'type': 'text', 'text': f'<Image {index}>{label}'},
                            {'type': 'image', 'image': images[row['path']]}])
        content.append({'type': 'text', 'text': question['text']})
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]
        pixels = image_tokens(len(question['images'])) * 32 * 32
        inputs = tokenize(processor, messages, pixels)
        prepared.append((messages, pixels, inputs if inputs.input_ids.shape[-1] <= 6500 else None,
                         int(inputs.input_ids.shape[-1])))
    stats.update(input_tokens=[row[3] for row in prepared], preprocess_seconds=time.monotonic() - preparing)
    answers = [None] * len(questions)
    timings = [dict(input_tokens=row[3]) for row in prepared]
    for index, row in enumerate(prepared):
        if row[2] is None:
            answers[index] = dict(error='context_length', text='', tokens=0, seconds=0.)
            timings[index].update(error='context_length')
    if all(answer is not None for answer in answers):
        raise ValueError('context_length')
    model, device = load_model(folder, torch, stats, checkpoint)
    loaded = time.monotonic()
    stats['answer_timings'] = timings
    checkpoint('prefill', 'answering', answered=0, questions=len(questions))
    for index, (messages, pixels, inputs, _) in enumerate(prepared):
        if inputs is None:
            continue
        started = time.monotonic()
        shown = [0.]
        def on_token(timing):
            timings[index].update(timing)
            if time.monotonic() - shown[0] > .5:
                checkpoint('decode', 'answering', answered=index, questions=len(questions))
                shown[0] = time.monotonic()
        ids, _ = generate(torch, model, inputs.to(device), value['max_tokens'], started, on_token)
        text = processor.decode(ids, skip_special_tokens=True).strip()
        answer = dict(text=text, tokens=len(ids))
        if len(ids) >= value['max_tokens']:
            answer['truncated'] = True
        if value['format'] == 'json':
            try:
                answer['text'], answer['json'] = parse_json(text)
            except ValueError:
                # One correction turn with the same images; the model sees its reply.
                retry = messages + [{'role': 'assistant', 'content': text},
                                    {'role': 'user', 'content': ASK_JSON_RETRY}]
                answer['retried'] = True
                retry_inputs = tokenize(processor, retry, pixels)
                if retry_inputs.input_ids.shape[-1] > 6500:
                    answer['error'] = 'invalid_json'
                else:
                    ids, _ = generate(torch, model, retry_inputs.to(device), value['max_tokens'],
                                      time.monotonic(), lambda timing: None)
                    text = processor.decode(ids, skip_special_tokens=True).strip()
                    answer.update(text=text, tokens=answer['tokens'] + len(ids))
                    if len(ids) >= value['max_tokens']:
                        answer['truncated'] = True
                    try:
                        answer['text'], answer['json'] = parse_json(text)
                    except ValueError:
                        answer['error'] = 'invalid_json'
        answer['seconds'] = time.monotonic() - started
        answers[index] = answer
        timings[index].update(seconds=answer['seconds'], output_tokens=answer['tokens'],
                              truncated=bool(answer.get('truncated')), retried=bool(answer.get('retried')),
                              **({'error': answer['error']} if 'error' in answer else {}))
        checkpoint('decode', 'answering', answered=index + 1, questions=len(questions))
    stats.update(answer_seconds=time.monotonic() - loaded, stage='complete')
    emit(phase='complete', answers=answers, diagnostics=dict(stats))


def main():
    stats = {}
    value = {}
    prestarting = os.environ.get(PRESTART_ENV) == '1'
    requested = False
    stop_on_signal()
    try:
        if prestarting:
            started = time.monotonic()
            torch, _ = import_dependencies()
            # This is the only CUDA query allowed before a request; it neither
            # opens the driver nor initializes a context.
            if torch.cuda.is_initialized():
                raise RuntimeError('Prestart imports initialized CUDA')
            imported = time.monotonic() - started
            ready_epoch = time.time()
            emit(phase='ready', import_seconds=imported, pid=os.getpid(), cuda_initialized=False)
            data = wait_request(max(0., float(os.environ.get(PRESTART_TIMEOUT_ENV, '300'))))
            if data is None:
                return 0
            value = json.loads(data)
            requested = True
            metadata = value.pop('_prewarm', {})
            waited = max(0., ready_epoch - metadata.get('sent_epoch', ready_epoch))
            stats.update(prewarm_import_seconds=imported, import_seconds=waited)
            stats['available_memory_at_start_bytes'] = request_memory(stats)
        else:
            value = json.loads(sys.stdin.buffer.read(1024 * 1024))
        run(Path(sys.argv[1]), value, stats=stats)
    except Cancelled:
        settle()
        emit(phase='cancelled')
        return 0
    except Exception as error:
        if not prestarting or requested:
            gpu_snapshot(stats)
        mode = value.get('mode') if isinstance(value, dict) else None
        emit(phase='failed', diagnostics=stats, **failure(error, mode))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
