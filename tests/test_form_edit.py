"""Edit original fields, retaining tree, other values and undoable transactions."""
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.pdf_forms import COMBO, MULTISELECT
from test_pdf_forms import form_pdf


def by_name(editor):
    return {f.name: f for f in editor.interactions()[1]}


@pytest.mark.parametrize("kind,value", [("checkbox", "/Yes"), ("combo", "b"), ("list", ["a", "b"])])
def test_change_type_fill_save_reopen_and_undo(form_pdf, tmp_path, kind, value):
    original = form_pdf.read_bytes()
    editor = PDFEditor(form_pdf)
    try:
        editor.fill_form({"nome": "Previous value", "email": "keep@example.com"}, editor.path)
        before = editor.path
        rows = editor.form_design()
        row = next(p for p in rows if p["source"] == "nome")
        row.update(kind=kind, options=[["a", "First"], ["b", "Second"]], required=False)
        editor.update_form_fields(rows, [], before)
        assert by_name(editor)["nome"].value in ("", "/Off")
        assert by_name(editor)["email"].value == "keep@example.com"
        editor.fill_form({"nome": value}, editor.path)
        output = editor.save_as(tmp_path/"changed.pdf")
        with PdfReader(output) as reader:
            assert reader.get_fields()["email"]["/V"] == "keep@example.com"
            assert reader.metadata.title
            widget = next(ref.get_object() for ref in reader.pages[0]["/Annots"] if ref.get_object().get("/T") == "nome")
            assert "/AP" in widget
            assert widget["/FT"] == ("/Btn" if kind == "checkbox" else "/Ch")
        other = PDFEditor(output)
        try:
            assert by_name(other)["nome"].value == (tuple(value) if isinstance(value, list) else value)
        finally:
            other.close()
        assert editor.undo() and editor.undo() and editor.path == before
        assert by_name(editor)["nome"].value == "Previous value"
        assert editor.redo() and by_name(editor)["nome"].kind != "text"
        assert form_pdf.read_bytes() == original
    finally:
        editor.close()


def test_same_type_properties_keep_values_defaults_appearance_and_other_fields(form_pdf, tmp_path):
    writer = PdfWriter(clone_from=form_pdf)
    widget = next(ref.get_object() for ref in writer.pages[0]["/Annots"] if ref.get_object().get("/T") == "nome")
    widget[NameObject("/DV")] = TextStringObject("Default")
    path = tmp_path/"default.pdf"
    writer.write(path)
    editor = PDFEditor(path)
    try:
        editor.fill_form({"nome": "Keep value", "categoria": "assistenza"}, editor.path)
        with PdfReader(editor.path) as reader:
            before_contents = reader.pages[0]["/Contents"].get_data()
        rows = editor.form_design()
        row = next(p for p in rows if p["source"] == "nome")
        row.update(name="customer", label="Customer name", readonly=True)
        editor.update_form_fields(rows, [], editor.path)
        assert by_name(editor)["customer"].value == "Keep value"
        assert by_name(editor)["customer"].readonly
        assert by_name(editor)["categoria"].value == "assistenza"
        with PdfReader(editor.path) as reader:
            assert reader.get_fields()["customer"]["/DV"] == "Default"
            assert reader.pages[0]["/Contents"].get_data() == before_contents
        rows = editor.form_design()
        next(p for p in rows if p["source"] == "customer")["readonly"] = False
        editor.update_form_fields(rows, [], editor.path)
        editor.fill_form({"customer": "Editable again"}, editor.path)
    finally:
        editor.close()


@pytest.mark.parametrize("source", ["consenso", "categoria", "interessi", "contatto"])
def test_existing_button_choice_and_radio_can_become_text(form_pdf, source):
    editor = PDFEditor(form_pdf)
    try:
        rows = editor.form_design()
        for p in rows:
            if p["source"] == source:
                p.update(kind="text", multiline=False)
        editor.update_form_fields(rows, [], editor.path)
        editor.fill_form({source: "New text"}, editor.path)
        field = by_name(editor)[source]
        assert field.kind == "text" and field.value == "New text"
        if source == "contatto":
            assert len(field.widgets) == 2
        with PdfReader(editor.path) as reader:
            for ref in reader.pages[0]["/Annots"]:
                obj = ref.get_object()
                if obj.get("/T") == source:
                    assert "/Opt" not in obj and "/AS" not in obj
    finally:
        editor.close()


