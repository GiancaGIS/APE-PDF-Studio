"""Exercise signature identification and certificate data independently of trust."""
from datetime import datetime, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from ape_pdf_studio.core import rpc_codec, rpc_validation
from ape_pdf_studio.core.document_info import MAX_SIGNATURE_CERTIFICATES, inspect_document
from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.signature_certificates import read_signature_certificates


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


def add_signature(writer, *, populated=True, field_name="Signature1", certify=False, contents=None):
    signature = DictionaryObject({
        NameObject("/Type"): NameObject("/Sig"),
        NameObject("/SubFilter"): NameObject("/adbe.pkcs7.detached"),
        NameObject("/Contents"): ByteStringObject(
            contents if contents is not None else (b"test-signature-data" if populated else b"\x00" * 24)
        ),
        NameObject("/ByteRange"): ArrayObject([NumberObject(n) for n in (0, 100, 200, 100)]),
        NameObject("/Name"): TextStringObject("Test Signer"),
        NameObject("/M"): TextStringObject("D:20261121120000+01'00'"),
        NameObject("/Reason"): TextStringObject("Test only"),
    })
    value = writer._add_object(signature)
    field = DictionaryObject({NameObject("/FT"): NameObject("/Sig"),
                              NameObject("/T"): TextStringObject(field_name), NameObject("/V"): value})
    field_ref = writer._add_object(field)
    if "/AcroForm" not in writer.root_object:
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(
            DictionaryObject({NameObject("/Fields"): ArrayObject()})
        )
    writer.root_object["/AcroForm"]["/Fields"].append(field_ref)
    if certify:
        writer.root_object[NameObject("/Perms")] = DictionaryObject({NameObject("/DocMDP"): value})
    return field_ref, value


def certificate_fixture(*, conflicting_serial=False):
    authority_key = ec.generate_private_key(ec.SECP256R1())
    signer_key = ec.generate_private_key(ec.SECP256R1())
    authority = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test Certificate Authority")])
    signer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Embedded PDF Signer")])

    def certificate(subject, key, serial):
        return (x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(authority)
                .public_key(key.public_key())
                .serial_number(serial)
                .not_valid_before(datetime(2026, 1, 1, tzinfo=timezone.utc))
                .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
                .sign(authority_key, hashes.SHA256()))

    ca = certificate(authority, authority_key, 42 if conflicting_serial else 1)
    leaf = certificate(signer, signer_key, 42)
    cms = (pkcs7.PKCS7SignatureBuilder()
           .set_data(b"PDF signature test")
           .add_signer(leaf, signer_key, hashes.SHA256())
           .add_certificate(ca)
           .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature]))
    return cms, leaf, ca


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
        assert info.signatures[0].date == "2026-11-21 12:00:00+01:00"


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


@pytest.mark.parametrize("encrypted", [False, True])
def test_signature_certificates_from_original_revision(tmp_path, encrypted):
    cms, leaf, ca = certificate_fixture()
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    add_signature(writer, field_name="Unreadable")
    add_signature(writer, field_name="With certificate", contents=cms + b"\x00" * 256)
    if encrypted:
        writer.encrypt("secret")
    source = tmp_path / "signatures.pdf"
    writer.write(source)
    editor = PDFEditor(source, password="secret" if encrypted else None)
    try:
        if not encrypted:
            editor.insert_blank(0)
            assert editor.modified_from_original
        signed = next(i for i, signature in enumerate(editor.document_info.signatures)
                      if signature.field == "With certificate")
        certificates = editor.signature_certificates(signed)
        assert editor.signature_certificates(1 - signed) == ()
        with pytest.raises(PDFOperationError):
            editor.signature_certificates(True)
        assert [certificate.sha256 for certificate in certificates] == [
            c.fingerprint(hashes.SHA256()).hex() for c in (leaf, ca)
        ]
        assert [certificate.referenced for certificate in certificates] == [True, False]
        assert certificates[0].subject == "CN=Embedded PDF Signer"
        assert certificates[0].issuer == "CN=Test Certificate Authority"
        assert certificates[0].serial == "2A"
        assert certificates[0].valid_from == "2026-01-01T00:00:00+00:00"
        assert certificates[0].valid_until == "2030-01-01T00:00:00+00:00"
    finally:
        editor.close()


def test_legacy_signature_uses_the_declared_certificate(tmp_path):
    _, leaf, ca = certificate_fixture()
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    _, signature_ref = add_signature(writer)
    signature = signature_ref.get_object()
    signature[NameObject("/SubFilter")] = NameObject("/adbe.x509.rsa_sha1")
    signature[NameObject("/Cert")] = ArrayObject([
        ByteStringObject(cert.public_bytes(serialization.Encoding.DER)) for cert in (leaf, ca)
    ])
    inspect_writer(writer, tmp_path)
    with PdfReader(tmp_path / "metadata.pdf") as reader:
        certificates = read_signature_certificates(reader, 0)
    assert [certificate.subject for certificate in certificates] == ["CN=Embedded PDF Signer", "CN=Test Certificate Authority"]
    assert [certificate.referenced for certificate in certificates] == [True, False]


def test_conflicting_signer_identifiers_do_not_label_a_certificate(tmp_path):
    cms, _, _ = certificate_fixture(conflicting_serial=True)
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    add_signature(writer, contents=cms)
    inspect_writer(writer, tmp_path)
    with PdfReader(tmp_path / "metadata.pdf") as reader:
        certificates = read_signature_certificates(reader, 0)
    assert len(certificates) == 2
    assert not any(certificate.referenced for certificate in certificates)


@pytest.mark.parametrize("contents", [b"\x30\x80\xff", b"\x30\x84\x7f\xff\xff\xff"])
def test_malformed_cms_does_not_hide_the_signature(tmp_path, contents):
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    add_signature(writer, contents=contents)
    info = inspect_writer(writer, tmp_path)
    assert len(info.signatures) == 1
    with PdfReader(tmp_path / "metadata.pdf") as reader:
        assert read_signature_certificates(reader, 0) == ()


def test_certificate_ipc_validates_fields_and_counts(tmp_path):
    cms, _, _ = certificate_fixture()
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    add_signature(writer, contents=cms)
    inspect_writer(writer, tmp_path)
    with PdfReader(tmp_path / "metadata.pdf") as reader:
        certificates = read_signature_certificates(reader, 0)
    assert rpc_codec.decode(rpc_codec.encode(certificates)) == certificates
    encoded = rpc_codec.encode(certificates[0])
    encoded["fields"]["valid_until"] = "2030-23-01T00:00:00+00:00"
    with pytest.raises(ValueError):
        rpc_codec.decode(encoded)
    with pytest.raises(ValueError):
        rpc_validation.result("signature_certificates", certificates * MAX_SIGNATURE_CERTIFICATES)
