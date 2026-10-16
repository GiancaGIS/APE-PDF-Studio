"""Split the working revision without parsing PDF data in the UI process."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QVBoxLayout,
)

from ..core.errors import PDFOperationError
from ..core.localization import tr
from ..core.pdf_split import part_name, split_groups
from .content_dialogs import plain_label


class SplitJob(QThread):
    progress = Signal(int)

    def __init__(self, editor, groups, directory, parent):
        super().__init__(parent)
        self.editor, self.groups, self.directory = editor, groups, directory
        self.cancelled = Event()
        self.outputs = []
        self.error = ""

    def run(self):
        try:
            self.outputs = self.editor.split(
                self.groups, self.directory, progress=self.progress.emit,
                cancelled=self.cancelled.is_set)
        except Exception as exc:
            self.error = str(exc)


class SplitDialog(QDialog):
    def __init__(self, editor, page_count, parent=None):
        super().__init__(parent)
        self.editor, self.page_count = editor, page_count
        self.outputs = []
        self._job = None
        self.setWindowTitle(tr("Dividi PDF"))
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(plain_label(tr("Dividi PDF"), "DialogTitle"))
        layout.addWidget(plain_label(tr(
            "Crea più PDF dalla versione corrente del documento. I file saranno salvati in una nuova sottocartella PDF_split, senza sovrascrivere file esistenti."), "Caption"))
        layout.addWidget(plain_label(tr("Pagine totali: {0}", page_count)))
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems([tr("Un file per pagina"), tr("Ogni N pagine"), tr("Intervalli personalizzati")])
        form.addRow(tr("Modalità"), self.mode)
        self.pages_per_file = QSpinBox()
        self.pages_per_file.setRange(1, max(1, page_count - 1))
        self.pages_per_file.setValue(min(2, max(1, page_count - 1)))
        form.addRow(tr("Pagine per file"), self.pages_per_file)
        self.ranges = QLineEdit()
        self.ranges.setMaxLength(20000)
        self.ranges.setPlaceholderText(tr("Esempio: 1-3; 4-6; 7-10"))
        form.addRow(tr("Intervalli"), self.ranges)
        layout.addLayout(form)
        self.hint = plain_label(tr(
            "Separa i file con ; e le pagine dello stesso file con virgole o trattini. Includi tutte le pagine, senza ripetizioni."), "Caption")
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        self.directory = QLineEdit(str(editor.output_path.parent))
        self.directory.setAccessibleName(tr("Cartella di destinazione"))
        self.browse = QPushButton(tr("Sfoglia…"))
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.directory, 1)
        row.addWidget(self.browse)
        layout.addWidget(plain_label(tr("Cartella di destinazione")))
        layout.addLayout(row)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName(tr("Anteprima dei file"))
        layout.addWidget(self.preview, 1)
        self.feedback = plain_label("", "EditFeedback")
        layout.addWidget(self.feedback)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText(tr("Annulla"))
        self.buttons.rejected.connect(self.reject)
        self.start_button = self.buttons.addButton(tr("Dividi PDF"), QDialogButtonBox.ButtonRole.ActionRole)
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        layout.addWidget(self.buttons)
        self.mode.currentIndexChanged.connect(self._update_preview)
        self.pages_per_file.valueChanged.connect(self._update_preview)
        self.ranges.textChanged.connect(self._update_preview)
        self.directory.textChanged.connect(self._update_preview)
        self._update_preview()

    def _groups(self):
        mode = self.mode.currentIndex()
        return split_groups(self.page_count, size=1 if mode == 0 else self.pages_per_file.value(),
                            ranges=self.ranges.text() if mode == 2 else None)

    def _update_preview(self, *_):
        mode = self.mode.currentIndex()
        self.pages_per_file.setEnabled(mode == 1)
        self.ranges.setEnabled(mode == 2)
        self.hint.setVisible(mode == 2)
        try:
            groups = self._groups()
            if not self.directory.text().strip() or not Path(self.directory.text()).expanduser().is_dir():
                raise PDFOperationError(tr("Scegli una cartella di destinazione esistente."))
            lines = [tr("{0} file PDF da creare", len(groups))]
            for index, pages in enumerate(groups[:20], 1):
                # Keep arbitrary ordered selections visible without enormous previews.
                numbers = ", ".join(str(page + 1) for page in pages[:12])
                if len(pages) > 12:
                    numbers += ", …"
                lines.append(tr("{0} · pagine {1}", part_name(index), numbers))
            if len(groups) > 20:
                lines.append(tr("… e altri {0} file", len(groups) - 20))
            self.preview.setPlainText("\n".join(lines))
            self.feedback.clear()
            self.start_button.setEnabled(True)
        except (PDFOperationError, OSError, ValueError) as exc:
            self.preview.clear()
            self.feedback.setText(str(exc))
            self.start_button.setEnabled(False)

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, tr("Cartella di destinazione"), self.directory.text())
        if directory:
            self.directory.setText(directory)

    def _start(self):
        if self._job and self._job.isRunning():
            return
        try:
            groups = self._groups()
        except PDFOperationError as exc:
            self.feedback.setText(str(exc))
            return
        for control in (self.mode, self.pages_per_file, self.ranges, self.directory, self.browse, self.start_button):
            control.setEnabled(False)
        self.progress.setRange(0, len(groups))
        self.progress.setValue(0)
        self.progress.show()
        self.feedback.setText(tr("Divisione in corso…"))
        self._job = SplitJob(self.editor, groups, self.directory.text(), self)
        self._job.progress.connect(self.progress.setValue)
        self._job.finished.connect(self._finished)
        self._job.start()

    def _finished(self):
        job = self._job
        self.outputs = job.outputs
        self._job = None
        job.deleteLater()
        if self.outputs:
            self.accept()
        elif job.error:
            for control in (self.mode, self.directory, self.browse, self.cancel_button):
                control.setEnabled(True)
            self.cancel_button.setText(tr("Annulla"))
            self.progress.hide()
            self._update_preview()
            self.feedback.setText(job.error)
        else:
            super().reject()

    def reject(self):
        if self._job:
            self._job.cancelled.set()
            self.cancel_button.setEnabled(False)
            self.feedback.setText(tr("Annullamento in corso…"))
            return
        super().reject()

    def closeEvent(self, event):
        if self._job:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