def test_choice_option_edits_keep_export_values_and_remove_invalid_selections(form_pdf):
    editor = PDFEditor(form_pdf)
    try:
        editor.fill_form({"categoria": "assistenza", "interessi": ["lettura", "ocr"]}, editor.path)
        rows = editor.form_design()
        next(p for p in rows if p["source"] == "categoria")["options"] = [["assistenza", "Support"], ["new", "New"]]
        next(p for p in rows if p["source"] == "interessi")["options"] = [["lettura", "Read"], ["new", "New"]]
        editor.update_form_fields(rows, [], editor.path)
        fields = by_name(editor)
        assert fields["categoria"].value == "assistenza"
        assert fields["categoria"].options == (("assistenza", "Support"), ("new", "New"))
        assert fields["interessi"].value == ("lettura",)
        editor.fill_form({"categoria": "new", "interessi": ["new"]}, editor.path)
    finally:
        editor.close()


def test_hierarchy_repeated_widgets_geometry_and_delete(form_pdf, tmp_path):
    writer = PdfWriter()
    writer.append(form_pdf)
    writer.append(form_pdf)
    fields = writer.root_object["/AcroForm"]["/Fields"]
    refs = [ref for ref in fields if ref.get_object().get("/T") == "nome"]
    parent = writer._add_object(DictionaryObject({NameObject("/T"): TextStringObject("sender"), NameObject("/Kids"): ArrayObject(refs)}))
    for ref in refs:
        ref.get_object()[NameObject("/Parent")] = parent
        fields.remove(ref)
    fields.append(parent)
    source = tmp_path/"repeated.pdf"
    writer.write(source)
    editor = PDFEditor(source)
    try:
        rows = editor.form_design()
        renamed = [p for p in rows if p["source"] == "sender.nome"]
        assert len(renamed) == 2
        for p in renamed:
            p.update(name="sender.customer", label="Shared")
        renamed[1]["rect"][0] += 5
        editor.update_form_fields(rows, [], editor.path)
        field = by_name(editor)["sender.customer"]
        assert len(field.widgets) == 2 and field.widgets[1][1][0] == pytest.approx(renamed[1]["rect"][0])
        editor.fill_form({"sender.customer": "Shared value"}, editor.path)
        rows = [p for p in editor.form_design() if p["source"] not in ("sender.customer", "contatto")]
        editor.update_form_fields(rows, ["sender.customer", "contatto"], editor.path)
        assert "sender.customer" not in by_name(editor) and "contatto" not in by_name(editor)
        with PdfReader(editor.path) as reader:
            assert "sender.customer" not in reader.get_fields()
            assert all(ref.get_object().get("/T") != "sender" for ref in reader.root_object["/AcroForm"]["/Fields"])
        assert editor.undo() and by_name(editor)["sender.customer"].value == "Shared value"
    finally:
        editor.close()


@pytest.mark.parametrize("change", ["duplicate", "options", "geometry", "signature", "missing-widget", "stale"])
def test_invalid_changes_do_not_mutate_document(form_pdf, change):
    editor = PDFEditor(form_pdf)
    try:
        rows = editor.form_design()
        revision = editor.path
        target = next(p for p in rows if p["source"] == "nome")
        if change == "duplicate": target["name"] = "email"
        elif change == "options": target.update(kind="combo", options=[["a", "A"], ["a", "B"]])
        elif change == "geometry": target["rect"] = [0, 0, float("nan"), 20]
        elif change == "signature": target["kind"] = "signature"
        elif change == "missing-widget": rows = [p for p in rows if not (p["source"] == "contatto" and p["widget"] == 1)]
        elif change == "stale": editor.rotate(0)
        before = editor.path
        with pytest.raises(PDFOperationError):
            editor.update_form_fields(rows, [], revision)
        assert editor.path == before
    finally:
        editor.close()


