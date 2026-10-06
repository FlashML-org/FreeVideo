"""Move an existing installation to the faster prepared model, then release the old one.

One consent covers the whole move, and each step starts only after the
previous one succeeded:

1. download: setup --prepared-format int8 --prefetch-only, planned and approved
   first, so the download cannot grow beyond what the user was shown. Only the
   setup lease is held, so the installed FP8 model keeps generating.
2. switch: the launcher's normal setup run with --prepared-format int8, which
   the session starts once the queue is idle. int8 kernels must pass on the
   GPU, and machine.json changes only when every step succeeded.
3. release: variant_cleanup removes the retired FP8 files one by one, only once
   the int8 model is in use and complete, retrying while a request holds the
   engine lease. Fused LoRA variants keep the old model whole.

Model files no longer in use - the old model left after the switch (the
launcher closed before the release, or a file was in use), or an int8 download
the GPU then failed to run - are offered for removal, and removed only on
request.
"""
import json
from pathlib import Path
import shutil
import threading
import time

GiB = 1 << 30
MARGIN = 2 * GiB
TARGET = 'int8_convrot'
RETRY_SECONDS = 30


def offer(root):
    """Whether this installation should move to int8 and what that costs, or which
    model files it no longer uses. Reads only."""
    from .hardware import Hardware
    from .kernel_capabilities import readiness
    from .prepared_model import catalog as load_catalog, files, preferred_format, select
    from . import variant_cleanup
    root = Path(root)
    try:
        machine = json.loads((root / 'machine.json').read_text(encoding='utf-8'))
        stamp = kernel_receipt(root)
        receipt = json.loads((root / 'kernel-capabilities.json').read_text(encoding='utf-8'))
        hardware = Hardware.from_dict(receipt['hardware'])
    except (OSError, ValueError, KeyError, TypeError):
        return dict(status='unavailable', reason='installation-unknown')
    if not machine.get('ready') or machine.get('device_backend') == 'mps':
        return dict(status='unavailable', reason='not-applicable')
    catalog = load_catalog()
    if machine.get('prepared_format') == TARGET:
        return unused(root, catalog, TARGET) or dict(status='unavailable', reason='current')
    current, _ = variant_cleanup.active_variant(root, catalog)
    if current is None or current == TARGET:
        # A reused or custom cache outside the prepared folders: never offered.
        return dict(status='unavailable', reason='custom-model')
    # A failed int8 probe on this GPU offers nothing. A receipt without one
    # comes from an engine before int8 (an engine update does not run setup):
    # the move is offered, and setup checks the int8 kernels before it changes
    # anything.
    try:
        rows = receipt.get('kernel_probes') or []
        checked = any(row.get('backend') == 'linear-int8' for row in rows)
        int8_ready = bool(readiness(rows).get('int8_ready'))
    except (KeyError, TypeError, AttributeError):
        checked = int8_ready = False
    reason = ('fp8-is-faster' if preferred_format(hardware) != TARGET
              else 'int8-kernels' if checked and not int8_ready else None)
    if reason:
        # For example an int8 download this GPU then failed to run.
        return unused(root, catalog, current) or dict(status='unavailable', reason=reason, receipt=stamp)
    selection = select(hardware.capability, root, scale_granularity=TARGET)
    directory = Path(selection['directory'])
    rows = files(selection)
    present = 0
    for row in rows:
        path = directory / row['file']
        try:
            if path.is_file() and path.stat().st_size == row['bytes']:
                present += row['bytes']
        except OSError:
            pass
    download = sum(row['bytes'] for row in rows) - present
    fused = variant_cleanup.fused_lora_variants(root, catalog, current)
    release = 0 if fused else variant_cleanup.retire(root, catalog, current)['bytes']
    free = shutil.disk_usage(root).free
    return dict(status='available' if free >= download + MARGIN else 'low-disk',
                architecture=hardware.architecture, gpu=hardware.gpu_name, current=current,
                download_bytes=download, release_bytes=release, free_bytes=free,
                required_bytes=download + MARGIN, lora_variants=fused, int8_checked=checked)


def kernel_receipt(root):
    """Identifies the GPU check receipt as written; it changes when the checks run again."""
    try:
        info = (Path(root) / 'kernel-capabilities.json').stat()
        return [info.st_mtime_ns, info.st_size]
    except OSError:
        return None


def unused(root, catalog, in_use):
    """Prepared variants on disk beside the one in use, removed only when the user asks; or None.

    The same scan and blockers as the release: the variant in use must be
    complete, and fused LoRA variants keep theirs whole.
    """
    from . import variant_cleanup
    plan = variant_cleanup.scan(root, catalog, expect_active=in_use)
    if plan['blockers'] or not plan['bytes']:
        return None
    return dict(status='releasable', in_use=in_use, release_bytes=plan['bytes'],
                variants=[retired['variant'] for retired in plan['retired']],
                lora_variants=[path for kept in plan['kept_variants'] for path in kept['lora_variants']])


def default_runner(source, logs):
    from .comfy_launcher_runtime import LauncherRunner
    from .comfy_setup import Events
    events = Events()
    return LauncherRunner(source, events, logs), events


