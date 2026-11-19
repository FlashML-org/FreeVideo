"""User-facing model download destinations, from the installation manifests."""
from contextlib import ExitStack
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit

from . import __version__, network
from .long_paths import io_path, plain_errors
from .model_status import FAMILIES, NAMES, family

# The download list covers the install plan; the image pack has its own launcher controls.
LIST_FAMILIES = tuple(name for name in FAMILIES if name != 'image')

# Iris can replace the third-party download copy here, including export guidance.
DOWNLOAD_COPY = {
    'title': ('Download list', '下载清单'),
    'guide': ('The list is filtered for this GPU, so the whole model repository is not needed. '
              'When the download finishes, choose "Use downloaded folder"; FreeVideo checks every file before using it.',
              '清单已按显卡筛选，无需下载整个模型仓库。下载完成后点“使用已下载的文件夹”，FreeVideo 会逐个校验后使用。'),
    'export': ('Export download list', '导出下载清单'),
    'use_folder': ('Use downloaded folder', '使用已下载的文件夹'),
    'checking': ('Checking the hardware and the downloaded files…', '正在检查硬件和已下载的文件…'),
    'check': ('Check again', '重新检查'),
    'unavailable': ('The download list is not ready yet. Choose "Check again".', '下载清单尚未生成，请点“重新检查”。'),
    'total': ('To download: {count} files ({size})', '需要下载：{count} 个文件（{size}）'),
    'total_remaining': ('Still to download: {count} files ({size})', '还需下载：{count} 个文件（{size}）'),
    'group': ('{count} files · {size}', '{count} 个文件 · {size}'),
    'reused': ('Checked and ready to use: {count} files ({size})', '已校验、可直接使用：{count} 个文件（{size}）'),
    'exported': ('Download list exported to {folder}', '下载清单已导出到 {folder}'),
    'open_folder': ('Open folder', '打开文件夹'),
    'close': ('Close', '关闭'),
    'missing_mirror': ('Not on ModelScope', '魔搭暂无'),
    'error_folder': ('Choose an existing folder to save the download list in.', '请选择一个已有的文件夹来保存下载清单。'),
    'error_unchecked': ('Choose "Check again" first, then export the download list.', '请先点“重新检查”，再导出下载清单。'),
    'error_changed': ('The required files have changed. Choose "Check again", then export the download list.',
                      '所需文件有变化。请点“重新检查”，再导出下载清单。'),
    'error_write': ('The download list could not be saved in this folder: {reason}', '下载清单未能保存到这个文件夹：{reason}'),
    'error_read': ('FreeVideo does not have permission to read some files in the selected folder. Choose another folder, or download the files again without administrator rights.',
                   'FreeVideo 没有权限读取所选文件夹里的部分文件。请选择其他文件夹，或不使用管理员身份重新下载。'),
    'write_permission': ('this account cannot write to it. Choose another folder.', '当前账户没有写入权限，请换一个文件夹。'),
    'write_disk_full': ('the drive is full. Free up space, or choose a folder on another drive.', '磁盘空间不足。请腾出空间，或换一个其他磁盘上的文件夹。'),
    'write_in_use': ('a file there is in use by another program. Close that program, then try again.', '该文件夹中有文件被其他程序占用。请关闭该程序后再试。'),
    'write_missing': ('the folder is no longer there, or its drive was disconnected. Choose the folder again.', '这个文件夹已不存在，或所在磁盘已断开。请重新选择文件夹。'),
    'write_path_long': ('the path is too long. Choose a folder closer to the top of the drive.', '路径太长，请选择层级更浅的文件夹。'),
    'write_other': ('the system did not allow the file to be written. Choose another folder and try again.', '系统不允许写入文件。请换一个文件夹再试一次。'),
    'video': NAMES['video'], 'text': NAMES['text'], 'decoder': NAMES['decoder'], 'sampling': NAMES['sampling'],
    'readme': (
        'FreeVideo download list\n'
        '\n'
        'Run this command in this folder:\n'
        'aria2c --input-file=aria2.txt --continue=true --check-integrity=true\n'
        '\n'
        'You can also import urls.txt into another download tool. urls.txt uses the download source chosen in Settings (Automatic uses Hugging Face); a few files are only available from Hugging Face. Keep all downloaded files in this folder or its subfolders.\n'
        '\n'
        'When the download finishes, return to the launcher, choose "Use downloaded folder", then "Check & continue". FreeVideo recognizes files by size and full checksum, so different file names are fine; missing or damaged files are downloaded as usual.\n'
        '\n'
        'The paths in SHA256SUMS are where aria2 saves the files. With urls.txt the file names may differ, which does not matter.\n'
        '\n'
        'There is no need to download the whole model repository. This folder is not an offline package; do not import it with "Choose offline packages".\n',
        'FreeVideo 下载清单\n'
        '\n'
        '在这个文件夹里运行下面的命令：\n'
        'aria2c --input-file=aria2.txt --continue=true --check-integrity=true\n'
        '\n'
        '也可以把 urls.txt 导入其他下载工具。urls.txt 用的是设置里选中的下载源（选“自动”时用 Hugging Face）；个别文件只能从 Hugging Face 下载。请把下载的文件都放在这个文件夹或它的子文件夹里。\n'
        '\n'
        '下载完成后，回到启动器点“使用已下载的文件夹”，再点“检查并继续”。FreeVideo 按文件大小和完整校验值识别文件，文件名不同也可以；缺少或损坏的文件会照常下载。\n'
        '\n'
        'SHA256SUMS 里的路径是 aria2 的存放路径。用 urls.txt 下载时文件名可能不同，不影响使用。\n'
        '\n'
        '不必下载整个模型仓库。这个文件夹不是离线包，不要用“选择离线包”导入。\n'
        '\n'),
}


