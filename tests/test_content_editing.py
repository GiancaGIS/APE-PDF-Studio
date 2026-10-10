"""PDF content changes must survive saving without damaging surrounding content."""
from pathlib import Path

import pytest
import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject

from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError
from ape_pdf_studio.core.page_coordinates import displayed_size


@pytest.fixture
def content_pdf(tmp_path):
    path = tmp_path / "content.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=600)
    page.draw_rect(pymupdf.Rect(20, 60, 350, 150), color=(0, 0, 1), fill=(.8, .9, 1))
    page.insert_text((48, 100), "Original phrase", fontsize=16, color=(.8, .1, .2))
    page.insert_text((48, 132), "Keep this line", fontsize=16)
    extra = document.new_page(width=400, height=600)
    extra.insert_text((48, 100), "Keep this page", fontsize=16)
    document.save(path)
    document.close()
    return path


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("unit", [1, 2])
def test_notes_position_colour_appearance_update_delete_and_history(content_pdf, tmp_path, rotation, unit):
    from pypdf.generic import RectangleObject
    reader = PdfReader(content_pdf)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    page = writer.pages[0]
    page.cropbox = RectangleObject([20, 30, 380, 550])
    page.rotate(rotation)
    page[NameObject("/UserUnit")] = NumberObject(unit)
    writer.write(content_pdf)
    reader.close()
    editor = PDFEditor(content_pdf)
    editor.add_note(0, "Nota nel punto scelto", (100, 150))
    note = editor.notes()[0][0]
    assert note["rect"] == pytest.approx((100, 150, 124, 174))
    obj = PdfReader(editor.path).pages[0]["/Annots"][0].get_object()
    assert obj["/Subtype"] == "/Text" and obj["/Name"] == "/Comment"
    assert obj["/C"] == [1, .84, .2] and b" rg " in obj["/AP"]["/N"].get_data()
    editor.update_note(0, note["index"], "Nota aggiornata")
    assert editor.notes()[0][0]["text"] == "Nota aggiornata"
    output = tmp_path / "notes.pdf"
    editor.save_as(output)
    reopened = PDFEditor(output)
    assert reopened.notes()[0][0]["rect"] == pytest.approx(note["rect"])
    reopened.close()
    editor.update_note(0, 0, None)
    assert not editor.notes()
    editor.undo()
    assert editor.notes()[0][0]["text"] == "Nota aggiornata"
    editor.redo()
    assert not editor.notes()
    editor.close()


def test_note_at_page_edge_remains_inside_page_and_invalid_position_does_not_commit(content_pdf):
    editor = PDFEditor(content_pdf)
    editor.add_note(0, "Bordo", (400, 600))
    assert editor.notes()[0][0]["rect"] == (376, 576, 400, 600)
    previous = editor.path
    for position in ((-1, 10), (10, 601), (float("nan"), 0)):
        with pytest.raises(PDFOperationError):
            editor.add_note(0, "Bad", position)
        assert editor.path == previous
    editor.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("unit", [1, 2])
