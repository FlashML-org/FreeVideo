"""Layer streaming for the image model's DiT blocks and text-encoder layers.

The engine's ``LayerOffloader`` already does what the image model needs:
frozen CPU weights packed into pinned planes, two CUDA slots, a copy stream and
one layer of prefetch, with weights rebound only while a layer runs. This
module wraps it for a stack of identical layers and adds the one thing the
video path does not need: choosing, per request, how many of the first layers
stay on the card between uses (``set_resident``).

A stack must be run front to back every pass (the offloader enforces it): 32
DiT blocks each denoising step, 36 encoder layers once per prompt.
"""
from ..offload import LayerOffloader, pin_layer_weights


IMAGE_WORKING_RESERVE = 6 * 2**30  # private memory beyond the weights: tensors, tokenizer, decoder, the process itself


def cgroup_pin_cap(reserve=IMAGE_WORKING_RESERVE):
    """Bytes of locked weights a memory-limited group can take, or None without a limit.

    ``live_pin_capacity`` reads the machine's free memory, which a container or
    systemd scope with a lower cap does not share; locked pages cannot be
    reclaimed, so what is left of the cap after the working set bounds them.
    """
    from ..hardware import cgroup_memory
    group = cgroup_memory()
    room = group.get('reclaimable_available_bytes', group.get('available_bytes'))
    return None if room is None else max(0, room - reserve)


class StreamedStack:
    """``layers`` hold frozen CPU weights; they are moved to the card on use."""

    def __init__(self, layers, *, name, device='cuda', pin=True, max_pin_bytes=None, prefetch=True, host_prefetch=None,
                 progress=None):
        self.name = name
        self.layers = list(layers)
        if not self.layers:
            raise ValueError('StreamedStack needs at least one layer')
        # Host bytes of every layer, pinned or not: the weights hold this much of
        # the host whichever storage they ended up on.
        self.host_bytes = sum(tensor.numel() * tensor.element_size() for layer in self.layers
                              for tensor in list(layer.parameters()) + list(layer.buffers()))
        # Pinning is an optimization: a host that refuses it costs transfer
        # speed, not the request (pin_layer_weights keeps the unpinned layers
        # on their ordinary storage and the offloader stages them).
        pin_options = {'progress': progress} if progress is not None else {}
        cap = cgroup_pin_cap()
        if cap is not None:
            max_pin_bytes = cap if max_pin_bytes is None else min(max_pin_bytes, cap)
        self.pinned_bytes = pin_layer_weights(self.layers, max_bytes=max_pin_bytes, **pin_options) if pin else 0
        self.offloader = LayerOffloader(self.layers, device=device, prefetch=prefetch, host_prefetch=host_prefetch)
        self.layer_bytes = max(sum(count * parameter.element_size()
                                   for parameter, _, _, _, count, _ in entries)
                               for entries in self.offloader.layout)
        self.resident = 0

    @property
    def pinned_layers(self):
        return sum(self.offloader.pinned_layers)

    def set_resident(self, count):
        """Keep the first ``count`` layers' weights on the card after their first use.

        Only at a pass boundary (between requests, or between stages). The
        planes fill during the next pass through the normal streaming path, so
        the first pass after raising it still pays the copies once.
        """
        count = max(0, min(int(count), len(self.layers)))
        self.offloader.cache_between_steps(count * self.layer_bytes, max_layers=count)
        self.resident = count
        return count

    def stats(self):
        return dict(name=self.name, layers=len(self.layers), layer_bytes=self.layer_bytes,
                    pinned_layers=self.pinned_layers, pinned_bytes=self.pinned_bytes,
                    resident_layers=self.resident, cached_bytes=self.offloader.cached_bytes(),
                    **self.offloader.stats())

    def close(self):
        self.offloader.close()


class ResidentStack:
    """Every layer lives on the card and nothing is kept on the host.

    Stands in for ``StreamedStack`` while the planned residency is the whole
    stack, so the host holds no second copy of the weights. ``to_streamed``
    brings the layers back for a request whose budget no longer fits them all.
    """

    def __init__(self, layers, *, name, device='cuda'):
        self.name = name
        self.layers = list(layers)
        if not self.layers:
            raise ValueError('ResidentStack needs at least one layer')
        self.device = device
        self.host_bytes = 0
        self.layer_bytes = max(sum(tensor.numel() * tensor.element_size()
                                   for tensor in list(layer.parameters()) + list(layer.buffers()))
                               for layer in self.layers)
        self.resident = len(self.layers)

    at_boundary = True
    cpu = ()   # no host weights

    @property
    def offloader(self):
        return self

    def set_resident(self, count):
        return self.resident

    def stats(self):
        return dict(name=self.name, layers=len(self.layers), layer_bytes=self.layer_bytes, pinned_layers=0,
                    pinned_bytes=0, resident_layers=self.resident, cached_bytes=0, host_copy=False)

    def to_streamed(self, **options):
        """Copy the layers back to the host and return a ``StreamedStack`` over them."""
        moved = []
        try:
            for layer in self.layers:
                layer.to('cpu')
                moved.append(layer)
        except BaseException:
            for layer in moved:
                layer.to(self.device)
            raise
        try:
            return StreamedStack(self.layers, name=self.name, device=self.device, **options)
        except Exception:
            # Locking pages failed: stream from ordinary memory instead.
            return StreamedStack(self.layers, name=self.name, device=self.device, **{**options, 'pin': False})

    def close(self):
        pass
