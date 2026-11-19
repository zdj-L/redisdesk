"""命令控制台：输入、历史输出与风险确认区。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.ui.dialogs import ConfirmRiskDialog
from redisdesk.app.ui.i18n import env_label, protection_label
from redisdesk.app.ui.theme import current_palette
from redisdesk.app.ui.widgets import PageFrame
from redisdesk.core.errors import AppError
from redisdesk.core.model import ConnectionProfile, SessionContext
from redisdesk.core.parser import ParseError


class ConsolePage(QWidget):
    def __init__(self, service: ConsoleService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.session: SessionContext | None = None
        self.profile: ConnectionProfile | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "写操作只走控制台。发送前经过风险分级与审计。生产环境禁止 FLUSHALL / KEYS。",
        )

        self.context = QLabel("尚未选择连接。")
        self.context.setObjectName("inlineInfo")
        self.context.setWordWrap(True)
        self.page.add_body(self.context, 0)

        self.output = QTextEdit()
        self.output.setObjectName("mono")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("命令与结果将按时间顺序显示在这里。")
        self.page.add_body(self.output, 1)

        self.risk = QLabel("")
        self.risk.setWordWrap(True)
        self.risk.hide()
        self.page.add_body(self.risk, 0)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("输入一条 Redis 命令，例如 GET token:abc 或 SCAN 0 MATCH cache:* COUNT 1000")
        self.input.setFixedHeight(88)
        self.input.setObjectName("mono")
        self.page.add_body(self.input, 0)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.run = QPushButton("执行")
        self.run.setObjectName("primary")
        self.run.clicked.connect(self._run)
        self.clear = QPushButton("清空输出")
        self.clear.clicked.connect(self.output.clear)
        self.hint = QLabel("Enter 换行，点击「执行」发送。危险命令会先出现确认区。")
        self.hint.setObjectName("hint")
        row.addWidget(self.run)
        row.addWidget(self.clear)
        row.addWidget(self.hint, 1)
        self.page.add_body(bar, 0)
        root.addWidget(self.page)

    def set_context(self, session: SessionContext, profile: ConnectionProfile | None) -> None:
        self.session = session
        self.profile = profile
        if profile:
            extra = " · 明文连接" if not profile.tls.enabled else ""
            self.context.setText(
                f"当前连接：{profile.name}    环境：{env_label(profile.env)}    "
                f"档位：{protection_label(profile.protection)}{extra}"
            )
            self.context.setObjectName("inlineInfo" if profile.tls.enabled else "inlineWarn")
            self.context.style().unpolish(self.context)
            self.context.style().polish(self.context)
        else:
            self.context.setText("尚未选择连接。请先在顶部选择一条已授权连接。")
            self.context.setObjectName("inlineInfo")
            self.context.style().unpolish(self.context)
            self.context.style().polish(self.context)
        self.run.setEnabled(profile is not None)
        self.risk.hide()

    def _append(self, html: str) -> None:
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertHtml(html + "<br>")
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def _tone(self, kind: str) -> str:
        tokens = current_palette()
        return {
            "muted": tokens["text_muted"],
            "cmd": tokens["accent"],
            "ok": tokens["success"],
            "warn": tokens["warn"],
            "err": tokens["danger"],
            "text": tokens["text_primary"],
        }.get(kind, tokens["text_primary"])

    def _run(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        raw = self.input.toPlainText().strip()
        stamp = datetime.now().strftime("%H:%M:%S")
        if self.session is None or self.profile is None:
            self._append(f'<span style="color:{self._tone("muted")}">[{stamp}]</span> '
                         f'<span style="color:{self._tone("err")}">尚未选择连接，命令未发送。</span>')
            QMessageBox.information(self, "尚未连接", "请先在顶部选择并连接一个已授权的连接。")
            return
        if not raw:
            return
        try:
            prepared = self.service.prepare(self.session, self.profile, raw)
        except (AppError, ParseError) as exc:
            message = getattr(exc, "message", str(exc))
            self._append(
                f'<span style="color:{self._tone("muted")}">[{stamp}]</span> '
                f'<span style="color:{self._tone("cmd")}">$ {self._escape(raw)}</span><br>'
                f'<span style="color:{self._tone("err")}">已拒绝：{self._escape(message)}</span>'
            )
            self.risk.setObjectName("inlineDanger")
            self.risk.setText(f"命令被拒绝或解析失败：{message}")
            self.risk.style().unpolish(self.risk)
            self.risk.style().polish(self.risk)
            self.risk.show()
            return
        decision = prepared.policy
        command_text = " ".join(prepared.argv)
        if decision.decision in {"confirm", "typed_confirm"}:
            self.risk.setObjectName("inlineWarn")
            self.risk.setText(f"需确认：{decision.assessment.reason}")
            self.risk.style().unpolish(self.risk)
            self.risk.style().polish(self.risk)
            self.risk.show()
            dlg = ConfirmRiskDialog(
                title=f"即将在「{self.profile.name}」执行 {prepared.argv[0]}",
                command=command_text,
                consequence=decision.assessment.reason,
                typed_text=self.profile.name,
                require_typed=decision.requires_typed_confirm or decision.decision == "typed_confirm",
                parent=self,
            )
            if not dlg.exec():
                self._append(
                    f'<span style="color:{self._tone("muted")}">[{stamp}]</span> '
                    f'<span style="color:{self._tone("cmd")}">$ {self._escape(command_text)}</span><br>'
                    f'<span style="color:{self._tone("warn")}">用户取消确认，命令未发送。</span>'
                )
                return
        else:
            self.risk.hide()
        self._append(
            f'<span style="color:{self._tone("muted")}">[{stamp}]</span> '
            f'<span style="color:{self._tone("cmd")}">$ {self._escape(command_text)}</span><br>'
            f'<span style="color:{self._tone("ok")}>已通过授权与风险判定。</span> '
            f'<span style="color:{self._tone("muted")}">真正发送需要已建立的 Redis 连接（请先完成连接测试）。</span>'
        )

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
