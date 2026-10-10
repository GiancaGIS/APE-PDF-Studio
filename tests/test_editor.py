from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError, parse_page_range


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    destination = tmp_path / "source.pdf"
    writer = PdfWriter()
    for number in range(3):
        page = writer.add_blank_page(width=400 + number * 20, height=600)
    writer.add_metadata({"/Title": "Example"})
    writer.write(destination)
    return destination


def page_count(filename):
    return len(PdfReader(filename).pages)


def test_open_and_clean_state(pdf):
    editor = PDFEditor(pdf)
    assert not editor.dirty
    assert not editor.can_undo
    assert not editor.encrypted
    editor.close()


def test_rotation_and_source_not_modified(pdf):
    editor = PDFEditor(pdf)
    editor.rotate(0)
    assert PdfReader(editor.path).pages[0].get("/Rotate") == 90
    assert PdfReader(pdf).pages[0].get("/Rotate") is None
    assert editor.dirty
    editor.close()


def test_delete_undo_redo(pdf):
    editor = PDFEditor(pdf)
    editor.delete(1)
    assert page_count(editor.path) == 2
    editor.undo()
    assert page_count(editor.path) == 3
    assert not editor.dirty
    editor.redo()
    assert page_count(editor.path) == 2
    assert editor.dirty
    editor.close()


def test_move_page(pdf):
    editor = PDFEditor(pdf)
    editor.move(0, 1)
    pages = PdfReader(editor.path).pages
    assert float(pages[0].mediabox.width) == 420
    assert float(pages[1].mediabox.width) == 400
    editor.close()


def test_insert_pdf(pdf, tmp_path):
    addon = tmp_path / "addon.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=900, height=400)
    writer.write(addon)
    editor = PDFEditor(pdf)
    editor.insert_pdf(addon, 0)
    pages = PdfReader(editor.path).pages
    assert len(pages) == 4
    assert float(pages[1].mediabox.width) == 900
    editor.close()


def test_extract_range(pdf, tmp_path):
    editor = PDFEditor(pdf)
    target = tmp_path / "selected.pdf"
    editor.extract([0, 2], target)
    pages = PdfReader(target).pages
    assert len(pages) == 2
    assert float(pages[1].mediabox.width) == 440
    assert not editor.dirty
    editor.close()


def test_save_as_and_keep_original(pdf, tmp_path):
    editor = PDFEditor(pdf)
    editor.rotate(1)
    dest = tmp_path / "result.pdf"
    editor.save_as(dest)
    assert dest.is_file()
    assert not editor.dirty
    assert PdfReader(dest).pages[1].get("/Rotate") == 90
    assert PdfReader(pdf).pages[1].get("/Rotate") is None
    editor.undo()
    assert editor.dirty
    editor.redo()
    assert not editor.dirty
    editor.close()


def test_redo_branching(pdf):
    editor = PDFEditor(pdf)
    editor.rotate(0)
    editor.rotate(1)
    editor.undo()
    editor.delete(2)
    assert not editor.can_redo
    assert page_count(editor.path) == 2
    editor.close()


def test_note(pdf):
    editor = PDFEditor(pdf)
    editor.add_note(0, "Nota di prova")
    annotations = PdfReader(editor.path).pages[0]["/Annots"]
    assert len(annotations) >= 1
    obj = annotations[0].get_object()
    assert obj["/Contents"] == "Nota di prova"
    editor.close()


def test_cannot_delete_last_page(tmp_path):
    file = tmp_path / "one.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    writer.write(file)
    editor = PDFEditor(file)
    with pytest.raises(PDFOperationError):
        editor.delete(0)
    assert page_count(editor.path) == 1
    assert not editor.can_undo
    editor.close()


@pytest.mark.parametrize("spec, expected", [
    ("1", [0]), ("1,3", [0, 2]), ("2-3", [1, 2]),
    ("1, 3-5", [0, 2, 3, 4]),
])
def test_parse_valid(spec, expected):
    assert parse_page_range(spec, 10) == expected


@pytest.mark.parametrize("spec", ["", "0", "1-0", "2,2", "1-3,2", "44", "x", "1-", "3-2"])
def test_parse_invalid(spec):
    with pytest.raises(PDFOperationError):
        parse_page_range(spec, 10)


def test_invalid_edit_does_not_commit(pdf):
    editor = PDFEditor(pdf)
    with pytest.raises(PDFOperationError):
        editor.rotate(0, 45)
    with pytest.raises(PDFOperationError):
        editor.move(0, -1)
    with pytest.raises(PDFOperationError):
        editor.extract([], pdf.with_name("no.pdf"))
    assert not editor.can_undo
    assert not editor.dirty
    editor.close()


def test_source_snapshot_survives_external_changes(pdf):
    original = pdf.read_bytes()
    editor = PDFEditor(pdf)
    pdf.write_bytes(b"replaced externally")
    assert editor.path.read_bytes() == original
    editor.rotate(0)
    assert editor.undo()
    assert editor.path.read_bytes() == original
    editor.close()


def test_saved_branch_stays_dirty_and_extract_protects_output(pdf, tmp_path):
    editor = PDFEditor(pdf)
    editor.rotate(0)
    destination = tmp_path / "saved.pdf"
    editor.save_as(destination)
    editor.undo()
    editor.rotate(1)
    assert editor.dirty
    assert not editor.can_redo
    with pytest.raises(PDFOperationError):
        editor.extract([0], destination)
    assert PdfReader(destination).pages[0].rotation == 90
    editor.close()
