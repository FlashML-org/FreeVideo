"""Translate launcher presentation without changing worker events or logs."""
import re

from .terminal_ui import clean


SOURCES = {
    'auto': ('Automatic', '自动选择'), 'official': ('Hugging Face', 'Hugging Face'),
    'hf-mirror': ('HF Mirror', 'HF 镜像'), 'modelscope': ('ModelScope', '魔搭'),
    'user': ('Custom source', '自定义源'),
}

ZH = dict([
    ('Prepare Python', '准备 Python'), ('Install Python', '安装 Python'),
    ('Use existing Python', '复用 Python'), ('Create Python environment', '创建运行环境'),
    ('Install PyTorch + CUDA', '安装 GPU 运行环境'),
    ('Install engine dependencies', '安装生成组件'), ('Install encoder support', '安装编码组件'),
    ('Install model support', '安装模型组件'), ('Download model code', '下载模型组件'),
    ('Check text encoder', '检查文本编码器'), ('Download model weights', '下载所需模型'),
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
    ('Check build tools', '检查编译工具'), ('Build SageAttention 2', '准备 GPU 加速'),
    ('Install SageAttention 2', '安装 GPU 加速组件'), ('Prepare dependencies', '准备运行组件'),
    ('Install components in parallel', '安装运行组件'),
    ('Install with space saver', '极限省空间安装'),
    ('Release installation cache', '释放安装缓存'),
    ('Install acceleration and download models', '安装加速组件和下载模型'),
    ('Verify installation on your GPU', '检查 GPU 加速'), ('Finish setup', '完成安装'),
    ('Setup complete', '安装完成'), ('Setup interrupted', '安装已暂停'), ('Setup failed', '安装未完成'),
    ('Download ComfyUI', '下载 ComfyUI'), ('Create ComfyUI Python environment', '创建 ComfyUI 环境'),
    ('Prepare ComfyUI GPU packages', '准备 ComfyUI 加速组件'),
    ('Install ComfyUI packages', '安装 ComfyUI 组件'), ('Check ComfyUI dependencies', '检查 ComfyUI 环境'),
    ('Finish ComfyUI setup', '完成 ComfyUI 安装'), ('ComfyUI ready', 'ComfyUI 已就绪'),
    ('Prepare ComfyUI', '准备 ComfyUI'), ('Install FreeVideo workflow', '安装 FreeVideo 工作流'),
    ('Open ComfyUI', '打开 ComfyUI'), ('Starting ComfyUI', '启动 ComfyUI'),
    ('Loading nodes and the web interface', '加载节点与界面'),
    ('Checking configuration', '检查配置'), ('Check installation', '检查安装'),
    ('Verify bundled files', '校验离线包'), ('Check local files', '检查本地文件'),
    ('All bundled files verified', '离线包校验完成'),
    ('Detecting your GPU and testing acceleration', '检测显卡与加速组件'),
    ('Start ComfyUI', '启动 ComfyUI'), ('Open FreeVideo workflow', '打开 FreeVideo 工作流'),
    ('Ready', '已就绪'), ('Verified and ready', '已就绪'),
    ('Stopped; files retained', '已暂停，下载进度已保留'),
    ('Local verification stopped; files retained', '校验已暂停，文件已保留'),
    ('Download and prepare the private Python runtime', '准备 Python 运行环境'),
    ('Check the Python already prepared by the launcher', '检查已有 Python'),
    ('Prepare an isolated environment for FreeVideo', '创建 FreeVideo 运行环境'),
    ('Download and install the GPU runtime packages', '下载并安装 GPU 运行组件'),
    ('Download and install video and text encoding libraries', '下载并安装生成组件'),
    ('Connect the encoder to FreeVideo', '准备编码器'), ('Install the verified model code', '安装模型组件'),
    ('Prepare model source files while runtime packages download', '下载模型组件'),
    ('Verify that the text encoding library loads', '检查文本编码器'),
    ('Download and verify the video model and text encoder', '下载并校验模型'),
    ('Prepare compact FP8 weights for your GPU', '准备适配显卡的模型'),
    ('Run small checks on your GPU', '检查 GPU 加速是否可用'),
    ('Verify prepared weights and apply your storage choice', '校验模型文件'),
    ('Verify dependency compatibility', '检查运行组件'),
    ('Record versions for future diagnostics', '保存安装信息'),
    ('Verify the compiler before building GPU acceleration', '检查编译工具'),
    ('Compile acceleration for your GPU', '编译 GPU 加速组件'),
    ('the first build takes longer', '首次准备需要一些时间'),
    ('Install the prepared GPU acceleration package', '安装 GPU 加速组件'),
    ('Install the verified Windows acceleration package', '安装 Windows 加速组件'),
    ('Verify CUDA and the Windows runtime libraries', '检查 CUDA 与 Windows 运行组件'),
    ('Prepare verified source files', '准备组件文件'), ('Download verified source code', '下载组件文件'),
    ('Prepare source files for installation', '准备安装文件'),
    ('Download and verify required files', '下载并校验所需文件'),
    ('Dependencies resolved', '依赖检查完成'), ('preparing downloads', '准备下载'),
    ('Downloads ready', '下载完成'), ('Downloading packages', '下载组件'), ('installing packages', '安装组件'),
    ('Packages installed', '组件已安装'), ('finishing checks', '完成检查'),
    ('Installing packages', '安装组件'), ('Compiling CUDA kernels', '编译 GPU 加速组件'),
    ('Load cached blocks and upload resident weights', '加载模型'), ('Finish model placement', '完成模型加载'),
    ('Verify cached weights', '校验模型缓存'), ('Read and convert source weights', '转换模型格式'),
    ('Storage ready', '模型文件已就绪'), ('Download', '下载'), ('Model', '模型'),
    ('Video model', '视频模型'), ('Video decoder', '视频解码器'), ('text encoder', '文本编码器'),
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
    ('Restarting this file; incompatible fragments retained on disk', '重新下载此文件，旧片段已保留'),
    ('Cannot resume this format; enable restart in Setup', '此文件无法续传，请允许重新下载'),
    ('Download response too large', '下载数据异常'), ('switching source', '正在切换下载源'),
    ('Download memory budget reached', '正在调整下载方式'), ('switching to streaming', '继续下载'),
    ('Download source failed', '下载源暂不可用'), ('trying another source', '正在尝试其他下载源'),
    ('Download counter mismatch', '下载数据异常'),
    ('setup', '安装'), ('initialize', '初始化'), ('launch', '启动'),
    ('Hugging Face · Video', 'Hugging Face · 视频'), ('Hugging Face · Audio', 'Hugging Face · 音频'),
])


