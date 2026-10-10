"""Static resource restrictions and clipping in the PDF-to-vector display list."""
import pytest

from ape_pdf_studio.core.vector_page import VectorUnsupported, clip_commands, prepare_svg


def svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100" '
            f'viewBox="0 0 100 100">{body}</svg>').encode()


@pytest.mark.parametrize("body", [
    '<script>alert(1)</script>',
    '<image xlink:href="file:///secret.png"/>',
    '<use xlink:href="https://example.com/a.svg#glyph"/>',
    '<defs><g id="cycle"><use xlink:href="#cycle"/></g></defs><use xlink:href="#cycle"/>',
    '<path d="M0 0L10 10" style="mix-blend-mode:multiply"/>',
    '<g filter="url(#filter)"><path d="M0 0L10 10"/></g>',
    '<g opacity="0.5"><path d="M0 0L10 10"/></g>',
    '<g clip-path="url(file:///clip.svg)"><path d="M0 0L10 10"/></g>',
])
def test_unsupported_features_never_produce_partial_vector_pages(body):
    with pytest.raises(VectorUnsupported):
        prepare_svg(svg(body))


def test_entities_and_oversized_svg_are_rejected(monkeypatch):
    with pytest.raises(VectorUnsupported):
        prepare_svg(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///secret">]>' + svg("&x;"))
    monkeypatch.setattr("ape_pdf_studio.core.vector_page.MAX_SVG_BYTES", 10)
    with pytest.raises(VectorUnsupported):
        prepare_svg(svg(""))


def test_layers_preserve_order_nested_clipping_and_glyph_references():
    data, layers = prepare_svg(svg('''
      <defs><path id="glyph" d="M0 0L5 0L5 5Z"/>
      <clipPath id="clip"><path transform="matrix(1,0,0,1,10,20)" d="M0 0H30V40H0Z" clip-rule="evenodd"/></clipPath></defs>
      <path d="M0 0L100 100"/>
      <g transform="matrix(2,0,0,2,0,0)" clip-path="url(#clip)"><use xlink:href="#glyph"/></g>
      <path d="M5 5L20 20"/>
    '''))
    assert len(layers) == 3
    assert not layers[0]["clips"] and not layers[2]["clips"]
    clip = layers[1]["clips"][0]
    assert clip["transforms"] == [[2, 0, 0, 2, 0, 0], [1, 0, 0, 1, 10, 20]]
    assert clip["rule"] == "evenodd"
    assert b"clip-path" not in data and b"clipPath" not in data
    assert b'xlink:href="#glyph"' in data


@pytest.mark.parametrize("path", ["Mnan 0Z", "M0 0A10 10 0 0 1 20 20Z", "M0 0C1 2 3", "M0 0HinfZ", "M1e999 0Z"])
def test_unknown_or_nonfinite_clip_grammar_is_rejected(path):
    with pytest.raises(VectorUnsupported):
        clip_commands(path)
