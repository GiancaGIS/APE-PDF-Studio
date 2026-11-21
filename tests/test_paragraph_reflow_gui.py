"""Use the default multiline editor and save actual wrapped PDF text."""
from pathlib import Path

from pypdf import PdfReader
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from ape_pdf_studio.ui.inline_text import ParagraphEdit
from test_gui import app, text_pdf, window, wait_for
from test_paragraph_reflow import paragraph_pdf


def open_paragraph(window, paragraph_pdf):
    window.open_pdf(paragraph_pdf)
    tab = window.current
    assert tab.inline_text.reflow.isChecked()
    window.edit_text_at(tab, 0, QPointF(60, 90))
    wait_for(lambda: tab.inline_text.control is not None, timeout=10000)
    assert isinstance(tab.inline_text.control, ParagraphEdit)
    return tab, tab.inline_text


def test_enter_adds_line_ctrl_enter_applies_and_history(window, paragraph_pdf):
    tab, inline = open_paragraph(window, paragraph_pdf)
    inline.control.selectAll()
    QTest.keyClicks(inline.control, 'First paragraph line')
    QTest.keyClick(inline.control, Qt.Key.Key_Return)
    QTest.keyClicks(inline.control, 'Second explicit line with extra words for automatic wrapping.')
    assert '\n' in inline.control.text() and not tab.editor.dirty
    QTest.keyClick(inline.control, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    wait_for(lambda: not inline.committing, timeout=10000)
    assert inline.control is None and tab.editor.dirty
    assert 'First paragraph line' in tab.pdf.getAllText(0).text()
    window.undo()
    assert 'Alpha paragraph' in tab.pdf.getAllText(0).text()
    window.redo()
    assert 'First paragraph line' in tab.pdf.getAllText(0).text()


def test_reflow_preview_grows_and_ctrl_s_saves_the_draft(window, paragraph_pdf, tmp_path, monkeypatch):
    tab, inline = open_paragraph(window, paragraph_pdf)
    inline.control.setText('Expanded paragraph ' + 'new words with useful information. ' * 7)
    wait_for(lambda: inline._layout_key() in inline._font_cache and not inline._font_running, timeout=15000)
    assert inline._preview_bbox is not None and not inline.feedback.isVisible()
    assert inline.control.height() >= 48
    output = tmp_path / 'reflowed.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    QTest.keyClick(inline.control, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for(output.exists, timeout=10000)
    with PdfReader(output) as reader:
        assert 'Expanded paragraph' in reader.pages[0].extract_text()
        assert 'Next paragraph stays' in reader.pages[0].extract_text()
    assert not tab.dirty


def test_overflow_keeps_draft_and_mode_then_escape_cancels(window, paragraph_pdf):
    tab, inline = open_paragraph(window, paragraph_pdf)
    revision = tab.editor.path
    text = 'This cannot fit in the box. ' * 250
    inline.control.setText(text)
    wait_for(lambda: inline.feedback.isVisible(), timeout=15000)
    assert not inline.finish()
    wait_for(lambda: not inline.committing, timeout=10000)
    assert inline.control.text() == text and tab.editor.path == revision
    inline.reflow.setChecked(False)
    wait_for(lambda: not inline.committing, timeout=10000)
    assert inline.reflow.isChecked() and inline.control.text() == text
    QTest.keyClick(inline.control, Qt.Key.Key_Escape)
    assert inline.control is None and not tab.dirty


def test_alignment_and_size_are_part_of_the_draft(window, paragraph_pdf):
    tab, inline = open_paragraph(window, paragraph_pdf)
    inline.alignment.setCurrentIndex(1)
    inline.size.setValue(11)
    assert inline.dirty and tab.dirty
    assert not inline.finish()
    wait_for(lambda: not inline.committing, timeout=10000)
    assert tab.editor.dirty
    inline.reflow.setChecked(False)
    assert not inline.reflow.isChecked()


def test_preview_of_scoped_format_preserves_accent_and_nonbreaking_space(window, paragraph_pdf):
    tab, inline = open_paragraph(window, paragraph_pdf)
    text = 'Left Cafe\u0301\u00a0gauche right'
    inline.control.setText(text)
    assert inline.control.text() == text
    cursor = inline.control.textCursor()
    cursor.setPosition(len('Left '))
    cursor.setPosition(len('Left Cafe\u0301'), QTextCursor.MoveMode.KeepAnchor)
    inline.control.setTextCursor(cursor)
    inline.format_button.click()
    inline.format_selection.click()
    inline.font_style.setCurrentIndex(2)
    inline.preview.click()
    wait_for(lambda: inline._preview_image is not None, timeout=10000)
    assert inline.control.isHidden()
    assert not inline._preview_image[1].isNull()
    info, _ = inline._font_cache[inline._layout_key()]
    normal, emphasis, remainder = info['runs']
    assert normal[0] == 'Left ' and emphasis[0] == 'Café'
    assert 'Bold' in emphasis[4] and 'Bold' not in normal[4]
    assert remainder[0].startswith('\u00a0gauche')
    inline.apply.click()
    wait_for(lambda: not inline.committing, timeout=10000)
    assert inline.control is None, inline.feedback.text()
    assert 'Café' in tab.pdf.getAllText(0).text()


def test_keyboard_can_select_text_box(window):
    tab = window.current
    inline = tab.inline_text
    inline.reflow.setChecked(False)
    window.a_edit_text.trigger()
    wait_for(lambda: len(inline.boxes.get(0, ())) >= 2, timeout=10000)
    QTest.keyClick(tab.viewer, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    wait_for(lambda: inline.control is not None, timeout=10000)
    assert 'Alpha beta PDF' in inline.control.text()
    QTest.keyClick(inline.control, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    wait_for(lambda: inline.control is not None and 'Second line' in inline.control.text(), timeout=10000)
