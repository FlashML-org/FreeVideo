"""Dedicated VRAM for each text-encoder forward pass, sized from its real input.

ComfyUI loads as many encoder weights as its reserve allows and streams the
rest. The forward then needs working memory that grows with the sequence the
language model actually sees: text tokens plus the expanded tokens of every
reference image and video block (the token list holds one entry per image).
When that room is short, Linux raises out-of-memory and the retry leaves more.
Windows does not: the driver places the overflow in shared system memory and
the encode runs many times slower. A reference request on a 16 GB RTX 4060 Ti
sat there for over ten minutes with zero dedicated VRAM free.

Every encode therefore sizes its room from the input before loading, checks
the dedicated room really free after loading (moving weights back to host
memory until it fits), and switches long inputs to the block-wise language
model (encoder_lowmem) when the whole-sequence forward cannot fit at all. On
Windows a sampler stops a forward that still outgrows its room so recovery can
retry with more. What each forward used is kept per machine and input kind.
"""
import json
import math
from pathlib import Path
import sys
import threading
import time

GiB, MiB = 2**30, 2**20
LOW_MEMORY_CHUNK = 2048
# From here the block-wise forward runs even when the whole sequence fits: the same
# math without the dense causal mask, 15.7k tokens in 7.2 s instead of 9.9 s in BF16.
WHOLE_BLOCK_TOKENS = 4096
# Upper bounds of the measured working memory (allocator peak above the loaded
# weights) of the 32B H3 encoder running in BF16, partially loaded as on a 16 GB
# card, for text from 480 to 2880 tokens, one to eight reference images up to
# 1920x1080 and reference videos up to two 15 s clips (34k tokens). Per sequence
# token and per patch of the largest image or video block, which the vision
# tower runs alone. Measured peaks: whole 0.49-15.17 GiB, blocks 0.76-8.11 GiB.
FORMULA = {'whole': (1.5 * GiB, 0.45 * MiB, 0.30 * MiB),
           'blocks': (1.5 * GiB, 0.20 * MiB, 0.30 * MiB)}
HEADROOM, MARGIN = 1.1, 256 * MiB
SPILL_MARGIN = 256 * MiB
HISTORY_NAME = 'encoder-workspace.json'
KEEP = 32
RECENT = 8


class SharedMemorySpill(RuntimeError):
    """The forward outgrew the dedicated VRAM that was free when it started."""

    def __init__(self, used_bytes, spilled_bytes):
        super().__init__('Text encoding spilled %.2f GiB into shared GPU memory' % (spilled_bytes / GiB))
        self.used_bytes, self.spilled_bytes = int(used_bytes), int(spilled_bytes)


