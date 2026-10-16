"""Direct page editing: drafts, shortcuts, geometry, sandbox records and history."""
from threading import Event

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from ape_pdf_studio.core.rpc_validation import result
from test_gui import app, text_pdf, window, wait_for


def start(window, point=QPointF(60, 95)):
    tab = window.current
    tab.inline_text.reflow.setChecked(False)
    window.edit_text_at(tab, 0, point)
    wait_for(lambda: tab.inline_text.control is not None, timeout=10000)
    tab.inline_text.control.selectAll()
    return tab, tab.inline_text


def test_mode_boxes_click_type_enter_and_history(window, text_pdf):
    original = text_pdf.read_bytes()
    tab = window.current
    tab.inline_text.reflow.setChecked(False)
    window.a_edit_text.trigger()
    inline = tab.inline_text
    assert inline.active and window.a_edit_text.isChecked()
    wait_for(lambda: bool(inline.boxes.get(0)), timeout=10000)
    point = tab.viewer.page_transform(0).map(QPointF(60, 95)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point)
    wait_for(lambda: inline.control is not None, timeout=10000)
    assert inline.control.parentWidget() is tab.viewer.viewport()
    assert not inline.control.hasSelectedText()
    assert not any(isinstance(widget, QDialog) and widget.isVisible() for widget in QApplication.topLevelWidgets())
    QTest.keyClick(inline.control, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(inline.control, "Edited on page")
    assert inline.dirty and tab.dirty and not tab.editor.dirty
    assert window.tabs.tabText(window.tabs.indexOf(tab)).endswith(" *")
    QTest.keyClick(inline.control, Qt.Key.Key_Return)
    assert inline.control is None and inline.active
    assert "Edited on page" in tab.pdf.getAllText(0).text()
    assert "Second line" in tab.pdf.getAllText(0).text()
    assert text_pdf.read_bytes() == original
    window.undo()
    assert "Alpha beta PDF" in tab.pdf.getAllText(0).text()
    window.redo()
    assert "Edited on page" in tab.pdf.getAllText(0).text()


def test_ctrl_s_commits_the_focused_draft_before_save(window, tmp_path, monkeypatch):
    tab, inline = start(window)
    output = tmp_path / "edited-inline.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    QTest.keyClicks(inline.control, "Saved inline")
    QTest.keyClick(inline.control, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for(output.exists, timeout=10000)
    with PdfReader(output) as reader:
        assert "Saved inline" in reader.pages[0].extract_text()
        assert len(reader.pages) == 3
    assert not tab.dirty and inline.control is None


def test_invalid_draft_stays_visible_and_escape_discards(window):
    tab, inline = start(window)
    revision = tab.editor.path
    inline.control.setText("far too long " * 100)
    assert not inline.deactivate()
    assert inline.active and inline.control is not None and inline.feedback.text()
    assert tab.editor.path == revision
    window.a_edit_text.trigger()
    assert inline.active and window.a_edit_text.isChecked()
    assert not window._confirm_close(tab)
    QTest.keyClick(inline.control, Qt.Key.Key_Escape)
    assert inline.control is None and not tab.dirty and inline.active
    QTest.keyClick(tab.viewer, Qt.Key.Key_Escape)
    assert not inline.active and not window.a_edit_text.isChecked()


def test_clicking_another_span_commits_the_first_draft(window):
    tab, inline = start(window)
    inline.control.setText("First edited")
    point = tab.viewer.page_transform(0).map(QPointF(60, 128)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point)
    wait_for(lambda: inline.control is not None, timeout=10000)
    assert inline.control.text() == "Second line for selection"
    assert "First edited" in tab.pdf.getAllText(0).text()
    inline.control.setText("Second edited")
    assert inline.finish()
    text = tab.pdf.getAllText(0).text()
    assert "First edited" in text and "Second edited" in text


def test_zoom_and_scroll_keep_the_field_on_its_pdf_region(window):
    tab, inline = start(window)
    tab.set_zoom(250)
    tab.viewer.verticalScrollBar().setValue(70)
    rect = inline.control.geometry()
    left, top, _, _ = inline.target.display_bbox
    expected = tab.viewer.page_transform(0).map(QPointF(left, top))
    assert abs(rect.left() - (expected.x() - 3)) <= 1
    assert abs(rect.top() - (expected.y() - 2)) <= 1
    position = QPointF(inline.control.rect().center())
    wheel = QWheelEvent(position, QPointF(inline.control.mapToGlobal(position.toPoint())),
                        QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(inline.control, wheel)
    assert tab.viewer.zoomFactor() == pytest.approx(3.0)
    assert inline.control is not None and not inline.dirty


def test_cancel_ignores_a_late_target_result(window, monkeypatch):
    tab = window.current
    tab.inline_text.reflow.setChecked(False)
    target = tab.editor.text_at(0, (60, 95))
    release, returned = Event(), Event()
    def delayed(*args):
        assert release.wait(10)
        returned.set()
        return target
    monkeypatch.setattr(tab.editor, "text_at", delayed)
    inline = tab.inline_text
    try:
        window.edit_text_at(tab, 0, QPointF(60, 95))
        inline.cancel()
        release.set()
        wait_for(returned.is_set, timeout=10000)
        QApplication.processEvents()
        assert inline.control is None and inline.target is None and not tab.dirty
    finally:
        release.set()


def test_draft_is_per_tab_and_survives_switching(window, text_pdf, tmp_path):
    first, inline = start(window)
    inline.control.setText("Unapplied draft")
    other = tmp_path / "other.pdf"
    other.write_bytes(text_pdf.read_bytes())
    window.open_pdf(other)
    assert not window.a_edit_text.isChecked() and not window.current.inline_text.active
    assert first.dirty and not first.editor.dirty
    window.tabs.setCurrentWidget(first)
    assert window.a_edit_text.isChecked()
    assert inline.control.text() == "Unapplied draft" and inline.control.isVisible()
    inline.cancel()


def test_extract_includes_the_current_text_draft(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    tab, inline = start(window)
    inline.control.setText("Extracted draft")
    output = tmp_path / "extract-inline.pdf"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("1", True))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    window.extract_pages()
    with PdfReader(output) as reader:
        assert len(reader.pages) == 1 and "Extracted draft" in reader.pages[0].extract_text()
    assert inline.control is None and tab.editor.dirty


def test_page_box_requests_coalesce_while_scrolling(window, monkeypatch):
    tab = window.current
    inline = tab.inline_text
    inline.reflow.setChecked(False)
    release = Event()
    calls = []
    def boxes(page):
        calls.append(page)
        assert release.wait(10)
        return []
    monkeypatch.setattr(tab.editor, "text_boxes", boxes)
    try:
        inline.activate()
        wait_for(lambda: bool(calls))
        tab.viewer.pageNavigator().jump(1)
        tab.viewer.pageNavigator().jump(2)
        assert calls == [0]
        release.set()
        wait_for(lambda: 2 in inline.boxes)
        assert calls == [0, 2]
    finally:
        release.set()


@pytest.mark.parametrize("boxes", [
    [(0, 0, float('nan'), 10)], [(1, 1, 0, 0)], [(0, 0, 0, 10)],
    [(0, 0, 1)], ["bad"], [(0, 0, 1e20, 1)],
])
def test_worker_text_box_geometry_is_validated(boxes):
    with pytest.raises(ValueError):
        result('text_boxes', boxes)
