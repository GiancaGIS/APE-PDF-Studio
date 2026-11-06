"""Move and resize one signature draft locally; commit static PDF content once."""
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..core.errors import PDFOperationError
from ..core.localization import tr
from .document_tools import EditProgress
from .icons import icon


class SignatureController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.view = tab, tab.viewer
        self.asset = None
        self.image = QImage()
        self.rect = QRectF()
        self.page = 0
        self.revision = None
        self._drag = None
        self._committing = False
        self.bar = QWidget(tab)
        self.bar.setObjectName('SignatureToolbar')
        layout = QVBoxLayout(self.bar)
        layout.setContentsMargins(12, 6, 12, 6)
        row = QHBoxLayout()
        choose = QPushButton(icon('signature'), tr('Scegli immagine…'))
        choose.clicked.connect(self.choose)
        row.addWidget(choose)
        row.addWidget(QLabel(tr('Larghezza')))
        self.width = QDoubleSpinBox()
        self.width.setRange(.4, 2000)
        self.width.setDecimals(1)
        self.width.setSuffix(' mm')
        self.width.valueChanged.connect(self._set_width)
        row.addWidget(self.width)
        row.addStretch()
        self.apply = QPushButton(tr('Inserisci firma'))
        self.apply.clicked.connect(self.finish)
        row.addWidget(self.apply)
        cancel = QPushButton(tr('Annulla'))
        cancel.clicked.connect(self.cancel)
        row.addWidget(cancel)
        layout.addLayout(row)
        hint = QLabel(tr("Firma da immagine: trascina per spostarla, usa l'angolo in basso a destra per ridimensionarla. Non è una firma digitale."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.bar.hide()
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)

    @property
    def active(self):
        return self.asset is not None

    @property
    def dirty(self):
        return self.active

    def choose(self):
        if not self.view.editable or self.tab.form_designer.isVisible():
            return False
        path, _ = QFileDialog.getOpenFileName(self.tab, tr('Scegli immagine della firma'), '',
                                             tr('Immagini PNG e JPEG (*.png *.jpg *.jpeg)'))
        if not path:
            return False
        if (not self.tab.inline_form.finish() or not self.tab.inline_text.deactivate() or
                not self.tab.ink.deactivate()):
            return False
        progress = EditProgress(self.tab.editor, 'prepare_signature', (path,), self.tab)
        try:
            data = progress.execute()
        except PDFOperationError as exc:
            QMessageBox.warning(self.tab, tr('Firma da immagine'), str(exc))
            return False
        finally:
            progress.deleteLater()
        self.cancel()
        self.asset = data['asset']
        self.revision = str(self.tab.editor.path)
        self.image = QImage(data['pixels'], data['width'], data['height'], data['width']*4,
                            QImage.Format.Format_RGBA8888_Premultiplied).copy()
        self.page = self.tab.page
        size = self.tab.pdf.pagePointSize(self.page)
        ratio = self.image.width()/self.image.height()
        width = min(180, size.width()*.6, size.height()*.6*ratio)
        transform, ok = self.view.page_transform(self.page).inverted()
        visible = transform.mapRect(QRectF(self.view.viewport().rect())).intersected(
            QRectF(0, 0, size.width(), size.height())) if ok else QRectF()
        center = visible.center() if not visible.isEmpty() else QPointF(size.width()/2, size.height()/2)
        self.rect = QRectF(center.x()-width/2, center.y()-width/ratio/2, width, width/ratio)
        self._clamp()
        self._sync_width()
        self.view._cancel_placement()
        self.view.clear_selection()
        self.bar.show()
        self.view.setFocus()
        self.view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        self.view.viewport().update()
        self.tab.changed.emit()
        return True

    def _clamp(self):
        size = self.tab.pdf.pagePointSize(self.page)
        self.rect.moveLeft(max(0, min(self.rect.left(), size.width()-self.rect.width())))
        self.rect.moveTop(max(0, min(self.rect.top(), size.height()-self.rect.height())))

    def _sync_width(self):
        blocked = self.width.blockSignals(True)
        self.width.setValue(self.rect.width()*25.4/72)
        self.width.blockSignals(blocked)

    def _set_width(self, millimeters):
        if not self.active:
            return
        self._resize(millimeters*72/25.4)

    def _resize(self, width):
        ratio = self.image.width()/self.image.height()
        size = self.tab.pdf.pagePointSize(self.page)
        limit = min(size.width()-self.rect.left(), (size.height()-self.rect.top())*ratio)
        width = max(min(max(10, ratio), limit), min(width, limit))
        self.rect.setSize(QSizeF(width, width/ratio))
        self._sync_width()
        self.view.viewport().update()

    def cancel(self):
        asset, self.asset = self.asset, None
        if asset is not None:
            self.tab.editor.release_signature(asset)
        self.image = QImage()
        self._drag = None
        self.bar.hide()
        self.view._stop_pan()
        self.view.viewport().unsetCursor()
        self.view.viewport().update()
        self.tab.changed.emit()

    def finish(self):
        if not self.active or self._committing:
            return True
        args = (self.page, self.asset, [self.rect.left(), self.rect.top(), self.rect.right(), self.rect.bottom()], self.revision)
        scroll = (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())
        progress = EditProgress(self.tab.editor, 'insert_signature', args, self.tab)
        self._committing = True
        try:
            progress.execute()
            page = self.page
            self.cancel()
            self.tab._reload(page)
            self.view.horizontalScrollBar().setValue(scroll[0])
            self.view.verticalScrollBar().setValue(scroll[1])
            self.tab.changed.emit()
            return True
        except PDFOperationError as exc:
            self.tab.message.emit(str(exc))
            return False
        finally:
            self._committing = False
            progress.deleteLater()

    def document_changed(self):
        if self.active and str(self.tab.editor.path) != self.revision:
            self.cancel()

    def eventFilter(self, watched, event):
        if not self.active or self._committing:
            return False
        kind = event.type()
        if kind == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.cancel()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish()
                return True
        if watched is not self.view.viewport():
            return False
        if kind in (QEvent.Type.ContextMenu, QEvent.Type.MouseButtonDblClick):
            return True
        if kind in (QEvent.Type.Hide, QEvent.Type.UngrabMouse, QEvent.Type.Wheel):
            self._drag = None
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            hit = self.view.point_on_page(event.position())
            self.view.setFocus()
            if hit:
                page, point = hit
                scale = max(.01, self.view.page_transform(page).m11())
                if page == self.page and (point-self.rect.bottomRight()).manhattanLength() <= 18/scale:
                    self._drag = ('resize', point, QRectF(self.rect))
                else:
                    if page != self.page:
                        self.page = page
                        size = self.tab.pdf.pagePointSize(page)
                        self.rect.moveTopLeft(QPointF(0, 0))
                        self._resize(min(self.rect.width(), size.width(), size.height()*self.image.width()/self.image.height()))
                    if not self.rect.contains(point):
                        self.rect.moveCenter(point)
                        self._clamp()
                    self._drag = ('move', point, QRectF(self.rect))
                self.view.viewport().update()
            return True
        if kind == QEvent.Type.MouseMove and self.view._pan_position is not None:
            return False
        if kind == QEvent.Type.MouseButtonRelease and event.button() != Qt.MouseButton.LeftButton:
            return False
        if kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if self._drag:
                point = self.view.page_transform(self.page).inverted()[0].map(event.position())
                mode, start, original = self._drag
                if mode == 'resize':
                    self._resize(original.width() + point.x()-start.x())
                else:
                    self.rect = original.translated(point-start)
                    self._clamp()
                self.view.viewport().update()
                if kind == QEvent.Type.MouseButtonRelease:
                    self._drag = None
            elif kind == QEvent.Type.MouseMove:
                hit = self.view.point_on_page(event.position())
                scale = max(.01, self.view.page_transform(self.page).m11())
                resize = hit and hit[0] == self.page and (hit[1]-self.rect.bottomRight()).manhattanLength() <= 18/scale
                self.view.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor if resize else Qt.CursorShape.SizeAllCursor)
            return True
        return False

    def paint(self, painter, page, transform):
        if not self.active or page != self.page:
            return
        painter.save()
        painter.setWorldTransform(transform)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(self.rect, self.image)
        scale = max(.01, transform.m11())
        painter.setPen(QPen(QColor('#1685f8'), 1.5/scale, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect)
        painter.setBrush(QColor('#1685f8'))
        painter.drawRect(QRectF(self.rect.right()-4/scale, self.rect.bottom()-4/scale, 8/scale, 8/scale))
        painter.restore()

    def shutdown(self):
        self.cancel()
        self.view.viewport().removeEventFilter(self)
        self.view.removeEventFilter(self)
