"""Local form suggestion worker. No PDF is modified by analysis."""
import json
from pathlib import Path
import sys


def main():
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ape_pdf_studio.core.security_worker import require_memory_limit
    require_memory_limit()
    from ape_pdf_studio.core.form_detector import detect_fields
    from ape_pdf_studio.core.security import MAX_FILE_BYTES
    request = json.loads(sys.stdin.buffer.read(65536))
    source = Path(request["path"])
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Input too large")
    return detect_fields(source, request["pages"], cache_dir=request.get("cache_dir"), use_ocr=request.get("use_ocr", True))


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=True), flush=True)
    except Exception:
        sys.exit(1)
