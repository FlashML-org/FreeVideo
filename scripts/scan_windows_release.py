"""Check final release files on the Windows build runner, never during installation.

MpCmdRun custom scans with DisableRemediation ignore file exclusions, inspect
archives, and retain detected files for investigation. A missing scanner, failed
update, timeout or changed artifact is an error, never a clean scan.
Explicit launch verification also enables real-time/cloud protection on the
disposable runner and checks Internet-zone downloads with Attachment Services.
Scripts get the AMSI verdict PowerShell would receive before running them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def scanner():
    platform = Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'Microsoft/Windows Defender/Platform'
    candidates = sorted(platform.glob('*/MpCmdRun.exe'), reverse=True)
    candidates.append(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Windows Defender/MpCmdRun.exe')
    return next((path for path in candidates if path.is_file()), None)


def command(args, log):
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT, timeout=600)
    return result.returncode


def powershell(script):
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90, check=True)
    return json.loads(result.stdout.lstrip('\ufeff'))


def metadata():
    return powershell("""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$s = Get-MpComputerStatus
$p = Get-MpPreference
[ordered]@{ engine = $s.AMEngineVersion; product = $s.AMProductVersion;
  signatures = $s.AntivirusSignatureVersion; signature_age_days = $s.AntivirusSignatureAge;
  service_enabled = $s.AMServiceEnabled; antivirus_enabled = $s.AntivirusEnabled;
  realtime_enabled = $s.RealTimeProtectionEnabled;
  behavior_enabled = $s.BehaviorMonitorEnabled; ioav_enabled = $s.IoavProtectionEnabled;
  maps_reporting = [int]$p.MAPSReporting; sample_consent = [int]$p.SubmitSamplesConsent;
  block_at_first_sight = -not $p.DisableBlockAtFirstSeen;
  archive_scanning = -not $p.DisableArchiveScanning; script_scanning = -not $p.DisableScriptScanning;
  excluded_paths = @($p.ExclusionPath | Where-Object { $_ });
  excluded_extensions = @($p.ExclusionExtension | Where-Object { $_ });
  excluded_processes = @($p.ExclusionProcess | Where-Object { $_ });
  signature_updated = $s.AntivirusSignatureLastUpdated.ToUniversalTime().ToString('o') } | ConvertTo-Json -Compress
""")


def enable_realtime():
    """Only the explicit release-test mode changes this disposable CI machine."""
    info = metadata()
    try:
        require_realtime(info)
        return info
    except RuntimeError:
        pass
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('Configure real-time/cloud protection and remove test exclusions before validation; '
                           'automatic Defender configuration is limited to disposable Actions runners')
    powershell("""
$ErrorActionPreference = 'Stop'
# GitHub's Windows image excludes C:\\ and D:\\ and disables archive/script
# scans. Remove its performance exclusions to test normal download protection.
$p = Get-MpPreference
if ($p.ExclusionPath) { Remove-MpPreference -ExclusionPath $p.ExclusionPath }
if ($p.ExclusionExtension) { Remove-MpPreference -ExclusionExtension $p.ExclusionExtension }
if ($p.ExclusionProcess) { Remove-MpPreference -ExclusionProcess $p.ExclusionProcess }
Set-MpPreference -DisableArchiveScanning $false -DisableScriptScanning $false
Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false -DisableIOAVProtection $false
Set-MpPreference -MAPSReporting Advanced -SubmitSamplesConsent SendSafeSamples -DisableBlockAtFirstSeen $false
ConvertTo-Json $true -Compress
""")
    deadline = time.monotonic() + 30
    while True:
        info = metadata()
        try:
            require_realtime(info)
            return info
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def require_realtime(info):
    fields = ('service_enabled', 'antivirus_enabled', 'realtime_enabled',
              'behavior_enabled', 'ioav_enabled', 'block_at_first_sight', 'archive_scanning', 'script_scanning')
    if (any(info.get(key) is not True for key in fields) or info.get('maps_reporting') != 2
            or info.get('sample_consent') not in (1, 3)
            or any(info.get(key) != [] for key in ('excluded_paths', 'excluded_extensions', 'excluded_processes'))):
        raise RuntimeError('Real-time/cloud Defender protection is unavailable; release validation is inconclusive')


def threat_history():
    return powershell("""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$names = @{}
