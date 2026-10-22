"""Page insertion and static watermarks, executed only inside the PDF worker."""
import io
import math

from pypdf import PdfReader

from .errors import PDFOperationError
from .page_coordinates import displayed_size, to_pdf
from .security import MAX_PAGES


def insert_blank(writer, reader, index, reference, paper):
    count = len(reader.pages)
    if (type(index) is not int or not 0 <= index <= count or
            type(reference) is not int or not 0 <= reference < count or
            paper not in ('same', 'a4', 'a4-landscape') or count >= MAX_PAGES):
        raise PDFOperationError('Invalid blank page position or size')
    size = displayed_size(reader.pages[reference]) if paper == 'same' else (595.276, 841.890)
    if paper == 'a4-landscape':
        size = size[::-1]
    writer.clone_document_from_reader(reader)
    writer.insert_blank_page(width=size[0], height=size[1], index=index)


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def watermark(writer, reader, text, size, color, opacity, angle):
    if (not isinstance(text, str) or not text.strip() or len(text) > 200 or
            any(ord(c) < 32 for c in text) or not number(size, 8, 144) or
            not isinstance(color, (list, tuple)) or len(color) != 3 or
            any(not number(c, 0, 1) for c in color) or
            not number(opacity, .05, 1) or not number(angle, -180, 180)):
        raise PDFOperationError('Invalid watermark')
    import pymupdf
    from .pdf_runtime import open_static_pdf
    font = pymupdf.Font('helv')
    if any(not font.has_glyph(ord(c)) for c in text):
        font = pymupdf.Font('cjk')
    padding = size * .15
    width = max(1, font.text_length(text, fontsize=size)) + 2 * padding
    height = (font.ascender - font.descender) * size + 2 * padding
    with open_static_pdf() as doc:
        page = doc.new_page(width=width, height=height)
        line = pymupdf.TextWriter(page.rect)
        line.append((padding, padding + font.ascender * size), text, font=font, fontsize=size)
        line.write_text(page, color=color, opacity=opacity)
        stamp_bytes = doc.tobytes()
    writer.clone_document_from_reader(reader)
    writer.pdf_header = max(writer.pdf_header, '%PDF-1.4')
    with PdfReader(io.BytesIO(stamp_bytes)) as stamp:
        rad = math.radians(angle)
        co, si = math.cos(rad), math.sin(rad)
        for page in writer.pages:
            w, h = displayed_size(page)
            scale = min(1, .9 * w / (abs(co) * width + abs(si) * height),
                        .9 * h / (abs(si) * width + abs(co) * height))
            a, b, c, d = scale * co, scale * si, -scale * si, scale * co
            e, f = w / 2 - a * width / 2 - c * height / 2, h / 2 - b * width / 2 - d * height / 2
            # Displayed, upright PDF coordinates -> original crop/rotation/UserUnit.
            ox, oy = to_pdf(page, 0, h)
            xx, xy = to_pdf(page, 1, h)
            yx, yy = to_pdf(page, 0, h - 1)
            xx, xy, yx, yy = xx - ox, xy - oy, yx - ox, yy - oy
            matrix = (xx*a + yx*b, xy*a + yy*b, xx*c + yx*d, xy*c + yy*d,
                      xx*e + yx*f + ox, xy*e + yy*f + oy)
            page.merge_transformed_page(stamp.pages[0], matrix, over=True, expand=False)
