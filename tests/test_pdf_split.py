"""Partition validation, real sandbox exports and cancellable split UI."""
from pathlib import Path

import pytest
from pypdf import PdfReader
from PySide6.QtWidgets import QDialog

from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.pdf_split import split_groups
from ape_pdf_studio.core.provenance import read_zone, propagate_zone
from ape_pdf_studio.ui.split_dialog import SplitDialog
from test_editor import pdf
from test_gui import app, text_pdf, window, wait_for
from test_pdf_forms import form_pdf


def test_even_groups_keep_the_remainder_and_single_pages():
    assert split_groups(5, size=2) == [[0, 1], [2, 3], [4]]
    assert split_groups(3) == [[0], [1], [2]]


def test_custom_groups_keep_requested_order():
    assert split_groups(5, ranges="3,1; 2; 4-5") == [[2, 0], [1], [3, 4]]


@pytest.mark.parametrize("options", [
    {"ranges": "1-2; 2-3"}, {"ranges": "1; 2"}, {"ranges": "1-4;5"},
    {"ranges": "1;;2-3"}, {"ranges": "3-1; 2"}, {"ranges": "1-3"},
    {"size": 3}, {"size": 0}, {"size": True},
])
def test_invalid_plans_are_rejected(options):
    with pytest.raises(PDFOperationError):
        split_groups(3, **options)


def test_split_current_revision_keeps_metadata_history_and_original(pdf, tmp_path):
    original = pdf.read_bytes()
    editor = BrokerEditor(pdf)
    try:
        editor.rotate(1, 90)
        revision = editor.path
        progress = []
        outputs = editor.split(split_groups(3, size=2), tmp_path, progress=progress.append)
        assert progress == [1, 2]
        with PdfReader(outputs[0]) as first, PdfReader(outputs[1]) as second:
            assert len(first.pages) == 2 and len(second.pages) == 1
            assert first.pages[1].rotation == 90
            assert float(second.pages[0].mediabox.width) == 440
            assert first.metadata.title == second.metadata.title == "Example"
        assert editor.path == revision and editor.dirty and editor.can_undo
        assert pdf.read_bytes() == original
        again = editor.split(split_groups(3), tmp_path)
        assert again[0].parent != outputs[0].parent
        assert all(path.exists() for path in outputs + again)
        editor.undo()
        assert not editor.dirty
    finally:
        editor.close()


def test_split_cancel_and_failure_remove_only_new_outputs(pdf, tmp_path, monkeypatch):
    editor = BrokerEditor(pdf)
    existing = tmp_path / "PDF_split"
    existing.mkdir()
    sentinel = existing / "part_0001.pdf"
    sentinel.write_bytes(b"keep me")
    try:
        progress = []
        outputs = editor.split(split_groups(3), tmp_path, progress=progress.append,
                               cancelled=lambda: bool(progress))
        assert outputs == [] and progress == [1]
        assert not (tmp_path / "PDF_split_2").exists()
        extract = editor.extract
        def failing(pages, destination):
            if Path(destination).name == "part_0002.pdf":
                raise PDFOperationError("simulated worker failure")
            return extract(pages, destination)
        monkeypatch.setattr(editor, "extract", failing)
        with pytest.raises(PDFOperationError, match="simulated"):
            editor.split(split_groups(3), tmp_path)
        assert not (tmp_path / "PDF_split_2").exists()
        assert sentinel.read_bytes() == b"keep me"
        assert not editor.dirty
    finally:
        editor.close()


def test_split_invalid_groups_create_no_folder(pdf, tmp_path):
    editor = BrokerEditor(pdf)
    try:
        for groups in ([[0], [0, 1, 2]], [[0], [1]], [[0, 1, 2]], [[True], [1, 2]]):
            with pytest.raises(PDFOperationError):
                editor.split(groups, tmp_path)
        assert not (tmp_path / "PDF_split").exists()
    finally:
        editor.close()


def test_split_preserves_internet_provenance(pdf, tmp_path):
    import os
    if os.name != "nt":
        pytest.skip("Windows Mark of the Web")
    propagate_zone(pdf, zone=3)
    editor = BrokerEditor(pdf)
    try:
        outputs = editor.split(split_groups(3), tmp_path)
        assert all(read_zone(path) == 3 for path in outputs)
    finally:
        editor.close()


