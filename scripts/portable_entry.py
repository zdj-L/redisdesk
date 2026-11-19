"""Standalone-only entry: internal preview uses a separate default data directory."""
import os
import sys
from pathlib import Path
from redisdesk import __version__

def configure_portable_data():
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    os.environ.setdefault("REDISDESK_APPDATA", str(base / "RedisDesk-Internal" / __version__))

if __name__ == "__main__":
    configure_portable_data()
    if "--smoke-test" in sys.argv:
        from redisdesk.app.packaging_smoke import run
        raise SystemExit(run(sys.argv[1:]))
    from redisdesk.app.main import main
    raise SystemExit(main())
