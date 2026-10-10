"""Real print-to-PDF jobs; tests never submit jobs to physical printers."""
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject
from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QFileDialog

from test_gui import app, text_pdf, window, wait_for
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.print_source import PrintSource
from ape_pdf_studio.ui.print_dialog import PrintDialog
from ape_pdf_studio.ui.printing import (
    PrintJob, PrintOptions, copy_sequence, print_geometry, raster_plan, selected_pages,
)


def test_intervals_parity_reverse_and_copies():
    assert selected_pages(8, 2, "range", "1,3-6", "odd", True) == [4, 2, 0]
    assert selected_pages(8, 2, "current") == [2]
    assert selected_pages(8, 2, "view") == [2]
    assert copy_sequence([0, 2], 2, True) == [0, 2, 0, 2]
    assert copy_sequence([0, 2], 2, False) == [0, 0, 2, 2]
    assert copy_sequence([0, 1, 2], 2, True, duplex=True) == [0, 1, 2, None, 0, 1, 2]
    assert copy_sequence([0, 1, 2], 2, False, duplex=True) == [0, 1, 0, 1, 2, None, 2]
    assert copy_sequence([0, 1], 2, False, duplex=True) == [0, 1, 0, 1]
    with pytest.raises(PDFOperationError):
        selected_pages(3, 0, "current", parity="even")
    with pytest.raises(PDFOperationError):
        selected_pages(3, 0, "range", "4")


def test_geometry_fit_rotation_actual_and_view_region():
    paper = QRectF(0, 0, 600, 800)
    area = paper.adjusted(20, 20, -20, -20)
    geometry = print_geometry(QSizeF(1200, 800), paper, area, PrintOptions())
    assert geometry.rotated
    assert geometry.scale == pytest.approx(760 / 1200)
    mapped = geometry.transform.mapRect(geometry.source)
    assert mapped.center().x() == pytest.approx(300)
    assert mapped.center().y() == pytest.approx(400)
    assert area.contains(mapped)
    geometry = print_geometry(QSizeF(1200, 800), paper, area, PrintOptions(mode="actual", rotate=False))
    assert geometry.scale == 1
    assert geometry.transform.mapRect(geometry.source).width() == 1200
    region = QRectF(100, 200, 400, 300)
    geometry = print_geometry(QSizeF(1200, 800), paper, area, PrintOptions(rotate=False), region)
    assert geometry.transform.mapRect(region).left() == pytest.approx(20)
    assert geometry.transform.mapRect(region).width() == pytest.approx(560)
    geometry = print_geometry(QSizeF(100, 200), paper, area, PrintOptions(mode="shrink"))
    assert geometry.scale == 1
    with pytest.raises(PDFOperationError):
        print_geometry(QSizeF(100, 200), paper, area, PrintOptions(), QRectF())


def test_poster_raster_plan_only_renders_sheet_content():
    size = QSizeF(3168, 2448)
    paper = QRectF(0, 0, 595, 842)
    geometry = print_geometry(size, paper, paper.adjusted(10, 10, -10, -10),
                              PrintOptions(mode="custom", scale=5, rotate=False))
    tiles, _ = raster_plan(0, size, geometry, 600)
    assert tiles
    assert all(tile.width <= 1024 and tile.height <= 1024 for tile in tiles)
    assert tiles[0].full_width == 132000  # No entire-poster bitmap allocation.
    assert sum(tile.width * tile.height for tile in tiles) < 60_000_000


