"""当代企业控制台设计系统：语义色、字体、间距、阴影与全局 QSS。"""

from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

from redisdesk.infra.paths import ui_settings_path

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SYSTEM = "system"
THEME_FOREST = "forest"
THEME_AMBER = "amber"
THEME_OCEAN = "ocean"
THEME_SLATE = "slate"

THEME_CHOICES = (
    (THEME_LIGHT, "浅色"),
    (THEME_DARK, "深色"),
    (THEME_FOREST, "森林绿"),
    (THEME_AMBER, "暖阳黄"),
    (THEME_OCEAN, "海洋蓝"),
    (THEME_SLATE, "蓝灰"),
)

SPACE = 8
PAGE_MARGIN = 28
RADIUS = 10
RADIUS_CONTROL = 8

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
FONT_MONO = 'Consolas, "Cascadia Mono", "Courier New", monospace'

# 浅色默认：飞书 / Semi / Linear 气质的空气感浅色工作台，主色为 Indigo 而非 2018 风 Ant 蓝。
TOKENS_LIGHT: dict[str, str] = {
    "bg": "#F7F8FA",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "text_primary": "#0F172A",
    "text_secondary": "#475569",
    "text_muted": "#94A3B8",
    "accent": "#4F46E5",
    "accent_hover": "#4338CA",
    "accent_pressed": "#3730A3",
    "accent_soft": "#EEF2FF",
    "accent_text": "#FFFFFF",
    "success": "#059669",
    "success_soft": "#ECFDF5",
    "warn": "#D97706",
    "warn_soft": "#FFFBEB",
    "danger": "#E11D48",
    "danger_soft": "#FFF1F2",
    "info": "#4F46E5",
    "info_soft": "#EEF2FF",
    "nav_bg": "#FFFFFF",
    "nav_hover": "#F1F5F9",
    "nav_active_bg": "#EEF2FF",
    "nav_text": "#475569",
    "nav_muted": "#94A3B8",
    "nav_active_text": "#4F46E5",
    "nav_accent": "#4F46E5",
    "table_alt": "#FAFBFC",
    "table_hover": "#F8FAFC",
    "selection": "#EEF2FF",
    "selection_text": "#0F172A",
    "disabled_bg": "#F1F5F9",
    "disabled_text": "#CBD5E1",
    "shadow": "rgba(15, 23, 42, 0.06)",
    "overlay": "rgba(15, 23, 42, 0.40)",
    "frame_line": "#64748B",
    "frame_line_inactive": "#CBD5E1",
    "chrome_bg": "#EEF2FF",
    "chrome_bg_inactive": "#F1F5F9",
    "chrome_divider": "#E0E7FF",
    "mono": "#0F172A",
    "font_family": FONT_FAMILY,
    "font_mono": FONT_MONO,
    "radius": "12px",
    "radius_sm": "8px",
    "radius_pill": "10px",
}

# 深色为可选；保持克制的石板色，避免霓虹或厚重海军蓝铬框。
TOKENS_DARK: dict[str, str] = {
    "bg": "#0B1220",
    "surface": "#111827",
    "surface_alt": "#1E293B",
    "border": "#1F2A3D",
    "border_strong": "#334155",
    "text_primary": "#F8FAFC",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "accent": "#818CF8",
    "accent_hover": "#A5B4FC",
    "accent_pressed": "#6366F1",
    "accent_soft": "#1E1B4B",
    "accent_text": "#0F172A",
    "success": "#34D399",
    "success_soft": "#064E3B",
    "warn": "#FBBF24",
    "warn_soft": "#3B2A0A",
    "danger": "#FB7185",
    "danger_soft": "#4C0519",
    "info": "#818CF8",
    "info_soft": "#1E1B4B",
    "nav_bg": "#0B1220",
    "nav_hover": "#1E293B",
    "nav_active_bg": "#1E1B4B",
    "nav_text": "#94A3B8",
    "nav_muted": "#64748B",
    "nav_active_text": "#A5B4FC",
    "nav_accent": "#818CF8",
    "table_alt": "#0F172A",
    "table_hover": "#1E293B",
    "selection": "#1E1B4B",
    "selection_text": "#F8FAFC",
    "disabled_bg": "#1E293B",
    "disabled_text": "#475569",
    "shadow": "rgba(0, 0, 0, 0.35)",
    "overlay": "rgba(2, 6, 23, 0.55)",
    "frame_line": "#94A3B8",
    "frame_line_inactive": "#334155",
    "chrome_bg": "#1E1B4B",
    "chrome_bg_inactive": "#1E293B",
    "chrome_divider": "#334155",
    "mono": "#E2E8F0",
    "font_family": FONT_FAMILY,
    "font_mono": FONT_MONO,
    "radius": "12px",
    "radius_sm": "8px",
    "radius_pill": "10px",
}


