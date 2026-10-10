"""Manual, asynchronous release check with bounded response and total deadline."""
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .. import __version__
from ..core.localization import tr
from ..core.updates import API_VERSION, LATEST_RELEASE_API, MAX_RESPONSE_BYTES, TIMEOUT_MS, UpdateError, parse_release


class UpdatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Aggiornamenti"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Versione installata: {0}", __version__)))
        self.status = QLabel(tr("Verifica la disponibilità di una nuova versione su GitHub."))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        privacy = QLabel(tr("Il controllo contatta api.github.com. Nessun PDF o password viene inviato. L'aggiornamento si installa manualmente."))
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.check_button = QPushButton(tr("Verifica aggiornamenti"))
        self.release_button = QPushButton(tr("Apri la release su GitHub"))
        self.release_button.setEnabled(False)
        buttons.addButton(self.check_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.release_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        self.check_button.clicked.connect(self.check)
        self.release_button.clicked.connect(self.open_release)
        layout.addWidget(buttons)
        self.network = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        self._reply = None
        self._body = bytearray()
        self._failure = ""
        self._release = None

    def check(self):
        if self._reply is not None:
            return
        self._body.clear()
        self._failure = ""
        self._release = None
        self.check_button.setEnabled(False)
        self.release_button.setEnabled(False)
        self.status.setText(tr("Controllo aggiornamenti in corso…"))
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"X-GitHub-Api-Version", API_VERSION.encode("ascii"))
        request.setRawHeader(b"User-Agent", f"APE-PDF-Studio/{__version__}".encode("ascii"))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(TIMEOUT_MS)
        self._reply = self.network.get(request)
        self._reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        self._reply.readyRead.connect(self._read)
        self._reply.finished.connect(self._finished)
        self.timer.start(TIMEOUT_MS)

    def _read(self):
        if self._reply is None or self._failure:
            return
        remaining = MAX_RESPONSE_BYTES - len(self._body)
        chunk = bytes(self._reply.read(remaining + 1))
        if len(chunk) > remaining or self._reply.bytesAvailable() > 0:
            self._failure = tr("Risposta di GitHub troppo grande: controllo interrotto.")
            self._reply.abort()
            return
        self._body.extend(chunk)

    def _timeout(self):
        if self._reply is not None:
            self._failure = tr("Tempo scaduto durante il controllo aggiornamenti. Riprova.")
            self._reply.abort()

    def _finished(self):
        reply = self._reply
        if reply is None:
            return
        self._read()
        if self._reply is not reply:
            return
        self.timer.stop()
        self._reply = None
        self.check_button.setEnabled(True)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            if self._failure:
                raise UpdateError(self._failure)
            if reply.error() != QNetworkReply.NetworkError.NoError and status in (None, 200):
                raise UpdateError(tr("Impossibile contattare GitHub. Controlla la connessione e riprova."))
            self._release = parse_release(status, bytes(self._body))
            if self._release is None:
                message = tr("Nessuna release pubblica stabile trovata. Il repository potrebbe non essere disponibile.")
            elif self._release.update_available:
                message = tr("È disponibile la versione {0}.", self._release.version)
                self.release_button.setEnabled(True)
            else:
                message = tr("La versione installata è aggiornata rispetto alla release {0}.", self._release.version)
            self.status.setText(message)
        except UpdateError as exc:
            self.status.setText(str(exc))
        finally:
            self._body.clear()
            reply.deleteLater()

    def open_release(self):
        if self._release is not None and self._release.update_available:
            if not QDesktopServices.openUrl(QUrl(self._release.url)):
                self.status.setText(tr("Impossibile aprire il browser. Riprova."))

    def done(self, result):
        self.timer.stop()
        if self._reply is not None:
            self._reply.abort()
        super().done(result)
