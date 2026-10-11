"""Metadata is parsed only by the bounded shell helper, never by Explorer."""
import io
import struct
import subprocess

import pytest
from pypdf import PdfWriter
from pypdf.constants import UserAccessPermissions
from pypdf.generic import DictionaryObject, NameObject

from ape_pdf_studio.core.shell_metadata import HEADER, MAX_METADATA_BYTES, MAX_TEXT_CHARS
from ape_pdf_studio.core.shell_worker import REQUEST
from ape_pdf_studio.core.worker_process import worker_command


def decode_reply(reply):
    magic, status, count, length, reserved = HEADER.unpack(reply[:HEADER.size])
    assert magic == b"APM1" and reserved == 0 and length <= MAX_METADATA_BYTES
    assert len(reply) == HEADER.size + length and count <= 32
    offset = HEADER.size
    rows = []
    for _ in range(count):
        row = []
        for _ in range(2):
            size, = struct.unpack_from("<I", reply, offset)
            offset += 4
            value = reply[offset:offset + size].decode("utf-16-le")
            assert "\x00" not in value and len(value) <= MAX_TEXT_CHARS
            offset += size
            row.append(value)
        rows.append(row)
    assert offset == len(reply)
    return status, dict(rows)


def run_metadata(writer, language=0):
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()
    result = subprocess.run(worker_command("shell"), input=REQUEST.pack(b"APQ1", language, 0, len(data)) + data,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return decode_reply(result.stdout)


@pytest.fixture
def writer():
    result = PdfWriter()
    result.add_blank_page(400, 600)
    result.add_metadata({"/Title": "Fattura è – 日本語 😀", "/Author": "GiancaGIS", "/CreationDate": "D:20261011123000+02'00'"})
    return result


@pytest.mark.parametrize("language,title,pages", [(0, "Titolo", "Pagine"), (1, "Title", "Pages"), (2, "Título", "Páginas"), (3, "Titre", "Pages")])
def test_metadata_and_unicode_in_all_windows_languages(writer, language, title, pages):
    status, rows = run_metadata(writer, language)
    assert status == 0 and rows[title] == "Fattura è – 日本語 😀"
    assert rows[pages] == "1" and "GiancaGIS" in rows.values()


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_restrictions_only_encryption_still_exposes_metadata(writer, algorithm):
    writer.encrypt("", owner_password="owner-secret", algorithm=algorithm, permissions_flag=UserAccessPermissions.EXTRACT)
    status, rows = run_metadata(writer)
    assert status == 0 and rows["Titolo"].startswith("Fattura")
    assert rows["Stampa"] == rows["Modifica contenuto"] == "Non consentita"
    assert rows["Copia del testo"] == "Consentita"
    assert "sola lettura" in rows["Protezione"]


def test_reading_password_has_no_metadata_or_prompt(writer):
    writer.encrypt("reading-password", algorithm="AES-256")
    status, rows = run_metadata(writer)
    assert status == 1 and not rows


def test_active_content_has_no_metadata(writer):
    writer.root_object[NameObject("/OpenAction")] = writer._add_object(DictionaryObject({NameObject("/S"): NameObject("/Launch")}))
    status, rows = run_metadata(writer)
    assert status == 2 and not rows


def test_pdfa_signature_and_xmp_values_are_preserved(writer):
    from test_document_info import add_pdfa, add_signature
    add_pdfa(writer)
    add_signature(writer)
    status, rows = run_metadata(writer)
    assert status == 0
    assert rows["PDF/A"] == "PDF/A-2b dichiarato"
    assert rows["Firme digitali"] == "1 · validità non verificata"


def test_bounded_display_text_and_no_embedded_controls(writer):
    writer.add_metadata({"/Title": "prefix\x00\x01" + "x" * 10000})
    status, rows = run_metadata(writer)
    assert status == 0 and rows["Titolo"].startswith("prefix")
    assert len(rows["Titolo"]) <= MAX_TEXT_CHARS and "\x01" not in rows["Titolo"]


@pytest.mark.parametrize("header,data", [
    (REQUEST.pack(b"APQ1", 0, 0, 12), b"%PDF-broken!"),
    (REQUEST.pack(b"APQ1", 4, 0, 1), b"x"),
    (REQUEST.pack(b"APQ1", 0, 1, 1), b"x"),
    (REQUEST.pack(b"APQ1", 0, 0, 268435457), b""),
    (REQUEST.pack(b"APQ1", 0, 0, 100), b"%PDF-1.7\nbroken"),
])
def test_invalid_and_truncated_metadata_requests_fail_closed(header, data):
    result = subprocess.run(worker_command("shell"), input=header + data, capture_output=True, timeout=20)
    assert (result.returncode != 0 and not result.stdout) or decode_reply(result.stdout) == (2, {})
