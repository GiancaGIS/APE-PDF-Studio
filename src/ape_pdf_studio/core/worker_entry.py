"""Entry point for the bundled helper; keep Qt out of worker startup."""
from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("Indica un processo: security, vector, docx, ocr oppure shell.", file=sys.stderr)
        return 2
    kind = sys.argv.pop(1)
    if kind == "shell":
        from .shell_worker import main as run
        return run()
    if kind == "security":
        from .security_worker import main as run
        print(json.dumps(run(), ensure_ascii=True), flush=True)
        return 0
    if kind == "vector":
        from .vector_worker import main as run
        try:
            run()
            return 0
        except Exception:
            return 1
    if kind == "docx":
        from .docx_export import main as run
        return run()
    if kind == "ocr":
        from .ocr import main as run
        return run()
    print(f"Processo sconosciuto: {kind}", file=sys.stderr)
    return 2
