"""Stage only allowlisted compiler outputs and named release metadata. No project-tree ZIP."""
from __future__ import annotations
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import argparse
import os
import shutil
import sys
import zipfile
from redisdesk import __version__

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'build' / 'portable' / 'portable_entry.dist'
RELEASE = ROOT / 'release' / f'RedisDesk-{__version__}-internal-win64-portable'
ARTIFACTS = ROOT / 'artifacts'
EXTENSIONS = {'.exe', '.dll', '.pyd', '.txt', '.json', '.qm', '.pak', '.bin', '.dat', '.rcc', '.conf', '.pem'}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def zip_allowlist(destination, base, names):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(names):
            data = (base / name).read_bytes()
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)



REQUIRED_SOURCE_EXTRAS = (
    'docs/INTERNAL_PORTABLE.md',
    'docs/release-licenses/GPL-3.0-only.txt',
    'docs/release-licenses/LGPL-3.0-only.txt',
    'pyproject.toml',
    'scripts/build_nuitka.ps1',
    'scripts/portable_entry.py',
    'scripts/stage_portable.py',
)


def iter_source_paths(root: Path) -> list[str]:
    names: list[str] = []
    package = root / 'redisdesk'
    for path in sorted(package.rglob('*.py')):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part.lower() in {'__pycache__', 'logs', '.git', 'tests', 'data'} for part in rel.parts):
            continue
        names.append(rel.as_posix())
    for extra in REQUIRED_SOURCE_EXTRAS:
        if not (root / extra).is_file():
            raise SystemExit(f'Missing required source: {extra}')
        if extra not in names:
            names.append(extra)
    return names


def write_source_allowlist(destination: Path) -> None:
    items = []
    for name in iter_source_paths(ROOT):
        path = ROOT / name
        items.append({'path': name, 'sha256': sha(path), 'size': path.stat().st_size})
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(items, indent=2) + '\n', encoding='utf-8')


