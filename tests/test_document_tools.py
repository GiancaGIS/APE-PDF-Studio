"""Blank pages, all-page watermarks and persistent, removable Ink annotations."""
import math

import pytest
import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject

from test_gui import text_pdf
from test_pdf_forms import form_pdf
from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError
from ape_pdf_studio.core.page_coordinates import displayed_size
from ape_pdf_studio.core.rpc_validation import result


def stroke(page=0):
    return dict(page=page, points=[(80, 160), (110, 190), (150, 165), (190, 185)],
                color=[1, 0, 0], width=8, opacity=.6)


def colored_bounds(pix, channel):
    data = pix.samples
    points = []
    for y in range(pix.height):
        for x in range(pix.width):
            i = y * pix.stride + x * pix.n
            if all(data[i+channel] > data[i+c] + 20 for c in (0, 1, 2) if c != channel):
                points.append((x, y))
    assert points, 'No colored content was rendered'
    return min(x for x,y in points), min(y for x,y in points), max(x for x,y in points), max(y for x,y in points)


@pytest.mark.parametrize('index', [0, 1, 3])
def test_blank_insertion_preserves_content_outlines_metadata_and_history(text_pdf, index):
    editor = PDFEditor(text_pdf)
    original = editor.path
    try:
        editor.insert_blank(index, 0, 'same')
        with PdfReader(editor.path) as reader:
            assert len(reader.pages) == 4
            assert not reader.pages[index].extract_text()
            assert displayed_size(reader.pages[index]) == (400, 600)
            assert [p.extract_text() for i, p in enumerate(reader.pages) if i != index] == [
                p.extract_text() for p in PdfReader(original).pages]
            assert reader.outline[0]['/Title'] == 'First page'
            assert dict(reader.metadata) == dict(PdfReader(original).metadata)
        assert editor.undo() and editor.path == original
        assert editor.redo()
    finally:
        editor.close()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_crop_rotation_userunit_watermark_and_ink_roundtrip(text_pdf, tmp_path, rotation):
    with PdfReader(text_pdf) as reader:
        writer = PdfWriter(clone_from=reader)
        page = writer.pages[0]
        page.cropbox = RectangleObject((20, 30, 380, 550))
        page.rotate(rotation)
        page[NameObject('/UserUnit')] = NumberObject(2)
        writer.write(tmp_path / 'rotated.pdf')
    editor = PDFEditor(tmp_path / 'rotated.pdf')
    try:
        editor.insert_blank(1, 0)
        with PdfReader(editor.path) as reader:
            assert displayed_size(reader.pages[1]) == displayed_size(reader.pages[0])
        editor.add_watermark('RISERVATO', 48, [0, 0, 1], .25, 45)
        with pymupdf.open(editor.path) as doc:
            assert all('RISERVATO' in p.get_text() for p in doc)
            page = doc[0]
            before = page.get_pixmap(alpha=False)
            left, top, right, bottom = colored_bounds(before, 2)
            assert abs((left+right)/2 - before.width/2) < 30
            assert abs((top+bottom)/2 - before.height/2) < 30
        editor.update_ink([stroke()], [], str(editor.path))
        records = editor.ink()
        result('ink', records)
        assert not records['truncated']
        row = records['rows'][0]
        for a, b in zip(row['paths'][0], stroke()['points']):
            assert a == pytest.approx(b)
        assert row['width'] == pytest.approx(8)
        with pymupdf.open(editor.path) as doc:
            after = doc[0].get_pixmap(alpha=False)
            assert after.samples != before.samples
            left, top, right, bottom = colored_bounds(after, 0)
            assert (left, top, right, bottom) == pytest.approx((76, 156, 194, 194), abs=10)
        output = tmp_path / 'saved.pdf'
        editor.save_as(output)
        reopened = PDFEditor(output)
        try:
            persisted = reopened.ink()['rows'][0]
            reopened.update_ink([], [[persisted['page'], persisted['index']]], str(reopened.path))
            assert not reopened.ink()['rows']
            with pymupdf.open(reopened.path) as doc:
                assert doc[0].get_pixmap(alpha=False).samples == before.samples
            assert reopened.undo()
            assert len(reopened.ink()['rows']) == 1
        finally:
            reopened.close()
    finally:
        editor.close()


