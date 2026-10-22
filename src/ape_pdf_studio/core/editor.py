"""Non-destructive PDF operations and revision history; no Qt imports.

Each edit creates a full PDF snapshot in a per-document temporary directory.
This favors reliable undo/redo over speed on very large files.
Saving uses an atomic replacement in the destination directory.
"""

from __future__ import annotations
from .localization import tr

import os
import math
import shutil
import tempfile
from pathlib import Path
from .user_paths import user_path, same_file
from typing import Callable
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Highlight, Text
from pypdf.generic import ArrayObject, FloatObject, NameObject, DictionaryObject, DecodedStreamObject, TextStringObject, NumberObject

from .document_info import inspect_document
from .metadata import EDITABLE_FIELDS, MetadataSnapshot, read_metadata, valid_xml_text, write_metadata
from .errors import PDFOperationError, PDFPasswordRequired, PDFSecurityError
from .security import SecurityReport, check_pdf, configure_parser, copy_checked
from .page_coordinates import to_pdf, from_pdf, displayed_size
from .provenance import propagate_zone
from .private_work import PrivateDirectory, check_budget


class PDFEditor:
    """Owns one PDF's immutable source, working snapshots and save target."""

    def __init__(self, source: str | Path, password: str | None = None):
        self.source = user_path(source)
        if not self.source.is_file() or self.source.suffix.lower() != ".pdf":
            raise PDFOperationError(tr("Seleziona un file PDF esistente."))
        self._temp = PrivateDirectory()
        original = Path(self._temp.name) / "original.pdf"
        try:
            copy_checked(self.source, original)
            self.security_report = check_pdf(original, password)
            configure_parser()
            with PdfReader(original) as reader:
                self.encrypted = reader.is_encrypted
                if self.encrypted and not reader.decrypt(password or ""):
                    raise PDFPasswordRequired(tr("Password necessaria o non corretta."))
                if not len(reader.pages):
                    raise PDFOperationError(tr("Il PDF non contiene pagine."))
                self.document_info = inspect_document(reader)
                self._original_metadata = read_metadata(reader)
        except PDFOperationError:
            self._temp.cleanup()
            raise
        except Exception as exc:
            self._temp.cleanup()
            raise PDFOperationError(tr('Impossibile leggere il PDF: {0}', exc)) from exc
        self._revisions: list[Path] = [original]
        self._index = 0
        self._saved_index = 0
        self.output_path = self.source
        self._password = password

    @property
    def path(self) -> Path:
        return self._revisions[self._index]

    @property
    def dirty(self) -> bool:
        return self._index != self._saved_index

    @property
    def modified_from_original(self) -> bool:
        return self._index != 0

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._revisions) - 1

    def _reader(self, path: Path | None = None) -> PdfReader:
        if self.encrypted:
            raise PDFOperationError(
                tr("I PDF protetti da password sono in sola lettura in questa versione.")
            )
        try:
            return PdfReader(path or self.path, strict=False)
        except Exception as exc:
            raise PDFOperationError(tr('Impossibile elaborare il PDF: {0}', exc)) from exc

    @staticmethod
    def _copy_metadata(writer: PdfWriter, reader: PdfReader) -> None:
        if reader.metadata:
            values = {
                str(key): str(value)
                for key, value in reader.metadata.items()
                if isinstance(key, str) and key.startswith("/") and value is not None
            }
            if values:
                writer.add_metadata(values)
        metadata = reader.root_object.get("/Metadata")
        if metadata is not None and "/Metadata" not in writer.root_object:
            writer.root_object[NameObject("/Metadata")] = metadata.clone(writer)

    def _commit(self, transform: Callable[[PdfWriter, PdfReader], None], *, copy_metadata: bool = True, origins=()) -> None:
        check_budget(Path(self._temp.name), self.path.stat().st_size)
        reader = self._reader()
        candidate = Path(self._temp.name) / f"rev_{uuid4().hex}.pdf"
        try:
            writer = PdfWriter()
            transform(writer, reader)
            if len(writer.pages) == 0:
                raise PDFOperationError(tr("Il documento deve contenere almeno una pagina."))
            if copy_metadata:
                self._copy_metadata(writer, reader)
            with candidate.open("wb") as stream:
                writer.write(stream)
            propagate_zone(candidate, self.path, *origins)
            check_budget(Path(self._temp.name))
            # Combined pages and generated content must still satisfy the input
            # policy before the revision is parsed or passed to the renderer.
            report = check_pdf(candidate)
            # Verify the PDF was produced before changing history.
            check = PdfReader(candidate)
            count = len(check.pages)
            check.close()
            if count == 0:
                raise PDFOperationError(tr("Il PDF generato è vuoto."))
        except PDFOperationError:
            candidate.unlink(missing_ok=True)
            raise
        except Exception as exc:
            candidate.unlink(missing_ok=True)
            raise PDFOperationError(tr('Modifica non riuscita: {0}', exc)) from exc
        finally:
            reader.close()

        obsolete = self._revisions[self._index + 1:]
        for old in obsolete:
            if old != self.source:
                old.unlink(missing_ok=True)
        if self._saved_index > self._index:
            self._saved_index = -1
        self._revisions = self._revisions[: self._index + 1] + [candidate]
        self._index += 1
        self.security_report = SecurityReport(
            tuple(sorted(set(self.security_report.findings) | set(report.findings))),
            self.security_report.memory_limited and report.memory_limited,
        )

    @staticmethod
    def _valid_page(page: int, count: int) -> None:
        if not 0 <= page < count:
            raise PDFOperationError(tr('Pagina non valida: {0}.', page + 1))

    def metadata(self) -> MetadataSnapshot:
        if self.encrypted:
            return self._original_metadata
        reader = self._reader()
        try:
            return read_metadata(reader)
        finally:
            reader.close()

    def update_metadata(self, values: dict[str, str]) -> None:
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti sono in sola lettura."))
        if not isinstance(values, dict) or any(key not in EDITABLE_FIELDS for key in values):
            raise PDFOperationError(tr("Si possono modificare soltanto i campi descrittivi del documento."))
        if any(not isinstance(value, str) or not valid_xml_text(value) for value in values.values()):
            raise PDFOperationError(tr("I metadati contengono caratteri non validi."))
        current = self.metadata()
        if not current.editable:
            raise PDFOperationError(tr("I metadati XMP non sono leggibili: modifica disabilitata."))
        changes = {key: value for key, value in values.items() if value != current.values[key]}
        if not changes:
            return

        def op(writer: PdfWriter, reader: PdfReader) -> None:
            # Preserve outlines, forms, attachments and custom metadata. This
            # still rewrites the file, so the original signatures are invalidated.
            writer.clone_document_from_reader(reader)
            write_metadata(writer, changes)

        self._commit(op, copy_metadata=False)

    def interactions(self):
        """Inspect links and static fields in the already checked revision."""
        from .pdf_links import read_links
        from .pdf_forms import read_fields
        with PdfReader(self.path) as reader:
            if reader.is_encrypted and not reader.decrypt(self._password or ""):
                raise PDFPasswordRequired(tr("Password necessaria o non corretta."))
            return read_links(reader), read_fields(reader)

    def fill_form(self, values: dict, revision: str | Path) -> None:
        from .pdf_forms import read_fields, validate_changes, write_values
        if Path(revision) != self.path:
            raise PDFOperationError(tr("Il documento è cambiato. Riapri il modulo prima di compilare."))
        with self._reader() as reader:
            changes = validate_changes(read_fields(reader), values)
        if not changes:
            return

        def op(writer, reader):
            writer.clone_document_from_reader(reader)
            writer.pdf_header = reader.pdf_header
            write_values(writer, changes)

        self._commit(op, copy_metadata=False)

    def insert_blank(self, index: int, reference: int = 0, paper: str = 'same') -> None:
        from .document_tools import insert_blank
        self._commit(lambda writer, reader: insert_blank(writer, reader, index, reference, paper))

    def add_watermark(self, text, size, color, opacity, angle) -> None:
        from .document_tools import watermark
        self._commit(lambda writer, reader: watermark(writer, reader, text, size, color, opacity, angle))

    def ink(self):
        from .ink import read_ink
        with PdfReader(self.path) as reader:
            if reader.is_encrypted and not reader.decrypt(self._password or ''):
                raise PDFPasswordRequired(tr("Password necessaria o non corretta."))
            return read_ink(reader)

    def update_ink(self, strokes, deleted, revision) -> None:
        from .ink import apply_ink
        if str(revision) != str(self.path):
            raise PDFOperationError(tr("Il documento è cambiato. Ripeti l'operazione."))
        if not isinstance(strokes, list) or not isinstance(deleted, list):
            raise PDFOperationError('Invalid ink batch')
        if strokes or deleted:
            self._commit(lambda writer, reader: apply_ink(writer, reader, strokes, deleted))

    def rotate(self, page: int, degrees: int = 90) -> None:
        if degrees not in (90, 180, 270, -90, -180, -270):
            raise PDFOperationError(tr("Rotazione consentita: multipli di 90°."))

        def op(writer: PdfWriter, reader: PdfReader) -> None:
            self._valid_page(page, len(reader.pages))
            writer.append(reader)
            writer.pages[page].rotate(degrees)

        self._commit(op)

    def create_form_fields(self, proposals: list, revision: str | Path) -> None:
        from .form_design import create_fields
        if Path(revision) != self.path:
            raise PDFOperationError(tr("Il documento è cambiato. Ripeti la preparazione del modulo."))

        def op(writer, reader):
            writer.clone_document_from_reader(reader)
            writer.pdf_header = reader.pdf_header
            create_fields(writer, proposals)

        self._commit(op, copy_metadata=False)

    def form_design(self):
        from .form_edit import design_fields
        with self._reader() as reader:
            return design_fields(reader)

    def update_form_fields(self, proposals: list, deleted: list, revision: str | Path) -> None:
        from .form_edit import apply_design, design_fields, validate_design
        if Path(revision) != self.path:
            raise PDFOperationError(tr("Il documento è cambiato. Ripeti la preparazione del modulo."))
        with self._reader() as reader:
            proposals = validate_design(reader, proposals, deleted)
            if not deleted and proposals == design_fields(reader):
                return

        def op(writer, reader):
            writer.clone_document_from_reader(reader)
            writer.pdf_header = reader.pdf_header
            apply_design(writer, proposals, deleted)

        self._commit(op, copy_metadata=False)

    def delete(self, page: int) -> None:
        def op(writer: PdfWriter, reader: PdfReader) -> None:
            count = len(reader.pages)
            self._valid_page(page, count)
            if count == 1:
                raise PDFOperationError(tr("Non puoi eliminare l'ultima pagina."))
            for i in range(count):
                if i != page:
                    writer.add_page(reader.pages[i])

        self._commit(op)

    def move(self, page: int, offset: int) -> None:
        if offset not in (-1, 1):
            raise PDFOperationError(tr("Spostamento consentito: -1 o +1."))

        def op(writer: PdfWriter, reader: PdfReader) -> None:
            count = len(reader.pages)
            self._valid_page(page, count)
            target = page + offset
            self._valid_page(target, count)
            order = list(range(count))
            order[page], order[target] = order[target], order[page]
            for i in order:
                writer.add_page(reader.pages[i])

        self._commit(op)

    def insert_pdf(self, filename: str | Path, after_page: int) -> None:
        incoming = user_path(filename)
        if not incoming.is_file():
            raise PDFOperationError(tr("Il PDF da inserire non esiste."))

        def op(writer: PdfWriter, reader: PdfReader) -> None:
            self._valid_page(after_page, len(reader.pages))
            other = PdfReader(snapshot)
            try:
                if other.is_encrypted:
                    raise PDFOperationError(tr("Non è possibile inserire un PDF protetto."))
                if not other.pages:
                    raise PDFOperationError(tr("Il PDF da inserire è vuoto."))
                for index, original in enumerate(reader.pages):
                    writer.add_page(original)
                    if index == after_page:
                        for extra in other.pages:
                            writer.add_page(extra)
            finally:
                other.close()

        with PrivateDirectory(dir=self._temp.name) as work:
            snapshot = Path(work) / "input.pdf"
            copy_checked(incoming, snapshot)
            report = check_pdf(snapshot)
            self._commit(op, origins=(snapshot,))
        self.security_report = SecurityReport(tuple(sorted(set(self.security_report.findings) | set(report.findings))),
                                              self.security_report.memory_limited and report.memory_limited)

    def add_note(self, page: int, message: str, position=None) -> None:
        message = message.strip()
        if not message:
            raise PDFOperationError(tr("La nota non può essere vuota."))
        if len(message) > 32768 or not valid_xml_text(message):
            raise PDFOperationError(tr("Il testo della nota è troppo lungo o contiene caratteri non validi."))

        def op(writer: PdfWriter, reader: PdfReader) -> None:
            self._valid_page(page, len(reader.pages))
            writer.clone_document_from_reader(reader)
            target = writer.pages[page]
            width, height = displayed_size(target)
            x, y = position if position is not None else (max(0, width - 60), 36)
            if not all(math.isfinite(v) for v in (x, y)) or not (0 <= x <= width and 0 <= y <= height):
                raise PDFOperationError(tr("Scegli un punto all'interno della pagina."))
            x, y = min(x, max(0, width - 24)), min(y, max(0, height - 24))
            corners = [to_pdf(target, px, py) for px, py in ((x, y), (min(width, x + 24), min(height, y + 24)))]
            rect = (min(p[0] for p in corners), min(p[1] for p in corners),
                    max(p[0] for p in corners), max(p[1] for p in corners))
            note = Text(rect=rect, text=message, open=False, flags=4)
            note[NameObject("/Name")] = NameObject("/Comment")
            note[NameObject("/C")] = ArrayObject([FloatObject(v) for v in (1, .84, .2)])
            appearance = DecodedStreamObject()
            appearance.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                               NameObject("/BBox"): ArrayObject([NumberObject(v) for v in (0, 0, 24, 24)]),
                               NameObject("/Resources"): DictionaryObject()})
            appearance.set_data(b"q 1 .84 .2 rg .65 .44 .05 RG 1 w 5 5 m 2 5 1 8 1 12 c 1 18 5 21 12 21 c 19 21 23 18 23 12 c 23 7 19 5 12 5 c 6 1 l 7 5 l h B .8 .58 .12 RG 1 w 6 15 m 18 15 l S 6 11 m 15 11 l S Q")
            note[NameObject("/AP")] = DictionaryObject({NameObject("/N"): writer._add_object(appearance)})
            writer.add_annotation(page_number=page, annotation=note)

        self._commit(op)

    def notes(self):
        reader = PdfReader(self.path)
        if reader.is_encrypted:
            reader.decrypt(self._password or "")
        result = {}
        try:
            for number, page in enumerate(reader.pages):
                entries = []
                for index, reference in enumerate(page.get("/Annots", ArrayObject()).get_object()):
                    note = reference.get_object()
                    if note.get("/Subtype") != "/Text" or "/Rect" not in note:
                        continue
                    x0, y0, x1, y1 = map(float, note["/Rect"])
                    points = [from_pdf(page, x, y) for x, y in ((x0, y0), (x1, y1))]
                    entries.append({"index": index, "text": str(note.get("/Contents", "")),
                                    "rect": (min(p[0] for p in points), min(p[1] for p in points),
                                             max(p[0] for p in points), max(p[1] for p in points))})
                if entries:
                    result[number] = entries
            return result
        finally:
            reader.close()

    def update_note(self, page: int, index: int, message: str | None):
        if message is not None and (not message.strip() or len(message) > 32768 or not valid_xml_text(message)):
            raise PDFOperationError(tr("Il testo della nota non è valido."))
        def op(writer, reader):
            self._valid_page(page, len(reader.pages))
            writer.clone_document_from_reader(reader)
            annotations = writer.pages[page].get("/Annots", ArrayObject()).get_object()
            if not 0 <= index < len(annotations) or annotations[index].get_object().get("/Subtype") != "/Text":
                raise PDFOperationError(tr("La nota non è più disponibile."))
            note = annotations[index].get_object()
            if message is None:
                popup = note.get("/Popup")
                annotations[:] = [ref for i, ref in enumerate(annotations) if i != index and ref != popup]
            else:
                note[NameObject("/Contents")] = TextStringObject(message.strip())
        self._commit(op)

    def add_text_markup(self, page: int, rectangles: list[tuple[float, float, float, float]], kind: str = "highlight") -> None:
        """Annotate bounds in displayed page points, with origin at top-left."""
        if kind not in ("highlight", "underline") or not rectangles:
            raise PDFOperationError(tr("Seleziona del testo da evidenziare o sottolineare."))

        def op(writer: PdfWriter, reader: PdfReader) -> None:
            self._valid_page(page, len(reader.pages))
            writer.append(reader)
            target = writer.pages[page]
            box = target.cropbox
            x0, y0, x1, y1 = map(float, box)
            rotation = target.rotation % 360
            unit = float(target.get("/UserUnit", 1))

            def pdf_point(x, y):
                x, y = x / unit, y / unit
                if rotation == 90:
                    return x0 + y, y0 + x
                if rotation == 180:
                    return x1 - x, y0 + y
                if rotation == 270:
                    return x1 - y, y1 - x
                return x0 + x, y1 - y

            quads = []
            points = []
            for left, top, right, bottom in rectangles:
                if right <= left or bottom <= top:
                    raise PDFOperationError(tr("La selezione di testo non è valida."))
                for x, y in ((left, top), (right, top), (left, bottom), (right, bottom)):
                    px, py = pdf_point(x, y)
                    quads.extend((FloatObject(px), FloatObject(py)))
                    points.append((px, py))
            rect = (min(p[0] for p in points), min(p[1] for p in points),
                    max(p[0] for p in points), max(p[1] for p in points))
            annotation = Highlight(rect=rect, quad_points=ArrayObject(quads),
                                   highlight_color="FFD24A" if kind == "highlight" else "2563EB", printing=True)
            if kind == "underline":
                annotation[NameObject("/Subtype")] = NameObject("/Underline")
            writer.add_annotation(page_number=page, annotation=annotation)

        self._commit(op)

    def extract(self, pages: list[int], destination: str | Path) -> Path:
        if any(same_file(destination, path) for path in [*self._revisions, self.source, self.output_path]):
            raise PDFOperationError(tr("Non puoi estrarre sopra un PDF aperto nella cronologia. Scegli un altro nome."))
        reader = self._reader()
        try:
            if not pages:
                raise PDFOperationError(tr("Indica almeno una pagina da estrarre."))
            if len(set(pages)) != len(pages):
                raise PDFOperationError(tr("L'intervallo contiene pagine duplicate."))
            writer = PdfWriter()
            for page in pages:
                self._valid_page(page, len(reader.pages))
            # Append also rebuilds AcroForm fields for the selected widgets.
            # add_page alone leaves form controls without a document field tree.
            writer.append(reader, pages=pages, import_outline=False)
            self._copy_metadata(writer, reader)
            return self._atomic_write(writer, Path(destination))
        finally:
            reader.close()

    def paragraph_at(self, page, position):
        from .paragraph_edit import paragraph_at
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti sono in sola lettura."))
        with self._reader() as reader:
            self._valid_page(page, len(reader.pages))
        return paragraph_at(self.path, page, position)

    def paragraph_boxes(self, page):
        from .paragraph_edit import paragraph_boxes
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti sono in sola lettura."))
        with self._reader() as reader:
            self._valid_page(page, len(reader.pages))
        return paragraph_boxes(self.path, page)

    def paragraph_info(self, target, text, size, alignment):
        from .paragraph_edit import paragraph_info
        if target.revision != str(self.path):
            raise PDFOperationError(tr("Il documento è cambiato. Seleziona nuovamente il testo."))
        return paragraph_info(self.path, target, text, size, alignment)

    def replace_paragraph(self, target, text, size=None, alignment=None):
        from .paragraph_edit import replace_paragraph
        if target.revision != str(self.path):
            raise PDFOperationError(tr("Il documento è cambiato. Seleziona nuovamente il testo."))
        size = target.size if size is None else size
        alignment = target.alignment if alignment is None else alignment
        if text == target.text and size == target.size and alignment == target.alignment:
            return
        def op(writer, reader):
            self._valid_page(target.page, len(reader.pages))
            replace_paragraph(writer, reader, self.path, target, text, size, alignment)
        self._commit(op)

    def text_boxes(self, page):
        from .text_edit import editable_boxes
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti sono in sola lettura."))
        with self._reader() as reader:
            self._valid_page(page, len(reader.pages))
        return editable_boxes(self.path, page)

    def text_at(self, page, position):
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti sono in sola lettura."))
        from .text_edit import find_target
        reader = self._reader()
        try:
            self._valid_page(page, len(reader.pages))
        finally:
            reader.close()
        try:
            return find_target(self.path, page, position)
        except PDFOperationError:
            raise
        except Exception as exc:
            raise PDFOperationError(tr("Impossibile leggere il testo per la modifica.")) from exc

    def replace_text(self, target, text, size=None):
        from .text_edit import replace_page
        if target.revision != str(self.path):
            raise PDFOperationError(tr("Il documento è cambiato. Seleziona nuovamente il testo."))
        size = target.size if size is None else size
        if text == target.text and size == target.size:
            return
        def op(writer, reader):
            self._valid_page(target.page, len(reader.pages))
            replace_page(writer, reader, self.path, target, text, size)
        self._commit(op)

    def apply_ocr(self, result: str | Path, revision: str | Path) -> None:
        """Import a completed OCR job as one undoable edit, rejecting stale results."""
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti sono in sola lettura."))
        if Path(revision) != self.path:
            raise PDFOperationError(tr("Il documento è cambiato durante l'OCR. Ripeti il riconoscimento."))
        check_pdf(Path(result))

        def op(writer, reader):
            with PdfReader(result) as recognized:
                if len(recognized.pages) != len(reader.pages) or any(
                        tuple(a.mediabox) != tuple(b.mediabox) or tuple(a.cropbox) != tuple(b.cropbox)
                        or a.rotation != b.rotation or a.get("/UserUnit", 1) != b.get("/UserUnit", 1)
                        for a, b in zip(recognized.pages, reader.pages)):
                    raise PDFOperationError(tr("Il risultato OCR non corrisponde alle pagine del documento."))
                writer.clone_document_from_reader(recognized)
                writer.pdf_header = recognized.pdf_header

        self._commit(op, copy_metadata=False)

    def _atomic_write(self, writer: PdfWriter, destination: Path) -> Path:
        destination = user_path(destination)
        if destination.suffix.lower() != ".pdf":
            raise PDFOperationError(tr("Il file di destinazione deve terminare con .pdf."))
        if not destination.parent.is_dir():
            raise PDFOperationError(tr("La cartella di destinazione non esiste."))
        fd, tmpname = tempfile.mkstemp(prefix=".apex_", suffix=".pdf", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                writer.write(output)
                output.flush()
                os.fsync(output.fileno())
            propagate_zone(tmpname, self.path, destination if destination.exists() else None)
            os.replace(tmpname, destination)
        except Exception as exc:
            Path(tmpname).unlink(missing_ok=True)
            raise PDFOperationError(tr('Salvataggio non riuscito: {0}', exc)) from exc
        return destination

    def save_as(self, destination: str | Path) -> Path:
        if self.encrypted:
            raise PDFOperationError(tr("I PDF protetti non possono essere risalvati con questa versione."))
        destination = user_path(destination)
        if same_file(destination, self.path):
            self.output_path = destination
            self._saved_index = self._index
            return destination
        if destination.suffix.lower() != ".pdf":
            raise PDFOperationError(tr("Il file di destinazione deve terminare con .pdf."))
        if not destination.parent.is_dir():
            raise PDFOperationError(tr("La cartella di destinazione non esiste."))
        fd, tmpname = tempfile.mkstemp(prefix=".apex_", suffix=".pdf", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output, self.path.open("rb") as source:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            propagate_zone(tmpname, self.path, destination if destination.exists() else None)
            os.replace(tmpname, destination)
        except Exception as exc:
            Path(tmpname).unlink(missing_ok=True)
            raise PDFOperationError(tr('Salvataggio non riuscito: {0}', exc)) from exc
        self.output_path = destination
        self._saved_index = self._index
        return destination

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        self._index -= 1
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        self._index += 1
        return True

    def close(self) -> None:
        self._password = None
        self._temp.cleanup()


def parse_page_range(spec: str, page_count: int) -> list[int]:
    """Parse human 1-indexed expressions like '1,3-5,9'."""
    result: list[int] = []
    seen: set[int] = set()
    if not spec.strip():
        raise PDFOperationError(tr("Indica le pagine da estrarre."))
    for group in spec.split(","):
        part = group.strip()
        try:
            if "-" in part:
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start > end:
                    raise ValueError(tr("intervallo invertito"))
                members = range(start, end + 1)
            else:
                members = [int(part)]
            for item in members:
                if not 1 <= item <= page_count or item in seen:
                    raise ValueError(tr("duplicato o fuori intervallo"))
                result.append(item - 1)
                seen.add(item)
        except ValueError as exc:
            raise PDFOperationError(tr("Intervallo di pagine non valido: '{0}'.", part)) from exc
    return result
