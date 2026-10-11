"""Shared color palettes for stylesheets, custom painting and icons."""
from dataclasses import dataclass
from functools import lru_cache
from string import Template

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

DEFAULT_THEME = "blue"


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
}


def normalize_theme(name):
    return name if isinstance(name, str) and name in THEMES else DEFAULT_THEME


def _mix(first, second, ratio):
    first, second = QColor(first), QColor(second)
    return QColor(*(round(a * (1 - ratio) + b * ratio) for a, b in zip(
        (first.red(), first.green(), first.blue()), (second.red(), second.green(), second.blue())))).name()


@lru_cache(maxsize=len(THEMES))
def theme_colors(name):
    theme = THEMES[normalize_theme(name)]
    tint = lambda ratio: _mix(theme.accent, "#ffffff", ratio)
    return {
        "accent": theme.accent, "accent_hover": theme.hover,
        "accent_dark": _mix(theme.accent, "#1f2937", .45),
        "text": "#283446", "muted": "#6e7b8e", "subtle": "#8591a3",
        "icon": _mix(theme.accent, "#6e7b8e", .55),
        "window": tint(.965), "surface": tint(.99), "sidebar": tint(.975),
        "selected": tint(.90), "pressed": tint(.83), "border": tint(.84),
        "strong_border": tint(.55), "canvas": tint(.92), "shadow": tint(.78),
        "notice": tint(.55), "notice_border": tint(.35),
        "notice_hover": tint(.47), "disabled": "#eef1f5", "disabled_text": "#9aa4b2",
        "tooltip": "#283446",
    }


def theme_color(role, name=None):
    app = QApplication.instance()
    if name is None:
        name = app.property("colorTheme") if app else DEFAULT_THEME
    return theme_colors(normalize_theme(name))[role]

