"""Names of the graphics adapters the operating system lists, without any vendor driver.

Used only to say what was seen on a setup card. None means the lookup itself failed,
which is different from a computer that lists no NVIDIA adapter.
"""
import os
import re
import subprocess

DISPLAY_CLASS = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11cf-bfc1-08002be10318}'
PCI_ENUM = r'SYSTEM\CurrentControlSet\Enum\PCI'
NVIDIA_VENDOR = '10de'
# PCI vendors whose cards are never NVIDIA's, for naming a driverless card the display class lists without a vendor.
OTHER_VENDOR_IDS = {'1002': 'AMD graphics', '8086': 'Intel graphics'}
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


ERROR_NO_MORE_ITEMS = 259
CR_SUCCESS, CR_NO_SUCH_DEVNODE = 0, 13


def _keys(winreg, key):
    """Every subkey name of an open key. Only "no more items" ends the list; any other failure raises OSError."""
    names, index = [], 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError as error:
            if getattr(error, 'winerror', None) == ERROR_NO_MORE_ITEMS or error.errno == ERROR_NO_MORE_ITEMS:
                return names
            raise
        index += 1


def device_present(instance_id):
    """True when the device is in the device tree now, False when only a record of a removed device is left.

    Raises OSError when the system cannot say, so the caller treats the answer as unknown.
    """
    import ctypes
    try:
        locate = ctypes.windll.cfgmgr32.CM_Locate_DevNodeW
    except (AttributeError, OSError) as error:
        raise OSError('device tree not available') from error
    handle = ctypes.c_uint32(0)
    # Flags 0: only a device that is currently configured is found; the registry keeps removed ones as well.
    status = locate(ctypes.byref(handle), ctypes.c_wchar_p(instance_id), 0)
    if status == CR_SUCCESS:
        return True
    if status == CR_NO_SUCH_DEVNODE:
        return False
    raise OSError('device lookup failed (%s)' % status)


def pci_display_vendors():
    """PCI vendor IDs (lower case, four digits) of the display devices present now, driver or not.

    A card without a driver is listed here by its hardware IDs even when the display class names it only as
    "Basic Display Adapter" with no vendor. Read-only. The answer is a complete one or an OSError: a key that
    cannot be read is never skipped, because a partial list could make an unreadable NVIDIA card look absent.
    Records of removed devices are left out.
    """
    import winreg
    vendors = set()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PCI_ENUM) as head:
        for device in _keys(winreg, head):
            match = re.match(r'ven_([0-9a-f]{4})&', device, re.I)
            if not match:
                continue
            with winreg.OpenKey(head, device) as parent:
                for slot in _keys(winreg, parent):
                    with winreg.OpenKey(parent, slot) as key:
                        ids = []
                        for name in ('HardwareID', 'CompatibleIDs'):
                            try:
                                value, _ = winreg.QueryValueEx(key, name)
                            except FileNotFoundError:
                                continue
                            ids.extend([value] if isinstance(value, str) else list(value or ()))
                    # PCI class 03 is a display controller (0300 VGA, 0302 3D).
                    if any(re.search(r'cc_03', str(item), re.I) for item in ids) \
                            and device_present('PCI\\' + device + '\\' + slot):
                        vendors.add(match.group(1).lower())
    return vendors


def vendor_of_unknown_adapter():
    """NVIDIA, a name for another vendor, or None when it cannot be told from the system's PCI list."""
    try:
        vendors = pci_display_vendors() if os.name == 'nt' else set()
    except Exception:
        # Any surprise from the registry or the device tree (ctypes.ArgumentError is not an OSError) means unknown;
        # this lookup only refines a card note and must never stop the launcher.
        return None
    if NVIDIA_VENDOR in vendors:
        return NVIDIA
    if vendors and vendors <= set(OTHER_VENDOR_IDS):
        return ' / '.join(OTHER_VENDOR_IDS[item] for item in sorted(vendors))
    return None


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
    # The PCI list names the vendor of a card that has no driver; when it cannot be read the answer stays unknown.
    if unknown and not any(NVIDIA_NAME.search(name) for name in seen):
        vendor = vendor_of_unknown_adapter()
        if vendor is None:
            return None
        if vendor == NVIDIA:
            seen.append(NVIDIA)
        elif not seen:
            seen.append(vendor)
    return seen


def detected_line(zh):
    return line(zh, adapters())


def only_other_vendors(names):
    """True when adapters are listed and none is NVIDIA; False when one is, none is listed or the lookup failed."""
    return bool(names) and not any(NVIDIA_NAME.search(name) for name in names)


def report_line(names):
    """One line for the report: adapter model names only, or that the lookup failed."""
    if names is None:
        return 'Display adapters: not available'
    return 'Display adapters: ' + ('; '.join(names) if names else 'none listed')


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
