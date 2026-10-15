"""Native field controls placed directly on the PDF viewport."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QRadioButton,
)

from ..core.errors import PDFOperationError
from ..core.localization import tr
from ..core.pdf_forms import COMBO, EDIT_CHOICE, MULTILINE, MULTISELECT, PASSWORD, validate_changes
from .icons import icon


class FieldCombo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        size = max(6, min(14, self.height() - 4))
        icon("down", "#172b45").paint(painter, QRect(self.width() - 17, (self.height() - size) // 2, size, size))
        painter.end()


class InlineForms(QObject):
    changed = Signal()

    def __init__(self, view, revision, save):
        super().__init__(view)
        self.view = view
        self.viewport = view.viewport()
        self._closed = False
        self.revision = revision
        self.save = save
        self.control = None
        self.field = None
        self.key = None
        self.last_key = None
        self._revision = None
        self._finishing = False
        self._positioning = False
        self._original_value = None
        self._initializing = False
        self._style_key = None
        self._focus_check = QTimer(self)
        self._focus_check.setSingleShot(True)
        self._focus_check.timeout.connect(self._leave_field)
        QApplication.instance().focusChanged.connect(self._focus_changed)
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        view.horizontalScrollBar().valueChanged.connect(self.reposition)
        view.verticalScrollBar().valueChanged.connect(self.reposition)
        view.zoomFactorChanged.connect(self.reposition)
        view.zoomModeChanged.connect(self.reposition)

    @property
    def dirty(self):
        return self.control is not None and self.value() != self._original_value

    def entries(self):
        return sorted((page, rect[1], rect[0], field.name, index)
                      for field in self.view.form_fields if not field.readonly and self.view.editable
                      for index, (page, rect) in enumerate(field.widgets)
                      if rect[2] > rect[0] and rect[3] > rect[1])

    def start(self, name=None, position=None, index=None, *, clicked=False):
        entries = self.entries()
        if not entries:
            self.view.message.emit(tr("Il PDF è in sola lettura: i campi non possono essere modificati."))
            return False
        if name is None:
            entry = next((entry for entry in entries if entry[0] == self.view.pageNavigator().currentPage()), entries[0])
            name, index = entry[3], entry[4]
        field = next((field for field in self.view.form_fields if field.name == name), None)
        if field is None or field.readonly or not self.view.editable:
            self.view.message.emit(tr("Questo campo del modulo non può essere modificato."))
            return False
        if index is None:
            index = 0
            if position is not None:
                hit = self.view.point_on_page(position)
                if hit:
                    page, point = hit
                    index = next((i for i, (number, rect) in enumerate(field.widgets) if number == page and
                                  QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]).contains(point)), 0)
        if not 0 <= index < len(field.widgets):
            return False
        if self.key == (name, index) and self.control is not None:
            self.control.setFocus()
            return True
        if not self.finish():
            return False
        # A commit replaces the revision's field descriptors.
        field = next(field for field in self.view.form_fields if field.name == name)
        self.last_key = (name, index)
        if clicked and field.kind in ("checkbox", "radio"):
            state = field.widget_states[index]
            value = "/Off" if field.kind == "checkbox" and field.value not in ("", "/Off") else state
            if value == field.value:
                return True
            try:
                validate_changes((field,), {name: value})
                self.save({name: value}, self.revision())
            except PDFOperationError as exc:
                self.view.message.emit(str(exc))
                return False
            self.view.setFocus()
            return True
        self.field, self.key = field, (name, index)
        self._original_value = field.value
        self._revision = self.revision()
        self._initializing = True
        self.control = self._create_control(field, index)
        self._initializing = False
        self.control.setAccessibleName(field.label)
        self.control.setToolTip("<qt>" + escape(field.label) + "</qt>")
        self.control.setMinimumSize(0, 0)
        self.control.installEventFilter(self)
        if isinstance(self.control, QComboBox) and self.control.lineEdit():
            self.control.lineEdit().installEventFilter(self)
        if isinstance(self.control, (QPlainTextEdit, QListWidget)):
            self.control.viewport().installEventFilter(self)
        self._ensure_visible(field.widgets[index])
        self.reposition()
        self.control.show()
        self.control.raise_()
        self.control.setFocus(Qt.FocusReason.MouseFocusReason if clicked else Qt.FocusReason.TabFocusReason)
        if clicked and position is not None:
            local = self.control.mapFrom(self.viewport, position.toPoint())
            if isinstance(self.control, QLineEdit):
                self.control.setCursorPosition(self.control.cursorPositionAt(local))
            elif isinstance(self.control, QPlainTextEdit):
                self.control.setTextCursor(self.control.cursorForPosition(self.control.viewport().mapFrom(self.control, local)))
        self.view.message.emit(tr("Compila sulla pagina · Tab: campo successivo · Esc: annulla il campo · Salva: conserva nel PDF"))
        return True

    def _create_control(self, field, index):
        parent = self.view.viewport()
        if field.kind == "text":
            if field.flags & MULTILINE:
                control = QPlainTextEdit(parent)
                control.document().setDocumentMargin(1)
                control.setPlainText(str(field.value))
                control.textChanged.connect(self._value_changed)
            else:
                control = QLineEdit(str(field.value), parent)
                control.setMaxLength(field.max_length)
                if field.flags & PASSWORD:
                    control.setEchoMode(QLineEdit.EchoMode.Password)
                control.textChanged.connect(self._value_changed)
            return control
        if field.kind in ("checkbox", "radio"):
            control = (QRadioButton if field.kind == "radio" else QCheckBox)(parent)
            control.setAutoExclusive(False)
            control.setChecked(field.value == field.widget_states[index])
            control.toggled.connect(self._value_changed)
            return control
        if field.kind == "choice" and (not field.flags & COMBO or field.flags & MULTISELECT):
            control = QListWidget(parent)
            control.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection if field.flags & MULTISELECT else
                                     QAbstractItemView.SelectionMode.SingleSelection)
            selected = field.value if isinstance(field.value, tuple) else (field.value,)
            for value, label in field.options:
                item = QListWidgetItem(label, control)
                item.setData(Qt.ItemDataRole.UserRole, value)
                item.setSelected(value in selected)
            control.itemSelectionChanged.connect(self._value_changed)
            return control
        control = FieldCombo(parent)
        control.addItem(tr("Nessuna selezione"), "")
        for value, label in field.options:
            control.addItem(label, value)
        control.setEditable(bool(field.flags & EDIT_CHOICE))
        control.setCurrentIndex(control.findData(field.value))
        if control.isEditable() and control.currentIndex() < 0:
            control.setEditText(str(field.value))
        control.currentIndexChanged.connect(self._value_changed)
        if control.isEditable():
            control.editTextChanged.connect(self._value_changed)
        control.activated.connect(lambda *_args, target=control: QTimer.singleShot(
            0, lambda: self.finish() if self.control is target else None))
        return control

    def value(self):
        control = self.control
        if isinstance(control, QLineEdit):
            return control.text()
        if isinstance(control, QPlainTextEdit):
            return control.toPlainText()
        if isinstance(control, (QCheckBox, QRadioButton)):
            if isinstance(control, QRadioButton) and not control.isChecked() and self._original_value != self.field.widget_states[self.key[1]]:
                return self._original_value
            return self.field.widget_states[self.key[1]] if control.isChecked() else "/Off"
        if isinstance(control, QListWidget):
            selected = tuple(item.data(Qt.ItemDataRole.UserRole) for item in control.selectedItems())
            return selected if self.field.flags & MULTISELECT else (selected[0] if selected else "")
        if isinstance(control, QComboBox):
            if control.isEditable() and (control.currentIndex() < 0 or control.currentText() != control.itemText(control.currentIndex())):
                return control.currentText()
            return control.currentData() if control.currentIndex() >= 0 else self._original_value
        return self._original_value

    def _value_changed(self, *_args):
        if not self._initializing:
            self.changed.emit()

    def finish(self):
        if self.control is None or self._finishing:
            return True
        self._focus_check.stop()
        if not self.dirty:
            self.cancel()
            return True
        self._finishing = True
        try:
            changes = {self.field.name: self.value()}
            validate_changes((self.field,), changes)
            self.save(changes, self._revision)
        except PDFOperationError as exc:
            self.view.message.emit(str(exc))
            if self.control is not None:
                self.control.setToolTip("<qt>" + escape(str(exc)) + "</qt>")
                if self.view.isVisible():
                    self.control.setFocus()
            return False
        finally:
            self._finishing = False
        self.cancel()
        return True

    def cancel(self):
        self._focus_check.stop()
        control, self.control = self.control, None
        self.field, self.key = None, None
        self._style_key = None
        if control is not None:
            control.hide()
            control.deleteLater()
        self.changed.emit()

    def _ensure_visible(self, widget):
        page, rect = widget
        if page != self.view.pageNavigator().currentPage() or page not in self.view.page_rects():
            self.view.pageNavigator().jump(page, QPointF(), self.view.zoomFactor())
        target = self.view.page_transform(page).mapRect(QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]))
        for bar, start, end, extent in ((self.view.horizontalScrollBar(), target.left(), target.right(), self.view.viewport().width()),
                                         (self.view.verticalScrollBar(), target.top(), target.bottom(), self.view.viewport().height())):
            if start < 0:
                bar.setValue(bar.value() + round(start - 8))
            elif end > extent:
                bar.setValue(bar.value() + round(min(start - 8, end - extent + 8)))

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        QApplication.instance().focusChanged.disconnect(self._focus_changed)
        self.cancel()

    def reposition(self, *_args):
        if self.control is None or self._positioning:
            return
        self._positioning = True
        try:
            page, rect = self.field.widgets[self.key[1]]
            transform = self.view.page_transform(page)
            geometry = transform.mapRect(QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]))
            self.control.setGeometry(geometry.toAlignedRect())
            pixels = max(6, min(72, round(11 * abs(transform.m22()))))
            indicator = max(6, min(geometry.width(), geometry.height()) - 2)
            style_key = (pixels, indicator if isinstance(self.control, (QCheckBox, QRadioButton)) else None)
            if self._style_key != style_key:
                self.control.setStyleSheet(f"""
                    QWidget {{ font-size: {pixels}px; }}
                    QLineEdit, QPlainTextEdit, QComboBox, QListWidget {{
                        background: white; color: #172b45; border: 1px solid #3776b9;
                        border-radius: 1px; padding: 1px; margin: 0; min-height: 0; min-width: 0;
                    }}
                    QComboBox {{ padding-right: 18px; }}
                    QComboBox::drop-down {{ width: 16px; }}
                    QComboBox::down-arrow {{ width: 0; height: 0; }}
                    QCheckBox, QRadioButton {{ background: white; padding: 0; margin: 0; }}
                    QCheckBox::indicator, QRadioButton::indicator {{ width: {indicator}px; height: {indicator}px; }}
                """)
                if isinstance(self.control, QComboBox):
                    self.control.view().setStyleSheet(f"QWidget {{ font-size: {max(11, pixels)}px; }}")
                self._style_key = style_key
        finally:
            self._positioning = False

    def _focus_changed(self, *_args):
        if self.control is not None and not self._finishing:
            self._focus_check.start(0)

    def _leave_field(self):
        if self.control is None or QApplication.activePopupWidget() is not None:
            return
        focused = QApplication.focusWidget()
        if focused is self.control or (focused is not None and self.control.isAncestorOf(focused)):
            return
        self.finish()

    def next_field(self, backwards=False):
        keys = [(entry[3], entry[4]) for entry in self.entries()]
        if not keys:
            return False
        previous = self.key or self.last_key
        index = keys.index(previous) if previous in keys else (-1 if not backwards else 0)
        key = keys[(index + (-1 if backwards else 1)) % len(keys)]
        if self.finish():
            return self.start(key[0], index=key[1])
        return False

    def eventFilter(self, watched, event):
        if self._closed:
            return False
        if watched is self.view or watched is self.viewport:
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self.reposition)
            return False
        if self.control is None:
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                event.accept()
                return True
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel()
                self.view.setFocus()
                return True
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.next_field(event.key() == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                    not isinstance(self.control, QPlainTextEdit) or event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if self.finish():
                    self.view.setFocus()
                return True
        if event.type() in (QEvent.Type.FocusOut, QEvent.Type.Hide):
            self._focus_check.start(0)
        if event.type() == QEvent.Type.Wheel and event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            delta = event.angleDelta().y()
            exponent = delta / 120 if delta else event.pixelDelta().y() / 60
            if exponent:
                position = QPointF(self.viewport.mapFromGlobal(event.globalPosition().toPoint()))
                self.view.zoom_to(self.view.effective_zoom() * 1.2 ** max(-4, min(4, exponent)), position)
            event.accept()
            return True
        return False
