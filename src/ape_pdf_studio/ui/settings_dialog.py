"""Persistent appearance, language and update preferences."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QLabel, QPushButton, QVBoxLayout,
)

from .theme import DEFAULT_MODE, DEFAULT_THEME, THEMES, is_dark_mode, normalize_mode, normalize_theme, theme_colors
from ..core.localization import LANGUAGES, current_language, normalize_language, tr


class SettingsDialog(QDialog):
    theme_applied = Signal(str, str)
    language_applied = Signal(str)
    auto_updates_applied = Signal(bool)

    def __init__(self, theme_name=DEFAULT_THEME, parent=None, *, appearance_mode=DEFAULT_MODE, language_name=None,
                 auto_updates=False, auto_updates_available=False):
        super().__init__(parent)
        self.setWindowTitle(tr("Impostazioni"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel(tr("Scegli i tuoi colori"))
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        preferences = QFormLayout()
        preferences.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        preferences.setVerticalSpacing(8)
        mode_label = QLabel(tr("Aspetto"))
        self.mode_combo = QComboBox()
        self.mode_combo.setAccessibleName(mode_label.text())
        mode_label.setBuddy(self.mode_combo)
        self.mode_combo.addItem(tr("Chiaro"), "light")
        self.mode_combo.addItem(tr("Scuro"), "dark")
        self.mode_combo.addItem(tr("Segui il sistema"), "system")
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(normalize_mode(appearance_mode)))
        preferences.addRow(mode_label, self.mode_combo)
        label = QLabel(tr("Tema colori"))
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName(tr("Tema colori"))
        label.setBuddy(self.theme_combo)
        for name, theme in THEMES.items():
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(theme.accent))
            self.theme_combo.addItem(QIcon(swatch), tr(theme.label), name)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(normalize_theme(theme_name)))
        preferences.addRow(label, self.theme_combo)
        language_label = QLabel(tr("Lingua dell’interfaccia"))
        self.language_combo = QComboBox()
        self.language_combo.setAccessibleName(tr("Lingua dell’interfaccia"))
        language_label.setBuddy(self.language_combo)
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        chosen = normalize_language(language_name if language_name is not None else current_language())
        self.language_combo.setCurrentIndex(self.language_combo.findData(chosen))
        preferences.addRow(language_label, self.language_combo)
        layout.addLayout(preferences)
        language_caption = QLabel(tr("La lingua si applica al prossimo avvio del programma."))
        language_caption.setObjectName("Caption")
        language_caption.setWordWrap(True)
        layout.addWidget(language_caption)
        self.preview = QFrame()
        self.preview.setObjectName("ThemePreview")
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(16, 12, 16, 12)
        preview_layout.setSpacing(8)
        preview_label = QLabel(tr("Anteprima del tema"))
        preview_label.setObjectName("PreviewTitle")
        preview_layout.addWidget(preview_label)
        banner = QLabel(tr("Informazioni sul documento"))
        banner.setObjectName("PreviewNotice")
        banner.setMinimumHeight(banner.fontMetrics().height() + 12)
        preview_layout.addWidget(banner)
        preview_button = QPushButton(tr("Apri documento"))
        preview_button.setObjectName("PreviewButton")
        preview_button.setMinimumHeight(preview_button.fontMetrics().height() + 12)
        preview_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        preview_layout.addWidget(preview_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.preview)
        self.reset_button = QPushButton(tr("Ripristina blu"))
        self.reset_button.clicked.connect(lambda: self.theme_combo.setCurrentIndex(self.theme_combo.findData(DEFAULT_THEME)))
        self.auto_updates = QCheckBox(tr("Proponi l’installazione automatica degli aggiornamenti"))
        self.auto_updates.setEnabled(auto_updates_available)
        self.auto_updates.setChecked(auto_updates)
        layout.addWidget(self.auto_updates)
        updates_caption = QLabel(tr("L’app Windows e il setup devono essere firmati con lo stesso certificato. L’installazione parte solo dopo la conferma.")
            if auto_updates_available else tr("L’installazione automatica è disponibile per l’app installata con il setup Windows."))
        updates_caption.setObjectName("Caption")
        updates_caption.setWordWrap(True)
        layout.addWidget(updates_caption)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        buttons.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("OK")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Annulla"))
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText(tr("Applica"))
        self.apply_button.clicked.connect(self.apply_selection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.theme_combo.currentIndexChanged.connect(self._update_preview)
        self.mode_combo.currentIndexChanged.connect(self._update_preview)
        QApplication.styleHints().colorSchemeChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self):
        colors = theme_colors(self.theme_combo.currentData(), is_dark_mode(self.mode_combo.currentData()))
        self.preview.setStyleSheet(f"""
            QFrame#ThemePreview {{ background: {colors['base']}; border: 1px solid {colors['border']}; border-radius: 12px; }}
            QLabel#PreviewTitle {{ color: {colors['accent_dark']}; font-weight: 600; }}
            QLabel#PreviewNotice {{ background: {colors['notice']}; color: {colors['accent_dark']}; padding: 0 10px; border-radius: 6px; }}
            QPushButton#PreviewButton {{ background: {colors['accent']}; color: {colors['on_accent']}; border: 0; padding: 0 14px; }}
            QPushButton#PreviewButton:hover {{ background: {colors['accent_hover']}; }}
        """)

    def apply_selection(self):
        self.theme_applied.emit(self.theme_combo.currentData(), self.mode_combo.currentData())
        self.language_applied.emit(self.language_combo.currentData())
        if self.auto_updates.isEnabled():
            self.auto_updates_applied.emit(self.auto_updates.isChecked())

    def accept(self):
        self.apply_selection()
        super().accept()
