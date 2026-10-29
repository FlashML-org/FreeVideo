"""Licences the user accepts before an installation or an offline first start.

The consent keeps one "View licenses" link; it opens a list of these rows.
A row names the component and its licence in English and Chinese and links
to the licence. A text file on this computer comes first, so the licence
reads without a connection; a web page otherwise.
"""
from pathlib import Path

from .long_paths import io_path, resolved, plain

HERE = Path(plain(resolved(__file__))).parent
# The full texts of what the Windows runtime package carries, shipped with every launcher, so
# a runtime package built before them still opens them without a connection.
TEXTS = HERE / 'licenses/runtime'
NOTICES = 'THIRD_PARTY_NOTICES.txt'
# Where and how the model may be used, below its row; said, never asked to be confirmed.
MODEL_TERMS = ('The MiniMax H3 Community License Agreement grants rights only outside the European Union, the United Kingdom, '
               'the Republic of Korea and the United States, and restricts how the model and its outputs may be used '
               '(for example, content posted publicly must be disclosed as AI-generated). See the agreement and its Exhibit A.',
               'MiniMax H3 社区许可协议只授权在欧盟、英国、韩国和美国以外的地区使用，并对模型及其生成内容的用途有限制'
               '（例如公开发布生成的内容时须标明由 AI 生成），详见协议原文及附件 A。')
MODEL_URL = 'https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE'
MODEL_TITLE = 'MiniMax H3 COMMUNITY LICENSE AGREEMENT'


def row(key, name, licence, link, text=None, terms=None):
    """One licence: name, licence and terms are (English, Chinese); text names its full text in TEXTS."""
    return dict(key=key, name=name, license=licence, link=str(link), text=text, terms=terms)


MH3 = ('MiniMax H3 Community License Agreement', 'MiniMax H3 社区许可协议')
MODEL = row('model', ('MiniMax H3 model', 'MiniMax H3 模型'), MH3, MODEL_URL, terms=MODEL_TERMS)
ENCODER = row('encoder', ('H3 text encoder', 'H3 文本编码器'),
              ('MiniMax H3 Community License Agreement, with the Qwen3-VL Apache License 2.0 notice',
               'MiniMax H3 社区许可协议，附 Qwen3-VL 的 Apache 2.0 声明'),
              'https://huggingface.co/t8star/Vdn-Minimax-H3-Comfy/blob/main/LICENSE')
DEPTH = row('depth-reference', ('Depth Anything V2 Small code and weights', 'Depth Anything V2 Small 代码和权重'),
            ('Apache License 2.0', 'Apache 许可证 2.0'),
            'https://github.com/DepthAnything/Depth-Anything-V2/blob/a561b849ebae10a6f5ef49e26c83cbbcd36c71bf/LICENSE')


def depth_license():
    return local(DEPTH, HERE / 'licenses/Depth-Anything-V2-Apache-2.0.txt')
# Libraries a runtime may carry, found by file name: versioned Windows names and Linux
# lib*.so.* alike. A CUDA build of Torch counts for CUDA, so a runtime whose CUDA files
# are named otherwise still lists it.
RUNTIME = (
    (row('cuda', ('NVIDIA CUDA', 'NVIDIA CUDA'),
         ('NVIDIA CUDA Toolkit End User License Agreement', 'NVIDIA CUDA 工具包最终用户许可协议'),
         'https://docs.nvidia.com/cuda/eula/index.html', text='NVIDIA-CUDA-EULA.txt'),
     ('cudart', 'cublas', 'cufft', 'curand', 'cusolver', 'cusparse', 'nvrtc', 'nvjitlink', 'cupti', 'nvtoolsext',
      'nvperf', 'ptxas', 'cuda.lib', 'torch_cuda', 'c10_cuda')),
    (row('cudnn', ('NVIDIA cuDNN', 'NVIDIA cuDNN'),
         ('NVIDIA cuDNN Software License Agreement', 'NVIDIA cuDNN 软件许可协议'),
         'https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html', text='NVIDIA-cuDNN-SLA.txt'), ('cudnn',)),
    # Intel's page lists several licences; the row names the one that applies.
    (row('intel', ('Intel OpenMP', 'Intel OpenMP'),
         ('Intel End User License Agreement for Developer Tools', '英特尔开发者工具最终用户许可协议'),
         'https://www.intel.com/content/www/us/en/developer/articles/license/end-user-license-agreement.html',
         text='Intel-EULA-Developer-Tools.txt'), ('libiomp5', 'libiompstubs5')),
    (row('rocm', ('AMD ROCm', 'AMD ROCm'), ('ROCm component licenses', 'ROCm 各组件许可'),
         'https://rocm.docs.amd.com/en/latest/about/license.html'), ('amdhip64', 'rocblas', 'hipblas', 'miopen')),
)


