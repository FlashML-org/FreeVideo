"""Per-child proxy routes; never edit the user's shell or Git configuration."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from urllib import request


PROXY_KEYS = ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
              'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')
MODES = ('auto', 'proxy', 'direct')
GIT_PROXY_PATTERN = r'^(http\..*proxy|https\.proxy|remote\..*\.proxy|core\.gitproxy)$'


def inherited(environ=None):
    from .linux_bundle import child_environment
    env = child_environment(environ)
    # GUI processes on Windows often have no proxy environment variables.
    # Read the user's OS proxy without editing its settings. Explicit child
    # overrides (including NO_PROXY) always take precedence.
    if not any(k in env for k in PROXY_KEYS):
        reader = getattr(request, 'getproxies_registry' if sys.platform == 'win32'
                         else 'getproxies_macosx_sysconf' if sys.platform == 'darwin' else '', None)
        if reader:
            try:
                for name, value in reader().items():
                    if name in ('http', 'https', 'all', 'no') and value:
                        env[name + '_proxy'] = str(value)
            except (OSError, ValueError):
                pass
    for name in ('http', 'https', 'all', 'no'):
        lower, upper = name + '_proxy', name.upper() + '_PROXY'
        if lower not in env and upper in env:
            env[lower] = env[upper]
    return env


def configured(environ=None):
    env = os.environ if environ is None else environ
    return any(v for k, v in env.items() if k.lower() in ('http_proxy', 'https_proxy', 'all_proxy'))


def git_proxy_keys(env):
    """Read names only. URL-scoped keys themselves can contain credentials."""
    if not shutil.which('git', path=env.get('PATH')):
        return []
    try:
        result = subprocess.run(['git', 'config', '--null', '--name-only', '--get-regexp', GIT_PROXY_PATTERN],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
        return list(dict.fromkeys(k for k in result.stdout.decode('utf-8', errors='replace').split('\0')
                                 if k and '\n' not in k and '\r' not in k)) if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def git_environment(environ, settings):
    env = dict(environ)
    index = int(env.get('GIT_CONFIG_COUNT', 0))
    if not 0 <= index <= 1024:
        raise ValueError('Invalid inherited Git configuration count')
    for key, value in settings.items():
        env['GIT_CONFIG_KEY_' + str(index)] = key
        env['GIT_CONFIG_VALUE_' + str(index)] = value
        index += 1
    env['GIT_CONFIG_COUNT'] = str(index)
    return env


def direct(environ=None, *, git=False, keys=None):
    env = inherited(environ)
    for key in list(env):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            env.pop(key)
    # Also suppress OS-discovered HTTP proxies in clients that consult them.
    env.update(NO_PROXY='*', no_proxy='*')
    env['FREEVIDEO_PROXY_ROUTE'] = 'direct'
    # pip can also obtain a proxy from its own configuration file.
    env.pop('PIP_PROXY', None)
    env['PIP_CONFIG_FILE'] = os.devnull
    if git:
        keys = git_proxy_keys(env) if keys is None else keys
        env.pop('GIT_PROXY_COMMAND', None)
        parameters = env.get('GIT_CONFIG_PARAMETERS')
        if parameters:
            # Git propagates -c options as shell-quoted key=value words. Keep
            # unrelated options, remove only proxy settings for this child.
            try:
                values = shlex.split(parameters)
            except ValueError:
                values = []  # Malformed inherited arguments cannot be used by Git.
            values = [v for v in values if not re.match(GIT_PROXY_PATTERN, v.split('=', 1)[0], re.I)]
            env['GIT_CONFIG_PARAMETERS'] = ' '.join(shlex.quote(v) for v in values)
        try:
            valid_count = 0 <= int(env.get('GIT_CONFIG_COUNT', 0)) <= 1024
        except ValueError:
            valid_count = False
        if not valid_count:
            for key in list(env):
                if key == 'GIT_CONFIG_COUNT' or key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')):
                    env.pop(key)
        overrides = {key: '' for key in ['http.proxy', 'https.proxy', 'remote.origin.proxy', *keys]}
        overrides['core.gitproxy'] = 'none'
        env = git_environment(env, overrides)
    return env


def routes(environ=None, *, git=False, mode='auto'):
    if mode not in MODES:
        raise ValueError('Unknown download connection mode')
    if mode == 'proxy':
        return ['inherited']
    if mode == 'direct':
        return ['direct']
    env = inherited(environ)
    return ['inherited', 'direct'] if configured(env) or env.get('PIP_PROXY') or (git and (env.get('GIT_PROXY_COMMAND') or git_proxy_keys(env))) else ['inherited']


def environment(environ, route, *, git=False):
    if route not in ('inherited', 'direct'):
        raise ValueError('Unknown proxy route')
    return direct(environ, git=git) if route == 'direct' else inherited(environ)
