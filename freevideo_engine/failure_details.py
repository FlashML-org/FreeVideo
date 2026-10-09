"""Readable, copyable failures for interactive callers. No GPU imports."""
import json
from pathlib import Path
import re
import sys

from .diagnostics import Redactor


STEP_OUTPUT = '\n\nFull step output:\n'


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


def setup_command_failure(label, log, exit_code):
    """Keep the complete failed step, with required GPU failures before metadata."""
    log = Path(log)
    try:
        output = log.read_text(encoding='utf-8', errors='replace')
    except OSError as error:
        output = 'Could not read the retained step log: ' + str(error)
    lines = ['Failed step: %s (exit code %s)' % (label, exit_code)]
    if label == 'kernels':
        try:
            report = json.loads((log.parent / 'kernel-capabilities.json').read_text(encoding='utf-8'))
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


# Checked before free space: freeing space cannot fix a disk that cannot hold the installation.
PLACE_RULES = (
    (r'The installation folder is on a FAT32 disk', 'disk-format',
     ('This disk cannot hold the models', '这块硬盘放不下模型'),
     ('It is formatted as FAT32, which stores files up to 4 GB; the text encoder alone is 15.7 GB. '
      'Choose a folder on an APFS or Mac OS Extended disk.',
      '它是 FAT32 格式，单个文件最大 4 GB，而文本编码器一个文件就有 15.7 GB。'
      '请选择 APFS 或 Mac OS 扩展格式硬盘上的文件夹。')),
    (r'The installation folder is on an exFAT disk', 'disk-format',
     ('This disk cannot hold the FreeVideo environment', '这块硬盘装不了 FreeVideo 的运行环境'),
     ("It is formatted as exFAT, where FreeVideo's Python environment does not work. "
      'Choose a folder on an APFS or Mac OS Extended disk.',
      '它是 exFAT 格式，FreeVideo 的 Python 运行环境在这种格式上无法正常工作。'
      '请选择 APFS 或 Mac OS 扩展格式硬盘上的文件夹。')),
    (r'The installation folder is on a disk that is not connected', 'disk-missing',
     ('The disk is not connected', '这块硬盘没有连接'),
     ('Connect the disk that holds the installation folder, then check again, or choose another folder.',
      '请接上安装位置所在的硬盘后重新检查，或者换一个位置。')),
    (r'The installation folder is on a read-only disk', 'disk-read-only',
     ('This disk is read-only', '这块硬盘是只读的'),
     ('This Mac can read the disk but not write to it, as with NTFS disks. Choose a folder on another disk.',
      '这台 Mac 只能读取这块硬盘，不能写入（例如 NTFS 格式的硬盘）。请选择其他硬盘上的文件夹。')),
)


