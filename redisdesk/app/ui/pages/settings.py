"""设置页：分组表单，界面偏好、只读安全参数与 SSH 已知主机管理。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.ui.theme import THEME_CHOICES, UiSettings
from redisdesk.app.ui.dialog_kit import StyledComboBox
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.app.ui.widgets import DataTable, FormSection, PageFrame
from redisdesk.core.errors import AppError
from redisdesk.core.limits import AUDIT_RETENTION_DAYS, VALUE_PAGE_SIZE
from redisdesk.infra.known_hosts import KnownHostEntry, KnownHostsStore
from redisdesk.infra.paths import known_hosts_path


class SettingsPage(QWidget):
    theme_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ui_settings = UiSettings()
        self._known_worker: FunctionWorker | None = None
        self._known_entries: list[KnownHostEntry] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "\u53ef\u4fee\u6539\u754c\u9762\u504f\u597d\u3002\u5b89\u5168\u4e0a\u9650\u7531\u4ea7\u54c1\u7b56\u7565\u786c\u7f16\u7801\uff0c\u4e0d\u80fd\u5728\u672c\u9875\u653e\u5bbd\u3002SSH \u5df2\u77e5\u4e3b\u673a\u4fdd\u5b58\u5728\u672c\u5e94\u7528\u6570\u636e\u76ee\u5f55\u3002",
        )

        appearance = FormSection("\u5916\u89c2", "\u6d45\u8272\u4e3a\u4f01\u4e1a\u9ed8\u8ba4\u4e3b\u9898\uff0c\u4e5f\u53ef\u5207\u6362\u6df1\u8272\u3001\u68ee\u6797\u7eff\u7b49\u914d\u8272\uff0c\u9002\u5408\u65e5\u5e38\u8fd0\u7ef4\u67e5\u770b\u3002")
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.theme = StyledComboBox()
        self.theme.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        for value, label in THEME_CHOICES:
            self.theme.addItem(label, value)
        self.theme.fit_to_contents()
        current = self.ui_settings.get("theme", "light")
        index = self.theme.findData(current)
        self.theme.setCurrentIndex(index if index >= 0 else 0)
        self.theme.currentIndexChanged.connect(self._on_theme)
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.addWidget(self.theme, 0)
        theme_row.addStretch(1)
        form.addRow("\u4e3b\u9898", theme_row)
        appearance.body.addLayout(form)
        self.page.add_body(appearance, 0)

        browse = FormSection("\u6d4f\u89c8\u9ed8\u8ba4\u503c", "\u4ee5\u4e0b\u4e3a\u5f53\u524d\u7248\u672c\u7684\u9ed8\u8ba4\u5206\u9875\u53c2\u6570\uff0c\u4ec5\u4f5c\u8bf4\u660e\u3002")
        browse_form = QFormLayout()
        browse_form.setHorizontalSpacing(16)
        browse_form.setVerticalSpacing(8)
        browse_form.addRow("SCAN COUNT", QLabel("1000\uff08\u786c\u7f16\u7801\u9ed8\u8ba4\uff0c\u6d4f\u89c8\u9875\u5de5\u5177\u680f\u53ef\u4e34\u65f6\u8c03\u6574\uff09"))
        browse_form.addRow("\u503c\u5206\u9875\u5927\u5c0f", QLabel(str(VALUE_PAGE_SIZE)))
        browse.body.addLayout(browse_form)
        self.page.add_body(browse, 0)

        security = FormSection(
            "\u5b89\u5168\u4e0e\u5ba1\u8ba1",
            "\u672c\u5e94\u7528\u65e0\u9700\u767b\u5f55\uff0c\u542f\u52a8\u540e\u76f4\u63a5\u8fdb\u5165\u5de5\u4f5c\u53f0\u3002\u4ee5\u4e0b\u4e3a\u53ea\u8bfb\u7684\u4ea7\u54c1\u53c2\u6570\u3002",
        )
        sec_form = QFormLayout()
        sec_form.setHorizontalSpacing(16)
        sec_form.setVerticalSpacing(8)
        sec_form.addRow("\u542f\u52a8\u65f6\u65b9\u5f0f", QLabel("\u76f4\u63a5\u8fdb\u5165\u5de5\u4f5c\u53f0\uff0c\u65e0\u9700\u767b\u5f55"))
        sec_form.addRow("\u5ba1\u8ba1\u4fdd\u7559\uff08\u5929\uff09", QLabel(str(AUDIT_RETENTION_DAYS)))
        security.body.addLayout(sec_form)
        self.page.add_body(security, 0)

        known = FormSection(
            "SSH \u5df2\u77e5\u4e3b\u673a",
            "\u9996\u6b21\u8fde\u63a5\u8df3\u677f\u65f6\uff0c\u82e5\u7b56\u7565\u4e3a\u300c\u9996\u6b21\u786e\u8ba4\u300d\uff0c\u4f1a\u5c55\u793a SHA-256 \u6307\u7eb9\u4f9b\u4eba\u5de5\u6838\u5bf9\uff0c\u786e\u8ba4\u540e\u5199\u5165\u4e0b\u65b9\u6587\u4ef6\u3002"
            "\u7cfb\u7edf ~/.ssh/known_hosts \u4ec5\u4f5c\u53ea\u8bfb\u53c2\u8003\uff0c\u4e0d\u4f1a\u88ab\u672c\u5e94\u7528\u6539\u5199\u3002"
            "\u5bc6\u94a5\u53d8\u66f4\u4f1a\u88ab\u62d2\u7edd\uff0c\u4e0d\u63d0\u4f9b\u300c\u5ffd\u7565\u5e76\u7ee7\u7eed\u300d\uff1b\u6838\u5bf9\u5b89\u5168\u540e\u53ef\u5220\u9664\u5bf9\u5e94\u6761\u76ee\u5e76\u91cd\u65b0\u767b\u8bb0\u3002",
        )
        self.known_path = QLabel(str(known_hosts_path()))
        self.known_path.setObjectName("hint")
        self.known_path.setWordWrap(True)
        self.known_path.setTextInteractionFlags(self.known_path.textInteractionFlags() | self.known_path.textInteractionFlags().TextSelectableByMouse)
        path_form = QFormLayout()
        path_form.setHorizontalSpacing(16)
        path_form.setVerticalSpacing(8)
        path_form.addRow("\u5b58\u50a8\u6587\u4ef6", self.known_path)
        known.body.addLayout(path_form)
        self.known_table = DataTable(["\u4e3b\u673a", "\u7aef\u53e3", "\u5bc6\u94a5\u7c7b\u578b", "\u6307\u7eb9"])
        self.known_table.setObjectName("knownHostsTable")
        self.known_table.setMinimumHeight(200)
        self.known_table.setMaximumHeight(280)
        self.known_table.itemSelectionChanged.connect(self._sync_known_actions)
        known.body.addWidget(self.known_table)
        self.known_status = QLabel("\u6682\u65e0\u5df2\u767b\u8bb0\u7684\u4e3b\u673a\u5bc6\u94a5\u3002")
        self.known_status.setObjectName("hint")
        self.known_status.setWordWrap(True)
        known.body.addWidget(self.known_status)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.refresh_known_btn = QPushButton("\u5237\u65b0")
        self.refresh_known_btn.setObjectName("tool")
        self.refresh_known_btn.clicked.connect(self.reload_known_hosts)
        self.delete_known_btn = QPushButton("\u5220\u9664\u6240\u9009")
        self.delete_known_btn.setObjectName("tool")
        self.delete_known_btn.clicked.connect(self._delete_selected_known_host)
        actions.addWidget(self.refresh_known_btn, 0)
        actions.addWidget(self.delete_known_btn, 0)
        actions.addStretch(1)
        known.body.addLayout(actions)
        self.page.add_body(known, 0)
        self.page.body.addStretch()
        root.addWidget(self.page)
        self._sync_known_actions()
        self.reload_known_hosts()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.reload_known_hosts()

    def _on_theme(self) -> None:
        mode = self.theme.currentData() or "light"
        self.ui_settings.set("theme", mode)
        self.theme_changed.emit(mode)

    def reload_known_hosts(self) -> None:
        if self._known_worker is not None and self._known_worker.isRunning():
            return

        def work():
            store = KnownHostsStore()
            return store.list_entries(), str(store.path)

        worker = FunctionWorker(work, parent=self)
        self._known_worker = worker
        worker.signals.finished.connect(self._on_known_loaded)
        worker.signals.failed.connect(self._on_known_load_fail)
        worker.start()

    def _on_known_loaded(self, result) -> None:  # noqa: ANN001
        self._known_worker = None
        entries, path = result
        self._known_entries = list(entries)
        self.known_path.setText(path)
        self.known_table.set_rows(
            [(item.host, item.port, item.key_type, item.fingerprint) for item in self._known_entries]
        )
        if self._known_entries:
            self.known_status.setText(f"\u5171 {len(self._known_entries)} \u6761\u3002\u5220\u9664\u524d\u8bf7\u5148\u6838\u5bf9\u8df3\u677f\u662f\u5426\u786e\u5b9e\u66f4\u6362\u4e86\u5bc6\u94a5\u3002")
        else:
            self.known_status.setText("\u6682\u65e0\u5df2\u767b\u8bb0\u7684\u4e3b\u673a\u5bc6\u94a5\u3002\u4f7f\u7528 SSH \u8df3\u677f\u5e76\u9009\u62e9\u300c\u9996\u6b21\u786e\u8ba4\u300d\u65f6\uff0c\u9996\u6b21\u8fde\u63a5\u4f1a\u5c55\u793a\u6307\u7eb9\uff0c\u786e\u8ba4\u540e\u5199\u5165\u672c\u6587\u4ef6\u3002")
        self._sync_known_actions()

    def _on_known_load_fail(self, exc) -> None:  # noqa: ANN001
        self._known_worker = None
        message = exc.message if isinstance(exc, AppError) else str(exc)
        self.known_status.setText("\u65e0\u6cd5\u8bfb\u53d6\u5df2\u77e5\u4e3b\u673a\uff1a{0}".format(message or type(exc).__name__))
        self._known_entries = []
        self.known_table.set_rows([])
        self._sync_known_actions()

    def _selected_entry(self) -> KnownHostEntry | None:
        row = self.known_table.selected_row_index()
        if row < 0 or row >= len(self._known_entries):
            return None
        return self._known_entries[row]

    def _sync_known_actions(self) -> None:
        self.delete_known_btn.setEnabled(self._selected_entry() is not None)

    def _delete_selected_known_host(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        loc = f"{entry.host}:{entry.port}"
        question = (
            f"\u786e\u5b9a\u5220\u9664\u300c{loc}\u300d\u7684 {entry.key_type} \u6761\u76ee\uff1f"
            "\u5220\u9664\u540e\u518d\u6b21\u8fde\u63a5\u9700\u91cd\u65b0\u6838\u5bf9\u6307\u7eb9\u3002"
            "\u7cfb\u7edf ~/.ssh/known_hosts \u4e0d\u4f1a\u88ab\u6539\u5199\u3002"
        )
        if QMessageBox.question(self, "\u5220\u9664\u5df2\u77e5\u4e3b\u673a", question) != QMessageBox.StandardButton.Yes:
            return

        def work(item=entry):
            return KnownHostsStore().remove(item.host, item.port, item.key_type)

        worker = FunctionWorker(work, parent=self)
        self._known_worker = worker
        worker.signals.finished.connect(self._on_known_deleted)
        worker.signals.failed.connect(self._on_known_delete_fail)
        worker.start()

    def _on_known_deleted(self, _result) -> None:  # noqa: ANN001
        self._known_worker = None
        self.reload_known_hosts()

    def _on_known_delete_fail(self, exc) -> None:  # noqa: ANN001
        self._known_worker = None
        message = exc.message if isinstance(exc, AppError) else str(exc)
        QMessageBox.warning(self, "\u5220\u9664\u5931\u8d25", message or type(exc).__name__)
        self.reload_known_hosts()