# The export goes into a folder of its own inside the chosen one, so files the
# user keeps there (a README.txt, say) are never overwritten.
EXPORT_FOLDER = 'FreeVideo-download-list'
GENERATOR = 'FreeVideo download list'
EXPORT_FILES = ('manifest.json', 'aria2.txt', 'SHA256SUMS', 'urls.txt', 'README.txt')


class DownloadListError(ValueError):
    """key names the DOWNLOAD_COPY 'error_<key>' text the launcher shows."""
    def __init__(self, key, reason='', original_error=''):
        self.key, self.reason = key, reason
        self.original_error = original_error
        super().__init__(DOWNLOAD_COPY['error_' + key][0].format(reason=reason))


def download_write_reason(error, folder):
    """Stable reason codes; OS messages are retained only for diagnostics."""
    if isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM):
        return 'permission'
    winerror = getattr(error, 'winerror', None)
    if error.errno == errno.ENOSPC or winerror in (112, 39):
        return 'disk_full'
    paths = (error.filename, error.filename2, folder)
    if (error.errno == errno.ENAMETOOLONG or winerror == 206
            or winerror == 3 and any(path is not None and len(str(path)) > 260 for path in paths)):
        return 'path_long'
    if winerror in (32, 33) or error.errno in (errno.EBUSY, errno.ETXTBSY):
        return 'in_use'
    if winerror in (2, 3) or error.errno == errno.ENOENT:
        return 'missing'
    return 'other'


def download_copy(zh=False):
    return {key: value[bool(zh)] for key, value in DOWNLOAD_COPY.items()}


def download_files(entries):
    """Project the install plan's placements; never select another GPU variant."""
    result = []
    for row, status in entries:
        if status != 'download':
            continue
        mirror = None if row.get('official_only') else network.modelscope_mapping(row)
        result.append(dict(group=family(row), repo=row['repo'], hf_revision=row['revision'],
            modelscope_repo=mirror['repo'] if mirror else '', modelscope_revision=mirror['revision'] if mirror else '',
            file=row['file'], bytes=row['bytes'], sha256=row.get('sha256') or row.get('download_sha256'),
            relative_path='/'.join(('payload', row['repo'], row['file'])),
            hf_url=network.model_url(row, 'official', env={}),
            modelscope_url=network.model_url(row, 'modelscope', env={}) if mirror else ''))
    return result


