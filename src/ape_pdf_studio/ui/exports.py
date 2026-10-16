"""Image rendering and cancellable, isolated Word export jobs."""
from __future__ import annotations
from ..core.localization import tr

import json
import math
from pathlib import Path
import sys
import tempfile
from ..core.private_work import PrivateDirectory

from PySide6.QtCore import QObject, QIODevice, QProcess, QSaveFile, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageWriter, QPainter
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

from ..core.docx_export import atomic_copy
from ..core.editor import PDFOperationError
from ..core.security import copy_checked
from ..core.worker_process import worker_command
from .worker_guard import WorkerGuard
from ..core.sandbox import wrap_command


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
    with PrivateDirectory() as folder:
        staged=Path(folder)/('image'+destination.suffix)
        writer=QImageWriter(str(staged),formats[destination.suffix.lower()])
        writer.setQuality(95)
        if not writer.write(image):
            raise PDFOperationError(tr("Impossibile salvare l'immagine: {0}",writer.errorString()))
        origin=getattr(pdf,'source',None)
        atomic_copy(staged,destination,origin,zone=getattr(getattr(pdf,'editor',None),'_zone',None))



class DocxExportJob(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(str, object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, source: Path, destination: Path, parent=None, *, zone=None):
        super().__init__(parent)
        self.destination = Path(destination).resolve()
        from ..core.provenance import read_zone
        self._zone=zone if zone is not None else read_zone(Path(source))
        self._work = PrivateDirectory(prefix="apex_word_job_")
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
        self.guard = WorkerGuard(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180_000)
        self.timer.timeout.connect(lambda: self.guard.fail(tr("La conversione Word ha superato il tempo limite per una pagina.")))

    def start(self):
        # QProcess uses redirected pipes and does not open a console window.
        self.timer.start()
        self.guard.start()
        command = wrap_command(worker_command("docx", str(self.snapshot), str(self.result_path)), self._work.name, timeout=600000)
        self.process.start(command[0], command[1:])

    def cancel(self):
        self._cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read(self):
        for payload in self.guard.read():
            if "progress" in payload:
                self.timer.start()
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
        self.timer.stop()
        self.guard.stop()
        try:
            if self._cancelled:
                self.cancelled.emit()
            elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit and self._result is not None and self.guard.result_ready():
                try:
                    atomic_copy(self.result_path, self.destination, artifact_root=Path(self._work.name),zone=self._zone)
                except OSError as exc:
                    self.failed.emit(tr('Impossibile salvare il DOCX: {0}', exc))
                else:
                    self.succeeded.emit(str(self.destination), self._result)
            else:
                self.failed.emit(self._error or tr("Conversione Word interrotta. Il file di destinazione non è stato modificato."))
        finally:
            self._work.cleanup()
            self.finished.emit()
