"""PDF UI models backed by an isolated worker; the GUI accepts raw pixels/data only."""
from __future__ import annotations

import math
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (QAbstractListModel,QMargins,QModelIndex,QObject,QPointF,
    QRunnable,QSize,QSizeF,Qt,QThreadPool,Signal,QTimer)
from PySide6.QtGui import QImage,QPolygonF,QStandardItem,QStandardItemModel
from PySide6.QtPdf import QPdfDocument,QPdfBookmarkModel,QPdfPageRenderer
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QAbstractScrollArea,QApplication

from ..core.errors import PDFOperationError


def number(value,bound=1e7):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>bound:
        raise ValueError('Invalid worker geometry')
    return value


class Selection:
    def __init__(self,value):
        if not isinstance(value,dict) or set(value)!={'text','start','end','bounds'}:
            raise ValueError('Invalid selection response')
        if not isinstance(value['text'],str) or len(value['text'])>250000 or any(type(value[k])is not int or not 0<=value[k]<=500000 for k in ('start','end')):
            raise ValueError('Invalid selection text')
        bounds=value['bounds']
        if not isinstance(bounds,list) or len(bounds)>10000:
            raise ValueError('Invalid selection bounds')
        self._bounds=[]
        for polygon in bounds:
            if not isinstance(polygon,list) or not 1<=len(polygon)<=8:
                raise ValueError('Invalid selection polygon')
            points=[]
            for pair in polygon:
                if not isinstance(pair,list) or len(pair)!=2:raise ValueError('Invalid selection point')
                points.append(QPointF(*map(number,pair)))
            self._bounds.append(QPolygonF(points))
        self.value=value
    def text(self):return self.value['text']
    def startIndex(self):return self.value['start']
    def endIndex(self):return self.value['end']
    def bounds(self):return self._bounds
    def isValid(self):return bool(self._bounds)
    def boundingRectangle(self):
        from PySide6.QtCore import QRectF
        rect=QRectF()
        for polygon in self._bounds:rect=rect.united(polygon.boundingRect())
        return rect
    def copyToClipboard(self):QApplication.clipboard().setText(self.text())


class RemoteDocument(QObject):
    Status=QPdfDocument.Status
    statusChanged=Signal(object)
    loaded=Signal()
    def __init__(self,editor,parent=None):
        super().__init__(parent)
        self.editor=editor
        self._status=self.Status.Null
        self._password=''
        self.source=None
        self.sizes=[]
        self.bookmarks=[]
    def setPassword(self,value):self._password=value
    def password(self):return self._password
    def status(self):return self._status
    def pageCount(self):return len(self.sizes)
    def pagePointSize(self,page):return QSizeF(*self.sizes[page]) if 0<=page<len(self.sizes) else QSizeF()
    def load(self,path):
        self.source=self.editor.checked_path(Path(path))
        self.setProperty('origin_path',str(self.source))
        self._status=self.Status.Loading
        self.statusChanged.emit(self._status)
        try:
            info=self.editor.call('qt_info',self.source)
            sizes=info['sizes']
            if not isinstance(sizes,list) or not 1<=len(sizes)<=3000:
                raise ValueError('Invalid page table')
            self.sizes=[]
            for pair in sizes:
                if not isinstance(pair,(list,tuple)) or len(pair)!=2 or any(not 0<number(v)<=14400 for v in pair):
                    raise ValueError('Invalid page size')
                self.sizes.append(tuple(pair))
            self.bookmarks=info['bookmarks']
            self._status=self.Status.Ready
            self.loaded.emit()
        except Exception:
            self._status=self.Status.Error
            self.statusChanged.emit(self._status)
            raise
        self.statusChanged.emit(self._status)
    def close(self):
        self.source=None;self.sizes=[];self.bookmarks=[]
        self._status=self.Status.Null
        self.statusChanged.emit(self._status)
    def render(self,page,size,options=None):
        width,height=size.width(),size.height()
        if not 0<=page<self.pageCount() or not 0<width<=40000 or not 0<height<=40000 or width*height>40000000:
            raise PDFOperationError('Richiesta immagine fuori limite.')
        description={}
        if options:
            scaled=options.scaledSize();clip=options.scaledClipRect()
            if scaled.isValid():description['scaled']=[scaled.width(),scaled.height()]
            if clip.isValid():description['clip']=[clip.x(),clip.y(),clip.width(),clip.height()]
        output=self.editor._root/(uuid4().hex+'.rgba')
        try:
            self.editor.call('qt_render',self.source,page,width,height,description,output)
            self.editor.checked_path(output)
            if output.stat().st_size!=width*height*4:
                raise ValueError('Invalid raw raster length')
            from ..core.artifacts import read_artifact
            data=read_artifact(output,self.editor._root,width*height*4)
            if len(data)!=width*height*4:raise ValueError('Invalid raw raster')
            return QImage(data,width,height,width*4,QImage.Format.Format_RGBA8888).copy()
        finally:output.unlink(missing_ok=True)
    def getAllText(self,page):return Selection(self.editor.call('qt_all_text',self.source,page))
    def getSelection(self,page,start,end):return Selection(self.editor.call('qt_selection',self.source,page,(start.x(),start.y()),(end.x(),end.y())))
    def getSelectionAtIndex(self,page,start,length):return Selection(self.editor.call('qt_selection_index',self.source,page,start,length))


