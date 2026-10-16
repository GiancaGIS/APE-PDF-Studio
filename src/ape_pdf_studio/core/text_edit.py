"""Replace an isolated horizontal text span, preserving the surrounding page."""
from .localization import tr
from dataclasses import dataclass
import io
import math
import unicodedata

from .errors import PDFOperationError


@dataclass(frozen=True)
class TextTarget:
    revision: str
    page: int
    address: tuple
    text: str
    bbox: tuple
    display_bbox: tuple
    origin: tuple
    size: float
    color: int
    font: str
    flags: int


def text_spans(page):
    import pymupdf
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    result = []
    for bi, block in enumerate(data["blocks"]):
        for li, line in enumerate(block.get("lines", [])):
            for si, span in enumerate(line["spans"]):
                result.append(((bi, li, si), line, span))
                if len(result) > 20_000:
                    raise PDFOperationError(tr("Questa pagina contiene troppo testo per la modifica diretta."))
    return result


def supported_paint(span):
    import pymupdf
    # Use the native enum: recent MuPDF versions added a BOLD bit before FILLED.
    # Hardcoded old masks misclassify ordinary bold glyphs as stroked text.
    filled = pymupdf.mupdf.FZ_STEXT_FILLED
    stroked = pymupdf.mupdf.FZ_STEXT_STROKED
    clipped = pymupdf.mupdf.FZ_STEXT_CLIPPED
    flags = span.get('char_flags', filled)
    return span.get('alpha', 255) == 255 and flags & filled and not flags & (stroked | clipped)


def find_target(source, number, position):
    import pymupdf
    with pymupdf.open(stream=source.read_bytes(), filetype="pdf") as document:
        page = document[number]
        point = pymupdf.Point(*position) * page.derotation_matrix
        hits = [(address, line, span) for address, line, span in text_spans(page)
                if pymupdf.Rect(span["bbox"]).contains(point) and span["text"].strip()]
        if not hits:
            raise PDFOperationError(tr("Nessun testo modificabile in questo punto. Le scansioni e i tracciati richiedono OCR o ricostruzione."))
        address, line, span = min(hits, key=lambda item: pymupdf.Rect(item[2]["bbox"]).get_area())
        if line["wmode"] != 0 or abs(line["dir"][0] - 1) > .001 or abs(line["dir"][1]) > .001:
            raise PDFOperationError(tr("La modifica di testo inclinato o verticale non è ancora supportata."))
        if not supported_paint(span):
            raise PDFOperationError(tr("La modifica di testo trasparente o con contorni speciali non è ancora supportata."))
        if not .5 <= span["size"] <= 300:
            raise PDFOperationError(tr("La dimensione del testo è fuori dall'intervallo modificabile (0,5–300 pt)."))
        if len(span["text"]) > 4000:
            raise PDFOperationError(tr("Scegli una porzione di testo più breve."))
        return TextTarget(str(source), number, address, span["text"], tuple(span["bbox"]),
                          tuple(pymupdf.Rect(span["bbox"]) * page.rotation_matrix), tuple(span["origin"]),
                          span["size"], span["color"], span["font"], span["flags"])


def editable_boxes(source, number):
    """Return bounded geometry only; the UI never parses PDF text objects."""
    import pymupdf
    with pymupdf.open(stream=source.read_bytes(), filetype="pdf") as document:
        page = document[number]
        boxes = []
        for _, line, span in text_spans(page):
            if (not span["text"].strip() or len(span["text"]) > 4000
                    or line["wmode"] != 0 or abs(line["dir"][0] - 1) > .001
                    or abs(line["dir"][1]) > .001 or not supported_paint(span)
                    or not .5 <= span["size"] <= 300):
                continue
            boxes.append(tuple(pymupdf.Rect(span["bbox"]) * page.rotation_matrix))
        return boxes


