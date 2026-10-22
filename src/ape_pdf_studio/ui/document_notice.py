"""Compact document notices with explicit, inspectable verification status."""
from ..core.localization import tr
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton


class DocumentNotice(QFrame):
    details_requested = Signal()
    links_requested = Signal()
    form_requested = Signal()

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.form_count = 0
        self.links_enabled = False
        self.setObjectName("DocumentNotice")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 5, 10, 5)
        row.setSpacing(12)
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        row.addWidget(self.text, 1)
        self.form_button = QToolButton()
        self.form_button.setText(tr("Compila modulo"))
        self.form_button.clicked.connect(self.form_requested)
        row.addWidget(self.form_button)
        self.links_button = QToolButton()
        self.links_button.clicked.connect(self.links_requested)
        row.addWidget(self.links_button)
        details = QToolButton()
        self.details = details
        details.setText(tr("Dettagli"))
        details.clicked.connect(self.show_details)
        row.addWidget(details)
        self.refresh()

    def refresh(self):
        info = self.editor.document_info
        modified = self.editor.modified_from_original
        parts = []
        if self.editor.encrypted:
            parts.append(tr("Modifiche disabilitate · documento aperto in sola lettura"))
        if info.pdfa:
            parts.append(tr("{0} nell'originale · conformità da verificare", info.pdfa) if modified else tr('{0} dichiarato', info.pdfa))
        if info.signatures:
            count = len(info.signatures)
            label = tr("1 firma digitale") if count == 1 else tr('{0} firme digitali', count)
            parts.append(tr("{0} nell'originale · PDF modificato", label) if modified else tr('{0} · validità non verificata', label))
        if info.warnings:
            parts.append(tr("Lettura metadati incompleta"))
        security = self.editor.security_report
        if "javascript" in security.findings:
            parts.append(tr("JavaScript incorporato · esecuzione disabilitata"))
        if "attachments" in security.findings:
            parts.append(tr("Allegati incorporati · apertura disabilitata"))
        if "external_links" in security.findings:
            parts.append(tr("Collegamenti esterni · conferma a ogni apertura") if self.links_enabled else
                         tr("Collegamenti esterni · apertura disabilitata"))
        if self.form_count:
            parts.append(tr("Modulo PDF · {0} campi", self.form_count))
        self.form_button.setVisible(bool(self.form_count))
        self.links_button.setVisible("external_links" in security.findings)
        self.links_button.setText(tr("Disabilita collegamenti") if self.links_enabled else tr("Abilita collegamenti…"))
        self.text.setText("   •   ".join(parts))
        tooltip = tr("PDF/A: identificazione dai metadati XMP, conformità non validata.\n"
                     "Firme: presenza di dati di firma, validità crittografica non verificata.\n"
                     "Le modifiche possono invalidare le firme e la conformità PDF/A.\n"
                     "Controllo preventivo attivo: dettagli nella scheda Sicurezza. Non è una scansione antivirus.")
        if self.editor.encrypted:
            tooltip = tr("Il documento è protetto. Le modifiche e il salvataggio sono disabilitati.") + "\n" + tooltip
        if "javascript" in security.findings:
            tooltip += "\n" + tr("JavaScript non viene eseguito. Calcoli, convalide e pulsanti basati su script potrebbero non funzionare.")
        self.setToolTip(tooltip)
        self.setProperty("modified", bool(modified))
        self.setProperty("securityWarning", bool(security.findings))
        # Descendant selectors must be refreshed too when the warning changes.
        for widget in (self, self.text, self.details):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.setVisible(bool(parts))

    def show_details(self):
        self.details_requested.emit()
