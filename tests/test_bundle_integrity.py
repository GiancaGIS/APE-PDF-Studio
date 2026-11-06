"""Sealed application files, hostile manifests and replaceable dependencies."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_integrity_tested', ROOT/'scripts/bundle_integrity.py')
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)


@pytest.fixture
def sealed(tmp_path):
    library = tmp_path/'lib'
    package = library/'ape_pdf_studio'
    package.mkdir(parents=True)
    (package/'__init__.cp314-win_amd64.pyd').write_bytes(b'package')
    (package/'app.cp314-win_amd64.pyd').write_bytes(b'native code')
    (package/'locales').mkdir()
    (package/'locales/en.json').write_bytes(b'{"example":"English"}')
    bundle = tmp_path/'bundle'
    bundle.mkdir()
    native = {}
    for name in integrity.NATIVE:
        path = bundle/name
        path.write_bytes(b'native isolation component')
        native[name] = path
    import shutil
    shutil.copytree(library,bundle/'_internal')
    data = integrity.create_manifest(library,native,'1.1.8')
    (bundle/integrity.MANIFEST).write_bytes(data)
    return bundle, hashlib.sha256(data).hexdigest()


def test_application_is_sealed_but_third_party_libraries_remain_replaceable(sealed):
    bundle, digest = sealed
    library = bundle/'_internal/PySide6/Qt6Core.dll'
    library.parent.mkdir()
    library.write_bytes(b'compatible Qt replacement')
    assert integrity.verify_bundle(bundle,digest)['version']=='1.1.8'
    library.write_bytes(b'another compatible Qt replacement')
    integrity.verify_bundle(bundle,digest)


@pytest.mark.parametrize('name', ['app.cp314-win_amd64.pyd','locales/en.json'])
@pytest.mark.parametrize('action', ['changed','missing'])
def test_modified_or_missing_application_files_are_rejected(sealed,name,action):
    bundle,digest = sealed
    target = bundle/'_internal/ape_pdf_studio'/name
    target.write_bytes(b'changed') if action=='changed' else target.unlink()
    with pytest.raises((integrity.IntegrityError,OSError)):
        integrity.verify_bundle(bundle,digest)


def test_replacing_manifest_cannot_reseal_modified_code(sealed):
    bundle,digest = sealed
    target = bundle/'_internal/ape_pdf_studio/app.cp314-win_amd64.pyd'
    target.write_bytes(b'patched')
    manifest = json.loads((bundle/integrity.MANIFEST).read_bytes())
    manifest['files'][target.relative_to(bundle).as_posix()] = integrity.file_record(target)
    (bundle/integrity.MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(integrity.IntegrityError,match='manifest changed'):
        integrity.verify_bundle(bundle,digest)


@pytest.mark.parametrize('filename',['injected.py','injected.pyc','injected.pyd','extra.json'])
def test_added_files_in_owned_namespace_are_rejected(sealed,filename):
    bundle,digest = sealed
    (bundle/'_internal/ape_pdf_studio'/filename).write_bytes(b'new file')
    with pytest.raises(integrity.IntegrityError,match='Unexpected application file'):
        integrity.verify_bundle(bundle,digest)


def test_private_worker_checks_its_readable_code_and_broker_checks_native_helpers(sealed):
    bundle,digest = sealed
    (bundle/'APEPDFSandbox.exe').write_bytes(b'changed native launcher')
    integrity.verify_bundle(bundle,digest,worker=True)
    with pytest.raises(integrity.IntegrityError):
        integrity.verify_bundle(bundle,digest,native_only=True)
    with pytest.raises(integrity.IntegrityError):
        integrity.verify_bundle(bundle,digest)


@pytest.mark.parametrize('path', ['../outside','/outside','a/../../outside','a\\outside','a:C','a//b'])
def test_integrity_paths_cannot_escape_bundle(tmp_path,path):
    with pytest.raises(integrity.IntegrityError):
        integrity.regular_file(tmp_path,path)


def test_hardlinked_application_file_is_rejected(sealed,tmp_path):
    bundle,digest = sealed
    target = bundle/'_internal/ape_pdf_studio/app.cp314-win_amd64.pyd'
    outside = tmp_path/'linked.pyd'
    outside.hardlink_to(target)
    with pytest.raises(integrity.IntegrityError):
        integrity.verify_bundle(bundle,digest)


def test_missing_integrity_guard_prevents_frozen_broker_startup(monkeypatch,tmp_path):
    from ape_pdf_studio.core import sandbox
    from ape_pdf_studio.core.errors import PDFSecurityError
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.delattr(sys,'_ape_verify_native',raising=False)
    with pytest.raises(PDFSecurityError):
        sandbox.wrap_command(['worker.exe'],tmp_path)


def test_changed_native_guard_never_launches_unisolated_fallback(monkeypatch,tmp_path):
    from ape_pdf_studio.core import sandbox
    from ape_pdf_studio.core.errors import PDFSecurityError
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    def fail():
        raise integrity.IntegrityError('changed')
    monkeypatch.setattr(sys,'_ape_verify_native',fail,raising=False)
    with pytest.raises(PDFSecurityError):
        sandbox.wrap_command(['worker.exe'],tmp_path)


def test_bootstrap_stops_before_application_import(sealed):
    bundle,digest = sealed
    (bundle/'_internal/ape_pdf_studio/injected.py').write_bytes(b'raise RuntimeError("must not import")')
    code = ('import sys;sys.path.insert(0,'+repr(str(ROOT/'scripts'))+');'
            'import bundle_integrity;sys.frozen=True;sys.executable='+repr(str(bundle/'ape-pdf-cli.exe'))+';'
            'bundle_integrity.frozen_check('+repr(digest)+');print("must not start")')
    process = subprocess.run([sys.executable,'-I','-c',code],capture_output=True,timeout=15)
    assert process.returncode==78
    assert b'must not start' not in process.stdout
    assert b'Installation incomplete' in process.stderr
