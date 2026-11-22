"""Exercise the installer download, publisher checks and consent without running a setup."""
import hashlib
import json

import pytest
from PySide6.QtCore import QByteArray, QProcess, QUrl
from PySide6.QtWidgets import QMessageBox

from test_gui import app, text_pdf, wait_for
from test_startup_updates import begin, window
from test_updates import installer_asset
from test_updates_gui import Network
from ape_pdf_studio.core.private_work import PrivateDirectory
from ape_pdf_studio.core.updates import AUTO_INSTALL_KEY, parse_release
from ape_pdf_studio.ui import update_installer
from ape_pdf_studio.ui.settings_dialog import SettingsDialog
from ape_pdf_studio.ui.updates_dialog import UpdatesDialog


PAYLOAD = b"Synthetic setup bytes"
SIGNER = b"A" * 64


def update_body():
    asset = installer_asset(size=len(PAYLOAD), digest="sha256:" + hashlib.sha256(PAYLOAD).hexdigest())
    return json.dumps({"tag_name": "v1.10.0", "draft": False, "prerelease": False, "assets": [asset]}).encode()


@pytest.fixture
def installer_mocks(monkeypatch, tmp_path):
    monkeypatch.setattr(update_installer, "QNetworkAccessManager", Network)
    monkeypatch.setattr(update_installer, "PrivateDirectory", lambda **kw: PrivateDirectory(dir=tmp_path, **kw))
    monkeypatch.setattr(update_installer, "system_powershell", lambda: tmp_path / "powershell.exe")
    launched = []

    class Process(QProcess):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.startDetached = self.launch_for_test

        def start(self):
            pass

        def launch_for_test(self):
            launched.append(self)
            return True

    monkeypatch.setattr(update_installer, "QProcess", Process)
    return launched


@pytest.fixture
def job(app, installer_mocks):
    widget = update_installer.UpdateInstaller(parse_release(200, update_body()), "/CURRENTUSER")
    widget.start()
    yield widget
    widget.cancel()
    widget.deleteLater()
    app.processEvents()


def verified(job, monkeypatch, signer=SIGNER):
    monkeypatch.setattr(job.verifier, "readAllStandardOutput", lambda: QByteArray(signer))
    job.verifier.finished.emit(0, QProcess.ExitStatus.NormalExit)


def test_local_signature_required_before_download(job, monkeypatch, installer_mocks):
    errors = []
    job.failed.connect(errors.append)
    verified(job, monkeypatch, b"")
    assert errors and not job.network.requests and not installer_mocks


@pytest.mark.parametrize("payload", [b"truncated", PAYLOAD + b"extra", bytes(len(PAYLOAD))])
def test_download_rejects_invalid_size_or_digest(job, monkeypatch, installer_mocks, payload):
    errors = []
    job.failed.connect(errors.append)
    verified(job, monkeypatch)
    job.network.replies[-1].deliver(payload)
    assert errors and "hash" in errors[0]
    assert not job.prepared and not job.launch() and not installer_mocks
    wait_for(lambda: not job.path.exists())


def test_download_requires_matching_signer(job, monkeypatch, installer_mocks):
    errors = []
    job.failed.connect(errors.append)
    verified(job, monkeypatch)
    job.network.replies[-1].deliver(PAYLOAD)
    assert job.verifier.processEnvironment().value("APE_UPDATE_VERIFY_VERSION") == "1.10.0"
    verified(job, monkeypatch, b"B" * 64)
    assert errors and not job.prepared and not job.launch() and not installer_mocks


@pytest.mark.parametrize("url,allowed", [
    ("https://release-assets.githubusercontent.com/asset?token=1", True),
    ("https://objects.githubusercontent.com/asset", True),
    ("http://release-assets.githubusercontent.com/asset", False),
    ("https://release-assets.githubusercontent.com:444/asset", False),
    ("https://release-assets.githubusercontent.com.example.com/asset", False),
    ("https://user@release-assets.githubusercontent.com/asset", False),
    ("https://release-assets.githubusercontent.com/asset#bad", False),
])
def test_asset_redirect_identity(url, allowed):
    assert update_installer.allowed_asset_redirect(QUrl(url)) is allowed


def test_redirect_and_cancel_abort(job, monkeypatch, installer_mocks):
    verified(job, monkeypatch)
    reply = job.network.replies[-1]
    reply.redirected.emit(QUrl("https://evil.invalid/setup.exe"))
    assert reply.aborted and not job.timer.isActive() and not job.launch() and not installer_mocks
    wait_for(lambda: not job.path.exists())


