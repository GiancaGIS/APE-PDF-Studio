"""Exercise persisted UI language, catalog formatting and real worker messages."""
from string import Formatter
import ast
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from test_gui import app, text_pdf
from ape_pdf_studio.core.errors import PDFSecurityError
from ape_pdf_studio.core.localization import LANGUAGES, catalog, current_language, normalize_language, tr
from ape_pdf_studio.core.security import SecurityReport, check_pdf
from ape_pdf_studio.ui.content_dialogs import NoteDialog
from ape_pdf_studio.ui.language import configure_language
from ape_pdf_studio.ui.main_window import MainWindow
from ape_pdf_studio.ui.ocr import OcrDialog
from ape_pdf_studio.ui.properties_dialog import PropertiesDialog
from ape_pdf_studio.ui.settings_dialog import SettingsDialog


@pytest.fixture
def preferences(app):
    settings = QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "APEX", "PDF Studio")
    previous = settings.value("ui/language")
    settings.remove("ui/language")
    settings.sync()
    configure_language(app, "it")
    yield settings
    if previous is None:
        settings.remove("ui/language")
    else:
        settings.setValue("ui/language", previous)
    settings.sync()
    configure_language(app, "it")


@pytest.mark.parametrize("language", ("en", "es", "fr"))
def test_catalog_formatting_and_coverage(language):
    entries = catalog(language)
    assert set(entries) == set(catalog("en"))
    assert len(entries) > 500
    formatter = Formatter()
    for source, translated in entries.items():
        fields = lambda text: sorted((field, spec, conversion) for _, field, spec, conversion in formatter.parse(text) if field is not None)
        assert fields(translated) == fields(source), source
        assert translated, source


@pytest.mark.parametrize("value", (None, "de", "", "EN", 1, ["en"]))
def test_invalid_language_uses_italian(value):
    assert normalize_language(value) == "it"


def test_all_explicit_application_messages_have_translations():
    messages = set()
    for path in (Path(__file__).resolve().parents[1] / "src/ape_pdf_studio").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                if node.args and isinstance(node.args[0], ast.Constant):
                    messages.add(node.args[0].value)
    from ape_pdf_studio.core.metadata import EDITABLE_FIELDS
    from ape_pdf_studio.core.security import FINDINGS
    from ape_pdf_studio.core.ocr import LANGUAGES as OCR_LANGUAGES
    from ape_pdf_studio.ui.theme import THEMES
    messages.update(EDITABLE_FIELDS.values())
    messages.update(FINDINGS.values())
    messages.update(OCR_LANGUAGES.values())
    messages.update(theme.label for theme in THEMES.values())
    for language in ("en", "es", "fr"):
        assert not messages - set(catalog(language))


@pytest.mark.parametrize("language,open_text,note_title,ocr_title,metadata", (
    ("it", "Apri PDF…", "Nota PDF", "Riconosci testo (OCR)", "Metadati"),
    ("en", "Open PDF…", "PDF note", "Recognize text (OCR)", "Metadata"),
    ("es", "Abrir PDF…", "Nota PDF", "Reconocer texto (OCR)", "Metadatos"),
    ("fr", "Ouvrir un PDF…", "Note PDF", "Reconnaître le texte (OCR)", "Métadonnées"),
))
def test_saved_language_translates_window_and_dialogs(app, preferences, text_pdf, language, open_text, note_title, ocr_title, metadata):
    preferences.setValue("ui/language", language)
    preferences.sync()
    window = MainWindow()
    try:
        assert window.language_name == language
        assert window.a_open.text() == open_text
        window.open_pdf(text_pdf)
        assert window.current is not None
        dialog = SettingsDialog(window.theme_name, window)
        assert dialog.language_combo.currentData() == language
        assert [dialog.language_combo.itemData(i) for i in range(4)] == list(LANGUAGES)
        note = NoteDialog("Salva", parent=window)
        assert note.windowTitle() == note_title
        assert note.text.toPlainText() == "Salva"  # User content is never translated.
        ocr = OcrDialog(0, 3, window)
        assert ocr.windowTitle() == ocr_title
        assert ocr.language.currentData() == "ita+eng"  # OCR language is independent.
        properties = PropertiesDialog(window.current.editor, lambda changes: None, window)
        assert properties.tabs.tabText(0) == metadata
        with window.current.editor._reader() as reader:
            assert "Alpha beta PDF page 1" in reader.pages[0].extract_text()
    finally:
        for index in reversed(range(window.tabs.count())):
            window.tabs.widget(index).close_document()
            window.tabs.removeTab(index)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_settings_apply_persists_language_for_next_start(app, preferences, monkeypatch):
    window = MainWindow()
    def choose(dialog):
        dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("fr"))
        dialog.apply_button.click()
        dialog.reject()
        return dialog.result()
    monkeypatch.setattr(SettingsDialog, "exec", choose)
    window.show_settings()
    assert preferences.value("ui/language") == "fr"
    assert current_language() == "it"
    assert window.a_open.text() == "Apri PDF…"
    assert "Riavvia" in window.statusBar().currentMessage()
    window.close()
    replacement = MainWindow()
    assert replacement.a_open.text() == "Ouvrir un PDF…"
    replacement.close()
    window.deleteLater()
    replacement.deleteLater()


def test_cancel_does_not_save_language(app, preferences, monkeypatch):
    window = MainWindow()
    def cancel(dialog):
        dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("es"))
        dialog.reject()
        return dialog.result()
    monkeypatch.setattr(SettingsDialog, "exec", cancel)
    window.show_settings()
    assert preferences.value("ui/language") is None
    window.close()
    window.deleteLater()


def test_invalid_saved_language_recovers(app, preferences):
    preferences.setValue("ui/language", "unsupported")
    window = MainWindow()
    assert window.language_name == "it"
    assert window.a_open.text() == "Apri PDF…"
    window.close()
    window.deleteLater()


@pytest.mark.parametrize("language,reason", (
    ("en", "unreadable file or incomplete analysis"),
    ("es", "archivo ilegible o análisis incompleto"),
    ("fr", "fichier illisible ou analyse incomplète"),
))
def test_real_security_worker_inherits_language(monkeypatch, tmp_path, language, reason):
    monkeypatch.setenv("APE_UI_LANGUAGE", language)
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"%PDF-1.7\nbroken")
    with pytest.raises(PDFSecurityError, match=reason):
        check_pdf(path)
    assert tr("Pagina {0}: resa di compatibilità per immagini o effetti PDF complessi.", 3).startswith(catalog(language)["Pagina {0}: resa di compatibilità per immagini o effetti PDF complessi."].split("{0}")[0] + "3")
    assert SecurityReport(("launch",)).summary == "• " + catalog(language)["Azioni di avvio di file o programmi"]


def test_qt_standard_buttons_are_translated(app, preferences):
    configure_language(app, "fr")
    assert app._apex_translators
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Annuler"
    assert tr("Unknown document title") == "Unknown document title"
