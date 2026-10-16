"""Verified upstream sources plus the small documented Windows compatibility patch."""
import hashlib
import json
from pathlib import Path
import tarfile
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.dev-deps/patched-mupdf'
SOURCES={
    'pymupdf-1.28.2.tar.gz':('https://pypi.org/pypi/pymupdf/1.28.2/json',
        '5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249'),
    'mupdf-1.28.5-source.tar.gz':('https://mupdf.com/downloads/archive/mupdf-1.28.5-source.tar.gz',
        '98a5c10cda20c3992cdf76ff6b2a1149c32bd79cc796d3f703230b1185b7e934'),
}


def fetch(url):
    return urlopen(Request(url,headers={'User-Agent':'APE-PDF-Studio-source-build'}),timeout=60)


def main():
    WORK.mkdir(parents=True,exist_ok=True)
    for filename,(url,expected) in SOURCES.items():
        archive=WORK/filename
        cached=ROOT/'.dev-deps/legal/sources'/filename
        if not archive.exists() and cached.is_file():
            import shutil
            shutil.copyfile(cached,archive)
        if not archive.is_file() or hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=expected:
            if url.endswith('/json'):
                with fetch(url) as stream:metadata=json.load(stream)
                record=next(f for f in metadata['urls'] if f['packagetype']=='sdist' and f['digests']['sha256']==expected)
                url=record['url']
            temporary=archive.with_suffix('.partial')
            try:
                with fetch(url) as source,temporary.open('wb') as output:
                    size=0
                    while chunk:=source.read(65536):
                        size+=len(chunk)
                        if size>256*1024*1024:raise RuntimeError('Source archive too large')
                        output.write(chunk)
                if hashlib.file_digest(temporary.open('rb'),'sha256').hexdigest()!=expected:raise RuntimeError('Source hash mismatch')
                temporary.replace(archive)
            finally:temporary.unlink(missing_ok=True)
        extracted=WORK/filename.removesuffix('.tar.gz')
        if not extracted.is_dir():
            with tarfile.open(archive) as source:source.extractall(WORK,filter='data')
    source=WORK/'pymupdf-1.28.2'
    patches={source/'setup.py':('        version_p,','        version_p+\'.post1285\','),
             source/'src/extra.i':('PyString_FromString(text)','PyUnicode_FromString(text)')}
    for path,(old,new) in patches.items():
        content=path.read_text(encoding='utf-8')
        if new not in content:
            if content.count(old)!=1:raise RuntimeError('Unexpected upstream patch context: '+path.name)
            path.write_text(content.replace(old,new),encoding='utf-8')
    print(source)


if __name__=='__main__':main()
