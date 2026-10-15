"""Generate our own static AcroForm example without JavaScript or submission."""
from pathlib import Path
import argparse

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
    FloatObject, NameObject, NumberObject, TextStringObject,
)


def create_example(destination: Path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    fonts = DictionaryObject()
    for name, face in (("/Helv", "/Helvetica"), ("/Bold", "/Helvetica-Bold")):
        fonts[NameObject(name)] = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(face), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    resources = DictionaryObject({NameObject("/Font"): fonts})
    page[NameObject("/Resources")] = resources
    annotations, fields, commands = ArrayObject(), ArrayObject(), []
    page[NameObject("/Annots")] = annotations
    form = DictionaryObject({NameObject("/Fields"): fields, NameObject("/DR"): resources,
        NameObject("/DA"): TextStringObject("/Helv 11 Tf 0.10 0.16 0.25 rg"),
        NameObject("/NeedAppearances"): BooleanObject(False)})
    writer.root_object[NameObject("/AcroForm")] = writer._add_object(form)

    def text(x, y, value, size=11, bold=False, color="0.10 0.16 0.25"):
        encoded = value.encode("cp1252")
        literal = "".join(chr(byte) if 32 <= byte <= 126 and chr(byte) not in "()\\" else f"\\{byte:03o}" for byte in encoded)
        commands.append(f"BT /{'Bold' if bold else 'Helv'} {size} Tf {color} rg {x} {y} Td ({literal}) Tj ET")

    def box(x, y, width, height, color="0.94 0.96 0.99"):
        commands.append(f"{color} rg {x} {y} {width} {height} re f")

    def field(name, label, ft, rect, flags=0, value="", options=None, max_length=None):
        obj = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject(ft), NameObject("/T"): TextStringObject(name),
            NameObject("/TU"): TextStringObject(label), NameObject("/F"): NumberObject(4),
            NameObject("/Ff"): NumberObject(flags), NameObject("/Rect"): ArrayObject(map(FloatObject, rect)),
            NameObject("/P"): page.indirect_reference,
            NameObject("/DA"): TextStringObject("/Helv 11 Tf 0.10 0.16 0.25 rg"),
            NameObject("/V"): TextStringObject(value),
            NameObject("/MK"): DictionaryObject({NameObject("/BG"): ArrayObject(map(FloatObject, (0.94, 0.96, 0.99)))}),
            NameObject("/BS"): DictionaryObject({NameObject("/W"): NumberObject(1), NameObject("/S"): NameObject("/S")})})
        if max_length is not None:
            obj[NameObject("/MaxLen")] = NumberObject(max_length)
        if options is not None:
            obj[NameObject("/Opt")] = ArrayObject(ArrayObject(map(TextStringObject, pair)) for pair in options)
        ref = writer._add_object(obj)
        fields.append(ref)
        annotations.append(ref)
        x0, y0, x1, y1 = rect
        box(x0, y0, x1 - x0, y1 - y0)
        return obj, ref

    def button_appearances(state, radio=False):
        appearances = DictionaryObject()
        circle = "9 1 m 13.42 1 17 4.58 17 9 c 17 13.42 13.42 17 9 17 c 4.58 17 1 13.42 1 9 c 1 4.58 4.58 1 9 1 c h"
        border = "0.65 0.72 0.80 RG 1 w " + (circle + " S\n" if radio else "1 1 16 16 re S\n")
        for key in ("/Off", state):
            stream = DecodedStreamObject()
            mark = ""
            if key != "/Off":
                mark = "0.13 0.34 0.68 rg 9 5 m 11.21 5 13 6.79 13 9 c 13 11.21 11.21 13 9 13 c 6.79 13 5 11.21 5 9 c 5 6.79 6.79 5 9 5 c h f" if radio else "0.13 0.34 0.68 RG 2 w 4 9 m 8 5 l 14 14 l S"
            stream.set_data((border + mark).encode("ascii"))
            stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject(map(NumberObject, (0, 0, 18, 18))), NameObject("/Resources"): DictionaryObject()})
            appearances[NameObject(key)] = writer._add_object(stream)
        return DictionaryObject({NameObject("/N"): appearances})

    box(0, 746, 595, 96, "0.10 0.20 0.37")
    text(40, 803, "APE PDF STUDIO / ESEMPIO", 10, True, "0.66 0.80 1")
    text(40, 769, "Un PDF da compilare", 26, True, "1 1 1")
    text(40, 719, "Dati di prova. Nessun invio automatico, nessuno script.", 11)
    text(40, 696, "Compila sulla pagina. Tab: campo successivo. Salva per conservare.", 10)
    text(40, 651, "Nome e cognome *", 11, True)
    field("nome", "Nome e cognome", "/Tx", (40, 611, 555, 639), flags=2, max_length=100)
    text(40, 585, "Email", 11, True)
    field("email", "Email", "/Tx", (40, 545, 555, 573), max_length=150)
    text(40, 519, "Tipo di richiesta", 11, True)
    field("categoria", "Tipo di richiesta", "/Ch", (40, 478, 282, 507), flags=1 << 17,
          options=(("informazioni", "Informazioni"), ("assistenza", "Assistenza"), ("altro", "Altro")))
    text(310, 519, "Argomenti (scelta multipla)", 11, True)
    field("interessi", "Argomenti (scelta multipla)", "/Ch", (310, 421, 555, 507), flags=1 << 21,
          options=(("lettura", "Lettura PDF"), ("moduli", "Moduli compilabili"), ("ocr", "Riconoscimento OCR")))
    text(40, 452, "Preferenza di contatto", 11, True)
    group = DictionaryObject({NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("contatto"),
        NameObject("/TU"): TextStringObject("Preferenza di contatto"), NameObject("/Ff"): NumberObject(1 << 15),
        NameObject("/V"): NameObject("/Off"), NameObject("/Kids"): ArrayObject()})
    group_ref = writer._add_object(group)
    fields.append(group_ref)
    for x, name, label in ((40, "/Email", "Email"), (135, "/Telefono", "Telefono")):
        widget = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Parent"): group_ref, NameObject("/P"): page.indirect_reference,
            NameObject("/Rect"): ArrayObject(map(NumberObject, (x, 421, x + 18, 439))), NameObject("/F"): NumberObject(4),
            NameObject("/AP"): button_appearances(name, True), NameObject("/AS"): NameObject("/Off")})
        ref = writer._add_object(widget)
        annotations.append(ref)
        group["/Kids"].append(ref)
        text(x + 24, 426, label, 10)
    text(40, 393, "Note", 11, True)
    field("note", "Note", "/Tx", (40, 269, 555, 380), flags=1 << 12, max_length=2000)
    checkbox, _ = field("consenso", "Ho letto le istruzioni dell'esempio", "/Btn", (40, 227, 58, 245))
    checkbox[NameObject("/AP")] = button_appearances("/Yes")
    checkbox[NameObject("/V")] = NameObject("/Off")
    checkbox[NameObject("/AS")] = NameObject("/Off")
    text(68, 232, "Ho letto le istruzioni dell'esempio", 11)
    text(40, 199, "Riferimento (sola lettura)", 10, True)
    field("riferimento", "Riferimento (sola lettura)", "/Tx", (40, 163, 555, 190), flags=1, value="APE-DEMO-001")
    text(40, 123, "Prova anche i collegamenti con conferma:", 10)
    for label, url, rect in (("Sito Python", "https://www.python.org/", (40, 92, 135, 111)),
                             ("Email di esempio", "mailto:info@example.com?subject=Prova%20modulo", (190, 92, 345, 111))):
        text(rect[0], 98, label, 11, False, "0.13 0.34 0.68")
        annotation = DictionaryObject({NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject(map(NumberObject, rect)), NameObject("/Border"): ArrayObject(map(NumberObject, (0, 0, 0))),
            NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(url)})})
        annotations.append(writer._add_object(annotation))
    text(40, 49, "Documento sintetico / AcroForm standard / I campi restano compilabili dopo il salvataggio", 8)
    stream = DecodedStreamObject()
    stream.set_data(("q\n" + "\n".join(commands) + "\nQ").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    from ape_pdf_studio.core.pdf_forms import write_values
    write_values(writer, {"nome": "", "email": "", "categoria": "", "interessi": [], "note": "", "riferimento": "APE-DEMO-001"})
    writer.add_metadata({"/Title": "Modulo compilabile di esempio", "/Author": "APE PDF Studio", "/Subject": "AcroForm statico e collegamenti con conferma"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer.write(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "examples/modulo_compilabile.pdf")
    print(create_example(parser.parse_args().destination))
