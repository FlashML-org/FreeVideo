"""User-facing model families and byte progress, independent of file names."""
import math
import threading

FAMILIES = ('video', 'text', 'decoder', 'sampling', 'prism')
NAMES = {'video': ('Video model', '视频模型'), 'text': ('Text encoder', '文本编码器'),
         'decoder': ('Video & audio decoder', '视频与音频解码器'),
         'sampling': ('Sampling caches', '采样缓存'),
         'prism': ('Prism (preview)', 'Prism（预览）')}
# The groups that belong to MiniMax H3; Prism downloads as one group.
H3_FAMILIES = ('video', 'text', 'decoder', 'sampling')


def family(row):
    if row.get('model') == 'prism':
        return 'prism'
    if row.get('sampling_file'):
        return 'sampling'
    if not row['repo'].startswith('OpenVDN/'):
        return 'text'
    parts = row['file'].replace('\\', '/').split('/')
    return 'decoder' if any(p in ('vae', 'audio_vae', 'vocoder') for p in parts) else 'video'


def inventory(entries):
    """Entries pair a manifest row with download / found / verified placement.

    Found means only size-matched, not integrity-verified. A scanned external
    library has already been hashed; a file at the installation path has not.
    """
    result = {name: dict(id=name, total_bytes=0, existing_bytes=0, verified_bytes=0,
                        download_bytes=0, downloaded_bytes=0, ready_bytes=0, files=0, ready_files=0,
                        state='waiting', bytes_per_second=None) for name in FAMILIES}
    for row, status in entries:
        item, size = result[family(row)], row['bytes']
        item['total_bytes'] += size
        item['files'] += 1
        item['download_bytes' if status == 'download' else 'existing_bytes'] += size
        if status == 'verified':
            item['verified_bytes'] += size
    return [result[name] for name in FAMILIES if result[name]['files']]


class ModelProgress:
    """Keep one counter per manifest file; retries replace, never add counters."""
    def __init__(self, entries, emit):
        self.lock, self.emit = threading.RLock(), emit
        self.files = {(row['repo'], row['file']): dict(row=row, origin=status, phase='waiting', done=0, rate=None)
                      for row, status in entries}

    def update(self, row=None, *, phase=None, done=None, rate=None, local=False):
        with self.lock:
            if row is not None:
                item = self.files[(row['repo'], row['file'])]
                if local:
                    item['origin'] = 'found'
                item['phase'] = phase
                valid = isinstance(done, (int, float)) and math.isfinite(done) and 0 <= done <= row['bytes']
                if done is not None:
                    item['done'] = done if valid else 0
                item['rate'] = rate if valid and isinstance(rate, (int, float)) and math.isfinite(rate) and rate > 0 else None
            elif phase == 'paused':
                for item in self.files.values():
                    if item['phase'] in ('downloading', 'verifying'):
                        item.update(phase='paused', rate=None)
            groups = inventory([(f['row'], f['origin']) for f in self.files.values()])
            for group in groups:
                items = [f for f in self.files.values() if family(f['row']) == group['id']]
                group['downloaded_bytes'] = sum(f['done'] for f in items if f['origin'] == 'download')
                group['ready_bytes'] = sum(f['row']['bytes'] for f in items if f['phase'] == 'ready')
                group['ready_files'] = sum(f['phase'] == 'ready' for f in items)
                group['verified_bytes'] = sum(f['row']['bytes'] for f in items
                                              if f['origin'] != 'download' and f['phase'] == 'ready')
                phases = {f['phase'] for f in items}
                group['state'] = next((s for s in ('paused', 'downloading', 'verifying') if s in phases),
                                      'ready' if phases == {'ready'} else 'pending' if 'ready' in phases else 'waiting')
                group['bytes_per_second'] = next((f['rate'] for f in items if f['phase'] == 'downloading' and f['rate']), None)
            self.emit('model_groups', groups=groups)
