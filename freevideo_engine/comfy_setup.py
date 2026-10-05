"""Reuse Comfy's registered libraries and provision a separate engine environment.

Import/GET discovery is read-only. Only explicit inspect/install actions start
children. The existing installer owns content checks, consent, download recovery,
resource locks and readiness; this module does not run pip in ComfyUI.
"""
from collections import deque
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

from .comfy_bridge import installation, installation_root, source_root
from .environments import ENVIRONMENTS
from .desktop_runtime import Runner, preflight_json
from .comfy_environment import isolated_environment
from .local_models import library_roots
from .monitoring import save
from .setup_progress import ProgressState


def discover_libraries(folder_paths, environ=None):
    env = os.environ if environ is None else environ
    candidates = [str(folder_paths.models_dir)]
    # folder_paths already incorporates extra_model_paths.yaml, including paths
    # registered by other nodes. No second, subtly different YAML parser.
    for name in ('diffusion_models', 'unet', 'checkpoints', 'text_encoders', 'clip', 'vae'):
        try:
            candidates.extend(folder_paths.get_folder_paths(name))
        except KeyError:
            pass
    cache = env.get('HF_HUB_CACHE') or env.get('HUGGINGFACE_HUB_CACHE')
    if not cache and env.get('HF_HOME'):
        cache = str(Path(env['HF_HOME']) / 'hub')
    modelscope = env.get('MODELSCOPE_CACHE')
    if not cache or not modelscope:
        # Optional cache discovery must also work in portable/service Windows
        # environments without USERPROFILE. Evaluate defaults only if needed.
        try:
            home_cache = Path.home() / '.cache'
        except (OSError, RuntimeError):
            pass
        else:
            cache = cache or str(home_cache / 'huggingface/hub')
            modelscope = modelscope or str(home_cache / 'modelscope/hub')
    candidates.extend(value for value in (cache, modelscope) if value)
    existing = []
    for value in candidates:
        try:
            path = Path(value).expanduser()
            if path.is_dir():
                existing.append(str(path))
        except (OSError, RuntimeError):
            pass
    return library_roots(existing)


# The calibration encodes this once and every variant reads it, so the
# comparison excludes text encoding and both halves see one input.
CALIBRATION_PROMPT = ('A neon-lit alley after heavy rain. The camera drifts forward past '
                      'reflected signage while distant thunder rolls and footsteps echo '
                      'on wet stone.')


class SetupRunner(Runner):
    token = ''

    def command(self, root, arguments):
        if os.name == 'nt':
            return super().command(root, arguments)
        if sys.platform == 'darwin':
            if getattr(sys, 'frozen', False):
                return [sys.executable, '--managed', str(self.source), '--root', str(root), *arguments]
            return [sys.executable, '-B', '-X', 'utf8', '-m', 'freevideo_engine.managed',
                    '--root', str(root), *arguments]
        return ['/bin/bash', str(self.source / 'freevideo'), '--root', str(root), *arguments]

    def environment(self, root):
        from .hf_auth import environment
        return environment(self.token, isolated_environment(root, self.source))


class Events:
    """A closed browser must not block a worker or grow a queue indefinitely."""
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = deque(maxlen=128)
        self.progress = ProgressState()

    def put(self, row):
        with self.lock:
            if row[0] == 'progress':
                self.progress.update(row[1])
            self.rows.append(row)

    def reset(self):
        with self.lock:
            self.rows.clear()
            self.progress = ProgressState()

    def snapshot(self):
        with self.lock:
            return self.progress.snapshot()

    def drain(self):
        with self.lock:
            rows = list(self.rows)
            self.rows.clear()
        return rows


