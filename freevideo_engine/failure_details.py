"""Readable, copyable failures for interactive callers. No GPU imports."""
import json
import errno
from pathlib import Path
import re
import sys

from .long_paths import io_path, plain_text
from .locking import ENGINE_BUSY
from .diagnostics import Redactor
from .tiers import STEP_CACHES, TIERS, on_mac, table_level
from .two_pass import SCALES


STEP_OUTPUT = '\n\nFull step output:\n'
QUALITY_LEVELS = (('Light', '轻量'), ('Medium', '标准'), ('High', '精细'), ('Max', '极致'))  # tiers.TIERS order
ENGINE_BUSY_TITLE = ('FreeVideo is busy with another task', 'FreeVideo 正在处理另一个任务')
ENGINE_BUSY_DETAIL = (
    'Try again when it finishes. If nothing else is running, close duplicate FreeVideo instances, then try again.',
    '请在它完成后重试。若没有其他任务在进行，请关闭重复运行的 FreeVideo 后重试。',
)


IMAGE_PACK_FAILURES = {
    'disk_space': ('Not enough disk space to install the image model.', '磁盘空间不足，无法安装图像模型。'),
    'offline': ('The network is unavailable; the image model could not be downloaded.', '网络不通，无法下载图像模型。'),
    'source_failed': ("The image model download didn't finish. Check your network and try again.", '图像模型没有下载完成，请检查网络后重试。'),
    'integrity_failed': ('The image model files are incomplete or damaged. Repair them in the launcher.', '图像模型文件不完整或已损坏，请在启动器里修复。'),
    'write_failed': ('The image model files are in use or cannot be written or deleted.', '图像模型文件被占用，或无法写入／删除。'),
    'invalid_catalog': ('The image model package version is incompatible; update FreeVideo and try again.', '图像模型包版本不匹配，请更新 FreeVideo 后重试。'),
    'busy': ('Another task is using the image model package; try again when it finishes.', '图像模型包被其他任务占用，完成后再试。'),
    'busy_video': ('A video is using the graphics card; try again when it finishes.', '视频正在使用显卡，完成后再试。'),
    'needs_repair': ('The image model files are incomplete or damaged; repair them in the launcher.', '图像模型文件不完整或已损坏，请在启动器里修复。'),
    'changed': ('The image model files changed during the check; they were kept, so check again.', '图像模型文件在检查期间发生变化，已保留，请重新检查。'),
    'unsafe_path': ('The image model folder contains a link that cannot be safely changed.', '图像模型目录含有无法安全修改的链接。'),
    'failed': ('The image model operation stopped; see the diagnostic report for details.', '图像模型操作已停止，详情见诊断报告。'),
}


def image_pack_error_code(error):
    """Keep specific pack reasons and OS causes through wrapper exceptions."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        reason = getattr(error, 'reason', None)
        if reason in IMAGE_PACK_FAILURES and reason != 'failed':
            return reason
        if isinstance(error, OSError):
            if error.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', errno.ENOSPC)) or getattr(error, 'winerror', None) == 112:
                return 'disk_space'
            if (isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM, errno.EROFS, errno.EBUSY, errno.ETXTBSY)
                    or getattr(error, 'winerror', None) in (5, 32, 33)):
                return 'write_failed'
            if isinstance(error, BlockingIOError):
                return 'busy'
            if isinstance(error, (ConnectionError, TimeoutError)):
                return 'offline'
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return 'failed'


def image_pack_failure(code, *, zh=False):
    return dict(kind='image-pack', title=('图像模型操作未完成' if zh else 'Image model operation stopped'),
                detail=IMAGE_PACK_FAILURES.get(code, IMAGE_PACK_FAILURES['failed'])[bool(zh)], action='')


def sampling_table_details(value):
    """Read the worker's bounded, numeric sampling-table failure metadata."""
    from .sampling_assets import UNAVAILABLE
    for match in re.finditer(r'^(?:(?:[\w.]*(?:Error|Exception)|(?:[\w.]+\.)?SamplingTableUnavailable):\s*)?'
                             + re.escape(UNAVAILABLE) + r'(\{[^\n]*\})$',
                             str(value)[:65536], re.M):
        try:
            details = json.loads(match[1])
            if (type(details['base_steps']) is int and 1 <= details['base_steps'] <= 32
                    and type(details['two_pass']) is bool
                    and type(details['refine_steps']) is int and 0 <= details['refine_steps'] <= 31
                    and type(details['missing_bytes']) is int and details['missing_bytes'] > 0
                    and type(details['decimal_sizes']) is bool and isinstance(details['available'], list)
                    and all(type(i) is int and 0 <= i <= 3 for i in details['available'])
                    and details.get('step_cache') in STEP_CACHES and details.get('scale', 2) in SCALES):
                return {key: details[key] for key in
                        ('base_steps', 'two_pass', 'refine_steps', 'missing_bytes', 'available', 'decimal_sizes', 'step_cache',
                         'scale')
                        if key in details}
        except (ValueError, KeyError, TypeError):
            pass
    return None


def sampling_table_failure(value, *, zh=False):
    details = sampling_table_details(value)
    if details is None:
        return None
    from .launcher_copy import size_text
    language = 1 if zh else 0
    # Tables follow the step count: 16 steps is high, 20 steps is max.
    level = table_level(details['base_steps'], details['two_pass'], details['refine_steps'],
                        details.get('step_cache'), on_mac(), details.get('scale', 2))
    index = list(TIERS).index(level) if level else None
    names = [('“%s”' if zh else '%s') % QUALITY_LEVELS[i][language] for i in sorted(set(details['available']))]
    available = ''
    if names:
        available = names[0] if len(names) == 1 else ('、' if zh else ', ').join(names[:-1]) + ('或' if zh else ' or ') + names[-1]
    size = size_text(details['missing_bytes'], details['decimal_sizes'])
    # A custom step count is not a quality level (Iris): no "档" / "quality level" for it.
    if zh:
        if index is None:
            title = '“自定义（%d 步）”还没有下载' % details['base_steps']
            what = '这个步数需要的文件'
        else:
            title = '“%s”还没有下载' % QUALITY_LEVELS[index][1]
            what = '这一档需要的文件'
        detail = ('这次生成要先下载%s（约 %s），但没能下载完。'
                  '请确认网络连接后再生成一次，FreeVideo 会自动下载。') % (what, size)
        if available:
            detail += '也可以改用已经可以使用的%s档。' % available
    else:
        if index is None:
            title = 'The sampling table for Custom (%d steps) is not downloaded yet' % details['base_steps']
            what = 'the sampling table for this step count'
        else:
            title = "The %s quality level's sampling table is not downloaded yet" % QUALITY_LEVELS[index][0]
            what = "that level's sampling table"
        detail = ('This video first needs %s (about %s), and it could not be downloaded this time. '
                  'Check the network connection and generate again; FreeVideo downloads it automatically.') % (what, size)
        if available:
            detail += ' You can also switch to a quality level that is already available: %s.' % available
    return dict(kind='sampling-table', title=title, detail=detail, action='')


def redacted_launcher_error(value, replacements=()):
    redactor = Redactor(replacements)
    # Seed structured secrets (including GPU UUIDs) from a doctor's full JSON
    # before redacting the surrounding exception or retained launcher log.
    output = str(value).partition(STEP_OUTPUT)[2].lstrip()
    if output.startswith('{'):
        try:
            report, _ = json.JSONDecoder().raw_decode(output)
            redactor.structured(report)
        except ValueError:
            pass
    return redactor.text(value)


def display_launcher_error(value, *, zh=False, replacements=()):
    """UI text only; diagnostics retain redacted_launcher_error's full cause."""
    from .offline_errors import alias_failure, guidance
    if alias_failure(value):
        return guidance('runtime-alias', zh)
    text = redacted_launcher_error(value, replacements)
    if zh:
        text = re.sub(r'Missing curl, needed for downloads and proxy connections\. '
                      r'(In a terminal, run: ([^\r\n]+?), then check again\.|'
                      r'Ask your administrator to install curl, then check again\.)'
                      r'( You can also run \./setup\.sh in the source folder\.)?',
                      lambda match: '缺少 curl，下载和代理连接需要它。' +
                      ('请在终端运行：' + match.group(2) + '，然后重新检查。' if match.group(2) else
                       '请管理员安装 curl，然后重新检查。') +
                      ('也可以在源码目录运行 ./setup.sh。' if match.group(3) else ''), text)
    return text


