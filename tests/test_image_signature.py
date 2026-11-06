"""Image signatures are decoded in isolation and become persistent page content."""
import hashlib
import math
import struct
import zlib

import pytest
import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from test_gui import text_pdf
from test_pdf_forms import form_pdf
from test_document_tools import colored_bounds
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.print_source import PrintSource
from ape_pdf_studio.core.rpc_validation import result


@pytest.fixture
def signature_image(tmp_path):
    def make(extension='png'):
        image = QImage(160, 80, QImage.Format.Format_RGBA8888)
        image.fill(QColor(0, 0, 0, 0) if extension == 'png' else QColor('white'))
        painter = QPainter(image)
        painter.fillRect(QRect(20, 20, 120, 40), QColor('#0000ff'))
        painter.end()
        path = tmp_path / ('firma à ' + extension + '.' + extension)
        assert image.save(str(path))
        return path
    return make


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('extension', ['png', 'jpg'])
def test_static_signature_crop_rotation_userunit_history_and_print(text_pdf, signature_image, tmp_path, rotation, extension):
    original = tmp_path / 'cropped.pdf'
    with PdfReader(text_pdf) as reader:
        writer = PdfWriter(clone_from=reader)
        page = writer.pages[0]
        page.cropbox = RectangleObject((20, 30, 380, 550))
        page.rotate(rotation)
        page[NameObject('/UserUnit')] = NumberObject(2)
        writer.add_metadata({'/Title': 'Signature test'})
        writer.write(original)
    image = signature_image(extension)
    hashes = [hashlib.sha256(p.read_bytes()).digest() for p in (original, image)]
    editor = BrokerEditor(original)
    try:
        revision = editor.path
        asset = editor.prepare_signature(image)
        assert not editor.dirty and editor.path == revision
        assert len(asset['pixels']) == asset['width']*asset['height']*4
        assert asset['pixels'][3] == (0 if extension == 'png' else 255)
        editor.insert_signature(0, asset['asset'], [80, 180, 240, 260], str(revision))
        editor.release_signature(asset['asset'])
        assert not asset['asset'].exists()
        assert editor.dirty
        accepted = editor.path
        with PdfReader(accepted) as reader:
            assert reader.metadata.title == 'Signature test'
            assert reader.outline[0]['/Title'] == 'First page'
            assert 'Alpha beta' in reader.pages[0].extract_text()
            assert not reader.pages[0].get('/Annots')
        with pymupdf.open(accepted) as document:
            bounds = colored_bounds(document[0].get_pixmap(alpha=False), 2)
            assert bounds == pytest.approx((100, 200, 219, 239), abs=3)
        assert editor.undo() and editor.path == revision
        assert editor.redo() and editor.path == accepted
        saved = tmp_path / 'signed.pdf'
        editor.save_as(saved)
        source = PrintSource(saved)
        try:
            with pymupdf.open(source.path(False)) as printed:
                assert colored_bounds(printed[0].get_pixmap(alpha=False), 2) == pytest.approx(bounds, abs=1)
        finally:
            source.close()
        with PdfReader(saved) as reader:
            assert reader.pages[0]['/Resources']['/XObject']
    finally:
        editor.close()
    assert hashes == [hashlib.sha256(p.read_bytes()).digest() for p in (original, image)]


def test_signature_preserves_filled_forms_and_annotations(form_pdf, signature_image):
    editor = BrokerEditor(form_pdf)
    try:
        editor.fill_form({'nome': 'Firma di prova'}, str(editor.path))
        before = editor.interactions()[1]
        editor.add_note(0, 'Nota conservata', [20, 400])
        notes = editor.notes()
        asset = editor.prepare_signature(signature_image())
        editor.insert_signature(0, asset['asset'], [80, 350, 240, 430], str(editor.path))
        assert editor.notes() == notes
        assert editor.interactions()[1] == before
        editor.fill_form({'email': 'firma@example.com'}, str(editor.path))
        assert {f.name: f.value for f in editor.interactions()[1]}['nome'] == 'Firma di prova'
    finally:
        editor.close()


