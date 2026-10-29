"""Names of the graphics adapters the operating system lists, without any vendor driver.

Used only to say what was seen on a setup card. None means the lookup itself failed,
which is different from a computer that lists no NVIDIA adapter.
"""
import os
import re
import subprocess

DISPLAY_CLASS = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11cf-bfc1-08002be10318}'
BASIC = re.compile(r'basic (?:display|render)', re.I)
NVIDIA = 'NVIDIA'
NVIDIA_NAME = re.compile(r'nvidia|geforce|quadro|rtx|tesla', re.I)
OTHER_VENDOR = re.compile(r'\bamd\b|radeon|\bati\b|intel', re.I)
VIRTUAL = re.compile(r'basic (?:display|render)|remote|virtual|indirect|mirror|hyper-v|vmware|parsec', re.I)


def windows_names():
    import winreg
    names = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS) as head:
        index = 0
        while True:
            try:
                slot = winreg.EnumKey(head, index)
            except OSError:
                break
            index += 1
            if not slot.isdigit():
                continue
            try:
                with winreg.OpenKey(head, slot) as key:
                    value, _ = winreg.QueryValueEx(key, 'DriverDesc')
                    try:
                        ids, _ = winreg.QueryValueEx(key, 'MatchingDeviceId')
                    except OSError:
                        ids = None
            except OSError:
                continue
            names.append((str(value), None if ids is None else str(ids)))
    return names


def linux_names():
    out = subprocess.run(['lspci', '-mm'], capture_output=True, text=True, timeout=10, check=True).stdout
    names = []
    for line in out.splitlines():
        fields = re.findall(r'"([^"]*)"', line)
        if len(fields) >= 3 and re.search(r'VGA|3D|Display', fields[0]):
            names.append(' '.join(fields[1:3]))
    return names


def adapters():
    """Adapter names in system order, or None when the system could not be asked."""
    try:
        names = windows_names() if os.name == 'nt' else linux_names()
    except (ImportError, OSError, ValueError, subprocess.SubprocessError):
        return None
    seen, unknown = [], False
    for entry in names:
        name, ids = (entry, '') if isinstance(entry, str) else entry
        name = ' '.join(name.split())
        if BASIC.search(name):
            # Without a driver a card is listed by this name; its PCI vendor says whose it is.
            # The matched ID of a driverless card is often only the display class (PCI\CC_0300), without a vendor.
            if ids is None or 'ven_' not in ids.lower():
                unknown = True
                continue
            if 'ven_10de' in ids.lower():
                name = NVIDIA
            else:
                continue
        if name and (name == NVIDIA or not VIRTUAL.search(name)) and name not in seen:
            seen.append(name)
    # An adapter of unknown vendor may be the missing NVIDIA card, so "none detected" would mislead.
    if unknown and not any(NVIDIA_NAME.search(name) for name in seen):
        return None
    return seen


def detected_line(zh):
    return line(zh, adapters())


def only_other_vendors(names):
    """True when the system lists an AMD or Intel adapter and no NVIDIA one; False when the lookup failed."""
    return bool(names) and not any(NVIDIA_NAME.search(name) for name in names) \
        and any(OTHER_VENDOR.search(name) for name in names)


def line(zh, names):
    """The one line for a "no NVIDIA driver" card; empty when detection itself failed."""
    if names is None:
        return ''
    nvidia = sorted((name for name in names if NVIDIA_NAME.search(name)),
                    key=lambda name: name == NVIDIA)
    if nvidia == [NVIDIA]:
        return '检测到：NVIDIA 显卡' if zh else 'Detected: NVIDIA graphics card'
    if nvidia:
        return ('检测到：' if zh else 'Detected: ') + nvidia[0]
    other = '、'.join(names[:2]) if zh else ', '.join(names[:2])
    if zh:
        return '未检测到 NVIDIA 显卡' + ('（%s）' % other if other else '')
    return 'No NVIDIA graphics card detected' + (' (%s)' % other if other else '')