class Setup:
    def __init__(self, source, folder_paths, runner_factory=SetupRunner):
        self.source = Path(source).resolve()
        self.folder_paths = folder_paths
        self.events = Events()
        self.logs = self.source / '.freevideo' / 'comfy-setup'
        self.runner = runner_factory(self.source, self.events, self.logs)
        self.state = {'status': 'idle'}
        self.plan = self.selection = None
        self.token = secrets.token_urlsafe(32)
        from .download_settings import Probe
        self.download_probe = Probe()

    def discovery(self, download_root=None):
        root = Path(self.selection['root']) if self.runner.busy and self.selection else installation_root(self.source)
        ready, detail = False, ''
        try:
            installation(self.source)
            ready = True
        except (OSError, ValueError) as error:
            detail = str(error)
        return dict(root=str(root), ready=ready, detail=detail,
                    libraries=discover_libraries(self.folder_paths), token=self.token,
                    environment='Managed FreeVideo environment; ComfyUI packages are kept',
                    restart_required=False, downloads=self.downloads(download_root or root),
                    resources=self.resource_settings())


    def resource_settings(self, value=None):
        from .resource_settings import read, write, setting
        root = installation_root(self.source)
        backend = 'mps' if sys.platform == 'darwin' else 'cuda'
        field, _, label = setting(backend)
        if value is None:
            return read(root, backend=backend)
        if field not in value:
            raise ValueError('Specify automatic (null) or ' + label + ' in GiB')
        return write(root, value[field], backend=backend)

    def downloads(self, root=None):
        from .download_settings import read
        return dict(preferences=read(Path(root or installation_root(self.source))/'download-settings.json'),
                    probe=self.download_probe.snapshot(), hf_token_set=bool(getattr(self.runner, 'token', '')))

    def download_token(self, value):
        from .hf_auth import validate
        if self.runner.busy:
            raise ValueError('Stop setup before changing the token, then resume with retained downloads.')
        self.runner.token = validate(value.get('hf_token', ''))
        self.plan = None
        self.state.pop('plan_id', None)
        self.state.pop('plan', None)
        return dict(hf_token_set=bool(self.runner.token))

    def network_settings(self, value, *, probe=False):
        root=self.validate_root(value.get('root'))
        if self.runner.busy and self.selection and root!=Path(self.selection['root']):
            raise ValueError('Choose the active installation folder to switch its download')
        if probe:
            self.download_probe.start(root, getattr(self.runner, 'token', ''))
        else:
            self.download_probe.select(root,value.get('source'),proxy_mode=value.get('proxy_mode'))
        return self.downloads(root)

    def compatibility_settings(self, value, action):
        from .compatibility import Store, installed_identity, check_installation
        root = self.validate_root(value.get('root') or str(installation_root(self.source)))
        identity = installed_identity(root)
        store = Store(root)
        if action == 'compatibility-check':
            result = check_installation(root)
        elif action == 'compatibility-save':
            result = store.set(identity, value.get('level'), value.get('automatic'))
        elif action == 'compatibility-ack':
            result = store.acknowledge(identity, value.get('notice_id'))
        else:
            raise ValueError('Unknown compatibility action')
        return result

    def validate_root(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError('Choose an engine installation folder')
        root = Path(value).expanduser().resolve()
        host = Path(self.folder_paths.base_path).resolve()
        if root == Path(root.anchor) or root == host or root in host.parents:
            raise ValueError('Use a dedicated FreeVideo folder, not the ComfyUI root or its parent')
        prefix = Path(sys.prefix).resolve()
        # Refuse to pip into the interpreter that is running, and nothing more.
        # The earlier test asked whether the live interpreter lived anywhere
        # under `root/envs`, which is true of every working installation the
        # product builds: comfy_host puts ComfyUI in `root/envs/comfyui-<key>`
        # beside the engine's own env. So updating an installed FreeVideo from
        # inside its own ComfyUI was refused as "the active environment", with
        # nothing wrong and nothing to change. Only the engine's own
        # environment names are its install target; a ComfyUI sibling is not.
        engine_envs = {(root / 'envs' / name).resolve() for name in ENVIRONMENTS}
        if root == prefix or prefix in root.parents or prefix in engine_envs:
            raise ValueError('FreeVideo cannot install packages into the environment it is '
                             'running in. Launch ComfyUI from its own environment, or run '
                             'setup from the FreeVideo launcher instead of this panel.')
        selected = os.environ.get('FREEVIDEO_HOME')
        if selected and installation_root(self.source) != root:
            raise ValueError('FREEVIDEO_HOME selects another installation. Change it before starting ComfyUI.')
        return root

    def inspect(self, value):
        self.status()
        if self.runner.busy:
            raise ValueError('FreeVideo setup is already running')
        root = self.validate_root(value.get('root'))
        extra = value.get('extra_libraries', [])
        if not isinstance(extra, list) or not isinstance(value.get('copy', False), bool):
            raise ValueError('Invalid setup choices')
        libraries = library_roots(discover_libraries(self.folder_paths) + extra)
        self.logs.mkdir(parents=True, exist_ok=True)
        manifest = self.logs / ('libraries-' + uuid.uuid4().hex + '.json')
        save(manifest, dict(version=1, roots=libraries))
        arguments = ['setup', '--reuse-models-manifest', str(manifest)]
        frontend = value.get('frontend')
        if frontend:
            arguments += ['--frontend-root', str(Path(frontend['root']).expanduser().resolve())]
            if frontend.get('separate'):
                arguments.append('--frontend-separate')
            if frontend.get('download'):
                arguments.append('--frontend-download')
        if value.get('copy'):
            arguments.append('--copy-existing-models')
        self.selection = dict(root=str(root), arguments=arguments)
        self.plan = None
        self.state = dict(status='running', action='plan', libraries=libraries)
        self.events.reset()
        self.runner.start('plan', root, [*arguments, '--plan', '--json'])
        return self.status()

    def bind(self, root):
        root = self.validate_root(root)
        installation(self.source, dict(os.environ, FREEVIDEO_HOME=str(root)))
        config = self.source / 'comfyui.json'
        value = json.loads(config.read_text(encoding='utf-8')) if config.is_file() else {}
        value['installation'] = str(root)
        save(config, value)

    def install(self, value):
        self.status()
        if self.runner.busy or not self.plan or value.get('plan_id') != self.state.get('plan_id'):
            raise ValueError('Inspect this installation again before installing')
        if value.get('accept_licenses') is not True or self.plan.get('errors'):
            raise ValueError('Review the plan and accept its model/toolkit licenses first')
        # Inputs are held server-side; the browser cannot swap paths after consent.
        receipt = self.logs / ('approved-' + uuid.uuid4().hex + '.json')
        save(receipt, self.plan)
        selection = self.selection
        self.state = dict(status='running', action='setup')
        self.events.reset()
        self.plan = None  # Consent cannot be replayed, even if a worker fails.
        self.runner.start('setup', selection['root'], [*selection['arguments'], '--plain',
            '--yes', '--accept-model-license', '--approved-plan', str(receipt)])
        return self.status()

    def calibrate(self, value):
        """Start the placement sweep on the same worker the installer uses.

        It is a measurement, not a change: nothing it produces alters this
        installation's placement. It can run for hours, so it goes to the
        background runner and reports through the same status the panel already
        polls, and it refuses to start beside queued generation work rather
        than competing with it for the card.
        """
        state = self.status()
        # Order matters: a machine with no installation cannot calibrate no
        # matter how idle the worker is, and being told to wait for a task it
        # never started sends it looking for the wrong thing.
        root = installation_root(self.source)
        if not root or not Path(root).is_dir():
            raise ValueError('Install FreeVideo first. The placement test measures an '
                             'installed engine; it cannot run before setup finishes.')
        if self.runner.busy:
            # Name what is running. The panel shows its progress, and a user
            # who reads only this message otherwise has nothing to wait for.
            running = {'plan': 'the installation check', 'setup': 'the installation',
                       'calibrate': 'a placement test'}.get(
                           state.get('action'), state.get('action') or 'a setup task')
            raise ValueError('Wait for %s to finish; its progress is in this panel.' % running)
        repeats = value.get('repeats', 1)
        if type(repeats) is not int or not 1 <= repeats <= 4:
            raise ValueError('Repeats must be a whole number from 1 to 4')
        prompt = str(value.get('prompt') or CALIBRATION_PROMPT).strip()
        if not prompt:
            raise ValueError('Enter a prompt for the calibration to encode')
        run = self.logs / ('calibration-' + uuid.uuid4().hex)
        run.mkdir(parents=True, exist_ok=True)
        (run / 'prompt.txt').write_text(prompt, encoding='utf-8')
        arguments = ['calibrate', '--prompt-file', str(run / 'prompt.txt'),
                     '--out', str(run / 'report'), '--repeats', str(repeats)]
        # Hand the child a free card and a free lease, exactly as generation
        # does. The resident worker holds the lease while it works and the idle
        # prewarmer takes it between requests, and ComfyUI keeps its own models
        # resident: releasing only the first two left a 16 GiB RTX 5060 Ti with
        # ComfyUI's weights still on the card, and the text encoder died in
        # dequantize_int8_embedding with CUDA out of memory before any
        # measurement ran. The same prompt encodes in two and a half minutes on
        # a card that size when it is actually free. Best effort and in this
        # order: the workers exit first, so emptying the cache afterwards
        # returns what they held.
        released = []
        for name, release in (('idle_prewarm', self._stop_idle_prewarm),
                              ('resident_worker', self._close_resident_worker),
                              ('comfy_models', self._release_comfy_models)):
            try:
                release()
                released.append(name)
            except Exception as error:
                released.append('%s failed: %s' % (name, type(error).__name__))
        self.state = dict(status='running', action='calibrate', run=str(run),
                          released=released)
        self.events.reset()
        self.runner.start('calibrate', str(root), arguments)
        return self.status()

    def _stop_idle_prewarm(self):
        from .encoder_prewarm import IDLE
        IDLE.stop()

    def _close_resident_worker(self):
        from .resident_process import OWNER
        OWNER.close()

    def _release_comfy_models(self):
        # Not keep_engine_cache(): that exists so a generation handoff keeps
        # the resident worker alive, and this one has already closed it.
        from comfy import model_management as memory
        memory.unload_all_models()
        memory.soft_empty_cache()

    def status(self):
        for kind, value in self.events.drain():
            if kind == 'started':
                self.state['log'] = value
            elif kind == 'progress':
                self.state['progress'] = value
            elif kind == 'log':
                self.state['tail'] = (self.state.get('tail', '') + value)[-8192:]
            elif kind == 'done':
                self.state.update({key: value[key] for key in ('status', 'action', 'error', 'wall_seconds', 'log')})
                if value['status'] == 'failed' and not value.get('error'):
                    # Older/bootstrap failures may not emit a structured error.
                    # Recover the complete retained output once, after exit.
                    try:
                        self.state['error'] = (Path(value['log']) / 'launcher.log').read_text(encoding='utf-8', errors='replace')
                    except OSError:
                        self.state['error'] = value.get('output') or self.state.get('tail', '')
                if value['action'] == 'plan' and value['status'] in ('complete', 'failed'):
                    try:
                        self.plan = preflight_json(value['output'])
                        self.state.update(plan=self.plan, plan_id=uuid.uuid4().hex)
                    except ValueError:
                        self.state.update(status='failed', error='Setup inspection failed\n' +
                                          (self.state.get('error') or value.get('output', '')))
                elif value['action'] == 'setup' and value['status'] == 'complete':
                    try:
                        self.bind(value['root'])
                        self.state['ready'] = True
                    except (OSError, ValueError) as error:
                        self.state.update(status='failed', error=str(error))
        return dict(self.state, **self.events.snapshot(), busy=self.runner.busy)


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_setup', None) is not None:
        return
    setup = Setup(source_root(), folder_paths)
    server._freevideo_setup = setup

    @server.routes.get('/freevideo/setup')
    async def info(request):
        try:
            root=setup.validate_root(request.query['root']) if request.query.get('root') else None
            return web.json_response(dict(discovery=setup.discovery(root), task=setup.status()))
        except (OSError, ValueError) as error:
            return web.json_response({'error': str(error)}, status=400)

    @server.routes.post('/freevideo/setup/{action}')
    async def action(request):
        # Same-origin clients must first read the per-process token. Blind
        # cross-site forms cannot trigger filesystem changes or installations.
        if not secrets.compare_digest(request.headers.get('X-FreeVideo-Setup', ''), setup.token):
            return web.json_response({'error': 'Reload the setup panel'}, status=403)
        try:
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError('Invalid setup request')
            name = request.match_info['action']
            if name == 'inspect':
                result = setup.inspect(value)
            elif name in ('network-probe','network-source'):
                result = setup.network_settings(value,probe=name=='network-probe')
            elif name == 'network-token':
                result = setup.download_token(value)
            elif name == 'resources':
                result = setup.resource_settings(value)
            elif name == 'calibrate':
                running, pending = server.prompt_queue.get_current_queue()
                if running or pending:
                    raise ValueError('Finish or cancel queued ComfyUI requests before calibrating')
                result = setup.calibrate(value)
            elif name in ('compatibility-check', 'compatibility-save', 'compatibility-ack'):
                result = setup.compatibility_settings(value, name)
            elif name == 'install':
                running, pending = server.prompt_queue.get_current_queue()
                if running or pending:
                    raise ValueError('Finish or cancel queued ComfyUI requests before installing')
                result = setup.install(value)
            elif name == 'use':
                if setup.runner.busy:
                    raise ValueError('Wait for setup to finish')
                setup.bind(value.get('root', ''))
                result = setup.discovery()
            elif name == 'cancel':
                setup.runner.cancel()
                result = setup.status()
            else:
                raise ValueError('Unknown setup action')
            return web.json_response(result)
        except (OSError, ValueError, RuntimeError) as error:
            return web.json_response({'error': str(error)}, status=400)
