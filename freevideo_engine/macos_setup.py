"""Native package/source steps inside the shared installer lifecycle."""
import json

from .bootstrap import Installer as SharedInstaller, SOURCE
from . import macos_bootstrap as native
from .monitoring import save


class Installer(SharedInstaller):
    def __init__(self, value, ui=None):
        native.require_native()
        super().__init__(value, ui)

    def device_environment(self, env):
        env = native.environment(self.root, env)
        links = native.probe_links(self.root)
        if links == 'blocked':
            raise RuntimeError(native.LOCATION_UNSUPPORTED)
        if links == 'copy':
            env['UV_LINK_MODE'] = 'copy'
        return env

    def device_configuration(self):
        return dict(device_backend='mps', device_identity=self.plan['inventory']['selected_device'])

    def prepare_tools(self):
        return native.prepare_uv(self.root, self.network, self.env, self.fetch)

    def install_git(self):
        """The system Git when it runs; otherwise Git from conda-forge in FreeVideo's own folder."""
        git = native.available_git()
        if git:
            return
        try:
            self.install_git_conda('git_macos.json', 'git-macos',
                                   lambda spec: self.fetch_conda_lock(spec, 'git-macos', 'Git'), wrapper=True)
        except Exception as error:
            if self.cancel.is_set():
                raise
            raise RuntimeError(native.GIT_UNAVAILABLE) from error

    def component_tasks(self, uv):
        tasks = {}
        def task(name, run, after=(), writes=()):
            tasks[name] = dict(run=run, after=tuple(after), writes=tuple(writes))
        python = self.pythons['engine']
        writer = ('environment:unified',)
        task('python', lambda: self.python_install(uv))
        task('git', self.install_git)
        task('unified-venv', lambda: None if python.is_file() else
             self.command('unified-venv', [uv, 'venv', '--python', native.PYTHON_VERSION, self.root / 'envs/unified']),
             ('python',), writer)
        task('runtime', lambda: self.packages('unified-packages', [uv, 'pip', 'install', '--python', python,
            '-r', SOURCE / 'constraints/macos-runtime-requirements.txt', '-c', native.constraints()]),
            ('unified-venv',), writer)
        task('engine', lambda: self.packages('unified-engine', [uv, 'pip', 'install', '--python', python,
            '--no-deps', '-e', SOURCE]), ('runtime',), writer)
        task('vdn-source', lambda: self.command('vdn-source', [python, '-c',
            'from freevideo_engine.install import install; install()']), ('unified-venv', 'git'))
        task('vdn-install', lambda: self.packages('vdn', [uv, 'pip', 'install', '--python', python,
            '--no-deps', '-e', self.root / 'vendor/vdn' / self.spec['vdn']['diffusers_subdirectory']]),
            ('engine', 'vdn-source'), writer)
        task('encoder-source', lambda: self.clone('h3-text-encoder',
            self.spec['encoder']['comfy_url'], self.spec['encoder']['comfy_commit']), ('git',))
        task('models', lambda: self.command('models', [python, '-m', 'freevideo_engine.provision',
            '--plan', self.run_dir / 'plan.json']), ('engine',))
        return tasks, {'unified': python}, self.root / 'vendor/h3-text-encoder'

    def check_kernels(self, python, kernel_report):
        # A cheap native execution check is repeated on repair. Do not reuse
        # a CUDA receipt or claim a full model benchmark from this probe.
        self.command('kernels', [python, '-m', 'freevideo_engine.macos_doctor',
            '--probe', '--require-paths', '--out', kernel_report])
        result = json.loads(kernel_report.read_text(encoding='utf-8'))
        if not result.get('ready') or result.get('device_backend') != 'mps':
            raise RuntimeError('Native GPU checks did not complete; see the retained report')
        save(self.root / 'kernel-capabilities.json', result)

    def record_kernel_validation(self, results, kernel_report):
        pass  # Native checks have no optional CUDA package build receipt.