# 浅色点缀主题：主色与浅表面变化，铬框只给顶栏/导航上色，不做窗体外圈亮边。
TOKENS_FOREST: dict[str, str] = {
    "bg": "#F3F7F4",
    "surface": "#FFFFFF",
    "surface_alt": "#EAF3EC",
    "border": "#D5E3D8",
    "border_strong": "#B5C9BA",
    "text_primary": "#14241A",
    "text_secondary": "#3F5A47",
    "text_muted": "#7A9482",
    "accent": "#2F6F4E",
    "accent_hover": "#275C41",
    "accent_pressed": "#1E4A34",
    "accent_soft": "#E4F0E8",
    "accent_text": "#FFFFFF",
    "success": "#1F7A4D",
    "success_soft": "#E6F5EC",
    "warn": "#C27803",
    "warn_soft": "#F8F1E0",
    "danger": "#C2414C",
    "danger_soft": "#FBECEE",
    "info": "#2F6F4E",
    "info_soft": "#E4F0E8",
    "nav_bg": "#FFFFFF",
    "nav_hover": "#EAF3EC",
    "nav_active_bg": "#E4F0E8",
    "nav_text": "#3F5A47",
    "nav_muted": "#7A9482",
    "nav_active_text": "#2F6F4E",
    "nav_accent": "#2F6F4E",
    "table_alt": "#F6FAF7",
    "table_hover": "#EAF3EC",
    "selection": "#E4F0E8",
    "selection_text": "#14241A",
    "disabled_bg": "#EAF3EC",
    "disabled_text": "#B5C9BA",
    "shadow": "rgba(20, 36, 26, 0.08)",
    "overlay": "rgba(20, 36, 26, 0.40)",
    "frame_line": "#4F7A5E",
    "frame_line_inactive": "#B5C9BA",
    "chrome_bg": "#E4F0E8",
    "chrome_bg_inactive": "#F0F5F1",
    "chrome_divider": "#D5E3D8",
    "mono": "#14241A",
    "font_family": FONT_FAMILY,
    "font_mono": FONT_MONO,
    "radius": "12px",
    "radius_sm": "8px",
    "radius_pill": "10px",
}

TOKENS_AMBER: dict[str, str] = {
    "bg": "#FBF7F0",
    "surface": "#FFFCFA",
    "surface_alt": "#F7F0E4",
    "border": "#E8D9C3",
    "border_strong": "#D4BE9A",
    "text_primary": "#2A2116",
    "text_secondary": "#6B542E",
    "text_muted": "#A38B6A",
    "accent": "#C4851C",
    "accent_hover": "#A86F14",
    "accent_pressed": "#8A5A10",
    "accent_soft": "#F8EDD8",
    "accent_text": "#FFFFFF",
    "success": "#3F7A3A",
    "success_soft": "#EAF5E8",
    "warn": "#C4851C",
    "warn_soft": "#F8EDD8",
    "danger": "#C2414C",
    "danger_soft": "#FBECEE",
    "info": "#C4851C",
    "info_soft": "#F8EDD8",
    "nav_bg": "#FFFCFA",
    "nav_hover": "#F7F0E4",
    "nav_active_bg": "#F8EDD8",
    "nav_text": "#6B542E",
    "nav_muted": "#A38B6A",
    "nav_active_text": "#8A5A10",
    "nav_accent": "#C4851C",
    "table_alt": "#FBF7F0",
    "table_hover": "#F7F0E4",
    "selection": "#F8EDD8",
    "selection_text": "#2A2116",
    "disabled_bg": "#F7F0E4",
    "disabled_text": "#D4BE9A",
    "shadow": "rgba(42, 33, 22, 0.08)",
    "overlay": "rgba(42, 33, 22, 0.40)",
    "frame_line": "#8A6A32",
    "frame_line_inactive": "#D4BE9A",
    "chrome_bg": "#F8EDD8",
    "chrome_bg_inactive": "#F7F0E4",
    "chrome_divider": "#E8D9C3",
    "mono": "#2A2116",
    "font_family": FONT_FAMILY,
    "font_mono": FONT_MONO,
    "radius": "12px",
    "radius_sm": "8px",
    "radius_pill": "10px",
}

