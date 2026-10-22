"""Script-bearing PDFs can open, while native engines cannot execute scripts."""
import ast
import io
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject
from PySide6.QtCore import QSize

from test_gui import app, text_pdf, window
from test_pdf_forms import form_pdf
from ape_pdf_studio.core import security
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.errors import PDFSecurityError
from ape_pdf_studio.core.form_appearance import display_copy
from ape_pdf_studio.core.pdf_runtime import disable_javascript, open_static_pdf


@pytest.fixture
def scripted_pdf(text_pdf, tmp_path):
    writer = PdfWriter(clone_from=text_pdf)
    script = writer._add_object(DictionaryObject({NameObject('/S'): NameObject('/JavaScript'),
        NameObject('/JS'): TextStringObject("app.alert('synthetic script; must never execute');")}))
    writer.root_object[NameObject('/Names')] = DictionaryObject({NameObject('/JavaScript'): DictionaryObject({
        NameObject('/Names'): ArrayObject([TextStringObject('document script'), script])})})
    action = DictionaryObject({NameObject('/S'): writer._add_object(NameObject('/JavaScript')),
        NameObject('/JS'): TextStringObject("app.launchURL('https://example.invalid/', true);")})
    writer.root_object[NameObject('/OpenAction')] = writer._add_object(action)
    writer.pages[0][NameObject('/AA')] = DictionaryObject({NameObject('/O'): writer._add_object(action)})
    writer._add_object(DictionaryObject({NameObject('/S'): NameObject('/JavaScript'),
                                      NameObject('/JS'): TextStringObject('orphan script')}))
    source = tmp_path / 'scripts.pdf'
    writer.write(source)
    return source


def test_display_copy_strips_all_scripts_but_preserves_original(scripted_pdf):
    original = scripted_pdf.read_bytes()
    report = security.check_pdf(scripted_pdf)
    assert report.findings == ('javascript',) and not report.blocked
    rendered = display_copy(original)
    with PdfReader(io.BytesIO(rendered)) as reader:
        assert 'javascript' not in security.inspect_reader(reader)
        assert 'Alpha beta PDF' in reader.pages[0].extract_text()
    assert scripted_pdf.read_bytes() == original


def test_script_content_survives_save_and_history_without_execution(scripted_pdf, tmp_path):
    original = scripted_pdf.read_bytes()
    editor = BrokerEditor(scripted_pdf)
    try:
        assert editor.security_report.findings == ('javascript',) and not editor.dirty
        editor.rotate(0, 90)
        destination = editor.save_as(tmp_path / 'saved.pdf')
        with PdfReader(destination) as reader:
            assert reader.pages[0].rotation == 90
            assert 'javascript' in security.inspect_reader(reader)
        assert editor.undo() and editor.redo()
    finally:
        editor.close()
    assert scripted_pdf.read_bytes() == original


def test_gui_opens_scripted_pdf_with_banner_and_can_render_and_inspect_text(window, scripted_pdf):
    window.open_pdf(scripted_pdf)
    tab = window.current
    assert tab.editor.source == scripted_pdf
    assert tab.notice.isVisible() and 'JavaScript' in tab.notice.text.text()
    assert 'disabilitata' in tab.notice.text.text() and tab.notice.property('securityWarning')
    assert not tab.pdf.render(0, QSize(256, 384)).isNull()
    assert 'Alpha beta PDF' in tab.pdf.getAllText(0).text()
    assert tab.editor.text_boxes(0)


@pytest.mark.parametrize('kind', ['/Launch', '/SubmitForm', '/GoToR', '/UnknownAction'])
def test_javascript_does_not_hide_other_blocked_actions(scripted_pdf, tmp_path, kind):
    writer = PdfWriter(clone_from=scripted_pdf)
    writer.root_object['/OpenAction'][NameObject('/Next')] = writer._add_object(
        DictionaryObject({NameObject('/S'): NameObject(kind)}))
    source = tmp_path / 'combined.pdf'
    writer.write(source)
    with pytest.raises(PDFSecurityError):
        BrokerEditor(source)


def test_native_guard_disables_an_already_enabled_runtime():
    with pymupdf.open() as document:
        native = pymupdf.mupdf.pdf_document_from_fz_document(document.this)
        pymupdf.mupdf.pdf_enable_js(native)
        disable_javascript(document)
        assert not pymupdf.mupdf.pdf_js_supported(native)


def test_static_document_opener_has_no_javascript_runtime(scripted_pdf):
    with open_static_pdf(scripted_pdf) as document:
        native = pymupdf.mupdf.pdf_document_from_fz_document(document.this)
        assert not pymupdf.mupdf.pdf_js_supported(native)
        assert 'Alpha beta PDF' in document[0].get_text()


def test_static_document_opener_closes_document_if_guard_fails(monkeypatch):
    document = pymupdf.open()
    monkeypatch.setattr(pymupdf, 'open', lambda *a, **kw: document)
    def fail(*args):
        raise RuntimeError('Synthetic native failure')
    monkeypatch.setattr('ape_pdf_studio.core.pdf_runtime.disable_javascript', fail)
    with pytest.raises(RuntimeError, match='native failure'):
        open_static_pdf()
    assert document.is_closed


def test_renderer_copy_refuses_incomplete_script_scrubbing(scripted_pdf, monkeypatch):
    monkeypatch.setattr(security, 'MAX_NODES', 1)
    with pytest.raises(PDFSecurityError):
        display_copy(scripted_pdf.read_bytes())


def test_scripted_form_still_accepts_manual_values_without_calculation(form_pdf, tmp_path):
    writer = PdfWriter(clone_from=form_pdf)
    field = next(ref.get_object() for ref in writer.pages[0]['/Annots'] if ref.get_object().get('/T') == 'nome')
    script = writer._add_object(DictionaryObject({NameObject('/S'): NameObject('/JavaScript'),
        NameObject('/JS'): TextStringObject("event.value = 'unexpected automatic calculation';")}))
    field[NameObject('/AA')] = DictionaryObject({NameObject('/C'): script})
    source = tmp_path / 'scripted-form.pdf'
    writer.write(source)
    original = source.read_bytes()
    editor = BrokerEditor(source)
    try:
        editor.fill_form({'nome': 'Valore manuale'}, editor.path)
        with PdfReader(editor.path) as reader:
            assert reader.get_fields()['nome']['/V'] == 'Valore manuale'
            assert 'javascript' in security.inspect_reader(reader)
        with PdfReader(io.BytesIO(display_copy(editor.path.read_bytes()))) as rendered:
            assert 'javascript' not in security.inspect_reader(rendered)
            assert 'Valore manuale' in rendered.pages[0].extract_text()
    finally:
        editor.close()
    assert source.read_bytes() == original


def test_application_native_openers_cannot_bypass_static_runtime():
    root = Path(__file__).resolve().parents[1] / 'src/ape_pdf_studio'
    for path in root.rglob('*.py'):
        if path.name == 'pdf_runtime.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == 'open_static_pdf' for node in ast.walk(tree)):
            assert any(isinstance(node, ast.ImportFrom) and any(alias.name == 'open_static_pdf' for alias in node.names)
                       for node in ast.walk(tree)), path
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                assert not (isinstance(owner, ast.Name) and owner.id in ('pymupdf', 'fitz')
                            and node.func.attr in ('open', 'Document')), path
