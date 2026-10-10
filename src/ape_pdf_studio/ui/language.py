"""Initialize the saved application language and Qt's standard dialog translations."""
import os

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

from ..core.localization import normalize_language


def configure_language(app, language):
    language = normalize_language(language)
    os.environ["APE_UI_LANGUAGE"] = language
    QLocale.setDefault(QLocale(language))
    for translator in getattr(app, "_apex_translators", []):
        app.removeTranslator(translator)
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translators = []
    if translator.load("qtbase_" + language, folder):
        app.installTranslator(translator)
        translators.append(translator)
    app._apex_translators = translators
    return language
