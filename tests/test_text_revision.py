"""Synthetic coverage for the worker's content guard and layout contract."""
from hashlib import sha256

import pymupdf
import pytest
from pypdf import PdfReader

from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.rpc_validation import result


@pytest.mark.parametrize('case, reason', [
    ('overpaint', 'ordine di visualizzazione'),
    ('adjacent', 'sovrappone'),
    ('layer', 'livelli PDF'),
    ('tagged', 'tag strutturali'),
    ('quad', 'marcatura'),
    ('clip', 'ritaglio grafico'),
])
def test_ambiguous_replacement_preserves_original_and_history(tmp_path, case, reason):
    path = tmp_path / 'protected.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=360, height=500)
        layer = pdf.add_ocg('Layer') if case == 'layer' else 0
        page.insert_text((48, 84), 'Original target', fontsize=11, oc=layer)
        if case == 'overpaint':
            page.draw_rect((46, 67, 180, 90), color=None, fill=(1, 1, 1))
        elif case == 'adjacent':
            page.insert_text((48, 94), 'Nearby', fontsize=11)
        elif case == 'quad':
            page.add_highlight_annot(page.search_for('Original'))
        elif case == 'tagged':
            tree = pdf.get_new_xref()
            pdf.update_object(tree, '<< /Type /StructTreeRoot /K [] >>')
            pdf.xref_set_key(pdf.pdf_catalog(), 'StructTreeRoot', f'{tree} 0 R')
        elif case == 'clip':
            body, = page.get_contents()
            pdf.update_stream(body, b'q 70 400 120 35 re W n\n' + pdf.xref_stream(body) + b'\nQ')
        pdf.save(path)
    with pymupdf.open(path) as pdf:
        before = pdf[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).samples
    original = sha256(path.read_bytes()).digest()
    editor = PDFEditor(path)
    try:
        chosen = editor.text_at(0, (80, 82))
        revision = editor.path
        with pytest.raises(PDFOperationError, match=reason):
            editor.replace_text(chosen, 'Visible')
        assert editor.path == revision and not editor.can_undo
        assert sha256(path.read_bytes()).digest() == original
        with pymupdf.open(revision) as pdf:
            assert pdf[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).samples == before
            assert 'target' in pdf[0].get_text()
    finally:
        editor.close()


def test_direct_edit_of_invisible_ocr_is_unavailable(tmp_path):
    path = tmp_path / 'ocr-layer.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((48, 84), 'Invisible OCR', render_mode=3)
        pdf.save(path)
    editor = PDFEditor(path)
    try:
        assert not editor.text_boxes(0) and not editor.paragraph_boxes(0)
        with pytest.raises(PDFOperationError):
            editor.text_at(0, (60, 82))
    finally:
        editor.close()


def test_repeated_form_keeps_the_unselected_instance_and_page(tmp_path):
    path = tmp_path / 'repeated-form.pdf'
    with pymupdf.open() as pdf, pymupdf.open() as form:
        form.new_page(width=160, height=50).insert_text((10, 25), 'Reusable words')
        page = pdf.new_page(width=400, height=500)
        page.show_pdf_page(pymupdf.Rect(10, 10, 170, 60), form, 0)
        page.show_pdf_page(pymupdf.Rect(10, 100, 170, 150), form, 0)
        pdf.new_page(width=400, height=500).show_pdf_page(pymupdf.Rect(10, 10, 170, 60), form, 0)
        pdf.save(path)
        unchanged = pdf[1].get_pixmap().samples
        second_form = pdf[0].get_pixmap(clip=(0, 90, 390, 160)).samples
    editor = PDFEditor(path)
    try:
        target = editor.text_at(0, (27, 32))
        editor.replace_text(target, 'Edited words')
        with pymupdf.open(editor.path) as pdf:
            assert pdf[0].get_text().count('Reusable words') == 1
            assert 'Edited words' in pdf[0].get_text()
            assert pdf[0].get_pixmap(clip=(0, 90, 390, 160)).samples == second_form
            assert pdf[1].get_pixmap().samples == unchanged
    finally:
        editor.close()


def test_preview_commit_unicode_and_reedit_through_broker(tmp_path):
    path = tmp_path / 'unicode.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=360, height=500)
        source = pymupdf.TextWriter(page.rect)
        font = pymupdf.Font('cjk')
        source.append((48, 80), '天地玄黃宇', font=font, fontsize=12)
        source.append((48, 96), '宙洪荒日月', font=font, fontsize=12)
        source.write_text(page)
        pdf.save(path)
    editor = BrokerEditor(path)
    try:
        target = editor.paragraph_at(0, (52, 80))
        assert target.text == '天地玄黃宇宙洪荒日月'
        text = '甲乙丙丁戊、春夏秋冬。\nCafe\u0301'
        info, pixels = editor.preview_text(target, text, 12., 0, raster=True)
        assert info['fits'], info['message']
        assert info['raster'] and len(pixels) == info['raster'][1] * info['raster'][2] * 3
        assert all(not run[0].startswith(('、', '。')) for run in info['runs'])
        revision = editor.path
        editor.replace_paragraph(target, text)
        assert revision != editor.path
        with pymupdf.open(editor.path) as pdf:
            spans = [span for block in pdf[0].get_text('dict')['blocks'] for line in block['lines']
                     for span in line['spans']]
            for run_text, origin, *_ in info['runs']:
                assert any(span['text'] == run_text and span['origin'] == pytest.approx(origin, abs=.1) for span in spans)
            assert 'Café' in pdf[0].get_text()
            box, width, height = info['raster']
            scale = width / (box[2] - box[0])
            raster = pdf[0].get_pixmap(clip=box, matrix=pymupdf.Matrix(scale, scale))
            assert (raster.width, raster.height) == (width, height)
            error = sum(abs(a - b) for a, b in zip(raster.samples, pixels)) / len(pixels)
            assert error <= .25
        assert 'Café' in PdfReader(editor.path).pages[0].extract_text()
        assert 'Café' in editor.call('qt_all_text', editor.path, 0)['text']
        target = editor.paragraph_at(0, (53, 80))
        editor.replace_paragraph(target, '第二次修改')
        assert '第二次修改' in PdfReader(editor.path).pages[0].extract_text()
        assert not list(editor._root.glob('*.rgba'))
    finally:
        editor.close()