def test_print_source_filters_nonprinting_comments_preserves_widgets(text_pdf, tmp_path):
    writer = PdfWriter(clone_from=text_pdf)
    annotations = []
    for subtype, flags in [("/Text", 4), ("/Text", 0), ("/Text", 6), ("/Widget", 36)]:
        obj = DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                                NameObject("/Subtype"): NameObject(subtype),
                                NameObject("/F"): NumberObject(flags)})
        annotations.append(writer._add_object(obj))
    writer.pages[0][NameObject("/Annots")] = ArrayObject(annotations)
    path = tmp_path / "annotations.pdf"
    writer.write(path)
    before = path.read_bytes()
    source = PrintSource(path)
    try:
        with PdfReader(source.path(True)) as reader:
            assert [ref.get_object()["/Subtype"] for ref in reader.pages[0]["/Annots"]] == ["/Text", "/Widget"]
            assert reader.pages[0]["/Annots"][1].get_object()["/F"] == 4
        with PdfReader(source.path(False)) as reader:
            assert [ref.get_object()["/Subtype"] for ref in reader.pages[0]["/Annots"]] == ["/Widget"]
        assert source.path(True) == source.path(True)
        assert path.read_bytes() == before
    finally:
        source.close()


def pdf_printer():
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setResolution(300)
    return printer


@pytest.mark.parametrize("images", [False, True])
def test_real_pdf_print_page_count_order_and_vector_quality(app, text_pdf, tmp_path, images):
    import pymupdf
    document = QPdfDocument()
    document.load(str(text_pdf))
    output = tmp_path / f"printed-{images}.pdf"
    original = text_pdf.read_bytes()
    printer = pdf_printer()
    job = PrintJob(document, text_pdf, printer, [2, 0, 2], PrintOptions(images=images, dpi=150), output=output)
    result, errors = [], []
    job.finished.connect(lambda: result.append(True))
    job.failed.connect(errors.append)
    job.start()
    wait_for(lambda: result or errors, 25000)
    assert not errors
    assert result == [True]
    assert not job.active
    with pymupdf.open(output) as printed:
        assert len(printed) == 3
        assert printed[0].rect.width == pytest.approx(595, abs=1)
        assert printed[0].rect.height == pytest.approx(842, abs=1)
        first = printed[0].get_pixmap().samples
        assert first == printed[2].get_pixmap().samples
        assert first != printed[1].get_pixmap().samples
        assert printed[0].get_images() if images else not printed[0].get_images()
        if not images:
            paths = printed[0].get_drawings()
            visible = [path for path in paths if path['fill_opacity'] > .5]
            # The SVG/PDF NoBrush regression used to add an opaque thick outline
            # around every glyph. Compare the actual printed glyph with the input.
            expected_ratio = min((595 - 20) / 400, (842 - 20) / 600)
            assert all(path['rect'].height < 25 for path in visible)
            assert visible[0]['rect'].height == pytest.approx(
                11.664 * expected_ratio, abs=1.0)  # Helvetica A outline, no added stroke.
            assert any(path['fill_opacity'] == 0 for path in paths)
    assert text_pdf.read_bytes() == original
    assert not list(tmp_path.glob(".apex_print_*"))
    document.close()


def test_print_cancel_preserves_existing_destination(app, text_pdf, tmp_path):
    document = QPdfDocument()
    document.load(str(text_pdf))
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"existing content")
    job = PrintJob(document, text_pdf, pdf_printer(), [0, 1, 2], PrintOptions(images=True), output=output)
    cancelled = []
    job.cancelled.connect(lambda: cancelled.append(True))
    job.start()
    job.cancel()
    assert cancelled == [True]
    assert not job.active
    assert output.read_bytes() == b"existing content"
    assert not list(tmp_path.glob(".apex_print_*"))
    document.close()


