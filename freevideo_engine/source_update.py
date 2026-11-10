"""Explicit, authenticated small updates; unavailable deltas fall back to EXEs."""
import logging
import os
from pathlib import Path
import re
import shutil
import sys

from . import disk_space, execution_paths, network
from . import launcher_update as update
from .top_level_modules import update_modules

ed25519, keys, source = update_modules()

log = logging.getLogger(__name__)
DOMAIN = b'FreeVideo source manifest v1\x00'


def signed(value):
    return source.signed(value)


def metadata_name(candidate):
    version = candidate.get('product_version')
    if not source.VERSION.fullmatch(str(version)):
        raise ValueError('Source updates require a product version')
    return 'source-%s-%s.json' % (version, update.build_target(candidate))


def read_metadata(url, token, *, github=False, limit=source.MAX_MANIFEST):
    with update.open_download(url, token, binary=True, github_only=github) as response:
        return b''.join(update._bytes(response, limit))


def same_build(first, second):
    first, second = update.build_identity(first), update.build_identity(second)
    # publish_release may trim older notes to stay under the 64 KiB legacy cap.
    return all(first.get(key, default) == second.get(key, default) for key, default in (
        ('schema', None), ('repository', None), ('channel', None), ('target', 'windows-x86_64'),
        ('packaging', None), ('track', 'stable'), ('revision', None), ('built_at', None),
        ('version', None), ('product_version', None)))


def fetch_manifest(candidate, token=''):
    """Never infer source trust from cached/remote _update_sources flags."""
    name = metadata_name(candidate)
    github = update.asset_url('v' + candidate['product_version'], name)
    try:
        digest = read_metadata(github + '.sha256', token, github=True, limit=65).decode('ascii').strip()
        if not source.HEX.fullmatch(digest):
            raise ValueError('Invalid GitHub source digest')
        data = read_metadata(github, token, github=True)
        if source.sha(data) != digest:
            raise ValueError('GitHub source digest mismatch')
        value = source.manifest(source.decode(data))
    except (OSError, ValueError, TypeError):
        # Mirrors may host a signed manifest, but a public RFC test key cannot
        # authorize anything. Fail quickly before fetching when still disabled.
        if not keys.ED25519_RELEASE_KEY_ENABLED or keys.ED25519_PUBLIC_KEY == keys.PLACEHOLDER_PUBLIC_KEY:
            raise ValueError('No authenticated source manifest') from None
        data = read_metadata(update.mirror_url('releases/v' + candidate['product_version'] + '/' + name), '')
        value = source.manifest(source.decode(data))
        if not signed(value):
            raise ValueError('Invalid source signature')
    if not same_build(value['build'], candidate):
        raise ValueError('Source manifest belongs to another release')
    return value


def pending_path(home, candidate):
    return source.io_path(home) / 'updates' / ('source-' + candidate['revision'] + '.json')


def try_download(candidate, home, token='', *, progress=None, cancel=None, language=None):
    context = source.bundle_context()
    if context is None or update.build_track(candidate) != 'stable':
        return None
    base, bundle = context
    active = source.ACTIVE
    previous = active['manifest'] if active else base
    current_source = active['source'] if active else bundle
    try:
        def cancelled():
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Source update cancelled')
        cancelled()
        source.storage(source.io_path(home))
        value = fetch_manifest(candidate, token)
        delta = value.get('delta')
        if (not update.newer(value['build'], previous['build']) or not source.compatible(value, base) or
                not delta or delta['from'] != previous['identity'] or
                (not value['gui'] and value.get('launcher_files_changed', True))):
            return None
        target = source.io_path(home) / 'updates' / delta['sha256'] / delta['name']
        # Sparse overlay + complete composed tree + unzip buffer + download.
        needed = delta['bytes'] + 3*sum(row['bytes'] for row in value['files'].values()) + 16*2**20
        if disk_space.free_bytes(target.parent) < needed:
            raise update.UpdateUnavailable('disk')
        sources = {'github': update.asset_url('v' + candidate['product_version'], delta['name']),
                   'mirror': update.mirror_url('releases/v' + candidate['product_version'] + '/' + delta['name'])}
        candidates = [(name, sources[name]) for name in update.source_order(language)]
        def report(done, total, speed):
            cancelled()
            if progress:
                progress(dict(done=done, total=total, bytes_per_second=speed,
                              remaining_seconds=(total-done)/speed if speed > 0 else 0))
        network.download(candidates, target, delta['sha256'], report,
                         network=dict(quiet=True, resource_check=cancelled), size=delta['bytes'],
                         category='launcher-update', cycles=1, stall_seconds=update.UPDATE_STALL_SECONDS,
                         low_speed_limit=0, validate_url=update._trusted_url)
        cancelled()
        folder = source.prepare(value, previous, bundle, current_source, target, home, base)
        cancelled()
        source.atomic_json(pending_path(home, candidate), {'overlay': folder.name, 'identity': value['identity']})
        return folder
    except InterruptedError:
        raise
    except Exception as error:
        # No paths, metadata, response bodies or credentials enter diagnostics.
        log.info('Source update unavailable; using full package (%s)', type(error).__name__)
        if isinstance(error, network.DownloadError):
            update.log_source_failures(home, 'source-update', getattr(error, 'package_attempts', None))
        try:
            pending_path(home, candidate).unlink(missing_ok=True)
        except OSError:
            pass
        return None


