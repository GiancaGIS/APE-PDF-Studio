# Publishing an AGPL release on GitHub

The original APE PDF Studio code and artwork are AGPL-3.0-only, copyright
GiancaGIS. Dependencies retain their respective licenses. The release procedure
provides full license texts, third-party copyright notices and corresponding
source for the compiled components.

## Build and prepare the release

Use `SOURCE_CODE.md` and `requirements-release.txt` to recreate the release
environment. `scripts/build_installer.ps1` prepares and verifies legal files,
builds the application and installer, runs the smoke checks and produces the
application/dependency source ZIPs. Downloaded sources are cached under the
ignored `.dev-deps/legal/sources/` directory.

The preparation script downloads **source only** from official Qt, PyPI,
CPython, MuPDF, OpenSSL, GNOME, crates.io and Tesseract locations; it does not execute those archives. It checks
PyPI/Qt SHA-256 values where provided and records SHA-256 for every archive.
Subsequent cache reuse revalidates the hashes. Original archives are retained.
License/copyright and Qt/Chromium attribution files are copied from those
sources into the installed `licenses/` directory, including MuPDF and its
third-party notices. The manifest records each notice's original archive/member path.
Native library versions and cryptography's wheel SBOMs are retained. OpenBLAS
includes both its tagged build scripts/Windows patches and pinned source commit.
The unused OpenCV FFmpeg video helper is excluded from the Windows bundle.

When intentionally changing dependencies, first create and test the new build
environment, then regenerate the lock with:

```powershell
.\.venv-build\Scripts\python.exe scripts/prepare_release.py --freeze-requirements
```

Commit the new lock and rebuild the release. Preparation stops on unavailable
exact source versions, corrupted archives or mismatched installed versions;
release packaging stops if application sources changed after preparation.

## Repository and release assets

Commit `src/`, `native/`, `scripts/`, `installer/`, tests, documentation,
original assets, `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_CODE.md`,
`licenses/`, `requirements-release.txt` and `pyproject.toml`. The ignore file
excludes environments, build outputs, dependency caches and local IDE files.

Create a tag matching the application version and a GitHub Release. Upload:

1. `dist/installer/APEPDFStudio-<version>-Setup-x64.exe`.
2. `dist/release/APEPDFStudio-<version>-Source.zip`.
3. `dist/release/APEPDFStudio-<version>-DependencySources.zip`.
4. `dist/release/APEPDFStudio-<version>-SHA256SUMS.txt`.

Offer the source downloads with equivalent access, at no additional charge, on
the same release page as the installer. Do not publish the EXE alone. GitHub's
automatic repository ZIP does not include third-party sources and is not a
substitute for the dependency-source ZIP. Keep historical source assets.

The generated `dist/release/RELEASE_NOTES.md` is ready to paste into the release
description. It identifies the app's license and matching source attachments.
This workflow prepares local artifacts; publishing to GitHub is a separate
action against your chosen repository.

## Upstream licensing information

- AGPLv3: https://opensource.org/license/agpl-3-0
- PyMuPDF: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
- Qt LGPL obligations: https://www.qt.io/development/open-source-lgpl-obligations
- Qt PDF attributions: https://doc.qt.io/qt-6/qtpdf-licensing.html
- PyInstaller exception: https://pyinstaller.org/en/stable/license.html

These files document the release's licensing arrangement; they do not claim a
legal certification or rights over upstream components.