def test_custom_dialog_preview_range_and_real_save(window, tmp_path, monkeypatch):
    dialog = PrintDialog(window.current, window)
    dialog.show()
    try:
        dialog.printers.setCurrentIndex(0)  # Never print to a physical device in tests.
        wait_for(lambda: dialog.preview.image is not None, 10000)
        assert dialog.print_button.isEnabled()
        dialog.selection.setCurrentIndex(dialog.selection.findData("range"))
        dialog.range_edit.setText("9")
        wait_for(lambda: bool(dialog.feedback.text()))
        assert not dialog.print_button.isEnabled()
        dialog.range_edit.setText("1,3")
        dialog.reverse.setChecked(True)
        dialog.copies.setValue(2)
        wait_for(lambda: dialog._pages == [2, 0, 2, 0])
        dialog._navigate(1)
        assert "Pagina PDF 1" in dialog.preview_info.text()
        assert dialog.sheet_label.text() == "Foglio 2 di 4"
        output = tmp_path / "from-dialog.pdf"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(output), "PDF (*.pdf)"))
        dialog._print()
        wait_for(lambda: dialog._closed or (dialog.job and not dialog.job.active), 25000)
        assert dialog._closed, dialog.feedback.text()
        assert len(PdfReader(output).pages) == 4
        assert not window.current.editor.dirty
        assert window.toast.title.text() == "Stampa salvata"
    finally:
        dialog.done(dialog.result())
        dialog.deleteLater()


def test_dialog_current_view_and_grayscale_preview(window):
    dialog = PrintDialog(window.current, window)
    try:
        dialog.printers.setCurrentIndex(0)
        dialog.selection.setCurrentIndex(dialog.selection.findData("view"))
        dialog.colors.setCurrentIndex(dialog.colors.findData(QPrinter.ColorMode.GrayScale))
        wait_for(lambda: dialog.preview.image is not None and dialog._loaded is not None, 10000)
        assert not dialog._view_rect.isEmpty()
        assert dialog._preview_geometry.source == dialog._view_rect
        assert dialog._pages == [window.current.page]
        assert dialog.print_button.isEnabled()
    finally:
        dialog.done(dialog.result())
        dialog.deleteLater()


@pytest.mark.parametrize("images", [False, True])
def test_output_grayscale_is_applied_to_colors(app, tmp_path, images):
    import pymupdf
    original = pymupdf.open()
    page = original.new_page(width=300, height=200)
    page.draw_rect(pymupdf.Rect(20, 20, 150, 150), color=(1, 0, 0), fill=(0, .4, 1))
    source = tmp_path / 'colored.pdf'
    original.save(source)
    original.close()
    document = QPdfDocument()
    document.load(str(source))
    printer = pdf_printer()
    printer.setColorMode(QPrinter.ColorMode.GrayScale)
    output = tmp_path / 'gray.pdf'
    job = PrintJob(document, source, printer, [0], PrintOptions(images=images, dpi=150), output=output)
    done, errors = [], []
    job.finished.connect(lambda: done.append(True))
    job.failed.connect(errors.append)
    job.start()
    wait_for(lambda: done or errors, 15000)
    assert not errors
    with pymupdf.open(output) as printed:
        pixels = printed[0].get_pixmap().samples
        # MuPDF's DeviceGray -> RGB conversion can differ by one channel level.
        assert max(abs(r - g) for r, g in zip(pixels[0::3], pixels[1::3])) <= 1
        assert max(abs(r - b) for r, b in zip(pixels[0::3], pixels[2::3])) <= 1
        assert min(pixels) < 200
    document.close()


