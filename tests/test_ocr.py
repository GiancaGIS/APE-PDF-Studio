"""Actual OCR, invisible overlay geometry, revision import and job lifecycle."""
import io
import os
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject

from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.ocr import prepare_tessdata, recognize_pdf


@pytest.fixture
def tessdata():
    candidates = [Path(__file__).resolve().parents[1] / ".dev-deps/tessdata"]
    if os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(os.environ["TESSDATA_PREFIX"]))
    for folder in candidates:
        if all((folder / f"{code}.traineddata").exists() for code in ("ita", "eng")):
            return folder
    pytest.skip("Real OCR requires ita.traineddata and eng.traineddata (see README).")


@pytest.fixture
def scan_pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as text:
        page = text.new_page(width=400, height=600)
        page.insert_text((45, 100), "Documento italiano per ricerca", fontsize=19)
        page.insert_text((45, 145), "English searchable document", fontsize=19)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        with pymupdf.open() as scan:
            page = scan.new_page(width=400, height=600)
            page.insert_image(page.rect, pixmap=pixels)
            page.add_text_annot((350, 200), "Nota conservata")
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(40, 80, 200, 110),
                              "uri": "https://example.com"})
            scan.new_page(width=400, height=600).insert_text((40, 100), "Existing text")
            scan.new_page(width=400, height=600)
            scan.set_metadata({"title": "OCR test", "author": "APEX"})
            scan.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                                  '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>')
            scan.set_toc([[1, "Scansione", 1]])
            scan.embfile_add("attachment.txt", b"attachment preserved")
            scan.save(path)
    return path


def test_real_bilingual_ocr_preserves_pdf_and_roundtrips_history(scan_pdf, tessdata, tmp_path):
    original = scan_pdf.read_bytes()
    output = tmp_path / "ocr.pdf"
    progress = []
    result = recognize_pdf(scan_pdf, output, cache_dir=tessdata, progress=lambda *a: progress.append(a))
    assert result == {"recognized": [1], "skipped": [2], "empty": [3], "reduced_dpi": []}
    assert progress[-1][:2] == (3, 3)
    with pymupdf.open(scan_pdf) as source, pymupdf.open(output) as recognized:
        assert "Documento italiano" in recognized[0].get_text()
        assert "English searchable" in recognized[0].get_text()
        assert recognized.metadata == source.metadata
        assert recognized.get_xml_metadata() == source.get_xml_metadata()
        assert recognized.get_toc() == source.get_toc()
        assert recognized.embfile_get("attachment.txt") == b"attachment preserved"
        assert recognized[0].get_links()[0]["uri"] == "https://example.com"
        assert list(recognized[0].annots())[0].info["content"] == "Nota conservata"
        for a, b in zip(source, recognized):
            assert a.get_pixmap().samples == b.get_pixmap().samples
            assert len(a.get_images()) == len(b.get_images())
    editor = PDFEditor(scan_pdf)
    try:
        revision = editor.path
        editor.apply_ocr(output, revision)
        assert editor.dirty and editor.can_undo
        with PdfReader(editor.path) as pdf:
            assert "English searchable" in pdf.pages[0].extract_text()
        saved = tmp_path / "saved.pdf"
        editor.save_as(saved)
        assert not editor.dirty
        assert editor.undo() and editor.path == revision and editor.dirty
        assert editor.redo() and not editor.dirty
        second = recognize_pdf(editor.path, tmp_path / "again.pdf", cache_dir=tessdata)
        assert second["recognized"] == [] and second["skipped"] == [1, 2]
        with pytest.raises(PDFOperationError, match="cambiato"):
            editor.apply_ocr(output, revision)
    finally:
        editor.close()
    assert scan_pdf.read_bytes() == original


