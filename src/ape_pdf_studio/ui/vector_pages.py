"""Asynchronous vector display lists, independent of zoom and screen resolution."""
from __future__ import annotations
from ..core.localization import tr

from collections import OrderedDict
import json
from pathlib import Path
import sys
import tempfile
from ..core.private_work import PrivateDirectory

from PySide6.QtCore import QByteArray, QObject, QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen, QTransform

from ..core.vector_page import MAX_SVG_BYTES
from ..core.security import copy_checked
from ..core.worker_process import worker_command
from ..core.sandbox import wrap_command


def painter_clip(description):
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill if description["rule"] == "evenodd" else Qt.FillRule.WindingFill)
    for command, coordinates in description["commands"]:
        if command == "M":
            path.moveTo(*coordinates)
        elif command == "L":
            path.lineTo(*coordinates)
        elif command == "H":
            path.lineTo(coordinates[0], path.currentPosition().y())
        elif command == "V":
            path.lineTo(path.currentPosition().x(), coordinates[0])
        elif command == "C":
            path.cubicTo(*coordinates)
        else:
            path.closeSubpath()
    for values in reversed(description["transforms"]):
        path = QTransform(*values).map(path)
    return path


class VectorPage:
    def __init__(self, data, layers=None):
        from ..core.vector_protocol import validate
        value=validate(json.loads(data))
        from PySide6.QtCore import QSizeF
        self.size=QSizeF(*value['size'])
        self.layers=[]
        for layer in value['layers']:
            draws=[]
            for draw in layer['draws']:
                path=painter_clip(dict(commands=draw['commands'],rule=draw['rule'],transforms=[]))
                transform=QTransform()
                for matrix in reversed(draw['transforms']):
                    transform=transform*QTransform(*matrix)
                fill=QColor(draw['fill']) if draw['fill']!='none' else None
                stroke=QColor(draw['stroke']) if draw['stroke']!='none' else None
                if fill:fill.setAlphaF(fill.alphaF()*draw['opacity']*draw['fill_opacity'])
                pen=QPen(Qt.PenStyle.NoPen)
                if stroke and draw['width']>0:
                    stroke.setAlphaF(stroke.alphaF()*draw['opacity']*draw['stroke_opacity'])
                    pen=QPen(stroke,draw['width'])
                    pen.setCapStyle({'butt':Qt.PenCapStyle.FlatCap,'round':Qt.PenCapStyle.RoundCap,'square':Qt.PenCapStyle.SquareCap}[draw['cap']])
                    pen.setJoinStyle({'miter':Qt.PenJoinStyle.MiterJoin,'round':Qt.PenJoinStyle.RoundJoin,'bevel':Qt.PenJoinStyle.BevelJoin}[draw['join']])
                    pen.setMiterLimit(draw['miter'])
                    if draw['dash']:
                        pen.setDashPattern([x/draw['width'] for x in draw['dash']])
                        pen.setDashOffset(draw['offset']/draw['width'])
                draws.append((path,transform,fill,pen))
            self.layers.append((draws,None,[painter_clip(clip) for clip in layer['clips']]))

    def paint(self,painter,transform,viewport):
        painter.save()
        painter.setClipRect(viewport,Qt.ClipOperation.IntersectClip)
        painter.setWorldTransform(transform,True)
        for draws,_bounds,clips in self.layers:
            painter.save()
            for clip in clips:painter.setClipPath(clip,Qt.ClipOperation.IntersectClip)
            for path,local,fill,pen in draws:
                painter.save()
                painter.setWorldTransform(local,True)
                painter.setBrush(fill if fill else Qt.BrushStyle.NoBrush)
                painter.setPen(pen)
                painter.drawPath(path)
                painter.restore()
            painter.restore()
        painter.restore()


