"""On-page signature placement, save/close drafts and mutually exclusive tools."""
import threading

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from pypdf import PdfReader

from test_gui import app, text_pdf, window, wait_for
from test_image_signature import signature_image
from ape_pdf_studio.core.errors import PDFOperationError


def choose(window, signature_image, monkeypatch, extension='png'):
    path = signature_image(extension)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    window.a_signature.trigger()
    controller = window.current.signature
    assert controller.active and controller.bar.isVisible()
    QTest.qWait(30)
    return controller


def gesture(controller, start, end):
    view = controller.view
    transform = view.page_transform(controller.page)
    start, end = transform.map(start).toPoint(), transform.map(end).toPoint()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)


def test_signature_motion_resize_and_cancel_are_local(window, signature_image, monkeypatch):
    tab = window.current
    revision = tab.editor.path
    controller = choose(window, signature_image, monkeypatch)
    assert tab.dirty and tab.editor.path == revision and window.a_undo.isEnabled()
    original_call = tab.editor.call
    calls = []
    main_thread = threading.get_ident()
    def monitored(method, *args):
        if threading.get_ident() == main_thread:
            calls.append(method)
        return original_call(method, *args)
    monkeypatch.setattr(tab.editor, 'call', monitored)
    original = QRectF(controller.rect)
    gesture(controller, original.center(), original.center()+QPointF(25, 30))
    assert controller.rect.x() == pytest.approx(original.x()+25, abs=2)
    assert controller.rect.y() == pytest.approx(original.y()+30, abs=2)
    width = controller.rect.width()
    corner = controller.rect.bottomRight()
    gesture(controller, corner, corner+QPointF(25, 12))
    assert controller.rect.width() > width+15
    assert controller.rect.width()/controller.rect.height() == pytest.approx(2)
    assert not calls and tab.editor.path == revision
    asset = controller.asset
    QTest.keyClick(tab.viewer, Qt.Key.Key_Escape)
    assert not controller.active and not controller.bar.isVisible()
    assert not asset.exists() and not tab.dirty
    assert not tab.editor._signatures


def test_save_commits_signature_and_reopen_has_permanent_content(window, signature_image, monkeypatch, tmp_path):
    tab = window.current
    controller = choose(window, signature_image, monkeypatch)
    controller.rect = QRectF(80, 180, 160, 80)
    target = tmp_path/'signature-saved.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    window.save_as()
    assert target.is_file() and not tab.dirty and not controller.active
    assert not tab.editor._signatures
    with PdfReader(target) as reader:
        assert not reader.pages[0].get('/Annots')
        assert reader.pages[0]['/Resources']['/XObject']
    rendered = tab.pdf.render(0, QSize(400, 600))
    assert rendered.pixelColor(160, 220).blue() > 240
    assert rendered.pixelColor(160, 220).red() < 15
    window.close_tab(window.tabs.currentIndex())
    window.open_pdf(target)
    reopened = window.current
    assert not reopened.signature.active
    rendered = reopened.pdf.render(0, QSize(400, 600))
    assert rendered.pixelColor(160, 220).red() < 15


def test_signature_apply_undo_redo_and_draft_undo(window, signature_image, monkeypatch):
    tab = window.current
    revision = tab.editor.path
    controller = choose(window, signature_image, monkeypatch)
    window.a_undo.trigger()
    assert not controller.active and tab.editor.path == revision
    controller = choose(window, signature_image, monkeypatch)
    controller.apply.click()
    assert tab.editor.path != revision and not controller.active
    accepted = tab.editor.path
    window.a_undo.trigger()
    assert tab.editor.path == revision
    window.a_redo.trigger()
    assert tab.editor.path == accepted


def test_signature_save_failure_keeps_draft_and_blocks_save(window, signature_image, monkeypatch, tmp_path):
    tab = window.current
    controller = choose(window, signature_image, monkeypatch)
    asset = controller.asset
    revision = tab.editor.path
    original = tab.editor.insert_signature
    monkeypatch.setattr(tab.editor, 'insert_signature', lambda *a: (_ for _ in ()).throw(PDFOperationError('Synthetic failure')))
    messages = []
    tab.message.connect(messages.append)
    target = tmp_path/'must-not-be-saved.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    assert not window._save_as_tab(tab)
    assert not target.exists()
    assert messages[-1] == 'Synthetic failure'
    assert controller.active and asset.exists() and tab.editor.path == revision and tab.dirty
    monkeypatch.setattr(tab.editor, 'insert_signature', original)
    assert tab.finish_form()
    assert not controller.active and not asset.exists()


@pytest.mark.parametrize('tool', ['ink', 'inline_text', 'form_designer'])
def test_signature_is_committed_before_switching_tools(window, signature_image, monkeypatch, tool):
    tab = window.current
    controller = choose(window, signature_image, monkeypatch)
    revision = tab.editor.path
    target = getattr(tab, tool)
    target.open() if tool == 'form_designer' else target.activate()
    assert not controller.active and tab.editor.path != revision


def test_close_saves_pending_signature_and_cancel_file_picker_keeps_draft(window, signature_image, monkeypatch, tmp_path):
    tab = window.current
    controller = choose(window, signature_image, monkeypatch)
    asset = controller.asset
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: ('', ''))
    assert not controller.choose()
    assert controller.asset == asset
    target = tmp_path/'closing-signature.pdf'
    # Route Save through Save As without ever changing the fixture input.
    monkeypatch.setattr(window, '_save_tab', lambda owner: owner.editor.save_as(target) is not None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Save)
    window.close_tab(window.tabs.currentIndex())
    assert target.exists() and window.current is None
    with PdfReader(target) as reader:
        assert reader.pages[0]['/Resources']['/XObject']


def test_signature_transfer_to_another_page_keeps_it_inside_bounds(window, signature_image, monkeypatch):
    tab = window.current
    controller = choose(window, signature_image, monkeypatch)
    tab.jump_to_page(1)
    QTest.qWait(30)
    point = tab.viewer.page_transform(1).map(QPointF(100, 150)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert controller.page == 1
    assert QRectF(0, 0, 400, 600).contains(controller.rect)
    assert controller.finish()
    with PdfReader(tab.editor.path) as reader:
        assert not reader.pages[0]['/Resources'].get('/XObject')
        assert reader.pages[1]['/Resources']['/XObject']


def test_signature_placement_preserves_middle_button_pan(window, signature_image, monkeypatch):
    tab = window.current
    controller = choose(window, signature_image, monkeypatch)
    tab.set_zoom(500)
    view = tab.viewer
    horizontal, vertical = view.horizontalScrollBar(), view.verticalScrollBar()
    horizontal.setValue(horizontal.maximum()//2)
    vertical.setValue(vertical.maximum()//3)
    before = horizontal.value(), vertical.value()
    rect = QRectF(controller.rect)
    QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=QPoint(200, 200))
    QTest.mouseMove(view.viewport(), QPoint(260, 240), 20)
    assert (horizontal.value(), vertical.value()) == (before[0]-60, before[1]-40)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=QPoint(260, 240))
    assert view._pan_position is None
    assert controller.active and controller.rect == rect