Get-MpThreat | ForEach-Object { $names[[string]$_.ThreatID] = $_.ThreatName }
$rows = @(Get-MpThreatDetection | ForEach-Object {
  [ordered]@{ id = $_.DetectionID; threat_id = $_.ThreatID; name = $names[[string]$_.ThreatID];
    detected_at = $_.InitialDetectionTime.ToUniversalTime().ToString('o');
    resources = @($_.Resources); action_success = $_.ActionSuccess }
})
ConvertTo-Json -InputObject $rows -Depth 4 -Compress
""")


AMSI_BLOCKED = 16384  # AMSI_RESULT_BLOCKED_BY_ADMIN_START; detections are 32768 and above


def amsi_verdicts(paths):
    """Scan each script's text the way PowerShell does before running it; returns {path: AMSI_RESULT}."""
    for path in paths:
        if "'" in str(path):
            raise ValueError('Script path must not contain a quote: ' + str(path))
    listing = ', '.join("'%s'" % Path(path).resolve() for path in paths)
    return powershell("""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class Amsi {
    [DllImport("amsi.dll", CharSet = CharSet.Unicode)] public static extern int AmsiInitialize(string appName, out IntPtr context);
    [DllImport("amsi.dll", CharSet = CharSet.Unicode)] public static extern int AmsiScanString(IntPtr context, string content, string name, IntPtr session, out int result);
    [DllImport("amsi.dll")] public static extern void AmsiUninitialize(IntPtr context);
}
'@
[IntPtr]$context = 0
if ([Amsi]::AmsiInitialize('FreeVideo release validation', [ref]$context) -ne 0) { throw 'AmsiInitialize failed' }
$rows = [ordered]@{}
foreach ($path in @(%s)) {
    $verdict = 0
    $status = [Amsi]::AmsiScanString($context, [IO.File]::ReadAllText($path), $path, [IntPtr]::Zero, [ref]$verdict)
    if ($status -ne 0) { throw "AmsiScanString failed for $path (HRESULT 0x$($status.ToString('x8')))" }
    $rows[$path] = $verdict
}
[Amsi]::AmsiUninitialize($context)
ConvertTo-Json -InputObject $rows -Compress
""" % listing)


def mark_download(path):
    # Preserve the Internet-zone evidence a browser supplies. Never unblock it.
    url = 'https://github.com/FlashML-org/FreeVideo/releases/download/windows-preview/'
    Path(str(path) + ':Zone.Identifier').write_text(
        '[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=' + url + path.name + '\r\n', encoding='utf-8')


def attachment_check(path):
    """Use Windows Attachment Services, as browsers do when saving a download.

    IAttachmentExecute::Save can quarantine/delete the file. Any rejection is
    retained, never bypassed by changing the file or its Internet-zone marker.
    The COM method order is defined in the Windows SDK's shobjidl_core.h.
    """
    import ctypes as C
    import uuid
    guid = lambda value: (C.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)
    ole = C.WinDLL('ole32')
    ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [C.c_void_p, C.c_uint32], C.c_long
    ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = [], None
    ole.CoCreateInstance.argtypes = [C.c_void_p, C.c_void_p, C.c_uint32, C.c_void_p, C.POINTER(C.c_void_p)]
    ole.CoCreateInstance.restype = C.c_long
    def checked(result, operation):
        if result < 0:
            raise RuntimeError('%s rejected %s (HRESULT 0x%08x)' % (operation, path.name, result & 0xffffffff))
    checked(ole.CoInitializeEx(None, 2), 'COM initialization')
    obj = C.c_void_p()
    release = None
    try:
        checked(ole.CoCreateInstance(guid('4125dd96-e03a-4103-8f70-e0597d803b9c'), None, 1,
            guid('73db1241-1e85-4581-8e4f-a81e1d0f8c57'), C.byref(obj)), 'Attachment Services')
        table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
        release = C.WINFUNCTYPE(C.c_ulong, C.c_void_p)(table[2])
        for index, value in ((3, 'FreeVideo release validation'), (5, str(path.resolve())),
                             (7, 'https://github.com/FlashML-org/FreeVideo/releases/download/windows-preview/' + path.name)):
            call = C.WINFUNCTYPE(C.c_long, C.c_void_p, C.c_wchar_p)(table[index])
            checked(call(obj, value), 'Attachment metadata')
        save = C.WINFUNCTYPE(C.c_long, C.c_void_p)(table[11])
        checked(save(obj), 'IAttachmentExecute::Save')
    finally:
        if release is not None:
            release(obj)
        ole.CoUninitialize()


