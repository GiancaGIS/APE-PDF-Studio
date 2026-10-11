"""Bounded bitmap/metadata protocols for the Windows shell extension.

No filenames, temporary files, GUI, downloads or PDF actions are used here.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import struct
import sys

REQUEST = struct.Struct("<4sIII")
RESPONSE = struct.Struct("<4sIIII")
MAX_EDGE = 2048


def render(data: bytes, edge: int, index: int) -> bytes:
    from pypdf import PdfReader
    from ape_pdf_studio.core.security import (
        BLOCKED, MAX_FILE_BYTES, MAX_PAGES, configure_parser, inspect_reader,
    )
    if not 1 <= edge <= MAX_EDGE or not 0 <= index < MAX_PAGES:
        raise ValueError("Invalid page or bitmap size")
    if not data or len(data) > MAX_FILE_BYTES or b"%PDF-" not in data[:1024]:
        raise ValueError("Invalid PDF stream")
    configure_parser()
    with PdfReader(io.BytesIO(data)) as reader:
        if reader.is_encrypted or set(inspect_reader(reader)) & BLOCKED:
            raise ValueError("PDF cannot be previewed")
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if document.needs_pass or not 1 <= document.page_count <= MAX_PAGES or index >= document.page_count:
            raise ValueError("PDF cannot be previewed")
        page = document[index]
        longest = max(page.rect.width, page.rect.height)
        if not 0 < longest < float("inf"):
            raise ValueError("Invalid page geometry")
        # Round down slightly to keep MuPDF's integer raster inside the bound.
        scale = max(edge - 1, 1) / longest
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
        if not 1 <= pixels.width <= edge or not 1 <= pixels.height <= edge:
            raise ValueError("Invalid raster dimensions")
        rgb = pixels.samples
        bgra = bytearray(pixels.width * pixels.height * 4)
        bgra[0::4], bgra[1::4], bgra[2::4] = rgb[2::3], rgb[1::3], rgb[0::3]
        bgra[3::4] = b"\xff" * (pixels.width * pixels.height)
        return RESPONSE.pack(b"APE1", pixels.width, pixels.height, document.page_count, index) + bgra


def main() -> int:
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ape_pdf_studio.core.security_worker import limit_memory
    from ape_pdf_studio.core.security import MAX_FILE_BYTES
    limit_memory()
    try:
        magic, edge, index, length = REQUEST.unpack(sys.stdin.buffer.read(REQUEST.size))
        if magic not in (b"APR1", b"APQ1") or not 0 < length <= MAX_FILE_BYTES:
            return 1
        if magic == b"APQ1" and (edge > 3 or index != 0):
            return 1
        data = sys.stdin.buffer.read(length)
        if len(data) != length:
            return 1
        if magic == b"APQ1":
            from ape_pdf_studio.core.shell_metadata import metadata_reply
            reply = metadata_reply(data, edge)
        else:
            reply = render(data, edge, index)
        sys.stdout.buffer.write(reply)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        if os.environ.get("APE_SHELL_DIAGNOSTICS"):
            import traceback
            traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
