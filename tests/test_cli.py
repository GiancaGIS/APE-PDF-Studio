from pathlib import Path

from pypdf import PdfReader, PdfWriter

from ape_pdf_studio.core.cli import main


def test_cli_rotate_and_extract(tmp_path: Path):
    source = tmp_path / "in.pdf"
    rotated = tmp_path / "rotated.pdf"
    extracted = tmp_path / "extracted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=600)
    writer.add_blank_page(width=500, height=700)
    writer.write(source)
    assert main([str(source), "rotate", "2", "-o", str(rotated)]) == 0
    assert PdfReader(rotated).pages[1].get("/Rotate") == 90
    assert main([str(source), "extract", "2", "-o", str(extracted)]) == 0
    assert len(PdfReader(extracted).pages) == 1
    assert float(PdfReader(extracted).pages[0].mediabox.width) == 500
    assert len(PdfReader(source).pages) == 2


def test_cli_error(tmp_path: Path):
    assert main([str(tmp_path / "missing.pdf"), "info"]) == 2
