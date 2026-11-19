"""本机页：应用信息、数据目录与运行状态，不再作为账号/角色管理面板。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from redisdesk import __version__
from redisdesk.app.ui.widgets import Card, KeyValueTable, PageFrame
from redisdesk.core.model import SessionContext
from redisdesk.infra.paths import app_data_dir, db_path, log_dir


class UsersPage(QWidget):
    def __init__(self, store, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "本机数据目录与运行状态。此应用无需登录，打开后即可使用。",
        )

        info = Card("应用与数据")
        self.kv = KeyValueTable()
        info.add_body(self.kv)
        self.page.add_body(info, 0)

        policy = Card("本地安全策略")
        hint = QLabel(
            "连接默认启用 TLS。浏览只走 SCAN，不会调用 KEYS。"
            "写操作只通过控制台发送，并经过风险确认与审计。"
            "审计链异常时进入只读应急，拒绝继续写入。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        policy.add_body(hint)
        self.page.add_body(policy, 0)
        self.page.body.addStretch(1)
        root.addWidget(self.page)
        self._show_static()

    def _show_static(self) -> None:
        self.kv.set_pairs(
            [
                ("应用版本", __version__),
                ("数据目录", str(app_data_dir())),
                ("数据库文件", str(db_path())),
                ("日志目录", str(log_dir())),
                ("运行状态", "已就绪"),
            ]
        )

    def set_session(self, session: SessionContext) -> None:
        # 保留入口以便主窗口绑定会话，但不展示账号或角色身份。
        self._show_static()
        if session is not None:
            self.kv.set_pairs(
                [
                    ("应用版本", __version__),
                    ("数据目录", str(app_data_dir())),
                    ("数据库文件", str(db_path())),
                    ("日志目录", str(log_dir())),
                    ("运行状态", "已就绪"),
                ]
            )
