"""对话框与下拉框企业级组件：可滚动表单、固定页脚、贴合下拉。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def available_work_area(widget: QWidget | None = None) -> QRect:
    """当前窗口所在屏幕的可用工作区（排除任务栏，尊重 DPI / 多屏）。"""
    screen = None
    if widget is not None:
        screen = widget.screen()
        if screen is None:
            window = widget.window()
            if window is not None:
                screen = window.screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1280, 800)
    return screen.availableGeometry()


def center_widget_on_work_area(widget: QWidget) -> None:
    area = available_work_area(widget)
    parent = widget.parentWidget()
    if parent is not None:
        center = parent.window().frameGeometry().center()
    else:
        center = area.center()
    frame = widget.frameGeometry()
    if frame.width() <= 0 or frame.height() <= 0:
        frame.setSize(widget.size())
    frame.moveCenter(center)
    if frame.top() < area.top():
        frame.moveTop(area.top())
    if frame.bottom() > area.bottom():
        frame.moveBottom(area.bottom())
    if frame.left() < area.left():
        frame.moveLeft(area.left())
    if frame.right() > area.right():
        frame.moveRight(area.right())
    widget.move(frame.topLeft())


def configure_spinbox(box: QSpinBox) -> QSpinBox:
    """去掉 Win32 风格的上下步进按钮，外观与行编辑一致。"""
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    box.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    box.setKeyboardTracking(True)
    return box


def _popup_style() -> str:
    from redisdesk.app.ui.theme import current_palette

    t = current_palette()
    return (
        f"background: {t['surface']};"
        f" color: {t['text_primary']};"
        f" border: 1px solid {t['border']};"
        f" border-radius: {t['radius_sm']};"
    )


class _DialogDimmer(QWidget):
    """约 40% 的模态遮罩，不改变对话框本身的尺寸与页脚布局。"""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("dialogDimmer")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setGeometry(host.rect())
        host.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: ANN001
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            parent = self.parentWidget()
            if parent is not None:
                self.setGeometry(parent.rect())
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:  # noqa: ANN001, ARG002
        from redisdesk.app.ui.theme import current_theme

        painter = QPainter(self)
        alpha = 102 if current_theme() == "light" else 140
        painter.fillRect(self.rect(), QColor(15, 23, 42, alpha))


class DialogCloseButton(QPushButton):
    """标题栏右侧的细线关闭按钮，不用系统默认图标。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dialogClose")
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("关闭")
        self.setFlat(True)

    def paintEvent(self, event) -> None:  # noqa: ANN001, ARG002
        hovered = self.underMouse()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFF1F2"))
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)
        color = QColor("#E11D48") if hovered else QColor("#64748B")
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx, cy = self.width() / 2, self.height() / 2
        painter.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
        painter.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))


