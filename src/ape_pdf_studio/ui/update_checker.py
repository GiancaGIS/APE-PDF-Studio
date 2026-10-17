"""Shared asynchronous release check, independent of notification/dialog widgets."""
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import __version__
from ..core.localization import tr
from ..core.updates import API_VERSION, LATEST_RELEASE_API, MAX_RESPONSE_BYTES, TIMEOUT_MS, UpdateError, parse_release


class UpdateChecker(QObject):
    started = Signal()
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        self._reply = None
        self._body = bytearray()
        self._failure = ""
        self.release = None
        self.error = ""
        self.checked = False

    @property
    def busy(self):
        return self._reply is not None

    def check(self):
        if self.busy:
            return
        self._body.clear()
        self._failure = ""
        self.release = None
        self.error = ""
        self.checked = False
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
        self.started.emit()

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
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if self._failure:
                raise UpdateError(self._failure)
            if reply.error() != QNetworkReply.NetworkError.NoError and status in (None, 200):
                raise UpdateError(tr("Impossibile contattare GitHub. Controlla la connessione e riprova."))
            self.release = parse_release(status, bytes(self._body))
        except UpdateError as exc:
            self.error = str(exc)
        finally:
            self._body.clear()
            self.checked = True
            reply.deleteLater()
        if self.error:
            self.failed.emit(self.error)
        else:
            self.completed.emit(self.release)

    def cancel(self):
        self.timer.stop()
        reply, self._reply = self._reply, None
        self._body.clear()
        if reply is not None:
            reply.abort()
            reply.deleteLater()
