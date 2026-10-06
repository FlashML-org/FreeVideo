"""Pinned prebuilt model selection and access checks, without Torch or Hub SDKs.

The catalog is shipped with the engine, never learned from an unverified remote
manifest. Each CUDA architecture keeps its existing FP8 quantization granularity;
Macs use the ConvRot int8 export (int8 products on Apple M5 and newer).
"""
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from . import network

CATALOG = Path(__file__).with_name('prepared_models.json')


def catalog():
    value = json.loads(CATALOG.read_text(encoding='utf-8'))
    if (value.get('schema_version') != 1 or
            not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value.get('repo', '')) or
            not re.fullmatch('[0-9a-f]{40}', value.get('revision', ''))):
        raise ValueError('Invalid pinned prepared model catalog')
    for variant in value['variants'].values():
        if not re.fullmatch('[0-9a-f]{40}', variant.get('revision', value['revision'])):
            raise ValueError('Invalid prepared variant revision')
        names = set()
        for row in variant['files']:
            name = row.get('file', '')
            if (not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or
                    any(p in ('', '.', '..') for p in name.split('/')) or name in names or
                    type(row.get('bytes')) is not int or row['bytes'] <= 0 or
                    not re.fullmatch('[0-9a-f]{64}', row.get('sha256', ''))):
                raise ValueError('Invalid prepared model file record')
            names.add(name)
        prefix = variant['cache_prefix']
        if prefix + '/manifest.json' not in names:
            raise ValueError('Prepared variant is missing its pinned cache manifest')
    return value


def preferred_format(hardware):
    """The prepared format a fresh installation should use on this GPU.

    int8 where it is the faster arithmetic: GeForce Ada and Blackwell cards run
    FP8 with FP32 accumulation at half rate but int8 at full rate, and Ampere
    has no FP8 tensor cores at all. Workstation and datacenter Ada/Blackwell
    cards run FP8 at full rate and keep it (int8 measured 5% slower on an RTX
    PRO 6000). Returns None to keep the GPU's FP8 granularity.
    """
    architecture = hardware.architecture
    if architecture == 'ampere':
        return 'int8_convrot'
    if architecture in ('ada', 'blackwell-rtx') and 'geforce' in (hardware.gpu_name or '').lower():
        return 'int8_convrot'
    return None


def select(capability, root, *, scale_granularity=None):
    value = catalog()
    scale = scale_granularity or ('per_tensor' if capability[0] >= 10 else 'rowwise')
    if scale not in ('rowwise', 'per_tensor', 'int8_convrot'):
        raise ValueError('Unknown prepared model scale format')
    variant = value['variants'].get(scale)
    if variant is None:
        return None  # Never change quantization to make an artifact fit a GPU.
    # Adding another format must not move an existing installation to a new
    # directory or redownload its unchanged model. Variants may pin separately.
    revision = variant.get('revision', value['revision'])
    folder = Path(root).expanduser().resolve() / 'prepared' / ('edge-' + revision[:16])
    return dict(repo=value['repo'], revision=revision, scale_granularity=scale,
                directory=str(folder), cache=str(folder / variant['cache_prefix']),
                private=value.get('private', False), format=value['format'])


def files(selection):
    if not selection:
        return []
    value = catalog()
    if any(selection.get(k) != value[k] for k in ('repo', 'format')):
        raise ValueError('Prepared model selection changed; review setup again')
    variant = value['variants'].get(selection['scale_granularity'])
    if variant is None or Path(selection['cache']).resolve() != (Path(selection['directory']) / variant['cache_prefix']).resolve():
        raise ValueError('Invalid prepared model cache path')
    revision = variant.get('revision', value['revision'])
    if selection.get('revision') != revision:
        raise ValueError('Prepared model selection changed; review setup again')
    return [dict(row, repo=value['repo'], revision=revision, prepared=True,
                 official_only=bool(value.get('private'))) for row in variant['files']]


def token(environ=None):
    """Read the same ordinary login/env token locations as HF, never serialize it."""
    env = os.environ if environ is None else environ
    direct = env.get('HF_TOKEN') or env.get('HUGGING_FACE_HUB_TOKEN')
    if direct:
        return direct.strip()
    try:
        location = env.get('HF_TOKEN_PATH')
        if not location:
            home = env.get('HF_HOME')
            if not home:
                home = Path(env.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'huggingface'
            location = Path(home) / 'token'
        path = Path(location).expanduser()
        with path.open('r', encoding='utf-8') as stream:
            value = stream.read(4097).strip()
        return value if len(value) <= 4096 else None
    except (OSError, RuntimeError):
        return None


def access_error(selection, networking, *, environ=None):
    """Bounded authenticated HEAD before large dependency/model installation.

    Public mirrors are tried in their measured order. Only the official Hub
    gets credentials; proxy/direct retries retain the existing policy.
    """
    env = os.environ if environ is None else environ
    secret = token(env)
    if selection.get('private') and not secret:
        return ('The prepared model repository is private: ' + selection['repo'] +
                '. Set HF_TOKEN for an account with access, or run hf auth login, then retry. '
                'Use --model-source source only if you want the larger original-model conversion.')
    manifest = next(r for r in files(selection) if r['file'].endswith('/manifest.json'))
    family = network.model_family(manifest)
    for source, url in network.model_urls(networking, manifest, env):
        headers = ['Authorization: Bearer ' + secret] if secret and source == 'official' else []
        for route in network.route_order(networking, family, source, env):
            try:
                result = subprocess.run([
                    'curl', '--disable', '--silent', '--show-error', '--location', '--head',
                    '--output', os.devnull, '--write-out', '%{http_code}', '--connect-timeout', '5',
                    '--max-time', '12', '--config', '-'],
                    input=network.curl_config(url, headers), capture_output=True, timeout=15,
                    env=network.route_environment(networking, family, source, env, route))
            except (OSError, subprocess.TimeoutExpired):
                continue
            status = result.stdout.decode('ascii', errors='ignore').strip()
            if result.returncode == 0 and status == '200':
                network.route_health(networking, family, source, route)
                return None
            if selection.get('private') and status in ('401', '403', '404'):
                return ('Cannot access the pinned prepared model in ' + selection['repo'] +
                        '. Check your Hugging Face token and repository access; no large downloads started.')
    return ('Could not reach the pinned prepared model sources. Check the proxy/network and retry; '
            'existing files are retained. No automatic switch to the much larger source model.')


def verify_manifest(selection):
    from .adaln_assets import file_hash, asset_path
    row = next(r for r in files(selection) if r['file'].endswith('/manifest.json'))
    path = asset_path(selection['directory'], row['file'])
    if not path.is_file() or path.stat().st_size != row['bytes'] or file_hash(path) != row['sha256']:
        raise ValueError('Prepared model manifest failed its published checksum; files retained')