def test_unsupported_rtl_returns_specific_feedback(tmp_path):
    path = tmp_path / 'latin.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((48, 84), 'Editable text')
        pdf.save(path)
    editor = PDFEditor(path)
    try:
        target = editor.paragraph_at(0, (60, 80))
        revision = editor.path
        info = editor.paragraph_info(target, 'مرحبا بالعالم', target.size, target.alignment)
        assert not info['fits'] and 'composizione tipografica' in info['message']
        assert editor.path == revision and not editor.can_undo
    finally:
        editor.close()


def test_insert_format_resize_and_delete_are_undoable(tmp_path):
    path = tmp_path / 'insert.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=300, height=350)
        page.insert_text((44, 90), 'Existing line')
        pdf.save(path)
    editor = BrokerEditor(path)
    try:
        with pytest.raises(PDFOperationError, match='spazio libero'):
            editor.new_paragraph_at(0, (50., 75.))
        target = editor.new_paragraph_at(0, (44., 120.))
        formatting = ('tiro', 0x003399, True, True, 160.)
        preview, _ = editor.preview_text(target, 'New block', 14., 0, formatting)
        assert preview['fits'] and preview['runs'][0][3] == 0x003399
        assert 'BoldItalic' in preview['runs'][0][4]
        assert preview['bbox'][2] - preview['bbox'][0] == pytest.approx(160)
        editor.replace_paragraph(target, 'New block', 14., 0, formatting)
        inserted = editor.paragraph_at(0, (50., 132.))
        assert inserted.text == 'New block'
        editor.delete_text(inserted)
        assert PdfReader(editor.path).pages[0].extract_text().strip() == 'Existing line'
        editor.undo()
        assert 'New block' in PdfReader(editor.path).pages[0].extract_text()
        editor.redo()
        assert 'New block' not in PdfReader(editor.path).pages[0].extract_text()
    finally:
        editor.close()


def test_edit_preserves_pixels_outside_the_target_in_both_renderers(tmp_path):
    from PySide6.QtCore import QSize
    from ape_pdf_studio.ui.remote_pdf import RemoteDocument

    path = tmp_path / 'appearance.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=300, height=260)
        page.draw_rect((30, 55, 200, 105), color=None, fill=(.8, .94, 1))
        page.insert_text((48, 84), 'Original line to replace', fontsize=13)
        page.insert_text((48, 125), 'Neighbour stays', fontsize=13)
        page.draw_rect((210, 140, 260, 176), fill=(1, .8, .6))
        pdf.save(path)
    editor = BrokerEditor(path)
    try:
        remote = RemoteDocument(editor)
        target = editor.text_at(0, (60, 80))

        def pdfium_pixels(scale):
            image = remote.render(0, QSize(300 * scale, 260 * scale))
            return bytes(image.constBits())

        def images():
            remote.load(editor.path)
            with pymupdf.open(editor.path) as pdf:
                return {(engine, scale): raster for scale in (1, 2) for engine, raster in (
                    ('mupdf', pdf[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale)).samples),
                    ('pdfium', pdfium_pixels(scale)),
                )}

        before = images()
        editor.replace_text(target, 'Edited line')
        after = images()
        for engine, scale in before:
            old, new = before[engine, scale], after[engine, scale]
            channels = 3 if engine == 'mupdf' else 4
            left, top, right, bottom = pymupdf.Rect(target.display_bbox) + (-4, -4, 4, 4)
            left, top, right, bottom = (round(coord * scale) for coord in (left, top, right, bottom))
            stride = 300 * scale * channels
            assert old != new and len(old) == len(new) == stride * 260 * scale
            for row in range(260 * scale):
                start, end = row * stride, (row + 1) * stride
                if top <= row <= bottom:
                    assert old[start:start + left * channels] == new[start:start + left * channels]
                    assert old[start + right * channels:end] == new[start + right * channels:end]
                else:
                    assert old[start:end] == new[start:end]
    finally:
        editor.close()


@pytest.mark.parametrize('raster', [
    ((0, 0, 500, 30), 1025, 30),
    ((0, 0, float('nan'), 30), 10, 30),
    ((20, 10, 15, 25), 10, 10),
])
def test_text_preview_rejects_untrusted_raster_geometry(raster):
    with pytest.raises(ValueError):
        result('text_preview', dict(bbox=(0, 0, 500, 30), lines=1, font='Test', fallback=False,
                                    fits=True, message='', runs=[], raster=raster))