def replacement_font(document, page, target, text):
    import pymupdf
    # Reuse a full embedded font when it has every required glyph. A subset
    # cannot be assumed to contain new characters typed by the user.
    for entry in page.get_fonts(full=True):
        if entry[3].split("+")[-1] != target.font.split("+")[-1]:
            continue
        try:
            buffer = document.extract_font(entry[0])[3]
            if buffer:
                font = pymupdf.Font(fontbuffer=buffer)
                if font.is_writable and all(font.has_glyph(ord(c)) for c in text):
                    return font, font.name, False
        except (ValueError, RuntimeError):
            pass
    name = target.font.lower()
    family = "cour" if any(s in name for s in ("courier", "consolas")) else "tiro" if "times" in name else "helv"
    bold, italic = bool(target.flags & 16), bool(target.flags & 2)
    aliases = {"helv": ("helv", "heit", "hebo", "hebi"),
               "cour": ("cour", "coit", "cobo", "cobi"),
               "tiro": ("tiro", "tiit", "tibo", "tibi")}
    font = pymupdf.Font(aliases[family][2 * bold + italic])
    if not all(font.has_glyph(ord(c)) for c in text):
        font = pymupdf.Font("cjk")
    if not all(font.has_glyph(ord(c)) for c in text):
        raise PDFOperationError(tr("Il font disponibile non contiene tutti i caratteri richiesti."))
    return font, font.name, target.font != font.name


def font_details(source, target, text=None):
    import pymupdf
    with pymupdf.open(stream=source.read_bytes(), filetype="pdf") as document:
        _, name, fallback = replacement_font(document, document[target.page], target, text or target.text)
    return name, fallback


def font_description(source, target, text=None):
    name, fallback = font_details(source, target, text)
    return name + (tr(" · font compatibile") if fallback else tr(" · font originale"))


def replace_page(writer, reader, source, target, text, size):
    import pymupdf
    from pypdf import PdfReader
    from pypdf.generic import NameObject
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or any(
            unicodedata.category(c).startswith("C") or unicodedata.bidirectional(c) in ("R", "AL", "AN")
            or unicodedata.combining(c) for c in text):
        raise PDFOperationError(tr("Inserisci una riga non vuota. Testo multilinea, scritture complesse e caratteri di controllo non sono ancora supportati."))
    if not math.isfinite(size) or not .5 <= size <= 300:
        raise PDFOperationError(tr("Dimensione del testo non valida."))
    with pymupdf.open(stream=source.read_bytes(), filetype="pdf") as document:
        page = document[target.page]
        spans = text_spans(page)
        matching = [span for address, _, span in spans if address == target.address]
        if not matching or matching[0]["text"] != target.text or tuple(matching[0]["bbox"]) != target.bbox:
            raise PDFOperationError(tr("Il testo è cambiato. Selezionalo nuovamente."))
        box = pymupdf.Rect(target.bbox)
        for address, _, span in spans:
            if address != target.address and (pymupdf.Rect(span["bbox"]) & box).get_area() > .1:
                raise PDFOperationError(tr("Il testo si sovrappone ad altri contenuti testuali: modifica non applicata."))
        font, _, _ = replacement_font(document, page, target, text)
        if font.text_length(text, fontsize=size) > box.x1 - target.origin[0] + .1:
            raise PDFOperationError(tr("Il testo supera lo spazio originale. Accorcialo oppure riduci la dimensione."))
        height = size * (font.ascender - font.descender)
        if height > box.height + .5:
            raise PDFOperationError(tr("Il testo supera l'altezza originale. Riduci la dimensione."))
        # Existing pending redactions must not be applied as a side effect.
        for annotation in list(page.annots() or []):
            if annotation.type[0] == pymupdf.PDF_ANNOT_REDACT:
                page.delete_annot(annotation)
        page.add_redact_annot(box, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        rotation = page.rotation
        page.set_rotation(0)
        unrotated = page.rect
        # TextWriter's direct CropBox compensation differs from PDF page space.
        # Write on a normalized transparent page, then place its vector content
        # through MuPDF's page transformation (including crop and UserUnit).
        with pymupdf.open() as overlay:
            overlay_page = overlay.new_page(width=unrotated.width, height=unrotated.height)
            output = pymupdf.TextWriter(overlay_page.rect)
            output.append(target.origin, text, font=font, fontsize=size)
            output.write_text(overlay_page, color=tuple(((target.color >> shift) & 255) / 255 for shift in (16, 8, 0)))
            page.show_pdf_page(unrotated, overlay, 0)
        page.set_rotation(rotation)
        modified = document.tobytes(deflate=True)
    # Transplant content/resources only. Preserve the original catalog, metadata,
    # annotations, forms, attachments, links and every other page through pypdf.
    writer.clone_document_from_reader(reader)
    with PdfReader(io.BytesIO(modified)) as changed:
        for key in ("/Contents", "/Resources"):
            writer.pages[target.page][NameObject(key)] = changed.pages[target.page][key].clone(writer)
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
