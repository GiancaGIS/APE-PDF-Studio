"""Real AcroForm round trips, appearances, qualified names and revision history."""
import importlib.util
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.pdf_forms import read_fields

spec = importlib.util.spec_from_file_location("generate_form_example", Path(__file__).resolve().parents[1] / "scripts/generate_form_example.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.fixture
def form_pdf(tmp_path):
    return generator.create_example(tmp_path / "form.pdf")


def test_round_trip_all_types_appearance_and_history(form_pdf, tmp_path):
    original = form_pdf.read_bytes()
    editor = PDFEditor(form_pdf)
    try:
        changes = {"nome": "Giulia Bianchi", "email": "giulia@example.com", "categoria": "assistenza",
                   "interessi": ["moduli", "ocr"], "contatto": "/Telefono", "consenso": "/Yes", "note": "Prima riga\nSeconda riga"}
        editor.fill_form(changes, editor.path)
        revision = editor.path
        with PdfReader(revision) as reader:
            fields = {field.name: field for field in read_fields(reader)}
            assert fields["interessi"].value == ("moduli", "ocr")
            assert fields["contatto"].value == "/Telefono"
            assert fields["riferimento"].readonly
            assert reader.root_object["/AcroForm"]["/NeedAppearances"].value is False
            widgets = [ref.get_object() for ref in reader.pages[0]["/Annots"] if ref.get_object().get("/Subtype") == "/Widget"]
            radios = [widget for widget in widgets if "/Parent" in widget]
            assert [str(widget["/AS"]) for widget in radios] == ["/Off", "/Telefono"]
            assert all("/V" not in widget for widget in radios)
            assert b"Giulia Bianchi" in next(widget for widget in widgets if widget.get("/T") == "nome")["/AP"]["/N"].get_data()
            choice = next(widget for widget in widgets if widget.get("/T") == "categoria")
            assert b"Assistenza" in choice["/AP"]["/N"].get_data()
            assert str(choice["/Opt"][1][0]) == "assistenza"
            assert choice["/I"] == [1]
            assert len(reader.get_fields()) == 8
        output = editor.save_as(tmp_path / "filled.pdf")
        assert not editor.dirty
        with PdfReader(output) as reader:
            assert reader.get_fields()["nome"]["/V"] == changes["nome"]
            assert reader.metadata.title == "Modulo compilabile di esempio"
        assert editor.undo() and not editor.modified_from_original and editor.dirty
        assert {f.name: f.value for f in editor.interactions()[1]}["nome"] == ""
        assert editor.redo() and editor.path == revision and not editor.dirty
        editor.fill_form({"contatto": "/Email", "consenso": "/Off", "interessi": []}, editor.path)
        assert {f.name: f.value for f in editor.interactions()[1]}["contatto"] == "/Email"
        assert form_pdf.read_bytes() == original
    finally:
        editor.close()


@pytest.mark.parametrize("changes", [{"riferimento": "changed"}, {"missing": "x"}, {"nome": "x" * 101},
    {"nome": "line\nbreak"}, {"nome": "\x00"}, {"categoria": "invalid"}, {"categoria": ["assistenza"]},
    {"consenso": "/Other"}, {"interessi": ["ocr", "ocr"]}, {"contatto": "/Other"}])
def test_invalid_values_do_not_create_revision(form_pdf, changes):
    editor = PDFEditor(form_pdf)
    try:
        with pytest.raises(PDFOperationError):
            editor.fill_form(changes, editor.path)
        assert not editor.dirty and not editor.can_undo
    finally:
        editor.close()


def test_stale_revision_and_noop(form_pdf):
    editor = PDFEditor(form_pdf)
    try:
        revision = editor.path
        editor.fill_form({"nome": ""}, revision)
        assert editor.path == revision
        editor.fill_form({"nome": "First"}, revision)
        with pytest.raises(PDFOperationError, match="cambiato"):
            editor.fill_form({"nome": "Stale"}, revision)
        editor.undo()
        editor.fill_form({"nome": "Branch"}, editor.path)
        assert not editor.can_redo
    finally:
        editor.close()


def test_qualified_names_do_not_update_same_leaf(form_pdf, tmp_path):
    writer = PdfWriter(clone_from=form_pdf)
    leaf = next(ref.get_object() for ref in writer.pages[0]["/Annots"] if ref.get_object().get("/T") == "email")
    leaf[NameObject("/T")] = TextStringObject("nome")
    leaf_ref = leaf.indirect_reference
    parent = writer._add_object(DictionaryObject({NameObject("/T"): TextStringObject("sender"), NameObject("/Kids"): ArrayObject([leaf_ref])}))
    leaf[NameObject("/Parent")] = parent
    roots = writer.root_object["/AcroForm"]["/Fields"]
    roots.remove(leaf_ref)
    roots.append(parent)
    source = tmp_path / "qualified.pdf"
    writer.write(source)
    editor = PDFEditor(source)
    try:
        editor.fill_form({"nome": "Root"}, editor.path)
        fields = {f.name: f.value for f in editor.interactions()[1]}
        assert fields["nome"] == "Root" and fields["sender.nome"] == ""
        editor.fill_form({"sender.nome": "Nested"}, editor.path)
        fields = {f.name: f.value for f in editor.interactions()[1]}
        assert fields["nome"] == "Root" and fields["sender.nome"] == "Nested"
    finally:
        editor.close()


@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_widget_geometry_crop_and_userunit(form_pdf, rotation):
    with PdfReader(form_pdf) as reader:
        page = reader.pages[0]
        page.cropbox.lower_left = (20, 30)
        page.cropbox.upper_right = (575, 812)
        page.rotate(rotation)
        page[NameObject("/UserUnit")] = NumberObject(2)
        field = read_fields(reader)[0]
        left, top, right, bottom = field.widgets[0][1]
        assert right > left and bottom > top
        assert (right - left, bottom - top) == ((1030, 56) if rotation % 180 == 0 else (56, 1030))


def test_protected_forms_stay_readonly(form_pdf, tmp_path):
    writer = PdfWriter(clone_from=form_pdf)
    writer.encrypt(user_password="", owner_password="owner", algorithm="AES-256")
    path = tmp_path / "protected.pdf"
    writer.write(path)
    editor = PDFEditor(path)
    try:
        assert editor.encrypted and len(editor.interactions()[1]) == 8
        with pytest.raises(PDFOperationError, match="sola lettura"):
            editor.fill_form({"nome": "x"}, editor.path)
    finally:
        editor.close()
