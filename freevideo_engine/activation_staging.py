"""Contiguous D2H head groups with double-buffered CPU projection tiles."""
import collections
import torch

# Every refused locked-page copy of this process, residual stream and head
# readouts alike, so a report shows whether the fallback fired, which copy was
# refused and what CUDA said. The newest entries are kept; the count is exact.
_REFUSED = collections.deque(maxlen=32)
_refused_total = 0


def record_refusal(source, copy, error):
    global _refused_total
    lines = str(error).strip().splitlines()
    _REFUSED.append(dict(source=source, copy=copy, error=(lines[0] if lines else '')[:200]))
    _refused_total += 1


def refusals_mark():
    return _refused_total


def refusals_since(mark):
    """Refusals recorded after `mark`, as plain dicts for the sampling metrics."""
    count = min(_refused_total - mark, len(_REFUSED))
    return [dict(row) for row in list(_REFUSED)[len(_REFUSED) - count:]] if count > 0 else []


class LockedCopies:
    """Asynchronous copies through page-locked buffers that continue in pageable memory.

    Windows can refuse a queued copy with "invalid argument" when the system
    takes locked pages back. The queued work is finished first, so a sticky
    error is raised as itself rather than hidden by the retry. The copy then
    goes through a pageable temporary, which holds the same bytes, and the
    remaining copies of these buffers skip the locked path. A failed device
    allocation is not a refusal: it is raised for the caller to recover from,
    and the buffers stay locked.
    """

    def __init__(self):
        self.locked = True
        self.fallbacks = 0

    def _leave(self, device, copy, error):
        record_refusal('readout', copy, error)
        if device.type == 'cuda':
            torch.cuda.synchronize(device)
        self.locked = False
        self.fallbacks += 1

    def to_host(self, destination, source):
        if self.locked:
            try:
                destination.copy_(source, non_blocking=True)
                return
            except torch.cuda.OutOfMemoryError:
                raise
            except RuntimeError as error:
                self._leave(source.device, 'to_host', error)
        destination.copy_(source.to('cpu'))

    def to_device(self, source, device):
        if self.locked:
            try:
                return source.to(device, non_blocking=True)
            except torch.cuda.OutOfMemoryError:
                raise
            except RuntimeError as error:
                self._leave(torch.device(device), 'to_device', error)
        return source.clone().to(device)


class HeadReadouts:
    def __init__(self, rows, head_widths, dtype, tile_rows):
        self.capacity = (rows + 1023) // 1024 * 1024
        self.widths = tuple(head_widths)
        self.dtype = dtype
        self.tile_rows = tile_rows
        self.groups = [torch.empty((self.capacity, width), dtype=dtype, pin_memory=True) for width in head_widths]
        self.tiles = [torch.empty((tile_rows, sum(head_widths)), dtype=dtype, pin_memory=True) for _ in range(2)]
        self.events = [None, None]
        self.copies = LockedCopies()

    def supports(self, rows, head_widths, dtype, tile_rows):
        return rows <= self.capacity and tuple(head_widths) == self.widths and dtype == self.dtype and tile_rows == self.tile_rows

    def store(self, group, values):
        if values.shape[1] != self.widths[group] or not values.is_contiguous():
            raise ValueError('Head readouts must be contiguous [rows, head channels]')
        self.copies.to_host(self.groups[group][:len(values)], values)

    def project(self, module, output, rows):
        # The caller synchronizes once after all D2H groups have been written.
        stream = torch.cuda.current_stream(output.device)
        for index, start in enumerate(range(0, rows, self.tile_rows)):
            slot = index % 2
            if self.events[slot] is not None:
                self.events[slot].synchronize()
            count = min(self.tile_rows, rows - start)
            tile = self.tiles[slot][:count]
            offset = 0
            for group, width in zip(self.groups, self.widths):
                tile[:, offset:offset + width].copy_(group[start:start + count])
                offset += width
            device_values = self.copies.to_device(tile, output.device)
            done = torch.cuda.Event()
            done.record(stream)
            self.events[slot] = done
            yield slice(start, start + count), module(device_values)