def prepared(candidate, home):
    context = source.bundle_context()
    if context is None:
        return None
    base, bundle = context
    path = pending_path(home, candidate)
    if not path.exists():
        return None
    try:
        receipt = source.read_json(path)
        name = receipt['overlay']
        if not re.fullmatch(r'\d+\.\d+\.\d+-[0-9a-f]{16}', name):
            raise ValueError('Invalid overlay name')
        folder = source.io_path(home) / 'overlay' / name
        value = source.validate_overlay(folder, base)
        if (receipt['identity'] != value['identity'] or
                not same_build(value['build'], candidate)):
            raise ValueError('Prepared source identity changed')
        source.compose(bundle, folder, value, source.io_path(home) / 'source-overlay')
        return folder
    except (OSError, ValueError, KeyError, TypeError):
        path.unlink(missing_ok=True)
        raise update.DownloadedLauncherUnavailable('Prepared source changed; download again') from None


def cleanup(home, keep):
    """Only remove validated, updater-owned obsolete directories; never user trees."""
    home = source.io_path(home)
    for folder in (home / 'overlay').iterdir():
        if folder.name in keep or not re.fullmatch(r'\d+\.\d+\.\d+-[0-9a-f]{16}', folder.name):
            continue
        # Another launcher may still be running from this version.
        if source.in_use(home, folder.name):
            continue
        try:
            if folder.is_symlink() or {p.name for p in folder.iterdir()} != {'receipt.json', 'files'}:
                continue
            receipt = source.read_json(folder / 'receipt.json')
            value = source.manifest(receipt['manifest'])
            if source.overlay_name(value) != folder.name or receipt['sha256'] != source.sha(source.canonical(value)):
                continue
            composed = home / 'source-overlay' / folder.name
            if composed.exists():
                source.verify_tree(composed, value['files'])
                shutil.rmtree(composed)
            # An edited overlay is retained for inspection, never executed.
            files = folder / 'files'
            for path in files.rglob('*'):
                if path.is_symlink():
                    raise ValueError('Changed overlay')
                if path.is_file():
                    name = path.relative_to(files).as_posix()
                    source.verify_file(path, value['files'][name])
            shutil.rmtree(folder)
        except (OSError, ValueError, KeyError, TypeError):
            continue


def launch(candidate, home, folder, *, token='', popen=None):
    home = source.io_path(home)
    active_path = home / 'overlay' / 'active.json'
    try:
        old = source.read_json(active_path)
    except (OSError, ValueError):
        old = {'entries': []}
    # Last actually running overlay is the rollback target, even after recovery.
    previous = source.ACTIVE['folder'].name if source.ACTIVE else None
    entries = [folder.name] + ([previous] if previous and previous != folder.name else [])
    from .linux_bundle import child_environment, appimage
    env = dict(child_environment(), FREEVIDEO_LAUNCHER_HOME=str(home), PYINSTALLER_RESET_ENVIRONMENT='1')
    if token:
        env['GITHUB_TOKEN'] = token
    executable = str(appimage() or sys.executable)
    kwargs = dict(cwd=Path(executable).parent, env=env, close_fds=True)
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess_flags()
    else:
        kwargs['start_new_session'] = True
    executable_receipt = home / 'updates' / 'active.json'
    try:
        old_executable = source.read_json(executable_receipt)
    except (OSError, ValueError):
        old_executable = None
    source.atomic_json(active_path, {'entries': entries})
    try:
        # Let the established handoff/resume path finish installing the engine.
        update.save(home / 'updates' / 'active.json', dict(approved=True, candidate=update.manifest(candidate)))
        process = (popen or execution_paths.popen)([executable], **kwargs)
    except BaseException:
        source.atomic_json(active_path, old)
        if old_executable is None:
            executable_receipt.unlink(missing_ok=True)
        else:
            source.atomic_json(executable_receipt, old_executable)
        raise
    try:
        cleanup(home, set(entries))
    except OSError:
        pass
    return process


def subprocess_flags():
    import subprocess
    return subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
