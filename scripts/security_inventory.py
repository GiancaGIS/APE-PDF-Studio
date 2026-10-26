"""Version inventory and a release gate for the reviewed native PDF engines."""
import argparse
from datetime import date
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

from packaging.version import Version

ROOT=Path(__file__).resolve().parents[1]
ADVISORY_REPOSITORIES=('py-pdf/pypdf','pymupdf/PyMuPDF')
MAX_ADVISORY_BYTES=2*1024*1024


class AdvisoryError(RuntimeError):
    def __init__(self,message,*,status=None,rate_limited=False):
        super().__init__(message)
        self.status=status
        self.rate_limited=rate_limited


def advisories(repo):
    if repo not in ADVISORY_REPOSITORIES:
        raise ValueError('Unexpected advisory repository')
    headers={'Accept':'application/vnd.github+json','User-Agent':'APE-PDF-Studio-security-inventory',
             'X-GitHub-Api-Version':'2022-11-28'}
    token=os.environ.get('GITHUB_TOKEN','').strip()
    # Published public advisories do not require additional repository permissions.
    request=Request(f'https://api.github.com/repos/{repo}/security-advisories?state=published&per_page=100',headers=headers)
    # A redirect must never forward the CI credential to another endpoint.
    if token:request.add_unredirected_header('Authorization',f'Bearer {token}')
    try:
        with urlopen(request,timeout=20) as response:data=response.read(MAX_ADVISORY_BYTES+1)
    except HTTPError as exc:
        try:
            limited=(exc.code==429 or exc.headers.get('X-RateLimit-Remaining')=='0'
                     or bool(exc.headers.get('Retry-After'))
                     or (exc.code==403 and b'rate limit' in exc.read(8192).lower()))
        finally:exc.close()
        if limited:
            # Do not retry immediately: the API can require waiting for its reset.
            hint='Retry after the GitHub API rate limit resets.'
            if not token:hint+=' Set GITHUB_TOKEN to authenticate this check.'
            raise AdvisoryError(f'GitHub API rate limit exceeded (HTTP {exc.code}). {hint}',
                                status=exc.code,rate_limited=True) from None
        raise AdvisoryError(f'GitHub advisory request failed (HTTP {exc.code}).',status=exc.code) from None
    except (URLError,TimeoutError,OSError):
        raise AdvisoryError('Cannot reach the GitHub advisory API; check network access and retry.') from None
    try:
        if len(data)>MAX_ADVISORY_BYTES:raise ValueError('Advisory response too large')
        values=json.loads(data)
        if not isinstance(values,list):raise ValueError('Invalid advisory feed')
        return [{'id':v['ghsa_id'],'url':v['html_url'],'published':v['published_at'],
                 'vulnerabilities':v.get('vulnerabilities',[])} for v in values]
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
        raise AdvisoryError('GitHub returned an invalid or oversized advisory feed.') from None


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
    errors={}
    if args.upstream:
        result['advisories']={}
        for repo in ADVISORY_REPOSITORIES:
            try:result['advisories'][repo]=advisories(repo)
            except AdvisoryError as exc:
                errors[repo]={'message':str(exc),'http_status':exc.status,'rate_limited':exc.rate_limited}
                # Avoid a second request against an already exhausted API budget.
                if exc.rate_limited:break
        result['upstream_check']={'status':'incomplete' if errors else 'complete','errors':errors,
            'unchecked_repositories':[repo for repo in ADVISORY_REPOSITORIES if repo not in result['advisories']]}
    content=json.dumps(result,indent=2,ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(content,encoding='utf-8')
    else:print(content)
    if args.release and (Version(result['pypdf'])<Version('6.21.2') or Version(result['MuPDF'])<Version('1.28.5')):
        raise SystemExit('Release blocked: pypdf >=6.21.2 and native MuPDF >=1.28.5 are required.')
    if errors:
        for repo,error in errors.items():print(f"{repo}: {error['message']}",file=sys.stderr)
        raise SystemExit('Upstream advisory check incomplete; see the security inventory report.')


if __name__=='__main__':main()