def test_invalid_inputs_do_not_create_revisions(text_pdf):
    editor = PDFEditor(text_pdf)
    try:
        revision = editor.path
        for args in ((True, 0, 'same'), (-1, 0, 'same'), (1, 9, 'same'), (1, 0, 'letter')):
            with pytest.raises(PDFOperationError):
                editor.insert_blank(*args)
        for args in (('', 48, [0,0,0], .2, 45), ('a', 48, [0,0,0], math.nan, 45)):
            with pytest.raises(PDFOperationError):
                editor.add_watermark(*args)
        for key, value in (('page', True), ('points', [(math.nan, 0)]), ('width', 100), ('opacity', 0)):
            row = stroke()
            row[key] = value
            with pytest.raises(PDFOperationError):
                editor.update_ink([row], [], str(revision))
        with pytest.raises(PDFOperationError):
            editor.update_ink([stroke()], [], 'stale.pdf')
        with pytest.raises(PDFOperationError):
            editor.update_ink([], [[0, 100]], str(revision))
        assert editor.path == revision and not editor.dirty
    finally:
        editor.close()


def test_rpc_rejects_untrusted_ink_records():
    with pytest.raises(ValueError):
        result('ink', {'rows': [dict(page=0, index=0, paths=[[(math.inf, 0)]],
                                    width=2, opacity=1, color=[1, 0, 0])], 'truncated': False})


def test_new_tools_preserve_filled_forms_and_shift_widgets(form_pdf):
    editor = PDFEditor(form_pdf)
    try:
        editor.fill_form({'nome': 'Giulia Bianchi'}, editor.path)
        fields_before = editor.interactions()[1]
        editor.insert_blank(0, 0, 'a4')
        editor.add_watermark('COPIA', 40, [.2, .3, .4], .2, 0)
        editor.update_ink([stroke(1)], [], str(editor.path))
        fields_after = editor.interactions()[1]
        assert {f.name: f.value for f in fields_after} == {f.name: f.value for f in fields_before}
        for before, after in zip(fields_before, fields_after):
            assert after.widgets == tuple((p+1, box) for p, box in before.widgets)
        editor.fill_form({'email': 'giulia@example.com'}, editor.path)
        assert len(editor.ink()['rows']) == 1
        assert {f.name: f.value for f in editor.interactions()[1]}['nome'] == 'Giulia Bianchi'
    finally:
        editor.close()


def test_complex_ink_read_stays_within_ipc_budget(text_pdf):
    import io
    from ape_pdf_studio.core import rpc_codec
    editor = PDFEditor(text_pdf)
    try:
        rows = []
        for _ in range(4):
            row = stroke()
            row['points'] = [(80 + (i % 100), 160 + (i % 50)) for i in range(7000)]
            rows.append(row)
        editor.update_ink(rows, [], str(editor.path))
        data = editor.ink()
        assert data['truncated'] and len(data['rows']) == 2
        decoded = rpc_codec.read(io.BytesIO(rpc_codec.dumps(data)))
        result('ink', decoded)
        editor.update_ink([], [[0, data['rows'][0]['index']]], str(editor.path))
        assert len(editor.ink()['rows']) == 2
    finally:
        editor.close()


def test_watermark_and_ink_print_flags(text_pdf):
    from ape_pdf_studio.core.print_source import PrintSource
    editor = PDFEditor(text_pdf)
    try:
        editor.add_watermark('BOZZA', 40, [.3, .3, .3], .2, -45)
        editor.update_ink([stroke()], [], str(editor.path))
        with PdfReader(editor.path) as reader:
            assert reader.pdf_header >= '%PDF-1.4'
        source = PrintSource(editor.path)
        try:
            with PdfReader(source.path(True)) as reader:
                assert reader.pages[0]['/Annots'][0].get_object()['/Subtype'] == '/Ink'
                assert reader.pages[0]['/Annots'][0].get_object()['/F'] == 4
            with PdfReader(source.path(False)) as reader:
                assert not reader.pages[0].get('/Annots')
                assert all('BOZZA' in p.extract_text() for p in reader.pages)
        finally:
            source.close()
    finally:
        editor.close()