class Task(QRunnable):
    def __init__(self,callback):super().__init__();self.callback=callback
    def run(self):self.callback()


class RemoteRenderer(QObject):
    RenderMode=QPdfPageRenderer.RenderMode
    pageRendered=Signal(int,QSize,QImage,object,int)
    def __init__(self,parent=None):
        super().__init__(parent);self.document=None;self.sequence=0;self.epoch=0
    def setRenderMode(self,*_):pass
    def setDocument(self,document):self.document=document;self.epoch+=1
    def requestPage(self,page,size,options=None):
        if self.document is None or self.document.status()!=QPdfDocument.Status.Ready:return 0
        self.sequence+=1
        request=self.sequence;document=self.document;epoch=self.epoch
        def run():
            try:
                image=document.render(page,size,options)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Isolated rendering failed')
                image=QImage()
            try:
                if epoch==self.epoch:self.pageRendered.emit(page,size,image,options,request)
            except RuntimeError:pass
        QThreadPool.globalInstance().start(Task(run))
        return request


class Link:
    def __init__(self,value,pages):
        from PySide6.QtCore import QRectF
        if not isinstance(value,dict) or type(value.get('page'))is not int or not 0<=value['page']<pages:
            raise ValueError('Invalid search result')
        self.value=value
        self._rectangles=[]
        rectangles=value.get('rectangles')
        if not isinstance(rectangles,list) or len(rectangles)>10000:raise ValueError('Invalid search geometry')
        for rect in rectangles:
            if not isinstance(rect,(tuple,list)) or len(rect)!=4:raise ValueError('Invalid search rectangle')
            self._rectangles.append(QRectF(*map(number,rect)))
        if any(not isinstance(value.get(k),str) or len(value[k])>4096 for k in ('before','after')):raise ValueError('Invalid search context')
    def page(self):return self.value['page']
    def rectangles(self):return self._rectangles
    def contextBefore(self):return self.value['before']
    def contextAfter(self):return self.value['after']


class RemoteSearch(QAbstractListModel):
    countChanged=Signal(int)
    completed=Signal(int,object)
    def __init__(self,parent=None):
        super().__init__(parent);self.document=None;self.query='';self.results=[];self.epoch=0
        self.completed.connect(self._complete)
    def setDocument(self,document):
        self.document=document
        self.setSearchString('')
    def searchString(self):return self.query
    def setSearchString(self,query):
        self.epoch+=1;epoch=self.epoch;self.query=query
        self.beginResetModel();self.results=[];self.endResetModel();self.countChanged.emit(0)
        if not query or self.document is None or self.document.source is None:return
        document=self.document;source=document.source
        def run():
            try:value=document.editor.call('qt_search',source,query)
            except Exception:value={'results':[],'truncated':True}
            try:self.completed.emit(epoch,value)
            except RuntimeError:pass
        QThreadPool.globalInstance().start(Task(run))
    def _complete(self,epoch,value):
        if epoch!=self.epoch or self.document is None:return
        try:
            rows=value['results']
            if not isinstance(rows,list) or len(rows)>5000:raise ValueError('Search response too large')
            results=[Link(row,self.document.pageCount()) for row in rows]
        except Exception:results=[]
        self.beginResetModel();self.results=results;self.endResetModel();self.countChanged.emit(len(results))
        if value.get('truncated') and hasattr(self.parent(),'message'):
            self.parent().message.emit('Ricerca limitata dal budget di sicurezza. Restringi il testo cercato.')
    def count(self):return len(self.results)
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.results)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.DisplayRole and 0<=index.row()<len(self.results):
            link=self.results[index.row()];return link.contextBefore()+self.query+link.contextAfter()
    def resultAtIndex(self,index):return self.results[index]
    def resultsOnPage(self,page):return [r for r in self.results if r.page()==page]


class RemoteBookmarks(QStandardItemModel):
    Role=QPdfBookmarkModel.Role
    def __init__(self,parent=None):super().__init__(parent);self.document=None
    def setDocument(self,document):
        if self.document:
            self.document.loaded.disconnect(self.refresh)
        self.document=document
        if document:document.loaded.connect(self.refresh)
        self.refresh()
    def refresh(self):
        self.clear()
        if self.document is None:return
        count=[0]
        def add(rows,parent,depth=0):
            if not isinstance(rows,list) or depth>64 or len(rows)>10000:raise ValueError('Invalid bookmarks')
            for row in rows:
                count[0]+=1
                if count[0]>10000 or not isinstance(row,dict) or not isinstance(row.get('title'),str) or len(row['title'])>4096:raise ValueError('Invalid bookmark')
                page=row.get('page');position=row.get('location')
                if (page is not None and (type(page)is not int or not 0<=page<self.document.pageCount())) or not isinstance(position,(tuple,list)) or len(position)!=2:raise ValueError('Invalid bookmark destination')
                item=QStandardItem(row['title']);item.setEditable(False)
                item.setData(page,int(self.Role.Page));item.setData(QPointF(*map(number,position)),int(self.Role.Location))
                parent.appendRow(item);add(row.get('children',[]),item,depth+1)
        try:add(self.document.bookmarks,self.invisibleRootItem())
        except Exception:self.clear()