def source_name(value, zh=False):
    return SOURCES.get(value, (value, value))[bool(zh)]


def display(value, zh=False):
    value = clean(value)
    if not zh:
        return {'检查配套包': 'Checking packages'}.get(value, value)
    if value in ZH:
        return ZH[value]
    if value in SOURCES:
        return source_name(value, True)
    if ' · ' in value:
        return ' · '.join(display(part, True) for part in value.split(' · '))
    for prefix, translated in (
        ('Switched current download to ', '已切换至 '), ('Connecting to ', '正在连接 '),
        ('Assemble downloaded parts', '合并已下载文件'), ('Reuse verified FP8 ', '复用模型缓存 '),
        ('Prepare FP8 ', '准备模型缓存 '), ('Verified ', '已校验 '), ('Verify ', '正在校验 '),
        ('Download ', '下载 '), ('Prepare ', '准备 '), ('Source: ', '下载源：'), ('Log: ', '日志：'),
    ):
        if value.startswith(prefix):
            return translated + display(value[len(prefix):], True)
    for pattern, translated in (
        (r'Downloading packages · about (.+) across (\d+) files', r'下载组件 · 约 \1，共 \2 个文件'),
        (r'about (.+) across (\d+) files', r'约 \1，共 \2 个文件'),
        (r'(\d+) files / folders scanned', r'已扫描 \1 个文件和目录'),
        (r'(\d+ / \d+) files', r'\1 个文件'), (r'shard (\d+ / \d+)', r'分片 \1'),
        (r'([\d.]+ MiB) retained', r'已保留 \1'), (r'([\d.]+ GiB) released', r'已释放 \1'),
        (r'([\d.]+ MiB) downloaded', r'已下载 \1'),
        (r'Resuming ([\d.]+ MiB) retained', r'继续下载，已保留 \1'),
        (r'retry (\d+) in (\d+)s', r'\2 秒后第 \1 次重试'),
        (r'Receiving objects:.*', '正在下载组件'), (r'Resolving deltas:.*', '正在整理组件'),
        (r'Updating files:.*', '正在准备文件'),
    ):
        if re.fullmatch(pattern, value):
            return re.sub(pattern, translated, value)
    return value


def progress_view(event, zh=False):
    return {key: display(value, zh) if key in ('label', 'detail') else value
            for key, value in event.items()}
