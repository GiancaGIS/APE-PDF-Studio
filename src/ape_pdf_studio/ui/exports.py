"""Image rendering and cancellable, isolated Word export jobs."""
from __future__ import annotations
from ..core.localization import tr

import json
import math
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QObject, QIODevice, QProcess, QSaveFile, QSize, Qt, Signal
from PySide6.QtGui import QImage, QImageWriter, QPainter
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

from ..core.docx_export import atomic_copy
from ..core.editor import PDFOperationError
from ..core.security import copy_checked
from ..core.worker_process import worker_command


def export_page_image(pdf: QPdfDocument, page: int, destination: Path, dpi: int = 150) -> None:
    destination = Path(destination).resolve()
    formats = {".png": b"png", ".jpg": b"jpeg", ".jpeg": b"jpeg"}
    if destination.suffix.lower() not in formats:
        raise PDFOperationError(tr("Scegli un'immagine PNG oppure JPEG."))
    if pdf.status() != QPdfDocument.Status.Ready or not 0 <= page < pdf.pageCount():
        raise PDFOperationError(tr("La pagina non è pronta per l'esportazione."))
    if dpi not in (72, 150, 300, 600):
        raise PDFOperationError(tr("Risoluzione immagine non supportata."))
    points = pdf.pagePointSize(page)
    size = QSize(math.ceil(points.width() * dpi / 72), math.ceil(points.height() * dpi / 72))
    if size.width() * size.height() > 40_000_000:
        raise PDFOperationError(tr("L'immagine supera 40 megapixel. Scegli una risoluzione inferiore."))
    options = QPdfDocumentRenderOptions()
    options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
    image = pdf.render(page, size, options)
    if image.isNull():
        raise PDFOperationError(tr("Impossibile renderizzare la pagina."))
    # QtPdf leaves unpainted paper transparent; JPEG would turn it black.
    paper = QImage(size, QImage.Format.Format_RGB32)
    paper.fill(Qt.GlobalColor.white)
    painter = QPainter(paper)
    painter.drawImage(0, 0, image)
    painter.end()
    image = paper
    image.setDotsPerMeterX(round(dpi / 0.0254))
    image.setDotsPerMeterY(round(dpi / 0.0254))
    output = QSaveFile(str(destination))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise PDFOperationError(tr("Impossibile scrivere l'immagine: {0}", output.errorString()))
    writer = QImageWriter(output, formats[destination.suffix.lower()])
    writer.setQuality(95)
    if not writer.write(image):
        output.cancelWriting()
        raise PDFOperationError(tr("Impossibile salvare l'immagine: {0}", writer.errorString()))
    if not output.commit():
        raise PDFOperationError(tr("Impossibile salvare l'immagine: {0}", output.errorString()))


class DocxExportJob(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(str, object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, source: Path, destination: Path, parent=None):
        super().__init__(parent)
        self.destination = Path(destination).resolve()
        self._work = tempfile.TemporaryDirectory(prefix="apex_word_job_")
        self.snapshot = Path(self._work.name) / "snapshot.pdf"
        self.result_path = Path(self._work.name) / "result.docx"
        try:
            copy_checked(Path(source), self.snapshot)
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

    def start(self):
        # QProcess uses redirected pipes and does not open a console window.
        command = worker_command("docx", str(self.snapshot), str(self.result_path))
        self.process.start(command[0], command[1:])

    def cancel(self):
        self._cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
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
                self.progress.emit(payload["progress"], payload["total"], payload["label"])
            elif "result" in payload:
                self._result = payload["result"]
            elif "error" in payload:
                self._error = payload["error"]

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = tr("Impossibile avviare la conversione Word: ") + self.process.errorString()
            self._complete(-1, QProcess.ExitStatus.CrashExit)

    def _complete(self, exit_code, exit_status):
        if self._finished:
            return
        self._finished = True
        self._read()
        try:
            if self._cancelled:
                self.cancelled.emit()
            elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit and self._result is not None and self.result_path.is_file():
                try:
                    atomic_copy(self.result_path, self.destination)
                except OSError as exc:
                    self.failed.emit(tr('Impossibile salvare il DOCX: {0}', exc))
                else:
                    self.succeeded.emit(str(self.destination), self._result)
            else:
                self.failed.emit(self._error or tr("Conversione Word interrotta. Il file di destinazione non è stato modificato."))
        finally:
            self._work.cleanup()
            self.finished.emit()
