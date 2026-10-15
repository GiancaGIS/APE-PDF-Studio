"""Both release and source builds keep every background job isolated."""
from pathlib import Path
import subprocess
import sys
import json

import pytest
from pypdf import PdfWriter

from ape_pdf_studio.core.worker_process import worker_command


@pytest.mark.parametrize("kind", ["security", "vector", "docx", "ocr", "shell", "form_detect"])
def test_frozen_jobs_use_bundled_console_helper(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "APEPDFStudio.exe"))
    assert worker_command(kind, "path with spaces") == [str(tmp_path / "APEPDFWorker.exe"), kind, "path with spaces"]


@pytest.mark.parametrize("kind", ["security", "vector", "shell", "form_detect"])
def test_source_security_and_vectors_keep_isolated_interpreter(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    command = worker_command(kind)
    assert command[0] == str(tmp_path / "python.exe")
    assert command[1] == "-I"
    assert Path(command[2]).name == kind + "_worker.py"


def test_no_arbitrary_worker_modules():
    with pytest.raises(ValueError):
        worker_command("arbitrary_module")


def test_helper_entry_protocol_runs_security_from_source(tmp_path):
    source = tmp_path / "input.pdf"
    writer = PdfWriter()
    writer.add_blank_page(400, 600)
    writer.write(source)
    launcher = Path(__file__).resolve().parents[1] / "scripts/worker_launcher.py"
    result = subprocess.run([sys.executable, str(launcher), "security"],
                            input=json.dumps({"path": str(source)}).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["status"] == "checked"
