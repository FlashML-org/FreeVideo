"""Acquire the pinned Windows tool without requiring pip or adding launcher bytes."""
import json
import os
from pathlib import Path
import re
import shutil
import zipfile

from . import network


def wheel_spec(uv):
    catalog = json.loads(Path(__file__).with_name('uv_downloads.json').read_text(encoding='utf-8'))
    if catalog['version'] != uv['version'] or catalog['windows']['legacy_sha256'] != uv['sha256']:
        raise ValueError('The uv download catalog does not match the pinned bootstrap version')
    return catalog['windows']


def wheel_url(wheel, name, timeout, env):
    if name in wheel['sources']:
        return wheel['sources'][name]
    return network.wheel_link(network.source_url('pypi', name, env) + '/uv/',
                              '^' + re.escape(wheel['filename']) + '$', timeout, env)


def verified(path, sha, size):
    return path.is_file() and path.stat().st_size == size and network.hash_file(path) == sha


def extract(archive, destination, member, size=None):
    """Only extract the pinned executable, with bounded memory and atomic replacement."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.partial')
    with zipfile.ZipFile(archive) as source:
        info = source.getinfo(member)
        if size is not None and info.file_size != size:
            raise ValueError('Unexpected uv executable size in the verified archive')
        with source.open(info) as incoming, temporary.open('wb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    os.replace(temporary, destination)
    return destination


def prepare_windows(root, uv, networking, env, fetch):
    from .system import bootstrap_root
    root = Path(root)
    bootstrap = bootstrap_root(root)
    wheel = wheel_spec(uv)
    destination = root / 'tools' / uv['executable']
    variants = [
        (wheel, wheel['executable'], [root / 'downloads' / wheel['filename'], bootstrap / wheel['filename']]),
        (dict(bytes=wheel['legacy_bytes'], sha256=uv['sha256']), uv['executable'],
         [root / 'downloads' / 'uv-windows.zip', bootstrap / 'uv.zip']),
    ]
    # Inspect both formats before contacting any server. A fully downloaded
    # .partial from an interrupted final publication is also reusable.
    for artifact, member, paths in variants:
        for path in paths:
            for candidate in (path, path.with_name(path.name + '.partial')):
                if verified(candidate, artifact['sha256'], artifact['bytes']):
                    return extract(candidate, destination, member, artifact.get('executable_bytes'))

    candidates = []
    names = network.ordered(networking, 'pypi')
    if not networking.get('sources', {}).get('pypi') and networking.get('mode') != 'official':
        names = list(wheel['sources'])
    for name in names:
        # Built-in mirrors use pinned artifact URLs, so no index/pip/Python
        # installation is needed. Custom indexes still retain their semantics.
        for route in network.route_order(networking, 'pypi', name, env):
            try:
                address = wheel_url(wheel, name, networking.get('timeout_seconds', 5),
                                    network.route_environment(networking, 'pypi', name, env, route))
                candidates.append((name, address))
                break
            except (OSError, ValueError, RuntimeError):
                network.event(networking, category='pypi', source=name, route=route,
                              file=wheel['filename'], action='fallback', reason='wheel-index-unavailable')

    attempts = [
        (wheel, root / 'downloads' / wheel['filename'], wheel['executable'], candidates, 'pypi'),
        (dict(bytes=wheel['legacy_bytes'], sha256=uv['sha256']), root / 'downloads' / 'uv-windows.zip',
         uv['executable'], network.urls(networking, 'github', uv['url'], env), 'github'),
    ]
    for index, (artifact, archive, member, sources, family) in enumerate(attempts):
        if not sources:
            continue
        # Retain an invalid completed tool archive rather than blocking repair.
        # Wheel and release ZIP partials never share a path or a hash.
        if archive.exists():
            network.retain_partial(archive, 'hash-rejected')
        try:
            fetch(sources[0][1], archive, artifact['sha256'], size=artifact['bytes'],
                  candidates=sources, category=family, low_speed_limit=32 * 1024, max_seconds=180, cycles=1)
        except network.DownloadError:
            if index + 1 == len(attempts):
                raise
            continue
        return extract(archive, destination, member, artifact.get('executable_bytes'))
    raise network.DownloadError('No uv download source is available; cached files retained')
