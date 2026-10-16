"""Shared preview/output geometry and cooperative, bounded-memory print jobs."""
from ..core.localization import tr
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QObject, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtGui import QPageLayout

from ..core.editor import parse_page_range
from ..core.errors import PDFOperationError
from .page_tiles import visible_tiles
from .vector_pages import VectorPages


@dataclass(frozen=True)
class PrintOptions:
    mode: str = "fit"
    scale: float = 1.0
    rotate: bool = True
    center: bool = True
    ignore_margins: bool = False
    images: bool = False
    dpi: int = 300


def selected_pages(count, current, selection="all", expression="", parity="all", reverse=False):
    if selection in ("current", "view"):
        pages = [current]
    elif selection == "range":
        pages = parse_page_range(expression, count)
    else:
        pages = list(range(count))
    if parity != "all":
        pages = [page for page in pages if (page + 1) % 2 == (1 if parity == "odd" else 0)]
    if reverse:
        pages.reverse()
    if not pages:
        raise PDFOperationError(tr("Nessuna pagina corrisponde all’intervallo e al filtro scelti."))
    return pages


def copy_sequence(pages, copies=1, collate=True, duplex=False):
    if not duplex:
        return pages * copies if collate else [page for page in pages for _ in range(copies)]
    # Duplicate sheets, not individual sides. Odd documents need a blank back
    # before the next copy; otherwise two copies share the same sheet.
    paired = list(pages) + ([None] if len(pages) % 2 else [])
    if collate:
        result = paired * copies
    else:
        result = [page for index in range(0, len(paired), 2) for _ in range(copies)
                  for page in paired[index:index + 2]]
    if result and result[-1] is None:
        result.pop()  # The last sheet naturally has an empty back.
    return result


@dataclass
class PrintGeometry:
    paper: QRectF
    printable: QRectF
    clip: QRectF
    source: QRectF
    transform: QTransform
    scale: float
    rotated: bool


def print_geometry(size, paper, printable, options, source=None):
    source = QRectF(source) if source is not None else QRectF(0, 0, size.width(), size.height())
    source = source.intersected(QRectF(0, 0, size.width(), size.height()))
    area = QRectF(paper if options.ignore_margins else printable)
    if source.isEmpty() or area.isEmpty():
        raise PDFOperationError(tr("L’area da stampare è vuota. Verifica formato e margini."))
    width, height = source.width(), source.height()
    straight = min(area.width() / width, area.height() / height)
    turned = min(area.width() / height, area.height() / width)
    rotated = options.rotate and turned > straight + 1e-8
    if rotated:
        width, height = height, width
    fit = min(area.width() / width, area.height() / height)
    scale = {"fit": fit, "shrink": min(1., fit), "actual": 1., "custom": options.scale}[options.mode]
    x, y = area.left(), area.top()
    if options.center:
        x += (area.width() - width * scale) / 2
        y += (area.height() - height * scale) / 2
    transform = QTransform()
    transform.translate(x, y)
    transform.scale(scale, scale)
    if rotated:
        transform.rotate(90)
        transform.translate(0, -source.height())
    transform.translate(-source.left(), -source.top())
    return PrintGeometry(QRectF(paper), QRectF(printable), area, source, transform, scale, rotated)


def printer_geometry(printer, size, options, source=None):
    layout = printer.pageLayout()
    paper = QRectF(layout.fullRect(QPageLayout.Unit.Point))
    margins = layout.margins(QPageLayout.Unit.Point)
    printable = paper.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
    return print_geometry(size, paper, printable, options, source)


def raster_plan(page, size, geometry, dpi):
    """Rasterize only content intersecting the sheet, one 1024px tile at a time."""
    ratio = max(.01, geometry.scale * dpi / 72)
    inverse, ok = geometry.transform.inverted()
    if not ok:
        raise PDFOperationError(tr("Scala di stampa non valida."))
    visible = inverse.mapRect(geometry.clip).intersected(geometry.source)
    full = QRectF(0, 0, size.width() * ratio, size.height() * ratio)
    area = QRectF(visible.x() * ratio, visible.y() * ratio,
                  visible.width() * ratio, visible.height() * ratio)
    return visible_tiles(page, full, area, 1), QRectF(0, 0, size.width(), size.height())


def paint_tile(painter, document, tile, source_rect, geometry):
    image = document.render(tile.page, QSize(tile.width, tile.height), tile.render_options())
    if image.isNull():
        raise PDFOperationError(tr('Impossibile renderizzare la pagina {0}.', tile.page + 1))
    painter.save()
    painter.setClipRect(geometry.clip)
    painter.setWorldTransform(geometry.transform, True)
    painter.setClipRect(geometry.source, Qt.ClipOperation.IntersectClip)
    painter.drawImage(tile.target_rect(source_rect), image)
    painter.restore()


from ..core.provenance import propagate_zone


