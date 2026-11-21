"""A busy parser must not block opening, typing in, or closing the text editor."""
from threading import Event
import time

from PySide6.QtCore import QPointF, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from pypdf import PdfReader

from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.ui.content_dialogs import TextEditDialog
from test_gui import app, text_pdf, window, wait_for


def test_inline_editor_opens_before_slow_target_and_keeps_event_loop_alive(window, monkeypatch):
    tab = window.current
    tab.inline_text.reflow.setChecked(False)
    release = Event()
    resolve = tab.editor.text_at
    def slow_target(*args):
        assert release.wait(10)
        return resolve(*args)
    monkeypatch.setattr(tab.editor, "text_at", slow_target)
    try:
        window.edit_text_at(tab, 0, QPointF(60, 95))
        inline = tab.inline_text
        assert inline.active and inline.bar.isVisible() and inline.control is None
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        wait_for(lambda: bool(heartbeat))
        assert not release.is_set()
        release.set()
        wait_for(lambda: inline.control is not None, timeout=10000)
        assert "Alpha beta PDF" in inline.control.text()
        assert inline.apply.isEnabled()
        assert tab.viewer.edit_target is not None
        inline.control.setText("New text")
        inline.apply.click()
        wait_for(lambda: not inline.committing, timeout=10000)
        assert "New text" in tab.pdf.getAllText(0).text()
        assert tab.viewer.edit_target is None
        window.undo()
        assert "Alpha beta PDF" in tab.pdf.getAllText(0).text()
    finally:
        release.set()


def test_font_requests_coalesce_cache_and_never_repeat_on_apply(window):
    tab = window.current
    target = tab.editor.text_at(0, (60, 95))
    release, entered = Event(), Event()
    calls, saved = [], []
    def font(target, text):
        calls.append(text)
        entered.set()
        assert release.wait(10)
        return "Helvetica", False
    dialog = TextEditDialog(tab.editor.path, target, lambda *args: saved.append(args),
                            window, font_resolver=font)
    dialog.show()
    try:
        wait_for(entered.is_set)
        dialog.text.setText("intermediate")
        dialog._font_info()
        dialog.text.setText("latest")
        dialog._font_info()
        assert calls == [target.text]
        release.set()
        wait_for(lambda: "latest" in dialog._font_cache)
        assert calls == [target.text, "latest"]
        dialog.text.setText(target.text)
        dialog._font_info()
        assert "Helvetica" in dialog.font_label.text()
        assert calls == [target.text, "latest"]
        dialog.save_button.click()
        assert saved and calls == [target.text, "latest"]
    finally:
        release.set()
        dialog.reject()
        dialog.deleteLater()


def test_cancel_during_target_load_ignores_late_result(window):
    release, returned = Event(), Event()
    tab = window.current
    target = tab.editor.text_at(0, (60, 95))
    def load():
        assert release.wait(10)
        returned.set()
        return target
    dialog = TextEditDialog(tab.editor.path, None, lambda *args: None, window,
                            target_resolver=load, font_resolver=lambda *args: ("Helvetica", False))
    dialog.show()
    ready = []
    dialog.target_ready.connect(ready.append)
    try:
        dialog.reject()
        assert not dialog.isVisible()
        release.set()
        wait_for(returned.is_set)
        QApplication.processEvents()
        assert not ready and dialog.target is None and not tab.editor.dirty
    finally:
        release.set()
        dialog.deleteLater()


def test_target_failure_is_visible_without_enabling_apply(window):
    def fail():
        raise PDFOperationError("No editable text")
    dialog = TextEditDialog(window.current.editor.path, None, lambda *args: None,
                            window, target_resolver=fail)
    dialog.show()
    try:
        wait_for(lambda: dialog.feedback.isVisible())
        assert dialog.feedback.text() == "No editable text"
        assert not dialog.save_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_context_menu_does_not_perform_synchronous_pdf_reads(window, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Context menu must not wait for a PDF RPC")
    tab = window.current
    monkeypatch.setattr(tab.pdf, "getSelection", forbidden)
    monkeypatch.setattr(tab.pdf, "getAllText", forbidden)
    menu = tab.viewer.make_context_menu(0, QPointF(60, 95))
    try:
        assert any(action.isEnabled() and action.text().startswith("Modifica testo")
                   for action in menu.actions())
    finally:
        menu.deleteLater()


def test_slow_commit_keeps_gui_live_and_save_uses_the_committed_tab(window, text_pdf, tmp_path, monkeypatch):
    tab = window.current
    inline = tab.inline_text
    other = tmp_path / 'other.pdf'
    other.write_bytes(text_pdf.read_bytes())
    window.open_pdf(other)
    other_tab = window.current
    window.tabs.setCurrentWidget(tab)
    inline.reflow.setChecked(False)
    window.edit_text_at(tab, 0, QPointF(60, 95))
    wait_for(lambda: inline.control is not None, timeout=10000)
    revision = tab.editor.path
    original_commit = tab.editor.replace_text
    calls = []
    release = Event()

    def slow_commit(*args):
        calls.append(args)
        time.sleep(.5)
        assert release.wait(10)
        return original_commit(*args)

    output = tmp_path / 'slow-commit.pdf'
    monkeypatch.setattr(tab.editor, 'replace_text', slow_commit)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    try:
        inline.control.setText('Background edit')
        inline.apply.click()
        assert inline.committing
        window.save()
        recorded = []
        QTimer.singleShot(0, lambda: recorded.append(True))
        wait_for(lambda: bool(recorded) and bool(calls))
        tab.set_zoom(250)
        tab.viewer.verticalScrollBar().setValue(70)
        assert tab.viewer.verticalScrollBar().value() == 70
        window.tabs.setCurrentWidget(other_tab)
        assert window.current is other_tab and not output.exists() and tab.editor.path == revision
        release.set()
        wait_for(lambda: not inline.committing, timeout=15000)
        assert inline.control is None, inline.feedback.text()
        wait_for(output.exists, timeout=15000)
        assert len(calls) == 1
        assert 'Background edit' in PdfReader(output).pages[0].extract_text()
        assert window.current is other_tab and not other_tab.dirty and not tab.dirty
    finally:
        release.set()


def test_failed_commit_preserves_draft_and_does_not_run_save(window, monkeypatch):
    tab = window.current
    inline = tab.inline_text
    inline.reflow.setChecked(False)
    window.edit_text_at(tab, 0, QPointF(60, 95))
    wait_for(lambda: inline.control is not None, timeout=10000)
    inline.control.setText('Preserve this draft')
    revision = tab.editor.path

    def fail(*_args):
        raise PDFOperationError('Worker interrupted')

    destinations = []
    monkeypatch.setattr(tab.editor, 'replace_text', fail)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: destinations.append(a))
    window.save()
    wait_for(lambda: not inline.committing, timeout=10000)
    assert inline.feedback.text() == 'Worker interrupted'
    assert inline.control.text() == 'Preserve this draft' and inline.dirty
    assert tab.editor.path == revision and not destinations
