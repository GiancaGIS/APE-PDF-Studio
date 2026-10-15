"""Static AcroForm fields and validated values; no scripts, submission or XFA."""
from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from .errors import PDFOperationError
from .localization import tr
from .metadata import valid_xml_text
from .pdf_links import displayed_rect, pdf_get

MAX_FIELDS = 500
MAX_TEXT = 32768
READ_ONLY = 1
MULTILINE = 1 << 12
PASSWORD = 1 << 13
NO_TOGGLE_OFF = 1 << 14
RADIO = 1 << 15
PUSH_BUTTON = 1 << 16
COMBO = 1 << 17
EDIT_CHOICE = 1 << 18
MULTISELECT = 1 << 21


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    kind: str
    value: str | tuple[str, ...]
    flags: int
    max_length: int
    options: tuple[tuple[str, str], ...]
    widgets: tuple[tuple[int, tuple[float, ...]], ...]
    readonly: bool = False
    widget_states: tuple[str, ...] = ()


def field_chain(widget):
    chain, seen = [], set()
    while widget is not None:
        widget = widget.get_object()
        if not isinstance(widget, DictionaryObject) or id(widget) in seen or len(chain) >= 64:
            raise PDFOperationError(tr("La struttura del modulo PDF non è leggibile."))
        seen.add(id(widget))
        chain.append(widget)
        widget = widget.get("/Parent")
    return chain


def inherited(chain, key, default=None):
    return next((obj[key] for obj in chain if key in obj), default)


def field_name(chain):
    return ".".join(str(obj["/T"]) for obj in reversed(chain) if obj.get("/T"))


def read_fields(reader) -> tuple[FormField, ...]:
    fields = {}
    for page_number, page in enumerate(reader.pages):
        for reference in pdf_get(page, "/Annots", []):
            widget = reference.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            chain = field_chain(widget)
            name = field_name(chain)
            if not name:
                continue
            if len(name) > 4096 or (name not in fields and len(fields) >= MAX_FIELDS):
                raise PDFOperationError(tr("Il modulo supera il limite di 500 campi."))
            flags = int(inherited(chain, "/Ff", 0))
            ft = str(inherited(chain, "/FT", ""))
            kind = {"/Tx": "text", "/Ch": "choice", "/Btn": "checkbox"}.get(ft, "unsupported")
            if ft == "/Btn" and flags & PUSH_BUTTON:
                kind = "unsupported"
            elif ft == "/Btn" and flags & RADIO:
                kind = "radio"
            owner_index = next(index for index, obj in enumerate(chain) if "/T" in obj)
            value = inherited(chain[owner_index:], "/V", "")
            value = tuple(map(str, value)) if isinstance(value, ArrayObject) else str(value)
            if kind == "unsupported":
                value = ""
            if len(str(value)) > MAX_TEXT:
                raise PDFOperationError(tr("Il valore di un campo del modulo è troppo lungo."))
            options = []
            if kind == "choice":
                for option in inherited(chain, "/Opt", []):
                    if len(options) >= MAX_FIELDS:
                        raise PDFOperationError(tr("Il modulo contiene troppe opzioni di scelta."))
                    if isinstance(option, ArrayObject):
                        if len(option) != 2:
                            raise PDFOperationError(tr("La struttura del modulo PDF non è leggibile."))
                        options.append((str(option[0]), str(option[1])))
                    else:
                        options.append((str(option), str(option)))
            elif kind in ("checkbox", "radio"):
                normal = widget.get("/AP", {}).get_object().get("/N", {}) if "/AP" in widget else {}
                normal = normal.get_object() if hasattr(normal, "get_object") else normal
                if isinstance(normal, DictionaryObject):
                    for state in normal:
                        if state != "/Off":
                            options.append((str(state), str(state)[1:]))
            if any(len(a) > 4096 or len(b) > 4096 for a, b in options):
                raise PDFOperationError(tr("Il valore di un campo del modulo è troppo lungo."))
            label = str(inherited(chain, "/TU", name))[:4096]
            rect = displayed_rect(page, widget["/Rect"]) if "/Rect" in widget else ()
            visible = bool(rect) and not int(widget.get("/F", 0)) & 35
            entry = FormField(name, label, kind, value, flags,
                              min(MAX_TEXT, max(0, int(inherited(chain, "/MaxLen", MAX_TEXT)))),
                              tuple(options), ((page_number, rect),) if visible else (),
                              bool(flags & READ_ONLY) or kind == "unsupported",
                              (options[0][0] if options and kind in ("checkbox", "radio") else "",) if visible else ())
            if name in fields:
                previous = fields[name]
                if (previous.kind, previous.flags, previous.value) != (entry.kind, entry.flags, entry.value):
                    raise PDFOperationError(tr("Il modulo contiene nomi di campo ambigui."))
                fields[name] = replace(previous, widgets=previous.widgets + entry.widgets,
                                       widget_states=previous.widget_states + entry.widget_states,
                                       options=tuple(dict.fromkeys(previous.options + entry.options)))
            else:
                fields[name] = entry
    return tuple(replace(field, readonly=True) if field.kind in ("checkbox", "radio") and
                 (not field.options or (field.kind == "checkbox" and len(field.options) != 1)) else field
                 for field in fields.values())


