"""Render the editable SVG to PNG and a Windows ICO without extra dependencies."""
from __future__ import annotations

import os
from pathlib import Path
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render(renderer, size):
    # Supersample the small frames so thin edges remain smooth in Explorer.
    resolution = size * 4 if size <= 64 else size
    image = QImage(resolution, resolution, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    if resolution != size:
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def png_data(image):
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError("Unable to encode application icon")
    buffer.close()
    return bytes(data)


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = Path(__file__).resolve().parents[1] / "src/ape_pdf_studio/assets"
    renderer = QSvgRenderer(str(assets / "app-icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid application icon SVG")
    (assets / "app-icon.png").write_bytes(png_data(render(renderer, 512)))
    frames = [png_data(render(renderer, size)) for size in ICON_SIZES]
    offset = 6 + 16 * len(frames)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    for size, frame in zip(ICON_SIZES, frames):
        # In an ICO directory, zero represents a dimension of 256 pixels.
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                     1, 32, len(frame), offset))
        offset += len(frame)
    (assets / "app-icon.ico").write_bytes(bytes(directory) + b"".join(frames))
    print(f"Application icon generated: {assets} ({len(frames)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
