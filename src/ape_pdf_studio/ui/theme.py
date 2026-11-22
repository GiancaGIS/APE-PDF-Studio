"""Shared color palettes for stylesheets, custom painting and icons."""
from dataclasses import dataclass
from functools import lru_cache
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

DEFAULT_THEME = "blue"
DEFAULT_MODE = "light"


@dataclass(frozen=True)
class ColorTheme:
    label: str
    accent: str
    hover: str


THEMES = {
    "blue": ColorTheme("Blu (predefinito)", "#386ea8", "#2d5d91"),
    "teal": ColorTheme("Petrolio", "#267a86", "#1f6570"),
    "green": ColorTheme("Verde salvia", "#34755d", "#28634e"),
    "graphite": ColorTheme("Grigio grafite", "#576675", "#455461"),
    "violet": ColorTheme("Violetto", "#8961bb", "#7851aa"),
    "coral": ColorTheme("Corallo", "#a34840", "#8b3933"),
    "ochre": ColorTheme("Ocra", "#87601d", "#714e15"),
    "raspberry": ColorTheme("Rosa lampone", "#a04472", "#8c355f"),
}


def normalize_theme(name):
    return name if isinstance(name, str) and name in THEMES else DEFAULT_THEME


def normalize_mode(mode):
    return mode if mode in ("light", "dark", "system") else DEFAULT_MODE


def is_dark_mode(mode):
    if mode == "system":
        app = QApplication.instance()
        return app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return mode == "dark"


def _mix(first, second, ratio):
    first, second = QColor(first), QColor(second)
    return QColor(*(round(a * (1 - ratio) + b * ratio) for a, b in zip(
        (first.red(), first.green(), first.blue()), (second.red(), second.green(), second.blue())))).name()


@lru_cache(maxsize=2 * len(THEMES))
def theme_colors(name, dark=False):
    theme = THEMES[normalize_theme(name)]
    if dark:
        accent = _mix(theme.accent, "#ffffff", .50)
        tint = lambda ratio: _mix(accent, "#242c39", ratio)
        return {
            "accent": accent, "accent_hover": _mix(accent, "#ffffff", .22),
            "accent_dark": _mix(accent, "#ffffff", .25), "on_accent": "#121823",
            "text": "#e8edf5", "muted": "#b7c1d1", "subtle": "#a2aec2",
            "icon": _mix(accent, "#bdc7d8", .4),
            "window": "#1d242f", "surface": "#252d39", "base": "#2c3543", "sidebar": "#222a36",
            "selected": tint(.84), "pressed": tint(.73), "border": "#404c5e",
            "strong_border": tint(.60), "canvas": "#151b24", "shadow": "#0e1219",
            "notice": tint(.82), "notice_border": tint(.60), "notice_hover": tint(.71),
            "disabled": "#252c37", "disabled_text": "#8793a5", "tooltip": "#151b24",
            "modified": "#533b19", "modified_border": "#977443", "modified_text": "#ffe1ab",
            "modified_hover": "#674c23",
            "warning": "#40321e", "warning_border": "#856b42", "warning_text": "#ffe1ac",
            "warning_hover": "#594426", "metadata_warning": "#40321e", "metadata_text": "#ffe1ac",
            "note": "#383021", "note_input": "#453a26", "note_border": "#90764e",
            "error": "#ffab9e",
        }
    tint = lambda ratio: _mix(theme.accent, "#ffffff", ratio)
    return {
        "accent": theme.accent, "accent_hover": theme.hover,
        "accent_dark": _mix(theme.accent, "#1f2937", .45), "on_accent": "#ffffff",
        "text": "#283446", "muted": "#6e7b8e", "subtle": "#8591a3",
        "icon": _mix(theme.accent, "#6e7b8e", .55),
        "window": tint(.965), "surface": tint(.99), "base": "#ffffff", "sidebar": tint(.975),
        "selected": tint(.90), "pressed": tint(.83), "border": tint(.84),
        "strong_border": tint(.55), "canvas": tint(.92), "shadow": tint(.78),
        "notice": tint(.55), "notice_border": tint(.35),
        "notice_hover": tint(.47), "disabled": "#eef1f5", "disabled_text": "#9aa4b2",
        "tooltip": "#283446",
        "modified": "#f2b544", "modified_border": "#c68d24", "modified_text": "#392807",
        "modified_hover": "#e5a42f",
        "warning": "#fff0cf", "warning_border": "#e1c07d", "warning_text": "#63440e",
        "warning_hover": "#f6dfb2", "metadata_warning": "#fff4da", "metadata_text": "#795625",
        "note": "#fff3ba", "note_input": "#fff9de", "note_border": "#e1c361",
        "error": "#a4392d",
    }


