"""First-connect host-key confirmation; mismatch is reject-only."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from redisdesk.app.ui.dialog_kit import ScrollableDialog
from redisdesk.infra.known_hosts import HostKeyMismatchError, KnownHostEntry, KnownHostsStore, UnknownHostKeyError


class HostKeyConfirmDialog(ScrollableDialog):
    def __init__(self, error: UnknownHostKeyError, parent: QWidget | None = None) -> None:
        super().__init__(
            "\u786e\u8ba4 SSH \u4e3b\u673a\u5bc6\u94a5",
            parent,
            width=520,
            ok_text="\u4fe1\u4efb\u5e76\u767b\u8bb0",
            cancel_text="\u53d6\u6d88",
            ok_role="primary",
            default_ok=False,
        )
        loc = QLabel(f"{error.host}:{error.port}")
        loc.setObjectName("sectionTitle")
        loc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(loc)
        intro = QLabel(
            "\u8fd9\u662f\u9996\u6b21\u8fde\u63a5\u8be5\u8df3\u677f\uff0c\u8bf7\u628a\u4e0b\u65b9\u6307\u7eb9\u4e0e\u8df3\u677f\u7ba1\u7406\u5458\u63d0\u4f9b\u7684\u503c\u6838\u5bf9\u3002"
            "\u786e\u8ba4\u540e\u4ec5\u5199\u5165\u672c\u5e94\u7528\u6570\u636e\u76ee\u5f55\u4e2d\u7684 known_hosts\uff0c\u4e0d\u4f1a\u6539\u5199\u7cfb\u7edf ~/.ssh/known_hosts\u3002"
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        self.body.addWidget(intro)
        typ = QLabel(f"\u5bc6\u94a5\u7c7b\u578b\uff1a{error.key_type}")
        typ.setWordWrap(True)
        typ.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(typ)
        fp = QLabel(error.fingerprint)
        fp.setObjectName("mono")
        fp.setWordWrap(True)
        fp.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(fp)
        note = QLabel(
            "\u82e5\u540e\u7eed\u6307\u7eb9\u53d8\u66f4\uff0c\u8fde\u63a5\u4f1a\u88ab\u62d2\u7edd\uff0c\u4e14\u4e0d\u63d0\u4f9b\u300c\u5ffd\u7565\u5e76\u7ee7\u7eed\u300d\u3002"
            "\u53ef\u5728\u300c\u8bbe\u7f6e \u2192 SSH \u5df2\u77e5\u4e3b\u673a\u300d\u4e2d\u67e5\u770b\u6216\u5220\u9664\u6761\u76ee\u3002"
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        self.body.addWidget(note)


def register_unknown_host_key(error: UnknownHostKeyError, *, service=None, session=None, profile=None) -> KnownHostEntry:
    entry = KnownHostsStore().add(error.host, error.port, error.key)
    if service is not None and session is not None and profile is not None and getattr(profile, "id", None):
        pin = getattr(service, "pin_ssh_host_key", None)
        if callable(pin):
            pin(session, profile, entry.fingerprint)
    return entry


def prompt_unknown_host_key(parent: QWidget | None, error: UnknownHostKeyError, *, service=None, session=None, profile=None) -> bool:
    """Return True if the user accepted and the key was stored (caller should retry)."""
    if error.policy != "tofu":
        QMessageBox.warning(parent, "SSH \u4e3b\u673a\u5bc6\u94a5\u672a\u767b\u8bb0", error.message + (("\n" + error.hint) if error.hint else ""))
        return False
    dialog = HostKeyConfirmDialog(error, parent)
    if dialog.exec() != dialog.DialogCode.Accepted:
        return False
    register_unknown_host_key(error, service=service, session=session, profile=profile)
    return True


def notify_host_key_mismatch(parent: QWidget | None, error: HostKeyMismatchError) -> None:
    details = error.message
    if error.hint:
        details = details + "\n\n" + error.hint
    QMessageBox.warning(parent, "SSH \u4e3b\u673a\u5bc6\u94a5\u4e0d\u5339\u914d", details)


def handle_host_key_error(parent: QWidget | None, exc: BaseException, *, service=None, session=None, profile=None) -> bool:
    if isinstance(exc, UnknownHostKeyError):
        return prompt_unknown_host_key(parent, exc, service=service, session=session, profile=profile)
    if isinstance(exc, HostKeyMismatchError):
        notify_host_key_mismatch(parent, exc)
    return False
