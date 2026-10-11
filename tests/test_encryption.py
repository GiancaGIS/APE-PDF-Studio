from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.constants import UserAccessPermissions

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


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
@pytest.mark.parametrize("password", [None, "", "owner-secret"])
def test_owner_password_only_opens_without_a_reading_password(tmp_path, algorithm, password):
    source = tmp_path / "restricted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(250, 400)
    writer.add_metadata({"/Title": "Readable but restricted"})
    writer.encrypt("", owner_password="owner-secret", algorithm=algorithm,
                   permissions_flag=UserAccessPermissions.PRINT | UserAccessPermissions.EXTRACT)
    writer.write(source)
    original = source.read_bytes()

    editor = PDFEditor(source, password=password)
    try:
        assert editor.encrypted and not editor.security_report.blocked
        assert editor.metadata().values["/Title"] == "Readable but restricted"
        assert not editor.metadata().editable
        with pytest.raises(PDFOperationError):
            editor.rotate(0)
        with pytest.raises(PDFOperationError):
            editor.save_as(tmp_path / "unlocked.pdf")
        assert not editor.dirty and not editor.can_undo
        assert source.read_bytes() == original
        assert not (tmp_path / "unlocked.pdf").exists()
    finally:
        editor.close()


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_nonempty_reading_password_is_still_required(tmp_path, algorithm):
    writer = PdfWriter()
    writer.add_blank_page(250, 400)
    writer.encrypt("read-secret", owner_password="owner-secret", algorithm=algorithm)
    source = tmp_path / "private.pdf"
    writer.write(source)
    for password in (None, "", "wrong"):
        with pytest.raises(PDFPasswordRequired):
            PDFEditor(source, password=password)
    for password in ("read-secret", "owner-secret"):
        editor = PDFEditor(source, password=password)
        assert editor.encrypted and not editor.security_report.blocked
        editor.close()
