"""Apply a verified text layout inside the document worker."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import io
import math

from .errors import PDFOperationError
from .localization import tr
from .pdf_runtime import open_static_pdf


def display_rotation(page):
    """Map unrotated MuPDF points to the physical viewport, including UserUnit."""
    matrix = page.rotation_matrix
    rect = page.rect
    # MuPDF's linear terms are correct, but its offsets use unscaled PDF units.
    if page.rotation in (90, 180):
        matrix.e = rect.width
    if page.rotation in (180, 270):
        matrix.f = rect.height
    return matrix


@dataclass(frozen=True)
class TextRun:
    x: float
    y: float
    text: str
    font: object
    size: float
    color: int
    name: str
    fallback: bool
    width: float

    def box(self):
        import pymupdf
        return pymupdf.Rect(self.x, self.y - self.size * self.font.ascender,
                            self.x + self.width, self.y - self.size * self.font.descender)


@dataclass(frozen=True)
class TextLayout:
    runs: tuple
    bbox: tuple
    lines: int

    def info(self, page, font):
        import pymupdf
        transform = display_rotation(page)
        return dict(bbox=tuple(pymupdf.Rect(self.bbox) * transform), lines=self.lines,
                    font=self.runs[0].name if self.runs else font,
                    fallback=any(run.fallback for run in self.runs), fits=True, message='',
                    runs=[(run.text, tuple(pymupdf.Point(run.x, run.y) * transform),
                           run.size, run.color, run.name, tuple(run.box() * transform))
                          for run in self.runs], raster=None)


def _origin_key(codepoint, position):
    return codepoint, round(position[0], 3), round(position[1], 3)


def _traces(page):
    """Keep glyph identity and paint state independent of span grouping."""
    traces = _content_log(page, text=True)
    if sum(len(trace['chars']) for trace in traces) > 100_000:
        raise PDFOperationError(tr("Questa pagina contiene troppo testo per la modifica diretta."))
    return traces


def _glyph_key(trace, entry):
    codepoint, glyph, origin, box = entry
    color = trace['color']
    return (_origin_key(codepoint, origin), glyph, tuple(round(v, 3) for v in box),
            trace['font'], trace['type'], trace['opacity'], trace.get('layer', ''),
            tuple(color) if isinstance(color, (list, tuple)) else color)


def _artwork(page):
    log = _content_log(page, text=False)
    if len(log) > 100_000:
        raise PDFOperationError(tr("Questa pagina contiene troppi elementi per la modifica diretta."))
    return log


def _content_log(page, *, text):
    import pymupdf
    from pymupdf import mupdf
    # Use the same devices as get_texttrace() and get_bboxlog(), limited to
    # page content. Annotation appearances are a separate, preserved layer.
    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        rows = []
        if text:
            device = pymupdf.extra.JM_new_texttrace_device(rows)
            bounds = mupdf.fz_bound_page(page.this)
            device.ptm = mupdf.FzMatrix(1, 0, 0, -1, 0, bounds.y1)
        else:
            device = pymupdf.JM_new_bbox_device(rows, True)
        mupdf.fz_run_page_contents(page.this, device, mupdf.FzMatrix(), mupdf.FzCookie())
        mupdf.fz_close_device(device)
        return rows
    finally:
        if rotation:
            page.set_rotation(rotation)


def _uses_layers(page, artwork):
    if any(layer for _, _, layer in artwork):
        return True
    document = page.parent
    if document.xref_get_key(document.pdf_catalog(), 'OCProperties')[0] == 'null':
        return False
    # Visibility can change after a save. Inspect attachments to XObjects
    # and marked-content operators even if the paint log lists no layer.
    from pypdf.generic import ContentStream, DecodedStreamObject

    def marked(data):
        stream = DecodedStreamObject()
        stream.set_data(data)
        return any(op in (b'BDC', b'BMC', b'DP') for _, op in ContentStream(stream, None).operations)

    forms = {entry[0] for entry in page.get_xobjects()}
    images = {entry[0] for entry in page.get_images(full=True)}
    if len(forms | images) > 1024:
        return True
    if any(document.xref_get_key(xref, 'OC')[0] != 'null' for xref in forms | images | {page.xref}):
        return True
    return marked(page.read_contents()) or any(marked(document.xref_stream(xref)) for xref in forms)


def _guard(page, spans, members, layout):
    import pymupdf
    document = page.parent
    if document.xref_get_key(document.pdf_catalog(), 'StructTreeRoot')[0] != 'null':
        raise PDFOperationError(tr("Questo PDF contiene tag strutturali. La modifica diretta potrebbe alterarne le associazioni."))
    artwork = _artwork(page)
    if _uses_layers(page, artwork):
        raise PDFOperationError(tr("La modifica diretta di questa pagina con livelli PDF non è supportata."))

    selected = {member.address for member in members}
    originals = [pymupdf.Rect(member.bbox) for member in members]
    changed = pymupdf.Rect(layout.bbox)
    for box in originals:
        changed |= box
    written = pymupdf.Rect()
    for run in layout.runs:
        written |= run.box()
    safe_page = page.rect * ~display_rotation(page)
    if not written.is_empty and not (safe_page + (-.25, -.25, .25, .25)).contains(written):
        raise PDFOperationError(tr("Il testo supera il bordo della pagina."))

    for address, _, span in spans:
        if address in selected:
            continue
        box = pymupdf.Rect(span['bbox'])
        if (box & changed).get_area() > .001 and (
                (box & written).get_area() > .1 or any((box & rect).get_area() > .001 for rect in originals)):
            raise PDFOperationError(tr("Il testo si sovrappone ad altri contenuti testuali: modifica non applicata."))

    # Links have an independent click region. Text markup has semantic quads
    # that would still refer to the geometry of the old words.
    markup = (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE,
              pymupdf.PDF_ANNOT_STRIKE_OUT, pymupdf.PDF_ANNOT_SQUIGGLY)
    if any(a.type[0] in markup and (a.rect & changed).get_area() > .1 for a in page.annots() or ()):
        raise PDFOperationError(tr("Il testo selezionato è associato a un'evidenziazione o a un'altra marcatura."))
    if any((widget.rect & changed).get_area() > .1 for widget in page.widgets() or ()):
        raise PDFOperationError(tr("Il testo si sovrappone a un campo del modulo."))
    if not members and any((link['from'] & changed).get_area() > .1 for link in page.get_links()):
        raise PDFOperationError(tr("Il nuovo testo si sovrappone a un collegamento PDF."))
    for drawing in page.get_drawings(extended=True):
        if drawing['type'] == 'clip' and (drawing['scissor'] & changed).get_area() > .1:
            segments = drawing['items']
            if (len(segments) != 1 or segments[0][0] != 're'
                    or not drawing['scissor'].contains(changed)):
                raise PDFOperationError(tr("Il testo interseca un ritaglio grafico che non può essere preservato."))
        elif (drawing['type'] == 'group' and (drawing['rect'] & changed).get_area() > .1
              and (drawing['opacity'] < 1 or drawing['blendmode'] != 'Normal')):
            raise PDFOperationError(tr("Il testo interseca un ritaglio grafico che non può essere preservato."))

    traces = _traces(page)
    origins = defaultdict(list)
    for trace in traces:
        for entry in trace['chars']:
            origins[_origin_key(entry[0], entry[2])].append((trace, entry))
    removed = Counter()
    order = len(artwork)
    for address, _, span in spans:
        if address not in selected:
            continue
        for entry in span['chars']:
            if entry.get('synthetic') and entry['c'].isspace():
                continue
            candidates = origins[_origin_key(ord(entry['c']), entry['origin'])]
            if len(candidates) != 1 or candidates[0][0]['type'] != 0 or candidates[0][0]['opacity'] < 1:
                raise PDFOperationError(tr("Non è possibile isolare i glifi selezionati con certezza."))
            trace, glyph = candidates[0]
            order = min(order, trace['seqno'])
            removed[_glyph_key(trace, glyph)] += 1
    if members and not removed:
        raise PDFOperationError(tr("Non è possibile isolare i glifi selezionati con certezza."))

    # An overpaint or even a translucent image must stay above its original
    # text. A reconstruction is appended to the original content stream.
    graphics = [(i, kind, bbox, layer) for i, (kind, bbox, layer) in enumerate(artwork)
                if kind not in ('fill-text', 'stroke-text', 'ignore-text')]
    for i, _, bbox, _ in graphics:
        if (not members or i > order) and (pymupdf.Rect(bbox) & changed).get_area() > .1:
            raise PDFOperationError(tr("Il testo interseca elementi grafici che devono mantenere il loro ordine di visualizzazione."))
    kept = []
    for trace in traces:
        for entry in trace['chars']:
            key = _glyph_key(trace, entry)
            if removed[key]:
                removed[key] -= 1
            else:
                kept.append(key)
    return kept, [(kind, bbox, layer) for _, kind, bbox, layer in graphics]


def rewrite_page(page, spans, members, layout):
    """Remove selected glyphs, check all neighbours, then write searchable runs."""
    import pymupdf
    kept, graphics = _guard(page, spans, members, layout)
    if members:
        document = page.parent
        saved_annotations = None
        if any(a.type[0] == pymupdf.PDF_ANNOT_REDACT for a in page.annots() or ()):
            _, saved_annotations = document.xref_get_key(page.xref, 'Annots')
            document.xref_set_key(page.xref, 'Annots', 'null')
            page = document.reload_page(page)
        for member in members:
            page.add_redact_annot(member.bbox, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if saved_annotations is not None:
            document.xref_set_key(page.xref, 'Annots', saved_annotations)
            page = document.reload_page(page)
        after = [_glyph_key(trace, entry) for trace in _traces(page) for entry in trace['chars']]
        remaining_art = [(kind, bbox, layer) for kind, bbox, layer in _artwork(page)
                         if kind not in ('fill-text', 'stroke-text', 'ignore-text')]
        if after != kept or graphics != remaining_art:
            raise PDFOperationError(tr("La modifica altererebbe altri contenuti della pagina."))

    if not layout.runs:
        return page
    rotation = page.rotation
    page.set_rotation(0)
    try:
        rect = page.rect
        # Use a normalized vector page so CropBox, UserUnit and rotation are
        # applied by the same page placement code in preview and commit.
        with open_static_pdf() as overlay:
            output = overlay.new_page(width=rect.width, height=rect.height)
            start = 0
            while start < len(layout.runs):
                color = layout.runs[start].color
                writer = pymupdf.TextWriter(rect)
                while start < len(layout.runs) and layout.runs[start].color == color:
                    run = layout.runs[start]
                    writer.append((run.x, run.y), run.text, font=run.font, fontsize=run.size)
                    start += 1
                writer.write_text(output, color=tuple(((color >> shift) & 255) / 255 for shift in (16, 8, 0)))
            # Subset only the newly generated fonts. The source font programs
            # and their resources may be shared by other pages.
            overlay.subset_fonts()
            page.show_pdf_page(rect, overlay, 0)
    finally:
        page.set_rotation(rotation)
    final = [(trace, entry) for trace in _traces(page) for entry in trace['chars']]
    if ([_glyph_key(trace, entry) for trace, entry in final[:len(kept)]] != kept or
            [entry[0] for _, entry in final[len(kept):] if not chr(entry[0]).isspace()] !=
            [ord(letter) for run in layout.runs for letter in run.text if not letter.isspace()]):
        raise PDFOperationError(tr("Non è stato possibile verificare il testo nel PDF risultante."))
    return page


def transplant_page(writer, reader, document, number):
    from pypdf import PdfReader
    from pypdf.generic import NameObject
    # Export just the edited page's resource graph from MuPDF. pypdf preserves
    # the original catalog, links, form, attachments and all other pages.
    with open_static_pdf() as changed:
        changed.insert_pdf(document, from_page=number, to_page=number, links=False, annots=False, widgets=False)
        data = changed.tobytes(deflate=True, garbage=1)
    writer.clone_document_from_reader(reader)
    with PdfReader(io.BytesIO(data)) as changed:
        for key in ('/Contents', '/Resources'):
            if key in changed.pages[0]:
                writer.pages[number][NameObject(key)] = changed.pages[0].raw_get(key).clone(writer)
            else:
                writer.pages[number].pop(NameObject(key), None)
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)


def render_preview(page, layout, original, output):
    """Return validated geometry for a bounded raw RGB patch, never a PDF."""
    import pymupdf
    clip = pymupdf.Rect(layout.bbox) | pymupdf.Rect(original)
    clip = ((clip + (-2, -2, 2, 2)) * display_rotation(page)) & page.rect
    if clip.is_empty or not all(math.isfinite(v) for v in clip):
        raise PDFOperationError(tr("Anteprima del testo non disponibile."))
    scale = min(2.0, 1022 / max(clip.width, clip.height))
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, colorspace=pymupdf.csRGB)
    if not 0 < pixmap.width <= 1024 or not 0 < pixmap.height <= 1024:
        raise PDFOperationError(tr("Anteprima del testo non disponibile."))
    with output.open('xb') as stream:
        stream.write(pixmap.samples_mv)
    box = tuple(v / scale for v in pixmap.irect)
    return box, pixmap.width, pixmap.height
