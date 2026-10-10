"""Read installed/source-distribution license notices without network access."""
from importlib import metadata
from pathlib import Path
import sys

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from ..core.localization import tr


def legal_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "licenses"
    project = Path(__file__).resolve().parents[3]
    if (project / "NOTICE").is_file() and (project / "LICENSE").is_file():
        return project
    package = metadata.distribution("ape-pdf-studio")
    for file in package.files or ():
        if str(file).replace("\\", "/").endswith(".dist-info/licenses/NOTICE"):
            return Path(package.locate_file(file)).parent
    raise FileNotFoundError("Application license notices are missing")


class LicensesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Licenze e codice sorgente"))
        self.resize(800, 600)
        self.documents = QComboBox()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.documents)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.folder = legal_directory()
        for label, filename in (
            (tr("Licenza del programma (AGPLv3)"), "LICENSE"),
            (tr("Copyright e avvisi"), "NOTICE"),
            (tr("Componenti di terze parti"), "THIRD_PARTY_NOTICES.md"),
            (tr("Codice sorgente e compilazione"), "SOURCE_CODE.md"),
            ("Qt / PySide6 — LGPLv3", "LGPL-3.0-only.txt"),
            ("GPLv3", "GPL-3.0-only.txt"),
            ("Tesseract / tessdata — Apache 2.0", "Apache-2.0.txt"),
            (tr("Versioni delle dipendenze"), "dependency-manifest.json"),
            (tr("Archivi sorgenti e checksum"), "source-manifest.json"),
        ):
            path = self.folder / filename
            if not path.is_file():
                path = self.folder / "licenses" / filename
            if path.is_file():
                self.documents.addItem(label, str(path))
        self.documents.currentIndexChanged.connect(self._show_document)
        self._show_document()

    def _show_document(self, *_args):
        filename = self.documents.currentData()
        self.text.setPlainText(Path(filename).read_text(encoding="utf-8", errors="replace") if filename else "")