def download_view(plan, zh=False, *, decimal_sizes=False, families=LIST_FAMILIES, extra_copy=None):
    from .launcher_copy import size_text
    def size(value):
        if value < (10**6 if decimal_sizes else 2**20):
            unit = 'MB' if decimal_sizes else 'MiB'
            return ('不到 1 ' if zh else 'under 1 ') + unit
        return size_text(value, decimal_sizes=decimal_sizes)
    copy = download_copy(zh)
    copy.update({key: value[bool(zh)] for key, value in (extra_copy or {}).items()})
    rows = plan.get('download_files', [])
    groups = []
    for group in families:
        files = [dict(row, name=row['file'], size=size(row['bytes'])) for row in rows if row['group'] == group]
        amount = sum(row['bytes'] for row in files)
        groups.append(dict(id=group, title=copy[group], files=files, count=len(files), bytes=amount,
                           summary=copy['group'].format(count=len(files), size=size(amount))))
    reused = (plan.get('local_models') or {}).get('matches', {})
    return dict(available='download_files' in plan, groups=groups, copy=copy,
                total_bytes=sum(row['bytes'] for row in rows), count=len(rows),
                total=copy['total_remaining' if reused else 'total'].format(size=size(sum(row['bytes'] for row in rows)), count=len(rows)),
                reused=copy['reused'].format(count=len(reused), size=size(sum(r['bytes'] for r in reused.values()))) if reused else '')


