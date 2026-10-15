"""Widget values must survive screen, image and print rendering, without flattening saves."""
import io

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject, TextStringObject
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtPdf import QPdfDocument

from test_gui import app
from test_pdf_forms import form_pdf
from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.form_appearance import display_copy
from ape_pdf_studio.core.print_source import PrintSource
from ape_pdf_studio.ui.exports import export_page_image


@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_static_appearance_matches_native_widget_rendering(form_pdf, rotation):
    writer = PdfWriter(clone_from=form_pdf)
    page = writer.pages[0]
    page.rotate(rotation)
    page.cropbox.lower_left = (20, 30)
    page.cropbox.upper_right = (575, 812)
    widget = next(ref.get_object() for ref in page["/Annots"] if ref.get_object().get("/T") == "riferimento")
    appearance = widget["/AP"]["/N"]
    appearance[NameObject("/Matrix")] = ArrayObject(map(FloatObject, (2, 0, 0, 0.5, 17, -8)))
    data = io.BytesIO()
    writer.write(data)
    original = data.getvalue()
    rendered = display_copy(original)
    with pymupdf.open(stream=original, filetype="pdf") as first, pymupdf.open(stream=rendered, filetype="pdf") as second:
        before, after = first[0].get_pixmap(), second[0].get_pixmap()
        assert before.width == after.width and before.height == after.height
        difference = sum(abs(x-y) for x, y in zip(before.samples, after.samples))
        assert difference / len(before.samples) < 0.2
    with PdfReader(io.BytesIO(rendered)) as reader:
        assert all(ref.get_object().get("/Subtype") != "/Widget" for ref in reader.pages[0].get("/Annots", []))
        assert "/AcroForm" not in reader.root_object


def test_qtpdf_and_image_export_show_saved_values(app, form_pdf, tmp_path):
    editor = PDFEditor(form_pdf)
    document = QPdfDocument()
    buffer = QBuffer()
    try:
        editor.fill_form({"nome": "Visible in QtPdf", "contatto": "/Email", "consenso": "/Yes"}, editor.path)
        buffer.setData(QByteArray(display_copy(editor.path.read_bytes())))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        document.load(buffer)
        assert document.status() == QPdfDocument.Status.Ready
        assert "Visible in QtPdf" in document.getAllText(0).text()
        assert "APE-DEMO-001" in document.getAllText(0).text()
        image = document.render(0, QSize(595, 842))
        assert not image.isNull()
        output = tmp_path / "form.png"
        export_page_image(document, 0, output, 72)
        assert output.stat().st_size > 1000
        with PdfReader(editor.path) as reader:
            assert reader.get_fields()["nome"]["/V"] == "Visible in QtPdf"
            assert len(reader.get_fields()) == 8
    finally:
        document.close()
        buffer.close()
        editor.close()


def test_missing_text_appearance_is_generated_only_for_display(form_pdf):
    writer = PdfWriter(clone_from=form_pdf)
    widget = next(ref.get_object() for ref in writer.pages[0]["/Annots"] if ref.get_object().get("/T") == "nome")
    widget.pop("/AP")
    widget[NameObject("/V")] = TextStringObject("Existing value without appearance")
    data = io.BytesIO()
    writer.write(data)
    original = data.getvalue()
    rendered = display_copy(original)
    with pymupdf.open(stream=rendered, filetype="pdf") as document:
        assert "Existing value without appearance" in document[0].get_text()
    with PdfReader(io.BytesIO(original)) as reader:
        original_widget = next(ref.get_object() for ref in reader.pages[0]["/Annots"] if ref.get_object().get("/T") == "nome")
        assert "/AP" not in original_widget


def test_printable_noview_fields_are_only_in_print_copy(form_pdf, tmp_path):
    writer = PdfWriter(clone_from=form_pdf)
    widget = next(ref.get_object() for ref in writer.pages[0]["/Annots"] if ref.get_object().get("/T") == "riferimento")
    widget[NameObject("/F")] = NumberObject(36)
    path = tmp_path / "hidden.pdf"
    writer.write(path)
    original = path.read_bytes()
    with pymupdf.open(stream=display_copy(original), filetype="pdf") as document:
        assert "APE-DEMO-001" not in document[0].get_text()
    source = PrintSource(path)
    try:
        for comments in (True, False):
            with pymupdf.open(source.path(comments)) as document:
                assert "APE-DEMO-001" in document[0].get_text()
        assert path.read_bytes() == original
    finally:
        source.close()
