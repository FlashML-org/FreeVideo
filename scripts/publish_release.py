"""Publish the built launchers: the rolling nightly, or a stable vX.Y.Z release for every platform.

The release workflow runs this after the Windows and Mac jobs. A stable release also refreshes the
platform tags (windows-preview, macos-preview) that launchers from before combined releases read.
Pushes to the prism-beta branch refresh only the rolling, Windows-only prism-preview prerelease.
Writes go through the gh CLI with retries. Update metadata is uploaded last and bound to the
immutable asset IDs, so a reader never pairs new metadata with an old executable.
Usage: publish_release.py --track stable|nightly --windows DIR --macos DIR --sha COMMIT
       publish_release.py --track prism --windows DIR --sha COMMIT
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freevideo_engine.launcher_update import (CHANNEL, MAC_CHANNEL, NIGHTLY_TAG, PRISM_TAG, RELEASE_ASSETS,  # noqa: E402
                                              TRACKS, build_identity, build_track)
from freevideo_engine.release_notes import notes  # noqa: E402

PLATFORMS = (CHANNEL, MAC_CHANNEL)
# The Prism beta is built from this branch; its release tag has the same name.
PRISM_BRANCH = 'prism-beta'
PRISM_TITLE = 'Prism beta (rolling)'
PAGE = 'https://github.com/FlashML-org/FreeVideo'
LOGO = '''<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://freevideo-community.pages.dev/logo-dark.svg">
    <img alt="FreeVideo" src="https://freevideo-community.pages.dev/logo-light.svg" width="55%">
  </picture>
</div>
'''
COMMUNITY = ('[Discord](https://discord.gg/MsA277cJzZ) · [QQ Group / QQ 群](https://freevideo-community.pages.dev/qq)'
             ' · [WeChat Group / 微信群](https://freevideo-community.pages.dev/wechat)')


def gh(label, *args, parse=False):
    """Run gh; retry transient failures, including empty or truncated JSON."""
    for attempt in range(1, 4):
        print('%s (attempt %d/3)' % (label, attempt), file=sys.stderr, flush=True)
        try:
            done = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            done = None
        if done is not None and done.returncode == 0:
            if not parse:
                return done.stdout
            try:
                return json.loads(done.stdout)
            except ValueError:
                print('%s returned incomplete JSON; retrying the read' % label, file=sys.stderr)
        elif done is not None:
            sys.stderr.write(done.stderr)
        if attempt < 3:
            time.sleep(2 * attempt)
    raise SystemExit('::error::%s failed after 3 attempts' % label)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def load_builds(windows, macos):
    builds = {}
    for channel, folder in ((CHANNEL, windows), (MAC_CHANNEL, macos)):
        if folder is None:
            continue  # The Prism beta has no Mac build.
        folder = Path(folder)
        raw = json.loads((folder / 'launcher-build.json').read_text(encoding='utf-8'))
        identity = build_identity(raw)
        if identity['channel'] != channel:
            raise SystemExit('%s holds a %s build' % (folder, identity['channel']))
        executable = folder / RELEASE_ASSETS[channel][0]
        if not executable.is_file():
            raise SystemExit('Missing %s' % executable)
        builds[channel] = dict(raw=raw, identity=identity, path=executable, sha256=digest(executable))
    versions = {b['identity'].get('product_version') for b in builds.values()}
    tracks = {build_track(b['identity']) for b in builds.values()}
    if len(versions) != 1 or None in versions:
        raise SystemExit('Windows and Mac builds must carry the same product version: %s' % sorted(map(str, versions)))
    if len(tracks) != 1:
        raise SystemExit('Windows and Mac builds come from different release tracks')
    return builds


def escape(text):
    return re.sub(r'([\\`*_{}\[\]()#+.!<>|~-])', r'\\\1', text)


def whats_new(raw):
    release = notes(raw.get('release_notes'))
    if not release:
        raise SystemExit('The build has no bilingual release notes')
    rows = []
    for language, heading in (('en', "What's new"), ('zh', '更新内容')):
        rows += ['## ' + heading, '', escape(release[language]['summary']), '']
        rows += ['- ' + escape(item) for item in release[language]['changes']] + ['']
    return rows


def downloads(base):
    return ['| | |', '|---|---|',
            '| **Windows** 10/11 · NVIDIA GPU | [FreeVideo.exe](%sFreeVideo.exe) |' % base,
            '| **macOS** 14+ · Apple silicon | [FreeVideo-Mac-arm64.dmg](%sFreeVideo-Mac-arm64.dmg) |' % base, '']


def checksums(builds):
    return ['<details><summary>sha256</summary>', '', '```',
            *('%s  %s' % (builds[c]['sha256'], builds[c]['path'].name) for c in PLATFORMS if c in builds),
            '```', '</details>', '']


def stable_body(builds, tag):
    # Visitors come to download: the files come first, then what changed.
    rows = [LOGO, '## Download / 下载', ''] + downloads(PAGE + '/releases/download/' + tag + '/')
    rows += ['**Windows:** run FreeVideo.exe, choose an existing ComfyUI folder or install a new one, then click '
             '**Install & launch**. Offline packages are on [Quark](https://pan.quark.cn/s/c51235b84618).',
             '',
             '**macOS:** open the DMG and drag FreeVideo.app into Applications. If macOS asks you to confirm the first '
             'launch, see the [first-open steps](%s/blob/main/docs/Mac.md#first-open).' % PAGE,
             '',
             '**Windows：** 运行 FreeVideo.exe，选择已有的 ComfyUI 或安装新的 ComfyUI，点击 **安装并启动**。'
             '离线包见[夸克网盘](https://pan.quark.cn/s/c51235b84618)。',
             '',
             '**macOS：** 打开 DMG，将 FreeVideo.app 拖入「应用程序」。首次打开如需确认，请参考'
             '[首次打开步骤](%s/blob/main/docs/Mac.zh-CN.md#首次打开)。' % PAGE,
             '']
    rows += whats_new(builds[CHANNEL]['raw'])
    rows += checksums(builds)
    rows += [COMMUNITY, '']
    return '\n'.join(rows)


def nightly_body(builds, sha):
    built = time.strftime('%Y-%m-%d %H:%M', time.gmtime(max(b['identity']['built_at'] for b in builds.values())))
    size = {c: '%.1f MB' % (builds[c]['path'].stat().st_size / 1e6) for c in PLATFORMS}
    rows = ['## FreeVideo nightly (rolling)', '',
            'Automated build of `main`, refreshed after every merge. For everyday use, download the '
            '[latest release](%s/releases/latest).' % PAGE,
            '',
            '基于 `main` 的自动构建，每次合并后更新。日常使用请下载[最新正式版](%s/releases/latest)。' % PAGE,
            '']
    rows += downloads(PAGE + '/releases/download/' + NIGHTLY_TAG + '/')
    rows += ['| commit | built (UTC) | version | Windows | macOS |', '|---|---|---|---|---|',
             '| [`%s`](%s/commit/%s) | %s | %s | %s | %s |' % (sha[:9], PAGE, sha, built,
                                                            escape('v' + builds[CHANNEL]['identity']['product_version']),
                                                            size[CHANNEL], size[MAC_CHANNEL]), '']
    rows += checksums(builds)
    return '\n'.join(rows)


class Releases:
    def __init__(self, repository):
        self.repository = repository

    def api(self, label, path, *args, parse=True):
        return gh(label, 'api', *args, 'repos/%s/%s' % (self.repository, path), parse=parse)

    def tag_exists(self, tag):
        refs = self.api('Read tag %s' % tag, 'git/matching-refs/tags/' + tag)
        return any(r.get('ref') == 'refs/tags/' + tag for r in refs)

    def point_tag(self, tag, sha):
        if self.tag_exists(tag):
            self.api('Move tag %s' % tag, 'git/refs/tags/' + tag, '-X', 'PATCH', '-f', 'sha=' + sha, '-F', 'force=true')
        else:
            gh('Create tag %s' % tag, 'api', '-X', 'POST', 'repos/%s/git/refs' % self.repository,
               '-f', 'ref=refs/tags/' + tag, '-f', 'sha=' + sha, parse=True)

    def find(self, tag):
        pages = gh('Read releases including drafts', 'api', '--paginate', '--slurp',
                   'repos/%s/releases?per_page=100' % self.repository, parse=True)
        return next((r for page in pages for r in page if r.get('tag_name') == tag), None)

    def assets(self, tag):
        release = self.find(tag)
        if release is None:
            raise SystemExit('Release %s disappeared while publishing' % tag)
        return {a['name']: a for a in self.api('Read %s assets' % tag, 'releases/%d' % release['id'])['assets']}


def metadata(build, asset, path):
    if asset['size'] != build['path'].stat().st_size:
        raise SystemExit('Uploaded %s differs in size from the built file' % asset['name'])
    value = dict(build['raw'], asset=dict(id=asset['id'], bytes=asset['size'], sha256=build['sha256']))
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')
    return path


def write(folder, name, text):
    path = Path(folder) / name
    path.write_text(text, encoding='utf-8')
    return str(path)


def bind_metadata(releases, tag, builds, channels, folder, names):
    """Upload update metadata for each platform, bound to the assets now on the release."""
    uploaded = releases.assets(tag)
    files = [metadata(builds[c], uploaded[builds[c]['path'].name], Path(folder) / names[c]) for c in channels]
    gh('Upload update metadata to %s' % tag, 'release', 'upload', tag, *map(str, files), '--clobber')


def publish_stable(releases, builds, sha, folder):
    version = builds[CHANNEL]['identity']['product_version']
    tag = 'v' + version
    if releases.tag_exists(tag) or releases.find(tag) is not None:
        raise SystemExit('::error::%s is already released. Bump product_version and the notes in '
                         'freevideo_engine/release_notes.json, merge, then publish again.' % tag)
    sums = write(folder, 'SHA256SUMS.txt', ''.join('%s  %s\n' % (builds[c]['sha256'], builds[c]['path'].name) for c in PLATFORMS))
    body = write(folder, 'release-body.md', stable_body(builds, tag))
    title = 'FreeVideo ' + tag
    releases.point_tag(tag, sha)
    gh('Create draft %s' % tag, 'release', 'create', tag, '--verify-tag', '--draft', '--title', title, '--notes-file', body)
    gh('Upload launchers to %s' % tag, 'release', 'upload', tag, *(str(builds[c]['path']) for c in PLATFORMS), sums, '--clobber')
    bind_metadata(releases, tag, builds, PLATFORMS, folder, {c: RELEASE_ASSETS[c][1] for c in PLATFORMS})
    gh('Publish %s' % tag, 'release', 'edit', tag, '--draft=false', '--prerelease=false', '--latest',
       '--title', title, '--notes-file', body)
    # Launchers from before combined releases read the platform tags; keep them current.
    for channel in PLATFORMS:
        build = builds[channel]
        name = 'Windows' if channel == CHANNEL else 'Mac'
        legacy = write(folder, 'legacy-%s.md' % channel,
                       'The latest FreeVideo is on the [latest release](%s/releases/latest) page.\n\n'
                       '最新版本请前往[最新正式版](%s/releases/latest)下载。\n' % (PAGE, PAGE))
        legacy_sums = write(folder, 'SHA256SUMS-%s.txt' % channel, '%s  %s\n' % (build['sha256'], build['path'].name))
        releases.point_tag(channel, sha)
        if releases.find(channel) is None:
            gh('Create %s' % channel, 'release', 'create', channel, '--verify-tag', '--prerelease', '--latest=false',
               '--title', 'FreeVideo %s for %s' % (tag, name), '--notes-file', legacy)
        staged = Path(folder) / channel
        staged.mkdir(exist_ok=True)
        (staged / 'SHA256SUMS.txt').write_bytes(Path(legacy_sums).read_bytes())
        gh('Upload %s launcher' % channel, 'release', 'upload', channel, str(build['path']),
           str(staged / 'SHA256SUMS.txt'), '--clobber')
        bind_metadata(releases, channel, builds, (channel,), staged, {channel: 'update.json'})
        gh('Describe %s' % channel, 'release', 'edit', channel, '--draft=false', '--prerelease', '--latest=false',
           '--title', 'FreeVideo %s for %s' % (tag, name), '--notes-file', legacy)
    print('Published %s/releases/tag/%s' % (PAGE, tag))


def prism_body(builds, sha):
    build = builds[CHANNEL]
    built = time.strftime('%Y-%m-%d %H:%M', time.gmtime(build['identity']['built_at']))
    guide = '%s/blob/%s/docs/' % (PAGE, sha)
    rows = ['## FreeVideo Prism beta (rolling)', '',
            'Experimental Windows build with the [Prism preview](%sPrism.md), refreshed after every push to the '
            '`%s` branch. It updates itself from this page. For everyday use, download the '
            '[latest release](%s/releases/latest).' % (guide, PRISM_BRANCH, PAGE),
            '',
            '包含 [Prism 预览](%sPrism.zh-CN.md)的 Windows 实验版，`%s` 分支每次推送后更新，并从本页自动更新。'
            '日常使用请下载[最新正式版](%s/releases/latest)。' % (guide, PRISM_BRANCH, PAGE),
            '',
            '| | |', '|---|---|',
            '| **Windows** 10/11 · NVIDIA GPU | [FreeVideo.exe](%s/releases/download/%s/FreeVideo.exe) |'
            % (PAGE, PRISM_TAG), '',
            '| commit | built (UTC) | version | Windows |', '|---|---|---|---|',
            '| [`%s`](%s/commit/%s) | %s | %s | %.1f MB |' % (sha[:9], PAGE, sha, built,
                                                       escape('v' + build['identity']['product_version']),
                                                       build['path'].stat().st_size / 1e6), '']
    rows += checksums(builds)
    return '\n'.join(rows)


def ignored(path):
    # Changes that never trigger a build (see the workflow's push paths-ignore).
    return path.endswith('.md') or path.startswith('.github/')


def superseded(releases, sha, branch='main'):
    head = releases.api('Read current %s' % branch, 'git/ref/heads/' + branch)['object']['sha']
    if head == sha:
        return False
    comparison = releases.api('Compare with current %s' % branch, 'compare/%s...%s' % (sha, head))
    files = comparison.get('files')
    later_only_ignored = (comparison.get('status') == 'ahead'
                          and comparison.get('merge_base_commit', {}).get('sha') == sha
                          and isinstance(files, list) and files
                          and all(ignored(f.get('filename', '')) and ignored(f.get('previous_filename', f.get('filename', '')))
                                  for f in files))
    return not later_only_ignored


def publish_nightly(releases, builds, sha, folder):
    if superseded(releases, sha):
        print('main moved on; the newer build publishes the nightly')
        return
    sums = write(folder, 'SHA256SUMS.txt', ''.join('%s  %s\n' % (builds[c]['sha256'], builds[c]['path'].name) for c in PLATFORMS))
    body = write(folder, 'release-body.md', nightly_body(builds, sha))
    releases.point_tag(NIGHTLY_TAG, sha)
    if releases.find(NIGHTLY_TAG) is None:
        gh('Create nightly', 'release', 'create', NIGHTLY_TAG, '--verify-tag', '--draft', '--prerelease', '--latest=false',
           '--title', 'Nightly (rolling)', '--notes-file', body)
    gh('Upload nightly launchers', 'release', 'upload', NIGHTLY_TAG, *(str(builds[c]['path']) for c in PLATFORMS), sums, '--clobber')
    bind_metadata(releases, NIGHTLY_TAG, builds, PLATFORMS, folder, {c: RELEASE_ASSETS[c][1] for c in PLATFORMS})
    keep = {builds[c]['path'].name for c in PLATFORMS} | {RELEASE_ASSETS[c][1] for c in PLATFORMS} | {'SHA256SUMS.txt'}
    for name in sorted(set(releases.assets(NIGHTLY_TAG)) - keep):
        gh('Remove stale %s' % name, 'release', 'delete-asset', NIGHTLY_TAG, name, '--yes')
    gh('Publish nightly', 'release', 'edit', NIGHTLY_TAG, '--draft=false', '--prerelease', '--latest=false',
       '--title', 'Nightly (rolling)', '--notes-file', body)
    print('Published %s/releases/tag/%s' % (PAGE, NIGHTLY_TAG))


def publish_prism(releases, builds, sha, folder):
    """Refresh only the prism-preview prerelease: no other release, tag or asset is touched."""
    if superseded(releases, sha, PRISM_BRANCH):
        print('%s moved on; the newer build publishes the Prism beta' % PRISM_BRANCH)
        return
    build = builds[CHANNEL]
    sums = write(folder, 'SHA256SUMS.txt', '%s  %s\n' % (build['sha256'], build['path'].name))
    body = write(folder, 'release-body.md', prism_body(builds, sha))
    releases.point_tag(PRISM_TAG, sha)
    if releases.find(PRISM_TAG) is None:
        gh('Create Prism beta', 'release', 'create', PRISM_TAG, '--verify-tag', '--draft', '--prerelease', '--latest=false',
           '--title', PRISM_TITLE, '--notes-file', body)
    gh('Upload Prism beta launcher', 'release', 'upload', PRISM_TAG, str(build['path']), sums, '--clobber')
    bind_metadata(releases, PRISM_TAG, builds, (CHANNEL,), folder, {CHANNEL: RELEASE_ASSETS[CHANNEL][1]})
    gh('Publish Prism beta', 'release', 'edit', PRISM_TAG, '--draft=false', '--prerelease', '--latest=false',
       '--title', PRISM_TITLE, '--notes-file', body)
    print('Published %s/releases/tag/%s' % (PAGE, PRISM_TAG))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track', choices=TRACKS, required=True)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--macos', type=Path, help='required for stable and nightly; the Prism beta is Windows only')
    parser.add_argument('--sha', required=True)
    parser.add_argument('--repository', default=os.environ.get('GH_REPO', 'FlashML-org/FreeVideo'))
    args = parser.parse_args()
    if (args.macos is None) != (args.track == 'prism'):
        parser.error('--macos is required for stable and nightly and not accepted for prism')
    builds = load_builds(args.windows, args.macos)
    if build_track(builds[CHANNEL]['identity']) != args.track:
        raise SystemExit('The builds are %s builds, not %s' % (build_track(builds[CHANNEL]['identity']), args.track))
    releases = Releases(args.repository)
    with tempfile.TemporaryDirectory(prefix='freevideo-publish-') as folder:
        publish = dict(stable=publish_stable, nightly=publish_nightly, prism=publish_prism)[args.track]
        publish(releases, builds, args.sha, folder)


if __name__ == '__main__':
    main()
