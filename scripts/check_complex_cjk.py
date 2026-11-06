"""Diagnose complex CJK reading and editing, retaining a report and page images.

Run with .venv-build/Scripts/python.exe scripts/check_complex_cjk.py.
The original is never changed. A failed feature check returns exit code 1;
in particular, stored Unicode form values are checked separately from their
visible appearance in fields whose font is Helvetica.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.ui.remote_pdf import RemoteDocument


def diagnose(source, output):
    app = QApplication.instance() or QApplication([])
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(source=str(source), source_sha256=original, bytes=source.stat().st_size,
                  checks={}, timings_seconds={}, limitations=[])

    def measured(name, action):
        start = time.monotonic()
        value = action()
        report["timings_seconds"][name] = round(time.monotonic() - start, 3)
        return value

    def save_image(image, name):
        # PDFium returns transparent paper; composite on white for visual QA.
        paper = QImage(image.size(), QImage.Format.Format_RGB32)
        paper.fill(Qt.GlobalColor.white)
        painter = QPainter(paper)
        painter.drawImage(0, 0, image)
        painter.end()
        if not paper.save(str(output / name)):
            raise RuntimeError("Cannot save the rendered page")

    editor = measured("open", lambda: BrokerEditor(source))
    remote = RemoteDocument(editor)
    try:
        measured("load_pdfium", lambda: remote.load(editor.path))
        report["pages"] = remote.pageCount()
        report["security_findings"] = list(editor.security_report.findings)
        report["checks"]["eight_pages"] = remote.pageCount() == 8
        report["checks"]["eight_bookmarks"] = len(remote.bookmarks) == 8
        texts = []
        for page in range(remote.pageCount()):
            size = remote.pagePointSize(page)
            factor = min(1100 / size.width(), 1300 / size.height())
            image = measured(f"render_page_{page+1}", lambda: remote.render(page,
                QSize(round(size.width() * factor), round(size.height() * factor))))
            save_image(image, f"page-{page+1}.png")
            texts.append(remote.getAllText(page).text())
        report["checks"]["all_pages_rendered"] = True
        for query in ("中文测试", "日本語テスト", "こんにちは世界"):
            result = measured("search_" + query, lambda: editor.call("qt_search", editor.path, query))
            report["checks"]["search_" + query] = bool(result["results"])
        selection = remote.getSelectionAtIndex(0, texts[0].index("中文测试"), len("中文测试"))
        selection.copyToClipboard()
        report["checks"]["copy_cjk"] = app.clipboard().text() == "中文测试"
        report["checks"]["raster_page_has_no_text"] = not texts[6].strip()
        report["checks"]["unicode_note"] = any("日本語" in note["text"] for note in editor.notes().get(0, []))
        metadata = editor.metadata().values
        before_edit = editor.path
        editor.add_note(1, "新增批注 / 新しい注釈", (50, 435))
        report["checks"]["undo"] = editor.undo() and editor.path == before_edit
        report["checks"]["redo"] = editor.redo()
        expected_values = {"customer": "山田太郎", "city": "北京市"}
        editor.fill_form(expected_values, editor.path)
        values = {field.name: field.value for field in editor.interactions()[1]}
        report["checks"]["cjk_form_values_stored"] = all(values[key] == value for key, value in expected_values.items())
        remote.load(editor.path)
        appearance = remote.getAllText(4).text()
        report["checks"]["cjk_form_values_visible"] = all(value in appearance for value in expected_values.values())
        save_image(remote.render(4, QSize(800, 1132)), "form-filled.png")
        if not report["checks"]["cjk_form_values_visible"]:
            report["limitations"].append("CJK values are stored, but Helvetica form fields lack a CJK appearance font fallback.")
        report["limitations"].append("The raster-only CJK page requires Chinese/Japanese OCR models, which are not currently offered by the app.")
        saved = measured("save", lambda: editor.save_as(output / "saved-cjk.pdf"))
        reopened = BrokerEditor(saved)
        try:
            report["checks"]["metadata_preserved"] = reopened.metadata().values == metadata
            values = {field.name: field.value for field in reopened.interactions()[1]}
            report["checks"]["cjk_form_values_reopened"] = all(values[key] == value for key, value in expected_values.items())
        finally:
            reopened.close()
    finally:
        remote.close()
        editor.close()
        report["checks"]["original_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == original
        report["passed"] = all(report["checks"].values())
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=ROOT / "examples/complex_cjk.pdf")
    parser.add_argument("--output", type=Path, default=ROOT / "build/cjk-report")
    args = parser.parse_args()
    report = diagnose(args.pdf, args.output)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
