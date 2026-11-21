"""Replace an isolated horizontal text span, preserving the surrounding page."""
from collections import OrderedDict
from .localization import tr
from dataclasses import dataclass, replace
from hashlib import sha256
import io
import math
import unicodedata

from .errors import PDFOperationError
from .pdf_runtime import open_static_pdf
from .text_rewrite import TextLayout, TextRun, display_rotation, render_preview, rewrite_page, transplant_page


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
    identity: str


@dataclass(frozen=True)
class TextFormat:
    family: str | None = None
    color: int | None = None
    bold: bool | None = None
    italic: bool | None = None
    width: float | None = None
    selection: tuple | None = None

    def apply(self, target):
        flags = target.flags
        for mask, value in ((16, self.bold), (2, self.italic)):
            if value is not None:
                flags = (flags | mask) if value else (flags & ~mask)
        return replace(target, color=target.color if self.color is None else self.color, flags=flags)


def text_format(value):
    if value is None:
        return TextFormat()
    if not isinstance(value, tuple) or len(value) not in (5, 6):
        raise PDFOperationError(tr("Formato del testo non valido."))
    family, color, bold, italic, width = value[:5]
    selection = value[5] if len(value) == 6 else None
    if (family not in (None, 'helv', 'tiro', 'cour') or
            color is not None and (type(color) is not int or not 0 <= color <= 0xffffff) or
            any(flag is not None and type(flag) is not bool for flag in (bold, italic)) or
            width is not None and (type(width) not in (int, float) or not math.isfinite(width) or not 1 <= width <= 14400)):
        raise PDFOperationError(tr("Formato del testo non valido."))
    if selection is not None and (not isinstance(selection, tuple) or len(selection) != 2
            or any(type(i) is not int for i in selection) or not 0 <= selection[0] < selection[1] <= 16000):
        raise PDFOperationError(tr("Selezione di testo non valida."))
    return TextFormat(family, color, bold, italic, width, selection)


class PageText:
    """Revision-local cache of bounded page analysis and reusable font programs."""
    def __init__(self, page):
        self.page = page
        self.spans = text_spans(page)
        self.groups = None
        self.artwork = None
        self.fonts = OrderedDict()
        self.layout = None


class TextCache:
    def __init__(self):
        self.revision = None
        self.document = None
        self.pages = OrderedDict()

    def invalidate(self):
        self.pages.clear()
        if self.document is not None:
            self.document.close()
        self.document = self.revision = None

    def page(self, source, number):
        if self.revision != source:
            self.invalidate()
            self.document = open_static_pdf(source)
            self.revision = source
        if type(number) is not int or not 0 <= number < self.document.page_count:
            raise PDFOperationError(tr("Pagina non valida."))
        if number not in self.pages:
            if len(self.pages) >= 3:
                self.pages.popitem(last=False)
            self.pages[number] = PageText(self.document[number])
        self.pages.move_to_end(number)
        return self.pages[number]


def text_spans(page):
    import pymupdf
    data = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    result = []
    glyphs = 0
    for bi, block in enumerate(data["blocks"]):
        for li, line in enumerate(block.get("lines", [])):
            for si, span in enumerate(line["spans"]):
                glyphs += len(span['chars'])
                span["text"] = "".join(char["c"] for char in span["chars"])
                result.append(((bi, li, si), line, span))
                if len(result) > 20_000 or glyphs > 100_000:
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


def make_target(source, page, address, line, span):
    import pymupdf
    identity = sha256(repr((address, line['dir'], line['wmode'], span)).encode('utf-8')).hexdigest()
    return TextTarget(str(source), page.number, address, span["text"], tuple(span["bbox"]),
                      tuple(pymupdf.Rect(span["bbox"]) * display_rotation(page)), tuple(span["origin"]),
                      span["size"], span["color"], span["font"], span["flags"], identity)


