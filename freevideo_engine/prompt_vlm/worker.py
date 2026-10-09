"""One rewrite job per process: model, CUDA context and private commit die together."""
import json
import os
from pathlib import Path
import sys
import time
import traceback


def available_memory():
    import psutil
    available = psutil.virtual_memory().available
    if os.name == 'nt':
        from ..win32 import memory_status
        available = min(available, memory_status()['commit_available_bytes'])
    return available


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
    allowed = {'ram_space', 'image_size', 'context_length', 'invalid_output'}
    message = str(error)
    code = message if message in allowed else 'out_of_memory' if 'out of memory' in message.lower() else 'rewrite_failed'
    frames = [dict(module=Path(f.filename).name, function=f.name, line=f.lineno)
              for f in traceback.extract_tb(error.__traceback__)]
    return dict(error=code, exception=type(error).__name__, error_details=frames)


def begin(stats):
    """Imports and the RAM check shared by every mode; returns checkpoint()."""
    started = time.monotonic()
    import hashlib
    stats['code_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                            for name in ('core.py', 'worker.py', 'rules.py')}
    emit(phase='loading', diagnostics=dict(stats))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    import transformers
    stats.update(import_seconds=time.monotonic() - started, torch_version=torch.__version__,
                 transformers_version=transformers.__version__, cuda_version=torch.version.cuda,
                 platform=sys.platform, python_version=sys.version.split()[0])
    def checkpoint(stage, phase='loading', **extra):
        stats['stage'] = stage
        stats['worker_seconds'] = time.monotonic() - started
        gpu_snapshot(stats)
        emit(phase=phase, diagnostics=dict(stats), **extra)
    stats['available_memory_before_load_bytes'] = available_memory()
    if stats['available_memory_before_load_bytes'] < 11 * 2**30:
        raise ValueError('ram_space')
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


def load_model(folder, torch, stats, checkpoint):
    """Weights, FreeToken operators and GPU placement; returns (model, device)."""
    import psutil
    from transformers import Qwen3VLForConditionalGeneration
    from .core import prepare, GatedMLP
    checkpoint('load_weights')
    loading = time.monotonic()
    model = Qwen3VLForConditionalGeneration.from_pretrained(folder, dtype=torch.bfloat16,
        device_map='cpu', local_files_only=True, trust_remote_code=False, attn_implementation='sdpa').eval()
    stats['load_seconds'] = time.monotonic() - loading
    preparing = time.monotonic()
    prepare(model)
    accelerations = []
    if isinstance(model.model.language_model.layers[0].mlp, GatedMLP):
        accelerations += ['merged_gate_up', 'per_image_sdpa', 'chunked_vision_mlp']
    if hasattr(model.model.language_model.layers[0].self_attn, 'qkv_proj'):
        accelerations.append('merged_qkv')
    stats.update(accelerations=accelerations,
                 backend='freetoken-portable-ops+transformers' if accelerations else 'transformers')
    stats['prepare_ops_seconds'] = time.monotonic() - preparing
    checkpoint('placement')
    placing = time.monotonic()
    mapping = {'': 'cpu'}
    device = 'cpu'
    if torch.cuda.is_available():
        from accelerate import dispatch_model, infer_auto_device_map
        from .. import gpu_budget
        free, _ = torch.cuda.mem_get_info()
        stats.update(gpu_name=torch.cuda.get_device_name(), gpu_free_before_load_bytes=free,
                     gpu_compute_capability=list(torch.cuda.get_device_capability()))
        admission = gpu_budget.configure(torch, free, reserve_bytes=256 * 2**20)
        free = min(free, admission.get('effective_allocator_limit_bytes') or free)
        # Keep activation/KV space and the desktop outside the weight budget.
        weight_budget = min(9 * 2**30, max(0, free - 2 * 2**30))
        stats.update(gpu_allocator_budget_bytes=free, gpu_weight_budget_bytes=weight_budget)
        if weight_budget > 2 * 2**30:
            weights = sum(p.numel() * p.element_size() for p in model.parameters())
            mapping = infer_auto_device_map(model, max_memory={0: weight_budget,
                'cpu': min(psutil.virtual_memory().total - 2 * 2**30, weights + available_memory() - 2 * 2**30)},
                no_split_module_classes=['Qwen3VLTextDecoderLayer', 'Qwen3VLVisionBlock'], dtype=torch.bfloat16)
            if 'disk' in mapping.values():
                raise ValueError('ram_space')
            model = dispatch_model(model, mapping)
            device = 'cuda:0' if any(v == 0 for v in mapping.values()) else 'cpu'
    stats.update(placement_seconds=time.monotonic() - placing,
                 cpu_offload=any(v == 'cpu' for v in mapping.values()) and any(v == 0 for v in mapping.values()),
                 execution_device=device, device_map=mapping)
    return model, device


def generate(torch, model, inputs, max_new_tokens, started, on_token):
    """Greedy decoding; on_token(timing) sees first-output and decode speed."""
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
            timing.update(output_tokens=tokens, decode_seconds=now - self.first,
                          decode_tokens_per_second=(tokens - 1) / (now - self.first) if now > self.first else None)
            on_token(timing)
            return False
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, use_cache=True,
                                stopping_criteria=StoppingCriteriaList([Progress()]))
    return output[0, prompt:], timing


def run(folder, value, *, stats=None):
    stats = {} if stats is None else stats
    return rewrite(folder, value, stats)


def rewrite(folder, value, stats):
    stats.update(stage='imports', pid=os.getpid(), image_count=len(value['media']),
                 mode=value['mode'], dtype='bfloat16', attention='sdpa', kv_cache='dynamic',
                 backend='transformers', accelerations=[],
                 persistent_model=False)
    torch, checkpoint = begin(stats)
    from transformers import AutoProcessor
    from .rules import DIALOGUE_RETRY, instruction, language, missing_lines, user_text, validate_output
    checkpoint('preprocess')
    preparing = time.monotonic()
    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    messages = [{'role': 'system', 'content': instruction(value)}]
    content = []
    for index, row in enumerate(value['media'], 1):
        content.extend([{'type': 'text', 'text': f'<Picture {index}> ({row["role"]})'},
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
    if missing:
        # One correction turn when the user's dialogue was dropped; keep the better draft.
        retry = messages + [{'role': 'assistant', 'content': text}, {'role': 'user', 'content': DIALOGUE_RETRY + '\n'.join(
            '- [%s] %s' % (language(line), line) for line in missing)}]
        retry_inputs = tokenize(processor, retry)
        if retry_inputs.input_ids.shape[-1] <= 6500:
            again, _ = generate(torch, model, retry_inputs.to(device), 1800, time.monotonic(), lambda timing: None)
            second = processor.decode(again, skip_special_tokens=True)
            stats.update(dialogue_retry=True, retry_output_tokens=len(again),
                         rewrite_seconds=time.monotonic() - loaded)
            try:
                validate_output(second, value)
                if len(again) < 1800 and len(missing_lines(second, value['text'])) < len(missing):
                    text = second
            except ValueError:
                pass
    rewritten = validate_output(text, value)
    stats['stage'] = 'complete'
    emit(phase='complete', text=rewritten, diagnostics=dict(stats))


def main():
    stats = {}
    value = {}
    try:
        value = json.loads(sys.stdin.buffer.read(1024 * 1024))
        run(Path(sys.argv[1]), value, stats=stats)
    except Exception as error:
        gpu_snapshot(stats)
        mode = value.get('mode') if isinstance(value, dict) else None
        emit(phase='failed', diagnostics=stats, **failure(error, mode))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
