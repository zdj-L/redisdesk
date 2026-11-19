"""简洁单色线性图标，风格统一，不依赖外部图标包。"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("connections", "连接", "server"),
    ("browser", "浏览", "search"),
    ("console", "控制台", "console"),
    ("diagnostics", "诊断", "pulse"),
    ("audit", "审计", "clipboard"),
    ("users", "本机", "user"),
    ("settings", "设置", "gear"),
)

# 兼容旧代码中的 NAV 字典（纯文本）。
NAV = {key: label for key, label, _icon in NAV_ITEMS}


def make_icon(name: str, color: str = "#64748B", size: int = 18, *, pixel_ratio: float = 1.0) -> QIcon:
    extent = max(16, int(size * pixel_ratio))
    pixmap = QPixmap(extent, extent)
    pixmap.setDevicePixelRatio(pixel_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    scale = extent / 18.0
    painter.scale(scale, scale)
    pen = QPen(QColor(color))
    pen.setWidthF(1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _draw(painter, name, QColor(color))
    painter.end()
    icon = QIcon()
    icon.addPixmap(pixmap)
    return icon


def _draw(p: QPainter, name: str, color: QColor) -> None:
    drawer = {
        "server": _draw_server,
        "search": _draw_search,
        "console": _draw_console,
        "pulse": _draw_pulse,
        "clipboard": _draw_clipboard,
        "user": _draw_user,
        "gear": _draw_gear,
        "empty": _draw_empty,
        "link": _draw_link,
        "key": _draw_key,
        "refresh": _draw_refresh,
        "ok": _draw_ok,
        "fail": _draw_fail,
        "folder": _draw_folder,
        "database": _draw_database,
    }.get(name, _draw_empty)
    drawer(p, color)


def _draw_server(p: QPainter, _color: QColor) -> None:
    p.drawRoundedRect(QRectF(3.5, 2.5, 11, 5.2), 1.2, 1.2)
    p.drawRoundedRect(QRectF(3.5, 10.3, 11, 5.2), 1.2, 1.2)
    p.drawPoint(QPointF(6.2, 5.1))
    p.drawPoint(QPointF(6.2, 12.9))
    p.drawLine(QPointF(8.5, 5.1), QPointF(12.5, 5.1))
    p.drawLine(QPointF(8.5, 12.9), QPointF(12.5, 12.9))


def _draw_search(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(3.2, 3.0, 8.6, 8.6))
    p.drawLine(QPointF(10.4, 10.6), QPointF(14.6, 14.8))


def _draw_console(p: QPainter, _color: QColor) -> None:
    p.drawRoundedRect(QRectF(2.5, 3.0, 13, 12), 1.5, 1.5)
    p.drawLine(QPointF(5.5, 7.2), QPointF(8.2, 9.0))
    p.drawLine(QPointF(8.2, 9.0), QPointF(5.5, 10.8))
    p.drawLine(QPointF(9.4, 12.2), QPointF(13.0, 12.2))


def _draw_pulse(p: QPainter, _color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(2.0, 9.0)
    path.lineTo(5.2, 9.0)
    path.lineTo(7.0, 4.6)
    path.lineTo(9.6, 13.6)
    path.lineTo(11.6, 7.6)
    path.lineTo(13.2, 9.0)
    path.lineTo(16.0, 9.0)
    p.drawPath(path)


def _draw_clipboard(p: QPainter, _color: QColor) -> None:
    p.drawRoundedRect(QRectF(4.0, 3.8, 10, 12.2), 1.4, 1.4)
    p.drawRoundedRect(QRectF(6.2, 2.2, 5.6, 2.8), 0.8, 0.8)
    p.drawLine(QPointF(6.4, 8.4), QPointF(11.6, 8.4))
    p.drawLine(QPointF(6.4, 11.0), QPointF(11.6, 11.0))
    p.drawLine(QPointF(6.4, 13.4), QPointF(10.2, 13.4))


def _draw_user(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(6.1, 2.8, 5.8, 5.8))
    path = QPainterPath()
    path.moveTo(3.8, 15.4)
    path.quadTo(9.0, 10.6, 14.2, 15.4)
    p.drawPath(path)


def _draw_gear(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(6.6, 6.6, 4.8, 4.8))
    cx, cy, r = 9.0, 9.0, 6.4
    for i in range(8):

        a = i * math.pi / 4
        x1 = cx + r * 0.72 * math.cos(a)
        y1 = cy + r * 0.72 * math.sin(a)
        x2 = cx + r * math.cos(a)
        y2 = cy + r * math.sin(a)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_empty(p: QPainter, _color: QColor) -> None:
    p.drawRoundedRect(QRectF(3.0, 4.5, 12, 10), 1.5, 1.5)
    p.drawLine(QPointF(6.0, 8.0), QPointF(12.0, 8.0))
    p.drawLine(QPointF(6.0, 11.0), QPointF(10.5, 11.0))


def _draw_link(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(2.8, 6.2, 5.6, 5.6))
    p.drawLine(QPointF(8.4, 9.0), QPointF(12.4, 9.0))
    p.drawEllipse(QRectF(9.6, 6.2, 5.6, 5.6))


def _draw_key(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(2.8, 5.5, 6.4, 6.4))
    p.drawLine(QPointF(9.2, 9.0), QPointF(15.4, 9.0))
    p.drawLine(QPointF(13.4, 9.0), QPointF(13.4, 12.2))
    p.drawLine(QPointF(15.4, 9.0), QPointF(15.4, 11.2))


def icon_size(size: int = 18) -> QSize:
    return QSize(size, size)


def _draw_refresh(p: QPainter, _color: QColor) -> None:
    path = QPainterPath()
    path.arcMoveTo(QRectF(3.2, 3.2, 11.6, 11.6), 40)
    path.arcTo(QRectF(3.2, 3.2, 11.6, 11.6), 40, 250)
    p.drawPath(path)
    p.drawLine(QPointF(12.8, 3.4), QPointF(15.0, 6.2))
    p.drawLine(QPointF(12.8, 3.4), QPointF(10.4, 6.0))


def _draw_ok(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(3.0, 3.0, 12.0, 12.0))
    p.drawLine(QPointF(6.0, 9.4), QPointF(8.2, 11.8))
    p.drawLine(QPointF(8.2, 11.8), QPointF(12.4, 6.6))


def _draw_fail(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(3.0, 3.0, 12.0, 12.0))
    p.drawLine(QPointF(6.8, 6.8), QPointF(11.2, 11.2))
    p.drawLine(QPointF(11.2, 6.8), QPointF(6.8, 11.2))


def _draw_folder(p: QPainter, _color: QColor) -> None:
    p.drawRoundedRect(QRectF(2.5, 6.2, 13.0, 8.8), 1.2, 1.2)
    p.drawRoundedRect(QRectF(2.5, 4.0, 6.2, 3.2), 1.0, 1.0)


def _draw_database(p: QPainter, _color: QColor) -> None:
    p.drawEllipse(QRectF(4.0, 2.8, 10.0, 3.4))
    p.drawLine(QPointF(4.0, 4.5), QPointF(4.0, 13.4))
    p.drawLine(QPointF(14.0, 4.5), QPointF(14.0, 13.4))
    p.drawArc(QRectF(4.0, 11.6, 10.0, 3.4), 0, -180 * 16)
    p.drawArc(QRectF(4.0, 7.2, 10.0, 3.4), 0, -180 * 16)
