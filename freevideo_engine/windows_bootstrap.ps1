# Shared by the launcher and CPU checks. Compatible with PowerShell 5.1 and 7.
function Invoke-FreeVideoBootstrapCommand {
    param(
        [Parameter(Mandatory=$true)][string]$Executable,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [Parameter(Mandatory=$true)][string]$LogPath,
        [Parameter(Mandatory=$true)][string]$Label,
        [switch]$Direct
    )
    # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecords.
    # uv writes normal progress there. Let the child finish and inspect its
    # exit code; keep these preferences local to this function.
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    $encoding = [Text.UTF8Encoding]::new($false)
    if (Test-Path -LiteralPath $LogPath) {
        # Preserve older PowerShell 5.1 UTF-16 logs when appending a retry.
        $reader = [IO.StreamReader]::new($LogPath, $encoding, $true)
        try {
            [void]$reader.Peek()
            $encoding = $reader.CurrentEncoding
        } finally { $reader.Dispose() }
    }
    $log = [IO.StreamWriter]::new($LogPath, $true, $encoding)
    $log.AutoFlush = $true
    $previousExitCode = $global:LASTEXITCODE
    $savedProxy = @{}
    if ($Direct) {
        foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')) {
            $savedProxy[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        [Environment]::SetEnvironmentVariable('NO_PROXY', '*', 'Process')
    }
    try {
        # Native commands update the global automatic variable. A local
        # LASTEXITCODE would shadow their result and keep a stale/null value.
        $global:LASTEXITCODE = $null
        $log.WriteLine(('[{0}] {1}' -f [DateTime]::UtcNow.ToString('o'), $Label))
        & $Executable @Arguments 2>&1 | ForEach-Object { $log.WriteLine($_.ToString()) }
        $nativeExit = $global:LASTEXITCODE
        if ($null -eq $nativeExit) { throw "Native command did not return an exit code: $Executable" }
        $log.WriteLine("Exit code: $nativeExit")
        return [int]$nativeExit
    } catch {
        $log.WriteLine('Invocation failed: ' + $_.Exception.Message)
        throw
    } finally {
        foreach ($name in $savedProxy.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedProxy[$name], 'Process')
        }
        $global:LASTEXITCODE = $previousExitCode
        $log.Dispose()
    }
}

function Test-FreeVideoUvArchive {
    param([string]$Path, [string]$Hash, [long]$Bytes)
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and
        (Get-Item -LiteralPath $Path).Length -eq $Bytes -and
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Hash)
}

function Expand-FreeVideoUv {
    param([string]$Archive, [string]$Member, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Destination)) | Out-Null
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        $entry = $zip.GetEntry($Member)
        if (-not $entry) { throw 'The verified uv archive is missing its executable' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, "$Destination.partial", $true)
    } finally { $zip.Dispose() }
    Move-Item -LiteralPath "$Destination.partial" -Destination $Destination -Force
    return $Destination
}

