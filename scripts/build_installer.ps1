# Build the RedisDesk 1.0.0 unsigned internal preview installer from the existing standalone payload.
# Does not rebuild Nuitka. Does not touch user data or a running RedisDesk process.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot\..

$ExpectedExeSha = '2f8d96054548d97a83e09763c76670d84f3b4e75f64c472ea4fe08974b7ec671'
$SourceDir = Join-Path $PWD 'release\RedisDesk-1.0.0-internal-win64-portable'
$SourceExe = Join-Path $SourceDir 'RedisDesk.exe'
$Script = Join-Path $PWD 'scripts\redisdesk-installer.iss'
$Output = Join-Path $PWD 'release\RedisDesk-1.0.0-internal-win64-setup.exe'

if (-not (Test-Path $SourceExe)) { throw "Standalone payload missing: $SourceExe" }
$actual = (Get-FileHash -Algorithm SHA256 $SourceExe).Hash.ToLower()
if ($actual -ne $ExpectedExeSha) { throw "Standalone RedisDesk.exe hash mismatch: $actual" }

$candidates = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  'C:\Program Files\Inno Setup 7\ISCC.exe',
  'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  $cmd = Get-Command iscc -ErrorAction SilentlyContinue
  if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) { throw 'ISCC.exe not found. Install Inno Setup 7 per-user and re-run.' }

Write-Host "ISCC $iscc"
Write-Host "SOURCE_EXE_SHA256 $actual"
& $iscc $Script
if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }
if (-not (Test-Path $Output)) { throw "Installer output missing: $Output" }

$setupHash = (Get-FileHash -Algorithm SHA256 $Output).Hash.ToLower()
$setupSize = (Get-Item $Output).Length
Write-Host "INSTALLER_PATH $Output"
Write-Host "INSTALLER_SIZE $setupSize"
Write-Host "INSTALLER_SHA256 $setupHash"
