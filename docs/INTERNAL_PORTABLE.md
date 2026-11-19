# RedisDesk 1.0.0 internal portable preview (Windows x64)

This is an UNSIGNED internal preview, not an installer and not an auto-updater.
Extract the complete folder to a writable location; run RedisDesk.exe. Do not move
only the EXE. No console window is shown. Do not bypass Windows security warnings
or disable antivirus. Stop and contact the distributor if security software blocks it.

The version 1.0.0 is read from the existing project metadata, not a new stable-release
claim. Program files are portable; user data is deliberately separate, by default:
%LOCALAPPDATA%\RedisDesk-Internal\1.0.0
This does not migrate or overwrite the development app's %LOCALAPPDATA%\RedisDesk.
REDISDESK_APPDATA explicitly overrides the data directory. Do not point it at a live
profile when trialing the preview. Only one normal RedisDesk instance may run per
Windows session; fully quit the existing one yourself before launching this preview.
DPAPI-saved credentials belong to the current Windows user/machine; do not copy data
between machines. No connections, passwords, keys, .env files, database, audit logs,
or user settings are shipped. Do not export production secrets with feedback logs.

## Build and reproduce
Use CPython 3.13 x64 and the installed VS2022 MSVC/Windows SDK. Never install into
or modify the development .venv for packaging. Create .build-venv and install the
exact pinned artifacts/build-requirements.lock.txt plus the allowlisted source
snapshot; a wheelhouse manifest/hash list accompanies the build where available.
Run scripts/build_nuitka.ps1 with .build-venv Python, then run the packaged EXE smoke.
The build uses standalone, PySide6 plugin, explicit application/dependency packages,
and no source-tree data-directory copying. Only compiler runtime outputs and named
release documents/licenses enter the staged allowlist. Build outputs and caches are
not part of the source snapshot or ZIP. ZIP timestamps/build binaries are not claimed
bit-for-bit deterministic; the toolchain, source manifest and SHA256 identify this run.

## Acceptance / limitations
Source pytest and packaged EXE smoke are distinct. Packaged smoke creates a fresh
Temp directory, never reuses user data, uses an ephemeral loopback RESP server and an
injected fake SSH transport. It checks the main window, Qt plugin, DPAPI roundtrip,
connection save, directory scan/value read, console PING, INFO diagnostics and settings.
This is NOT a real SSH host-key/authentication acceptance test or a clean-machine test.

Before wider use, another Windows x64 machine WITHOUT Python/VS installed must verify
startup, plugin/runtime availability, ordinary window display and exit, test-instance
Redis/TLS/SSH connectivity, DPAPI save/reopen, and settings persistence. Test read-only
against non-production fixtures first. No code signing or clean-system validation is
claimed. No installation or update service is included.

Run smoke from PowerShell (report path must be outside extracted release folder):
.\RedisDesk.exe --smoke-test --smoke-report "$env:TEMP\redisdesk-smoke.json"
Wait for process exit and inspect ok:true. Smoke creates disposable test-only data.

## Third-party licensing
Qt/PySide6/Shiboken are used under the applicable open-source LGPLv3 terms,
not a purchased Qt commercial license. Full LGPLv3/GPLv3 texts and distribution
license notices are included. The wheel's LicenseRef-Qt-Commercial notice is kept
as upstream metadata, not as a claim that a commercial entitlement was purchased.
Qt runtime libraries remain separate/unmodified and may be replaced with compatible
builds. Qt/PySide upstream source links are in THIRD-PARTY-NOTICES.txt. Preserve the
LICENSES directory and notices when sharing internally.
