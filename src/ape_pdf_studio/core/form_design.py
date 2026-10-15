"""Validated creation of script-free AcroForm text fields and checkboxes."""
from __future__ import annotations

import math
import re
import unicodedata

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                          FloatObject, NameObject, NumberObject, TextStringObject)

from .errors import PDFOperationError
from .localization import tr
from .metadata import valid_xml_text
from .page_coordinates import displayed_size, to_pdf
from .pdf_forms import MAX_FIELDS, read_fields, write_values
from .pdf_links import pdf_get

MAX_ANALYSIS_PAGES = 50


def field_slug(label):
    value = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")[:64] or "campo"


def overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smallest = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return area / smallest if smallest > 0 else 0


def document_field_names(reader):
    """Include hidden fields and parent namespaces when reserving names."""
    form = pdf_get(reader.root_object,"/AcroForm",{})
    stack = [(reference,"",0) for reference in pdf_get(form,"/Fields",[])]
    names,seen = set(),set()
    while stack:
        reference,prefix,depth = stack.pop()
        field = reference.get_object()
        if id(field) in seen:
            continue
        seen.add(id(field))
        if depth>64 or len(seen)>10000:
            raise PDFOperationError(tr("La struttura del modulo PDF non è leggibile."))
        name = str(pdf_get(field,"/T",""))
        qualified = (prefix+"." if prefix else "")+name if name else prefix
        if name:
            names.add(qualified)
        stack.extend((child,qualified,depth+1) for child in pdf_get(field,"/Kids",[]))
    return names


def validate_proposals(proposals, sizes, existing=(), reserved_names=()):
    if not isinstance(proposals, list) or not 0 < len(proposals) <= MAX_FIELDS - len(existing):
        raise PDFOperationError(tr("Seleziona almeno un campo e rispetta il limite complessivo di 500 campi."))
    names = {field.name for field in existing} | set(reserved_names)
    occupied = [(page, rect) for field in existing for page, rect in field.widgets]
    result = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise PDFOperationError(tr("Proposta di campo non valida."))
        page, kind, name, label = (proposal.get(key) for key in ("page", "kind", "name", "label"))
        if type(page) is not int or not 0 <= page < len(sizes) or kind not in ("text", "checkbox"):
            raise PDFOperationError(tr("Proposta di campo non valida."))
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) or name in names:
            raise PDFOperationError(tr("Usa nomi di campo univoci, con lettere, numeri e trattini bassi; inizia con una lettera."))
        if not isinstance(label, str) or len(label) > 256 or not valid_xml_text(label):
            raise PDFOperationError(tr("Etichetta del campo non valida (massimo 256 caratteri)."))
        raw = proposal.get("rect")
        if not isinstance(raw, (list, tuple)) or len(raw) != 4 or any(type(v) not in (float, int) for v in raw):
            raise PDFOperationError(tr("Proposta di campo non valida."))
        rect = tuple(map(float, raw))
        width, height = sizes[page]
        if not all(math.isfinite(v) for v in rect) or not (0 <= rect[0] < rect[2] <= width and 0 <= rect[1] < rect[3] <= height
                and rect[2]-rect[0] >= 5 and rect[3]-rect[1] >= 5):
            raise PDFOperationError(tr("Il campo deve rimanere nella pagina e misurare almeno 5 punti per lato."))
        if any(number == page and overlap(rect, other) > .5 for number, other in occupied):
            raise PDFOperationError(tr("Il campo si sovrappone a un altro campo. Correggi le proposte prima di crearle."))
        multiline = proposal.get("multiline", False)
        if type(multiline) is not bool:
            raise PDFOperationError(tr("Proposta di campo non valida."))
        names.add(name)
        occupied.append((page, rect))
        result.append(dict(page=page, kind=kind, name=name, label=label, rect=list(rect), multiline=multiline))
    return result


def create_fields(writer, proposals):
    existing = read_fields(writer)
    proposals = validate_proposals(proposals, [displayed_size(page) for page in writer.pages], existing, document_field_names(writer))
    form = pdf_get(writer.root_object, "/AcroForm")
    if form is None:
        form = DictionaryObject({NameObject("/Fields"): ArrayObject()})
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(form)
    if "/Fields" not in form:
        form[NameObject("/Fields")] = ArrayObject()
    original_need_appearances = pdf_get(form,"/NeedAppearances")
    resources = pdf_get(form, "/DR", DictionaryObject())
    fonts = pdf_get(resources, "/Font", DictionaryObject())
    font_name = "/APEHelv"
    while font_name in fonts:
        font_name += "_"
    fonts[NameObject(font_name)] = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    resources[NameObject("/Font")] = fonts
    form[NameObject("/DR")] = resources
    initial = {}
    for proposal in proposals:
        page = writer.pages[proposal["page"]]
        left, top, right, bottom = proposal["rect"]
        points = [to_pdf(page, x, y) for x in (left, right) for y in (top, bottom)]
        rect = (min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points))
        checkbox = proposal["kind"] == "checkbox"
        field = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Btn" if checkbox else "/Tx"),
            NameObject("/T"): TextStringObject(proposal["name"]), NameObject("/TU"): TextStringObject(proposal["label"]),
            NameObject("/F"): NumberObject(4), NameObject("/Ff"): NumberObject(4096 if proposal["multiline"] and not checkbox else 0),
            NameObject("/Rect"): ArrayObject(map(FloatObject, rect)), NameObject("/P"): page.indirect_reference,
            NameObject("/DA"): TextStringObject(f"{font_name} {min(11,(bottom-top)*.65)/float(page.get('/UserUnit',1)):.6g} Tf 0 g"),
            NameObject("/MK"): DictionaryObject({NameObject("/R"): NumberObject(page.rotation % 360)}),
            NameObject("/BS"): DictionaryObject({NameObject("/W"): NumberObject(0)})})
        if checkbox:
            appearances = DictionaryObject()
            for state in ("/Off", "/Yes"):
                stream = DecodedStreamObject()
                # Transparent appearance: retain the original printed box.
                stream.set_data(b"" if state == "/Off" else b"0 G 2 w 3 8 m 7 4 l 14 13 l S")
                stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                    NameObject("/BBox"): ArrayObject(map(NumberObject, (0, 0, 18, 18))), NameObject("/Resources"): DictionaryObject()})
                appearances[NameObject(state)] = writer._add_object(stream)
            field[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearances})
            field[NameObject("/V")] = NameObject("/Off")
            field[NameObject("/AS")] = NameObject("/Off")
        else:
            field[NameObject("/V")] = TextStringObject("")
            field[NameObject("/MaxLen")] = NumberObject(32768)
            initial[proposal["name"]] = ""
        reference = writer._add_object(field)
        pdf_get(form, "/Fields").append(reference)
        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(reference)
    if initial:
        write_values(writer, initial)
    else:
        form[NameObject("/NeedAppearances")] = BooleanObject(False)
    if getattr(original_need_appearances,"value",False):
        # Existing fields may require appearance regeneration in other readers.
        form[NameObject("/NeedAppearances")] = BooleanObject(True)