def unpack_folder(archive, destination):
    from pathlib import PureWindowsPath
    with zipfile.ZipFile(archive) as stream:
        for member in stream.infolist():
            path = PureWindowsPath(member.filename)
            if path.drive or path.root or '..' in path.parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe path in launcher ZIP')
        stream.extractall(destination)
    executable = destination / 'FreeVideo/FreeVideo.exe'
    if not executable.is_file():
        raise ValueError('Launcher ZIP does not contain FreeVideo/FreeVideo.exe')
    return executable


def verify_launches(onefile, folder_zip, program, report, logs, *, blocked_layouts=()):
    """Test final bytes outside build exclusions, with download zone and live AV."""
    downloads = Path(os.environ['USERPROFILE']) / 'Downloads'
    downloads.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='FreeVideo-release-check-', dir=downloads))
    report['launch_checks'] = dict(directory=str(root), internet_zone=3, attachments=[], cases=[])
    excluded = command([program, '-CheckExclusion', '-Path', root], logs/'defender-exclusions.log')
    report['launch_checks']['exclusion_exit_code'] = excluded
    if excluded != 1:
        raise RuntimeError('Download test directory is excluded or its protection could not be checked')
    for label, source, target in (('onefile', onefile, root/'FreeVideo.exe'),
                                   ('folder', folder_zip, root/'FreeVideo-Windows-folder.zip')):
        row = dict(layout=label, status='pending')
        report['launch_checks']['cases'].append(row)
        if label in blocked_layouts:
            row.update(status='blocked', error='Static scan rejected this package; it was not opened')
            continue
        # A rejection stays a failure. The other independently built layout
        # is still checked so a onefile detection cannot hide the ZIP result.
        attachment = None
        try:
            shutil.copyfile(source, target)
            mark_download(target)
            if digest(source) != digest(target):
                raise RuntimeError('Downloaded test package differs from release bytes')
            attachment = dict(name=target.name, sha256=digest(target), status='pending')
            report['launch_checks']['attachments'].append(attachment)
            attachment_check(target)
            if not target.is_file() or digest(target) != attachment['sha256']:
                raise RuntimeError('Attachment security removed or changed ' + target.name)
            attachment['status'] = 'passed'
            executable = target
            if label == 'folder':
                executable = unpack_folder(target, root)
                for path in (root/'FreeVideo').rglob('*'):
                    if path.is_file():
                        mark_download(path)
            row['sha256'] = digest(executable)
            out = root/('smoke-'+label)
            environment = dict(os.environ, FREEVIDEO_PYTHON=sys.executable)
            started = time.monotonic()
            result = subprocess.run([str(executable), '--smoke-test', str(out)], env=environment, timeout=300)
            row.update(exit_code=result.returncode, seconds=time.monotonic()-started)
            payload = json.loads((out/'smoke.json').read_text(encoding='utf-8'))
            reopened = payload.get('payload', {}).get('reopen', {})
            if (result.returncode != 0 or payload.get('success') is not True or payload.get('model_modules_imported')
                    or not reopened.get('installed_history') or not reopened.get('process_inspection')):
                row['smoke'] = payload
                raise RuntimeError('Downloaded launcher failed its startup/resource check')
            if not executable.is_file() or digest(executable) != row['sha256']:
                raise RuntimeError('Defender removed or changed the launcher during startup')
            row['status'] = 'passed'
        except Exception as error:
            row.update(status='blocked', error=str(error))
            if attachment is not None and attachment['status'] == 'pending':
                attachment.update(status='blocked', error=str(error))
    failures = [row['layout'] + ': ' + row['error'] for row in report['launch_checks']['cases'] if row['status'] != 'passed']
    if failures:
        raise RuntimeError('; '.join(failures))
    report['defender_after_launch'] = metadata()
    require_realtime(report['defender_after_launch'])


