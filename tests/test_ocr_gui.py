"""Verify Qt search/selection after OCR and real subprocess cancellation."""
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMessageBox

from test_gui import app, text_pdf, window, wait_for
from test_ocr import scan_pdf, tessdata
from ape_pdf_studio.ui.ocr import OcrDialog, OcrJob


def test_ocr_dialog_validates_ranges(app, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    dialog = OcrDialog(1, 4)
    assert dialog.language.currentData() == "ita+eng"
    dialog.scope.setCurrentIndex(2)
    dialog.range.setText("5")
    dialog.accept()
    assert errors and not dialog.result()
    dialog.range.setText("1,3-4")
    dialog.accept()
    assert dialog.pages == [0, 2, 3] and dialog.result()
    dialog.deleteLater()


def test_gui_ocr_search_selection_save_undo_and_redo(app, window, scan_pdf, tessdata, tmp_path, monkeypatch):
    import ape_pdf_studio.ui.main_window as module
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(OcrDialog, "exec", lambda self: (self.accept(), self.result())[1])
    monkeypatch.setattr(module, "OcrJob", lambda *args, **kwargs: OcrJob(*args, cache_dir=tessdata, **kwargs))
    window.open_pdf(scan_pdf)
    tab = window.current
    wait_for(lambda: tab.count == 3)
    assert not tab.pdf.getAllText(0).text().strip()
    assert window.a_ocr.isEnabled()
    window.recognize_text()
    assert window._ocr_job is not None
    assert not window.a_ocr.isEnabled()
    window.close_tab(window.tabs.indexOf(tab))
    assert window.tabs.indexOf(tab) >= 0
    wait_for(lambda: window._ocr_job is None, timeout=30000)
    assert messages and tab.editor.dirty
    assert "Documento italiano" in tab.pdf.getAllText(0).text()
    tab.viewer.select_page_text(0)
    assert tab.viewer.has_selection
    tab.viewer.copy_selection()
    assert "English searchable" in app.clipboard().text()
    tab.set_search("Documento")
    wait_for(lambda: tab.search_model.count() > 0)
    saved = tmp_path / "saved.pdf"
    tab.editor.save_as(saved)
    window.undo()
    wait_for(lambda: not tab.pdf.getAllText(0).text().strip())
    window.redo()
    wait_for(lambda: "English searchable" in tab.pdf.getAllText(0).text())


def test_job_cancellation_discards_result_and_cleans_temp(app, scan_pdf, tessdata):
    job = OcrJob(scan_pdf, cache_dir=tessdata, download=False)
    completed, cancelled, succeeded = [], [], []
    job.progress.connect(lambda *_: job.cancel())
    job.cancelled.connect(lambda: cancelled.append(True))
    job.succeeded.connect(lambda *_: succeeded.append(True))
    job.finished.connect(lambda: completed.append(True))
    job.start()
    wait_for(lambda: completed, timeout=30000)
    assert cancelled and not succeeded
    assert job.process.state() == QProcess.ProcessState.NotRunning
    assert not job.snapshot.parent.exists()
    job.deleteLater()


def test_job_failed_start_reports_error_and_cleans_temp(app, scan_pdf, monkeypatch):
    import ape_pdf_studio.ui.ocr as module
    monkeypatch.setattr(module, "worker_command", lambda *_: ["C:/missing-python-for-ocr-test/python.exe"])
    job = OcrJob(scan_pdf)
    errors, completed = [], []
    job.failed.connect(errors.append)
    job.finished.connect(lambda: completed.append(True))
    job.start()
    wait_for(lambda: completed)
    assert errors and "avviare" in errors[0]
    assert not job.snapshot.parent.exists()
    job.deleteLater()
