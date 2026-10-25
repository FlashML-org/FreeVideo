"""Image failure codes and bounded, redacted diagnostics. No GPU imports."""
from datetime import datetime, timezone
import math
import json
from pathlib import Path, PurePosixPath
import platform
import re
import time
import traceback

from .. import __version__
from ..disk_space import ran_out
from ..exit_diagnostics import clean_reading, clean_sample
from ..diagnostic_resources import commit_exhausted
from ..failure_details import Redactor


ERRORS = frozenset(('no_memory', 'small_memory', 'busy_video', 'busy', 'pack_missing', 'model_unreadable',
                    'device_unavailable', 'worker_exit', 'timeout', 'cancelled', 'disk_full',
                    'invalid_request', 'invalid_media', 'invalid_points', 'segment_not_installed', 'generation_failed'))
NO_REPORT = frozenset(('small_memory', 'busy', 'invalid_request', 'invalid_media', 'invalid_points', 'segment_not_installed', 'cancelled',
                       'disk_full'))
STAGES = ('start', 'load', 'text', 'encode', 'denoise', 'decode')
KINDS = ('generate', 'edit', 'cast', 'compose', 'download', 'install')
MESSAGE_ZH = {
    'no_memory': '显存不够，最小尺寸也没能生成。关掉其他占用显卡的程序后再试。',
    'small_memory': '这台 Mac 的内存不足 16 GB，无法生成图片。',
    'busy_video': '视频正在生成，完成后再试。',
    'busy': '已有一张图在生成，请等它完成。',
    'pack_missing': '请先在启动器里下载“图像生成模型”。',
    'model_unreadable': '图像生成模型的文件读不出来。在启动器里修复后再试。',
    'device_unavailable': '显卡暂时用不了，稍后再试。',
    'worker_exit': '图像生成意外停止了，请再试一次。',
    'timeout': '这一张用时太长，已停止。请再试一次。',
    'disk_full': '保存图片时出错：磁盘空间不够，空出空间后再生成。',
    'generation_failed': '图像生成出错了，请再试一次。',
    'invalid_request': '图片设置有误，请调整后重试。',
    'invalid_media': '图片无效，请重新选择输入目录中的图片。点选支持不超过 4096×4096 像素总数的图片。',
    'invalid_points': '请选择 1–16 个图片范围内的点，且至少有一个选中点；1 为选中，0 为排除。',
    'segment_not_installed': '点选模型未安装，请先下载点选模型（SAM 2.1 small）。',
}
_DEVICE = re.compile(
    r'cuda.*(?:initializ|device.*(?:lost|unavailable|removed)|invalid device ordinal|'
    r'driver.*(?:insufficient|too old|mismatch|shutting down)|context.*destroyed|system not yet initialized)|'
    r'(?:no|not any).*cuda(?:-capable)? (?:gpu|device)|'
    r'(?:no nvidia driver|found no nvidia driver|not compiled with cuda|'
    r'cuda-capable device\(s\) (?:is/are|is|are) busy or unavailable|cuda is not available)|'
    r'(?:gpu|device) (?:has been |was )?(?:lost|removed)|'
    r'nvidia driver.*(?:too old|initializ|not found|unavailable)|'
    r'cudaError(?:InitializationError|DeviceUninitialized|DeviceLost|SystemNotReady|InsufficientDriver)', re.I)
_MODEL = re.compile(
    r'safetensors?|(?:deserializ|invalid|incomplete|too large).*header|'
    r'(?:checkpoint|weights?).*(?:cannot|could not|unable|failed|corrupt|unreadable|mismatch)|'
    r'pytorchstreamreader|unexpected (?:end of file|eof)', re.I)
_MPS_MEMORY = re.compile(r'MPS backend out of memory', re.I)
_LIMIT = 16 * 1024
# On a Mac the GPU's memory is the machine's: the same failure, in its words.
UNIFIED_MESSAGE_ZH = {'no_memory': '内存不够，最小尺寸也没能生成。关掉其他程序后再试。'}
HOST_MESSAGE_ZH = '电脑内存或虚拟内存不够，模型没能载入。关掉其他程序，或调大系统的虚拟内存后再试。'


