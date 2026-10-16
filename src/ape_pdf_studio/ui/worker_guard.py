"""Common conversion deadlines, response validation and output budgets."""
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer

from ..core.worker_protocol import Messages, ProtocolError, MAX_RESULT_BYTES
from ..core.localization import tr


class WorkerGuard:
    def __init__(self, job):
        self.job = job
        self.messages = Messages()
        job.process.setStandardErrorFile(QProcess.nullDevice())
        self.deadline = QTimer(job)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.fail(tr("Operazione interrotta: superato il limite di tempo complessivo.")))
        self.watch = QTimer(job)
        self.watch.setInterval(1000)
        self.watch.timeout.connect(self.check_output)

    def start(self):
        self.deadline.start(600_000)
        self.watch.start()

    def stop(self):
        self.deadline.stop()
        self.watch.stop()

    def fail(self, message):
        self.job._error = message
        self.job._result = None
        self.job.process.kill()

    def check_output(self):
        try:
            from ..core.private_work import tree_bytes
            if tree_bytes(Path(self.job._work.name)) > 512*1024*1024:
                self.fail(tr("Operazione interrotta: superato il limite dei file temporanei."))
        except OSError:
            self.fail(tr("Operazione interrotta: impossibile controllare i file temporanei."))

    def read(self):
        try:
            for payload in self.messages.feed(bytes(self.job.process.readAllStandardOutput())):
                if not self.job._error:
                    # QMessageBox/QProgressDialog may auto-detect rich text.
                    # Never let a worker turn diagnostics into image/HTML loads.
                    payload=dict(payload)
                    for key in ('error','label'):
                        if key in payload:payload[key]=payload[key].replace('<','‹').replace('>','›')
                    yield payload
        except ProtocolError:
            self.fail(tr("Operazione interrotta: risposta del processo non valida o troppo grande."))

    def result_ready(self):
        path = self.job.result_path
        return not self.job._error and not path.is_symlink() and not (getattr(path.lstat(),"st_file_attributes",0)&0x400) and path.is_file() and 0 < path.stat().st_size <= MAX_RESULT_BYTES
