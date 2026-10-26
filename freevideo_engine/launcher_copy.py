"""Translate launcher presentation without changing worker events or logs."""
import re

from .terminal_ui import clean
from .model_repair import PROGRESS as MODEL_REPAIR_PROGRESS
from .model_status import NAMES


ACTION_COPY = {
    'download_missing': ('The download folder no longer exists. Export the list again.',
                         '下载目录已不存在，请重新导出清单'),
    'folder_open_failed': ('This system has no file manager to open the folder. Open it from your own file manager.',
                           '此系统没有可用的文件管理器来打开该文件夹，请在自己的文件管理器中打开'),
    'installation_missing': ('There is no installation information yet. Complete installation first.',
                             '还没有安装信息，请先完成安装'),
    'installation_unavailable': ('The saved ComfyUI folder could not be opened. It may have been moved or renamed, or its drive may be disconnected. Choose its current location below, or install ComfyUI.',
                                 '无法打开已保存的 ComfyUI 文件夹，它可能已被移动、改名，或所在磁盘未连接。请在下方选择它现在的位置，或安装 ComfyUI。'),
    'checking_update': ('Checking for updates. Please wait.', '正在检查更新，请稍候'),
    'update_install_failed': ('The update could not be installed. You can keep using the current version.',
                              '无法安装更新。当前版本可以照常使用。'),
    'update_page_failed': ('Couldn\'t open the download page. Please try again later.', '无法打开下载页，请稍后再试。'),
    'busy': ('The background task is busy. You can do this when it finishes.',
             '后台正在忙，完成后可以操作'),
    'import_queued': ('Saved. Importing will start when the current task finishes.',
                      '已记下，处理完当前任务后自动导入'),
    'setup_queued': ('Models are being prepared in the background. Setup will continue when they are ready.',
                     '后台正在准备模型，好了会接着继续'),
    'update_queued': ('The update will start after the current task finishes.',
                      '当前任务完成后会自动更新'),
}


SOURCES = {
    'auto': ('Automatic', '自动选择'), 'official': ('Hugging Face', 'Hugging Face'),
    'hf-mirror': ('HF Mirror', 'HF 镜像'), 'modelscope': ('ModelScope', '魔搭'),
    'user': ('Custom source', '自定义源'),
}

