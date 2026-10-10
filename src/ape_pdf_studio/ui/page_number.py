"""Explicit page navigation that does not discard numbers while typing."""
from ..core.localization import tr
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit


class PageNumberEdit(QLineEdit):
    page_requested = Signal(int)
    invalid_input = Signal(str)

    def __init__(self, parent=None):
        super().__init__("1", parent)
        self.setObjectName("PageNumber")
        self.setAccessibleName(tr("Numero pagina"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMaxLength(10)
        self.setToolTip(tr("Vai alla pagina · conferma con Invio"))
        self._value = 1
        self._total = 1
        self._draft = False
        self.textEdited.connect(self._edited)
        self.returnPressed.connect(self.commit)
        self.sync_page(1, 1)

    def _edited(self, _text):
        self._draft = True

    def value(self):
        return self._value

    def setValue(self, number):
        """Explicit programmatic navigation; display refresh uses sync_page."""
        if 1 <= number <= self._total:
            self._draft = False
            self._value = number
            self.setText(str(number))
            self.page_requested.emit(number)

    def sync_page(self, number, total, *, reset=False):
        reset = reset or total != self._total
        self._total = max(1, total)
        self._value = max(1, min(number, self._total))
        width = max(52, self.fontMetrics().horizontalAdvance(str(self._total)) + 22)
        self.setFixedWidth(width)
        if reset or not self._draft:
            self._draft = False
            self.setText(str(self._value))

    def commit(self):
        raw = self.text().strip()
        if not raw.isdecimal() or not 1 <= int(raw) <= self._total:
            self.invalid_input.emit(tr('Indica una pagina tra 1 e {0}.', self._total))
            self._draft = False
            self.setText(str(self._value))
            self.selectAll()
            return
        # Emit even for the current page, to return to its beginning.
        self.setValue(int(raw))
        self.selectAll()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()

    def mousePressEvent(self, event):
        newly_focused = not self.hasFocus()
        super().mousePressEvent(event)
        if newly_focused and event.button() == Qt.MouseButton.LeftButton:
            self.selectAll()

    def focusOutEvent(self, event):
        if self._draft:
            self.commit()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._draft = False
            self.setText(str(self._value))
            self.selectAll()
            event.accept()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 1 if event.key() == Qt.Key.Key_Up else -1
            self.setValue(max(1, min(self._value + step, self._total)))
            self.selectAll()
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        event.ignore()