TOKENS_OCEAN: dict[str, str] = {
    "bg": "#F3F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#E8F0F5",
    "border": "#D3DEE7",
    "border_strong": "#B3C4D1",
    "text_primary": "#163040",
    "text_secondary": "#3E5A6B",
    "text_muted": "#7A90A0",
    "accent": "#2C6A8A",
    "accent_hover": "#245A76",
    "accent_pressed": "#1C4860",
    "accent_soft": "#E4F0F6",
    "accent_text": "#FFFFFF",
    "success": "#1F7A4D",
    "success_soft": "#E6F5EC",
    "warn": "#C27803",
    "warn_soft": "#F8F1E0",
    "danger": "#C2414C",
    "danger_soft": "#FBECEE",
    "info": "#2C6A8A",
    "info_soft": "#E4F0F6",
    "nav_bg": "#FFFFFF",
    "nav_hover": "#E8F0F5",
    "nav_active_bg": "#E4F0F6",
    "nav_text": "#3E5A6B",
    "nav_muted": "#7A90A0",
    "nav_active_text": "#2C6A8A",
    "nav_accent": "#2C6A8A",
    "table_alt": "#F5F8FB",
    "table_hover": "#E8F0F5",
    "selection": "#E4F0F6",
    "selection_text": "#163040",
    "disabled_bg": "#E8F0F5",
    "disabled_text": "#B3C4D1",
    "shadow": "rgba(22, 48, 64, 0.08)",
    "overlay": "rgba(22, 48, 64, 0.40)",
    "frame_line": "#4A738A",
    "frame_line_inactive": "#B3C4D1",
    "chrome_bg": "#E4F0F6",
    "chrome_bg_inactive": "#EEF3F8",
    "chrome_divider": "#D3DEE7",
    "mono": "#163040",
    "font_family": FONT_FAMILY,
    "font_mono": FONT_MONO,
    "radius": "12px",
    "radius_sm": "8px",
    "radius_pill": "10px",
}

TOKENS_SLATE: dict[str, str] = {
    "bg": "#F3F5F7",
    "surface": "#FFFFFF",
    "surface_alt": "#ECEFF2",
    "border": "#D5DBE1",
    "border_strong": "#B7C0C8",
    "text_primary": "#1C242C",
    "text_secondary": "#4A5763",
    "text_muted": "#7C8893",
    "accent": "#4E6A80",
    "accent_hover": "#3F5668",
    "accent_pressed": "#314453",
    "accent_soft": "#E7EEF2",
    "accent_text": "#FFFFFF",
    "success": "#2F7A4E",
    "success_soft": "#E6F5EC",
    "warn": "#C27803",
    "warn_soft": "#F8F1E0",
    "danger": "#C2414C",
    "danger_soft": "#FBECEE",
    "info": "#4E6A80",
    "info_soft": "#E7EEF2",
    "nav_bg": "#FFFFFF",
    "nav_hover": "#ECEFF2",
    "nav_active_bg": "#E7EEF2",
    "nav_text": "#4A5763",
    "nav_muted": "#7C8893",
    "nav_active_text": "#4E6A80",
    "nav_accent": "#4E6A80",
    "table_alt": "#F6F8F9",
    "table_hover": "#ECEFF2",
    "selection": "#E7EEF2",
    "selection_text": "#1C242C",
    "disabled_bg": "#ECEFF2",
    "disabled_text": "#B7C0C8",
    "shadow": "rgba(28, 36, 44, 0.08)",
    "overlay": "rgba(28, 36, 44, 0.40)",
    "frame_line": "#5B7184",
    "frame_line_inactive": "#B7C0C8",
    "chrome_bg": "#E7EEF2",
    "chrome_bg_inactive": "#EEF1F4",
    "chrome_divider": "#D5DBE1",
    "mono": "#1C242C",
    "font_family": FONT_FAMILY,
    "font_mono": FONT_MONO,
    "radius": "12px",
    "radius_sm": "8px",
    "radius_pill": "10px",
}

