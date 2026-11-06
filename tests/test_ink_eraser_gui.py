"""Live partial erasing, rich controls, preservation and zero gesture-time RPC."""
import threading
import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject

from test_gui import app, text_pdf, window, wait_for, wait_for_tiles
from test_document_tools_gui import draw


def point(tab, x, y):
    return tab.viewer.page_transform(0).map(QPointF(x, y)).toPoint()


def test_toolbar_palette_preview_and_independent_tool_settings(window):
    ink = window.current.ink
    window.a_draw.trigger()
    bar = ink.bar
    assert bar.isVisible() and len(bar.swatches) == 30
    ink.set_tool('pencil')
    assert ink.width.value() == 1.2 and ink.opacity.value() == 75
    ink.width.setValue(2.5)
    bar.set_color(QColor('#ed1515'))
    ink.set_tool('marker')
    assert ink.width.value() == 3 and ink.opacity.value() == 100
    ink.set_tool('pencil')
    assert ink.width.value() == 2.5 and ink.color == QColor('#ed1515')
    bar.menu.popup(bar.settings_button.mapToGlobal(QPoint(0, bar.settings_button.height())))
    QTest.qWait(30)
    assert bar.menu.isVisible() and bar.preview.isVisible()
    QTest.mouseClick(bar.swatches[18], Qt.MouseButton.LeftButton)
    assert ink.color == bar.swatches[18].color
    bar.width_slider.setValue(42)
    assert ink.width.value() == 4.2
    bar.menu.hide()
    ink.set_tool('highlighter')
    assert ink.opacity.value() == 35 and ink.width.value() == 12
    ink.set_tool('eraser')
    assert ink.width.value() == 16 and ink.width.maximum() == 60
    assert not bar.colors.isVisible() and not bar.opacity_area.isVisible()
    assert bar.width_label.text() == 'Dimensione gomma'


def test_settings_popup_scrolls_instead_of_clipping_on_small_screens(window, monkeypatch):
    from types import SimpleNamespace
    ink = window.current.ink
    ink.activate()
    bar = ink.bar
    monkeypatch.setattr(bar.settings_button, 'screen', lambda: SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 400, 300)))
    bar.menu.popup(bar.settings_button.mapToGlobal(QPoint(0, bar.settings_button.height())))
    QTest.qWait(30)
    assert bar.scroll.height() <= 270
    assert bar.scroll.verticalScrollBar().maximum() > 0
    bar.scroll.ensureWidgetVisible(bar.opacity)
    app = __import__('PySide6.QtWidgets', fromlist=['QApplication']).QApplication.instance()
    app.processEvents()
    top = bar.opacity.mapTo(bar.scroll.viewport(), QPoint())
    assert bar.scroll.viewport().rect().intersects(QRect(top, bar.opacity.size()))
    bar.menu.hide()


def test_precise_draft_eraser_is_live_and_one_undoable_gesture(window, monkeypatch):
    tab = window.current
    assert tab.ink.activate()
    QTest.qWait(40)
    draw(tab, [(80, 180), (280, 180)])
    original = tab.ink.pending[0]
    revision = tab.editor.path
    tab.ink.set_tool('eraser')
    gui_thread = threading.get_ident()
    original_call, calls = tab.editor.call, []
    def watched(method, *args):
        if threading.get_ident() == gui_thread:
            calls.append(method)
        return original_call(method, *args)
    monkeypatch.setattr(tab.editor, 'call', watched)
    target = point(tab, 180, 180)
    QTest.mousePress(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert len(tab.ink.pending) == 2, 'The cut must be visible before mouse release'
    QTest.mouseRelease(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert len(tab.ink.pending) == 2 and not calls
    assert max(p[0] for p in tab.ink.pending[0]['points']) < 172
    assert min(p[0] for p in tab.ink.pending[1]['points']) > 188
    assert tab.editor.path == revision
    tab.undo()
    assert len(tab.ink.pending) == 1 and tab.ink.pending[0] is original
    tab.redo()
    assert len(tab.ink.pending) == 2
    before = list(tab.ink.pending)
    QTest.mousePress(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point(tab, 100, 180))
    QTest.keyClick(tab.viewer, Qt.Key.Key_Escape)
    QTest.mouseRelease(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point(tab, 100, 180))
    assert tab.ink.pending == before and not calls


@pytest.mark.parametrize('unit', (1, 2))
def test_saved_stroke_partial_erase_and_background_survive_save_reopen(window, tmp_path, monkeypatch, unit):
    import pymupdf
    source = tmp_path / 'background.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=600)
        page.draw_rect(page.rect, fill=(.55, .85, .6), color=None)
        document.save(source)
    if unit != 1:
        writer = PdfWriter(clone_from=source)
        writer.pages[0].cropbox = RectangleObject((20, 30, 380, 550))
        writer.pages[0][NameObject('/UserUnit')] = NumberObject(unit)
        writer.write(tmp_path / 'scaled.pdf')
        source = tmp_path / 'scaled.pdf'
    original = source.read_bytes()
    window.open_pdf(source)
    tab = window.current
    tab.apply_edit('update_ink', [dict(page=0, points=[(80, 180), (280, 180)],
        color=[0, 0, 1], width=8, opacity=1, smooth=False)], [], str(tab.editor.path))
    assert tab.ink.activate()
    wait_for_tiles(tab.viewer)
    QTest.qWait(30)
    center = point(tab, 180, 180)
    color = tab.viewer.viewport().grab().toImage().pixelColor(center)
    assert color.blue() > color.red() + 50, 'Editable overlay must match the saved physical coordinates'
    tab.ink.set_tool('eraser')
    target = point(tab, 180, 180)
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert len(tab.ink.visible_records()[0]['paths']) == 2 and tab.ink.edits
    QTest.qWait(20)
    color = tab.viewer.viewport().grab().toImage().pixelColor(target)
    assert color.green() > color.blue() + 20, 'Erasing must reveal the original colored background'
    saved = tmp_path / 'saved.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(saved), ''))
    window.save_as()
    assert not tab.dirty and len(tab.editor.ink()['rows'][0]['paths']) == 2
    window.close_tab(window.tabs.indexOf(tab))
    window.open_pdf(saved)
    reopened = window.current
    assert reopened is not tab
    assert len(reopened.ink.records[0]['paths']) == 2
    assert source.read_bytes() == original


def test_erasing_saved_ink_has_no_synchronous_rpc_and_preserves_drafts_on_error(window, monkeypatch):
    from ape_pdf_studio.core.errors import PDFOperationError
    tab = window.current
    tab.ink.activate()
    QTest.qWait(40)
    draw(tab, [(80, 180), (280, 180)])
    assert tab.ink.finish()
    tab.ink.set_tool('eraser')
    gui_thread, calls = threading.get_ident(), []
    original_call = tab.editor.call
    def watched(method, *args):
        if threading.get_ident() == gui_thread:
            calls.append(method)
        return original_call(method, *args)
    monkeypatch.setattr(tab.editor, 'call', watched)
    revision = tab.editor.path
    start, end = point(tab, 180, 150), point(tab, 180, 210)
    QTest.mousePress(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(tab.viewer.viewport(), end)
    QTest.mouseRelease(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert tab.ink.edits and not calls and tab.editor.path == revision
    monkeypatch.setattr(tab.editor, 'update_ink', lambda *args: (_ for _ in ()).throw(PDFOperationError('synthetic failure')))
    assert not tab.ink.deactivate()
    assert tab.ink.active and tab.ink.edits and tab.dirty
