# Third-party notices

APE PDF Studio — Copyright (C) 2026 GiancaGIS — AGPL-3.0-only.

The application combines third-party software. Each component retains its
original copyright and license; the application license does not relicense
those components. Required notices accompany the Windows distribution in
`licenses/`. `dependency-manifest.json` records the exact packages and versions
in that release. The dependency-source archive includes their source and build
files, with download provenance and SHA-256 checksums in `source-manifest.json`.

| Component | License / distribution notes | Upstream |
| --- | --- | --- |
| PyMuPDF and MuPDF | Open-source AGPLv3 build. Preserve upstream notices and applicable producer notices. Both exact source distributions are included; the MuPDF archive includes its bundled components. | https://github.com/pymupdf/PyMuPDF |
| PySide6 and Shiboken6 | LGPLv3 / GPL alternatives. Used under the applicable open-source terms, without a commercial Qt license. Qt DLLs remain separate and replaceable. | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| Qt, Qt PDF and PDFium | Module-specific LGPL/GPL and third-party licenses. Includes the exact Qt and Qt PDF/WebEngine sources, copyright/license files and third-party attributions used for the release. | https://doc.qt.io/qt-6/licensing.html |
| pypdf | BSD-3-Clause. Retain copyright, conditions and disclaimer. | https://github.com/py-pdf/pypdf |
| pdf2docx | MIT for pdf2docx itself. Its use of PyMuPDF remains subject to PyMuPDF's AGPL terms. | https://github.com/ArtifexSoftware/pdf2docx |
| python-docx | MIT. Retain copyright and permission notice. | https://github.com/python-openxml/python-docx |
| Tesseract and tessdata_fast | Apache-2.0. OCR models are downloaded from tessdata_fast 4.1.0 and hash-checked. Model licenses and source are included with the release even though model downloads are optional. | https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0 |
| CPython | Python Software Foundation license and bundled third-party notices. | https://www.python.org/ |
| Other Python/native dependencies | Their own licenses, including NumPy, OpenCV, lxml, cryptography and their bundled third-party components. See the release inventory and copied upstream notices. | See dependency-manifest.json |
| OpenSSL and cryptography's Rust crates | Apache-2.0/BSD and crate-specific terms. Exact native versions and wheel SBOMs are recorded; OpenSSL and registry crate sources are included separately. | https://github.com/pyca/cryptography |
| OpenBLAS/LAPACK and libxml2/libxslt | Original licenses retained. Includes the OpenBLAS build tag, its exact upstream commit, and the native XML/XSLT source versions used by lxml. | See source-manifest.json |
| PyInstaller | GPL with a bootloader exception allowing distribution under the application's license. Its source/build files accompany the release. | https://pyinstaller.org/en/stable/license.html |

No third-party source was intentionally modified for the release. Upstream
archives are preserved without modification. The manifests identify the
versions; they must be regenerated when dependencies change.

OpenCV's optional `opencv_videoio_ffmpeg*.dll` video helper is excluded from
the Windows bundle: the application uses OpenCV for PDF images, not video.
Upstream OpenCV notices are preserved verbatim, including references to
components from other platforms and optional features. They do not imply that
all those components are distributed by APE.

## Source availability and replacing libraries

Every public installer release must be accompanied by the matching application
source ZIP and dependency-source ZIP at the same download location, at no
additional charge. See `SOURCE_CODE.md` and `docs/github-release.md`.

Qt/PySide6 libraries are dynamically loaded from `_internal/PySide6/` and
`_internal/shiboken6/`. Users may replace/rebuild those libraries and rebuild
the application; the application imposes no technical restriction on that
replacement or on reverse engineering required to debug modifications to
LGPL components. Use compatible builds and the supplied source/build
instructions. No proprietary EULA overrides these rights.
