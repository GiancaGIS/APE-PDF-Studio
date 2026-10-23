"""Release checks must reject corrupt caches and sources from another build."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
from types import SimpleNamespace
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("prepare_release", Path(__file__).resolve().parents[1] / "scripts/prepare_release.py")
release_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_tools)


def test_offline_cache_rejects_changed_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(release_tools, "CACHE", tmp_path)
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"original source")
    checksum = release_tools.digest(archive)
    assert release_tools.download("https://example.invalid/source", archive.name, checksum, offline=True) == archive
    archive.write_bytes(b"changed source")
    with pytest.raises(RuntimeError, match="Missing or corrupt"):
        release_tools.download("https://example.invalid/source", archive.name, checksum, offline=True)


def test_download_rejects_wrong_upstream_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(release_tools, "CACHE", tmp_path)
    monkeypatch.setattr(release_tools.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"unexpected bytes"))
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        release_tools.download("https://example.invalid/source", "source.tar.gz", "0" * 64)
    assert not list(tmp_path.glob("*.partial"))
    assert not (tmp_path / "source.tar.gz").exists()


def test_archive_notices_do_not_extract_untrusted_paths(tmp_path, monkeypatch):
    legal = tmp_path / "legal"
    legal.mkdir()
    monkeypatch.setattr(release_tools, "LEGAL", legal)
    archive = tmp_path / "upstream.tar.gz"
    content = b"Upstream copyright and license\n"
    with tarfile.open(archive, "w:gz") as output:
        for name in ("../../LICENSE", "/absolute/NOTICE", "project/LICENSES/BSD-3-Clause.txt", "project/source.py"):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            output.addfile(member, io.BytesIO(content))
    records = []
    release_tools.copy_source_notices(archive, records)
    assert len(records) == 3
    for record in records:
        target = legal / record["license_file"]
        assert target.resolve().is_relative_to(legal)
        assert target.read_bytes() == content
        assert release_tools.digest(target) == record["sha256"]
    assert not (tmp_path / "LICENSE").exists()


def test_offline_catalog_rejects_different_native_libraries(tmp_path, monkeypatch):
    monkeypatch.setattr(release_tools, "CACHE", tmp_path)
    monkeypatch.setattr(release_tools, "native_versions", lambda: {"Qt": "6.12.0"})
    (tmp_path / "source-catalog.json").write_text("[]")
    (tmp_path / "native-versions.json").write_text('{"Qt": "6.11.0"}')
    with pytest.raises(RuntimeError, match="Cached native sources differ"):
        release_tools.source_records({}, offline=True)


@pytest.fixture
def small_release(tmp_path, monkeypatch):
    root, legal, cache, bundle = [tmp_path / name for name in ("project", "legal", "cache", "bundle")]
    for path in (root, legal, cache, bundle / "licenses"):
        path.mkdir(parents=True)
    source = root / "app.py"
    source.write_text("print('release')\n")
    upstream = cache / "upstream.tar.gz"
    upstream.write_bytes(b"original upstream archive")
    manifest = {"version": "1.1.4", "application_files": {"app.py": release_tools.digest(source)},
                "archives": [{"filename": upstream.name, "sha256": release_tools.digest(upstream)}]}
    for path in (legal, bundle / "licenses"):
        (path / "source-manifest.json").write_text(json.dumps(manifest))
    (legal / "dependency-manifest.json").write_text('{"version": "1.1.4"}')
    (legal / "SOURCE_CODE.md").write_text("Rebuild instructions")
    monkeypatch.setattr(release_tools, "ROOT", root)
    monkeypatch.setattr(release_tools, "LEGAL", legal)
    monkeypatch.setattr(release_tools, "CACHE", cache)
    monkeypatch.setattr(release_tools, "project_files", lambda: [source])
    monkeypatch.setattr(release_tools, "verify_legal", lambda path: {"version": "1.1.4"})
    return SimpleNamespace(root=root, legal=legal, cache=cache, bundle=bundle, manifest=manifest, output=tmp_path / "release")


def test_release_refuses_source_edits_after_build(small_release):
    release = small_release
    (release.root / "app.py").write_text("different source")
    with pytest.raises(RuntimeError, match="source changed"):
        release_tools.release(release.bundle, release.output)


def test_release_refuses_manifest_from_another_bundle(small_release):
    release = small_release
    (release.bundle / "licenses/source-manifest.json").write_text('{"version": "0.9.0"}')
    with pytest.raises(RuntimeError, match="differs from the actual bundle"):
        release_tools.release(release.bundle, release.output)


def test_release_archives_and_final_checksums_match_installer(small_release):
    release = small_release
    release_tools.release(release.bundle, release.output)
    with zipfile.ZipFile(release.output / "APEPDFStudio-1.1.4-Source.zip") as archive:
        assert archive.read("APE-PDF-Studio-1.1.4/app.py") == (release.root / "app.py").read_bytes()
    with zipfile.ZipFile(release.output / "APEPDFStudio-1.1.4-DependencySources.zip") as archive:
        assert archive.read("sources/upstream.tar.gz") == (release.cache / "upstream.tar.gz").read_bytes()
    installer = release.root / "APEPDFStudio-1.1.4-Setup-x64.exe"
    installer.write_bytes(b"matching installer")
    release_tools.release(release.bundle, release.output, installer)
    sums = (release.output / "APEPDFStudio-1.1.4-SHA256SUMS.txt").read_text().splitlines()
    assert len(sums) == 3
    assert sums[0] == f"{release_tools.digest(installer)}  {installer.name}"
    assert "at no additional charge" in (release.output / "RELEASE_NOTES.md").read_text()
    with (release.output / "APEPDFStudio-1.1.4-Source.zip").open("ab") as archive:
        archive.write(b"tampering")
    with pytest.raises(RuntimeError, match="source asset was changed"):
        release_tools.release(release.bundle, release.output, installer)


def test_bundle_inventory_rejects_stale_metadata_and_video_codec(tmp_path):
    legal = tmp_path / "licenses"
    legal.mkdir()
    for name in release_tools.REQUIRED_TEXTS:
        shutil.copyfile(release_tools.ROOT / "licenses" / (name + ".txt"), legal / (name + ".txt"))
    shutil.copyfile(release_tools.ROOT / "LICENSE", legal / "LICENSE")
    (legal / "dependency-manifest.json").write_text('{"version": "1.1.4", "license": "AGPL-3.0-only", "dependencies": [{"name": "pypdf", "version": "6.19.0"}]}')
    (legal / "upstream-notices.json").write_text("[]")
    package = tmp_path / "_internal/pypdf-6.19.0.dist-info"
    package.mkdir(parents=True)
    (package / "METADATA").write_text("Metadata-Version: 2.1\nName: pypdf\nVersion: 6.19.0\n")
    application = tmp_path / "_internal/ape_pdf_studio-1.1.4.dist-info"
    application.mkdir()
    (application / "METADATA").write_text("Metadata-Version: 2.4\nName: ape-pdf-studio\nVersion: 1.1.4\nLicense-Expression: AGPL-3.0-only\n")
    release_tools.verify_legal(tmp_path)
    (application / "METADATA").write_text("Metadata-Version: 2.4\nName: ape-pdf-studio\nVersion: 1.1.4\nLicense-Expression: MIT\n")
    with pytest.raises(RuntimeError, match="application license"):
        release_tools.verify_legal(tmp_path)
    (application / "METADATA").write_text("Metadata-Version: 2.4\nName: ape-pdf-studio\nVersion: 1.1.4\nLicense-Expression: AGPL-3.0-only\n")
    (package / "METADATA").write_text("Metadata-Version: 2.1\nName: pypdf\nVersion: 5.0.0\n")
    with pytest.raises(RuntimeError, match="Unexpected/stale"):
        release_tools.verify_legal(tmp_path)
    (tmp_path / "_internal/opencv_videoio_ffmpeg500_64.dll").write_bytes(b"codec")
    with pytest.raises(RuntimeError, match="Unused FFmpeg"):
        release_tools.verify_legal(tmp_path)
