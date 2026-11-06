"""Legal documents remain readable and the help dialog follows UI language."""
import pytest
from pathlib import Path

from test_gui import app
from test_localization import preferences
from ape_pdf_studio import __version__
from ape_pdf_studio.ui.language import configure_language
from ape_pdf_studio.ui.licenses_dialog import LicensesDialog
from ape_pdf_studio.ui.main_window import MainWindow
from PySide6.QtWidgets import QMessageBox


@pytest.mark.parametrize("language,title", (
    ("it", "Licenze e codice sorgente"),
    ("en", "Licenses and source code"),
    ("es", "Licencias y código fuente"),
    ("fr", "Licences et code source"),
))
def test_legal_dialog_and_about_show_license_and_current_version(app, preferences, monkeypatch, language, title):
    preferences.setValue("ui/language", language)
    window = MainWindow()
    dialog = LicensesDialog(window)
    try:
        assert dialog.windowTitle() == title
        assert dialog.text.isReadOnly()
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in dialog.text.toPlainText()
        assert dialog.documents.count() >= 7
        for index in range(dialog.documents.count()):
            dialog.documents.setCurrentIndex(index)
            assert dialog.text.toPlainText().strip()
        messages = []
        monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
        window.about()
        assert f"APE PDF Studio {__version__}" in messages[0]
        assert "Copyright (C) 2026 GiancaGIS" in messages[0]
        assert "AGPLv3" in messages[0] and "LGPLv3" in messages[0]
        assert "?" not in messages[0]
        assert {'it': 'SENZA GARANZIE', 'en': 'WITHOUT WARRANTIES',
                'es': 'SIN GARANTÍAS', 'fr': 'SANS GARANTIE'}[language] in messages[0]
        assert '15–17' in messages[0]
        if language == 'en':
            readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
            assert readme.split('## Disclaimer', 1)[1].strip().split('\n', 1)[0] in messages[0]
    finally:
        dialog.close()
        window.close()
        dialog.deleteLater()
        window.deleteLater()
        configure_language(app, "it")
        app.processEvents()