def should_refresh_allowlist(flag: bool) -> bool:
    env = os.environ.get('REDISDESK_REFRESH_SOURCE_ALLOWLIST', '').strip().lower()
    return flag or env in {'1', 'true', 'yes', 'on'}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Stage RedisDesk portable release files')
    parser.add_argument('--refresh-allowlist', action='store_true', help='Rebuild artifacts/release-source-allowlist.json from current sources')
    args = parser.parse_args(argv)
    allowlist_path = ARTIFACTS / 'release-source-allowlist.json'
    if should_refresh_allowlist(args.refresh_allowlist) or not allowlist_path.is_file():
        if not allowlist_path.is_file() and not should_refresh_allowlist(args.refresh_allowlist):
            raise SystemExit('Missing artifacts/release-source-allowlist.json (pass --refresh-allowlist in CI)')
        write_source_allowlist(allowlist_path)
    source_items = json.loads(allowlist_path.read_text(encoding='utf-8'))
    if RELEASE.exists():
        raise SystemExit(f'Refusing to overwrite existing release: {RELEASE}')
    if not (RUNTIME / 'RedisDesk.exe').is_file():
        raise SystemExit('Build the standalone first')
    runtime_files = []
    for p in sorted(RUNTIME.rglob('*')):
        if not p.is_file(): continue
        rel = p.relative_to(RUNTIME).as_posix()
        if p.is_symlink() or p.suffix.lower() not in EXTENSIONS:
            raise SystemExit(f'Output needs explicit review/allowlist extension: {rel}')
        if p.name.startswith('.') or p.name.lower().endswith(('.db', '.sqlite', '.key', '.log', '.env')):
            raise SystemExit(f'Forbidden output: {rel}')
        if any(part.lower() in {'__pycache__', 'logs', '.git', 'tests', 'data'} for part in p.relative_to(RUNTIME).parts):
            raise SystemExit(f'Forbidden directory: {rel}')
        if p.suffix.lower() == '.pem' and b'PRIVATE KEY' in p.read_bytes():
            raise SystemExit(f'Private PEM forbidden: {rel}')
        runtime_files.append({'path': rel, 'size': p.stat().st_size, 'sha256': sha(p)})
    # Persist the exact reviewed compiler-output allowlist, then copy ONLY those names.
    (ARTIFACTS / 'runtime-allowlist.json').write_text(json.dumps(runtime_files, indent=2), encoding='utf-8')
    RELEASE.mkdir(parents=True)
    for item in runtime_files:
        target = RELEASE / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RUNTIME / item['path'], target)
        assert sha(target) == item['sha256']
    # Required public license notices, from installed distribution license directories only.
    deps = ['PySide6-Essentials','shiboken6','redis','paramiko','cryptography','bcrypt','PyNaCl','cffi','pycparser']
    notices = []
    for name in deps:
        package = md.distribution(name)
        notices.append(f'{package.metadata["Name"]}=={package.version} | {package.metadata.get("License-Expression", package.metadata.get("License", "see bundled license"))}')
        for f in package.files or []:
            parts = str(f).replace('\\', '/').lower()
            if 'license' not in parts and 'copying' not in parts and 'notice' not in parts: continue
            source = Path(package.locate_file(f)).resolve()
            if not source.is_file() or source.suffix.lower() in {'.py', '.pyc', '.pyd', '.dll', '.exe'}: continue
            # License file path is metadata only; do not follow user/project files.
            if '.build-venv' not in str(source): continue
            target = RELEASE / 'LICENSES' / name / Path(str(f)).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != source.read_bytes():
                target = target.with_name(sha(source)[:10] + '-' + target.name)
            shutil.copyfile(source, target)
    for filename in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt'):
        source = ROOT / 'docs' / 'release-licenses' / filename
        target = RELEASE / 'LICENSES' / 'Qt-OpenSource' / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        target = RELEASE / 'LICENSES' / 'Python-LICENSE.txt'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(python_license, target)
    (RELEASE / 'THIRD-PARTY-NOTICES.txt').write_text('\n'.join(notices) + '\nQt/PySide are unmodified dynamically linked libraries. See LICENSES; upstream sources: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.2.1-src/ and https://download.qt.io/archive/qt/6.8/ .\n', encoding='utf-8')
    shutil.copyfile(ROOT / 'docs' / 'INTERNAL_PORTABLE.md', RELEASE / 'README-INTERNAL.md')
    shutil.copyfile(ARTIFACTS / 'build-requirements.lock.txt', RELEASE / 'BUILD-REQUIREMENTS.txt')
    for item in source_items:
        assert sha(ROOT / item['path']) == item['sha256'], f'Source changed since manifest: {item["path"]}'
    source_zip = ROOT / 'release' / f'RedisDesk-{__version__}-internal-source.zip'
    zip_allowlist(source_zip, ROOT, [x['path'] for x in source_items])
    shutil.copyfile(ARTIFACTS / 'release-source-allowlist.json', RELEASE / 'SOURCE-MANIFEST.json')
    (RELEASE / 'BUILD-INFO.json').write_text(json.dumps({'version': __version__, 'channel': 'internal-preview', 'signed': False,
        'python': sys.version, 'nuitka': md.version('Nuitka'), 'platform': 'Windows x64',
        'default_data': r'%LOCALAPPDATA%\RedisDesk-Internal\1.0.0', 'source_archive_sha256': sha(source_zip),
        'clean_machine_validated': False, 'runtime_file_count': len(runtime_files)}, indent=2), encoding='utf-8')
    names = [p.relative_to(RELEASE).as_posix() for p in RELEASE.rglob('*') if p.is_file()]
    manifest = [{'path': n, 'sha256': sha(RELEASE / n), 'size': (RELEASE / n).stat().st_size} for n in sorted(names)]
    (RELEASE / 'MANIFEST.sha256').write_text(''.join(f'{x["sha256"]}  {x["path"]}\n' for x in manifest), encoding='utf-8')
    names.append('MANIFEST.sha256')
    zip_path = RELEASE.parent / (RELEASE.name + '.zip')
    zip_allowlist(zip_path, RELEASE, names)
    (ROOT / 'release' / 'SHA256SUMS.txt').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in (zip_path, source_zip)), encoding='utf-8')
    print(json.dumps({'exe': str(RELEASE / 'RedisDesk.exe'), 'exe_bytes': (RELEASE / 'RedisDesk.exe').stat().st_size,
        'exe_sha256': sha(RELEASE / 'RedisDesk.exe'), 'zip': str(zip_path), 'zip_bytes': zip_path.stat().st_size,
        'zip_sha256': sha(zip_path), 'source_zip': str(source_zip), 'source_sha256': sha(source_zip),
        'runtime_files': len(runtime_files), 'folder_bytes': sum(p.stat().st_size for p in RELEASE.rglob('*') if p.is_file())}, indent=2))

if __name__ == '__main__': main()
