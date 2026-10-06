"""One rewrite per process: model, CUDA context and private commit die together."""
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


def failure(error):
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


def run(folder, value, *, stats=None):
    stats = {} if stats is None else stats
    started = time.monotonic()
    stats.update(stage='imports', pid=os.getpid(), image_count=len(value['media']),
                 mode=value['mode'], dtype='bfloat16', attention='sdpa', kv_cache='dynamic',
                 backend='transformers', accelerations=[],
                 persistent_model=False)
    import hashlib
    stats['code_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                            for name in ('core.py', 'worker.py', 'rules.py')}
    emit(phase='loading', diagnostics=dict(stats))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import psutil
    import torch
    import transformers
    from PIL import Image, ImageOps
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, StoppingCriteria, StoppingCriteriaList
    from .core import prepare
    from .rules import instruction, validate_output
    stats.update(import_seconds=time.monotonic() - started, torch_version=torch.__version__,
                 transformers_version=transformers.__version__, cuda_version=torch.version.cuda,
                 platform=sys.platform, python_version=sys.version.split()[0])
    def checkpoint(stage, phase='loading'):
        stats['stage'] = stage
        stats['worker_seconds'] = time.monotonic() - started
        gpu_snapshot(stats)
        emit(phase=phase, diagnostics=dict(stats))
    stats['available_memory_before_load_bytes'] = available_memory()
    if stats['available_memory_before_load_bytes'] < 11 * 2**30:
        raise ValueError('ram_space')
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    checkpoint('preprocess')
    preparing = time.monotonic()
    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    messages = [{'role': 'system', 'content': instruction(value)}]
    content = []
    for index, row in enumerate(value['media'], 1):
        with Image.open(row['path']) as source:
            if source.width * source.height > 50_000_000:
                raise ValueError('image_size')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((768, 768))
        content.extend([{'type': 'text', 'text': f'<Picture {index}> ({row["role"]})'},
                        {'type': 'image', 'image': image}])
    content.append({'type': 'text', 'text': f'User scene to rewrite (preserve every requested action):\n{value["text"]}'})
    messages.append({'role': 'user', 'content': content})
    inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
        return_dict=True, return_tensors='pt', processor_kwargs={'images_kwargs': {'max_pixels': 512 * 32 * 32}})
    if inputs.input_ids.shape[-1] > 6500:
        raise ValueError('context_length')
    stats.update(input_tokens=int(inputs.input_ids.shape[-1]), preprocess_seconds=time.monotonic() - preparing)
    checkpoint('load_weights')
    loading = time.monotonic()
    model = Qwen3VLForConditionalGeneration.from_pretrained(folder, dtype=torch.bfloat16,
        device_map='cpu', local_files_only=True, trust_remote_code=False, attn_implementation='sdpa').eval()
    stats['load_seconds'] = time.monotonic() - loading
    preparing = time.monotonic()
    prepare(model)
    from .core import GatedMLP
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
            inputs = inputs.to('cuda:0' if any(v == 0 for v in mapping.values()) else 'cpu')
    stats.update(placement_seconds=time.monotonic() - placing,
                 cpu_offload=any(v == 'cpu' for v in mapping.values()) and any(v == 0 for v in mapping.values()),
                 execution_device=str(inputs.input_ids.device), device_map=mapping)
    loaded = time.monotonic()
    checkpoint('prefill', 'rewriting')
    class Progress(StoppingCriteria):
        shown = 0.
        first = None
        def __call__(self, input_ids, scores, **kwargs):
            now = time.monotonic()
            tokens = int(input_ids.shape[-1] - inputs.input_ids.shape[-1])
            if self.first is None:
                if inputs.input_ids.is_cuda:
                    torch.cuda.synchronize()
                    now = time.monotonic()
                self.first = now
                stats['time_to_first_output_seconds'] = now - loaded
            stats.update(output_tokens=tokens, decode_seconds=now - self.first,
                         decode_tokens_per_second=(tokens - 1) / (now - self.first) if now > self.first else None)
            if now - self.shown > .5:
                checkpoint('decode', 'rewriting')
                self.shown = now
            return False
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=1800, do_sample=False, use_cache=True,
                                stopping_criteria=StoppingCriteriaList([Progress()]))
    ids = output[0, inputs.input_ids.shape[-1]:]
    stats.update(rewrite_seconds=time.monotonic() - loaded, output_tokens=len(ids))
    checkpoint('validate', 'rewriting')
    if len(ids) >= 1800:
        raise ValueError('context_length')
    rewritten = validate_output(processor.decode(ids, skip_special_tokens=True), value)
    stats['stage'] = 'complete'
    emit(phase='complete', text=rewritten, diagnostics=dict(stats))


def main():
    stats = {}
    try:
        value = json.loads(sys.stdin.buffer.read(512 * 1024))
        run(Path(sys.argv[1]), value, stats=stats)
    except Exception as error:
        gpu_snapshot(stats)
        emit(phase='failed', diagnostics=stats, **failure(error))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
