"""Device services used by the runtime, independent of an OS or Torch import.

Backends must preserve the requested arithmetic policy or reject it. Selection
never means that every candidate kernel is installed or supported by the device.
Memory observations and enforcement are backend-specific: unified memory must
not be added to host RAM as if it were a separate physical pool.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendCapabilities:
    name: str
    memory_model: str
    linear_policies: tuple[str, ...]
    attention_candidates: tuple[str, ...]
    pinned_host_weights: bool
    streamed_weights: bool


class DeviceBackend(ABC):
    """Current-device contract. A backend owns placement and its memory policy.

    Streams, events and PCIe scheduling belong to the CUDA offloader, not this
    interface. A unified-memory implementation must supply its own transport.
    Creating a backend must not initialize an accelerator or allocate tensors.
    """
    capabilities: BackendCapabilities
    device: str

    @abstractmethod
    def is_available(self): ...

    @abstractmethod
    def synchronize(self): ...

    @abstractmethod
    def empty_cache(self): ...

    @abstractmethod
    def memory_info(self):
        """Return available/total bytes in the device budget domain.

        A unified-memory backend must define its admitted working-set domain;
        it must not report host RAM plus a second copy of GPU capacity.
        """

    @abstractmethod
    def max_memory_allocated(self): ...

    @abstractmethod
    def max_memory_reserved(self): ...

    @abstractmethod
    def reset_peak_memory_stats(self): ...

    @abstractmethod
    def configure_budget(self, budget_bytes, allocator_limit_bytes=None, *,
                         reserve_bytes=0, capacity_trial=False): ...

    @abstractmethod
    def arithmetic_identity(self):
        """Device and arithmetic flags for cache invalidation, not a parity claim."""

    @abstractmethod
    def attention_kernels(self, global_backend, window_backend, *,
                          query_chunk=0, window_varlen=False): ...

    @abstractmethod
    def prepare_linears(self, model, manifest, linear_compute, fp8_gemm): ...

    @abstractmethod
    def install_chunked_ff(self, module, chunk, *, recompute=False): ...

    @abstractmethod
    def make_offloader(self, layers, **options): ...

    @abstractmethod
    def pin_layer_weights(self, layers, **options): ...

    @abstractmethod
    def host_memory_stats(self): ...

    @abstractmethod
    def prepare_streamed_layer(self, layer, source, index, **options): ...

    @abstractmethod
    def unload_streamed_layer(self, layer, source, index): ...