_STYLE_TEMPLATE = """
QWidget { color: $text; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QWidget#Welcome, QDialog { background: $window; }
QLabel { background: transparent; }
QLabel#StartTitle { font-size: 26px; font-weight: 600; color: $accent_dark; }
QLabel#Caption { font-size: 13px; color: $muted; }
QLabel#Accent { font-size: 10px; font-weight: 600; color: $subtle; padding-top: 12px; }
QFrame#StartCard { background: white; border: 1px solid $border; border-radius: 18px; }
QFrame#PrintCard { background: white; border: 1px solid $border; border-radius: 12px; }
QScrollArea#PrintOptions, QScrollArea#PrintOptions > QWidget > QWidget { background: $window; }
QDialog#PrintDialog QComboBox { padding-right: 26px; }
QDialog#PrintDialog QComboBox::drop-down { width: 24px; }
QDialog#PrintDialog QComboBox::down-arrow { width: 0; height: 0; }
QDialog#PrintDialog QProgressBar { border: 1px solid $border; border-radius: 6px; text-align: center; background: $surface; }
QDialog#PrintDialog QProgressBar::chunk { background: $selected; border-radius: 5px; }
QMenuBar { background: $surface; color: $muted; padding: 1px 8px; border-bottom: 1px solid $border; }
QMenuBar::item { padding: 4px 8px; background: transparent; }
QMenuBar::item:selected { background: $selected; border-radius: 3px; }
QMenu { background: white; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 7px 26px 7px 10px; border-radius: 3px; }
QMenu::item:selected { background: $selected; color: $accent_dark; }
QMenu::item:disabled { color: $disabled_text; }
QMenu::separator { height: 1px; background: $border; margin: 4px 7px; }
QToolBar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #ffffff,stop:1 $surface); border: 0; border-bottom: 1px solid $border; spacing: 3px; padding: 5px 8px; }
QToolBar::separator { background: $border; width: 1px; margin: 4px 7px; }
QToolButton { background: transparent; padding: 5px 6px; border: 1px solid transparent; border-radius: 7px; }
QToolButton:hover, QToolButton:checked { background: $selected; border-color: $strong_border; }
QToolButton:pressed { background: $pressed; }
QToolButton:disabled { color: $disabled_text; }
QToolButton::menu-button { border: 0; border-left: 1px solid $border; width: 14px; }
QPushButton { background: white; border: 1px solid $border; border-radius: 8px; padding: 8px 12px; }
QPushButton:hover { background: $selected; border-color: $strong_border; }
QPushButton#Primary { background: $accent; color: white; border: 0; font-weight: 600; padding: 10px 16px; }
QPushButton#Primary:hover { background: $accent_hover; }
QPushButton#RecentFile { text-align: left; padding: 7px 10px; font-size: 12px; border-color: transparent; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit { background: $surface; border: 1px solid $border; border-radius: 6px; padding: 4px 7px; selection-background-color: $accent; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: $strong_border; background: white; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: $disabled_text; background: $disabled; }
QComboBox::drop-down { border: 0; width: 16px; }
QTabWidget::pane { border: 0; }
QTabBar { background: $window; }
QTabBar::tab { background: $window; color: $muted; padding: 8px 12px; margin-right: 3px; border-top-left-radius: 7px; border-top-right-radius: 7px; min-width: 70px; }
QTabBar::tab:selected { background: white; color: $accent; border-bottom: 2px solid $strong_border; }
QTabBar::tab:hover { background: $selected; }
QTabWidget#DocumentTabs::pane { background: white; }
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
QTabBar#DocumentTabBar::tab:selected { background: white; color: $accent_dark; border-color: $strong_border; border-bottom-color: $accent; }
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
QToolBar#NavigationBar { border-top: 1px solid $border; border-bottom: 0; padding: 4px 12px; }
QStatusBar { background: $surface; border-top: 1px solid $border; color: $muted; padding: 0px 9px; font-size: 11px; }
QStatusBar QLabel { color: $muted; font-size: 11px; }
QStatusBar::item { border: 0; }
QFrame#DocumentNotice { background: $notice; border-bottom: 1px solid $notice_border; }
QFrame#DocumentNotice QLabel { color: $accent_dark; font-size: 12px; font-weight: 600; }
QFrame#DocumentNotice[modified="true"] { background: #f2b544; border-bottom: 1px solid #c68d24; }
QFrame#DocumentNotice[modified="true"] QLabel { color: #392807; }
QFrame#DocumentNotice QToolButton { color: $accent_dark; border: 1px solid $notice_border; font-size: 11px; padding: 2px 8px; }
QFrame#DocumentNotice QToolButton:hover { background: $notice_hover; }
QFrame#DocumentNotice[modified="true"] QToolButton { color: #392807; border-color: #9c701f; }
QFrame#DocumentNotice[modified="true"] QToolButton:hover { background: #e5a42f; }
QFrame#DocumentNotice[securityWarning="true"] { background: #fff0cf; border-bottom: 1px solid #e1c07d; }
QFrame#DocumentNotice[securityWarning="true"] QLabel { color: #63440e; }
QFrame#DocumentNotice[securityWarning="true"] QToolButton { color: #63440e; border-color: #c79f54; }
QFrame#DocumentNotice[securityWarning="true"] QToolButton:hover { background: #f6dfb2; }
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
QLabel#MetadataWarning { background: #fff4da; color: #795625; border-radius: 8px; padding: 10px; font-size: 12px; }
QLabel#MetadataFeedback { color: $accent_dark; font-size: 12px; }
QDialog#NoteDialog { background: #fff3ba; }
QDialog#NoteDialog QPlainTextEdit { background: #fff9de; border-color: #e1c361; }
QLabel#EditFeedback { color: #a4392d; font-size: 12px; }
QFrame#CompletionToast { background: $surface; border: 1px solid $strong_border; border-radius: 12px; }
QLabel#ToastSuccess { background: $selected; color: $accent; border-radius: 16px; font-size: 21px; font-weight: 600; }
QLabel#ToastTitle { color: $accent_dark; font-size: 13px; font-weight: 600; }
QLabel#ToastFilename { color: $text; font-size: 12px; }
QLabel#ToastDetail { color: $muted; font-size: 11px; }
QTabWidget#PropertiesTabs::pane { background: white; border: 1px solid $border; border-radius: 9px; }
QTabWidget#PropertiesTabs QScrollArea, QTabWidget#PropertiesTabs QScrollArea > QWidget > QWidget { background: white; }
QTabWidget#PropertiesTabs QTabBar::tab { min-width: 0; padding: 9px 14px; }
QTableWidget#MetadataTable { background: white; alternate-background-color: $sidebar; border: 1px solid $border; border-radius: 6px; gridline-color: $border; }
QHeaderView::section { background: $selected; color: $muted; padding: 7px; border: 0; }
QPlainTextEdit#XmpSource { font-family: 'Consolas'; font-size: 12px; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: $strong_border; border-radius: 3px; min-height: 26px; }
QScrollBar:horizontal { height: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { background: $strong_border; border-radius: 3px; min-width: 26px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: $accent_dark; color: white; border: 0; padding: 5px; }
"""

def stylesheet(name=DEFAULT_THEME):
    return Template(_STYLE_TEMPLATE).substitute(theme_colors(normalize_theme(name)))


def apply_theme(app: QApplication, name=DEFAULT_THEME):
    name = normalize_theme(name)
    colors = theme_colors(name)
    app.setProperty("colorTheme", name)
    if app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    if app.font() != font:
        app.setFont(font)
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: colors["window"], QPalette.ColorRole.WindowText: colors["text"],
        QPalette.ColorRole.Base: "#ffffff", QPalette.ColorRole.AlternateBase: colors["sidebar"],
        QPalette.ColorRole.Text: colors["text"], QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: colors["text"], QPalette.ColorRole.Highlight: colors["accent"],
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.Dark: colors["border"],
        QPalette.ColorRole.ToolTipBase: colors["tooltip"], QPalette.ColorRole.ToolTipText: "#ffffff",
    }.items():
        palette.setColor(role, QColor(color))
    if app.palette() != palette:
        app.setPalette(palette)
    css = stylesheet(name)
    if app.styleSheet() != css:
        app.setStyleSheet(css)
    return name