def theme_color(role, name=None):
    app = QApplication.instance()
    if name is None:
        name = app.property("colorTheme") if app else DEFAULT_THEME
    return theme_colors(normalize_theme(name), bool(app and app.property("darkAppearance")))[role]

_STYLE_TEMPLATE = """
QWidget { color: $text; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QWidget#Welcome, QDialog { background: $window; }
QLabel { background: transparent; }
QLabel#StartTitle { font-size: 26px; font-weight: 600; color: $accent_dark; }
QLabel#Caption { font-size: 13px; color: $muted; }
QLabel#Accent { font-size: 10px; font-weight: 600; color: $subtle; padding-top: 12px; }
QFrame#StartCard { background: $base; border: 1px solid $border; border-radius: 18px; }
QFrame#PrintCard { background: $base; border: 1px solid $border; border-radius: 12px; }
QScrollArea#PrintOptions, QScrollArea#PrintOptions > QWidget > QWidget { background: $window; }
QDialog#PrintDialog QComboBox { padding-right: 26px; }
QDialog#PrintDialog QComboBox::drop-down { width: 24px; }
QDialog#PrintDialog QComboBox::down-arrow { width: 0; height: 0; }
QDialog#PrintDialog QProgressBar { border: 1px solid $border; border-radius: 6px; text-align: center; background: $surface; }
QDialog#PrintDialog QProgressBar::chunk { background: $selected; border-radius: 5px; }
QMenuBar { background: $surface; color: $muted; padding: 1px 8px; border-bottom: 1px solid $border; }
QMenuBar::item { padding: 4px 8px; background: transparent; }
QMenuBar::item:selected { background: $selected; border-radius: 3px; }
QMenu { background: $base; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 7px 26px 7px 10px; border-radius: 3px; }
QMenu::item:selected { background: $selected; color: $accent_dark; }
QMenu::item:disabled { color: $disabled_text; }
QMenu::separator { height: 1px; background: $border; margin: 4px 7px; }
QToolBar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 $base,stop:1 $surface); border: 0; border-bottom: 1px solid $border; spacing: 3px; padding: 5px 8px; }
QToolBar::separator { background: $border; width: 1px; margin: 4px 7px; }
QToolButton { background: transparent; padding: 5px 6px; border: 1px solid transparent; border-radius: 7px; }
QToolButton:hover, QToolButton:checked { background: $selected; border-color: $strong_border; }
QToolButton:pressed { background: $pressed; }
QToolBar#InkToolbar { background: $surface; border-bottom: 1px solid $border; padding: 4px 8px; spacing: 3px; }
QToolButton#InkTool:checked { background: $selected; color: $accent_dark; border: 1px solid $strong_border; }
QToolButton#InkStyleButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: right center; right: 4px; }
QFrame#InkStylePanel { background: $surface; border: 0; }
QScrollArea#InkStyleScroll, QScrollArea#InkStyleScroll > QWidget > QWidget { background: $surface; border: 0; }
QToolButton:disabled { color: $disabled_text; }
QToolButton::menu-button { border: 0; border-left: 1px solid $border; width: 14px; }
QPushButton { background: $base; border: 1px solid $border; border-radius: 8px; padding: 8px 12px; }
QPushButton:hover { background: $selected; border-color: $strong_border; }
QPushButton#Primary { background: $accent; color: $on_accent; border: 0; font-weight: 600; padding: 10px 16px; }
QPushButton#Primary:hover { background: $accent_hover; }
QPushButton#RecentFile { text-align: left; padding: 7px 10px; font-size: 12px; border-color: transparent; }
QLineEdit, QAbstractSpinBox, QComboBox, QTextEdit, QPlainTextEdit { background: $surface; border: 1px solid $border; border-radius: 6px; padding: 4px 7px; selection-background-color: $accent; selection-color: $on_accent; }
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: $strong_border; background: $base; }
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled { color: $disabled_text; background: $disabled; }
QCheckBox:disabled, QRadioButton:disabled { color: $disabled_text; }
QTabWidget::pane { border: 0; }
QTabBar { background: $window; }
QTabBar::tab { background: $window; color: $muted; padding: 8px 12px; margin-right: 3px; border-top-left-radius: 7px; border-top-right-radius: 7px; min-width: 70px; }
QTabBar::tab:selected { background: $base; color: $accent; border-bottom: 2px solid $strong_border; }
QTabBar::tab:hover { background: $selected; }
QTabWidget#DocumentTabs::pane { background: $base; }
QTabBar#DocumentTabBar { background: $canvas; border-bottom: 1px solid $border; }
QTabBar#DocumentTabBar::tab {
    background: $window; color: $muted; font-weight: 600;
    min-width: 120px; max-width: 260px; padding: 8px 12px;
    margin: 6px 3px 0px 3px;
    border: 1px solid $border; border-bottom: 3px solid transparent;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
}
QTabBar#DocumentTabBar::tab:first { margin-left: 8px; }
QTabBar#DocumentTabBar::tab:hover { background: $selected; color: $accent_dark; border-color: $strong_border; border-bottom-color: transparent; }
QTabBar#DocumentTabBar::tab:selected { background: $base; color: $accent_dark; border-color: $strong_border; border-bottom-color: $accent; }
QTabBar#DocumentTabBar[activePane="false"]::tab:selected { background: $surface; color: $muted; border-bottom-color: $strong_border; }
QTabBar#DocumentTabBar::scroller { width: 64px; }
QTabBar#DocumentTabBar QToolButton[tabScroll="true"] { background: $canvas; color: $accent; border: 1px solid $border; border-radius: 6px; padding: 0; margin: 7px 2px; }
QTabBar#DocumentTabBar QToolButton[tabScroll="true"]:hover { background: $selected; border-color: $strong_border; }
QTabBar#DocumentTabBar QToolButton[tabScroll="true"]:disabled { color: $disabled_text; }
QToolButton#DocumentTabClose { background: transparent; border: 0; border-radius: 6px; padding: 0; }
QToolButton#DocumentTabClose:hover { background: $pressed; }
QToolButton#DocumentTabClose:pressed { background: $strong_border; }
QTabWidget#Sidebar QTabBar::tab { min-width: 0; padding: 7px 8px; font-size: 11px; }
QListWidget, QTreeView, QListView { background: $sidebar; border: 0; outline: 0; }
QTreeView::item, QListView::item { padding: 5px 4px; }
QTreeView::item:selected, QListView::item:selected { background: $selected; color: $accent_dark; }
QSplitter::handle { background: $border; width: 1px; }
QSplitter#DocumentSplit::handle { background: $strong_border; }
QSplitter#DocumentSplit::handle:hover { background: $accent; }
QToolBar#NavigationBar { border-top: 1px solid $border; border-bottom: 0; padding: 4px 12px; }
QStatusBar { background: $surface; border-top: 1px solid $border; color: $muted; padding: 0px 9px; font-size: 11px; }
QStatusBar QLabel { color: $muted; font-size: 11px; }
QStatusBar::item { border: 0; }
QFrame#DocumentNotice { background: $notice; border-bottom: 1px solid $notice_border; }
QFrame#DocumentNotice QLabel { color: $accent_dark; font-size: 12px; font-weight: 600; }
QFrame#DocumentNotice[modified="true"] { background: $modified; border-bottom: 1px solid $modified_border; }
QFrame#DocumentNotice[modified="true"] QLabel { color: $modified_text; }
QFrame#DocumentNotice QToolButton { color: $accent_dark; border: 1px solid $notice_border; font-size: 11px; padding: 2px 8px; }
QFrame#DocumentNotice QToolButton:hover { background: $notice_hover; }
QFrame#DocumentNotice[modified="true"] QToolButton { color: $modified_text; border-color: $modified_border; }
QFrame#DocumentNotice[modified="true"] QToolButton:hover { background: $modified_hover; }
QFrame#DocumentNotice[securityWarning="true"] { background: $warning; border-bottom: 1px solid $warning_border; }
QFrame#DocumentNotice[securityWarning="true"] QLabel { color: $warning_text; }
QFrame#DocumentNotice[securityWarning="true"] QToolButton { color: $warning_text; border-color: $warning_border; }
QFrame#DocumentNotice[securityWarning="true"] QToolButton:hover { background: $warning_hover; }
QLabel#BrandName { color: $accent; font-size: 11px; font-weight: 600; }
QLabel#DocumentBadge { background: $selected; border-radius: 14px; }
QToolButton#PrimaryTool { background: $selected; color: $accent_dark; border-color: $border; }
QToolButton#PrimaryTool:hover { background: $pressed; }
QToolButton#PrimaryTool:disabled { background: $window; color: $disabled_text; border-color: $disabled; }
QPushButton#Primary:disabled { background: $disabled; color: $disabled_text; }
QLineEdit:read-only { background: $sidebar; color: $muted; }
QStatusBar QLabel#DocumentState { background: $selected; color: $muted; border-radius: 5px; padding: 2px 8px; }
QLabel#DialogTitle { font-size: 20px; font-weight: 600; color: $accent_dark; }
QLabel#SectionTitle { color: $accent_dark; font-size: 13px; font-weight: 600; }
QLabel#MetadataWarning { background: $metadata_warning; color: $metadata_text; border-radius: 8px; padding: 10px; font-size: 12px; }
QLabel#MetadataFeedback { color: $accent_dark; font-size: 12px; }
QDialog#NoteDialog { background: $note; }
QDialog#NoteDialog QPlainTextEdit { background: $note_input; border-color: $note_border; }
QFrame#InlineTextToolbar { background: $surface; border: 1px solid $strong_border; border-radius: 8px; }
QLabel#EditFeedback { color: $error; font-size: 12px; }
QFrame#CompletionToast { background: $surface; border: 1px solid $strong_border; border-radius: 12px; }
QLabel#ToastSuccess { background: $selected; color: $accent; border-radius: 16px; font-size: 21px; font-weight: 600; }
QLabel#ToastTitle { color: $accent_dark; font-size: 13px; font-weight: 600; }
QLabel#ToastFilename { color: $text; font-size: 12px; }
QLabel#ToastDetail { color: $muted; font-size: 11px; }
QTabWidget#PropertiesTabs::pane { background: $base; border: 1px solid $border; border-radius: 9px; }
QTabWidget#PropertiesTabs QScrollArea, QTabWidget#PropertiesTabs QScrollArea > QWidget > QWidget { background: $base; }
QTabWidget#PropertiesTabs QTabBar::tab { min-width: 0; padding: 9px 14px; }
QTableWidget#MetadataTable { background: $base; alternate-background-color: $sidebar; border: 1px solid $border; border-radius: 6px; gridline-color: $border; }
QHeaderView::section { background: $selected; color: $muted; padding: 7px; border: 0; }
QPlainTextEdit#XmpSource { font-family: 'Consolas'; font-size: 12px; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: $strong_border; border-radius: 3px; min-height: 26px; }
QScrollBar:horizontal { height: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { background: $strong_border; border-radius: 3px; min-width: 26px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: $tooltip; color: #ffffff; border: 0; padding: 5px; }
"""

