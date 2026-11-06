"""Local drawing and precise erasing; gestures never perform synchronous RPC."""
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QColorDialog

from ..core.errors import PDFOperationError
from ..core.ink import MAX_POINTS, MAX_STROKES
from ..core.ink_geometry import cut_paths, segments
from ..core.localization import tr
from .icons import pen_cursor, eraser_cursor
from .ink_toolbar import DrawingToolbar
from .theme import theme_color


def path_for(points, smooth=True):
    path = QPainterPath()
    for kind, data in segments(points, smooth):
        if kind == 'M':
            path.moveTo(*data)
        elif kind == 'L':
            path.lineTo(*data)
        else:
            path.quadTo(QPointF(*data[0]), QPointF(*data[1]))
    return path


def chunks(points):
    for start in range(0, len(points), 7999):
        yield points[start:start+8000]


class InkController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.view = tab, tab.viewer
        self.active = False
        self.pending, self.records = [], []
        self.edits = {}
        self.history, self.future = [], []
        self.current, self.selected = None, None
        self._paths = {}
        self._erase_before = self._erase_last = self._hover = None
        self.bar = DrawingToolbar(tab)
        self.width, self.opacity = self.bar.width_control, self.bar.opacity
        self.delete_button = self.bar.delete_button
        self.color_button = self.bar.settings_button
        self.bar.tool_changed.connect(self._mode_changed)
        self.bar.style_changed.connect(self._style_changed)
        self.bar.custom_color_requested.connect(self.choose_color)
        self.bar.delete_action.triggered.connect(self.delete_selected)
        self.bar.done_action.triggered.connect(self.deactivate)
        self.bar.delete_action.setEnabled(False)
        self.bar.hide()
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)

    @property
    def color(self):
        return self.bar.color

    @property
    def dirty(self):
        return bool(self.pending or self.edits or self.current)

    def set_tool(self, tool):
        self.bar.set_tool(tool)

    def toggle(self):
        return self.deactivate() if self.active else self.activate()

    def activate(self):
        if not self.view.editable or self.tab.form_designer.isVisible():
            return False
        if not self.tab.signature.finish() or not self.tab.inline_form.finish() or not self.tab.inline_text.deactivate():
            return False
        self.active = True
        self.view._cancel_placement()
        self.view.clear_selection()
        self.bar.show()
        # Tiles omit only editable Ink; the canvas paints it above the page.
        self.view._document_status()
        self._mode_changed()
        self.view.setFocus()
        self.tab.message.emit(tr('Disegna o cancella parti dei tratti. Ctrl+Z annulla; Fine applica le modifiche.'))
        self.tab.changed.emit()
        return True

    def deactivate(self):
        if not self.finish():
            return False
        self.active = False
        self.selected = self._hover = None
        self.bar.menu.hide()
        self.bar.hide()
        self.view._document_status()
        self.view.viewport().unsetCursor()
        self.tab.changed.emit()
        return True

    def _mode_changed(self, *_):
        self._end_stroke()
        self._end_erase()
        self.selected = self._hover = None
        self.bar.delete_action.setEnabled(False)
        self._update_cursor()
        self.view.viewport().update()

    def _style_changed(self):
        self._end_stroke()
        self._end_erase()
        self._update_cursor()
        self.view.viewport().update()

    def _update_cursor(self):
        if self.active:
            ratio = self.view.viewport().devicePixelRatioF()
            cursor = (eraser_cursor(ratio) if self.bar.tool == 'eraser' else
                Qt.CursorShape.ArrowCursor if self.bar.tool == 'select' else pen_cursor(self.color, ratio))
            self.view.viewport().setCursor(cursor)

    def choose_color(self):
        self.bar.set_color(QColorDialog.getColor(self.color, self.bar, tr('Colore del disegno')))

    def document_changed(self):
        self.pending.clear()
        self.edits.clear()
        self.history.clear()
        self.future.clear()
        self.current = self.selected = self._erase_before = self._erase_last = self._hover = None
        self._paths.clear()
        self.bar.delete_action.setEnabled(False)
        data = self.tab.editor.ink()
        self.records = data['rows']
        if data['truncated']:
            self.tab.message.emit(tr('Alcuni disegni complessi non sono selezionabili.'))

    def finish(self):
        self._end_stroke()
        self._end_erase()
        if not self.pending and not self.edits:
            return True
        try:
            scroll = (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())
            deleted = [list(key) for key, value in self.edits.items() if value is None]
            replaced = [dict(page=row['page'], index=row['index'], paths=row['paths'], smooth=row['smooth'])
                        for row in self.edits.values() if row is not None]
            self.tab.editor.update_ink(self.pending, deleted, str(self.tab.editor.path), replaced)
            self.tab._reload(self.tab.page)
            self.view.horizontalScrollBar().setValue(scroll[0])
            self.view.verticalScrollBar().setValue(scroll[1])
            self.tab.changed.emit()
            self._update_cursor()
            return True
        except PDFOperationError as exc:
            self.tab.message.emit(str(exc))
            return False

    def _state(self):
        return list(self.pending), dict(self.edits)

    def _remember(self, before):
        after = self._state()
        if before == after:
            return
        self.history.append((before, after))
        self.future.clear()
        # Unchanged geometry is shared; bound retained changed points as well.
        while len(self.history) > 1:
            seen, count = set(), 0
            for action in self.history:
                for pending, edits in action:
                    for row in pending + [v for v in edits.values() if v is not None]:
                        if id(row) not in seen:
                            seen.add(id(row))
                            count += sum(map(len, row.get('paths', [row.get('points', [])])))
            if count <= 250000 and len(self.history) <= 256:
                break
            self.history.pop(0)
            self._paths.clear()
        self.tab.changed.emit()

    def _end_stroke(self):
        if self.current is not None:
            before = self._state()
            row = self.current
            path = QPainterPath(self._live_path)
            x, y = row['points'][-1]
            path.lineTo(x + (.01 if len(row['points']) == 1 else 0), y)
            self._paths[id(row)] = path
            self.pending.append(self.current)
            self.current = None
            self._remember(before)
            self.view.viewport().update()

    def _end_erase(self):
        if self._erase_before is not None:
            before, self._erase_before = self._erase_before, None
            self._erase_last = None
            self._remember(before)

    def _restore(self, state):
        self.pending, self.edits = list(state[0]), dict(state[1])
        self._paths.clear()
        self._selection(None)
        self.tab.changed.emit()

    def undo(self):
        self._end_stroke()
        self._end_erase()
        if not self.history:
            return False
        action = self.history.pop()
        self.future.append(action)
        self._restore(action[0])
        return True

    def redo(self):
        if not self.future:
            return False
        action = self.future.pop()
        self.history.append(action)
        self._restore(action[1])
        return True

    def visible_records(self):
        return [value for row in self.records
                if (value := self.edits.get((row['page'], row['index']), row)) is not None]

    def _selection(self, row):
        self.selected = row
        self.bar.delete_action.setEnabled(row is not None)
        self.view.viewport().update()

    def delete_selected(self):
        row = self.selected
        if row is None:
            return
        before = self._state()
        if 'points' in row:
            self.pending = [value for value in self.pending if value is not row]
        else:
            self.edits[(row['page'], row['index'])] = None
        self._remember(before)
        self._selection(None)

    def _path(self, row):
        key = id(row)
        if key not in self._paths:
            path = QPainterPath()
            for points in row.get('paths', [row.get('points')]):
                path.addPath(path_for(points, row.get('smooth', True)))
            self._paths[key] = path
        return self._paths[key]

    def _hit(self, page, point):
        scale = max(.01, self.view.page_transform(page).m11())
        for row in reversed(self.visible_records() + self.pending):
            if row['page'] != page:
                continue
            path = self._path(row)
            width = max(row['width'] + 8/scale, 10/scale)
            if path.boundingRect().adjusted(-width/2, -width/2, width/2, width/2).contains(point):
                stroker = QPainterPathStroker()
                stroker.setWidth(width)
                if stroker.createStroke(path).contains(point):
                    return row
        return None

    def _erase_piece(self, page, start, end):
        radius = self.width.value()/2
        swept = QRectF(QPointF(*start), QPointF(*end)).normalized().adjusted(-radius, -radius, radius, radius)
        pending, edits = [], dict(self.edits)
        try:
            for row in self.visible_records() + self.pending:
                paths = None
                if row['page'] == page and self._path(row).boundingRect().adjusted(
                        -row['width']/2, -row['width']/2, row['width']/2, row['width']/2).intersects(swept):
                    paths = cut_paths(row.get('paths', [row.get('points')]), start, end,
                                      radius, row['width'], row.get('smooth', True))
                if 'points' in row:
                    if paths is None:
                        pending.append(row)
                    else:
                        pending.extend({**row, 'points': part, 'smooth': False}
                                       for path in paths for part in chunks(path))
                elif paths is not None:
                    edits[(row['page'], row['index'])] = ({**row, 'paths': [part for path in paths for part in chunks(path)],
                        'smooth': False} if paths else None)
            replacements = [v for v in edits.values() if v is not None]
            points = sum(len(row['points']) for row in pending) + sum(sum(map(len, row['paths'])) for row in replacements)
            if (len(pending) > MAX_STROKES or points > MAX_POINTS or
                    any(len(row['paths']) > 2000 for row in replacements)):
                raise ValueError('Ink geometry exceeds budget')
            self.pending, self.edits = pending, edits
            self._paths.clear()
            self.view.viewport().update()
            self.tab.changed.emit()
        except ValueError:
            self._end_erase()
            self.tab.message.emit(tr('Disegno troppo complesso per questa cancellazione. Applica le modifiche o usa una gomma più piccola.'))

    def _append_point(self, position):
        hit = self.view.point_on_page(position)
        if not self.current or not hit or hit[0] != self.current['page']:
            return
        points, point = self.current['points'], hit[1]
        scale = max(.01, self.view.page_transform(hit[0]).m11())
        if (point - QPointF(*points[-1])).manhattanLength()*scale < .6:
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
                self.delete_selected(); return True
            if event.key() == Qt.Key.Key_Escape:
                if self.current:
                    self.current = None
                elif self._erase_before is not None:
                    before = self._erase_before
                    self._erase_before = self._erase_last = None
                    self._restore(before)
                else:
                    self.deactivate()
                self.view.viewport().update()
                self.tab.changed.emit()
                return True
            if event.matches(QKeySequence.StandardKey.Undo):
                self.tab.undo(); return True
            if event.matches(QKeySequence.StandardKey.Redo):
                self.tab.redo(); return True
        if watched is not self.view.viewport():
            return False
        if kind in (QEvent.Type.Wheel, QEvent.Type.UngrabMouse, QEvent.Type.Hide):
            self._end_stroke(); self._end_erase()
        if kind in (QEvent.Type.ContextMenu, QEvent.Type.MouseButtonDblClick):
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.MiddleButton:
            self._end_stroke(); self._end_erase()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            hit = self.view.point_on_page(event.position())
            self.view.setFocus()
            if self.bar.tool == 'select':
                self._selection(self._hit(*hit) if hit else None)
            elif hit:
                page, point = hit
                if self.bar.tool == 'eraser':
                    self._erase_before = self._state()
                    self._erase_last = (page, (point.x(), point.y()))
                    self._erase_piece(page, self._erase_last[1], self._erase_last[1])
                else:
                    draft_points = sum(len(r['points']) for r in self.pending) + sum(
                        sum(map(len, r['paths'])) for r in self.edits.values() if r is not None)
                    if len(self.pending) >= MAX_STROKES or draft_points >= MAX_POINTS-8000:
                        self.finish(); return True
                    self.current = dict(page=page, points=[(point.x(), point.y())],
                        color=[self.color.redF(), self.color.greenF(), self.color.blueF()],
                        width=self.width.value(), opacity=self.opacity.value()/100, smooth=True)
                    self._live_path = QPainterPath(point)
                    self._selection(None)
                    self.tab.changed.emit()
            return True
        if kind == QEvent.Type.MouseMove and not self.view._pan_position:
            if self.current:
                self._append_point(event.position())
            elif self.bar.tool == 'eraser':
                hit = self.view.point_on_page(event.position())
                self._hover = hit
                if self._erase_before is not None and hit:
                    point = (hit[1].x(), hit[1].y())
                    start = self._erase_last[1] if self._erase_last and hit[0] == self._erase_last[0] else point
                    self._erase_piece(hit[0], start, point)
                    if self._erase_before is not None:
                        self._erase_last = (hit[0], point)
                elif self._erase_last:
                    self._erase_last = None
                self.view.viewport().update()
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.current:
                self._append_point(event.position())
            if self._erase_last:
                hit = self.view.point_on_page(event.position())
                if hit and hit[0] == self._erase_last[0]:
                    self._erase_piece(hit[0], self._erase_last[1], (hit[1].x(), hit[1].y()))
            self._end_stroke(); self._end_erase()
            return True
        return False

    def paint(self, painter, page, transform):
        if not self.active and not self.pending:
            return
        painter.save()
        painter.setWorldTransform(transform)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        size = self.tab.pdf.pagePointSize(page)
        painter.setClipRect(QRectF(0, 0, size.width(), size.height()))
        rows = (self.visible_records() if self.active else []) + self.pending + ([self.current] if self.current else [])
        for row in rows:
            if row['page'] == page:
                color = QColor.fromRgbF(*row['color'], row['opacity'])
                painter.setPen(QPen(color, row['width'], Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                if row is self.current:
                    path = QPainterPath(self._live_path)
                    x, y = row['points'][-1]
                    path.lineTo(x + (.01 if len(row['points']) == 1 else 0), y)
                else:
                    path = self._path(row)
                painter.drawPath(path)
        scale = max(.01, transform.m11())
        if self.selected and self.selected['page'] == page:
            painter.setPen(QPen(QColor(theme_color('accent')), 1.5/scale, Qt.PenStyle.DashLine))
            painter.drawRect(self._path(self.selected).boundingRect().adjusted(-4, -4, 4, 4))
        if self.bar.tool == 'eraser' and self._hover and self._hover[0] == page:
            radius = self.width.value()/2
            painter.setPen(QPen(QColor('white'), 3/scale))
            painter.drawEllipse(self._hover[1], radius, radius)
            painter.setPen(QPen(QColor(theme_color('accent')), 1/scale))
            painter.drawEllipse(self._hover[1], radius, radius)
        painter.restore()

    def shutdown(self):
        self.active = False
        self.bar.menu.hide()
        self.bar.hide()
        self.view.viewport().removeEventFilter(self)
        self.view.removeEventFilter(self)
