"""Document inspector with editable descriptive metadata and technical details."""
from __future__ import annotations
from ..core.localization import tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QScrollArea,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.metadata import EDITABLE_FIELDS
from .icons import icon
from .theme import theme_color


def _label(text, name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


def verification_text(editor):
    info = editor.document_info
    lines = [tr("Firme e identificazione PDF/A del documento originale"), ""]
    if info.pdfa:
        lines.extend((tr('Standard dichiarato: {0}', info.pdfa),
                      tr("La dichiarazione proviene dai metadati XMP. La conformità PDF/A non è stata validata."), ""))
    else:
        lines.extend((tr("Nessuna dichiarazione PDF/A riconosciuta."), ""))
    for signature in info.signatures:
        lines.extend((tr('Campo firma: {0}', signature.field),
                      tr('Firmatario dichiarato: {0}', signature.signer or tr('Non indicato')),
                      tr('Data dichiarata: {0}', signature.date or tr('Non indicata')),
                      tr('Motivo: {0}', signature.reason or tr('Non indicato')), ""))
    lines.append(tr("Sono presenti dati di firma. Certificati, integrità e marche temporali non sono stati validati crittograficamente.")
                 if info.signatures else tr("Nessun dato di firma digitale rilevato."))
    if info.empty_signature_fields:
        lines.append(tr("Campi firma vuoti: ") + ", ".join(info.empty_signature_fields))
    if editor.modified_from_original:
        lines.append(tr("\nIl documento è stato modificato: le firme dell'originale non sono valide per questa revisione e la conformità PDF/A va verificata nuovamente."))
    lines.extend(info.warnings)
    return "\n".join(lines)


class PropertiesDialog(QDialog):
    def __init__(self, editor, apply_changes, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.apply_changes = apply_changes
        self.setObjectName("PropertiesDialog")
        self.setWindowTitle(tr("Proprietà del documento"))
        self.setMinimumSize(620, 500)
        self.resize(740, 610)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(14)
        badge = QLabel()
        badge.setObjectName("DocumentBadge")
        badge.setFixedSize(48, 48)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icon("page", theme_color("accent")).pixmap(26, 26))
        row.addWidget(badge)
        headings = QVBoxLayout()
        headings.setSpacing(3)
        headings.addWidget(_label(tr("Proprietà del documento"), "DialogTitle"))
        self.caption = _label(editor.output_path.name, "Caption")
        headings.addWidget(self.caption)
        row.addLayout(headings, 1)
        layout.addLayout(row)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("PropertiesTabs")
        page = QWidget()
        form_layout = QVBoxLayout(page)
        form_layout.setContentsMargins(20, 20, 20, 16)
        form_layout.setSpacing(16)
        form_layout.addWidget(_label(tr("Descrivi il tuo documento"), "SectionTitle"))
        form_layout.addWidget(_label(tr("Lascia vuoto un campo per rimuoverlo. I campi modificati vengono sincronizzati con XMP, se presente."), "Caption"))
        self.warning = _label("", "MetadataWarning")
        form_layout.addWidget(self.warning)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(16)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.fields = {}
        for key, label in EDITABLE_FIELDS.items():
            field = QLineEdit()
            field.setObjectName("metadata_" + key[1:])
            field.setPlaceholderText(tr("Non indicato"))
            field.setClearButtonEnabled(True)
            field.setMinimumHeight(34)
            field.setMaxLength(32767)
            if key == "/Author":
                field.setToolTip(tr("Per più autori, separa i nomi con un punto e virgola (;)."))
            self.fields[key] = field
            field.textChanged.connect(self._update_apply)
            form.addRow(tr(label), field)
        form_layout.addLayout(form)
        form_layout.addStretch()
        form_layout.addWidget(_label(tr("Applica aggiorna la copia di lavoro. Usa Salva per scrivere le modifiche nel PDF. Puoi annullare con Ctrl+Z."), "Caption"))
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, tr("Metadati"))
        document_page = QWidget()
        document_layout = QVBoxLayout(document_page)
        document_layout.setContentsMargins(18, 18, 18, 18)
        self.document_summary = _label("", "Caption")
        self.document_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        document_layout.addWidget(self.document_summary)
        document_layout.addWidget(_label(tr("Dizionario Info · campi tecnici e aggiuntivi in sola lettura"), "SectionTitle"))
        self.info_table = QTableWidget(0, 2)
        self.info_table.setObjectName("MetadataTable")
        self.info_table.setHorizontalHeaderLabels([tr("Campo"), tr("Valore")])
        self.info_table.verticalHeader().hide()
        self.info_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.info_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.info_table.setAlternatingRowColors(True)
        self.info_table.setWordWrap(True)
        self.info_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.info_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        document_layout.addWidget(self.info_table, 1)
        self.tabs.addTab(document_page, tr("Documento"))
        self.verification = QPlainTextEdit()
        self.verification.setReadOnly(True)
        self.tabs.addTab(self.verification, tr("Firme e PDF/A"))
        self.xmp = QPlainTextEdit()
        self.xmp.setObjectName("XmpSource")
        self.xmp.setReadOnly(True)
        self.tabs.addTab(self.xmp, "XMP")
        self.security_details = QPlainTextEdit()
        self.security_details.setReadOnly(True)
        report = editor.security_report
        self.security_details.setPlainText(
            tr("Controllo preventivo attivo\n\n"
            "Il PDF è stato analizzato in un processo separato prima della visualizzazione. "
            "I contenuti attivi riconosciuti e i controlli non completati bloccano l'apertura.\n\n")
            + (report.summary or tr("Nessuno dei contenuti attivi cercati è stato rilevato.")) +
            tr("\n\nGli allegati non vengono aperti. I collegamenti web/email richiedono l'abilitazione per documento e la conferma dell'indirizzo. "
            "Il controllo si applica anche ai PDF inseriti e alle esportazioni Word.\n\n"
            "Limiti di ingresso: 256 MiB, 3000 pagine; controllo entro 12 secondi.\n")
            + (tr("Limite di memoria del processo di controllo: 768 MiB.\n") if report.memory_limited else
               tr("Il sistema non ha consentito il limite di memoria del processo di controllo.\n")) +
            tr("\nQuesto controllo non certifica che il PDF sia innocuo e non sostituisce un antivirus. ") +
            (tr("Su Windows, lettura, rendering, ricerca e modifiche vengono eseguiti in AppContainer, senza capacità di rete né processi figli. Il worker accede alla propria cartella privata e al runtime autorizzato. La GUI riceve pixel e dati validati. Anche conversioni e integrazione Explorer usano worker isolati.")
             if __import__('os').name=='nt' else
             tr("Su questa piattaforma sono applicati limiti dei processi, ma non è disponibile la sandbox AppContainer di Windows."))

        )
        self.tabs.addTab(self.security_details, tr("Sicurezza"))
        layout.addWidget(self.tabs, 1)
        self.feedback = _label("", "MetadataFeedback")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText(tr("Applica modifiche"))
        self.apply_button.setObjectName("Primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("Chiudi"))
        self.apply_button.clicked.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._load()

    def _load(self):
        self.snapshot = self.editor.metadata()
        version = self.snapshot.version.replace("%PDF-", "PDF ")
        self.caption.setText(tr('{0} · {1} pagine · {2}', self.editor.output_path.name, self.snapshot.page_count, version))
        for key, field in self.fields.items():
            field.setReadOnly(not self.snapshot.editable)
            field.setClearButtonEnabled(self.snapshot.editable)
            field.setText(self.snapshot.values[key])
        warnings = list(self.snapshot.warnings)
        if self.snapshot.conflicts:
            names = ", ".join(tr(EDITABLE_FIELDS[key]) for key in self.snapshot.conflicts)
            warnings.append(tr('Info e XMP differiscono per: {0}. I valori Info hanno precedenza; i campi modificati verranno sincronizzati.', names))
        if self.snapshot.editable and (self.editor.document_info.signatures or self.editor.document_info.pdfa):
            warnings.append(tr("Anche modificare i metadati può invalidare firme e conformità PDF/A. L'originale rimane nella cronologia."))
        self.warning.setText("\n".join(warnings))
        self.warning.setVisible(bool(warnings))
        state = tr("Sola lettura") if self.editor.encrypted else tr("Modifiche da salvare") if self.editor.dirty else tr("Salvato")
        self.document_summary.setText(tr('File: {0}\nPagine: {1} · Versione: {2}\nDimensione della revisione: {3:,} byte · {4}', self.editor.output_path, self.snapshot.page_count, version, self.editor.path.stat().st_size, state))
        self.info_table.setRowCount(len(self.snapshot.info))
        for row, (key, value) in enumerate(sorted(self.snapshot.info.items())):
            self.info_table.setItem(row, 0, QTableWidgetItem(key))
            cell = QTableWidgetItem(value)
            cell.setToolTip(value)
            self.info_table.setItem(row, 1, cell)
        self.info_table.resizeRowsToContents()
        self.verification.setPlainText(verification_text(self.editor))
        self.xmp.setPlainText(self.snapshot.xml or tr("Nessun pacchetto XMP presente nel documento."))
        self._update_apply()

    def _update_apply(self):
        if not hasattr(self, "snapshot") or not hasattr(self, "apply_button"):
            return
        changed = any(field.text() != self.snapshot.values[key] for key, field in self.fields.items())
        self.apply_button.setEnabled(self.snapshot.editable and changed)

    def apply(self):
        if not self.apply_button.isEnabled():
            return
        try:
            self.apply_changes({key: field.text() for key, field in self.fields.items()})
        except Exception as exc:
            self.feedback.setText(tr('Modifica dei metadati non riuscita: {0}', exc))
        else:
            self._load()
            self.feedback.setText(tr("Metadati aggiornati nella copia di lavoro. Salva il PDF per conservarli."))
        self.feedback.show()