class PrintJob(QObject):
    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, document, path, printer, pages, options, parent=None, *,
                 output=None, source_rect=None):
        super().__init__(parent)
        self.document, self.path, self.printer = document, path, printer
        self.pages, self.options, self.source_rect = list(pages), options, source_rect
        self.output = Path(output) if output else None
        self._temporary = None
        self._painter = None
        self._position = 0
        self._tiles = None
        self._geometry = None
        self._page_open = False
        self.active = False
        self.vectors = VectorPages(self, printing=True)
        self.vectors.changed.connect(self._schedule)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)

    def start(self):
        if self.active:
            return
        try:
            if not self.pages:
                raise PDFOperationError(tr("Seleziona almeno una pagina da stampare."))
            if any(page is not None and (page < 0 or page >= self.document.pageCount()) for page in self.pages):
                raise PDFOperationError(tr("Intervallo di stampa non valido."))
            if not self.printer.isValid():
                raise PDFOperationError(tr("La stampante non è disponibile."))
            if self.output and self.output.resolve() == Path(self.path).resolve():
                raise PDFOperationError(tr("La copia di stampa richiede un nome diverso dal documento sorgente."))
            self.printer.setFullPage(True)  # Origin at physical sheet, margins handled explicitly.
            self.printer.setCopyCount(1)  # Same deterministic copies/order on every driver.
            if self.output:
                handle, name = tempfile.mkstemp(prefix=".apex_print_", suffix=".pdf", dir=self.output.parent)
                os.close(handle)
                self._temporary = Path(name)
                self.printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                self.printer.setOutputFileName(name)
            self._painter = QPainter()
            if not self._painter.begin(self.printer):
                raise PDFOperationError(tr("La stampante non ha accettato il lavoro. Verifica connessione e impostazioni."))
            self._painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._painter.setPen(Qt.PenStyle.NoPen)
            self.active = True
            self.vectors.reset(self.path)
            self.progress.emit(0, len(self.pages))
            self._schedule()
        except Exception as exc:
            self._stop()
            self.failed.emit(str(exc))

    def _schedule(self):
        if self.active and not self._timer.isActive():
            self._timer.start(0)

    def _step(self):
        if not self.active:
            return
        try:
            if self._position == len(self.pages):
                painter, self._painter = self._painter, None
                if not painter.end():
                    raise PDFOperationError(tr("Il driver ha segnalato un errore al termine della stampa."))
                if self.printer.printerState() == QPrinter.PrinterState.Error:
                    raise PDFOperationError(tr("Il driver ha segnalato un errore di stampa."))
                if self.output:
                    if not self._temporary.is_file() or self._temporary.stat().st_size == 0:
                        raise PDFOperationError(tr("La stampa su PDF non ha prodotto un file valido."))
                    propagate_zone(self._temporary, self.path, self.output if self.output.exists() else None,
                                   zone=getattr(getattr(self.document,'editor',None),'_zone',None))
                    os.replace(self._temporary, self.output)
                    self._temporary = None
                self._stop()
                self.finished.emit()
                return
            page = self.pages[self._position]
            if page is None:
                if self._position and not self.printer.newPage():
                    raise PDFOperationError(tr("Il driver non ha accettato la facciata vuota."))
                self._position += 1
                self.progress.emit(self._position, len(self.pages))
                self._schedule()
                return
            if self._geometry is None:
                size = self.document.pagePointSize(page)
                self._geometry = printer_geometry(self.printer, size, self.options, self.source_rect)
                if not self.options.images:
                    self.vectors.request([page])
            if not self.options.images and page not in self.vectors.cache and page not in self.vectors.failed:
                return  # Worker completion resumes us; the GUI remains responsive.
            if self._tiles is None:
                if self._position and not self.printer.newPage():
                    raise PDFOperationError(tr("Il driver non ha accettato il foglio successivo."))
                self._painter.save()
                self._page_open = True
                self._painter.scale(self.printer.resolution() / 72, self.printer.resolution() / 72)
                if not self.options.images and page in self.vectors.cache:
                    self._painter.setClipRect(self._geometry.clip.intersected(
                        self._geometry.transform.mapRect(self._geometry.source)))
                    self.vectors.cache[page].paint(self._painter, self._geometry.transform, self._geometry.clip)
                    self._page_done()
                    return
                tiles, self._tile_source = raster_plan(page, self.document.pagePointSize(page),
                                                       self._geometry, self.options.dpi)
                self._tiles = iter(tiles)
            tile = next(self._tiles, None)
            if tile is None:
                self._page_done()
            else:
                paint_tile(self._painter, self.document, tile, self._tile_source, self._geometry)
                self._schedule()
        except Exception as exc:
            self._stop(abort=True)
            self.failed.emit(str(exc))

    def _page_done(self):
        self._painter.restore()
        self._page_open = False
        self._position += 1
        self._tiles = self._geometry = None
        self.progress.emit(self._position, len(self.pages))
        self._schedule()

    def cancel(self):
        if self.active:
            self._stop(abort=True)
            self.cancelled.emit()

    def _stop(self, abort=False):
        self.active = False
        self._timer.stop()
        self.vectors.reset()
        if self._painter:
            if abort:
                self.printer.abort()
            if self._painter.isActive():
                if self._page_open:
                    self._painter.restore()
                    self._page_open = False
                self._painter.end()
            self._painter = None
        if self._temporary:
            self._temporary.unlink(missing_ok=True)
            self._temporary = None
