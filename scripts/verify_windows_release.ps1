param(
    [Parameter(Mandatory = $true)][string[]]$Signed,
    [Parameter(Mandatory = $true)][string]$FolderDist
)

$ErrorActionPreference = 'Stop'

foreach ($path in $Signed) {
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid') { throw "$path is not validly signed: $($signature.Status) $($signature.StatusMessage)" }
    if ($signature.SignerCertificate.Subject -notmatch 'CN=FlashML LLC') { throw "Unexpected signer for ${path}: $($signature.SignerCertificate.Subject)" }
    if ($null -eq $signature.TimeStamperCertificate) { throw "Signature has no timestamp: $path" }
    Write-Host "signed $path as $($signature.SignerCertificate.Subject), timestamped by $($signature.TimeStamperCertificate.Subject)"
}

# The ZIP cannot carry a signature and was built before its executable was signed. Rebuild it around the signed executable, with the same archiver as build_windows.py.
$folder = Join-Path $FolderDist 'FreeVideo'
$executable = Join-Path $folder 'FreeVideo.exe'
$hash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $folder 'SHA256SUMS.txt'), $hash + "  FreeVideo.exe`n")
python -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', root_dir=sys.argv[2], base_dir='FreeVideo')" (Join-Path $FolderDist 'FreeVideo-Windows-folder') $FolderDist
if ($LASTEXITCODE -ne 0) { throw 'Could not rebuild the folder ZIP' }
Write-Host "rebuilt $(Join-Path $FolderDist 'FreeVideo-Windows-folder.zip')"