def smart_grid(height, width, factor=32, min_pixels=3136, max_pixels=12845056, patch=16):
    """Patch grid of one image or video block after the native Qwen3-VL resize."""
    h_bar, w_bar = round(height / factor) * factor, round(width / factor) * factor
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt(height * width / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar, w_bar = math.ceil(height * beta / factor) * factor, math.ceil(width * beta / factor) * factor
    return (h_bar // patch) * (w_bar // patch)


def input_size(tokens):
    """The language model's real sequence length and the largest vision-tower input."""
    entries = []
    if isinstance(tokens, dict):
        batches = tokens.get('qwen3vl_32b') or []
        entries = [entry for batch in batches if isinstance(batch, (list, tuple)) for entry in batch]
    text = vision = largest = blocks = 0
    for entry in entries:
        value = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
        data = value.get('data') if isinstance(value, dict) else None
        if data is not None and hasattr(data, 'shape') and len(data.shape) == 4:
            grid = smart_grid(int(data.shape[1]), int(data.shape[2]))
            vision += grid // 4
            largest = max(largest, grid)
            blocks += 1
        else:
            text += 1
    return dict(text_tokens=text, vision_tokens=vision, sequence=text + vision, largest_patches=largest,
                vision_entries=blocks)


def formula(mode, size):
    fixed, per_token, per_patch = FORMULA[mode]
    return fixed + per_token * size['sequence'] + per_patch * size['largest_patches']


class Workspace:
    """Room estimates for this machine: the measured bound, raised by what forwards really used."""

    def __init__(self, history=None, machine='default'):
        self.history = Path(history) if history else None
        self.machine = machine
        self.data = {}
        if self.history is not None:
            try:
                value = json.loads(self.history.read_text(encoding='utf-8'))
                if value.get('schema') == 1 and isinstance(value.get('machines'), dict):
                    self.data = value
            except (OSError, ValueError, AttributeError):
                pass
        self.data.setdefault('schema', 1)
        self.data.setdefault('machines', {})

    @classmethod
    def for_encoder(cls, torch, checkpoint):
        props = torch.cuda.get_device_properties(torch.cuda.current_device())
        path = Path(checkpoint)
        machine = '|'.join((sys.platform, props.name, str(round(props.total_memory / GiB)), path.name,
                            str(path.stat().st_size)))
        try:
            from .paths import data_root
            history = data_root() / HISTORY_NAME
        except Exception:
            history = None
        return cls(history, machine)

    def scale(self, mode):
        """How far this machine's recent forwards exceeded the measured bound's input-dependent part.

        Only the latest forwards count, so a driver or code change that fixed an
        overflow stops reserving extra memory after a few encodes.
        """
        rows = self.data['machines'].get(self.machine, {}).get(mode, [])[-RECENT:]
        return max([1.] + [row[2] for row in rows if isinstance(row, list) and len(row) > 2
                           and isinstance(row[2], (int, float)) and math.isfinite(row[2])])

    def need(self, mode, size):
        fixed = FORMULA[mode][0]
        variable = (formula(mode, size) - fixed) * min(4., self.scale(mode))
        return int((fixed + variable) * HEADROOM + MARGIN)

    def learn(self, mode, size, used, spilled=False):
        if not used or used <= 0:
            return
        fixed = FORMULA[mode][0]
        variable = formula(mode, size) - fixed
        ratio = (used - fixed) / variable if variable > 0 else 1.
        rows = self.data['machines'].setdefault(self.machine, {}).setdefault(mode, [])
        rows.append([int(size['sequence']), int(used), round(max(0., ratio), 4), bool(spilled), round(time.time())])
        del rows[:-KEEP]
        if self.history is not None:
            try:
                from .monitoring import save
                save(self.history, self.data)
            except OSError:
                pass  # Learning only refines the bound; the encode itself does not depend on it.


def dedicated_room(torch, reader=None, budget=None):
    """Bytes a new allocation can use without leaving dedicated VRAM or this request's budget.

    PyTorch's cached free blocks count. cudaMemGetInfo is not the WDDM budget:
    after a load that left 1.9 GiB by its count, the 4060 Ti above reported no
    free memory at all, so on Windows the DXGI budget bounds it as well.
    `budget` is what this process may hold in total under FreeVideo's plan.
    """
    free = torch.cuda.mem_get_info()[0]
    if reader is not None:
        try:
            local = reader.sample()['local']
            free = min(free, max(0, local['budget_bytes'] - local['usage_bytes']))
        except (OSError, KeyError):
            pass
    room = free + torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
    if budget is not None:
        room = min(room, budget - torch.cuda.memory_allocated())
    return int(max(0, room))


def adapter_reader():
    if sys.platform != 'win32':
        return None
    try:
        from .windows_gpu_memory import AdapterMemory
        return AdapterMemory()
    except Exception:
        return None


def make_room(patcher, torch, need, reader=None, budget=None):
    """Move loaded weights back to host memory until `need` bytes of dedicated VRAM are free."""
    before = dedicated_room(torch, reader, budget)
    result = dict(need_bytes=int(need), room_before_bytes=before, loaded_before_bytes=int(patcher.loaded_size()))
    if before < need:
        unloaded = patcher.partially_unload(patcher.offload_device, need - before)
        result.update(unloaded_bytes=int(unloaded or 0), room_after_bytes=dedicated_room(torch, reader, budget),
                      loaded_after_bytes=int(patcher.loaded_size()))
    return result


def plan(workspace, size, capacity, attempt=0, failed=None):
    """Mode and room for this attempt: the whole-sequence forward when it fits, else blocks.

    `capacity` is the dedicated room with no encoder weights on the device. After
    a spill or an out-of-memory, whole-sequence forwards move to blocks and block
    forwards ask for half again as much room.
    """
    whole, blocks = workspace.need('whole', size), workspace.need('blocks', size)
    mode = 'whole' if failed is None and whole <= capacity else 'blocks'
    need = whole if mode == 'whole' else blocks
    if failed == 'blocks':
        need = int(need * 1.5 ** attempt)
    return mode, min(need, max(capacity, 0)), dict(whole_need_bytes=whole, blocks_need_bytes=blocks,
                                                    capacity_bytes=int(capacity))


class SpillGuard:
    """Measure one forward's working memory; on Windows, stop it before it runs from shared memory.

    WDDM does not fail an allocation past the dedicated budget, so nothing
    raises. A sampler compares the allocator's growth with the dedicated room
    free at the start; once it is exceeded, the next module call raises
    SharedMemorySpill and the recovery retries with more room.
    """

    def __init__(self, torch, reader=None, interval=.25):
        self.torch, self.reader, self.interval = torch, reader, interval
        self.hook = self.thread = None
        self.spill = None
        self.base_allocated = 0

    def arm(self):
        torch = self.torch
        self.disarm()
        self.spill = None
        self.base_allocated = torch.cuda.memory_allocated()
        self.base_reserved = torch.cuda.memory_reserved()
        torch.cuda.reset_peak_memory_stats()
        self.room = None
        if self.reader is not None:
            try:
                local = self.reader.sample()['local']
                self.room = max(0, local['budget_bytes'] - local['usage_bytes'])
            except (OSError, KeyError):
                self.room = None
        if self.room is None:
            return
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, name='freevideo-encoder-spill', daemon=True)
        self.thread.start()
        self.hook = torch.nn.modules.module.register_module_forward_pre_hook(self._check)

    def _watch(self):
        while not self.stop.wait(self.interval):
            grown = self.torch.cuda.memory_reserved() - self.base_reserved
            if grown - self.room > SPILL_MARGIN:
                self.spill = (self.used(), grown - self.room)
                return

    def _check(self, module, arguments):
        if self.spill is not None:
            raise SharedMemorySpill(*self.spill)

    def used(self):
        """Working memory beyond the loaded weights: cached blocks it reused plus new ones."""
        return max(0, int(self.torch.cuda.max_memory_reserved() - self.base_allocated))

    def disarm(self):
        if self.hook is not None:
            self.hook.remove()
            self.hook = None
        if self.thread is not None:
            self.stop.set()
            self.thread.join()
            self.thread = None