def setup_command_failure(label, log, exit_code, report=None):
    """Keep the complete failed step, with required GPU failures before metadata."""
    log = Path(log)
    try:
        output = log.read_text(encoding='utf-8', errors='replace')
    except OSError as error:
        output = 'Could not read the retained step log: ' + str(error)
    lines = ['Failed step: %s (exit code %s)' % (label, exit_code)]
    if label == 'kernels':
        try:
            report = json.loads(Path(report or log.parent / 'kernel-capabilities.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            report = {}
        if isinstance(report, dict):
            probes = report.get('kernel_probes', [])
            probes = [row for row in probes if isinstance(row, dict)] if isinstance(probes, list) else []
            linear_ok = any(row.get('backend') == 'linear' and row.get('status') == 'complete' for row in probes)
            attention_ok = any(row.get('backend') not in (None, 'linear') and row.get('status') == 'complete' for row in probes)
            if probes and not linear_ok:
                lines.append('Required linear kernel did not pass.')
            if probes and not attention_ok:
                lines.append('No attention backend passed.')
            for row in probes:
                # An optional backend failure is not the reason setup stopped
                # when another attention backend passed.
                if row.get('status') == 'complete' or (row.get('backend') != 'linear' and attention_ok):
                    continue
                reason = row.get('error') or row.get('stderr_tail') or row.get('stdout_tail') or row.get('status', 'unknown')
                lines.append('%s: %s' % (row.get('backend', 'unknown'), reason))
            paths = report.get('paths', {})
            if isinstance(paths, dict):
                for name, row in paths.items():
                    if isinstance(row, dict) and row.get('exists') is False:
                        lines.append('Required path missing (%s): %s' % (name, row.get('path', 'unknown')))
        if len(lines) == 1:
            # The doctor can fail before it writes a JSON report, e.g. during
            # an import. Keep that exception visible above the full traceback.
            causes = re.findall(r'^[\w.]+(?:Error|Exception):[^\n]*', output, re.M)
            if causes:
                lines.append(causes[-1])
    lines.append('Retained step log: ' + str(log))
    return '\n'.join(lines) + STEP_OUTPUT + output


# Leads the error of a step that needed an environment's Python file after it
# disappeared, e.g. while security software was holding it in quarantine.
ENVIRONMENT_PYTHON_REMOVED = 'The Python file of the FreeVideo environment is missing'


def environment_python_removed(args, pythons):
    """The line that opens a failed step's error when it used an environment Python that is gone, or ''."""
    used = {str(value) for value in args}
    for python in pythons:
        if str(python) in used and not io_path(python).exists():
            return '%s: %s\n' % (ENVIRONMENT_PYTHON_REMOVED, python)
    return ''


# Checked before free space: freeing space cannot fix a disk that cannot hold the installation.
PLACE_RULES = (
    (r'Git is unavailable\. Check the network connection', 'git-unavailable',
     ('Git could not be set up', '无法准备 Git'),
     ('Check the network connection and click Check again, or install the Apple Command Line Tools.',
      '请检查网络连接后点击“重新检查”，或者安装 Apple 命令行工具。')),
    (r'The installation folder is on a FAT32 disk', 'disk-format',
     ('This disk cannot hold the models', '这块硬盘放不下模型'),
     ('It is formatted as FAT32, which stores files up to 4 GB; the prompt model alone is 15.7 GB. '
      'Choose a folder on an APFS or Mac OS Extended disk.',
      '它不能存放 4 GB 以上的文件。'
      '请选择其他硬盘上的文件夹。')),
    (r'The installation folder is on an exFAT disk', 'disk-format',
     ('This disk cannot hold the FreeVideo environment', '这块硬盘装不了 FreeVideo 的运行环境'),
     ("It is formatted as exFAT, where FreeVideo's Python environment does not work. "
      'Choose a folder on an APFS or Mac OS Extended disk.',
      '它不能安装 FreeVideo。'
      '请选择其他硬盘上的文件夹。')),
    (r'The installation folder is on a disk that is not connected', 'disk-missing',
     ('The disk is not connected', '这块硬盘没有连接'),
     ('Connect the disk that holds the installation folder, then check again, or choose another folder.',
      '请接上安装位置所在的硬盘后重新检查，或者换一个位置。')),
    (r'The installation folder is on a read-only disk', 'disk-read-only',
     ('This disk is read-only', '这块硬盘是只读的'),
     ('This Mac can read the disk but not write to it, as with NTFS disks. Choose a folder on another disk.',
      '这块硬盘只能读取、不能写入，请选择其他硬盘上的文件夹。')),
)


from .model_repair import (UNCHANGED as MODEL_UNCHANGED, OFFLINE as MODEL_OFFLINE, BUSY as MODEL_BUSY,  # noqa: E402
                           FIRST_INSTALL as MODEL_FIRST_INSTALL, CHANGED_WHY, CHANGED_WAYS)


def _joined(head, tail):
    return tuple(first + (' ' if zh == 0 else '') + rest for zh, (first, rest) in enumerate(zip(head, tail)))


def _model_unchanged(tail):
    return _joined(MODEL_UNCHANGED, tail)


# Failures the launcher's Repair button fixes, and what to do where it has none, by
# why it has none (Session.repair_unavailable): a task still running ('busy'), an
# offline package ('bundled') or imported offline environment ('offline') it never
# repairs, or no installed engine yet ('none').
WITHOUT_REPAIR_NONE = ('Return to Installation and check the same folder to restore missing dependencies.',
                       '返回安装设置，检查当前安装目录，补齐运行依赖。')
_KEPT = ('; the models and videos in the current folder are not deleted.', '；原文件夹里的模型和作品不会被删除。')
_EXTRACT = ('Extract the downloaded package again into a new folder and start FreeVideo from there' + _KEPT[0],
            '请把下载的整合包重新解压到一个新文件夹，再从新文件夹启动 FreeVideo' + _KEPT[1])
_IMPORT = ('On the Installation page in the sidebar, select Install ComfyUI, change Install location to a new folder, '
           'then click Replace package… under Import environment package and choose the downloaded environment package; '
           'if you are online, you can instead choose Automatic as the install method there. '
           'The models and videos in the current folder are not deleted.',
           '请在左侧“安装”页选择“帮我安装 ComfyUI”，把“安装位置”改为一个新文件夹，然后在“导入运行环境包”中点击“更换环境包…”'
           '并选择下载的运行环境包；能联网时，也可以在那里把安装方式改为“自动安装”。原文件夹里的模型和作品不会被删除。')
WITHOUT_REPAIR = {
    # A model file that differs from the tested one; FreeVideo leaves it in place.
    'model-mismatch': dict(
        busy=_model_unchanged(MODEL_BUSY),
        bundled=_model_unchanged(('The portable package cannot switch back in one step. ' + _EXTRACT[0],
                                  '整合包版不能一键换回。' + _EXTRACT[1])),
        offline=_model_unchanged(MODEL_OFFLINE),
        none=MODEL_FIRST_INSTALL),
    # Confirmed, then something changed: setup stops (offline installations never get here).
    **{kind: dict(busy=_joined(CHANGED_WHY[why], CHANGED_WAYS['busy']),
                  **dict.fromkeys(('bundled', 'offline', 'none'), _joined(CHANGED_WHY[why], CHANGED_WAYS['none'])))
       for kind, why in (('plan-changed-files', 'files'), ('plan-changed', 'other'))},
    'dependencies': dict(
        busy=('When the current task ends, a Repair button appears here; it checks this installation and restores what is missing.',
              '当前任务结束后，这里会出现“修复”按钮，点击后会补装缺失的组件。'),
        bundled=('The portable package is missing components it needs and cannot restore them in one step. ' + _EXTRACT[0],
                 '整合包缺少运行所需的组件，不能一键补齐。' + _EXTRACT[1]),
        offline=('An imported offline environment is missing components it needs and cannot restore them in one step. ' + _IMPORT[0],
                 '离线导入的运行环境缺少运行所需的组件，不能一键补齐。' + _IMPORT[1]),
        none=WITHOUT_REPAIR_NONE),
    'runtime-libraries': dict(
        busy=('When the current task ends, a Repair button appears here; it reinstalls a complete copy of PyTorch and keeps your models and videos.',
              '当前任务结束后，这里会出现“修复”按钮，点击后会重新安装运行组件，模型和视频都会保留。'),
        bundled=('The portable package cannot be repaired in one step. ' + _EXTRACT[0],
                 '整合包版不能一键修复。' + _EXTRACT[1]),
        offline=('An imported offline environment cannot be repaired in one step. ' + _IMPORT[0],
                 '离线导入的运行环境不能一键修复。' + _IMPORT[1]),
        none=WITHOUT_REPAIR_NONE),
    # An engine update moved the editable install back after a failure (engine_binding).
    # Offline and portable installations never reach it; their lines only say where things stand.
    'update-unfinished': dict(
        busy=('FreeVideo is back on the previous version and works as before. When the current task ends, a Repair button appears here; '
              'clicking it reinstalls the engine environment and updates again. The details give the reason.',
              'FreeVideo 已退回原来的版本，可以照常使用。当前任务结束后，点这里出现的“修复”可以再次更新。'),
        **dict.fromkeys(('bundled', 'offline'), (
            'FreeVideo is back on the previous version and works as before; the details give the reason.',
            'FreeVideo 已退回原来的版本，可以照常使用；原因写在详情里。')),
        none=('FreeVideo is back on the previous version and works as before. To update again, open Installation in the sidebar, keep the same folder, '
              'click Continue and then Check & continue, and finally click Install & launch on the confirmation page; the details give the reason.',
              'FreeVideo 已退回原来的版本，可以照常使用。要再次更新，请在“安装”页保持同一个安装目录，依次点“继续”“检查并继续”和“安装并启动”。')),
}


# The failures whose card offers Repair (when the launcher can repair this installation).
REPAIR_KINDS = tuple(WITHOUT_REPAIR)


DEPENDENCIES = (('The environment needs repair', '运行环境需要修复'),
                ('Click Repair to check this installation and restore what is missing.',
                 '请点击“修复”，FreeVideo 将检查这份安装并补装缺失的组件。'))

# Added by the launcher when ComfyUI exits during startup and the environment it
# made has packages whose recorded files are missing or have another size. A
# missing file often surfaces as some other package's ImportError instead.
ENVIRONMENT_FILES = 'Files are missing or changed in these ComfyUI environment packages: '


def _stopped_on_missing_module(text):
    """ComfyUI itself stopped on an import, not a custom node that logged one and loaded on."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [i for i, line in enumerate(lines) if re.match(r'^[\w.]+(?:Error|Exception)\b', line)]
    return bool(errors) and bool(re.match(r'^(?:ModuleNotFoundError|ImportError): (?:No module named|DLL load failed)',
                                          lines[errors[-1]])) \
        and not any('for custom nodes' in line for line in lines[errors[-1] + 1:])


def _without_ready_curl_diagnostics(text):
    """Ignore successful tool checks only when matching failure guidance.

    Candidate failures can be normal (e.g. Sysnative from a 64-bit process).
    The complete diagnostics stay in the caller's retained and copied output.
    """
    lines = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('{'):
            try:
                report = json.loads(line)
            except ValueError:
                pass
            else:
                if isinstance(report, dict) and report.get('event') == 'curl_diagnostic' and report.get('status') == 'ready':
                    continue
        lines.append(line)
    return ''.join(lines)


def launcher_failure(value, *, zh=False, other_disk=False, disk_name='', can_repair=False, repairs_comfy=False,
                     unavailable='none'):
    """Short guidance for known failures; callers retain the full error separately.

    other_disk: the launcher can offer another disk with enough room (macOS).
    disk_name: the disconnected disk the launcher found; the error's paths are redacted.
    can_repair: the launcher's Repair button can fix this installation.
    repairs_comfy: ComfyUI runs in the environment the launcher made, which Repair reinstalls.
    unavailable: why Repair is missing when it cannot run (a key of WITHOUT_REPAIR's rows).
    """
    if not value:
        return dict(title='', detail='', action='', kind='')
    text = _without_ready_curl_diagnostics(str(value))
    # Match only the cause position, never an echoed input or a log line below it.
    # Holder diagnostics follow this exact cause; they are not part of its signature.
    causes = [line.strip() for line in text.splitlines() if line.strip()]
    index = 1 if causes and re.fullmatch(r'FreeVideo generation failed(?: \(exit -?\d+\))?', causes[0]) else 0
    cause = re.sub(r'^(?:[A-Za-z_][\w.]*(?:Error|Exception)):\s*', '', causes[index]) if len(causes) > index else ''
    if cause == ENGINE_BUSY:
        return dict(title=ENGINE_BUSY_TITLE[zh], detail=ENGINE_BUSY_DETAIL[zh], action='', kind='engine-busy')
    from .offline_errors import alias_failure, guidance
    if alias_failure(text):
        return dict(title=guidance('title', zh), detail=guidance('runtime-alias', zh),
                    action='', kind='runtime-alias')
    summary = text.partition(STEP_OUTPUT)[0]
    from .comfy_launcher_runtime import OlderLauncher
    # Controller and offline Importer retain strings; direct callers keep the exception.
    downgrade = re.search(r'This launcher \(([^\r\n]*?)\) is older than the installed FreeVideo '
                          r'\(([^\r\n]*?)\); nothing was changed\.', summary)
    if isinstance(value, OlderLauncher) or downgrade:
        launcher, installed = ((value.launcher_label, value.installed_label)
                               if isinstance(value, OlderLauncher) else downgrade.groups())
        if launcher and installed:
            detail = (f'已安装的 FreeVideo 是 {installed}，这个启动器是 {launcher}。' if zh else
                      f'The installed FreeVideo is {installed} and this launcher is {launcher}. ')
        else:
            detail = ('已安装的 FreeVideo 比这个启动器新。' if zh else
                      'The installed FreeVideo is newer than this launcher. ')
        detail += ('为避免把 FreeVideo 换回旧版本，这次没有继续，已安装的文件均未改动。'
                   '请从桌面快捷方式打开 FreeVideo，或下载最新的启动器。' if zh else
                   'To avoid replacing FreeVideo with an older version, nothing was done and no installed files were changed. '
                   'Open FreeVideo from its desktop shortcut, or download the latest launcher.')
        return dict(title='启动器比已安装的 FreeVideo 旧' if zh else 'This launcher is older than the installed FreeVideo',
                    detail=detail, action='', kind='older-launcher', manual=True)
    from .model_repair import MISMATCH, MISMATCH_PATH, FAILURE_TITLE, FAILURE, CHANGED_TITLE, CHANGED_REPAIR
    mismatches = re.findall(MISMATCH + r': ([^\r\n]+)', text)
    if mismatches:
        rows = WITHOUT_REPAIR['model-mismatch']
        action = (FAILURE[zh].format(file=mismatches[-1], entry='修复' if zh else 'Repair') if can_repair else
                  rows.get(unavailable, rows['none'])[zh].format(file=mismatches[-1]))
        failure = dict(title=FAILURE_TITLE[zh], detail='', kind='model-mismatch', action=action)
        if not can_repair and unavailable == 'offline':
            # The text points at the file below it.
            paths = re.findall(MISMATCH_PATH + r': ([^\r\n]+)', text)
            failure['path'] = paths[-1] if paths else mismatches[-1]
        return failure
    if disk_name and 'on a disk that is not connected' in summary:
        return dict(title='这块硬盘没有连接' if zh else 'The disk is not connected', kind='disk-missing', detail='',
                    action=('请接上“%s”后点击“重新检查”' % disk_name + ('，或者改装到其他硬盘。' if other_disk else '。') if zh else
                            'Connect "%s" and click Check again' % disk_name + (', or install on another disk.' if other_disk else '.')))
    folder = 'Select the ComfyUI folder, or the portable folder containing ComfyUI.'
    # A saved installation that vanished raises the same sentence after its own message; the
    # user chose no folder then, so it keeps its own path.
    if folder in summary and not re.search(r'Locate or repair this installation|重新定位或修复这份安装', summary):
        # The retained error carries a traceback whose source lines quote the message
        # (and its Found part) too; only the exception line itself names the folder.
        named = list(re.finditer(re.escape(folder) + r'(?: Found one at "([^"\r\n]+)"; select that folder\.)?[ \t\r]*$',
                                 summary, re.M))
        found = named[-1].group(1) if named else None
        if found:
            action = ('请改选“%s”文件夹。' if zh else 'Select the "%s" folder instead.') % found
        else:
            action = ('请选择包含 main.py 的 ComfyUI 文件夹，或包含它的便携包文件夹。' if zh else
                      'Select the ComfyUI folder (it contains main.py), or the portable folder that contains it.')
        return dict(title='这不是 ComfyUI 文件夹' if zh else "This isn't a ComfyUI folder", detail='',
                    action=action, kind='comfy-folder')
    for pattern, kind, title, action in PLACE_RULES:
        if re.search(pattern, summary):
            return dict(title=title[zh], detail='', action=action[zh], kind=kind)
    disk = re.search(r'Insufficient disk space: need ([\d.]+) GiB, available ([\d.]+) GiB, short ([\d.]+) GiB', summary, re.I)
    if disk:
        if sys.platform == 'darwin':
            # In Finder's units, so the numbers match what it shows for the disk.
            from .disk_space import size_text
            sizes = tuple(size_text(float(value) * 2**30) for value in disk.groups())
            # The figures and what to do go on their own lines, so a narrow window breaks between them.
            detail = (('需要约 %s，可用 %s。\n再释放 %s 后点击“重新检查”' % sizes) + ('，或者改装到其他硬盘。' if other_disk else '。')
                      if zh else ('Needs about %s; %s available.\nFree up %s and click Check again' % sizes)
                      + (', or install on another disk.' if other_disk else '.'))
        else:
            sizes = tuple('%s GiB' % value for value in disk.groups())
            detail = ('需要 %s，可用 %s。再释放 %s 后点击“重新检查”。' % sizes
                      if zh else 'Need %s; %s available. Free another %s, then click “Check again”.' % sizes)
        return dict(title='磁盘空间不足' if zh else 'Not enough disk space', kind='disk', detail=detail,
                    action='重新检查' if zh else 'Check again')
    if text.startswith('ComfyUI could not start.'):
        if can_repair and repairs_comfy and (_stopped_on_missing_module(summary) or ENVIRONMENT_FILES in summary):
            return dict(title=DEPENDENCIES[0][zh], detail='', action=DEPENDENCIES[1][zh], kind='dependencies')
        return dict(title='ComfyUI 启动失败' if zh else 'ComfyUI could not start',
                    detail='ComfyUI 启动时退出了。' if zh else 'ComfyUI quit while starting.',
                    action='原因在详情里；使用已有的 ComfyUI 时，可以用原来的启动器检查。' if zh else
                           'Open details for the exit code and plugin errors. For an existing ComfyUI, also check its original launcher.',
                    kind='comfy-startup')
    if 'Failed step: kernels (' in summary:
        detail = summary[summary.index('Failed step: kernels ('):]
        detail = detail.partition('\nRetained step log:')[0]
        if zh:
            detail = detail.replace('Failed step: kernels (exit code ', '失败步骤：GPU 检查（退出码 ').replace(')\n', '）\n', 1)
        detail = detail.replace('Required linear kernel did not pass.',
                                '显卡必需的运算检查没有通过。' if zh else 'A required GPU check did not pass.')
        detail = detail.replace('No attention backend passed.',
                                '显卡加速检查没有通过。' if zh else 'The GPU acceleration check did not pass.')
        detail = detail.replace('Required path missing',
                                '缺少必需的文件夹' if zh else 'A required folder is missing')
        return dict(title='GPU 检查未通过' if zh else 'GPU validation failed', detail=detail,
                    action='展开详情或导出报告，查看完整检查结果。' if zh else
                           'Open the details or export the report for the complete results.', kind='kernels')
    if 'Download list error: read' in text.splitlines():
        from .model_guidance import DOWNLOAD_COPY
        return dict(title=DOWNLOAD_COPY['error_read'][zh], detail='', action='', kind='download-list-read')
    rules = (
        (r'The previous video has no usable continuation data', 'continuation-missing',
         ('This video cannot be continued', '这个视频暂时不能接续'),
         ('Generate the previous video again, then continue it.', '请重新生成上一段视频，再接续。')),
        (r'The previous video is too short to continue', 'continuation-short',
         ('The previous video is too short', '上一段视频太短'),
         ('Choose a video that is at least 1.7 seconds long.', '请选择至少 1.7 秒的视频。')),
        (r'The previous video cannot be read', 'continuation-unreadable',
         ('The previous video cannot be read', '无法读取上一段视频'),
         ('Choose an MP4 video that plays normally.', '请选择能正常播放的 MP4 视频。')),
        (r"The previous video's frame rate is too low to continue", 'continuation-rate',
         ("The previous video's frame rate is too low", '上一段视频帧率太低'),
         ('Choose a video with at least 10 frames per second.', '请选择每秒至少 10 帧的视频。')),
        (r'Use the same aspect ratio as the previous video', 'continuation-size',
         ('The two videos have different aspect ratios', '两段视频的画面比例不同'),
         ('Choose a video size with the same aspect ratio as the previous video.', '请选择与上一段视频比例相同的画面大小。')),
        (r'Frame count must be 5 \+ 17n and at least 39', 'continuation-frames',
         ('Choose a supported frame count', '请选择支持的帧数'),
         ('Use 39, 56, 73, 90, or another value reached by adding 17. This includes the 22 repeated frames when continuing a video.',
          '请使用 39、56、73、90 等每次增加 17 的帧数；接续时，这个数包含开头重复的 22 帧。')),
        (r'Continue from video cannot be used with these settings\. Continuing a video needs an NVIDIA', 'continuation-route',
         ("This computer can't continue a video", '这台电脑无法接续视频'),
         ('Continuing a video needs an NVIDIA GPU or a Mac. To make a new video, clear "Continue from video".', '接续视频需要 NVIDIA 显卡或 Mac。要生成新视频，请清空“接着生成”。')),
        (r'Continue from video cannot be used with these settings\. Turn on "Two-pass', 'continuation-passes',
         ('Continuing needs two-pass acceleration', '接续需要二次采样加速'),
         ('Turn on "Two-pass acceleration" and turn off the first-pass preview.', '请打开“二次采样加速”，并关闭先出预览。')),
        (r'Continue from video cannot be used with these settings\. Clear either', 'continuation-refine',
         ('A continued part is made in full', '接续会完整生成这一段'),
         ('Clear either "Second pass of preview" or "Continue from video".', '请清空“继续二采”或“接着生成”其中一项。')),
        (r'Continue from video cannot be used with these settings\. Connect up to 3 pictures', 'continuation-media',
         ('A continued part takes picture references only', '接续段只能用图片参考'),
         ('Connect up to 3 pictures as references, and remove video, audio and pre-encoded inputs.', '最多接 3 张图片作为参考图，并移除视频、音频参考和预编码输入。')),
        (r'Choose an existing MP4 video using its absolute path', 'continuation-file',
         ('Choose the previous video', '请选择上一段视频'),
         ('Select an existing MP4 video generated by FreeVideo. In ComfyUI, keep it inside the output or input folder.',
          '请选择 FreeVideo 生成且仍存在的 MP4 视频；在 ComfyUI 中，请把它放在输出或输入文件夹内。')),
        # An engine update waits for a video being generated, whoever started this installation's ComfyUI.
        (r'ComfyUI is running a job, so the engine update waits', 'update-busy',
         ('A video is still generating', '还有视频正在生成'),
         ('Click Update now again when this video has finished. The video being generated is not interrupted.',
          '这个视频生成完成后，再点击“立即更新”。正在生成的视频不会被打断。')),
        # Moving the engine's editable install to the new source failed and was undone (engine_binding).
        (r'Editable install failed for|Editable verification failed for|The engine.s uv is missing', 'update-unfinished',
         ('The update did not finish', '这次更新没有完成'),
         ('FreeVideo is back on the previous version and works as before. Click Repair to reinstall the engine environment and update again; the details give the reason.',
          'FreeVideo 已退回原来的版本，可以照常使用。点“修复”可以再次更新，原因在详情里。')),
        (r'Git \d+\.\d+ is too old to download the pinned sources', 'git-version',
         ('Git is too old', 'Git 版本过旧'),
         ('Click Retry installation; FreeVideo then installs its own Git. On macOS, update the Command Line Tools.',
          '请点击“重试安装”，FreeVideo 会改用自带的 Git；macOS 请更新命令行工具（Command Line Tools）。')),
        (r'installation from offline packages; automatic installation does not change it|'
         r'Existing dependency is not a Git checkout of the pinned source', 'offline-installation',
         ('This folder holds an offline installation', '这个位置是离线包安装'),
         ('Open it from the launcher. For an automatic installation, go back to Setup and choose another folder.',
          '请在启动页直接打开这份安装。如需自动安装，请回到安装设置，换一个安装位置。')),
        # Both carry ComfyUI's log, whose unrelated lines must not pick the card.
        (r'ComfyUI is still starting or could not load FreeVideo', 'comfy-nodes',
         ('ComfyUI did not load FreeVideo', 'ComfyUI 没有加载 FreeVideo'),
         ('Click Retry. If it stops here again, the ComfyUI log in the details shows why the FreeVideo nodes did not load; export the report to send it.',
          '请点“重试”。仍然停在这里时，可以导出报告。')),
        (r'ComfyUI is already running\. Restart it once', 'comfy-restart',
         ('Restart ComfyUI to load FreeVideo', '需要重启 ComfyUI 才能加载 FreeVideo'),
         ('Close the ComfyUI that is running now, then click Retry.', '请关闭正在运行的 ComfyUI，然后点击重试。')),
        # web/health.js lists the failed files and what to do on the page itself.
        (r'The browser page did not load FreeVideo completely|浏览器页面没有完整加载 FreeVideo', 'page',
         ('FreeVideo did not load completely in the browser', '浏览器里的 FreeVideo 没有完整加载'),
         ('The browser page names the files that failed and what to do. Follow it, then reload the page; if it happens again, export the report.',
          '请按浏览器页面上的提示处理后刷新；仍然出现时，可以导出报告。')),
        (r'The browser has not opened FreeVideo yet|浏览器还没有打开 FreeVideo', 'page-silent',
         ('The browser has not opened FreeVideo', '浏览器还没有打开 FreeVideo'),
         ('Click “Open FreeVideo”. If the page stays blank or keeps loading, open it in a current Chrome or Edge.',
          '请点击“打开 FreeVideo”。如果页面空白或一直在加载，请用最新版 Chrome 或 Edge 打开。')),
        (r'NVIDIA driver/nvidia-smi is missing\.', 'gpu-driver-missing',
         ('No NVIDIA driver was found', '未找到 NVIDIA 显卡驱动'),
         ('If this computer has an NVIDIA graphics card, install NVIDIA driver 580 or newer, '
          'restart the computer, then click Retry installation.',
          '如果这台电脑有 NVIDIA 显卡，请安装 580 或更新版本的 NVIDIA 显卡驱动，重启电脑后点击“重试安装”。')),
        (r'NVIDIA driver(?: [\w.]+)?(?: \([^)]*\))? is older than 580', 'gpu-driver-old',
         ('The NVIDIA driver is too old', 'NVIDIA 显卡驱动版本过低'),
         ('Update the NVIDIA driver to version 580 or newer, restart the computer, then click "Retry installation".',
          '请将 NVIDIA 显卡驱动更新到 580 或更新版本，重启电脑后点击“重试安装”。')),
        (r'CUDA_ARCH_UNSUPPORTED', 'gpu-architecture',
         ('This GPU is not supported', '这张显卡不受支持'),
         ('FreeVideo requires a GPU with compute capability 8.0 or higher, such as an NVIDIA GeForce RTX 30 series card or newer. '
          'This requirement does not depend on the amount of VRAM, and a driver update cannot change it.',
          'FreeVideo 需要计算能力 8.0 及以上的显卡，例如 NVIDIA GeForce RTX 30 系列及更新型号。这一要求与显存大小无关，更新驱动也无法改变。')),
        (r'CUDA_CAPABILITY_UNKNOWN', 'gpu-detection',
         ('GPU information could not be read', '未能读取显卡信息'),
         ('Retry hardware detection. If it still fails, export the report; it includes the GPU identity and the raw driver query.',
          '请点“重新检查”。仍然失败时，可以导出报告。')),
        (r'CURL_REPAIR_(?:TIMEOUT|HTTP|TLS|DNS|FAILED|PROXY_UNSUPPORTED|PROXY_MISSING|REDIRECT|SIZE|INTEGRITY)', 'download-tool-network',
         ('The download tool could not be prepared', '下载工具自动补齐失败'),
         ('Check the connection mode in Downloads and retry. Export the report for the source, route and failure code.',
          '请在“下载”里检查连接方式后重试。')),
        (r'CURL_START_DENIED|CURL_REPAIR_STORAGE', 'download-tool-access',
         ('Windows blocked the download tool', '下载工具启动或保存被拒绝'),
         ('Check folder permissions and Windows Security protection history, then retry. Export the report for the Windows error code.',
          '请检查安装目录的权限，并查看 Windows 安全中心的保护历史记录，然后重试。')),
        (r'CURL_[A-Z_]+|Windows curl\.exe was not found', 'download-tool',
         ('The download tool is unavailable', '下载工具不可用'),
         ('Retry installation to repair the tool automatically. If it still fails, export the report for the complete tool diagnostics.',
          '请点“重试安装”，启动器会自动修复下载工具。仍然失败时，可以导出报告。')),
        (r'ComfyUI is still running a job', 'busy',
         ('A video is still generating', '还有视频正在生成'),
         ('The update is installed. Click Connect again after the job finishes to restart ComfyUI with it.',
          '更新已装好。等当前任务完成后点“重新连接”，ComfyUI 会重启并使用新版。')),
        (r'WinError 206|The filename or extension is too long|is too long for Git to read', 'path-too-long',
         ("The installation folder's path is too long", '安装文件夹的路径太长'),
         ('Install FreeVideo in a folder with a shorter path, such as D:\\FreeVideo, then click "Retry installation".',
          '请把 FreeVideo 安装到路径较短的文件夹，例如 D:\\FreeVideo，然后点击“重试安装”。')),
        (r'Installation paused: Windows commit headroom', 'memory-commit',
         ('Windows is low on available memory', 'Windows 可用内存不足'),
         ('Close memory-heavy applications, then retry. Downloaded files are retained.',
          '关闭占用内存较多的程序后重试，已下载文件会保留。')),
        (r'Installation paused: system RAM is nearly exhausted', 'memory-system',
         ('Available system RAM is low', '系统可用内存不足'),
         ('Close memory-heavy applications, then retry. Downloaded files are retained.',
          '关闭占用内存较多的程序后重试，已下载文件会保留。')),
        (r'Installation RAM monitoring failed|Cannot read process-tree memory', 'memory-monitor',
         ('Memory usage could not be read', '暂时无法读取内存用量'),
         ('Download progress is saved. Retry installation; copy the details if it happens again.',
          '下载进度已保留，请点“重试安装”。')),
        (r'No space left on device|WinError 112|disk (?:is )?full|Not enough disk space|Insufficient disk space', 'disk',
         ('Not enough disk space', '磁盘空间不足'),
         ('Free up space on the installation drive, then retry.', '清理安装盘空间后，点击重试。')),
        (r'Could not reach the pinned prepared model sources', 'model-source',
         ('The model download sources cannot be reached', '无法连接模型下载源'),
         ('Check your network or proxy, or change the source or connection mode, then retry.',
          '请检查网络或代理，或切换下载源、连接模式后重试。')),
        (r'Model download paused|unexpected-transfer-size|ConnectionError|ConnectTimeout|ReadTimeout|HTTP probe failed|Could not resolve host|SSL certificate|Every download source failed|All Git sources failed|All package sources failed|Python download failed on every route', 'download',
         ('Download interrupted', '下载中断了'),
         ('Check your network or change the source or connection mode in Settings → Downloads, then retry.', '检查网络，或在“设置 → 下载”中切换下载源、连接模式后重试。')),
        (r'FreeVideo cannot read its program files', 'unreadable-files',
         ('FreeVideo cannot read its own files', 'FreeVideo 读不到自己的程序文件'),
         ('Windows denies this account access to them, usually because they were created while FreeVideo ran as administrator. '
          'Open FreeVideo normally and import the offline packages again; the files already there are reused.',
          'Windows 不允许当前账户读取它们，通常是因为这些文件是在“以管理员身份运行”时创建的。'
          '请正常打开 FreeVideo，重新导入一次离线包，已有的文件会直接复用。')),
        (r'A file in the installation folder changed after the review', 'plan-changed-files', CHANGED_TITLE,
         _joined(CHANGED_WHY['files'], CHANGED_REPAIR)),
        (r'The installation plan changed\. Detect configuration again', 'plan-changed', CHANGED_TITLE,
         _joined(CHANGED_WHY['other'], CHANGED_REPAIR)),
        (ENVIRONMENT_PYTHON_REMOVED, 'environment-python-removed',
         ('A program file is missing', '缺少一个程序文件'),
         ('It may have been quarantined by security software. Restore the file or allow the FreeVideo folder, '
          'then click Retry installation.',
          '可能被安全软件隔离了。恢复这个文件或允许 FreeVideo 所在的文件夹后，点“重试安装”。')),
        # Windows refuses PyTorch's unsigned DLLs under Smart App Control; reinstalling changes nothing.
        (r'WinError 4551|Application Control policy has blocked', 'app-control',
         ('Windows blocked some runtime files', 'Windows 拦截了运行组件'),
         ('Smart App Control in Windows blocked these files. Search Windows for "Smart App Control", or open Windows Security → App & browser control → Smart App Control settings.',
          'Windows 的“智能应用控制”拦截了这些文件。在 Windows 中搜索“智能应用控制”，或打开“Windows 安全中心 → 应用和浏览器控制 → 智能应用控制设置”。')),
        (r'PermissionError|Access is denied|Permission denied|WinError 5\b', 'permission',
         ('This folder is not writable', '无法写入这个目录'),
         ('Choose an installation folder you can write to, then retry.', '选择有写入权限的安装目录后重试。')),
        # A worker's own check of the cuDNN files it loaded (runtime_libraries).
        (r"FreeVideo's PyTorch library files do not match", 'runtime-libraries',
         ('Runtime files have been changed', '运行组件已被更改'),
         ('Click Repair to reinstall a complete copy of PyTorch. Models and videos are kept.',
          '请点“修复”，FreeVideo 会重新安装运行组件，模型和视频都会保留。')),
        (r'ModuleNotFoundError|No module named|DLL load failed', 'dependencies', *DEPENDENCIES),
        (r'CUDA (?:error: )?out of memory|torch\.OutOfMemoryError|Insufficient currently available memory|crossed its RAM budget|paging file is too small|WinError 1455|os error 1455', 'memory',
         ('Memory is unavailable for this step', '这一步可用内存不足'),
         ('Close other memory-heavy applications and retry. Copy the details if it continues.', '关闭占用内存较多的程序后重试；仍失败时可复制详情反馈。')),
    )
    if re.search(r'Installation crossed its RAM budget', text):
        # Only the installer's memory monitor says this. The sentence carries the amount; an
        # older retained error does not, and then the card names none.
        free = re.search(r'At least ([\d.]+) GiB of RAM must be free', text)
        if free:
            action = ('Close other applications until at least %s GiB of RAM is free, then click "Retry installation". Downloaded files are retained.' % free.group(1),
                      '请关闭其他程序，直到至少有 %s GiB 内存空闲，然后点击“重试安装”。已下载的文件会保留。' % free.group(1))
        else:
            action = ('Close other applications to free memory, then click "Retry installation". Downloaded files are retained.',
                      '请关闭其他程序以空出内存，然后点击“重试安装”。已下载的文件会保留。')
        return dict(title=('There is not enough free memory to continue installing', '可用内存不足，无法继续安装')[zh],
                    detail='', action=action[zh], kind='memory-budget')
    # The Linux setup script names the missing basic tools and the command that installs them; without a terminal
    # that sentence is the whole next step, so it stays on the card with the command under it.
    tools = (re.search(r'^FreeVideo: (缺少基础工具：[^\r\n]*)(?:\r?\n  ([^\r\n]+))?', text, re.M) if zh else None) \
        or re.search(r'^FreeVideo: (Missing basic tools: [^\r\n]*)(?:\r?\n  ([^\r\n]+))?', text, re.M)
    if tools and tools.group(1).endswith((':', '：', '.', '。')):
        return dict(title=('缺少基础工具' if zh else 'Basic tools are missing'),
                    detail='\n'.join(part for part in tools.groups() if part), action='', kind='system-tools')
    for pattern, kind, title, action in rules:
        if re.search(pattern, text, re.I):
            if kind in WITHOUT_REPAIR and not can_repair:
                action = WITHOUT_REPAIR[kind].get(unavailable) or WITHOUT_REPAIR[kind].get('none', WITHOUT_REPAIR_NONE)
            detail = ''
            if kind == 'gpu-driver-old':
                version, model = re.search(r'NVIDIA driver(?: ([\w.]+))?(?: \(([^)]*)\))? is older than 580', text, re.I).groups()
                if model and version:
                    detail = ('检测到：%s，驱动 %s' if zh else 'Detected: %s, driver %s') % (model, version)
                elif model:
                    # The CUDA fallback names the card but reads no driver version.
                    detail = ('检测到：%s' if zh else 'Detected: %s') % model
                elif version:
                    detail = ('当前驱动版本：' if zh else 'Installed driver: ') + version
                else:
                    detail = ''
                # The launcher's failure card shows the action only when there is no detail, so the
                # instruction goes under the detected driver instead of being hidden by it.
                return dict(title=title[zh], detail=(detail + '\n' if detail else '') + action[zh], action='', kind=kind)
            if kind == 'gpu-architecture':
                found = re.search(r'\[CUDA_ARCH_UNSUPPORTED\]\s+(.+?)\s+\(SM\w+; compute capability ([\d.]+);', text)
                if found:
                    model, capability = found.groups()
                    detail = ('检测到：%s，计算能力 %s' if zh else 'Detected: %s, compute capability %s') % (model, capability)
                    return dict(title=title[zh], detail=detail + '\n' + action[zh], action='', kind=kind)
            if kind == 'path-too-long':
                # A dependency folder Git cannot read is named, above the same next step.
                found = re.search(r'The path of the dependency folder (.+?) is too long for Git to read', text)
                if found:
                    detail = ('文件夹：%s' if zh else 'Folder: %s') % found[1]
                    return dict(title=title[zh], detail=detail + '\n' + action[zh], action='', kind=kind)
            return dict(title=title[zh], detail=detail, action=action[zh], kind=kind)
    # Validation messages are already actionable. Show the cause, not a stack
    # trace or a guessed hardware diagnosis, and leave long details expandable.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    causes = [line for line in lines if re.match(r'^(?:[\w.]+(?:Error|Exception):|ERROR[: ]|Failed step:|Setup failed:)', line)]
    detail = causes[-1] if causes else next((line for line in reversed(lines)
        if not line.startswith(('File ', 'Traceback', '^', 'All files retained.', 'Rerun the same setup command'))), '')
    detail = re.sub(r'^(?:ValueError|RuntimeError|OSError):\s*', '', detail)
    from .launcher_copy import lan_error
    title = '暂时未能完成' if zh else 'Couldn’t complete this step'
    # The cause line is the last error line, which a traceback or pip's "ERROR:" can displace; the
    # failed step's own line is found anywhere in the text.
    step = re.search(r'^Failed step: ([\w.-]+) \(exit code (-?\w+)\)', text, re.M)
    if step:
        # The internal step id never reaches the card; the exit code stays as the detail.
        name = _step_name(step.group(1), zh)
        if name:
            title = ('“%s”这一步没有完成' if zh else 'The step "%s" did not finish') % name
        detail = ('退出码 %s' if zh else 'Exit code %s') % step.group(2)
    # A message the launcher raised itself (a ValueError) is the next step already.
    unexpected = 'Traceback' in text and not re.match(r'(?:[\w.]+\.)?ValueError:', causes[-1] if causes else '')
    detail = lan_error(detail, zh)
    if step or unexpected:
        # The launcher's failure card shows the action only when there is no detail, so the next
        # step goes under the detail rather than being hidden by it.
        return dict(title=title, detail=(detail + '\n' if detail else '') + UNKNOWN_NEXT_STEP[zh], action='', kind='unknown')
    return dict(title=title, detail=detail, action='', kind='unknown')


UNKNOWN_NEXT_STEP = ('Try again. If it fails again, export the report, then use "Report a problem" and attach it.',
                     '请重试。仍然失败时，请先导出报告，再点“提交问题”并附上报告。')


def _step_name(label, zh):
    """The name the progress card showed for a setup step, in the interface language, or ''."""
    try:
        from .bootstrap import setup_task
        from .launcher_copy import display
        return display(setup_task(label)[0], zh)
    except Exception:
        return ''


def _read(path, limit, *, tail=False):
    path = io_path(path)
    try:
        with path.open('rb') as stream:
            if tail:
                stream.seek(0, 2)
                start = max(0, stream.tell() - limit)
                stream.seek(start)
                value = stream.read(limit).decode('utf-8', errors='replace')
                return plain_text(value.partition('\n')[2] if start and '\n' in value else value)
            raw = stream.read(limit + 1)
            return plain_text(raw.decode('utf-8', errors='replace')) if len(raw) <= limit else ''
    except OSError:
        return ''


def _report(run):
    try:
        report = json.loads(_read(Path(run) / 'video.request.json', 512*1024))
    except (ValueError, TypeError):
        report = {}
    return report if isinstance(report, dict) else {}


def _checkpoint_change(report):
    """The file-identity fields a streamed checkpoint check found changed, or ''.

    Only the fixed names kept by the diagnostics: no paths or timestamp values.
    """
    from .diagnostic_resources import checkpoint_change
    encoding, attempts = report.get('encoding_failure'), report.get('resource_attempts')
    found = [report, encoding.get('failure') if isinstance(encoding, dict) else None]
    found += [row.get('failure') for row in attempts if isinstance(row, dict)] if isinstance(attempts, list) else []
    for holder in found:
        rows = holder.get('exception') if isinstance(holder, dict) else None
        for row in rows if isinstance(rows, list) else [rows]:
            if isinstance(row, dict) and row.get('category') == 'checkpoint_changed':
                change = checkpoint_change(row.get('checkpoint_change'))
                if change.get('source') and change['fields']:
                    return 'Checkpoint changed: %s (%s)' % (change['source'], ', '.join(change['fields']))
    return ''


# Labels of the resource-planning snapshots (generate.py), never stages of the run.
SNAPSHOT_LABEL = re.compile(r'(?:before|after)-encoding|retry-\d+')


def _attempt_phase(report):
    """The stage the last failed video attempt stopped in, or ''."""
    attempts = report.get('resource_attempts')
    for row in reversed(attempts) if isinstance(attempts, list) else ():
        phase = row.get('phase') if isinstance(row, dict) and row.get('failure') else None
        if isinstance(phase, str) and re.fullmatch(r'[a-z_]{1,32}', phase) and phase not in ('unknown', 'complete'):
            return phase
    return ''


# Stands in for the repair sentence until the report text is redacted: the
# redactor reads "Install / repair" as a path.
REPAIR_MARK = 'FREEVIDEO_REPAIR_SENTENCE'


def _runtime_explanation(report, repair=None):
    """What the request or a worker found when cuDNN files differed, or ''."""
    from .runtime_libraries import REINSTALL, summary
    encoding, attempts = report.get('encoding_failure'), report.get('resource_attempts')
    # The request's own check comes first: torch can fail to load before any worker starts.
    found = [report, encoding.get('failure') if isinstance(encoding, dict) else None]
    found += [row.get('failure') for row in attempts if isinstance(row, dict)] if isinstance(attempts, list) else []
    return next(filter(None, (summary(f.get('runtime_libraries'), repair or REINSTALL)
                              for f in found if isinstance(f, dict))), '')


def runtime_explanation(run, repair=None):
    """The changed-library sentence of a failed run, for the launcher's Repair card."""
    return _runtime_explanation(_report(run), repair)


# A traceback's final line names the error at the start of the line; the one a parent
# process raises about its failed child only repeats the exit code.
CHILD_ERROR = re.compile(r'[\w.]*(?:Error|Exception|OutOfMemory\w*)\b')
WRAPPING_ERROR = re.compile(r'(?:subprocess\.)?CalledProcessError\b')
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))')


PLAIN_FIELD = re.compile(r'[^\w .+()/-]')


def _plain(value, limit=40):
    return PLAIN_FIELD.sub('', '' if value is None else str(value))[:limit].strip()


def _mac_run(report, run, redactor):
    """The machine, size, length and settings of a Mac failure, and whether its stage was retried.

    One line of fixed fields; typed text is not part of it. The retried error is redacted whole
    before it is shortened: a prompt or credential is only recognized in full.
    """
    hardware = report.get('runtime_hardware')
    if report.get('device_backend') != 'mps' or not isinstance(hardware, dict):
        return ''
    ram = hardware.get('ram_total')
    machine = [_plain(hardware.get('gpu_name')) or 'Apple GPU']
    if type(ram) is int and ram > 0:
        machine.append('%d GiB' % round(ram / 2**30))
    software = report.get('runtime_software')
    if isinstance(software, dict):
        for key, label in (('macos', 'macOS'), ('mlx', 'MLX'), ('torch', 'torch')):
            if software.get(key):
                machine.append('%s %s' % (label, _plain(software[key], 20)))
    parts = [', '.join(machine)]
    canvas, plan = report.get('geometry'), report.get('sampling_plan')
    if isinstance(canvas, dict) and type(canvas.get('width')) is int and type(canvas.get('height')) is int:
        size = '%dx%d' % (canvas['width'], canvas['height'])
        if isinstance(canvas.get('seconds'), (int, float)):
            size += ' %.1f s' % canvas['seconds']
        first = plan.get('first') if isinstance(plan, dict) and plan.get('enabled') is True else None
        if isinstance(first, dict) and type(first.get('width')) is int and type(first.get('height')) is int:
            size += ' (first pass %dx%d)' % (first['width'], first['height'])
        parts.append(size)
    if isinstance(plan, dict) and type(plan.get('base_steps')) is int:
        two = plan.get('enabled') is True and type(plan.get('refine_steps')) is int
        parts.append('two-pass %d+%d' % (plan['base_steps'], plan['refine_steps']) if two else '%d steps' % plan['base_steps'])
    engine = (report.get('profile') or {}).get('engine') if isinstance(report.get('profile'), dict) else None
    if isinstance(engine, dict) and engine.get('task'):
        parts.append(_plain(engine['task']))
    if report.get('step_cache'):
        parts.append('step cache ' + _plain(report['step_cache']))
    metrics = _read(Path(run) / 'video.engine.json', 512*1024)
    try:
        metrics = json.loads(metrics)
    except ValueError:
        metrics = {}
    retries = metrics.get('memory_retries') if isinstance(metrics, dict) else None
    row = retries[0] if isinstance(retries, list) and retries and isinstance(retries[0], dict) else None
    if row is None:
        parts.append('not retried')
    else:
        stages = metrics.get('stages') if isinstance(metrics.get('stages'), dict) else {}
        key = row.get('phase')
        joined = stages.get(key)
        if (key == 'first-pass' and isinstance(joined, dict) and joined.get('joined_sampling') is True
                and joined.get('success') is False and joined.get('sampling_phase') in ('upscale', 'refinement')):
            # A joined child failed in a later pass; the retry ran that pass on its own.
            key = joined['sampling_phase']
        stage = stages.get(key)
        passed = isinstance(stage, dict) and stage.get('success') is True
        error = (redactor.text(str(row.get('error_message') or '')).strip().splitlines() or [''])[0][:160].replace('"', "'")
        parts.append('%s retried once after "%s"; the retry %s' % (_plain(key, 20), error, 'passed' if passed else 'failed too'))
    return 'Run: ' + '; '.join(parts)


def missing_encoder(text):
    return re.search(r"ENCODER_MODEL_MISSING:|Model in folder ['\"]text_encoders['\"] "
                     r"with filename ['\"][^\r\n]+?['\"] not found\.", text, re.I) is not None


def generation_failure(run, exit_code=None, *, repair=None):
    """Surface the cause, including a child traceback, even without a report.

    `repair` is the sentence that says where this user repairs an installation.
    """
    run = Path(run)
    redactor = Redactor()
    prompt = _read(run / 'prompt.txt', 128*1024).strip()
    if prompt:
        redactor.prompts.add(prompt)
    lines = ['FreeVideo generation failed' + (' (exit %s)' % exit_code if exit_code is not None else '')]
    report = _report(run)
    redactor.structured(report)  # Collect prompt values without showing the request.
    # Read the whole bounded log, not a tail that could hide other output.
    # Managed failures contain only the exact busy sentence and optional holder
    # diagnostics. Other output must never acquire a busy classification.
    busy_lines = [line for line in _read(run / 'generate.log', 8192).splitlines() if line.strip()]
    if exit_code == 1 and busy_lines[:1] == [ENGINE_BUSY] \
            and all(line.startswith('Lock file open:') for line in busy_lines[1:]):
        lines.extend(busy_lines)
    # What the worker found when cuDNN refused its own files, ahead of the trace.
    explained = _runtime_explanation(report, REPAIR_MARK)
    if explained:
        lines.append(explained)
    changed = _checkpoint_change(report)
    if changed:
        lines.append(changed)
    error = report.get('error_message') or report.get('error')
    if isinstance(error, str):
        lines.append(error[:8192])
    planning = report.get('resource_planning')
    encoding_failure = report.get('encoding_failure')
    phase = encoding_failure.get('phase') if isinstance(encoding_failure, dict) else None
    phase = phase or report.get('phase')
    snapshot = None
    if isinstance(planning, list) and planning and isinstance(planning[-1], dict):
        snapshot = planning[-1].get('stage')
    if isinstance(phase, str) and SNAPSHOT_LABEL.fullmatch(phase):
        # A planning label is where the resources were last read, not where the run stopped.
        snapshot, phase = snapshot or phase, None
    phase = phase or _attempt_phase(report)
    if not phase and (snapshot or report.get('resource_attempts')):
        phase = 'generation'
    if phase:
        lines.append('Stage: ' + str(phase))
    if isinstance(planning, list) and planning and isinstance(planning[-1], dict):
        last = planning[-1]
        lines.append('\nLast resource-planning snapshot (%s, before this failure; not the stopping sample):'
                     % (re.sub(r'[^a-z0-9-]', '', str(snapshot))[:32] or 'unlabeled'))
        state = last.get('idle_cache') or {}
        accounting = state.get('ram_accounting', {}) if isinstance(state, dict) else {}
        if not isinstance(accounting, dict):
            accounting = {}
        for key, label in (('raw_available_bytes', 'System reported available RAM'),
                           ('credited_available_bytes', 'Available RAM including reclaimable idle cache')):
            value = accounting.get(key)
            if type(value) is int and value >= 0:
                lines.append('%s: %.2f GiB' % (label, value / 2**30))
        memory = state.get('memory') if isinstance(state, dict) else None
        if isinstance(memory, dict):
            for key, label in (('system_physical_available_bytes', 'Windows free physical RAM'),
                               ('system_commit_available_bytes', 'Windows commit headroom'),
                               ('reclaimable_mapped_bytes', 'Worker reclaimable mappings'),
                               ('guard_bytes', 'Worker private working RAM')):
                value = memory.get(key)
                if type(value) is int and value >= 0:
                    lines.append('%s: %.2f GiB' % (label, value / 2**30))
        recovery = next((r.get('recovery') for r in reversed(planning) if isinstance(r, dict) and r.get('recovery')), None)
        if recovery:
            lines.append('Idle cache release: ' + str(recovery.get('reason', recovery) if isinstance(recovery, dict) else recovery))
    # Child logs carry the actual CUDA/encoder error when the parent only knows
    # an exit code. Read bounded tails; do not read model data or entire logs.
    causes, known = [], redactor.text('\n'.join(lines))[:300]
    for name in ('generate.log', 'video.engine.log', 'video.encoding.log'):
        value = _read(run / name, 8192, tail=True)
        if not value:
            continue
        # Redact the whole tail first: a prompt or credential is only recognized in full.
        value = redactor.text(value)
        rows = [line for line in value.splitlines() if line.strip() and not line.lstrip().startswith('{"event":')]
        trace = '\n'.join(rows[-45:])
        causes += [line for line in (ANSI.sub('', row) for row in rows)
                   if CHILD_ERROR.match(line) and not WRAPPING_ERROR.match(line)]
        if trace:
            lines.extend(['\n' + name + ':', trace])
    # A long child traceback pushes its own last line out of a shortened copy of this text
    # (the report's error already holds the log's end), so the error it ends with also
    # stands near the top unless the text starts with it. It is taken from the redacted tails.
    promoted = bool(causes) and causes[-1] not in known
    if promoted:
        lines.insert(1, 'Cause: ' + causes[-1][:300])
    encoder_missing = missing_encoder('\n'.join(lines))
    if encoder_missing:
        lines.insert(1, 'ENCODER_MODEL_MISSING: The encoder model file is missing. ' + REPAIR_MARK)
    if len(lines) == 1:
        lines.append('The worker ended without a readable error report.')
    summary = _mac_run(report, run, redactor)
    if summary:
        lines.insert(1 + bool(promoted) + bool(encoder_missing), summary)
    lines.append('\nRetained outputs: ' + str(run))
    # The table failure can be in a retained child log rather than the report.
    # Keep its numeric metadata ahead of technical output for all interactive callers.
    sampling = sampling_table_details('\n'.join(lines))
    value = redactor.text('\n'.join(lines))
    if sampling:
        from .sampling_assets import UNAVAILABLE
        marker = UNAVAILABLE + json.dumps(sampling, separators=(',', ':'))
        card = sampling_table_failure(marker)
        first, _, rest = value.partition('\n')
        value = '\n'.join((first, marker, card['title'], card['detail'], rest))
    if explained or encoder_missing:
        from .runtime_libraries import REINSTALL
        value = value.replace(REPAIR_MARK, repair or REINSTALL)
    value = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))', '', value)
    value = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)
    return value


def startup_failure(message, log, *, exit_code=None, context=None):
    detail = _read(Path(log), 8192, tail=True).strip()
    if exit_code is not None:
        message += '\nProcess exit code: ' + str(exit_code)
    if context:
        message += '\nEnvironment: ' + context['environment'] + '\nCustom nodes: ' + context['custom_nodes']
    return Redactor().text(message + ('\n\n' + detail if detail else '\nNo readable process output was produced.') + '\n\nLog: ' + str(log))
