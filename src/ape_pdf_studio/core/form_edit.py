"""Edit existing AcroForm fields in place, preserving their tree and widgets."""
from __future__ import annotations

from collections import defaultdict
import math
import re

from pypdf.generic import ArrayObject, BooleanObject, FloatObject, NameObject, NumberObject, TextStringObject

from .errors import PDFOperationError
from .form_design import (checkbox_appearance, create_fields, document_field_names,
                          install_font, overlap, validate_options, validate_proposals)
from .localization import tr
from .metadata import valid_xml_text
from .page_coordinates import displayed_size, to_pdf
from .pdf_forms import (COMBO, MULTILINE, MULTISELECT, READ_ONLY, field_chain,
                        field_name, read_fields, write_values)
from .pdf_links import pdf_get

PROPERTIES = ("name", "label", "kind", "multiline", "readonly", "required", "options")


def field_kind(field):
    return ("combo" if field.flags & COMBO else "list") if field.kind == "choice" else field.kind


def design_fields(reader):
    proposals = []
    for field in read_fields(reader):
        if field.kind == "unsupported":  # Includes signatures and action buttons.
            continue
        for index, (page, rect) in enumerate(field.widgets):
            proposals.append(dict(source=field.name, widget=index, page=page, rect=list(rect),
                name=field.name, label=field.label, kind=field_kind(field),
                multiline=bool(field.flags & MULTILINE), readonly=bool(field.flags & READ_ONLY),
                required=bool(field.flags & 2), options=[list(pair) for pair in field.options] if field.kind == "choice" else []))
    return proposals


def validate_design(reader, proposals, deleted=()):
    originals = {(p["source"], p["widget"]): p for p in design_fields(reader)}
    fields = {f.name: f for f in read_fields(reader)}
    if (not isinstance(proposals, list) or len(proposals) > 10000 or
            not isinstance(deleted, (list, tuple, set)) or len(deleted) > 500 or
            any(not isinstance(name, str) or name not in fields or fields[name].kind == "unsupported" for name in deleted)):
        raise PDFOperationError(tr("Modifiche del modulo non valide."))
    deleted = set(deleted)
    groups, additions, seen = defaultdict(list), [], set()
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise PDFOperationError(tr("Modifiche del modulo non valide."))
        if "source" not in proposal:
            additions.append(proposal)
            continue
        source, index = proposal.get("source"), proposal.get("widget")
        if not isinstance(source, str) or type(index) is not int:
            raise PDFOperationError(tr("Modifiche del modulo non valide."))
        key = (source, index)
        original = originals.get(key)
        if original is None or key in seen or source in deleted or type(proposal.get("page")) is not int or proposal.get("page") != original["page"]:
            raise PDFOperationError(tr("Modifiche del modulo non valide."))
        seen.add(key)
        entry = dict(proposal)
        name, label, kind = (entry.get(k) for k in ("name", "label", "kind"))
        prefix = source.rpartition(".")[0]
        leaf = name[len(prefix)+1:] if isinstance(name, str) and prefix and name.startswith(prefix+".") else name
        if name != source and (not isinstance(name, str) or not isinstance(leaf, str) or
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", leaf) or
                name != ((prefix+".") if prefix else "")+leaf):
            raise PDFOperationError(tr("Rinomina solo il nome finale del campo, mantenendo la gerarchia e usando lettere, numeri e trattini bassi."))
        if not isinstance(label, str) or len(label) > 4096 or not valid_xml_text(label):
            raise PDFOperationError(tr("Modifiche del modulo non valide."))
        if kind not in ("text", "checkbox", "combo", "list") and not (kind == "radio" and original["kind"] == "radio"):
            raise PDFOperationError(tr("Modifiche del modulo non valide."))
        for flag in ("multiline", "readonly", "required"):
            if type(entry.get(flag)) is not bool:
                raise PDFOperationError(tr("Modifiche del modulo non valide."))
        rect = entry.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect):
            raise PDFOperationError(tr("Modifiche del modulo non valide."))
        if list(rect) != original["rect"]:
            width, height = displayed_size(reader.pages[original["page"]])
            if not (0 <= rect[0] < rect[2] <= width and 0 <= rect[1] < rect[3] <= height and rect[2]-rect[0] >= 5 and rect[3]-rect[1] >= 5):
                raise PDFOperationError(tr("Il campo deve rimanere nella pagina e misurare almeno 5 punti per lato."))
        entry["rect"] = list(rect)
        if kind in ("combo", "list"):
            # Some existing choice fields have no options (or empty export values).
            # Preserve those definitions unless the user actually edits them.
            entry["options"] = original["options"] if kind == original["kind"] and entry.get("options") == original["options"] else validate_options(entry.get("options"))
        else:
            entry["options"] = []
        groups[source].append(entry)
    names = document_field_names(reader) - deleted - set(groups)
    for source, entries in groups.items():
        if len(entries) != len(fields[source].widgets) or any(
                any(p.get(k) != entries[0].get(k) for k in PROPERTIES) for p in entries):
            raise PDFOperationError(tr("Le proprietà devono essere uguali per tutte le istanze dello stesso campo."))
        name = entries[0]["name"]
        if name in names:
            raise PDFOperationError(tr("Usa nomi di campo univoci, con lettere, numeri e trattini bassi; inizia con una lettera."))
        names.add(name)
    occupied = [(name, page, rect) for name, field in fields.items() if name not in deleted and name not in groups
                for page, rect in field.widgets]
    occupied += [(source, p["page"], p["rect"]) for source, entries in groups.items() for p in entries]
    for source, entries in groups.items():
        for p in entries:
            if p["rect"] != originals[(source, p["widget"])]["rect"] and any(
                    other != source and page == p["page"] and overlap(rect, p["rect"]) > .5 for other, page, rect in occupied):
                raise PDFOperationError(tr("Il campo si sovrappone a un altro campo. Correggi le proposte prima di crearle."))
    if additions:
        from dataclasses import replace
        existing = [replace(f, name=groups[name][0]["name"] if name in groups else name,
                            widgets=tuple((p["page"], tuple(p["rect"])) for p in groups[name]) if name in groups else f.widgets)
                    for name, f in fields.items() if name not in deleted]
        additions = validate_proposals(additions, [displayed_size(p) for p in reader.pages], existing, names)
    return [p for entries in groups.values() for p in entries] + additions


