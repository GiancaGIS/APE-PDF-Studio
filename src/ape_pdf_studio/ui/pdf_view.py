"""PDF text interactions and annotation rendering on the Qt scroll viewer."""
from __future__ import annotations
from ..core.localization import tr

from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QTransform
from PySide6.QtPdf import QPdfDocument, QPdfPageRenderer
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QApplication, QFrame, QMenu

from .theme import theme_color
from .page_tiles import CACHE_BYTES, MAX_PENDING, viewport_plan
from .vector_pages import VectorPages
from .remote_pdf import RemotePdfView, RemoteRenderer


class TextPdfView(RemotePdfView):
    selection_changed = Signal(bool)
    markup_requested = Signal(str)
    message = Signal(str)
    note_requested = Signal(int, QPointF)
    note_open_requested = Signal(int, int)
    text_edit_requested = Signal(int, QPointF)
    link_requested = Signal(str)
    form_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.selection = None
        self.selection_page = -1
        self._anchor = None
        self._dragged = False
        self._pan_position = None
        self._pan_cursor = None
        self.notes = {}
        self._note_click = None
        self._placement = None
        self.edit_target = None
        self.editable = True
        self.links = {}
        self.form_fields = ()
        self.inline_form = None
        self.inline_text = None
        self.form_designer = None
        self._interaction_click = None
        self._interaction_start = None
        self._geometry_key = None
        self._geometry = {}
        self._images = OrderedDict()
        self._requests = {}
        self._wanted = {}
        self._failed = set()
        self._cache_bytes = 0
        self._render_epoch = 0
        self._renderer = None
        self._watched_document = None
        self.vectors = VectorPages(self)
        self.vectors.changed.connect(self.viewport().update)
        self.vectors.fallback.connect(lambda page: self.message.emit(
            tr('Pagina {0}: resa di compatibilità per immagini o effetti PDF complessi.', page + 1)))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.documentChanged.connect(self._document_changed)

    def _document_changed(self, document):
        if self._watched_document:
            self._watched_document.statusChanged.disconnect(self._document_status)
        self._watched_document = document
        if document:
            document.statusChanged.connect(self._document_status)
        self._document_status()

    def _document_status(self, *_):
        self._stop_pan()
        self._cancel_placement()
        self._note_click = None
        self._interaction_click = None
        self.vectors.reset(self.vectors.source, self.vectors.password)
        self._render_epoch += 1
        if self._renderer:
            self._renderer.setDocument(None)
            self._renderer.deleteLater()
            self._renderer = None
        self.clear_selection()
        self._images.clear()
        self._requests.clear()
        self._wanted.clear()
        self._failed.clear()
        self._cache_bytes = 0
        self._geometry_key = None
        document = self.document()
        if document and document.status() == QPdfDocument.Status.Ready:
            self._renderer = RemoteRenderer(self)
            self._renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
            self._renderer.setDocument(document)
            epoch = self._render_epoch
            self._renderer.pageRendered.connect(lambda *args: self._page_rendered(*args)
                                                if epoch == self._render_epoch else None)
        self.viewport().update()
        if self.inline_form:
            self.inline_form.reposition()

    def clear_selection(self):
        self.selection = None
        self.selection_page = -1
        self._anchor = None
        self.selection_changed.emit(False)
        self.viewport().update()

    @property
    def has_selection(self):
        return self.selection is not None and bool(self.selection.text().strip())

    def page_rects(self):
        """Page rectangles in scroll coordinates; match Qt's public layout modes."""
        document = self.document()
        if not document or document.status() != QPdfDocument.Status.Ready:
            return {}
        margins = self.documentMargins()
        dpi_scale = QApplication.primaryScreen().logicalDotsPerInch() / 72
        key = (document.pageCount(), self.viewport().size(), self.zoomMode(),
               self.zoomFactor(), self.pageMode(), self.pageNavigator().currentPage(),
               margins, self.pageSpacing(), dpi_scale)
        if self._geometry_key == key:
            return self._geometry
        pages = range(document.pageCount()) if self.pageMode() == self.PageMode.MultiPage else [self.pageNavigator().currentPage()]
        sizes = {}
        available = QSize(max(1, self.viewport().width() - margins.left() - margins.right()),
                          max(1, self.viewport().height() - self.pageSpacing()))
        for page in pages:
            points = document.pagePointSize(page)
            natural = (points * dpi_scale).toSize()
            if self.zoomMode() == self.ZoomMode.FitToWidth:
                scale = available.width() / max(1, natural.width())
                size = natural * scale
            elif self.zoomMode() == self.ZoomMode.FitInView:
                size = natural.scaled(available, Qt.AspectRatioMode.KeepAspectRatio)
            else:
                size = (points * (dpi_scale * self.zoomFactor())).toSize()
            sizes[page] = size
        canvas_width = max(self.viewport().width(), max((s.width() for s in sizes.values()), default=0) + margins.left() + margins.right())
        y = margins.top()
        self._geometry = {}
        for page, size in sizes.items():
            self._geometry[page] = QRectF(int((canvas_width - size.width()) / 2), y, size.width(), size.height())
            y += size.height() + self.pageSpacing()
        self._geometry_key = key
        return self._geometry

    def page_transform(self, page):
        rect = self.page_rects().get(page)
        if rect is None:
            return QTransform()
        size = self.document().pagePointSize(page)
        # Account for the point-to-pixel scale and both scrollbars.
        transform = QTransform()
        transform.translate(rect.x() - self.horizontalScrollBar().value(),
                            rect.y() - self.verticalScrollBar().value())
        transform.scale(rect.width() / size.width(), rect.height() / size.height())
        return transform

    def point_on_page(self, position):
        absolute = position + QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        for page, rect in self.page_rects().items():
            if rect.contains(absolute):
                inverse, ok = self.page_transform(page).inverted()
                if ok:
                    return page, inverse.map(position)
        return None

    def effective_zoom(self):
        if self.zoomMode() == self.ZoomMode.Custom:
            return self.zoomFactor()
        page = max(0, self.pageNavigator().currentPage())
        rect = self.page_rects().get(page)
        if not rect:
            return 1.0
        dpi = QApplication.primaryScreen().logicalDotsPerInch() / 72
        return rect.width() / self.document().pagePointSize(page).width() / dpi

    def zoom_to(self, factor, position=None):
        """Preserve the pointer's page point within the scrollbar limits."""
        position = position or QPointF(self.viewport().rect().center())
        anchor = self.point_on_page(position)
        self.setZoomMode(self.ZoomMode.Custom)
        self.setZoomFactor(min(5.0, max(0.25, factor)))
        if anchor:
            page, point = anchor
            destination = self.page_transform(page).map(point)
            horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
            horizontal.setValue(horizontal.value() + round(destination.x() - position.x()))
            vertical.setValue(vertical.value() + round(destination.y() - position.y()))
        self.viewport().update()

    def wheelEvent(self, event):
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        if event.modifiers() & modifiers:
            delta = event.angleDelta().y()
            exponent = delta / 120 if delta else event.pixelDelta().y() / 60
            if exponent:
                self.zoom_to(self.effective_zoom() * 1.2 ** max(-4, min(4, exponent)), event.position())
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            document = self.document()
            if document and document.status() == QPdfDocument.Status.Ready:
                self._stop_pan()
                self._anchor = None
                self._dragged = False
                self.setFocus()
                self._pan_position = event.globalPosition()
                self._pan_cursor = self.viewport().cursor()
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._pan_position is not None:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self.point_on_page(event.position())
            if self.form_designer and self.form_designer.isVisible():
                self.clear_selection()
                self.form_designer.press(hit,event.position())
                event.accept()
                return
            if self.inline_text and self.inline_text.active:
                self.clear_selection()
                if hit:
                    self.text_edit_requested.emit(*hit)
                event.accept()
                return
            if self._placement:
                if hit:
                    kind = self._placement
                    self._cancel_placement()
                    (self.note_requested if kind == "note" else self.text_edit_requested).emit(*hit)
                else:
                    self.message.emit(tr("Scegli un punto all'interno della pagina."))
                event.accept()
                return
            note = self.note_at(event.position())
            if note:
                self._note_click = note
                self._anchor = None
                self._dragged = False
                event.accept()
                return
            self.clear_selection()
            self._interaction_click = self.interaction_at(event.position())
            self._interaction_start = event.position()
            self._anchor = hit
            self._dragged = False
            self.setFocus()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.form_designer and self.form_designer.isVisible():
            self.viewport().setCursor(Qt.CursorShape.CrossCursor if self.form_designer.mode != "select" else Qt.CursorShape.ArrowCursor)
            self.form_designer.move(event.position())
            if self._pan_position is None:
                event.accept()
                return
        if self._pan_position is not None:
            if event.buttons() & Qt.MouseButton.MiddleButton:
                delta = event.globalPosition() - self._pan_position
                self._pan_position = event.globalPosition()
                horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
                horizontal.setValue(horizontal.value() - round(delta.x()))
                vertical.setValue(vertical.value() - round(delta.y()))
                event.accept()
                return
            self._stop_pan()
        note = self.note_at(event.position())
        interaction = self.interaction_at(event.position())
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if self._placement == "note" else
                                  Qt.CursorShape.PointingHandCursor if note or interaction else Qt.CursorShape.IBeamCursor)
        if note:
            entry = next(n for n in self.notes[note[0]] if n["index"] == note[1])
            # Qt tooltips may interpret rich text; escape document content.
            from html import escape
            self.viewport().setToolTip("<qt>" + escape(entry["text"][:2000]) + "</qt>")
        elif interaction:
            from html import escape
            text = interaction[1] if interaction[0] == "link" else tr("Compila questo campo del modulo")
            self.viewport().setToolTip("<qt>" + escape(text) + "</qt>")
        else:
            self.viewport().setToolTip("")
        if self._anchor and event.buttons() & Qt.MouseButton.LeftButton:
            if self._interaction_click is not None:
                if (event.position() - self._interaction_start).manhattanLength() < QApplication.startDragDistance():
                    event.accept()
                    return
                self._interaction_click = None
            page, start = self._anchor
            inverse, _ = self.page_transform(page).inverted()
            end = inverse.map(event.position())
            size = self.document().pagePointSize(page)
            end = QPointF(min(size.width(), max(0, end.x())), min(size.height(), max(0, end.y())))
            self.selection = self.document().getSelection(page, start, end)
            self.selection_page = page
            self._dragged = True
            self.selection_changed.emit(self.has_selection)
            self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.form_designer and self.form_designer.isVisible():
            self.form_designer.release(event.position())
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._stop_pan()
            event.accept()
            return
        if self._pan_position is not None:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._note_click:
            note, self._note_click = self._note_click, None
            if self.note_at(event.position()) == note:
                self.note_open_requested.emit(*note)
            event.accept()
            return
        dragged = self._dragged
        self._anchor = None
        if event.button() == Qt.MouseButton.LeftButton:
            interaction, self._interaction_click = self._interaction_click, None
            if interaction and not dragged and self.interaction_at(event.position()) == interaction:
                (self.link_requested if interaction[0] == "link" else self.form_requested).emit(interaction[1])
            if dragged and not self.has_selection:
                self.message.emit(tr("Nessun testo selezionabile qui. Usa Annota → Riconosci testo (OCR)…"))
            # Only our validated interaction signals can request an external action.
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _stop_pan(self):
        if getattr(self, "_pan_position", None) is not None:
            self._pan_position = None
            self.viewport().setCursor(self._pan_cursor)
            self._pan_cursor = None

    def focusOutEvent(self, event):
        self._stop_pan()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._stop_pan()
        self._cancel_placement()
        if self.inline_form:
            self.inline_form.finish()
        super().hideEvent(event)

    def begin_placement(self, kind):
        if not self.editable:
            return
        if self.inline_text:
            if kind == "text":
                self.inline_text.activate()
                return
            if not self.inline_text.deactivate():
                return
        self._stop_pan()
        self._anchor = None
        self._placement = kind
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if kind == "note" else Qt.CursorShape.IBeamCursor)
        self.setFocus()
        self.message.emit(tr("Fai clic nel punto della nota · Esc per annullare") if kind == "note" else
                          tr("Fai clic sul testo da modificare · Esc per annullare"))

    def _cancel_placement(self):
        if getattr(self, "_placement", None):
            self._placement = None
            self.viewport().unsetCursor()

    def note_at(self, position):
        for page, entries in self.notes.items():
            if page not in self.page_rects():
                continue
            transform = self.page_transform(page)
            for entry in reversed(entries):
                left, top, right, bottom = entry["rect"]
                if transform.mapRect(QRectF(left, top, right - left, bottom - top)).contains(position):
                    return page, entry["index"]
        return None

    def interaction_at(self, position):
        hit = self.point_on_page(position)
        if not hit:
            return None
        page, point = hit
        for field in self.form_fields:
            for number, rect in field.widgets:
                if number == page and QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]).contains(point):
                    return "form", field.name
        for entry in self.links.get(page, []):
            rect = entry["rect"]
            if QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]).contains(point):
                return "link", entry["url"]
        return None

    def mouseDoubleClickEvent(self, event):
        if self.form_designer and self.form_designer.isVisible():
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self.mousePressEvent(event)
            return
        hit = self.point_on_page(event.position())
        if event.button() == Qt.MouseButton.LeftButton and hit:
            if self.interaction_at(event.position()):
                event.accept()
                return
            page, point = hit
            selection = self.document().getSelection(page, point, point)
            text = self.document().getAllText(page).text()
            index = selection.startIndex()
            if 0 <= index < len(text):
                start, end = index, index
                while start > 0 and not text[start - 1].isspace():
                    start -= 1
                while end < len(text) and not text[end].isspace():
                    end += 1
                self.selection = self.document().getSelectionAtIndex(page, start, end - start)
                self.selection_page = page
                self._anchor = None
                self.selection_changed.emit(self.has_selection)
                self.viewport().update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def copy_selection(self):
        if self.has_selection:
            self.selection.copyToClipboard()
            self.message.emit(tr("Testo copiato negli appunti"))

    def select_page_text(self, page=None):
        document = self.document()
        if not document or document.status() != QPdfDocument.Status.Ready:
            return
        self.selection_page = self.pageNavigator().currentPage() if page is None else page
        self.selection = document.getAllText(self.selection_page)
        self.selection_changed.emit(self.has_selection)
        self.viewport().update()
        if not self.has_selection:
            self.message.emit(tr("Questa pagina non contiene testo selezionabile. Usa Annota → Riconosci testo (OCR)…"))

    def keyPressEvent(self, event):
        preparing = self.form_designer and self.form_designer.isVisible()
        if preparing and event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            self.form_designer.remove_selected()
            event.accept()
        elif preparing and event.key()==Qt.Key.Key_Escape:
            self.form_designer._drag=None
            self.form_designer._preview=None
            self.form_designer.draw_kind.setCurrentIndex(0)
            self.viewport().update()
            event.accept()
        elif not preparing and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and self.inline_form and self.form_fields:
            self.inline_form.next_field(event.key() == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_page_text()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            if self.inline_text and self.inline_text.active:
                if self.inline_text.control is not None or self.inline_text._loading:
                    self.inline_text.cancel()
                else:
                    self.inline_text.deactivate()
                event.accept()
                return
            if self._placement:
                self._cancel_placement()
            elif self._pan_position is not None:
                self._stop_pan()
            else:
                self.clear_selection()
            event.accept()
        else:
            super().keyPressEvent(event)

    def make_context_menu(self, page=None, point=None):
        menu = QMenu(self)
        if self.form_designer and self.form_designer.isVisible():
            panel = self.form_designer
            if page is not None and point is not None:
                index = next((i for i in reversed(range(len(panel.proposals))) if panel.proposals[i]["page"] == page and
                    QRectF(panel.proposals[i]["rect"][0], panel.proposals[i]["rect"][1],
                           panel.proposals[i]["rect"][2]-panel.proposals[i]["rect"][0],
                           panel.proposals[i]["rect"][3]-panel.proposals[i]["rect"][1]).contains(point)), -1)
                panel.select(index, navigate=False)
            if panel.selected >= 0:
                menu.addAction(tr("Elimina campo") if "source" in panel.proposals[panel.selected] else tr("Elimina proposta"), panel.remove_selected)
            return menu
        if point is not None and page is not None:
            viewport_point = self.page_transform(page).map(point)
            self._interaction_start = viewport_point
            interaction = self.interaction_at(viewport_point)
            if interaction:
                kind, value = interaction
                menu.addAction(tr("Apri collegamento…") if kind == "link" else tr("Compila modulo"),
                               lambda: (self.link_requested if kind == "link" else self.form_requested).emit(value))
                menu.addSeparator()
            note = self.note_at(viewport_point)
            if note:
                menu.addAction(tr("Apri nota…"), lambda: self.note_open_requested.emit(*note))
            add_note = menu.addAction(tr("Aggiungi nota qui…"), lambda: self.note_requested.emit(page, point))
            add_note.setEnabled(self.editable)
            edit_text = menu.addAction(tr("Modifica testo…"), lambda: self.text_edit_requested.emit(page, point))
            # Resolve editability asynchronously after opening the text dialog.
            # Context menus must not wait for the rendering worker's RPC lock.
            edit_text.setEnabled(self.editable)
            menu.addSeparator()
        copy = menu.addAction(tr("Copia testo selezionato"), self.copy_selection)
        copy.setEnabled(self.has_selection)
        menu.addSeparator()
        for title, kind in ((tr("Evidenzia"), "highlight"), (tr("Sottolinea"), "underline")):
            action = menu.addAction(title, lambda checked=False, value=kind: self.markup_requested.emit(value))
            action.setEnabled(self.has_selection and self.editable)
        menu.addSeparator()
        menu.addAction(tr("Seleziona testo della pagina"), lambda: self.select_page_text(page))
        return menu

    def focusNextPrevChild(self, next):
        if self.hasFocus() and self.inline_form and self.form_fields and not (self.form_designer and self.form_designer.isVisible()):
            return self.inline_form.next_field(not next)
        return super().focusNextPrevChild(next)

    def contextMenuEvent(self, event):
        hit = self.point_on_page(QPointF(event.pos()))
        menu = self.make_context_menu(*(hit if hit else (None, None)))
        menu.exec(event.globalPos())
        menu.deleteLater()

    def selection_rectangles(self):
        if not self.has_selection:
            return []
        return [(r.left(), r.top(), r.right(), r.bottom())
                for r in (polygon.boundingRect() for polygon in self.selection.bounds())
                if not r.isEmpty()]

    def _page_rendered(self, page, size, image, options, request_id):
        key = self._requests.pop(request_id, None)
        if key is None:
            return
        if image.isNull():
            self._failed.add(key)
            self.message.emit(tr("Impossibile visualizzare una parte della pagina."))
        else:
            self._images[key] = image
            self._cache_bytes += image.sizeInBytes()
            while self._cache_bytes > CACHE_BYTES:
                # Evict old zoom levels and offscreen tiles before visible ones.
                discard = next((tile for tile in self._images if tile not in self._wanted), next(iter(self._images)))
                self._cache_bytes -= self._images.pop(discard).sizeInBytes()
        self._pump_requests()
        self.viewport().update()

    def set_vector_source(self, source, password=None):
        """Only accept the editor's private, already preflighted revision."""
        self.vectors.reset(source, password)
        self.viewport().update()

    def set_vector_enabled(self, enabled):
        self.vectors.enabled = enabled
        if not enabled:
            self.vectors.reset(self.vectors.source, self.vectors.password)
        self.viewport().update()

    def _pump_requests(self):
        if not self._renderer:
            return
        pending = set(self._requests.values())
        for tile in self._wanted:
            if len(self._requests) >= MAX_PENDING:
                break
            if tile in self._images or tile in pending or tile in self._failed:
                continue
            request = self._renderer.requestPage(tile.page, QSize(tile.width, tile.height), tile.render_options())
            if request:
                self._requests[request] = tile
                pending.add(tile)

    def paintEvent(self, event):
        # Qt's default widget renderer omits PDF annotations. Request annotated
        # pages asynchronously, and paint search/selection above the rendered PDF.
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(event.rect(), QColor(theme_color("canvas")))
        viewport = QRectF(self.viewport().rect())
        pages = {page: rect.translated(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())
                 for page, rect in self.page_rects().items()}
        pages = {page: rect for page, rect in pages.items() if rect.intersects(viewport)}
        self.vectors.request(sorted(pages, key=lambda page: (pages[page].center() - viewport.center()).manhattanLength()))
        vector_pages = self.vectors.cache if self.vectors.enabled else {}
        self._wanted = viewport_plan({page: rect for page, rect in pages.items() if page not in vector_pages},
                                     viewport, self.devicePixelRatioF())
        self._failed.intersection_update(self._wanted)
        self._pump_requests()
        for page, visible in pages.items():
            painter.fillRect(visible.adjusted(-2, 0, 4, 5), QColor(theme_color("border")))
            painter.fillRect(visible.adjusted(2, 3, 2, 3), QColor(theme_color("shadow")))
            painter.fillRect(visible, Qt.GlobalColor.white)
            current = next((tile for tile in self._wanted if tile.page == page), None)
            # Keep the previous zoom visible while sharp tiles arrive.
            fallback = next((tile for tile in reversed(self._images) if tile.page == page and current and
                             (tile.full_width, tile.full_height) != (current.full_width, current.full_height)), None)
            if fallback:
                for tile, image in self._images.items():
                    if (tile.page, tile.full_width, tile.full_height) == (fallback.page, fallback.full_width, fallback.full_height):
                        target = tile.target_rect(visible)
                        if target.intersects(viewport):
                            painter.drawImage(target, image)
            for tile, target in self._wanted.items():
                if tile.page == page and tile in self._images:
                    painter.fillRect(target, Qt.GlobalColor.white)
                    painter.drawImage(target, self._images[tile])
                    self._images.move_to_end(tile)
            transform = self.page_transform(page)
            if page in vector_pages:
                vector_pages[page].paint(painter, transform, viewport.intersected(visible))
            if self.form_designer and self.form_designer.isVisible():
                self.form_designer.paint(painter,page,transform)
            for field in self.form_fields:
                for number, rect in field.widgets:
                    if number != page:
                        continue
                    target = transform.mapRect(QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]))
                    color = QColor(theme_color("accent"))
                    color.setAlpha(18 if field.readonly or not self.editable else 38)
                    painter.fillRect(target, color)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    color.setAlpha(90)
                    painter.setPen(QPen(color, 1))
                    painter.drawRect(target)
            if self.edit_target and self.edit_target[0] == page:
                left, top, right, bottom = self.edit_target[1]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(theme_color("accent")), 2))
                painter.drawRect(transform.mapRect(QRectF(left, top, right - left, bottom - top)))
            if self.inline_text and self.inline_text.active:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(theme_color("accent")), 1, Qt.PenStyle.DashLine))
                for left, top, right, bottom in self.inline_text.boxes.get(page, []):
                    painter.drawRect(transform.mapRect(QRectF(left, top, right - left, bottom - top)))
            model = self.searchModel()
            if model and model.searchString():
                for result in model.resultsOnPage(page):
                    for bound in result.rectangles():
                        painter.fillRect(transform.mapRect(bound), QColor(255, 202, 55, 95))
                index = self.currentSearchResultIndex()
                if 0 <= index < model.count():
                    result = model.resultAtIndex(index)
                    if result.page() == page:
                        painter.setPen(QPen(QColor("#d48806"), 2))
                        for bound in result.rectangles():
                            painter.drawRect(transform.mapRect(bound).adjusted(-2, -2, 2, 2))
            if self.has_selection and self.selection_page == page:
                painter.setPen(Qt.PenStyle.NoPen)
                selection_color = QColor(theme_color("accent"))
                selection_color.setAlpha(85)
                painter.setBrush(selection_color)
                for polygon in self.selection.bounds():
                    painter.drawPolygon(transform.map(polygon))
        painter.end()