def test_noop_and_atomic_delete_edit_add(form_pdf):
    editor = PDFEditor(form_pdf)
    try:
        rows = editor.form_design()
        editor.update_form_fields(rows, [], editor.path)
        assert not editor.dirty and not editor.can_undo
        rows = [p for p in rows if p["source"] != "nome"]
        next(p for p in rows if p["source"] == "email")["label"] = "Updated"
        rows.append(dict(page=0, rect=[40, 203, 555, 231], kind="combo", name="nome", label="Replacement", multiline=False,
                         options=[["a", "A"], ["b", "B"]]))
        editor.update_form_fields(rows, ["nome"], editor.path)
        assert by_name(editor)["nome"].flags & COMBO
        assert by_name(editor)["email"].label == "Updated"
        assert editor.undo() and not editor.dirty
        assert by_name(editor)["nome"].kind == "text"
    finally:
        editor.close()


@pytest.mark.parametrize('rotation', [0,90,180,270])
def test_type_change_on_rotated_scaled_pages(form_pdf,tmp_path,rotation):
    writer=PdfWriter(clone_from=form_pdf)
    page=writer.pages[0]
    page.rotate(rotation)
    page[NameObject('/UserUnit')]=NumberObject(2)
    path=tmp_path/'rotated.pdf'
    writer.write(path)
    editor=PDFEditor(path)
    try:
        original=by_name(editor)['consenso'].widgets
        rows=editor.form_design()
        next(p for p in rows if p['source']=='consenso')['kind']='text'
        editor.update_form_fields(rows,[],editor.path)
        assert by_name(editor)['consenso'].widgets==original
        editor.fill_form({'consenso':'Test'},editor.path)
        from ape_pdf_studio.core.form_appearance import display_copy
        import pymupdf
        data=display_copy(editor.path.read_bytes())
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            assert pdf[0].get_pixmap().width>0
    finally:
        editor.close()


def test_inherited_default_is_cleared_and_signature_field_is_excluded(form_pdf,tmp_path):
    writer=PdfWriter(clone_from=form_pdf)
    fields=writer.root_object['/AcroForm']['/Fields']
    ref=next(ref for ref in fields if ref.get_object().get('/T')=='nome')
    parent=writer._add_object(DictionaryObject({NameObject('/T'):TextStringObject('person'),
        NameObject('/DV'):TextStringObject('Inherited default'),NameObject('/Kids'):ArrayObject([ref])}))
    ref.get_object()[NameObject('/Parent')]=parent
    fields.remove(ref)
    fields.append(parent)
    signature=writer._add_object(DictionaryObject({NameObject('/T'):TextStringObject('signature'),NameObject('/FT'):NameObject('/Sig'),
        NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Widget'),
        NameObject('/Rect'):ArrayObject(map(NumberObject,[40,20,200,40]))}))
    fields.append(signature)
    writer.pages[0]['/Annots'].append(signature)
    path=tmp_path/'inherited.pdf'
    writer.write(path)
    editor=PDFEditor(path)
    try:
        rows=editor.form_design()
        assert all(p['source']!='signature' for p in rows)
        next(p for p in rows if p['source']=='person.nome')['kind']='checkbox'
        editor.update_form_fields(rows,[],editor.path)
        with PdfReader(editor.path) as reader:
            assert reader.get_fields()['person.nome']['/DV']=='/Off'
            assert reader.get_fields()['signature']['/FT']=='/Sig'
        before=editor.path
        with pytest.raises(PDFOperationError):
            editor.update_form_fields(editor.form_design(),['signature'],editor.path)
        assert editor.path==before
    finally:
        editor.close()


def test_existing_empty_choices_and_long_tooltips_are_preserved(form_pdf,tmp_path):
    writer=PdfWriter(clone_from=form_pdf)
    for ref in writer.pages[0]['/Annots']:
        obj=ref.get_object()
        if obj.get('/T')=='categoria':
            obj[NameObject('/Opt')]=ArrayObject()
        if obj.get('/T')=='nome':
            obj[NameObject('/TU')]=TextStringObject('X'*5000)
    path=tmp_path/'unusual.pdf'
    writer.write(path)
    editor=PDFEditor(path)
    try:
        rows=editor.form_design()
        next(p for p in rows if p['source']=='nome')['required']=False
        editor.update_form_fields(rows,[],editor.path)
        with PdfReader(editor.path) as reader:
            assert reader.get_fields()['nome']['/TU']=='X'*5000
            assert reader.get_fields()['categoria']['/Opt']==[]
    finally:
        editor.close()
