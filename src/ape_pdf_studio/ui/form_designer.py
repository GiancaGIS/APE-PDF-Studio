"""Review form suggestions on the PDF page, then explicitly create AcroForm fields."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile

from PySide6.QtCore import QPointF, QProcess, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core.editor import parse_page_range
from ..core.errors import PDFOperationError
from ..core.form_design import MAX_ANALYSIS_PAGES, validate_proposals
from ..core.localization import tr
from ..core.security import copy_checked
from ..core.worker_process import worker_command
from .ocr import default_ocr_cache


class FormDesigner(QWidget):
    state_changed = Signal()

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.proposals = []
        self.selected = -1
        self.revision = tab.editor.path
        self.mode = "select"
        self.process = None
        self._work = None
        self._output = bytearray()
        self._loading = False
        self._error = None
        self._analysis_revision = None
        self._applying = False
        self._drag = None
        self._preview = None
        self.setMinimumWidth(285)
        self.setMaximumWidth(370)
        outer = QVBoxLayout(self)
        content = QWidget()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(content)
        outer.addWidget(area,1)
        layout = QVBoxLayout(content)
        title = QLabel(tr("Prepara modulo"))
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        description = QLabel(tr("Rivedi i riquadri sulla pagina. Le proposte diventano campi compilabili solo dopo la conferma."))
        description.setWordWrap(True)
        layout.addWidget(description)
        row = QHBoxLayout()
        self.pages = QLineEdit(str(tab.page+1))
        self.pages.setPlaceholderText(tr("Esempio: 1,3-5"))
        row.addWidget(QLabel(tr("Pagine")))
        row.addWidget(self.pages, 1)
        layout.addLayout(row)
        self.ocr = QCheckBox(tr("OCR etichette scansioni"))
        self.ocr.setChecked(True)
        self.ocr.setToolTip(tr("Usa le lingue OCR già installate. Nessun download o invio del documento."))
        layout.addWidget(self.ocr)
        row = QHBoxLayout()
        self.detect_button = QPushButton(tr("Riconosci campi"))
        self.detect_button.clicked.connect(self.detect)
        row.addWidget(self.detect_button)
        self.cancel_button = QPushButton(tr("Annulla analisi"))
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.draw_kind = QComboBox()
        self.draw_kind.addItem(tr("Seleziona / sposta / ridimensiona"), "select")
        self.draw_kind.addItem(tr("Disegna campo di testo"), "text")
        self.draw_kind.addItem(tr("Disegna casella di spunta"), "checkbox")
        self.draw_kind.currentIndexChanged.connect(self._set_mode)
        layout.addWidget(self.draw_kind)
        self.list = QListWidget()
        self.list.setMinimumHeight(90)
        self.list.setMaximumHeight(150)
        self.list.currentRowChanged.connect(self.select)
        layout.addWidget(self.list, 1)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.label = QLineEdit()
        self.label.setMaxLength(256)
        self.kind = QComboBox()
        self.kind.addItem(tr("Testo"), "text")
        self.kind.addItem(tr("Casella di spunta"), "checkbox")
        self.multiline = QCheckBox(tr("Testo su più righe"))
        for title, control in ((tr("Nome campo"), self.name), (tr("Etichetta"), self.label), (tr("Tipo"), self.kind)):
            form.addRow(title, control)
        form.addRow(self.multiline)
        self.coordinates = []
        for text in ("X", "Y", tr("Larghezza"), tr("Altezza")):
            control = QDoubleSpinBox()
            control.setRange(0, 14400)
            control.setDecimals(1)
            control.setSuffix(" pt")
            form.addRow(text, control)
            self.coordinates.append(control)
            control.valueChanged.connect(self.update_selected)
        layout.addLayout(form)
        self.name.textEdited.connect(self.update_selected)
        self.label.textEdited.connect(self.update_selected)
        self.kind.currentIndexChanged.connect(self.update_selected)
        self.multiline.toggled.connect(self.update_selected)
        self.remove_button = QPushButton(tr("Elimina proposta"))
        self.remove_button.clicked.connect(self.remove_selected)
        layout.addWidget(self.remove_button)
        self.feedback = QLabel(tr("Disegna un campo oppure avvia il riconoscimento. Controlla sempre le proposte."))
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        outer.addWidget(self.feedback)
        self.apply_button = QPushButton(tr("Crea campi…"))
        self.apply_button.setObjectName("Primary")
        self.apply_button.clicked.connect(self.apply)
        outer.addWidget(self.apply_button)
        close = QPushButton(tr("Chiudi preparazione"))
        close.clicked.connect(self.close_preparation)
        outer.addWidget(close)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self.refresh()
        self.hide()

    def open(self):
        if not self.tab.finish_form():
            return
        if not self.isVisible():
            self.revision = self.tab.editor.path
            self.pages.setText(str(self.tab.page+1))
        self.show()
        self.tab.viewer._cancel_placement()
        self.tab.viewer.form_designer = self
        self.tab.splitter.setSizes([180, max(300, self.tab.width()-510), 315])
        self.state_changed.emit()
        self.tab.viewer.viewport().update()

    def _set_mode(self):
        self.mode = self.draw_kind.currentData()
        self.tab.viewer.setFocus()

    def refresh(self):
        self._loading = True
        self.list.blockSignals(True)
        self.list.clear()
        for proposal in self.proposals:
            self.list.addItem(tr("P{0} · {1}", proposal["page"]+1, proposal["name"]))
        self.list.setCurrentRow(self.selected)
        self.list.blockSignals(False)
        self._loading = False
        self.select(self.selected, navigate=False)
        self.apply_button.setEnabled(bool(self.proposals) and self.process is None and self.revision == self.tab.editor.path)
        self.tab.viewer.viewport().update()

    def select(self, index, navigate=True):
        self.selected = index if 0 <= index < len(self.proposals) else -1
        self._loading = True
        for control in (self.name, self.label, self.kind, self.multiline, *self.coordinates, self.remove_button):
            control.setEnabled(self.selected >= 0 and self.process is None)
        if self.selected >= 0:
            proposal = self.proposals[self.selected]
            self.name.setText(proposal["name"])
            self.label.setText(proposal["label"])
            self.kind.setCurrentIndex(self.kind.findData(proposal["kind"]))
            self.multiline.setChecked(proposal.get("multiline", False))
            self.multiline.setEnabled(proposal["kind"]=="text" and self.process is None)
            left, top, right, bottom = proposal["rect"]
            for control, value in zip(self.coordinates, (left, top, right-left, bottom-top)):
                control.setValue(value)
            if navigate and self.tab.page != proposal["page"]:
                self.tab.jump_to_page(proposal["page"])
        self._loading = False
        self.tab.viewer.viewport().update()

    def update_selected(self, *_args):
        if self._loading or not 0 <= self.selected < len(self.proposals):
            return
        proposal = self.proposals[self.selected]
        x, y, width, height = (control.value() for control in self.coordinates)
        proposal.update(name=self.name.text(), label=self.label.text(), kind=self.kind.currentData(),
                        multiline=self.multiline.isChecked(), rect=[x,y,x+width,y+height])
        self.multiline.setEnabled(proposal["kind"]=="text")
        self.list.item(self.selected).setText(tr("P{0} · {1}", proposal["page"]+1, proposal["name"]))
        self.tab.viewer.viewport().update()

    def press(self, hit, position):
        if self.process is not None or self.revision != self.tab.editor.path or hit is None:
            return
        page, point = hit
        if self.mode in ("text", "checkbox"):
            self._drag = dict(page=page, start=point, kind="draw")
            return
        for index in reversed(range(len(self.proposals))):
            proposal = self.proposals[index]
            if proposal["page"] != page:
                continue
            left, top, right, bottom = proposal["rect"]
            target = self.tab.viewer.page_transform(page).mapRect(QRectF(left,top,right-left,bottom-top))
            handle = QRectF(target.right()-7,target.bottom()-7,14,14)
            if target.contains(position) or handle.contains(position):
                self.select(index, navigate=False)
                self._drag = dict(page=page,start=point,kind="resize" if handle.contains(position) else "move",
                                  index=index,rect=list(proposal["rect"]))
                return

    def move(self, position):
        if self._drag is None:
            return
        drag = self._drag
        page = drag["page"]
        inverse, ok = self.tab.viewer.page_transform(page).inverted()
        if not ok:
            return
        point = inverse.map(position)
        size = self.tab.pdf.pagePointSize(page)
        x, y = min(size.width(),max(0,point.x())), min(size.height(),max(0,point.y()))
        start = drag["start"]
        if drag["kind"] == "draw":
            self._preview = (page,[min(x,start.x()),min(y,start.y()),max(x,start.x()),max(y,start.y())])
        else:
            left,top,right,bottom = drag["rect"]
            if drag["kind"] == "move":
                width,height = right-left,bottom-top
                left = max(0,min(size.width()-width,left+x-start.x()))
                top = max(0,min(size.height()-height,top+y-start.y()))
                rect = [left,top,left+width,top+height]
            else:
                rect = [left,top,max(left+5,x),max(top+5,y)]
            self.proposals[drag["index"]]["rect"] = rect
        self.tab.viewer.viewport().update()

    def release(self, position):
        if self._drag is None:
            return
        self.move(position)
        drag,self._drag = self._drag,None
        if drag["kind"] == "draw" and self._preview:
            page,rect = self._preview
            if rect[2]-rect[0]>=5 and rect[3]-rect[1]>=5:
                self.add_manual(page,rect)
        else:
            self.select(self.selected,navigate=False)
        self._preview = None
        self.tab.viewer.viewport().update()

    def paint(self, painter, page, transform):
        if self.revision != self.tab.editor.path:
            return
        for index,proposal in enumerate(self.proposals):
            if proposal["page"] != page:
                continue
            left,top,right,bottom = proposal["rect"]
            target = transform.mapRect(QRectF(left,top,right-left,bottom-top))
            color = QColor("#bd6b00" if index == self.selected else "#19775a")
            tint = QColor(color)
            tint.setAlpha(50)
            painter.fillRect(target,tint)
            painter.setPen(QPen(color,2 if index == self.selected else 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target)
            painter.save()
            painter.setClipRect(target.adjusted(1,1,-1,-1))
            painter.drawText(target.adjusted(3,1,-1,-1),Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignLeft,proposal["name"])
            painter.restore()
            if index == self.selected:
                painter.fillRect(QRectF(target.right()-4,target.bottom()-4,8,8),color)
        if self._preview and self._preview[0] == page:
            left,top,right,bottom = self._preview[1]
            painter.setPen(QPen(QColor("#bd6b00"),2,Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(transform.mapRect(QRectF(left,top,right-left,bottom-top)))

    def add_manual(self, page, rect):
        if self.process is not None or self.mode not in ("text", "checkbox"):
            return
        names = {proposal["name"] for proposal in self.proposals} | {field.name for field in self.tab.form_fields}
        number = 1
        while f"campo_{number}" in names:
            number += 1
        if len(self.proposals)+len(self.tab.form_fields) >= 500:
            self.feedback.setText(tr("Raggiunto il limite di 500 campi: analizza meno pagine per volta."))
            return
        self.proposals.append(dict(page=page, rect=list(rect), kind=self.mode, name=f"campo_{number}", label=f"campo_{number}", multiline=False))
        self.selected = len(self.proposals)-1
        self.refresh()
        self.draw_kind.setCurrentIndex(0)

    def remove_selected(self):
        if 0 <= self.selected < len(self.proposals):
            self.proposals.pop(self.selected)
            self.selected = min(self.selected, len(self.proposals)-1)
            self.refresh()

    def _confirm(self, message):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr("Prepara modulo"))
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        result = dialog.exec()
        dialog.deleteLater()
        return result == QMessageBox.StandardButton.Yes

    def detect(self):
        if self.process is not None:
            return
        if self.proposals and not self._confirm(tr("Sostituire le proposte attuali con un nuovo riconoscimento?")):
            return
        try:
            pages = parse_page_range(self.pages.text(), self.tab.count)
            if len(pages) > MAX_ANALYSIS_PAGES:
                raise PDFOperationError(tr("Analizza al massimo 50 pagine per volta."))
            self._analysis_revision = self.tab.editor.path
            self._work = tempfile.TemporaryDirectory(prefix="ape_form_detect_")
            source = Path(self._work.name)/"source.pdf"
            copy_checked(self._analysis_revision, source)
            command = worker_command("form_detect")
            process = QProcess(self)
            self.process = process
            self._output = bytearray()
            self._error = None
            process.setProgram(command[0])
            process.setArguments(command[1:])
            process.setStandardErrorFile(QProcess.nullDevice())
            process.readyReadStandardOutput.connect(self._read)
            process.finished.connect(self._finished)
            process.errorOccurred.connect(self._process_error)
            request = json.dumps(dict(path=str(source), pages=pages, cache_dir=str(default_ocr_cache()), use_ocr=self.ocr.isChecked())).encode()
            process.started.connect(lambda: (process.write(request), process.closeWriteChannel()))
            self.detect_button.setEnabled(False)
            self.draw_kind.setEnabled(False)
            self.list.setEnabled(False)
            self.select(self.selected,navigate=False)
            self.cancel_button.show()
            self.apply_button.setEnabled(False)
            self.feedback.setText(tr("Analisi locale in corso… Puoi annullarla senza modificare il PDF."))
            self._timer.start(90000)
            process.start()
        except Exception as exc:
            self.feedback.setText(str(exc))
            self._cleanup()

    def _read(self):
        if self.process is None:
            return
        chunk = bytes(self.process.readAllStandardOutput())
        if len(self._output)+len(chunk) > 2_000_000:
            self._error = tr("Risultato del riconoscimento troppo grande.")
            self.process.kill()
        else:
            self._output.extend(chunk)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = tr("Impossibile avviare il riconoscimento dei campi.")
            self._finished(1)

    def _finished(self, code, *_args):
        self._read()
        try:
            if self._error:
                raise PDFOperationError(self._error)
            if code != 0:
                raise PDFOperationError(tr("Riconoscimento interrotto o PDF non analizzabile. Le proposte precedenti sono conservate."))
            if self._analysis_revision != self.tab.editor.path:
                raise PDFOperationError(tr("Il documento è cambiato. Ripeti la preparazione del modulo."))
            payload = json.loads(self._output)
            proposals = payload["proposals"]
            sizes = [(self.tab.pdf.pagePointSize(i).width(),self.tab.pdf.pagePointSize(i).height()) for i in range(self.tab.count)]
            if proposals:
                proposals = validate_proposals(proposals, sizes, self.tab.form_fields)
            elif not isinstance(proposals, list):
                raise ValueError("Invalid proposals")
            warnings = payload.get("warnings", [])
            if not isinstance(warnings, list) or len(warnings) > 10 or any(not isinstance(w, str) or len(w)>1000 for w in warnings):
                raise ValueError("Invalid warnings")
            self.revision = self._analysis_revision
            self.proposals = proposals
            self.selected = 0 if proposals else -1
            self.feedback.setText(tr("{0} proposte da verificare. Puoi correggerle o aggiungere campi manualmente.", len(proposals)) + ("\n"+"\n".join(warnings) if warnings else ""))
        except Exception as exc:
            self.feedback.setText(str(exc) if isinstance(exc, PDFOperationError) else tr("Risultato del riconoscimento non valido."))
        finally:
            self._cleanup()
            self.refresh()

    def _cleanup(self):
        self._timer.stop()
        process, self.process = self.process, None
        if process is not None:
            process.deleteLater()
        if self._work is not None:
            self._work.cleanup()
            self._work = None
        self.detect_button.setEnabled(True)
        self.draw_kind.setEnabled(True)
        self.list.setEnabled(True)
        self.cancel_button.hide()

    def _timeout(self):
        self.cancel_analysis(tr("Analisi annullata: superato il limite di 90 secondi."))

    def cancel_analysis(self, message=None):
        if self.process is not None:
            self._error = message if isinstance(message, str) else tr("Analisi annullata. Il PDF non è stato modificato.")
            self.process.kill()

    def apply(self):
        if self.process is not None or self.revision != self.tab.editor.path:
            self.feedback.setText(tr("Il documento è cambiato. Ripeti la preparazione del modulo."))
            return
        try:
            sizes = [(self.tab.pdf.pagePointSize(i).width(),self.tab.pdf.pagePointSize(i).height()) for i in range(self.tab.count)]
            proposals = validate_proposals(deepcopy(self.proposals), sizes, self.tab.form_fields)
            if not self._confirm(tr("Creare {0} campi compilabili nella copia di lavoro?\n\nLe modifiche possono invalidare firme e conformità PDF/A. Potrai annullare con Ctrl+Z. Usa Salva per conservarle nel file.", len(proposals))):
                return
            self._applying = True
            self.tab.apply_edit("create_form_fields", proposals, self.revision)
            self.proposals = []
            self.selected = -1
            self.feedback.setText(tr("Campi creati. Chiudi la preparazione per compilarli sulla pagina e usa Salva per conservarli."))
            self.revision = self.tab.editor.path
        except PDFOperationError as exc:
            self.feedback.setText(str(exc))
        finally:
            self._applying = False
            self.refresh()

    def confirm_discard(self):
        return not self.proposals or self._confirm(tr("Scartare le proposte non ancora create?"))

    def close_preparation(self):
        if not self.confirm_discard():
            return
        self.shutdown()
        self.proposals = []
        self.selected = -1
        self._drag = None
        self._preview = None
        self.hide()
        self.tab.viewer.form_designer = None
        self.tab.viewer.viewport().unsetCursor()
        self.tab.viewer.viewport().update()
        self.state_changed.emit()

    def document_changed(self):
        if self.isVisible() and not self._applying and self.revision != self.tab.editor.path:
            self.feedback.setText(tr("Il documento è cambiato. Ripeti la preparazione del modulo."))
            self.apply_button.setEnabled(False)

    def shutdown(self):
        if self.process is not None:
            self.process.blockSignals(True)
            self.process.kill()
            self.process.waitForFinished(1000)
        self._cleanup()
