"""Compression slider and cancellable AppContainer export of a private snapshot."""
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout

from ..core.docx_export import atomic_copy
from ..core.errors import PDFOperationError
from ..core.localization import tr
from ..core.pdf_compression import compression_profile
from ..core.private_work import PrivateDirectory
from ..core.security import copy_checked
from ..core.sandbox import wrap_command
from ..core.user_paths import same_file, user_path
from ..core.worker_process import worker_command
from .worker_guard import WorkerGuard


class CompressionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Esporta PDF compresso'))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        description = QLabel(tr('Riduci la dimensione delle immagini mantenendo testo e grafica vettoriale.'))
        description.setWordWrap(True)
        layout.addWidget(description)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(40)
        self.slider.setAccessibleName(tr('Livello di compressione'))
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(25)
        layout.addWidget(self.slider)
        labels = QHBoxLayout()
        labels.addWidget(QLabel(tr('Senza perdita')))
        labels.addStretch()
        labels.addWidget(QLabel(tr('File più piccolo')))
        layout.addLayout(labels)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        note = QLabel(tr('La copia compressa può invalidare firme digitali e conformità PDF/A. '
                         'Il risparmio dipende dal PDF; l’originale rimane invariato.'))
        note.setWordWrap(True)
        note.setObjectName('MutedLabel')
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Esporta…'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.slider.valueChanged.connect(self._update_detail)
        self._update_detail()

    def _update_detail(self):
        profile = compression_profile(self.slider.value())
        self.detail.setText(tr('Ottimizzazione senza perdita: immagini alla risoluzione originale.') if profile is None else
                            tr('Compressione {0}/100 · risoluzione obiettivo {1} dpi · qualità JPEG {2}/100',
                               self.slider.value(), profile['dpi_target'], profile['quality']))


class CompressedPdfExportJob(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(str, object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, source, destination, level, parent=None, *, zone=None, protected_paths=()):
        super().__init__(parent)
        compression_profile(level)
        source, self.destination = user_path(source), user_path(destination)
        if (self.destination.suffix.lower() != '.pdf' or not self.destination.parent.is_dir() or
                any(same_file(self.destination, path) for path in (source, *protected_paths))):
            raise PDFOperationError(tr('La compressione richiede una nuova copia PDF.'))
        self.level = level
        from ..core.provenance import read_zone
        self._zone = zone if zone is not None else read_zone(source)
        self._work = PrivateDirectory(prefix='ape_compress_')
        self.snapshot = Path(self._work.name) / 'snapshot.pdf'
        self.result_path = Path(self._work.name) / 'result.pdf'
        try:
            copy_checked(source, self.snapshot)
            self.input_bytes = self.snapshot.stat().st_size
        except Exception:
            self._work.cleanup()
            raise
        self._error = ''
        self._result = None
        self._cancelled = False
        self._finished = False
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._complete)
        self.process.errorOccurred.connect(self._process_error)
        self.guard = WorkerGuard(self)

    def start(self):
        self.guard.start()
        try:
            command = wrap_command(worker_command('compress', str(self.snapshot), str(self.result_path), str(self.level)),
                                   self._work.name, timeout=600000)
        except (OSError, PDFOperationError, ValueError) as exc:
            self._error = str(exc)
            self._complete(-1, QProcess.ExitStatus.CrashExit)
            return
        self.process.start(command[0], command[1:])

    def cancel(self):
        self._cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read(self):
        for payload in self.guard.read():
            if 'progress' in payload:
                self.progress.emit(payload['progress'], payload['total'], payload['label'])
            elif 'result' in payload:
                if set(payload['result']) != {'pages'} or self._result is not None:
                    self.guard.fail(tr('Operazione interrotta: risposta del processo non valida o troppo grande.'))
                else:
                    self._result = payload['result']
            elif 'error' in payload:
                self._error = payload['error']

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = tr('Impossibile avviare la compressione PDF: {0}', self.process.errorString())
            self._complete(-1, QProcess.ExitStatus.CrashExit)

    def _complete(self, exit_code, exit_status):
        if self._finished:
            return
        self._finished = True
        self._read()
        self.guard.stop()
        try:
            if self._cancelled:
                self.cancelled.emit()
            elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit and self._result is not None and self.guard.result_ready():
                output_bytes = self.result_path.stat().st_size
                if output_bytes > self.input_bytes:
                    raise PDFOperationError(tr('Il PDF compresso supera la dimensione originale.'))
                atomic_copy(self.result_path, self.destination, artifact_root=Path(self._work.name), zone=self._zone)
                self.succeeded.emit(str(self.destination), dict(self._result, before=self.input_bytes, after=output_bytes))
            else:
                self.failed.emit(self._error or tr('Compressione PDF interrotta. Il file di destinazione non è stato modificato.'))
        except (OSError, PDFOperationError) as exc:
            self.failed.emit(str(exc))
        finally:
            self._work.cleanup()
            self.finished.emit()
