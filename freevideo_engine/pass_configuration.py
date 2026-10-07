"""Change pass-local compute tiling while preserving loaded model weights."""
from contextlib import contextmanager


COMPUTE_KEYS = ('head_chunk', 'window_batch', 'ff_chunk', 'projection_chunk',
                'attention_cpu_outputs', 'grouped_attention_outputs',
                'fp8_ff_recompute', 'residual_offload', 'prefetch')


@contextmanager
def configuration(engine, selected):
    if not selected:
        yield
        return
    previous = engine.config
    values = {key: selected.get(key, previous.get(key)) for key in COMPUTE_KEYS}
    changed = {key for key, value in values.items() if value != previous.get(key)}
    if not changed:
        yield
        return
    model, attention = engine.transformer, engine.attention
    if not previous.get('inference_kernels') or not previous.get('head_chunk'):
        raise ValueError('Pass configuration requires the bounded inference path')
    from src.models.hybrid_transform import iter_hybrids
    methods = [(module, name, getattr(module, name)) for module, name in
               [(model, 'forward')] + [(block, 'forward') for block in model.transformer_blocks]
               + [(block.ff, 'forward') for block in model.transformer_blocks]
               + [(module, '_hybrid_forward') for module in iter_hybrids(model)]]
    old_prefetch, old_window = engine.prefetch, attention.window_batch
    try:
        if changed.intersection(('head_chunk', 'attention_cpu_outputs', 'grouped_attention_outputs', 'projection_chunk')):
            from .head_chunk import install_head_chunks
            install_head_chunks(model, attention, values['head_chunk'],
                cpu_outputs=values['attention_cpu_outputs'], projection_chunk=values['projection_chunk'],
                grouped_outputs=values['grouped_attention_outputs'], parallelism=previous.get('head_parallelism', 1))
        if changed.intersection(('ff_chunk', 'fp8_ff_recompute')):
            if previous.get('precision') == 'fp8' and previous.get('linear_compute') == 'native-fp8':
                for block in model.transformer_blocks:
                    engine.device_backend.install_chunked_ff(block.ff, values['ff_chunk'], recompute=values['fp8_ff_recompute'])
            elif 'ff_chunk' in changed:
                # Ampere keeps its existing weight-only arithmetic. Replace
                # the outer row tiling without nesting the old tiling wrapper.
                import types
                from .blocks import chunked_ff
                for block in model.transformer_blocks:
                    block.ff.forward = types.MethodType(chunked_ff(block.ff._freevideo_unchunked_forward,
                                                                   values['ff_chunk']), block.ff)
        if changed.intersection(('projection_chunk', 'residual_offload')):
            from .packing import install_streamed_forward
            from .blocks import install_bounded_blocks
            install_streamed_forward(model, values['projection_chunk'], residual_offload=values['residual_offload'])
            install_bounded_blocks(model, residual_offload=values['residual_offload'])
        engine.config = dict(previous, **values)
        engine.prefetch, attention.window_batch = values['prefetch'], values['window_batch']
        attention.current_plan = attention.offsets = attention.batches = None
        yield
    finally:
        for module, name, method in methods:
            setattr(module, name, method)
        engine.config = previous
        engine.prefetch, attention.window_batch = old_prefetch, old_window
        attention.current_plan = attention.offsets = attention.batches = None
        # Canvas-specific host buffers must not survive alongside the next
        # pass's differently shaped buffers. Immutable weights remain untouched.
        for name in ('host_outputs', 'host_output_key', 'group_readouts'):
            if hasattr(attention, name):
                delattr(attention, name)
