"""Synthetic active PDFs must be rejected before rendering or conversion."""
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject, DecodedStreamObject

from ape_pdf_studio.core import security
from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFPasswordRequired, PDFSecurityError
from ape_pdf_studio.core.docx_export import convert_pdf_to_docx


def action(kind):
    return DictionaryObject({NameObject("/S"): NameObject(kind)})


def write_pdf(tmp_path, change=lambda _writer: None, *, filename="input.pdf", password=None):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    change(writer)
    if password:
        writer.encrypt(password)
    path = tmp_path / filename
    writer.write(path)
    return path


@pytest.mark.parametrize("kind", ["/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE", "/Rendition", "/Sound", "/Movie", "/UnknownAction"])
def test_active_open_actions_blocked_before_main_process_parse(tmp_path, monkeypatch, kind):
    source = write_pdf(tmp_path, lambda w: w.root_object.update({NameObject("/OpenAction"): w._add_object(action(kind))}))
    original = source.read_bytes()
    # The GUI and core must never parse the rejected snapshot in-process.
    monkeypatch.setattr("ape_pdf_studio.core.editor.PdfReader", lambda *_a, **_k: pytest.fail("unsafe main process parse"))
    with pytest.raises(PDFSecurityError, match="bloccata"):
        PDFEditor(source)
    assert source.read_bytes() == original


def test_javascript_name_tree_and_chained_page_actions_are_detected(tmp_path):
    def change(w):
        script = action("/JavaScript")
        script[NameObject("/JS")] = TextStringObject("app.alert('synthetic test');")
        ref = w._add_object(script)
        w.root_object[NameObject("/Names")] = DictionaryObject({NameObject("/JavaScript"): DictionaryObject({
            NameObject("/Names"): ArrayObject([TextStringObject("test"), ref])})})
        go = action("/GoTo")
        go[NameObject("/Next")] = ArrayObject([ref])
        w.pages[0][NameObject("/AA")] = w._add_object(DictionaryObject({NameObject("/O"): w._add_object(go)}))
    source = write_pdf(tmp_path, change)
    with pytest.raises(PDFSecurityError, match="JavaScript"):
        PDFEditor(source)


def test_escaped_pdf_names_and_orphan_actions_are_detected(tmp_path):
    def change(w):
        # Object is deliberately outside the catalog; scanner checks xref too.
        w._add_object(action("/JavaScript"))
    source = write_pdf(tmp_path, change)
    # pypdf repairs offsets: escaped names are parsed structurally, not grepped.
    source.write_bytes(source.read_bytes().replace(b"/JavaScript", b"/J#61vaScript"))
    with pytest.raises(PDFSecurityError, match="JavaScript"):
        PDFEditor(source)


@pytest.mark.parametrize("feature", ["xfa", "richmedia", "3d", "entities"])
def test_dynamic_forms_media_and_xml_entities_are_blocked(tmp_path, feature):
    def change(w):
        if feature == "xfa":
            w.root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/XFA"): TextStringObject("synthetic")})
        elif feature in ("richmedia", "3d"):
            annotation = DictionaryObject({NameObject("/Subtype"): NameObject("/RichMedia" if feature == "richmedia" else "/3D")})
            w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annotation)])
        else:
            stream = DecodedStreamObject()
            stream.set_data(b'<!DOCTYPE x [<!ENTITY e "test">]><x>&e;</x>')
            stream[NameObject("/Type")] = NameObject("/Metadata")
            stream[NameObject("/Subtype")] = NameObject("/XML")
            w.root_object[NameObject("/Metadata")] = w._add_object(stream)
    with pytest.raises(PDFSecurityError):
        PDFEditor(write_pdf(tmp_path, change))


@pytest.mark.parametrize("uri", ["file:///C:/test.exe", "javascript:alert(1)", "data:text/html,test", "custom-app:run", "../local-file"])
def test_local_files_and_unapproved_uri_schemes_are_blocked(tmp_path, uri):
    def change(w):
        link = action("/URI")
        link[NameObject("/URI")] = TextStringObject(uri)
        w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(DictionaryObject({
            NameObject("/Subtype"): NameObject("/Link"), NameObject("/A"): w._add_object(link)}))])
    with pytest.raises(PDFSecurityError, match="protocolli"):
        PDFEditor(write_pdf(tmp_path, change))


def test_embedded_files_and_web_links_are_reported_without_extraction(tmp_path):
    def change(w):
        w.add_attachment("danger.exe", b"synthetic bytes, not executable")
        link = action("/URI")
        link[NameObject("/URI")] = TextStringObject("https://example.invalid/")
        w.root_object[NameObject("/OpenAction")] = w._add_object(link)
    source = write_pdf(tmp_path, change)
    before = set(tmp_path.iterdir())
    editor = PDFEditor(source)
    try:
        assert editor.security_report.findings == ("attachments", "external_links")
        assert not editor.security_report.blocked
        assert set(tmp_path.iterdir()) == before
    finally:
        editor.close()