class StyledComboBox(QComboBox):
    """弹出层贴合控件底部、宽度与控件一致；右侧为细线箭头而非系统块状箭头。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMaxVisibleItems(8)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(0, 0))
        view = QListView(self)
        view.setObjectName("comboPopup")
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setUniformItemSizes(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setView(view)

    def contents_width(self) -> int:
        fm = self.fontMetrics()
        widest = fm.horizontalAdvance("XXXX")
        for index in range(self.count()):
            widest = max(widest, fm.horizontalAdvance(self.itemText(index)))
        # left pad + drop-down arrow area + slack so CJK labels are not clipped
        return widest + 12 + 32 + 8

    def fit_to_contents(self) -> None:
        width = max(96, self.contents_width())
        self.setMinimumWidth(width)
        self.updateGeometry()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self.fit_to_contents()

    def showPopup(self) -> None:
        self.fit_to_contents()
        super().showPopup()
        self._reposition_popup()
        QTimer.singleShot(0, self._reposition_popup)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        from redisdesk.app.ui.theme import current_palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(current_palette().get("text_muted", "#94A3B8"))
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx = self.width() - 14
        cy = self.height() / 2
        painter.drawLine(QPointF(cx - 4.0, cy - 1.5), QPointF(cx, cy + 2.2))
        painter.drawLine(QPointF(cx + 4.0, cy - 1.5), QPointF(cx, cy + 2.2))

    def _reposition_popup(self) -> None:
        view = self.view()
        if view is None:
            return
        popup = view.window()
        if popup is None or popup is self or popup is self.window():
            return
        area = available_work_area(self)
        width = max(self.width(), self.contents_width(), 80)
        n = min(max(self.count(), 1), self.maxVisibleItems())
        row = view.sizeHintForRow(0)
        if row <= 0:
            row = max(32, self.height())
        frame = popup.frameWidth() * 2
        height = min(row * n + frame + 12, max(row + frame, area.height() // 2))
        below = self.mapToGlobal(QPoint(0, self.height() - 1))
        above = self.mapToGlobal(QPoint(0, 0))
        if below.y() + height <= area.bottom() or (below.y() - area.top()) >= (area.bottom() - below.y()):
            y = min(below.y(), area.bottom() - height)
        else:
            y = max(area.top(), above.y() - height)
        x = below.x()
        if x + width > area.right():
            x = area.right() - width + 1
        if x < area.left():
            x = area.left()
        popup.setFixedWidth(width)
        popup.resize(width, height)
        popup.move(x, y)
        popup.setStyleSheet(_popup_style())
        if popup.graphicsEffect() is None:
            effect = QGraphicsDropShadowEffect(popup)
            effect.setBlurRadius(24)
            effect.setOffset(0, 8)
            effect.setColor(QColor(15, 23, 42, 28))
            popup.setGraphicsEffect(effect)


class DialogSection(QWidget):
    """对话框内的紧凑分组：标题 + 可选说明 + 内容，不用大卡片撑高。"""

    def __init__(self, title: str, description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dialogSection")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("sectionTitle")
        root.addWidget(head)
        if description:
            hint = QLabel(description)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            root.addWidget(hint)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 2, 0, 0)
        self.body.setSpacing(8)
        root.addLayout(self.body)


class ScrollableDialog(QDialog):
    """无系统图标的排版标题栏 + 可滚动表单 + 固定页脚。高度不超过工作区 80%。"""

    PREFERRED_WIDTH = 560
    MAX_WIDTH = 640
    MIN_WIDTH = 480
    HEIGHT_RATIO = 0.8

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        width: int = 560,
        ok_text: str = "确定",
        cancel_text: str = "取消",
        ok_role: str = "primary",
        default_ok: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("scrollableDialog")
        self.setWindowIcon(QIcon())
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.CustomizeWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._pref_width = width
        self._fitted = False
        self._dimmer: _DialogDimmer | None = None
        self._moving = False
        self._move_offset = QPoint()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("dialogHeader")
        self.header.setFixedHeight(56)
        head = QHBoxLayout(self.header)
        head.setContentsMargins(24, 0, 12, 0)
        head.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("dialogTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.close_button = DialogCloseButton()
        self.close_button.clicked.connect(self.reject)
        head.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.header, 0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("dialogScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.body_widget = QWidget()
        self.body_widget.setObjectName("dialogBody")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(24, 12, 24, 16)
        self.body.setSpacing(16)
        self.body.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)
        self.scroll.setWidget(self.body_widget)
        root.addWidget(self.scroll, 1)

        self._has_status = False
        self._relayouting = False
        self.status_strip = QFrame()
        self.status_strip.setObjectName("dialogStatus")
        self.status_strip.setVisible(False)
        self.status_strip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.status_strip.setMaximumHeight(96)
        status_layout = QHBoxLayout(self.status_strip)
        status_layout.setContentsMargins(20, 8, 20, 8)
        status_layout.setSpacing(0)
        self.status_label = QLabel("")
        self.status_label.setObjectName("dialogStatusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        status_layout.addWidget(self.status_label, 1)
        root.addWidget(self.status_strip, 0)

        self.footer = QFrame()
        self.footer.setObjectName("dialogFooter")
        self.footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(20, 12, 20, 14)
        foot.setSpacing(8)
        self.footer_left = QHBoxLayout()
        self.footer_left.setContentsMargins(0, 0, 0, 0)
        self.footer_left.setSpacing(8)
        foot.addLayout(self.footer_left)
        foot.addStretch()
        self.cancel_button = QPushButton(cancel_text)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = QPushButton(ok_text)
        if ok_role:
            self.ok_button.setObjectName(ok_role)
        self.ok_button.setDefault(default_ok)
        self.ok_button.setAutoDefault(True)
        self.ok_button.clicked.connect(self.accept)
        if not default_ok:
            self.cancel_button.setDefault(True)
            self.ok_button.setDefault(False)
        foot.addWidget(self.cancel_button)
        foot.addWidget(self.ok_button)
        root.addWidget(self.footer, 0)

        self.header.installEventFilter(self)
        self.title_label.installEventFilter(self)

    def add_footer_left(self, widget: QWidget) -> None:
        self.footer_left.addWidget(widget)

    @property
    def result_banner(self) -> QLabel:
        return self.status_label

    def set_status(self, kind: str, text: str) -> None:
        # Show one complete message in the reserved strip above the footer.
        text = (text or "").strip()
        if not text:
            self.clear_status()
            return
        tones = {"ok": "success", "success": "success", "warn": "warn", "danger": "danger", "info": "info"}
        tone = tones.get(kind, "info")
        self._has_status = True
        self.status_label.setText(text)
        for widget, name in ((self.status_strip, "dialogStatus"), (self.status_label, "dialogStatusLabel")):
            widget.setObjectName(name)
            widget.setProperty("tone", tone)
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
        self.status_strip.setVisible(True)
        self.status_label.setVisible(True)
        self._relayout_shell()

    def clear_status(self) -> None:
        self._has_status = False
        self.status_label.setText("")
        self.status_strip.setProperty("tone", "")
        self.status_label.setProperty("tone", "")
        self.status_strip.setVisible(False)
        self._relayout_shell()

    def _status_height(self) -> int:
        if not self._has_status:
            return 0
        layout = self.status_strip.layout()
        margins = layout.contentsMargins() if layout is not None else None
        ml = (margins.left() + margins.right()) if margins is not None else 40
        mt = (margins.top() + margins.bottom()) if margins is not None else 16
        width = self.width() if self.width() > 0 else self._pref_width
        avail = max(120, width - ml)
        hfw = self.status_label.heightForWidth(avail)
        if hfw < 0:
            hfw = max(self.status_label.sizeHint().height(), 18)
        return min(96, max(hfw + mt, 32))

    def _relayout_shell(self) -> None:
        if self._relayouting:
            return
        self._relayouting = True
        try:
            if self._has_status:
                self.status_strip.setVisible(True)
                self.status_strip.setMinimumHeight(0)
                self.status_strip.setMaximumHeight(96)
                self.status_strip.setFixedHeight(self._status_height())
            else:
                self.status_strip.setVisible(False)
                self.status_strip.setFixedHeight(0)
                self.status_strip.setMaximumHeight(0)
            if self.isVisible():
                self.fit_to_work_area()
        finally:
            self._relayouting = False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: ANN001
        if obj in {self.header, self.title_label} and isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                child = self.header.childAt(self.header.mapFromGlobal(event.globalPosition().toPoint()))
                if child is self.close_button:
                    return super().eventFilter(obj, event)
                handle = self.windowHandle()
                if handle is not None:
                    try:
                        handle.startSystemMove()
                        return True
                    except Exception:
                        pass
                self._moving = True
                self._move_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseMove and self._moving and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._move_offset)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._moving = False
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: ANN001
        self._show_dimmer()
        super().showEvent(event)
        self.fit_to_work_area()

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._hide_dimmer()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._hide_dimmer()
        super().closeEvent(event)

    def _show_dimmer(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        host = parent.window()
        if host is None or host is self:
            return
        if self._dimmer is None:
            self._dimmer = _DialogDimmer(host)
        self._dimmer.setGeometry(host.rect())
        self._dimmer.show()
        self._dimmer.raise_()
        self.raise_()
        self.activateWindow()

    def _hide_dimmer(self) -> None:
        if self._dimmer is not None:
            self._dimmer.hide()

    def fit_to_work_area(self) -> None:
        area = available_work_area(self)
        max_w = max(360, area.width() - 32)
        max_h = max(320, int(area.height() * self.HEIGHT_RATIO))
        width = min(max(self._pref_width, self.MIN_WIDTH), self.MAX_WIDTH, max_w)
        header_h = max(self.header.sizeHint().height(), self.header.height(), 56)
        footer_h = max(self.footer.sizeHint().height(), self.footer.minimumSizeHint().height(), 48)
        if self._has_status:
            # Height depends on width; use the width we are about to apply.
            if self.width() != width:
                self.resize(width, max(self.height(), 240))
            status_h = self._status_height()
            self.status_strip.setVisible(True)
            self.status_strip.setMinimumHeight(0)
            self.status_strip.setMaximumHeight(96)
            self.status_strip.setFixedHeight(status_h)
        else:
            status_h = 0
            self.status_strip.setVisible(False)
            self.status_strip.setFixedHeight(0)
            self.status_strip.setMaximumHeight(0)
        content_h = self.body_widget.sizeHint().height()
        desired = header_h + content_h + status_h + footer_h + 8
        height = min(max(desired, 240), max_h)
        # When height-capped, the scroll area shrinks; status strip and footer keep their reserved heights.
        self.setMinimumWidth(min(self.MIN_WIDTH, width))
        self.setMaximumWidth(min(self.MAX_WIDTH, max_w))
        self.setMinimumHeight(min(240, max_h))
        self.setMaximumHeight(max_h)
        self.resize(width, height)
        center_widget_on_work_area(self)

    def sizeHint(self) -> QSize:
        area = available_work_area(self)
        max_h = max(320, int(area.height() * self.HEIGHT_RATIO))
        width = min(max(self._pref_width, self.MIN_WIDTH), self.MAX_WIDTH, max(360, area.width() - 32))
        header_h = max(self.header.sizeHint().height(), 56)
        footer_h = max(self.footer.sizeHint().height(), 48)
        status_h = self._status_height()
        content_h = self.body_widget.sizeHint().height()
        return QSize(width, min(header_h + content_h + status_h + footer_h + 8, max_h))
