"""设置页：分组表单，仅界面偏好与只读展示的安全参数。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from redisdesk.app.ui.theme import THEME_CHOICES, UiSettings
from redisdesk.app.ui.dialog_kit import StyledComboBox
from redisdesk.app.ui.widgets import FormSection, PageFrame
from redisdesk.core.limits import AUDIT_RETENTION_DAYS, VALUE_PAGE_SIZE


class SettingsPage(QWidget):
    theme_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ui_settings = UiSettings()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "可修改界面偏好。安全上限由产品策略硬编码，不能在本页放宽。",
        )

        appearance = FormSection("外观", "浅色为企业默认主题，也可切换深色、森林绿等配色，适合日常运维查看。")
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
        form.addRow("主题", theme_row)
        appearance.body.addLayout(form)
        self.page.add_body(appearance, 0)

        browse = FormSection("浏览默认值", "以下为当前版本的默认分页参数，仅作说明。")
        browse_form = QFormLayout()
        browse_form.setHorizontalSpacing(16)
        browse_form.setVerticalSpacing(8)
        browse_form.addRow("SCAN COUNT", QLabel("1000（硬编码默认，浏览页工具栏可临时调整）"))
        browse_form.addRow("值分页大小", QLabel(str(VALUE_PAGE_SIZE)))
        browse.body.addLayout(browse_form)
        self.page.add_body(browse, 0)

        security = FormSection(
            "安全与审计",
            "本应用无需登录，启动后直接进入工作台。以下为只读的产品参数。",
        )
        sec_form = QFormLayout()
        sec_form.setHorizontalSpacing(16)
        sec_form.setVerticalSpacing(8)
        sec_form.addRow("启动方式", QLabel("直接进入工作台，无需登录"))
        sec_form.addRow("审计保留（天）", QLabel(str(AUDIT_RETENTION_DAYS)))
        security.body.addLayout(sec_form)
        self.page.add_body(security, 0)
        self.page.body.addStretch()
        root.addWidget(self.page)

    def _on_theme(self) -> None:
        mode = self.theme.currentData() or "light"
        self.ui_settings.set("theme", mode)
        self.theme_changed.emit(mode)