ZH = dict([
    ('The network is slow. Downloading again; this may take a while.', '网络较慢，正在重试'),
    ('Prepare Python', '准备运行环境'), ('Install Python', '安装运行环境'),
    ('Prepare download tools', '准备下载工具'), ('Tsinghua mirror', '清华镜像'),
    ('Preparing installation', '准备安装'), ('Checking the environment', '正在检查运行环境'),
    ('Testing download sources', '正在测试下载源'), ('Preparing download tools', '正在准备下载工具'),
    ('GitHub mirror', 'GitHub 镜像'), ('attempt', '正在连接'),
    ('fallback', '正在切换下载源'), ('route-retry', '正在切换连接'),
    ('verifying', '正在检查'), ('no-progress', '下载无进展'),
    ('slow-transfer', '持续低速'), ('resume-refused', '当前下载源不支持续传'),
    ('Download PyTorch + CUDA', '下载运行组件'),
    ('Use existing Python', '使用已有环境'), ('Create Python environment', '创建运行环境'),
    ('Install PyTorch + CUDA', '安装运行组件'),
    ('Install engine dependencies', '安装生成组件'), ('Install encoder support', '安装编码组件'),
    ('Install model support', '安装模型组件'), ('Download model code', '下载模型组件'),
    ('Check text encoder', '检查提示词模型'), ('Download model weights', '下载所需模型'),
    ('Reuse models / download missing files', '复用模型并补齐文件'),
    ('Verify / reuse local models', '检查并复用已有模型'),
    ('Verify existing models', '校验已有模型'), ('Check existing model folders', '查找已有模型'),
    ('Find existing models', '查找已有模型'), ('Verify local model', '校验已有模型'),
    ('Copy local model', '复制模型'), ('Reuse local model', '复用模型'),
    ('Existing models checked', '已有模型检查完成'), ('Check local models', '检查已有模型'),
    ('Local models verified', '已有模型校验完成'),
    ('Optimize model storage', '准备模型'), ('Finish model storage', '完成模型准备'),
    ('Test GPU acceleration', '检查 GPU 加速'), ('Check GPU runtime', '检查 GPU 运行环境'),
    ('Check installed packages', '检查运行环境'), ('Save installed versions', '保存安装信息'),
    ('Check build tools', '检查安装工具'), ('Build SageAttention 2', '准备显卡加速'),
    ('Install SageAttention 2', '安装显卡加速组件'), ('Prepare dependencies', '准备运行组件'),
    # The parallel phase also downloads the models (internal names stay as they are).
    ('Install components in parallel', '安装组件和模型'),
    ('Install with space saver', '安装组件和模型（极限省空间）'),
    ('Download the faster model', '下载更快的模型'), ('Model downloaded', '模型已下载'),
    ('Model download incomplete', '模型下载未完成'),
    ('Release installation cache', '释放安装缓存'),
    ('Install acceleration and download models', '安装加速组件和下载模型'),
    ('Verify installation on your GPU', '检查 GPU 加速'), ('Finish setup', '完成安装'),
    ('Setup complete', '安装完成'), ('Setup interrupted', '安装已暂停'), ('Setup failed', '安装未完成'),
    ('Download ComfyUI', '下载 ComfyUI'), ('Create ComfyUI Python environment', '创建 ComfyUI 环境'),
    ('Prepare ComfyUI GPU packages', '准备 ComfyUI 加速组件'),
    ('Install ComfyUI packages', '安装 ComfyUI 组件'), ('Check ComfyUI dependencies', '检查 ComfyUI 环境'),
    ('Finish ComfyUI setup', '完成 ComfyUI 安装'), ('ComfyUI ready', 'ComfyUI 已就绪'),
    ('Prepare ComfyUI', '准备 ComfyUI'), ('Update ComfyUI packages', '更新 ComfyUI 组件'),
    ('Install FreeVideo workflow', '安装 FreeVideo 工作流'),
    ('Open ComfyUI', '打开 ComfyUI'), ('Starting ComfyUI', '启动 ComfyUI'),
    ('Loading nodes and the web interface', '正在加载界面'),
    ('Still loading. This may take a few more minutes.', '仍在加载，可能还需要几分钟'),
    ('Checking configuration', '检查配置'), ('Check installation', '检查安装'),
    ('Verify bundled files', '校验离线包'), ('Check local files', '检查本地文件'),
    ('All bundled files verified', '离线包校验完成'),
    ('Detecting your GPU and testing acceleration', '检测显卡与加速组件'),
    ('Start ComfyUI', '启动 ComfyUI'), ('Open FreeVideo workflow', '打开 FreeVideo 工作流'),
    ('Ready', '已就绪'), ('Verified and ready', '已就绪'),
    ('Stopped; files retained', '已暂停，下载进度已保留'),
    ('Local verification stopped; files retained', '校验已暂停，文件已保留'),
    ('Download and prepare the private Python runtime', '准备运行环境'),
    ('Check the Python already prepared by the launcher', '检查已有环境'),
    ('Prepare an isolated environment for FreeVideo', '创建 FreeVideo 运行环境'),
    ('Download and install the GPU runtime packages', '下载并安装 GPU 运行组件'),
    ('Download and install video and text encoding libraries', '下载并安装生成组件'),
    ('Connect the encoder to FreeVideo', '准备提示词模型'), ('Install the verified model code', '安装模型组件'),
    ('Prepare model source files while runtime packages download', '下载模型组件'),
    ('Verify that the text encoding library loads', '检查提示词模型'),
    ('Download and verify the video model and text encoder', '下载并校验模型'),
    ('Prepare compact FP8 weights for your GPU', '准备适配显卡的模型'),
    ('Prepare the model weights for your GPU', '准备适配显卡的模型'),
    ('Run small checks on your GPU', '检查 GPU 加速是否可用'),
    ('Verify prepared weights and apply your storage choice', '检查模型文件'),
    ('Verify dependency compatibility', '检查运行组件'),
    ('Record versions for future diagnostics', '保存安装信息'),
    ('Verify the compiler before building GPU acceleration', '检查编译工具'),
    ('Compile acceleration for your GPU', '准备显卡加速'),
    ('the first build takes longer', '首次准备需要一些时间'),
    ('Install the prepared GPU acceleration package', '安装显卡加速组件'),
    ('Install the verified Windows acceleration package', '安装显卡加速组件'),
    ('Verify CUDA and the Windows runtime libraries', '检查运行组件'),
    ('Prepare verified source files', '准备组件文件'), ('Download verified source code', '下载组件文件'),
    ('Prepare source files for installation', '准备安装文件'),
    ('Download and verify required files', '下载并校验所需文件'),
    ('Dependencies resolved', '组件检查完成'), ('preparing downloads', '准备下载'),
    ('Downloads ready', '下载完成'), ('Downloading packages', '下载组件'), ('installing packages', '安装组件'),
    ('Packages installed', '组件已安装'), ('finishing checks', '完成检查'),
    ('Installing packages', '安装组件'), ('Compiling CUDA kernels', '准备显卡加速'),
    ('Load cached blocks and upload resident weights', '加载模型'), ('Finish model placement', '完成模型加载'),
    ('Verify cached weights', '校验模型缓存'), ('Read and convert source weights', '正在准备模型'),
    ('Storage ready', '模型文件已就绪'), ('Download', '下载'), ('Model', '模型'),
    ('Video model', '视频模型'), ('Video decoder', '视频解码组件'), ('text encoder', '提示词模型'),
    ('Text encoder', '提示词模型'),
    ('ModelScope', '魔搭'), ('ModelScope parallel', '魔搭'), ('HF Mirror', 'HF 镜像'),
    ('HF Xet', 'Hugging Face'), ('HF HTTP', 'Hugging Face'), ('Parallel HTTP', '并行下载'),
    ('Custom source', '自定义源'), ('Model source', '模型下载源'),
    ('official source', '官方源'), ('Nanjing University mirror', '南京大学镜像'),
    ('Tsinghua mirror', '清华镜像'), ('GitHub mirror', 'GitHub 镜像'),
    ('your Python mirror', '自定义 Python 镜像'), ('your configured source', '自定义源'),
    ('Retrying same source', '正在重试'),
    ('Retrying same source with direct connection', '通过直连重试'),
    ('Retrying same source with configured proxy', '通过代理重试'),
    ('Resume unavailable; checking another source', '正在尝试其他下载源'),
    ('Paused; existing data kept', '已暂停，下载进度已保留'),
    ('Restart explicitly allowed', '重新下载此文件'),
    ('Restarting this file; incompatible fragments retained on disk', '正在重新下载文件'),
    ('Cannot resume this format; enable restart in Setup', '此文件无法续传，请允许重新下载'),
    ('Download response too large', '下载数据异常'), ('switching source', '正在切换下载源'),
    ('Download memory budget reached', '正在调整下载方式'), ('switching to streaming', '继续下载'),
    ('Download source failed', '下载源暂不可用'), ('trying another source', '正在尝试其他下载源'),
    ('Download counter mismatch', '下载数据异常'),
    ('setup', '安装'), ('initialize', '初始化'), ('launch', '启动'),
    ('Hugging Face · Video', 'Hugging Face · 视频'), ('Hugging Face · Audio', 'Hugging Face · 音频'),
])
ZH.update(NAMES.values())
ZH.update([
    ('Completed ', '已完成 '), ('Verifying ', '正在校验'), ('Assembling ', '正在合并'),
    ('Copying ', '正在复制'), ('Model files', '模型文件'),
    ('Packages resolving', '正在检查组件'), ('Packages installing', '正在安装组件'), ('Packages ready', '组件已就绪'),
    ('Environment creating', '正在创建运行环境'), ('Base packages installing', '正在安装基础组件'),
    ('Python ready', '运行环境已就绪'), ('Python downloading', '正在下载运行环境'),
    ('GPU acceleration building', '正在准备显卡加速'),
    ('Runtime libraries verifying', '正在校验运行组件'), ('Runtime libraries importing', '正在载入运行组件'),
    ('Encoder configuring', '正在准备提示词模型'), ('Encoder importing', '正在加载提示词模型'),
    ('Encoder ready', '提示词模型已就绪'),
    ('GPU information reading', '正在读取显卡信息'), ('GPU tests running', '正在检查显卡'),
    ('GPU tests finished', '显卡检查已完成'),
    ('Packages preparing', '正在准备组件'), ('Packages removing', '正在移除旧组件'),
    ('Model files verifying', '正在校验模型文件'), ('Model files preparing', '正在准备模型文件'),
    ('Model files assembling', '正在合并模型文件'),
    ('Package files', '离线包文件'), ('Connecting to download server', '正在连接下载服务器'),
    ('Runtime components', '运行组件'), ('video model', '视频模型'), ('video decoder', '视频解码组件'),
    ('model files', '模型文件'), ('runtime components', '运行组件'),
    ('Uses models already on this computer; original files stay in place', '使用电脑上已有的模型，不移动原文件'),
])
# Card lines for steps that only report counts (the terminal keeps the counts).
ZH.update([('Downloading components', '正在下载组件'), ('Preparing components', '正在准备组件'),
           ('Downloading runtime packages', '正在下载运行组件'), ('Finding existing models', '正在查找已有模型')])


