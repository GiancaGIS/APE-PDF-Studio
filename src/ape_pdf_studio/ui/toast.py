"""Non-modal completion notice anchored inside the application window."""
from ..core.localization import tr
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout


class CompletionToast(QFrame):
    DURATION_MS = 6000

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("CompletionToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAccessibleName(tr("Operazione completata"))
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 12, 14)
        row.setSpacing(12)
        badge = self.badge = QLabel("✓")
        badge.setObjectName("ToastSuccess")
        badge.setFixedSize(32, 32)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        text = self.text_layout = QVBoxLayout()
        text.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("ToastTitle")
        self.filename = QLabel()
        self.filename.setObjectName("ToastFilename")
        self.detail = QLabel()
        self.detail.setObjectName("ToastDetail")
        for label in (self.title, self.filename, self.detail):
            label.setTextFormat(Qt.TextFormat.PlainText)
            text.addWidget(label)
        self.detail.setWordWrap(True)
        row.addLayout(text, 1)
        self.close_button = QToolButton()
        self.close_button.setText("×")
        self.close_button.setToolTip(tr("Chiudi notifica"))
        self.close_button.setAccessibleName(tr("Chiudi notifica"))
        self.close_button.setFixedSize(24, 24)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.close_button.clicked.connect(self.dismiss)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(40, 52, 70, 35))
        self.setGraphicsEffect(shadow)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self._remaining = self.DURATION_MS
        self._filename = ""
        parent.installEventFilter(self)
        self.hide()

    def show_completion(self, title, path, detail=""):
        self.timer.stop()
        path = Path(path)
        self.title.setText(title)
        self._filename = path.name
        self.filename.setToolTip(str(path))
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setToolTip(str(path) + ("\n" + detail if detail else ""))
        self.setAccessibleDescription(title + ": " + str(path) + (". " + detail if detail else ""))
        self._show_notice()

    def _show_notice(self):
        self.timer.stop()
        self._reposition()
        self.show()
        self.raise_()
        self._remaining = self.DURATION_MS
        if not self.underMouse():
            self.timer.start(self._remaining)

    def dismiss(self):
        self.timer.stop()
        self.hide()

    def _reposition(self):
        parent = self.parentWidget()
        self.setFixedWidth(min(420, max(200, parent.width() - 32)))
        # Elide unusually long filenames while keeping the full path in a tooltip.
        available = self.width() - 112
        self.filename.setText(self.filename.fontMetrics().elidedText(
            self._filename, Qt.TextElideMode.ElideMiddle, available))
        self.layout().activate()
        self.adjustSize()
        self.move(max(16, parent.width() - self.width() - 16),
                  max(16, parent.height() - parent.statusBar().height() - self.height() - 16))

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            if self.isVisible():
                self._reposition()
        return super().eventFilter(watched, event)

    def enterEvent(self, event):
        if self.timer.isActive():
            self._remaining = max(1, self.timer.remainingTime())
            self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible():
            self.timer.start(self._remaining)
        super().leaveEvent(event)


class UpdateToast(CompletionToast):
    DURATION_MS = 15000

    def __init__(self, parent, anchor):
        self._anchor = anchor
        super().__init__(parent)
        anchor.installEventFilter(self)
        self.badge.setText("↑")
        self.setAccessibleName(tr("Aggiornamento disponibile"))
        self.release_button = QPushButton(tr("Apri la release su GitHub"))
        self.release_button.setAutoDefault(False)
        self.release_button.clicked.connect(self.open_release)
        self.text_layout.addWidget(self.release_button)
        self.release = None

    def show_update(self, release):
        if release is None or not release.update_available:
            return
        self.release = release
        self.title.setText(tr("Aggiornamento disponibile"))
        self._filename = tr("È disponibile la versione {0}.", release.version)
        self.detail.setText(tr("Apri la release su GitHub per scaricare e installare l'aggiornamento."))
        self.detail.show()
        self.setAccessibleDescription(self._filename + " " + self.detail.text())
        self._show_notice()

    def open_release(self):
        if self.release is not None:
            if QDesktopServices.openUrl(QUrl(self.release.url)):
                self.dismiss()
            else:
                self.detail.setText(tr("Impossibile aprire il browser. Riprova."))
                self._reposition()

    def _reposition(self):
        super()._reposition()
        if self._anchor.isVisible():
            self.move(self.x(), max(16, self._anchor.y() - self.height() - 12))

    def eventFilter(self, watched, event):
        if watched is self._anchor and event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Resize, QEvent.Type.Move):
            if self.isVisible():
                self._reposition()
        return super().eventFilter(watched, event)