def host_memory_shortage(error):
    """The machine's own memory ran out, not the card's."""
    return commit_exhausted(error)


class FailureError(ValueError):
    """Admission rejection with the same safe status as the HTTP reply."""
    def __init__(self, status):
        super().__init__(status['error'])
        self.status = dict(status)
        self.code = status['error']
        self.report_id = status['report_id']
        self.stage = status['stage']


def classify_exception(error, *, stage='start', cancelled=False):
    if cancelled:
        return 'cancelled'
    chain, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append(error)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    if chain and commit_exhausted(chain[0]):
        return 'no_memory'
    for item in chain:
        names = {cls.__name__ for cls in type(item).__mro__}
        if isinstance(item, MemoryError) or 'OutOfMemoryError' in names or _MPS_MEMORY.search(str(item)):
            return 'no_memory'
    if chain and ran_out(chain[0]):
        return 'disk_full'
    for item in chain:
        cuda_init = any(frame.f_globals.get('__name__') == 'torch.cuda'
                        and frame.f_code.co_name in ('_lazy_init', '_check_driver')
                        for frame, _ in traceback.walk_tb(item.__traceback__))
        if _DEVICE.search(str(item)) or type(item).__name__ == 'DeferredCudaCallError' or cuda_init:
            return 'device_unavailable'
    for item in chain:
        model_frame = any(frame.f_globals.get('__name__') in (
            'freevideo_engine.image_gen.qwen_weights', 'freevideo_engine.image_gen.qwen_te_weights',
            'freevideo_engine.streamed_weights') for frame, _ in traceback.walk_tb(item.__traceback__))
        if (type(item).__name__ in ('SafetensorError', 'SafetensorsError') or _MODEL.search(str(item))
                or stage == 'load' and isinstance(item, (OSError, EOFError))
                or stage == 'load' and type(item).__name__ == 'JSONDecodeError'
                or model_frame and isinstance(item, (ValueError, KeyError, OSError, EOFError))):
            return 'model_unreadable'
    return 'generation_failed'


def _number(value):
    return (type(value) is int and 0 <= value < 2**64
            or type(value) is float and math.isfinite(value) and value >= 0)


def seed_redactor(redactor, value):
    """Seed before allowlisting; escaped and multiline echoes must also disappear."""
    redactor.structured(value)
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('prompt', 'negative_prompt', 'prompt_text', 'scene', 'name') and isinstance(item, str) and item:
                redactor.prompts.update((item, repr(item)[1:-1], json.dumps(item, ensure_ascii=True)[1:-1],
                                         json.dumps(item, ensure_ascii=False)[1:-1]))
                redactor.prompts.update(line for line in item.splitlines() if line.strip())
            elif isinstance(item, (dict, list)):
                seed_redactor(redactor, item)
    elif isinstance(value, list):
        for item in value:
            seed_redactor(redactor, item)


def clean(value, redactor, depth=0):
    """Reapply redaction at the parent boundary; keep only bounded JSON values."""
    if depth > 8:
        return None
    if isinstance(value, str):
        return redactor.text(value)[:_LIMIT]
    if value is None or type(value) is bool or _number(value):
        return value
    if isinstance(value, dict):
        value = redactor.structured(value)
        return {redactor.text(str(key))[:128]: clean(item, redactor, depth + 1)
                for key, item in list(value.items())[:64]}
    if isinstance(value, (list, tuple)):
        return [clean(item, redactor, depth + 1) for item in value[:64]]
    return None


def safe_plan(value, redactor):
    from dataclasses import fields
    from .image_policy import ImagePlan
    if hasattr(value, 'to_dict'):
        value = value.to_dict()
    if not isinstance(value, dict):
        return None
    return {field.name: clean(value[field.name], redactor) for field in fields(ImagePlan) if field.name in value}


