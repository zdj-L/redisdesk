"""Standalone-only entry: internal preview uses a separate default data directory."""
import os
import sys
from pathlib import Path

# 1.0.x internal preview keeps the already documented isolated path across patch updates.
INTERNAL_APPDATA_FAMILY = "1.0.0"


def configure_portable_data():
    """Register the preview default. Do not set REDISDESK_APPDATA so a pointer or env can win."""
    from redisdesk.infra.paths import set_packaged_default_data_dir

    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    set_packaged_default_data_dir(base / "RedisDesk-Internal" / INTERNAL_APPDATA_FAMILY)


if __name__ == "__main__":
    configure_portable_data()
    if "--smoke-test" in sys.argv:
        from redisdesk.app.packaging_smoke import run
        raise SystemExit(run(sys.argv[1:]))
    from redisdesk.app.main import main
    raise SystemExit(main())
