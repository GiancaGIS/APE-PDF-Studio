"""Conservative input policy. A structural check is not an antivirus or OS sandbox."""
from __future__ import annotations
from .localization import tr

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import sys

from .errors import PDFPasswordRequired, PDFSecurityError
from .worker_process import worker_command
from .provenance import propagate_zone

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_PAGES = 3000
MAX_OBJECTS = 100_000
# Visits include scalar values, not just indirect PDF objects. Sparse structure
# parent trees in ordinary tagged documents can alone contain 400,000+ nulls.
# Keep a bounded full scan, with the independent object, depth, time and memory
# limits still applying; never skip the remaining objects when a limit is hit.
MAX_NODES = 2_000_000
MAX_DEPTH = 64
MAX_STREAM_BYTES = 32 * 1024 * 1024
SCAN_TIMEOUT = 12

FINDINGS = {
    "javascript": "JavaScript incorporato · esecuzione disabilitata",
    "launch": "Azioni di avvio di file o programmi",
    "external_action": "Azioni esterne o invio/importazione di dati",
    "multimedia": "Contenuti multimediali attivi o 3D",
    "xfa": "Moduli dinamici XFA",
    "xml_entities": "Dichiarazioni DTD o entità nei metadati XML",
    "unsafe_uri": "Collegamenti a file locali o protocolli non consentiti",
    "unknown_action": "Azioni PDF non supportate dal controllo",
    "attachments": "Allegati incorporati: apertura ed esecuzione disabilitate",
    "external_links": "Collegamenti web/email: disabilitati fino alla conferma dell'utente",
}
# JavaScript remains detectable and is never executed: Qt receives scrubbed
# display copies, while all MuPDF documents use the non-scripting runtime guard.
BLOCKED = frozenset(FINDINGS) - {"javascript", "attachments", "external_links"}


@dataclass(frozen=True)
class SecurityReport:
    findings: tuple[str, ...] = ()
    memory_limited: bool = False

    @property
    def blocked(self):
        return bool(set(self.findings) & BLOCKED)

    @property
    def summary(self):
        return "\n".join("• " + tr(FINDINGS[key]) for key in self.findings)


def configure_parser():
    """Apply pypdf's supported resource limits, including disabling helper binaries."""
    from pypdf import overwrite_configuration
    overwrite_configuration(
        maximum_declared_stream_length=MAX_STREAM_BYTES,
        array_based_stream_maximum_output_length=MAX_STREAM_BYTES,
        zlib_maximum_output_length=MAX_STREAM_BYTES,
        lzw_maximum_output_length=MAX_STREAM_BYTES,
        run_length_maximum_output_length=MAX_STREAM_BYTES,
        jbig2_maximum_output_length=MAX_STREAM_BYTES,
        image_maximum_buffer_size=MAX_STREAM_BYTES,
        xmp_maximum_input_length=1_000_000,
        xmp_maximum_element_count=10_000,
        page_tree_maximum_entries=MAX_PAGES * 2,
        page_tree_maximum_depth=MAX_DEPTH,
        outline_maximum_entries=10_000,
        outline_maximum_depth=MAX_DEPTH,
        jbig2dec_binary=None,
    )


