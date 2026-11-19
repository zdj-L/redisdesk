"""通用界面组件：页面框架、卡片、表格、空状态、徽章、分区表单。"""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QAbstractScrollArea,
    QHeaderView,
    QStyle,
    QStyleOptionHeader,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QSizePolicy,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.ui.icons import make_icon
from redisdesk.app.ui.theme import SPACE, apply_elevation, badge_qss, current_palette


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    color = current_palette()["border"]
    line.setStyleSheet(f"background: transparent; border: none; border-top: 1px solid {color};")
    return line


def localize_dialog_buttons(box: QDialogButtonBox) -> None:
    labels = {
        QDialogButtonBox.StandardButton.Ok: "确定",
        QDialogButtonBox.StandardButton.Cancel: "取消",
        QDialogButtonBox.StandardButton.Save: "保存",
        QDialogButtonBox.StandardButton.Yes: "是",
        QDialogButtonBox.StandardButton.No: "否",
        QDialogButtonBox.StandardButton.Close: "关闭",
        QDialogButtonBox.StandardButton.Apply: "应用",
        QDialogButtonBox.StandardButton.Retry: "重试",
    }
    for standard, text in labels.items():
        button = box.button(standard)
        if button is not None:
            button.setText(text)


class Card(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None, *, elevated: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 20, 20, 20)
        self._layout.setSpacing(SPACE)
        self._title = QLabel(title)
        self._title.setObjectName("sectionTitle")
        if title:
            self._layout.addWidget(self._title)
        else:
            self._title.hide()
        if elevated:
            apply_elevation(self, level="sm")
        else:
            self.setProperty("noElevation", True)
            apply_elevation(self, level="none")

    def set_title(self, title: str) -> None:
        self._title.setText(title)
        self._title.setVisible(bool(title))

    def add_body(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    @property
    def body_layout(self) -> QVBoxLayout:
        return self._layout


class FormSection(QFrame):
    """设置页等场景的分组区块。"""

    def __init__(self, title: str, description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formSection")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(SPACE)
        head = QLabel(title)
        head.setObjectName("sectionTitle")
        root.addWidget(head)
        if description:
            hint = QLabel(description)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            root.addWidget(hint)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 4, 0, 0)
        self.body.setSpacing(SPACE)
        root.addLayout(self.body)
        apply_elevation(self, level="sm")


class PageFrame(QWidget):
    """页头以说明文案 + 右侧操作为主，避免再把导航名放大写一遍。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(16)

        header = QWidget()
        header.setObjectName("pageHeader")
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(0, 0, 0, 0)
        header_l.setSpacing(16)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        self.title_label.setVisible(bool(title))
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)

        self.actions_host = QWidget()
        self.actions_host.setObjectName("pageActions")
        self.actions = QHBoxLayout(self.actions_host)
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        self.actions_host.hide()

        header_l.addLayout(titles, 1)
        header_l.addWidget(self.actions_host, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(header)

        self.toolbar_host = QWidget()
        self.toolbar_host.setObjectName("filterBar")
        self.toolbar = QHBoxLayout(self.toolbar_host)
        self.toolbar.setContentsMargins(0, 0, 0, 0)
        self.toolbar.setSpacing(10)
        self.toolbar_host.hide()
        root.addWidget(self.toolbar_host)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)
        root.addLayout(self.body, 1)

    def set_title(self, title: str, subtitle: str | None = None) -> None:
        self.title_label.setText(title)
        self.title_label.setVisible(bool(title))
        if subtitle is not None:
            self.subtitle_label.setText(subtitle)
            self.subtitle_label.setVisible(bool(subtitle))

    def add_action(self, widget: QWidget) -> None:
        self.actions_host.show()
        self.actions.addWidget(widget)

    def add_tool(self, widget: QWidget, *, stretch: int = 0) -> None:
        self.toolbar_host.show()
        self.toolbar.addWidget(widget, stretch)

    def add_toolbar_stretch(self) -> None:
        self.toolbar_host.show()
        self.toolbar.addStretch()

    def add_body(self, widget: QWidget, stretch: int = 1) -> None:
        self.body.addWidget(widget, stretch)



class Badge(QLabel):
    def __init__(self, text: str, kind: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.apply(kind, text)

    def apply(self, kind: str, text: str | None = None) -> None:
        if text is not None:
            self.setText(text)
        mapping = {
            "info": ("info", "info_soft"),
            "success": ("success", "success_soft"),
            "warn": ("warn", "warn_soft"),
            "danger": ("danger", "danger_soft"),
            "muted": ("text_muted", "surface_alt"),
            "prod": ("danger", "danger_soft"),
            "plaintext": ("danger", "danger_soft"),
            "unauthorized": ("text_muted", "disabled_bg"),
            "role": ("accent", "accent_soft"),
            "tls": ("success", "success_soft"),
        }
        fg, bg = mapping.get(kind, ("text_secondary", "surface_alt"))
        tokens = current_palette()
        self.setStyleSheet(badge_qss(tokens, fg, bg))


class EmptyState(QWidget):
    """居中空状态：标题 + 一句完整说明 + 可选主按钮。装饰不得与文字重叠。"""

    def __init__(
        self,
        title: str,
        hint: str = "",
        action: str = "",
        *,
        icon_name: str = "empty",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        # icon_name 保留以兼容调用方，不再绘制会压住标题的色块。
        self._icon_name = icon_name

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(0)
        root.addStretch(1)

        self.block = QWidget()
        self.block.setObjectName("emptyBlock")
        self.block.setMaximumWidth(480)
        col = QVBoxLayout(self.block)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(12)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        col.addWidget(self.title_label)

        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("emptyHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(bool(hint))
        col.addWidget(self.hint_label)

        self.button = QPushButton(action)
        self.button.setObjectName("primary")
        self.button.setVisible(bool(action))
        self.button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        col.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.block, 0)
        row.addStretch(1)
        root.addLayout(row, 0)
        root.addStretch(1)

    def set_content(self, title: str, hint: str = "", action: str = "") -> None:
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self.button.setText(action)
        self.button.setVisible(bool(action))




# Shared inset so header labels and cell text share one left edge.
TABLE_TEXT_PAD_LEFT = 12
TABLE_TEXT_PAD_RIGHT = 12


class LeftAlignHeaderView(QHeaderView):
    """Paint section titles ourselves. Windows / QSS often ignore setDefaultAlignment."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None, pad_left: int = TABLE_TEXT_PAD_LEFT) -> None:
        super().__init__(orientation, parent)
        self._pad_left = pad_left
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSortIndicatorShown(False)
        self.setSectionsClickable(False)
        self.setHighlightSections(False)
        self.setMinimumHeight(36)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:  # type: ignore[override]
        if not rect.isValid():
            return
        painter.save()
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logicalIndex
        opt.text = ""
        opt.textAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        opt.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        visual = self.visualIndex(logicalIndex)
        count = self.count()
        if count <= 1:
            opt.position = QStyleOptionHeader.SectionPosition.OnlyOneSection
        elif visual == 0:
            opt.position = QStyleOptionHeader.SectionPosition.Beginning
        elif visual == count - 1:
            opt.position = QStyleOptionHeader.SectionPosition.End
        else:
            opt.position = QStyleOptionHeader.SectionPosition.Middle
        opt.selectedPosition = QStyleOptionHeader.SelectedPosition.NotAdjacent
        opt.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Horizontal
        if self.isEnabled():
            opt.state |= QStyle.StateFlag.State_Enabled
        self.style().drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)
        painter.restore()

        # drawControl often leaves a clip that swallows our title, and some
        # Windows styles still paint a centered label from the model.
        model = self.model()
        text = ""
        if model is not None:
            raw = model.headerData(logicalIndex, self.orientation(), Qt.ItemDataRole.DisplayRole)
            text = "" if raw is None else str(raw)
        if not text:
            return
        painter.save()
        painter.setClipping(False)
        try:
            fill = QColor(current_palette()["surface_alt"])
        except Exception:
            fill = opt.palette.color(QPalette.ColorRole.Button)
        painter.fillRect(rect.adjusted(0, 0, 0, -1), fill)
        try:
            painter.setPen(QColor(current_palette()["text_muted"]))
        except Exception:
            painter.setPen(opt.palette.color(QPalette.ColorRole.PlaceholderText))
        painter.setFont(self.font())
        text_rect = rect.adjusted(self._pad_left, 0, -TABLE_TEXT_PAD_RIGHT, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            text,
        )
        painter.restore()



