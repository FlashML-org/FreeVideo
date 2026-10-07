"""Check an installation source bundle without models, a GPU or network access."""
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


def roomy_disk():
    """Offline setup plans check the free disk under a temporary folder. CI runners have
    about 14 GiB free, so these checks report plenty: they test the plan, not this machine."""
    import shutil
    from unittest import mock
    real = shutil.disk_usage
    def usage(path):
        value = real(path)
        return type(value)(value.total + 2**41, value.used, value.free + 2**41)
    return mock.patch('shutil.disk_usage', usage)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NoModelImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('torch', 'triton', 'transformers', 'diffusers'):
            raise RuntimeError('Installation unexpectedly imports ' + fullname)


def check_update_tracks():
    """The prism track follows only the prism-preview prerelease; no track is offered another's builds."""
    from freevideo_engine import comfy_updates, launcher_update as lu
    base = dict(schema=1, repository=lu.REPOSITORY, channel=lu.CHANNEL, revision='1' * 40,
                built_at=1000, version='2026.10.7.1')
    later = dict(revision='2' * 40, built_at=2000, version='2026.10.7.2')
    prism = lu.build_identity(dict(base, track='prism'))
    assert prism['track'] == 'prism' and lu.release_page(prism).endswith('/releases/tag/prism-preview')
    try:
        lu.build_identity(dict(base, track='beta'))
        raise AssertionError('An unknown release track was accepted')
    except ValueError:
        pass
    assert lu.newer(dict(prism, **later), prism) and not lu.newer(prism, dict(prism, **later))
    for track in ('stable', 'nightly'):
        other = lu.build_identity(dict(base, track=track))
        assert not lu.newer(dict(other, **later), prism) and not lu.newer(dict(prism, **later), other)

    def release(tag, metadata_id, **build):
        executable = dict(id=metadata_id - 1, name='FreeVideo.exe', size=1234)
        responses[lu.API + '/releases/assets/%d' % metadata_id] = dict(
            base, **later, **build, asset=dict(id=executable['id'], bytes=1234, sha256='0' * 64))
        return dict(tag_name=tag, draft=False, prerelease=tag != 'v0.3.1',
                    assets=[executable, dict(id=metadata_id, name='update-windows.json', size=1)])
    responses = {}
    responses[lu.API + '/releases/tags/prism-preview'] = release('prism-preview', 11, track='prism')
    responses[lu.API + '/releases/tags/nightly'] = release('nightly', 21, track='nightly')
    responses[lu.API + '/releases/latest'] = release('v0.3.1', 31)
    requested = []

    def fake_json(url, token, *, binary=False, limit=2**20):
        requested.append(url)
        if url not in responses:
            raise AssertionError('Unexpected update request ' + url)
        return json.loads(json.dumps(responses[url]))
    original = lu._json
    lu._json = fake_json
    try:
        found = lu.check(prism)
        assert found and found['track'] == 'prism' and found['revision'] == later['revision']
        assert requested == [lu.API + '/releases/tags/prism-preview', lu.API + '/releases/assets/11'], requested
        status = comfy_updates.UpdateStatus(prism)
        status._check()
        assert status.state['track'] == 'prism' and status.state['status'] == 'available'
        assert requested[-2] == lu.API + '/releases/tags/prism-preview'
        for track, tag in (('stable', 'latest'), ('nightly', 'tags/nightly')):
            del requested[:]
            found = lu.check(lu.build_identity(dict(base, track=track)))
            assert found and lu.build_track(found) == track and requested[0] == lu.API + '/releases/' + tag
            assert not any('prism' in url for url in requested), requested
        # A build of another track published under the prism-preview tag is refused.
        responses[lu.API + '/releases/assets/11']['track'] = 'nightly'
        try:
            lu.check(prism)
            raise AssertionError('A nightly build under the prism-preview tag was not refused')
        except ValueError:
            pass
    finally:
        lu._json = original
    # Every build of this branch, also a local one, is on the prism track.
    import os
    from scripts import build_windows
    saved = os.environ.pop('FREEVIDEO_RELEASE_TRACK', None)
    try:
        assert build_windows.release_track() == {'track': 'prism'}
        for track in ('stable', 'nightly'):
            os.environ['FREEVIDEO_RELEASE_TRACK'] = track
            try:
                build_windows.release_track()
                raise AssertionError('A %s build of the Prism beta was allowed' % track)
            except SystemExit:
                pass
    finally:
        os.environ.pop('FREEVIDEO_RELEASE_TRACK', None)
        if saved is not None:
            os.environ['FREEVIDEO_RELEASE_TRACK'] = saved


