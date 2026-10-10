"""Verify the built EXEs from a temporary directory, without installing them."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import struct
import tempfile
import time

import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=root / "dist/APEPDFStudio")
    parser.add_argument("--tessdata", type=Path, help="Optional local ita/eng models for a real OCR check")
    parser.add_argument("--shell-host", type=Path, default=root / "build/windows/shell/APEPDFShellSmoke.exe",
                        help="Native registration-free COM test host")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    worker = bundle / "APEPDFWorker.exe"
    gui = bundle / "APEPDFStudio.exe"
    cli = bundle / "ape-pdf-cli.exe"
    if not all(executable.is_file() for executable in (worker, gui, cli)):
        raise RuntimeError("Eseguibili mancanti. Esegui prima la build PyInstaller.")
    models = args.tessdata
    if models is None:
        candidates = [Path(os.environ.get("TESSDATA_PREFIX", "")), root / ".dev-deps/tessdata"]
        models = next((folder for folder in candidates if all(
            (folder / f"{code}.traineddata").is_file() for code in ("ita", "eng"))), None)
    checks = []

    def passed(label):
        checks.append(label)
        print(f"[APE] OK: {label}", flush=True)

    from prepare_release import verify_legal
    verify_legal(bundle)
    passed("Licenze AGPL/Qt, avvisi upstream e inventario delle dipendenze")

    assets = bundle / "_internal/ape_pdf_studio/assets"
    for name in ("app-icon.svg", "app-icon.png", "app-icon.ico"):
        if not (assets / name).is_file():
            raise RuntimeError(f"Risorsa icona mancante: {name}")
    icon_data = (assets / "app-icon.ico").read_bytes()
    count = struct.unpack_from("<H", icon_data, 4)[0]
    frames = []
    for index in range(count):
        length, offset = struct.unpack_from("<II", icon_data, 6 + 16 * index + 8)
        frames.append(icon_data[offset:offset + length])
    for executable in (gui, worker, cli):
        binary = executable.read_bytes()
        if not all(frame in binary for frame in frames):
            raise RuntimeError(f"Icona personalizzata non incorporata: {executable.name}")
    passed("Icona applicazione e risorse incorporate")
    shell_dll = bundle / "APEPDFShell.dll"
    if not shell_dll.is_file() or not args.shell_host.is_file():
        raise RuntimeError("Shell extension o host di verifica mancanti. Esegui scripts/build_windows_shell.ps1 e ricrea il pacchetto.")

    (root / "build/windows").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle_smoke_", dir=root / "build/windows") as folder:
        work = Path(folder)
        source = work / "text.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=600)
            page.insert_text((40, 100), "Documento italiano per ricerca", fontsize=18)
            page.insert_text((40, 140), "English searchable document", fontsize=18)
            pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
            doc.save(source)

        def run(kind, arguments=(), request=None, language=None):
            environment = {**os.environ, "PYTHONPATH": str(work)}
            if language is not None:
                environment["APE_UI_LANGUAGE"] = language
            result = subprocess.run(
                [str(worker), kind, *map(str, arguments)],
                input=json.dumps(request).encode() if request is not None else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=work, env=environment,
            )
            if result.returncode:
                raise RuntimeError(f"{kind}: {result.stderr.decode(errors='replace')}\n"
                                   f"{result.stdout.decode(errors='replace')}")
            return result.stdout

        # A frozen worker must load its own bundled code, regardless of CWD.
        (work / "pypdf.py").write_text("raise RuntimeError('Untrusted import')", encoding="utf-8")
        report = json.loads(run("security", request={"path": str(source)}))
        if report["status"] != "checked":
            raise RuntimeError(f"Controllo PDF fallito: {report}")
        unsafe = work / "unsafe.pdf"
        writer = PdfWriter()
        writer.add_blank_page(400, 600)
        writer.root_object[NameObject("/OpenAction")] = writer._add_object(DictionaryObject({
            NameObject("/S"): NameObject("/Launch")}))
        writer.write(unsafe)
        unsafe_report = json.loads(run("security", request={"path": str(unsafe)}))
        # The worker reports structural findings; check_pdf then rejects them.
        if not (unsafe_report.get("status") == "blocked" or (
                unsafe_report.get("status") == "checked" and "launch" in unsafe_report.get("findings", []))):
            raise RuntimeError("Il worker non rileva le azioni PDF bloccate.")
        passed("Controllo PDF e blocco contenuti attivi")

        def run_cli(*arguments, expected=0):
            result = subprocess.run([str(cli), *map(str, arguments)], cwd=work,
                env={**os.environ, "PYTHONPATH": str(work), "APE_UI_LANGUAGE": "en"},
                capture_output=True, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode != expected:
                raise RuntimeError(f"CLI: exit {result.returncode}, expected {expected}: "
                                   + result.stderr.decode(errors="replace"))
            return result

        help_result = run_cli("--help")
        if b"ape-pdf-cli" not in help_result.stdout or b"rotate" not in help_result.stdout:
            raise RuntimeError("Help CLI mancante sulla console.")
        cli_source = work / "CLI con spazi e accenti è.pdf"
        shutil.copyfile(source, cli_source)
        info = run_cli(cli_source, "info")
        if b"Pages: 1" not in info.stdout:
            raise RuntimeError("Info CLI mancanti sulla console.")
        rotated = work / "CLI rotated.pdf"
        run_cli(cli_source, "rotate", "1", "-o", rotated)
        with PdfReader(rotated) as reader:
            if reader.pages[0].rotation != 90:
                raise RuntimeError("Rotazione CLI fallita.")
        selection = work / "CLI selection.pdf"
        run_cli(rotated, "extract", "1", "-o", selection)
        with PdfReader(selection) as reader:
            if len(reader.pages) != 1 or reader.pages[0].rotation != 90:
                raise RuntimeError("Estrazione CLI fallita.")
        error = run_cli(work / "missing.pdf", "info", expected=2)
        if not error.stderr:
            raise RuntimeError("Errori CLI mancanti su stderr.")
        run_cli("unknown-command", expected=2)
        blocked = work / "CLI blocked output.pdf"
        run_cli(unsafe, "rotate", "1", "-o", blocked, expected=2)
        if blocked.exists():
            raise RuntimeError("La CLI non blocca i PDF con contenuti attivi.")
        passed("CLI autonoma: help, info, rotazione, estrazione, codici di uscita e blocco PDF attivi")

        # Real native COM calls without changing Explorer's registry settings.
        shell_source = work / "Anteprima con spazi e accenti è.pdf"
        with pymupdf.open() as document:
            for color in ((1, 0, 0), (0, 0, 1)):
                page = document.new_page(width=300, height=500)
                page.draw_rect(page.rect, color=color, fill=color)
            document.save(shell_source)

        def shell_check(document, mode=None, dll=shell_dll):
            command = [str(args.shell_host.resolve()), str(dll), str(document)]
            if mode:
                command.append(mode)
            result = subprocess.run(command, cwd=work, capture_output=True, timeout=65,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError("Verifica COM: " + result.stderr.decode(errors="replace"))

        shell_check(shell_source)
        shell_check(shell_source, "--low")
        shell_check(unsafe, "--reject")
        protected = work / "protected.pdf"
        protected_writer = PdfWriter(clone_from=PdfReader(source))
        protected_writer.encrypt("secret"); protected_writer.write(protected)
        shell_check(protected, "--reject")
        malformed = work / "malformed.pdf"
        malformed.write_bytes(b"%PDF-1.7\nbroken")
        shell_check(malformed, "--reject")
        # A helper that never reads stdin must be killed by the native watchdog.
        stalled = work / "stalled"
        stalled.mkdir()
        shutil.copyfile(shell_dll, stalled / shell_dll.name)
        shutil.copyfile(args.shell_host, stalled / "APEPDFWorker.exe")
        shell_check(shell_source, "--reject", dll=stalled / shell_dll.name)
        passed("COM miniature e anteprima: pagine, resize, annullamento, low integrity, PDF bloccati e timeout")

        broken = work / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\nbroken")
        for language, expected in (
            ("en", "unreadable file or incomplete analysis"),
            ("es", "archivo ilegible o análisis incompleto"),
            ("fr", "fichier illisible ou analyse incomplète"),
        ):
            localized = json.loads(run("security", request={"path": str(broken)}, language=language))
            if localized.get("status") != "blocked" or expected not in localized.get("reason", ""):
                raise RuntimeError(f"Traduzione worker mancante: {language}: {localized}")
        for language in ("it", "es", "en", "fr"):
            if not (bundle / "_internal/PySide6/translations" / f"qtbase_{language}.qm").is_file():
                raise RuntimeError(f"Traduzione Qt mancante: {language}")
        passed("Lingue inglese, spagnola e francese e traduzioni Qt")

        svg, layers = work / "page.svg", work / "layers.json"
        run("vector", request={"path": str(source), "page": 0, "output": str(svg), "layers": str(layers)})
        if b"<svg" not in svg.read_bytes() or not json.loads(layers.read_text(encoding="utf-8")):
            raise RuntimeError("Conversione vettoriale fallita.")
        passed("Rendering vettoriale")

        docx = work / "result.docx"
        output = run("docx", (source, docx))
        if not docx.is_file() or b'"result"' not in output:
            raise RuntimeError("Esportazione Word fallita.")
        passed("Esportazione Word")

        ocr_source = source
        if models is not None:
            ocr_source = work / "scan.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page(width=400, height=600)
                page.insert_image(page.rect, pixmap=pixels)
                doc.save(ocr_source)
        recognized = work / "recognized.pdf"
        request = work / "ocr.json"
        request.write_text(json.dumps({
            "source": str(ocr_source), "destination": str(recognized),
            "cache_dir": str(models.resolve() if models else work / "no_models"),
            "language": "ita+eng", "download": False,
        }), encoding="utf-8")
        run("ocr", (request,))
        with PdfReader(recognized) as doc:
            text = doc.pages[0].extract_text()
            if "Documento italiano" not in text or "English searchable" not in text:
                raise RuntimeError("Il testo non sopravvive al processo OCR.")
        passed("OCR bilingue reale" if models else "Processo OCR (pagina gia testuale; modelli assenti)")

        process = subprocess.Popen(
            [str(gui)], cwd=work, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            time.sleep(3)
            if process.poll() is not None:
                raise RuntimeError("Avvio GUI fallito: " + process.communicate()[1].decode(errors="replace"))
            passed("Avvio interfaccia senza console")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

    (root / "build/windows/smoke-report.json").write_text(
        json.dumps({"bundle": str(bundle), "checks": checks}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
