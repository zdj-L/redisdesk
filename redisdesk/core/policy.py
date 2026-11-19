"""RiskCatalog + PolicyEngine + 只读白名单。

判定必须考虑参数规模，不能只看命令名。本模块不得 import adapters/infra/app。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from redisdesk.core.limits import (
    DEL_L3_KEY_THRESHOLD,
    LRANGE_MAX_SPAN,
    PLAINTEXT_REASON_MIN_CHARS,
    SLOWLOG_COUNT_MAX,
    TYPED_CONFIRM_REQUIRED,
)
from redisdesk.core.model import ConnectionProfile, RiskAssessment, RuntimeFacts


# ---------------------------------------------------------------------------
# 命令目录
# ---------------------------------------------------------------------------

L1_SIMPLE: frozenset[str] = frozenset(
    {
        "GET", "MGET", "STRLEN", "GETRANGE", "EXISTS", "TYPE", "TTL", "PTTL",
        "SCAN", "OBJECT", "DUMP",
        "HGET", "HMGET", "HSCAN", "HLEN", "HEXISTS",
        "LINDEX", "LLEN",
        "SCARD", "SSCAN", "SISMEMBER",
        "ZCARD", "ZSCORE", "ZSCAN", "ZCOUNT",
        "XLEN", "XINFO", "XPENDING",
        "INFO", "COMMAND", "DBSIZE", "ROLE", "TIME", "PING", "ECHO", "LASTSAVE",
        "CLUSTER",  # 子命令再细分
        "MEMORY",
        "SLOWLOG",
        "CLIENT",
        "CONFIG",  # CONFIG GET 为 L1；CONFIG SET/REWRITE 另判
        "SCRIPT",  # SCRIPT EXISTS 为 L1? 文档 SCRIPT LOAD/EXISTS/KILL 为 L2，EXISTS 在 L2 行
    }
)

# 明确 L1 的「命令 + 子命令」
L1_SUB: frozenset[tuple[str, str]] = frozenset(
    {
        ("CONFIG", "GET"),
        ("COMMAND", "DOCS"),
        ("COMMAND", "INFO"),
        ("COMMAND", "COUNT"),
        ("COMMAND", "GETKEYS"),
        ("COMMAND", "LIST"),
        ("CLIENT", "LIST"),
        ("CLIENT", "INFO"),
        ("CLIENT", "GETNAME"),
        ("CLIENT", "ID"),
        ("SLOWLOG", "GET"),
        ("SLOWLOG", "LEN"),
        ("MEMORY", "USAGE"),
        ("MEMORY", "STATS"),
        ("MEMORY", "DOCTOR"),
        ("CLUSTER", "INFO"),
        ("CLUSTER", "NODES"),
        ("CLUSTER", "SLOTS"),
        ("CLUSTER", "KEYSLOT"),
        ("CLUSTER", "COUNTKEYSINSLOT"),
        ("CLUSTER", "GETKEYSINSLOT"),
        ("OBJECT", "ENCODING"),
        ("OBJECT", "IDLETIME"),
        ("OBJECT", "REFCOUNT"),
        ("OBJECT", "FREQ"),
        ("XINFO", "STREAM"),
        ("XINFO", "GROUPS"),
        ("XINFO", "CONSUMERS"),
        ("CLIENT", "SETNAME"),  # 文档：直接允许
    }
)

L2_SIMPLE: frozenset[str] = frozenset(
    {
        "SET", "SETNX", "SETEX", "PSETEX", "GETSET", "GETDEL", "APPEND", "SETRANGE",
        "INCR", "DECR", "INCRBY", "DECRBY", "INCRBYFLOAT", "HINCRBY", "HINCRBYFLOAT",
        "ZINCRBY", "ZADD",
        "HSET", "HSETNX", "HDEL",
        "LPUSH", "RPUSH", "LPOP", "RPOP", "LSET", "LTRIM", "LINSERT",
        "SADD", "SREM", "SPOP",
        "ZREM", "ZREMRANGEBYRANK", "ZREMRANGEBYSCORE", "ZREMRANGEBYLEX",
        "EXPIRE", "PEXPIRE", "EXPIREAT", "PEXPIREAT", "PERSIST", "EXPIRETIME",
        "RENAME", "RENAMENX", "COPY",
        "XADD", "XDEL", "XTRIM", "XACK",
        "PUBLISH", "SPUBLISH",
        "DEL", "UNLINK",
    }
)

L2_SUB: frozenset[tuple[str, str]] = frozenset(
    {
        ("SCRIPT", "LOAD"),
        ("SCRIPT", "EXISTS"),
        ("SCRIPT", "KILL"),
        ("SLOWLOG", "RESET"),
        ("LATENCY", "RESET"),
        ("XGROUP", "CREATE"),
        ("XGROUP", "SETID"),
        ("XGROUP", "DESTROY"),
        ("XGROUP", "CREATECONSUMER"),
        ("XGROUP", "DELCONSUMER"),
    }
)

L3_SIMPLE: frozenset[str] = frozenset(
    {
        "EVAL", "EVALSHA", "EVAL_RO", "EVALSHA_RO", "FCALL", "FCALL_RO",
        "MONITOR",
        "RESTORE", "MIGRATE", "SORT",
        "SMEMBERS", "HKEYS", "HVALS",
    }
)

L3_SUB: frozenset[tuple[str, str]] = frozenset(
    {
        ("CONFIG", "SET"),
        ("CONFIG", "REWRITE"),
        ("SCRIPT", "FLUSH"),
        ("CLIENT", "KILL"),
        ("CLIENT", "NO-EVICT"),
        ("CLIENT", "UNPAUSE"),
        ("CLIENT", "PAUSE"),
        ("MEMORY", "PURGE"),
    }
)

L3_PERSISTENCE: frozenset[str] = frozenset({"SAVE", "BGSAVE", "BGREWRITEAOF"})

# L4 受控（dev/test + allow_l4 + Admin + 理由）
L4_CONTROLLED: frozenset[str] = frozenset(
    {
        "FLUSHALL", "FLUSHDB", "SWAPDB", "REPLICAOF", "SLAVEOF", "KEYS",
        "FAILOVER",
    }
)

L4_CONTROLLED_SUB: frozenset[tuple[str, str]] = frozenset(
    {
        ("ACL", "SETUSER"),
        ("ACL", "DELUSER"),
        ("CLUSTER", "RESET"),
        ("CLUSTER", "MEET"),
        ("CLUSTER", "FORGET"),
        ("CLUSTER", "ADDSLOTS"),
        ("CLUSTER", "DELSLOTS"),
        ("CLUSTER", "SETSLOT"),
        ("CLUSTER", "REPLICATE"),
        ("CLUSTER", "SET-CONFIG-EPOCH"),
        ("CLUSTER", "ADDSLOTSRANGE"),
        ("CLUSTER", "DELSLOTSRANGE"),
        ("CLUSTER", "FLUSHSLOTS"),
        ("CLUSTER", "BUMPEPOCH"),
        ("CLUSTER", "FAILOVER"),
    }
)

# L4 永久阻断：对 Admin 同样不可执行
L4_PERMANENT: frozenset[str] = frozenset(
    {
        "SHUTDOWN",
        "DEBUG",
        "SUBSCRIBE", "PSUBSCRIBE", "SSUBSCRIBE",
        "UNSUBSCRIBE", "PUNSUBSCRIBE", "SUNSUBSCRIBE",
        "BLPOP", "BRPOP", "BLMOVE", "BRPOPLPUSH", "BZPOPMIN", "BZPOPMAX",
        "BLMPOP", "BZMPOP",
        "WAIT", "WAITAOF",
    }
)

READONLY_BLOCKED: frozenset[str] = frozenset(
    {
        "EVAL", "EVALSHA", "EVAL_RO", "EVALSHA_RO", "FCALL", "FCALL_RO",
        "PUBLISH", "SPUBLISH",
        "RESTORE", "MIGRATE", "RENAME", "RENAMENX",
    }
)


def _head(argv: list[str] | tuple[str, ...]) -> tuple[str, Optional[str]]:
    if not argv:
        return "", None
    name = argv[0].upper()
    sub = argv[1].upper() if len(argv) > 1 else None
    return name, sub


def _is_blocked_read(name: str, sub: Optional[str], argv: list[str] | tuple[str, ...]) -> bool:
    if name == "XREAD" or name == "XREADGROUP":
        upper = [a.upper() for a in argv]
        return "BLOCK" in upper
    return False


def _range_span(argv: list[str] | tuple[str, ...], start_idx: int) -> tuple[bool, Optional[int], bool]:
    """返回 (参数足够, 区间长度或 None, stop_is_minus_one)。"""
    if len(argv) <= start_idx + 1:
        return False, None, False
    try:
        start = int(argv[start_idx])
        stop = int(argv[start_idx + 1])
    except (TypeError, ValueError):
        return True, None, False
    if stop < 0 or start < 0:
        return True, None, stop == -1 or start == -1
    if stop < start:
        return True, 0, False
    return True, stop - start + 1, False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    decision: str  # allow / confirm / typed_confirm / deny
    assessment: RiskAssessment
    deny_reason: str = ""
    message: str = ""
    requires_typed_confirm: bool = False
    requires_reason: bool = False


class PolicyEngine:
    """风险判定与档位/角色矩阵（与 authz 是「与」关系）。"""

    def assess(
        self,
        argv: list[str] | tuple[str, ...],
        profile: ConnectionProfile,
        runtime: RuntimeFacts | None = None,
    ) -> RiskAssessment:
        facts = runtime or RuntimeFacts()
        if not argv:
            return RiskAssessment(
                level="L3",
                category="empty",
                reason="空命令",
                hard_deny=True,
            )
        name, sub = _head(argv)

        if name in L4_PERMANENT or (name, sub or "") in {("DEBUG", s) for s in ("SLEEP", "OBJECT", "JMAP", "SEGFAULT", "HELP", "*")}:
            return self._l4_permanent(name, argv)
        if name == "DEBUG":
            return self._l4_permanent(name, argv)
        if _is_blocked_read(name, sub, argv):
            return RiskAssessment(
                level="L4",
                category="blocking",
                reason="阻塞类命令会占用连接直到超时，本工具永久阻断",
                alternative="请改用非阻塞命令（如 XRANGE / XREAD 不带 BLOCK）",
                l4_permanent=True,
                hard_deny=True,
                irreversible=False,
            )

        # LRANGE / ZRANGE 规模
        if name in {"LRANGE"}:
            return self._assess_index_range(name, argv, start_idx=2)
        if name in {"ZRANGE", "ZREVRANGE"}:
            # ZRANGE key start stop  — 若为 0 -1 视为全量
            if len(argv) >= 4:
                ok, span, minus = _range_span(argv, 2)
                if minus or (span is not None and span > LRANGE_MAX_SPAN):
                    return RiskAssessment(
                        level="L3",
                        category="full_range",
                        reason="ZRANGE 区间过大或为 0 -1，禁止一次性全量读取",
                        alternative="请使用索引分页或带 LIMIT 的 ZRANGEBYSCORE",
                        hard_deny=True,
                    )
                if span is not None and span <= LRANGE_MAX_SPAN:
                    return self._l1(name, "有序集合区间读取")
            return self._l1(name, "有序集合区间读取")
        if name in {"ZRANGEBYSCORE", "ZREVRANGEBYSCORE", "ZRANGEBYLEX", "ZREVRANGEBYLEX"}:
            upper = [a.upper() for a in argv]
            if "LIMIT" not in upper:
                return RiskAssessment(
                    level="L3",
                    category="full_range",
                    reason="ZRANGEBYSCORE 必须带 LIMIT，禁止无限制全量读取",
                    alternative="请追加 LIMIT offset count",
                    hard_deny=True,
                )
            return self._l1(name, "有序集合按分数分页")

        if name in {"XRANGE", "XREVRANGE"}:
            return self._assess_xrange(name, argv)

        if name in {"SMEMBERS", "HKEYS", "HVALS"}:
            size = facts.collection_size
            if size is not None and size > 10_000:
                return RiskAssessment(
                    level="L3",
                    category="full_fetch",
                    reason=f"{name} 将一次性返回 {size} 个元素，超过 10000 上限",
                    alternative="请改用 HSCAN / SSCAN / ZSCAN 分页",
                    hard_deny=True,
                )
            return RiskAssessment(
                level="L3",
                category="full_fetch",
                reason=f"{name} 会全量返回集合，需确认后执行；大集合请改用分页命令",
                alternative="请改用 HSCAN / SSCAN 分页",
            )

        if name in {"DEL", "UNLINK"}:
            key_count = max(0, len(argv) - 1)
            if facts.from_batch_delete or key_count > DEL_L3_KEY_THRESHOLD:
                return RiskAssessment(
                    level="L3",
                    category="bulk_delete",
                    reason=f"将删除 {key_count} 个 key，属于批量不可逆操作",
                    irreversible=True,
                    impact_estimate=f"{key_count} keys",
                    typed_confirm_text=profile.name,
                )
            return RiskAssessment(
                level="L2",
                category="write",
                reason="删除 key，数据不可恢复",
                irreversible=True,
                impact_estimate=f"{key_count} keys",
            )

        if name == "SLOWLOG" and sub == "GET":
            reason = "慢查询读取"
            if len(argv) >= 3:
                try:
                    count = int(argv[2])
                    if count > SLOWLOG_COUNT_MAX:
                        reason = f"SLOWLOG GET count 超过 {SLOWLOG_COUNT_MAX}，将被截断"
                except ValueError:
                    pass
            return self._l1(name, reason)

        if name == "MEMORY" and sub == "USAGE" and len(argv) > 3:
            # 仅单 key；多余参数仍允许但提示
            pass

        if (name, sub or "") in L1_SUB or (name in L1_SIMPLE and (name, sub or "") not in L2_SUB | L3_SUB | L4_CONTROLLED_SUB):
            # 可能被更高优先级覆盖
            if (name, sub or "") in L2_SUB:
                return self._l2(name, "可能修改诊断数据或脚本缓存")
            if (name, sub or "") in L3_SUB:
                return self._l3(name, profile, self._l3_reason(name, sub))
            if (name, sub or "") in L4_CONTROLLED_SUB:
                return self._l4_controlled(name, profile, f"{name} {sub} 会改变权限或集群拓扑")
            if name in L2_SIMPLE:
                return self._l2_named(name, profile)
            if name in L3_SIMPLE or name in L3_PERSISTENCE:
                return self._l3(name, profile, self._l3_reason(name, sub))
            if name in L4_CONTROLLED:
                return self._assess_l4_controlled(name, profile)
            if (name, sub or "") in L1_SUB or name in L1_SIMPLE:
                return self._l1(name, "只读查询")

        if (name, sub or "") in L2_SUB or name in L2_SIMPLE:
            return self._l2_named(name, profile)
        if (name, sub or "") in L3_SUB or name in L3_SIMPLE or name in L3_PERSISTENCE:
            return self._l3(name, profile, self._l3_reason(name, sub))
        if name in L4_CONTROLLED or (name, sub or "") in L4_CONTROLLED_SUB:
            return self._assess_l4_controlled(name, profile)

        # 未知命令：默认 L3
        return RiskAssessment(
            level="L3",
            category="unknown",
            reason=f"未知命令 {name}，默认按高危处理，请先在测试环境验证",
            typed_confirm_text=profile.name,
        )

    def decide(
        self,
        assessment: RiskAssessment,
        profile: ConnectionProfile,
        role: str,
        *,
        typed_confirm_required: bool = TYPED_CONFIRM_REQUIRED,
        unlock_reason: str | None = None,
    ) -> PolicyDecision:
        """在已有风险评估上套用角色 × 档位矩阵。不替代 authz.check。"""
        if assessment.hard_deny or assessment.l4_permanent:
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="risk",
                message=assessment.reason,
            )

        level = assessment.level
        env = profile.env
        protection = profile.protection

        # 生产环境不可逆 L3/L4 升为永久阻断
        if env == "prod" and level in {"L3", "L4"} and assessment.irreversible:
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="risk",
                message="生产环境禁止不可逆操作",
            )
        if env == "prod" and level == "L4":
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="risk",
                message="生产连接永久阻断 L4 命令，无解锁路径",
            )

        if protection == "readonly" and level != "L1":
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="protection",
                message="只读保护档禁止该命令",
            )
        # 只读档额外阻断脚本与 PUBLISH 等（即便被误标 L1）
        name = ""  # assess 已处理；这里按 category
        if protection == "readonly" and assessment.category in {"script", "pubsub", "write", "unknown"}:
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="protection",
                message="只读保护档禁止该命令",
            )

        if role == "viewer" and level != "L1":
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="role",
                message="需要 Operator 角色才能执行此操作。请联系管理员申请角色或改用已授权账号。",
            )
        if role == "operator" and level == "L4":
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="role",
                message="需要 Admin 角色才能在非生产环境解锁受控 L4 命令。",
            )

        if level == "L1":
            return PolicyDecision(True, "allow", assessment)

        if level == "L2":
            if protection == "unrestricted":
                return PolicyDecision(True, "allow", assessment)
            return PolicyDecision(True, "confirm", assessment)

        if level == "L3":
            need_typed = typed_confirm_required or env == "prod"
            return PolicyDecision(
                True,
                "typed_confirm" if need_typed else "confirm",
                assessment,
                requires_typed_confirm=need_typed,
            )

        # L4 受控
        if role != "admin" or env == "prod" or not profile.allow_l4:
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="risk",
                message="受控 L4 仅允许 Admin 在非生产且已解锁的连接上执行",
            )
        reason = (unlock_reason or "").strip()
        if len(reason) < PLAINTEXT_REASON_MIN_CHARS:
            return PolicyDecision(
                allowed=False,
                decision="deny",
                assessment=assessment,
                deny_reason="risk",
                message=f"解锁 L4 必须填写理由（不少于 {PLAINTEXT_REASON_MIN_CHARS} 个字符）",
                requires_typed_confirm=True,
                requires_reason=True,
            )
        return PolicyDecision(
            True,
            "typed_confirm",
            assessment,
            requires_typed_confirm=True,
            requires_reason=True,
        )

    def evaluate(
        self,
        argv: list[str] | tuple[str, ...],
        profile: ConnectionProfile,
        role: str,
        runtime: RuntimeFacts | None = None,
        *,
        typed_confirm_required: bool = TYPED_CONFIRM_REQUIRED,
        unlock_reason: str | None = None,
    ) -> PolicyDecision:
        assessment = self.assess(argv, profile, runtime)
        return self.decide(
            assessment,
            profile,
            role,
            typed_confirm_required=typed_confirm_required,
            unlock_reason=unlock_reason,
        )

    # ------------------------------------------------------------------
    def _l1(self, name: str, reason: str) -> RiskAssessment:
        return RiskAssessment(level="L1", category="read", reason=reason)

    def _l2(self, name: str, reason: str) -> RiskAssessment:
        return RiskAssessment(level="L2", category="write", reason=reason)

    def _l2_named(self, name: str, profile: ConnectionProfile) -> RiskAssessment:
        reasons = {
            "SET": "将覆盖或写入 key",
            "DEL": "删除 key，数据不可恢复",
            "UNLINK": "删除 key，数据不可恢复",
            "PUBLISH": "将向订阅方推送消息，可能触发下游行为",
            "SPUBLISH": "将向分片订阅方推送消息",
            "RENAME": "可能覆盖目标 key",
            "RENAMENX": "重命名 key",
            "COPY": "可能覆盖目标 key",
            "EXPIRE": "修改 key 过期时间",
            "PERSIST": "移除 key 过期时间",
        }
        irreversible = name in {"DEL", "UNLINK"}
        return RiskAssessment(
            level="L2",
            category="pubsub" if name in {"PUBLISH", "SPUBLISH"} else "write",
            reason=reasons.get(name, "将修改数据"),
            irreversible=irreversible,
        )

    def _l3(self, name: str, profile: ConnectionProfile, reason: str) -> RiskAssessment:
        category = "script" if name in {"EVAL", "EVALSHA", "EVAL_RO", "EVALSHA_RO", "FCALL", "FCALL_RO"} else "high_risk"
        if name == "MONITOR":
            category = "monitor"
        irreversible = name in {"RESTORE", "MIGRATE"}
        return RiskAssessment(
            level="L3",
            category=category,
            reason=reason,
            typed_confirm_text=profile.name,
            irreversible=irreversible,
        )

    def _l3_reason(self, name: str, sub: Optional[str]) -> str:
        mapping = {
            "EVAL": "脚本可执行任意逻辑，可能写入或阻塞服务端",
            "EVALSHA": "脚本可执行任意逻辑，可能写入或阻塞服务端",
            "FCALL": "函数调用可执行任意逻辑",
            "FCALL_RO": "只读函数仍可能产生副作用，按高危处理",
            "MONITOR": "持续输出全部命令，显著增加 CPU/带宽",
            "CONFIG": "将修改运行时参数",
            "SCRIPT": "将清空或修改脚本缓存",
            "CLIENT": "可能断开他人连接",
            "SAVE": "触发持久化，可能造成 IO 抖动",
            "BGSAVE": "触发后台持久化，可能造成 IO 抖动",
            "BGREWRITEAOF": "重写 AOF，可能造成 IO 抖动",
            "RESTORE": "覆盖写入序列化数据",
            "MIGRATE": "跨实例迁移并删除源 key",
            "SORT": "大集合上可能造成 CPU 飙升",
            "MEMORY": "强制释放内存，可能引起抖动",
        }
        return mapping.get(name, "高危操作，需二次确认")

    def _l4_permanent(self, name: str, argv: list[str] | tuple[str, ...]) -> RiskAssessment:
        reasons = {
            "SHUTDOWN": "停服命令，控制台不提供任何执行路径",
            "DEBUG": "调试命令可能导致阻塞、崩溃或泄漏",
            "SUBSCRIBE": "本工具不提供订阅客户端",
            "PSUBSCRIBE": "本工具不提供订阅客户端",
            "SSUBSCRIBE": "本工具不提供订阅客户端",
            "UNSUBSCRIBE": "本工具不提供订阅客户端",
            "PUNSUBSCRIBE": "本工具不提供订阅客户端",
            "SUNSUBSCRIBE": "本工具不提供订阅客户端",
            "BLPOP": "阻塞类命令会占用连接直到超时",
            "BRPOP": "阻塞类命令会占用连接直到超时",
            "BLMOVE": "阻塞类命令会占用连接直到超时",
            "BRPOPLPUSH": "阻塞类命令会占用连接直到超时",
            "WAIT": "阻塞类命令会占用连接直到超时",
        }
        alt = "请改用非阻塞命令" if name not in {"SHUTDOWN", "DEBUG"} else "请通过堡垒机使用 redis-cli（工具外）"
        if name.startswith("S") and "SUBSCRIBE" in name or name.endswith("SUBSCRIBE"):
            alt = "本工具不提供订阅客户端"
        return RiskAssessment(
            level="L4",
            category="permanent",
            reason=reasons.get(name, "该命令被永久阻断"),
            alternative=alt,
            l4_permanent=True,
            hard_deny=True,
            irreversible=True,
        )

    def _assess_l4_controlled(self, name: str, profile: ConnectionProfile) -> RiskAssessment:
        reasons = {
            "FLUSHALL": "清空全部数据，不可恢复",
            "FLUSHDB": "清空当前数据库，不可恢复",
            "SWAPDB": "两个 DB 数据互换，业务可能读错库",
            "REPLICAOF": "改变主从关系，可能造成数据丢失或脑裂",
            "SLAVEOF": "改变主从关系，可能造成数据丢失或脑裂",
            "KEYS": "KEYS 会在大实例上造成秒级阻塞，请改用 SCAN",
            "FAILOVER": "将触发故障转移，改变主从拓扑",
            "ACL": "修改 ACL 可能导致锁死或越权",
            "CLUSTER": "集群拓扑变更，极高风险",
        }
        alternative = "请用 SCAN" if name == "KEYS" else ""
        return RiskAssessment(
            level="L4",
            category="controlled",
            reason=reasons.get(name, "受控 L4 高危操作"),
            alternative=alternative,
            irreversible=True,
            requires_reason=True,
            typed_confirm_text=profile.name,
            l4_permanent=False,
            hard_deny=False,
        )

    def _assess_index_range(self, name: str, argv: list[str] | tuple[str, ...], start_idx: int) -> RiskAssessment:
        ok, span, minus = _range_span(argv, start_idx)
        if minus or (span is not None and span > LRANGE_MAX_SPAN):
            return RiskAssessment(
                level="L3",
                category="full_range",
                reason=f"{name} 区间过大或 stop 为 -1，禁止一次性全量读取",
                alternative="请使用分页区间，单次不超过 500 个元素",
                hard_deny=True,
            )
        return self._l1(name, "列表区间读取")

    def _assess_xrange(self, name: str, argv: list[str] | tuple[str, ...]) -> RiskAssessment:
        upper = [a.upper() for a in argv]
        if "COUNT" not in upper:
            return RiskAssessment(
                level="L3",
                category="full_range",
                reason=f"{name} 必须带 COUNT，否则可能全量返回",
                alternative="请追加 COUNT，且不超过 500",
                hard_deny=True,
            )
        idx = upper.index("COUNT")
        if idx + 1 >= len(argv):
            return RiskAssessment(
                level="L3",
                category="full_range",
                reason="COUNT 参数缺失",
                hard_deny=True,
            )
        try:
            count = int(argv[idx + 1])
        except ValueError:
            return RiskAssessment(
                level="L3",
                category="full_range",
                reason="COUNT 不是有效整数",
                hard_deny=True,
            )
        if count > LRANGE_MAX_SPAN:
            return RiskAssessment(
                level="L3",
                category="full_range",
                reason=f"{name} COUNT 超过 500",
                alternative="请将 COUNT 调整到 500 以内",
                hard_deny=True,
            )
        return self._l1(name, "Stream 区间读取")


