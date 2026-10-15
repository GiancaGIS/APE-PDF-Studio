"""Only validated, explicit web/email links can be offered for confirmation."""
import pytest
from pypdf import PdfReader

from ape_pdf_studio.core.pdf_links import allowed_url, read_links
from test_pdf_forms import form_pdf


@pytest.mark.parametrize("url", ["https://www.python.org/", "http://example.com:8080/path?q=1#part",
    "mailto:info@example.com?subject=Hello%20world", "HTTPS://example.com/path", "https://xn--bcher-kva.example/"])
def test_allowed_links(url):
    assert allowed_url(url)


@pytest.mark.parametrize("url", ["file:///C:/test.exe", "javascript:alert(1)", "data:text/html,test", "ms-settings:privacy",
    "https:", "https:///missing", "https://example.com:bad/", "https://example.com:99999/", "https://user@example.com/",
    "https://example.com\\@evil.example/", "https://example.com/%0d%0a", "https://example.com/%2500",
    "https://example.com/\u202eexe", "https://example.com/a b", "mailto:info@example.com?attach=C:/private.pdf",
    "mailto:info@example.com?subject=%0aInjected", "mailto:", "mailto://host@example.com", "../file.pdf", "", None])
def test_unsafe_and_ambiguous_links(url):
    assert allowed_url(url) is None


def test_example_has_two_explicit_links(form_pdf):
    with PdfReader(form_pdf) as reader:
        links = read_links(reader)
        assert len(links[0]) == 2
        assert links[0][0]["url"] == "https://www.python.org/"
        assert all(link["rect"][2] > link["rect"][0] for link in links[0])
