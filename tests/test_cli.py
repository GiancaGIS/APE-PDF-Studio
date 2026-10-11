from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions

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


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_cli_reads_permissions_only_pdf_without_a_password(tmp_path, capsys, algorithm):
    source = tmp_path / "restricted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(250, 400)
    writer.encrypt("", owner_password="owner-secret", algorithm=algorithm,
                   permissions_flag=UserAccessPermissions.PRINT | UserAccessPermissions.EXTRACT)
    writer.write(source)
    assert main([str(source), "info"]) == 0
    assert not capsys.readouterr().err
    destination = tmp_path / "modified.pdf"
    assert main([str(source), "rotate", "1", "-o", str(destination)]) == 2
    assert not destination.exists()
