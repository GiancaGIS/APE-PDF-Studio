"""Qt PDF rendering/search inside the document worker, returning raw pixels/data."""
from collections import OrderedDict
import bisect
import math
from pathlib import Path
import time

MAX_TEXT = 250000


class Documents:
    def __init__(self,editor,root):
        from PySide6.QtGui import QGuiApplication
        self.app=QGuiApplication.instance() or QGuiApplication([])
        self.editor,self.root=editor,root
        self.cache=OrderedDict()
        self.prints=None
        self.print_revision=None

    def document(self,path,hide_ink=False):
        from .rpc_worker import checked_path
        from .form_appearance import display_copy
        from PySide6.QtCore import QBuffer,QByteArray,QIODevice
        from PySide6.QtPdf import QPdfDocument
        path=checked_path(path,self.root)
        key=(path,hide_ink)
        if key not in self.cache:
            while len(self.cache)>=2:
                _,(old,buffer)=self.cache.popitem(last=False)
                old.close();buffer.close()
            from pypdf import PdfReader
            from .page_coordinates import displayed_size
            with PdfReader(path) as reader:
                if reader.is_encrypted and not reader.decrypt(self.editor._password or ''):
                    raise ValueError('PDF authentication failed')
                sizes=[displayed_size(page) for page in reader.pages]
            if any(not math.isfinite(v) or not 0<v<=1e7 for size in sizes for v in size):
                raise ValueError('Invalid physical page size')
            data=path.read_bytes()
            data=display_copy(data,self.editor._password,omit_editable_ink=hide_ink)
            buffer=QBuffer()
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document=QPdfDocument()
            document.setPassword(self.editor._password or '')
            document.load(buffer)
            deadline=time.monotonic()+10
            while document.status()==QPdfDocument.Status.Loading and time.monotonic()<deadline:
                self.app.processEvents()
            if document.status()!=QPdfDocument.Status.Ready:
                raise ValueError('Cannot load the PDF inside the protected renderer')
            if document.pageCount()!=len(sizes):raise ValueError('Inconsistent page table')
            document._ape_sizes=sizes
            document._ape_scales=[(w/document.pagePointSize(i).width(),h/document.pagePointSize(i).height())
                                  for i,(w,h) in enumerate(sizes)]
            self.cache[key]=(document,buffer)
        self.cache.move_to_end(key)
        return self.cache[key][0]

    def text(self,document,page):
        value=document.getAllText(page).text()
        if len(value)>MAX_TEXT:
            raise ValueError('Page text exceeds the protected text budget')
        return value

    def offsets(self,text):
        result=[0]
        for char in text:
            result.append(result[-1]+(2 if ord(char)>0xffff else 1))
        return result

    def selection(self,selection,text,scale=(1,1)):
        offsets=self.offsets(text)
        bounds=[[[p.x()*scale[0],p.y()*scale[1]] for p in polygon] for polygon in selection.bounds()]
        if len(bounds)>10000:
            raise ValueError('Selection geometry exceeds budget')
        return dict(text=selection.text(),start=bisect.bisect_left(offsets,selection.startIndex()),
                    end=bisect.bisect_left(offsets,selection.endIndex()),bounds=bounds)

    def call(self,method,args):
        from PySide6.QtCore import QPointF,QRect,QSize
        from PySide6.QtGui import QImage
        from PySide6.QtPdf import QPdfDocumentRenderOptions,QPdfBookmarkModel
        from .rpc_worker import checked_path
        if method=='print_source':
            from .print_source import PrintSource
            if self.prints is None or self.print_revision!=self.editor.path:
                if self.prints:self.prints.close()
                self.prints=PrintSource(self.editor.path,self.editor._password)
                self.print_revision=self.editor.path
            return self.prints.path(args[0])
        if not isinstance(args,list) or not args:
            raise ValueError('Invalid protected renderer request')
        hide_ink=False
        if method=='qt_render':
            if len(args)!=6 or not isinstance(args[4],dict) or type(args[4].get('hide_ink',False))is not bool:
                raise ValueError('Invalid protected raster options')
            hide_ink=args[4].get('hide_ink',False)
        document=self.document(args[0],hide_ink)
        if method=='qt_info':
            sizes=document._ape_sizes
            model=QPdfBookmarkModel();model.setDocument(document)
            def children(parent,depth=0,budget=None):
                budget=[0] if budget is None else budget
                if depth>64:raise ValueError('Bookmark depth exceeds budget')
                result=[]
                for row in range(model.rowCount(parent)):
                    budget[0]+=1
                    if budget[0]>10000:raise ValueError('Too many bookmarks')
                    index=model.index(row,0,parent)
                    location=index.data(int(QPdfBookmarkModel.Role.Location))
                    page=index.data(int(QPdfBookmarkModel.Role.Page))
                    scale=document._ape_scales[page] if type(page)is int and 0<=page<len(sizes) else (1,1)
                    result.append(dict(title=str(index.data() or '')[:4096],page=page,
                        location=(location.x()*scale[0],location.y()*scale[1]) if location is not None else (0,0),children=children(index,depth+1,budget)))
                return result
            from PySide6.QtCore import QModelIndex
            return dict(sizes=sizes,bookmarks=children(QModelIndex()))
        if method=='qt_search':
            query=args[1]
            if not isinstance(query,str) or len(query)>4096:
                raise ValueError('Invalid search query')
            result=[]; deadline=time.monotonic()+10
            if not query:return dict(results=[],truncated=False)
            for page in range(document.pageCount()):
                text=self.text(document,page)
                folded=[]; positions=[]
                for index,char in enumerate(text):
                    chars=char.casefold();folded.append(chars);positions.extend([index]*len(chars))
                haystack=''.join(folded);needle=query.casefold();offset=0
                utf16=self.offsets(text)
                while (hit:=haystack.find(needle,offset))>=0:
                    start,end=positions[hit],positions[hit+len(needle)-1]+1
                    selection=document.getSelectionAtIndex(page,utf16[start],utf16[end]-utf16[start])
                    sx,sy=document._ape_scales[page]
                    rects=[(p.boundingRect().x()*sx,p.boundingRect().y()*sy,p.boundingRect().width()*sx,p.boundingRect().height()*sy) for p in selection.bounds()]
                    result.append(dict(page=page,rectangles=rects,before=text[max(0,start-50):start],after=text[end:end+80]))
                    offset=hit+len(needle)
                    if len(result)>=5000 or time.monotonic()>deadline:return dict(results=result,truncated=True)
                if time.monotonic()>deadline:return dict(results=result,truncated=True)
            return dict(results=result,truncated=False)
        page=args[1]
        if type(page)is not int or not 0<=page<document.pageCount():
            raise ValueError('Invalid page')
        if method=='qt_render':
            width,height,description,output=args[2:]
            if (type(width)is not int or type(height)is not int or not 0<width<=40000 or not 0<height<=40000 or width*height>40000000):
                raise ValueError('Raster budget exceeded')
            options=QPdfDocumentRenderOptions()
            options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
            if description:
                scaled=description.get('scaled')
                clip=description.get('clip')
                if scaled:
                    if len(scaled)!=2 or any(type(x)is not int or not 0<x<=4000000 for x in scaled):raise ValueError('Invalid raster scale')
                    options.setScaledSize(QSize(*scaled))
                if clip:
                    if len(clip)!=4 or any(type(x)is not int or not 0<=x<=4000000 for x in clip):raise ValueError('Invalid raster clip')
                    options.setScaledClipRect(QRect(*clip))
            image=document.render(page,QSize(width,height),options)
            if image.isNull():raise ValueError('Protected renderer returned no image')
            image=image.convertToFormat(QImage.Format.Format_RGBA8888)
            if image.width()!=width or image.height()!=height or image.sizeInBytes()!=width*height*4:
                raise ValueError('Unexpected raster format')
            output=checked_path(output,self.root)
            with output.open('wb') as stream:stream.write(image.constBits())
            return None
        text=self.text(document,page)
        sx,sy=document._ape_scales[page]
        if method=='qt_all_text':
            return self.selection(document.getAllText(page),text,(sx,sy))
        if method=='qt_selection':
            return self.selection(document.getSelection(page,QPointF(args[2][0]/sx,args[2][1]/sy),
                QPointF(args[3][0]/sx,args[3][1]/sy)),text,(sx,sy))
        if method=='qt_selection_index':
            start,length=args[2:]
            if type(start)is not int or type(length)is not int or not 0<=start<=start+length<=len(text):raise ValueError('Invalid text range')
            offsets=self.offsets(text)
            return self.selection(document.getSelectionAtIndex(page,offsets[start],offsets[start+length]-offsets[start]),text,(sx,sy))
        raise ValueError('Unknown protected renderer operation')

    def close(self):
        for document,buffer in self.cache.values():document.close();buffer.close()
        self.cache.clear()
        if self.prints:self.prints.close()
