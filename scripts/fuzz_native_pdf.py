"""Synthetic PDF mutation runner. Use a disposable VM for extended campaigns."""
from pathlib import Path
import json
import os
import sys


def worker():
    from ape_pdf_studio.core.sandbox import in_appcontainer
    from ape_pdf_studio.core.security_worker import require_memory_limit
    if os.name!='nt' or not in_appcontainer():return 2
    require_memory_limit()
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    app=QGuiApplication([])
    result={}
    try:
        import pymupdf
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        with pymupdf.open('input.pdf') as doc:
            if not 0<len(doc)<=10:raise ValueError('page budget')
            page=doc[0];page.get_pixmap(matrix=pymupdf.Matrix(.2,.2),alpha=False)
            if len(page.get_text())>250000:raise ValueError('text budget')
        result['mupdf']='accepted'
    except Exception:result['mupdf']='rejected'
    try:
        doc=QPdfDocument();doc.load(str(Path('input.pdf').resolve()));app.processEvents()
        if doc.status()!=doc.Status.Ready or not 0<doc.pageCount()<=10:raise ValueError('invalid PDF')
        image=doc.render(0,QSize(128,128));doc.getAllText(0);doc.close()
        result['pdfium']='accepted' if not image.isNull() else 'rejected'
    except Exception:result['pdfium']='rejected'
    print(json.dumps(result),flush=True);return 0


def main():
    import argparse
    import io
    import random
    import shutil
    import subprocess
    import time
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'src'))
    from ape_pdf_studio.core.private_work import PrivateDirectory
    from ape_pdf_studio.core.sandbox import wrap_command
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=int,default=12)
    parser.add_argument('--seed',type=int,default=29147)
    parser.add_argument('--budget',type=int,default=120)
    parser.add_argument('--output',type=Path,default=root/'build/windows/native-fuzz.json')
    args=parser.parse_args()
    if os.name!='nt' or not 1<=args.cases<=10000 or not 1<=args.budget<=3600:raise SystemExit('Invalid Windows fuzz budgets')
    runtime=root/'.dev-deps/sandbox-python'
    if not (runtime/'python.exe').is_file():raise SystemExit('Prepare the development sandbox runtime first')
    script=runtime/'ape_native_fuzz.py';shutil.copyfile(__file__,script)
    writer=PdfWriter();page=writer.add_blank_page(200,200)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=b'BT /F1 12 Tf 20 100 Td (Synthetic fuzz seed) Tj ET'
    stream=DecodedStreamObject();stream.set_data(content);page[NameObject('/Contents')]=writer._add_object(stream)
    data=io.BytesIO();writer.write(data);seed=data.getvalue();rng=random.Random(args.seed)
    results=[];deadline=time.monotonic()+args.budget
    for index in range(args.cases):
        if time.monotonic()>=deadline:break
        mutated=bytearray(seed)
        if index:
            start,end=(seed.index(content),seed.index(content)+len(content)) if index%2 else (0,len(seed))
            for _ in range(rng.randint(1,12)):mutated[rng.randrange(start,end)]=rng.randrange(256)
        with PrivateDirectory() as folder:
            Path(folder,'input.pdf').write_bytes(mutated)
            try:
                command=wrap_command([sys.executable,'-I',str(script),'--worker'],folder,timeout=5000,output_limit=65536)
                child=subprocess.run(command,input=b'',capture_output=True,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
                status={'exit':child.returncode} if child.returncode else {'invalid_response':True}
                if child.returncode==0:
                    for line in child.stdout.splitlines():
                        try:record=json.loads(line)
                        except (ValueError,UnicodeError):continue
                        if isinstance(record,dict) and set(record)=={'mupdf','pdfium'} and all(v in ('accepted','rejected') for v in record.values()):status=record
            except subprocess.TimeoutExpired:status={'timeout':True}
            results.append({'case':index,**status})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'seed':args.seed,'cases':len(results),'results':results},indent=2),encoding='utf-8')
    print(f'Native mutation cases: {len(results)}; report: {args.output.name}')
    return int(any('exit' in row or 'invalid_response' in row for row in results))


if __name__=='__main__':raise SystemExit(worker() if '--worker' in sys.argv else main())
