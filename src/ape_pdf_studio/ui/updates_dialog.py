"""Manual release check using the same bounded client as the startup notice."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .. import __version__
from ..core.localization import tr
from .update_checker import UpdateChecker


class UpdatesDialog(QDialog):
    def __init__(self, parent=None, *, checker=None):
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
        self._owns_checker = checker is None
        self.checker = checker if checker is not None else UpdateChecker(self)
        self.checker.started.connect(self._started)
        self.checker.completed.connect(self._completed)
        self.checker.failed.connect(self._failed)
        if self.checker.busy:
            self._started()
        elif self.checker.checked:
            if self.checker.error:
                self._failed(self.checker.error)
            else:
                self._completed(self.checker.release)

    def check(self):
        self.checker.check()

    def _started(self):
        self.check_button.setEnabled(False)
        self.release_button.setEnabled(False)
        self.status.setText(tr("Controllo aggiornamenti in corso…"))

    def _completed(self, release):
        self.check_button.setEnabled(True)
        if release is None:
            message = tr("Nessuna release pubblica stabile trovata. Il repository potrebbe non essere disponibile.")
        elif release.update_available:
            message = tr("È disponibile la versione {0}.", release.version)
            self.release_button.setEnabled(True)
        else:
            message = tr("La versione installata è aggiornata rispetto alla release {0}.", release.version)
        self.status.setText(message)

    def _failed(self, message):
        self.check_button.setEnabled(True)
        self.release_button.setEnabled(False)
        self.status.setText(message)

    def open_release(self):
        release = self.checker.release
        if release is not None and release.update_available:
            if not QDesktopServices.openUrl(QUrl(release.url)):
                self.status.setText(tr("Impossibile aprire il browser. Riprova."))

    def done(self, result):
        if self._owns_checker:
            self.checker.cancel()
        super().done(result)
