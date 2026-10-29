"""Application entrypoint."""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .ui.icons import application_icon
from .ui.single_instance import SingleInstance, InstanceError, pdf_arguments
from .core.localization import tr
from . import __version__


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if sys.platform == "win32":
        # Match installed shortcuts and distinguish the app from its Python host.
        import ctypes
        try:
            set_identity = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
            set_identity.argtypes = [ctypes.c_wchar_p]
            set_identity.restype = ctypes.c_long
            set_identity("APE.PDFStudio")
        except (AttributeError, OSError):
            logging.debug("Windows application identity unavailable", exc_info=True)
    app = QApplication(sys.argv)
    app.setApplicationName("APE PDF Studio")
    app.setOrganizationName("GiancaGIS")
    app.setApplicationVersion(__version__)
    instance = None
    try:
        paths = pdf_arguments(sys.argv[1:])
        instance = SingleInstance(app)
        if not instance.start(paths):
            return 0
        app.aboutToQuit.connect(instance.close)
        from .core.private_work import cleanup_stale
        cleanup_stale()
        app.setWindowIcon(application_icon())
        apply_theme(app)
        window = MainWindow()
        instance.attach(window)
        window.show()
        window.schedule_update_check()
        instance.open_initial(paths)
        return app.exec()
    except (InstanceError, OSError) as exc:
        logging.error('Application instance startup failed: %s', exc)
        QMessageBox.critical(None, tr('Apertura PDF'), tr(
            "Impossibile avviare o contattare l'applicazione. Attendi qualche secondo e riprova."))
        return 1
    finally:
        if instance is not None:
            instance.close()
            instance.deleteLater()