TOKENS = {
    THEME_LIGHT: TOKENS_LIGHT,
    THEME_DARK: TOKENS_DARK,
    THEME_FOREST: TOKENS_FOREST,
    THEME_AMBER: TOKENS_AMBER,
    THEME_OCEAN: TOKENS_OCEAN,
    THEME_SLATE: TOKENS_SLATE,
}

RISK_TOKENS = {
    "safe": ("success", "success_soft", "安全"),
    "needs_confirm": ("warn", "warn_soft", "需确认"),
    "destructive": ("danger", "danger_soft", "危险"),
}

_current_theme = THEME_LIGHT


def palette(theme: str) -> dict[str, str]:
    return dict(TOKENS.get(theme, TOKENS_LIGHT))


def current_theme() -> str:
    return _current_theme


def current_palette() -> dict[str, str]:
    return palette(_current_theme)


def set_current_theme(theme: str) -> dict[str, str]:
    global _current_theme
    _current_theme = theme if theme in TOKENS else THEME_LIGHT
    return palette(_current_theme)


class UiSettings:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else ui_settings_path()
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


def system_theme() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return THEME_LIGHT if int(value) == 1 else THEME_DARK
    except Exception:
        return THEME_LIGHT


def resolve_theme(mode: str) -> str:
    if mode == THEME_SYSTEM:
        return system_theme()
    if mode in TOKENS:
        return mode
    return THEME_LIGHT


def apply_theme(app, mode: str) -> dict[str, str]:
    """Apply palette and QSS to the running application, then refresh chrome widgets."""
    tokens = set_current_theme(resolve_theme(mode))
    app.setStyleSheet(make_qss(tokens))
    try:
        from PySide6.QtWidgets import QWidget

        style = app.style()
        windows = list(app.topLevelWidgets())
        for window in windows:
            widgets = [window, *window.findChildren(QWidget)]
            for widget in widgets:
                if style is not None:
                    style.unpolish(widget)
                    style.polish(widget)
                if widget.objectName() in {"card", "formSection"}:
                    apply_elevation(widget, level="none" if widget.property("noElevation") else "sm")
                widget.update()
    except Exception:
        pass
    return tokens


# Drop-shadow blur used by apply_elevation("sm"). Graphics effects paint outside
# the widget rect, so stacked cards need a gap greater than this, or no effect.
ELEVATION_SM_BLUR = 16
ELEVATION_SM_OFFSET_Y = 1


def apply_elevation(widget, *, level: str = "sm") -> None:
    """用软阴影代替 QSS 无法表达的 box-shadow。level='none' 关掉会溢出边框的阴影。"""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    if level in {"none", "off"} or bool(widget.property("noElevation")):
        widget.setGraphicsEffect(None)
        return
    effect = QGraphicsDropShadowEffect(widget)
    if level == "md":
        effect.setBlurRadius(28)
        effect.setOffset(0, 8)
        effect.setColor(QColor(15, 23, 42, 22))
    else:
        effect.setBlurRadius(ELEVATION_SM_BLUR)
        effect.setOffset(0, ELEVATION_SM_OFFSET_Y)
        effect.setColor(QColor(15, 23, 42, 18))
    widget.setGraphicsEffect(effect)


