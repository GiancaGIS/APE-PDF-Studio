"""Real form controls and document-scoped link permission, without external I/O."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from test_gui import app, text_pdf, window, wait_for
from test_pdf_forms import form_pdf


def test_form_controls_apply_undo_save_and_page_click(window, form_pdf, tmp_path, monkeypatch):
    window.open_pdf(form_pdf)
    tab = window.current
    wait_for(lambda: tab.count == 1)
    assert window.a_form.isEnabled() and tab.notice.form_button.isVisible()

    inline = tab.inline_form
    inline.start("nome")
    QTest.keyClicks(inline.control, "Ada Lovelace")
    inline.start("categoria")
    inline.control.setCurrentIndex(inline.control.findData("assistenza"))
    inline.start("interessi")
    inline.control.item(1).setSelected(True)
    inline.start("contatto", index=0, clicked=True)
    inline.start("consenso", clicked=True)
    inline.start("note")
    inline.control.setPlainText("A note\nAnother line")
    assert inline.finish()
    assert tab.editor.dirty and tab.editor.can_undo
    values = {field.name: field.value for field in tab.form_fields}
    assert values["nome"] == "Ada Lovelace" and values["interessi"] == ("moduli",)
    assert values["contatto"] == "/Email" and values["consenso"] == "/Yes"
    revision = tab.editor.path
    assert inline.finish()
    assert tab.editor.path == revision
    tab.editor.save_as(tmp_path / "saved.pdf")
    assert not tab.editor.dirty
    window.undo()
    assert {field.name: field.value for field in tab.form_fields}["note"] == ""
    assert {field.name: field.value for field in tab.form_fields}["nome"] == "Ada Lovelace"
    window.redo()
    assert {field.name: field.value for field in tab.form_fields}["nome"] == "Ada Lovelace"
    QTest.qWait(80)
    rect = tab.form_fields[0].widgets[0][1]
    position = tab.viewer.page_transform(0).map(QPointF((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert inline.key == ("nome", 0)
    assert inline.control.parentWidget() is tab.viewer.viewport()


def test_form_cancel_and_stale_field_preserve_values(window, form_pdf):
    window.open_pdf(form_pdf)
    tab = window.current

    inline = tab.inline_form
    inline.start("nome")
    inline.control.setText("Not applied")
    inline.cancel()
    assert not tab.editor.dirty
    inline.start("nome")
    inline.control.setText("Stale")
    tab.editor.fill_form({"nome": "Another revision"}, tab.editor.path)
    assert not inline.finish()
    inline.cancel()
    tab._reload(0)
    assert {field.name: field.value for field in tab.form_fields}["nome"] == "Another revision"


def test_links_require_enable_and_each_destination_confirmation(window, form_pdf, monkeypatch):
    window.open_pdf(form_pdf)
    tab = window.current
    target = tab.links[0][0]["url"]
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(bytes(url.toEncoded()).decode()) or True)
    answers, prompts = [False], []

    def confirm(title, message):
        prompts.append((title, message))
        return answers.pop(0)

    monkeypatch.setattr(window, "_confirm_pdf_content", confirm)
    window.open_pdf_link(tab, target)
    assert not tab.links_enabled and not opened and len(prompts) == 1
    answers.extend([True, False])
    window.open_pdf_link(tab, target)
    assert tab.links_enabled and not opened
    assert target in prompts[-1][1] and "conferma a ogni apertura" in tab.notice.text.text()
    answers.append(True)
    window.open_pdf_link(tab, target)
    assert opened == [target]
    answers.append(False)
    window.open_pdf_link(tab, target)
    assert opened == [target]
    before = len(prompts)
    window.open_pdf_link(tab, "file:///C:/test.exe")
    window.open_pdf_link(tab, "https://not-in-document.example/")
    assert len(prompts) == before and opened == [target]
    window.toggle_pdf_links(tab)
    assert not tab.links_enabled
    window.tabs.setCurrentIndex(0)
    window.open_pdf_link(tab, target)
    assert len(prompts) == before


def test_permission_never_persists_or_crosses_tabs(window, form_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(window, "_confirm_pdf_content", lambda *args: True)
    window.open_pdf(form_pdf)
    first = window.current
    window.toggle_pdf_links(first)
    other = tmp_path / "other.pdf"
    other.write_bytes(form_pdf.read_bytes())
    window.open_pdf(other)
    assert not window.current.links_enabled and first.links_enabled
    window.close_current()
    window.close_current()
    window.open_pdf(form_pdf)
    assert not window.current.links_enabled


def test_confirmation_plain_text_defaults_to_no(window, monkeypatch):
    inspected = []

    def execute(dialog):
        inspected.append((dialog.textFormat(), dialog.defaultButton(), dialog.text()))
        assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.No)
        assert dialog.escapeButton() == dialog.button(QMessageBox.StandardButton.No)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", execute)
    assert not window._confirm_pdf_content("PDF", "https://example.com/<img src=x>")
    assert inspected[0][0] == Qt.TextFormat.PlainText


def test_page_link_click_routes_to_confirmation(window, form_pdf, monkeypatch):
    window.open_pdf(form_pdf)
    tab = window.current
    QTest.qWait(80)
    received = []
    monkeypatch.setattr(window, "open_pdf_link", lambda owner, url: received.append((owner, url)))
    rect = tab.links[0][0]["rect"]
    position = tab.viewer.page_transform(0).map(QPointF((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)).toPoint()
    QTest.mouseClick(tab.viewer.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert received == [(tab, tab.links[0][0]["url"])]