@pytest.mark.parametrize("language", ["ita", "eng"])
def test_language_and_current_page_selection(scan_pdf, tessdata, tmp_path, language):
    result = recognize_pdf(scan_pdf, tmp_path / "ocr.pdf", language=language, pages=[0], dpi=150, cache_dir=tessdata)
    assert result["recognized"] == [1] and result["skipped"] == [] and result["empty"] == []


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_cropped_userunit_text_bounds_match_scan(scan_pdf, tessdata, tmp_path, rotation):
    # Rotate the scan pixels oppositely so the displayed words remain upright.
    with pymupdf.open(scan_pdf) as source:
        pixels = source[0].get_pixmap(matrix=pymupdf.Matrix(2, 2).prerotate(-rotation), annots=False)
    intermediate = tmp_path / "intermediate.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=pixels.width / 2 + 60, height=pixels.height / 2 + 80)
        page.insert_image(pymupdf.Rect(30, 40, 30 + pixels.width / 2, 40 + pixels.height / 2), pixmap=pixels)
        page.set_cropbox(pymupdf.Rect(30, 40, 30 + pixels.width / 2, 40 + pixels.height / 2))
        page.set_rotation(rotation)
        doc.save(intermediate)
    source_path = tmp_path / "geometry.pdf"
    with PdfReader(intermediate) as reader:
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.pages[0][NameObject("/UserUnit")] = NumberObject(2)
        writer.write(source_path)
    output = tmp_path / "recognized.pdf"
    result = recognize_pdf(source_path, output, cache_dir=tessdata)
    assert result["recognized"] == [1]
    with pymupdf.open(source_path) as original, pymupdf.open(output) as changed:
        page = changed[0]
        assert "Documento italiano" in page.get_text()
        assert page.get_pixmap().samples == original[0].get_pixmap().samples
        # PyMuPDF's rotation_matrix translations omit UserUnit, whereas text
        # bounds include it. Scale translations to displayed physical points.
        rotation_matrix = pymupdf.Matrix(page.rotation_matrix)
        rotation_matrix.e *= 2
        rotation_matrix.f *= 2
        bounds = page.search_for("Documento")[0] * rotation_matrix
        assert bounds.x0 == pytest.approx(90, abs=8)
        assert 165 <= bounds.y0 <= 205
        assert bounds.x1 < page.rect.width
    with PdfReader(output) as pdf, PdfReader(source_path) as original:
        assert tuple(pdf.pages[0].cropbox) == tuple(original.pages[0].cropbox)
        assert pdf.pages[0].rotation == rotation
        assert pdf.pages[0]["/UserUnit"] == 2


def test_invalid_options_encryption_and_failure_keep_output(scan_pdf, tessdata, tmp_path, monkeypatch):
    import ape_pdf_studio.core.ocr as module
    output = tmp_path / "output.pdf"
    output.write_bytes(b"existing output")
    for options in ({"language": "deu"}, {"dpi": 600}, {"pages": []}, {"pages": [0, 0]}, {"pages": [3]}):
        with pytest.raises(PDFOperationError):
            recognize_pdf(scan_pdf, output, cache_dir=tessdata, **options)
        assert output.read_bytes() == b"existing output"
    encrypted = tmp_path / "protected.pdf"
    with pymupdf.open(scan_pdf) as doc:
        doc.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")
    with pytest.raises(PDFOperationError, match="protetti"):
        recognize_pdf(encrypted, output, cache_dir=tessdata)
    with pytest.raises(PDFOperationError):
        recognize_pdf(scan_pdf, scan_pdf, cache_dir=tessdata)
    original_merge = module._merge_text_layer
    calls = []
    def fail(*args):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("engine failed")
        return original_merge(*args)
    monkeypatch.setattr(module, "_merge_text_layer", fail)
    with pytest.raises(PDFOperationError, match="engine failed"):
        recognize_pdf(scan_pdf, output, cache_dir=tessdata)
    assert output.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".apex_export_*"))


def test_model_download_is_verified_cached_and_reusable_offline(tmp_path, monkeypatch):
    import hashlib
    import ape_pdf_studio.core.ocr as module
    data = {"ita": b"italian model", "eng": b"english model"}
    for code, content in data.items():
        monkeypatch.setitem(module.MODEL_HASHES, code, hashlib.sha256(content).hexdigest())
    downloads = []
    def fetch(url, **_kwargs):
        code = url.rsplit("/", 1)[-1].split(".")[0]
        downloads.append(code)
        return io.BytesIO(data[code])
    monkeypatch.setattr(module, "urlopen", fetch)
    assert prepare_tessdata("ita+eng", tmp_path, download=True) == tmp_path
    assert downloads == ["ita", "eng"]
    assert prepare_tessdata("ita+eng", tmp_path, download=False) == tmp_path
    assert downloads == ["ita", "eng"]
    (tmp_path / "ita.traineddata").write_bytes(b"corrupted cached model")
    prepare_tessdata("ita+eng", tmp_path, download=True)
    assert downloads == ["ita", "eng", "ita"]
    assert (tmp_path / "ita.traineddata").read_bytes() == data["ita"]
    assert not list(tmp_path.glob(".ocr_*"))


def test_models_missing_offline_or_corrupted_download(tmp_path, monkeypatch):
    import ape_pdf_studio.core.ocr as module
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"bad download"))
    # Test against a fake language entry to avoid finding system installations.
    monkeypatch.setitem(module.LANGUAGES, "test", "Test")
    monkeypatch.setitem(module.MODEL_HASHES, "test", "expected hash")
    with pytest.raises(PDFOperationError, match="mancanti"):
        prepare_tessdata("test", tmp_path, download=False)
    with pytest.raises(PDFOperationError, match="integrità"):
        prepare_tessdata("test", tmp_path, download=True)
    assert not list(tmp_path.iterdir())


def test_no_text_pages_need_no_download(tmp_path):
    source = tmp_path / "text.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), "Already searchable")
        doc.save(source)
    result = recognize_pdf(source, tmp_path / "ocr.pdf", cache_dir=tmp_path / "missing")
    assert result["recognized"] == [] and result["skipped"] == [1]
    assert not (tmp_path / "missing").exists()
