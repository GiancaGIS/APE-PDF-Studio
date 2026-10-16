"""Version inventory and a release gate for the reviewed native PDF engines."""
import argparse
from datetime import date
from importlib.metadata import version
import json
from pathlib import Path
import sys
from urllib.request import Request,urlopen

from packaging.version import Version

ROOT=Path(__file__).resolve().parents[1]


def inventory():
    import pymupdf
    from PySide6.QtCore import qVersion
    result={'date':date.today().isoformat(),'python':sys.version.split()[0],
            'pypdf':version('pypdf'),'PyMuPDF':version('pymupdf'),'MuPDF':pymupdf.mupdf_version,'Qt':qVersion(),
            'manual_native_advisories':[
                'https://mupdf.com/releases/history',
                'https://www.qt.io/blog/tag/security',
                'https://chromereleases.googleblog.com/search/label/Stable%20updates',
            ]}
    if sys.platform=='win32':
        from prepare_release import native_versions
        result['native']=native_versions()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--release',action='store_true')
    parser.add_argument('--upstream',action='store_true',help='Check official public Python upstream advisory feeds')
    args=parser.parse_args()
    result=inventory()
    if args.upstream:
        result['advisories']={}
        for repo in ('py-pdf/pypdf','pymupdf/PyMuPDF'):
            request=Request(f'https://api.github.com/repos/{repo}/security-advisories?per_page=100',headers={
                'Accept':'application/vnd.github+json','User-Agent':'APE-PDF-Studio-security-inventory','X-GitHub-Api-Version':'2022-11-28'})
            with urlopen(request,timeout=20) as response:data=response.read(2*1024*1024+1)
            if len(data)>2*1024*1024:raise RuntimeError('Advisory response too large')
            values=json.loads(data)
            if not isinstance(values,list):raise RuntimeError('Invalid advisory feed')
            result['advisories'][repo]=[{'id':v['ghsa_id'],'url':v['html_url'],'published':v['published_at'],
                'vulnerabilities':v.get('vulnerabilities',[])} for v in values]
    content=json.dumps(result,indent=2,ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(content,encoding='utf-8')
    else:print(content)
    if args.release and (Version(result['pypdf'])<Version('6.20.1') or Version(result['MuPDF'])<Version('1.28.5')):
        raise SystemExit('Release blocked: pypdf >=6.20.1 and native MuPDF >=1.28.5 are required.')


if __name__=='__main__':main()
