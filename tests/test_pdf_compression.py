"""Real isolated compression, content preservation and atomic publication."""
import hashlib
import os
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from PySide6.QtWidgets import QFileDialog

from test_gui import app, window, text_pdf, wait_for
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.pdf_compression import compression_profile
from ape_pdf_studio.core.pdf_runtime import open_static_pdf
from ape_pdf_studio.ui.compression import CompressionDialog, CompressedPdfExportJob


@pytest.fixture
def illustrated_pdf(tmp_path):
    width, height = 1280, 1600
    y, x = np.mgrid[:height, :width]
    noise = np.random.default_rng(42).integers(0, 15, (height, width, 3), dtype=np.uint8)
    pixels = np.stack(((x // 6 + y // 10) % 240, (y // 7) % 240, (x // 8) % 240), axis=2).astype(np.uint8)
    pixels += noise
    image = pymupdf.Pixmap(pymupdf.csRGB, width, height, pixels.tobytes(), False)
    path = tmp_path / 'illustrated.pdf'
    with open_static_pdf() as pdf:
        page = pdf.new_page(width=320, height=400)
        page.insert_image(page.rect, pixmap=image)
        page.insert_text((20, 30), 'Searchable text preserved', fontsize=13)
        page.draw_line((20, 60), (200, 60), color=(0, 0, 1), width=2)
        page.add_text_annot((270, 30), 'Retained note')
        page.add_ink_annot([[(30, 90), (70, 110), (150, 95)]])
        widget = pymupdf.Widget()
        widget.field_name = 'Customer'
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_value = 'Example value'
        widget.rect = pymupdf.Rect(30, 310, 250, 340)
        page.add_widget(widget)
        page.insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(20, 350, 180, 380), 'uri': 'https://example.com/'})
        pdf.new_page(width=400, height=320)
        pdf.set_toc([[1, 'Start', 1], [1, 'Blank', 2]])
        pdf.set_metadata({'title': 'Compression example', 'author': 'Synthetic test'})
        pdf.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><test>Retained XMP</test></x:xmpmeta>')
        pdf.save(path)  # Deliberately uncompressed RGB samples.
    return path


def completed_job(app, source, output, level):
    job = CompressedPdfExportJob(source, output, level)
    successes, errors, finished = [], [], []
    job.succeeded.connect(lambda path, result: successes.append(result))
    job.failed.connect(errors.append)
    job.finished.connect(lambda: finished.append(True))
    job.start()
    wait_for(lambda: bool(finished), timeout=30000)
    assert not errors, errors
    assert len(successes) == 1
    return successes[0]


@pytest.mark.parametrize('level', [False, True, -1, 101, 50.0, '40', None])
def test_invalid_profile_rejected_before_creating_job(level, tmp_path):
    with pytest.raises(PDFOperationError):
        CompressedPdfExportJob(tmp_path / 'missing.pdf', tmp_path / 'out.pdf', level)


def test_slider_profiles_and_keyboard(app):
    dialog = CompressionDialog()
    assert dialog.slider.value() == 40
    assert compression_profile(40) == dict(quality=85, dpi_target=228)
    dialog.slider.setValue(0)
    assert compression_profile(0) is None and 'senza perdita' in dialog.detail.text()
    dialog.slider.setValue(100)
    assert compression_profile(100) == dict(quality=70, dpi_target=120)
    assert '120 dpi' in dialog.detail.text()
    profiles = [compression_profile(level) for level in range(1, 101)]
    assert all(a['quality'] >= b['quality'] and a['dpi_target'] >= b['dpi_target'] for a, b in zip(profiles, profiles[1:]))
    dialog.deleteLater()


@pytest.mark.parametrize('level', [0, 40, 100])
def test_isolated_compression_preserves_document_and_reduces_size(app, illustrated_pdf, tmp_path, level):
    original = illustrated_pdf.read_bytes()
    output = tmp_path / f'compressed-{level}.pdf'
    result = completed_job(app, illustrated_pdf, output, level)
    assert result['pages'] == 2 and result['before'] == len(original)
    assert result['after'] == output.stat().st_size < len(original)
    assert illustrated_pdf.read_bytes() == original
    with open_static_pdf(illustrated_pdf) as before, open_static_pdf(output) as after:
        assert len(after) == 2
        assert after[0].get_text() == before[0].get_text()
        assert [p.rect for p in after] == [p.rect for p in before]
        assert after.get_toc() == before.get_toc()
        assert after.metadata['title'] == before.metadata['title']
        assert after.get_xml_metadata() == before.get_xml_metadata()
        assert [a.type for a in after[0].annots()] == [a.type for a in before[0].annots()]
        assert [(w.field_name, w.field_value) for w in after[0].widgets()] == [('Customer', 'Example value')]
        assert after[0].get_links()[0]['uri'] == 'https://example.com/'
        assert len(after[0].get_drawings()) == len(before[0].get_drawings())
        a, b = before[0].get_pixmap(alpha=False), after[0].get_pixmap(alpha=False)
        pixels_a, pixels_b = np.frombuffer(a.samples, np.uint8), np.frombuffer(b.samples, np.uint8)
        if level == 0:
            assert np.array_equal(pixels_a, pixels_b)
            assert after[0].get_images()[0][2:4] == (1280, 1600)
        else:
            assert np.abs(pixels_a.astype(float) - pixels_b).mean() < 9
            assert after[0].get_images()[0][2] < 1280
            assert result['after'] < len(original) / 5


def test_more_compression_reduces_image_resolution(app, illustrated_pdf, tmp_path):
    outputs = [tmp_path / f'level-{n}.pdf' for n in (40, 100)]
    for level, output in zip((40, 100), outputs):
        completed_job(app, illustrated_pdf, output, level)
    assert outputs[1].stat().st_size < outputs[0].stat().st_size
    with open_static_pdf(outputs[0]) as mild, open_static_pdf(outputs[1]) as strong:
        assert strong[0].get_images()[0][2] < mild[0].get_images()[0][2]


def test_transparent_images_preserve_appearance(app, tmp_path):
    source, output = tmp_path / 'transparent.pdf', tmp_path / 'transparent-small.pdf'
    with open_static_pdf() as pdf:
        page = pdf.new_page(width=200, height=200)
        page.draw_rect(page.rect, color=None, fill=(0, 0, 1))
        pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), True)
        pixels.clear_with(0)
        for y in range(100):
            for x in range(50):
                pixels.set_pixel(x, y, (128, 0, 0, 128))
        page.insert_image(page.rect, pixmap=pixels)
        pdf.save(source)
    completed_job(app, source, output, 100)
    with open_static_pdf(source) as before, open_static_pdf(output) as after:
        a, b = before[0].get_pixmap(alpha=False), after[0].get_pixmap(alpha=False)
        assert np.abs(np.frombuffer(a.samples, np.uint8).astype(float) - np.frombuffer(b.samples, np.uint8)).mean() < 4


