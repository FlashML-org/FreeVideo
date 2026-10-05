"""Native process memory accounting; no CUDA or MPS imports.

RSS can miss kernel-accounted Metal allocations. Use phys_footprint for the
request guard, retain RSS separately, and never add the two or GPU driver bytes.
"""
from .macos_memory import memory_status, process_footprint


class ProcessMemory:
    def __init__(self):
        import psutil
        self.psutil = psutil
        self.peak_rss = self.peak_footprint = self.samples = 0
        self.complete = True
        self.minimum_available = None

    def sample(self, root_pid):
        psutil = self.psutil
        errors, processes = [], []

        def unreadable(pid, error):
            errors.append(dict(pid=pid, error=type(error).__name__))

        try:
            root = psutil.Process(root_pid)
            processes = [root]
            try:
                processes.extend(root.children(recursive=True))
            except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
                # The tree may be incomplete even if its root exits mid-query.
                unreadable(root_pid, error)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            unreadable(root_pid, error)
        rss = footprint = count = 0
        seen = set()
        for process in processes:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    continue
                resident = process.memory_info().rss
                try:
                    charged = process_footprint(process.pid)
                except ProcessLookupError:
                    continue
                except OSError as error:
                    unreadable(process.pid, error)
                    charged = 0
                rss += resident
                footprint += charged
                count += 1
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as error:
                unreadable(process.pid, error)
        system = memory_status()
        available = system['available_bytes']
        self.samples += 1
        self.peak_rss = max(self.peak_rss, rss)
        self.peak_footprint = max(self.peak_footprint, footprint)
        self.complete &= not errors
        self.minimum_available = (available if self.minimum_available is None
                                  else min(self.minimum_available, available))
        guard = None if errors else footprint
        return dict(rss_bytes=rss, pss_bytes=None, nonfile_pss_bytes=None,
            phys_footprint_bytes=footprint,
            private_commit_bytes=None, guard_bytes=guard, inference_guard_bytes=guard,
            guard_metric='phys_footprint', processes=count, memory_model='unified',
            unreadable_memory_pids=sorted({row['pid'] for row in errors}),
            memory_read_errors=errors, system_available_bytes=available,
            system_physical_available_bytes=available, system_commit_available_bytes=None,
            effective_available_bytes=available, inference_available_bytes=available,
            macos_memory=system)

    def result(self):
        return dict(process_tree_peak_rss_bytes=self.peak_rss,
            process_tree_peak_phys_footprint_bytes=self.peak_footprint,
            process_tree_peak_guard_bytes=self.peak_footprint, process_tree_guard_complete=self.complete,
            process_tree_peak_pss_bytes=None, process_tree_pss_complete=False,
            inference_peak_working_pss_bytes=None, ram_guard_metric='phys_footprint',
            system_min_available_bytes=self.minimum_available,
            effective_min_available_bytes=self.minimum_available,
            inference_min_available_bytes=self.minimum_available,
            cgroup_memory_final=None, ram_observation_samples=self.samples,
            process_tree_disk_read_bytes=None, process_tree_disk_write_bytes=None,
            ram_observation_scope='Sampled live worker/descendant kernel phys_footprint charge, '
                'including compressed and kernel-accounted IOKit memory; not resident physical RAM. '
                'RSS is retained separately. No PSS, private-commit, disk-I/O or kernel-limit claim. '
                'Metal driver memory is not added again. Peaks can miss events between samples.')