class VectorPages(QObject):
    changed = Signal()
    fallback = Signal(int)

    def __init__(self, parent=None, *, printing=False):
        super().__init__(parent)
        self.printing = printing
        self.cache = OrderedDict()
        self.failed = set()
        self.enabled = True
        self.source = None
        self.password = None
        self.wanted = []
        self.process = None
        self._work = None
        self._epoch = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)

    def reset(self, source=None, password=None):
        self._epoch += 1
        self._timer.stop()
        work, self._work = self._work, None
        if self.process:
            process, self.process = self.process, None
            process.kill()
            process.waitForFinished(1000)
            if process.state() != QProcess.ProcessState.NotRunning:
                # A delayed Windows termination must finish before removing output.
                process.finished.connect(lambda *_args, task=work: self._dispose_work(task))
                process.finished.connect(process.deleteLater)
                work = None
            else:
                process.deleteLater()
        self._dispose_work(work)
        self.cache.clear()
        self.failed.clear()
        self.wanted = []
        self.source = source
        self.password = password

    def _dispose_work(self, work, attempt=0):
        if work is None:
            return
        try:
            work.cleanup()
        except PermissionError:
            # Windows scanners can briefly retain a newly written SVG after the
            # worker exits. Retry without blocking the GUI or breaking tab close.
            if attempt < 20:
                QTimer.singleShot(100, lambda: self._dispose_work(work, attempt + 1))
            else:
                raise

    def request(self, pages):
        # Three visible display lists bound native SVG tree growth. Other pages
        # remain available through PDFium, including unusually tall viewports.
        self.wanted = list(pages)[:3] if self.enabled and self.source else []
        self._pump()

    def _pump(self):
        if self.process or not self.enabled or not self.source:
            return
        page = next((p for p in self.wanted if p not in self.cache and p not in self.failed), None)
        if page is None:
            return
        self._work = PrivateDirectory(prefix="apex_vector_")
        folder = Path(self._work.name)
        process = QProcess(self)
        self.process = process
        epoch = self._epoch
        process.finished.connect(lambda code, status: self._complete(process, epoch, page, code, status))
        process.errorOccurred.connect(lambda error: self._complete(process, epoch, page, -1, QProcess.ExitStatus.CrashExit)
                                     if error == QProcess.ProcessError.FailedToStart else None)
        # Never capture unlimited native diagnostics in the GUI process.
        process.setStandardErrorFile(QProcess.nullDevice())
        process.setStandardOutputFile(QProcess.nullDevice())
        snapshot=folder/"source.pdf"
        try:
            copy_checked(Path(self.source),snapshot)
            command = wrap_command(worker_command("vector"), folder)
        except Exception:
            self._complete(process,epoch,page,-1,QProcess.ExitStatus.CrashExit)
            return
        process.start(command[0], command[1:])
        # Passwords travel through the private pipe, never command arguments.
        process.write(json.dumps({"path": str(snapshot), "password": self.password, "page": page,
                                  "printing": self.printing,
                                  "output": str(folder / "page.svg"), "layers": str(folder / "layers.json")}).encode())
        process.closeWriteChannel()
        self._timer.start(12000)

    def _timeout(self):
        if self.process:
            self.process.kill()

    def _complete(self, process, epoch, page, code, status):
        if epoch != self._epoch or process is not self.process:
            return
        self._timer.stop()
        try:
            folder = Path(self._work.name)
            svg, manifest = folder / "page.svg", folder / "layers.json"
            if code != 0 or status != QProcess.ExitStatus.NormalExit:
                raise ValueError(tr("Conversione interrotta"))
            if svg.stat().st_size > MAX_SVG_BYTES or manifest.stat().st_size > MAX_SVG_BYTES:
                raise ValueError(tr("Display list fuori limite"))
            from ..core.artifacts import read_artifact
            result = VectorPage(read_artifact(svg,folder,MAX_SVG_BYTES))
            self.cache[page] = result
            while len(self.cache) > 3:
                discard = next((p for p in self.cache if p not in self.wanted), next(iter(self.cache)))
                self.cache.pop(discard)
        except Exception:
            self.failed.add(page)
            self.fallback.emit(page)
        finally:
            work, self._work = self._work, None
            self._dispose_work(work)
            self.process = None
            process.deleteLater()
        self.changed.emit()
        self._pump()
