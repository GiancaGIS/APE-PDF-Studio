"""Prepare notices, verified upstream sources and matching AGPL release assets.

Downloads source archives only; never installs or executes downloaded code.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tomllib
import urllib.request
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".dev-deps/legal/sources"
LEGAL = ROOT / "build/windows/legal"
REQUIRED_TEXTS = ("AGPL-3.0-only", "LGPL-3.0-only", "GPL-3.0-only", "GPL-2.0-only", "Apache-2.0")
SOURCE_DIRS = ("src", "scripts", "native", "installer", "docs", "tests", "examples", "licenses", ".github")
SOURCE_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "SOURCE_CODE.md", "README.md",
                "pyproject.toml", "requirements-release.txt", "requirements-native-build.txt", ".gitignore")
NOTICE_SCHEMA = 2


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_url(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def download(url, filename, expected=None, offline=False):
    """Cache exact bytes, checking SHA-256 before and after every download."""
    if Path(filename).name != filename:
        raise ValueError("Source archive filename must be a basename")
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / filename
    checksum = path.with_name(path.name + ".sha256")
    cached_hash = expected or (checksum.read_text().strip() if checksum.exists() else None)
    if path.is_file() and cached_hash and digest(path) == cached_hash:
        return path
    if offline:
        raise RuntimeError(f"Missing or corrupt cached source: {filename}")
    print(f"[sources] Download: {filename}", flush=True)
    temporary = path.with_name(path.name + ".partial")
    try:
        # Use Qt's master server instead of a geo-selected mirror with varying TLS chains.
        request_url = url.replace("https://download.qt.io/", "https://master.qt.io/", 1) if url.startswith("https://download.qt.io/") else url
        with urllib.request.urlopen(request_url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        observed = digest(temporary)
        if expected and observed != expected:
            raise RuntimeError(f"Source SHA-256 mismatch: {filename}")
        temporary.replace(path)
        checksum.write_text(observed + "\n", encoding="ascii")
    finally:
        temporary.unlink(missing_ok=True)
    return path


def packages(project):
    """Resolve installed runtime/build dependencies, including requested extras."""
    pending = [Requirement(value) for value in project["dependencies"]
               + project["optional-dependencies"]["installer"] + ["setuptools", "wheel"]]
    requested = {}
    result = {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        extras = set(requirement.extras)
        if name in requested and extras <= requested[name]:
            continue
        requested.setdefault(name, set()).update(extras)
        package = metadata.distribution(name)
        if requirement.specifier and package.version not in requirement.specifier:
            raise RuntimeError(f"Installed package does not satisfy {requirement}")
        result[name] = package
        for value in package.requires or ():
            child = Requirement(value)
            if child.marker is None or any(child.marker.evaluate({"extra": extra})
                                           for extra in requested[name] | {""}):
                pending.append(child)
    return result


def qt_source_records(qt_version, pyside_version, pdf_version, offline):
    qt_base = f"https://download.qt.io/archive/qt/{'.'.join(qt_version.split('.')[:2])}/{qt_version}/single/"
    pyside_base = f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{pyside_version}-src/"
    result = []
    for name, base, filename in (
        ("Qt", qt_base, f"qt-everywhere-src-{qt_version}.tar.xz"),
        ("PySide6/Shiboken6", pyside_base, f"pyside-setup-everywhere-src-{pyside_version}.tar.xz"),
    ):
        if offline:
            expected = (CACHE / (filename + ".sha256")).read_text().strip()
        else:
            expected = read_url(base + filename + ".sha256").decode().split()[0]
        result.append({"component": name, "version": qt_version if name == "Qt" else pyside_version,
                       "url": base + filename, "filename": filename, "sha256": expected})
    # Qt PDF/WebEngine now have a separate version/release train.
    if pdf_version != qt_version:
        qt_digits = qt_version.replace(".", "")
        pdf_digits = pdf_version.replace(".", "")
        base = ("https://download.qt.io/online/qtsdkrepository/all_os/extensions/qtwebengine/"
                f"{qt_digits}/{pdf_digits}/src_doc_examples/extensions.qtwebengine.{qt_digits}.{pdf_digits}.src/")
        index = CACHE / f"qtwebengine-{pdf_version}-index.json"
        if offline:
            record = json.loads(index.read_text(encoding="utf-8"))
        else:
            listing = read_url(base).decode()
            matches = re.findall(r'href="([^"/]*qtwebengine-everywhere-src-' + re.escape(pdf_version) + r'\.tar\.xz)"', listing)
            if len(matches) != 1:
                raise RuntimeError("Cannot identify exact Qt PDF/WebEngine source archive")
            record = {"component": "Qt PDF/WebEngine (including PDFium/Chromium)", "version": pdf_version,
                      "url": base + matches[0], "filename": matches[0]}
            CACHE.mkdir(parents=True, exist_ok=True)
            index.write_text(json.dumps(record, indent=2), encoding="utf-8")
        result.append(record)
    return result


def native_versions():
    from PySide6.QtCore import qVersion
    from PySide6 import __version__ as pyside_version
    from PySide6.QtPdf import __file__ as pdf_module
    # Read the native PDF library's version, not the binding/package version.
    import ctypes
    from ctypes import wintypes
    dll = Path(pdf_module).with_name("Qt6Pdf.dll")
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(dll), None)
    data = ctypes.create_string_buffer(size)
    if not size or not ctypes.windll.version.GetFileVersionInfoW(str(dll), 0, size, data):
        raise RuntimeError("Cannot read the Qt PDF DLL version")
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not ctypes.windll.version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        raise RuntimeError("Cannot read the Qt PDF version information")
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    pdf_version = f"{words[2] >> 16}.{words[2] & 65535}.{words[3] >> 16}"
    import pymupdf
    import ssl
    import numpy
    from lxml import etree
    from cryptography.hazmat.backends.openssl.backend import backend
    return {"Qt": qVersion(), "PySide6/Shiboken6": pyside_version,
            "Qt PDF/WebEngine (including PDFium/Chromium)": pdf_version,
            "MuPDF (including bundled third-party libraries)": pymupdf.mupdf_version,
            "CPython": ".".join(map(str, sys.version_info[:3])),
            "CPython OpenSSL": ssl.OPENSSL_VERSION.split()[1],
            "cryptography OpenSSL": backend.openssl_version_text().split()[1],
            "libxml2": ".".join(map(str, etree.LIBXML_COMPILED_VERSION)),
            "libxslt": ".".join(map(str, etree.LIBXSLT_COMPILED_VERSION)),
            "scipy-openblas64 build": numpy.show_config(mode="dicts")["Build Dependencies"]["blas"]["version"]}


def extra_native_sources(records, installed, versions):
    """Include sources not contained in the Python-package source archives."""
    result = []
    for component in ("CPython OpenSSL", "cryptography OpenSSL"):
        version = versions[component]
        filename = f"openssl-{version}.tar.gz"
        if not any(item["filename"] == filename for item in result):
            result.append({"component": component, "version": version, "filename": filename,
                           "url": f"https://github.com/openssl/openssl/releases/download/openssl-{version}/{filename}"})
    for component in ("libxml2", "libxslt"):
        version = versions[component]
        filename = f"{component}-{version}.tar.xz"
        result.append({"component": component, "version": version, "filename": filename,
                       "url": f"https://download.gnome.org/sources/{component}/{'.'.join(version.split('.')[:2])}/{filename}"})
    version = versions["scipy-openblas64 build"]
    tag = f"v{version}"
    submodule = json.loads(read_url(f"https://api.github.com/repos/MacPython/openblas-libs/contents/OpenBLAS?ref={tag}"))
    commit = submodule["sha"]
    result += [
        {"component": "scipy-openblas64 build", "version": version,
         "filename": f"openblas-libs-{version}.tar.gz",
         "url": f"https://codeload.github.com/MacPython/openblas-libs/tar.gz/refs/tags/{tag}"},
        {"component": "OpenBLAS/LAPACK", "version": commit,
         "filename": f"OpenBLAS-{commit}.tar.gz",
         "url": f"https://codeload.github.com/OpenMathLib/OpenBLAS/tar.gz/{commit}"},
    ]
    # Wheel SBOM gives exact crate versions and registry SHA-256 checksums.
    crypto = installed["cryptography"]
    sbom = next(file for file in crypto.files if str(file).endswith("sboms/cryptography-rust.cyclonedx.json"))
    for component in json.loads(Path(crypto.locate_file(sbom)).read_text())["components"]:
        if not component.get("purl", "").startswith("pkg:cargo/") or "file://" in component["purl"]:
            continue  # Local cryptography crates are already in its source archive.
        name, version = component["name"], component["version"]
        sha = next(item["content"] for item in component["hashes"] if item["alg"] == "SHA-256")
        filename = f"{name}-{version}.crate"
        result.append({"component": f"Rust crate: {name}", "version": version, "filename": filename,
                       "url": f"https://static.crates.io/crates/{name}/{filename}", "sha256": sha})
    # CPython's Windows build references exact external-source tags, including
    # its Windows-specific patches. Skip GUI toolkits and build tools.
    record = next(item for item in records if item["component"] == "CPython")
    archive = download(record["url"], record["filename"], record.get("sha256"))
    with tarfile.open(archive) as source:
        member = next(item for item in source if item.name.endswith("PCbuild/get_externals.bat"))
        script = source.extractfile(member).read().decode()
    for tag in re.findall(r"set libraries=%libraries%\s+([\w.-]+)", script):
        if tag.startswith(("tcl-", "tk-")):
            continue
        name, version = tag.rsplit("-", 1)
        result.append({"component": f"CPython external: {name}", "version": version,
                       "filename": f"cpython-source-deps-{tag}.tar.gz",
                       "url": f"https://codeload.github.com/python/cpython-source-deps/tar.gz/refs/tags/{tag}"})
    return result


def source_records(installed, offline=False):
    versions = native_versions()
    catalog = CACHE / "source-catalog.json"
    if offline and catalog.is_file():
        records = json.loads(catalog.read_text(encoding="utf-8"))
        cached_native = CACHE / "native-versions.json"
        if not cached_native.is_file() or json.loads(cached_native.read_text()) != versions:
            raise RuntimeError("Cached native sources differ from the build libraries; prepare online again")
        source_versions = {canonicalize_name(item["component"]): item["version"] for item in records}
        for name, package in installed.items():
            if name.startswith("pyside6") or name == "shiboken6":
                continue
            if source_versions.get(name) != package.version:
                raise RuntimeError(f"Cached source version differs from {name} {package.version}")
        for component in ("Qt", "PySide6/Shiboken6", "Qt PDF/WebEngine (including PDFium/Chromium)",
                          "MuPDF (including bundled third-party libraries)", "CPython"):
            if component.startswith("Qt PDF/") and versions[component] == versions["Qt"]:
                continue  # Included in the unified Qt archive for older releases.
            if source_versions.get(canonicalize_name(component)) != versions[component]:
                raise RuntimeError(f"Cached source version differs from {component}")
        return records
    records = qt_source_records(versions["Qt"], versions["PySide6/Shiboken6"],
                                versions["Qt PDF/WebEngine (including PDFium/Chromium)"], offline)
    mupdf_version = versions["MuPDF (including bundled third-party libraries)"]
    records.append({"component": "MuPDF (including bundled third-party libraries)", "version": mupdf_version,
                    "url": f"https://mupdf.com/downloads/archive/mupdf-{mupdf_version}-source.tar.gz",
                    "filename": f"mupdf-{mupdf_version}-source.tar.gz"})
    for name, package in sorted(installed.items()):
        if name.startswith("pyside6") or name == "shiboken6":
            continue  # Covered by the Qt and PySide source archives above.
        upstream_version='1.28.2' if name=='pymupdf' and package.version=='1.28.2.post1285' else package.version
        index = CACHE / f"{name}-{upstream_version}-pypi.json"
        if offline:
            data = json.loads(index.read_text(encoding="utf-8"))
        else:
            data = json.loads(read_url(f"https://pypi.org/pypi/{name}/{upstream_version}/json"))
            index.write_text(json.dumps(data), encoding="utf-8")
        archives = [file for file in data["urls"] if file["packagetype"] == "sdist"]
        if len(archives) != 1:
            raise RuntimeError(f"An exact upstream sdist is required for {name} {package.version}")
        archive = archives[0]
        records.append({"component": name, "version": package.version, "url": archive["url"],
                        "filename": archive["filename"], "sha256": archive["digests"]["sha256"]})
        if upstream_version!=package.version:
            records[-1]['upstream_version']=upstream_version
            records[-1]['derivation']='scripts/prepare_patched_mupdf.py; scripts/build_patched_mupdf.ps1; MuPDF 1.28.5'
    python_version = ".".join(map(str, sys.version_info[:3]))
    records.append({"component": "CPython", "version": python_version,
                    "url": f"https://www.python.org/ftp/python/{python_version}/Python-{python_version}.tar.xz",
                    "filename": f"Python-{python_version}.tar.xz"})
    # Preserve the exact OCR repository, including its data, LICENSE and scripts.
    records.append({"component": "tessdata_fast", "version": "4.1.0",
                    "url": "https://codeload.github.com/tesseract-ocr/tessdata_fast/tar.gz/refs/tags/4.1.0",
                    "filename": "tessdata_fast-4.1.0.tar.gz"})
    records.extend(extra_native_sources(records, installed, versions))
    return records


def is_notice(name):
    base = Path(name).name.lower()
    return ("licenses" in Path(name).parts or "LICENSES" in Path(name).parts
            or base.startswith(("license", "licence", "copying", "copyright", "notice"))
            or base in ("qt_attribution.json", "readme.chromium", "ofl.txt"))


def copy_source_notices(archive, records, origin=None, depth=0):
    """Read members without extracting paths; include MuPDF's nested archive."""
    origin = origin or archive.name
    with tarfile.open(archive, mode="r|*") as source:
        for member in source:
            if not member.isfile():
                continue
            basename = Path(member.name).name
            nested = (depth == 0 and "mupdf" in basename.lower()
                      and basename.lower().endswith((".tar.gz", ".tgz", ".tar.xz")))
            if nested:
                folder = CACHE / "nested"
                folder.mkdir(exist_ok=True)
                path = folder / basename
                with source.extractfile(member) as stream, path.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                copy_source_notices(path, records, origin + "/" + member.name, depth + 1)
            elif is_notice(member.name):
                if member.size > 16 * 1024 * 1024:
                    raise RuntimeError(f"Unexpectedly large upstream notice: {member.name}")
                content = source.extractfile(member).read()
                sha = hashlib.sha256(content).hexdigest()
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", basename)[:80]
                target = LEGAL / "upstream" / (sha[:20] + "-" + safe_name)
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(content)
                records.append({"archive": origin, "path": member.name,
                                "license_file": target.relative_to(LEGAL).as_posix(), "sha256": sha})