def test_signature_rejects_invalid_images_without_edits(text_pdf, signature_image, tmp_path):
    editor = BrokerEditor(text_pdf)
    revision = editor.path
    try:
        files = []
        for name, data in [('bad.png', b'\x89PNG\r\n\x1a\ntruncated'),
                           ('bad.jpg', b'%PDF-1.7\n'), ('vector.svg', b'<svg/>'),
                           ('large.png', b'x'*(16*1024*1024+1))]:
            path = tmp_path / name
            path.write_bytes(data)
            files.append(path)
        header = struct.pack('>IIBBBBB', 100000, 100000, 8, 6, 0, 0, 0)
        bomb = tmp_path / 'bomb.png'
        bomb.write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', len(header)) + b'IHDR' + header +
                         struct.pack('>I', zlib.crc32(b'IHDR'+header)) + b'\0\0\0\0IEND\xaeB`\x82')
        files.append(bomb)
        for image in files:
            with pytest.raises(PDFOperationError):
                editor.prepare_signature(image)
            assert editor.path == revision and not editor.dirty
        assert not editor._signatures
        assert not list(editor._root.glob('*.image'))
        assert not list(editor._root.glob('*.rgba'))
        assert not list(editor._root.glob('*.signature.pdf'))
        good = editor.prepare_signature(signature_image())
        editor.insert_signature(0, good['asset'], [80, 180, 240, 260], str(revision))
    finally:
        editor.close()


def test_signature_rejects_stale_geometry_and_external_artifacts(text_pdf, signature_image, tmp_path):
    editor = BrokerEditor(text_pdf)
    try:
        asset = editor.prepare_signature(signature_image())['asset']
        revision = editor.path
        for page, rect, selected in [(True, [1, 1, 100, 100], str(revision)),
                                     (0, [0, 0, math.nan, 10], str(revision)),
                                     (0, [-1, 0, 100, 100], str(revision)),
                                     (0, [0, 0, 1000, 100], str(revision)),
                                     (0, [0, 0, 100, 100], 'stale.pdf')]:
            with pytest.raises(PDFOperationError):
                editor.insert_signature(page, asset, rect, selected)
        with pytest.raises(PDFOperationError):
            editor.insert_signature(0, text_pdf, [1, 1, 100, 100], str(revision))
        with pytest.raises(PDFOperationError):
            editor.call('prepare_signature', str(text_pdf), str(tmp_path/'out.pdf'), str(tmp_path/'out.rgba'))
        with pytest.raises(PDFOperationError):
            editor.call('prepare_signature', text_pdf, tmp_path/'out.pdf', tmp_path/'out.rgba')
        assert not (tmp_path/'out.pdf').exists()
        assert editor.path == revision
    finally:
        editor.close()


@pytest.mark.parametrize('data', [{'width': True, 'height': 1}, {'width': 2049, 'height': 20},
                                  {'width': 2, 'height': math.inf}, {'width': 2, 'height': 2, 'path': 'x'}])
def test_signature_preview_schema_is_bounded(data):
    with pytest.raises(ValueError):
        result('prepare_signature', data)


def test_signature_downsamples_large_images_and_rejects_readonly_pdf(text_pdf, tmp_path):
    image = QImage(3200, 1600, QImage.Format.Format_RGBA8888)
    image.fill(QColor('blue'))
    path = tmp_path/'large-valid.png'
    assert image.save(str(path))
    editor = BrokerEditor(text_pdf)
    try:
        asset = editor.prepare_signature(path)
        assert asset['width'] == 1600 and asset['height'] == 800
        assert len(asset['pixels']) == 1600*800*4
    finally:
        editor.close()
    protected = tmp_path/'restricted.pdf'
    with PdfReader(text_pdf) as reader:
        writer = PdfWriter(clone_from=reader)
        writer.encrypt('', 'owner-password')
        writer.write(protected)
    editor = BrokerEditor(protected)
    try:
        assert editor.encrypted
        with pytest.raises(PDFOperationError):
            editor.prepare_signature(path)
        assert not editor._signatures and not editor.dirty
    finally:
        editor.close()


def test_broker_rejects_inconsistent_raw_preview_without_keeping_asset(text_pdf, signature_image, monkeypatch):
    editor = BrokerEditor(text_pdf)
    try:
        original = editor.call
        def tampered(method, *args):
            value = original(method, *args)
            if method == 'prepare_signature':
                args[2].write_bytes(b'short')
            return value
        monkeypatch.setattr(editor, 'call', tampered)
        with pytest.raises((ValueError, OSError)):
            editor.prepare_signature(signature_image())
        assert not editor._signatures and not editor.dirty
        assert not list(editor._root.glob('*.signature.pdf'))
    finally:
        editor.close()


def test_prepared_signature_survives_worker_restart(text_pdf, signature_image):
    editor = BrokerEditor(text_pdf)
    try:
        asset = editor.prepare_signature(signature_image())['asset']
        revision = str(editor.path)
        editor._stop()
        editor.insert_signature(0, asset, [80, 180, 240, 260], revision)
        assert editor.dirty and str(editor.path) != revision
    finally:
        editor.close()
