"""The managed host's dependency contract, without ComfyUI's example library."""
import re

from .runtime_libraries import normalize_name


# ComfyUI imports requests at start-up (app/frontend_management.py) without declaring it:
# it used to arrive through huggingface_hub, which no longer depends on it.
# Installed with the requirements, but not part of the identity below, so an environment
# that is already complete keeps being recognised as it is.
HOST_PACKAGES = ('requests',)


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


def install_content(path):
    """The requirements to install: the identity content plus what ComfyUI needs but does not name."""
    content = requirements_content(path)
    declared = set()
    for row in content.decode('utf-8').splitlines():
        match = re.match(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)', row)
        if match:
            declared.add(normalize_name(match[1]))
    missing = [name for name in HOST_PACKAGES if normalize_name(name) not in declared]
    if not missing:
        return content
    if content and not content.endswith((b'\n', b'\r')):
        content += b'\n'
    return content + ('# FreeVideo managed host: add packages ComfyUI imports but does not declare.\n'
                      + ''.join(name + '\n' for name in missing)).encode('utf-8')


def managed_requirements(source, directory):
    """(file to install from, content its identity is hashed from)."""
    content = requirements_content(source)
    install = install_content(source)
    if install == source.read_bytes():
        return source, content
    target = directory / 'comfy-requirements.txt'
    target.write_bytes(install)
    return target, content
