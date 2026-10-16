"""Application entrypoint."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .ui.icons import application_icon
from . import __version__


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from .core.private_work import cleanup_stale
    cleanup_stale()
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
    app.setWindowIcon(application_icon())
    apply_theme(app)
    window = MainWindow()
    window.show()
    for argument in sys.argv[1:]:
        candidate = Path(argument)
        if candidate.is_file() and candidate.suffix.lower() == ".pdf":
            window.open_pdf(candidate)
    return app.exec()
