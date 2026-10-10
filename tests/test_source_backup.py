from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError


def test_explicit_overwrite_preserves_undo_history(tmp_path: Path):
    source = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=600)
    writer.add_blank_page(width=500, height=600)
    writer.write(source)
    editor = PDFEditor(source)
    editor.rotate(0)
    editor.save_as(source)
    assert PdfReader(source).pages[0].get("/Rotate") == 90
    assert editor.undo()
    assert PdfReader(editor.path).pages[0].get("/Rotate") is None
    assert editor.redo()
    assert PdfReader(editor.path).pages[0].get("/Rotate") == 90
    editor.close()


def test_extract_wont_clobber_active_source(tmp_path: Path):
    source = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=500)
    writer.write(source)
    editor = PDFEditor(source)
    with pytest.raises(PDFOperationError):
        editor.extract([0], source)
    editor.close()
