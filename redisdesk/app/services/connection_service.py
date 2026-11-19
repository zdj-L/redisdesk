"""连接 CRUD、授权、TLS 例外与测试编排。"""

from __future__ import annotations

from typing import Optional

from redisdesk.adapters.acl_probe import AclProbeResult, probe
from redisdesk.adapters.direct import classify_connect_error
from redisdesk.adapters.gateway import open_gateway
from redisdesk.adapters.tls import require_tls_or_exception
from redisdesk.core.authz import Action, GrantResolver, check_or_raise
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import PLAINTEXT_REASON_MIN_CHARS
from redisdesk.core.model import ConnectionProfile, SecretRef, SessionContext, TlsConfig
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.secrets import SecretLease, protect_secret
from redisdesk.infra.store import Store, StoreGrantResolver, new_id


class ConnectionService:
    def __init__(self, store: Store, audit: AuditLog, grants: GrantResolver | None = None) -> None:
        self.store = store
        self.audit = audit
        self.grants = grants or StoreGrantResolver(store)

    def list_profiles(self, session: SessionContext, *, hide_unauthorized: bool = False) -> list[ConnectionProfile]:
        check_or_raise(session, Action.VIEW_CONNECTIONS, grants=self.grants)
        profiles = self.store.list_connections()
        if hide_unauthorized:
            return [p for p in profiles if self.grants.has_grant(p.id, session.role)]
        return profiles

    def is_authorized(self, session: SessionContext, profile: ConnectionProfile) -> bool:
        return self.grants.has_grant(profile.id, session.role)

    def save(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        *,
        secret_mode: str = "dpapi",
        password: str | None = None,
        ssh_password: str | None = None,
        ssh_passphrase: str | None = None,
    ) -> ConnectionProfile:
        check_or_raise(session, Action.MANAGE_CONNECTION, grants=self.grants)
        tls = profile.tls
        if not tls.enabled:
            check_or_raise(session, Action.ALLOW_PLAINTEXT, grants=self.grants)
            if not tls.plaintext_reason or len(tls.plaintext_reason) < PLAINTEXT_REASON_MIN_CHARS:
                raise AppError(ErrorCode.E08, f"明文例外必须填写不少于 {PLAINTEXT_REASON_MIN_CHARS} 字的理由")
            tls = TlsConfig(
                enabled=False,
                version_min=tls.version_min,
                ca_path=tls.ca_path,
                client_cert_path=tls.client_cert_path,
                client_key_path=tls.client_key_path,
                verify_hostname=True,
                allow_plaintext=True,
                plaintext_reason=tls.plaintext_reason,
                approved_by=session.username,
                approved_at=profile.updated_at,
            )
            profile = ConnectionProfile(**{**_profile_dict(profile), "tls": tls})  # type: ignore[arg-type]
            self._audit(session, profile, "high_risk_plaintext", "allow", None)
        else:
            require_tls_or_exception(tls)
        if profile.env == "prod" and (profile.protection != "readonly" or profile.allow_l4):
            raise AppError(ErrorCode.E08, "生产连接必须为只读档，且不可开启 allow_l4")
        if not profile.id:
            profile = ConnectionProfile(**{**_profile_dict(profile), "id": new_id("c")})  # type: ignore[arg-type]
        if password:
            profile = self._store_password(profile, password, secret_mode=secret_mode)
        if profile.ssh is not None:
            profile = self._store_ssh_secrets(
                profile,
                ssh_password=ssh_password,
                ssh_passphrase=ssh_passphrase,
                secret_mode=secret_mode,
            )
        self.store.save_connection(profile, created_by=session.username, secret_mode=secret_mode)
        self._audit(session, profile, "profile_change", "allow", None)
        return profile

    def _store_password(self, profile: ConnectionProfile, password: str, *, secret_mode: str) -> ConnectionProfile:
        sid = profile.secret_ref or new_id("s")
        cipher = protect_secret(password.encode("utf-8"), storage=secret_mode)
        ref = SecretRef(id=sid, kind="redis_password", storage=secret_mode, cipher=cipher)  # type: ignore[arg-type]
        self.store.put_secret(ref)
        return ConnectionProfile(**{**_profile_dict(profile), "secret_ref": sid})  # type: ignore[arg-type]


    def _store_ssh_secrets(
        self,
        profile: ConnectionProfile,
        *,
        ssh_password: str | None,
        ssh_passphrase: str | None,
        secret_mode: str,
    ) -> ConnectionProfile:
        ssh = profile.ssh
        if ssh is None:
            return profile
        from redisdesk.core.model import SshConfig
        data = {
            "host": ssh.host,
            "port": ssh.port,
            "username": ssh.username,
            "auth_mode": ssh.auth_mode,
            "private_key_path": ssh.private_key_path,
            "passphrase_ref": ssh.passphrase_ref,
            "known_hosts_policy": ssh.known_hosts_policy,
            "local_port": ssh.local_port,
            "password_auth_flagged": bool(ssh_password) or ssh.password_auth_flagged or ssh.auth_mode == "password",
            "host_key_sha256": ssh.host_key_sha256,
            "password_ref": ssh.password_ref,
        }
        if ssh_password:
            sid = ssh.password_ref or new_id("s")
            cipher = protect_secret(ssh_password.encode("utf-8"), storage=secret_mode)
            self.store.put_secret(SecretRef(id=sid, kind="ssh_password", storage=secret_mode, cipher=cipher))  # type: ignore[arg-type]
            data["password_ref"] = sid
            data["password_auth_flagged"] = True
        if ssh_passphrase:
            sid = ssh.passphrase_ref or new_id("s")
            cipher = protect_secret(ssh_passphrase.encode("utf-8"), storage=secret_mode)
            self.store.put_secret(SecretRef(id=sid, kind="ssh_key_passphrase", storage=secret_mode, cipher=cipher))  # type: ignore[arg-type]
            data["passphrase_ref"] = sid
        return ConnectionProfile(**{**_profile_dict(profile), "ssh": SshConfig(**data)})  # type: ignore[arg-type]

    def delete(self, session: SessionContext, connection_id: str) -> None:
        check_or_raise(session, Action.MANAGE_CONNECTION, grants=self.grants)
        profile = self.store.get_connection(connection_id)
        self.store.delete_connection(connection_id)
        if profile:
            self._audit(session, profile, "profile_change", "allow", None)

    def grant(self, session: SessionContext, connection_id: str, role: str) -> None:
        check_or_raise(session, Action.GRANT_ACCESS, grants=self.grants)
        self.store.grant(connection_id, role, session.username)
        profile = self.store.get_connection(connection_id)
        if profile:
            self._audit(session, profile, "grant_access", "allow", None)

    def test_connect(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        *,
        password: memoryview | bytes | str | None = None,
        ssh_password: memoryview | bytes | str | None = None,
        ssh_passphrase: memoryview | bytes | str | None = None,
        require_grant: bool = True,
        ssh_client=None,
    ) -> AclProbeResult:
        """测试当前表单或已保存连接。draft（未保存）时 require_grant=False，只需具备管理连接权限。"""
        if require_grant:
            check_or_raise(session, Action.CONNECT, profile, grants=self.grants)
        else:
            check_or_raise(session, Action.MANAGE_CONNECTION, grants=self.grants)
            self._validate_tls_for_test(profile)
        if self.audit.is_chain_broken() or not self.audit._writable:
            raise AppError(ErrorCode.E13, "审计不可写，已拒绝建立连接")
        event = "connect_plaintext" if (not profile.tls.enabled and profile.tls.allow_plaintext) else "connect"
        self._audit(session, profile, event, "allow", "pending")
        try:
            return self._run_probe(
                profile,
                password,
                ssh_password=ssh_password,
                ssh_passphrase=ssh_passphrase,
                ssh_client=ssh_client,
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(
                exc,
                host=profile.host,
                port=profile.port,
                tls_enabled=bool(profile.tls and profile.tls.enabled),
            ) from exc

    def _validate_tls_for_test(self, profile: ConnectionProfile) -> None:
        tls = profile.tls
        if tls.enabled:
            return
        if tls.allow_plaintext and tls.plaintext_reason and len(tls.plaintext_reason) >= PLAINTEXT_REASON_MIN_CHARS:
            return
        raise AppError(
            ErrorCode.E08,
            f"请填写不少于 {PLAINTEXT_REASON_MIN_CHARS} 字的明文例外理由。",
        )

    def _run_probe(
        self,
        profile: ConnectionProfile,
        password: memoryview | bytes | str | None,
        *,
        ssh_password: memoryview | bytes | str | None = None,
        ssh_passphrase: memoryview | bytes | str | None = None,
        ssh_client=None,
    ) -> AclProbeResult:
        redis_secret = _as_memoryview(password)
        return self._open_and_probe(
            profile,
            redis_secret,
            ssh_password=ssh_password,
            ssh_passphrase=ssh_passphrase,
            ssh_client=ssh_client,
        )

    def _open_and_probe(
        self,
        profile: ConnectionProfile,
        password: memoryview | None,
        *,
        ssh_password: memoryview | bytes | str | None = None,
        ssh_passphrase: memoryview | bytes | str | None = None,
        ssh_client=None,
    ) -> AclProbeResult:
        try:
            if getattr(self, "store", None) is not None:
                gw = open_gateway_with_secrets(
                    self.store,
                    profile,
                    password=password,
                    ssh_password=ssh_password,
                    ssh_passphrase=ssh_passphrase,
                    ssh_client=ssh_client,
                )
            else:
                gw = open_gateway(
                    profile,
                    password=password,
                    ssh_password=ssh_password,
                    ssh_passphrase=ssh_passphrase,
                    ssh_client=ssh_client,
                )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(
                exc,
                host=profile.host,
                port=profile.port,
                tls_enabled=bool(profile.tls and profile.tls.enabled),
            ) from exc
        try:
            gw.ping()
            result = probe(gw)
            return result
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(
                exc,
                host=profile.host,
                port=profile.port,
                tls_enabled=bool(profile.tls and profile.tls.enabled),
            ) from exc
        finally:
            gw.close()


    def fetch_info(self, session: SessionContext, profile: ConnectionProfile) -> dict[str, str]:
        """只读拉取 INFO。失败时抛出中文 AppError，不把空值伪装成已采集数据。"""
        check_or_raise(session, Action.CONNECT, profile, grants=self.grants)
        if self.audit.is_chain_broken() or not self.audit._writable:
            raise AppError(ErrorCode.E13, "审计不可写，已拒绝建立连接")
        try:
            gw = open_gateway_with_secrets(self.store, profile)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(
                exc,
                host=profile.host,
                port=profile.port,
                tls_enabled=bool(profile.tls and profile.tls.enabled),
            ) from exc
        try:
            return gw.info()
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(
                exc,
                host=profile.host,
                port=profile.port,
                tls_enabled=bool(profile.tls and profile.tls.enabled),
            ) from exc
        finally:
            gw.close()

    def _audit(self, session: SessionContext, profile: ConnectionProfile, event: str, decision: str, result: str | None) -> None:
        self.audit.append(
            {
                "actor_user_id": session.user_id,
                "actor_username": session.username,
                "actor_role": session.role,
                "client_host": "localhost",
                "client_ip": None,
                "session_id": session.session_id,
                "connection_id": profile.id,
                "connection_name": profile.name,
                "env": profile.env,
                "host": profile.host,
                "port": profile.port,
                "event": event,
                "command_name": None,
                "argv_masked": None,
                "argv_digest": None,
                "risk_level": "L3" if event == "connect_plaintext" else None,
                "decision": decision,
                "deny_reason": None,
                "result": result,
                "duration_ms": None,
                "impact_estimate": profile.tls.plaintext_reason if event == "high_risk_plaintext" else None,
                "error_code": None,
            }
        )


def _profile_dict(profile: ConnectionProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "topology": profile.topology,
        "transport": profile.transport,
        "endpoint": profile.endpoint,
        "db": profile.db,
        "username": profile.username,
        "secret_ref": profile.secret_ref,
        "tls": profile.tls,
        "ssh": profile.ssh,
        "sentinel": profile.sentinel,
        "cluster": profile.cluster,
        "env": profile.env,
        "protection": profile.protection,
        "allow_l4": profile.allow_l4,
        "tags": profile.tags,
        "note": profile.note,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _as_memoryview(password: memoryview | bytes | str | None) -> memoryview | None:
    if password is None:
        return None
    if isinstance(password, memoryview):
        return password if password.nbytes else None
    if isinstance(password, str):
        raw = password.encode("utf-8") if password else b""
        return memoryview(raw) if raw else None
    return memoryview(password) if password else None


def _lease_optional(store, secret_id: str | None):
    if not secret_id:
        return None
    return store.get_secret(secret_id)


def open_gateway_with_secrets(
    store,
    profile: ConnectionProfile,
    *,
    password: memoryview | bytes | str | None = None,
    ssh_password: memoryview | bytes | str | None = None,
    ssh_passphrase: memoryview | bytes | str | None = None,
    ssh_client=None,
):
    """???????????????????? Redis / SSH ???"""
    redis_pw = _as_memoryview(password)
    ssh_pw = _as_memoryview(ssh_password) if not isinstance(ssh_password, memoryview) else ssh_password
    ssh_ph = _as_memoryview(ssh_passphrase) if not isinstance(ssh_passphrase, memoryview) else ssh_passphrase

    redis_ref = _lease_optional(store, profile.secret_ref) if redis_pw is None else None
    ssh_pw_ref = None
    ssh_ph_ref = None
    if profile.transport == "ssh" and profile.ssh is not None:
        if ssh_pw is None:
            ssh_pw_ref = _lease_optional(store, profile.ssh.password_ref)
        if ssh_ph is None:
            ssh_ph_ref = _lease_optional(store, profile.ssh.passphrase_ref)

    # Nested leases only when stored secrets are used.
    def _open(redis_lease, ssh_pw_lease, ssh_ph_lease):
        return open_gateway(
            profile,
            password=redis_lease,
            ssh_password=ssh_pw_lease,
            ssh_passphrase=ssh_ph_lease,
            ssh_client=ssh_client,
        )

    if redis_ref is None and ssh_pw_ref is None and ssh_ph_ref is None:
        return _open(redis_pw, ssh_pw, ssh_ph)
    if redis_ref is not None:
        with SecretLease(redis_ref) as redis_lease:
            return _open_ssh_leases(store, profile, redis_lease, ssh_pw, ssh_ph, ssh_pw_ref, ssh_ph_ref, ssh_client)
    return _open_ssh_leases(store, profile, redis_pw, ssh_pw, ssh_ph, ssh_pw_ref, ssh_ph_ref, ssh_client)


def _open_ssh_leases(store, profile, redis_lease, ssh_pw, ssh_ph, ssh_pw_ref, ssh_ph_ref, ssh_client):
    if ssh_pw_ref is not None:
        with SecretLease(ssh_pw_ref) as leased_ssh_pw:
            if ssh_ph_ref is not None:
                with SecretLease(ssh_ph_ref) as leased_ph:
                    return open_gateway(
                        profile,
                        password=redis_lease,
                        ssh_password=leased_ssh_pw,
                        ssh_passphrase=leased_ph,
                        ssh_client=ssh_client,
                    )
            return open_gateway(
                profile,
                password=redis_lease,
                ssh_password=leased_ssh_pw,
                ssh_passphrase=ssh_ph,
                ssh_client=ssh_client,
            )
    if ssh_ph_ref is not None:
        with SecretLease(ssh_ph_ref) as leased_ph:
            return open_gateway(
                profile,
                password=redis_lease,
                ssh_password=ssh_pw,
                ssh_passphrase=leased_ph,
                ssh_client=ssh_client,
            )
    return open_gateway(
        profile,
        password=redis_lease,
        ssh_password=ssh_pw,
        ssh_passphrase=ssh_ph,
        ssh_client=ssh_client,
    )


