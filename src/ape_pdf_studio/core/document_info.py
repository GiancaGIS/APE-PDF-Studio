"""Inspect PDF identification and signature structures without claiming validity."""
from __future__ import annotations
from .localization import tr

from dataclasses import dataclass

from pypdf import PdfReader
from pypdf._utils import parse_iso8824_date


@dataclass(frozen=True)
class SignatureInfo:
    field: str
    signer: str = ""
    date: str = ""
    reason: str = ""
    subfilter: str = ""


MAX_SIGNATURE_CERTIFICATES = 24


@dataclass(frozen=True)
class SignatureCertificate:
    subject: str
    issuer: str
    serial: str
    valid_from: str
    valid_until: str
    sha256: str
    referenced: bool


@dataclass(frozen=True)
class DocumentInfo:
    pdfa: str | None = None
    signatures: tuple[SignatureInfo, ...] = ()
    empty_signature_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def pdf_string_bytes(value) -> bytes:
    if isinstance(value, bytes):
        return value
    try:
        return value.original_bytes if isinstance(value, str) else b""
    except (AttributeError, ValueError):
        return b""


def _declared_date(value) -> str:
    text = str(value)
    digits = text.removeprefix("D:")[:14]
    if len(digits) == 14 and digits.isascii() and digits.isdigit() and len(text) <= 23:
        try:
            return parse_iso8824_date(text).isoformat(sep=" ", timespec="seconds")
        except ValueError:
            pass
    return text


def inspect_document(reader: PdfReader, *, on_signature=None) -> DocumentInfo:
    """Read XMP identification and populated signature fields, including DocMDP.

    This does not verify CMS signatures, certificates, timestamps or PDF/A rules.
    Empty signature widgets are distinguished from actual embedded signature data.
    """
    warnings = []
    pdfa = None
    try:
        xmp = reader.xmp_metadata
        if xmp:
            part = str(xmp.pdfaid_part or "").strip()
            level = str(xmp.pdfaid_conformance or "").strip().upper()
            valid = ((part == "1" and level in ("A", "B")) or
                     (part in ("2", "3") and level in ("A", "B", "U")) or
                     (part == "4" and level in ("", "E", "F")))
            if valid:
                pdfa = f"PDF/A-{part}{level.lower()}"
            elif part or level:
                warnings.append(tr("Identificazione PDF/A incompleta o non riconosciuta."))
    except Exception:
        warnings.append(tr("Metadati XMP non leggibili: identificazione PDF/A non disponibile."))

    signatures = []
    empty = []
    seen_fields = set()
    seen_signatures = set()

    def resolved(value):
        return value.get_object() if hasattr(value, "get_object") else value

    def key(value):
        reference = getattr(value, "indirect_reference", value)
        if hasattr(reference, "idnum"):
            return reference.idnum, reference.generation
        return id(value)

    def signature(value, field):
        identity = key(value)
        value = resolved(value)
        if not isinstance(value, dict):
            return False
        contents = pdf_string_bytes(resolved(value.get("/Contents")))
        if not contents.strip(b"\x00"):
            return False
        if identity in seen_signatures:
            return True
        seen_signatures.add(identity)
        signatures.append(SignatureInfo(
            field=field, signer=str(value.get("/Name", "")),
            date=_declared_date(value.get("/M", "")), reason=str(value.get("/Reason", "")),
            subfilter=str(value.get("/SubFilter", "")),
        ))
        if on_signature is not None:
            on_signature(len(signatures) - 1, value, contents)
        ranges = resolved(value.get("/ByteRange"))
        if not isinstance(ranges, list) or len(ranges) < 4:
            warnings.append(tr("Firma '{0}': struttura ByteRange incompleta.", field))
        return True

    try:
        root = reader.root_object
        form = resolved(root.get("/AcroForm", {}))
        fields = resolved(form.get("/Fields", [])) if isinstance(form, dict) else []
        stack = []
        # Include orphan widgets found on pages, following their parent fields.
        for page in reader.pages:
            for ref in resolved(page.get("/Annots", [])):
                widget = resolved(ref)
                if isinstance(widget, dict) and widget.get("/Subtype") == "/Widget":
                    stack.append((widget.get("/Parent", ref), "", None, None))
        # Process the form tree first, retaining fully qualified field names.
        stack.extend((ref, "", None, None) for ref in fields)
        while stack:
            ref, prefix, inherited_type, inherited_value = stack.pop()
            identity = key(ref)
            if identity in seen_fields:
                continue
            seen_fields.add(identity)
            field = resolved(ref)
            if not isinstance(field, dict):
                continue
            name = str(field.get("/T", ""))
            full_name = ".".join(p for p in (prefix, name) if p)
            field_type = field.get("/FT", inherited_type)
            value = field.get("/V", inherited_value)
            children = resolved(field.get("/Kids", []))
            container = any(isinstance(resolved(child), dict) and resolved(child).get("/T") for child in children)
            if field_type == "/Sig":
                label = full_name or tr("Firma senza nome")
                if not signature(value, label) and label not in empty and not container:
                    empty.append(label)
            stack.extend((child, full_name, field_type, value) for child in children)
        perms = resolved(root.get("/Perms", {}))
        if isinstance(perms, dict) and perms.get("/DocMDP"):
            signature(perms["/DocMDP"], tr("Firma di certificazione"))
    except Exception:
        warnings.append(tr("Lettura dei campi firma incompleta."))
    return DocumentInfo(pdfa, tuple(signatures), tuple(empty), tuple(dict.fromkeys(warnings)))
