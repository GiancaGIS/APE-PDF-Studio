"""Metadata edits must preserve the rest of the document and revision history."""
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError


@pytest.fixture
def metadata_pdf(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    writer.add_blank_page(400, 600)
    writer.add_metadata({"/Title": "Old title", "/Author": "Ada", "/Subject": "Old subject",
                         "/Keywords": "old", "/Creator": "Original tool", "/Producer": "Original producer",
                         "/CreationDate": "D:20240102102030+01'00'", "/ModDate": "D:20240103102030Z"})
    writer._info[NameObject("/CustomCount")] = NumberObject(7)
    writer.add_outline_item("Section one", 0)
    writer.add_attachment("notes.txt", b"untouched attachment")
    xml = '''<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"
 xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="Original tool" pdfaid:part="2" pdfaid:conformance="B"
 xmlns:custom="urn:apex:test"><custom:Keep>untouched</custom:Keep></rdf:Description>
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" pdf:Keywords="old">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Old title</rdf:li><rdf:li xml:lang="fr">Autre titre</rdf:li></rdf:Alt></dc:title>
<dc:description><rdf:Alt><rdf:li xml:lang="x-default">Old subject</rdf:li></rdf:Alt></dc:description>
<dc:creator><rdf:Seq><rdf:li>Ada</rdf:li></rdf:Seq></dc:creator>
<dc:subject><rdf:Bag><rdf:li>old</rdf:li></rdf:Bag></dc:subject></rdf:Description>
</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'''
    stream = DecodedStreamObject()
    stream.set_data(xml.encode())
    stream.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)
    destination = tmp_path / "metadata.pdf"
    writer.write(destination)
    return destination


def test_read_common_and_technical_metadata(metadata_pdf):
    editor = PDFEditor(metadata_pdf)
    try:
        data = editor.metadata()
        assert data.editable and not data.warnings and not data.conflicts
        assert data.values["/Title"] == "Old title"
        assert data.info["/Producer"] == "Original producer"
        assert data.info["/CreationDate"].startswith("D:2024")
        assert "custom:Keep" in data.xml and data.page_count == 2
        assert not editor.dirty
    finally:
        editor.close()


def test_edit_info_xmp_and_preserve_catalog_dates_languages_and_source(metadata_pdf, tmp_path):
    original = metadata_pdf.read_bytes()
    editor = PDFEditor(metadata_pdf)
    try:
        editor.update_metadata({"/Title": "Titolo è <nuovo> & corretto", "/Author": "Ada; Bob",
                                "/Subject": "Nuovo oggetto", "/Keywords": "PDF, ricerca; archivio",
                                "/Creator": "My application"})
        with PdfReader(editor.path) as reader:
            assert reader.metadata.title == "Titolo è <nuovo> & corretto"
            assert reader.metadata.author == "Ada; Bob"
            xmp = reader.xmp_metadata
            assert xmp.dc_title == {"x-default": "Titolo è <nuovo> & corretto", "fr": "Autre titre"}
            assert xmp.dc_creator == ["Ada", "Bob"]
            assert xmp.dc_description["x-default"] == "Nuovo oggetto"
            assert xmp.pdf_keywords == "PDF, ricerca; archivio"
            assert xmp.dc_subject == ["PDF", "ricerca", "archivio"]
            assert xmp.xmp_creator_tool == "My application"
            assert xmp.pdfaid_part == "2"
            assert "untouched" in reader.root_object["/Metadata"].get_data().decode()
            assert isinstance(reader.metadata["/CustomCount"], NumberObject)
            assert reader.metadata["/Producer"] == "Original producer"
            assert reader.metadata["/ModDate"] == "D:20240103102030Z"
            assert len(reader.outline) == 1
            assert reader.attachments["notes.txt"] == [b"untouched attachment"]
        assert editor.dirty and metadata_pdf.read_bytes() == original
        editor.save_as(tmp_path / "saved.pdf")
        assert not editor.dirty
        editor.undo()
        assert editor.metadata().values["/Title"] == "Old title"
        editor.redo()
        assert editor.metadata().values["/Title"] == "Titolo è <nuovo> & corretto"
    finally:
        editor.close()


def test_clear_fields_removes_info_and_xmp(metadata_pdf):
    editor = PDFEditor(metadata_pdf)
    try:
        editor.update_metadata({"/Title": "", "/Keywords": "", "/Author": ""})
        with PdfReader(editor.path) as reader:
            assert "/Title" not in reader.metadata and "/Author" not in reader.metadata
            assert not reader.xmp_metadata.dc_title and not reader.xmp_metadata.dc_creator
            assert not reader.xmp_metadata.pdf_keywords and not reader.xmp_metadata.dc_subject
        assert editor.metadata().values["/Title"] == ""
    finally:
        editor.close()


def test_info_only_noop_and_metadata_survive_later_edits(tmp_path):
    source = tmp_path / "info-only.pdf"
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    writer.add_metadata({"/Title": "Original"})
    writer.write(source)
    editor = PDFEditor(source)
    try:
        editor.update_metadata({"/Title": "Original"})
        assert not editor.can_undo and not editor.dirty
        editor.update_metadata({"/Title": "Changed"})
        assert not editor.metadata().xml
        editor.rotate(0)
        assert editor.metadata().values["/Title"] == "Changed"
    finally:
        editor.close()


def test_xmp_survives_page_edit_and_extraction(metadata_pdf, tmp_path):
    editor = PDFEditor(metadata_pdf)
    try:
        editor.update_metadata({"/Title": "New"})
        editor.rotate(0)
        assert editor.metadata().xmp_values["/Title"] == "New"
        output = editor.extract([0], tmp_path / "extracted.pdf")
        with PdfReader(output) as reader:
            assert reader.xmp_metadata.dc_title["x-default"] == "New"
    finally:
        editor.close()


@pytest.mark.parametrize("changes", [{"/Producer": "Fake"}, {"/Title": "bad\x00value"}, {"/Author": 12}])
def test_invalid_edits_do_not_change_history(metadata_pdf, changes):
    editor = PDFEditor(metadata_pdf)
    try:
        with pytest.raises(PDFOperationError):
            editor.update_metadata(changes)
        assert not editor.can_undo and not editor.dirty
    finally:
        editor.close()


def test_bad_xmp_stays_readable_and_readonly(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    writer.add_metadata({"/Title": "Readable title"})
    stream = DecodedStreamObject()
    stream.set_data(b"<invalid XML")
    writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)
    source = tmp_path / "bad-xmp.pdf"
    writer.write(source)
    editor = PDFEditor(source)
    try:
        assert editor.metadata().values["/Title"] == "Readable title"
        assert not editor.metadata().editable and editor.metadata().warnings
        with pytest.raises(PDFOperationError):
            editor.update_metadata({"/Title": "New"})
        assert not editor.dirty
    finally:
        editor.close()


def test_encrypted_metadata_can_be_read_but_not_changed(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    writer.add_metadata({"/Title": "Private title"})
    writer.encrypt("secret")
    source = tmp_path / "private.pdf"
    writer.write(source)
    editor = PDFEditor(source, "secret")
    try:
        assert editor.metadata().values["/Title"] == "Private title"
        assert not editor.metadata().editable
        with pytest.raises(PDFOperationError):
            editor.update_metadata({"/Title": "New"})
    finally:
        editor.close()
