"""Sticky-note editor and replacement of an existing PDF text span."""
from ..core.localization import tr
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QThreadPool, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout

from ..core.errors import PDFOperationError
def font_details(source,target,text):
    from ..core.broker_editor import BrokerEditor
    editor=BrokerEditor(source)
    try:return editor.call('font_details',target,text)
    finally:editor.close()


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


class _TextSignals(QObject):
    completed = Signal(str, object, object, str)


class _TextTask(QRunnable):
    def __init__(self, kind, key, operation, signals):
        super().__init__()
        self.kind, self.key, self.operation, self.signals = kind, key, operation, signals

    def run(self):
        try:
            value, error = self.operation(), ""
        except Exception as exc:
            value, error = None, str(exc)
        try:
            self.signals.completed.emit(self.kind, self.key, value, error)
        except RuntimeError:
            pass  # The dialog may have been closed and deleted during the RPC.


class TextEditDialog(QDialog):
    target_ready = Signal(object)

    def __init__(self, source, target, save, parent=None, *, font_resolver=None, target_resolver=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Modifica testo PDF"))
        self.setMinimumWidth(500)
        self.source, self.target, self.save_callback = source, target, save
        self.font_resolver=font_resolver or (lambda target,text:font_details(self.source,target,text))
        self._closed = False
        self._font_running = False
        self._font_cache = OrderedDict()
        self._signals = _TextSignals(self)
        self._signals.completed.connect(self._completed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(plain_label(tr("Modifica testo"), "DialogTitle"))
        layout.addWidget(plain_label(tr("Sostituisci questa porzione di testo nello spazio originale. Posizione e colore vengono conservati; puoi ridurre la dimensione per far entrare un testo più lungo."), "Caption"))
        self.text = QLineEdit(target.text if target else "")
        self.text.setMaxLength(4000)
        self.text.setMinimumWidth(450)
        self.size = QDoubleSpinBox()
        self.size.setRange(.5, 300)
        self.size.setDecimals(2)
        self.size.setSingleStep(.5)
        self.size.setSuffix(" pt")
        self.size.setValue(target.size if target else 12)
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Annulla"))
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.addButton(tr("Applica modifica"), QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save)
        layout.addWidget(buttons)
        self.text.selectAll()
        if target is None:
            self.text.setEnabled(False)
            self.size.setEnabled(False)
            self.save_button.setEnabled(False)
            self.font_label.setText(tr("Lettura del testo in corso…"))
            self._start_task("target", None, target_resolver)
        else:
            self._font_info()

    def _start_task(self, kind, key, operation):
        # Prioritize interactive reads over queued background page renders.
        # The broker still serializes all PDF work inside the sandbox.
        QThreadPool.globalInstance().start(_TextTask(kind, key, operation, self._signals), 1)

    @staticmethod
    def _resolve_font(resolver, target, text):
        name, fallback = resolver(target, text)
        if not isinstance(name, str) or len(name) > 4096 or type(fallback) is not bool:
            raise PDFOperationError('Risposta font non valida.')
        return name, fallback

    def _show_font(self, result):
        name, fallback = result
        name += tr(" · font compatibile") if fallback else tr(" · font originale")
        self.font_label.setText(tr("Font di scrittura: ") + name +
                               (tr(". Il font originale non è riutilizzabile: l'aspetto può cambiare.") if fallback else "."))

    def _completed(self, kind, key, value, error):
        if self._closed:
            return
        if kind == "target":
            if error:
                self.font_label.clear()
                self.feedback.setText(error)
                self.feedback.show()
                return
            self.target = value
            with QSignalBlocker(self.text):
                self.text.setText(value.text)
            self.size.setValue(value.size)
            self.text.setEnabled(True)
            self.size.setEnabled(True)
            self.save_button.setEnabled(True)
            self.text.setFocus()
            self.text.selectAll()
            self.target_ready.emit(value)
            self._font_info()
            return
        self._font_running = False
        if not error:
            self._font_cache[key] = value
            while len(self._font_cache) > 16:
                self._font_cache.popitem(last=False)
        if key == self.text.text():
            if error:
                self.font_label.setText(error)
            else:
                self._show_font(value)
        elif not self.timer.isActive():
            self._font_info()

    def _font_info(self):
        if self._closed or self.target is None:
            return
        text = self.text.text()
        if text in self._font_cache:
            self._show_font(self._font_cache[text])
            return
        if self._font_running:
            return
        self._font_running = True
        self.font_label.setText(tr("Analisi del font in corso…"))
        resolver, target = self.font_resolver, self.target
        self._start_task("font", text, lambda: TextEditDialog._resolve_font(resolver, target, text))

    def _save(self):
        self.timer.stop()
        if self._closed or self.target is None:
            return
        try:
            self.save_callback(self.text.text(), self.size.value())
        except PDFOperationError as exc:
            self.feedback.setText(str(exc))
            self.feedback.show()
        else:
            self.accept()

    def done(self, result):
        self._closed = True
        self.timer.stop()
        super().done(result)