def _delete_fields(writer, names, widgets):
    owners = {id(next(obj for obj in field_chain(widget) if "/T" in obj))
              for name, widget in widgets if name in names}
    for page in writer.pages:
        if "/Annots" in page:
            page[NameObject("/Annots")] = ArrayObject(ref for ref in pdf_get(page, "/Annots")
                if not (pdf_get(ref.get_object(), "/Subtype") == "/Widget" and field_name(field_chain(ref)) in names))
    removed = set(owners)

    def prune(refs, depth=0):
        if depth > 64:
            raise PDFOperationError(tr("La struttura del modulo PDF non è leggibile."))
        kept = ArrayObject()
        for ref in refs:
            obj = ref.get_object()
            if id(obj) in removed:
                continue
            if "/Kids" in obj:
                children = pdf_get(obj, "/Kids")
                result = prune(children, depth+1)
                if children and not result:
                    removed.add(id(obj))
                    continue
                obj[NameObject("/Kids")] = result
            kept.append(ref)
        return kept

    form = pdf_get(writer.root_object, "/AcroForm")
    form[NameObject("/Fields")] = prune(pdf_get(form, "/Fields", []))
    if "/CO" in form:
        form[NameObject("/CO")] = ArrayObject(ref for ref in pdf_get(form, "/CO") if id(ref.get_object()) not in removed)


