"""无边框窗口铬框：自定义标题栏、系统级拖动/缩放、工作区最大化。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

EDGE_PX = 8


PREFERRED_WINDOW_SIZE = (1280, 800)
LAUNCH_SIZE_RATIO = 0.75
DEFAULT_MIN_SIZE = (800, 560)


class WindowRoot(QWidget):
    """主窗口根控件：内容铺满窗口边缘，不再绘制外圈或阴影留白。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("windowRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)


def work_area_for(widget: QWidget) -> QRect:
    screen = widget.screen() if widget is not None else None
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1280, 800)
    return screen.availableGeometry()



def normal_launch_geometry(area: QRect) -> QRect:
    """启动时的普通窗口几何：1280x800 与工作区 75% 中的较小者，并居中，不覆盖任务栏。"""
    pref_w, pref_h = PREFERRED_WINDOW_SIZE
    target_w = min(pref_w, int(area.width() * LAUNCH_SIZE_RATIO), area.width())
    target_h = min(pref_h, int(area.height() * LAUNCH_SIZE_RATIO), area.height())
    target_w = max(1, target_w)
    target_h = max(1, target_h)
    x = area.x() + max(0, (area.width() - target_w) // 2)
    y = area.y() + max(0, (area.height() - target_h) // 2)
    return QRect(x, y, target_w, target_h)


def edges_at(pos: QPoint, rect: QRect, *, edge: int = EDGE_PX, slack: int = 2) -> Qt.Edges:
    """按内容矩形计算缩放边，而不是把透明阴影区当成窗口外沿。"""
    hit = rect.adjusted(-slack, -slack, slack, slack)
    if not hit.contains(pos):
        return Qt.Edges()
    flags = Qt.Edges()
    if pos.x() <= rect.left() + edge:
        flags |= Qt.Edge.LeftEdge
    if pos.x() >= rect.right() - edge:
        flags |= Qt.Edge.RightEdge
    if pos.y() <= rect.top() + edge:
        flags |= Qt.Edge.TopEdge
    if pos.y() >= rect.bottom() - edge:
        flags |= Qt.Edge.BottomEdge
    return flags


def cursor_for_edges(edges: Qt.Edges) -> Qt.CursorShape:
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)
    if (top and left) or (bottom and right):
        return Qt.CursorShape.SizeFDiagCursor
    if (top and right) or (bottom and left):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    if top or bottom:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


class WindowButton(QPushButton):
    """最小化 / 最大化 / 关闭的线型按钮。"""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName({"min": "winMin", "max": "winMax", "close": "winClose"}[kind])
        self.setFixedSize(40, 32)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip({"min": "最小化", "max": "最大化", "close": "关闭"}[kind])
        self._restore = False

    def set_restorable(self, restore: bool) -> None:
        if self.kind != "max":
            return
        self._restore = restore
        self.setToolTip("还原" if restore else "最大化")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, ARG002
        hovered = self.underMouse()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if hovered:
            bg = QColor("#E11D48") if self.kind == "close" else QColor("#F1F5F9")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)
        color = QColor("#FFFFFF") if (hovered and self.kind == "close") else QColor("#475569")
        pen = QPen(color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx, cy = self.width() / 2, self.height() / 2
        if self.kind == "min":
            painter.drawLine(QPoint(int(cx - 5), int(cy)), QPoint(int(cx + 5), int(cy)))
        elif self.kind == "max" and self._restore:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(int(cx - 3), int(cy - 5), 8, 8)
            painter.drawLine(int(cx - 5), int(cy - 1), int(cx - 5), int(cy + 5))
            painter.drawLine(int(cx - 5), int(cy + 5), int(cx + 1), int(cy + 5))
        elif self.kind == "max":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(int(cx - 5), int(cy - 5), 10, 10, 1, 1)
        else:
            painter.drawLine(QPoint(int(cx - 4), int(cy - 4)), QPoint(int(cx + 4), int(cy + 4)))
            painter.drawLine(QPoint(int(cx + 4), int(cy - 4)), QPoint(int(cx - 4), int(cy + 4)))


class TitleBar(QFrame):
    """应用名、当前页、连接状态与窗口按钮。空白处拖动，双击最大化。"""

    move_requested = Signal()
    maximize_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(10)

        self.mark = QLabel("")
        self.mark.setObjectName("brandMark")
        self.mark.hide()
        self.brand = QLabel("RedisDesk")
        self.brand.setObjectName("brandTitle")
        layout.addWidget(self.brand)

        rule = QFrame()
        rule.setObjectName("titleDivider")
        rule.setFixedSize(1, 16)
        layout.addWidget(rule)

        self.page_title = QLabel("连接")
        self.page_title.setObjectName("chromePage")
        layout.addWidget(self.page_title)
        layout.addStretch(1)

        self.trailing = QHBoxLayout()
        self.trailing.setContentsMargins(0, 0, 8, 0)
        self.trailing.setSpacing(8)
        layout.addLayout(self.trailing)

        self.win_min = WindowButton("min")
        self.win_max = WindowButton("max")
        self.win_close = WindowButton("close")
        layout.addWidget(self.win_min)
        layout.addWidget(self.win_max)
        layout.addWidget(self.win_close)

    def add_trailing(self, widget: QWidget) -> None:
        self.trailing.addWidget(widget)

    def _is_interactive(self, pos: QPoint) -> bool:
        child = self.childAt(pos)
        if child is None:
            return False
        if child in {self.page_title, self.brand, self.mark}:
            return False
        if child.objectName() == "titleDivider":
            return False
        return True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_interactive(event.position().toPoint()):
            # 顶边留给窗口缩放。
            if event.position().y() > 2:
                self.move_requested.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_interactive(event.position().toPoint()):
            self.maximize_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class TopNavButton(QPushButton):
    def __init__(self, key: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.nav_key = key
        self.setObjectName("topNav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)

    def refresh_icon(self) -> None:
        # 横向文字导航不使用图标，保留方法以免主题刷新处报错。
        return


class HorizontalNav(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navBar")
        self.setFixedHeight(44)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(20, 0, 20, 0)
        self.layout_.setSpacing(4)
        self.buttons: list[TopNavButton] = []

    def add_item(self, key: str, text: str) -> TopNavButton:
        btn = TopNavButton(key, text)
        self.buttons.append(btn)
        self.layout_.addWidget(btn)
        return btn

    def finish(self) -> None:
        self.layout_.addStretch(1)


class FramelessMixin:
    """给主窗口加上无边框拖动、边缘缩放和工作区最大化。"""

    def init_frameless(self) -> None:
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self._work_area_maximized = False
        self._restore_geom: QRect | None = None
        self._manual_edges: Qt.Edges | None = None
        self._manual_origin: QPoint | None = None
        self._manual_geom: QRect | None = None
        self._manual_move = False
        self._manual_move_offset = QPoint()
        self.setMouseTracking(True)
        self.installEventFilter(self)

    def place_normal_on_work_area(self) -> None:
        """以普通窗口启动：不最大化、不全屏，居中于 availableGeometry。"""
        area = work_area_for(self)
        geom = normal_launch_geometry(area)
        min_w = min(DEFAULT_MIN_SIZE[0], geom.width(), area.width())
        min_h = min(DEFAULT_MIN_SIZE[1], geom.height(), area.height())
        self.setMinimumSize(max(1, min_w), max(1, min_h))
        state = self.windowState()
        self.setWindowState(state & ~Qt.WindowState.WindowMaximized & ~Qt.WindowState.WindowFullScreen)
        self._work_area_maximized = False
        self.setGeometry(geom)
        self._sync_max_button()

    def is_work_area_maximized(self) -> bool:
        return bool(getattr(self, "_work_area_maximized", False))

    def toggle_work_area_maximize(self) -> None:
        if self.is_work_area_maximized():
            self.restore_from_work_area()
        else:
            self.maximize_to_work_area()

    def maximize_to_work_area(self) -> None:
        self._restore_geom = QRect(self.geometry())
        self.setGeometry(work_area_for(self))
        self._work_area_maximized = True
        self._sync_max_button()
        self.unsetCursor()

    def restore_from_work_area(self) -> None:
        if self._restore_geom is not None:
            self.setGeometry(self._restore_geom)
        else:
            area = work_area_for(self)
            self.resize(min(1280, area.width()), min(800, area.height()))
            self.move(area.center() - self.rect().center())
        self._work_area_maximized = False
        self._sync_max_button()

    def _sync_max_button(self) -> None:
        bar = getattr(self, "title_bar", None)
        if bar is not None:
            bar.win_max.set_restorable(self.is_work_area_maximized())

    def begin_chrome_move(self) -> None:
        if self.is_work_area_maximized():
            cursor = QCursor.pos()
            geom = self.geometry()
            ratio = (cursor.x() - geom.x()) / max(geom.width(), 1)
            self.restore_from_work_area()
            QApplication.processEvents()
            new_x = cursor.x() - int(self.width() * max(0.0, min(ratio, 1.0)))
            self.move(new_x, cursor.y() - 12)
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.startSystemMove()
                return
            except Exception:
                pass
        self._manual_move = True
        self._manual_move_offset = QCursor.pos() - self.frameGeometry().topLeft()

    def begin_chrome_resize(self, edges: Qt.Edges) -> None:
        if self.is_work_area_maximized() or not edges:
            return
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.startSystemResize(edges)
                return
            except Exception:
                pass
        self._manual_edges = edges
        self._manual_origin = QCursor.pos()
        self._manual_geom = QRect(self.geometry())

    def arm_resize_filters(self) -> None:
        """让边缘命中在子控件上也能工作（中央控件铺满窗口）。"""
        for child in self.findChildren(QWidget):
            if child.isWindow() and child is not self:
                continue
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _is_interactive_target(self, obj) -> bool:  # noqa: ANN001
        from PySide6.QtWidgets import QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLineEdit, QScrollBar, QSlider, QTabBar

        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None and widget is not self:
            if isinstance(
                widget,
                (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QAbstractItemView, QScrollBar, QSlider, QTabBar),
            ):
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        typ = event.type()
        if typ == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget) and not (child.isWindow() and child is not self):
                child.setMouseTracking(True)
                child.installEventFilter(self)
        if typ in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease} and isinstance(event, QMouseEvent):
            if typ == QEvent.Type.MouseButtonRelease:
                self._manual_edges = None
                self._manual_move = False
            global_pos = event.globalPosition().toPoint()
            local = self.mapFromGlobal(global_pos)
            if typ == QEvent.Type.MouseMove and self._manual_edges and self._manual_origin and self._manual_geom:
                self._apply_manual_resize(global_pos)
                return True
            if typ == QEvent.Type.MouseMove and self._manual_move:
                self.move(global_pos - self._manual_move_offset)
                return True
            if not self.is_work_area_maximized():
                current = edges_at(local, self.rect())
                interactive = self._is_interactive_target(obj)
                if typ == QEvent.Type.MouseMove and event.buttons() == Qt.MouseButton.NoButton and not interactive:
                    self.setCursor(cursor_for_edges(current) if current else Qt.CursorShape.ArrowCursor)
                if typ == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and current and not interactive:
                    self.begin_chrome_resize(current)
                    return True
        return super().eventFilter(obj, event)

    def _apply_manual_resize(self, global_pos: QPoint) -> None:
        assert self._manual_origin is not None and self._manual_geom is not None and self._manual_edges is not None
        delta = global_pos - self._manual_origin
        geom = QRect(self._manual_geom)
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        if self._manual_edges & Qt.Edge.LeftEdge:
            geom.setLeft(min(geom.right() - min_w, geom.left() + delta.x()))
        if self._manual_edges & Qt.Edge.RightEdge:
            geom.setWidth(max(min_w, geom.width() + delta.x()))
        if self._manual_edges & Qt.Edge.TopEdge:
            geom.setTop(min(geom.bottom() - min_h, geom.top() + delta.y()))
        if self._manual_edges & Qt.Edge.BottomEdge:
            geom.setHeight(max(min_h, geom.height() + delta.y()))
        area = work_area_for(self)
        # 不要把窗口拖出工作区过远，但允许贴边缩放。
        if geom.width() > area.width():
            geom.setWidth(area.width())
        if geom.height() > area.height():
            geom.setHeight(area.height())
        self.setGeometry(geom)