def test_internal_destination_and_cyclic_next_are_handled(tmp_path):
    def change(w):
        go = action("/GoTo")
        go[NameObject("/D")] = ArrayObject([w.pages[0].indirect_reference, NameObject("/Fit")])
        ref = w._add_object(go)
        go[NameObject("/Next")] = ref
        w.root_object[NameObject("/OpenAction")] = ArrayObject([w.pages[0].indirect_reference, NameObject("/Fit")])
    editor = PDFEditor(write_pdf(tmp_path, change))
    assert not editor.security_report.findings
    editor.close()


def test_encrypted_active_content_is_checked_after_password(tmp_path):
    source = write_pdf(tmp_path, lambda w: w.root_object.update({NameObject("/OpenAction"): w._add_object(action("/JavaScript"))}), password="secret")
    with pytest.raises(PDFPasswordRequired):
        PDFEditor(source)
    with pytest.raises(PDFSecurityError, match="JavaScript"):
        PDFEditor(source, password="secret")


def test_incoming_active_pdf_does_not_change_history_or_source(tmp_path):
    source = write_pdf(tmp_path, filename="original.pdf")
    incoming = write_pdf(tmp_path, lambda w: w.pages[0].update({NameObject("/AA"): DictionaryObject({NameObject("/O"): w._add_object(action("/Launch"))})}), filename="incoming.pdf")
    editor = PDFEditor(source)
    original = editor.path.read_bytes()
    try:
        with pytest.raises(PDFSecurityError):
            editor.insert_pdf(incoming, 0)
        assert editor.path.read_bytes() == original and not editor.dirty
        assert not editor.can_undo
        assert not list(Path(editor._temp.name).glob("incoming_*"))
    finally:
        editor.close()


def test_word_export_refuses_active_pdf_and_preserves_existing_output(tmp_path):
    source = write_pdf(tmp_path, lambda w: w.root_object.update({NameObject("/OpenAction"): w._add_object(action("/Launch"))}))
    output = tmp_path / "output.docx"
    output.write_bytes(b"existing file")
    with pytest.raises(PDFSecurityError):
        convert_pdf_to_docx(source, output)
    assert output.read_bytes() == b"existing file"


@pytest.mark.parametrize("failure", ["timeout", "crash", "invalid-json", "incomplete", "unknown-finding"])
def test_scanner_failure_never_falls_back_to_unsafe_open(tmp_path, monkeypatch, failure):
    source = write_pdf(tmp_path)
    def result(*args, **kwargs):
        assert "secret" not in " ".join(args[0])
        assert b"secret" in kwargs["input"]
        assert kwargs["timeout"] == security.SCAN_TIMEOUT
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        payload = {"crash": b"", "invalid-json": b"error", "incomplete": b'{"status":"incomplete"}',
                   "unknown-finding": b'{"status":"checked","findings":["unknown"]}'}[failure]
        return subprocess.CompletedProcess(args[0], 1 if failure == "crash" else 0, payload)
    monkeypatch.setattr(security.subprocess, "run", result)
    with pytest.raises(PDFSecurityError):
        security.check_pdf(source, "secret")


def test_limits_large_file_page_count_and_malformed_input(tmp_path, monkeypatch):
    source = write_pdf(tmp_path)
    monkeypatch.setattr(security, "MAX_FILE_BYTES", 100)
    with pytest.raises(PDFSecurityError, match="256"):
        PDFEditor(source)
    monkeypatch.undo()
    writer = PdfWriter()
    for _ in range(security.MAX_PAGES + 1):
        writer.add_blank_page(100, 100)
    many = tmp_path / "many.pdf"
    writer.write(many)
    with pytest.raises(PDFSecurityError, match="3000"):
        PDFEditor(many)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\n malformed")
    with pytest.raises(PDFSecurityError):
        PDFEditor(broken)


def test_extreme_page_dimensions_are_blocked(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(200_000, 200_000)
    path = tmp_path / "huge.pdf"
    writer.write(path)
    with pytest.raises(PDFSecurityError, match="dimensioni"):
        PDFEditor(path)


def test_worker_ignores_pdf_directory_and_pythonpath(tmp_path, monkeypatch):
    source = write_pdf(tmp_path)
    # A PDF directory or inherited PYTHONPATH must not hijack worker imports.
    (tmp_path / "pypdf.py").write_text("raise RuntimeError('untrusted import')", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    report = security.check_pdf(source)
    assert report.findings == ()


def test_aes_encrypted_document_is_checked_with_the_correct_password(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    writer.encrypt("secret", algorithm="AES-256")
    source = tmp_path / "aes.pdf"
    writer.write(source)
    with pytest.raises(PDFPasswordRequired):
        PDFEditor(source)
    editor = PDFEditor(source, "secret")
    assert editor.encrypted and not editor.security_report.blocked
    editor.close()


@pytest.mark.parametrize("feature", ["javascript", "richmedia"])
def test_indirect_action_and_annotation_names_are_resolved(tmp_path, feature):
    def change(w):
        if feature == "javascript":
            value = DictionaryObject({NameObject("/S"): w._add_object(NameObject("/JavaScript"))})
            w.root_object[NameObject("/OpenAction")] = w._add_object(value)
        else:
            annotation = DictionaryObject({NameObject("/Subtype"): w._add_object(NameObject("/RichMedia"))})
            w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annotation)])
    with pytest.raises(PDFSecurityError, match="JavaScript" if feature == "javascript" else "multimediali"):
        PDFEditor(write_pdf(tmp_path, change))