def stylesheet(name=DEFAULT_THEME, dark=False):
    return Template(_STYLE_TEMPLATE).substitute(theme_colors(normalize_theme(name), dark))


def apply_theme(app: QApplication, name=DEFAULT_THEME, mode=DEFAULT_MODE):
    name = normalize_theme(name)
    dark = is_dark_mode(mode)
    colors = theme_colors(name, dark)
    app.setProperty("colorTheme", name)
    app.setProperty("darkAppearance", dark)
    if app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    if app.font() != font:
        app.setFont(font)
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: colors["window"], QPalette.ColorRole.WindowText: colors["text"],
        QPalette.ColorRole.Base: colors["base"], QPalette.ColorRole.AlternateBase: colors["sidebar"],
        QPalette.ColorRole.Text: colors["text"], QPalette.ColorRole.Button: colors["base"],
        QPalette.ColorRole.ButtonText: colors["text"], QPalette.ColorRole.Highlight: colors["accent"],
        QPalette.ColorRole.HighlightedText: colors["on_accent"], QPalette.ColorRole.Dark: colors["border"],
        QPalette.ColorRole.Light: colors["strong_border"], QPalette.ColorRole.Midlight: colors["selected"],
        QPalette.ColorRole.Mid: colors["border"], QPalette.ColorRole.Shadow: colors["shadow"],
        QPalette.ColorRole.BrightText: colors["text"], QPalette.ColorRole.PlaceholderText: colors["muted"],
        QPalette.ColorRole.Link: colors["accent"], QPalette.ColorRole.LinkVisited: colors["accent_dark"],
        QPalette.ColorRole.Accent: colors["accent"],
        QPalette.ColorRole.ToolTipBase: colors["tooltip"], QPalette.ColorRole.ToolTipText: "#ffffff",
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_text"]))
    if app.palette() != palette:
        app.setPalette(palette)
    css = stylesheet(name, dark)
    if app.styleSheet() != css:
        app.setStyleSheet(css)
    return name
