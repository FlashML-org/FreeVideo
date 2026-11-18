"""Keep the computer from sleeping on its own while a video is being made.

Windows holds an execution-state request, Linux the desktop's and logind's sleep inhibitors, macOS its own power assertion
(macos_power). An explicit sleep, a closed lid and the display turning off keep their normal meaning, and
on battery nothing is held unless the user asked for it. Failing to take the hold never stops a generation.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

BATTERY_ENV = 'FREEVIDEO_AWAKE_ON_BATTERY'


def on_battery():
    """True only when the machine reports running on battery; unknown counts as plugged in."""
    try:
        if sys.platform == 'win32':
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte), ('BatteryLifePercent', ctypes.c_ubyte),
                            ('SystemStatusFlag', ctypes.c_ubyte), ('BatteryLifeTime', ctypes.c_uint32), ('BatteryFullLifeTime', ctypes.c_uint32)]
            status = Status()
            return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))) and status.ACLineStatus == 0
        if sys.platform.startswith('linux'):
            supplies = list(Path('/sys/class/power_supply').iterdir())
            kinds = {path: (path / 'type').read_text().strip() for path in supplies if (path / 'type').is_file()}
            mains = [path for path, kind in kinds.items() if kind in ('Mains', 'USB')]
            if not any(kind == 'Battery' for kind in kinds.values()) or not mains:
                return False
            return not any((path / 'online').read_text().strip() == '1' for path in mains if (path / 'online').is_file())
    except (OSError, ValueError):
        pass
    return False


WHY = 'Generating a video'


def _start(command):
    """The helper while it holds its inhibitor, or None when it ended at once (refused or unavailable)."""
    try:
        # Its own process group, so the release ends the helper and the command it waits on together.
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None


def _holding(processes, seconds=.3):
    """The helpers still running after a short wait; one that is refused exits at once."""
    deadline = time.monotonic() + seconds
    for process in processes:
        try:
            process.wait(timeout=max(0., deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            continue
    return [process for process in processes if process.poll() is None]


def _linux_inhibitors():
    """Helpers that each hold one sleep inhibitor while they run; every hold also ends with this process.

    A desktop suspends an idle computer through logind as the same user, and logind lets that request past
    the user's own inhibitors, so the desktop's own list is held too: gnome-session (suspend only, so the
    screen still blanks and locks) and KDE's power management. logind's hold covers its own idle action and
    a computer without a desktop; where blocking sleep is not allowed (a remote session), the idle hold alone
    is kept.
    """
    if not shutil.which('tail'):
        return []
    wait = ['tail', f'--pid={os.getpid()}', '-f', '/dev/null']
    commands = []
    tool = shutil.which('gnome-session-inhibit')
    if tool:
        commands.append([tool, '--app-id', 'FreeVideo', '--reason', WHY, '--inhibit', 'suspend', *wait])
    tool = shutil.which('kde-inhibit')
    if tool:
        commands.append([tool, '--power', *wait])
    logind = shutil.which('systemd-inhibit')

    def hold(what):
        return [logind, f'--what={what}', '--who=FreeVideo', f'--why={WHY}', '--mode=block', *wait]
    if logind:
        commands.append(hold('sleep:idle'))
    started = [(command, _start(command)) for command in commands]
    held = _holding([process for _, process in started if process is not None])
    if logind and not any(process in held for command, process in started if command[0] == logind):
        held += _holding([process for process in [_start(hold('idle'))] if process is not None])
    return held


def _release(processes):
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
            process.wait()


@contextmanager
def awake():
    """Hold the sleep prevention for the duration of the block; yields whether it is held."""
    if os.environ.get(BATTERY_ENV) != '1' and on_battery():
        yield False
        return
    release = None
    try:
        if sys.platform == 'win32':
            from .windows_ux import awake as windows_awake
            guard = windows_awake()
        elif sys.platform == 'darwin':
            from .macos_power import awake as mac_awake
            guard = mac_awake()
        elif sys.platform.startswith('linux'):
            release = _linux_inhibitors() or None
            guard = None
        else:
            guard = None
    except Exception:
        guard = None
    if release is not None:
        try:
            yield True
        finally:
            _release(release)
        return
    if guard is None:
        yield False
        return
    with guard as held:
        yield held
