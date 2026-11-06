"""Bounded image decoding and static page content, inside the PDF worker only."""
import math
from pathlib import Path
import struct

from pypdf import PdfReader

from .errors import PDFOperationError
from .localization import tr
from .page_coordinates import displayed_size, to_pdf
from .pdf_runtime import open_static_pdf

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_RASTER_SIDE = 2048


def image_size(data):
    """Read only bounded PNG/JPEG headers before allocating a decoded raster."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(data) < 33 or data[8:16] != b'\0\0\0\rIHDR':
            raise ValueError('PNG header')
        return struct.unpack('>II', data[16:24])
    if not data.startswith(b'\xff\xd8\xff'):
        raise ValueError('Image format')
    offset = 2
    limit = min(len(data), 1024*1024)
    while offset+4 <= limit:
        if data[offset] != 0xff:
            break
        while offset < limit and data[offset] == 0xff:
            offset += 1
        if offset+3 > limit:
            break
        marker = data[offset]
        offset += 1
        if marker in (0xda, 0xd9):
            break
        length = int.from_bytes(data[offset:offset+2], 'big')
        if length < 2 or offset+length > limit:
            break
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length < 8:
                break
            height, width = struct.unpack('>HH', data[offset+3:offset+7])
            return width, height
        offset += length
    raise ValueError('JPEG header')


def prepare(source, asset, raster):
    import pymupdf
    try:
        with Path(source).open('rb') as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise ValueError('Image size')
        width, height = image_size(data)
        if (not 0 < width <= 8192 or not 0 < height <= 8192 or width*height > MAX_IMAGE_PIXELS or
                max(width, height) > 100*min(width, height)):
            raise ValueError('Image dimensions')
        pix = pymupdf.Pixmap(data)
        if (pix.width, pix.height) != (width, height):
            raise ValueError('Inconsistent image dimensions')
        if pix.colorspace != pymupdf.csRGB:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if not pix.alpha:
            pix = pymupdf.Pixmap(pix, 1)
        while max(pix.width, pix.height) > MAX_RASTER_SIDE:
            pix.shrink(1)
        with open_static_pdf() as document:
            page = document.new_page(width=pix.width, height=pix.height)
            page.insert_image(page.rect, pixmap=pix)
            document.save(str(asset), deflate=True)
        # MuPDF samples have premultiplied alpha. No image decoder in the GUI.
        Path(raster).write_bytes(pix.samples)
        return {'width': pix.width, 'height': pix.height}
    except Exception as exc:
        Path(asset).unlink(missing_ok=True)
        Path(raster).unlink(missing_ok=True)
        raise PDFOperationError(tr('Immagine firma non valida. Usa un PNG o JPEG fino a 16 MiB e 16 megapixel.')) from exc


def insert(writer, reader, page_index, asset, rect):
    if (type(page_index) is not int or not 0 <= page_index < len(reader.pages) or
            not isinstance(rect, (list, tuple)) or len(rect) != 4 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in rect)):
        raise PDFOperationError(tr('Posizione della firma non valida.'))
    width, height = displayed_size(reader.pages[page_index])
    x0, y0, x1, y1 = rect
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height and x1-x0 >= 1 and y1-y0 >= 1):
        raise PDFOperationError(tr('Posizione della firma non valida.'))
    with PdfReader(asset) as stamp:
        if len(stamp.pages) != 1:
            raise PDFOperationError(tr('Posizione della firma non valida.'))
        sw, sh = displayed_size(stamp.pages[0])
        scale = min((x1-x0)/sw, (y1-y0)/sh)
        left = x0 + (x1-x0 - sw*scale)/2
        bottom = height - y1 + (y1-y0 - sh*scale)/2
        writer.clone_document_from_reader(reader)
        writer.pdf_header = max(reader.pdf_header, '%PDF-1.4')
        page = writer.pages[page_index]
        ox, oy = to_pdf(page, 0, height)
        xx, xy = to_pdf(page, 1, height)
        yx, yy = to_pdf(page, 0, height - 1)
        xx, xy, yx, yy = xx - ox, xy - oy, yx - ox, yy - oy
        matrix = (xx*scale, xy*scale, yx*scale, yy*scale,
                  xx*left + yx*bottom + ox, xy*left + yy*bottom + oy)
        page.merge_transformed_page(stamp.pages[0], matrix, over=True, expand=False)
