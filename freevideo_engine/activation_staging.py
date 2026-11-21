"""Contiguous D2H head groups, joined into projection tiles on the device."""
import collections
import contextlib
import torch

# The first copies through a freshly locked buffer, and the first tiles of its
# first projection, are followed by a wait for the device. A deferred CUDA
# "invalid argument" from a queued locked copy is otherwise reported by
# whichever later call happens to run (reports on the second pass's first layer
# name three different statements), outside the handler that continues in
# pageable memory. Waiting here raises it inside that handler, with the stage
# named. A handful of waits per buffer set, none afterwards.
CHECKED_COPIES = 3

# Every refused locked-page copy of this process, residual stream and head
# readouts alike, so a report shows whether the fallback fired, which copy was
# refused and what CUDA said. The newest entries are kept; the count is exact.
_REFUSED = collections.deque(maxlen=32)
_refused_total = 0


def record_refusal(source, copy, error, checked=False):
    global _refused_total
    lines = str(error).strip().splitlines()
    row = dict(source=source, copy=copy, error=(lines[0] if lines else '')[:200])
    if checked:
        row['checked'] = True  # raised by the wait after one of the first copies, not by the copy call
    _REFUSED.append(row)
    _refused_total += 1


@contextlib.contextmanager
def checked(active, stage, **fields):
    """Wait for the device after `stage` while `active`; name the stage on a CUDA error."""
    try:
        yield
        if active and torch.cuda.is_initialized():
            torch.cuda.synchronize()
    except RuntimeError as error:
        if active and not isinstance(error, torch.cuda.OutOfMemoryError) and hasattr(error, 'add_note'):
            error.add_note('checked stage: %s %s' % (stage, ' '.join('%s=%s' % item for item in fields.items())))
        raise


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

    def __init__(self, checked_copies=CHECKED_COPIES):
        self.locked = True
        self.fallbacks = 0
        # One budget per direction: the host-to-device copies (the report's refused statement) are not
        # used up by the device-to-host copies that come first in a layer.
        self.unchecked = dict(to_host=checked_copies, to_device=checked_copies)

    def _leave(self, device, copy, error, waited=False):
        record_refusal('readout', copy, error, checked=waited)
        if device.type == 'cuda':
            torch.cuda.synchronize(device)
        self.locked = False
        self.fallbacks += 1

    def _wait(self, device, copy):
        # Inside the handler of the copy that was just queued.
        if self.unchecked[copy] > 0:
            self.unchecked[copy] -= 1
            if device.type == 'cuda':
                torch.cuda.synchronize(device)
                return True
        return False

    def to_host(self, destination, source):
        if self.locked:
            waited = False
            try:
                destination.copy_(source, non_blocking=True)
                waited = True  # from here on an error comes from the wait
                self._wait(source.device, 'to_host')
                return
            except torch.cuda.OutOfMemoryError:
                raise
            except RuntimeError as error:
                self._leave(source.device, 'to_host', error, waited)
        destination.copy_(source.to('cpu'))

    def to_device(self, source, device):
        if self.locked:
            waited = False
            try:
                value = source.to(device, non_blocking=True)
                waited = True  # from here on an error comes from the wait
                self._wait(torch.device(device), 'to_device')
                return value
            except torch.cuda.OutOfMemoryError:
                raise
            except RuntimeError as error:
                self._leave(torch.device(device), 'to_device', error, waited)
        return source.clone().to(device)


class HeadReadouts:
    def __init__(self, rows, head_widths, dtype, tile_rows):
        self.capacity = (rows + 1023) // 1024 * 1024
        self.widths = tuple(head_widths)
        self.dtype = dtype
        self.tile_rows = tile_rows
        self.groups = [torch.empty((self.capacity, width), dtype=dtype, pin_memory=True) for width in head_widths]
        self.copies = LockedCopies()

    def supports(self, rows, head_widths, dtype, tile_rows):
        return rows <= self.capacity and tuple(head_widths) == self.widths and dtype == self.dtype and tile_rows == self.tile_rows

    def store(self, group, values):
        if values.shape[1] != self.widths[group] or not values.is_contiguous():
            raise ValueError('Head readouts must be contiguous [rows, head channels]')
        self.copies.to_host(self.groups[group][:len(values)], values)

    def project(self, module, output, rows):
        # The caller synchronizes once after all D2H groups have been written.
        # Each group's rows of a tile are contiguous on the host, so they go to the
        # device as they are and the tile is joined there. Joining it on the host,
        # one strided copy per group and tile, kept the processor busy for most of
        # the second pass at 1344x768x362 while the device waited (about 200k
        # small copies a step). The tile holds the same values in the same rows,
        # so the projection is unchanged bit for bit. The host-to-device copies and
        # the next layer's device-to-host stores share the current stream, so a
        # group is not overwritten before its rows have been read.
        width = sum(self.widths)
        for start in range(0, rows, self.tile_rows):
            count = min(self.tile_rows, rows - start)
            device_values = torch.empty((count, width), dtype=self.dtype, device=output.device)
            offset = 0
            for group, group_width in zip(self.groups, self.widths):
                part = self.copies.to_device(group[start:start + count], output.device)
                device_values[:, offset:offset + group_width].copy_(part)
                del part
                offset += group_width
            yield slice(start, start + count), module(device_values)
