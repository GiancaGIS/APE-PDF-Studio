# Corresponding source and rebuilding APE PDF Studio

Copyright (C) 2026 GiancaGIS. APE PDF Studio is licensed under AGPL-3.0-only.

The **same download location as the installer** provides, at no additional
charge, these matching release assets:

- `APEPDFStudio-<version>-Source.zip`: application, native shell extension,
  artwork, build scripts, tests, license texts and exact dependency lock.
- `APEPDFStudio-<version>-DependencySources.zip`: original source distributions
  of runtime/build dependencies, Qt, PySide/Shiboken, Qt PDF/WebEngine/PDFium,
  CPython and the OCR data. Includes separate MuPDF and its third-party sources.
  Also includes native OpenSSL, Rust crates, XML/XSLT libraries, the exact
  OpenBLAS/LAPACK commit and Windows build patches, and CPython's native externals.
- `APEPDFStudio-<version>-SHA256SUMS.txt`: hashes of the release assets.

The installed `licenses/source-manifest.json` records each upstream archive's
filename, version, URL and SHA-256, plus the application source file hashes.
`licenses/dependency-manifest.json` identifies the packages used for that build.
It also records native versions independently of their Python bindings.
The ZIPs contain those manifests too. Request/download the assets belonging to
your installer version, rather than the current development branch.

## Rebuild the application on Windows x64

1. Unpack the application source ZIP. Install the CPython version recorded in
   `dependency-manifest.json` (the current release uses Python 3.14.5 x64).
2. Install Visual Studio/Build Tools with Desktop development with C++, x64
   MSVC and Windows SDK; install Inno Setup 6.3 or later to build the installer.
   These are separately available system/build tools, not application code.
3. In PowerShell, from the unpacked project root:

   ```powershell
   py -3.14 -m venv .venv-build
   .\.venv-build\Scripts\python.exe -m pip install -r requirements-release.txt
   .\.venv-build\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
   .\scripts\build_installer.ps1 -PythonExe .\.venv-build\Scripts\python.exe -SkipDependencyInstall
   ```

   The dependency lock fixes package versions. Network access is needed to
   retrieve packages and verify upstream source archives on the first build.
   To reuse release source archives offline, unpack `sources/` from the
   dependency ZIP into `.dev-deps/legal/sources/`, retain the source manifest
   and run `scripts/prepare_release.py --seed-source-manifest source-manifest.json`
   before using `-OfflineSources` with the installer build script.

4. The application folder is produced under `dist/APEPDFStudio/`. The installer
   is under `dist/installer/`; source assets are under `dist/release/`.

Install optional development dependencies to run the supplied tests:
`python -m pip install pytest` then `python -m pytest -q`.

## Rebuild or replace third-party libraries

Upstream archives contain their own build systems, configuration files and
instructions. Their original directory structures and licenses are preserved.
Unpack both the PyMuPDF and MuPDF archives before building. Qt's full source and the separately versioned Qt PDF/WebEngine
archive cover native Qt DLLs and PDFium/Chromium; the PySide archive covers
bindings and Shiboken.

- Qt: https://doc.qt.io/qt-6/windows-building.html
- Qt for Python: https://doc.qt.io/qtforpython-6/building_from_source/index.html
- Qt PDF/WebEngine: https://wiki.qt.io/QtWebEngine/Qt6Build
- PyMuPDF: https://pymupdf.readthedocs.io/en/latest/installation.html

Use the source manifest's **exact versions** and the appropriate upstream
Windows build instructions. Wheels built from modified sources can be installed
in the build environment before rebuilding APE. To replace compatible Qt/PySide
libraries in an installed copy, close APE and the relevant Explorer preview
processes, back up the installation and replace libraries under
`_internal/PySide6/` and `_internal/shiboken6/`. The app has no library-signature
check that prevents replacement. For incompatible ABI changes, rebuild the
application and bindings together. Users retain the LGPL rights to modify,
relink, run replacement libraries and reverse engineer for debugging them.

## Redistributing a release

Publish the installer **and all matching source assets together**. Retain the
copyright and license notices. Preserve the release assets so recipients of
older installers can still obtain their corresponding source. A link to an
upstream project's latest branch does not replace the exact source archives.
`docs/github-release.md` describes the release procedure.