class Navigator(QObject):
    currentPageChanged=Signal(int)
    def __init__(self,view):super().__init__(view);self.view=view;self.page=0
    def currentPage(self):return self.page
    def jump(self,page,location=QPointF(),zoom=0):
        view=self.view
        if view.document() is None or not 0<=page<view.document().pageCount():return
        changed=page!=self.page;self.page=page
        if zoom>0 and view.zoomMode()==view.ZoomMode.Custom:view.setZoomFactor(zoom)
        view._layout_pages()
        rect=view.page_rects().get(page)
        if rect:
            points=view.document().pagePointSize(page)
            view.verticalScrollBar().setValue(round(rect.top()+location.y()*rect.height()/max(1,points.height())))
        if changed:self.currentPageChanged.emit(page)
        view.viewport().update()


class RemotePdfView(QAbstractScrollArea):
    """Scroll/zoom layout with the same public surface used by the existing UI."""
    PageMode=QPdfView.PageMode
    ZoomMode=QPdfView.ZoomMode
    documentChanged=Signal(object)
    zoomFactorChanged=Signal(float)
    zoomModeChanged=Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent)
        self._document=None;self._search=None;self._factor=1.;self._zoom=self.ZoomMode.FitInView
        self._mode=self.PageMode.MultiPage;self._margins=QMargins(32,24,32,24);self._spacing=24
        self._navigator=Navigator(self);self._search_index=-1;self._laying_out=False
        # Scroll ranges are pixels: the default step of 1 makes each wheel
        # notch move only a few pixels. Let Qt apply the system's line count.
        self.horizontalScrollBar().setSingleStep(24)
        self.verticalScrollBar().setSingleStep(24)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
    def document(self):return self._document
    def setDocument(self,document):
        if self._document:
            self._document.statusChanged.disconnect(self._document_ready)
        self._document=document
        if document:document.statusChanged.connect(self._document_ready)
        self.documentChanged.emit(document);self._layout_pages()
    def _document_ready(self,*_):self._layout_pages()
    def pageNavigator(self):return self._navigator
    def setSearchModel(self,model):self._search=model
    def searchModel(self):return self._search
    def setCurrentSearchResultIndex(self,index):self._search_index=index
    def currentSearchResultIndex(self):return self._search_index
    def setPageMode(self,mode):self._mode=mode;self._layout_pages()
    def pageMode(self):return self._mode
    def setZoomMode(self,mode):
        changed=mode!=self._zoom;self._zoom=mode;self._layout_pages()
        if changed:self.zoomModeChanged.emit(mode)
    def zoomMode(self):return self._zoom
    def setZoomFactor(self,factor):
        self._factor=factor;self._layout_pages();self.zoomFactorChanged.emit(factor)
    def zoomFactor(self):return self._factor
    def setDocumentMargins(self,margins):self._margins=margins;self._layout_pages()
    def documentMargins(self):return self._margins
    def setPageSpacing(self,spacing):self._spacing=spacing;self._layout_pages()
    def pageSpacing(self):return self._spacing
    def resizeEvent(self,event):super().resizeEvent(event);self._layout_pages()
    def _layout_pages(self):
        if self._laying_out or not hasattr(self,'_geometry_key'):return
        self._laying_out=True
        try:
            self._geometry_key=None
            rects=self.page_rects()
            width=max((r.right() for r in rects.values()),default=0)+self._margins.right()
            height=max((r.bottom() for r in rects.values()),default=0)+self._margins.bottom()
            self.horizontalScrollBar().setRange(0,max(0,math.ceil(width-self.viewport().width())))
            self.verticalScrollBar().setRange(0,max(0,math.ceil(height-self.viewport().height())))
            self.horizontalScrollBar().setPageStep(self.viewport().width())
            self.verticalScrollBar().setPageStep(self.viewport().height())
            self.viewport().update()
        finally:self._laying_out=False
    def _scrolled(self):
        if self._laying_out or not self.document() or self._mode!=self.PageMode.MultiPage:return
        position=self.verticalScrollBar().value()+min(40,self.viewport().height()/4)
        page=next((p for p,r in self.page_rects().items() if r.top()<=position<=r.bottom()),None)
        if page is not None and page!=self._navigator.page:
            self._navigator.page=page;self._navigator.currentPageChanged.emit(page)
        self.viewport().update()


class RemotePrintSource:
    def __init__(self,editor):self.editor=editor
    def path(self,comments=True):return self.editor.checked_path(self.editor.call('print_source',bool(comments)))
    def close(self):pass