def apply_design(writer, proposals, deleted=()):
    proposals = validate_design(writer, proposals, deleted)
    form = pdf_get(writer.root_object, "/AcroForm", {})
    need_appearances = getattr(pdf_get(form, "/NeedAppearances"), "value", False)
    original_rows = {(p["source"], p["widget"]): p for p in design_fields(writer)}
    fields = {f.name: f for f in read_fields(writer)}
    groups = defaultdict(list)
    additions = []
    for p in proposals:
        (groups[p["source"]] if "source" in p else additions).append(p)
    changed = {name for name, rows in groups.items() if any(p != original_rows[(name, p["widget"])] for p in rows)}
    if not changed and not additions and not deleted:
        return False
    widgets = []
    for page in writer.pages:
        for ref in pdf_get(page, "/Annots", []):
            widget = ref.get_object()
            if pdf_get(widget, "/Subtype") == "/Widget":
                widgets.append((field_name(field_chain(widget)), widget))
    if deleted:
        _delete_fields(writer, set(deleted), widgets)
    values = {}
    font = None
    for source in changed:
        rows = groups[source]
        proposal, original = rows[0], fields[source]
        kind = proposal["kind"]
        type_changed = kind != field_kind(original)
        options_changed = kind in ("combo", "list") and proposal["options"] != [list(p) for p in original.options]
        regen = type_changed or options_changed or proposal["multiline"] != bool(original.flags & MULTILINE) or any(
            p["rect"] != original_rows[(source, p["widget"])]["rect"] for p in rows)
        related = [widget for name, widget in widgets if name == source]
        owners = {id(owner): owner for widget in related for owner in [next(obj for obj in field_chain(widget) if "/T" in obj)]}
        flags = original.flags & 7 if type_changed else original.flags
        flags = (flags & ~(READ_ONLY | 2 | MULTILINE)) | int(proposal["readonly"]) | (2 if proposal["required"] else 0)
        if kind == "text" and proposal["multiline"]:
            flags |= MULTILINE
        if type_changed:
            flags |= COMBO if kind == "combo" else MULTISELECT if kind == "list" else 0
        for owner in owners.values():
            if proposal["name"] != source:
                old_partial = str(owner["/T"])
                prefix = source[:-len(old_partial)] if old_partial else ""
                owner[NameObject("/T")] = TextStringObject(proposal["name"][len(prefix):])
            if proposal["label"] != original.label:
                owner[NameObject("/TU")] = TextStringObject(proposal["label"])
            owner[NameObject("/Ff")] = NumberObject(flags)
            if type_changed:
                for key in ("/V", "/DV", "/RV", "/Opt", "/I", "/TI", "/MaxLen", "/AP", "/AS"):
                    owner.pop(key, None)
                owner[NameObject("/FT")] = NameObject("/Tx" if kind == "text" else "/Ch" if kind in ("combo", "list") else "/Btn")
                # Override a default inherited from an ancestor of another type.
                owner[NameObject("/DV")] = NameObject("/Off") if kind == "checkbox" else ArrayObject() if kind == "list" else TextStringObject("")
                if kind == "text":
                    owner[NameObject("/MaxLen")] = NumberObject(32768)
            if kind in ("combo", "list") and (type_changed or options_changed):
                owner[NameObject("/Opt")] = ArrayObject(ArrayObject(map(TextStringObject, pair)) for pair in proposal["options"])
                owner.pop("/I", None)
                # Removing an option also invalidates a default selecting it.
                default = pdf_get(owner, "/DV")
                allowed = {pair[0] for pair in proposal["options"]}
                if default is not None and any(str(v) not in allowed for v in (default if isinstance(default, ArrayObject) else [default])):
                    owner.pop("/DV", None)
        for widget in related:
            # Child overrides must not retain the old field type/properties.
            if id(widget) not in owners:
                for key in ("/TU", "/Ff") + (("/FT", "/V", "/DV", "/RV", "/Opt", "/I", "/TI", "/MaxLen") if type_changed else ()):
                    widget.pop(key, None)
                if options_changed:
                    widget.pop("/Opt", None)
            if type_changed:
                widget.pop("/AP", None)
                widget.pop("/AS", None)
                if kind == "checkbox":
                    checkbox_appearance(writer, widget)
        for p in rows:
            # Visible widgets use the same ordering as read_fields. Include hidden
            # widgets above for shared properties, without changing their geometry.
            visible = [widget for widget in related if "/Rect" in widget and not int(pdf_get(widget, "/F", 0)) & 35]
            widget = visible[p["widget"]]
            if p["rect"] != original_rows[(source, p["widget"])]["rect"]:
                page = writer.pages[p["page"]]
                left, top, right, bottom = p["rect"]
                points = [to_pdf(page, x, y) for x in (left, right) for y in (top, bottom)]
                widget[NameObject("/Rect")] = ArrayObject(map(FloatObject,
                    (min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points))))
        if regen and kind in ("text", "combo", "list"):
            font = font or install_font(writer)
            for widget in related:
                widget.pop("/AP", None)
                widget[NameObject("/DA")] = TextStringObject(f"{font} 11 Tf 0 g")
            for p in rows:
                widget = visible[p["widget"]]
                page = writer.pages[p["page"]]
                size = min(11, (p["rect"][3]-p["rect"][1])*.65)/float(pdf_get(page, "/UserUnit", 1))
                widget[NameObject("/DA")] = TextStringObject(f"{font} {size:.6g} Tf 0 g")
            value = "" if type_changed else original.value
            if kind in ("combo", "list"):
                allowed = {pair[0] for pair in proposal["options"]}
                value = tuple(v for v in value if v in allowed) if isinstance(value, tuple) else value if value in allowed else ""
            if kind == "text" and not proposal["multiline"]:
                value = value.replace("\r", " ").replace("\n", " ")
            values[proposal["name"]] = list(value) if isinstance(value, tuple) else value
        elif type_changed and kind == "checkbox":
            values[proposal["name"]] = "/Off"
    if values:
        write_values(writer, values)
    if additions:
        create_fields(writer, additions)
    if need_appearances:
        form[NameObject("/NeedAppearances")] = BooleanObject(True)
    return True
