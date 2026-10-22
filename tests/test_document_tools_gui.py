"""Real mouse drawing, persistence, deletion and document tool dialogs."""
import threading

from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtPdf import QPdfDocumentRenderOptions
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog

from test_gui import app, text_pdf, window, wait_for
from ape_pdf_studio.ui.document_tools import BlankPageDialog, WatermarkDialog


def draw(tab, points):
    viewport = tab.viewer.viewport()
    mapped = [tab.viewer.page_transform(0).map(QPointF(*p)).toPoint() for p in points]
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=mapped[0])
    for point in mapped[1:]:
        QTest.mouseMove(viewport, point)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=mapped[-1])


def test_pen_motion_uses_no_synchronous_rpc_and_local_undo_delete(window, monkeypatch):
    tab = window.current
    window.a_draw.trigger()
    wait_for(lambda: tab.ink.bar.isVisible())
    QTest.qWait(30)
    assert tab.ink.active and window.a_draw.isChecked()
    original = tab.editor.path
    original_call = tab.editor.call
    ui_calls = []
    gui_thread = threading.get_ident()

    def monitored(method, *args):
        if threading.get_ident() == gui_thread:
            ui_calls.append(method)
        return original_call(method, *args)

    monkeypatch.setattr(tab.editor, 'call', monitored)
    draw(tab, [(80, 160), (110, 170), (150, 165), (190, 185)])
    assert not ui_calls
    assert len(tab.ink.pending) == 1 and tab.dirty
    assert tab.editor.path == original
    assert window.a_undo.isEnabled()
    window.a_undo.trigger()
    assert not tab.ink.pending and not tab.dirty
    assert window.a_redo.isEnabled()
    window.a_redo.trigger()
    assert len(tab.ink.pending) == 1
    tab.ink.mode.setCurrentIndex(1)
    point = tab.viewer.page_transform(0).map(QPointF(80, 160)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert tab.ink.selected is tab.ink.pending[0]
    tab.ink.delete_button.click()
    assert not tab.ink.pending and tab.editor.path == original
    window.a_undo.trigger()
    assert len(tab.ink.pending) == 1
    assert not ui_calls


def test_save_reopen_select_and_delete_persisted_ink(window, tmp_path, monkeypatch):
    tab = window.current
    tab.ink.activate()
    QTest.qWait(30)
    tab.ink.width.setValue(6)
    tab.ink.opacity.setValue(60)
    draw(tab, [(80, 160), (110, 190), (150, 165), (190, 185)])
    target = tmp_path / 'drawings.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), ''))
    window.save_as()
    assert target.is_file() and not tab.dirty and not tab.ink.pending
    assert len(tab.editor.ink()['rows']) == 1
    options = QPdfDocumentRenderOptions()
    options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
    rendered = tab.pdf.render(0, QSize(400, 600), options)
    color = rendered.pixelColor(80, 160)
    assert color.blue() > color.red() + 25, 'Saved ink must be visible in the Qt renderer'
    window.close_tab(window.tabs.currentIndex())
    window.open_pdf(target)
    reopened = window.current
    assert reopened is not tab
    reopened.ink.activate()
    reopened.ink.mode.setCurrentIndex(1)
    QTest.qWait(30)
    point = reopened.viewer.page_transform(0).map(QPointF(80, 160)).toPoint()
    QTest.mouseClick(reopened.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert reopened.ink.selected is not None
    QTest.keyClick(reopened.viewer, Qt.Key.Key_Delete)
    assert not reopened.editor.ink()['rows']
    assert reopened.dirty
    window.a_undo.trigger()
    assert len(reopened.editor.ink()['rows']) == 1
    assert reopened.ink.deactivate()
    assert not window.a_draw.isChecked()


def test_release_position_and_duplicate_strokes_keep_individual_history(window):
    tab = window.current
    tab.ink.activate()
    QTest.qWait(30)
    viewport = tab.viewer.viewport()
    start = tab.viewer.page_transform(0).map(QPointF(80, 160)).toPoint()
    end = tab.viewer.page_transform(0).map(QPointF(180, 180)).toPoint()
    # A fast release may arrive without an intervening mouse move event.
    for _ in range(2):
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    first, second = tab.ink.pending
    assert len(first['points']) == 2 and len(second['points']) == 2
    tab.undo()
    assert tab.ink.pending[0] is first
    tab.redo()
    assert tab.ink.pending[1] is second


def test_blank_page_and_watermark_actions(window, monkeypatch):
    tab = window.current

    def insert_dialog(dialog):
        dialog.page.setValue(2)
        dialog.position.setCurrentIndex(1)
        dialog.paper.setCurrentIndex(2)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BlankPageDialog, 'exec', insert_dialog)
    window.a_blank.trigger()
    assert tab.count == 4 and tab.page == 1
    size = tab.pdf.pagePointSize(1)
    assert size.width() > size.height()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    def watermark_dialog(dialog):
        dialog.text.setText('COPIA TEST')
        dialog.opacity.setValue(35)
        dialog.angle.setValue(-30)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(WatermarkDialog, 'exec', watermark_dialog)
    window.a_watermark.trigger()
    timer.stop()
    assert ticks, 'The GUI event loop must run during the document edit'
    assert all('COPIA TEST' in tab.pdf.getAllText(page).text() for page in range(tab.count))
    window.a_undo.trigger()
    assert all('COPIA TEST' not in tab.pdf.getAllText(page).text() for page in range(tab.count))


def test_drawing_and_other_editing_tools_are_exclusive(window):
    tab = window.current
    assert tab.ink.activate()
    QTest.qWait(30)
    draw(tab, [(80, 160), (110, 190)])
    assert tab.inline_text.activate()
    assert not tab.ink.active and not tab.ink.pending
    assert len(tab.ink.records) == 1 and tab.inline_text.active
    assert tab.ink.activate()
    assert not tab.inline_text.active
    tab.form_designer.open()
    assert not tab.ink.active and tab.form_designer.isVisible()