function Get-FreeVideoUv {
    param($Spec, $Catalog, [string]$BootstrapRoot, [string]$EngineRoot,
          [string]$LogPath, [string[]]$GithubSources, [string[]]$ProxyRoutes,
          [switch]$OfficialOnly)
    $wheel = $Catalog.windows
    if ($Catalog.version -ne $Spec.version -or $wheel.legacy_sha256 -ne $Spec.sha256) {
        throw 'The uv download catalog does not match the pinned bootstrap version'
    }
    $destination = Join-Path $BootstrapRoot ('uv-' + $Spec.version + '\uv.exe')
    $artifacts = @(
        [pscustomobject]@{Name=$wheel.filename; Hash=$wheel.sha256; Bytes=$wheel.bytes; Member=$wheel.executable;
            Paths=@((Join-Path $BootstrapRoot $wheel.filename), (Join-Path $EngineRoot ('downloads\' + $wheel.filename)))},
        [pscustomobject]@{Name='uv.zip'; Hash=$Spec.sha256; Bytes=$wheel.legacy_bytes; Member=$Spec.executable;
            Paths=@((Join-Path $BootstrapRoot 'uv.zip'), (Join-Path $EngineRoot 'downloads\uv-windows.zip'))}
    )
    foreach ($artifact in $artifacts) {
        foreach ($path in $artifact.Paths) {
            foreach ($candidate in @($path, "$path.partial")) {
                if (Test-FreeVideoUvArchive $candidate $artifact.Hash $artifact.Bytes) {
                    Write-Host 'Using verified local download tools'
                    return Expand-FreeVideoUv $candidate $artifact.Member $destination
                }
            }
        }
    }
    $curlPath = $env:FREEVIDEO_CURL
    if (-not $curlPath -or -not (Test-Path -LiteralPath $curlPath -PathType Leaf)) {
        $curlCommand = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue
        $curlPath = if ($curlCommand) { $curlCommand.Source } else { $null }
    }
    if (-not $curlPath) {
        foreach ($directory in @('Sysnative', 'System32')) {
            $candidate = Join-Path $env:SystemRoot ($directory + '\curl.exe')
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $curlPath = $candidate; break }
        }
    }
    if (-not $curlPath) { throw '[CURL_NOT_FOUND] No Windows curl.exe found in PATH or the system directory. Open FreeVideo.exe and retry installation to repair the tool automatically.' }
    $common = @('--disable', '--fail', '--location', '--silent', '--show-error',
                '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '5')
    $candidates = @()
    Write-Host 'Checking download tool sources'
    foreach ($source in $wheel.sources.PSObject.Properties) {
        if ($OfficialOnly -and $source.Name -ne 'official') { continue }
        foreach ($route in $ProxyRoutes) {
            # A small, bounded sample compares actual artifact delivery; no
            # region lookup or machine-wide proxy changes are involved.
            $sample = Join-Path $BootstrapRoot ('uv-probe-' + [Guid]::NewGuid().ToString('N'))
            $score = 0.0
            try {
                $watch = [Diagnostics.Stopwatch]::StartNew()
                $probeArgs = $common + @('--range', '0-262143', '--max-filesize', '1048576',
                                        '--max-time', '5', '--output', $sample, $source.Value)
                $code = Invoke-FreeVideoBootstrapCommand -Executable $curlPath -Arguments $probeArgs `
                    -LogPath $LogPath -Label ("Probe uv: {0}, {1}" -f $source.Name, $route) -Direct:($route -eq 'direct')
                $watch.Stop()
                if ($code -eq 0 -and (Test-Path -LiteralPath $sample)) {
                    $score = (Get-Item -LiteralPath $sample).Length / [Math]::Max(0.001, $watch.Elapsed.TotalSeconds)
                }
            } finally {
                if (Test-Path -LiteralPath $sample) { Remove-Item -LiteralPath $sample }
            }
            $candidates += [pscustomobject]@{Source=$source.Name; Route=$route; Url=$source.Value; Score=$score; Artifact=$artifacts[0]}
        }
    }
    $candidates = @($candidates | Sort-Object -Property @{Expression={$_.Score}; Descending=$true})
    # Release ZIPs remain a fallback and use a separate partial/cache file.
    foreach ($source in $GithubSources) {
        foreach ($route in $ProxyRoutes) {
            $candidates += [pscustomobject]@{Source='GitHub'; Route=$route;
                Url=$Spec.url.Replace('https://github.com', $source); Artifact=$artifacts[1]}
        }
    }
    foreach ($candidate in $candidates) {
        $artifact = $candidate.Artifact
        $archive = Join-Path $BootstrapRoot $artifact.Name
        $partial = "$archive.partial"
        if (Test-Path -LiteralPath $archive) {
            Move-Item -LiteralPath $archive -Destination "$archive.rejected-$([DateTime]::UtcNow.Ticks)"
        }
        for ($restart = 0; $restart -lt 2; $restart++) {
            $offset = if (Test-Path -LiteralPath $partial) { (Get-Item -LiteralPath $partial).Length } else { 0 }
            if ($offset -ge $artifact.Bytes) {
                if (Test-FreeVideoUvArchive $partial $artifact.Hash $artifact.Bytes) {
                    Move-Item -LiteralPath $partial -Destination $archive
                    return Expand-FreeVideoUv $archive $artifact.Member $destination
                }
                Move-Item -LiteralPath $partial -Destination "$partial.rejected-$([DateTime]::UtcNow.Ticks)"
                $offset = 0
            }
            Write-Host ('Downloading uv: {0:N1} / {1:N1} MiB; source {2}; {3} connection' -f
                ($offset / 1MB), ($artifact.Bytes / 1MB), $candidate.Source, $candidate.Route)
            $downloadArgs = $common + @('--max-time', '180', '--speed-limit', '32768', '--speed-time', '20',
                '--max-filesize', [string]($artifact.Bytes - $offset), '--output', $partial)
            if ($offset -gt 0) { $downloadArgs += @('--continue-at', [string]$offset) }
            $downloadArgs += $candidate.Url
            $code = Invoke-FreeVideoBootstrapCommand -Executable $curlPath -Arguments $downloadArgs `
                -LogPath $LogPath -Label ("Download uv: {0}, {1}" -f $candidate.Source, $candidate.Route) -Direct:($candidate.Route -eq 'direct')
            if (Test-FreeVideoUvArchive $partial $artifact.Hash $artifact.Bytes) {
                Move-Item -LiteralPath $partial -Destination $archive
                return Expand-FreeVideoUv $archive $artifact.Member $destination
            }
            if ($code -eq 0 -or ($offset -gt 0 -and $code -in @(22, 33))) {
                # Keep incompatible/unchecked bytes; do not append a different
                # artifact or treat a rejected Range response as completion.
                if (Test-Path -LiteralPath $partial) {
                    Move-Item -LiteralPath $partial -Destination "$partial.retained-$([DateTime]::UtcNow.Ticks)"
                }
                if ($restart -eq 0) { continue }
            }
            Write-Host ("Download source unavailable or too slow (curl {0}); trying another source" -f $code)
            break
        }
    }
    throw "uv download failed on all sources. Partial files retained. See $LogPath and retry."
}
