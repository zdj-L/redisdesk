"""根据 ConnectionProfile 选择 Gateway 实现并注入传输层。"""
from __future__ import annotations

from redisdesk.adapters.cluster import ClusterGateway
from redisdesk.adapters.sentinel import SentinelGateway
from redisdesk.adapters.direct import DirectGateway
from redisdesk.adapters.gateway import GatewayNotImplemented, RedisGateway
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import ConnectionProfile
from redisdesk.infra.tunnel import SshTunnel, classify_ssh_error


class TopologyResolver:
    def open(
        self,
        profile: ConnectionProfile,
        *,
        password: memoryview | None = None,
        ssh_password: memoryview | bytes | str | None = None,
        ssh_passphrase: memoryview | bytes | str | None = None,
        ssh_client=None,
        sentinel_password: memoryview | bytes | str | None = None,
    ) -> RedisGateway:
        if profile.topology == "sentinel":
            if profile.transport == "ssh":
                raise AppError(ErrorCode.E07, "当前版本的哨兵连接暂不支持 SSH 跳板（发现主节点后地址会变化，需要再为新主节点建立隧道）。请改用 TCP 直连。")
            return SentinelGateway(profile, password=password, sentinel_password=sentinel_password)
        if profile.topology == "cluster":
            if profile.transport == "ssh":
                raise AppError(ErrorCode.E07, "\u5f53\u524d\u7248\u672c\u7684\u96c6\u7fa4\u8fde\u63a5\u6682\u4e0d\u652f\u6301 SSH \u8df3\u677f\uff08\u91cd\u5b9a\u5411\u4f1a\u8fde\u5230\u5176\u4ed6\u8282\u70b9\uff0c\u9700\u8981\u6309\u8282\u70b9\u6620\u5c04\uff09\u3002\u8bf7\u6539\u7528 TCP \u76f4\u8fde\u3002")
            return ClusterGateway(profile, password=password)
        if profile.topology != "direct":
            raise AppError(ErrorCode.E01, f"未知拓扑 {profile.topology}")

        tunnel = None
        if profile.transport == "ssh":
            if profile.ssh is None or not profile.ssh.host:
                raise AppError(ErrorCode.E07, "请填写 SSH 跳板主机与端口。")
            if not profile.host:
                raise AppError(ErrorCode.E01, "连接缺少 Redis 主机地址。")
            tunnel = SshTunnel(
                profile.ssh,
                remote_host=profile.host,
                remote_port=profile.port,
                password=ssh_password,
                passphrase=ssh_passphrase,
                client=ssh_client,
            )
            try:
                tunnel.start()
            except Exception as exc:  # noqa: BLE001
                if tunnel is not None:
                    tunnel.close()
                raise classify_ssh_error(
                    exc,
                    bastion_host=profile.ssh.host,
                    bastion_port=int(profile.ssh.port or 22),
                    redis_host=profile.host,
                    redis_port=profile.port,
                    phase="bastion",
                ) from exc
        elif profile.transport != "tcp":
            raise AppError(ErrorCode.E01, f"未知传输方式 {profile.transport}")

        try:
            gw = DirectGateway(profile, password=password, tunnel=tunnel)
        except Exception:
            if tunnel is not None:
                tunnel.close()
            raise
        try:
            gw.discover_capabilities()
        except AppError:
            gw.close()
            raise
        except Exception:
            pass
        return gw
