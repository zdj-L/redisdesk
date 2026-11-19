from __future__ import annotations

from typing import Any

from redisdesk.adapters.gateway import capabilities_from_version
from redisdesk.adapters.scan import CancellationToken, ScanEngine
from redisdesk.core.model import KeyMeta, NodeInfo, ScanQuery


class FakeGateway:
    def __init__(self, batches: list[tuple[int, list[bytes]]], *, version: str = "7.2.0") -> None:
        self.capabilities = capabilities_from_version(version)
        self.batches = list(batches)
        self.calls: list[dict[str, Any]] = []
        self._ttl: dict[bytes, int] = {}
        self._types: dict[bytes, str] = {}

    def ping(self) -> float:
        return 0.001

    def info(self, section: str | None = None) -> dict[str, str]:
        return {"redis_version": self.capabilities.server_version}

    def scan(self, node_id, cursor, *, match, count, key_type):
        self.calls.append({"cursor": cursor, "match": match, "count": count, "key_type": key_type, "node_id": node_id})
        if not self.batches:
            return 0, [], None
        item = self.batches.pop(0)
        if len(item) == 2:
            return item[0], item[1], None
        return item

    def execute(self, argv, *, timeout_s: float):
        raise AssertionError(f"unexpected execute {argv}")

    def pipeline(self, commands, *, batch: int):
        out = []
        for argv in commands:
            name = argv[0].upper()
            key = argv[1].encode("utf-8") if isinstance(argv[1], str) else argv[1]
            if name == "TTL":
                out.append(self._ttl.get(key, -1))
            elif name == "TYPE":
                out.append(self._types.get(key, "string"))
            else:
                raise AssertionError(f"unexpected pipeline cmd {argv}")
        return out

    def route(self, keys):
        return {"default": keys}

    def nodes(self):
        return [NodeInfo("default", "127.0.0.1", 6379)]

    def close(self):
        return None


def test_fill_page_and_empty_batch_continues():
    # 空批必须继续；游标 1 返回空，游标 2 返回数据，然后结束
    keys = [f"k{i}".encode() for i in range(3)]
    gw = FakeGateway([(2, []), (0, keys)])
    engine = ScanEngine(gw, ScanQuery(count=1000), fetch_ttl=False, page_size=10)
    rows = engine.fill_page()
    assert [r.key for r in rows] == ["k0", "k1", "k2"]
    assert engine.session.state == "finished"
    assert len(gw.calls) == 2
    assert engine.session.scanned == 3


def test_dedupe_repeated_keys():
    gw = FakeGateway([(1, [b"a", b"b"]), (0, [b"b", b"c"])])
    engine = ScanEngine(gw, ScanQuery(), fetch_ttl=False, page_size=10)
    rows = engine.fill_page()
    assert [r.key for r in rows] == ["a", "b", "c"]


def test_cancel_stops_and_keeps_results():
    token = CancellationToken()
    gw = FakeGateway([(1, [b"a"]), (0, [b"b"])])
    orig = gw.scan

    def scan_and_cancel(*args, **kwargs):
        result = orig(*args, **kwargs)
        token.cancel()  # 一批完成后取消，下一轮循环必须停下
        return result

    gw.scan = scan_and_cancel
    engine = ScanEngine(gw, ScanQuery(), fetch_ttl=False, page_size=100, token=token)
    engine.fill_page()
    assert engine.session.state == "cancelled"
    assert any(r.key == "a" for r in engine.buffer)
    assert all(r.key != "b" for r in engine.buffer)


def test_budget_pauses_when_client_filter():
    # 启用 TTL 过滤才会启动预算
    many = [f"k{i}".encode() for i in range(20)]
    gw = FakeGateway([(1, many), (0, many)])
    for k in many:
        gw._ttl[k] = 10
    engine = ScanEngine(
        gw,
        ScanQuery(ttl_min=1, count=20),
        fetch_ttl=True,
        page_size=1000,
        cursor_budget=10,
        time_budget_s=120,
    )
    engine.fill_page()
    assert engine.session.state == "paused"
    assert engine.session.budget_exhausted


def test_server_type_filter_passed_to_scan_on_redis6():
    gw = FakeGateway([(0, [b"s1"])], version="6.2.0")
    engine = ScanEngine(gw, ScanQuery(key_type="string"), fetch_ttl=False)
    engine.fill_page()
    assert gw.calls[0]["key_type"] == "string"


def test_type_filter_degrades_without_scan_type():
    gw = FakeGateway([(0, [b"a", b"b"])], version="5.0.0")
    gw._types[b"a"] = "string"
    gw._types[b"b"] = "hash"
    engine = ScanEngine(gw, ScanQuery(key_type="string"), fetch_ttl=False)
    rows = engine.fill_page()
    assert [r.key for r in rows] == ["a"]
    assert gw.calls[0]["key_type"] is None  # 不能把 TYPE 传给旧服务端
