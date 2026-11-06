"""Partial Ink edits preserve the PDF, annotation identity, metadata and history."""
import math
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject, TextStringObject

from test_gui import text_pdf
from ape_pdf_studio.core.editor import PDFEditor, PDFOperationError
from ape_pdf_studio.core.ink_geometry import cut_paths
from ape_pdf_studio.core.form_appearance import display_copy
from ape_pdf_studio.core.rpc_validation import result


def line(page=0, y=180):
    return dict(page=page, points=[(80, y), (280, y)], width=6, opacity=.7,
                color=[1, 0, 0], smooth=False)


@pytest.mark.parametrize('rotation', (0, 90, 180, 270))
def test_partial_erasure_roundtrips_crop_rotation_userunit_and_identity(text_pdf, tmp_path, rotation):
    writer = PdfWriter(clone_from=text_pdf)
    page = writer.pages[0]
    page.cropbox = RectangleObject((20, 30, 380, 550))
    page[NameObject('/UserUnit')] = NumberObject(2)
    page.rotate(rotation)
    source = tmp_path / 'geometry.pdf'
    writer.write(source)
    original = source.read_bytes()
    editor = PDFEditor(source)
    try:
        editor.update_ink([line(), line(y=260)], [], str(editor.path))
        initial = editor.path
        row, neighbor = editor.ink()['rows']
        paths = cut_paths(row['paths'], (180, 160), (180, 200), 5, row['width'], row['smooth'])
        assert len(paths) == 2
        with PdfReader(initial) as reader:
            annotation = reader.pages[0]['/Annots'][row['index']].get_object()
            name = annotation['/NM']
        editor.update_ink([], [], str(initial), [dict(page=0, index=row['index'], paths=paths, smooth=False)])
        saved = editor.save_as(tmp_path / 'erased.pdf')
        data = editor.ink()
        result('ink', data)
        assert len(data['rows']) == 2 and len(data['rows'][0]['paths']) == 2
        assert data['rows'][1] == neighbor
        with PdfReader(saved) as reader:
            annotation = reader.pages[0]['/Annots'][row['index']].get_object()
            assert annotation['/NM'] == name
            assert len(annotation['/InkList']) == 2 and not annotation['/APEInkSmoothing'].value
            assert reader.pages[0]['/UserUnit'] == 2
        with pymupdf.open(source) as background, pymupdf.open(saved) as changed:
            base = background[0].get_pixmap(alpha=False)
            image = changed[0].get_pixmap(alpha=False)
            assert image.pixel(180, 180) == base.pixel(180, 180)
            assert image.pixel(100, 180)[0] > image.pixel(100, 180)[1] + 30
            assert image.pixel(260, 180)[0] > image.pixel(260, 180)[1] + 30
        assert editor.undo() and editor.path == initial
        assert len(editor.ink()['rows'][0]['paths']) == 1
        assert editor.redo() and len(editor.ink()['rows'][0]['paths']) == 2
    finally:
        editor.close()
    assert source.read_bytes() == original


def test_other_viewer_ink_keeps_title_comment_and_linear_paths(tmp_path):
    source = tmp_path / 'external.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        annotation = page.add_ink_annot([[(50, 150), (350, 150)], [(50, 250), (350, 250)]])
        annotation.set_info(title='Other viewer', content='Keep this annotation comment')
        annotation.set_border(width=4)
        annotation.update()
        document.save(source)
    editor = PDFEditor(source)
    try:
        row = editor.ink()['rows'][0]
        assert not row['smooth']
        paths = cut_paths(row['paths'], (200, 130), (200, 170), 5, row['width'], False)
        editor.update_ink([], [], str(editor.path), [dict(page=0, index=row['index'], paths=paths, smooth=False)])
        assert len(editor.ink()['rows'][0]['paths']) == 3
        with pymupdf.open(editor.path) as document:
            annotation = next(document[0].annots())
            assert annotation.info['title'] == 'Other viewer'
            assert annotation.info['content'] == 'Keep this annotation comment'
    finally:
        editor.close()


def test_editing_display_hides_ink_only_and_does_not_touch_original(text_pdf):
    editor = PDFEditor(text_pdf)
    try:
        editor.add_note(0, 'Keep note', (300, 200))
        editor.update_ink([line()], [], str(editor.path))
        original = editor.path.read_bytes()
        with PdfReader(__import__('io').BytesIO(display_copy(original, omit_editable_ink=True))) as reader:
            kinds = [ref.get_object()['/Subtype'] for ref in reader.pages[0]['/Annots']]
            assert '/Ink' not in kinds and '/Text' in kinds
            assert 'Alpha beta' in reader.pages[0].extract_text()
        assert editor.path.read_bytes() == original
        with PdfReader(__import__('io').BytesIO(display_copy(original))) as reader:
            assert '/Ink' in [ref.get_object()['/Subtype'] for ref in reader.pages[0]['/Annots']]
    finally:
        editor.close()


@pytest.mark.parametrize('change', (
    {'paths': [[(math.nan, 10)]]}, {'paths': []}, {'paths': [[(1e20, 0)]]},
    {'smooth': 'false'}, {'page': True}, {'index': 999}, {'extra': 'unexpected'},
))
def test_invalid_partial_edits_never_create_revisions(text_pdf, change):
    editor = PDFEditor(text_pdf)
    try:
        editor.update_ink([line()], [], str(editor.path))
        before = editor.path
        row = dict(page=0, index=0, paths=[[(80, 180), (140, 180)]], smooth=False)
        row.update(change)
        with pytest.raises(PDFOperationError):
            editor.update_ink([], [], str(before), [row])
        assert editor.path == before
        with pytest.raises(PDFOperationError):
            editor.update_ink([], [[0, 0]], str(before), [dict(page=0, index=0, paths=[[(80, 180)]], smooth=False)])
        assert editor.path == before
    finally:
        editor.close()
