"""Load the prompt model straight onto the GPU with bounded reads.

Transformers maps each weight file copy-on-write; on Windows that charges the
whole file to commit, and merging on the CPU adds private copies (18-20 GB of
commit in reports, for 8.3 GB of weights). When every weight fits on the GPU,
read each layer into a small buffer instead and merge (and quantize) it there.
"""
import json
from pathlib import Path


def files(folder):
    """{checkpoint key: shard file name}."""
    index = Path(folder) / 'model.safetensors.index.json'
    if index.is_file():
        return json.loads(index.read_text(encoding='utf-8'))['weight_map']
    from ..tensor_io import ParallelReads
    reader = ParallelReads(Path(folder) / 'model.safetensors')
    try:
        return {name: 'model.safetensors' for name in reader.entries}
    finally:
        reader.close()


def stored(folder):
    """{checkpoint key: dict(shape, bytes)} from the shard headers alone."""
    from ..tensor_io import ParallelReads
    rows = {}
    for shard in sorted(set(files(folder).values())):
        reader = ParallelReads(Path(folder) / shard)
        try:
            rows.update({name: dict(shape=shape, bytes=end - start)
                         for name, (_, shape, start, end) in reader.entries.items()})
        finally:
            reader.close()
    return rows


def resident(folder, torch, device, precision):
    """Returns the model on `device`, with int8 projections if precision == 'int8'."""
    from accelerate import init_empty_weights
    from torch import nn
    from transformers import AutoConfig, GenerationConfig, Qwen3VLForConditionalGeneration
    from ..tensor_io import ParallelReads
    from . import core, int8
    folder = Path(folder)
    config = AutoConfig.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    # Parameters stay on the meta device until read; buffers such as the
    # rotary frequencies are computed on the CPU as usual.
    with init_empty_weights(include_buffers=False):
        model = Qwen3VLForConditionalGeneration._from_config(config, dtype=torch.bfloat16,
                                                             attn_implementation='sdpa')
    model.generation_config = GenerationConfig.from_pretrained(folder, local_files_only=True)
    model.eval()
    shards = files(folder)
    # Qwen3-VL-4B ties lm_head to the token embeddings and does not store it.
    tied = any(getattr(c, 'tie_word_embeddings', False) for c in (config, config.get_text_config()))
    missing = ({name for name, _ in model.named_parameters(remove_duplicate=False)} - set(shards)
               - ({'lm_head.weight'} if tied else set()))
    if missing:
        raise ValueError('Prompt model weights are incomplete')
    readers = {}

    def load(prefix):
        groups = {}
        for name, shard in shards.items():
            if name.startswith(prefix):
                groups.setdefault(shard, []).append(name)
        for shard, names in groups.items():
            if shard not in readers:
                readers[shard] = ParallelReads(folder / shard)
            for name, tensor in readers[shard].tensors(names).items():
                parent, _, leaf = name.rpartition('.')
                module = model.get_submodule(parent)
                # The dtype Transformers would load it in (the skeleton's).
                dtype = getattr(module, leaf).dtype
                setattr(module, leaf, nn.Parameter(tensor.to(device=device, dtype=dtype), requires_grad=False))

    try:
        layers = 'model.language_model.layers.'
        # Everything outside the layers, in pieces of at most one vision block.
        outside = sorted({name.split('.blocks.')[0] + '.blocks.' + name.split('.blocks.')[1].split('.')[0] + '.'
                          if '.blocks.' in name else name for name in shards if not name.startswith(layers)})
        for prefix in outside:
            load(prefix)

        def each_layer(layer, merge):
            load('%s%d.' % (layers, layer.self_attn.layer_idx))
            merge(layer)
            if precision == 'int8':
                int8.install(layer, device)
        core.prepare(model, each_layer)
    finally:
        for reader in readers.values():
            reader.close()
    if tied:
        model.lm_head.weight = model.model.language_model.embed_tokens.weight
    model = model.to(device)
    torch.cuda.empty_cache()  # Merge and quantize temporaries; KV space comes next.
    return model
