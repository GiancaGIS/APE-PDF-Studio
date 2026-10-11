"""Read-only, bounded metadata replies for the native Windows properties page."""
from __future__ import annotations

import io
import os
import struct
import unicodedata

from .localization import tr

MAX_METADATA_BYTES = 256 * 1024
MAX_TEXT_CHARS = 4096
HEADER = struct.Struct("<4sIIII")


def _text(value):
    text = str(value)[:MAX_TEXT_CHARS]
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def metadata_reply(data: bytes, language: int) -> bytes:
    from pypdf import PdfReader
    from pypdf.constants import UserAccessPermissions
    from .document_info import inspect_document
    from .metadata import EDITABLE_FIELDS, read_metadata
    from .security import BLOCKED, MAX_FILE_BYTES, configure_parser, inspect_reader

    if language not in (0, 1, 2, 3) or not data or len(data) > MAX_FILE_BYTES or b"%PDF-" not in data[:1024]:
        raise ValueError("Invalid metadata request")
    os.environ["APE_UI_LANGUAGE"] = ("it", "en", "es", "fr")[language]
    configure_parser()
    try:
        with PdfReader(io.BytesIO(data)) as reader:
            if reader.is_encrypted and not reader.decrypt(""):
                return HEADER.pack(b"APM1", 1, 0, 0, 0)
            if set(inspect_reader(reader)) & BLOCKED:
                return HEADER.pack(b"APM1", 2, 0, 0, 0)
            snapshot = read_metadata(reader)
            info = inspect_document(reader)
            rows = [(tr(label), snapshot.values[key] or tr("Non indicato")) for key, label in EDITABLE_FIELDS.items()]
            rows.extend((
                (tr("Produttore"), snapshot.info.get("/Producer") or tr("Non indicato")),
                (tr("Data di creazione"), snapshot.info.get("/CreationDate") or tr("Non indicata")),
                (tr("Data di modifica"), snapshot.info.get("/ModDate") or tr("Non indicata")),
                (tr("Pagine"), str(snapshot.page_count)),
                (tr("Versione PDF"), snapshot.version.removeprefix("%PDF-")),
                ("PDF/A", tr("{0} dichiarato", info.pdfa) if info.pdfa else tr("Non dichiarato")),
                (tr("Firme digitali"), tr("{0} · validità non verificata", len(info.signatures)) if info.signatures else "0"),
                (tr("Protezione"), tr("PDF protetto: metadati consultabili in sola lettura.") if reader.is_encrypted else tr("Nessuna cifratura")),
            ))
            permissions = UserAccessPermissions(int(reader.trailer["/Encrypt"]["/P"])) if reader.is_encrypted else None
            for label, flag in (("Stampa", UserAccessPermissions.PRINT),
                                ("Modifica contenuto", UserAccessPermissions.MODIFY),
                                ("Annotazioni", UserAccessPermissions.ADD_OR_MODIFY),
                                ("Copia del testo", UserAccessPermissions.EXTRACT)):
                rows.append((tr(label), tr("Consentita") if permissions is None or permissions & flag else tr("Non consentita")))
            warnings = snapshot.warnings + info.warnings
            if snapshot.conflicts:
                warnings += (tr("Differenze tra metadati Info e XMP."),)
            if warnings:
                rows.append((tr("Avvisi"), "\n".join(warnings)))
        payload = bytearray()
        for label, value in rows:
            for text in (label, value):
                encoded = _text(text).encode("utf-16-le", errors="replace")
                payload.extend(struct.pack("<I", len(encoded)))
                payload.extend(encoded)
        if len(payload) > MAX_METADATA_BYTES or len(rows) > 32:
            raise ValueError("Metadata reply too large")
        return HEADER.pack(b"APM1", 0, len(rows), len(payload), 0) + payload
    except Exception:
        return HEADER.pack(b"APM1", 2, 0, 0, 0)
