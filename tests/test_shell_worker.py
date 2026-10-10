"""Actual isolated shell rendering, protocol bounds and untrusted PDFs."""
import io
import os
from pathlib import Path
import struct
import subprocess

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject

from ape_pdf_studio.core.shell_worker import REQUEST, RESPONSE, render
from ape_pdf_studio.core.worker_process import worker_command


@pytest.fixture
def pages():
    with pymupdf.open() as pdf:
        for color in ((1, 0, 0), (0, 0, 1)):
            page = pdf.new_page(width=300, height=500)
            page.draw_rect(page.rect, color=color, fill=color)
        return pdf.tobytes()


@pytest.mark.parametrize("index,expected", ((0, b"\x00\x00\xff\xff"), (1, b"\xff\x00\x00\xff")))
def test_isolated_worker_protocol_and_real_page_colors(pages, tmp_path, index, expected):
    (tmp_path / "pypdf.py").write_text("raise RuntimeError('untrusted import')", encoding="utf-8")
    response = subprocess.run(worker_command("shell"), input=REQUEST.pack(b"APR1", 256, index, len(pages)) + pages,
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)}, capture_output=True, timeout=20)
    assert response.returncode == 0, response.stderr
    magic, width, height, count, selected = RESPONSE.unpack(response.stdout[:RESPONSE.size])
    assert magic == b"APE1" and count == 2 and selected == index
    assert 0 < width <= 256 and 0 < height <= 256
    assert width / height == pytest.approx(300 / 500, abs=0.01)
    pixels = response.stdout[RESPONSE.size:]
    assert len(pixels) == width * height * 4
    center = (height // 2 * width + width // 2) * 4
    assert pixels[center:center + 4] == expected


@pytest.mark.parametrize("edge,index", ((0, 0), (2049, 0), (256, -1), (256, 2)))
def test_bad_bounds_rejected(pages, edge, index):
    with pytest.raises(ValueError):
        render(pages, edge, index)


def test_rotation_and_crop_match_page_geometry(pages):
    with pymupdf.open(stream=pages, filetype="pdf") as pdf:
        pdf[0].set_cropbox(pymupdf.Rect(0, 0, 200, 400))
        pdf[0].set_rotation(90)
        data = pdf.tobytes()
    _, width, height, _, _ = RESPONSE.unpack(render(data, 256, 0)[:RESPONSE.size])
    assert width / height == pytest.approx(2, abs=0.02)


@pytest.mark.parametrize("kind", ("broken", "encrypted", "active"))
def test_untrusted_documents_have_no_bitmap(pages, kind):
    if kind == "broken":
        data = b"%PDF-1.7\nbroken"
    else:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pages)))
        if kind == "encrypted":
            writer.encrypt("secret")
        else:
            writer.root_object[NameObject("/OpenAction")] = writer._add_object(DictionaryObject({NameObject("/S"): NameObject("/Launch")}))
        stream = io.BytesIO(); writer.write(stream); data = stream.getvalue()
    result = subprocess.run(worker_command("shell"), input=REQUEST.pack(b"APR1", 256, 0, len(data)) + data,
        capture_output=True, timeout=20)
    assert result.returncode != 0 and result.stdout == b""


@pytest.mark.parametrize("header", (b"", REQUEST.pack(b"NOPE", 256, 0, 1), REQUEST.pack(b"APR1", 256, 0, 0),
    REQUEST.pack(b"APR1", 256, 0, 268435457), REQUEST.pack(b"APR1", 256, 0, 100)))
def test_invalid_or_truncated_requests_fail_closed(header):
    result = subprocess.run(worker_command("shell"), input=header, capture_output=True, timeout=20)
    assert result.returncode != 0 and result.stdout == b""
