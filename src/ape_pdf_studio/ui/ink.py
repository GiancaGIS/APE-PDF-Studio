"""Immediate vector ink overlay. Mouse motion never calls the PDF worker."""
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QWidget)

from ..core.errors import PDFOperationError
from ..core.ink import MAX_POINTS, MAX_STROKES, segments
from ..core.localization import tr
from .icons import pen_cursor


def path_for(points):
    path = QPainterPath()
    for kind, data in segments(points):
        if kind == 'M':
            path.moveTo(*data)
        elif kind == 'L':
            path.lineTo(*data)
        else:
            path.quadTo(QPointF(*data[0]), QPointF(*data[1]))
    return path


class InkController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.view = tab, tab.viewer
        self.active = False
        self.pending, self.records = [], []
        self.history, self.future = [], []
        self.current, self.selected = None, None
        self._paths = {}
        self._live_path = QPainterPath()
        self.color = QColor('#1765cf')
        self.bar = QWidget(tab)
        self.bar.setObjectName('InkToolbar')
        layout = QHBoxLayout(self.bar)
        layout.setContentsMargins(12, 6, 12, 6)
        self.mode = QComboBox()
        self.mode.addItems([tr('Pennarello'), tr('Seleziona disegni')])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        layout.addWidget(self.mode)
        self.color_button = QPushButton(tr('Colore…'))
        self.color_button.clicked.connect(self.choose_color)
        layout.addWidget(self.color_button)
        self._color_style()
        layout.addWidget(QLabel(tr('Spessore')))
        self.width = QDoubleSpinBox()
        self.width.setRange(.5, 30)
        self.width.setValue(3)
        self.width.setSingleStep(.5)
        self.width.setSuffix(' pt')
        layout.addWidget(self.width)
        layout.addWidget(QLabel(tr('Opacità')))
        self.opacity = QSpinBox()
        self.opacity.setRange(10, 100)
        self.opacity.setValue(100)
        self.opacity.setSuffix('%')
        layout.addWidget(self.opacity)
        self.delete_button = QPushButton(tr('Elimina disegno'))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        layout.addWidget(self.delete_button)
        layout.addStretch()
        done = QPushButton(tr('Fine'))
        done.clicked.connect(self.deactivate)
        layout.addWidget(done)
        self.bar.hide()
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)

    @property
    def dirty(self):
        return bool(self.pending or self.current)

    def toggle(self):
        return self.deactivate() if self.active else self.activate()

    def activate(self):
        if not self.view.editable or self.tab.form_designer.isVisible():
            return False
        if not self.tab.inline_form.finish() or not self.tab.inline_text.deactivate():
            return False
        self.active = True
        self.view._cancel_placement()
        self.view.clear_selection()
        self.bar.show()
        self._mode_changed()
        self.view.setFocus()
        self.tab.message.emit(tr('Disegna sulla pagina. Seleziona un tratto per eliminarlo; Ctrl+Z annulla.'))
        self.tab.changed.emit()
        return True

    def deactivate(self):
        if not self.finish():
            return False
        self.active = False
        self.selected = None
        self.bar.hide()
        self.view.viewport().unsetCursor()
        self.view.viewport().update()
        self.tab.changed.emit()
        return True

    def _mode_changed(self, *_):
        self._end_stroke()
        self.selected = None
        self.delete_button.setEnabled(False)
        self._update_cursor()
        self.view.viewport().update()

    def _update_cursor(self):
        if self.active:
            cursor = (pen_cursor(self.color, self.view.viewport().devicePixelRatioF())
                      if self.mode.currentIndex() == 0 else Qt.CursorShape.ArrowCursor)
            self.view.viewport().setCursor(cursor)

    def _color_style(self):
        self.color_button.setStyleSheet('border-bottom: 4px solid ' + self.color.name() + ';')

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self.bar, tr('Colore del disegno'))
        if color.isValid():
            self.color = color
            self._color_style()
            self._update_cursor()

    def document_changed(self):
        self.pending.clear()
        self.history.clear()
        self.future.clear()
        self.current, self.selected = None, None
        self._paths.clear()
        self.delete_button.setEnabled(False)
        data = self.tab.editor.ink()
        self.records = data['rows']
        if data['truncated']:
            self.tab.message.emit(tr('Alcuni disegni complessi non sono selezionabili.'))

    def finish(self):
        self._end_stroke()
        if not self.pending:
            return True
        try:
            scroll = (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())
            self.tab.editor.update_ink(self.pending, [], str(self.tab.editor.path))
            self.tab._reload(self.tab.page)
            self.view.horizontalScrollBar().setValue(scroll[0])
            self.view.verticalScrollBar().setValue(scroll[1])
            self.tab.changed.emit()
            return True
        except PDFOperationError as exc:
            self.tab.message.emit(str(exc))
            return False

    def _end_stroke(self):
        if self.current is None:
            return
        row, self.current = self.current, None
        self.pending.append(row)
        self.history.append(('add', row, len(self.pending) - 1))
        self.future.clear()
        path = QPainterPath(self._live_path)
        if len(row['points']) == 1:
            path.lineTo(row['points'][0][0] + .01, row['points'][0][1])
        else:
            path.lineTo(*row['points'][-1])
        self._paths[id(row)] = path
        self.view.viewport().update()
        self.tab.changed.emit()

    def undo(self):
        self._end_stroke()
        if not self.history:
            return False
        kind, row, index = action = self.history.pop()
        if kind == 'add':
            self.pending.pop(index)
        else:
            self.pending.insert(index, row)
        self.future.append(action)
        self._selection(None)
        self.tab.changed.emit()
        return True

    def redo(self):
        if not self.future:
            return False
        kind, row, index = action = self.future.pop()
        if kind == 'add':
            self.pending.insert(index, row)
        else:
            self.pending.pop(index)
        self.history.append(action)
        self._selection(None)
        self.tab.changed.emit()
        return True

    def _selection(self, row):
        self.selected = row
        self.delete_button.setEnabled(row is not None)
        self.view.viewport().update()

    def delete_selected(self):
        row = self.selected
        if row is None:
            return
        if 'points' in row:
            index = next(i for i, item in enumerate(self.pending) if item is row)
            del self.pending[index]
            self.history.append(('remove', row, index))
            self.future.clear()
            self._selection(None)
            self.tab.changed.emit()
            return
        target = [row['page'], row['index']]
        if not self.finish():
            return
        try:
            self.tab.apply_edit('update_ink', [], [target], str(self.tab.editor.path))
        except PDFOperationError as exc:
            self.tab.message.emit(str(exc))

    def _path(self, row):
        key = id(row)
        if key not in self._paths:
            path = QPainterPath()
            for points in row.get('paths', [row.get('points')]):
                path.addPath(path_for(points))
            self._paths[key] = path
        return self._paths[key]

    def _hit(self, page, point):
        scale = max(.01, self.view.page_transform(page).m11())
        for row in reversed(self.records + self.pending):
            if row['page'] != page:
                continue
            path = self._path(row)
            width = max(row['width'] + 8 / scale, 10 / scale)
            if not path.boundingRect().adjusted(-width/2, -width/2, width/2, width/2).contains(point):
                continue
            stroker = QPainterPathStroker()
            stroker.setWidth(width)
            if stroker.createStroke(path).contains(point):
                return row
        return None

    def _append_point(self, position):
        hit = self.view.point_on_page(position)
        if not self.current or not hit or hit[0] != self.current['page']:
            return
        points, point = self.current['points'], hit[1]
        scale = max(.01, self.view.page_transform(hit[0]).m11())
        if (point - QPointF(*points[-1])).manhattanLength() * scale < .6:
            return
        points.append((point.x(), point.y()))
        if len(points) >= 3:
            a, b = points[-2:]
            self._live_path.quadTo(QPointF(*a), QPointF((a[0]+b[0])/2, (a[1]+b[1])/2))
        self.view.viewport().update()
        if len(points) == 8000:
            self._end_stroke()
            self.tab.message.emit(tr('Tratto completato. Rilascia il pulsante per iniziarne un altro.'))

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        kind = event.type()
        if kind == QEvent.Type.DevicePixelRatioChange:
            self._update_cursor()
        if kind == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_selected()
                return True
            if event.key() == Qt.Key.Key_Escape:
                if self.current:
                    self.current = None
                    self.view.viewport().update()
                    self.tab.changed.emit()
                else:
                    self.deactivate()
                return True
            if event.matches(QKeySequence.StandardKey.Undo):
                self.tab.undo()
                return True
            if event.matches(QKeySequence.StandardKey.Redo):
                self.tab.redo()
                return True
        if watched is not self.view.viewport():
            return False
        if kind in (QEvent.Type.Wheel, QEvent.Type.UngrabMouse, QEvent.Type.Hide):
            self._end_stroke()
        if kind in (QEvent.Type.ContextMenu, QEvent.Type.MouseButtonDblClick):
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            hit = self.view.point_on_page(event.position())
            self.view.setFocus()
            if self.mode.currentIndex() == 1:
                self._selection(self._hit(*hit) if hit else None)
            elif hit:
                if len(self.pending) >= MAX_STROKES or sum(len(r['points']) for r in self.pending) >= MAX_POINTS - 8000:
                    if not self.finish():
                        return True
                    # A reload can change layout; wait for the next gesture.
                    return True
                page, point = hit
                self.current = dict(page=page, points=[(point.x(), point.y())],
                    color=[self.color.redF(), self.color.greenF(), self.color.blueF()],
                    width=self.width.value(), opacity=self.opacity.value() / 100)
                self._live_path = QPainterPath(point)
                self._selection(None)
                self.tab.changed.emit()
            return True
        if kind == QEvent.Type.MouseMove and self.current:
            self._append_point(event.position())
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.current:
                self._append_point(event.position())
            self._end_stroke()
            return True
        # Block text selection and its worker calls while the pen is active.
        if kind == QEvent.Type.MouseMove and not self.view._pan_position:
            return True
        return False

    def paint(self, painter, page, transform):
        if not self.pending and not self.current and self.selected is None:
            return
        painter.save()
        painter.setWorldTransform(transform)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        size = self.tab.pdf.pagePointSize(page)
        painter.setClipRect(QRectF(0, 0, size.width(), size.height()))
        for row in self.pending + ([self.current] if self.current else []):
            if row['page'] != page:
                continue
            color = QColor.fromRgbF(*row['color'], row['opacity'])
            pen = QPen(color, row['width'], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if row is self.current:
                path = QPainterPath(self._live_path)
                x, y = row['points'][-1]
                path.lineTo(x + (.01 if len(row['points']) == 1 else 0), y)
            else:
                path = self._path(row)
            painter.drawPath(path)
        if self.selected and self.selected['page'] == page:
            scale = max(.01, transform.m11())
            painter.setPen(QPen(QColor('#1685f8'), 1.5 / scale, Qt.PenStyle.DashLine))
            painter.drawRect(self._path(self.selected).boundingRect().adjusted(-4, -4, 4, 4))
        painter.restore()

    def shutdown(self):
        self.active = False
        self.bar.hide()
        self.view.viewport().removeEventFilter(self)
        self.view.removeEventFilter(self)
