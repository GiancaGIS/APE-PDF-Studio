"""Local links must not prevent reading or become executable viewer actions."""
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from PySide6.QtCore import QSize
from PySide6.QtGui import QDesktopServices

from test_gui import app, text_pdf, window
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.errors import PDFSecurityError
from ape_pdf_studio.core.form_appearance import display_copy
from ape_pdf_studio.core.print_source import PrintSource
from ape_pdf_studio.core.security import check_pdf, inspect_reader


def uri_action(uri):
    return DictionaryObject({NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject(uri)})


@pytest.fixture
def local_link_pdf(text_pdf, tmp_path):
    writer = PdfWriter(clone_from=text_pdf)
    annotations = ArrayObject()
    for index, uri in enumerate(('file:///C:/project/LICENSE', '../docs/manual.pdf', 'https://example.com/')):
        annotations.append(writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
            NameObject('/Subtype'): NameObject('/Link'), NameObject('/F'): NumberObject(4),
            NameObject('/Rect'): ArrayObject([NumberObject(v) for v in (20, 100+index*25, 200, 120+index*25)]),
            NameObject('/A'): writer._add_object(uri_action(uri))})))
    writer.pages[0][NameObject('/Annots')] = annotations
    # Automatic actions must be inert as well, including indirect action names.
    automatic = uri_action('file:///C:/project/README.md')
    automatic[NameObject('/S')] = writer._add_object(NameObject('/URI'))
    writer.root_object[NameObject('/OpenAction')] = writer._add_object(automatic)
    path = tmp_path/'local-links.pdf'
    writer.write(path)
    return path


def test_local_links_open_and_save_without_becoming_clickable(local_link_pdf, tmp_path):
    original = local_link_pdf.read_bytes()
    editor = BrokerEditor(local_link_pdf)
    try:
        assert editor.security_report.findings == ('external_links', 'local_links')
        assert not editor.security_report.blocked
        assert [row['url'] for rows in editor.interactions()[0].values() for row in rows] == ['https://example.com/']
        editor.rotate(0, 90)
        destination = editor.save_as(tmp_path/'saved.pdf')
        assert 'local_links' in check_pdf(destination).findings
        with PdfReader(destination) as reader:
            assert reader.pages[0]['/Annots'][0].get_object()['/A']['/URI'] == 'file:///C:/project/LICENSE'
        assert editor.undo() and editor.redo()
    finally:
        editor.close()
    assert local_link_pdf.read_bytes() == original


def test_display_and_print_copies_remove_local_actions_preserve_web_links(local_link_pdf):
    original = local_link_pdf.read_bytes()
    with PdfReader(io.BytesIO(display_copy(original))) as reader:
        assert inspect_reader(reader) == ('external_links',)
        assert not reader.root_object['/OpenAction']
        assert 'Alpha beta PDF' in reader.pages[0].extract_text()
    source = PrintSource(local_link_pdf)
    try:
        with PdfReader(source.path(True)) as reader:
            assert 'local_links' not in inspect_reader(reader)
            assert reader.pages[0]['/Annots'][2].get_object()['/A']['/URI'] == 'https://example.com/'
    finally:
        source.close()
    assert local_link_pdf.read_bytes() == original


@pytest.mark.parametrize('kind', ['/Launch', '/GoToR', '/SubmitForm', '/UnknownAction'])
def test_local_link_does_not_hide_blocked_action_chains(local_link_pdf, tmp_path, kind):
    writer = PdfWriter(clone_from=local_link_pdf)
    writer.pages[0]['/Annots'][0].get_object()['/A'][NameObject('/Next')] = writer._add_object(
        DictionaryObject({NameObject('/S'): NameObject(kind)}))
    path = tmp_path/'chained.pdf'
    writer.write(path)
    with pytest.raises(PDFSecurityError):
        BrokerEditor(path)


def test_gui_reads_local_link_pdf_with_warning_but_never_opens_local_files(window, local_link_pdf, monkeypatch):
    window.open_pdf(local_link_pdf)
    tab = window.current
    assert tab.editor.source == local_link_pdf
    assert tab.notice.isVisible() and 'file locali o relativi' in tab.notice.text.text()
    assert not tab.pdf.render(0, QSize(256, 384)).isNull()
    assert 'Alpha beta PDF' in tab.pdf.getAllText(0).text()
    tab.links_enabled = True
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda *_: pytest.fail('Local URL reached OS'))
    monkeypatch.setattr(window, '_confirm_pdf_content', lambda *_: pytest.fail('Local URL offered for approval'))
    for uri in ('file:///C:/project/LICENSE', '../docs/manual.pdf'):
        window.open_pdf_link(tab, uri)