def export_download_list(plan, folder, source='auto'):
    """Offline export. The imported manifest is never trusted as a hash source."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise DownloadListError('folder')
    if 'download_files' not in plan or not plan.get('download_files_verified', True):
        raise DownloadListError('unchecked')
    if source not in ('auto', 'official', 'hf-mirror', 'modelscope'):
        raise DownloadListError('changed')
    rows = plan['download_files']
    outputs = set()
    aria, sums, urls = [], [], []
    for row in rows:
        relative = row['relative_path']
        path = PurePosixPath(relative)
        if (path.is_absolute() or any(part in ('', '.', '..') for part in relative.split('/'))
                or any(char in relative for char in '\\:\r\n\t\x00') or relative in outputs
                or not re.fullmatch('[0-9a-f]{64}', row.get('sha256') or '')
                or not re.fullmatch('[0-9a-f]{40}', row['hf_revision'])
                or type(row['bytes']) is not int or row['bytes'] <= 0):
            raise DownloadListError('changed')
        outputs.add(relative)
        # Use only catalog pins and URLs generated by network.model_url.
        original = dict(repo=row['repo'], revision=row['hf_revision'], file=row['file'])
        hf = network.model_url(original, 'official', env={})
        mirror = network.model_url(original, 'modelscope', env={}) if row['modelscope_url'] else ''
        if row['hf_url'] != hf or row['modelscope_url'] != mirror:
            raise DownloadListError('changed')
        aria.extend(['\t'.join(url for url in (hf, mirror) if url),
                     '  dir=' + path.parent.as_posix(), '  out=' + path.name,
                     '  checksum=sha-256=' + row['sha256']])
        sums.append(row['sha256'] + '  ' + relative)
        chosen = 'official' if source == 'auto' or (source == 'modelscope' and not mirror) else source
        urls.append(network.model_url(original, chosen, env={}))
    prepared = plan.get('prepared_model') or {}
    manifest = dict(schema_version=1, generator=GENERATOR, engine_version=plan.get('engine_version'),
                    video_model=dict(format=prepared.get('scale_granularity') or plan.get('model_scale_granularity') or plan.get('prepared_format'),
                                     reason=plan.get('model_source_reason', ''), source=plan.get('model_source')),
                    sampling_caches=bool(plan.get('sampling_caches')), source=source,
                    total_bytes=sum(row['bytes'] for row in rows), files=rows)
    documents = {'manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                 'aria2.txt': '\n'.join(aria) + ('\n' if aria else ''),
                 'SHA256SUMS': '\n'.join(sums) + ('\n' if sums else ''),
                 'urls.txt': '\n'.join(urls) + ('\n' if urls else ''),
                 'README.txt': DOWNLOAD_COPY['readme'][1] + '\n' + DOWNLOAD_COPY['readme'][0]}
    return publish_export(folder, documents)


def publish_export(folder, documents, folder_name=EXPORT_FOLDER, generator=GENERATOR):
    # Finish every file before anything appears or is replaced. No payload is downloaded.
    stage = None
    try:
        # Not mkdtemp: Python 3.12.4 protects its Windows ACL from inheritance.
        # Downloads made by an administrator must remain readable by the account
        # that chose this folder, as with desktop_shortcut's launcher staging.
        temporary = folder / ('.freevideo-list-' + uuid.uuid4().hex)
        temporary.mkdir()
        stage = temporary
        for name, content in documents.items():
            (stage / name).write_text(content, encoding='utf-8', newline='\n')
        # Two launchers exporting into one folder take turns: the five files
        # always come from the same export.
        with export_lock(folder):
            target, earlier = export_target(folder, folder_name, generator)
            if earlier:
                replace_export(stage, target)
            else:
                os.rename(stage, target)  # One step: the folder appears complete or not at all.
                stage = None
    except OSError as error:
        raise DownloadListError('write', download_write_reason(error, folder), str(error)) from error
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    return str(target)


# The optional image model has its own list: files laid out as FreeVideo keeps them, one export folder of its own.
IMAGE_FOLDER = 'image_gen'
IMAGE_EXPORT_FOLDER = 'FreeVideo-image-model-download-list'
IMAGE_GENERATOR = 'FreeVideo image model download list'
IMAGE_COPY = {
    'image': NAMES['image'],
    'guide': ('Download the files with the exported list and place them as its README.txt describes. '
              'Back in the launcher, choose Resume on Image generation models; FreeVideo checks every file before using it.',
              '用导出的清单下载这些文件，按清单里 README.txt 的说明放好。'
              '回到启动器，在“图像生成模型”处点“继续”，FreeVideo 校验每个文件后才会使用。'),
    # The list needs no re-check, so the shared "Check again" text does not apply.
    'error_changed': ('The download list could not be generated. Restart the launcher and try again.',
                      '下载清单未能生成，请重启启动器后再试。'),
    # {model_folder} is replaced with the folder that holds image_gen.
    'readme': (
        'FreeVideo image generation models download list\n'
        '\n'
        'Run this command in this folder:\n'
        'aria2c --input-file=aria2.txt --continue=true --check-integrity=true\n'
        '\n'
        'You can also import urls.txt into another download tool. urls.txt uses the download source chosen in Settings (Automatic uses Hugging Face).\n'
        '\n'
        'When the download finishes, copy the image_gen folder inside payload into the folder below, merging it with any image_gen folder already there:\n'
        '{model_folder}\n'
        'Then return to the launcher and choose "Resume" on Image generation models. '
        'FreeVideo checks every file by its checksum and downloads again any file that is missing or damaged.\n'
        '\n'
        'SHA256SUMS lists the checksum of every file. This list covers only the image generation models; the video model list is exported separately.\n',
        'FreeVideo 图像生成模型下载清单\n'
        '\n'
        '在这个文件夹里运行下面的命令：\n'
        'aria2c --input-file=aria2.txt --continue=true --check-integrity=true\n'
        '\n'
        '也可以把 urls.txt 导入其他下载工具。urls.txt 用的是设置里选中的下载源（选“自动”时用 Hugging Face）。\n'
        '\n'
        '下载完成后，把 payload 里的 image_gen 文件夹复制到下面这个文件夹，和其中已有的 image_gen 合并：\n'
        '{model_folder}\n'
        '然后回到启动器，在“图像生成模型”处点“继续”。FreeVideo 会按校验值逐个检查文件，缺少或损坏的文件会重新下载。\n'
        '\n'
        'SHA256SUMS 列出每个文件的校验值。这份清单只含图像生成模型，视频模型的清单另外导出。\n'),
}


def image_files():
    """The image model's files as list rows; the catalog ships with the engine and is validated on load."""
    from .image_gen import image_pack
    result = []
    for row in image_pack.rows():
        ids = {source['id'] for source in row['sources']}
        link = lambda name: network.model_url(row, name, env={}) if name in ids else ''
        result.append(dict(group='image', repo=row['repo'], hf_revision=row['revision'],
            modelscope_repo='', modelscope_revision='', file=row['file'], bytes=row['bytes'], sha256=row['sha256'],
            relative_path='/'.join(('payload', IMAGE_FOLDER, row['file'])),
            hf_url=link('official'), hf_mirror_url=link('hf-mirror'), modelscope_url=link('modelscope')))
    return result