def exception_details(error, redactor, code):
    # Stack locations without source lines or locals: neither can safely enter a report.
    trace, seen = [], set()
    item = error
    while item is not None and id(item) not in seen and len(seen) < 8:
        seen.add(id(item))
        trace.append('Traceback (most recent call last):')
        for frame in traceback.extract_tb(item.__traceback__, limit=32):
            trace.append('  File "%s", line %d, in %s' % (redactor.path(frame.filename), frame.lineno, frame.name))
        trace.append('%s: %s' % (type(item).__name__, str(item)))
        item = item.__cause__ or (None if item.__suppress_context__ else item.__context__)
        if item is not None:
            trace.append('Caused by:')
    return dict(code=code, **{'class': redactor.text(type(error).__name__)[:128],
                            'message': redactor.text(str(error))[:2048],
                            'traceback': redactor.text('\n'.join(trace))[:_LIMIT]})


class StageTimes:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.started = self.entered = self.clock()
        self.stage = 'start'
        self.elapsed = {}

    def enter(self, stage):
        if stage not in STAGES or stage == self.stage:
            return
        now = self.clock()
        self.elapsed[self.stage] = self.elapsed.get(self.stage, 0.) + max(0., now - self.entered)
        self.stage, self.entered = stage, now

    def snapshot(self):
        now = self.clock()
        elapsed = dict(self.elapsed)
        elapsed[self.stage] = elapsed.get(self.stage, 0.) + max(0., now - self.entered)
        return [dict(stage=stage, seconds=round(seconds, 6)) for stage, seconds in elapsed.items()]

    def seconds(self):
        return max(0., self.clock() - self.started)


