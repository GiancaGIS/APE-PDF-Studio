"""Startup notices repeat per launch, stay quiet on errors, and never open URLs automatically."""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from test_gui import app, wait_for
from test_updates_gui import Network, release
from ape_pdf_studio.core.updates import RELEASES_URL
from ape_pdf_studio.ui.main_window import MainWindow
from ape_pdf_studio.ui.updates_dialog import UpdatesDialog


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr("ape_pdf_studio.ui.update_checker.QNetworkAccessManager", Network)
    widget = MainWindow()
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def begin(window):
    window.schedule_update_check()
    window._startup_timer.start(0)
    wait_for(lambda: bool(window.update_checker.network.requests))
    return window.update_checker.network.replies[-1]


def test_startup_is_deferred_once_and_toast_requires_browser_click(window, monkeypatch, app):
    opened = []
    monkeypatch.setattr("ape_pdf_studio.ui.toast.QDesktopServices.openUrl", lambda url: opened.append(url.toString()) or True)
    focus = QLineEdit(window)
    focus.show()
    focus.setFocus()
    window.schedule_update_check()
    window.schedule_update_check()
    assert not window.update_checker.network.requests
    assert window._startup_timer.interval() == 800
    wait_for(lambda: bool(window.update_checker.network.requests))
    window.update_checker.network.replies[-1].deliver(release())
    toast = window.update_toast
    assert toast.isVisible() and "1.10.0" in toast.filename.text()
    assert toast.title.text() == "Aggiornamento disponibile"
    assert toast.filename.textFormat() == Qt.TextFormat.PlainText
    assert app.focusWidget() is focus
    assert not opened and not window.toast.isVisible()
    window.schedule_update_check()
    assert len(window.update_checker.network.requests) == 1
    toast.release_button.click()
    assert opened == [RELEASES_URL + "/tag/v1.10.0"]
    assert not toast.isVisible()


@pytest.mark.parametrize("status,body,error", [
    (200, release("v1.1.6"), QNetworkReply.NetworkError.NoError),
    (200, release("v1.0.0"), QNetworkReply.NetworkError.NoError),
    (404, b"{}", QNetworkReply.NetworkError.ContentNotFoundError),
    (403, b"{}", QNetworkReply.NetworkError.ContentAccessDenied),
    (429, b"{}", QNetworkReply.NetworkError.UnknownContentError),
    (200, b"not json", QNetworkReply.NetworkError.NoError),
    (None, b"", QNetworkReply.NetworkError.SslHandshakeFailedError),
])
def test_no_toast_for_current_older_missing_or_invalid_release(window, monkeypatch, status, body, error):
    monkeypatch.setattr("ape_pdf_studio.ui.toast.QDesktopServices.openUrl", lambda *_: pytest.fail("Automatic browser launch"))
    begin(window).deliver(body, status, error)
    assert not window.update_toast.isVisible() and not window._startup_pending


def test_timeout_is_silent_and_request_is_canceled_on_close(window):
    reply = begin(window)
    window.update_checker.timer.timeout.emit()
    assert reply.aborted and not window.update_toast.isVisible()
    window.update_checker.check()
    pending = window.update_checker.network.replies[-1]
    window.close()
    assert pending.aborted and not window.update_checker.timer.isActive()


def test_closing_before_scheduled_check_prevents_network(window, app):
    window.schedule_update_check()
    window.close()
    app.processEvents()
    assert not window._startup_timer.isActive()
    assert not window.update_checker.network.requests


def test_manual_dialog_shares_startup_request_and_closing_it_keeps_check_running(window):
    reply = begin(window)
    dialog = UpdatesDialog(window, checker=window.update_checker)
    dialog.check()
    assert not dialog.check_button.isEnabled()
    assert len(window.update_checker.network.requests) == 1
    dialog.reject()
    assert not reply.aborted
    reply.deliver(release())
    assert window.update_toast.isVisible() and dialog.release_button.isEnabled()
    dialog.deleteLater()


def test_manual_result_before_startup_does_not_trigger_another_request(window):
    window.update_checker.check()
    window.update_checker.network.replies[-1].deliver(release())
    begin(window)
    wait_for(lambda: window.update_toast.isVisible())
    assert len(window.update_checker.network.requests) == 1


def test_notification_repeats_on_new_launch(window, monkeypatch, app):
    begin(window).deliver(release())
    window.update_toast.dismiss()
    second = MainWindow()
    second.show()
    try:
        begin(second).deliver(release())
        assert second.update_toast.isVisible()
    finally:
        second.close()
        second.deleteLater()
        app.processEvents()


def test_toast_stacks_with_save_notice_and_can_be_dismissed(window, tmp_path, app):
    begin(window).deliver(release())
    window.toast.show_completion("PDF salvato", tmp_path / "document.pdf")
    app.processEvents()
    assert not window.toast.geometry().intersects(window.update_toast.geometry())
    window.resize(1000, 700)
    app.processEvents()
    toast = window.update_toast
    assert toast.geometry().right() < window.width()
    assert not window.toast.geometry().intersects(toast.geometry())
    QTest.mouseClick(toast.close_button, Qt.MouseButton.LeftButton)
    assert not toast.isVisible() and not toast.timer.isActive()


def test_browser_failure_keeps_notice_with_retry_action(window, monkeypatch):
    monkeypatch.setattr("ape_pdf_studio.ui.toast.QDesktopServices.openUrl", lambda _: False)
    begin(window).deliver(release())
    window.update_toast.release_button.click()
    assert window.update_toast.isVisible()
    assert "browser" in window.update_toast.detail.text()


def test_update_notice_pauses_on_hover_and_expires(window):
    begin(window).deliver(release())
    toast = window.update_toast
    assert toast.DURATION_MS == 15000
    toast.timer.start(2000)
    toast.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))
    assert not toast.timer.isActive() and 0 < toast._remaining <= 2000
    toast.leaveEvent(QEvent(QEvent.Type.Leave))
    assert toast.timer.isActive()
    toast.timer.timeout.emit()
    assert not toast.isVisible() and not toast.timer.isActive()
