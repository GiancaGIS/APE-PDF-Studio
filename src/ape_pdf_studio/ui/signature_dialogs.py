"""Display signature and certificate details returned by the document worker."""
from PySide6.QtCore import QDateTime, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..core.localization import tr
from .content_dialogs import plain_label
from .remote_pdf import Task


def _close_buttons(dialog):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("Chiudi"))
    buttons.rejected.connect(dialog.reject)
    return buttons


def _details(layout, names):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    body = QWidget()
    form = QFormLayout(body)
    form.setContentsMargins(0, 6, 0, 6)
    form.setHorizontalSpacing(16)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    values = []
    for name in names:
        label = plain_label("")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(plain_label(name, "SectionTitle"), label)
        values.append(label)
    scroll.setWidget(body)
    layout.addWidget(scroll, 1)
    return scroll, values


def _utc(value):
    date = QDateTime.fromString(value, Qt.DateFormat.ISODate).toUTC()
    return date.toString("yyyy-MM-dd HH:mm:ss 'UTC'") if date.isValid() else value


class SignatureSummaryDialog(QDialog):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.signatures = editor.document_info.signatures
        self.setWindowTitle(tr("Firme digitali"))
        self.resize(540, 345)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(plain_label(tr("Firme digitali"), "DialogTitle"))
        layout.addWidget(plain_label(tr("Validità delle firme non verificata."), "Caption"))
        if editor.modified_from_original:
            layout.addWidget(plain_label(tr("Il PDF è stato modificato. Le firme mostrate appartengono al documento originale."), "Caption"))

        self.selection = QComboBox()
        self.selection.setAccessibleName(tr("Firme digitali"))
        self.selection.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for index, signature in enumerate(self.signatures, start=1):
            self.selection.addItem(f"{index}. {signature.field[:100]}")
        layout.addWidget(self.selection)
        self.selection.setVisible(len(self.signatures) > 1)

        _, self.values = _details(layout, (
            tr("Campo firma"), tr("Firmatario dichiarato"), tr("Data dichiarata"),
            tr("Motivo della firma"), tr("Formato della firma"),
        ))
        buttons = _close_buttons(self)
        self.certificate_button = buttons.addButton(tr("Visualizza certificato…"), QDialogButtonBox.ButtonRole.ActionRole)
        self.certificate_button.clicked.connect(self._show_certificate)
        layout.addWidget(buttons)
        self.selection.currentIndexChanged.connect(self._show_signature)
        self._show_signature(0)

    def _show_signature(self, index):
        signature = self.signatures[index]
        format_name = tr("Marca temporale del documento") if signature.subfilter == "/ETSI.RFC3161" else signature.subfilter
        for label, value in zip(self.values, (signature.field, signature.signer, signature.date,
                                             signature.reason, format_name)):
            label.setText(value or tr("Non indicato"))

    def _show_certificate(self):
        dialog = CertificateDialog(self.editor, self.selection.currentIndex(), self)
        dialog.exec()
        dialog.deleteLater()


class CertificateDialog(QDialog):
    certificates_ready = Signal(object)

    def __init__(self, editor, index, parent=None):
        super().__init__(parent)
        self.certificates = ()
        self.setWindowTitle(tr("Proprietà del certificato"))
        self.resize(620, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(plain_label(tr("Proprietà del certificato"), "DialogTitle"))
        self.status = plain_label(tr("Lettura del certificato incorporato…"), "Caption")
        layout.addWidget(self.status)
        self.selection = QComboBox()
        self.selection.setAccessibleName(tr("Certificati incorporati"))
        layout.addWidget(self.selection)
        self.selection.hide()
        self.association = plain_label("", "SectionTitle")
        layout.addWidget(self.association)
        self.association.hide()
        self.details, self.values = _details(layout, (
            tr("Titolare"), tr("Emittente"), tr("Valido dal"), tr("Scadenza"),
            tr("Numero di serie"), tr("Impronta SHA-256"),
        ))
        self.details.hide()
        layout.addStretch()
        layout.addWidget(_close_buttons(self))
        self.selection.currentIndexChanged.connect(self._show_certificate)
        self.certificates_ready.connect(self._populate)

        emit = self.certificates_ready.emit

        def load():
            try:
                certificates = editor.signature_certificates(index)
            except Exception:
                certificates = None
            try:
                emit(certificates)
            except RuntimeError:
                pass  # The dialog can be closed while the worker is busy.

        QThreadPool.globalInstance().start(Task(load))

    def _populate(self, certificates):
        if certificates is None:
            self.status.setText(tr("Impossibile leggere i certificati della firma."))
            return
        if not certificates:
            self.status.setText(tr("Nessun certificato incorporato leggibile per questa firma."))
            return
        self.certificates = certificates
        self.status.setText(tr("Attendibilità e revoca del certificato non verificate."))
        for index, certificate in enumerate(certificates, start=1):
            label = tr("Certificato indicato nella firma") if certificate.referenced else tr("Altro certificato incorporato")
            self.selection.addItem(f"{index}. {label}")
        self.selection.setVisible(len(certificates) > 1)
        self.association.show()
        self.details.show()

    def _show_certificate(self, index):
        certificate = self.certificates[index]
        self.association.setText(tr("Certificato indicato nella firma") if certificate.referenced
                                 else tr("Associazione alla firma non determinata"))
        values = (certificate.subject, certificate.issuer, _utc(certificate.valid_from),
                  _utc(certificate.valid_until), certificate.serial, certificate.sha256.upper())
        for label, value in zip(self.values, values):
            label.setText(value or tr("Non indicato"))
