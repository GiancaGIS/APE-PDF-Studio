"""OCR options and cancellable recognition in an isolated process."""
from __future__ import annotations
from ..core.localization import tr

import hashlib
import json
import logging
from pathlib import Path
import tempfile

from PySide6.QtCore import QObject, QProcess, QStandardPaths, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)

from ..core.editor import parse_page_range
from ..core.errors import PDFOperationError
from ..core.docx_export import atomic_copy
from ..core.ocr import LANGUAGES, MODEL_HASHES
from ..core.security import copy_checked
from ..core.worker_process import worker_command


class OcrDialog(QDialog):
    def __init__(self, page, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Riconosci testo (OCR)"))
        self.setMinimumWidth(470)
        self.page, self.count = page, count
        self.pages = None
        layout = QVBoxLayout(self)
        description = QLabel(tr("Rendi il testo delle scansioni ricercabile e selezionabile.\n"
                             "Le pagine che contengono già testo verranno saltate."))
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.language = QComboBox()
        for code in ("ita+eng", "ita", "eng"):
            self.language.addItem(tr(LANGUAGES[code]), code)
        form.addRow(tr("Lingua"), self.language)
        self.scope = QComboBox()
        self.scope.addItem(tr("Tutte le pagine"), "all")
        self.scope.addItem(tr('Pagina corrente ({0})', page + 1), "current")
        self.scope.addItem(tr("Intervallo di pagine"), "range")
        form.addRow(tr("Pagine"), self.scope)
        self.range = QLineEdit()
        self.range.setPlaceholderText(tr("Esempio: 1,3-5"))
        self.range.setEnabled(False)
        self.scope.currentIndexChanged.connect(lambda _: self.range.setEnabled(self.scope.currentData() == "range"))
        form.addRow(tr("Intervallo"), self.range)
        self.dpi = QComboBox()
        self.dpi.addItem(tr("300 dpi · qualità"), 300)
        self.dpi.addItem(tr("150 dpi · veloce"), 150)
        form.addRow(tr("Risoluzione"), self.dpi)
        layout.addLayout(form)
        self.download = QCheckBox(tr("Scarica i dati delle lingue mancanti al primo utilizzo"))
        self.download.setChecked(True)
        layout.addWidget(self.download)
        detail = QLabel(tr("Download iniziale: fino a 7 MB. Dopo il download l'OCR funziona offline.\n"
                        "Il documento viene elaborato sul computer.\n"
                        "Controlla il testo riconosciuto: la qualità dipende dalla scansione.\n"
                        "La modifica può essere annullata con Ctrl+Z. Usa Salva per conservarla."))
        detail.setWordWrap(True)
        layout.addWidget(detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Avvia OCR"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        scope = self.scope.currentData()
        try:
            self.pages = ([self.page] if scope == "current" else
                          parse_page_range(self.range.text(), self.count) if scope == "range" else None)
        except PDFOperationError as exc:
            QMessageBox.warning(self, tr("Pagine OCR"), str(exc))
            return
        super().accept()


def default_ocr_cache():
    """Use the current organization folder and migrate verified legacy models."""
    current = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation)) / "ocr/tessdata"
    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
    previous = (base / "APE/APE PDF Studio/ocr/tessdata",
                base / "APEX/APEX PDF Studio/ocr/tessdata",
                base / "APEX PDF Studio/ocr/tessdata")
    for code, expected in MODEL_HASHES.items():
        destination = current / f"{code}.traineddata"
        try:
            if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
                continue
        except OSError:
            pass
        for folder in previous:
            source = folder / destination.name
            try:
                if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    continue
                current.mkdir(parents=True, exist_ok=True)
                atomic_copy(source, destination)
                break
            except OSError:
                logging.getLogger(__name__).warning("Cannot migrate OCR model from %s", source, exc_info=True)
    return current


class OcrJob(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(str, object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, source, *, language="ita+eng", pages=None, dpi=300,
                 download=True, cache_dir=None, parent=None):
        super().__init__(parent)
        self.revision = Path(source)
        self._work = tempfile.TemporaryDirectory(prefix="apex_ocr_job_")
        self.snapshot = Path(self._work.name) / "snapshot.pdf"
        self.result_path = Path(self._work.name) / "result.pdf"
        self.request_path = Path(self._work.name) / "request.json"
        try:
            copy_checked(self.revision, self.snapshot)
            cache = Path(cache_dir) if cache_dir is not None else default_ocr_cache()
            self.request_path.write_text(json.dumps({
                "source": str(self.snapshot), "destination": str(self.result_path),
                "language": language, "pages": pages, "dpi": dpi,
                "download": download, "cache_dir": str(cache),
            }), encoding="utf-8")
        except Exception:
            self._work.cleanup()
            raise
        self._buffer = b""
        self._result = None
        self._error = ""
        self._cancelled = False
        self._finished = False
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._complete)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180_000)
        self.timer.timeout.connect(self._timeout)

    def start(self):
        if self._cancelled:
            self._complete(-1, QProcess.ExitStatus.CrashExit)
            return
        self.timer.start()
        command = worker_command("ocr", str(self.request_path))
        self.process.start(command[0], command[1:])

    def cancel(self):
        self._cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _timeout(self):
        self._error = tr("L'OCR ha superato il tempo limite per una pagina. Prova a 150 dpi.")
        self.process.kill()

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                payload = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if "progress" in payload:
                self.timer.start()
                self.progress.emit(payload["progress"], payload["total"], payload["label"])
            elif "result" in payload:
                self._result = payload["result"]
            elif "error" in payload:
                self._error = payload["error"]

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = tr("Impossibile avviare l'OCR: ") + self.process.errorString()
            self._complete(-1, QProcess.ExitStatus.CrashExit)

    def _complete(self, exit_code, exit_status):
        if self._finished:
            return
        self._finished = True
        self._read()
        self.timer.stop()
        try:
            if self._cancelled:
                self.cancelled.emit()
            elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit and isinstance(self._result, dict) and self.result_path.is_file():
                # The editor imports this result synchronously before temporary cleanup.
                self.succeeded.emit(str(self.result_path), self._result)
            else:
                self.failed.emit(self._error or tr("OCR interrotto. Il documento non è stato modificato."))
        finally:
            self._work.cleanup()
            self.finished.emit()
