"""Local PDF-to-Word conversion, also runnable in an isolated GUI subprocess.

Text pages use pdf2docx's layout analysis. Pages without text are included as
images, retaining scans and blank pages without pretending to perform OCR.
"""
from __future__ import annotations
from .localization import tr

import io
import json
import os
from pathlib import Path
from .user_paths import user_path, same_file
import sys
import tempfile
from .private_work import PrivateDirectory
from typing import Callable

from .editor import PDFOperationError
from .errors import PDFPasswordRequired
from .security import check_pdf, copy_checked
from .provenance import propagate_zone


def atomic_copy(source: Path, destination: Path, *origins, artifact_root=None, zone=None) -> None:
    """Commit a completed export without exposing a partial destination."""
    import shutil

    from .worker_protocol import MAX_RESULT_BYTES
    if not source.is_file() or source.is_symlink() or getattr(source.lstat(),'st_file_attributes',0)&0x400 or not 0<source.stat().st_size<=MAX_RESULT_BYTES:
        raise PDFOperationError('Output non valido o superiore al limite di sicurezza.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".apex_export_", suffix=destination.suffix,
                                         dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            from .artifacts import open_artifact
            context=open_artifact(source,artifact_root,MAX_RESULT_BYTES) if artifact_root else source.open('rb')
            with context as reader:
                copied=0
                while chunk:=reader.read(65536):
                    copied+=len(chunk)
                    if copied>MAX_RESULT_BYTES:raise PDFOperationError('Output superiore al limite di sicurezza.')
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        propagate_zone(temporary, *(origins if artifact_root else (source,*origins)), destination if destination.exists() else None, zone=zone)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _image_page(document, page) -> None:
    from docx.enum.section import WD_SECTION_START
    from docx.shared import Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    section = document.add_section(WD_SECTION_START.NEW_PAGE) if document.paragraphs else document.sections[0]
    width, height = page.rect.width, page.rect.height
    # Word stores page dimensions in a signed 16-bit twip value.
    if max(width, height) > 1584:
        raise PDFOperationError(tr("Una pagina supera le dimensioni supportate da Word (55,8 cm)."))
    section.page_width, section.page_height = Pt(width), Pt(height)
    section.top_margin = section.bottom_margin = Pt(0)
    section.left_margin = section.right_margin = Pt(0)
    columns = section._sectPr.find(qn("w:cols"))
    if columns is None:
        columns = OxmlElement("w:cols")
        section._sectPr.append(columns)
    columns.set(qn("w:num"), "1")
    for child in list(columns):
        columns.remove(child)
    # Cap raster memory for unusually large sheets.
    scale = min(150 / 72, (24_000_000 / (width * height)) ** 0.5)
    import pymupdf
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run()
    run.font.size = Pt(1)
    run.add_picture(io.BytesIO(pixels.tobytes("png")), width=Pt(width), height=Pt(max(1, height - 2)))


def convert_pdf_to_docx(source: str | Path, destination: str | Path,
                        progress: Callable[[int, int, str], None] | None = None) -> dict:
    source, destination = user_path(source), user_path(destination)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise PDFOperationError(tr("Seleziona un PDF esistente da convertire."))
    if destination.suffix.lower() != ".docx" or same_file(destination, source):
        raise PDFOperationError(tr("La destinazione deve essere un nuovo file .docx."))
    try:
        from pdf2docx import Converter
        from docx import Document
    except ImportError as exc:
        raise PDFOperationError(tr("Conversione Word non disponibile. Aggiorna le dipendenze con lo script di avvio.")) from exc

    converter = None
    with PrivateDirectory(prefix="apex_docx_") as work:
        try:
            snapshot = Path(work) / "input.pdf"
            copy_checked(source, snapshot)
            try:
                check_pdf(snapshot)
            except PDFPasswordRequired as exc:
                raise PDFOperationError(tr("I PDF protetti sono in sola lettura: esportazione disabilitata.")) from exc
            converter = Converter(str(snapshot))
            from .pdf_runtime import disable_javascript
            disable_javascript(converter.fitz_doc)
            pdf = converter.fitz_doc
            if pdf.is_encrypted:
                raise PDFOperationError(tr("I PDF protetti sono in sola lettura: esportazione disabilitata."))
            total = len(pdf)
            if not total:
                raise PDFOperationError(tr("Il PDF non contiene pagine."))
            notify = progress or (lambda *_: None)
            notify(0, total, tr("Analisi dell'impaginazione…"))
            text_pages = {i for i in range(total) if pdf[i].get_text().strip()}
            if any(max(pdf[i].rect.width, pdf[i].rect.height) > 1584 for i in range(total)):
                raise PDFOperationError(tr("Una pagina supera le dimensioni supportate da Word (55,8 cm)."))
            settings = converter.default_settings
            settings.update(ignore_page_error=False, raw_exceptions=True, multi_processing=False)
            if text_pages:
                converter.load_pages(pages=sorted(text_pages)).parse_document(**settings)
            document = Document()
            image_pages = []
            for index in range(total):
                notify(index, total, tr('Conversione pagina {0} di {1}…', index + 1, total))
                if index in text_pages:
                    page = converter.pages[index]
                    page.parse(**settings)
                    page.make_docx(document)
                    # An empty converted page must still occupy its own page.
                    if not document.paragraphs:
                        document.add_paragraph()
                else:
                    _image_page(document, pdf[index])
                    image_pages.append(index + 1)
            notify(total, total, tr("Salvataggio del documento Word…"))
            completed = Path(work) / "converted.docx"
            document.save(completed)
            atomic_copy(completed, destination, source)
            return {"pages": total, "image_pages": image_pages}
        except PDFOperationError:
            raise
        except Exception as exc:
            raise PDFOperationError(tr('Impossibile convertire il PDF in Word: {0}', exc)) from exc
        finally:
            if converter is not None:
                converter.close()


def main() -> int:
    from .security_worker import require_memory_limit
    require_memory_limit()
    def send(payload):
        print(json.dumps(payload, ensure_ascii=True), flush=True)

    try:
        if len(sys.argv) != 3:
            raise PDFOperationError(tr("Indica PDF sorgente e DOCX destinazione."))
        result = convert_pdf_to_docx(sys.argv[1], sys.argv[2],
                                    lambda done, total, label: send({"progress": done, "total": total, "label": label}))
        send({"result": result})
        return 0
    except Exception as exc:
        send({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
