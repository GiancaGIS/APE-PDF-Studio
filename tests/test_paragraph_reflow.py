"""Real paragraph reconstruction, wrapping, style retention and safe overflow."""
from dataclasses import replace

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject

from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core import rpc_codec


@pytest.fixture
def paragraph_pdf(tmp_path):
    source = tmp_path / 'paragraphs.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=600)
        page.insert_text((48, 40), 'Header stays', fontsize=14, fontname='hebo')
        page.draw_rect((40, 72, 255, 300), color=None, fill=(.9, .95, 1))
        text = ('Alpha paragraph has several words on each line. '
                'These lines belong to the same paragraph and can flow automatically. '
                'The original text remains editable.')
        writer = pymupdf.TextWriter(page.rect)
        writer.fill_textbox((48, 80, 248, 210), text, fontsize=12, font=pymupdf.Font('helv'), warn=False)
        writer.write_text(page)
        page.insert_text((280, 100), 'Right column', fontsize=10)
        page.insert_text((280, 116), 'must stay here', fontsize=10)
        page.insert_text((48, 350), 'Next paragraph stays', fontsize=12)
        page.draw_rect((48, 390, 248, 430), color=(0, 0, 1), fill=(.7, .8, .9))
        document.set_metadata({'title': 'Paragraph example'})
        document.new_page().insert_text((48, 100), 'Second page stays')
        document.save(source)
    return source


def test_recognition_and_real_sandbox_records(paragraph_pdf):
    editor = BrokerEditor(paragraph_pdf)
    try:
        target = editor.paragraph_at(0, (60, 90))
        assert 'Alpha paragraph' in target.text and 'remain' in target.text
        assert len(target.members) >= 3
        assert 'Next paragraph' not in target.text and 'Right column' not in target.text
        assert target.layout_bbox[3] <= 300
        assert rpc_codec.decode(rpc_codec.encode(target)) == target
        assert target.display_bbox in editor.paragraph_boxes(0)
        info = editor.paragraph_info(target, target.text, target.size, target.alignment)
        assert info['fits'] and info['lines'] >= 3
    finally:
        editor.close()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('unit', [1, 2])
def test_reflow_saves_wraps_and_preserves_other_content(paragraph_pdf, tmp_path, rotation, unit):
    writer = PdfWriter(clone_from=paragraph_pdf)
    writer.pages[0].cropbox = RectangleObject((10, 20, 390, 580))
    writer.pages[0].rotate(rotation)
    writer.pages[0][NameObject('/UserUnit')] = NumberObject(unit)
    writer.add_attachment('keep.txt', b'unchanged attachment')
    writer.add_outline_item('Second page', 1)
    writer.write(paragraph_pdf)
    original = paragraph_pdf.read_bytes()
    editor = PDFEditor(paragraph_pdf)
    try:
        with pymupdf.open(editor.path) as document:
            page = document[0]
            span = next(span for block in page.get_text('dict')['blocks'] for line in block.get('lines', [])
                        for span in line['spans'] if 'Alpha paragraph' in span['text'])
            point = (pymupdf.Rect(span['bbox']).tl + (2, 2)) * page.rotation_matrix
            drawings = page.get_drawings()
        target = editor.paragraph_at(0, (point.x, point.y))
        text = 'Updated paragraph ' + 'with automatic word wrapping and more useful text. ' * 5
        editor.replace_paragraph(target, text)
        with pymupdf.open(editor.path) as document:
            content = document[0].get_text()
            assert 'Updated paragraph' in content
            assert 'Alpha paragraph' not in content
            assert 'Next paragraph stays' in content and 'Right column' in content
            without_sequence = lambda rows: [{key: value for key, value in row.items() if key != 'seqno'} for row in rows]
            assert without_sequence(document[0].get_drawings()) == without_sequence(drawings)
            assert 'Second page stays' in document[1].get_text()
            lines = [line for block in document[0].get_text('dict')['blocks'] for line in block.get('lines', [])
                     if any('Updated' in span['text'] or 'automatic' in span['text'] for span in line['spans'])]
            assert len(lines) >= 2
        output = editor.save_as(tmp_path / 'reflowed.pdf')
        with PdfReader(output) as reader:
            assert reader.metadata.title == 'Paragraph example'
            assert reader.attachments['keep.txt'] == [b'unchanged attachment']
            assert reader.outline[0].title == 'Second page'
        editor.undo()
        assert editor.path.read_bytes() == original
        editor.redo()
        assert 'Updated' in PdfReader(editor.path).pages[0].extract_text()
        assert paragraph_pdf.read_bytes() == original
    finally:
        editor.close()


