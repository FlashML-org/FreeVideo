"""Single-buffer host residuals for the small-VRAM block path.

The container transfers ownership: passing a GPU Tensor directly to Module
would leave it alive in Module's argument tuple throughout both sublayers.
Copies retain dtype and shape; normalization and residual kernels are unchanged.
"""
import time
import torch

# Rows handled per step of the tiled sublayer boundaries below. A bounded
# block holds the residual stream on the card only between the host copies, so
# a whole-stream normalization output or restored copy next to the stream
# itself doubles the largest activation. Tiles keep one stream-sized buffer.
ROW_TILE = 8192


class ResidualState:
    def __init__(self, tensor):
        if torch.is_grad_enabled() or tensor.requires_grad:
            raise RuntimeError('Residual offload requires inference without autograd')
        if not tensor.is_contiguous():
            raise ValueError('Residual offload requires the original contiguous packed layout')
        self.tensor = tensor
        self.shape, self.dtype, self.device = tuple(tensor.shape), tensor.dtype, tensor.device
        self.host = None
        self.pinned = False
        self.stored = False
        self.closed = False
        self.copies = {'to_host': 0, 'to_device': 0}
        self.pinned_fallbacks = 0
        self.copy_wall_seconds = 0.
        # Set by the block loop: collect reference cycles once this block has
        # dropped its input (packing.install_streamed_forward).
        self.collect_after_store = False

    def take(self):
        if self.closed or self.tensor is None:
            raise RuntimeError('Residual has no device value to consume')
        tensor, self.tensor = self.tensor, None
        return tensor

    def _check(self, tensor):
        if self.closed or tuple(tensor.shape) != self.shape or tensor.dtype != self.dtype or tensor.device != self.device or not tensor.is_contiguous():
            raise ValueError('Residual shape, dtype or device changed')

    def store(self, tensor):
        self._check(tensor)
        if self.tensor is not None:
            raise RuntimeError('Consume the device residual before staging it')
        tick = time.perf_counter()
        if self.host is None:
            self.host = self._allocate()
        try:
            self.host.copy_(tensor, non_blocking=self.pinned)
        except torch.cuda.OutOfMemoryError:
            raise
        except RuntimeError as error:
            if not self.pinned:
                raise
            self._leave_locked_pages(keep=False, error=error)
            self.host.copy_(tensor)
        self.stored = True
        self.copies['to_host'] += 1
        self.copy_wall_seconds += time.perf_counter()-tick

    def _allocate(self):
        # Locked pages make both copies asynchronous DMA on the compute
        # stream, so the CPU keeps queueing kernels instead of draining the
        # GPU twice per block. Stream order is all the synchronization this
        # needs: the restore and every later kernel run after the store on the
        # same stream, and the host allocator holds a freed block until the
        # copies recorded on it finish. The planner already keeps this buffer
        # out of the weight cache (residual_host_headroom), so locking it
        # takes no pages that pinned weights were promised. On a 12 GiB
        # RTX 4070 capped at 8 GiB, 1344x768x243, second-pass steps measured
        # 108 -> 96 s with native FP8 and 99 -> 87 s with int8 projections.
        # Pageable storage remains the fallback when pages cannot be locked.
        if self.device.type == 'cuda':
            try:
                host = torch.empty(self.shape, dtype=self.dtype, device='cpu', pin_memory=True)
            except RuntimeError:
                pass
            else:
                self.pinned = True
                return host
        return torch.empty(self.shape, dtype=self.dtype, device='cpu')

    def _leave_locked_pages(self, keep, error):
        # A copy through the locked buffer was refused (Windows can fail a
        # queued copy with "invalid argument" when the system takes pages back).
        # Finish the queued work first, so a sticky error is raised as itself
        # rather than hidden by the retry, then continue in pageable memory,
        # which holds the same bytes and is read back unchanged.
        from .activation_staging import record_refusal
        record_refusal('residual', 'to_device' if keep else 'to_host', error)
        if self.device.type == 'cuda':
            torch.cuda.synchronize(self.device)
        host = torch.empty(self.shape, dtype=self.dtype, device='cpu')
        if keep:
            host.copy_(self.host)
        self.host, self.pinned = host, False
        self.pinned_fallbacks += 1

    def restore(self):
        if self.closed or not self.stored:
            raise RuntimeError('No complete host residual is available')
        tick = time.perf_counter()
        # copy=True also makes CPU contract checks use independent storage.
        try:
            tensor = self.host.to(self.device, copy=True, non_blocking=self.pinned)
        except torch.cuda.OutOfMemoryError:
            raise
        except RuntimeError as error:
            if not self.pinned:
                raise
            self._leave_locked_pages(keep=True, error=error)
            tensor = self.host.to(self.device, copy=True)
        self.copies['to_device'] += 1
        self.copy_wall_seconds += time.perf_counter()-tick
        return tensor

    def tiled(self):
        """True when the stream has more rows than one tile."""
        return self.shape[-2] > ROW_TILE

    def store_through(self, tensor, transform):
        """Stage `tensor` on the host, then overwrite its rows with `transform`.

        `transform(rows, start, stop)` maps a tile of rows to its replacement of
        the same shape. The staged copy of each tile is queued before that tile
        is overwritten on the same stream, so the host receives the original
        rows and the device buffer ends up holding the transformed ones, with
        no second stream-sized buffer. Row-wise kernels give every row the same
        bytes whatever the tile boundaries (row_kernels).
        """
        self._check(tensor)
        if self.tensor is not None:
            raise RuntimeError('Consume the device residual before staging it')
        tick = time.perf_counter()
        if self.host is None:
            self.host = self._allocate()
        for start in range(0, self.shape[-2], ROW_TILE):
            stop = min(start + ROW_TILE, self.shape[-2])
            rows = tensor[..., start:stop, :]
            self.host[..., start:stop, :].copy_(rows, non_blocking=self.pinned)
            rows.copy_(transform(rows, start, stop))
        self.stored = True
        self.copies['to_host'] += 1
        self.copy_wall_seconds += time.perf_counter()-tick
        return tensor

    def restore_into(self, branch, combine):
        """Combine the staged stream with `branch` tile by tile, in place.

        `combine(residual_rows, start, stop, branch_rows)` writes into
        `branch_rows` (a view of `branch`); the staged rows reach the device one
        tile at a time instead of as a second stream-sized copy.
        """
        if self.closed or not self.stored:
            raise RuntimeError('No complete host residual is available')
        if tuple(branch.shape) != self.shape or branch.dtype != self.dtype or branch.device != self.device:
            raise ValueError('Residual shape, dtype or device changed')
        tick = time.perf_counter()
        for start in range(0, self.shape[-2], ROW_TILE):
            stop = min(start + ROW_TILE, self.shape[-2])
            rows = self.host[..., start:stop, :].to(self.device, copy=True, non_blocking=self.pinned)
            combine(rows, start, stop, branch[..., start:stop, :])
            del rows
        self.copies['to_device'] += 1
        self.copy_wall_seconds += time.perf_counter()-tick
        return branch

    def replace(self, tensor):
        self._check(tensor)
        if self.tensor is not None:
            raise RuntimeError('Cannot overwrite an unconsumed device residual')
        self.tensor = tensor

    def stats(self):
        host_bytes = self.host.numel()*self.host.element_size() if self.host is not None else 0
        return dict(host_buffer_bytes=host_bytes, pinned_host_bytes=host_bytes if self.pinned else 0,
                    copies=dict(self.copies), pinned_fallbacks=self.pinned_fallbacks, blocking_copy_wall_seconds=self.copy_wall_seconds,
                    timing_scope=('Locked-page copies are queued asynchronously; this is the time spent issuing them.'
                                  if self.pinned else
                                  'Blocking copy calls can include waiting for prior compute; not pure PCIe transfer time.'))

    def close(self):
        self.tensor = self.host = None
        self.stored = False
        self.closed = True


