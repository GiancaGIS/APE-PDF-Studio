"""Bounded geometric form suggestions. Suggestions are never applied automatically."""
from __future__ import annotations

from pathlib import Path
import re

from .form_design import MAX_ANALYSIS_PAGES, document_field_names, field_slug, overlap
from .localization import tr
from .pdf_forms import MAX_FIELDS, read_fields
from .security import BLOCKED, configure_parser, inspect_reader

MAX_PIXELS = 4_000_000


def _label(rect, words):
    left, top, right, bottom = rect
    candidates = [word for word in words if word[4].strip("_. ") and (
        (0 <= left-word[2] < 150 and word[1] < bottom+4 and word[3] > top-4) or
        (right-left <= 30 and 0 <= word[0]-right < 150 and word[1] < bottom+4 and word[3] > top-4) or
        (0 <= top-word[3] < 28 and word[0] < right and word[2] > left))]
    if not candidates:
        return ""
    closest = min(candidates, key=lambda word: abs(left-word[2]) + 2*abs(top-word[1]))
    line = [word for word in candidates if abs(word[1]-closest[1]) < 3 and abs(word[3]-closest[3]) < 4]
    return " ".join(word[4] for word in sorted(line, key=lambda word: word[0])).strip(" :_\t")[:160]


def _raster_shapes(page):
    import cv2
    import numpy as np
    import pymupdf
    cv2.setNumThreads(1)
    scale = min(2.0, ((MAX_PIXELS-10000)/(page.rect.width*page.rect.height)) ** .5)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False, annots=False)
    if pix.width*pix.height > MAX_PIXELS:
        raise ValueError("Raster budget exceeded")
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    ink = cv2.threshold(image, 155, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    shapes,empty_boxes = [],[]
    if len(contours) > 30000:
        raise ValueError("Too many raster contours")
    sx, sy = page.rect.width/pix.width, page.rect.height/pix.height
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        w, h = width*sx, height*sy
        if not (7 <= h <= 200 and 7 <= w <= page.rect.width*.92):
            continue
        approximation = cv2.approxPolyDP(contour, .025*cv2.arcLength(contour, True), True)
        if len(approximation) == 4 and cv2.isContourConvex(approximation) and cv2.contourArea(contour) > width*height*.75:
            rect=(x*sx, y*sy, (x+width)*sx, (y+height)*sy)
            shapes.append(rect)
            if 7<=w<=24 and 7<=h<=24:
                pad=max(2,round(min(width,height)*.22))
                interior=ink[y+pad:y+height-pad,x+pad:x+width-pad]
                if interior.size and cv2.countNonZero(interior)/interior.size<.08:
                    empty_boxes.append(rect)
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, round(45/sx)), 1)))
    lines, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in lines[:10000]:
        x, y, width, height = cv2.boundingRect(contour)
        if height*sy < 3.5 and 45 <= width*sx < page.rect.width*.85:
            shapes.append((x*sx, max(0, y*sy-19), (x+width)*sx, (y+height)*sy))
    return shapes, pix,empty_boxes


