from pathlib import Path

import pytest
from pypdf import PdfWriter

from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError, PDFPasswordRequired


def test_encrypted_pdf_read_only(tmp_path: Path):
    source = tmp_path / "private.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=250, height=400)
    writer.encrypt("secret")
    writer.write(source)
    with pytest.raises(PDFPasswordRequired):
        PDFEditor(source)
    with pytest.raises(PDFPasswordRequired):
        PDFEditor(source, password="wrong")
    editor = PDFEditor(source, password="secret")
    assert editor.encrypted
    with pytest.raises(PDFOperationError):
        editor.rotate(0)
    with pytest.raises(PDFOperationError):
        editor.save_as(tmp_path / "unlocked.pdf")
    editor.close()
