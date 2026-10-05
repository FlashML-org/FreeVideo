"""Import user-downloaded ZIPs. No downloader, model imports or subprocesses."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import zipfile

from .monitoring import save
from .portable import inside

PREFIX = 'FreeVideo-Windows/'
MAX_BYTES = 300 * 2**30


def dependency_id():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in (root / 'dependencies.json', root / 'bootstrap_versions.json',
                 root.parent / 'constraints/windows-portable.txt'):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def inventory(rows):
    seen = set()
    for row in rows:
        name = row['path']
        inside(Path('/unused'), name)
        # Windows strips trailing dots/spaces and recognizes device names.
        for part in name.split('/'):
            if (part.rstrip(' .') != part or part.split('.')[0].upper() in
                    ('CON', 'PRN', 'AUX', 'NUL', *('COM%d' % n for n in range(1, 10)),
                     *('LPT%d' % n for n in range(1, 10)))):
                raise ValueError('Invalid Windows package path: ' + name)
        if (name.casefold() in seen or type(row['bytes']) is not int or row['bytes'] < 0
                or not isinstance(row['sha256'], str) or len(row['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in row['sha256'])):
            raise ValueError('Invalid package inventory: ' + name)
        seen.add(name.casefold())
    if sum(r['bytes'] for r in rows) > MAX_BYTES:
        raise ValueError('Offline package exceeds supported size')


def check_runtime_platform():
    import sys
    if sys.platform == 'darwin':
        raise ValueError('这是 Windows 运行环境包，不能用于 Mac。请导入模型包后继续安装，安装器会自动下载 Mac 运行环境。')


def inspect_archive(path):
    """Read only bounded metadata; file contents are checked during import."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        marker = next((name for name in ('offline-package.json', 'runtime.json')
                       if PREFIX + name in names), None)
        if marker is None:
            raise ValueError('不是 FreeVideo 离线包，请选择公用模型、显卡模型或运行环境 ZIP。')
        info = archive.getinfo(PREFIX + marker)
        if info.file_size > 16 * 2**20:
            raise ValueError('Oversized offline inventory')
        raw = archive.read(info)
        value = json.loads(raw)
        if marker == 'runtime.json':
            check_runtime_platform()
            if value.get('schema_version') != 2 or value.get('dependency_id') != dependency_id():
                raise ValueError('运行环境包版本不匹配，请从下载页面获取配套的运行环境包。')
            value = dict(value, kind='runtime', variant=None)
        elif value.get('schema_version') != 1 or value.get('kind') != 'models' or value.get('variant') not in ('common', 'rowwise', 'per_tensor'):
            raise ValueError('Invalid model package')
        import sys
        if sys.platform == 'darwin' and value.get('variant') == 'per_tensor':
            raise ValueError('Mac 请使用「30/40 系模型包」；「公用模型」包可以通用。此处无需导入 50 系模型包。')
        rows = value['files']
        if not isinstance(rows, list) or not rows:
            raise ValueError('Empty offline package')
        inventory(rows)
        if value['kind'] == 'models' and any(not r['path'].startswith('models/') for r in rows):
            raise ValueError('Model packages may contain only models')
        if value['kind'] == 'runtime' and any(r['path'].startswith('models/') or r['path'].lower().endswith('freevideo.exe') for r in rows):
            raise ValueError('环境包不得包含模型或 FreeVideo.exe')
        permitted = {PREFIX + r['path']: r for r in rows}
        extra = {PREFIX + marker, PREFIX + '模型包使用说明.txt', PREFIX + '运行环境使用说明.txt'}
        seen = set()
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            name = entry.filename
            if name.casefold() in seen or (name not in permitted and name not in extra):
                raise ValueError('Unexpected or duplicate ZIP entry: ' + name)
            seen.add(name.casefold())
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                raise ValueError('Encrypted files and links are not supported')
            if name in permitted and entry.file_size != permitted[name]['bytes']:
                raise ValueError('Incorrect package file size: ' + name)
        if not set(permitted).issubset(names):
            raise ValueError('离线包缺少文件，请重新下载这个 ZIP。')
        return dict(value, marker=marker, raw=raw, id=hashlib.sha256(raw).hexdigest())