def test_vector_fallback_keeps_embedded_images(app, tmp_path):
    import pymupdf
    from PySide6.QtGui import QImage
    from PySide6.QtCore import Qt
    image_path = tmp_path / 'blue.png'
    image = QImage(50, 50, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    image.save(str(image_path))
    source = tmp_path / 'image.pdf'
    original = pymupdf.open()
    page = original.new_page(width=200, height=300)
    page.insert_image(pymupdf.Rect(30, 30, 100, 100), filename=str(image_path))
    original.save(source)
    original.close()
    document = QPdfDocument()
    document.load(str(source))
    output = tmp_path / 'image-printed.pdf'
    job = PrintJob(document, source, pdf_printer(), [0], PrintOptions(dpi=150), output=output)
    done, errors = [], []
    job.finished.connect(lambda: done.append(True))
    job.failed.connect(errors.append)
    job.start()
    wait_for(lambda: done or errors, 15000)
    assert not errors
    with pymupdf.open(output) as printed:
        assert printed[0].get_images()
        samples = printed[0].get_pixmap().samples
        assert any(b > r + 100 for r, b in zip(samples[0::3], samples[2::3]))
    document.close()


def test_cancel_after_first_sheet_preserves_destination(app, text_pdf, tmp_path):
    document = QPdfDocument()
    document.load(str(text_pdf))
    output = tmp_path / 'previous.pdf'
    output.write_bytes(b'previous file')
    job = PrintJob(document, text_pdf, pdf_printer(), [0, 1], PrintOptions(images=True, dpi=150), output=output)
    cancelled = []
    job.cancelled.connect(lambda: cancelled.append(True))
    job.progress.connect(lambda current, total: job.cancel() if current == 1 else None)
    job.start()
    wait_for(lambda: cancelled, 15000)
    assert output.read_bytes() == b'previous file'
    assert not list(tmp_path.glob('.apex_print_*'))
    document.close()


def test_print_source_handles_password_without_changing_source(text_pdf, tmp_path):
    writer = PdfWriter(clone_from=text_pdf)
    writer.encrypt('secret', algorithm='AES-256')
    path = tmp_path / 'encrypted.pdf'
    writer.write(path)
    before = path.read_bytes()
    source = PrintSource(path, 'secret')
    folder = source.original.parent
    try:
        with PdfReader(source.path()) as reader:
            assert len(reader.pages) == 3
            assert not reader.is_encrypted
        assert path.read_bytes() == before
    finally:
        source.close()
    assert not folder.exists()


def test_dialog_refuses_print_over_open_document(window, monkeypatch):
    dialog = PrintDialog(window.current, window)
    try:
        dialog.printers.setCurrentIndex(0)
        wait_for(lambda: dialog.print_button.isEnabled(), 10000)
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs:
                            (str(window.current.editor.source), 'PDF (*.pdf)'))
        original = window.current.editor.source.read_bytes()
        dialog._print()
        assert dialog.job is None
        assert 'nome diverso' in dialog.feedback.text()
        assert window.current.editor.source.read_bytes() == original
    finally:
        dialog.done(dialog.result())
        dialog.deleteLater()


def test_blank_back_keeps_duplex_copies_separate(app, text_pdf, tmp_path):
    import pymupdf
    document = QPdfDocument()
    document.load(str(text_pdf))
    output = tmp_path / 'duplex-copies.pdf'
    pages = copy_sequence([0, 1, 2], 2, True, duplex=True)
    job = PrintJob(document, text_pdf, pdf_printer(), pages, PrintOptions(images=True, dpi=150), output=output)
    done, errors = [], []
    job.finished.connect(lambda: done.append(True))
    job.failed.connect(errors.append)
    job.start()
    wait_for(lambda: done or errors, 15000)
    assert not errors
    with pymupdf.open(output) as printed:
        assert len(printed) == 7
        assert not printed[3].get_images()
        assert not printed[3].get_drawings()
        assert min(printed[3].get_pixmap().samples) == 255
        assert printed[0].get_pixmap().samples == printed[4].get_pixmap().samples
    document.close()


def test_vector_cleanup_retries_transient_windows_lock(app):
    import tempfile
    from ape_pdf_studio.ui.vector_pages import VectorPages
    temp = tempfile.TemporaryDirectory(prefix='apex_test_print_')
    path = Path(temp.name)
    calls = []

    class LockedOnce:
        def cleanup(self):
            calls.append(True)
            if len(calls) == 1:
                raise PermissionError('Transient Windows file lock')
            temp.cleanup()

    vectors = VectorPages()
    vectors._work = LockedOnce()
    vectors.reset()
    assert vectors._work is None
    wait_for(lambda: not path.exists())
    assert len(calls) == 2
