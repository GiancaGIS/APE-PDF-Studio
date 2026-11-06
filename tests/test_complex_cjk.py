"""Complex Chinese/Japanese PDFs through the real broker and protected renderer."""
import hashlib
import importlib.util
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QSize
from pypdf import PdfReader

from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.ui.remote_pdf import RemoteDocument
from test_gui import app, text_pdf, window, wait_for, wait_for_tiles

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("generate_complex_cjk_example", ROOT / "scripts/generate_complex_cjk_example.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.fixture(scope="module")
def complex_pdf(tmp_path_factory):
    return generator.create_example(tmp_path_factory.mktemp("complex-cjk") / "中文と日本語.pdf")


@pytest.fixture
def protected_document(app, complex_pdf):
    original = hashlib.sha256(complex_pdf.read_bytes()).digest()
    editor = BrokerEditor(complex_pdf)
    remote = RemoteDocument(editor)
    try:
        remote.load(editor.path)
        yield editor, remote
    finally:
        remote.close()
        editor.close()
        assert hashlib.sha256(complex_pdf.read_bytes()).digest() == original


def test_example_has_embedded_font_and_complex_structures(complex_pdf):
    with pymupdf.open(complex_pdf) as document:
        assert len(document) == 8
        assert all(document[page].get_fonts() for page in (0, 1, 3, 7))
        fonts = {entry[0] for entry in document[0].get_fonts()}
        assert any(len(document.extract_font(xref)[3]) > 1_000_000 for xref in fonts)
        assert "中文测试" in document[0].get_text() and "日本語テスト" in document[0].get_text()
        assert len(document.get_toc()) == 8 and document.get_ocgs()
        assert document[3].rotation == 90 and document[3].cropbox != document[3].mediabox
        assert document[5].rect.width > document[5].rect.height
        assert document[6].get_images() and not document[6].get_text().strip()
        assert {widget.field_name for widget in document[4].widgets()} == {"customer", "city", "confirmed"}
        assert {"sample-notes.txt", "font-license.txt"} <= set(document.embfile_names())
        assert b"Apache License" in document.embfile_get("font-license.txt")
    with PdfReader(complex_pdf) as reader:
        assert reader.pages[3]["/UserUnit"] == 1.25
        assert "中文" in reader.metadata.title and reader.xmp_metadata is not None


def test_pdfium_renders_all_pages_and_preserves_unicode_text(protected_document):
    editor, remote = protected_document
    assert remote.pageCount() == 8
    assert len(remote.bookmarks) == 8
    assert set(editor.security_report.findings) == {"attachments", "external_links"}
    assert not editor.security_report.blocked
    for page in range(8):
        size = remote.pagePointSize(page)
        scale = min(900 / size.width(), 1100 / size.height())
        image = remote.render(page, QSize(round(size.width() * scale), round(size.height() * scale)))
        assert not image.isNull() and image.width() > 500 and image.height() > 500
        assert image.sizeInBytes() == image.width() * image.height() * 4
    text = remote.getAllText(0).text()
    for value in ("中文测试", "繁體中文", "日本語テスト", "こんにちは世界"):
        assert value in text
    assert not remote.getAllText(6).text().strip()
    assert "日本語テスト" in remote.getAllText(3).text()


@pytest.mark.parametrize("query", ("中文测试", "日本語テスト", "こんにちは世界"))
def test_protected_cjk_search_selection_and_copy(protected_document, app, query):
    editor, remote = protected_document
    result = editor.call("qt_search", editor.path, query)
    assert not result["truncated"] and len(result["results"]) >= 3
    assert all(match["rectangles"] for match in result["results"])
    text = remote.getAllText(0).text()
    selection = remote.getSelectionAtIndex(0, text.index(query), len(query))
    assert selection.text() == query and selection.isValid()
    selection.copyToClipboard()
    assert app.clipboard().text() == query


def test_roundtrip_edit_history_keeps_cjk_structures(protected_document, tmp_path):
    editor, remote = protected_document
    metadata = editor.metadata().values
    text = remote.getAllText(0).text()
    revision = editor.path
    editor.add_note(1, "新增批注 / 新しい注釈", (45, 435))
    assert editor.undo() and editor.path == revision
    assert editor.redo()
    # ASCII values test preservation of existing fields, not CJK font fallback.
    editor.fill_form({"customer": "Test Reader", "city": "Tokyo"}, editor.path)
    output = editor.save_as(tmp_path / "已保存-日本語.pdf")
    reopened = BrokerEditor(output)
    try:
        assert reopened.metadata().values == metadata
        assert {field.name: field.value for field in reopened.interactions()[1]}["customer"] == "Test Reader"
        view = RemoteDocument(reopened)
        try:
            view.load(reopened.path)
            assert view.getAllText(0).text() == text
            assert len(view.bookmarks) == 8
        finally:
            view.close()
        assert any("新しい注釈" in note["text"] for note in reopened.notes()[1])
    finally:
        reopened.close()
    with pymupdf.open(output) as document:
        assert document.embfile_get("sample-notes.txt").startswith(b"Synthetic sample")
        assert document.get_ocgs() and document[3].rotation == 90
    with PdfReader(output) as reader:
        assert reader.pages[3]["/UserUnit"] == 1.25


def test_cjk_watermark_keeps_original_text_and_forms(protected_document):
    editor, remote = protected_document
    revision = editor.path
    editor.add_watermark("中文水印 / 日本語", 24, (.1, .4, .6), .3, 30)
    remote.load(editor.path)
    for page in (0, 3, 4, 6, 7):
        assert "中文水印" in remote.getAllText(page).text()
    assert "こんにちは世界" in remote.getAllText(0).text()
    assert len(editor.interactions()[1]) == 3
    assert editor.undo() and editor.path == revision


def test_actual_gui_search_copy_and_navigation(window, complex_pdf, app):
    original = hashlib.sha256(complex_pdf.read_bytes()).digest()
    window.open_pdf(complex_pdf)
    tab = window.current
    assert tab.count == 8 and "中文" in tab.editor.metadata().values["/Title"]
    wait_for_tiles(tab.viewer)
    for query in ("中文测试", "日本語テスト"):
        tab.set_search(query)
        wait_for(lambda: tab.search_model.count() > 0, timeout=10000)
        assert tab.search_model.count() >= 3
    tab.viewer.select_page_text(0)
    tab.viewer.copy_selection()
    assert "中文测试" in app.clipboard().text() and "こんにちは世界" in app.clipboard().text()
    for page in (2, 3, 4, 5, 6, 7):
        tab.jump_to_page(page)
        wait_for_tiles(tab.viewer)
    assert not tab.editor.dirty
    assert hashlib.sha256(complex_pdf.read_bytes()).digest() == original