def detect_fields(source, pages, *, cache_dir=None, use_ocr=True):
    from pypdf import PdfReader
    import pymupdf
    configure_parser()
    with PdfReader(source) as reader:
        if reader.is_encrypted or set(inspect_reader(reader)) & BLOCKED:
            raise ValueError("Unsupported PDF")
        existing = read_fields(reader)
        reserved_names = document_field_names(reader)
        if len(existing) >= MAX_FIELDS:
            return dict(proposals=[], warnings=[tr("Raggiunto il limite di 500 campi: analizza meno pagine per volta.")])
    with pymupdf.open(source) as document:
        if not isinstance(pages, list) or not 0 < len(pages) <= MAX_ANALYSIS_PAGES or len(set(pages)) != len(pages) or any(
                type(page) is not int or not 0 <= page < len(document) for page in pages):
            raise ValueError("Invalid page selection")
        proposals, warnings = [], []
        names = {field.name for field in existing} | reserved_names
        occupied = [(page, rect) for field in existing for page, rect in field.widgets]
        tessdata, ocr_missing = None, False
        for number in pages:
            page = document[number]
            words = []
            for word in page.get_text("words"):
                rect = pymupdf.Rect(word[:4])*page.rotation_matrix
                words.append((*tuple(rect), str(word[4])[:256]))
            shapes,empty_boxes = [],[]
            drawings = page.get_drawings()
            if len(drawings) > 20000:
                raise ValueError("Too many drawing paths")
            for drawing in drawings:
                if drawing.get("type") not in ("s", "fs"):
                    continue
                for item in drawing["items"]:
                    if item[0] == "re":
                        shapes.append(tuple(item[1]*page.rotation_matrix))
                    elif item[0] == "l":
                        start, end = item[1]*page.rotation_matrix, item[2]*page.rotation_matrix
                        if abs(start.y-end.y) < 1.5 and abs(start.x-end.x) >= 45:
                            shapes.append((min(start.x,end.x), max(0, start.y-19), max(start.x,end.x), start.y+1))
                if drawing.get("closePath") and len(drawing["items"]) in (3,4) and all(item[0] == "l" for item in drawing["items"]):
                    shapes.append(tuple(drawing["rect"]*page.rotation_matrix))
            for word in words:
                if re.fullmatch(r"[_\.]{4,}", word[4]):
                    shapes.append((word[0], max(0, word[3]-20), word[2], word[3]))
            if page.get_images():
                raster, pixels,empty_boxes = _raster_shapes(page)
                shapes.extend(raster)
                if not words and use_ocr and not ocr_missing:
                    try:
                        from .ocr import prepare_tessdata
                        if tessdata is None:
                            tessdata = prepare_tessdata("ita+eng", Path(cache_dir or "."), download=False)
                        ocr_pixels = pymupdf.Pixmap(pymupdf.csRGB,pixels)
                        ocr_pixels.set_dpi(max(1, round(pixels.width/page.rect.width*72)), max(1, round(pixels.height/page.rect.height*72)))
                        data = ocr_pixels.pdfocr_tobytes(language="ita+eng", tessdata=str(tessdata))
                        with pymupdf.open(stream=data, filetype="pdf") as recognized:
                            ocr_page = recognized[0]
                            sx, sy = page.rect.width/ocr_page.rect.width, page.rect.height/ocr_page.rect.height
                            words = [(w[0]*sx,w[1]*sy,w[2]*sx,w[3]*sy,str(w[4])[:256]) for w in ocr_page.get_text("words")]
                        if not words:
                            raise ValueError("No OCR labels")
                    except Exception:
                        ocr_missing = True
                        warnings.append(tr("Etichette OCR non disponibili: puoi rinominare manualmente le proposte delle scansioni."))
            # Boxes precede underlines so a box's bottom edge isn't suggested twice.
            shapes.sort(key=lambda rect: (abs((rect[2]-rect[0])-(rect[3]-rect[1])) > 5, rect[3]-rect[1] < 21))
            for rect in shapes:
                left, top, right, bottom = rect
                width, height = right-left, bottom-top
                if not (0 <= left < right <= page.rect.width and 0 <= top < bottom <= page.rect.height):
                    continue
                checkbox = 7 <= width <= 24 and 7 <= height <= 24 and .7 <= width/height <= 1.4
                if not checkbox and not (45 <= width <= page.rect.width*.92 and 10 <= height <= 200):
                    continue
                inside = [word for word in words if word[4].strip("_. ") and overlap(rect, word[:4]) > .35]
                if checkbox and any(overlap(rect,empty)>.8 for empty in empty_boxes):
                    # OCR can mistake an empty printed square for letters.
                    inside=[]
                if inside or any(p == number and overlap(rect, other) > .45 for p, other in occupied):
                    continue
                label = _label(rect, words)
                base = field_slug(label) if label else f"campo_p{number+1}"
                if not base[0].isalpha():
                    base = "campo_" + base
                name, suffix = base, 2
                while name in names:
                    name, suffix = f"{base}_{suffix}", suffix+1
                names.add(name)
                occupied.append((number, rect))
                proposals.append(dict(page=number, rect=[round(v,3) for v in rect], kind="checkbox" if checkbox else "text",
                                      name=name, label=label or name, multiline=not checkbox and height > 40))
                if len(proposals) >= MAX_FIELDS-len(existing):
                    warnings.append(tr("Raggiunto il limite di 500 campi: analizza meno pagine per volta."))
                    return dict(proposals=proposals, warnings=warnings)
        proposals.sort(key=lambda p:(p["page"],p["rect"][1],p["rect"][0]))
        return dict(proposals=proposals, warnings=warnings)
