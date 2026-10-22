"""Application-owned print controls and live sheet preview, without native dialogs."""
from ..core.localization import tr
from pathlib import Path
from ..core.user_paths import user_path, same_file

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPageLayout, QPageSize, QPen
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
    QMessageBox, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.errors import PDFOperationError
from ..core.print_source import PrintSource
from .remote_pdf import RemoteDocument, RemotePrintSource
from .icons import icon
from .printing import (PrintJob, PrintOptions, copy_sequence, paint_tile,
                      printer_geometry, raster_plan, selected_pages)
from .theme import theme_color


class PrintCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        glyph = icon("down", theme_color("icon" if self.isEnabled() else "disabled_text"))
        glyph.paint(painter, self.width() - 22, (self.height() - 14) // 2, 14, 14)


def combo(entries):
    widget = PrintCombo()
    for label, value in entries:
        widget.addItem(label, value)
    return widget


def check(text, checked=False):
    widget = QCheckBox(text)
    widget.setChecked(checked)
    return widget


class SheetPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 310)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image = None
        self.geometry = None
        self.setAccessibleName(tr("Anteprima del foglio di stampa"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme_color("canvas")))
        if self.image is None:
            painter.setPen(QColor(theme_color("muted")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("Preparazione anteprima…"))
            return
        area = QRectF(self.rect()).adjusted(24, 24, -24, -24)
        factor = min(area.width() / self.image.width(), area.height() / self.image.height())
        width, height = self.image.width() * factor, self.image.height() * factor
        paper = QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)
        painter.fillRect(paper.translated(3, 5), QColor(theme_color("shadow")))
        painter.drawImage(paper, self.image)
        painter.setPen(QPen(QColor(theme_color("border")), 1))
        painter.drawRect(paper)
        if self.geometry:
            # The dotted frame shows the selected printable area; it is not printed.
            painter.save()
            painter.translate(paper.topLeft())
            painter.scale(paper.width() / self.geometry.paper.width(),
                          paper.height() / self.geometry.paper.height())
            pen = QPen(QColor(theme_color("strong_border")), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(self.geometry.printable)
            painter.restore()


class PrintDialog(QDialog):
    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.setObjectName("PrintDialog")
        self.setWindowTitle(tr("Stampa · APE PDF Studio"))
        self.setMinimumSize(850, 600)
        self.resize(1040, 780)
        self.tab = tab
        self.settings = parent.settings if parent else None
        self.source = RemotePrintSource(tab.editor)
        self.document = RemoteDocument(tab.editor, self)
        self._buffer = QBuffer(self)
        self.printer = None
        self.job = None
        self.output = None
        self._loaded = None
        self._pages = []
        self._sheet = 0
        self._closed = False
        self._ready = False
        self._preview_tiles = iter(())
        self._preview_image = None
        self._preview_geometry = None
        self._view_rect = self._visible_region()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._update_preview)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_tile)
        self._build_ui()
        self._printer_changed()
        self._ready = True
        self._update_preview()

    def _visible_region(self):
        transform = self.tab.viewer.page_transform(self.tab.page)
        inverse, ok = transform.inverted()
        if not ok:
            return QRectF()
        size = self.tab.pdf.pagePointSize(self.tab.page)
        return inverse.mapRect(QRectF(self.tab.viewer.viewport().rect())).intersected(
            QRectF(0, 0, size.width(), size.height()))

    def _card(self, title, parent_layout):
        frame = QFrame()
        frame.setObjectName("PrintCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        parent_layout.addWidget(frame)
        return layout

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)
        heading = QHBoxLayout()
        title = QLabel(tr("Stampa documento"))
        title.setObjectName("DialogTitle")
        heading.addWidget(title)
        heading.addStretch()
        filename = QLabel(self.tab.editor.source.name)
        filename.setTextFormat(Qt.TextFormat.PlainText)
        filename.setObjectName("Caption")
        filename.setMaximumWidth(380)
        filename.setWordWrap(True)
        heading.addWidget(filename)
        root.addLayout(heading)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        scroll = QScrollArea()
        scroll.setObjectName("PrintOptions")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(370)
        scroll.setMaximumWidth(430)
        controls = QWidget()
        self.controls = controls
        left = QVBoxLayout(controls)
        left.setContentsMargins(0, 0, 6, 0)
        left.setSpacing(12)
        scroll.setWidget(controls)
        columns.addWidget(scroll, 2)

        card = self._card(tr("Stampante"), left)
        self.printers = combo([(tr("Salva su PDF"), "")])
        for name in QPrinterInfo.availablePrinterNames():
            self.printers.addItem(name, name)
        default = QPrinterInfo.defaultPrinterName()
        preferred = self.settings.value("print/printer", default) if self.settings else default
        index = self.printers.findData(preferred)
        self.printers.setCurrentIndex(max(0, index))
        card.addWidget(self.printers)
        self.printer_status = QLabel()
        self.printer_status.setObjectName("Caption")
        self.printer_status.setWordWrap(True)
        card.addWidget(self.printer_status)
        form = QFormLayout()
        form.setSpacing(6)
        self.paper = PrintCombo()
        self.orientation = combo([(tr("Verticale"), QPageLayout.Orientation.Portrait),
                                  (tr("Orizzontale"), QPageLayout.Orientation.Landscape)])
        self.colors = PrintCombo()
        self.duplex = PrintCombo()
        self.copies = QSpinBox()
        self.copies.setRange(1, 999)
        self.collate = check(tr("Fascicola"), True)
        copy_row = QHBoxLayout()
        copy_row.addWidget(self.copies)
        copy_row.addWidget(self.collate)
        paper_row = QHBoxLayout()
        paper_row.addWidget(self.paper, 1)
        paper_row.addWidget(self.orientation, 1)
        output_row = QHBoxLayout()
        output_row.addWidget(self.colors, 1)
        output_row.addWidget(self.duplex, 1)
        self.paper.setToolTip(tr("Formato della carta"))
        self.orientation.setToolTip(tr("Orientamento del foglio"))
        self.colors.setToolTip(tr("Colore della stampa"))
        self.duplex.setToolTip(tr("Fronte-retro, se supportato dalla stampante"))
        form.addRow(tr("Carta"), paper_row)
        form.addRow(tr("Uscita"), output_row)
        form.addRow(tr("Copie"), copy_row)
        card.addLayout(form)

        card = self._card(tr("Pagine da stampare"), left)
        self.selection = combo([(tr("Tutte le pagine"), "all"), (tr("Pagina corrente"), "current"),
                                (tr("Intervallo personalizzato"), "range"), (tr("Vista corrente"), "view")])
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText(tr("Esempio: 1, 3–5, 8"))
        self.range_edit.setAccessibleName(tr("Intervallo delle pagine di stampa"))
        self.range_edit.setText(str(self.tab.page + 1))
        self.parity = combo([(tr("Tutte"), "all"), (tr("Solo dispari"), "odd"), (tr("Solo pari"), "even")])
        self.reverse = check(tr("Ordine inverso"))
        card.addWidget(self.selection)
        card.addWidget(self.range_edit)
        row = QHBoxLayout()
        row.addWidget(self.parity, 1)
        row.addWidget(self.reverse)
        card.addLayout(row)

        card = self._card(tr("Posizione e scala"), left)
        self.scale_mode = combo([(tr("Adatta all’area stampabile"), "fit"), (tr("Riduci solo pagine grandi"), "shrink"),
                                 (tr("Dimensioni reali (100%)"), "actual"), (tr("Scala personalizzata"), "custom")])
        self.scale = QSpinBox()
        self.scale.setRange(10, 1000)
        self.scale.setValue(100)
        self.scale.setSuffix(" %")
        row = QHBoxLayout()
        row.addWidget(self.scale_mode, 1)
        row.addWidget(self.scale)
        card.addLayout(row)
        self.auto_rotate = check(tr("Ruota automaticamente"), True)
        self.center = check(tr("Centra sul foglio"), True)
        self.ignore_margins = check(tr("Ignora i margini della stampante"))
        self.ignore_margins.setToolTip(tr("Usa tutto il foglio per il posizionamento. La stampante può comunque tagliare i bordi."))
        for widget in (self.auto_rotate, self.center, self.ignore_margins):
            card.addWidget(widget)

        card = self._card(tr("Contenuto e qualità"), left)
        self.comments = check(tr("Includi note e annotazioni stampabili"), True)
        self.images = check(tr("Stampa come immagini"))
        self.images.setToolTip(tr("Compatibilità per documenti complessi. Altrimenti si preferisce la stampa vettoriale."))
        self.dpi = combo([("150 dpi", 150), ("300 dpi", 300), ("600 dpi", 600)])
        self.dpi.setCurrentIndex(1)
        card.addWidget(self.comments)
        row = QHBoxLayout()
        row.addWidget(self.images, 1)
        row.addWidget(self.dpi)
        card.addLayout(row)
        self.dpi.setToolTip(tr("Risoluzione per le immagini e le pagine di compatibilità. I tracciati vettoriali restano indipendenti dai DPI."))
        left.addStretch()

        right = QVBoxLayout()
        right.setSpacing(10)
        preview_heading = QHBoxLayout()
        label = QLabel(tr("Anteprima di stampa"))
        label.setObjectName("SectionTitle")
        preview_heading.addWidget(label)
        preview_heading.addStretch()
        self.sheet_label = QLabel()
        self.sheet_label.setObjectName("Caption")
        preview_heading.addWidget(self.sheet_label)
        right.addLayout(preview_heading)
        self.preview = SheetPreview()
        right.addWidget(self.preview, 1)
        navigation = QHBoxLayout()
        self.previous = QPushButton()
        self.previous.setIcon(icon("prev"))
        self.previous.setToolTip(tr("Foglio precedente"))
        self.next = QPushButton()
        self.next.setIcon(icon("next"))
        self.next.setToolTip(tr("Foglio successivo"))
        self.preview_info = QLabel()
        self.preview_info.setObjectName("Caption")
        self.preview_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_info.setWordWrap(True)
        navigation.addWidget(self.previous)
        navigation.addWidget(self.preview_info, 1)
        navigation.addWidget(self.next)
        right.addLayout(navigation)
        columns.addLayout(right, 3)
        root.addLayout(columns, 1)
        self.feedback = QLabel()
        self.feedback.setObjectName("EditFeedback")
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        footer = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("Caption")
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(190)
        self.progress.hide()
        footer.addWidget(self.progress)
        self.cancel_button = QPushButton(tr("Annulla"))
        self.print_button = QPushButton(tr("Stampa"))
        self.print_button.setObjectName("Primary")
        self.print_button.setIcon(icon("print", "#ffffff"))
        self.print_button.setDefault(True)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.print_button)
        root.addLayout(footer)
        self.print_button.clicked.connect(self._print)
        self.cancel_button.clicked.connect(self.reject)
        self.printers.currentIndexChanged.connect(self._printer_changed)
        self.previous.clicked.connect(lambda: self._navigate(-1))
        self.next.clicked.connect(lambda: self._navigate(1))
        for widget in (self.paper, self.orientation, self.colors, self.duplex, self.selection,
                       self.parity, self.scale_mode, self.dpi):
            widget.currentIndexChanged.connect(self._changed)
        for widget in (self.copies, self.scale):
            widget.valueChanged.connect(self._changed)
        for widget in (self.collate, self.reverse, self.auto_rotate, self.center,
                       self.ignore_margins, self.comments, self.images):
            widget.toggled.connect(self._changed)
        self.range_edit.textChanged.connect(self._changed)

    def _printer_changed(self, *_):
        name = self.printers.currentData()
        info = QPrinterInfo.printerInfo(name) if name else None
        if info and info.isNull():
            self.printer = None
            self.printer_status.setText(tr("Stampante non disponibile"))
            self.feedback.setText(tr("La stampante selezionata non è più disponibile."))
            self.print_button.setEnabled(False)
            return
        self.printer = QPrinter(info, QPrinter.PrinterMode.HighResolution) if info else QPrinter(QPrinter.PrinterMode.HighResolution)
        if not name:
            self.printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        self.printer_status.setText(info.makeAndModel() or tr("Stampante di sistema") if info else tr("Esporta una copia pronta per la stampa"))
        for widget in (self.paper, self.colors, self.duplex):
            widget.blockSignals(True)
            widget.clear()
        sizes = info.supportedPageSizes() if info else [QPageSize(value) for value in (
            QPageSize.PageSizeId.A4, QPageSize.PageSizeId.A3, QPageSize.PageSizeId.A5,
            QPageSize.PageSizeId.Letter, QPageSize.PageSizeId.Legal, QPageSize.PageSizeId.A2,
            QPageSize.PageSizeId.A1, QPageSize.PageSizeId.A0)]
        default = info.defaultPageSize() if info else QPageSize(QPageSize.PageSizeId.A4)
        if not sizes:
            sizes = [self.printer.pageLayout().pageSize()]
        for size in sizes:
            self.paper.addItem(size.name(), size)
        self.paper.setCurrentIndex(next((i for i, size in enumerate(sizes) if size.isEquivalentTo(default)), 0))
        colors = info.supportedColorModes() if info else [QPrinter.ColorMode.Color, QPrinter.ColorMode.GrayScale]
        for mode in colors or [self.printer.colorMode()]:
            self.colors.addItem(tr("Colore") if mode == QPrinter.ColorMode.Color else tr("Bianco e nero"), mode)
        self.colors.setCurrentIndex(max(0, self.colors.findData(self.printer.colorMode())))
        modes = info.supportedDuplexModes() if info else [QPrinter.DuplexMode.DuplexNone]
        for mode, label in [(QPrinter.DuplexMode.DuplexNone, tr("Solo fronte")),
                            (QPrinter.DuplexMode.DuplexLongSide, tr("Lato lungo")),
                            (QPrinter.DuplexMode.DuplexShortSide, tr("Lato corto"))]:
            if mode in modes or mode == QPrinter.DuplexMode.DuplexNone:
                self.duplex.addItem(label, mode)
        self.duplex.setEnabled(self.duplex.count() > 1)
        self._resolutions = info.supportedResolutions() if info else [300, 600]
        for widget in (self.paper, self.colors, self.duplex):
            widget.blockSignals(False)
        self._changed()

    def _changed(self, *_):
        self.range_edit.setVisible(self.selection.currentData() == "range")
        self.scale.setEnabled(self.scale_mode.currentData() == "custom")
        self.collate.setEnabled(self.copies.value() > 1)
        if self._ready:
            self._render_timer.stop()
            self.print_button.setEnabled(False)
            self._debounce.start(140)

    def _options(self):
        return PrintOptions(self.scale_mode.currentData(), self.scale.value() / 100,
                            self.auto_rotate.isChecked(), self.center.isChecked(),
                            self.ignore_margins.isChecked(), self.images.isChecked(), self.dpi.currentData())

    def _configure_printer(self):
        if self.printer is None or not self.printer.isValid():
            raise PDFOperationError(tr("La stampante selezionata non è disponibile."))
        if not self.printer.setPageSize(self.paper.currentData()):
            raise PDFOperationError(tr("La stampante non supporta il formato scelto."))
        if not self.printer.setPageOrientation(self.orientation.currentData()):
            raise PDFOperationError(tr("La stampante non supporta l’orientamento scelto."))
        self.printer.setColorMode(self.colors.currentData())
        self.printer.setDuplex(self.duplex.currentData())
        self.printer.setResolution(min(self._resolutions or [300], key=lambda value: abs(value - self.dpi.currentData())))
        self.printer.setDocName(self.tab.editor.source.name)
        self.printer.setCreator("APE PDF Studio")
        self.printer.setFullPage(True)

    def _load_source(self):
        key = self.comments.isChecked()
        if key != self._loaded:
            self._render_timer.stop()
            self.document.close()
            self._buffer.close()
            path = self.source.path(key)
            # Loading from QFile retains a Windows handle beyond close() in QtPdf.
            self.document.load(path)
            if self.document.status() != QPdfDocument.Status.Ready:
                raise PDFOperationError(tr("Impossibile preparare il documento per la stampa."))
            self._loaded = key
        return self.source.path(key)

    def _update_preview(self):
        if self._closed or (self.job and self.job.active):
            return
        self._render_timer.stop()
        self._debounce.stop()
        try:
            self._configure_printer()
            self._load_source()
            selection = self.selection.currentData()
            pages = selected_pages(self.tab.count, self.tab.page, selection,
                                   self.range_edit.text().replace("–", "-"),
                                   self.parity.currentData(), self.reverse.isChecked())
            duplex = self.duplex.currentData() != QPrinter.DuplexMode.DuplexNone
            self._pages = copy_sequence(pages, self.copies.value(), self.collate.isChecked(), duplex)
            self._sheet = min(self._sheet, len(self._pages) - 1)
            page = self._pages[self._sheet]
            region = self._view_rect if selection == "view" else None
            size = self.document.pagePointSize(self.tab.page if page is None else page)
            geometry = printer_geometry(self.printer, size, self._options(), region)
            self._preview_geometry = geometry
            ratio = min(1000 / geometry.paper.width(), 1200 / geometry.paper.height())
            image = QImage(round(geometry.paper.width() * ratio), round(geometry.paper.height() * ratio), QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            self._preview_image = image
            tiles, self._tile_source = raster_plan(page, size, geometry, ratio * 72)
            if page is None:
                tiles = []
            self._preview_tiles = iter(tiles)
            self._preview_ratio = ratio
            self.sheet_label.setText(tr('{0} {1} di {2}', tr('Facciata') if duplex else tr('Foglio'), self._sheet + 1, len(self._pages)))
            size = self.paper.currentData().size(QPageSize.Unit.Millimeter)
            if self.orientation.currentData() == QPageLayout.Orientation.Landscape:
                size.transpose()
            prefix = tr("Retro vuoto tra le copie") if page is None else tr('Pagina PDF {0}', page + 1)
            self.preview_info.setText(tr('{0} · {1:.0f} × {2:.0f} mm\nScala effettiva {3:.1f}%', prefix, size.width(), size.height(), geometry.scale * 100) + (tr(" · ruotata") if geometry.rotated else ""))
            sheets = (len(self._pages) + 1) // 2 if duplex else len(self._pages)
            self.summary.setText(tr('{0} pagine · {1} copie · {2} fogli', len(pages), self.copies.value(), sheets))
            self.feedback.clear()
            self.previous.setEnabled(self._sheet > 0)
            self.next.setEnabled(self._sheet + 1 < len(self._pages))
            self.print_button.setText(tr("Salva PDF…") if not self.printers.currentData() else tr("Stampa"))
            self.print_button.setEnabled(True)
            self._render_timer.start(0)
        except Exception as exc:
            self._pages = []
            self.feedback.setText(str(exc))
            self.print_button.setEnabled(False)
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            self.preview.image = None
            self.preview.update()

    def _render_tile(self):
        tile = next(self._preview_tiles, None)
        try:
            if tile is not None:
                painter = QPainter(self._preview_image)
                try:
                    painter.scale(self._preview_ratio, self._preview_ratio)
                    paint_tile(painter, self.document, tile, self._tile_source, self._preview_geometry)
                finally:
                    painter.end()
                self._render_timer.start(0)
            else:
                if self.colors.currentData() == QPrinter.ColorMode.GrayScale:
                    self._preview_image = self._preview_image.convertToFormat(QImage.Format.Format_Grayscale8)
                self.preview.image = self._preview_image
                self.preview.geometry = self._preview_geometry
                self.preview.update()
        except Exception as exc:
            self.feedback.setText(str(exc))
            self.print_button.setEnabled(False)

    def _navigate(self, delta):
        self._sheet = max(0, min(len(self._pages) - 1, self._sheet + delta))
        self._update_preview()

    def _print(self):
        self._update_preview()  # Validate pending edits even if debounce has not fired yet.
        if not self._pages or not self.print_button.isEnabled():
            return
        self.output = None
        if not self.printers.currentData():
            destination, _ = QFileDialog.getSaveFileName(self, tr("Salva stampa su PDF"), str(
                self.tab.editor.source.with_name(self.tab.editor.source.stem + "_stampa.pdf")), "PDF (*.pdf)",
                options=QFileDialog.Option.DontConfirmOverwrite)
            if not destination:
                return
            self.output = Path(destination).expanduser()
            if self.output.suffix.lower() != ".pdf":
                self.output = self.output.with_suffix(".pdf")
            self.output = user_path(self.output)
            if any(same_file(self.output, path) for path in (self.tab.editor.source, self.tab.editor.output_path)):
                self.feedback.setText(tr("Scegli un nome diverso dal documento aperto per la copia di stampa."))
                return
            parent = self.parentWidget()
            if parent and hasattr(parent, "_output_available") and not parent._output_available(self.output, self.tab):
                return
            if self.output.exists():
                answer = QMessageBox.question(self, tr("Sostituisci copia di stampa"),
                    tr('Il file esiste già:\n{0}\n\nSostituirlo con la nuova copia di stampa?', self.output),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
        self._render_timer.stop()
        self._debounce.stop()
        self.controls.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.print_button.setEnabled(False)
        self.progress.setRange(0, len(self._pages))
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.setText(tr("Interrompi"))
        self.feedback.clear()
        if self.settings:
            self.settings.setValue("print/printer", self.printers.currentData())
        self.job = PrintJob(self.document, self._load_source(), self.printer, self._pages,
                            self._options(), self, output=self.output,
                            source_rect=self._view_rect if self.selection.currentData() == "view" else None)
        self.job.progress.connect(lambda value, total: self.progress.setValue(value))
        self.job.finished.connect(self._finished)
        self.job.failed.connect(self._failed)
        self.job.cancelled.connect(self._cancelled)
        self.job.start()

    def _finished(self):
        parent = self.parentWidget()
        if parent and hasattr(parent, "toast"):
            parent.toast.show_completion(tr("Stampa salvata") if self.output else tr("Stampa inviata"),
                                         self.output or self.tab.editor.source,
                                         "" if self.output else tr('Inviata a {0}', self.printers.currentText()))
        self.accept()

    def _failed(self, message):
        self.controls.setEnabled(True)
        self.progress.hide()
        self.cancel_button.setText(tr("Annulla"))
        self._update_preview()
        self.feedback.setText(message)

    def _cancelled(self):
        self._failed(tr("Stampa interrotta.") if self.output else
                     tr("Interruzione richiesta. I fogli già inviati potrebbero essere stampati: controlla la coda della stampante."))

    def reject(self):
        if self.job and self.job.active:
            self.job.cancel()
            return
        super().reject()

    def done(self, result):
        if not self._closed:
            self._closed = True
            self._render_timer.stop()
            self._debounce.stop()
            if self.job:
                self.job.cancel()
            self.document.close()
            self._buffer.close()
            self._buffer.setData(b"")
            self.source.close()
        super().done(result)
