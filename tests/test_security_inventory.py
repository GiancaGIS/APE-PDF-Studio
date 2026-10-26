"""Authenticated advisory checks must preserve reports and fail on missing data."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler

import pytest


spec = importlib.util.spec_from_file_location(
    "security_inventory", Path(__file__).resolve().parents[1] / "scripts/security_inventory.py"
)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(inventory, "inventory", lambda: {"pypdf": "6.21.2", "MuPDF": "1.28.5"})


def command(monkeypatch, tmp_path, *options):
    output = tmp_path / "reports" / "inventory.json"
    monkeypatch.setattr(sys, "argv", ["security_inventory.py", "--output", str(output), *options])
    return output


@pytest.mark.parametrize("token", [None, "", "  synthetic-token  "])
def test_public_feed_uses_environment_token_when_available(monkeypatch, token):
    if token is not None:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 20
        return io.BytesIO(b"[]")
    monkeypatch.setattr(inventory, "urlopen", open_request)
    assert inventory.advisories("py-pdf/pypdf") == []
    request = requests[0]
    assert request.get_header("Authorization") == ("Bearer synthetic-token" if token else None)
    assert request.full_url == "https://api.github.com/repos/py-pdf/pypdf/security-advisories?state=published&per_page=100"


def test_complete_report_contains_both_feeds(monkeypatch, tmp_path):
    output = command(monkeypatch, tmp_path, "--upstream")
    body = [{"ghsa_id": "GHSA-synthetic", "html_url": "https://github.com/advisories/GHSA-synthetic",
             "published_at": "2026-10-17T00:00:00Z", "vulnerabilities": []}]
    monkeypatch.setattr(inventory, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(body).encode()))
    inventory.main()
    report = json.loads(output.read_text())
    assert set(report["advisories"]) == set(inventory.ADVISORY_REPOSITORIES)
    assert report["advisories"]["py-pdf/pypdf"][0]["id"] == "GHSA-synthetic"
    assert report["upstream_check"] == {"status": "complete", "errors": {}, "unchecked_repositories": []}


def test_redirect_cannot_forward_ci_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-secret")
    def redirect(request, **kwargs):
        redirected = HTTPRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://example.invalid/redirect"
        )
        assert request.get_header("Authorization") == "Bearer synthetic-secret"
        assert redirected.get_header("Authorization") is None
        return io.BytesIO(b"[]")
    monkeypatch.setattr(inventory, "urlopen", redirect)
    assert inventory.advisories("py-pdf/pypdf") == []


@pytest.mark.parametrize("status,headers,body", [
    (403, {"X-RateLimit-Remaining": "0"}, b"{}"),
    (403, {}, b'{"message":"API rate limit exceeded"}'),
    (403, {"Retry-After": "60"}, b"{}"),
    (429, {}, b"{}"),
])
def test_rate_limit_preserves_inventory_and_stops_requests(monkeypatch, tmp_path, capsys, status, headers, body):
    output = command(monkeypatch, tmp_path, "--upstream")
    requests = []
    def limited(request, **kwargs):
        requests.append(request)
        raise HTTPError(request.full_url, status, "rate limit exceeded", headers, io.BytesIO(body))
    monkeypatch.setattr(inventory, "urlopen", limited)
    with pytest.raises(SystemExit, match="incomplete"):
        inventory.main()
    report = json.loads(output.read_text())
    assert report["pypdf"] == "6.21.2" and report["MuPDF"] == "1.28.5"
    assert report["advisories"] == {} and len(requests) == 1
    check = report["upstream_check"]
    assert check["status"] == "incomplete"
    assert check["unchecked_repositories"] == list(inventory.ADVISORY_REPOSITORIES)
    assert check["errors"]["py-pdf/pypdf"]["rate_limited"] is True
    assert check["errors"]["py-pdf/pypdf"]["http_status"] == status
    assert "GITHUB_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_http_failure_keeps_completed_feed_and_does_not_expose_token(monkeypatch, tmp_path, capsys, status):
    output = command(monkeypatch, tmp_path, "--upstream")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-secret")
    def open_request(request, **kwargs):
        if "/py-pdf/pypdf/" in request.full_url:
            return io.BytesIO(b"[]")
        raise HTTPError(request.full_url, status, "synthetic-secret", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(inventory, "urlopen", open_request)
    with pytest.raises(SystemExit, match="incomplete"):
        inventory.main()
    text = output.read_text()
    report = json.loads(text)
    assert report["advisories"] == {"py-pdf/pypdf": []}
    assert report["upstream_check"]["unchecked_repositories"] == ["pymupdf/PyMuPDF"]
    assert report["upstream_check"]["errors"]["pymupdf/PyMuPDF"]["http_status"] == status
    assert "synthetic-secret" not in text + capsys.readouterr().err


@pytest.mark.parametrize("payload", [b"not-json", b"{}", b"[{}]", b"[null]", b"x" * (inventory.MAX_ADVISORY_BYTES + 1)],
                         ids=["invalid-json", "object", "missing-fields", "null-advisory", "oversized"])
def test_invalid_feed_cannot_be_reported_as_empty_or_successful(monkeypatch, payload):
    monkeypatch.setattr(inventory, "urlopen", lambda *a, **kw: io.BytesIO(payload))
    with pytest.raises(inventory.AdvisoryError, match="invalid or oversized"):
        inventory.advisories("py-pdf/pypdf")


@pytest.mark.parametrize("error", [URLError("synthetic-secret"), TimeoutError(), OSError()])
def test_network_failure_is_reported_without_leaking_details(monkeypatch, error):
    def unavailable(*args, **kwargs):
        raise error
    monkeypatch.setattr(inventory, "urlopen", unavailable)
    with pytest.raises(inventory.AdvisoryError, match="Cannot reach") as exc:
        inventory.advisories("py-pdf/pypdf")
    assert "synthetic-secret" not in str(exc.value)


def test_offline_release_inventory_does_not_query_github(monkeypatch, tmp_path):
    output = command(monkeypatch, tmp_path, "--release")
    monkeypatch.setattr(inventory, "urlopen", lambda *a, **kw: pytest.fail("Unexpected network request"))
    inventory.main()
    assert json.loads(output.read_text()) == {"pypdf": "6.21.2", "MuPDF": "1.28.5"}


@pytest.mark.parametrize('pypdf,mupdf', [('6.20.1', '1.28.5'), ('6.21.0', '1.28.5'),
                                       ('6.21.2rc1', '1.28.5'), ('6.21.2', '1.28.4')])
def test_release_gate_rejects_versions_before_security_fixes(monkeypatch, tmp_path, pypdf, mupdf):
    output = command(monkeypatch, tmp_path, '--release')
    monkeypatch.setattr(inventory, 'inventory', lambda: {'pypdf': pypdf, 'MuPDF': mupdf})
    with pytest.raises(SystemExit, match='Release blocked'):
        inventory.main()
    assert json.loads(output.read_text()) == {'pypdf': pypdf, 'MuPDF': mupdf}
