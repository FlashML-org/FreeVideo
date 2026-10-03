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
            $env:UV_HTTP_TIMEOUT = '20'
            $env:UV_HTTP_RETRIES = '1'
            $env:UV_NO_CONFIG = '1'
            $installed = $false
            $pythonRoutes = @($routes | ForEach-Object { "$_/astral-sh/python-build-standalone/releases/download" })
            if (-not $officialOnly -and $freevideoSavedEnvironment['UV_PYTHON_INSTALL_MIRROR']) {
                $pythonRoutes = @($freevideoSavedEnvironment['UV_PYTHON_INSTALL_MIRROR']) + $pythonRoutes
            }
            $attempt = 0
            foreach ($route in $pythonRoutes) {
              foreach ($proxyRoute in $proxyRoutes) {
                if ($installed) { break }
                $attempt++
                $env:UV_PYTHON_INSTALL_MIRROR = $route
                Write-Progress -Activity 'FreeVideo bootstrap' -Status ('Install isolated Python ' + $versions.python)
                $pythonExit = Invoke-FreeVideoBootstrapCommand -Executable $uv `
                    -Arguments @('python', 'install', '--no-bin', '--no-registry', '--no-progress', '--color', 'never', $versions.python) `
                    -LogPath $bootstrapLog -Label ("Install Python $($versions.python), attempt $attempt ($proxyRoute connection)") `
                    -Direct:($proxyRoute -eq 'direct')
                if ($pythonExit -eq 0) { $installed = $true; break }
                Write-Host "Python install source $attempt returned exit code $pythonExit. Details retained in $bootstrapLog"
              }
            }
            if (-not $installed) { throw "Python installation failed (uv exit $pythonExit). Check your network or connection mode in Downloads. See $bootstrapLog and retry." }
            $freevideoPython = (& $uv python find --managed-python $versions.python | Select-Object -Last 1).Trim()
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $freevideoPython)) { throw 'Managed Python could not be located.' }
        } finally {
            $bootstrapLock.Dispose()
            Write-Progress -Activity 'FreeVideo bootstrap' -Completed
        }
    }
    & $freevideoPython @freevideoPythonPrefix -B -X utf8 -m freevideo_engine.managed @freevideoArguments
    $freevideoExit = $LASTEXITCODE
} catch {
    Write-Host ("FreeVideo could not start: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Existing files are retained. Retry the same command after resolving the reported issue.'
} finally {
    [Console]::OutputEncoding = $freevideoConsoleEncoding
    foreach ($name in $freevideoSavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $freevideoSavedEnvironment[$name], 'Process')
    }
}
exit $freevideoExit
