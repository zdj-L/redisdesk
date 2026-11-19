"""Write latest.json and SHA256SUMS.txt next to a Windows setup.exe for a GitHub Release.

This does not upload anything, does not sign the binary, and does not read secrets.
Example:
  python scripts/write_update_manifest.py release/RedisDesk-1.0.1-internal-win64-setup.exe --version 1.0.1
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Allow running from a source checkout
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redisdesk.infra.update import write_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write RedisDesk update manifest files")
    parser.add_argument("setup", type=Path, help="Path to the Windows setup.exe")
    parser.add_argument("--version", required=True, help="Version string such as 1.0.1")
    parser.add_argument("--notes", default="", help="Optional release notes")
    parser.add_argument("--dest", type=Path, default=None, help="Output directory (default: setup parent)")
    args = parser.parse_args(argv)
    latest, sums = write_manifest(args.setup, version=args.version, notes=args.notes, dest_dir=args.dest)
    print(latest)
    print(sums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