def image_download_view(zh=False, *, decimal_sizes=False):
    view = download_view(dict(download_files=image_files()), zh, decimal_sizes=decimal_sizes,
                         families=('image',), extra_copy=IMAGE_COPY)
    return dict(view, scope='image', reused='')


def export_image_download_list(folder, source='auto', model_folder=None):
    """Only the image model, with every source; nothing of the video list changes.

    model_folder is where the README tells the user to put image_gen: the folder the
    launcher looks in, or the installed model folder when none is given.
    """
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise DownloadListError('folder')
    if source not in ('auto', 'official', 'hf-mirror', 'modelscope'):
        raise DownloadListError('changed')
    if model_folder is None:
        from .paths import installed_model_root
        model_folder = installed_model_root()
    rows = image_files()
    aria, sums, urls, outputs = [], [], [], set()
    for row in rows:
        relative = row['relative_path']
        path = PurePosixPath(relative)
        if (path.is_absolute() or any(part in ('', '.', '..') for part in relative.split('/'))
                or any(char in relative for char in '\\:\r\n\t\x00') or relative in outputs
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'] or '') or not row['hf_url']
                or type(row['bytes']) is not int or row['bytes'] <= 0):
            raise DownloadListError('changed')
        outputs.add(relative)
        aria.extend(['\t'.join(url for url in (row['hf_url'], row['hf_mirror_url'], row['modelscope_url']) if url),
                     '  dir=' + path.parent.as_posix(), '  out=' + path.name,
                     '  checksum=sha-256=' + row['sha256']])
        sums.append(row['sha256'] + '  ' + relative)
        chosen = {'official': 'hf_url', 'auto': 'hf_url', 'hf-mirror': 'hf_mirror_url', 'modelscope': 'modelscope_url'}[source]
        urls.append(row[chosen] or row['hf_url'])
    manifest = dict(schema_version=1, generator=IMAGE_GENERATOR, engine_version=__version__, source=source,
                    total_bytes=sum(row['bytes'] for row in rows), files=rows)
    documents = {'manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                 'aria2.txt': '\n'.join(aria) + '\n', 'SHA256SUMS': '\n'.join(sums) + '\n',
                 'urls.txt': '\n'.join(urls) + '\n',
                 'README.txt': (IMAGE_COPY['readme'][1] + '\n' + IMAGE_COPY['readme'][0]).replace('{model_folder}', str(model_folder))}
    return publish_export(folder, documents, IMAGE_EXPORT_FOLDER, IMAGE_GENERATOR)


