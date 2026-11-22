"""Version comparisons and untrusted release metadata, without network access."""
import json

import pytest

from ape_pdf_studio.core.updates import MAX_INSTALLER_BYTES, MAX_RESPONSE_BYTES, RELEASES_URL, UpdateError, parse_release, stable_version


def body(tag="v1.2.0", **values):
    return json.dumps({"tag_name": tag, "draft": False, "prerelease": False, **values}).encode()


@pytest.mark.parametrize("tag", [None, 123, "", "1.0", "01.2.3", "1.2.3-rc.1", "v1.2.3\n", "<img>", "1.2.3/../../other", "9" * 129])
def test_invalid_or_preview_version_is_rejected(tag):
    with pytest.raises(UpdateError):
        parse_release(200, body(tag))


@pytest.mark.parametrize("payload", [b"not json", b"[]", b"null", b"{}", body(draft=True), body(prerelease=True), body(draft="false"), b"x" * (MAX_RESPONSE_BYTES + 1)],
                         ids=["invalid-json", "array", "null", "empty", "draft", "preview", "invalid-flags", "oversized"])
def test_invalid_payload_cannot_enable_an_update(payload):
    with pytest.raises(UpdateError):
        parse_release(200, payload)


@pytest.mark.parametrize("status", [301, 302, 403, 429, 500, None])
def test_http_failure_is_not_reported_as_up_to_date(status):
    with pytest.raises(UpdateError):
        parse_release(status, body())


def test_missing_public_release_has_a_distinct_result():
    assert parse_release(404, b"not a release") is None


def test_release_links_are_built_from_the_fixed_repository():
    release = parse_release(200, body("v1.2.3+build", html_url="file:///C:/evil.exe", assets=[{"browser_download_url": "https://evil.invalid/"}]))
    assert release.url == RELEASES_URL + "/tag/v1.2.3%2Bbuild"
    assert stable_version("v1.2.3+build") == (1, 2, 3)


def installer_asset(version="1.10.0", **changes):
    name = f"APEPDFStudio-{version}-Setup-x64.exe"
    return {"name": name, "state": "uploaded", "size": 123, "digest": "sha256:" + "a" * 64,
            "browser_download_url": f"{RELEASES_URL}/download/v{version}/{name}", **changes}


def test_only_newer_release_exposes_matching_installer():
    data = body("v1.10.0", assets=[installer_asset(), installer_asset("1.2.3")])
    asset = parse_release(200, data, current="1.3.1").installer
    assert asset.sha256 == "a" * 64 and asset.size == 123
    assert asset.url == installer_asset()["browser_download_url"]
    assert parse_release(200, data, current="1.10.0").installer is None


@pytest.mark.parametrize("changes", [
    {"state": "new"}, {"size": True}, {"size": 0}, {"size": MAX_INSTALLER_BYTES + 1},
    {"digest": None}, {"digest": "sha256:bad"}, {"browser_download_url": "https://invalid.example/setup.exe"},
    {"name": "APE-PDF-Studio.msi"},
])
def test_invalid_installer_keeps_manual_release_available(changes):
    release = parse_release(200, body("v1.10.0", assets=[installer_asset(**changes)]), current="1.3.1")
    assert release.update_available and release.installer is None


def test_ambiguous_installer_asset_is_ignored():
    release = parse_release(200, body("v1.10.0", assets=[installer_asset()] * 2), current="1.3.1")
    assert release.installer is None
