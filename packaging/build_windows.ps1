<#
.SYNOPSIS
  Build the Windows release bundle and zip it.

.DESCRIPTION
  Runs PyInstaller with packaging/rpgmaker_translator.spec (onedir), then zips
  dist/RPGMakerTranslator into RPGMakerTranslator-<version>-win64.zip and writes
  a matching .sha256 file. Used by .github/workflows/release.yml and for local builds.

  Prerequisite (in the Python environment you build with):
      pip install -r requirements-lock.txt     # exact pins, same as CI
  (or: pip install -r requirements-dev.txt)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
  powershell -File packaging\build_windows.ps1 -OutDir C:\temp\rt -Python py
#>
param(
    [string]$OutDir = "",          # default: <repo>/dist
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root "dist" }
$WorkDir = Join-Path $OutDir "_work"
$AppName = "RPGMakerTranslator"

$Version = & $Python -c "import sys; sys.path.insert(0, r'$Root'); from translator.version import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "Could not read translator/version.py" }
Write-Host "Building $AppName $Version"

& $Python -m PyInstaller (Join-Path $PSScriptRoot "rpgmaker_translator.spec") `
    --noconfirm --clean --distpath $OutDir --workpath $WorkDir
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$BundleDir = Join-Path $OutDir $AppName
if (-not (Test-Path (Join-Path $BundleDir "$AppName.exe"))) { throw "Bundle exe missing" }

$Zip = Join-Path $OutDir "$AppName-$Version-win64.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
# Zip the folder itself so users get RPGMakerTranslator\ when they extract.
Compress-Archive -Path $BundleDir -DestinationPath $Zip -CompressionLevel Optimal

$Hash = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
"$Hash  $(Split-Path -Leaf $Zip)" | Out-File -Encoding ascii "$Zip.sha256"

$SizeMB = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host "Created $Zip ($SizeMB MB)"
Write-Host "SHA256  $Hash"

# Expose paths to GitHub Actions when running in CI.
if ($env:GITHUB_OUTPUT) {
    "version=$Version" | Out-File -Append -Encoding utf8 $env:GITHUB_OUTPUT
    "zip=$Zip" | Out-File -Append -Encoding utf8 $env:GITHUB_OUTPUT
    "sha256=$Zip.sha256" | Out-File -Append -Encoding utf8 $env:GITHUB_OUTPUT
}