def export_lock(folder, wait=30.):
    """Hold the cross-process lock of one export folder; waits for another export.

    The lock file lives in the temporary directory, not in the user's folder.
    """
    from .locking import runtime_lock
    key = hashlib.sha256(os.path.normcase(str(folder)).encode('utf-8')).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / ('freevideo-download-list-%s.lock' % key)
    deadline = time.monotonic() + wait
    while True:
        stack = ExitStack()
        try:
            stack.enter_context(runtime_lock(path, inherit=False))
            return stack
        except BlockingIOError:
            stack.close()
            if time.monotonic() > deadline:
                raise OSError(errno.EBUSY, 'Another download list export into this folder did not finish')
            time.sleep(.1)


def earlier_export(path, generator=GENERATOR):
    """A folder this launcher exported before, identified by its manifest."""
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        value = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get('generator') == generator


def export_target(folder, name=EXPORT_FOLDER, generator=GENERATOR):
    """(folder, earlier): our earlier export is refreshed; anything else is left alone."""
    for index in range(1, 100):
        path = folder / (name if index == 1 else '%s-%d' % (name, index))
        if not os.path.lexists(path):
            return path, False
        if earlier_export(path, generator):
            return path, True
    raise OSError(errno.EEXIST, 'Too many folders named ' + name)


def replace_export(stage, target):
    """Swap in the five list files; downloads already in the folder stay.

    The files always belong to one export: if any move fails, the earlier ones
    are put back before the error is raised.
    """
    previous, placed = [], []
    try:
        for name in EXPORT_FILES:
            current = target / name
            if os.path.lexists(current):
                kept = target / ('.%s.previous-%s' % (name, os.urandom(4).hex()))
                os.replace(current, kept)
                previous.append((kept, current))
            os.replace(stage / name, current)
            placed.append(current)
    except OSError:
        for current in placed:
            try:
                os.unlink(current)
            except OSError:
                pass
        for kept, current in previous:
            try:
                os.replace(kept, current)
            except OSError:
                pass
        raise
    for kept, _ in previous:
        try:
            os.unlink(kept)
        except OSError:
            pass


def runtime_packages_supported():
    """The offline Environment package carries a Windows runtime."""
    return sys.platform == 'win32'


def package_instructions(runtime_needed, zh=False):
    """Describe the payloads this host can actually import.

    runtime_needed: a new installation with the offline environment chosen and
    no Environment ZIP imported yet; once it is in, asking again only invites
    importing it a second time.
    """
    if sys.platform == 'darwin':
        return (
            'Get the four model packs: video model, prompt model (FreeVideo-文本编码器.zip), video & audio decoder, and sampling caches. They also work on Mac. '
            'The installer downloads the Mac environment automatically. Do not download the Windows Environment package.',
            '下载「视频模型」「提示词模型（FreeVideo-文本编码器.zip）」「视频与音频解码器」「采样缓存」四个模型包，Mac 同样适用。'
            'Mac 运行环境由安装器自动下载，无需下载 Windows「运行环境」包。')[bool(zh)]
    if sys.platform.startswith('linux'):
        return (
            'Get the four model packs: video model, prompt model (FreeVideo-文本编码器.zip), video & audio decoder, and sampling caches. They also work on Linux. '
            'The installer downloads the Linux environment automatically. Do not download the Windows Environment package.',
            '下载「视频模型」「提示词模型（FreeVideo-文本编码器.zip）」「视频与音频解码器」「采样缓存」四个模型包，Linux 同样适用。'
            'Linux 运行环境由安装器自动下载，无需下载 Windows「运行环境」包。')[bool(zh)]
    text = ('Get the four model packs: video model, prompt model (FreeVideo-文本编码器.zip), video & audio decoder, and sampling caches. '
            'The reference audio pack is optional, for audio references.',
            '下载「视频模型」「提示词模型（FreeVideo-文本编码器.zip）」「视频与音频解码器」「采样缓存」四个模型包；'
            '「音频参考缓存」可选，用音频做参考时才需要。')[bool(zh)]
    if runtime_needed:
        text += '\n' + ('For a new installation, also get the Environment package.',
                        '全新安装还需「运行环境」包。')[bool(zh)]
    return text


