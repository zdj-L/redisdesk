"""根据 ConnectionProfile 选择 Gateway 实现并注入传输层。"""
from __future__ import annotations

from redisdesk.adapters.cluster import ClusterGateway
from redisdesk.adapters.sentinel import SentinelGateway
from redisdesk.adapters.direct import DirectGateway
from redisdesk.adapters.gateway import GatewayNotImplemented, RedisGateway
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import ConnectionProfile
from redisdesk.infra.tunnel import SshJumpSession, SshTunnel, classify_ssh_error



def _classify_jump(exc, profile, *, redis_host="", redis_port=0):
    return classify_ssh_error(
        exc,
        bastion_host=profile.ssh.host if profile.ssh else "",
        bastion_port=int(profile.ssh.port or 22) if profile.ssh else 22,
        redis_host=redis_host or (profile.host or ""),
        redis_port=int(redis_port or profile.port or 0),
        phase="bastion",
    )


def _start_jump(profile, *, ssh_password=None, ssh_passphrase=None, ssh_client=None):
    if profile.transport != "ssh":
        return None
    if profile.ssh is None or not profile.ssh.host:
        raise AppError(ErrorCode.E07, "请填写 SSH 跳板主机与端口。")
    jump = SshJumpSession(
        profile.ssh,
        password=ssh_password,
        passphrase=ssh_passphrase,
        client=ssh_client,
    )
    try:
        jump.start()
    except Exception as exc:  # noqa: BLE001
        jump.close()
        raise _classify_jump(exc, profile) from exc
    return jump


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
            jump = None
            try:
                jump = _start_jump(
                    profile,
                    ssh_password=ssh_password,
                    ssh_passphrase=ssh_passphrase,
                    ssh_client=ssh_client,
                )
                return SentinelGateway(
                    profile,
                    password=password,
                    sentinel_password=sentinel_password,
                    jump=jump,
                )
            except Exception:
                if jump is not None:
                    jump.close()
                raise
        if profile.topology == "cluster":
            jump = None
            try:
                jump = _start_jump(
                    profile,
                    ssh_password=ssh_password,
                    ssh_passphrase=ssh_passphrase,
                    ssh_client=ssh_client,
                )
                return ClusterGateway(profile, password=password, jump=jump)
            except Exception:
                if jump is not None:
                    jump.close()
                raise
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
