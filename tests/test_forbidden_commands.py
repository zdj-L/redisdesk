from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "redisdesk"

CALL_PATTERNS = (
    re.compile(r"\bKEYS\s*\("),
    re.compile(r"\.keys\s*\("),
    re.compile(r"\bHGETALL\s*\("),
    re.compile(r"\bSMEMBERS\s*\("),
)


def _strip_comments_and_strings_docs(text: str) -> str:
    lines = []
    in_doc = False
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith('"""') or s.startswith("'''"):
            if s.count('"""') == 1 or (s.startswith("'''") and s.count("'''") == 1):
                in_doc = not in_doc
            continue
        if in_doc or s.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_scan_and_value_paths_have_no_forbidden_commands():
    targets = [
        ROOT / "adapters" / "scan.py",
        ROOT / "adapters" / "direct.py",
        ROOT / "app" / "services" / "key_service.py",
        ROOT / "app" / "services" / "value_service.py",
    ]
    for path in targets:
        text = _strip_comments_and_strings_docs(path.read_text(encoding="utf-8"))
        for rx in CALL_PATTERNS:
            if rx.pattern == r"\.keys\s*\(":
                # dict.keys() 允许；redis.keys() 不允许。本批实现未调用 redis.keys。
                if "redis" not in text.lower() or ".keys(" not in text.replace(" ", ""):
                    continue
            assert not rx.search(text), f"{path} contains forbidden call {rx.pattern}"
        assert "LRANGE 0 -1" not in text
        assert "ZRANGE 0 -1" not in text
