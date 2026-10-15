"""Version comparisons and untrusted release metadata, without network access."""
import json

import pytest

from ape_pdf_studio.core.updates import MAX_RESPONSE_BYTES, RELEASES_URL, UpdateError, parse_release, stable_version


def body(tag="v1.2.0", **values):
    return json.dumps({"tag_name": tag, "draft": False, "prerelease": False, **values}).encode()


@pytest.mark.parametrize("tag,current,available", [
    ("v1.2.0", "1.0.2", True), ("1.10.0", "1.9.0", True),
    ("v2.0.0", "1.99.99", True), ("1.0.2", "1.0.2", False),
    ("1.0.2", "2.0.0", False), ("v1.0.2+build.2", "1.0.2+build.1", False),
])
def test_versions_use_numeric_precedence(tag, current, available):
    release = parse_release(200, body(tag), current)
    assert release.update_available is available
    assert release.url.startswith(RELEASES_URL + "/tag/")


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