def library_names(paths):
    """Library file names from paths, lower case and without a leading 'lib' (Intel's keep it)."""
    names = set()
    for path in paths:
        name = str(path).replace('\\', '/').rsplit('/', 1)[-1].lower()
        if name.endswith(('.dll', '.exe', '.lib', '.dylib', '.so')) or '.so.' in name or '.' not in name:
            names.add(name[3:] if name.startswith('lib') and not name.startswith('libiomp') else name)
    return names


def file_exists(path):
    """Whether path is a file; False also when it cannot be looked at (a folder the user may not open)."""
    try:
        return io_path(path).is_file()
    except OSError:
        return False


def local(value, *paths, title=None):
    """The row opening the first of these files that exists (and starts with title), or as it was."""
    def fits(path):
        if not file_exists(path):
            return False
        if title is None:
            return True
        try:
            with io_path(path).open('rb') as file:
                return file.read(256).decode('utf-8', 'replace').lstrip('﻿ \r\n').startswith(title)
        except OSError:
            return False
    path = next((Path(p) for p in paths if p and fits(Path(p))), None)
    return dict(value, link=plain(path)) if path else value


def text(value):
    """The row opening its full text on this computer when FreeVideo ships one."""
    return local(value, TEXTS / value['text']) if value['text'] else value


def bundle(root, value):
    """What an offline runtime asks the user to accept: its models, libraries and notices.

    Read from the package's file list, so a runtime without NVIDIA's or Intel's
    libraries does not ask the user to accept their terms.
    """
    root = Path(root)
    names = library_names(entry['path'] for entry in value['files'])
    # A model folder may hold another LICENSE (a LoRA's, say): only the agreement itself opens.
    # The text encoder is under the same agreement, Qwen3-VL's notice at its end.
    model = local(MODEL, root / 'models/edge/LICENSE', root / value.get('model_root', 'models/base') / 'LICENSE', title=MODEL_TITLE)
    rows = [model, dict(ENCODER, link=model['link']) if is_file(model['link']) else ENCODER,
            *(text(licence) for licence, prefixes in RUNTIME if any(name.startswith(prefixes) for name in names))]
    if any(licence['text'] for licence in rows[2:]) and file_exists(TEXTS / NOTICES):
        # The rest of what the Windows runtime carries (Triton's tools, TinyCC) and where its source is.
        rows.append(row('notices', ('Third-party notices', '第三方组件说明'),
                        ('Copyrights and licenses of the other components', '其余组件的版权和许可'), TEXTS / NOTICES))
    if any('depth_anything_v2/' in entry['path'].replace('\\', '/')
           or entry['path'].endswith('depth_anything_v2_vits.pth') for entry in value['files']):
        rows.append(depth_license())
    return rows


def named(url):
    """A row for a licence link from an installation plan; one not listed here shows its site."""
    from urllib.parse import urlsplit
    if url.startswith('https://huggingface.co/depth-anything/Depth-Anything-V2-Small/'):
        return depth_license()
    prefixes = ((MODEL, 'https://huggingface.co/OpenVDN/'), (ENCODER, 'https://huggingface.co/t8star/'),
                *((value, value['link'].rsplit('/', 1)[0] + '/') for value, _ in RUNTIME))
    known = next((value for value, prefix in prefixes if url.startswith(prefix)), None)
    if known:
        return dict(known, link=url)
    site = urlsplit(url).hostname or url
    return row('link', (site, site), (url, url), url)


def is_file(link):
    return not str(link).startswith(('https://', 'http://'))
