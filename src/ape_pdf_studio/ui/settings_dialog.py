"""Persistent appearance and language preferences with an isolated color preview."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QPushButton, QVBoxLayout,
)

from .theme import DEFAULT_THEME, THEMES, normalize_theme, theme_colors
from ..core.localization import LANGUAGES, current_language, normalize_language, tr


class SettingsDialog(QDialog):
    theme_applied = Signal(str)
    language_applied = Signal(str)

    def __init__(self, theme_name=DEFAULT_THEME, parent=None, *, language_name=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Impostazioni · Aspetto e lingua"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        title = QLabel(tr("Scegli i tuoi colori"))
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        caption = QLabel(tr("Il tema si applica a tutta l'interfaccia e viene ricordato al prossimo avvio."))
        caption.setObjectName("Caption")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        row = QHBoxLayout()
        label = QLabel(tr("Tema colori"))
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName(tr("Tema colori"))
        label.setBuddy(self.theme_combo)
        for name, theme in THEMES.items():
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(theme.accent))
            self.theme_combo.addItem(QIcon(swatch), tr(theme.label), name)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(normalize_theme(theme_name)))
        row.addWidget(label)
        row.addWidget(self.theme_combo, 1)
        layout.addLayout(row)
        language_row = QHBoxLayout()
        language_label = QLabel(tr("Lingua dell’interfaccia"))
        self.language_combo = QComboBox()
        self.language_combo.setAccessibleName(tr("Lingua dell’interfaccia"))
        language_label.setBuddy(self.language_combo)
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        chosen = normalize_language(language_name if language_name is not None else current_language())
        self.language_combo.setCurrentIndex(self.language_combo.findData(chosen))
        language_row.addWidget(language_label)
        language_row.addWidget(self.language_combo, 1)
        layout.addLayout(language_row)
        language_caption = QLabel(tr("La lingua si applica al prossimo avvio del programma."))
        language_caption.setObjectName("Caption")
        language_caption.setWordWrap(True)
        layout.addWidget(language_caption)
        self.preview = QFrame()
        self.preview.setObjectName("ThemePreview")
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(18, 18, 18, 18)
        preview_layout.setSpacing(10)
        preview_label = QLabel(tr("Anteprima del tema"))
        preview_label.setObjectName("PreviewTitle")
        preview_layout.addWidget(preview_label)
        banner = QLabel(tr("Informazioni sul documento"))
        banner.setObjectName("PreviewNotice")
        preview_layout.addWidget(banner)
        preview_button = QPushButton(tr("Apri documento"))
        preview_button.setObjectName("PreviewButton")
        preview_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        preview_layout.addWidget(preview_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.preview)
        self.reset_button = QPushButton(tr("Ripristina blu"))
        self.reset_button.clicked.connect(lambda: self.theme_combo.setCurrentIndex(self.theme_combo.findData(DEFAULT_THEME)))
        layout.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignLeft)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("OK")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Annulla"))
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText(tr("Applica"))
        self.apply_button.clicked.connect(self.apply_selection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.theme_combo.currentIndexChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self):
        colors = theme_colors(self.theme_combo.currentData())
        self.preview.setStyleSheet(f"""
            QFrame#ThemePreview {{ background: white; border: 1px solid {colors['border']}; border-radius: 12px; }}
            QLabel#PreviewTitle {{ color: {colors['accent_dark']}; font-weight: 600; }}
            QLabel#PreviewNotice {{ background: {colors['notice']}; color: {colors['accent_dark']}; padding: 10px; border-radius: 6px; }}
            QPushButton#PreviewButton {{ background: {colors['accent']}; color: white; border: 0; padding: 8px 14px; }}
            QPushButton#PreviewButton:hover {{ background: {colors['accent_hover']}; }}
        """)

    def apply_selection(self):
        self.theme_applied.emit(self.theme_combo.currentData())
        self.language_applied.emit(self.language_combo.currentData())

    def accept(self):
        self.apply_selection()
        super().accept()
