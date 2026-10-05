"""Worker creation, inherited leases and platform-specific bounded cleanup."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager

from .system import windows

HANDLE_ENV = 'FREEVIDEO_RUNTIME_LOCK_HANDLE'
CREATE_SUSPENDED = 0x4
_spawn_lock = threading.Lock()


def inherited_fd():
    """Adopt a Windows handle once; CRT descriptor numbers are process-local."""
    value = os.environ.get(HANDLE_ENV)
    if windows() and value:
        import msvcrt
        handle = int(value)
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        os.environ.pop(HANDLE_ENV)
        os.environ['FREEVIDEO_RUNTIME_LOCK_FD'] = str(descriptor)
        return descriptor
    value = os.environ.get('FREEVIDEO_RUNTIME_LOCK_FD')
    return int(value) if value is not None else None


def descriptors():
    descriptor = inherited_fd()
    return () if descriptor is None else (descriptor,)


def module_command(module, *arguments):
    """Also works with portable Windows Python, whose ._pth ignores PYTHONPATH."""
    code = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module(sys.argv.pop(1),run_name='__main__',alter_sys=True)")
    return [sys.executable, '-B', '-X', 'utf8', '-c', code,
            str(Path(__file__).resolve().parents[1]), module, *map(str, arguments)]


class WindowsPopen(subprocess.Popen):
    def __init__(self, command, *, pass_fds=(), env=None, **kwargs):
        import ctypes
        import msvcrt
        from .win32 import Job, HANDLE, checked, kernel32, resume_suspended
        lib = kernel32()
        self.job = Job()
        handles = []
        environment = dict(os.environ if env is None else env)
        # A worker must belong to the Job before it can spawn anything; this
        # closes the launch/kill race. The frozen GUI starts the worker itself
        # suspended, assigns it and resumes it. It used to start a hidden
        # PowerShell gate with -ExecutionPolicy Bypass, the pattern behind a
        # Defender Trojan:Script/Wacatac.B!ml verdict on the unsigned EXE.
        # Managed Python keeps its Python gate, which waits for assignment.
        frozen = getattr(sys, 'frozen', False)
        try:
            if frozen:
                if pass_fds:
                    raise ValueError('The desktop launcher delegates runtime leases to managed Python')
                host, flags = list(command), subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
            else:
                gate = checked(lib.CreateEventW(None, True, False, None))
                handles.append(gate)
                host, flags = module_command('freevideo_engine.process_host', gate, *command), subprocess.CREATE_NEW_PROCESS_GROUP
            with _spawn_lock:
                if not frozen:
                    checked(lib.SetHandleInformation(gate, 1, 1))
                if len(pass_fds) > 1:
                    raise ValueError('Only the runtime lease may be inherited by a Windows worker')
                for fd in pass_fds:
                    duplicate = HANDLE()
                    current = lib.GetCurrentProcess()
                    checked(lib.DuplicateHandle(current, msvcrt.get_osfhandle(fd), current,
                                                ctypes.byref(duplicate), 0, True, 2))
                    handles.append(duplicate.value)
                    environment[HANDLE_ENV] = str(duplicate.value)
                if not pass_fds:
                    environment.pop(HANDLE_ENV, None)
                    environment.pop('FREEVIDEO_RUNTIME_LOCK_FD', None)
                info = subprocess.STARTUPINFO()
                info.lpAttributeList = {'handle_list': handles}
                # Managed Python is not frozen. It still launches native
                # helpers after the GUI starts; hide those job gates too.
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = 0
                kwargs.pop('start_new_session', None)
                kwargs.pop('close_fds', None)
                super().__init__(host, env=environment, startupinfo=info, close_fds=True,
                                 creationflags=flags, **kwargs)
                self.job.assign(self._handle)
                if frozen:
                    resume_suspended(self.pid)
                else:
                    checked(lib.SetEvent(gate))
        except BaseException:
            if getattr(self, '_child_created', False):
                super().kill()
                super().wait(timeout=5)
            self.job.close()
            raise
        finally:
            for handle in handles:
                lib.CloseHandle(handle)

    def poll(self):
        value = super().poll()
        if value is not None:
            self.job.close()
        return value

    def wait(self, timeout=None):
        value = super().wait(timeout=timeout)
        self.job.close()
        return value

    def kill(self):
        self.job.kill()

    def terminate(self):
        self.kill()


def popen(command, *, pass_fds=(), supervise=False, **kwargs):
    if windows():
        return WindowsPopen(command, pass_fds=pass_fds, **kwargs)
    if sys.platform == 'darwin':
        import shutil
        from .macos_process_host import command as watched
        executable = str(command[0])
        if os.sep in executable and not os.path.isabs(executable):
            executable = str(Path(kwargs.get('cwd') or os.getcwd()) / executable)
        if shutil.which(executable, path=(kwargs.get('env') or os.environ).get('PATH')) is None:
            raise FileNotFoundError(2, 'Install executable not found', str(command[0]))
        command = watched(command, pass_fds)
    if supervise and sys.platform == 'linux':
        import shutil
        executable = str(command[0])
        if os.sep in executable and not os.path.isabs(executable):
            executable = str(Path(kwargs.get('cwd') or os.getcwd()) / executable)
        if shutil.which(executable, path=(kwargs.get('env') or os.environ).get('PATH')) is None:
            raise FileNotFoundError(2, 'Install executable not found', str(command[0]))
        command = module_command('freevideo_engine.linux_process_host',
                                 os.getpid(), ','.join(map(str, pass_fds)), *command)
    return subprocess.Popen(command, pass_fds=pass_fds, **kwargs)


def stop(process, grace=10):
    if hasattr(process, 'stop_request'):
        return process.stop_request(grace=grace)
    if sys.platform == 'darwin':
        from .macos_process_host import stop as stop_native
        return stop_native(process, grace=grace)
    if process.poll() is not None:
        return
    try:
        if windows():
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if windows():
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)


def run(command, *, timeout=None, check=False, capture_output=False, input=None, **kwargs):
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    kwargs.setdefault('start_new_session', True)
    with popen(command, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            stop(process)
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def termination_handler(handler):
    """Enter cancellation once; repeated signals must not interrupt cleanup.

    A supervisor may signal both a process group and its adopted children.
    Raising again while stop()/report settlement is running turns a confirmed
    user cancellation into an unacknowledged worker exit. Forced termination
    remains the supervising process's bounded SIGKILL / Job responsibility.
    """
    names = (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK) if windows() else (signal.SIGINT, signal.SIGTERM)
    cancelled = False
    def once(signum, frame):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            handler(signum, frame)
    return {name: signal.signal(name, once) for name in names}


def restore_handlers(previous):
    for name, handler in previous.items():
        signal.signal(name, handler)


@contextmanager
def worker_signals():
    """Let Windows workers finalize stage reports before the Job timeout."""
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Worker interrupted by signal %s' % signum)
    previous = termination_handler(interrupted) if windows() else {}
    try:
        yield
    finally:
        restore_handlers(previous)