def launcher_failure(value, *, zh=False, other_disk=False, disk_name=''):
    """Short guidance for known failures; callers retain the full error separately.

    other_disk: the launcher can offer another disk with enough room (macOS).
    disk_name: the disconnected disk the launcher found; the error's paths are redacted.
    """
    if not value:
        return dict(title='', detail='', action='', kind='')
    text = str(value)
    summary = text.partition(STEP_OUTPUT)[0]
    if disk_name and 'on a disk that is not connected' in summary:
        return dict(title='这块硬盘没有连接' if zh else 'The disk is not connected', kind='disk-missing', detail='',
                    action=('请接上“%s”后点击“重新检查”' % disk_name + ('，或者改装到其他硬盘。' if other_disk else '。') if zh else
                            'Connect "%s" and click Check again' % disk_name + (', or install on another disk.' if other_disk else '.')))
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
        return dict(title='ComfyUI 启动失败' if zh else 'ComfyUI could not start',
                    detail='ComfyUI 进程在启动时退出。' if zh else 'The ComfyUI process exited during startup.',
                    action='展开详情查看退出码与插件错误；已有 ComfyUI 可使用原启动器检查。' if zh else
                           'Open details for the exit code and plugin errors. For an existing ComfyUI, also check its original launcher.',
                    kind='comfy-startup')
    if 'Failed step: kernels (' in summary:
        detail = summary[summary.index('Failed step: kernels ('):]
        detail = detail.partition('\nRetained step log:')[0]
        if zh:
            detail = detail.replace('Failed step: kernels (exit code ', '失败步骤：GPU 检查（退出码 ').replace(')\n', '）\n', 1)
            detail = detail.replace('Required linear kernel did not pass.', '必需的 linear 运算检查未通过。')
            detail = detail.replace('No attention backend passed.', '没有可用的 attention 后端。')
            detail = detail.replace('Required path missing', '缺少必需目录')
        return dict(title='GPU 检查未通过' if zh else 'GPU validation failed', detail=detail,
                    action='展开详情或导出报告，查看完整检查结果。' if zh else
                           'Open the details or export the report for the complete results.', kind='kernels')
    rules = (
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
          '请点击重试。如果仍停在这里，详情里的 ComfyUI 日志会显示 FreeVideo 节点没有加载的原因，可以导出报告反馈。')),
        (r'ComfyUI is already running\. Restart it once', 'comfy-restart',
         ('ComfyUI must restart to load FreeVideo', 'ComfyUI 需要重启才能加载 FreeVideo'),
         ('Close the ComfyUI that is running now, then click Retry.', '请关闭正在运行的 ComfyUI，然后点击重试。')),
        # web/health.js lists the failed files and what to do on the page itself.
        (r'The browser page did not load FreeVideo completely|浏览器页面没有完整加载 FreeVideo', 'page',
         ('FreeVideo did not load completely in the browser', '浏览器里的 FreeVideo 没有完整加载'),
         ('The browser page names the files that failed and what to do. Follow it, then reload the page; if it happens again, export the report.',
          '浏览器页面上列出了出错的文件和处理方法。按提示处理后刷新页面；仍然出现时请导出报告。')),
        (r'The browser has not opened FreeVideo yet|浏览器还没有打开 FreeVideo', 'page-silent',
         ('The browser has not opened FreeVideo', '浏览器还没有打开 FreeVideo'),
         ('Click “Open FreeVideo”. If the page stays blank or keeps loading, open it in a current Chrome or Edge.',
          '请点击“打开 FreeVideo”。如果页面空白或一直在加载，请用最新版 Chrome 或 Edge 打开。')),
        (r'CUDA_ARCH_UNSUPPORTED', 'gpu-architecture',
         ('This GPU lacks the required compute support', '这张显卡不满足当前计算要求'),
         ('The detected GPU and compute capability are shown below. The CUDA runtime requires native BF16 computation (SM80 or newer). Use a compatible GPU; changing the driver does not change its hardware capability.',
          '下方显示实际显卡和计算能力。当前 CUDA 引擎要求原生 BF16 计算（SM80 及以上）；需要使用兼容显卡，更新驱动无法改变显卡的硬件能力。')),
        (r'CUDA_CAPABILITY_UNKNOWN', 'gpu-detection',
         ('The GPU architecture could not be read', '未能读取显卡架构'),
         ('Retry hardware detection. If it still fails, export the report; it includes the GPU identity and the raw driver query.',
          '请重试硬件检测。仍失败时导出报告，其中包含显卡信息和驱动查询的原始结果。')),
        (r'CURL_REPAIR_(?:TIMEOUT|HTTP|TLS|DNS|FAILED|PROXY_UNSUPPORTED|PROXY_MISSING|REDIRECT|SIZE|INTEGRITY)', 'download-tool-network',
         ('The download tool could not be prepared', '下载工具自动补齐失败'),
         ('Check the connection mode in Downloads and retry. Export the report for the source, route and failure code.',
          '请在“下载”中检查连接方式后重试。导出报告已包含来源、连接方式和失败码。')),
        (r'CURL_START_DENIED|CURL_REPAIR_STORAGE', 'download-tool-access',
         ('Windows blocked the download tool', '下载工具启动或保存被拒绝'),
         ('Check folder permissions and Windows Security protection history, then retry. Export the report for the Windows error code.',
          '请检查安装目录权限和 Windows 安全中心的保护历史记录后重试。导出报告包含 Windows 错误码。')),
        (r'CURL_[A-Z_]+|Windows curl\.exe was not found', 'download-tool',
         ('The download tool is unavailable', '下载工具不可用'),
         ('Retry installation to repair the tool automatically. If it still fails, export the report for the complete tool diagnostics.',
          '请重试安装，启动器会自动修复下载工具。仍失败时导出报告，其中包含完整工具诊断。')),
        (r'ComfyUI is still running a job', 'busy',
         ('A video is still generating', '还有视频正在生成'),
         ('The update is installed. Click Connect again after the job finishes to restart ComfyUI with it.',
          '更新已装好。等当前任务完成后点“重新连接”，ComfyUI 会重启并使用新版。')),
        (r'Installation paused: Windows commit headroom', 'memory-commit',
         ('Windows memory headroom is low', 'Windows 内存提交余量不足'),
         ('Close memory-heavy applications, then retry. Downloaded files are retained.',
          '关闭占用内存较多的程序后重试，已下载文件会保留。')),
        (r'Installation paused: system RAM is nearly exhausted', 'memory-system',
         ('Available system RAM is low', '系统可用内存不足'),
         ('Close memory-heavy applications, then retry. Downloaded files are retained.',
          '关闭占用内存较多的程序后重试，已下载文件会保留。')),
        (r'Installation RAM monitoring failed|Cannot read process-tree memory', 'memory-monitor',
         ('Memory usage could not be read', '暂时无法读取进程内存'),
         ('Download progress is saved. Retry installation; copy the details if it happens again.',
          '下载进度已保留。点击“重试安装”；若再次出现，请复制详情反馈。')),
        (r'No space left on device|WinError 112|disk (?:is )?full|Not enough disk space|Insufficient disk space', 'disk',
         ('Not enough disk space', '磁盘空间不足'),
         ('Free up space on the installation drive, then retry.', '清理安装盘空间后，点击重试。')),
        (r'Model download paused|unexpected-transfer-size|ConnectionError|ConnectTimeout|ReadTimeout|HTTP probe failed|Could not resolve host|SSL certificate|Every download source failed|All Git sources failed|All package sources failed|Python download failed on every route', 'download',
         ('Download interrupted', '下载中断了'),
         ('Check your network or change the source or connection mode in Settings → Downloads, then retry.', '检查网络，或在“设置 → 下载”中切换下载源、连接模式后重试。')),
        (r'FreeVideo cannot read its program files', 'unreadable-files',
         ('FreeVideo cannot read its own files', 'FreeVideo 读不到自己的程序文件'),
         ('Windows denies this account access to them, usually because they were created while FreeVideo ran as administrator. '
          'Open FreeVideo normally and import the offline packages again; the files already there are reused.',
          'Windows 不允许当前账户读取它们，通常是因为这些文件是在“以管理员身份运行”时创建的。'
          '请正常打开 FreeVideo，重新导入一次离线包，已有的文件会直接复用。')),
        (r'PermissionError|Access is denied|Permission denied|WinError 5\b', 'permission',
         ('This folder is not writable', '无法写入这个目录'),
         ('Choose an installation folder you can write to, then retry.', '选择有写入权限的安装目录后重试。')),
        (r'ModuleNotFoundError|No module named|DLL load failed', 'dependencies',
         ('The environment needs repair', '运行环境需要修复'),
         ('Return to Installation and check the same folder to restore missing dependencies.', '返回安装设置，检查当前安装目录，补齐运行依赖。')),
        (r'CUDA (?:error: )?out of memory|torch\.OutOfMemoryError|Insufficient currently available memory|crossed its RAM budget|paging file is too small|WinError 1455', 'memory',
         ('Memory is unavailable for this step', '这一步可用内存不足'),
         ('Close other memory-heavy applications and retry. Copy the details if it continues.', '关闭占用内存较多的程序后重试；仍失败时可复制详情反馈。')),
    )
    for pattern, kind, title, action in rules:
        if re.search(pattern, text, re.I):
            return dict(title=title[zh], detail='', action=action[zh], kind=kind)
    # Validation messages are already actionable. Show the cause, not a stack
    # trace or a guessed hardware diagnosis, and leave long details expandable.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    causes = [line for line in lines if re.match(r'^(?:[\w.]+(?:Error|Exception):|ERROR[: ]|Failed step:|Setup failed:)', line)]
    detail = causes[-1] if causes else next((line for line in reversed(lines)
        if not line.startswith(('File ', 'Traceback', '^', 'All files retained.', 'Rerun the same setup command'))), '')
    detail = re.sub(r'^(?:ValueError|RuntimeError|OSError):\s*', '', detail)
    return dict(title='暂时未能完成' if zh else 'Couldn’t complete this step',
                detail=detail, action='', kind='unknown')


