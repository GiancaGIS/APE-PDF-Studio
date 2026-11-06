"""Capture real Qt screenshots with sample PDFs and isolated preferences.

Run with .venv-build/Scripts/python scripts/capture_readme_images.py on Windows
after preparing the normal AppContainer runtime. No installed app is contacted,
no network checks are scheduled, and the sample PDFs are never saved over.
"""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_SCALE_FACTOR'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QRect, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ape_pdf_studio.ui.main_window import MainWindow


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError('Screenshot timed out while waiting for the interface')
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        loop.exec()


def settle(window, tab):
    view = tab.viewer
    view.viewport().update()
    QApplication.processEvents()
    wait_until(lambda: tab.page in view.vectors.cache or tab.page in view.vectors.failed)
    wait_until(lambda: not view._failed and all(tile in view._images for tile in view._wanted))
    wait_until(lambda: bool(tab.thumbs.cache))
    window._update_state()
    QApplication.processEvents()
    view.viewport().repaint()


def signature_image(destination):
    """A synthetic pen stroke, unrelated to any real person's signature."""
    image = QImage(600, 180, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#164c9d'), 6, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath(QPointF(35, 130))
    path.cubicTo(130, 0, 150, 10, 90, 120)
    path.cubicTo(60, 170, 110, 70, 125, 90)
    path.cubicTo(160, 160, 150, 30, 190, 70)
    path.cubicTo(200, 115, 175, 135, 165, 100)
    path.cubicTo(210, 80, 220, 100, 240, 105)
    path.cubicTo(265, 110, 290, 0, 310, 20)
    path.cubicTo(330, 50, 235, 170, 330, 110)
    path.cubicTo(360, 100, 350, 60, 355, 90)
    path.cubicTo(360, 135, 390, 70, 410, 100)
    path.cubicTo(440, 135, 440, 50, 465, 95)
    path.cubicTo(480, 130, 500, 65, 555, 90)
    painter.drawPath(path)
    painter.drawLine(QPointF(160, 140), QPointF(570, 130))
    painter.end()
    if not image.save(str(destination)):
        raise RuntimeError('Cannot create synthetic signature')


def capture(output, work):
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(work / 'settings'))
    preferences = QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, 'APEX', 'PDF Studio')
    preferences.setValue('ui/language', 'en')
    preferences.sync()
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        font = Path('C:/Windows/Fonts') / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    examples = [ROOT / 'examples' / name for name in (
        'sample_document.pdf', 'modulo_compilabile.pdf', 'riflusso_paragrafi.pdf')]
    original_hashes = {path: hashlib.sha256(path.read_bytes()).digest() for path in examples}
    errors = []
    window = MainWindow()
    window.resize(1280, 900)
    window.show()
    screenshots = []

    def save(widget, name, rect=None):
        app.processEvents()
        image = widget.grab(rect) if rect is not None else widget.grab()
        if image.isNull() or not image.save(str(output / name)):
            raise RuntimeError('Cannot capture ' + name)
        screenshots.append(name)
        print(f'{name}: {image.width()}x{image.height()}', flush=True)

    try:
        # Report dialog errors instead of leaving an unattended capture blocked.
        with patch.object(QMessageBox, 'critical', lambda *a, **k: errors.append(a[2])), \
             patch.object(QMessageBox, 'warning', lambda *a, **k: errors.append(a[2])):
            tabs = []
            for path in examples:
                window.open_pdf(path)
                if errors or window.current is None:
                    raise RuntimeError('Cannot open sample: ' + '; '.join(errors))
                tabs.append(window.current)
            sample, form, paragraph = tabs
            window.tabs.setCurrentWidget(sample)
            sample.set_zoom(85)
            sample.viewer.horizontalScrollBar().setValue(0)
            sample.viewer.verticalScrollBar().setValue(0)
            settle(window, sample)
            save(window, 'main_window.png')
            bar = window.tabs.tabBar()
            bottom = bar.mapTo(window, QPoint(0, bar.height())).y()
            save(window, 'main_tools.png', QRect(0, 0, window.width(), bottom))
            for button, action, name in (
                    (window.annotate_button, window.a_signature, 'menu_1.png'),
                    (window.pages_button, window.a_split, 'menu_2.png')):
                menu = button.menu()
                menu.popup(button.mapToGlobal(QPoint(0, button.height())))
                menu.setActiveAction(action)
                app.processEvents()
                save(menu, name)
                menu.hide()

            window.tabs.setCurrentWidget(form)
            window.resize(1280, 1060)
            form.set_zoom(90)
            form.apply_edit('fill_form', {
                'nome': 'Alex Morgan', 'email': 'alex@example.com',
                'categoria': 'assistenza', 'contatto': '/Email', 'consenso': '/Yes'},
                str(form.editor.path))
            settle(window, form)
            assert form.inline_form.start('note')
            form.inline_form.control.setPlainText('I can fill this form directly on the PDF page.\nTab moves to the next field.')
            app.processEvents()
            save(window, 'form_filling.png')
            form.inline_form.cancel()

            window.resize(1280, 900)
            window.tabs.setCurrentWidget(sample)
            sample.fit_page()
            png = work / 'synthetic-signature.png'
            signature_image(png)
            with patch.object(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(png), '')):
                assert sample.signature.choose(), errors
            size = sample.pdf.pagePointSize(0)
            width = min(210, size.width()*.4)
            sample.signature.rect = QRectF(size.width()*.5, size.height()*.72, width, width*.3)
            sample.signature._clamp()
            sample.signature._sync_width()
            settle(window, sample)
            save(window, 'image_signature.png')
            sample.signature.cancel()

            window.tabs.setCurrentWidget(paragraph)
            paragraph.set_zoom(95)
            paragraph.viewer.horizontalScrollBar().setValue(0)
            paragraph.viewer.verticalScrollBar().setValue(0)
            window.edit_text_at(paragraph, 0, QPointF(65, 140))
            inline = paragraph.inline_text
            wait_until(lambda: inline.control is not None)
            inline.control.setText(inline.target.text + ' Una nuova frase viene distribuita automaticamente sulle righe successive.')
            wait_until(lambda: inline._layout_key() in inline._font_cache and not inline._font_running)
            if inline.feedback.isVisible():
                raise RuntimeError(inline.feedback.text())
            settle(window, paragraph)
            save(window, 'paragraph_reflow.png')
            inline.cancel()
            if errors:
                raise RuntimeError('; '.join(errors))
    finally:
        for index in reversed(range(window.tabs.count())):
            tab = window.tabs.widget(index)
            tab.close_document()
            window.tabs.removeTab(index)
            tab.deleteLater()
        window.close()
        app.processEvents()
        for path, expected in original_hashes.items():
            if hashlib.sha256(path.read_bytes()).digest() != expected:
                raise RuntimeError('Sample PDF changed: ' + path.name)
    print(f'{len(screenshots)} screenshots captured; original sample hashes unchanged.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'images')
    arguments = parser.parse_args()
    output = arguments.output_dir.absolute()
    output.mkdir(parents=True, exist_ok=True)
    work_root = ROOT / 'build/readme-capture'
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='session-', dir=work_root) as work:
        capture(output, Path(work))


if __name__ == '__main__':
    main()