def test_unknown_additional_action_in_indirect_map_is_blocked(tmp_path):
    source = write_pdf(tmp_path, lambda w: w.pages[0].update({NameObject("/AA"): w._add_object(DictionaryObject({
        NameObject("/O"): w._add_object(action("/Unrecognized"))}))}))
    with pytest.raises(PDFSecurityError, match="non supportate"):
        PDFEditor(source)


def test_active_content_in_compressed_objects_is_detected(tmp_path):
    import pymupdf
    original = write_pdf(tmp_path, lambda w: w.root_object.update({NameObject("/OpenAction"): w._add_object(action("/JavaScript"))}))
    compressed = tmp_path / "compressed.pdf"
    with pymupdf.open(original) as pdf:
        pdf.save(compressed, use_objstms=1)
    with PdfReader(compressed) as reader:
        assert reader.xref_objStm
    with pytest.raises(PDFSecurityError, match="JavaScript"):
        PDFEditor(compressed)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_xml_entity_detection_also_handles_wide_encodings(tmp_path, encoding):
    def change(w):
        stream = DecodedStreamObject()
        stream.set_data('<!DOCTYPE x [<!ENTITY e "test">]><x>&e;</x>'.encode(encoding))
        stream[NameObject("/Type")] = NameObject("/Metadata")
        w.root_object[NameObject("/Metadata")] = w._add_object(stream)
    with pytest.raises(PDFSecurityError, match="XML"):
        PDFEditor(write_pdf(tmp_path, change))


def test_snapshot_never_deletes_an_existing_destination(tmp_path):
    source = write_pdf(tmp_path)
    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"existing contents")
    with pytest.raises(FileExistsError):
        security.copy_checked(source, destination)
    assert destination.read_bytes() == b"existing contents"


def test_xml_entities_are_blocked_even_without_stream_type_and_subtype(tmp_path):
    def change(w):
        stream = DecodedStreamObject()
        stream.set_data(b'<!DOCTYPE x [<!ENTITY e "test">]><x>&e;</x>')
        w.root_object[NameObject("/Metadata")] = w._add_object(stream)
    with pytest.raises(PDFSecurityError, match="XML"):
        PDFEditor(write_pdf(tmp_path, change))


@pytest.mark.parametrize("compressed", [False, True])
def test_actual_stream_bytes_are_bounded_after_pypdf_removes_length(tmp_path, monkeypatch, compressed):
    def change(writer):
        stream = DecodedStreamObject()
        stream.set_data(bytes(range(256)))
        if compressed:
            stream = stream.flate_encode()
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    source = write_pdf(tmp_path, change)
    monkeypatch.setattr(security, "MAX_STREAM_BYTES", 128)
    with PdfReader(source) as reader:
        stream = reader.pages[0]["/Contents"]
        assert "/Length" not in stream and len(stream._data) > 128
        with pytest.raises(PDFSecurityError, match="stream PDF"):
            security.inspect_reader(reader)


def test_oversized_stream_is_rejected_before_main_process_parse(tmp_path, monkeypatch):
    def change(writer):
        stream = DecodedStreamObject()
        stream.set_data(b" " * (security.MAX_STREAM_BYTES + 1))
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    source = write_pdf(tmp_path, change)
    monkeypatch.setattr("ape_pdf_studio.core.editor.PdfReader", lambda *a, **k: pytest.fail("Unsafe main process parse"))
    with pytest.raises(PDFSecurityError):
        PDFEditor(source)


def test_generated_active_revision_is_rejected_without_changing_history(tmp_path):
    source = write_pdf(tmp_path)
    editor = PDFEditor(source)
    original = editor.path
    def transform(writer, reader):
        writer.append(reader)
        writer.root_object[NameObject("/OpenAction")] = writer._add_object(action("/Launch"))
    try:
        with pytest.raises(PDFSecurityError, match="avvio"):
            editor._commit(transform)
        assert editor.path == original and not editor.dirty and not editor.can_undo
        assert not list(Path(editor._temp.name).glob("rev_*.pdf"))
        assert not editor.security_report.findings
    finally:
        editor.close()


def test_combining_valid_pdfs_cannot_exceed_the_page_limit(tmp_path):
    sources = []
    for number, count in enumerate((1500, 1501)):
        writer = PdfWriter()
        for _ in range(count):
            writer.add_blank_page(100, 100)
        path = tmp_path / f"part{number}.pdf"
        writer.write(path)
        sources.append(path)
    editor = PDFEditor(sources[0])
    original = editor.path
    try:
        with pytest.raises(PDFSecurityError, match="3000"):
            editor.insert_pdf(sources[1], 0)
        assert editor.path == original and not editor.dirty and not editor.can_undo
        assert not list(Path(editor._temp.name).glob("rev_*.pdf"))
    finally:
        editor.close()
