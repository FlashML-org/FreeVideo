"""Download the pinned uv again when a source update finds it missing from the engine's tools folder.

Security software and cleaners sometimes remove it after installation. The installer fetches it again
(`Installer.ensure_uv`); this does the same for an update, with the same pinned files, sources and hashes.
"""
import json
import os
from pathlib import Path

from . import network
from .long_paths import io_path

PACKAGE = Path(__file__).resolve().parent
TOOL = {'Windows': 'uv.exe', 'Darwin': 'uv', 'Linux': 'uv-x86_64-unknown-linux-gnu/uv'}
# The sources each system's pinned uv comes from; nothing else is probed.
FAMILIES = {'Windows': ('pypi', 'github'), 'Darwin': ('pypi',), 'Linux': ('github',)}


def tool_path(engine_root, system):
    relative = TOOL.get(system)
    return io_path(engine_root) / 'tools' / relative if relative else None


def restore(engine_root, system, *, cancelled=None):
    """Return the engine's uv, downloading and verifying the pinned one when it is missing. Raises on failure."""
    from . import bootstrap
    from .download_settings import read
    from .environments import bootstrap_versions
    tool = tool_path(engine_root, system)
    if tool is None:
        raise RuntimeError('No uv is pinned for ' + str(system))
    if tool.is_file():
        return tool  # Whatever is in place stays.
    root = Path(engine_root)
    versions = bootstrap_versions(json.loads((PACKAGE / 'bootstrap_versions.json').read_text(encoding='utf-8')), system)
    env = network.proxy_environment(dict(os.environ, FREEVIDEO_HOME=str(root)))
    networking = network.plan(json.loads((PACKAGE / 'dependencies.json').read_text(encoding='utf-8')), versions,
                              'unified', env=env, measure_speed=False, only=FAMILIES[system],
                              proxy_mode=read(root / 'download-settings.json')['proxy_mode'])
    networking.update(download_settings_path=str(root / 'download-settings.json'), quiet=True)
    if cancelled is not None:
        networking['resource_check'] = cancelled

    def fetch(url, path, sha, *, size=None, candidates=None, category=None, low_speed_limit=1024,
              max_seconds=None, cycles=2):
        if candidates is None:
            return bootstrap.download(url, path, sha, networking=networking, env=env)
        return network.download(candidates, path, sha, None, network=networking, env=env, size=size,
                                category=category or 'download', low_speed_limit=low_speed_limit,
                                max_seconds=max_seconds, cycles=cycles)

    bootstrap.prepare_uv(system, root, versions['uv'], networking, env, fetch)
    if not tool.is_file():
        raise RuntimeError('The pinned uv was not found after downloading it')
    return tool