def check_prism_download_first(fixture, folder):
    """Adding Prism to a ready installation downloads it before setup marks the installation unfinished.

    Offline: the installed environments and the download are stand-ins; the
    plan decision and bootstrap.main's order of steps are the real ones.
    """
    import contextlib
    import copy
    import io
    import threading
    import time
    from freevideo_engine import bootstrap
    root = Path(folder) / 'ready'
    root.mkdir()
    machine = root / 'machine.json'
    machine.write_text(json.dumps(dict(
        ready=True, root=str(root), environment_layout='unified', python='python', comfy_python='python',
        comfy_root=str(root / 'vendor'), vdn_root=str(root / 'vendor'), model_root=str(root / 'models' / 'vdn'),
        cache=str(root / 'cache'), base='base', checkpoint='checkpoint', model_paths='paths', encoder='encoder',
        gpu_uuid='GPU-00000000-0000-0000-0000-000000004070')), encoding='utf-8')
    before = machine.read_bytes()
    original = dict(dependency_status=bootstrap.dependency_status, plan=bootstrap.plan,
                    Prefetcher=bootstrap.Prefetcher, Installer=bootstrap.Installer, sleep=time.sleep)
    def planned(*extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = bootstrap.main(['--root', str(root), '--plan', '--json', '--hardware-json', str(fixture),
                                   '--video-models', 'h3,prism', *extra])
        value = json.loads(output.getvalue())
        assert code == 0 and not value['errors'], value['errors']
        return value
    events = []
    class Download:
        fail = False
        def __init__(self, value, ui=None, *, prism=False):
            assert prism, 'Prism files must use the Prism download'
            self.run_dir = root / 'setup-runs' / 'download'
        def run(self):
            assert json.loads(machine.read_text(encoding='utf-8'))['ready'] is True
            events.append('download')
            if Download.fail:
                raise RuntimeError('network stopped')
            return dict(complete=True)
    class Setup:
        attempts = 0
        def __init__(self, value, ui=None):
            Setup.attempts += 1
            assert events == ['download'], 'setup may mark the installation unfinished only after the download'
            if Setup.attempts == 1:
                raise BlockingIOError('a video is generating')  # setup waits for it
            self.run_dir, self.root, self.state = root / 'setup-runs' / 'setup', root, dict(status='running')
            self.monitor_stop, self.monitor_thread, self.started = threading.Event(), None, time.monotonic()
            self.locks = contextlib.ExitStack()
        def start_monitor(self):
            pass
        def execute(self):
            events.append('setup')
    try:
        bootstrap.dependency_status = lambda root, layout='unified', system=None: {
            'unified': dict(python='python', exists=True, installed={}, missing=[], mismatched={})}
        value = planned()
        assert value['prism_download_first'] is True and value['selected_models'] == ['h3', 'prism']
        bootstrap.plan = lambda args, local_progress=None: copy.deepcopy(value)
        bootstrap.Prefetcher, bootstrap.Installer = Download, Setup
        time.sleep = lambda seconds: None
        arguments = ['--root', str(root), '--video-models', 'h3,prism', '--yes', '--accept-model-license', '--plain']
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            Download.fail = True
            assert bootstrap.main(arguments) == 1
            assert events == ['download'] and Setup.attempts == 0 and machine.read_bytes() == before, \
                'A stopped Prism download must leave the ready installation untouched'
            Download.fail, events[:] = False, []
            assert bootstrap.main(arguments + ['--prefetch-only']) == 0
            assert events == ['download'] and Setup.attempts == 0
            events[:] = []
            assert bootstrap.main(arguments) == 0
        assert events == ['download', 'setup'] and Setup.attempts == 2, (events, Setup.attempts)
    finally:
        bootstrap.dependency_status, bootstrap.plan = original['dependency_status'], original['plan']
        bootstrap.Prefetcher, bootstrap.Installer, time.sleep = original['Prefetcher'], original['Installer'], original['sleep']
    # An unfinished installation, or one whose environment must change first, uses the usual order.
    machine.write_text(json.dumps(dict(json.loads(before), ready=False)), encoding='utf-8')
    try:
        bootstrap.dependency_status = lambda root, layout='unified', system=None: {
            'unified': dict(python='python', exists=True, installed={}, missing=[], mismatched={})}
        assert planned()['prism_download_first'] is False
    finally:
        bootstrap.dependency_status = original['dependency_status']
    machine.write_bytes(before)
    assert planned()['prism_download_first'] is False
    # The launcher runs the download (--prefetch-only) while its ComfyUI keeps
    # generating, then setup itself under the same consent, once.
    from types import SimpleNamespace
    from freevideo_engine.comfy_setup import Setup as Service
    class Runner:
        busy = False
        def __init__(self, source, events, logs):
            self.calls = []
        def start(self, action, root, arguments):
            self.calls.append(list(arguments))
    service = Service(Path(folder), SimpleNamespace(base_path=str(Path(folder) / 'ComfyUI')), Runner)
    service.plan, service.selection = value, dict(root=str(root), arguments=['setup', '--video-models', 'h3,prism'])
    service.state = dict(status='complete', action='plan', plan_id='reviewed')
    service.install(dict(plan_id='reviewed', accept_licenses=True, download_first=True))
    download = service.runner.calls[-1]
    assert download[-1] == '--prefetch-only' and '--approved-plan' in download
    try:
        service.install_downloaded()
        raise AssertionError('Setup must wait for the download to complete')
    except ValueError:
        pass
    service.state = dict(status='complete', action='setup')
    service.install_downloaded()
    assert service.runner.calls[-1] == download[:-1]
    try:
        service.install_downloaded()
        raise AssertionError('The consent of one review must not start setup twice')
    except ValueError:
        pass


def check_kept_h3(fixture, folder):
    """Choosing Prism alone on an installation with MiniMax H3 adds Prism and keeps H3."""
    import contextlib
    import io
    from freevideo_engine import bootstrap
    root = Path(folder) / 'with-h3'
    (root / 'cache').mkdir(parents=True)
    machine = dict(ready=True, root=str(root), environment_layout='unified', cache=str(root / 'cache'),
                   base='base', checkpoint='checkpoint', model_paths='paths', encoder='encoder')
    def planned():
        (root / 'machine.json').write_text(json.dumps(machine), encoding='utf-8')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = bootstrap.main(['--root', str(root), '--plan', '--json', '--hardware-json', str(fixture),
                                   '--video-models', 'prism'])
        value = json.loads(output.getvalue())
        assert code == 0 and not value['errors'], value['errors']
        return value
    value = planned()
    assert value['selected_models'] == ['h3', 'prism'] and value['kept_models'] == ['h3'], value['selected_models']
    machine['cache'] = str(root / 'missing')  # H3's files are gone: nothing to keep, nothing downloaded for it
    value = planned()
    assert value['selected_models'] == ['prism'] and value['kept_models'] == []


def check_launcher_model_choice(folder):
    """A launcher.json without the model choice (the official launcher drops it) must not drop Prism."""
    from freevideo_engine.launcher_session import Session
    root = Path(folder) / 'seeded'
    (root / 'models' / 'prism').mkdir(parents=True)
    machine = dict(ready=True, cache='cache', base='base', checkpoint='checkpoint', model_paths='paths', encoder='encoder',
                   models=dict(prism=dict(ready=True, root=str(root / 'models' / 'prism'), addons=['bf16'])))
    (root / 'machine.json').write_text(json.dumps(machine), encoding='utf-8')
    def session(saved_choice, models=('h3',), bf16=False):
        value = Session.__new__(Session)
        value.form = dict(engine=str(root), new_comfy=False, comfy='', destination='',
                          selected_models=list(models), prism_bf16=bf16)
        value.saved_choice, value.models_edited = set(saved_choice), False
        value.seed_models()
        return value.form
    form = session(())
    assert form['selected_models'] == ['h3', 'prism'] and form['prism_bf16'] is True
    # A completed installation decides when launcher.json disagrees with it.
    assert session({'selected_models', 'prism_bf16'})['selected_models'] == ['h3', 'prism']
    # An unfinished setup keeps the choice it was started with.
    (root / 'machine.json').write_text(json.dumps(dict(machine, ready=False)), encoding='utf-8')
    form = session({'selected_models', 'prism_bf16'}, models=('prism',))
    assert form['selected_models'] == ['prism'] and form['prism_bf16'] is False
    assert session(())['selected_models'] == ['h3', 'prism']


def check_setup_choices():
    """Setup and launcher choices around adding Prism, offline, on an RTX 4070 (12 GB) with Windows."""
    import contextlib
    import io
    from freevideo_engine import bootstrap
    with tempfile.TemporaryDirectory(prefix='FreeVideo choices ') as folder:
        fixture = Path(folder) / 'hardware.json'
        uuid = 'GPU-00000000-0000-0000-0000-000000004070'
        hardware = dict(
            hardware=dict(gpu_name='NVIDIA GeForce RTX 4070', capability=[8, 9], vram_total=12 * 2**30,
                          vram_free=11 * 2**30, ram_total=64 * 2**30, ram_available=48 * 2**30, system='Windows',
                          gpu_uuid=uuid, driver_version='581.15'),
            selected_gpu={'index': '0', 'uuid': uuid, 'name': 'NVIDIA GeForce RTX 4070', 'compute_cap': '8.9',
                          'memory.total': '12282', 'memory.free': '11264', 'driver_version': '581.15',
                          'pci.bus_id': '00000000:01:00.0'},
            gpus=[], machine='AMD64', cpu_threads=16,
            system_memory=dict(total_bytes=64 * 2**30, available_bytes=48 * 2**30))
        fixture.write_text(json.dumps(hardware), encoding='utf-8')
        # A GPU without a Prism weight format hears that the computer is the reason.
        hardware['hardware']['capability'] = [7, 5]
        older = Path(folder) / 'hardware-sm75.json'
        older.write_text(json.dumps(hardware), encoding='utf-8')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            bootstrap.main(['--root', str(Path(folder) / 'root'), '--plan', '--hardware-json', str(older),
                            '--video-models', 'prism'])
        assert 'Prism (preview)  Not available on this computer' in output.getvalue(), output.getvalue()
        check_prism_download_first(fixture, folder)
        check_kept_h3(fixture, folder)
        check_launcher_model_choice(folder)


def main():
    sys.meta_path.insert(0, NoModelImports())
    from freevideo_engine import cli, comfy_launcher, comfy_launcher_runtime, modern_launcher
    from freevideo_engine.desktop_runtime import check_launcher_payload, materialize_source, source_files

    spec = importlib.util.spec_from_file_location('freevideo_install_check', ROOT / '__init__.py',
                                                submodule_search_locations=[str(ROOT)])
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    assert callable(entry.comfy_entrypoint)
    assert (ROOT / entry.WEB_DIRECTORY).is_dir()
    assert callable(cli.main) and callable(comfy_launcher.main)
    assert callable(modern_launcher.main)
    assert (ROOT / 'freevideo_engine/launcher/Main.qml').is_file()

    check_update_tracks()
    workflow = json.loads((ROOT / 'example_workflows' / comfy_launcher_runtime.TEMPLATE).read_text(encoding='utf-8'))
    assert {node['type'] for node in workflow['nodes']} == {'FreeVideoMedia', 'FreeVideoGenerate'}

    # The optional Prism preview: MiniMax H3 stays the default, the manifests
    # keep model_files.json's row format and the five levels keep their ids.
    from freevideo_engine import video_models
    assert video_models.parse(None) == ('h3',) and video_models.parse('prism,h3') == ('h3', 'prism')
    assert video_models.from_comfy(None) == 'h3' and video_models.from_comfy('Prism (preview)') == 'prism'
    rows = json.loads((ROOT / 'freevideo_engine/model_files.json').read_text(encoding='utf-8'))
    keys = {'repo', 'revision', 'file', 'bytes', 'sha256', 'git_blob'}
    assert all(keys <= set(row) for row in rows)
    manifest = video_models.prism_manifest()
    assert set(manifest['variants']) == {'int8', 'fp8'}
    for name in manifest['variants']:
        prism_rows = video_models.prism_rows(name, ROOT / 'prism-check')
        assert prism_rows and all(keys <= set(row) and row.get('role') for row in prism_rows)
        assert len({row['file'] for row in prism_rows}) == len(prism_rows)
    assert video_models.prism_variant((8, 6)) == 'int8' and video_models.prism_variant((12, 0)) == 'int8'  # INT8 everywhere
    tiers = video_models.prism_tiers()
    assert [tier['id'] for tier in tiers['tiers']] == ['light', 'standard', 'high', 'max', 'original']
    assert [tier['zh'] for tier in tiers['tiers']] == ['轻量', '标准', '精细', '极致', '原版']
    assert all({'en', 'steps', 'cfg_steps', 'audio_cfg', 'note'} <= set(tier) for tier in tiers['tiers'])
    assert tiers['default'] == 'light' and video_models.prism_tier(steps=20)['id'] == 'max'  # the fastest level is the default
    assert video_models.prism_sampling_plan(video_models.prism_geometry(1280, 720, seconds=8.5),
                                            video_models.prism_tier('light'))['tier'] == 'light'
    # Setup's plan with Prism selected (bootstrap.plan -> video_models.prism_plan),
    # offline, for an RTX 4070 on Windows.
    import contextlib
    import io
    from freevideo_engine import bootstrap
    def setup_plan(folder, vram_gib, ram_gib, name='NVIDIA GeForce RTX 4070'):
        fixture = Path(folder) / ('hardware-%s-%s.json' % (vram_gib, ram_gib))
        uuid = 'GPU-00000000-0000-0000-0000-000000004070'
        fixture.write_text(json.dumps(dict(
            hardware=dict(gpu_name=name, capability=[8, 9], vram_total=int(vram_gib * 2**30),
                          vram_free=int((vram_gib - 1) * 2**30), ram_total=int(ram_gib * 2**30),
                          ram_available=int(ram_gib * 0.75 * 2**30), system='Windows', gpu_uuid=uuid,
                          driver_version='581.15'),
            selected_gpu={'index': '0', 'uuid': uuid, 'name': name, 'compute_cap': '8.9',
                          'memory.total': str(int(vram_gib * 1024)), 'memory.free': str(int((vram_gib - 1) * 1024)),
                          'driver_version': '581.15', 'pci.bus_id': '00000000:01:00.0'},
            gpus=[], machine='AMD64', cpu_threads=16,
            system_memory=dict(total_bytes=int(ram_gib * 2**30), available_bytes=int(ram_gib * 0.75 * 2**30)))),
            encoding='utf-8')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), roomy_disk():
            code = bootstrap.main(['--root', str(Path(folder) / 'root'), '--plan', '--json',
                                   '--hardware-json', str(fixture), '--video-models', 'prism'])
        return code, json.loads(output.getvalue())
    with tempfile.TemporaryDirectory(prefix='FreeVideo setup ') as folder:
        code, plan = setup_plan(folder, 11.99, 64)  # an RTX 4070 (12 GB)
        assert code == 0 and not plan['errors'], plan['errors']
        assert plan['prism']['variant'] == 'int8' and plan['prism']['published']
        # Below Prism's minimum, setup refuses before the 47 GB download.
        for vram_gib, ram_gib, refused in ((8, 64, 'VRAM'), (10, 64, 'VRAM'), (11.99, 16, 'RAM')):
            code, plan = setup_plan(folder, vram_gib, ram_gib)
            assert code == 1 and any('Prism' in error and refused in error for error in plan['errors']), plan['errors']
    assert video_models.prism_geometry(1280, 720, seconds=8.5)['frames'] == 205
    with tempfile.TemporaryDirectory(prefix='FreeVideo install 中文 ') as folder:
        root = Path(folder)
        source = materialize_source(ROOT, root / 'launcher')
        payload = check_launcher_payload(source, root)
        assert {'freevideo_engine/prism_models.json', 'freevideo_engine/prism_tiers.json'} <= set(payload['resources'])
        assert all((source / path.relative_to(ROOT)).read_bytes() == path.read_bytes()
                   for path in source_files(ROOT))
        assert not any((source / name).exists() for name in ('benchmarks', 'docs', 'tests', 'AGENTS.md'))
        for name in ('rewriter.py', 'rewriter_setup.py', 'rewriter_models.json'):
            assert not (source / 'freevideo_engine' / name).exists(), 'Retired feature in package: ' + name
        assert not (source / 'web/rewriter.js').exists(), 'Retired prompt model panel in package'
        from freevideo_engine import support_report
        report = dict(success=False, prompt='PRIVATE PROMPT',
                      error_message='PRIVATE PROMPT', token='PRIVATE TOKEN',
                      resources=dict(ram=dict(process_tree_peak_guard_bytes=123)))
        output = root / 'video.mp4'
        report_file = support_report.write(output, report)
        assert report_file and report_file.name == 'video.debug.json'
        saved = report_file.read_text(encoding='utf-8')
        assert 'PRIVATE' not in saved
        assert json.loads(saved)['memory']['video']['ram']['process_tree_peak_guard_bytes'] == 123
    with roomy_disk():
        check_setup_choices()
    print(json.dumps(dict(success=True, resources=len(payload['resources']),
                          checks=['CLI and launcher imports', 'ComfyUI entry and workflow',
                                  'durable launcher source', 'packaged installation manifests',
                                  'Prism preview manifests', 'launcher update tracks (offline)',
                                  'Prism download before setup (offline)', 'installed MiniMax H3 kept',
                                  'launcher model choice seeded from the installation']), indent=2))


if __name__ == '__main__':
    main()
