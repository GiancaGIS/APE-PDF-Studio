"""Single-document editor worker. All original PDF parsing stays in this process."""
from __future__ import annotations

import os
from pathlib import Path
import sys

if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from ape_pdf_studio.core import rpc_codec

METHODS = frozenset(('insert_blank','add_watermark','ink','update_ink','metadata','update_metadata','interactions','fill_form','rotate','create_form_fields',
    'form_design','update_form_fields','delete','move','insert_pdf','add_note','notes','update_note',
    'add_text_markup','extract','text_at','text_boxes','replace_text','paragraph_at','paragraph_boxes',
    'paragraph_info','replace_paragraph','apply_ocr','undo','redo'))


def checked_path(path,root):
    path=Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path outside the document workspace')
    current=path
    while current!=root:
        if current.exists() and (current.is_symlink() or getattr(current.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('Reparse point in document workspace')
        current=current.parent
    return path


def state(editor):
    return dict(revisions=list(editor._revisions), index=editor._index,
        encrypted=editor.encrypted, document_info=editor.document_info, security_report=editor.security_report)


def main():
    from ape_pdf_studio.core.security_worker import require_memory_limit
    require_memory_limit()
    from ape_pdf_studio.core.editor import PDFEditor
    from ape_pdf_studio.core.sandbox import in_appcontainer
    if os.name=='nt' and not in_appcontainer():
        return 2
    root=Path.cwd().resolve()
    editor=None
    qt=None
    try:
        while True:
            try:
                request=rpc_codec.read(sys.stdin.buffer)
            except EOFError:
                return 0
            try:
                if not isinstance(request,dict) or not isinstance(request.get('method'),str):
                    raise ValueError('Invalid worker request')
                method=request['method']
                if method=='open':
                    if editor is not None:
                        raise ValueError('Document already open')
                    source=checked_path(request['source'],root)
                    editor=PDFEditor(source,request.get('password'))
                    restore=request.get('restore')
                    if restore:
                        revisions=[checked_path(p,root) for p in restore['revisions']]
                        index=restore['index']
                        if not revisions or len(revisions)>1000 or type(index)is not int or not 0<=index<len(revisions):
                            raise ValueError('Invalid revision history')
                        from ape_pdf_studio.core.security import check_pdf
                        check_pdf(revisions[index],request.get('password'))
                        editor._revisions=revisions
                        editor._index=index
                    result=None
                elif editor is None:
                    raise ValueError('No open document')
                elif method in METHODS:
                    args=request.get('args',[])
                    if not isinstance(args,list) or len(args)>8:
                        raise ValueError('Invalid method arguments')
                    for arg in args:
                        if isinstance(arg,Path):
                            checked_path(arg,root)
                    result=getattr(editor,method)(*args)
                elif method=='validate_form_design':
                    from ape_pdf_studio.core.form_edit import validate_design
                    with editor._reader() as reader:
                        result=validate_design(reader,*request['args'])
                elif method=='font_details':
                    from ape_pdf_studio.core.text_edit import font_details
                    result=font_details(editor.path,*request['args'])
                elif method.startswith('qt_') or method=='print_source':
                    if qt is None:
                        from ape_pdf_studio.core.qt_worker_document import Documents
                        qt=Documents(editor,root)
                    result=qt.call(method,request.get('args',[]))
                else:
                    raise ValueError('Unknown document operation')
                response={'ok':True,'value':result,'state':state(editor)}
            except Exception as exc:
                response={'ok':False,'error':str(exc)[:4096],'type':type(exc).__name__}
            sys.stdout.buffer.write(rpc_codec.dumps(response))
            sys.stdout.buffer.flush()
    finally:
        if qt is not None:
            qt.close()
        if editor is not None:
            editor.close()


if __name__=='__main__':
    raise SystemExit(main())
