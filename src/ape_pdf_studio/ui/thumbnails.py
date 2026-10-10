"""Stable one-column thumbnails with visible-page asynchronous rendering."""
from ..core.localization import tr
from collections import OrderedDict
from math import ceil

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions, QPdfPageRenderer
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyledItemDelegate

from .theme import theme_color


class ThumbnailDelegate(QStyledItemDelegate):
    HEIGHT = 180

    def sizeHint(self, option, index):
        return QSize(160, self.HEIGHT)

    def paint(self, painter, option, index):
        from PySide6.QtWidgets import QStyle
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.rect)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme_color("selected")))
            painter.drawRoundedRect(option.rect.adjusted(5, 2, -5, -2), 9, 9)
        page = index.row()
        points = self.parent().document.pagePointSize(page)
        size = points.scaled(120, 144, Qt.AspectRatioMode.KeepAspectRatio)
        sheet = QRectF(option.rect.center().x() - size.width() / 2,
                       option.rect.top() + 8, size.width(), size.height())
        painter.fillRect(sheet.translated(1, 2), QColor(theme_color("shadow")))
        painter.fillRect(sheet, Qt.GlobalColor.white)
        glyph = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(glyph, QIcon) and not glyph.isNull():
            glyph.paint(painter, sheet.toRect())
        painter.setPen(QPen(QColor(theme_color("accent" if selected else "border")), 2 if selected else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(sheet)
        painter.setPen(QColor(theme_color("accent" if selected else "muted")))
        label = option.rect.adjusted(0, 154, 0, -5)
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, str(page + 1))
        painter.restore()


class ThumbnailList(QListWidget):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.setObjectName("Thumbnails")
        self.document = document
        self.cache = OrderedDict()
        self._requests = {}
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setUniformItemSizes(True)
        self.setSpacing(3)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.render_visible)
        self._renderer = QPdfPageRenderer(self)
        self._renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
        self._renderer.setDocument(document)
        self._renderer.pageRendered.connect(self._rendered)
        document.statusChanged.connect(self._status_changed)
        self.verticalScrollBar().valueChanged.connect(self.schedule)
        self.viewport().installEventFilter(self)

    def _status_changed(self, status):
        if status != QPdfDocument.Status.Ready:
            self._timer.stop()
            self.cache.clear()
            self._requests.clear()

    def reset_pages(self):
        self.cache.clear()
        self._requests.clear()
        self.blockSignals(True)
        self.clear()
        for page in range(self.document.pageCount()):
            item = QListWidgetItem(str(page + 1))
            item.setToolTip(tr('Pagina {0} · tasto destro per organizzare', page + 1))
            self.addItem(item)
        self.blockSignals(False)
        self.schedule()

    def schedule(self, *_):
        self._timer.start(20)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.schedule()
        return super().eventFilter(watched, event)

    def visible_pages(self):
        height = self.viewport().height()
        first = -1
        # Hit the full-width list row, including after scrolling into row gaps.
        for y in range(0, min(height, ThumbnailDelegate.HEIGHT), 4):
            first = self.indexAt(QPoint(self.viewport().width() // 2, y)).row()
            if first >= 0:
                break
        if first < 0:
            return range(0)
        visible = ceil(height / (ThumbnailDelegate.HEIGHT + 2 * self.spacing())) + 1
        return range(max(0, first - 1), min(self.count(), first + visible + 1))

    def render_visible(self):
        if not self.isVisible() or self.document.status() != QPdfDocument.Status.Ready:
            return
        for page in self.visible_pages():
            if page in self.cache:
                self.item(page).setIcon(self.cache[page])
                self.cache.move_to_end(page)
                continue
            if page in self._requests.values() or len(self._requests) >= 12:
                continue
            points = self.document.pagePointSize(page)
            size = (points.scaled(120, 144, Qt.AspectRatioMode.KeepAspectRatio) * self.devicePixelRatioF()).toSize()
            options = QPdfDocumentRenderOptions()
            options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
            request = self._renderer.requestPage(page, size, options)
            self._requests[request] = page

    def _rendered(self, page, size, image, options, request):
        expected = self._requests.pop(request, None)
        if expected != page or image.isNull() or page >= self.count():
            return
        glyph = QIcon(QPixmap.fromImage(image))
        self.cache[page] = glyph
        self.item(page).setIcon(glyph)
        while len(self.cache) > 72:
            old_page, _ = self.cache.popitem(last=False)
            self.item(old_page).setIcon(QIcon())
        self.schedule()

    def select_page(self, page):
        if 0 <= page < self.count():
            self.blockSignals(True)
            self.setCurrentRow(page)
            self.blockSignals(False)
            self.scrollToItem(self.item(page), QAbstractItemView.ScrollHint.EnsureVisible)
            self.schedule()

    def shutdown(self):
        self._timer.stop()
        self._requests.clear()
        self._renderer.setDocument(None)
