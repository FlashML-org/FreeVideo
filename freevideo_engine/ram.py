"""Observed process memory and dedicated cgroup limits; no GPU dependencies."""
from pathlib import Path
import sys
from .system import windows


def inference_working_pss(rollup, protected_file_bytes=None):
    """Keep anonymous/shared/dirty pages; clean file pages may be reclaimed.

    The raw PSS remains the reported footprint; cgroups enforce capacity.
    Dirty/writeback/unevictable filesystem bytes must be bounded separately:
    Pss_Dirty also includes anonymous pages, so max(Pss-Pss_File, Pss_Dirty)
    alone could discard dirty file pages. Missing counters earn no credit.
    """
    total = rollup['Pss']
    file_pages, dirty = rollup.get('Pss_File'), rollup.get('Pss_Dirty')
    if (any(type(value) is not int or not 0 <= value <= total for value in (file_pages, dirty))
            or type(protected_file_bytes) is not int or protected_file_bytes < 0):
        return total
    return max(total - max(0, file_pages - protected_file_bytes), dirty)


class ProcessMemory:
    """Sample worker/descendant RSS and PSS without pretending to enforce RAM.

    PSS apportions shared resident mappings. Neither RSS nor PSS includes the
    worker's unmapped file cache. MemAvailable is system-wide, not attribution.
    """

    def __init__(self):
        self.macos = None
        if sys.platform == 'darwin':
            from .macos_process_memory import ProcessMemory as MacMemory
            self.macos = MacMemory()
        self.windows = None
        if windows():
            from .win32 import ProcessMemory as WindowsMemory
            self.windows = WindowsMemory()
        self.peak_rss = self.peak_pss = self.samples = 0
        self.peak_inference_pss = 0
        self.minimum_available = None
        self.minimum_effective_available = None
        self.minimum_inference_available = None
        self.last_cgroup = None
        self.pss_complete = True
        self.process_io = {}
        self.peak_tree_read = self.peak_tree_write = 0

    @staticmethod
    def kilobytes(path):
        return {key: int(value.split()[0]) * 1024 for key, value in
                (line.split(':', 1) for line in path.read_text(encoding='utf-8').splitlines() if ':' in line)
                if value.strip().endswith('kB')}

    def sample(self, root_pid):
        if self.macos is not None:
            return self.macos.sample(root_pid)
        if self.windows is not None:
            return self.windows.sample(root_pid)
        parents = {}
        for path in Path('/proc').iterdir():
            if path.name.isdigit():
                try:
                    fields = (path / 'stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()
                    parents[int(path.name)] = int(fields[1])
                except (OSError, ValueError, IndexError):
                    continue
        pids, frontier, ordered = {root_pid}, {root_pid}, [root_pid]
        while frontier:
            frontier = {pid for pid, parent in parents.items() if parent in frontier} - pids
            pids.update(frontier)
            ordered.extend(sorted(frontier))
        rss = pss = file_pss = dirty_pss = count = 0
        file_counters_complete = True
        tree_read = tree_write = 0
        incomplete = []
        # /proc/PID/io includes waited-for children. Read parents before live
        # descendants, and never add saved counters from exited PIDs again.
        for pid in ordered:
            directory = Path('/proc') / str(pid)
            try:
                io = {key: int(value) for key, value in
                      (line.split(':', 1) for line in (directory / 'io').read_text(encoding='utf-8').splitlines())}
                # Keep exited children's last observed counters, keyed by PID
                # and start tick so PID reuse cannot combine unrelated workers.
                fields = (directory / 'stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()
                start_tick = fields[19]
                self.process_io[(pid, start_tick)] = (io['read_bytes'], io['write_bytes'])
                if fields[0] != 'Z':
                    tree_read += io['read_bytes']
                    tree_write += io['write_bytes']
            except (OSError, ValueError, KeyError, IndexError):
                pass
            try:
                status = self.kilobytes(directory / 'status')
                if 'VmRSS' not in status:
                    continue  # Exited/zombie processes no longer own mappings.
                current_rss = status['VmRSS']
                count += 1
            except OSError:
                continue
            try:
                rollup = self.kilobytes(directory / 'smaps_rollup')
                current_rss = rollup['Rss']
                pss += rollup['Pss']
                file_counters_complete &= all(type(rollup.get(key)) is int and 0 <= rollup[key] <= rollup['Pss']
                                              for key in ('Pss_File', 'Pss_Dirty'))
                file_pss += rollup.get('Pss_File', 0)
                dirty_pss += rollup.get('Pss_Dirty', 0)
            except (OSError, KeyError) as error:
                # A process which exits during the sample is not an unreadable
                # live process. Keep permission failures visible in the record.
                if directory.exists() and not isinstance(error, ProcessLookupError):
                    incomplete.append(pid)
            rss += current_rss
        from .hardware import cgroup_memory
        system = self.kilobytes(Path('/proc/meminfo'))
        available = system['MemAvailable']
        self.last_cgroup = cgroup_memory()
        # Each visible ancestor contains this process tree's protected file
        # pages. The smallest complete bound avoids attributing unrelated
        # host dirty pages to a dedicated container. Native Linux can use
        # the wider system bound. Never subtract anonymous dirty memory from
        # file dirty memory: aggregate PSS cannot establish their overlap.
        protected_bounds = []
        if all(type(system.get(key)) is int and system[key] >= 0 for key in ('Dirty', 'Writeback', 'Unevictable')):
            protected_bounds.append(sum(system[key] for key in ('Dirty', 'Writeback', 'Unevictable')))
        for group in self.last_cgroup.get('groups', []):
            keys = ('dirty_bytes', 'writeback_bytes', 'unevictable_bytes')
            if all(type(group.get(key)) is int and group[key] >= 0 for key in keys):
                protected_bounds.append(sum(group[key] for key in keys))
        protected_file = min(protected_bounds) if protected_bounds else None
        inference_pss = inference_working_pss(
            {'Pss': pss, 'Pss_File': file_pss, 'Pss_Dirty': dirty_pss}, protected_file) if file_counters_complete else pss
        group_available = self.last_cgroup['available_bytes']
        effective = min(available, group_available) if group_available is not None else available
        reclaimable = self.last_cgroup.get('reclaimable_available_bytes', group_available)
        inference_available = min(available, reclaimable) if reclaimable is not None else available
        self.peak_rss = max(self.peak_rss, rss)
        self.peak_tree_read = max(self.peak_tree_read, tree_read)
        self.peak_tree_write = max(self.peak_tree_write, tree_write)
        if not incomplete:
            self.peak_pss = max(self.peak_pss, pss)
            self.peak_inference_pss = max(self.peak_inference_pss, inference_pss)
        self.pss_complete &= not incomplete
        self.minimum_available = min(self.minimum_available or available, available)
        self.minimum_effective_available = min(self.minimum_effective_available if self.minimum_effective_available is not None else effective, effective)
        self.minimum_inference_available = min(self.minimum_inference_available if self.minimum_inference_available is not None else inference_available, inference_available)
        self.samples += 1
        return {'rss_bytes': rss, 'pss_bytes': pss if not incomplete else None,
                # Unlike the guard's upper bound, this excludes every file page.
                # Live planning can add it to available memory without counting
                # clean mapped cache (already reclaimable) a second time.
                'nonfile_pss_bytes': pss - file_pss if file_counters_complete and not incomplete else None,
                'inference_guard_bytes': inference_pss if not incomplete else None,
                'inference_file_protection_bytes': protected_file,
                'guard_bytes': pss if not incomplete else None, 'guard_metric': 'PSS',
                'processes': count, 'unreadable_pss_pids': incomplete,
                'system_available_bytes': available, 'effective_available_bytes': effective,
                'inference_available_bytes': inference_available,
                'system_file_cache_bytes': system.get('Cached'), 'system_dirty_bytes': system.get('Dirty'),
                'system_writeback_bytes': system.get('Writeback'), 'cgroup_memory': self.last_cgroup}

    def result(self):
        if self.macos is not None:
            return self.macos.result()
        if self.windows is not None:
            return self.windows.result()
        return {'process_tree_peak_rss_bytes': self.peak_rss,
                'inference_peak_working_pss_bytes': self.peak_inference_pss,
                'process_tree_peak_guard_bytes': self.peak_pss,
                'process_tree_guard_complete': self.pss_complete, 'ram_guard_metric': 'PSS',
                'process_tree_peak_pss_bytes': self.peak_pss,
                'process_tree_pss_complete': self.pss_complete,
                'system_min_available_bytes': self.minimum_available,
                'effective_min_available_bytes': self.minimum_effective_available,
                'inference_min_available_bytes': self.minimum_inference_available,
                'cgroup_memory_final': self.last_cgroup,
                'ram_observation_samples': self.samples,
                'process_tree_disk_read_bytes': self.peak_tree_read,
                'process_tree_disk_write_bytes': self.peak_tree_write,
                'process_tree_io_version': 2,
                'process_tree_io_scope': 'Maximum sampled live-tree sum, parents before children. Waited-child I/O rolls into the parent; exited PIDs are not added again. Sampling can miss I/O at exit; this is not cgroup disk accounting.',
                'ram_observation_scope': 'Sampled worker and descendants; RSS sums may double-count shared pages, PSS apportions resident shared pages; unmapped file cache excluded. System MemAvailable is not process attribution.'}


class CgroupMemory:
    """Validate an externally imposed hard cap and account for all descendants.

    The launcher must create a fresh, dedicated cgroup/container. This class does
    not create limits and never substitutes RSS polling for kernel enforcement.
    """

    def __init__(self, budget_bytes, root=Path('/sys/fs/cgroup'), *, swap_budget_bytes=0):
        self.root = Path(root)
        self.budget_bytes = budget_bytes
        self.swap_budget_bytes = swap_budget_bytes
        self.sampled_swap_peak = 0
        self.swap_limit_violation = False
        if budget_bytes <= 0:
            raise ValueError('RAM budget must be positive')
        if type(swap_budget_bytes) is not int or swap_budget_bytes < 0:
            raise ValueError('Swap budget must be a nonnegative integer number of bytes')
        self.initial = self.snapshot()
        limit = self.initial['limit_bytes']
        if limit is None or limit > budget_bytes:
            raise RuntimeError('A dedicated cgroup memory.max at or below the requested RAM budget is required')
        if self.swap_limit_violation or self.initial['swap_current_bytes'] != 0:
            raise RuntimeError('RAM measurements require a bounded memory.swap.max at or below the explicit swap budget and no existing swap use (default: zero swap)')

    def number(self, name):
        value = (self.root / name).read_text(encoding='utf-8').strip()
        return None if value == 'max' else int(value)

    def fields(self, name):
        return {key: int(value) for key, value in
                (line.split() for line in (self.root / name).read_text(encoding='utf-8').splitlines())}

    def optional(self, name, reader):
        try:
            return reader(name)
        except FileNotFoundError:
            return None

    def io_fields(self, name):
        return {parts[0]: {key: int(value) for key, value in
                          (entry.split('=', 1) for entry in parts[1:])}
                for parts in (line.split() for line in (self.root / name).read_text(encoding='utf-8').splitlines())}

    def snapshot(self):
        state = {'limit_bytes': self.number('memory.max'),
                'current_bytes': self.number('memory.current'),
                'peak_bytes': self.number('memory.peak'),
                'swap_limit_bytes': self.number('memory.swap.max'),
                'swap_current_bytes': self.number('memory.swap.current'),
                'swap_peak_bytes': self.optional('memory.swap.peak', self.number),
                'swap_events': self.optional('memory.swap.events', self.fields),
                'io_stat': self.optional('io.stat', self.io_fields),
                'events': self.fields('memory.events'), 'stat': self.fields('memory.stat')}
        self.sampled_swap_peak = max(self.sampled_swap_peak, state['swap_current_bytes'])
        state['sampled_swap_peak_bytes'] = self.sampled_swap_peak
        swap_limit = state['swap_limit_bytes']
        self.swap_limit_violation |= swap_limit is None or swap_limit > self.swap_budget_bytes
        return state

    def events_since_start(self, state):
        return {key: value - self.initial['events'].get(key, 0)
                for key, value in state['events'].items()}

    def result(self):
        final = self.snapshot()
        events = self.events_since_start(final)
        physical_within = (final['limit_bytes'] is not None and final['limit_bytes'] <= self.budget_bytes
                           and final['peak_bytes'] <= self.budget_bytes)
        swap_peak = max(self.sampled_swap_peak, final['swap_peak_bytes'] or 0)
        swap_within = not self.swap_limit_violation and swap_peak <= self.swap_budget_bytes
        within = (physical_within and swap_within and not events.get('oom', 0)
                  and not events.get('oom_kill', 0) and not events.get('oom_group_kill', 0))
        swap_events = (None if final['swap_events'] is None or self.initial['swap_events'] is None else
                       {key: value - self.initial['swap_events'].get(key, 0)
                        for key, value in final['swap_events'].items()})
        io_delta = (None if final['io_stat'] is None or self.initial['io_stat'] is None else
                    {device: {key: value - self.initial['io_stat'].get(device, {}).get(key, 0)
                              for key, value in fields.items()} for device, fields in final['io_stat'].items()})
        return {'ram_budget_bytes': self.budget_bytes, 'ram_limit_enforced': True,
                'ram_within_budget': within, 'ram_cgroup_peak_bytes': final['peak_bytes'],
                'physical_ram_within_budget': physical_within,
                'swap_budget_bytes': self.swap_budget_bytes, 'swap_limit_enforced': True,
                'swap_within_budget': swap_within, 'swap_peak_bytes': swap_peak,
                'swap_peak_scope': ('Cgroup lifetime kernel peak' if final['swap_peak_bytes'] is not None
                                    else 'Sampled lower bound; memory.swap.peak unavailable'),
                'swap_cgroup_events': swap_events, 'cgroup_io_delta': io_delta,
                'cgroup_io_scope': 'All charged block I/O, including model reads and swap; not swap-only traffic',
                'memory_mode': 'bounded_swap' if self.swap_budget_bytes else 'no_swap',
                'ram_cgroup_initial': self.initial, 'ram_cgroup_final': final,
                'ram_cgroup_events': events,
                'ram_scope': 'Dedicated cgroup and descendants, including anonymous memory, charged file cache, kernel and shared memory; cgroup lifetime peak'}