def validate_changes(fields, values):
    if not isinstance(values, dict):
        raise PDFOperationError(tr("Valori del modulo non validi."))
    by_name = {field.name: field for field in fields}
    changes = {}
    for name, value in values.items():
        field = by_name.get(name)
        if field is None or field.readonly:
            raise PDFOperationError(tr("Questo campo del modulo non può essere modificato."))
        if isinstance(value, list):
            value = tuple(value)
        texts = value if isinstance(value, tuple) else (value,)
        if any(not isinstance(text, str) or len(text) > MAX_TEXT or not valid_xml_text(text) for text in texts):
            raise PDFOperationError(tr("Valori del modulo non validi."))
        allowed = {option[0] for option in field.options}
        if field.kind == "text":
            if not isinstance(value, str) or len(value) > field.max_length or (
                    not field.flags & MULTILINE and ("\n" in value or "\r" in value)):
                raise PDFOperationError(tr("Il testo supera il limite del campo o contiene righe non consentite."))
        elif field.kind in ("checkbox", "radio"):
            if not isinstance(value, str) or value not in allowed | {"/Off"}:
                raise PDFOperationError(tr("Scegli una delle opzioni del modulo."))
            if field.kind == "radio" and field.flags & NO_TOGGLE_OFF and value == "/Off":
                raise PDFOperationError(tr("Scegli una delle opzioni del modulo."))
        elif field.kind == "choice":
            if isinstance(value, tuple) and not field.flags & MULTISELECT:
                raise PDFOperationError(tr("Scegli una delle opzioni del modulo."))
            if len(texts) != len(set(texts)) or any(text not in allowed and text != "" for text in texts):
                if not (field.flags & COMBO and field.flags & EDIT_CHOICE and isinstance(value, str)):
                    raise PDFOperationError(tr("Scegli una delle opzioni del modulo."))
        if value != field.value:
            changes[name] = list(value) if isinstance(value, tuple) else value
    return changes


def write_values(writer, changes):
    # pypdf also matches partial names. Give every leaf a temporary unique name
    # so changing "city" cannot accidentally update "sender.city" as well.
    renamed, tokens, owners, pending, choices = [], {}, set(), [], []
    for page in writer.pages:
        for reference in pdf_get(page, "/Annots", []):
            widget = reference.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            chain = field_chain(widget)
            if not field_name(chain):
                continue
            owner = next(obj for obj in chain if "/T" in obj)
            if id(owner) in owners:
                continue
            owners.add(id(owner))
            name = field_name(chain)
            token = "APE_" + uuid4().hex
            renamed.append((owner, owner["/T"], owner.get("/FT")))
            if name in changes:
                tokens[token] = changes[name]
                if inherited(chain, "/FT") == "/Ch":
                    options = inherited(chain, "/Opt", [])
                    labels = {str(option[0] if isinstance(option, ArrayObject) else option):
                              str(option[1] if isinstance(option, ArrayObject) else option) for option in options}
                    value = changes[name]
                    tokens[token] = [labels.get(v, v) for v in value] if isinstance(value, list) else labels.get(value, value)
                    # pypdf's list appearance generator expects strings, even
                    # though PDF permits [export value, display label] pairs.
                    choices.append((owner, owner.get("/Opt"), options, value))
            pending.append((owner, token, inherited(chain, "/FT", "/Tx")))
    for owner, token, field_type in pending:
        owner[NameObject("/T")] = TextStringObject(token)
        owner[NameObject("/FT")] = NameObject(field_type)
    for owner, original_options, options, value in choices:
        owner[NameObject("/Opt")] = ArrayObject(TextStringObject(str(option[1] if isinstance(option, ArrayObject) else option)) for option in options)
    try:
        writer.update_page_form_field_values(None, tokens, auto_regenerate=False)
    finally:
        for owner, name, original_type in renamed:
            owner[NameObject("/T")] = name
            if original_type is None:
                del owner["/FT"]
            else:
                owner[NameObject("/FT")] = original_type
        for owner, original_options, options, value in choices:
            if original_options is None:
                del owner["/Opt"]
            else:
                owner[NameObject("/Opt")] = original_options
            owner[NameObject("/V")] = ArrayObject(map(TextStringObject, value)) if isinstance(value, list) else TextStringObject(value)
    # Repair button group values and retain choice selection indices.
    for page in writer.pages:
        for reference in pdf_get(page, "/Annots", []):
            widget = reference.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            chain = field_chain(widget)
            name = field_name(chain)
            if name not in changes:
                continue
            if inherited(chain, "/FT") == "/Btn":
                owner = next(obj for obj in chain if "/T" in obj)
                owner[NameObject("/V")] = NameObject(changes[name])
                if widget is not owner and "/V" in widget:
                    del widget["/V"]
            elif inherited(chain, "/FT") == "/Tx":
                # A plain text edit supersedes any old rich-text representation.
                for obj in chain:
                    if "/RV" in obj:
                        del obj["/RV"]
            elif inherited(chain, "/FT") == "/Ch":
                owner = next(obj for obj in chain if "/T" in obj)
                options = inherited(chain, "/Opt", [])
                selected = changes[name] if isinstance(changes[name], list) else [changes[name]]
                indices = [NumberObject(i) for i, option in enumerate(options)
                           if str(option[0] if isinstance(option, ArrayObject) else option) in selected]
                owner[NameObject("/I")] = ArrayObject(indices)