def test_overflow_invalid_and_forged_layout_do_not_commit(paragraph_pdf):
    editor = PDFEditor(paragraph_pdf)
    try:
        target = editor.paragraph_at(0, (60, 90))
        revision = editor.path
        for text in ('Too much text ' * 1000, '', 'invalid\x00text'):
            with pytest.raises(PDFOperationError):
                editor.replace_paragraph(target, text)
            assert editor.path == revision and not editor.dirty
        info = editor.paragraph_info(target, 'Too much text ' * 1000, 12, 0)
        assert not info['fits'] and info['message']
        forged = replace(target, layout_bbox=(0, 0, 400, 600))
        with pytest.raises(PDFOperationError):
            editor.replace_paragraph(forged, 'Attempted escape')
        assert editor.path == revision
    finally:
        editor.close()


def test_explicit_newlines_and_long_words(paragraph_pdf):
    editor = PDFEditor(paragraph_pdf)
    try:
        target = editor.paragraph_at(0, (60, 90))
        text = 'First explicit line\n\nSecond section ' + 'a' * 90
        editor.replace_paragraph(target, text)
        content = PdfReader(editor.path).pages[0].extract_text()
        assert 'First explicit line' in content and 'Second section' in content
        assert 'a' * 90 in content.replace('\n', '').replace(' ', '')
    finally:
        editor.close()


def test_mixed_inline_styles_survive_reflow(tmp_path):
    source = tmp_path / 'styled.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=600)
        x, y = 48, 100
        for text, font, color in [('Ordinary ', 'helv', (0, 0, 0)), ('important', 'hebo', (1, 0, 0)),
                                  (' words on a line', 'helv', (0, 0, 0))]:
            page.insert_text((x, y), text, fontname=font, fontsize=12, color=color)
            x += pymupdf.Font(font).text_length(text, fontsize=12)
        page.insert_text((48, 115), 'followed by a second line of the paragraph.', fontsize=12)
        document.save(source)
    editor = PDFEditor(source)
    try:
        target = editor.paragraph_at(0, (60, 95))
        assert len(target.members) >= 4
        editor.replace_paragraph(target, target.text + ' More words for wrapping.')
        with pymupdf.open(editor.path) as document:
            spans = [span for block in document[0].get_text('dict')['blocks'] for line in block.get('lines', [])
                     for span in line['spans'] if 'important' in span['text']]
            assert spans and spans[0]['flags'] & 16 and spans[0]['color'] == 0xff0000
        again = editor.paragraph_at(0, (60, 95))
        assert again.text == target.text + ' More words for wrapping.'
        editor.replace_paragraph(again, again.text + ' Again.')
    finally:
        editor.close()


@pytest.mark.parametrize('alignment', [0, 1, 2, 3])
def test_alignment_and_column_boundaries(paragraph_pdf, alignment):
    editor = PDFEditor(paragraph_pdf)
    try:
        target = editor.paragraph_at(0, (60, 90))
        editor.replace_paragraph(target, 'Flow words ' * 15, alignment=alignment)
        with pymupdf.open(editor.path) as document:
            spans = [span for block in document[0].get_text('dict')['blocks'] for line in block.get('lines', [])
                     for span in line['spans'] if 70 < span['bbox'][1] < 300 and span['bbox'][0] < 255]
            assert spans
            assert all(span['bbox'][0] >= target.layout_bbox[0] - .1 and span['bbox'][2] <= target.layout_bbox[2] + .1 for span in spans)
            if alignment == 2:
                assert abs(max(span['bbox'][2] for span in spans) - target.layout_bbox[2]) < .1
    finally:
        editor.close()


def test_paragraph_ipc_rejects_inconsistent_members(paragraph_pdf):
    editor = PDFEditor(paragraph_pdf)
    try:
        target = editor.paragraph_at(0, (60, 90))
        for forged in (replace(target, members=target.members * 2),
                       replace(target, members=(replace(target.members[0], page=1),))):
            with pytest.raises(ValueError):
                rpc_codec.decode(rpc_codec.encode(forged))
    finally:
        editor.close()