def find_target(source, number, position, cache=None):
    import pymupdf
    owned = cache is None
    cache = cache or TextCache()
    try:
        index = cache.page(source, number)
        page = index.page
        point = pymupdf.Point(*position) * ~display_rotation(page)
        hits = [(address, line, span) for address, line, span in index.spans
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
        return make_target(source, page, address, line, span)
    finally:
        if owned:
            cache.invalidate()


def editable_boxes(source, number, cache=None):
    """Return bounded geometry only; the UI never parses PDF text objects."""
    import pymupdf
    owned = cache is None
    cache = cache or TextCache()
    try:
        index = cache.page(source, number)
        page = index.page
        boxes = []
        for _, line, span in index.spans:
            if (not span["text"].strip() or len(span["text"]) > 4000
                    or line["wmode"] != 0 or abs(line["dir"][0] - 1) > .001
                    or abs(line["dir"][1]) > .001 or not supported_paint(span)
                    or not .5 <= span["size"] <= 300):
                continue
            boxes.append(tuple(pymupdf.Rect(span["bbox"]) * display_rotation(page)))
        return boxes
    finally:
        if owned:
            cache.invalidate()


def _embed_allowed(buffer):
    # Only accept an embedded SFNT with a known OS/2 embedding policy. Other
    # formats and unresolved licensing fall back to our distributed fonts.
    from fontTools.ttLib import TTFont, TTLibError
    try:
        with TTFont(io.BytesIO(buffer), lazy=True) as font:
            if 'OS/2' not in font:
                return False
            permissions = font['OS/2'].fsType
            return not permissions & (0x0002 | 0x0100 | 0x0200) and (
                not permissions & 0x0004 or bool(permissions & 0x0008))
    except (TTLibError, KeyError, ValueError):
        return False


def replacement_font(document, page, target, text, cache=None, family=None, *, reuse_embedded=True):
    import pymupdf
    # Reuse a full embedded font when it has every required glyph. A subset
    # cannot be assumed to contain new characters typed by the user.
    if family is None and reuse_embedded:
        entries = {entry[0] for entry in page.get_fonts(full=True)
                   if entry[3].split("+")[-1].replace(" ", "") == target.font.split("+")[-1].replace(" ", "")}
        # Equal font names are insufficient when more than one resource matches.
        if len(entries) == 1:
            xref = next(iter(entries))
            font = cache.get(xref) if cache is not None else None
            if font is None:
                try:
                    buffer = document.extract_font(xref)[3]
                    if buffer and len(buffer) <= 8 * 1024**2 and _embed_allowed(buffer):
                        font = pymupdf.Font(fontbuffer=buffer)
                    if cache is not None and font is not None:
                        cache[xref] = font
                        if len(cache) > 4:
                            cache.popitem(last=False)
                except (ValueError, RuntimeError):
                    pass
            if font is not None and font.is_writable and all(font.has_glyph(ord(c)) for c in set(text)):
                return font, font.name, False
    name = target.font.lower()
    family = family or ("cour" if any(s in name for s in ("courier", "consolas")) else
                        "tiro" if "times" in name else "helv")
    bold, italic = bool(target.flags & 16), bool(target.flags & 2)
    aliases = {"helv": ("helv", "heit", "hebo", "hebi"),
               "cour": ("cour", "coit", "cobo", "cobi"),
               "tiro": ("tiro", "tiit", "tibo", "tibi")}
    font = pymupdf.Font(aliases[family][2 * bold + italic])
    cjk_fallback = not all(font.has_glyph(ord(c)) for c in set(text))
    if cjk_fallback:
        font = pymupdf.Font("cjk")
    if not all(font.has_glyph(ord(c)) for c in set(text)):
        raise PDFOperationError(tr("Il font disponibile non contiene tutti i caratteri richiesti."))
    return font, font.name, cjk_fallback or target.font != font.name


def font_details(source, target, text=None):
    with open_static_pdf(source) as document:
        _, name, fallback = replacement_font(document, document[target.page], target, text or target.text)
    return name, fallback


def font_description(source, target, text=None):
    name, fallback = font_details(source, target, text)
    return name + (tr(" · font compatibile") if fallback else tr(" · font originale"))


def normalize_draft(text, limit, *, multiline=False):
    if not isinstance(text, str) or len(text) > limit or not text.strip():
        message = ("Inserisci un paragrafo non vuoto, fino a 16000 caratteri." if multiline
                   else "Inserisci una riga non vuota, fino a 4000 caratteri.")
        raise PDFOperationError(tr(message))
    text = unicodedata.normalize('NFC', text)
    if any((unicodedata.category(c).startswith('C') or c in '\u2028\u2029')
           and not (multiline and c == '\n') for c in text):
        raise PDFOperationError(tr("Il testo contiene caratteri di controllo o interruzioni di riga non supportati."))
    if any(unicodedata.category(c).startswith('M') or unicodedata.bidirectional(c) in ('R', 'AL', 'AN')
           for c in text):
        raise PDFOperationError(tr("Il testo contiene una scrittura che richiede composizione tipografica non ancora supportata."))
    return text


def validate_size(size):
    if type(size) not in (int, float) or not math.isfinite(size) or not .5 <= size <= 300:
        raise PDFOperationError(tr("Dimensione del testo non valida."))


def layout_font(index, target, text, style):
    return replacement_font(index.page.parent, index.page, target, text, index.fonts, style.family,
                            reuse_embedded=style.bold is None and style.italic is None)


def span_layout(index, target, text, size, style, *, delete=False):
    import pymupdf
    page = index.page
    found = next(((line, span) for address, line, span in index.spans if address == target.address), None)
    if (found is None or not supported_paint(found[1]) or found[0]['wmode'] != 0
            or abs(found[0]['dir'][0] - 1) > .001 or abs(found[0]['dir'][1]) > .001
            or make_target(target.revision, page, target.address, *found) != target):
        raise PDFOperationError(tr("Il testo è cambiato. Selezionalo nuovamente."))
    if delete:
        return TextLayout((), target.bbox, 0)
    if style.width is not None or style.selection is not None:
        raise PDFOperationError(tr("Formato del testo non valido."))
    text = normalize_draft(text, 4000)
    validate_size(size)
    box = pymupdf.Rect(target.bbox)
    styled = style.apply(target)
    font, name, fallback = layout_font(index, styled, text, style)
    width = font.text_length(text, fontsize=size)
    if width > box.x1 - target.origin[0] + .1:
        raise PDFOperationError(tr("Il testo supera lo spazio originale. Accorcialo oppure riduci la dimensione."))
    if size * (font.ascender - font.descender) > box.height + .5:
        raise PDFOperationError(tr("Il testo supera l'altezza originale. Riduci la dimensione."))
    run = TextRun(*target.origin, text, font, size, styled.color, name, fallback, width)
    return TextLayout((run,), tuple(box | run.box()), 1)


def plan_text(index, target, text, size, alignment, formatting=None, *, delete=False):
    from .paragraph_edit import ParagraphTarget, paragraph_layout
    if type(target) not in (TextTarget, ParagraphTarget):
        raise PDFOperationError(tr("Selezione di testo non valida."))
    style = text_format(formatting)
    if type(target) is ParagraphTarget:
        return paragraph_layout(index, target, text, size, alignment, style, delete=delete), target.members
    return span_layout(index, target, text, size, style, delete=delete), (target,)


def layout_info(source, target, text, size, alignment, cache, output=None, formatting=None):
    index = cache.page(source, target.page)
    key = target, text, size, alignment, formatting
    try:
        if index.layout is None or index.layout[0] != key:
            layout, members = plan_text(index, *key)
            index.layout = key, layout, members
        _, layout, members = index.layout
        info = layout.info(index.page, target.font)
        if output is not None:
            with open_static_pdf(source) as modified:
                page = modified[target.page]
                page = rewrite_page(page, index.spans, members, layout)
                info['raster'] = render_preview(page, layout, target.bbox, output)
        return info
    except PDFOperationError as exc:
        return dict(bbox=target.display_bbox, lines=0, font=target.font, fallback=False,
                    fits=False, message=str(exc), runs=[], raster=None)


def replace_content(writer, reader, source, target, text, size, alignment, cache, formatting=None, *, delete=False):
    index = cache.page(source, target.page)
    # Cached layouts are scoped to an immutable revision and exact target.
    # Rewrite still verifies the actual glyphs and paint order on a fresh page.
    key = target, text, size, alignment, formatting
    if not delete and index.layout is not None and index.layout[0] == key:
        _, layout, members = index.layout
    else:
        layout, members = plan_text(index, *key, delete=delete)
    with open_static_pdf(source) as document:
        rewrite_page(document[target.page], index.spans, members, layout)
        transplant_page(writer, reader, document, target.page)