class Context:
    def __init__(self, action, value):
        self.redactor = Redactor()
        self.timing = StageTimes()
        self.kind = action if action in KINDS else 'generate'
        self.request = {}
        self.machine = {}
        self.plan = None
        self.pack = None
        self.start_memory = None
        # ComfyUI's own models this request unloaded before its worker started (comfy_residency.release_native).
        self.native_release = None
        self.update_request(value)

    def note_start(self):
        """Memory readings when the request begins; the worker's own are lost if it is ended."""
        from ..exit_diagnostics import memory_sample
        self.start_memory = memory_sample()

    def update_request(self, value):
        if not isinstance(value, dict):
            return
        seed_redactor(self.redactor, value)
        images = value.get('images')
        references = len(images) if isinstance(images, list) else 0
        if self.kind == 'compose' and isinstance(value.get('people'), list):
            references = len(value['people']) + int(bool(value.get('scene_image')))
        if self.kind in ('generate', 'edit', 'cast'):
            self.kind = value.get('kind') if value.get('kind') in ('generate', 'edit', 'cast') else ('edit' if references else 'generate')
        self.request = dict(references=references)
        for key in ('width', 'height', 'steps', 'seed'):
            item = value.get(key)
            if type(item) is int and -2**63 <= item < 2**64 and (key == 'seed' or item > 0):
                self.request[key] = item

    def set_machine(self, machine):
        if isinstance(machine, dict):
            seed_redactor(self.redactor, machine)
            self.machine = machine

    def report(self, code, *, row=None, error=None, video_busy=False, worker_restarts=0):
        row = row if isinstance(row, dict) else {}
        seed_redactor(self.redactor, row)
        stage = row.get('stage') if row.get('stage') in STAGES else self.timing.stage
        details = exception_details(error, self.redactor, code) if error is not None else row.get('error')
        details = details if isinstance(details, dict) else {}
        details = {key: self.redactor.text(str(details.get(key) or ''))[:limit]
                   for key, limit in (('class', 128), ('message', 2048), ('traceback', _LIMIT))}
        details['code'] = code
        stages = row.get('stages')
        if isinstance(stages, list):
            stages = [dict(stage=item['stage'], seconds=item['seconds']) for item in stages[:32]
                      if isinstance(item, dict) and item.get('stage') in STAGES and _number(item.get('seconds'))]
        if not stages:
            stages = self.timing.snapshot()
        observed = {}
        # Older installations keep this inventory in the setup receipt. Reuse
        # the video's device matching rule; never start a CUDA probe to report failure.
        if self.machine.get('setup_run'):
            try:
                from ..effort_forecast import same_device
                device = (self.machine.get('device_identity') if self.machine.get('device_backend') == 'mps'
                          else self.machine.get('gpu_uuid'))
                with (Path(self.machine['setup_run']) / 'plan.json').open('rb') as stream:
                    raw = stream.read(1024 * 1024 + 1)
                if len(raw) <= 1024 * 1024:
                    found = json.loads(raw).get('inventory', {}).get('hardware', {})
                    if isinstance(found, dict) and same_device(found, device):
                        observed = {key: found[key] for key in ('gpu_name', 'vram_total', 'ram_total', 'system') if key in found}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
        if isinstance(self.machine.get('hardware'), dict):
            observed.update(self.machine['hardware'])
        device = self.machine.get('device_identity')
        device = device if isinstance(device, dict) else {}
        hardware = dict(gpu_name=observed.get('gpu_name') or self.machine.get('gpu_name') or device.get('name'),
                        system=observed.get('system') or self.machine.get('system') or platform.system())
        for key in ('vram_total', 'ram_total'):
            item = observed.get(key) or self.machine.get(key) or device.get('unified_ram_bytes')
            hardware[key] = item if _number(item) else None
        if hardware['ram_total'] is None:
            try:
                from ..system import system_memory
                hardware['ram_total'] = system_memory()['total_bytes']
            except (OSError, ValueError, KeyError):
                pass
        pack = None
        if isinstance(self.pack, dict):
            missing = self.pack.get('missing', [])
            missing = missing if isinstance(missing, list) else []
            pack = dict(ready=bool(self.pack.get('ready')), missing=[name for name in missing[:64]
                        if isinstance(name, str) and not PurePosixPath(name).is_absolute()
                        and not any(part in ('..', '.') for part in name.split('/'))
                        and not any(char in name for char in ('\\', ':'))])
            size = self.pack.get('total_bytes')
            if _number(size):
                pack['bytes'] = size
        hardware = {key: clean(value, self.redactor) for key, value in hardware.items()}
        geometry = {key: self.request[key] for key in ('width', 'height') if key in self.request}
        plan = safe_plan(row.get('plan') if row.get('plan') is not None else self.plan, self.redactor)
        # Macs only: which attention kernel ran and why it left MLX (mlx_attention.summary).
        attention = row.get('attention')
        extra = dict(attention=clean(attention, self.redactor)) if isinstance(attention, dict) else {}
        return dict(schema='freevideo.image-report', schema_version=1, **extra,
                    created_at=datetime.now(timezone.utc).isoformat(), kind=self.kind,
                    analysis=dict(version=__version__, hardware=hardware, geometry=geometry),
                    summary=dict(request_seconds=self.timing.seconds()), request=dict(self.request),
                    plan=plan, stages=stages, stage_at_failure=stage, error=details, pack=clean(pack, self.redactor),
                    service=self.service(video_busy, worker_restarts, error))

    def service(self, video_busy, worker_restarts, error):
        service = dict(video_busy=bool(video_busy), worker_restarts=worker_restarts)
        if self.start_memory:
            service['memory_at_request_start'] = clean_sample(self.start_memory)
        released = self.native_release
        if isinstance(released, dict):
            service['comfy_models_released'] = {key: released[key] for key in
                ('models', 'reserved_bytes', 'reserved_after_bytes', 'released') if type(released.get(key)) in (int, bool)}
        # Numbers and fixed words only, taken by the service when it saw the worker end.
        reading = clean_reading(getattr(error, 'exit_reading', None))
        if reading:
            service['worker_exit'] = reading
        return service
