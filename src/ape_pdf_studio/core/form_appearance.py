"""Bake existing widget appearances in private render copies for QtPdf.

QtPdf does not draw AcroForm widgets. The saved editable revision is untouched;
only the already checked page appearance streams become static drawing commands.
"""
from __future__ import annotations

import io
import math

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, StreamObject
from .pdf_links import pdf_get


def bake_widgets(writer, *, printing=False):
    for number, page in enumerate(writer.pages):
        kept, commands = ArrayObject(), []
        resources = DictionaryObject(pdf_get(page, "/Resources", {}))
        xobjects = DictionaryObject(pdf_get(resources, "/XObject", {}))
        for index, reference in enumerate(pdf_get(page, "/Annots", [])):
            widget = reference.get_object()
            if widget.get("/Subtype") != "/Widget":
                kept.append(reference)
                continue
            flags = int(widget.get("/F", 0))
            if flags & (1 | 2) or (printing and not flags & 4) or (not printing and flags & 32):
                continue
            normal = pdf_get(pdf_get(widget, "/AP", {}), "/N")
            if normal is None:
                kept.append(reference)
                continue
            normal = normal.get_object()
            if not isinstance(normal, StreamObject):
                normal = normal.get(pdf_get(widget, "/AS", "/Off"))
                normal = normal.get_object() if normal is not None else None
            if not isinstance(normal, StreamObject) or "/BBox" not in normal or "/Rect" not in widget:
                kept.append(reference)
                continue
            box = tuple(map(float, normal["/BBox"]))
            rect = tuple(map(float, widget["/Rect"]))
            matrix = tuple(map(float, pdf_get(normal, "/Matrix", (1, 0, 0, 1, 0, 0))))
            if len(box) != 4 or len(rect) != 4 or len(matrix) != 6 or not all(
                    math.isfinite(value) and abs(value) <= 1e7 for value in box + rect + matrix):
                raise ValueError("Invalid widget appearance geometry")
            a, b, c, d, e, f = matrix
            corners = [(a * x + c * y + e, b * x + d * y + f)
                       for x in (box[0], box[2]) for y in (box[1], box[3])]
            left, bottom = min(x for x, _ in corners), min(y for _, y in corners)
            width, height = max(x for x, _ in corners) - left, max(y for _, y in corners) - bottom
            if width <= 0 or height <= 0:
                continue
            sx, sy = abs(rect[2] - rect[0]) / width, abs(rect[3] - rect[1]) / height
            tx, ty = min(rect[0], rect[2]) - left * sx, min(rect[1], rect[3]) - bottom * sy
            if not all(math.isfinite(value) and abs(value) <= 1e9 for value in (sx, sy, tx, ty)):
                raise ValueError("Invalid widget appearance transform")
            key = NameObject(f"/APEForm{number}_{index}")
            while key in xobjects:
                key = NameObject(str(key) + "_")
            normal[NameObject("/Type")] = NameObject("/XObject")
            normal[NameObject("/Subtype")] = NameObject("/Form")
            xobjects[key] = normal.indirect_reference or writer._add_object(normal)
            commands.append(f"q {sx:.12g} 0 0 {sy:.12g} {tx:.12g} {ty:.12g} cm {key} Do Q")
        if commands:
            resources[NameObject("/XObject")] = xobjects
            page[NameObject("/Resources")] = resources
            stream = DecodedStreamObject()
            # Isolate the original content's graphics state from appearances.
            stream.set_data(("Q\n" + "\n".join(commands)).encode("ascii"))
            prefix = DecodedStreamObject()
            prefix.set_data(b"q\n")
            old = page.get("/Contents")
            contents = list(old.get_object()) if old is not None and isinstance(old.get_object(), ArrayObject) else ([old] if old is not None else [])
            page[NameObject("/Contents")] = ArrayObject([writer._add_object(prefix), *contents, writer._add_object(stream)])
        if kept:
            page[NameObject("/Annots")] = kept
        else:
            page.pop("/Annots", None)
    writer.root_object.pop("/AcroForm", None)


def display_copy(data, password=None):
    with PdfReader(io.BytesIO(data)) as reader:
        if reader.is_encrypted and not reader.decrypt(password or ""):
            raise ValueError("PDF authentication failed")
        writer = PdfWriter(clone_from=reader)
        from .pdf_forms import field_chain, field_name, inherited, read_fields, write_values
        regenerate = set()
        form = pdf_get(reader.root_object, "/AcroForm", {})
        need = pdf_get(form, "/NeedAppearances")
        need = getattr(need, "value", False)
        for page in reader.pages:
            for reference in pdf_get(page, "/Annots", []):
                widget = reference.get_object()
                if widget.get("/Subtype") == "/Widget":
                    chain = field_chain(widget)
                    if inherited(chain, "/FT") in ("/Tx", "/Ch") and (need or "/N" not in pdf_get(widget, "/AP", {})):
                        regenerate.add(field_name(chain))
        if regenerate:
            write_values(writer, {field.name: list(field.value) if isinstance(field.value, tuple) else field.value
                                 for field in read_fields(reader) if field.name in regenerate})
        bake_widgets(writer)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