class PaddedTextDelegate(QStyledItemDelegate):
    """Paint cell text at the same left inset as LeftAlignHeaderView."""

    def __init__(self, parent: QWidget | None = None, pad_left: int = TABLE_TEXT_PAD_LEFT, pad_right: int = TABLE_TEXT_PAD_RIGHT) -> None:
        super().__init__(parent)
        self._pad_left = pad_left
        self._pad_right = pad_right

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else self.parent().style() if self.parent() is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        if not text:
            return
        painter.save()
        painter.setClipping(False)
        try:
            painter.setPen(QColor(current_palette()["text_primary"]))
        except Exception:
            painter.setPen(opt.palette.color(QPalette.ColorRole.Text))
        painter.setFont(opt.font)
        text_rect = option.rect.adjusted(self._pad_left, 0, -self._pad_right, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            text,
        )
        painter.restore()


class DataTable(QTableWidget):
    def __init__(self, headers: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(0, len(headers), parent)
        header = LeftAlignHeaderView(Qt.Orientation.Horizontal, self)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setHorizontalHeader(header)
        self.setHorizontalHeaderLabels(list(headers))
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(40)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setWordWrap(False)
        self.setSortingEnabled(False)
        self.setItemDelegate(PaddedTextDelegate(self))
        self._align_header_labels()

    def set_headers(self, headers: Sequence[str]) -> None:
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(list(headers))
        self._align_header_labels()

    def _align_header_labels(self) -> None:
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col in range(self.columnCount()):
            item = self.horizontalHeaderItem(col)
            if item is not None:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_rows(self, rows: Iterable[Sequence[object]]) -> None:
        self.setRowCount(0)
        for row in rows:
            r = self.rowCount()
            self.insertRow(r)
            for c, value in enumerate(row):
                item = QTableWidgetItem(str(value) if value is not None else "")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(r, c, item)

    def selected_row_index(self) -> int:
        return self.currentRow()


class KeyValueTable(DataTable):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(["项目", "值"], parent)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setObjectName(self.objectName() or "keyValueTable")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setWordWrap(False)
        self._fitting = False

    def set_pairs(self, pairs: Sequence[tuple[str, object]]) -> None:
        self.set_rows([(k, "" if v is None or v == "" else v) for k, v in pairs])
        self.fit_to_contents()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.fit_to_contents()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.fit_to_contents()

    def fit_to_contents(self, *, max_height: int = 480) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            header = self.horizontalHeader()
            if self.columnCount() >= 2:
                self.resizeColumnToContents(0)
                available = self.viewport().width() if self.viewport().width() > 0 else max(self.width() - self.frameWidth() * 2, 0)
                max_first = max(72, available - 72)
                if self.columnWidth(0) > max_first:
                    self.setColumnWidth(0, max_first)
                header.setStretchLastSection(True)
            row_h = self.verticalHeader().defaultSectionSize() or 40
            rows_h = 0
            for index in range(self.rowCount()):
                rows_h += max(self.rowHeight(index), row_h)
            if 0 < header.maximumHeight() < 16777215:
                header_h = header.maximumHeight()
            else:
                header_h = max(header.sizeHint().height(), header.minimumHeight(), header.height(), 36)
            margins = self.contentsMargins()
            frame = max(self.frameWidth(), 1) * 2
            height = header_h + rows_h + frame + margins.top() + margins.bottom() + 4
            if height > max_height:
                self.setFixedHeight(max_height)
                self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            else:
                self.setFixedHeight(height)
                self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.updateGeometry()
        finally:
            self._fitting = False


class StatusDot(QLabel):
    def __init__(self, text: str = "", kind: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_status(kind, text)

    def set_status(self, kind: str, text: str) -> None:
        tokens = current_palette()
        color = {
            "ok": tokens["success"],
            "warn": tokens["warn"],
            "error": tokens["danger"],
            "muted": tokens["text_muted"],
        }.get(kind, tokens["text_muted"])
        self.setText(f"●  {text}" if text else "●")
        self.setStyleSheet(f"color: {color}; font-size: 12px;")


class NavButton(QPushButton):
    def __init__(self, key: str, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.nav_key = key
        self._icon_name = icon_name
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(40)
        self.toggled.connect(lambda _: self.refresh_icon())
        self.refresh_icon()

    def refresh_icon(self) -> None:
        tokens = current_palette()
        color = tokens["nav_active_text"] if self.isChecked() else tokens["nav_text"]
        self.setIcon(make_icon(self._icon_name, color, 18))
        self.setIconSize(QSize(18, 18))


class SplitView(QSplitter):
    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(1)


class RowStatusCell(QWidget):
    """\u5217\u8868\u884c\u4e0a\u7684\u72b6\u6001/\u5237\u65b0\u6307\u793a\uff0c\u5fd9\u65f6\u65cb\u8f6c\u5237\u65b0\u56fe\u6807\u3002"""

    _ICON = 16
    _SPACING = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, TABLE_TEXT_PAD_RIGHT, 0)
        layout.setSpacing(self._SPACING)
        self.icon = QLabel()
        self.icon.setFixedSize(self._ICON, self._ICON)
        self.icon.hide()
        self.label = QLabel("\u2014")
        self.label.setObjectName("muted")
        self.label.setWordWrap(False)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.icon, 0)
        layout.addWidget(self.label, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._angle = 0
        self._busy = False
        self._full_text = "\u2014"
        self._tooltip = ""

    @classmethod
    def minimum_column_width(cls, font_source: QWidget, text: str = "\u6d4b\u8bd5\u901a\u8fc7") -> int:
        """Column width that keeps icon + common status text fully visible."""
        fm = font_source.fontMetrics()
        text_w = fm.horizontalAdvance(text)
        return TABLE_TEXT_PAD_LEFT + cls._ICON + cls._SPACING + text_w + TABLE_TEXT_PAD_RIGHT + 4

    def _set_display(self, text: str, *, tooltip: str | None = None) -> None:
        self._full_text = text
        self._tooltip = "" if tooltip is None else tooltip
        tip = self._tooltip or (text if text and text != "\u2014" else "")
        self.setToolTip(tip)
        self.label.setToolTip(tip)
        self._apply_elide()

    def _apply_elide(self) -> None:
        available = self.label.width()
        if available <= 0:
            self.label.setText(self._full_text)
            return
        elided = self.label.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        self.label.setText(elided)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._apply_elide()

    def set_idle(self, text: str = "\u2014") -> None:
        self._busy = False
        self._timer.stop()
        self.icon.hide()
        self.icon.clear()
        self._set_display(text)

    def set_busy(self, text: str = "\u6d4b\u8bd5\u4e2d\u2026") -> None:
        self._busy = True
        self.icon.show()
        if not self._timer.isActive():
            self._timer.start(80)
        self._paint()
        self._set_display(text)

    def set_ok(self, text: str = "\u6210\u529f") -> None:
        self._busy = False
        self._timer.stop()
        self.icon.show()
        self.icon.setPixmap(make_icon("ok", current_palette()["success"], 16).pixmap(16, 16))
        self._set_display(text)

    def set_fail(self, text: str = "\u5931\u8d25", *, tooltip: str | None = None) -> None:
        self._busy = False
        self._timer.stop()
        self.icon.show()
        self.icon.setPixmap(make_icon("fail", current_palette()["danger"], 16).pixmap(16, 16))
        self._set_display(text, tooltip=tooltip if tooltip is not None else text)

    def _tick(self) -> None:
        self._angle = (self._angle + 24) % 360
        self._paint()

    def _paint(self) -> None:
        from PySide6.QtGui import QTransform

        pix = make_icon("refresh", current_palette()["accent"], 16).pixmap(16, 16)
        xform = QTransform()
        xform.translate(8, 8)
        xform.rotate(self._angle)
        xform.translate(-8, -8)
        self.icon.setPixmap(pix.transformed(xform))


