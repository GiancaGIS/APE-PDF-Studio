"""Identification fixtures intentionally contain no valid cryptographic signature."""
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from ape_pdf_studio.core.document_info import inspect_document
from ape_pdf_studio.core.editor import PDFEditor


def add_pdfa(writer, part="2", level="B", attributes=True):
    declarations = f'pdfaid:part="{part}"' + (f' pdfaid:conformance="{level}"' if level else "")
    children = f"<pdfaid:part>{part}</pdfaid:part>" + (f"<pdfaid:conformance>{level}</pdfaid:conformance>" if level else "")
    xml = (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           f'<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
           f'{declarations if attributes else ""}>{"" if attributes else children}</rdf:Description></rdf:RDF></x:xmpmeta>')
    metadata = DecodedStreamObject()
    metadata.set_data(xml.encode())
    metadata.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    writer.root_object[NameObject("/Metadata")] = writer._add_object(metadata)


def add_signature(writer, *, populated=True, field_name="Signature1", certify=False):
    signature = DictionaryObject({
        NameObject("/Type"): NameObject("/Sig"),
        NameObject("/SubFilter"): NameObject("/adbe.pkcs7.detached"),
        NameObject("/Contents"): ByteStringObject(b"test-signature-data" if populated else b"\x00" * 24),
        NameObject("/ByteRange"): ArrayObject([NumberObject(n) for n in (0, 100, 200, 100)]),
        NameObject("/Name"): TextStringObject("Test Signer"),
        NameObject("/Reason"): TextStringObject("Test only"),
    })
    value = writer._add_object(signature)
    field = DictionaryObject({NameObject("/FT"): NameObject("/Sig"),
                              NameObject("/T"): TextStringObject(field_name), NameObject("/V"): value})
    field_ref = writer._add_object(field)
    writer.root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([field_ref])}))
    if certify:
        writer.root_object[NameObject("/Perms")] = DictionaryObject({NameObject("/DocMDP"): value})
    return field_ref, value


def inspect_writer(writer, tmp_path):
    path = tmp_path / "metadata.pdf"
    writer.write(path)
    reader = PdfReader(path)
    try:
        return inspect_document(reader)
    finally:
        reader.close()


@pytest.mark.parametrize("part,level,attributes,expected", [
    ("1", "A", True, "PDF/A-1a"), ("2", "B", True, "PDF/A-2b"),
    ("3", "U", False, "PDF/A-3u"), ("4", "", False, "PDF/A-4"),
    ("4", "F", True, "PDF/A-4f"), ("9", "B", True, None),
])
def test_pdfa_identification(part, level, attributes, expected, tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    add_pdfa(writer, part, level, attributes)
    info = inspect_writer(writer, tmp_path)
    assert info.pdfa == expected
    assert bool(info.warnings) == (expected is None)


def test_plain_pdf_and_bad_xmp_are_readable(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    info = inspect_writer(writer, tmp_path)
    assert not info.pdfa and not info.signatures and not info.warnings
    stream = DecodedStreamObject()
    stream.set_data(b"<bad XML")
    writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)
    info = inspect_writer(writer, tmp_path)
    assert not info.pdfa
    assert "XMP" in info.warnings[0]
    editor = PDFEditor(tmp_path / "metadata.pdf")
    editor.close()


@pytest.mark.parametrize("populated", [False, True])
def test_empty_widgets_do_not_count_as_signatures(populated, tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    add_signature(writer, populated=populated)
    info = inspect_writer(writer, tmp_path)
    assert len(info.signatures) == int(populated)
    assert len(info.empty_signature_fields) == int(not populated)
    if populated:
        assert info.signatures[0].signer == "Test Signer"


def test_certification_and_widget_do_not_duplicate_signature(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(400, 600)
    field, value = add_signature(writer, certify=True)
    widget = DictionaryObject({NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Parent"): field})
    widget_ref = writer._add_object(widget)
    field.get_object()[NameObject("/Kids")] = ArrayObject([widget_ref])
    page[NameObject("/Annots")] = ArrayObject([widget_ref])
    info = inspect_writer(writer, tmp_path)
    assert len(info.signatures) == 1
    assert info.signatures[0].field == "Signature1"


def test_inherited_signature_type_and_nested_names(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    field, value = add_signature(writer)
    field.get_object().pop("/FT")
    empty = writer._add_object(DictionaryObject({NameObject("/T"): TextStringObject("Empty")}))
    parent = writer._add_object(DictionaryObject({NameObject("/T"): TextStringObject("Group"),
                                                NameObject("/FT"): NameObject("/Sig"),
                                                NameObject("/Kids"): ArrayObject([field, empty])}))
    writer.root_object["/AcroForm"][NameObject("/Fields")] = ArrayObject([parent])
    info = inspect_writer(writer, tmp_path)
    assert info.signatures[0].field == "Group.Signature1"
    assert info.empty_signature_fields == ("Group.Empty",)


def test_orphan_widget_and_encrypted_pdf(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(400, 600)
    field, _ = add_signature(writer)
    writer.root_object.pop("/AcroForm")
    widget = writer._add_object(DictionaryObject({NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Parent"): field}))
    page[NameObject("/Annots")] = ArrayObject([widget])
    writer.encrypt("secret")
    path = tmp_path / "encrypted.pdf"
    writer.write(path)
    editor = PDFEditor(path, password="secret")
    assert len(editor.document_info.signatures) == 1
    editor.close()
