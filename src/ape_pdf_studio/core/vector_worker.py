"""Convert one already preflighted private revision, with resource limits."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def main():
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ape_pdf_studio.core.security_worker import require_memory_limit
    from ape_pdf_studio.core.pdf_runtime import open_static_pdf
    require_memory_limit()
    from ape_pdf_studio.core.vector_page import prepare_svg
    from ape_pdf_studio.core.vector_protocol import from_svg
    import pymupdf
    request = json.loads(sys.stdin.buffer.read(65536))
    # Release filesystem handles before conversion; undo can remove revisions.
    with open_static_pdf(stream=Path(request["path"]).read_bytes(), filetype="pdf") as document:
        if document.needs_pass and not document.authenticate(request.get("password") or ""):
            raise ValueError("Password richiesta")
        page = document[request["page"]]
        # SVG widgets/form appearances are not sufficiently portable.
        if next(page.widgets(), None) is not None:
            raise ValueError("Campi modulo")
        svg, layers = prepare_svg(page.get_svg_image(text_as_path=True).encode("utf-8"))
        drawing = from_svg(svg,layers)
    data=json.dumps(drawing,allow_nan=False).encode("utf-8")
    if len(data)>12*1024*1024:raise ValueError("Drawing exceeds budget")
    Path(request["output"]).write_bytes(data)
    Path(request["layers"]).write_text(json.dumps([]), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Conversion failure is a compatibility fallback, never a partial page.
        sys.exit(1)