_QSS_TEMPLATE = string.Template(
    """
* { outline: none; }
QWidget {
    color: $text_primary;
    font-family: $font_family;
    font-size: 13px;
}
QMainWindow { background: $bg; }
QDialog { background: $surface; }
QWidget#content { background: $bg; }
QWidget#pageRoot { background: transparent; }
QLabel#pageTitle { font-size: 18px; font-weight: 600; color: $text_primary; background: transparent; }
QLabel#emptyTitle { font-size: 20px; font-weight: 600; color: $text_primary; background: transparent; padding: 0 8px; }
QLabel#emptyHint { font-size: 13px; color: $text_secondary; background: transparent; padding: 0 8px; }
QWidget#emptyBlock { background: transparent; }
QLabel#chromePage { font-size: 13px; font-weight: 500; color: $text_secondary; }
QLabel#pageSubtitle { font-size: 13px; color: $text_secondary; background: transparent; }
QLabel#brandTitle { font-size: 13px; font-weight: 600; color: $text_primary; letter-spacing: 0.2px; }
QLabel#brandSub { font-size: 12px; color: $text_muted; }
QLabel#brandMark {
    background: $accent; color: $accent_text;
    border-radius: $radius_sm; font-size: 13px; font-weight: 700;
    qproperty-alignment: AlignCenter;
}
QLabel#sectionTitle { font-size: 13px; font-weight: 600; color: $text_secondary; }
QLabel#fieldLabel { font-size: 12px; color: $text_secondary; }
QLabel#hint { color: $text_secondary; font-size: 12px; }
QLabel#muted { color: $text_muted; font-size: 12px; }
QLabel#mono, QPlainTextEdit#mono, QTextEdit#mono {
    font-family: $font_mono; font-size: 12px; color: $mono;
}
QPlainTextEdit#mono {
    min-height: 120px;
}
QLineEdit#pageFilter {
    min-height: 28px;
    padding: 4px 8px;
}
QLabel#inlineWarn, QFrame#inlineWarn {
    color: $warn; background: $warn_soft; border: none; border-radius: $radius_sm; padding: 10px 12px;
}
QLabel#inlineDanger, QFrame#inlineDanger {
    color: $danger; background: $danger_soft; border: none; border-radius: $radius_sm; padding: 10px 12px;
}
QLabel#inlineSuccess, QFrame#inlineSuccess {
    color: $success; background: $success_soft; border: none; border-radius: $radius_sm; padding: 10px 12px;
}
QLabel#inlineInfo, QFrame#inlineInfo {
    color: $accent; background: $info_soft; border: none; border-radius: $radius_sm; padding: 10px 12px;
}
QLabel#banner {
    color: $danger; background: $danger_soft; border: none;
    padding: 10px 24px; font-weight: 600;
}
QWidget#windowRoot {
    background: $bg;
    border: none;
}
QFrame#chromeHeader {
    background: $chrome_bg;
    border: none;
    border-bottom: 1px solid $chrome_divider;
}
QFrame#chromeHeader[chromeActive="false"] {
    background: $chrome_bg_inactive;
    border-bottom: 1px solid $border;
}
QFrame#titleBar {
    background: transparent;
    border: none;
}
QFrame#titleDivider { background: $border; border: none; }
QFrame#navBar {
    background: transparent;
    border: none;
}
QFrame#contentStage { background: $bg; }
QFrame#contentCanvas {
    background: $surface;
    border: none;
    border-radius: 16px;
}
QFrame#canvasFooter {
    background: transparent;
    border: none;
    border-top: 1px solid $border;
}
QWidget#filterBar { background: transparent; }
QPushButton#topNav {
    background: transparent;
    color: $text_secondary;
    border: none;
    border-radius: 10px;
    padding: 6px 14px;
    min-height: 32px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#topNav:hover { background: $nav_hover; color: $text_primary; }
QPushButton#topNav:checked {
    background: $surface;
    color: $nav_active_text;
    font-weight: 600;
}
QPushButton#winMin, QPushButton#winMax, QPushButton#winClose {
    background: transparent;
    border: none;
    min-height: 24px;
    padding: 0;
    border-radius: 6px;
}
QFrame#card, QFrame#formSection {
    background: $surface;
    border: 1px solid $border;
    border-radius: $radius;
}
QFrame#toolbar, QWidget#toolbar { background: transparent; border: none; }
QFrame#pageHeader { background: transparent; border: none; }
QPushButton#nav {
    background: transparent;
    color: $nav_text;
    border: none;
    border-radius: $radius_pill;
    text-align: left;
    padding: 0 14px;
    font-size: 13px;
    min-height: 40px;
    margin: 1px 4px;
}
QPushButton#nav:hover { background: $nav_hover; color: $text_primary; }
QPushButton#nav:checked {
    background: $nav_active_bg;
    color: $nav_active_text;
    font-weight: 600;
}
QPushButton {
    background: $surface;
    color: $text_primary;
    border: 1px solid $border;
    border-radius: $radius_sm;
    padding: 6px 14px;
    min-height: 32px;
}
QPushButton:hover { background: $surface_alt; border-color: $border_strong; color: $text_primary; }
QPushButton:pressed { background: $nav_hover; }
QPushButton:disabled { background: $disabled_bg; color: $disabled_text; border-color: $border; }
QPushButton#primary {
    background: $accent; color: $accent_text; border: 1px solid $accent; font-weight: 600;
}
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; color: $accent_text; }
QPushButton#primary:pressed { background: $accent_pressed; border-color: $accent_pressed; color: $accent_text; }
QPushButton#danger { background: $danger; color: #FFFFFF; border: 1px solid $danger; font-weight: 600; }
QPushButton#danger:hover { background: #BE123C; border-color: #BE123C; color: #FFFFFF; }
QPushButton#dangerOutline {
    background: $surface; color: $danger; border: 1px solid $border; font-weight: 500;
}
QPushButton#dangerOutline:hover { background: $danger_soft; border-color: $danger; color: $danger; }
QPushButton#dangerOutline:disabled, QPushButton#danger:disabled, QPushButton#primary:disabled {
    background: $disabled_bg; color: $disabled_text; border-color: $border;
}
QPushButton#link {
    background: transparent; color: $accent; border: none; padding: 4px 8px; min-height: 24px;
}
QPushButton#link:hover { color: $accent_hover; }
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QDateTimeEdit {
    background: $surface; color: $text_primary;
    border: 1px solid $border; border-radius: $radius_sm;
    padding: 6px 10px; min-height: 32px;
    selection-background-color: $selection; selection-color: $selection_text;
}
QPlainTextEdit, QTextEdit { padding: 10px 12px; }
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid $accent;
}
QComboBox {
    background: $surface; color: $text_primary;
    border: 1px solid $border; border-radius: $radius_sm;
    padding: 6px 28px 6px 10px; min-height: 32px;
    combobox-popup: 0;
    selection-background-color: $selection; selection-color: $selection_text;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border: none;
}
QSpinBox, QDoubleSpinBox {
    background: $surface; color: $text_primary;
    border: 1px solid $border; border-radius: $radius_sm;
    padding: 6px 10px; min-height: 32px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0; height: 0; border: none; background: transparent;
}
QSpinBox::up-arrow, QSpinBox::down-arrow,
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
    width: 0; height: 0; image: none; border: none;
}
QComboBox QAbstractItemView, QListView#comboPopup {
    background: $surface;
    color: $text_primary;
    border: 1px solid $border;
    border-radius: $radius_sm;
    outline: 0;
    padding: 6px 0;
    margin: 0;
    min-width: 0;
    min-height: 0;
    selection-background-color: $selection;
    selection-color: $selection_text;
}
QComboBox QAbstractItemView::item, QListView#comboPopup::item {
    min-height: 32px;
    padding: 6px 12px;
    border: none;
    border-radius: 6px;
    margin: 0 6px;
}
QComboBox QAbstractItemView::item:hover, QListView#comboPopup::item:hover {
    background: $table_hover;
}
QComboBox QAbstractItemView::item:selected, QListView#comboPopup::item:selected {
    background: $selection;
    color: $accent;
}
QCheckBox, QRadioButton { color: $text_primary; spacing: 8px; }
QCheckBox:disabled, QRadioButton:disabled { color: $disabled_text; }
QCheckBox:unchecked, QRadioButton:unchecked { color: $text_primary; }
QTableView, QTableWidget, QTreeView {
    background: $surface;
    alternate-background-color: $table_alt;
    border: 1px solid $border;
    border-radius: $radius;
    gridline-color: transparent;
    selection-background-color: $selection;
    selection-color: $selection_text;
}
QListView {
    background: $surface;
    color: $text_primary;
    border: 1px solid $border;
    border-radius: $radius_sm;
    outline: 0;
    selection-background-color: $selection;
    selection-color: $selection_text;
}
QTableView::item, QTableWidget::item {
    padding: 8px 12px;
    border: none;
}
QTableView::item:hover, QTableWidget::item:hover {
    background: $table_hover;
}
QHeaderView::section {
    background: $surface_alt;
    color: $text_muted;
    font-weight: 600;
    font-size: 12px;
    border: none;
    border-bottom: 1px solid $border;
    padding: 10px 12px;
    text-align: left;
}
QHeaderView::down-arrow, QHeaderView::up-arrow {
    image: none;
    width: 0;
    height: 0;
}

QTabWidget::pane {
    background: $surface;
    border: 1px solid $border;
    border-radius: $radius;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: $text_secondary;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 16px;
    min-width: 72px;
    font-size: 13px;
    margin-right: 4px;
}
QTabBar::tab:hover { color: $text_primary; }
QTabBar::tab:selected {
    color: $accent; font-weight: 600; border-bottom: 2px solid $accent;
}
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 4px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSplitter::handle { background: $border; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QTreeWidget#keyTree {
    background: $surface;
    border: none;
    border-radius: 0;
    outline: 0;
    show-decoration-selected: 1;
    font-size: 13px;
}
QTreeWidget#keyTree::item {
    height: 26px;
    padding: 2px 6px;
    border: none;
    border-radius: 6px;
    color: $text_primary;
}
QTreeWidget#keyTree::item:hover { background: $table_hover; }
QTreeWidget#keyTree::item:selected { background: $accent_soft; color: $text_primary; }
QTreeWidget#keyTree::branch { background: transparent; }
QPushButton#tool, QPushButton#rowAction {
    background: transparent;
    color: $accent;
    border: none;
    border-radius: 6px;
    padding: 2px 8px;
    min-height: 24px;
    min-width: 0;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#rowAction {
    padding: 2px 8px 2px 0;
    text-align: left;
}
QPushButton#tool { color: $text_primary; border: 1px solid $border; background: $surface; }
QPushButton#tool:hover { background: $surface_alt; border-color: $border_strong; }
QPushButton#rowAction:hover { background: $accent_soft; color: $accent_hover; }
QPushButton#consoleRun {
    background: $accent;
    color: $accent_text;
    border: 1px solid $accent;
    border-radius: $radius_sm;
    padding: 4px 12px;
    min-height: 28px;
    min-width: 64px;
    max-width: 96px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#consoleRun:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#consoleRun:disabled { background: $disabled_bg; color: $disabled_text; border-color: $border; }
QPlainTextEdit#consoleOutput, QPlainTextEdit#commandLog {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: $radius_sm;
    padding: 8px 10px;
    font-family: $font_mono;
    font-size: 12px;
    color: $mono;
}
QWidget#workspaceHeader, QWidget#treeToolbar, QWidget#consoleBar, QWidget#keyActionBar, QWidget#rowActions {
    background: transparent;
    border: none;
}
QLabel#workspaceMeta { font-size: 13px; font-weight: 600; color: $text_primary; }
QLabel#keyEmpty, QLabel#logToggle {
    color: $text_muted;
    font-size: 12px;
    background: transparent;
}
QPushButton#logToggle {
    min-height: 22px;
    max-height: 24px;
    padding: 0 6px;
    font-size: 12px;
    font-weight: 500;
    background: transparent;
    border: none;
}
QWidget#workspaceRoot { background: $surface; }
QTableWidget#connectionTable { border: 1px solid $border; }
QWidget#diagPage,
QWidget#diagPage QWidget#pageRoot,
QScrollArea#diagGridScroll,
QWidget#diagGridViewport,
QWidget#diagGridHost {
    background: $surface;
    border: none;
}
QTableWidget#diagTable, QTableWidget#keyValueTable {
    border: 1px solid $border;
    border-radius: 0;
}

QWidget#diagSection {
    background: $surface;
    border: none;
}
QLabel#diagSectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: $text_secondary;
    padding: 0;
    background: transparent;
    border: none;
}
QFrame#diagSectionRule {
    color: $border;
    background: $border;
    border: none;
    max-height: 1px;
}

QToolTip {
    background: $surface; color: $text_primary;
    border: 1px solid $border; border-radius: $radius_sm; padding: 6px 10px;
}
QMessageBox { background: $surface; }
QScrollArea#dialogScroll, QWidget#dialogBody, QWidget#dialogSection {
    background: $surface; border: none;
}
QFrame#dialogHeader {
    background: $surface;
    border: none;
}
QLabel#dialogTitle {
    font-size: 18px;
    font-weight: 600;
    color: $text_primary;
    background: transparent;
    padding: 0;
}
QPushButton#dialogClose {
    background: transparent;
    border: none;
    min-height: 24px;
    padding: 0;
    border-radius: 6px;
}
QFrame#dialogFooter {
    background: $surface;
    border: none;
    border-top: 1px solid $border;
}
QFrame#dialogStatus {
    background: $surface;
    border: none;
    padding: 0;
}
QFrame#dialogStatus[tone="danger"] { background: $danger_soft; }
QFrame#dialogStatus[tone="warn"] { background: $warn_soft; }
QFrame#dialogStatus[tone="success"] { background: $success_soft; }
QFrame#dialogStatus[tone="info"] { background: $info_soft; }
QLabel#dialogStatusLabel {
    background: transparent;
    border: none;
    padding: 0;
    font-size: 12px;
    color: $text_secondary;
}
QLabel#dialogStatusLabel[tone="danger"] { color: $danger; }
QLabel#dialogStatusLabel[tone="warn"] { color: $warn; }
QLabel#dialogStatusLabel[tone="success"] { color: $success; }
QLabel#dialogStatusLabel[tone="info"] { color: $accent; }
QLineEdit[invalid="true"], QLineEdit[invalid="true"]:focus {
    border: 1px solid $danger;
}
QDialog#scrollableDialog {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QMenu {
    background: $surface;
    color: $text_primary;
    border: 1px solid $border;
    border-radius: $radius_sm;
    padding: 6px 0;
}
QMenu::item {
    padding: 8px 16px;
    min-height: 28px;
    border-radius: 6px;
    margin: 0 6px;
}
QMenu::item:selected {
    background: $selection;
    color: $accent;
}
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 6px 10px;
}
QDialogButtonBox QPushButton { min-width: 76px; }
"""
)


def make_qss(tokens: dict[str, str]) -> str:
    return _QSS_TEMPLATE.substitute(tokens)


def badge_qss(tokens: dict[str, str], fg: str, bg: str, *, strong: bool = False) -> str:
    # 状态胶囊：柔和底色，不再使用刺眼的同色描边。
    return (
        "QLabel {"
        f" color: {tokens[fg]}; background: {tokens[bg]};"
        " border: none; border-radius: 8px;"
        " padding: 2px 8px; font-size: 11px; font-weight: 600;"
        "}"
    )


def app_font():
    """返回适合中文 Windows 的界面字体（需在 QApplication 创建后调用）。"""
    from PySide6.QtGui import QFont, QFontDatabase

    families = set(QFontDatabase().families())
    for name in ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei", "Segoe UI"):
        if name in families:
            font = QFont(name)
            font.setPixelSize(13)
            font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
            return font
    font = QFont()
    font.setPixelSize(13)
    return font