def scan(files, report_path, *, launches=None, scripts=()):
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(schema=1, status='error', started_at=time.time(), files=[], scripts=[],
                  scope='Microsoft Defender custom file/archive scan; not a SmartScreen or malware-free certification')
    try:
        if platform.system() != 'Windows':
            raise RuntimeError('Release antivirus scanning requires native Windows')
        program = scanner()
        if program is None:
            raise RuntimeError('Microsoft Defender scanner is unavailable; scan inconclusive')
        report['scanner'] = str(program)
        if launches:
            report['detections_before'] = threat_history()
            report['defender_before'] = metadata()
            report['defender_prepared'] = enable_realtime()
            report['scope'] += '; Internet-zone files and extracted ZIP launched with real-time/cloud protection'
        if not files:
            raise ValueError('No release files selected')
        for path in map(Path, files):
            if not path.is_file() or path.is_symlink():
                raise ValueError('Release file is missing or linked: ' + str(path))
            report['files'].append(dict(name=path.name, path=str(path.resolve()), bytes=path.stat().st_size,
                                        sha256=digest(path), status='pending'))
        update_log = report_path.parent/'defender-update.log'
        report['update_log'] = update_log.name
        report['update_exit_code'] = command([program, '-SignatureUpdate'], update_log)
        if report['update_exit_code'] != 0:
            raise RuntimeError('Defender signature update failed; see ' + str(update_log))
        report['defender'] = metadata()
        info = report['defender']
        if (not info.get('signatures') or type(info.get('signature_age_days')) is not int
                or not 0 <= info['signature_age_days'] <= 2 or not info.get('service_enabled')):
            raise RuntimeError('Defender service or current signatures are unavailable; scan inconclusive')
        if launches or scripts:
            # Defender answers AMSI from its real-time engine; with protection off every script reads as clean.
            require_realtime(info)
        if launches:
            report['maps_exit_code'] = command([program, '-ValidateMapsConnection'], report_path.parent/'defender-maps.log')
            if report['maps_exit_code'] != 0:
                raise RuntimeError('Defender cloud connection unavailable; release validation is inconclusive')
        for index, row in enumerate(report['files']):
            path = Path(row['path'])
            log = report_path.parent/('defender-scan-%d.log' % index)
            row['log'] = log.name
            row['exit_code'] = command([program, '-Scan', '-ScanType', '3', '-File', path, '-DisableRemediation'], log)
            unchanged = path.is_file() and path.stat().st_size == row['bytes'] and digest(path) == row['sha256']
            row['status'] = 'clean' if row['exit_code'] == 0 and unchanged else 'blocked'
        if scripts:
            for path, verdict in amsi_verdicts(scripts).items():
                report['scripts'].append(dict(path=path, amsi_result=verdict,
                                              status='clean' if verdict < AMSI_BLOCKED else 'blocked'))
        blocked = [row for row in report['files'] + report['scripts'] if row['status'] != 'clean']
        if launches:
            blocked_layouts = {'onefile' if Path(row['path']) == Path(launches[0]).resolve() else 'folder'
                               for row in report['files'] if row['status'] != 'clean'}
            verify_launches(*map(Path, launches), program, report, report_path.parent, blocked_layouts=blocked_layouts)
            report['detections'] = threat_history()
            previous = {row['id'] for row in report['detections_before']}
            report['new_detections'] = [row for row in report['detections'] if row['id'] not in previous]
            if report['new_detections']:
                raise RuntimeError('Defender reported a new threat during release validation; publication blocked')
            for row in report['files']:
                path = Path(row['path'])
                if not path.is_file() or digest(path) != row['sha256']:
                    raise RuntimeError('A final release file was removed or changed during live protection checks')
        if blocked:
            raise RuntimeError('Defender detected a threat, could not scan, or the release changed. See retained scan logs.')
        report['status'] = 'clean'
        return report
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if launches and platform.system() == 'Windows':
            try:
                if 'detections' not in report:
                    report['detections'] = threat_history()
                report['defender_final'] = metadata()
                if report['detections']:
                    # Preserve evidence even when a deleted file prevented startup.
                    report['scope'] += '; detections include this disposable runner history'
            except Exception as error:
                report['detection_history_error'] = str(error)
        report['finished_at'] = time.time()
        temporary = report_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(report_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--script', type=Path, action='append', default=[],
                        help='PowerShell script to check with AMSI; may be repeated')
    parser.add_argument('--verify-launches', nargs=2, type=Path, metavar=('EXE', 'FOLDER_ZIP'),
                        help='Enable real-time/cloud Defender on this test machine and open Internet-zone copies')
    args = parser.parse_args()
    result = scan(args.file, args.report, launches=args.verify_launches, scripts=args.script)
    print('Defender scan completed for %d release files and %d scripts. Report: %s'
          % (len(result['files']), len(result['scripts']), args.report))


if __name__ == '__main__':
    main()