def residual_forward(pre, post):
    def forward(self, state, temb, adaln_indices, rotary_emb, attention_mask=None):
        if torch.is_grad_enabled():
            raise RuntimeError('Residual offload requires inference without autograd')
        if not isinstance(state, ResidualState):
            raise TypeError('Residual offload needs the owning packed-input container')
        tiled = state.tiled()
        hidden = state.take()
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = self.adaln_proj(temb)
        if tiled:
            # The normalized rows replace the stream rows once they are staged.
            normalized = state.store_through(hidden, lambda rows, start, stop: pre(
                rows, self.norm1.weight, self.norm1.eps, scale_a, shift_a, adaln_indices[start:stop]))
            del hidden
        else:
            normalized = pre(hidden, self.norm1.weight, self.norm1.eps, scale_a, shift_a, adaln_indices)
            state.store(hidden)
            del hidden
        if state.collect_after_store:
            state.collect_after_store = False
            import gc
            gc.collect()
        branch = self.attn(normalized, rotary_emb, attention_mask)
        del normalized
        if tiled:
            hidden = state.restore_into(branch, lambda residual, start, stop, rows: post(
                residual, gate_a, adaln_indices[start:stop], rows))
            del branch
            normalized = state.store_through(hidden, lambda rows, start, stop: pre(
                rows, self.norm2.weight, self.norm2.eps, scale_f, shift_f, adaln_indices[start:stop]))
            del hidden
            branch = self.ff(normalized)
            del normalized
            state.replace(state.restore_into(branch, lambda residual, start, stop, rows: post(
                residual, gate_f, adaln_indices[start:stop], rows)))
            del branch
            return state
        residual = state.restore()
        hidden = post(residual, gate_a, adaln_indices, branch)
        del residual, branch
        normalized = pre(hidden, self.norm2.weight, self.norm2.eps, scale_f, shift_f, adaln_indices)
        state.store(hidden)
        del hidden
        branch = self.ff(normalized)
        del normalized
        residual = state.restore()
        state.replace(post(residual, gate_f, adaln_indices, branch))
        del residual, branch
        return state
    return forward
