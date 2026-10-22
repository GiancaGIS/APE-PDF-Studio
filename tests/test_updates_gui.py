"""Exercise asynchronous Qt replies, cancellation and browser boundaries."""
import json

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from test_gui import app
from ape_pdf_studio.ui.language import configure_language
from ape_pdf_studio.ui.main_window import MainWindow
from ape_pdf_studio.ui.updates_dialog import UpdatesDialog
from ape_pdf_studio.core.updates import API_VERSION, LATEST_RELEASE_API, MAX_RESPONSE_BYTES, RELEASES_URL, TIMEOUT_MS


class Reply(QNetworkReply):
    def __init__(self, parent):
        super().__init__(parent)
        self.data = bytearray()
        self.aborted = False
        self.open(self.OpenModeFlag.ReadOnly)

    def readData(self, size):
        chunk = bytes(self.data[:size])
        del self.data[:size]
        return chunk

    def bytesAvailable(self):
        return len(self.data) + super().bytesAvailable()

    def abort(self):
        if not self.isFinished():
            self.aborted = True
            self.setError(self.NetworkError.OperationCanceledError, "Canceled")
            self.setFinished(True)
            self.finished.emit()

    def deliver(self, data, status=200, error=QNetworkReply.NetworkError.NoError, finish=True):
        self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
        self.setError(error, "Synthetic reply")
        self.data.extend(data)
        self.readyRead.emit()
        if finish and not self.isFinished():
            self.setFinished(True)
            self.finished.emit()


class Network(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.requests = []
        self.replies = []

    def get(self, request):
        self.requests.append(request)
        reply = Reply(self)
        self.replies.append(reply)
        return reply


@pytest.fixture
def dialog(app, monkeypatch):
    configure_language(app, "it")
    monkeypatch.setattr("ape_pdf_studio.ui.update_checker.QNetworkAccessManager", Network)
    widget = UpdatesDialog()
    yield widget
    widget.reject()
    widget.deleteLater()
    app.processEvents()


def release(tag="v1.10.0"):
    return json.dumps({"tag_name": tag, "draft": False, "prerelease": False, "html_url": "https://evil.invalid/"}).encode()


def test_manual_check_headers_and_no_duplicate_request(dialog):
    assert not dialog.checker.network.requests
    dialog.check()
    dialog.check()
    assert len(dialog.checker.network.requests) == 1
    request = dialog.checker.network.requests[0]
    assert request.url() == QUrl(LATEST_RELEASE_API)
    assert bytes(request.rawHeader("Accept")) == b"application/vnd.github+json"
    assert bytes(request.rawHeader("X-GitHub-Api-Version")) == API_VERSION.encode()
    assert bytes(request.rawHeader("User-Agent")).startswith(b"APE-PDF-Studio/")
    assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    assert request.transferTimeout() == TIMEOUT_MS
    assert not dialog.check_button.isEnabled()


def test_success_accumulates_chunks_and_only_user_opens_browser(dialog, monkeypatch):
    opened = []
    monkeypatch.setattr("ape_pdf_studio.ui.updates_dialog.QDesktopServices.openUrl", lambda url: opened.append(url.toString()) or True)
    dialog.check()
    reply = dialog.checker.network.replies[0]
    data = release()
    reply.deliver(data[:20], finish=False)
    reply.deliver(data[20:])
    assert "1.10.0" in dialog.status.text()
    assert dialog.release_button.isEnabled() and dialog.check_button.isEnabled()
    assert not dialog.checker.timer.isActive() and dialog.checker._reply is None
    assert not opened
    dialog.release_button.click()
    assert opened == [RELEASES_URL + "/tag/v1.10.0"]


@pytest.mark.parametrize("status,data,error,expected", [
    (200, release("v1.1.2"), QNetworkReply.NetworkError.NoError, "aggiornata"),
    (404, b"{}", QNetworkReply.NetworkError.ContentNotFoundError, "Nessuna release"),
    (403, b"{}", QNetworkReply.NetworkError.ContentAccessDenied, "limitato"),
    (429, b"{}", QNetworkReply.NetworkError.UnknownContentError, "limitato"),
    (None, b"", QNetworkReply.NetworkError.SslHandshakeFailedError, "connessione"),
    (200, b"not json", QNetworkReply.NetworkError.NoError, "valida"),
    (302, b"{}", QNetworkReply.NetworkError.NoError, "HTTP 302"),
    (500, b"{}", QNetworkReply.NetworkError.InternalServerError, "HTTP 500"),
])
def test_failures_and_current_version_have_no_download_button(dialog, status, data, error, expected):
    dialog.check()
    dialog.checker.network.replies[-1].deliver(data, status, error)
    assert expected in dialog.status.text()
    assert dialog.check_button.isEnabled() and not dialog.release_button.isEnabled()


@pytest.mark.parametrize("failure", ["timeout", "oversized", "close"])
def test_deadline_size_cap_and_close_abort_request(dialog, failure):
    dialog.check()
    reply = dialog.checker.network.replies[-1]
    if failure == "timeout":
        dialog.checker.timer.timeout.emit()
        assert "Tempo scaduto" in dialog.status.text()
    elif failure == "oversized":
        reply.deliver(b"x" * (MAX_RESPONSE_BYTES + 1))
        assert "troppo grande" in dialog.status.text()
        assert not dialog.checker._body
    else:
        dialog.reject()
    assert reply.aborted and dialog.checker._reply is None
    assert not dialog.checker.timer.isActive()
    assert not dialog.release_button.isEnabled()


def test_retry_clears_previous_release_and_error(dialog):
    dialog.check()
    dialog.checker.network.replies[-1].deliver(release())
    dialog.check()
    assert not dialog.release_button.isEnabled() and dialog.checker.release is None
    dialog.checker.network.replies[-1].deliver(b"invalid")
    assert dialog.checker.release is None
    dialog.check()
    dialog.checker.network.replies[-1].deliver(release())
    assert dialog.release_button.isEnabled()


def test_main_window_exposes_check_without_network_during_construction(app, monkeypatch):
    monkeypatch.setattr("ape_pdf_studio.ui.update_checker.QNetworkAccessManager.get", lambda *args: pytest.fail("Unexpected startup network request"))
    window = MainWindow()
    try:
        assert window.a_updates.text()
        assert any(window.a_updates in action.menu().actions() for action in window.menuBar().actions() if action.menu())
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
