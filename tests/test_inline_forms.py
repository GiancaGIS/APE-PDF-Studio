"""Type and choose directly in the real PDF viewport; drafts cannot be lost on save."""
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLineEdit, QPlainTextEdit, QMessageBox

from test_gui import app, text_pdf, window, wait_for
from test_pdf_forms import form_pdf
from ape_pdf_studio.core.errors import PDFOperationError


def open_form(window, path):
    window.open_pdf(path)
    tab = window.current
    wait_for(lambda: tab.count > 0)
    QTest.qWait(80)
    return tab


def click_field(tab, name, index=0):
    field = next(field for field in tab.form_fields if field.name == name)
    page, rect = field.widgets[index]
    tab.jump_to_page(page)
    QTest.qWait(20)
    position = tab.viewer.page_transform(page).map(QPointF((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=position)


def values(tab):
    return {field.name: field.value for field in tab.form_fields}


def test_click_type_tab_and_save_without_dialog(window, form_pdf, tmp_path, monkeypatch):
    tab = open_form(window, form_pdf)
    original = form_pdf.read_bytes()
    click_field(tab, "nome")
    control = tab.inline_form.control
    assert isinstance(control, QLineEdit) and control.parentWidget() is tab.viewer.viewport()
    assert not any(isinstance(widget, QDialog) and widget.isVisible() for widget in QApplication.topLevelWidgets())
    QTest.keyClicks(control, "Grace Hopper")
    assert tab.dirty and not tab.editor.dirty
    assert window.tabs.tabText(window.tabs.indexOf(tab)).endswith(" *")
    QTest.keyClick(control, Qt.Key.Key_Tab)
    assert values(tab)["nome"] == "Grace Hopper"
    assert tab.inline_form.key == ("email", 0)
    QTest.keyClicks(tab.inline_form.control, "grace@example.com")
    output = tmp_path / "inline-filled.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    # Ctrl+S must flush the focused field, without first clicking elsewhere.
    QTest.keyClick(tab.inline_form.control, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for(output.exists)
    with PdfReader(output) as reader:
        assert reader.get_fields()["nome"]["/V"] == "Grace Hopper"
        assert reader.get_fields()["email"]["/V"] == "grace@example.com"
        assert len(reader.get_fields()) == 8
    assert not tab.dirty and form_pdf.read_bytes() == original
    window.undo()
    assert values(tab)["email"] == "" and values(tab)["nome"] == "Grace Hopper"
    window.redo()
    assert values(tab)["email"] == "grace@example.com"


def test_checkbox_and_specific_radio_widget_change_on_page(window, form_pdf):
    tab = open_form(window, form_pdf)
    click_field(tab, "consenso")
    assert values(tab)["consenso"] == "/Yes" and tab.inline_form.control is None
    click_field(tab, "consenso")
    assert values(tab)["consenso"] == "/Off"
    click_field(tab, "contatto", 1)
    assert values(tab)["contatto"] == "/Telefono"
    click_field(tab, "contatto", 0)
    assert values(tab)["contatto"] == "/Email"
    revision = tab.editor.path
    click_field(tab, "contatto", 0)
    assert tab.editor.path == revision
    window.undo()
    assert values(tab)["contatto"] == "/Telefono"
    revision = tab.editor.path
    tab.inline_form.start("contatto", index=0)
    assert not tab.inline_form.dirty
    assert tab.finish_form() and tab.editor.path == revision


def test_choice_list_multiline_and_escape(window, form_pdf):
    tab = open_form(window, form_pdf)
    click_field(tab, "categoria")
    control = tab.inline_form.control
    control.setCurrentIndex(control.findData("assistenza"))
    QTest.keyClick(control, Qt.Key.Key_Tab)
    assert values(tab)["categoria"] == "assistenza"
    click_field(tab, "interessi")
    control = tab.inline_form.control
    control.item(1).setSelected(True)
    control.item(2).setSelected(True)
    assert tab.finish_form()
    assert values(tab)["interessi"] == ("moduli", "ocr")
    click_field(tab, "note")
    control = tab.inline_form.control
    assert isinstance(control, QPlainTextEdit)
    QTest.keyClicks(control, "First line")
    QTest.keyClick(control, Qt.Key.Key_Return)
    QTest.keyClicks(control, "Second line")
    assert tab.inline_form.control is control
    QTest.keyClick(control, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert values(tab)["note"] == "First line\nSecond line"
    click_field(tab, "nome")
    QTest.keyClicks(tab.inline_form.control, "Discard this draft")
    revision = tab.editor.path
    QTest.keyClick(tab.inline_form.control, Qt.Key.Key_Escape)
    assert values(tab)["nome"] == "" and tab.editor.path == revision
    assert tab.inline_form.control is None


def test_dropdown_popup_selects_in_place_without_losing_selection(window, form_pdf):
    tab = open_form(window, form_pdf)
    click_field(tab, "categoria")
    control = tab.inline_form.control
    control.showPopup()
    QTest.qWait(30)
    view = control.view()
    index = view.model().index(control.findData("assistenza"), 0)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
    wait_for(lambda: tab.inline_form.control is None)
    assert values(tab)["categoria"] == "assistenza"


def test_fields_on_other_pages_and_repeated_widgets_share_value(window, form_pdf, tmp_path):
    writer = PdfWriter()
    writer.append(form_pdf)
    writer.append(form_pdf)
    path = tmp_path / "multipage-form.pdf"
    writer.write(path)
    tab = open_form(window, path)
    assert tab.count == 2
    tab.inline_form.start("email", index=1)
    assert tab.viewer.pageNavigator().currentPage() == 1
    control = tab.inline_form.control
    assert control.geometry().intersects(tab.viewer.viewport().rect())
    QTest.keyClicks(control, "shared@example.com")
    QTest.keyClick(control, Qt.Key.Key_Tab)
    assert values(tab)["email"] == "shared@example.com"
    assert tab.viewer.pageNavigator().currentPage() == 1
    with PdfReader(tab.editor.path) as reader:
        for page in reader.pages:
            email = next(ref.get_object() for ref in page["/Annots"] if ref.get_object().get("/T") == "email")
            assert email["/V"] == "shared@example.com"
    tab.inline_form.cancel()


def test_geometry_tracks_zoom_scroll_resize_and_preserves_draft(window, form_pdf):
    tab = open_form(window, form_pdf)
    click_field(tab, "nome")
    control = tab.inline_form.control
    QTest.keyClicks(control, "Draft survives layout")
    for zoom in (100, 200, 70):
        tab.set_zoom(zoom)
        window.resize(1000 + zoom, 720 + zoom)
        QTest.qWait(35)
        field = next(field for field in tab.form_fields if field.name == "nome")
        page, rect = field.widgets[0]
        expected = tab.viewer.page_transform(page).mapRect(QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])).toAlignedRect()
        assert control.geometry() == expected
        assert control.text() == "Draft survives layout"
    tab.viewer.verticalScrollBar().setValue(30)
    field = next(field for field in tab.form_fields if field.name == "nome")
    rect = field.widgets[0][1]
    expected = tab.viewer.page_transform(0).mapRect(QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])).toAlignedRect()
    assert control.geometry() == expected
    assert tab.finish_form() and values(tab)["nome"] == "Draft survives layout"