def test_cancel_during_download_cleans_partial_file(job, monkeypatch, installer_mocks):
    verified(job, monkeypatch)
    reply = job.network.replies[-1]
    reply.deliver(PAYLOAD[:4], finish=False)
    job.cancel()
    assert reply.aborted and not job.timer.isActive() and not installer_mocks
    wait_for(lambda: not job.path.exists())


def test_setting_is_opt_in_and_persists(window, monkeypatch):
    monkeypatch.setattr("ape_pdf_studio.ui.main_window.installation_scope", lambda: "/CURRENTUSER")
    window.settings.remove(AUTO_INSTALL_KEY)
    try:
        assert window._auto_update_scope() is None
        begin(window).deliver(update_body())
        toast = window.update_toast
        assert toast.isVisible() and toast.install_button.isHidden()
        dialog = SettingsDialog(auto_updates_available=True)
        assert not dialog.auto_updates.isChecked()
        dialog.auto_updates_applied.connect(window._apply_auto_updates)
        dialog.auto_updates.setChecked(True)
        dialog.apply_selection()
        assert window.settings.value(AUTO_INSTALL_KEY, type=bool)
        assert toast.install_button.isVisible()
        dialog.auto_updates.setChecked(False)
        dialog.accept()
        assert not window.settings.value(AUTO_INSTALL_KEY, type=bool)
        assert toast.install_button.isHidden()
    finally:
        window.settings.remove(AUTO_INSTALL_KEY)


@pytest.mark.parametrize("answer,close_allowed", [
    (QMessageBox.StandardButton.Cancel, True),
    (QMessageBox.StandardButton.Yes, False),
    (QMessageBox.StandardButton.Yes, True),
])
def test_update_needs_confirmation_and_safe_close(window, monkeypatch, installer_mocks, answer, close_allowed):
    monkeypatch.setattr("ape_pdf_studio.ui.main_window.installation_scope", lambda: "/ALLUSERS")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: answer)
    window.close_guard = lambda: assert_closed_correctly() if close_allowed else False

    def assert_closed_correctly():
        assert not installer_mocks
        return True

    window.settings.setValue(AUTO_INSTALL_KEY, True)
    try:
        reply = begin(window)
        dialog = UpdatesDialog(window, checker=window.update_checker, allow_install=True)
        reply.deliver(update_body())
        dialog.install_button.click()
        assert dialog.install_release is window.update_checker.release
        window._start_update_install(dialog.install_release)
        job = window._update_install_job
        assert not job.network.requests
        verified(job, monkeypatch)
        job.network.replies[-1].deliver(PAYLOAD)
        assert not installer_mocks
        verified(job, monkeypatch)

        launch_expected = answer == QMessageBox.StandardButton.Yes and close_allowed
        assert bool(installer_mocks) is launch_expected
        assert window.isVisible() is not launch_expected
        if launch_expected:
            runner = installer_mocks[0]
            assert runner.processEnvironment().value("APE_UPDATE_SCOPE") == "/ALLUSERS"
            assert runner.processEnvironment().value("APE_UPDATE_SIGNER") == SIGNER.decode()
            assert runner.processEnvironment().value("APE_UPDATE_SHA256") == hashlib.sha256(PAYLOAD).hexdigest()
            assert runner.arguments()[-1] == update_installer.INSTALL_COMMAND
            assert job.path.read_bytes() == PAYLOAD
    finally:
        window.settings.remove(AUTO_INSTALL_KEY)
        window.close_guard = None


def test_declining_to_close_dirty_pdf_cancels_update(window, text_pdf, monkeypatch, installer_mocks):
    monkeypatch.setattr("ape_pdf_studio.ui.main_window.installation_scope", lambda: "/CURRENTUSER")
    original = text_pdf.read_bytes()
    window.open_pdf(text_pdf)
    tab = window.current
    tab.apply_edit("update_metadata", {"/Title": "Draft title"})
    assert tab.editor.dirty

    prompts = []
    def answer(_parent, title, _text, buttons, _default):
        prompts.append(title)
        return QMessageBox.StandardButton.Cancel if buttons & QMessageBox.StandardButton.Save else QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    window.settings.setValue(AUTO_INSTALL_KEY, True)
    try:
        reply = begin(window)
        reply.deliver(update_body())
        window.update_toast.install_button.click()
        job = window._update_install_job
        verified(job, monkeypatch)
        job.network.replies[-1].deliver(PAYLOAD)
        verified(job, monkeypatch)
        assert prompts == ["Aggiornamenti", "Modifiche non salvate"]
        assert window.isVisible() and window.current is tab and tab.editor.dirty
        assert window._update_install_job is None and not installer_mocks
        assert text_pdf.read_bytes() == original
    finally:
        window.settings.remove(AUTO_INSTALL_KEY)
        tab.close_document()
        window.tabs.removeTab(window.tabs.indexOf(tab))
