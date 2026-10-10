"""Per-document viewer, thumbnails, bookmarks and navigation."""
from __future__ import annotations
from ..core.localization import tr

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMargins, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QSplitter,
    QStyledItemDelegate,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.editor import PDFEditor, PDFOperationError
from .pdf_view import TextPdfView
from .thumbnails import ThumbnailList
from .document_notice import DocumentNotice
from .properties_dialog import PropertiesDialog
from .theme import theme_color


class SearchResultDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        model = index.model()
        result = model.resultAtIndex(index.row())
        before = " ".join(result.contextBefore().replace("\u23ce", " ").replace("\u21b5", " ").split())[-35:]
        after = " ".join(result.contextAfter().replace("\u23ce", " ").replace("\u21b5", " ").split())[:65]
        option.text = tr('Pagina {0}\n{1} {2} {3}', result.page() + 1, before, model.searchString(), after)

    def paint(self, painter, option, index):
        # Paint the page number separately so Qt's item elision cannot collapse
        # the heading into the surrounding search context.
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        heading, snippet = styled.text.split("\n", 1)
        styled.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, option.widget)
        painter.save()
        painter.setClipRect(option.rect)
        content = option.rect.adjusted(10, 9, -10, -8)
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme_color("accent")))
        painter.drawText(content.adjusted(0, 0, 0, -content.height() + 19), Qt.AlignmentFlag.AlignLeft, heading)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(theme_color("muted")))
        metrics = option.fontMetrics
        split = len(snippet)
        while split > 0 and metrics.horizontalAdvance(snippet[:split]) > content.width():
            split -= 1
        space = snippet.rfind(" ", 0, split)
        if space > 0 and split < len(snippet):
            split = space
        first = snippet[:split]
        second = metrics.elidedText(snippet[split:].strip(), Qt.TextElideMode.ElideRight, content.width())
        line_height = min(19, max(1, (content.height() - 24) // 2))
        for offset, line in enumerate((first, second)):
            rect = content.adjusted(0, 24 + offset * line_height, 0, 0)
            rect.setHeight(line_height)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, 82)


class DocumentTab(QWidget):
    changed = Signal()
    page_changed = Signal(int, int)
    search_changed = Signal(int, int)
    selection_changed = Signal(bool)
    message = Signal(str)
    markup_requested = Signal(str)

    def __init__(self, filename: Path, parent: QWidget | None = None, password: str | None = None):
        super().__init__(parent)
        try:
            self.editor = PDFEditor(filename, password=password)
        except Exception:
            self.deleteLater()
            raise
        self._load_path = None
        self._selected_search = -1
        self._initializing = False
        self._pending_page: int | None = None
        self._query = ""
        self._search_open = False

        self.pdf = QPdfDocument(self)
        self._buffer = QBuffer(self)
        if password is not None:
            self.pdf.setPassword(password)
        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.pdf)
        self.search_model.rowsInserted.connect(self._on_search_updated)
        self.search_model.rowsRemoved.connect(self._on_search_updated)
        self.search_model.modelReset.connect(self._on_search_updated)
        if hasattr(self.search_model, "countChanged"):
            self.search_model.countChanged.connect(self._on_search_updated)
        self.bookmark_model = QPdfBookmarkModel(self)
        self.bookmark_model.setDocument(self.pdf)

        self.viewer = TextPdfView(self)
        self.viewer.editable = not self.editor.encrypted
        self.viewer.selection_changed.connect(self.selection_changed)
        self.viewer.message.connect(self.message)
        self.viewer.markup_requested.connect(self.markup_requested)
        self.viewer.setDocument(self.pdf)
        self.viewer.setSearchModel(self.search_model)
        self.viewer.setPageMode(QPdfView.PageMode.MultiPage)
        self.viewer.setZoomMode(QPdfView.ZoomMode.FitInView)
        self.viewer.setPageSpacing(24)
        self.viewer.setDocumentMargins(QMargins(32, 24, 32, 24))
        self.viewer.pageNavigator().currentPageChanged.connect(self._on_page_changed)

        self.thumbs = ThumbnailList(self.pdf, self)
        self._thumb_cache = self.thumbs.cache
        self.thumbs.currentRowChanged.connect(self.jump_to_page)

        self.bookmarks = QTreeView()
        self.bookmarks.setModel(self.bookmark_model)
        self.bookmarks.setHeaderHidden(True)
        self.bookmarks.clicked.connect(self._bookmark_clicked)

        self.results = QListView()
        self.results.setModel(self.search_model)
        self.results.setItemDelegate(SearchResultDelegate(self.results))
        self.results.clicked.connect(lambda index: self.select_result(index.row()))
        self.results.setWordWrap(True)
        self.results.setSpacing(6)
        self.sidebar = QTabWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.addTab(self.thumbs, tr("Pagine"))
        self.sidebar.addTab(self.bookmarks, tr("Indice"))
        self.sidebar.addTab(self.results, tr("Ricerca"))
        self.sidebar.setMinimumWidth(180)
        self.sidebar.currentChanged.connect(lambda _: self._schedule_thumbnails())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.viewer)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([200, 1000])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.notice = DocumentNotice(self.editor, self)
        self.notice.details_requested.connect(self.show_properties)
        root.addWidget(self.notice)
        root.addWidget(self.splitter, 1)
        self.pdf.statusChanged.connect(self._on_pdf_status)
        try:
            self._reload(0)
        except Exception:
            self.close_document()
            self.deleteLater()
            raise

    @property
    def title(self) -> str:
        return self.editor.output_path.name + (" *" if self.editor.dirty else "")

    @property
    def page(self) -> int:
        return max(0, self.viewer.pageNavigator().currentPage())

    @property
    def count(self) -> int:
        return self.pdf.pageCount()

    def _reload(self, target_page: int) -> None:
        self.notice.refresh()
        self.viewer.notes = self.editor.notes()
        self._pending_page = target_page
        self._initializing = True
        self._thumb_cache.clear()
        self._load_path = self.editor.path
        self._selected_search = -1
        self.search_model.setSearchString("")
        self.viewer.setCurrentSearchResultIndex(-1)
        self.pdf.close()
        self.viewer.set_vector_source(self.editor.path, self.pdf.password())
        # Load from memory so PDFium's render workers never hold Windows locks
        # on revisions that Undo branching or closing needs to remove.
        self._buffer.close()
        self._buffer.setData(QByteArray(self.editor.path.read_bytes()))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.pdf.load(self._buffer)
        # Most local files load synchronously; statusChanged also covers asynchronous load.
        if self.pdf.status() == QPdfDocument.Status.Ready and self._initializing:
            self._populate_pages(target_page)
        elif self.pdf.status() == QPdfDocument.Status.Error:
            self._initializing = False
            raise PDFOperationError(tr("Il motore QtPdf non riesce ad aprire il file."))

    def _on_pdf_status(self, status) -> None:
        if status == QPdfDocument.Status.Ready and self._initializing:
            self._populate_pages(0 if self._pending_page is None else self._pending_page)

    def _populate_pages(self, target_page: int) -> None:
        self._initializing = False
        self._pending_page = None
        self.thumbs.reset_pages()
        if self.pdf.pageCount():
            self.jump_to_page(min(target_page, self.pdf.pageCount() - 1))
        self.search_model.setSearchString(self._query)
        self._schedule_thumbnails()
        self.changed.emit()

    def _schedule_thumbnails(self):
        self.thumbs.schedule()

    def _bookmark_clicked(self, index) -> None:
        page = index.data(int(QPdfBookmarkModel.Role.Page))
        if isinstance(page, int):
            location = index.data(int(QPdfBookmarkModel.Role.Location))
            self.viewer.pageNavigator().jump(page, location if isinstance(location, QPointF) else QPointF(), self.viewer.zoomFactor())

    def _on_page_changed(self, page: int) -> None:
        self.thumbs.select_page(page)
        self.page_changed.emit(page, self.pdf.pageCount())

    def jump_to_page(self, page: int) -> None:
        if page is None or not 0 <= page < self.pdf.pageCount():
            return
        self.viewer.pageNavigator().jump(page, QPointF(0, 0), self.viewer.zoomFactor())
        self._on_page_changed(page)

    def zoom_in(self) -> None:
        self.viewer.zoom_to(self.effective_zoom() * 1.2)
        self.changed.emit()

    def zoom_out(self) -> None:
        self.viewer.zoom_to(self.effective_zoom() / 1.2)
        self.changed.emit()

    def fit_width(self) -> None:
        self.viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.changed.emit()

    def fit_page(self) -> None:
        self.viewer.setZoomMode(QPdfView.ZoomMode.FitInView)
        self.changed.emit()

    def set_search(self, query: str) -> None:
        self._query = query.strip()
        self._selected_search = -1
        self.search_model.setSearchString(self._query)
        self.viewer.setCurrentSearchResultIndex(-1)
        if self._query:
            self.sidebar.setCurrentWidget(self.results)
        self._on_search_updated()

    def _on_search_updated(self, *_args) -> None:
        count = self.search_model.count()
        if count and self._selected_search < 0:
            self.select_result(0)
            return
        self.search_changed.emit(self._selected_search + 1, count)
        self.viewer.viewport().update()

    def next_result(self, direction: int) -> None:
        count = self.search_model.count()
        if not count:
            self._on_search_updated()
            return
        index = (self._selected_search + direction) % count if self._selected_search >= 0 else (0 if direction > 0 else count - 1)
        self.select_result(index)

    def select_result(self, index: int) -> None:
        if not 0 <= index < self.search_model.count():
            return
        self._selected_search = index
        link = self.search_model.resultAtIndex(index)
        self.jump_to_page(link.page())
        # Bring the matching line into view without changing the user's zoom.
        bounds = link.rectangles()
        if bounds:
            rect = self.viewer.page_transform(link.page()).mapRect(bounds[0])
            if rect.top() < 0 or rect.bottom() > self.viewer.viewport().height():
                bar = self.viewer.verticalScrollBar()
                bar.setValue(bar.value() + int(rect.top() - self.viewer.viewport().height() / 3))
        self.viewer.setCurrentSearchResultIndex(index)
        self.results.setCurrentIndex(self.search_model.index(index, 0))
        self._on_search_updated()

    def effective_zoom(self) -> float:
        return self.viewer.effective_zoom()

    def set_zoom(self, percent: int) -> None:
        self.viewer.zoom_to(percent / 100)
        self.changed.emit()

    def apply_edit(self, method: str, *args) -> None:
        current = self.page
        getattr(self.editor, method)(*args)
        if method == "delete" and current >= self.count - 1:
            current = max(0, current - 1)
        elif method == "move":
            current += args[1]
        self._pending_page = current
        self._reload(current)
        self.changed.emit()

    def undo(self) -> None:
        if self.editor.undo():
            self._pending_page = self.page
            self._reload(self.page)

    def redo(self) -> None:
        if self.editor.redo():
            self._pending_page = self.page
            self._reload(self.page)

    def show_properties(self):
        try:
            dialog = PropertiesDialog(self.editor, lambda values: self.apply_edit("update_metadata", values), self)
        except PDFOperationError as exc:
            self.message.emit(str(exc))
            return
        dialog.exec()
        dialog.deleteLater()

    def close_document(self) -> None:
        self.thumbs.shutdown()
        self.viewer.set_vector_source(None)
        self.viewer.setSearchModel(None)
        self.viewer.setDocument(None)
        self.search_model.setDocument(None)
        self.bookmark_model.setDocument(None)
        self.pdf.close()
        self._buffer.close()
        self.editor.close()
