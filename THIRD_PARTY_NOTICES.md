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
| Droid Sans Fallback | Apache-2.0; Copyright (c) 2005-2008, The Android Open Source Project. The font supplied by MuPDF is embedded in `examples/complex_cjk.pdf`, together with its copyright notice and license. | https://github.com/ArtifexSoftware/mupdf/tree/master/resources/fonts/droid |
| CPython | Python Software Foundation license and bundled third-party notices. | https://www.python.org/ |
| Other Python/native dependencies | Their own licenses, including NumPy, OpenCV, lxml, cryptography and their bundled third-party components. See the release inventory and copied upstream notices. | See dependency-manifest.json |
| OpenSSL and cryptography's Rust crates | Apache-2.0/BSD and crate-specific terms. Exact native versions and wheel SBOMs are recorded; OpenSSL and registry crate sources are included separately. | https://github.com/pyca/cryptography |
| OpenBLAS/LAPACK and libxml2/libxslt | Original licenses retained. Includes the OpenBLAS build tag, its exact upstream commit, and the native XML/XSLT source versions used by lxml. | See source-manifest.json |
| PyInstaller | GPL with a bootloader exception allowing distribution under the application's license. Its source/build files accompany the release. | https://pyinstaller.org/en/stable/license.html |
| Cython | Apache-2.0 compiler; generated runtime utility code retains its applicable upstream notices. Exact compiler source and license accompany the build/source distribution. Cython itself is not required at application runtime. | https://github.com/cython/cython |

Original upstream archives are preserved without modification. The corrected
PyMuPDF/MuPDF build is derived using `scripts/prepare_patched_mupdf.py` and
`scripts/build_patched_mupdf.ps1`; the manifests record its versions and
derivation. Regenerate the manifests when dependencies change.

Native installers use OpenCV built with `build_tools/common/build_opencv.py`.
The verified OpenCV source distribution, build recipe and inventory accompany
the corresponding source. The build uses the modules needed for PDF image
processing, with Intel IPP and FFmpeg disabled. The original OpenCV notices
are retained, including notices for upstream optional features.

The Linux AppImage builds the AppImage type-2 runtime from pinned sources.
It links libfuse, Squashfuse and Zstandard statically and loads glibc from
the host. The source record and license notices for the bundled code are under
`usr/share/doc/ape-pdf-studio/appimage-runtime` in the AppImage. See
[`SOURCE_CODE.md`](SOURCE_CODE.md) for the matching source archives.

## Source availability and replacing libraries

The matching GitHub tag provides the application source for Windows and Linux
at no charge. The same release page provides the platform dependency-source
archives. See
[source and build instructions](SOURCE_CODE.md).

Qt/PySide6 libraries are dynamically loaded from `_internal/PySide6/` and
`_internal/shiboken6/`. Users may replace/rebuild those libraries and rebuild
the application; the application imposes no technical restriction on that
replacement or on reverse engineering required to debug modifications to
LGPL components. Use compatible builds and the supplied source/build
instructions. No proprietary EULA overrides these rights.