def test_already_optimized_never_grows(app, text_pdf, tmp_path):
    first, second = tmp_path / 'first.pdf', tmp_path / 'second.pdf'
    completed_job(app, text_pdf, first, 0)
    completed_job(app, first, second, 100)
    assert 0 < second.stat().st_size <= first.stat().st_size


def test_bitonal_scan_retains_pixels_and_resolution(app, tmp_path):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    source, output = tmp_path / 'bitonal.pdf', tmp_path / 'bitonal-small.pdf'
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    image = DecodedStreamObject()
    image.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                  NameObject('/Width'): NumberObject(800), NameObject('/Height'): NumberObject(800),
                  NameObject('/BitsPerComponent'): NumberObject(1), NameObject('/ColorSpace'): NameObject('/DeviceGray')})
    image.set_data(bytes([0xAA, 0x55]) * 40000)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Im'): writer._add_object(image)})})
    content = DecodedStreamObject()
    content.set_data(b'q 100 0 0 100 0 0 cm /Im Do Q')
    page[NameObject('/Contents')] = writer._add_object(content)
    writer.write(source)
    completed_job(app, source, output, 100)
    with PdfReader(output) as reader:
        saved = reader.pages[0]['/Resources']['/XObject']['/Im'].get_object()
        assert (saved['/Width'], saved['/Height'], saved['/BitsPerComponent']) == (800, 800, 1)
    # Lossless optimization may encode raw 1-bit samples as CCITT. pypdf's
    # get_data() returns a TIFF wrapper for that filter, not the sample bytes.
    with open_static_pdf(source) as before, open_static_pdf(output) as after:
        a = pymupdf.Pixmap(before, before[0].get_images()[0][0])
        b = pymupdf.Pixmap(after, after[0].get_images()[0][0])
        assert a.samples == b.samples


def test_cancel_preserves_existing_output(app, illustrated_pdf, tmp_path):
    output = tmp_path / 'existing.pdf'
    output.write_bytes(b'Existing destination canary')
    job = CompressedPdfExportJob(illustrated_pdf, output, 40)
    finished, cancelled, errors = [], [], []
    job.finished.connect(lambda: finished.append(True))
    job.cancelled.connect(lambda: cancelled.append(True))
    job.failed.connect(errors.append)
    folder = Path(job._work.name)
    job.start()
    job.cancel()
    wait_for(lambda: bool(finished), timeout=10000)
    assert cancelled == [True] and not errors
    assert output.read_bytes() == b'Existing destination canary'
    assert not folder.exists() and not list(tmp_path.glob('.apex_export_*'))


@pytest.mark.parametrize('kind', ['malformed', 'encrypted', 'restricted', 'launch'])
def test_worker_rejects_unsupported_input_without_changing_destination(app, tmp_path, kind):
    source, output = tmp_path / 'input.pdf', tmp_path / 'output.pdf'
    if kind == 'malformed':
        source.write_bytes(b'%PDF-1.7\nnot a real document')
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=300)
        if kind in ('encrypted', 'restricted'):
            writer.encrypt('secret' if kind == 'encrypted' else '', owner_password='owner-secret')
        else:
            from pypdf.generic import DictionaryObject, NameObject, TextStringObject
            writer.root_object[NameObject('/OpenAction')] = DictionaryObject({NameObject('/S'): NameObject('/Launch'), NameObject('/F'): TextStringObject('blocked.exe')})
        writer.write(source)
    original = source.read_bytes()
    output.write_bytes(b'Existing destination')
    job = CompressedPdfExportJob(source, output, 40)
    errors, successes, finished = [], [], []
    job.failed.connect(errors.append)
    job.succeeded.connect(lambda *_: successes.append(True))
    job.finished.connect(lambda: finished.append(True))
    job.start()
    wait_for(lambda: bool(finished), timeout=20000)
    assert errors and not successes
    assert output.read_bytes() == b'Existing destination' and source.read_bytes() == original