def test_text_replacement_after_crop_rotation_userunit_preserves_neighbours_and_background(content_pdf, tmp_path, rotation, unit):
    from pypdf.generic import RectangleObject
    writer = PdfWriter()
    reader = PdfReader(content_pdf)
    writer.clone_document_from_reader(reader)
    writer.pages[0].cropbox = RectangleObject([20, 30, 380, 550])
    writer.pages[0].rotate(rotation)
    writer.pages[0][NameObject("/UserUnit")] = NumberObject(unit)
    writer.add_metadata({"/Title": "Keep metadata", "/Custom": "Keep custom"})
    writer.add_attachment("example.txt", b"Keep attachment")
    writer.add_outline_item("Keep bookmark", 1)
    writer.write(content_pdf)
    reader.close()
    original = content_pdf.read_bytes()
    editor = PDFEditor(content_pdf)
    with pymupdf.open(editor.path) as doc:
        span = doc[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
        point = pymupdf.Rect(span["bbox"]).tl + (3, 3)
        point *= doc[0].rotation_matrix
        before = doc[0].get_drawings()
    target = editor.text_at(0, (point.x, point.y))
    editor.replace_text(target, "New phrase")
    with pymupdf.open(editor.path) as doc:
        assert "Original phrase" not in doc[0].get_text()
        assert "New phrase" in doc[0].get_text() and "Keep this line" in doc[0].get_text()
        assert "Keep this page" in doc[1].get_text()
        assert doc[0].get_drawings() == before
        new_span = next(s for b in doc[0].get_text("dict")["blocks"] for l in b.get("lines", [])
                        for s in l["spans"] if s["text"] == "New phrase")
        assert new_span["origin"] == pytest.approx(target.origin, abs=.01)
        assert new_span["color"] == target.color
    output = tmp_path / "edited.pdf"
    editor.save_as(output)
    with PdfReader(output) as check:
        assert "New phrase" in check.pages[0].extract_text()
        assert "Original phrase" not in check.pages[0].extract_text()
        assert check.metadata["/Title"] == "Keep metadata" and check.metadata["/Custom"] == "Keep custom"
        assert check.attachments["example.txt"] == [b"Keep attachment"]
        assert check.outline[0].title == "Keep bookmark"
    assert content_pdf.read_bytes() == original
    editor.undo()
    assert editor.path.read_bytes() == original
    editor.redo()
    assert "New phrase" in PdfReader(editor.path).pages[0].extract_text()
    editor.close()


def test_oversized_invalid_and_stale_text_edits_do_not_commit(content_pdf):
    editor = PDFEditor(content_pdf)
    target = editor.text_at(0, (60, 95))
    original = editor.path
    for text in ("Much too long " * 30, "", "two\nlines", "bad\x00text"):
        with pytest.raises(PDFOperationError):
            editor.replace_text(target, text)
        assert editor.path == original and not editor.dirty
    editor.replace_text(target, "New")
    with pytest.raises(PDFOperationError, match="cambiato"):
        editor.replace_text(target, "Stale")
    editor.close()


def test_unicode_replacement_is_readable_and_saves_real_text(content_pdf):
    editor = PDFEditor(content_pdf)
    target = editor.text_at(0, (60, 95))
    editor.replace_text(target, "Caffè €")
    assert "Caffè €" in PdfReader(editor.path).pages[0].extract_text()
    editor.close()


def test_overlapping_text_is_rejected_without_losing_either_layer(content_pdf):
    with pymupdf.open(content_pdf) as document:
        document[0].insert_text((48, 100), "Overlapping words", fontsize=16)
        result = document.tobytes()
    content_pdf.write_bytes(result)
    editor = PDFEditor(content_pdf)
    target = editor.text_at(0, (60, 95))
    original = editor.path
    with pytest.raises(PDFOperationError, match="sovrappone"):
        editor.replace_text(target, "New")
    assert editor.path == original and not editor.dirty
    assert "Overlapping words" in PdfReader(editor.path).pages[0].extract_text()
    editor.close()


def test_text_edit_preserves_notes_links_pending_redactions_and_embedded_images(content_pdf):
    import zlib
    import struct
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"\x00" + b"\x00\xff\x00" * 2 + b"\x00" + b"\x00\xff\x00" * 2)) + chunk(b"IEND", b"")
    with pymupdf.open(content_pdf) as document:
        page = document[0]
        page.add_redact_annot(pymupdf.Rect(40, 112, 220, 140))
        page.add_text_annot((200, 180), "Keep note")
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(48, 80, 160, 110), "uri": "https://example.com"})
        page.insert_image(pymupdf.Rect(20, 70, 200, 110), stream=png, overlay=False)
        result = document.tobytes()
    content_pdf.write_bytes(result)
    editor = PDFEditor(content_pdf)
    target = editor.text_at(0, (60, 95))
    editor.replace_text(target, "New phrase")
    with pymupdf.open(editor.path) as document:
        page = document[0]
        assert "Keep this line" in page.get_text()
        assert page.get_links()[0]["uri"] == "https://example.com"
        assert len(page.get_images()) == 1
        assert {a.type[0] for a in page.annots()} >= {pymupdf.PDF_ANNOT_REDACT, pymupdf.PDF_ANNOT_TEXT}
    assert editor.notes()[0][0]["text"] == "Keep note"
    editor.close()
