<#
.SYNOPSIS
  Download the pinned UberWolfCli.exe and check its SHA-256.

.DESCRIPTION
  UberWolf (https://github.com/Sinflower/UberWolf, MIT License) unpacks Wolf
  RPG Data.wolf archives. The app runs its command-line build. The version,
  download URL and SHA-256 are pinned in third_party/UberWolf/PIN.txt; the
  file is saved as third_party/UberWolf/UberWolfCli.exe (not committed, see
  .gitignore), where both the PyInstaller spec and source runs look for it.

  Fails (and leaves no exe behind) when the download's SHA-256 does not match
  the pin. An existing exe with the right hash is kept as is.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\fetch_uberwolf.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dir = Join-Path $Root "third_party/UberWolf"
$Exe = Join-Path $Dir "UberWolfCli.exe"

$Pin = @{}
foreach ($line in Get-Content (Join-Path $Dir "PIN.txt")) {
    if ($line -match '^\s*([a-z0-9]+)\s*=\s*(\S+)\s*$') { $Pin[$Matches[1]] = $Matches[2] }
}
foreach ($k in "version", "url", "sha256") {
    if (-not $Pin[$k]) { throw "third_party/UberWolf/PIN.txt has no '$k='" }
}
$Expected = $Pin["sha256"].ToLower()

if (Test-Path $Exe) {
    if ((Get-FileHash -Algorithm SHA256 $Exe).Hash.ToLower() -eq $Expected) {
        Write-Host "UberWolfCli.exe $($Pin['version']) already present (SHA-256 OK)"
        exit 0
    }
    Write-Host "Existing UberWolfCli.exe does not match the pin; downloading again"
}

$Tmp = "$Exe.download"
if (Test-Path $Tmp) { Remove-Item $Tmp -Force }
Write-Host "Downloading UberWolfCli.exe $($Pin['version'])"
Invoke-WebRequest -Uri $Pin["url"] -OutFile $Tmp -UseBasicParsing
$Got = (Get-FileHash -Algorithm SHA256 $Tmp).Hash.ToLower()
if ($Got -ne $Expected) {
    Remove-Item $Tmp -Force
    throw "UberWolfCli.exe SHA-256 mismatch: expected $Expected, got $Got"
}
Move-Item -Force $Tmp $Exe
Write-Host "UberWolfCli.exe $($Pin['version']) saved (SHA-256 $Got)"