def project_files():
    result = [ROOT / name for name in SOURCE_FILES]
    for name in SOURCE_DIRS:
        for path in (ROOT / name).rglob("*"):
            if not path.is_file() or any(part == "__pycache__" or part.endswith(".egg-info") for part in path.parts):
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            if not path.resolve().is_relative_to(ROOT):
                raise RuntimeError(f"Source file escapes the project: {path}")
            result.append(path)
    return sorted(set(result))


def prepare(project, installed, offline=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    LEGAL.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_TEXTS:
        text = ROOT / "licenses" / (name + ".txt")
        if not text.is_file():
            raise RuntimeError(f"Missing license text: {name}")
        shutil.copyfile(text, LEGAL / text.name)
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "SOURCE_CODE.md"):
        shutil.copyfile(ROOT / name, LEGAL / name)
    notices = []
    for name, package in sorted(installed.items()):
        for file in package.files or ():
            if (is_notice(str(file)) or "/sboms/" in str(file)) and ".dist-info/" in str(file).replace("\\", "/"):
                original = Path(package.locate_file(file))
                target = LEGAL / "packages" / name / Path(*str(file).replace("\\", "/").split("/")[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
    sources = source_records(installed, offline)
    def fetch(record):
        archive = download(record["url"], record["filename"], record.get("sha256"), offline)
        record["sha256"] = digest(archive)
        record["size"] = archive.stat().st_size
        return archive
    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(pool.map(fetch, sources))
    old_catalog = CACHE / "source-catalog.json"
    notice_index = LEGAL / "upstream-notices.json"
    schema_file = LEGAL / "notice-schema.json"
    if (old_catalog.is_file() and notice_index.is_file() and schema_file.is_file()
            and json.loads(schema_file.read_text()) == NOTICE_SCHEMA and json.loads(old_catalog.read_text()) == sources):
        notices = json.loads(notice_index.read_text(encoding="utf-8"))
        for record in notices:
            if digest(LEGAL / record["license_file"]) != record["sha256"]:
                raise RuntimeError("Cached upstream license notice was changed")
    else:
        for archive in archives:
            print(f"[sources] Collect notices: {archive.name}", flush=True)
            copy_source_notices(archive, notices)
    old_catalog.write_text(json.dumps(sources, indent=2), encoding="utf-8")
    schema_file.write_text(json.dumps(NOTICE_SCHEMA), encoding="utf-8")
    native = native_versions()
    (CACHE / "native-versions.json").write_text(json.dumps(native, indent=2), encoding="utf-8")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    shutil.copyfile(python_license, LEGAL / "Python-LICENSE.txt")
    (LEGAL / "upstream-notices.json").write_text(json.dumps(notices, indent=2), encoding="utf-8")
    inventory = {"application": "APE PDF Studio", "package": project["name"],
                 "version": project["version"], "license": project["license"],
                 "python": ".".join(map(str, sys.version_info[:3])), "platform": "Windows x64",
                 "native_libraries": native,
                 "dependencies": [{"name": name, "version": package.version,
                                   "license": package.metadata.get("License-Expression") or package.metadata.get("License"),
                                   "urls": package.metadata.get_all("Project-URL", [])}
                                  for name, package in sorted(installed.items())]}
    (LEGAL / "dependency-manifest.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    manifest = {"version": project["version"], "native_versions": native, "archives": sources,
                "application_files": {str(path.relative_to(ROOT).as_posix()): digest(path) for path in project_files()}}
    (LEGAL / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[sources] Prepared {len(sources)} upstream archives and {len(notices)} notices", flush=True)


def verify_legal(bundle):
    folder = Path(bundle) / "licenses"
    inventory = json.loads((folder / "dependency-manifest.json").read_text(encoding="utf-8"))
    for name in REQUIRED_TEXTS:
        path = folder / (name + ".txt")
        if not path.is_file() or path.stat().st_size < 5000:
            raise RuntimeError(f"Missing full license text: {name}")
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in (folder / "LICENSE").read_text(encoding="utf-8"):
        raise RuntimeError("The bundle does not include the canonical AGPL license")
    for record in json.loads((folder / "upstream-notices.json").read_text(encoding="utf-8")):
        if digest(folder / record["license_file"]) != record["sha256"]:
            raise RuntimeError("Upstream notice was changed or lost")
    allowed = {item["name"]: item["version"] for item in inventory["dependencies"]}
    application_package = canonicalize_name(inventory.get("package", "ape-pdf-studio"))
    allowed[application_package] = inventory["version"]
    if any((Path(bundle) / "_internal").rglob("opencv_videoio_ffmpeg*.dll")):
        raise RuntimeError("Unused FFmpeg video helper must not be redistributed in the PDF bundle")
    for info in (Path(bundle) / "_internal").glob("*.dist-info"):
        package = metadata.PathDistribution(info)
        name = canonicalize_name(package.metadata["Name"])
        if name not in allowed or allowed[name] != package.version:
            raise RuntimeError(f"Unexpected/stale bundled metadata: {name} {package.version}")
        if name == application_package and package.metadata.get("License-Expression") != inventory["license"]:
            raise RuntimeError("Bundled application metadata does not declare the application license")
    return inventory


def release(bundle, output, installer=None):
    inventory = verify_legal(bundle)
    manifest = json.loads((LEGAL / "source-manifest.json").read_text(encoding="utf-8"))
    bundled_manifest = json.loads((Path(bundle) / "licenses/source-manifest.json").read_text(encoding="utf-8"))
    if manifest != bundled_manifest or manifest["version"] != inventory["version"]:
        raise RuntimeError("Source manifest differs from the actual bundle; rebuild before releasing")
    current = {path.relative_to(ROOT).as_posix(): digest(path) for path in project_files()}
    if current != manifest["application_files"]:
        raise RuntimeError("Application source changed since license preparation; rebuild before releasing")
    version = inventory["version"]
    output.mkdir(parents=True, exist_ok=True)
    application_archive = output / f"APEPDFStudio-{version}-Source.zip"
    dependencies_archive = output / f"APEPDFStudio-{version}-DependencySources.zip"
    if installer:
        sums = output / f"APEPDFStudio-{version}-SHA256SUMS.txt"
        expected = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in sums.read_text().splitlines()}
        for path in (application_archive, dependencies_archive):
            if digest(path) != expected[path.name]:
                raise RuntimeError("Prepared source asset was changed")
        sums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in
                                (installer, application_archive, dependencies_archive)), encoding="ascii")
        (output / "RELEASE_NOTES.md").write_text(
            f"# APE PDF Studio {version}\n\n"
            "PDF reader and editor by GiancaGIS, licensed under **GNU AGPLv3 only**.\n\n"
            "Windows x64; includes the GUI, CLI, optional Explorer previews, and local Italian/English OCR.\n\n"
            "Download the installer and its matching corresponding source from this release:\n\n"
            f"- `{installer.name}`\n- `{application_archive.name}`\n- `{dependencies_archive.name}`\n"
            f"- `{sums.name}`\n\n"
            "The source ZIPs include application/native source, build scripts, the exact dependency lock, "
            "and original upstream source archives. Third-party components retain their own licenses. "
            "See LICENSE, THIRD_PARTY_NOTICES.md and SOURCE_CODE.md.\n\n"
            "Source downloads are provided at no additional charge. Preserve these assets with the installer.\n",
            encoding="utf-8")
        print(f"[release] Installer and source checksums finalized: {output}", flush=True)
        return
    with zipfile.ZipFile(application_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in project_files():
            archive.write(path, f"APE-PDF-Studio-{version}/" + path.relative_to(ROOT).as_posix())
        for name in ("source-manifest.json", "dependency-manifest.json"):
            archive.write(LEGAL / name, f"APE-PDF-Studio-{version}/" + name)
    with zipfile.ZipFile(dependencies_archive, "w", compression=zipfile.ZIP_STORED) as archive:
        for record in manifest["archives"]:
            path = CACHE / record["filename"]
            if digest(path) != record["sha256"]:
                raise RuntimeError(f"Dependency source changed: {path.name}")
            archive.write(path, "sources/" + path.name)
        for name in ("source-manifest.json", "dependency-manifest.json", "SOURCE_CODE.md"):
            archive.write(LEGAL / name, name)
    if dependencies_archive.stat().st_size >= 2 * 1024**3:
        raise RuntimeError("Dependency sources exceed the GitHub per-asset limit; split before publication")
    checksums = output / f"APEPDFStudio-{version}-SHA256SUMS.txt"
    checksums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in (application_archive, dependencies_archive)), encoding="ascii")
    print(f"[release] Source assets: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="Create release assets for an already built bundle")
    parser.add_argument("--installer", type=Path, help="Finalize checksums/release notes after compiling the installer")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/release")
    parser.add_argument("--offline", action="store_true", help="Require verified sources already in the local cache")
    parser.add_argument("--freeze-requirements", action="store_true", help="Record this environment's exact release dependencies")
    parser.add_argument("--seed-source-manifest", type=Path, help="Verify source archives unpacked from a release for offline use")
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["license"] != "AGPL-3.0-only":
        raise RuntimeError("Release metadata must declare AGPL-3.0-only")
    if args.seed_source_manifest:
        manifest = json.loads(args.seed_source_manifest.read_text(encoding="utf-8"))
        for record in manifest["archives"]:
            filename = record["filename"]
            if Path(filename).name != filename or digest(CACHE / filename) != record["sha256"]:
                raise RuntimeError(f"Release source hash mismatch: {filename}")
            (CACHE / (filename + ".sha256")).write_text(record["sha256"] + "\n", encoding="ascii")
        (CACHE / "source-catalog.json").write_text(json.dumps(manifest["archives"], indent=2), encoding="utf-8")
        (CACHE / "native-versions.json").write_text(json.dumps(manifest["native_versions"], indent=2), encoding="utf-8")
        print("[sources] Verified release archives; offline source preparation is available")
        return
    if args.bundle:
        release(args.bundle.resolve(), args.output.resolve(), args.installer.resolve() if args.installer else None)
        return
    installed = packages(project)
    if args.freeze_requirements:
        lines = ["# Exact Windows release runtime/build dependencies; generated by prepare_release.py.",
                 f"# CPython {'.'.join(map(str, sys.version_info[:3]))}; application {project['version']}."]
        lines += [f"{name}=={package.version}" for name, package in sorted(installed.items())]
        (ROOT / "requirements-release.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        lock_text = (ROOT / "requirements-release.txt").read_text()
        python_pin = re.search(r"^# CPython ([0-9.]+);", lock_text, re.MULTILINE)
        if not python_pin or python_pin[1] != ".".join(map(str, sys.version_info[:3])):
            raise RuntimeError("Release interpreter differs from the CPython version recorded in the lock")
        locked = set()
        for line in lock_text.splitlines():
            if line and not line.startswith("#"):
                requirement = Requirement(line)
                locked.add(canonicalize_name(requirement.name))
                if installed[canonicalize_name(requirement.name)].version not in requirement.specifier:
                    raise RuntimeError(f"Release environment differs from the lock: {line}")
        if locked != set(installed):
            raise RuntimeError("Release dependency set differs from the lock; regenerate it intentionally")
    prepare(project, installed, args.offline)


if __name__ == "__main__":
    main()
