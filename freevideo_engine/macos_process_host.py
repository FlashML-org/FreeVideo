"""Mac child ownership, including FreeVideo workers in separate sessions.

Each FreeVideo spawn has a lightweight parent watcher. Descendants are tracked
with psutil process identities so cleanup never signals a reused PID. This is
not a kernel subreaper: unrelated daemonization is outside this contract.
"""
import os
import signal
import subprocess
import sys
import time

import psutil


class Tree:
    def __init__(self, pid):
        self.processes = {}
        try:
            process = psutil.Process(pid)
            self.processes[process.pid] = process
        except psutil.NoSuchProcess:
            pass

    def alive(self):
        found = {}
        for process in list(self.processes.values()):
            if process.pid in found:
                continue  # An earlier live ancestor already enumerated this subtree.
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    found[process.pid] = process
                    for child in process.children(recursive=True):
                        found[child.pid] = child
            except psutil.NoSuchProcess:
                pass
        self.processes.update(found)
        result = []
        for process in self.processes.values():
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    result.append(process)
            except psutil.NoSuchProcess:
                pass
        self.processes = {process.pid: process for process in result}
        return result

    def cleanup(self, grace):
        deadline = time.monotonic() + grace
        terminated = set()
        while pending := self.alive():
            force = time.monotonic() >= deadline
            for process in pending:
                try:
                    identity = (process.pid, process.create_time())
                    if force:
                        process.kill()
                    elif identity not in terminated:
                        process.terminate()
                        terminated.add(identity)
                except psutil.NoSuchProcess:
                    pass
            if time.monotonic() > deadline + 3:
                raise RuntimeError('Mac worker cleanup could not confirm exit; retain the runtime lease')
            time.sleep(.05)


def stop(process, grace=10):
    # Capture descendants before signalling. The root can exit first and leave
    # separate-session workers reparented to launchd while they clean up.
    tree = Tree(process.pid) if process.poll() is None else None
    if tree is not None:
        try:
            tree.cleanup(grace)
        finally:
            process.poll()
    process.wait(timeout=3)


def command(command, inherited):
    from .processes import module_command
    arguments = [str(os.getpid()), ','.join(map(str, inherited)), *command]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--macos-process-host', *arguments]
    return module_command('freevideo_engine.macos_process_host', *arguments)


def main(argv=None):
    parent, inherited, *arguments = sys.argv[1:] if argv is None else argv
    stopping = []
    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(name, lambda *_: stopping.append(True))
    if os.getppid() != int(parent) or stopping:
        return 130
    child = subprocess.Popen(arguments, start_new_session=True,
        pass_fds=tuple(int(fd) for fd in inherited.split(',') if fd))
    tree = Tree(child.pid)
    try:
        while child.poll() is None and not stopping:
            tree.alive()
            if os.getppid() != int(parent):
                stopping.append(True)
                break
            try:
                child.wait(timeout=.1)
            except subprocess.TimeoutExpired:
                pass
        return 130 if stopping else child.returncode if child.returncode >= 0 else 128 - child.returncode
    finally:
        tree.cleanup(2)
        child.wait(timeout=3)


if __name__ == '__main__':
    raise SystemExit(main())