def test_output_cannot_replace_input_or_hardlink(app, text_pdf, tmp_path):
    alias = tmp_path / 'alias.pdf'
    alias.hardlink_to(text_pdf)
    for destination in (text_pdf, alias, tmp_path / 'out.txt'):
        with pytest.raises(PDFOperationError):
            CompressedPdfExportJob(text_pdf, destination, 40)


def test_sandbox_start_failure_preserves_destination(app, text_pdf, tmp_path, monkeypatch):
    output = tmp_path / 'existing.pdf'
    output.write_bytes(b'Destination canary')
    job = CompressedPdfExportJob(text_pdf, output, 40)
    folder = Path(job._work.name)
    errors, finished = [], []
    job.failed.connect(errors.append)
    job.finished.connect(lambda: finished.append(True))
    def fail(*args, **kwargs):
        raise PDFOperationError('Sandbox unavailable')
    monkeypatch.setattr('ape_pdf_studio.ui.compression.wrap_command', fail)
    job.start()
    assert errors == ['Sandbox unavailable'] and finished == [True]
    assert output.read_bytes() == b'Destination canary' and not folder.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows security zone ADS')
def test_export_preserves_security_zone_without_private_urls(app, text_pdf, tmp_path):
    from ape_pdf_studio.core.provenance import read_zone
    Path(str(text_pdf) + ':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\nHostUrl=https://private.invalid/document\n')
    output = tmp_path / 'internet-copy.pdf'
    completed_job(app, text_pdf, output, 40)
    assert read_zone(output) == 3
    assert 'HostUrl' not in Path(str(output) + ':Zone.Identifier').read_text()


def test_worker_rejects_destination_outside_workspace(app, text_pdf, tmp_path):
    import subprocess
    from ape_pdf_studio.core.private_work import PrivateDirectory
    from ape_pdf_studio.core.security import copy_checked
    from ape_pdf_studio.core.sandbox import sandbox_command
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'Outside canary')
    with PrivateDirectory() as work:
        source = Path(work) / 'source.pdf'
        copy_checked(text_pdf, source)
        result = subprocess.run(sandbox_command('compress', work, source, outside, '40', timeout=15000),
                                input=b'', capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode != 0 and b'"error"' in result.stdout
    assert outside.read_bytes() == b'Outside canary'


def test_compression_keeps_cjk_forms_layers_and_cropped_rotated_pages(app, tmp_path):
    source = Path(__file__).resolve().parents[1] / 'examples/complex_cjk.pdf'
    digest = hashlib.sha256(source.read_bytes()).digest()
    output = tmp_path / 'cjk-compressed.pdf'
    completed_job(app, source, output, 40)
    with open_static_pdf(source) as before, open_static_pdf(output) as after:
        assert len(before) == len(after) == 8
        assert [page.get_text() for page in after] == [page.get_text() for page in before]
        assert [(p.rect, p.rotation) for p in after] == [(p.rect, p.rotation) for p in before]
        assert [w.field_name for p in after for w in p.widgets()] == [w.field_name for p in before for w in p.widgets()]
        assert len(after.get_ocgs()) == len(before.get_ocgs())
        assert after.embfile_names() == before.embfile_names()
    assert hashlib.sha256(source.read_bytes()).digest() == digest


def test_gui_export_uses_current_revision_and_keeps_history(window, tmp_path, monkeypatch):
    tab = window.current
    original = hashlib.sha256(tab.editor.source.read_bytes()).digest()
    tab.apply_edit('add_note', 0, 'Unsaved annotation', (30, 40))
    accepted = tab.editor.path
    output = tmp_path / 'exported.pdf'
    monkeypatch.setattr(CompressionDialog, 'exec', lambda self: (self.slider.setValue(40), self.accept(), self.result())[-1])
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    window.export_compressed()
    assert window._compression_job is not None and not window.a_export_compressed.isEnabled()
    wait_for(lambda: window._compression_job is None, timeout=25000)
    assert tab.editor.dirty and tab.editor.path == accepted and tab.editor.can_undo
    with PdfReader(output) as reader:
        assert 'Unsaved annotation' in [str(a.get_object().get('/Contents', '')) for a in reader.pages[0]['/Annots']]
    assert hashlib.sha256(tab.editor.source.read_bytes()).digest() == original
    assert window.toast.title.text() == 'PDF compresso esportato'
    assert window.a_export_compressed.isEnabled()
    tab.editor.undo()
    assert not tab.editor.dirty