def import_archive(path, destination, progress=lambda **kw: None, cancelled=lambda: False):
    from .network import hash_file
    path = Path(path).resolve()
    value = inspect_archive(path)
    root = Path(destination).resolve() / '.freevideo-packages' / value['id'] / 'FreeVideo-Windows'
    total = sum(r['bytes'] for r in value['files'])
    missing = sum(r['bytes'] for r in value['files'] if not inside(root, r['path']).is_file())
    parent = root
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < missing + 64 * 2**20:
        raise ValueError('磁盘空间不足，导入此包还需约 %.1f GiB。' % (missing / 2**30))
    done = 0
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for row in value['files']:
            if cancelled():
                raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
            target = inside(root, row['path'])
            if target.is_file():
                if target.stat().st_size != row['bytes'] or hash_file(target) != row['sha256']:
                    raise ValueError('已有导入文件已改变，未覆盖：' + str(target))
                done += row['bytes']
                progress(done=done, total=total, detail=path.name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + '.importing')
            digest = hashlib.sha256()
            with archive.open(PREFIX + row['path']) as source, partial.open('wb') as output:
                while True:
                    if cancelled():
                        raise InterruptedError('已暂停导入，ZIP 与已校验文件保留。')
                    data = source.read(4 * 2**20)
                    if not data:
                        break
                    output.write(data); digest.update(data); done += len(data)
                    progress(done=done, total=total, detail=path.name)
            if partial.stat().st_size != row['bytes'] or digest.hexdigest() != row['sha256']:
                raise ValueError('离线包校验失败，已保留文件：' + path.name)
            partial.replace(target)
    inside(root, value['marker']).write_bytes(value['raw'])
    return dict(root=str(root), kind=value['kind'], variant=value['variant'], name=path.name)


def assemble(runtime, models, source):
    """Bind imported components to the current EXE's engine, without running it."""
    from .desktop_runtime import materialize_source
    check_runtime_platform()
    runtime = Path(runtime)
    config = json.loads((runtime / 'runtime.json').read_text(encoding='utf-8'))
    if config.get('schema_version') != 2 or config.get('dependency_id') != dependency_id():
        raise ValueError('运行环境包与安装器不匹配')
    candidates = []
    for root in models:
        path = Path(root) / 'models/model-pack.json'
        if path.is_file():
            candidates.append((Path(root), json.loads(path.read_text(encoding='utf-8'))))
    variants = {value['variant'] for _, value in candidates}
    if not candidates:
        raise ValueError('还需导入公用模型包和对应显卡的模型包。')
    if len(variants) != 1:
        raise ValueError('请只导入本机对应的一个显卡模型包；无需同时导入两种。')
    model = candidates[0][1]
    inventory(model['files'])
    if any(not r['path'].startswith('models/') for r in model['files']):
        raise ValueError('Invalid model inventory')
    from .network import hash_file
    for row in model['files']:
        target = inside(runtime, row['path'])
        sources = [inside(Path(p), row['path']) for p in models]
        src = next((p for p in sources if p.is_file() and p.stat().st_size == row['bytes']
                    and hash_file(p) == row['sha256']), None)
        if src is None:
            raise ValueError('还缺少公用模型或显卡模型包中的文件：' + row['path'])
        if target.exists():
            if hash_file(target) != row['sha256']:
                raise ValueError('离线安装已有不同模型，未覆盖：' + str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, target)
            except OSError:
                shutil.copyfile(src, target)
    code = materialize_source(source, runtime / 'engine/launcher/source')
    variant = model['variant']
    value = dict(config['configuration'], schema_version=1, variant=variant,
        source=str(code.relative_to(runtime).as_posix()),
        source_commit=code.name, cache='models/edge/' + ('rowwise/cache' if variant == 'rowwise' else 'cache'),
        files=config['files'] + model['files'])
    inventory(value['files'])
    save(runtime / 'portable.json', value)
    return runtime


class Importer:
    def __init__(self):
        self.thread = None
        self.cancelled = threading.Event()
        self.state = dict(status='idle', done=0, total=None, detail='', packages=[])

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def prepare(self, runtime, models, source):
        if self.busy:
            return
        self.state = dict(status='preparing', done=0, total=None, detail='检查配套包', packages=[])
        def work():
            try:
                root = assemble(runtime, models, source)
                self.state = dict(self.state, status='prepared', ready_root=str(root))
            except Exception as error:
                self.state = dict(self.state, status='error', error=str(error))
        self.thread = threading.Thread(target=work, name='freevideo-offline-prepare', daemon=True)
        self.thread.start()

    def start(self, paths, destination):
        if self.busy:
            return
        self.cancelled.clear()
        self.state = dict(status='running', done=0, total=None, detail='', packages=[])
        def update(**row):
            self.state = dict(self.state, **row)
        def work():
            try:
                packages = []
                for path in paths:
                    packages.append(import_archive(path, destination, update, self.cancelled.is_set))
                    update(packages=list(packages))
                update(status='complete')
            except Exception as error:
                update(status='error', error=str(error))
        self.thread = threading.Thread(target=work, name='freevideo-package-import', daemon=True)
        self.thread.start()