def _read(path, limit, *, tail=False):
    try:
        with path.open('rb') as stream:
            if tail:
                stream.seek(0, 2)
                start = max(0, stream.tell() - limit)
                stream.seek(start)
                value = stream.read(limit).decode('utf-8', errors='replace')
                return value.partition('\n')[2] if start and '\n' in value else value
            raw = stream.read(limit + 1)
            return raw.decode('utf-8', errors='replace') if len(raw) <= limit else ''
    except OSError:
        return ''


def generation_failure(run, exit_code=None):
    """Surface the cause, including a child traceback, even without a report."""
    run = Path(run)
    redactor = Redactor()
    prompt = _read(run / 'prompt.txt', 128*1024).strip()
    if prompt:
        redactor.prompts.add(prompt)
    lines = ['FreeVideo generation failed' + (' (exit %s)' % exit_code if exit_code is not None else '')]
    raw = _read(run / 'video.request.json', 512*1024)
    try:
        report = json.loads(raw)
    except (ValueError, TypeError):
        report = {}
    if not isinstance(report, dict):
        report = {}
    redactor.structured(report)  # Collect prompt values without showing the request.
    error = report.get('error_message') or report.get('error')
    if isinstance(error, str):
        lines.append(error[:8192])
    planning = report.get('resource_planning')
    encoding_failure = report.get('encoding_failure')
    phase = encoding_failure.get('phase') if isinstance(encoding_failure, dict) else None
    phase = phase or report.get('phase')
    if phase:
        lines.append('Stage: ' + str(phase))
    if isinstance(planning, list) and planning and isinstance(planning[-1], dict):
        last = planning[-1]
        if not phase:
            lines.append('Stage: ' + str(last.get('stage', 'unknown')))
        lines.append('\nLast resource-planning snapshot (before this failure; not the stopping sample):')
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
    for name in ('generate.log', 'video.engine.log', 'video.encoding.log'):
        value = _read(run / name, 8192, tail=True)
        if not value:
            continue
        rows = [line for line in value.splitlines() if line.strip() and not line.lstrip().startswith('{"event":')]
        trace = '\n'.join(rows[-45:])
        if trace:
            lines.extend(['\n' + name + ':', trace])
    if len(lines) == 1:
        lines.append('The worker ended without a readable error report.')
    lines.append('\nRetained outputs: ' + str(run))
    value = redactor.text('\n'.join(lines))
    value = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))', '', value)
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)


def startup_failure(message, log, *, exit_code=None, context=None):
    detail = _read(Path(log), 8192, tail=True).strip()
    if exit_code is not None:
        message += '\nProcess exit code: ' + str(exit_code)
    if context:
        message += '\nEnvironment: ' + context['environment'] + '\nCustom nodes: ' + context['custom_nodes']
    return Redactor().text(message + ('\n\n' + detail if detail else '\nNo readable process output was produced.') + '\n\nLog: ' + str(log))
