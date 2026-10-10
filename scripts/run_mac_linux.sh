#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import PySide6, pypdf, pdf2docx, docx, cryptography, ape_pdf_studio; from pypdf.xmp import XmpInformation; from ape_pdf_studio.core.security import configure_parser; configure_parser(); assert tuple(map(int,pypdf.__version__.split(".")[:2])) >= (6,19); assert XmpInformation.dc_title.fset is not None' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -e .
fi
exec .venv/bin/python -m ape_pdf_studio "$@"