def test_focus_loss_commits_and_shortcut_starts_on_page(window, form_pdf):
    tab = open_form(window, form_pdf)
    window.a_form.trigger()
    assert tab.inline_form.key == ("nome", 0)
    QTest.keyClicks(tab.inline_form.control, "Applied on blur")
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    wait_for(lambda: tab.inline_form.control is None)
    assert values(tab)["nome"] == "Applied on blur"
    window.a_form.trigger()
    QTest.keyClick(tab.inline_form.control, Qt.Key.Key_Backtab)
    assert tab.inline_form.key == ("consenso", 0)
    tab.inline_form.cancel()


def test_validation_or_write_failure_preserves_draft_and_blocks_save(window, form_pdf, tmp_path, monkeypatch):
    tab = open_form(window, form_pdf)
    click_field(tab, "note")
    tab.inline_form.control.setPlainText("x" * 2001)
    assert not tab.finish_form() and not tab.editor.dirty
    assert tab.inline_form.control.toPlainText() == "x" * 2001
    assert not window._confirm_close(tab)
    tab.inline_form.control.setPlainText("Retain this if writing fails")

    def fail(*args):
        raise PDFOperationError("Synthetic write failure")

    monkeypatch.setattr(tab.inline_form, "save", fail)
    assert not window._save_as_tab(tab)
    assert tab.inline_form.control.toPlainText() == "Retain this if writing fails"
    assert not tab.editor.dirty
    tab.inline_form.cancel()


def test_readonly_fields_and_protected_pdf_never_create_controls(window, form_pdf, tmp_path):
    tab = open_form(window, form_pdf)
    click_field(tab, "riferimento")
    assert tab.inline_form.control is None and not tab.editor.dirty
    writer = PdfWriter(clone_from=form_pdf)
    writer.encrypt(user_password="", owner_password="owner", algorithm="AES-256")
    source = tmp_path / "protected.pdf"
    writer.write(source)
    locked = open_form(window, source)
    click_field(locked, "nome")
    assert locked.inline_form.control is None and not locked.editor.dirty


def test_tab_change_and_close_cannot_lose_focused_input(window, form_pdf, monkeypatch):
    tab = open_form(window, form_pdf)
    click_field(tab, "nome")
    QTest.keyClicks(tab.inline_form.control, "Before changing tabs")
    window.tabs.setCurrentIndex(0)
    QTest.qWait(35)
    assert values(tab)["nome"] == "Before changing tabs"
    window.tabs.setCurrentWidget(tab)
    click_field(tab, "email")
    QTest.keyClicks(tab.inline_form.control, "before-close@example.com")
    prompted = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompted.append(args[2]) or QMessageBox.StandardButton.Cancel)
    assert not window._confirm_close(tab)
    assert prompted and values(tab)["email"] == "before-close@example.com"
