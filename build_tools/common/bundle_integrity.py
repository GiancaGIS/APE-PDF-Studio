"""Standalone integrity bootstrap, embedded with its digest in each frozen EXE.

Only APE-owned code/resources/native helpers are sealed. Third-party libraries
remain replaceable. This detects damage/basic tampering, not a patched loader.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

MANIFEST = '_internal/ape-integrity.json'
MAX_MANIFEST = 1024*1024
NATIVE = ('APEPDFSandbox.exe', 'APEPDFShell.dll')


class IntegrityError(RuntimeError):
    pass


def regular_file(root, name):
    parts = PurePosixPath(name).parts
    if (not parts or '\\' in name or ':' in name or name.startswith('/') or
            any(part in ('', '.', '..') for part in name.split('/'))):
        raise IntegrityError('Invalid integrity path')
    path = root
    for part in parts:
        path = path / part
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise IntegrityError('Linked application file: '+name)
    if not path.is_file() or info.st_nlink!=1:
        raise IntegrityError('Missing application file: '+name)
    return path


def file_record(path):
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if not 0<=size<=64*1024*1024:
            raise IntegrityError('Application file exceeds size budget')
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            data = stream.read(min(1024*1024,remaining))
            if not data:
                raise IntegrityError('Application file changed while reading')
            digest.update(data)
            remaining-=len(data)
        if stream.read(1):
            raise IntegrityError('Application file grew while reading')
    return {'size': size, 'sha256': digest.hexdigest()}


def load_manifest(root, expected):
    path = regular_file(root, MANIFEST)
    with path.open('rb') as stream:
        data = stream.read(MAX_MANIFEST+1)
    if len(data)>MAX_MANIFEST or hashlib.sha256(data).hexdigest()!=expected:
        raise IntegrityError('Application integrity manifest changed')
    value = json.loads(data)
    if (not isinstance(value, dict) or set(value)!={'schema','version','files'} or type(value['schema']) is not int or value['schema']!=1 or
            not isinstance(value['version'], str) or len(value['version'])>64 or
            not isinstance(value['files'], dict) or not 1<=len(value['files'])<=2048):
        raise IntegrityError('Invalid application integrity manifest')
    for name, record in value['files'].items():
        if (not isinstance(name,str) or len(name)>512 or
                not (name.startswith('_internal/ape_pdf_studio/') or name in NATIVE) or
                not isinstance(record,dict) or set(record)!={'size','sha256'} or
                type(record['size']) is not int or not 0<=record['size']<=64*1024*1024 or
                not isinstance(record['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',record['sha256'])):
            raise IntegrityError('Invalid application integrity record')
    if not set(NATIVE)<=set(value['files']):
        raise IntegrityError('Missing native helper records')
    return value


def verify_bundle(root, expected, *, worker=False, native_only=False):
    root = Path(root).absolute()
    manifest = load_manifest(root, expected)
    for name, record in manifest['files'].items():
        if (worker and name in NATIVE) or (native_only and name not in NATIVE):
            continue
        path = regular_file(root, name)
        if path.stat().st_size!=record['size'] or file_record(path)!=record:
            raise IntegrityError('Application file changed: '+name)
    if not native_only:
        package = root/'_internal/ape_pdf_studio'
        # Reject injected modules as well as changed/missing expected files.
        for directory, folders, files in os.walk(package, followlinks=False):
            for name in folders+files:
                path = Path(directory)/name
                info = path.lstat()
                if path.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                    raise IntegrityError('Linked application entry')
            for name in files:
                relative = (Path(directory)/name).relative_to(root).as_posix()
                if relative not in manifest['files']:
                    raise IntegrityError('Unexpected application file: '+relative)
    return manifest


def create_manifest(library, native, version):
    files = {}
    for path in sorted((Path(library)/'ape_pdf_studio').rglob('*')):
        if path.is_file():
            if path.suffix.lower() not in ('.pyd','.json','.svg','.png','.ico'):
                raise IntegrityError('Uncompiled or unexpected application file: '+str(path))
            files['_internal/'+path.relative_to(library).as_posix()] = file_record(path)
    for name in NATIVE:
        files[name] = file_record(Path(native[name]))
    data = json.dumps(dict(schema=1,version=version,files=files),sort_keys=True,separators=(',',':')).encode()
    if len(data)>MAX_MANIFEST:
        raise IntegrityError('Integrity manifest exceeds its size budget')
    return data


def frozen_check(expected):
    if not getattr(sys, 'frozen', False):
        raise IntegrityError('Integrity bootstrap requires the frozen launcher')
    root = Path(sys.executable).absolute().parent
    worker = Path(sys.executable).name.lower()=='apepdfworker.exe'
    try:
        verify_bundle(root, expected, worker=worker)
    except Exception as exc:
        message = ('APE PDF Studio: installazione incompleta o file dell\'applicazione alterati. '
                   'Reinstalla il pacchetto o ricompilalo dai sorgenti.\n'
                   'Installation incomplete or application files changed. Reinstall or rebuild from source.')
        if worker or Path(sys.executable).name.lower()=='ape-pdf-cli.exe':
            print(message,file=sys.stderr)
        elif os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,message,'APE PDF Studio',0x10)
        raise SystemExit(78) from exc
    # Recheck native isolation helpers immediately before broker launches.
    sys._ape_verify_native = lambda: verify_bundle(root,expected,native_only=True)
