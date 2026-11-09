"""The managed host's dependency contract, without ComfyUI's example library."""
import re

from .runtime_libraries import normalize_name


def requirements_content(path):
    """Keep upstream intact; omit the entire template family, including its meta package.

    The meta package requires the media packages unconditionally. Keeping it
    would bring the media back on install and make pip check fail without them.
    The policy comment is hashed with the effective requirements in host receipts.
    """
    original = path.read_bytes()
    rows = original.decode('utf-8').splitlines(keepends=True)
    kept = []
    for row in rows:
        match = re.match(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)', row)
        name = normalize_name(match[1]) if match else ''
        if name == 'comfyui-workflow-templates' or name.startswith('comfyui-workflow-templates-'):
            continue
        kept.append(row)
    if len(kept) == len(rows):
        return original
    return ('# FreeVideo managed host: omit workflow templates (policy v1).\n' + ''.join(kept)).encode('utf-8')


def managed_requirements(source, directory):
    content = requirements_content(source)
    if content == source.read_bytes():
        return source, content
    target = directory / 'comfy-requirements.txt'
    target.write_bytes(content)
    return target, content