def size_text(value, decimal_sizes=False):
    if decimal_sizes:
        for unit, scale, digits in (('TB', 1e12, 2), ('GB', 1e9, 2), ('MB', 1e6, 1)):
            if value >= scale:
                return ('%.*f' % (digits, value / scale)).rstrip('0').rstrip('.') + ' ' + unit
        return '%d KB' % round(value / 1e3)
    return '%.1f GiB' % (value / 2**30) if value >= 2**30 else '%d MiB' % (value // 2**20)


def source_name(value, zh=False):
    return SOURCES.get(value, (value, value))[bool(zh)]


LAN_ERRORS = {
    'Enter localhost or an IP address of this computer, for example http://127.0.0.1:8188.': '请填写 localhost 或本机的 IP 地址，例如 http://127.0.0.1:8188。',
    'This address does not belong to this computer. Enter one of its IP addresses or 0.0.0.0.': '这个地址不属于本机。请填写本机的 IP 地址或 0.0.0.0。',
    'This port and the next ones are in use or reserved by the system. Set a different local ComfyUI address.': '这个端口和后面的端口都已被占用或被系统保留，请换一个本机 ComfyUI 地址。',
    'The ports for LAN access are in use or reserved by the system. Choose another ComfyUI port.': '局域网访问要用的端口已被占用或被系统保留，请换一个 ComfyUI 端口。',
    'A generation is running. Change the address after it finishes.': '正在生成，完成后可修改地址。',
    'The ComfyUI already running does not use this address. After its jobs finish, restart it with this address and connect again.': '已在运行的 ComfyUI 没有使用这个地址。请在任务完成后用这个地址重新启动 ComfyUI，再重新连接。',
    'The ComfyUI already running is open to the network directly. After its jobs finish, restart it on 127.0.0.1, then turn on LAN access.': '已在运行的 ComfyUI 正在直接对局域网开放。请在任务完成后改用 127.0.0.1 重新启动它，再开启局域网访问。',
    'LAN access startup cancelled': '已取消开启局域网访问。',
}


def lan_error(value, zh=False):
    value = str(value)
    translations = LAN_ERRORS if zh else {
        'A generation is running. Change the address after it finishes.': 'Generation is running; the address can be changed when it finishes.',
    }
    for english, chinese in translations.items():
        value = value.replace(english, chinese)
    return value


MODEL_FILE = re.compile(r'[\w.+-]+\.(?:safetensors|gguf|bin|pth?|ckpt|onnx|json|txt|model)', re.I)
# A whole path to a model file, with a folder the user chose and any characters in its names.
MODEL_PATH = re.compile(r'(?:[A-Za-z]:)?[\\/][^·]*[\\/][^\\/·]+\.(?:safetensors|gguf|bin|pth?|ckpt|onnx)', re.I)
# One downloaded package or archive: MinGit-2.55.0.5.zip, a wheel with a local version, uv.zip.
PACKAGE_FILE = re.compile(r'[\w.+-]+\.(?:zip|whl|tar\.gz|tgz|tar\.xz|7z|exe|msi)', re.I)
SHARD_FILE = re.compile(r'diffusion_pytorch_model-\d+-of-(\d+)\.safetensors')
# Components are named, never their file or package names.
FILE_GROUPS = {14: 'video model', 3: 'video decoder'}


def file_group(name):
    """The group a model file belongs to, in lower case for use inside a sentence."""
    name = re.sub(r' · shard \d+ / \d+$', '', name)
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    shard = SHARD_FILE.fullmatch(name)
    if shard:
        return FILE_GROUPS.get(int(shard[1]), 'model files')
    if MODEL_FILE.fullmatch(name):
        return 'model files'
    if PACKAGE_FILE.fullmatch(name):
        return 'runtime components'
    return re.sub(r' · shard \d+ / \d+$', '', name)


def names_a_file(rest):
    """Whether what follows a verb is one file or group name; a sentence or a count such as "3 / 2" is not."""
    rest = re.sub(r' · shard \d+ / \d+$', '', rest)
    return bool(rest) and ' · ' not in rest and ' / ' not in rest and not rest.startswith('/')


def without_names(value):
    """Lines the launcher shows, without file or package names; known sentences and states stay as they are."""
    if value in ZH or value in SOURCES:
        return value
    match = re.fullmatch(r'Connecting to [^·]+(?: · .*)?', value)
    if match:
        return 'Connecting to download server'
    for prefix in ('Verified ', 'Verify ', 'Download ', 'Assemble downloaded parts · '):
        if value.startswith(prefix) and names_a_file(value[len(prefix):]):
            group = file_group(value[len(prefix):])
            if group[:1].isupper() and group.split(' ')[0].lower() in ('video', 'audio', 'text', 'model'):
                group = group[:1].lower() + group[1:]
            return prefix.split(' · ')[0].rstrip() + ('' if prefix.startswith('Assemble') else ' ' + group)
    if ' · ' in value:
        return ' · '.join('Model files' if MODEL_FILE.fullmatch(part) else
                          'Runtime components' if PACKAGE_FILE.fullmatch(part) else part for part in value.split(' · '))
    if MODEL_FILE.fullmatch(value) or MODEL_PATH.fullmatch(value):
        return 'Model files'
    if PACKAGE_FILE.fullmatch(value):
        return 'Runtime components'
    if re.fullmatch(r'[a-z0-9][a-z0-9_.]*[-_.][a-z0-9_.-]*', value) and re.search(r'[a-z]', value):
        return 'Runtime components'
    return value


# Counters in these English progress parts are dropped; the line says what is happening.
EN_PLAIN = {r'\d+ files / folders scanned': 'Finding files', r'shard \d+ / \d+': 'Downloading',
            r'retry \d+ in \d+s': 'Retrying download'}


def display(value, zh=False):
    value = without_names(clean(value))
    if not zh:
        if ' · ' in value:
            return ' · '.join(display(part) for part in value.split(' · '))
        for prefix, translated in (
            ('Reuse verified FP8 ', 'Use existing model files '),
            ('Prepare FP8 ', 'Prepare model files '),
        ):
            if value.startswith(prefix):
                return translated + display(value[len(prefix):])
        # English names for stages whose internal names describe how they run, not what they do.
        value = {'检查配套包': 'Checking packages', '下载模型': 'Downloading models',
                'Prepare Python': 'Prepare environment',
                'Install Python': 'Install environment',
                'Download PyTorch + CUDA': 'Download runtime components',
                'Use existing Python': 'Use existing environment',
                'Install PyTorch + CUDA': 'Install runtime components',
                'Check text encoder': 'Check prompt model',
                'Check build tools': 'Check installation tools',
                'Build SageAttention 2': 'Prepare GPU acceleration',
                'Install SageAttention 2': 'Install GPU acceleration',
                'Loading nodes and the web interface': 'Loading the interface',
                'Download and prepare the private Python runtime': 'Prepare environment',
                'Check the Python already prepared by the launcher': 'Check existing environment',
                'Connect the encoder to FreeVideo': 'Prepare the prompt model',
                'Verify that the text encoding library loads': 'Check prompt model',
                'Download and verify the video model and text encoder': 'Download and check models',
                'Verify prepared weights and apply your storage choice': 'Check model files',
                'Compile acceleration for your GPU': 'Prepare GPU acceleration',
                'the first build takes longer': 'the first setup takes longer',
                'Verify CUDA and the Windows runtime libraries': 'Check runtime components',
                'Dependencies resolved': 'Component checks complete',
                'Read and convert source weights': 'Preparing the model',
                'text encoder': 'prompt model',
                'Text encoder': 'Prompt model',
                'Restarting this file; incompatible fragments retained on disk': 'Downloading the file again',
                'Python ready': 'Environment ready',
                'Python downloading': 'Downloading the environment',
                'Encoder configuring': 'Preparing the prompt model',
                'Encoder importing': 'Loading the prompt model',
                'Encoder ready': 'Prompt model ready',
                'Install components in parallel': 'Install components and models',
                'Install with space saver': 'Install components and models (space saver)',
                'Optimize model storage': 'Prepare models'}.get(value, value)
        for prefix in ('Download ', 'Prepare '):
            if value.startswith(prefix):
                return prefix + display(value[len(prefix):])
        return ' · '.join(EN_PLAIN.get(next((p for p in EN_PLAIN if re.fullmatch(p, part)), None), part)
                          for part in value.split(' · '))
    if value in ZH:
        return ZH[value]
    if value in SOURCES:
        return source_name(value, True)
    if ' · ' in value:
        return ' · '.join(display(part, True) for part in value.split(' · '))
    for prefix, translated in (
        MODEL_REPAIR_PROGRESS,
        ('Switched current download to ', '已切换至 '), ('Connecting to ', '正在连接'),
        ('Assemble downloaded parts', '合并已下载文件'), ('Reuse verified FP8 ', '使用已有模型文件 '),
        ('Prepare FP8 ', '准备模型文件 '), ('Verified ', '已校验'), ('Verify ', '正在校验'),
        ('Download ', '正在下载'), ('Prepare ', '准备 '), ('Source: ', '下载源：'), ('Log: ', '日志：'),
    ):
        if value.startswith(prefix):
            return translated + display(value[len(prefix):], True)
    for pattern, translated in (
        (r'Downloading packages · about (.+) across (\d+) files', r'下载组件 · 约 \1，共 \2 个文件'),
        (r'about (.+) across (\d+) files', r'约 \1，共 \2 个文件'),
        (r'(\d+) files / folders scanned', '正在查找文件'),
        (r'Testing download sources (\d+ / \d+)', r'正在测试下载源 \1'),
        (r'(\d+ / \d+) files', r'\1 个文件'), (r'shard (\d+ / \d+)', '正在下载'),
        (r'([\d.]+ MiB) retained', r'已保留 \1'), (r'([\d.]+ GiB) released', r'已释放 \1'),
        (r'([\d.]+ MiB) downloaded', r'已下载 \1'),
        (r'Resuming ([\d.]+ MiB) retained', r'继续下载，已保留 \1'),
        (r'retry (\d+) in (\d+)s', '正在重试下载'),
        (r'Receiving objects:.*', '正在下载组件'), (r'Resolving deltas:.*', '正在整理组件'),
        (r'Updating files:.*', '正在准备文件'),
    ):
        if re.fullmatch(pattern, value):
            return re.sub(pattern, translated, value)
    return value


def progress_view(event, zh=False):
    return {key: display(value, zh) if key in ('label', 'detail', 'name') else value
            for key, value in event.items()}
