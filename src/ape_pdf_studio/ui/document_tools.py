"""Dialogs for blank pages and all-page text watermarks."""
from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
    QSpinBox, QVBoxLayout, QWidget)

from ..core.errors import PDFOperationError
from ..core.localization import tr


class BlankPageDialog(QDialog):
    def __init__(self, page, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Inserisci pagina bianca'))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.position = QComboBox()
        self.position.addItems([tr('Dopo la pagina'), tr('Prima della pagina')])
        self.page = QSpinBox()
        self.page.setRange(1, count)
        self.page.setValue(page + 1)
        self.paper = QComboBox()
        for text, value in ((tr('Come la pagina scelta'), 'same'),
                            (tr('A4 verticale'), 'a4'), (tr('A4 orizzontale'), 'a4-landscape')):
            self.paper.addItem(text, value)
        form.addRow(tr('Posizione'), self.position)
        form.addRow(tr('Pagina'), self.page)
        form.addRow(tr('Formato'), self.paper)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def arguments(self):
        reference = self.page.value() - 1
        return reference + (self.position.currentIndex() == 0), reference, self.paper.currentData()


class WatermarkPreview(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.setMinimumSize(320, 200)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor('#e6e9ef'))
        painter.fillRect(30, 8, w-60, h-16, Qt.GlobalColor.white)
        painter.setPen(QColor('#c4cbd5'))
        for y in range(35, h-20, 16):
            painter.drawLine(52, y, w-52, y)
        painter.translate(w/2, h/2)
        painter.rotate(-self.dialog.angle.value())
        font = QFont()
        font.setPointSizeF(self.dialog.size.value() * .5)
        painter.setFont(font)
        color = QColor(self.dialog.color)
        color.setAlphaF(self.dialog.opacity.value()/100)
        painter.setPen(color)
        text = self.dialog.text.text()
        width = painter.fontMetrics().horizontalAdvance(text)
        if width > w*.8:
            painter.scale(w*.8/width, w*.8/width)
        painter.drawText(-width/2, painter.fontMetrics().ascent()/2, text)
        painter.end()


class WatermarkDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Filigrana su tutte le pagine'))
        self.resize(430, 460)
        self.color = QColor('#607084')
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.text = QLineEdit('BOZZA')
        self.text.setMaxLength(200)
        self.size = QDoubleSpinBox()
        self.size.setRange(8, 144)
        self.size.setValue(48)
        self.size.setSuffix(' pt')
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setValue(20)
        self.opacity.setSuffix('%')
        self.angle = QSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setValue(45)
        self.angle.setSuffix('°')
        self.color_button = QPushButton(tr('Colore…'))
        self.color_button.clicked.connect(self.choose_color)
        for label, control in ((tr('Testo'), self.text), (tr('Dimensione'), self.size),
                               (tr('Colore'), self.color_button), (tr('Opacità'), self.opacity),
                               (tr('Rotazione'), self.angle)):
            form.addRow(label, control)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr('Anteprima indicativa · applicata al centro di ogni pagina')))
        self.preview = WatermarkPreview(self)
        layout.addWidget(self.preview, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.text.textChanged.connect(self._changed)
        for control in (self.size, self.opacity, self.angle):
            control.valueChanged.connect(self._changed)
        self._changed()

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, tr('Colore della filigrana'))
        if color.isValid():
            self.color = color
            self._changed()

    def _changed(self, *_):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.text.text().strip()))
        self.color_button.setStyleSheet('border-bottom: 4px solid ' + self.color.name() + ';')
        self.preview.update()

    def arguments(self):
        return (self.text.text().strip(), self.size.value(),
                [self.color.redF(), self.color.greenF(), self.color.blueF()], self.opacity.value()/100,
                self.angle.value())


class EditJob(QThread):
    def __init__(self, editor, method, args, parent):
        super().__init__(parent)
        self.editor, self.method, self.args = editor, method, args
        self.error = ''

    def run(self):
        try:
            getattr(self.editor, self.method)(*self.args)
        except Exception as exc:
            self.error = str(exc)


class EditProgress(QDialog):
    """Keep Qt responsive while the sandbox creates and validates a revision."""
    def __init__(self, editor, method, args, parent):
        super().__init__(parent)
        self.setWindowTitle(tr('Modifica PDF'))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('Applicazione della modifica…')))
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.job = EditJob(editor, method, args, self)
        self.job.finished.connect(self.accept)
        QTimer.singleShot(0, self.job.start)

    def reject(self):
        # A revision is atomic: keep the owning document alive until completion.
        if self.job.isFinished():
            super().reject()

    def closeEvent(self, event):
        if not self.job.isFinished():
            event.ignore()
        else:
            super().closeEvent(event)

    def execute(self):
        self.exec()
        self.job.wait()
        if self.job.error:
            raise PDFOperationError(self.job.error)
