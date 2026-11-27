# Windows PowerShell 5.1 / PowerShell 7. No activation or machine-wide PATH changes.
$ErrorActionPreference = 'Stop'
$freevideoArguments = @($args)
$freevideoExit = 1
$freevideoConsoleEncoding = [Console]::OutputEncoding
$freevideoSavedEnvironment = @{}
foreach ($name in @('PYTHONPATH', 'PYTHONUTF8', 'PYTHONIOENCODING', 'UV_PYTHON_INSTALL_DIR',
                    'UV_PYTHON_INSTALL_MIRROR', 'UV_HTTP_TIMEOUT', 'UV_HTTP_RETRIES', 'UV_NO_CONFIG',
                    'FREEVIDEO_HOME', 'FREEVIDEO_BOOTSTRAP_ROOT', 'FREEVIDEO_PROXY_MODE')) {
    $freevideoSavedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'Use native Windows x64, or the Linux ./freevideo entry point.'
    }
    . (Join-Path $PSScriptRoot 'freevideo_engine\windows_bootstrap.ps1')
    $env:PYTHONPATH = $PSScriptRoot
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $freevideoRoot = if ($env:FREEVIDEO_HOME) { $env:FREEVIDEO_HOME } elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'launcher-source.json')) {
        Join-Path $env:LOCALAPPDATA 'FreeVideo'
    } else { $PSScriptRoot }
    for ($i = 0; $i -lt $freevideoArguments.Count; $i++) {
        if ($freevideoArguments[$i] -eq '--root' -and $i + 1 -lt $freevideoArguments.Count) {
            $freevideoRoot = $freevideoArguments[$i + 1]
        } elseif ($freevideoArguments[$i].StartsWith('--root=')) {
            $freevideoRoot = $freevideoArguments[$i].Substring(7)
        }
    }
    $env:FREEVIDEO_HOME = $freevideoRoot
    $downloadSettings = Join-Path $freevideoRoot 'download-settings.json'
    if (Test-Path -LiteralPath $downloadSettings) {
        $preferences = Get-Content -LiteralPath $downloadSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($preferences.proxy_mode) { $env:FREEVIDEO_PROXY_MODE = $preferences.proxy_mode }
    }
    if ($env:FREEVIDEO_PROXY_MODE -and $env:FREEVIDEO_PROXY_MODE -notin @('auto', 'proxy', 'direct')) {
        throw 'Invalid download connection mode.'
    }
    $bootstrapRoot = if ($env:FREEVIDEO_BOOTSTRAP_ROOT) { $env:FREEVIDEO_BOOTSTRAP_ROOT } else { Join-Path $freevideoRoot '.freevideo\bootstrap' }
    $env:FREEVIDEO_BOOTSTRAP_ROOT = $bootstrapRoot
    $freevideoPython = $null
    $freevideoPythonPrefix = @()
    $freevideoCandidates = @()
    if ($env:FREEVIDEO_PYTHON) { $freevideoCandidates += ,@($env:FREEVIDEO_PYTHON) }
    $freevideoCandidates += ,@((Join-Path $freevideoRoot 'envs\unified\Scripts\python.exe'))
    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand) { $freevideoCandidates += ,@($pyCommand.Source, '-3') }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') {
            $freevideoCandidates += ,@($command.Source)
        }
    }
    if ($env:FREEVIDEO_FORCE_BOOTSTRAP -ne '1') {
        foreach ($candidate in $freevideoCandidates) {
            if (-not (Test-Path -LiteralPath $candidate[0] -PathType Leaf)) { continue }
            $prefix = @($candidate | Select-Object -Skip 1)
            try {
                & $candidate[0] @prefix -B -X utf8 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $freevideoPython = $candidate[0]
                    $freevideoPythonPrefix = $prefix
                    break
                }
            } catch { continue }
        }
    }
    if (-not $freevideoPython) {
        # Keep the bootstrap inside the selected installation, including source clones.
        # The Python setup still reviews hardware, resources and licenses before
        # creating an environment or downloading any models.
        [IO.Directory]::CreateDirectory($bootstrapRoot) | Out-Null
        $bootstrapLog = Join-Path $bootstrapRoot 'bootstrap.log'
        Write-Host 'Preparing isolated Python for setup. Model/environment installation requires the setup confirmation.'
        Write-Host "Bootstrap cache and log: $bootstrapRoot"
        $bootstrapLock = [IO.File]::Open((Join-Path $bootstrapRoot 'bootstrap.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        $patientDownloadNotice = $false
        try {
            $versions = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'freevideo_engine\bootstrap_versions.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $uvSpec = $versions.windows.uv
            $officialOnly = $freevideoArguments -contains '--network=official'
            for ($i = 0; $i -lt $freevideoArguments.Count - 1; $i++) {
                if ($freevideoArguments[$i] -eq '--network' -and $freevideoArguments[$i + 1] -eq 'official') { $officialOnly = $true }
            }
            $routes = @('https://github.com')
            if (-not $officialOnly) { $routes += 'https://ghfast.top/https://github.com' }
            $proxyRoutes = @('inherited')
            if ($env:FREEVIDEO_PROXY_MODE -eq 'direct') { $proxyRoutes = @('direct') }
            elseif ($env:FREEVIDEO_PROXY_MODE -ne 'proxy' -and ($env:HTTP_PROXY -or $env:HTTPS_PROXY -or $env:ALL_PROXY)) { $proxyRoutes += 'direct' }
            $uvCatalog = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'freevideo_engine\uv_downloads.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $uv = Get-FreeVideoUv -Spec $uvSpec -Catalog $uvCatalog -BootstrapRoot $bootstrapRoot `
                -EngineRoot $freevideoRoot -LogPath $bootstrapLog -GithubSources $routes `
                -ProxyRoutes $proxyRoutes -OfficialOnly:$officialOnly
            $env:UV_PYTHON_INSTALL_DIR = Join-Path $bootstrapRoot 'python'
            $env:UV_NO_CONFIG = '1'
            # Isolated Python: begin (scripts/test_windows_python_recover.ps1 runs this part with stand-ins for uv and Python)
            # An isolated Python already here whose python.exe is gone or does not answer (security software): uv still
            # calls it installed, and every step after fails on it. It is installed again, once: before the download when
            # it is found broken, or after it when it breaks at once. managed_python.py keeps the same rules for setup.
            $pythonReinstall = @()
            if (Get-ChildItem -LiteralPath $env:UV_PYTHON_INSTALL_DIR -Directory -Filter ('cpython-' + $versions.python + '-*') -ErrorAction SilentlyContinue) {
                $pythonState = Get-FreeVideoManagedPythonProblem -Uv $uv -Version $versions.python -LogPath $bootstrapLog
                if ($pythonState.Kind) {
                    Write-FreeVideoPythonReinstall -State $pythonState -LogPath $bootstrapLog
                    $pythonReinstall = @('--reinstall')
                }
            }
            # The download below runs a second time only to install a Python that broke at once again.
            foreach ($pythonRound in @(1, 2)) {
            $installed = $false
            # Python sources: begin (scripts/test_windows_python_sources.ps1 runs this block with a stand-in for uv)
            # The GitHub mirror (ghfast) refuses the Python runtime files (403), so it is not a Python source:
            # GitHub, then the public npmmirror copy (same SHA-256 that uv pins). Keep in sync with
            # bootstrap.python_sources.
            $pythonRoutes = @('https://github.com/astral-sh/python-build-standalone/releases/download')
            if (-not $officialOnly) { $pythonRoutes += 'https://registry.npmmirror.com/-/binary/python-build-standalone' }
            if (-not $officialOnly -and $freevideoSavedEnvironment['UV_PYTHON_INSTALL_MIRROR']) {
                $pythonRoutes = @($freevideoSavedEnvironment['UV_PYTHON_INSTALL_MIRROR']) + $pythonRoutes
            }
            # Security software or a policy that stops a Python file from being written: the same table as the launcher.
            $blockedTable = Get-FreeVideoBlockedTable (Join-Path $PSScriptRoot 'freevideo_engine\windows_blocked.json')
            $attempt = 0
            $otherFailures = $false
            $slowRoutes = @{}
            $deniedRoutes = @{}
            $blockedRoutes = @{}
            $sourceResults = @()
            # Keep these defaults and the notice in sync with network.py (CPU tests).
            $pythonRetryProfiles = @(
                @{Mode='fast'; UV_HTTP_TIMEOUT='20'; UV_HTTP_RETRIES='1'},
                @{Mode='patient'; UV_HTTP_TIMEOUT='120'; UV_HTTP_RETRIES='5'}
            )
            foreach ($profile in $pythonRetryProfiles) {
              if ($installed) { break }
              # A source that refused access (HTTP 401/403) or whose file Windows blocked says nothing
              # about a source that timed out: neither cancels its slow retry.
              if ($profile.Mode -eq 'patient') {
                if ($otherFailures) { break }
                $retryRoutes = @($slowRoutes.Keys | Where-Object { -not $deniedRoutes.ContainsKey($_) -and -not $blockedRoutes.ContainsKey($_) })
                if ($retryRoutes.Count -eq 0) { break }
                Write-Host 'The network is slow. Downloading again; this may take a while.'
                $patientDownloadNotice = $true
                if ($env:FREEVIDEO_UI_EVENTS -eq '1') {
                    Write-Host '{"event":"freevideo_ui","kind":"progress","key":"download-patient-python","label":"Download","detail":"The network is slow. Downloading again; this may take a while."}'
                }
              }
              foreach ($name in @('UV_HTTP_TIMEOUT', 'UV_HTTP_RETRIES')) {
                $value = $freevideoSavedEnvironment[$name]
                if ($null -eq $value) { $value = $profile[$name] }
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
              }
              foreach ($route in $pythonRoutes) {
                if ($profile.Mode -eq 'patient' -and ($deniedRoutes.ContainsKey($route) -or $blockedRoutes.ContainsKey($route))) { continue }
                foreach ($proxyRoute in $proxyRoutes) {
                  if ($installed) { break }
                  $attempt++
                  $env:UV_PYTHON_INSTALL_MIRROR = $route
                  Write-Progress -Activity 'FreeVideo bootstrap' -Status ('Install isolated Python ' + $versions.python)
                  $pythonOutput = ''
                  $pythonLabel = "Install Python $($versions.python), attempt $attempt ($proxyRoute connection)"
                  if ($profile.Mode -eq 'patient') { $pythonLabel += ", $($profile.Mode)" }
                  $pythonExit = Invoke-FreeVideoBootstrapCommand -Executable $uv `
                      -Arguments (@('python', 'install', '--no-bin', '--no-registry', '--no-progress', '--color', 'never', $versions.python) + $pythonReinstall) `
                      -LogPath $bootstrapLog -Label $pythonLabel `
                      -Direct:($proxyRoute -eq 'direct') -OutputTail ([ref]$pythonOutput)
                  if ($pythonExit -ne 0 -and (Test-FreeVideoCertificateFailure $pythonOutput)) {
                      $originalOutput = $pythonOutput
                      $originalExit = $pythonExit
                      $retryOutput = ''
                      $retryExit = Invoke-FreeVideoBootstrapCommand -Executable $uv `
                          -Arguments (@('--native-tls', 'python', 'install', '--no-bin', '--no-registry', '--no-progress', '--color', 'never', $versions.python) + $pythonReinstall) `
                          -LogPath $bootstrapLog -Label ($pythonLabel + ', system certificates') `
                          -Direct:($proxyRoute -eq 'direct') -OutputTail ([ref]$retryOutput)
                      if ($retryExit -eq 0) {
                          $pythonExit = 0
                          $pythonOutput = $retryOutput
                      } else {
                          $pythonExit = $originalExit
                          $pythonOutput = $originalOutput
                      }
                  }
                  # The download arrived but Windows did not let uv write a file of it (security software
                  # still examining it, or another program holding it): the same source again after each wait.
                  $blocked = $null
                  if ($pythonExit -ne 0) { $blocked = Get-FreeVideoUnpackBlocked $pythonOutput $blockedTable }
                  foreach ($wait in $blockedTable.Waits) {
                      if (-not $blocked -or $blockedTable.Retry -notcontains $blocked.Code) { break }
                      Write-Host "Windows did not let setup write $($blocked.File) (Windows error $($blocked.Code)). Trying the same source again in $wait s."
                      Start-Sleep -Seconds $wait
                      $pythonOutput = ''
                      $pythonExit = Invoke-FreeVideoBootstrapCommand -Executable $uv `
                          -Arguments (@('python', 'install', '--no-bin', '--no-registry', '--no-progress', '--color', 'never', $versions.python) + $pythonReinstall) `
                          -LogPath $bootstrapLog -Label ($pythonLabel + ", again after $wait s") `
                          -Direct:($proxyRoute -eq 'direct') -OutputTail ([ref]$pythonOutput)
                      $blocked = $null
                      if ($pythonExit -ne 0) { $blocked = Get-FreeVideoUnpackBlocked $pythonOutput $blockedTable }
                  }
                  if ($pythonExit -eq 0) { $installed = $true; break }
                  if ($blocked) { $blockedRoutes[$route] = $true; $kind = 'blocked' }
                  elseif (Test-FreeVideoAccessDenied $pythonOutput) { $deniedRoutes[$route] = $true; $kind = 'refused' }
                  elseif (Test-FreeVideoSlowDownloadFailure $pythonOutput) { $slowRoutes[$route] = $true; $kind = 'slow' }
                  else { $otherFailures = $true; $kind = 'other' }
                  Write-Host "Python install source $attempt returned exit code $pythonExit. Details retained in $bootstrapLog"
                  $pythonReason = Get-FreeVideoFailureReason $pythonOutput
                  if ($pythonReason) { Write-Host "uv reported: $pythonReason" }
                  $sourceHost = ''
                  try { $sourceHost = ([Uri]$route).Host } catch { $sourceHost = 'custom' }
                  $sourceResults += ,@{Attempt = $attempt; Host = $sourceHost; Kind = $kind; Exit = $pythonExit; Reason = $pythonReason; Blocked = $blocked}
                }
              }
            }
            if (-not $installed) {
                # Every source's result reaches the console (and so the launcher's report) and the log; the
                # message leads with the failure that matters most: a file Windows blocked on this computer,
                # then any other error, then a slow network, then a source that refused access.
                $resultsLine = 'Python install sources: ' + (($sourceResults | ForEach-Object { "$($_.Attempt) $($_.Host) $($_.Kind)" }) -join '; ')
                Write-Host $resultsLine
                [IO.File]::AppendAllText($bootstrapLog, ('[' + [DateTime]::UtcNow.ToString('o') + '] ' + $resultsLine + [Environment]::NewLine), (New-Object System.Text.UTF8Encoding($false)))
                $lead = $null
                foreach ($kind in @('blocked', 'other', 'slow', 'refused')) {
                    $lead = $sourceResults | Where-Object { $_.Kind -eq $kind } | Select-Object -First 1
                    if ($lead) { break }
                }
                if (-not $lead) { throw "Python installation failed (uv exit $pythonExit). Check your network or connection mode in Downloads. See $bootstrapLog and retry." }
                if ($lead.Kind -eq 'blocked') {
                    # failure_details reads the marker and the reason in brackets (windows_blocked.python_file_message).
                    throw ("Python installation failed: Windows did not let setup write a Python file [PYTHON_FILE_BLOCKED $($lead.Blocked.Reason)] (Windows error $($lead.Blocked.Code)): $($lead.Blocked.File). " +
                           "Allow the FreeVideo folder in the security software, then retry. See $bootstrapLog.")
                }
                $leadDetail = ''
                if ($lead.Reason) { $leadDetail = " uv reported for source $($lead.Attempt) ($($lead.Host)): $($lead.Reason)." }
                if ($lead.Kind -eq 'slow' -or $lead.Kind -eq 'refused') {
                    # Every source failed on the network (nothing blocked, no other error): the launcher's download card,
                    # with its source switch, reads this sentence (failure_details, as for setup in Python).
                    throw "Python download failed on every route (uv exit $($lead.Exit)).$leadDetail Check your network or connection mode in Downloads. See $bootstrapLog and retry."
                }
                throw "Python installation failed (uv exit $($lead.Exit)).$leadDetail Check your network or connection mode in Downloads. See $bootstrapLog and retry."
            }
            # Python sources: end
            $pythonState = Get-FreeVideoManagedPythonProblem -Uv $uv -Version $versions.python -LogPath $bootstrapLog
            if (-not $pythonState.Kind) { break }
            if ($pythonReinstall.Count) { throw (Get-FreeVideoManagedPythonMessage -State $pythonState -LogPath $bootstrapLog) }
            Write-FreeVideoPythonReinstall -State $pythonState -LogPath $bootstrapLog
            $pythonReinstall = @('--reinstall')
            }
            $freevideoPython = $pythonState.Path
            # Isolated Python: end
        } finally {
            if ($patientDownloadNotice -and $env:FREEVIDEO_UI_EVENTS -eq '1') {
                Write-Host '{"event":"freevideo_ui","kind":"progress","key":"download-patient-python","label":"Download","detail":"","state":"complete"}'
            }
            $bootstrapLock.Dispose()
            Write-Progress -Activity 'FreeVideo bootstrap' -Completed
        }
    }
    # Restore only our download defaults before Python determines user overrides.
    # A null saved value removes the variable from the child environment.
    foreach ($name in @('UV_HTTP_TIMEOUT', 'UV_HTTP_RETRIES')) {
        [Environment]::SetEnvironmentVariable($name, $freevideoSavedEnvironment[$name], 'Process')
    }
    & $freevideoPython @freevideoPythonPrefix -B -X utf8 (Join-Path $PSScriptRoot 'freevideo_engine\installed_entry.py') freevideo_engine.managed @freevideoArguments
    $freevideoExit = $LASTEXITCODE
    if ($freevideoExit -gt 127 -or $freevideoExit -lt 0) {
        # A crash or a blocked start prints nothing, so the exit code was all a report held.
        # The note is best effort and never changes the exit code.
        $freevideoDiagErrorMode = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $freevideoDiagHex = '0x{0:X8}' -f [BitConverter]::ToUInt32([BitConverter]::GetBytes([int]$freevideoExit), 0)
            $freevideoDiagPresent = Test-Path -LiteralPath $freevideoPython -PathType Leaf
            $freevideoDiagProbe = 'not run'
            if ($freevideoDiagPresent) {
                $freevideoDiagText = (& $freevideoPython @freevideoPythonPrefix -B -X utf8 -c 'import sys; print(sys.version.split()[0])' 2>&1 | Select-Object -First 1 | Out-String).Trim()
                if ($freevideoDiagText.Length -gt 80) { $freevideoDiagText = $freevideoDiagText.Substring(0, 80) }
                $freevideoDiagProbe = 'exit code ' + $LASTEXITCODE + ', printed: ' + $freevideoDiagText
            }
            $freevideoDiagNote = 'The setup step ended with exit code ' + $freevideoExit + ' (' + $freevideoDiagHex + '). Python file present: ' + $freevideoDiagPresent + '; a short check of it ended with ' + $freevideoDiagProbe + '; Python path: ' + $freevideoPython
            Write-Host $freevideoDiagNote
            [IO.Directory]::CreateDirectory($bootstrapRoot) | Out-Null
            [IO.File]::AppendAllText((Join-Path $bootstrapRoot 'bootstrap.log'), ('[' + [DateTime]::UtcNow.ToString('o') + '] ' + $freevideoDiagNote + [Environment]::NewLine), (New-Object System.Text.UTF8Encoding($false)))
        } catch {
        } finally {
            $ErrorActionPreference = $freevideoDiagErrorMode
        }
    }
} catch {
    Write-Host ("FreeVideo could not start: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Existing files are retained. Retry the same command after resolving the reported issue.'
    $freevideoReportLog = ''
    if ($bootstrapLog -and (Test-Path -LiteralPath $bootstrapLog)) { $freevideoReportLog = " and attach $bootstrapLog" }
    Write-Host ("To report it: open a new issue at https://github.com/FlashML-org/FreeVideo/issues/new with this message" + $freevideoReportLog + '.')
} finally {
    [Console]::OutputEncoding = $freevideoConsoleEncoding
    foreach ($name in $freevideoSavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $freevideoSavedEnvironment[$name], 'Process')
    }
}
exit $freevideoExit
