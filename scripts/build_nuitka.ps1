param(
    [string]$Python = "$PSScriptRoot\..\.build-venv\Scripts\python.exe",
    [int]$Jobs = 4
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path $Python)) { throw "Missing isolated .build-venv; see docs/INTERNAL_PORTABLE.md" }
$Python = (Resolve-Path $Python).Path
$version = & $Python -c "from redisdesk import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Cannot read project version" }
New-Item -ItemType Directory -Force artifacts | Out-Null
& $Python -m pip freeze --all | Where-Object { $_ -notmatch "^redisdesk(==| @)" } | Set-Content -Encoding utf8 artifacts\build-requirements.lock.txt
& $Python -m nuitka --standalone --msvc=latest --jobs=$Jobs --assume-yes-for-downloads `
    --enable-plugin=pyside6 --include-qt-plugins=sensible,platforms `
    --windows-console-mode=disable --output-filename=RedisDesk.exe `
    --company-name=RedisDesk --product-name=RedisDesk `
    --file-version="$version.0" --product-version="$version" `
    --include-package=redisdesk --include-package=redis --include-package=paramiko `
    --include-package=cryptography --include-package=bcrypt --include-package=nacl `
    --nofollow-import-to=pytest --noinclude-unittest-mode=nofollow `
    --output-dir=build\portable --report=artifacts\nuitka-compilation-report.xml `
    scripts\portable_entry.py
if ($LASTEXITCODE -ne 0) { throw "Nuitka failed: $LASTEXITCODE" }
Write-Host "Standalone built: build\portable\portable_entry.dist\RedisDesk.exe"
Write-Host "Unsigned internal preview only. Run packaged smoke before staging."