class ModelUpgrade:
    def __init__(self, source, runner_factory=default_runner):
        self.source = Path(source)
        self.runner_factory = runner_factory
        self.state = dict(status='idle')
        self.thread = None
        self.runner = None
        self.closing = threading.Event()

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def active(self):
        """Downloading or releasing: holding the setup lease or about to remove files."""
        return self.busy and self.state.get('status') in ('downloading', 'releasing')

    def _start(self, work):
        if self.busy:
            return False
        self.thread = threading.Thread(target=work, name='freevideo-model-upgrade')
        self.thread.start()
        return True

    def inspect(self, root):
        def work():
            try:
                self.state = offer(root)
            except Exception as error:
                self.state = dict(status='unavailable', reason='error', error=str(error))
        return self._start(work)

    @staticmethod
    def progress(snapshot, phase):
        """The whole model download, never just the file being fetched at the moment."""
        view = snapshot.get('progress') or {}
        if phase != 'download':
            return {key: view.get(key) for key in ('label', 'done', 'total', 'unit', 'fraction')}
        groups = snapshot.get('model_groups') or []
        total = sum(group.get('download_bytes') or 0 for group in groups)
        if total <= 0:
            # Until the download reports its files, show no numbers rather than one file's.
            return dict(label=view.get('label'), done=None, total=None, unit=None, fraction=None)
        done = min(total, sum(group.get('downloaded_bytes') or 0 for group in groups))
        return dict(label=view.get('label'), done=done, total=total, unit='bytes', fraction=done / total)

    def _run(self, runner, events, action, root, arguments, phase):
        """Run one setup command and follow its progress until it ends."""
        runner.start(action, root, arguments)
        result = None
        while result is None:
            for kind, value in events.drain():
                if kind == 'done':
                    result = value
            self.state = dict(self.state, phase=phase, progress=self.progress(events.snapshot(), phase))
            if result is None:
                time.sleep(.25)
        return result

    def download(self, root, offered):
        """Plan, approve exactly what was offered, then prefetch beside the model in use."""
        from .desktop_runtime import preflight_json
        from .monitoring import save
        root = Path(root)
        def work():
            runner, events = self.runner_factory(self.source, root / 'launcher' / 'runs')
            self.runner = runner
            base = dict(offered, status='downloading')
            self.state = dict(base, phase='plan')
            try:
                planned = self._run(runner, events, 'plan', root,
                                    ['setup', '--prepared-format', 'int8', '--plan', '--json'], 'plan')
                if planned['status'] == 'cancelled':
                    self.state = dict(offered)
                    return
                value = preflight_json(planned.get('output', ''))
                if value.get('errors'):
                    raise ValueError('\n'.join(value['errors']))
                if value.get('prepared_format') != TARGET or not value.get('prepared_model'):
                    raise ValueError('The setup plan does not move this installation to int8.')
                # Nothing beyond what the user agreed to: the approval receipt
                # makes the run refuse a larger download or a changed model.
                if value['model_download_bytes'] > offered['download_bytes'] + 64 * (1 << 20):
                    raise ValueError('The download is larger than shown; check again.')
                receipt = root / 'launcher' / 'model-upgrade-plan.json'
                save(receipt, value)
                fetched = self._run(runner, events, 'setup', root,
                                    ['setup', '--prepared-format', 'int8', '--prefetch-only', '--yes',
                                     '--accept-model-license', '--approved-plan', str(receipt)], 'download')
                if fetched['status'] == 'cancelled':
                    self.state = dict(offered, status='available')
                    return
                if fetched['status'] != 'complete':
                    raise RuntimeError(fetched.get('error') or 'The model download stopped; downloaded files are kept.')
                self.state = dict(self.state, status='downloaded', phase='switch')
            except Exception as error:
                self.state = dict(offered, status='failed', step='download', error=str(error))
            finally:
                self.runner = None
        return self._start(work)

    def cancel(self):
        runner = self.runner
        if runner is not None:
            runner.cancel()

    def waiting_for(self, reason):
        """Downloaded, and the switch waits for this (a ComfyUI the launcher cannot restart)."""
        if self.state.get('status') == 'downloaded' and self.state.get('waiting') != reason:
            self.state = dict(self.state, waiting=reason)

    def switching(self):
        if self.state.get('status') == 'downloaded':
            self.state = dict(self.state, status='switching', waiting=None)

    def switch_failed(self, error, kept):
        """kept: machine.json still describes the previous model, which keeps working."""
        self.state = dict(self.state, status='failed', step='switch', error=error, kept=kept, waiting=None)

    def release(self, root, expect=TARGET):
        """Remove the variants not in use once `expect` is in use and complete, waiting out running requests."""
        from . import variant_cleanup
        root = Path(root)
        def work():
            self.state = dict(self.state, status='releasing', waiting=False)
            while not self.closing.is_set():
                try:
                    plan = variant_cleanup.scan(root, expect_active=expect)
                    if plan['blockers']:
                        # Nothing was removed; the page explains each reason.
                        self.state = dict(self.state, status='failed', step='release', blockers=plan['blockers'],
                                          error='', waiting=False)
                        return
                    receipt = root / 'logs' / ('retired-models-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.json')
                    result = variant_cleanup.clean(plan, receipt=receipt)
                    self.state = dict(self.state, status='complete', released_bytes=result['released_bytes'],
                                      kept_bytes=result['kept_bytes'], skipped=len(result['skipped']),
                                      lora_variants=[path for kept in plan['kept_variants'] for path in kept['lora_variants']],
                                      receipt=str(receipt), waiting=False)
                    return
                except BlockingIOError:
                    # A request or another setup holds a lease; try again after it.
                    self.state = dict(self.state, waiting=True)
                    self.closing.wait(RETRY_SECONDS)
                except Exception as error:
                    self.state = dict(self.state, status='failed', step='release', error=str(error), waiting=False)
                    return
        return self._start(work)

    def close(self):
        self.closing.set()
        self.cancel()
