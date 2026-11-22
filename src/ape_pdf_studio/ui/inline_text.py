"""Direct text editing on the viewport; all PDF reads remain sandboxed RPCs."""
from collections import OrderedDict
from threading import Event

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QShortcut, QTextCursor, QTextOption, QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core.localization import tr
from ..core.paragraph_edit import ParagraphTarget
from .content_dialogs import _TextSignals, _TextTask, plain_label
from .theme import theme_color


class ParagraphEdit(QPlainTextEdit):
    """Plain text only: PDF strings can never become HTML or external resources."""
    def text(self):
        # Qt's toPlainText converts nonbreaking spaces to ordinary spaces.
        return self.document().toRawText().replace('\u2029', '\n')

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
        self._after_commit = None
        self._loading = False
        self._font_running = False
        self._preview_cancel = Event()
        self._font_cache = OrderedDict()
        self._preview_bbox = None
        self._preview_image = None
        self._color = None
        self._format_cursor = None
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
        self.new_button = QPushButton(tr("Nuovo testo"))
        self.new_button.setCheckable(True)
        self.new_button.toggled.connect(self._new_block_toggled)
        row.addWidget(self.new_button)
        self.delete_button = QPushButton(tr("Elimina testo"))
        self.delete_button.clicked.connect(self.delete_text)
        row.addWidget(self.delete_button)
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
        self.preview = QCheckBox(tr("Anteprima"))
        self.preview.toggled.connect(self._preview_changed)
        options.addWidget(self.preview)
        self.format_button = QPushButton(tr("Formato"))
        self.format_button.setCheckable(True)
        self.format_button.toggled.connect(self._format_toggled)
        options.addWidget(self.format_button)
        self.font_label = plain_label("", "Caption")
        self.font_label.setMaximumHeight(40)
        options.addWidget(self.font_label, 1)
        layout.addLayout(options)
        self.format_panel = QWidget(self.bar)
        format_layout = QVBoxLayout(self.format_panel)
        format_layout.setContentsMargins(0, 0, 0, 0)
        format_row = QHBoxLayout()
        format_layout.addLayout(format_row)
        self.family = QComboBox()
        self.family.setAccessibleName(tr("Famiglia del font"))
        for label, value in (("Font originale", None), ("Sans serif", 'helv'), ("Serif", 'tiro'), ("Monospazio", 'cour')):
            self.family.addItem(tr(label), value)
        self.family.currentIndexChanged.connect(self._value_changed)
        format_row.addWidget(self.family)
        self.font_style = QComboBox()
        self.font_style.setAccessibleName(tr("Stile del testo"))
        for label in ("Stile originale", "Normale", "Grassetto", "Corsivo", "Grassetto corsivo"):
            self.font_style.addItem(tr(label))
        self.font_style.currentIndexChanged.connect(self._value_changed)
        format_row.addWidget(self.font_style)
        self.color_button = QPushButton(tr("Colore testo"))
        self.color_button.clicked.connect(self._choose_color)
        format_row.addWidget(self.color_button)
        size_row = QHBoxLayout()
        format_layout.addLayout(size_row)
        self.format_selection = QCheckBox(tr("Solo selezione"))
        self.format_selection.toggled.connect(self._scope_changed)
        size_row.addWidget(self.format_selection)
        size_row.addStretch()
        self.width = QDoubleSpinBox()
        self.width.setDecimals(2)
        self.width.setMinimum(1)
        self.width.setSuffix(' pt')
        self.width.setAccessibleName(tr("Larghezza del paragrafo"))
        self.width.setToolTip(tr("Larghezza del paragrafo"))
        self.width.valueChanged.connect(self._value_changed)
        size_row.addWidget(self.width)
        self.reset_format = QPushButton(tr("Ripristina formato"))
        self.reset_format.clicked.connect(self._reset_format)
        size_row.addWidget(self.reset_format)
        layout.addWidget(self.format_panel)
        self.format_panel.hide()
        self._initial_width = 0
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
        self._box_shortcuts = []
        for key, step in (("Alt+Down", 1), ("Alt+Up", -1)):
            shortcut = QShortcut(QKeySequence(key), self.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.setEnabled(False)
            shortcut.activated.connect(lambda step=step: self._step_box(step))
            self._box_shortcuts.append(shortcut)

    @property
    def dirty(self):
        return self.control is not None and self.target is not None and (
            self.control.text() != self.target.text or self.size.value() != self._initial_size
            or (isinstance(self.target, ParagraphTarget) and self.alignment.currentIndex() != self.target.alignment)
            or self._formatting() is not None)

    @property
    def committing(self):
        return self._finishing

    def _mode_changed(self, checked):
        if not self.finish(lambda: self.reflow.setChecked(checked)):
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
        if not self.tab.signature.finish():
            return False
        if not self.tab.inline_form.finish():
            return False
        if self.tab.ink.active and not self.tab.ink.deactivate():
            return False
        self.active = True
        for shortcut in self._box_shortcuts:
            shortcut.setEnabled(True)
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
        if not self.finish(self.deactivate):
            return False
        self.active = False
        for shortcut in self._box_shortcuts:
            shortcut.setEnabled(False)
        with QSignalBlocker(self.new_button):
            self.new_button.setChecked(False)
        self.boxes.clear()
        self.bar.hide()
        self.view.viewport().update()
        self.changed.emit()
        return True

    def _idle(self):
        if self.new_button.isChecked():
            self.font_label.setText(tr("Fai clic in uno spazio libero della pagina per inserire il testo."))
        else:
            self.font_label.setText(tr("Invio: nuova riga · Ctrl+Invio: applica · Esc: annulla") if self.reflow.isChecked() else
                                   tr("Fai clic su un riquadro e modifica il testo sulla pagina. Invio: applica · Esc: annulla."))
        self.size.setEnabled(False)
        self.alignment.setEnabled(False)
        self.apply.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.preview.setEnabled(False)
        self.format_button.setEnabled(False)
        with QSignalBlocker(self.format_button):
            self.format_button.setChecked(False)
        self.format_panel.hide()

    def _new_block_toggled(self, checked):
        if not self.finish(lambda: self.new_button.setChecked(checked)):
            with QSignalBlocker(self.new_button):
                self.new_button.setChecked(not checked)
        if self.target is None:
            self._idle()
        self.reposition()

    def _format_toggled(self, checked):
        self.format_panel.setVisible(checked)
        self.reposition()

    def _choose_color(self):
        if self.target is None or self._finishing:
            return
        current = self.target.color if self._color is None else self._color
        color = QColorDialog.getColor(QColor.fromRgb(current), self.bar, tr("Colore testo"))
        if color.isValid():
            self._color = color.rgb() & 0xffffff
            self.color_button.setText(color.name())
            self._value_changed()

    def _reset_format(self):
        self._color = None
        self._format_cursor = None
        with QSignalBlocker(self.format_selection):
            self.format_selection.setChecked(False)
        for widget in (self.family, self.font_style, self.width):
            with QSignalBlocker(widget):
                if widget is self.width:
                    self.width.setValue(self._initial_width)
                else:
                    widget.setCurrentIndex(0)
        self.color_button.setText(tr("Colore testo"))
        self._value_changed()

    def _scope_changed(self, checked):
        self._format_cursor = None
        if checked:
            cursor = self.control.textCursor() if isinstance(self.control, ParagraphEdit) else None
            if cursor is None or not cursor.hasSelection():
                with QSignalBlocker(self.format_selection):
                    self.format_selection.setChecked(False)
                self.feedback.setText(tr("Seleziona i caratteri da formattare nel riquadro."))
                self.feedback.show()
                self.reposition()
                return
            # QTextCursor follows edits to the source document. The worker
            # validates the converted character offsets against graphemes.
            self._format_cursor = QTextCursor(cursor)
        self._value_changed()

    def _formatting(self):
        bold, italic = ((None, None), (False, False), (True, False), (False, True),
                        (True, True))[self.font_style.currentIndex()]
        width = self.width.value() if isinstance(self.target, ParagraphTarget) and self.width.value() != self._initial_width else None
        formatting = self.family.currentData(), self._color, bold, italic, width
        if self._format_cursor is not None and (any(value is not None for value in formatting[:4])
                or self.size.value() != self._initial_size):
            data = self.control.text().encode('utf-16-le', errors='surrogatepass')
            offsets = (self._format_cursor.selectionStart(), self._format_cursor.selectionEnd())
            selection = tuple(len(data[:2 * i].decode('utf-16-le', errors='ignore')) for i in offsets)
            return (*formatting, selection)
        return formatting if any(value is not None for value in formatting) else None

    def _task(self, kind, key, operation, priority=1):
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().start(_TextTask(kind, key, operation, self._signals), priority)

    def _page_changed(self, *_):
        if not self.active or self._closed or self._finishing:
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

    def _step_box(self, step):
        page = self.tab.page
        boxes = self.boxes.get(page)
        if not boxes or self._finishing:
            return
        current = self.target.display_bbox if self.target is not None and self.target.page == page else None
        index = boxes.index(current) if current in boxes else (-1 if step > 0 else 0)
        left, top, right, bottom = boxes[(index + step) % len(boxes)]
        with QSignalBlocker(self.new_button):
            self.new_button.setChecked(False)
        self.start(page, QPointF((left + right) / 2, (top + bottom) / 2))

    def start(self, page, point):
        if not self.finish(lambda: self.start(page, point)) or (not self.active and not self.activate()):
            return False
        self._loading = True
        self.feedback.clear()
        self.feedback.hide()
        self.font_label.setText(tr("Lettura del testo in corso…"))
        editor = self.tab.editor
        key = (self._epoch, editor.path)
        position = (point.x(), point.y())
        self._cursor_position = page, QPointF(point)
        if self.new_button.isChecked():
            resolver = editor.new_paragraph_at
        else:
            resolver = editor.paragraph_at if self.reflow.isChecked() else editor.text_at
        self._task("target", key, lambda: resolver(page, position))
        self.reposition()
        return True

    def _completed(self, kind, key, value, error):
        if self._closed:
            return
        if kind == 'commit':
            if key[0] == self._epoch:
                self._commit_completed(error)
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
        if not self.active or key[0] != self._epoch or self._finishing:
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
            with QSignalBlocker(self.new_button):
                self.new_button.setChecked(False)
            with QSignalBlocker(self.width):
                self.width.setMaximum(value.column_limit if paragraph else 1)
                self.width.setValue(value.layout_bbox[2] - value.layout_bbox[0] if paragraph else 1)
            self._initial_width = self.width.value()
            self.width.setEnabled(paragraph)
            self.format_selection.setEnabled(paragraph)
            self._reset_format()
            with QSignalBlocker(self.alignment):
                self.alignment.setCurrentIndex(value.alignment if paragraph else 0)
            self.alignment.setEnabled(paragraph)
            self.apply.setEnabled(True)
            self.delete_button.setEnabled(bool(value.text))
            self.preview.setEnabled(True)
            self.format_button.setEnabled(True)
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
            if value.text:
                self._font_info()
            else:
                self.font_label.setText(tr("Scrivi il testo nel nuovo riquadro."))
            return
        self._font_running = False
        if self.control is None or key[2] != self.tab.editor.path:
            return
        draft = key[1]
        if not error and value[0] is not None:
            self._font_cache[draft] = value
            while len(self._font_cache) > 4:
                self._font_cache.popitem(last=False)
        if draft == self._layout_key():
            if error:
                self.feedback.setText(error)
                self.feedback.show()
            elif value[0] is not None:
                self._show_layout(*value)
            elif not self._font_timer.isActive():
                self._font_info()
            self.reposition()
        elif not self._font_timer.isActive():
            self._font_info()

    def _layout_key(self):
        size = self.target.size if self.size.value() == self._initial_size else self.size.value()
        alignment = self.alignment.currentIndex() if isinstance(self.target, ParagraphTarget) else None
        return self.control.text(), size, alignment, self._formatting(), self.preview.isChecked()

    def _show_layout(self, value, data):
        self._preview_bbox = value['bbox'] if value['fits'] else None
        fonts = ', '.join(dict.fromkeys(run[4] for run in value['runs'])) or value['font']
        self.font_label.setText(tr('Paragrafo: {0} righe · font: {1}', value['lines'], fonts)
                               if isinstance(self.target, ParagraphTarget) else tr("Font di scrittura: ") + fonts)
        if value['fallback']:
            self.font_label.setText(self.font_label.text() + tr(' · font compatibile'))
        self.feedback.setText(value['message'])
        self.feedback.setVisible(not value['fits'])
        self._preview_image = None
        if data is not None and self.preview.isChecked():
            bbox, width, height = value['raster']
            x0, y0, x1, y1 = bbox
            page_size = self.tab.pdf.pagePointSize(self.target.page)
            pad = max(1., (x1 - x0) / width, (y1 - y0) / height)
            if max(-x0, -y0, x1 - page_size.width(), y1 - page_size.height()) <= pad + .25:
                self._preview_image = (QRectF(x0, y0, x1 - x0, y1 - y0),
                                       QImage(data, width, height, width * 3, QImage.Format.Format_RGB888).copy())
        self.view.edit_target = self.target.page, self._preview_bbox or self.target.display_bbox
        self.reposition()
        self.view.viewport().update()

    def _font_info(self):
        if self.control is None or self._closed or self._finishing:
            return
        draft = self._layout_key()
        if draft in self._font_cache:
            self._show_layout(*self._font_cache[draft])
            return
        if self._font_running:
            return
        self._font_running = True
        self.font_label.setText(tr("Analisi del font in corso…"))
        editor, target = self.tab.editor, self.target
        cancel = self._preview_cancel = Event()
        text, size, alignment, formatting, raster = draft
        self._task('layout', (self._epoch, draft, editor.path),
                   lambda: editor.preview_text(target, text, size, alignment, formatting,
                                               raster=raster, cancelled=cancel))

    def _preview_changed(self, checked):
        self._preview_image = None
        self.view.viewport().update()
        self.reposition()
        if self.control is not None:
            self._preview_cancel.set()
            self._font_timer.stop()
            self._font_info()
            if not checked:
                self.control.setFocus()

    def paint_preview(self, painter, page, transform):
        if self._preview_image is not None and self.target.page == page:
            bounds, image = self._preview_image
            painter.drawImage(transform.mapRect(bounds), image)

    def _value_changed(self, *_):
        if self.control is not None:
            self._preview_cancel.set()
            self._font_timer.start(150)
            self.feedback.clear()
            self.feedback.hide()
            self.reposition()
            self.changed.emit()

    def finish(self, when_ready=None):
        if self._closed:
            return True
        if self._finishing:
            if callable(when_ready) and self._after_commit is None:
                self._after_commit = when_ready
            return False
        if not self.dirty:
            self.cancel()
            return True
        text, size, alignment, formatting, _ = self._layout_key()
        method = 'replace_text' if alignment is None else 'replace_paragraph'
        args = (self.target, text, size) + (() if alignment is None else (alignment,)) + (formatting,)
        self._commit(method, args, when_ready if callable(when_ready) else None)
        return False

    def delete_text(self):
        if self.target is not None and self.target.text and not self._finishing:
            self._commit('delete_text', (self.target,), None)

    def _commit(self, method, args, when_ready):
        self._epoch += 1
        self._preview_cancel.set()
        self._font_timer.stop()
        self._finishing = True
        self._after_commit = when_ready
        self.feedback.setText(tr("Applicazione della modifica in corso…"))
        self.feedback.show()
        self._enable_editing(False)
        self.reposition()
        editor = self.tab.editor
        self._task('commit', (self._epoch, editor.path), lambda: getattr(editor, method)(*args), priority=10)
        self.changed.emit()

    def _enable_editing(self, enabled):
        self.control.setReadOnly(not enabled)
        self.size.setEnabled(enabled)
        self.alignment.setEnabled(enabled and isinstance(self.target, ParagraphTarget))
        self.delete_button.setEnabled(enabled and bool(self.target.text))
        self.format_panel.setEnabled(enabled)
        for widget in (self.apply, self.new_button, self.preview, self.format_button,
                       self.reflow, self.cancel_button, self.done_button):
            widget.setEnabled(enabled)

    def _commit_completed(self, error):
        self._finishing = self._font_running = False
        when_ready, self._after_commit = self._after_commit, None
        self._enable_editing(True)
        if error:
            self.feedback.setText(error)
            self.feedback.show()
            self.reposition()
            self.control.setFocus()
            self.changed.emit()
            return
        page = self.target.page
        self.cancel()
        self.boxes.clear()
        self.tab.publish_text_revision(page)
        self._page_changed()
        self.view.setFocus()
        if when_ready is not None:
            when_ready()

    def cancel(self):
        if self._finishing:
            return
        self._epoch += 1
        self._preview_cancel.set()
        self._font_timer.stop()
        self._font_running = False
        self._loading = False
        control, self.control = self.control, None
        self.target = None
        self._format_cursor = None
        self._preview_bbox = None
        self._preview_image = None
        with QSignalBlocker(self.preview):
            self.preview.setChecked(False)
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
        for shortcut in self._box_shortcuts:
            shortcut.setEnabled(False)
        self.cancel()
        self.boxes.clear()
        self.bar.hide()

    def reposition(self, *_):
        if not self.active:
            return
        viewport = self.view.viewport()
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
            formatting = self._formatting() if self._format_cursor is None else None
            bold, italic = formatting[2:4] if formatting is not None else (None, None)
            bold = bool(self.target.flags & 16) if bold is None else bold
            italic = bool(self.target.flags & 2) if italic is None else italic
            font.setBold(bold)
            font.setItalic(italic)
            points = self._initial_size if self._format_cursor is not None else self.size.value()
            pixels = max(6, min(240, round(points * abs(transform.m11()))))
            font.setPixelSize(pixels)
            self.control.setFont(font)
            name = self.target.font.lower()
            family = ('Courier New' if any(part in name for part in ('cour', 'mono')) else
                      'Times New Roman' if any(part in name for part in ('times', 'roman')) else
                      'Arial' if any(part in name for part in ('helv', 'arial', 'sans')) else 'Segoe UI')
            if formatting is not None:
                family = {'helv': 'Arial', 'tiro': 'Times New Roman', 'cour': 'Courier New'}.get(formatting[0], family)
            color = self.target.color if self._color is None or self._format_cursor is not None else self._color
            self.control.setStyleSheet(f"QLineEdit, QPlainTextEdit {{ color: #{color:06x}; background: white; "
                                      f"border: 2px solid {theme_color('accent')}; border-radius: 1px; "
                                      f"font-size: {pixels}px; font-family: '{family}'; "
                                      f"font-weight: {'bold' if bold else 'normal'}; font-style: {'italic' if italic else 'normal'}; "
                                      "padding: 0px; margin: 0px; min-height: 0px; }")
            if paragraph:
                last = self.control.document().lastBlock()
                content_height = self.control.blockBoundingGeometry(last).bottom() + 8
                available = self.target.display_layout_bbox
                limit = transform.mapRect(QRectF(available[0], available[1], available[2]-available[0], available[3]-available[1])).height()
                self.control.resize(self.control.width(), max(height, min(round(limit), round(content_height))))
            self.control.setVisible((not self.preview.isChecked() or self._preview_image is None)
                                    and rect.intersects(QRectF(viewport.rect())))
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
