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
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "APEPDFStudio.exe"))
    assert worker_command(kind, "path with spaces") == [str(tmp_path / "APEPDFWorker.exe"), kind, "path with spaces"]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unix_bundle_reenters_its_native_executable_for_worker(monkeypatch, tmp_path, platform):
    executable = str(tmp_path / "APE PDF Studio" / "APEPDFStudio")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", executable)
    assert worker_command("rpc", "path with spaces") == [executable, "--ape-worker", "rpc", "path with spaces"]


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
    launcher = Path(__file__).resolve().parents[1] / "build_tools/launchers/worker_launcher.py"
    result = subprocess.run([sys.executable, str(launcher), "security"],
                            input=json.dumps({"path": str(source)}).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["status"] == "checked"


@pytest.mark.skipif(sys.platform == "win32", reason="The Windows worker already stages revisions inside AppContainer")
def test_unix_broker_keeps_worker_revisions_inside_its_workspace(tmp_path):
    from ape_pdf_studio.core.broker_editor import BrokerEditor

    source = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(200, 300)
    writer.write(source)
    original = source.read_bytes()
    editor = BrokerEditor(source)
    try:
        editor.rotate(0)
        assert editor.path.is_relative_to(editor._root)
        assert editor.can_undo and source.read_bytes() == original
    finally:
        editor.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux Qt PDF address-space reservation")
def test_linux_renderer_loads_pdf_with_bounded_writable_mappings(tmp_path, monkeypatch):
    from ape_pdf_studio.core.broker_editor import BrokerEditor

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    source = tmp_path / "render.pdf"
    writer = PdfWriter()
    writer.add_blank_page(200, 300)
    writer.write(source)
    editor = BrokerEditor(source)
    try:
        raster = editor.path.parent / "render.rgba"
        editor.call("qt_render", editor.path, 0, 20, 30, {}, raster)
        assert raster.stat().st_size == 20 * 30 * 4
    finally:
        editor.close()
    result = subprocess.run(
        [sys.executable, "-c", "import errno,mmap; "
         "from ape_pdf_studio.core.security_worker import require_memory_limit; "
         "require_memory_limit(renderer=True); "
         "\ntry: mmap.mmap(-1, 800*1024**2, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)"
         "\nexcept OSError as exc: assert exc.errno == errno.ENOMEM"
         "\nelse: raise AssertionError('Writable allocation exceeded limit')"],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