def test_split_retains_fillable_fields(form_pdf, tmp_path):
    # The example has all widgets on page one; append a blank second page.
    from pypdf import PdfWriter
    source = tmp_path / "two-page-form.pdf"
    writer = PdfWriter(clone_from=form_pdf)
    writer.add_blank_page(width=595, height=842)
    writer.write(source)
    editor = BrokerEditor(source)
    try:
        editor.fill_form({"nome": "Ada"}, editor.path)
        outputs = editor.split(split_groups(2), tmp_path)
        with PdfReader(outputs[0]) as first, PdfReader(outputs[1]) as second:
            assert first.get_fields()["nome"]["/V"] == "Ada"
            assert not second.get_fields()
        # Ensure retained controls remain editable after reopening.
        reopened = BrokerEditor(outputs[0])
        try:
            reopened.fill_form({"nome": "Grace"}, reopened.path)
            assert {f.name: f.value for f in reopened.interactions()[1]}["nome"] == "Grace"
        finally:
            reopened.close()
    finally:
        editor.close()


def test_dialog_preview_validation_and_real_export(window, tmp_path):
    tab = window.current
    original = tab.editor.path
    dialog = SplitDialog(tab.editor, tab.count, window)
    dialog.directory.setText(str(tmp_path))
    dialog.show()
    try:
        assert "3 file" in dialog.preview.toPlainText()
        dialog.mode.setCurrentIndex(1)
        dialog.pages_per_file.setValue(2)
        assert "2 file" in dialog.preview.toPlainText()
        dialog.mode.setCurrentIndex(2)
        dialog.ranges.setText("1;2")
        assert not dialog.start_button.isEnabled()
        dialog.ranges.setText("1,3;2")
        assert dialog.start_button.isEnabled()
        dialog.start_button.click()
        wait_for(lambda: dialog._job is None, timeout=20000)
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert len(dialog.outputs) == 2
        with PdfReader(dialog.outputs[0]) as reader:
            assert "page 1" in reader.pages[0].extract_text()
            assert "page 3" in reader.pages[1].extract_text()
        assert tab.editor.path == original and not tab.editor.dirty
    finally:
        dialog.close()
        dialog.deleteLater()


def test_main_window_action_and_completion(window, tmp_path, monkeypatch):
    assert window.a_split.isEnabled()
    assert window.a_split in window.pages_button.menu().actions()
    def execute(dialog):
        dialog.directory.setText(str(tmp_path))
        dialog.start_button.click()
        wait_for(lambda: dialog._job is None, timeout=20000)
        return dialog.result()
    monkeypatch.setattr(SplitDialog, "exec", execute)
    window.a_split.trigger()
    assert window.toast.isVisible()
    assert window.toast.title.text() == "PDF diviso"
    assert len(list((tmp_path / "PDF_split").glob("*.pdf"))) == 3


def test_dialog_cancel_waits_for_worker_and_removes_partial_outputs(window, tmp_path, monkeypatch):
    from threading import Event
    first_exported, resume = Event(), Event()
    editor = window.current.editor
    extract = editor.extract
    def pause_after_first(pages, destination):
        output = extract(pages, destination)
        first_exported.set()
        assert resume.wait(10)
        return output
    monkeypatch.setattr(editor, "extract", pause_after_first)
    dialog = SplitDialog(editor, window.current.count, window)
    dialog.directory.setText(str(tmp_path))
    dialog.show()
    try:
        dialog.start_button.click()
        wait_for(first_exported.is_set, timeout=10000)
        dialog.reject()
        assert dialog.isVisible() and dialog._job is not None
        assert not dialog.cancel_button.isEnabled()
        resume.set()
        wait_for(lambda: dialog._job is None, timeout=10000)
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert not dialog.outputs and not (tmp_path / "PDF_split").exists()
    finally:
        resume.set()
        if dialog._job:
            dialog._job.wait(15000)
        dialog.close()
        dialog.deleteLater()