def video_instructions(zh=False):
    if sys.platform == 'darwin':
        return ('For Mac, choose the int8 model files and keep their folder structure.',
                'Mac 请选择 int8 模型文件，并保留下载的目录结构。')[bool(zh)]
    return ('Choose your GPU variant on the model page and keep its folder structure.',
            '按模型页说明选择显卡版本，保留下载的目录结构。')[bool(zh)]


def links(plan=None):
    """Compatibility for the Tk launcher: fixed files from a checked plan only."""
    return {group: [(row['file'] + ' · ' + source, row[key])
                    for row in (plan or {}).get('download_files', []) if row['group'] == group
                    for source, key in (('Hugging Face', 'hf_url'), ('ModelScope', 'modelscope_url')) if row[key]]
            for group in LIST_FAMILIES}


@plain_errors
def cloud_models():
    """Only publisher-approved or release-verified shares become download links."""
    path = io_path(Path(__file__).with_name('model_shares.json'))
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding='utf-8'))
    result = []
    for row in value.get('models', []):
        url = urlsplit(row.get('url', ''))
        if ((row.get('publisher_approved') is True or row.get('verified') is True)
                and url.scheme == 'https' and url.hostname == 'pan.quark.cn'
                and url.path.startswith('/s/') and not url.username and not url.password):
            result.append(row)
    return result


def show_component(app, component):
    import webbrowser
    from . import branding as b
    from .launcher_copy import display
    titles = {'video': ('Video model', '视频模型'), 'encoder': ('Prompt model', '提示词模型'),
              'decoder': ('Video & audio decoders', '视频与音频解码器')}
    instructions = {
        'video': ('Choose your GPU variant on the model page and keep its folder structure.',
                  '按模型页说明选择显卡版本，保留下载的目录结构。'),
        'encoder': ('Download the prompt model to your model folder.',
                    '下载提示词模型，放入模型目录。'),
        'decoder': ('Download the vae and audio_vae folders.',
                    '下载 vae 和 audio_vae 文件夹。'),
    }
    dialog = app.tk.Toplevel(app.window)
    dialog.title('FreeVideo · ' + app.t(*titles[component])); dialog.transient(app.window)
    dialog.configure(bg=b.BACKGROUND)
    body = app.ttk.Frame(dialog, padding=24); body.pack(fill='both', expand=True)
    app.ttk.Label(body, text=app.t(*titles[component]), style='Heading.TLabel').pack(anchor='w')
    app.ttk.Label(body, text=app.t(*instructions[component]), wraplength=490).pack(fill='x', pady=12)
    checked = app.controller.state.get('plan', {})
    for title, url in links(checked)['text' if component == 'encoder' else component]:
        app.ttk.Button(body, text=display(title, app.zh) + ' ↗', command=lambda u=url: webbrowser.open(u)).pack(fill='x', pady=4)
    for row in cloud_models():
        app.ttk.Button(body, text=app.t('Quark model pack · ', '夸克模型包 · ') + row['label'] + ' ↗',
                       command=lambda u=row['url']: webbrowser.open(u)).pack(fill='x', pady=4)
    app.ttk.Label(body, text=app.t('After downloading, extract ZIP files and choose the model folder below. Subfolders are scanned automatically.',
        '下载完成后先解压 ZIP，再选择模型所在文件夹；会自动扫描子文件夹。'), wraplength=490).pack(fill='x', pady=12)
    def choose():
        dialog.destroy()
        app.model_method.set('reuse')
        app.add_model_folder()
    app.ttk.Button(body, text=app.t('Downloaded · choose folder…', '已下载，选择文件夹…'), command=choose).pack(fill='x')
