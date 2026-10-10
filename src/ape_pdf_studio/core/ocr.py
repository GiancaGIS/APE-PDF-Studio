"""Local OCR: preserve the original PDF and merge only invisible text layers."""
from __future__ import annotations
from .localization import tr

import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

from pypdf import PdfReader, PdfWriter

from .docx_export import atomic_copy
from .errors import PDFOperationError, PDFPasswordRequired
from .page_coordinates import displayed_size, to_pdf
from .security import check_pdf, configure_parser, copy_checked

LANGUAGES = {"ita": "Italiano", "eng": "Inglese", "ita+eng": "Italiano e inglese"}
MODEL_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/"
MODEL_HASHES = {
    "ita": "b8f89e1e785118dac4d51ae042c029a64edb5c3ee42ef73027a6d412748d8827",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
MAX_PIXELS = 16_000_000


def prepare_tessdata(language, cache_dir, *, download=False, progress=None):
    """Use installed language data, or fetch fixed, hash-checked official models."""
    if language not in LANGUAGES:
        raise PDFOperationError(tr("Scegli italiano, inglese oppure entrambe le lingue."))
    languages = language.split("+")
    cache = Path(cache_dir)
    candidates = [cache]
    if os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(os.environ["TESSDATA_PREFIX"]))
    candidates.extend([
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tessdata",
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/opt/homebrew/share/tessdata"), Path("/usr/local/share/tessdata"),
    ])
    for folder in candidates:
        if all((folder / f"{code}.traineddata").is_file() for code in languages):
            if folder != cache or all(hashlib.sha256((folder / f"{code}.traineddata").read_bytes()).hexdigest()
                                      == MODEL_HASHES[code] for code in languages):
                return folder
    if not download:
        raise PDFOperationError(tr("Dati OCR mancanti. Abilita il download delle lingue oppure imposta TESSDATA_PREFIX."))
    cache.mkdir(parents=True, exist_ok=True)
    for code in languages:
        destination = cache / f"{code}.traineddata"
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == MODEL_HASHES[code]:
            continue
        if progress:
            progress(0, 1, tr('Download dati OCR: {0}…', tr(LANGUAGES[code])))
        temporary = None
        try:
            # Models only: no PDF content is ever sent over the network.
            with urlopen(MODEL_URL + f"{code}.traineddata", timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if hashlib.sha256(data).hexdigest() != MODEL_HASHES[code]:
                raise PDFOperationError(tr("Il download dei dati OCR non supera la verifica di integrità."))
            with tempfile.NamedTemporaryFile(prefix=".ocr_", dir=cache, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            os.replace(temporary, destination)
        except PDFOperationError:
            raise
        except OSError as exc:
            raise PDFOperationError(tr("Impossibile scaricare i dati OCR. Controlla la connessione oppure imposta "
                                    "TESSDATA_PREFIX su una cartella con ita.traineddata e eng.traineddata.")) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return cache


def _merge_text_layer(target, pixels, language, tessdata):
    """Map the OCR's displayed coordinates back to crop/rotation/UserUnit space."""
    data = pixels.pdfocr_tobytes(language=language, tessdata=str(tessdata))
    with PdfReader(io.BytesIO(data)) as recognized:
        if not recognized.pages[0].extract_text().strip():
            return False
        overlay = PdfWriter()
        overlay.add_page(recognized.pages[0])
        # The original scan stays in place; the OCR raster is never imported.
        overlay.remove_images()
        layer = overlay.pages[0]
        width, height = displayed_size(target)
        origin = to_pdf(target, 0, height)
        x_axis = to_pdf(target, width, height)
        y_axis = to_pdf(target, 0, 0)
        ow, oh = float(layer.mediabox.width), float(layer.mediabox.height)
        matrix = ((x_axis[0] - origin[0]) / ow, (x_axis[1] - origin[1]) / ow,
                  (y_axis[0] - origin[0]) / oh, (y_axis[1] - origin[1]) / oh,
                  origin[0], origin[1])
        target.merge_transformed_page(layer, matrix, over=True, expand=False)
        return True


def recognize_pdf(source, destination, *, language="ita+eng", pages=None, dpi=300,
                  cache_dir, download=False, progress=None):
    """Return recognized/skipped/empty page numbers; commit the complete PDF atomically."""
    import pymupdf

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or destination.suffix.lower() != ".pdf":
        raise PDFOperationError(tr("La destinazione OCR deve essere un altro file .pdf."))
    if language not in LANGUAGES or dpi not in (150, 300):
        raise PDFOperationError(tr("Lingua o risoluzione OCR non valida."))
    notify = progress or (lambda *_: None)
    try:
        with tempfile.TemporaryDirectory(prefix="apex_ocr_") as work:
            snapshot = Path(work) / "input.pdf"
            copy_checked(source, snapshot)
            try:
                check_pdf(snapshot)
            except PDFPasswordRequired as exc:
                raise PDFOperationError(tr("I PDF protetti sono in sola lettura: OCR disabilitato.")) from exc
            configure_parser()
            with PdfReader(snapshot) as reader, pymupdf.open(snapshot) as pdf:
                if reader.is_encrypted:
                    raise PDFOperationError(tr("I PDF protetti sono in sola lettura: OCR disabilitato."))
                chosen = list(range(len(pdf))) if pages is None else list(pages)
                if not chosen or len(set(chosen)) != len(chosen) or any(
                        type(page) is not int or not 0 <= page < len(pdf) for page in chosen):
                    raise PDFOperationError(tr("Selezione pagine OCR non valida."))
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                writer.pdf_header = reader.pdf_header
                result = {"recognized": [], "skipped": [], "empty": [], "reduced_dpi": []}
                tessdata = None
                for done, number in enumerate(chosen):
                    notify(done, len(chosen), tr('OCR pagina {0}…', number + 1))
                    page = pdf[number]
                    if page.get_text().strip():
                        result["skipped"].append(number + 1)
                        continue
                    if tessdata is None:
                        tessdata = prepare_tessdata(language, cache_dir, download=download,
                                                    progress=lambda _d, _t, label: notify(done, len(chosen), label))
                    width, height = page.rect.width, page.rect.height
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise PDFOperationError(tr('Dimensioni non valide per la pagina {0}.', number + 1))
                    scale = min(dpi / 72, ((MAX_PIXELS - 20_000) / (width * height)) ** .5)
                    if scale < dpi / 72:
                        result["reduced_dpi"].append(number + 1)
                    # Avoid recognizing annotation text and burning it into scans.
                    pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB,
                                             alpha=False, annots=False)
                    if pixels.width * pixels.height > MAX_PIXELS:
                        raise PDFOperationError(tr("La pagina supera il limite di memoria OCR."))
                    pixels.set_dpi(max(1, round(scale * 72)), max(1, round(scale * 72)))
                    changed = _merge_text_layer(writer.pages[number], pixels, language, tessdata)
                    result["recognized" if changed else "empty"].append(number + 1)
                    del pixels
                notify(len(chosen), len(chosen), tr("Verifica del PDF riconosciuto…"))
                completed = Path(work) / "completed.pdf"
                writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
                writer.write(completed)
            check_pdf(completed)
            atomic_copy(completed, destination)
            return result
    except PDFOperationError:
        raise
    except Exception as exc:
        raise PDFOperationError(tr('Riconoscimento OCR non riuscito: {0}', exc)) from exc


def main():
    from .security_worker import limit_memory
    limit_memory()

    def send(payload):
        print(json.dumps(payload, ensure_ascii=True), flush=True)

    try:
        request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        result = recognize_pdf(**request, progress=lambda done, total, label:
                               send({"progress": done, "total": total, "label": label}))
        send({"result": result})
        return 0
    except Exception as exc:
        send({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
