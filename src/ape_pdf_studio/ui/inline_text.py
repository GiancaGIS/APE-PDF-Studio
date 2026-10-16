"""Direct text editing on the viewport; all PDF reads remain sandboxed RPCs."""
from collections import OrderedDict

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextOption, QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..core.errors import PDFOperationError
from ..core.localization import tr
from ..core.paragraph_edit import ParagraphTarget
from .content_dialogs import TextEditDialog, _TextSignals, _TextTask, plain_label
from .theme import theme_color


class ParagraphEdit(QPlainTextEdit):
    """Plain text only: PDF strings can never become HTML or external resources."""
    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)


class InlineText(QObject):
    changed = Signal()

    def __init__(self, tab):
        super().__init__(tab.viewer)
        self.tab, self.view = tab, tab.viewer
        self.active = False
        self.control = None
        self.target = None
        self._epoch = 0
        self._closed = False
        self._finishing = False
        self._loading = False
        self._font_running = False
        self._font_cache = OrderedDict()
        self._preview_bbox = None
        self.boxes = OrderedDict()
        self._box_pending = set()
        self._signals = _TextSignals(self)
        self._signals.completed.connect(self._completed)
        self._font_timer = QTimer(self)
        self._font_timer.setSingleShot(True)
        self._font_timer.timeout.connect(self._font_info)

        self.bar = QFrame(self.view.viewport())
        self.bar.setObjectName("InlineTextToolbar")
        self.bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self.bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.addWidget(plain_label(tr("Modifica testo"), "SectionTitle"))
        row.addStretch()
        row.addWidget(plain_label(tr("Dimensione")))
        self.size = QDoubleSpinBox()
        self.size.setRange(.5, 300)
        self.size.setDecimals(2)
        self.size.setSingleStep(.5)
        self.size.setSuffix(" pt")
        self.size.setFixedWidth(90)
        self.size.setAccessibleName(tr("Dimensione"))
        self.size.valueChanged.connect(self._value_changed)
        self.size.installEventFilter(self)
        row.addWidget(self.size)
        self.apply = QPushButton(tr("Applica"))
        self.apply.setObjectName("Primary")
        self.apply.clicked.connect(self.finish)
        row.addWidget(self.apply)
        self.cancel_button = QPushButton(tr("Annulla"))
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        self.done_button = QPushButton(tr("Fine"))
        self.done_button.clicked.connect(self.deactivate)
        row.addWidget(self.done_button)
        layout.addLayout(row)
        options = QHBoxLayout()
        self.reflow = QCheckBox(tr("Riflusso paragrafi"))
        self.reflow.setChecked(True)
        self.reflow.toggled.connect(self._mode_changed)
        options.addWidget(self.reflow)
        self.alignment = QComboBox()
        self.alignment.addItems([tr("Sinistra"), tr("Centro"), tr("Destra"), tr("Giustificato")])
        self.alignment.setAccessibleName(tr("Allineamento"))
        self.alignment.currentIndexChanged.connect(self._value_changed)
        options.addWidget(self.alignment)
        self.font_label = plain_label("", "Caption")
        options.addWidget(self.font_label, 1)
        layout.addLayout(options)
        self.feedback = plain_label("", "EditFeedback")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.bar.hide()
        self.view.viewport().installEventFilter(self)
        self.view.horizontalScrollBar().valueChanged.connect(self.reposition)
        self.view.verticalScrollBar().valueChanged.connect(self.reposition)
        self.view.zoomFactorChanged.connect(self.reposition)
        self.view.zoomModeChanged.connect(self.reposition)
        self.view.pageNavigator().currentPageChanged.connect(self._page_changed)

    @property
    def dirty(self):
        return self.control is not None and self.target is not None and (
            self.control.text() != self.target.text or self.size.value() != self._initial_size
            or (isinstance(self.target, ParagraphTarget) and self.alignment.currentIndex() != self.target.alignment))

    def _mode_changed(self, checked):
        if not self.finish():
            with QSignalBlocker(self.reflow):
                self.reflow.setChecked(not checked)
            return
        self.boxes.clear()
        if self.active:
            self._idle()
            self._page_changed()
            self.reposition()
            self.view.viewport().update()

    def activate(self):
        if self._closed or not self.view.editable or self.tab.form_designer.isVisible():
            return False
        if not self.tab.inline_form.finish():
            return False
        self.active = True
        self.view._cancel_placement()
        self.view.clear_selection()
        self.feedback.clear()
        self.feedback.hide()
        self.bar.show()
        self._idle()
        self._page_changed()
        self.reposition()
        self.view.setFocus()
        self.changed.emit()
        return True

    def toggle(self):
        result = self.deactivate() if self.active else self.activate()
        # A failed validation must restore the checkable toolbar action too.
        self.changed.emit()
        return result

    def deactivate(self):
        if not self.finish():
            return False
        self.active = False
        self.boxes.clear()
        self.bar.hide()
        self.view.viewport().update()
        self.changed.emit()
        return True

    def _idle(self):
        self.font_label.setText(tr("Invio: nuova riga · Ctrl+Invio: applica · Esc: annulla") if self.reflow.isChecked() else
                               tr("Fai clic su un riquadro e modifica il testo sulla pagina. Invio: applica · Esc: annulla."))
        self.size.setEnabled(False)
        self.alignment.setEnabled(False)
        self.apply.setEnabled(False)

    def _task(self, kind, key, operation):
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().start(_TextTask(kind, key, operation, self._signals), 1)

    def _page_changed(self, *_):
        if not self.active or self._closed:
            return
        page = self.view.pageNavigator().currentPage()
        revision = self.tab.editor.path
        key = (revision, page, self.reflow.isChecked())
        # Keep only one page scan in flight while the user scrolls quickly.
        if page not in self.boxes and not self._box_pending:
            self._box_pending.add(key)
            editor = self.tab.editor
            resolver = editor.paragraph_boxes if self.reflow.isChecked() else editor.text_boxes
            self._task("boxes", key, lambda: resolver(page))

    def start(self, page, point):
        if not self.finish() or (not self.active and not self.activate()):
            return False
        self._loading = True
        self.feedback.clear()
        self.feedback.hide()
        self.font_label.setText(tr("Lettura del testo in corso…"))
        editor = self.tab.editor
        key = (self._epoch, editor.path)
        position = (point.x(), point.y())
        self._cursor_position = page, QPointF(point)
        resolver = editor.paragraph_at if self.reflow.isChecked() else editor.text_at
        self._task("target", key, lambda: resolver(page, position))
        self.reposition()
        return True

    def _completed(self, kind, key, value, error):
        if self._closed:
            return
        if kind == "boxes":
            self._box_pending.discard(key)
            if self.active and key[0] == self.tab.editor.path and key[2] == self.reflow.isChecked():
                self.boxes[key[1]] = value if not error else []
                while len(self.boxes) > 3:
                    self.boxes.popitem(last=False)
                self.view.viewport().update()
            self._page_changed()
            return
        if not self.active or key[0] != self._epoch:
            return
        if kind == "target":
            self._loading = False
            if key[1] != self.tab.editor.path:
                error = tr("Il documento è cambiato. Seleziona nuovamente il testo.")
            if error:
                self._idle()
                self.feedback.setText(error)
                self.feedback.show()
                self.reposition()
                return
            self.target = value
            self._font_cache.clear()
            with QSignalBlocker(self.size):
                self.size.setValue(value.size)
            self._initial_size = self.size.value()
            self.size.setEnabled(True)
            paragraph = isinstance(value, ParagraphTarget)
            with QSignalBlocker(self.alignment):
                self.alignment.setCurrentIndex(value.alignment if paragraph else 0)
            self.alignment.setEnabled(paragraph)
            self.apply.setEnabled(True)
            if paragraph:
                self.control = ParagraphEdit(self.view.viewport())
                self.control.setPlainText(value.text)
                self.control.setTabChangesFocus(True)
                self.control.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
                self.control.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
                self.control.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                self.control.document().setDocumentMargin(1)
                self.control.viewport().installEventFilter(self)
            else:
                self.control = QLineEdit(value.text, self.view.viewport())
                self.control.setMaxLength(4000)
            self.control.setAccessibleName(tr("Modifica testo sulla pagina"))
            self.control.setMinimumSize(0, 0)
            self.control.installEventFilter(self)
            self.control.textChanged.connect(self._value_changed)
            self.view.edit_target = value.page, value.display_bbox
            self.reposition()
            self.control.show()
            self.control.raise_()
            self.control.setFocus(Qt.FocusReason.MouseFocusReason)
            page, point = self._cursor_position
            position = self.view.page_transform(page).map(point).toPoint()
            local = self.control.mapFrom(self.view.viewport(), position)
            if paragraph:
                self.control.setTextCursor(self.control.cursorForPosition(self.control.viewport().mapFrom(self.control, local)))
            else:
                self.control.setCursorPosition(self.control.cursorPositionAt(local))
            self._font_info()
            return
        self._font_running = False
        if self.control is None:
            return
        text = key[1]
        if not error:
            self._font_cache[text] = value
            while len(self._font_cache) > 16:
                self._font_cache.popitem(last=False)
        current = self._layout_key() if kind == 'paragraph' else self.control.text()
        if text == current:
            if error:
                self.font_label.setText(error)
            else:
                self._show_paragraph(value) if kind == 'paragraph' else self._show_font(value)
            self.reposition()
        elif not self._font_timer.isActive():
            self._font_info()

    def _show_font(self, value):
        name, fallback = value
        self.font_label.setText(tr("Font di scrittura: ") + name +
                               (tr(" · font compatibile") if fallback else tr(" · font originale")))

    def _layout_key(self):
        size = self.target.size if self.size.value() == self._initial_size else self.size.value()
        return self.control.text(), size, self.alignment.currentIndex()

    def _show_paragraph(self, value):
        self._preview_bbox = value['bbox']
        self.font_label.setText(tr('Paragrafo: {0} righe · font: {1}', value['lines'], value['font']) +
                               (tr(' · font compatibile') if value['fallback'] else ''))
        self.feedback.setText(value['message'])
        self.feedback.setVisible(not value['fits'])
        self.view.edit_target = self.target.page, value['bbox']
        self.reposition()
        self.view.viewport().update()

    def _font_info(self):
        if self.control is None or self._closed:
            return
        paragraph = isinstance(self.target, ParagraphTarget)
        text = self._layout_key() if paragraph else self.control.text()
        if text in self._font_cache:
            self._show_paragraph(self._font_cache[text]) if paragraph else self._show_font(self._font_cache[text])
            return
        if self._font_running:
            return
        self._font_running = True
        self.font_label.setText(tr("Analisi del font in corso…"))
        editor, target = self.tab.editor, self.target
        if paragraph:
            self._task('paragraph', (self._epoch, text), lambda: editor.paragraph_info(target, *text))
        else:
            self._task("font", (self._epoch, text), lambda: TextEditDialog._resolve_font(
                lambda target, text: editor.call('font_details', target, text), target, text))

    def _value_changed(self, *_):
        if self.control is not None:
            self._font_timer.start(200)
            self.feedback.clear()
            self.feedback.hide()
            self.reposition()
            self.changed.emit()

    def finish(self):
        if self._closed or self._finishing:
            return True
        if not self.dirty:
            self.cancel()
            return True
        target, text = self.target, self.control.text()
        size = target.size if self.size.value() == self._initial_size else self.size.value()
        self._finishing = True
        try:
            if isinstance(target, ParagraphTarget):
                self.tab.apply_edit('replace_paragraph', target, text, size, self.alignment.currentIndex())
            else:
                self.tab.apply_edit("replace_text", target, text, size)
        except PDFOperationError as exc:
            self.feedback.setText(str(exc))
            self.feedback.show()
            self.reposition()
            if self.control is not None:
                self.control.setFocus()
            return False
        finally:
            self._finishing = False
        self.cancel()
        return True

    def cancel(self):
        self._epoch += 1
        self._font_timer.stop()
        self._font_running = False
        self._loading = False
        control, self.control = self.control, None
        self.target = None
        self._preview_bbox = None
        self.view.edit_target = None
        if control is not None:
            control.hide()
            control.deleteLater()
        self.feedback.clear()
        self.feedback.hide()
        if self.active:
            self._idle()
        self.view.viewport().update()
        self.changed.emit()

    def document_changed(self):
        self.cancel()
        self.boxes.clear()
        self._page_changed()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.active = False
        self.cancel()
        self.boxes.clear()
        self.bar.hide()

    def reposition(self, *_):
        if not self.active:
            return
        viewport = self.view.viewport()
        self.bar.setStyleSheet(
            f"QFrame#InlineTextToolbar {{ background: {theme_color('surface')}; "
            f"border: 1px solid {theme_color('strong_border')}; border-radius: 8px; }}")
        self.bar.setFixedWidth(min(660, max(120, viewport.width() - 24)))
        self.bar.adjustSize()
        bar_y = 12
        if self.control is not None and self.target is not None:
            paragraph = isinstance(self.target, ParagraphTarget)
            if self.target.page not in self.view.page_rects():
                self.control.hide()
                return
            left, top, right, bottom = self._preview_bbox or self.target.display_bbox
            transform = self.view.page_transform(self.target.page)
            rect = transform.mapRect(QRectF(left, top, right - left, bottom - top)).adjusted(-3, -2, 3, 2)
            height = max(48 if paragraph else 24, round(rect.height()))
            self.control.setGeometry(round(rect.left()), round(rect.top()), max(24, round(rect.width())), height)
            font = QFont(self.target.font.split('+')[-1])
            font.setBold(bool(self.target.flags & 16))
            font.setItalic(bool(self.target.flags & 2))
            pixels = max(6, min(240, round(self.size.value() * abs(transform.m11()))))
            font.setPixelSize(pixels)
            self.control.setFont(font)
            name = self.target.font.lower()
            family = ('Courier New' if any(part in name for part in ('cour', 'mono')) else
                      'Times New Roman' if any(part in name for part in ('times', 'roman')) else
                      'Arial' if any(part in name for part in ('helv', 'arial', 'sans')) else 'Segoe UI')
            self.control.setStyleSheet(f"QLineEdit, QPlainTextEdit {{ color: #{self.target.color:06x}; background: white; "
                                      f"border: 2px solid {theme_color('accent')}; border-radius: 1px; "
                                      f"font-size: {pixels}px; font-family: '{family}'; "
                                      "padding: 0px; margin: 0px; min-height: 0px; }")
            if paragraph:
                last = self.control.document().lastBlock()
                content_height = self.control.blockBoundingGeometry(last).bottom() + 8
                available = self.target.display_layout_bbox
                limit = transform.mapRect(QRectF(available[0], available[1], available[2]-available[0], available[3]-available[1])).height()
                self.control.resize(self.control.width(), max(height, min(round(limit), round(content_height))))
            self.control.setVisible(rect.intersects(QRectF(viewport.rect())))
            if rect.intersects(QRectF(12, 12, self.bar.width(), self.bar.height())):
                bar_y = max(12, viewport.height() - self.bar.height() - 12)
        self.bar.move(12, bar_y)
        self.bar.raise_()

    def eventFilter(self, watched, event):
        if self._closed:
            return False
        if watched is self.view.viewport():
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self.reposition)
            return False
        text_viewport = self.control.viewport() if isinstance(self.control, QPlainTextEdit) else None
        if watched in (self.control, self.size, text_viewport):
            if event.type() == QEvent.Type.ShortcutOverride and event.key() in (
                    Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self.cancel()
                    self.view.setFocus()
                    return True
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if isinstance(self.control, QPlainTextEdit) and watched is not self.size and not event.modifiers() & (
                            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        return False
                    if self.finish():
                        self.view.setFocus()
                    return True
            if watched in (self.control, text_viewport) and event.type() == QEvent.Type.Wheel:
                if isinstance(self.control, QPlainTextEdit) and self.control.verticalScrollBar().maximum() > 0 and not event.modifiers() & (
                        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                    return False
                position = QPointF(self.view.viewport().mapFromGlobal(event.globalPosition().toPoint()))
                wheel = QWheelEvent(position, event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                    event.buttons(), event.modifiers(), event.phase(), event.inverted())
                QApplication.sendEvent(self.view.viewport(), wheel)
                event.accept()
                return True
        return False
