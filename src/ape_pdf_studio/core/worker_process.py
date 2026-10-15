"""Worker commands for development Python and the standalone Windows bundle."""
from __future__ import annotations

from pathlib import Path
import sys

WORKERS = {"security": "security_worker", "vector": "vector_worker", "docx": "docx_export", "ocr": "ocr", "shell": "shell_worker", "form_detect": "form_detect_worker"}


def worker_command(kind: str, *arguments: str) -> list[str]:
    if kind not in WORKERS:
        raise ValueError(f"Unknown worker: {kind}")
    if getattr(sys, "frozen", False):
        # The console helper provides real pipes even though the GUI is windowed.
        executable = Path(sys.executable).with_name("APEPDFWorker.exe")
        return [str(executable), kind, *map(str, arguments)]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    if kind in ("security", "vector", "shell", "form_detect"):
        return [str(executable), "-I", str(Path(__file__).with_name(WORKERS[kind] + ".py")), *map(str, arguments)]
    return [str(executable), "-u", "-m", "ape_pdf_studio.core." + WORKERS[kind], *map(str, arguments)]
