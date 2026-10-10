"""Sticky-note editor and replacement of an existing PDF text span."""
from ..core.localization import tr
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout

from ..core.errors import PDFOperationError
from ..core.text_edit import font_details


def plain_label(text, name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


class NoteDialog(QDialog):
    def __init__(self, text="", save=None, delete=None, parent=None):
        super().__init__(parent)
        self.setObjectName("NoteDialog")
        self.setWindowTitle(tr("Nota PDF"))
        self.setMinimumWidth(340)
        self.resize(380, 260)
        self.save_callback, self.delete_callback = save, delete
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(plain_label(tr("Nota"), "SectionTitle"))
        self.text = QPlainTextEdit()
        self.text.setPlainText(text)
        self.text.setPlaceholderText(tr("Scrivi la nota in questo punto…"))
        self.text.setReadOnly(save is None)
        layout.addWidget(self.text)
        self.feedback = plain_label("", "EditFeedback")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel if save else QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Cancel if save else QDialogButtonBox.StandardButton.Close).setText(tr("Annulla") if save else tr("Chiudi"))
        buttons.rejected.connect(self.reject)
        if save:
            self.save_button = buttons.addButton(tr("Salva nota"), QDialogButtonBox.ButtonRole.ActionRole)
            self.save_button.clicked.connect(self._save)
        if delete:
            self.delete_button = buttons.addButton(tr("Elimina"), QDialogButtonBox.ButtonRole.DestructiveRole)
            self.delete_button.clicked.connect(lambda: self._perform(self.delete_callback))
        layout.addWidget(buttons)

    def _save(self):
        value = self.text.toPlainText().strip()
        if not value:
            self.feedback.setText(tr("Scrivi un testo per la nota."))
            self.feedback.show()
            return
        self._perform(lambda: self.save_callback(value))

    def _perform(self, operation):
        try:
            operation()
        except PDFOperationError as exc:
            self.feedback.setText(str(exc))
            self.feedback.show()
        else:
            self.accept()


class TextEditDialog(QDialog):
    def __init__(self, source, target, save, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Modifica testo PDF"))
        self.setMinimumWidth(500)
        self.source, self.target, self.save_callback = source, target, save
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(plain_label(tr("Modifica testo"), "DialogTitle"))
        layout.addWidget(plain_label(tr("Sostituisci questa porzione di testo nello spazio originale. Posizione e colore vengono conservati; puoi ridurre la dimensione per far entrare un testo più lungo."), "Caption"))
        self.text = QLineEdit(target.text)
        self.text.setMaxLength(4000)
        self.text.setMinimumWidth(450)
        self.size = QDoubleSpinBox()
        self.size.setRange(.5, 300)
        self.size.setDecimals(2)
        self.size.setSingleStep(.5)
        self.size.setSuffix(" pt")
        self.size.setValue(target.size)
        form = QFormLayout()
        form.addRow(tr("Testo"), self.text)
        form.addRow(tr("Dimensione"), self.size)
        layout.addLayout(form)
        self.font_label = plain_label("", "Caption")
        layout.addWidget(self.font_label)
        self.feedback = plain_label("", "EditFeedback")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._font_info)
        self.text.textChanged.connect(lambda: self.timer.start(200))
        self._font_info()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Annulla"))
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.addButton(tr("Applica modifica"), QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save)
        layout.addWidget(buttons)
        self.text.selectAll()

    def _font_info(self):
        try:
            name, fallback = font_details(self.source, self.target, self.text.text())
            name += tr(" · font compatibile") if fallback else tr(" · font originale")
            self.font_label.setText(tr("Font di scrittura: ") + name +
                                   (tr(". Il font originale non è riutilizzabile: l'aspetto può cambiare.") if fallback else "."))
        except PDFOperationError as exc:
            self.font_label.setText(str(exc))

    def _save(self):
        self.timer.stop()
        self._font_info()
        try:
            self.save_callback(self.text.text(), self.size.value())
        except PDFOperationError as exc:
            self.feedback.setText(str(exc))
            self.feedback.show()
        else:
            self.accept()
