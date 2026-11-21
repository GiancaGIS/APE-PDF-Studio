# -*- mode: python; coding: utf-8 -*-
"""Native Linux onedir bundle or macOS application, with the same worker entry."""
import json
import hashlib
import os
from pathlib import Path
import sys
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(root / 'build_tools/common'))
from release_privacy import public_tables

target = 'macos' if sys.platform == 'darwin' else 'linux'
compiled = Path(os.environ['APE_UNIX_COMPILED_DIR'])
inventory = json.loads((compiled / 'compiled-modules.json').read_text(encoding='utf-8'))
library = compiled / 'lib'
for name, expected in inventory['sources'].items():
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
        raise RuntimeError('Application changed after native compilation: ' + name)

datas = [(str(root / name), 'legal') for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'SOURCE_CODE.md')]
datas += [(str(path), 'legal') for path in sorted((root / 'licenses').glob('*.txt'))]
datas += collect_data_files('PySide6', includes=['**/qtbase_' + lang + '.qm' for lang in ('it', 'en', 'es', 'fr')])
datas += collect_data_files('ape_pdf_studio', includes=['locales/*.json', 'assets/*'])
for package in ('PySide6', 'pypdf', 'PyMuPDF', 'pdf2docx', 'python-docx', 'cryptography'):
    datas += copy_metadata(package, recursive=True)

analysis = Analysis(
    [str(root / 'build_tools/launchers/unix_launcher.py')],
    pathex=[str(root / 'src')],
    datas=datas,
    hiddenimports=[name.removesuffix('.__init__') for name in inventory['modules']],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'pytest', 'Cython'],
)
analysis.pure = [entry for entry in analysis.pure
                 if entry[0] != 'ape_pdf_studio' and not entry[0].startswith('ape_pdf_studio.')]
suffix = sysconfig.get_config_var('EXT_SUFFIX')
for name in inventory['modules']:
    path = Path(*name.split('.')).with_suffix(suffix)
    analysis.binaries.append((path.as_posix(), str(library / path), 'EXTENSION'))

analysis.pure, analysis.datas = public_tables(analysis.pure, analysis.datas, compiled / 'public-metadata')
pyz = PYZ(analysis.pure)
# Keep standard streams available to the worker/CLI on both platforms.
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='APEPDFStudio',
          console=True, strip=False, upx=False,
          codesign_identity=os.environ.get('APE_MACOS_CODESIGN_IDENTITY') or None)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='APEPDFStudio')
if target == 'macos':
    app = BUNDLE(
        collection, name='APE PDF Studio.app',
        icon=str(root / 'src/ape_pdf_studio/assets/app-icon.png'),
        bundle_identifier='it.giancagis.apepdfstudio',
        version=os.environ['APE_BUILD_VERSION'],
        info_plist={'CFBundleDisplayName': 'APE PDF Studio',
                    'CFBundleVersion': os.environ['APE_BUILD_VERSION'],
                    'CFBundleShortVersionString': os.environ['APE_BUILD_VERSION'],
                    'LSBackgroundOnly': False,
                    'NSHighResolutionCapable': True,
                    'CFBundleDocumentTypes': [{'CFBundleTypeName': 'PDF', 'CFBundleTypeRole': 'Viewer',
                                               'LSItemContentTypes': ['com.adobe.pdf'], 'LSHandlerRank': 'Alternate'}]},
    )