def copy_checked(source: Path, destination: Path):
    """Bounded snapshot: scan and later parse the same bytes, despite source changes."""
    if source.stat().st_size > MAX_FILE_BYTES:
        raise PDFSecurityError(tr("PDF bloccato: dimensione superiore al limite di 256 MiB."))
    created = False
    try:
        from .sandbox import in_appcontainer
        from .private_work import work_root
        from .artifacts import open_artifact
        context=None
        if not in_appcontainer():
            root=work_root()
            original=source.absolute()
            if original.is_relative_to(root):
                parts=original.relative_to(root).parts
                if len(parts)<2:raise PDFSecurityError('Snapshot fuori dalla sessione PDF.')
                context=open_artifact(original,root/parts[0],MAX_FILE_BYTES)
        with (context if context is not None else source.open('rb')) as incoming, destination.open("xb") as outgoing:
            created = True
            first = incoming.read(1024)
            if b"%PDF-" not in first:
                raise PDFSecurityError(tr("PDF bloccato: intestazione PDF non riconosciuta."))
            outgoing.write(first)
            total = len(first)
            while chunk := incoming.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise PDFSecurityError(tr("PDF bloccato: dimensione superiore al limite di 256 MiB."))
                outgoing.write(chunk)
        propagate_zone(destination, source)
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def check_pdf(snapshot: Path, password: str | None = None) -> SecurityReport:
    """Run the preflight without loading Qt/PDFium or exposing passwords in argv."""
    from .sandbox import in_appcontainer, sandbox_command
    from .private_work import PrivateDirectory
    if in_appcontainer():
        from pypdf import PdfReader
        configure_parser()
        with PdfReader(snapshot) as reader:
            if reader.is_encrypted and not reader.decrypt(password or ""):
                raise PDFPasswordRequired(tr("Password necessaria o non corretta."))
            report = SecurityReport(inspect_reader(reader), True)
        if report.blocked:
            raise PDFSecurityError(tr("Apertura bloccata dal controllo di sicurezza:\n\n") + report.summary)
        return report
    try:
        # Never grant an AppContainer the parent of a user-selected file.
        with PrivateDirectory() as work:
            checked = Path(work)/"input.pdf"
            copy_checked(snapshot, checked)
            result = subprocess.run(
                sandbox_command("security", work, timeout=SCAN_TIMEOUT*1000),
                input=json.dumps({"path": str(checked), "password": password}).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=SCAN_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise PDFSecurityError(tr("PDF bloccato: il controllo di sicurezza ha superato 12 secondi.")) from exc
    except OSError as exc:
        raise PDFSecurityError(tr("PDF bloccato: impossibile avviare il controllo di sicurezza.")) from exc
    try:
        payload = json.loads(result.stdout)
        if result.returncode != 0 or not isinstance(payload, dict):
            raise ValueError("worker failed")
        if payload.get("status") == "password":
            raise PDFPasswordRequired(tr("Password necessaria o non corretta."))
        if payload.get("status") != "checked":
            raise PDFSecurityError(tr("PDF bloccato: ") + payload.get("reason", tr("controllo incompleto.")))
        findings = payload["findings"]
        if not isinstance(findings, list) or any(key not in FINDINGS for key in findings):
            raise ValueError("invalid findings")
        report = SecurityReport(tuple(findings), bool(payload.get("memory_limited")))
    except (ValueError, KeyError, TypeError) as exc:
        raise PDFSecurityError(tr("PDF bloccato: controllo interrotto o risultato non leggibile.")) from exc
    if report.blocked:
        raise PDFSecurityError(tr("Apertura bloccata dal controllo di sicurezza:\n\n") + report.summary +
                               tr("\n\nIl file originale non è stato modificato. Il rilevamento non prova che il PDF sia un malware."))
    return report


def inspect_reader(reader) -> tuple[str, ...]:
    """Inspect decoded object names and references, never execute actions or attachments.

    Traverse reachable objects and xref entries (including compressed objects).
    Object streams may be decompressed by pypdf under its configured limits;
    page/image/attachment payloads are not decompressed by this scanner.
    """
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

    found = set()

    def resolved_scalar(value):
        seen = set()
        for _ in range(MAX_DEPTH):
            if not isinstance(value, IndirectObject):
                return value
            key = (value.idnum, value.generation)
            if key in seen:
                break
            seen.add(key)
            value = value.get_object()
        raise PDFSecurityError(tr("riferimenti non risolvibili durante il controllo."))

    def scalar_text(value):
        value = resolved_scalar(value)
        return str(value) if isinstance(value, str) else ""

    checked_xml = set()

    def inspect_xml(value):
        value = resolved_scalar(value)
        if not isinstance(value, StreamObject):
            raise PDFSecurityError(tr("stream di metadati XML non leggibile."))
        if id(value) in checked_xml:
            return
        checked_xml.add(id(value))
        data = value.get_data()
        if len(data) > 1_000_000:
            raise PDFSecurityError(tr("metadati XML troppo grandi per il controllo."))
        # Removing NULs also detects UTF-16/UTF-32 XML declarations.
        xml = data.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            found.add("xml_entities")

    pages = reader.pages
    if not 0 < len(pages) <= MAX_PAGES:
        raise PDFSecurityError(tr("numero di pagine non consentito (limite: 3000)."))
    for page in pages:
        unit = float(page.get("/UserUnit", 1))
        dimensions = (float(page.mediabox.width) * unit, float(page.mediabox.height) * unit,
                      float(page.cropbox.width) * unit, float(page.cropbox.height) * unit)
        if not math.isfinite(unit) or unit <= 0 or any(not math.isfinite(x) or not 0 < x <= 14400 for x in dimensions):
            raise PDFSecurityError(tr("dimensioni di pagina anomale o superiori a 200 pollici."))

    references = [(generation, number) for generation, entries in reader.xref.items()
                  for number in entries if number and generation != 65535]
    references.extend((0, number) for number in reader.xref_objStm)
    if len(references) > MAX_OBJECTS:
        raise PDFSecurityError(tr("troppi oggetti PDF per completare il controllo."))
    stack = [(reader.trailer, 0, False)]
    stack.extend((IndirectObject(number, generation, reader), 0, False) for generation, number in references)
    seen_refs, seen_containers = set(), set()
    visited = 0
    external_actions = {"/GoToR", "/GoToE", "/SubmitForm", "/ImportData"}
    multimedia_actions = {"/Rendition", "/Sound", "/Movie"}
    while stack:
        value, depth, action_context = stack.pop()
        visited += 1
        if visited > MAX_NODES or len(stack) > MAX_NODES or depth > MAX_DEPTH:
            raise PDFSecurityError(tr("struttura troppo complessa per completare il controllo."))
        if isinstance(value, IndirectObject):
            identity = (value.idnum, value.generation, action_context)
            if identity in seen_refs:
                continue
            seen_refs.add(identity)
            value = value.get_object()
        if isinstance(value, (DictionaryObject, ArrayObject)):
            identity = (id(value), action_context)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
        if isinstance(value, DictionaryObject):
            action = scalar_text(value.get("/S", ""))
            subtype = scalar_text(value.get("/Subtype", ""))
            object_type = scalar_text(value.get("/Type", ""))
            if action == "/JavaScript" or "/JS" in value or "/JavaScript" in value:
                found.add("javascript")
            if action == "/Launch":
                found.add("launch")
            if action in external_actions:
                found.add("external_action")
            if action in multimedia_actions or subtype in ("/RichMedia", "/3D", "/Movie", "/Sound"):
                found.add("multimedia")
            if "/XFA" in value:
                found.add("xfa")
            if "/EmbeddedFiles" in value or "/EF" in value or object_type == "/EmbeddedFile":
                found.add("attachments")
            if action == "/URI":
                from urllib.parse import urlsplit
                uri = scalar_text(value.get("/URI", ""))
                if urlsplit(uri).scheme.lower() in ("http", "https", "mailto"):
                    found.add("external_links")
                else:
                    found.add("unsafe_uri")
            if (action_context and "/S" in value) or object_type == "/Action":
                if action not in ("/GoTo", "/URI", "/JavaScript", "/Launch", *external_actions, *multimedia_actions):
                    found.add("unknown_action")
            if isinstance(value, StreamObject):
                # pypdf removes /Length while reading a stream. Check the bytes
                # it actually stored, rather than relying on the removed key.
                if len(value._data) > MAX_STREAM_BYTES or int(value.get("/Length", 0)) > MAX_STREAM_BYTES:
                    raise PDFSecurityError(tr("stream PDF superiore al limite di 32 MiB."))
                if object_type == "/Metadata" or subtype == "/XML":
                    inspect_xml(value)
            for key, child in value.items():
                if key == "/Metadata":
                    # pypdf reads catalog XMP even when Type/Subtype are missing.
                    inspect_xml(child)
                if key == "/AA":
                    action_map = resolved_scalar(child)
                    if not isinstance(action_map, DictionaryObject):
                        raise PDFSecurityError(tr("azioni automatiche non leggibili."))
                    stack.extend((entry, depth + 1, True) for entry in action_map.values())
                else:
                    stack.append((child, depth + 1, key in ("/A", "/OpenAction", "/Next")))
        elif isinstance(value, ArrayObject):
            stack.extend((child, depth + 1, action_context) for child in value)
    return tuple(sorted(found))
