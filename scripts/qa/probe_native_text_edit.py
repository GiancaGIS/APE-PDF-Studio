"""Compare restricted Tj/TJ substitutions and shaped RTL extraction on synthetic PDFs."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ByteStringObject, DecodedStreamObject, DictionaryObject, NameObject

from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.pdf_runtime import open_static_pdf


def sample(path, operator):
    writer = PdfWriter()
    page = writer.add_blank_page(300, 250)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Courier')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    show = b'(1234) Tj' if operator == b'Tj' else b'[(12) 15 (34)] TJ'
    stream = DecodedStreamObject()
    stream.set_data(b'0.94 g 32 155 120 42 re f 0 g BT /F1 18 Tf 48 170 Td ' + show +
                    b' ET 1 g 58 150 26 50 re f')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def substitute(source, destination, operator):
    """Trial restricted to controlled inputs with a known reversible map."""
    start = perf_counter()
    with PdfReader(source) as reader:
        font = reader.pages[0]['/Resources']['/Font']['/F1']
        assert font['/BaseFont'] == '/Courier' and '/ToUnicode' not in font and '/Encoding' not in font
        writer = PdfWriter(clone_from=reader)
        stream = writer.pages[0].get_contents()
        matches = [operands for operands, code in stream.operations if code == operator]
        assert len(matches) == 1
        operands = matches[0]
        if operator == b'Tj':
            assert operands[0].original_bytes == b'1234'
            operands[0] = ByteStringObject(b'5269')
        else:
            items = operands[0]
            assert items[0].original_bytes == b'12' and items[2].original_bytes == b'34'
            items[0], items[2] = ByteStringObject(b'52'), ByteStringObject(b'69')
        writer.pages[0].replace_contents(stream)
        writer.write(destination)
    return round((perf_counter() - start) * 1000, 2)


def extraction(path, expected):
    with open_static_pdf(path) as mupdf, PdfReader(path) as pypdf:
        result = dict(mupdf=expected in mupdf[0].get_text(), pypdf=expected in pypdf.pages[0].extract_text())
    editor = BrokerEditor(path)
    try:
        result['pdfium'] = expected in editor.call('qt_all_text', editor.path, 0)['text']
    finally:
        editor.close()
    return result


def probe(directory, operator):
    source, output = directory / 'source.pdf', directory / 'trial.pdf'
    sample(source, operator)
    elapsed = substitute(source, output, operator)
    result = extraction(output, '5269')
    with open_static_pdf(source) as before, open_static_pdf(output) as after:
        for zoom in (1, 2):
            mask = pymupdf.Rect(58, 56, 84, 100)
            matrix = pymupdf.Matrix(zoom, zoom)
            assert before[0].get_pixmap(clip=mask, matrix=matrix).samples == after[0].get_pixmap(clip=mask, matrix=matrix).samples
    assert all(result.values()), result
    return dict(milliseconds=elapsed, original_bytes=source.stat().st_size, native_bytes=output.stat().st_size,
                preserved_cover=True, extraction=result)


if __name__ == '__main__':
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    build = Path(__file__).resolve().parents[2] / 'build'
    build.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=build, prefix='text-native-') as temporary:
        directory = Path(temporary)
        results = {op.decode('ascii'): probe(directory, op) for op in (b'Tj', b'TJ')}
        shaped = directory / 'rtl.pdf'
        with open_static_pdf() as document:
            page = document.new_page()
            page.insert_htmlbox((30, 30, 340, 110), '<div dir="rtl">مرحبا بالعالم</div>')
            document.save(shaped)
        results['shaped_arabic_logical_search'] = extraction(shaped, 'مرحبا بالعالم')
    print(json.dumps(results, indent=2))
