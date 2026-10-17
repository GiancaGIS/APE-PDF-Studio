"""Product rename keeps prior preferences, cached OCR data and document behavior."""
import hashlib

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QLabel, QMessageBox

from test_gui import app, text_pdf
from test_localization import preferences
from ape_pdf_studio.ui.main_window import MainWindow
from ape_pdf_studio.ui.ocr import default_ocr_cache


@pytest.mark.parametrize("language", ("it", "en", "es", "fr"))
def test_product_rename_with_legacy_preferences(app, preferences, text_pdf, monkeypatch, language):
    # These are the preferences written by the former APEX installation.
    previous_recent = preferences.value("recent/files")
    preferences.setValue("ui/language", language)
    preferences.setValue("recent/files", [str(text_pdf)])
    preferences.sync()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[1:]))
    window = MainWindow()
    try:
        assert window.language_name == language
        assert window.recent_files == [str(text_pdf)]
        assert window.windowTitle() == "APE PDF Studio"
        assert not window.windowIcon().isNull()
        assert any(label.text() == "APE PDF STUDIO" for label in window.findChildren(QLabel))
        window.about()
        assert messages and all("APEX" not in message for message in messages[0])
        assert "APE PDF Studio" in messages[0][0]
        assert "GiancaGIS" in messages[0][1]
        window.open_pdf(text_pdf)
        assert window.current is not None
        assert window.windowTitle().endswith(" — APE PDF Studio")
        assert "Alpha beta" in window.current.pdf.getAllText(0).text()
    finally:
        for index in reversed(range(window.tabs.count())):
            window.tabs.widget(index).close_document()
            window.tabs.removeTab(index)
        window.close()
        window.deleteLater()
        if previous_recent is None:
            preferences.remove("recent/files")
        else:
            preferences.setValue("recent/files", previous_recent)
        app.processEvents()


@pytest.mark.parametrize("relative", (
    "APE/APE PDF Studio/ocr/tessdata",
    "APEX/APEX PDF Studio/ocr/tessdata", "APEX PDF Studio/ocr/tessdata",
))
def test_rename_reuses_previously_downloaded_ocr_models(tmp_path, monkeypatch, relative):
    current_base = tmp_path / "GiancaGIS/APE PDF Studio"
    legacy = tmp_path / relative
    legacy.mkdir(parents=True)
    data = b"existing language data"
    monkeypatch.setattr("ape_pdf_studio.ui.ocr.MODEL_HASHES", {"ita": hashlib.sha256(data).hexdigest()})
    (legacy / "ita.traineddata").write_bytes(data)
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda location: str(
        current_base if location == QStandardPaths.StandardLocation.AppLocalDataLocation else tmp_path))
    current = current_base / "ocr/tessdata"
    assert default_ocr_cache() == current
    assert (current / "ita.traineddata").read_bytes() == data
    assert (legacy / "ita.traineddata").read_bytes() == data
    assert default_ocr_cache() == current
    assert not list(current.glob(".apex_export_*"))


def test_ocr_migration_completes_partial_cache_and_skips_corrupt_models(tmp_path, monkeypatch):
    current_base = tmp_path / "GiancaGIS/APE PDF Studio"
    current = current_base / "ocr/tessdata"
    current.mkdir(parents=True)
    data = {"ita": b"Italian model", "eng": b"English model"}
    monkeypatch.setattr("ape_pdf_studio.ui.ocr.MODEL_HASHES", {
        code: hashlib.sha256(model).hexdigest() for code, model in data.items()})
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda location: str(
        current_base if location == QStandardPaths.StandardLocation.AppLocalDataLocation else tmp_path))
    (current / "ita.traineddata").write_bytes(data["ita"])
    first = tmp_path / "APE/APE PDF Studio/ocr/tessdata"
    first.mkdir(parents=True)
    (first / "eng.traineddata").write_bytes(b"incomplete download")
    second = tmp_path / "APEX/APEX PDF Studio/ocr/tessdata"
    second.mkdir(parents=True)
    (second / "eng.traineddata").write_bytes(data["eng"])
    original_time = (current / "ita.traineddata").stat().st_mtime_ns
    assert default_ocr_cache() == current
    assert (current / "ita.traineddata").stat().st_mtime_ns == original_time
    assert (current / "eng.traineddata").read_bytes() == data["eng"]


