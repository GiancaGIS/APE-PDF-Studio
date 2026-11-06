"""Generate a sealed manifest and a self-contained PyInstaller startup hook."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib

from bundle_integrity import create_manifest, verify_bundle

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    output = ROOT/'build/windows/cython'
    native = {name: ROOT/'build/windows'/folder/name for name, folder in (
        ('APEPDFShell.dll','shell'),('APEPDFSandbox.exe','sandbox'))}
    version = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    data = create_manifest(output/'lib',native,version)
    (output/'ape-integrity.json').write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    source = (ROOT/'scripts/bundle_integrity.py').read_text(encoding='utf-8')
    (output/'integrity_hook.py').write_text(source+'\nfrozen_check('+repr(digest)+')\n',encoding='utf-8')
    print('Integrity manifest and executable startup hook prepared: '+digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',type=Path)
    args = parser.parse_args()
    if args.verify:
        data = (ROOT/'build/windows/cython/ape-integrity.json').read_bytes()
        manifest = verify_bundle(args.verify,hashlib.sha256(data).hexdigest())
        if (args.verify/'_internal/ape-integrity.json').read_bytes()!=data:
            raise RuntimeError('Bundle integrity manifest differs from prepared build')
        print(f'Verified {len(manifest["files"])} application files')
    else:
        prepare()


if __name__=='__main__':
    main()
