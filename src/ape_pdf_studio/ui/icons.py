"""Consistent vector icons without external assets."""
from functools import lru_cache
from importlib.resources import files

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from .theme import theme_color

@lru_cache(maxsize=1)
def application_icon():
    renderer = QSvgRenderer(QByteArray(files("ape_pdf_studio").joinpath("assets/app-icon.svg").read_bytes()))
    if not renderer.isValid():
        raise RuntimeError("Invalid application icon SVG")
    result = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        result.addPixmap(pixmap)
    return result


PATHS = {
    'draw': [[(3,21),(5,14),(17,2),(22,7),(10,19),(3,21)],[(14,5),(19,10)],[(3,23),(9,22),(15,23),(21,20)]],
    'watermark': [[(3,3),(21,3),(21,21),(3,21),(3,3)],[(5,16),(10,7),(13,16),(18,7)]],
    'edit_text': [[(4,5),(18,5)],[(11,5),(11,19)],[(7,19),(15,19)],[(18,11),(21,14),(15,20),(12,21),(13,18),(18,11)]],
    'split': [[(8,3),(16,3),(16,8)],[(12,8),(12,13)],[(12,13),(5,18),(5,22)],[(12,13),(19,18),(19,22)],[(3,20),(5,22),(7,20)],[(17,20),(19,22),(21,20)]],
    'print': [[(7,8),(7,3),(17,3),(17,8)],[(7,17),(3,17),(3,8),(21,8),(21,17),(17,17)],[(7,14),(17,14),(17,22),(7,22),(7,14)],[(17,11),(18,11)]],
    'close': [[(6,6),(18,18)],[(6,18),(18,6)]],
    'open': [[(3,7),(9,7),(11,9),(21,9),(18,20),(3,20),(3,7)],[(3,12),(20,12)]],
    'save': [[(3,3),(18,3),(21,6),(21,21),(3,21),(3,3)],[(7,3),(7,9),(17,9),(17,3)],[(7,21),(7,14),(17,14),(17,21)]],
    'undo': [[(8,5),(3,10),(8,15)],[(3,10),(14,10),(19,13),(20,18)]],
    'redo': [[(16,5),(21,10),(16,15)],[(21,10),(10,10),(5,13),(4,18)]],
    'rotate': [[(4,10),(5,6),(9,3),(15,3),(20,7),(21,13),(18,19),(11,21),(6,18)],[(3,4),(3,10),(9,10)]],
    'delete': [[(4,6),(20,6)],[(9,6),(9,3),(15,3),(15,6)],[(6,6),(7,21),(17,21),(18,6)],[(10,10),(10,17)],[(14,10),(14,17)]],
    'note': [[(4,3),(20,3),(20,16),(15,21),(4,21),(4,3)],[(15,21),(15,16),(20,16)],[(8,8),(16,8)],[(8,12),(14,12)]],
    'highlight': [[(4,16),(14,3),(21,10),(10,21),(4,21),(4,16)],[(8,12),(15,19)],[(3,23),(21,23)]],
    'underline': [[(6,3),(6,12),(8,16),(12,18),(16,16),(18,12),(18,3)],[(4,22),(20,22)]],
    'copy': [[(8,7),(21,7),(21,21),(8,21),(8,7)],[(16,7),(16,3),(3,3),(3,16),(8,16)]],
    'insert': [[(6,3),(18,3),(18,21),(6,21),(6,3)],[(9,12),(15,12)],[(12,9),(12,15)]],
    'extract': [[(4,3),(14,3),(14,8)],[(4,3),(4,21),(14,21),(14,16)],[(10,12),(22,12)],[(18,8),(22,12),(18,16)]],
    'prev': [[(15,5),(8,12),(15,19)]], 'next': [[(9,5),(16,12),(9,19)]],
    'up': [[(5,15),(12,8),(19,15)]], 'down': [[(5,9),(12,16),(19,9)]],
    'minus': [[(5,12),(19,12)]], 'plus': [[(5,12),(19,12)],[(12,5),(12,19)]],
    'search': [[(15,15),(21,21)]],
    'sidebar': [[(3,4),(21,4),(21,20),(3,20),(3,4)],[(9,4),(9,20)]],
    'width': [[(3,7),(3,17)],[(21,7),(21,17)],[(3,12),(21,12)],[(7,8),(3,12),(7,16)],[(17,8),(21,12),(17,16)]],
    'page': [[(5,3),(19,3),(19,21),(5,21),(5,3)]],
    'info': [[(12,10),(12,17)],[(12,7),(12,7.3)]],
}

def pen_cursor(color, device_pixel_ratio=1.0):
    """Draw at the screen's resolution; the nib is the actual drawing point."""
    pixmap = QPixmap(round(32 * device_pixel_ratio), round(32 * device_pixel_ratio))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    outline = QPainterPath(QPointF(4, 28))
    for point in ((7, 19), (23, 3), (29, 9), (13, 25)):
        outline.lineTo(*point)
    outline.closeSubpath()
    # A light halo and dark edge keep the pen visible over any page content.
    painter.setPen(QPen(QColor('white'), 3.5, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(color))
    painter.drawPath(outline)
    edge = QPen(QColor('#182537'), 1.3, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(edge)
    painter.drawPath(outline)
    nib = QPainterPath(QPointF(4, 28))
    nib.lineTo(7, 19)
    nib.lineTo(13, 25)
    nib.closeSubpath()
    painter.setBrush(QColor('white'))
    painter.drawPath(nib)
    painter.drawLine(QPointF(4, 28), QPointF(8, 24))
    painter.setPen(QPen(QColor('white'), 1.4))
    painter.drawLine(QPointF(20, 6), QPointF(26, 12))
    painter.end()
    return QCursor(pixmap, 4, 28)


def icon(name, color=None):
    color = color or theme_color("icon")
    pixmap = QPixmap(48,48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2,2)
    painter.setPen(QPen(QColor(color),1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    for points in PATHS.get(name,[]):
        for start,end in zip(points,points[1:]):
            painter.drawLine(QPointF(*start),QPointF(*end))
    if name == 'search':
        painter.drawEllipse(QRectF(3,3,13,13))
    elif name == 'info':
        painter.drawEllipse(QRectF(3,3,18,18))
    painter.end()
    return QIcon(pixmap)