def test_ocr_migration_failure_retains_old_models(tmp_path, monkeypatch):
    current_base = tmp_path / "GiancaGIS/APE PDF Studio"
    legacy = tmp_path / "APE/APE PDF Studio/ocr/tessdata"
    legacy.mkdir(parents=True)
    data = b"Italian model"
    (legacy / "ita.traineddata").write_bytes(data)
    monkeypatch.setattr("ape_pdf_studio.ui.ocr.MODEL_HASHES", {"ita": hashlib.sha256(data).hexdigest()})
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda location: str(
        current_base if location == QStandardPaths.StandardLocation.AppLocalDataLocation else tmp_path))

    def cannot_copy(*args):
        raise PermissionError("read-only destination")

    monkeypatch.setattr("ape_pdf_studio.ui.ocr.atomic_copy", cannot_copy)
    assert default_ocr_cache() == current_base / "ocr/tessdata"
    assert (legacy / "ita.traineddata").read_bytes() == data


def test_application_author_sets_qt_data_folder(app, monkeypatch):
    import ape_pdf_studio.app as entrypoint

    previous_name, previous_organization = app.applicationName(), app.organizationName()
    monkeypatch.setattr(entrypoint, "QApplication", lambda args: app)
    monkeypatch.setattr(entrypoint, "MainWindow", lambda: type("Window", (), {
        "show": lambda self: None, "schedule_update_check": lambda self: None})())
    monkeypatch.setattr(entrypoint, "apply_theme", lambda app: None)
    monkeypatch.setattr(app, "exec", lambda: 0)
    monkeypatch.setattr(entrypoint.sys, "argv", ["ape-pdf-studio"])
    try:
        assert entrypoint.main() == 0
        assert app.organizationName() == "GiancaGIS"
        assert app.applicationName() == "APE PDF Studio"
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        assert folder.replace("\\", "/").endswith("/GiancaGIS/APE PDF Studio")
    finally:
        app.setApplicationName(previous_name)
        app.setOrganizationName(previous_organization)


def test_renamed_cli_and_module_launchers_are_installed():
    from importlib.metadata import distribution
    from ape_pdf_studio import __version__
    assert __version__ == distribution("ape-pdf-studio").version
    entries = distribution("ape-pdf-studio").entry_points
    commands = {entry.name for entry in entries}
    assert {"ape-pdf-studio", "ape-pdf-cli", "apex-pdf-studio", "apex-pdf-cli"} <= commands
    for entry in entries:
        if entry.name in {"ape-pdf-studio", "ape-pdf-cli", "apex-pdf-studio", "apex-pdf-cli"}:
            assert callable(entry.load())


@pytest.mark.parametrize("filename", ("PDF with spaces.pdf", "Documento con accenti è.PDF"))
def test_association_launch_opens_pdf_argument(app, text_pdf, tmp_path, monkeypatch, filename):
    import ape_pdf_studio.app as entrypoint

    document = tmp_path / filename
    document.write_bytes(text_pdf.read_bytes())
    opened = []

    class Window:
        def show(self):
            pass

        def schedule_update_check(self):
            opened.append("startup-update-check")

        def open_pdf(self, path):
            opened.append(path)

    previous_name, previous_organization = app.applicationName(), app.organizationName()
    monkeypatch.setattr(entrypoint, "QApplication", lambda args: app)
    monkeypatch.setattr(entrypoint, "MainWindow", Window)
    monkeypatch.setattr(entrypoint, "apply_theme", lambda app: None)
    monkeypatch.setattr(app, "exec", lambda: 0)
    monkeypatch.setattr(entrypoint.sys, "argv", ["APEPDFStudio.exe", str(document)])
    try:
        assert entrypoint.main() == 0
        assert opened == ["startup-update-check", document]
    finally:
        app.setApplicationName(previous_name)
        app.setOrganizationName(previous_organization)
