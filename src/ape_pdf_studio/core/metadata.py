"""Read document metadata and synchronize editable Info/XMP properties."""
from __future__ import annotations
from .localization import tr

from dataclasses import dataclass
import re

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject
from pypdf.xmp import DC_NAMESPACE, PDF_NAMESPACE, RDF_NAMESPACE, XMP_NAMESPACE


EDITABLE_FIELDS = {
    "/Title": "Titolo",
    "/Author": "Autore",
    "/Subject": "Oggetto",
    "/Keywords": "Parole chiave",
    "/Creator": "Applicazione creatrice",
}


@dataclass(frozen=True)
class MetadataSnapshot:
    values: dict[str, str]
    info: dict[str, str]
    xmp_values: dict[str, str]
    xml: str
    warnings: tuple[str, ...]
    conflicts: tuple[str, ...]
    editable: bool
    page_count: int
    version: str


def _lang_default(values):
    values = values or {}
    return values.get("x-default") or next(iter(values.values()), "")


def read_metadata(reader: PdfReader) -> MetadataSnapshot:
    warnings = []
    editable = not reader.is_encrypted
    try:
        info = {str(key): str(value) if value is not None else ""
                for key, value in (reader.metadata or {}).items()}
    except Exception:
        info = {}
        editable = False
        warnings.append(tr("Dizionario Info non leggibile: modifica dei metadati disabilitata."))
    xml = ""
    xmp_values = {}
    stream = reader.root_object.get("/Metadata")
    if stream is not None:
        try:
            data = stream.get_object().get_data()
            xml = data.decode("utf-8", errors="replace")
            xmp = reader.xmp_metadata
            if xmp is None:
                raise ValueError(tr("pacchetto XMP non riconosciuto"))
            xml = xmp.rdf_root.ownerDocument.toprettyxml(indent="  ")
            xmp_values = {
                "/Title": _lang_default(xmp.dc_title),
                "/Author": "; ".join(xmp.dc_creator or []),
                "/Subject": _lang_default(xmp.dc_description),
                "/Keywords": xmp.pdf_keywords or ", ".join(xmp.dc_subject or []),
                "/Creator": xmp.xmp_creator_tool or "",
            }
        except Exception:
            editable = False
            warnings.append(tr("XMP non leggibile: modifica dei metadati disabilitata per preservare i dati esistenti."))
    values = {key: info[key] if key in info else xmp_values.get(key, "") for key in EDITABLE_FIELDS}
    conflicts = tuple(key for key in EDITABLE_FIELDS
                      if key in info and key in xmp_values and info[key] != xmp_values[key] and xmp_values[key])
    if reader.is_encrypted:
        warnings.append(tr("PDF protetto: metadati consultabili in sola lettura."))
    return MetadataSnapshot(values, info, xmp_values, xml, tuple(warnings), conflicts, editable,
                            len(reader.pages), reader.pdf_header)


def valid_xml_text(value: str) -> bool:
    return all(ch in "\t\n\r" or 0x20 <= ord(ch) <= 0xD7FF or
               0xE000 <= ord(ch) <= 0xFFFD or 0x10000 <= ord(ch) <= 0x10FFFF for ch in value)


def _remove_xmp_property(xmp, namespace, name):
    # A property may occur in several top-level rdf:Description nodes or as an
    # attribute. Remove those duplicates before the public setter inserts it.
    for desc in list(xmp.rdf_root.childNodes):
        if desc.namespaceURI != RDF_NAMESPACE or desc.localName != "Description":
            continue
        for node in list(desc.childNodes):
            if node.namespaceURI == namespace and node.localName == name:
                desc.removeChild(node)
        attr = desc.getAttributeNodeNS(namespace, name)
        if attr is not None:
            desc.removeAttributeNode(attr)


def write_metadata(writer: PdfWriter, changes: dict[str, str]) -> None:
    """Writer must already contain a cloned document, including its catalog."""
    if writer._info is None:
        writer.add_metadata({})
    info = writer._info
    for key, value in changes.items():
        if value:
            info[NameObject(key)] = TextStringObject(value)
        else:
            info.pop(NameObject(key), None)
    xmp = writer.xmp_metadata
    if xmp is None:
        return  # Keep Info-only documents Info-only.
    # pypdf's setters write into the first description using these prefixes.
    # Prefixes declared on a different sibling are out of scope there. Use a
    # dedicated description so existing custom prefix bindings stay intact.
    namespaces = {"rdf": RDF_NAMESPACE, "dc": DC_NAMESPACE, "pdf": PDF_NAMESPACE, "xmp": XMP_NAMESPACE}
    descriptions = [node for node in xmp.rdf_root.childNodes
                    if node.namespaceURI == RDF_NAMESPACE and node.localName == "Description"
                    and node.getAttributeNS(RDF_NAMESPACE, "about") == ""]
    if not descriptions or any(descriptions[0].getAttribute("xmlns:" + prefix) != uri
                               for prefix, uri in namespaces.items()):
        desc = xmp.rdf_root.ownerDocument.createElementNS(RDF_NAMESPACE, "rdf:Description")
        desc.setAttributeNS(RDF_NAMESPACE, "rdf:about", "")
        for prefix, uri in namespaces.items():
            desc.setAttribute("xmlns:" + prefix, uri)
        xmp.rdf_root.insertBefore(desc, xmp.rdf_root.firstChild)
    properties = {
        "/Title": (DC_NAMESPACE, "title", "dc_title"),
        "/Author": (DC_NAMESPACE, "creator", "dc_creator"),
        "/Subject": (DC_NAMESPACE, "description", "dc_description"),
        "/Keywords": (PDF_NAMESPACE, "Keywords", "pdf_keywords"),
        "/Creator": (XMP_NAMESPACE, "CreatorTool", "xmp_creator_tool"),
    }
    for key, value in changes.items():
        namespace, name, attribute = properties[key]
        translated = dict(getattr(xmp, attribute) or {}) if key in ("/Title", "/Subject") else None
        _remove_xmp_property(xmp, namespace, name)
        if key in ("/Title", "/Subject"):
            translated["x-default"] = value
            new_value = translated if value else None
        elif key == "/Author":
            new_value = [part.strip() for part in value.split(";") if part.strip()] or None
        else:
            new_value = value or None
        setattr(xmp, attribute, new_value)
        if key == "/Keywords":
            _remove_xmp_property(xmp, DC_NAMESPACE, "subject")
            xmp.dc_subject = [part.strip() for part in re.split("[,;]", value) if part.strip()] or None
    writer.xmp_metadata = xmp
    # Reparse serialized XML before committing the revision.
    _ = writer.xmp_metadata
