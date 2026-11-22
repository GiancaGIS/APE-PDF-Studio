"""Stream a release installer and check its digest and Windows publisher."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ..core.localization import tr
from ..core.private_work import MIN_FREE_BYTES, PrivateDirectory
from ..core.updates import TIMEOUT_MS
from ..core.windows_update import INSTALL_COMMAND, SIGNATURE_COMMAND, system_powershell

CHUNK_SIZE = 256 * 1024
DOWNLOAD_DEADLINE_MS = 20 * 60 * 1000
SIGNATURE_DEADLINE_MS = 30_000
ASSET_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def allowed_asset_redirect(url: QUrl) -> bool:
    return (url.isValid() and url.scheme() == "https" and url.host() in ASSET_HOSTS
            and url.port() in (-1, 443) and not url.userInfo() and not url.hasFragment())


class UpdateInstaller(QObject):
    progress = Signal(int)
    status = Signal(str)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, release, scope, parent=None):
        super().__init__(parent)
        self.release = release
        self.asset = release.installer
        self.scope = scope
        self.workspace = None
        self.path = None
        self._stream = None
        self._reply = None
        self._received = 0
        self._hash = hashlib.sha256()
        self._signer = None
        self._verifying = False
        self._ready = False
        self.network = QNetworkAccessManager(self)
        self.verifier = QProcess(self)
        self.verifier.finished.connect(self._signature_finished)
        self.verifier.errorOccurred.connect(self._signature_error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)

    def start(self):
        self.status.emit(tr("Verifica della firma dell’applicazione…"))
        self._check_signature(Path(sys.executable))

    @property
    def prepared(self):
        return self._ready

    def _check_signature(self, path, version=""):
        try:
            self._configure_process(self.verifier, SIGNATURE_COMMAND, path, version)
        except OSError:
            self._fail(tr("Impossibile verificare la firma dell’aggiornamento."))
            return
        self._verifying = True
        self.timer.start(SIGNATURE_DEADLINE_MS)
        self.verifier.start()

    def _configure_process(self, process, command, path, version, extra=None):
        powershell = system_powershell()
        env = QProcessEnvironment.systemEnvironment()
        env.insert("APE_UPDATE_VERIFY_PATH", str(path))
        env.insert("APE_UPDATE_VERIFY_VERSION", version)
        for key, value in (extra or {}).items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(powershell.parent))
        process.setProgram(str(powershell))
        process.setArguments(["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", command])

    def _signature_error(self, _):
        if self._verifying:
            self._fail(tr("Impossibile verificare la firma dell’aggiornamento."))

    def _signature_finished(self, exit_code, exit_status):
        signer = bytes(self.verifier.readAllStandardOutput()).strip()
        self.verifier.readAllStandardError()
        if not self._verifying:
            return
        self._verifying = False
        self.timer.stop()
        if exit_code or exit_status != QProcess.ExitStatus.NormalExit or not re.fullmatch(b"[0-9A-F]{64}", signer):
            self._fail(tr("L’aggiornamento automatico richiede firme digitali valide per l’app e per l’installer."))
        elif self._signer is None:
            self._signer = signer
            self._download()
        elif signer != self._signer:
            self._fail(tr("L’installer è firmato con un certificato diverso da quello dell’app installata."))
        else:
            self._ready = True
            self.ready.emit()

    def _download(self):
        try:
            # After a successful handoff Windows may still be reading the file.
            # The existing private workspace cleanup collects it on a later start.
            self.workspace = PrivateDirectory(delete=False)
            if shutil.disk_usage(self.workspace.name).free < self.asset.size + MIN_FREE_BYTES:
                raise OSError("Insufficient space for installer")
            self.path = Path(self.workspace.name) / "APEPDFStudio-Setup.exe"
            self._stream = self.path.open("xb")
        except OSError:
            self._fail(tr("Impossibile preparare il download. Controlla lo spazio disponibile."))
            return
        request = QNetworkRequest(QUrl(self.asset.url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.UserVerifiedRedirectPolicy)
        request.setTransferTimeout(TIMEOUT_MS)
        request.setMaximumRedirectsAllowed(3)
        self._reply = self.network.get(request)
        self._reply.setReadBufferSize(CHUNK_SIZE)
        self._reply.redirected.connect(self._redirect)
        self._reply.readyRead.connect(self._read)
        self._reply.finished.connect(self._download_finished)
        self.status.emit(tr("Download dell’aggiornamento {0}…", self.release.version))
        self.progress.emit(0)
        self.timer.start(DOWNLOAD_DEADLINE_MS)

    def _redirect(self, url):
        if self._reply is None:
            return
        if allowed_asset_redirect(url):
            self._reply.redirectAllowed.emit()
        else:
            self._fail(tr("Il download dell’installer è stato reindirizzato a un indirizzo non valido."))

    def _read(self):
        reply = self._reply
        if reply is None or reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
            return
        try:
            while reply.bytesAvailable():
                chunk = bytes(reply.read(min(CHUNK_SIZE, self.asset.size - self._received + 1)))
                if not chunk:
                    break
                self._received += len(chunk)
                if self._received > self.asset.size:
                    self._fail(tr("Dimensione o hash dell’installer non corrispondenti alla release di GitHub."))
                    return
                self._stream.write(chunk)
                self._hash.update(chunk)
        except OSError:
            self._fail(tr("Impossibile salvare l’installer scaricato."))
            return
        self.progress.emit(self._received)

    def _download_finished(self):
        reply = self._reply
        if reply is None:
            return
        self._read()
        if self._reply is not reply:
            return
        self._reply = None
        self.timer.stop()
        reply.deleteLater()
        if (reply.error() != QNetworkReply.NetworkError.NoError or
                reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200):
            self._fail(tr("Download dell’installer non riuscito. Riprova."))
            return
        if self._received != self.asset.size or self._hash.hexdigest() != self.asset.sha256:
            self._fail(tr("Dimensione o hash dell’installer non corrispondenti alla release di GitHub."))
            return
        try:
            self._stream.close()
        except OSError:
            self._fail(tr("Impossibile salvare l’installer scaricato."))
            return
        self._stream = None
        self.status.emit(tr("Verifica della firma dell’installer…"))
        self._check_signature(self.path, self.release.version)

    def _timeout(self):
        self._fail(tr("Tempo scaduto durante la preparazione dell’aggiornamento. Riprova."))

    def _fail(self, message):
        self.cancel()
        self.failed.emit(message)

    def cancel(self):
        self.timer.stop()
        self._ready = self._verifying = False
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if self.verifier.state() != QProcess.ProcessState.NotRunning:
            self.verifier.kill()
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            finally:
                self._stream = None
        if self.workspace is not None:
            self.workspace.cleanup_in_background()
            self.workspace = None

    def launch(self):
        if not self._ready:
            return False
        self._ready = False
        launcher = QProcess()
        try:
            self._configure_process(launcher, INSTALL_COMMAND, self.path, self.release.version, {
                "APE_UPDATE_OWNER": str(os.getpid()),
                "APE_UPDATE_SCOPE": self.scope,
                "APE_UPDATE_SHA256": self.asset.sha256,
                "APE_UPDATE_SIGNER": self._signer.decode("ascii"),
                "APE_UPDATE_TITLE": tr("Aggiornamenti"),
                "APE_UPDATE_FAILURE": tr("Impossibile completare l’aggiornamento. Riapri l’app per riprovare."),
            })
            started = launcher.startDetached()
        except OSError:
            started = False
        if started:
            self.workspace = None
        return started
