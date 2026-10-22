"""Bounded, interoperable PDF Ink annotations. No Qt or native PDF engine."""
from uuid import uuid4

from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, TextStringObject, DecodedStreamObject)

from .document_tools import number
from .errors import PDFOperationError
from .page_coordinates import to_pdf, from_pdf, displayed_size

MAX_STROKES = 256
MAX_POINTS = 50000
# Leave room for annotation/style records and document state in the bounded IPC
# decoder (200000 nodes). Very large existing drawings remain visible in PDF.
MAX_READ_POINTS = 20000


def segments(points):
    """Quadratic midpoint smoothing shared by the live canvas and PDF appearance."""
    yield 'M', points[0]
    if len(points) == 1:
        yield 'L', (points[0][0] + .01, points[0][1])
    elif len(points) == 2:
        yield 'L', points[-1]
    else:
        for i in range(1, len(points) - 1):
            yield 'Q', (points[i], ((points[i][0] + points[i+1][0]) / 2,
                                   (points[i][1] + points[i+1][1]) / 2))
        yield 'L', points[-1]


def read_ink(reader):
    rows, total, scanned = [], 0, 0
    truncated = False
    for page_num, page in enumerate(reader.pages):
        annots = page.get('/Annots', [])
        if hasattr(annots, 'get_object'):
            annots = annots.get_object()
        for index, ref in enumerate(annots):
            scanned += 1
            if scanned > 20000 or len(rows) >= 2000 or total >= MAX_READ_POINTS:
                return {'rows': rows, 'truncated': True}
            try:
                annot = ref.get_object()
                if annot.get('/Subtype') != '/Ink':
                    continue
                # Hidden annotations should not create invisible deletion targets.
                if int(annot.get('/F', 0)) & (1 | 2 | 32):
                    continue
                paths = []
                ink_list = annot.get('/InkList', [])
                if hasattr(ink_list, 'get_object'):
                    ink_list = ink_list.get_object()
                for values in ink_list:
                    if hasattr(values, 'get_object'):
                        values = values.get_object()
                    if not 2 <= len(values) <= 16000 or len(values) % 2:
                        raise ValueError('Invalid ink path')
                    total += len(values) // 2
                    if total > MAX_READ_POINTS:
                        return {'rows': rows, 'truncated': True}
                    points = []
                    for i in range(0, len(values), 2):
                        x, y = float(values[i]), float(values[i+1])
                        if not number(x, -1e7, 1e7) or not number(y, -1e7, 1e7):
                            raise ValueError('Invalid point')
                        point = from_pdf(page, x, y)
                        if any(not number(v, -1e7, 1e7) for v in point):
                            raise ValueError('Invalid displayed point')
                        points.append(point)
                    paths.append(points)
                if not paths:
                    continue
                color = [float(c) for c in annot.get('/C', [0, 0, 0])]
                if len(color) != 3 or any(not number(c, 0, 1) for c in color):
                    color = [0, 0, 0]
                unit = float(page.get('/UserUnit', 1))
                border = annot.get('/BS', {})
                if hasattr(border, 'get_object'):
                    border = border.get_object()
                width = float(border.get('/W', 1)) * unit
                opacity = float(annot.get('/CA', 1))
                if not number(width, .01, 10000) or not number(opacity, 0, 1):
                    raise ValueError('Invalid ink style')
                rows.append(dict(page=page_num, index=index, paths=paths, color=color,
                                 width=width, opacity=opacity))
            except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
                truncated = True
    return {'rows': rows, 'truncated': truncated}


def validate(reader, strokes, deleted):
    if (not isinstance(strokes, list) or len(strokes) > MAX_STROKES or
            not isinstance(deleted, list) or len(deleted) > 2000):
        raise PDFOperationError('Invalid ink batch')
    total = 0
    for row in strokes:
        if not isinstance(row, dict) or set(row) != {'page', 'points', 'color', 'width', 'opacity'}:
            raise PDFOperationError('Invalid ink stroke')
        page = row['page']
        if type(page) is not int or not 0 <= page < len(reader.pages):
            raise PDFOperationError('Invalid ink page')
        points, color = row['points'], row['color']
        w, h = displayed_size(reader.pages[page])
        if not isinstance(points, list) or not 1 <= len(points) <= 8000:
            raise PDFOperationError('Invalid ink points')
        total += len(points)
        if total > MAX_POINTS:
            raise PDFOperationError('Too many ink points')
        for p in points:
            if (not isinstance(p, (list, tuple)) or len(p) != 2 or
                    not number(p[0], 0, w) or not number(p[1], 0, h)):
                raise PDFOperationError('Invalid ink point')
        if (not isinstance(color, (list, tuple)) or len(color) != 3 or
                any(not number(c, 0, 1) for c in color) or
                not number(row['width'], .5, 30) or not number(row['opacity'], .1, 1)):
            raise PDFOperationError('Invalid ink style')
    seen = set()
    for pair in deleted:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or
                any(type(n) is not int for n in pair) or
                not 0 <= pair[0] < len(reader.pages)):
            raise PDFOperationError('Invalid ink deletion')
        annots = reader.pages[pair[0]].get('/Annots', [])
        if hasattr(annots, 'get_object'):
            annots = annots.get_object()
        if not 0 <= pair[1] < len(annots) or annots[pair[1]].get_object().get('/Subtype') != '/Ink':
            raise PDFOperationError('Invalid ink deletion target')
        if tuple(pair) in seen:
            raise PDFOperationError('Duplicate ink deletion')
        seen.add(tuple(pair))


def array(values):
    return ArrayObject([FloatObject(float(v)) for v in values])


def annotation(writer, page, row):
    points = [to_pdf(page, *p) for p in row['points']]
    width = row['width'] / float(page.get('/UserUnit', 1))
    margin = width / 2 + 1
    x0, y0 = min(x for x, y in points) - margin, min(y for x, y in points) - margin
    x1, y1 = max(x for x, y in points) + margin, max(y for x, y in points) + margin
    commands = ['q', '/InkOpacity gs', ' '.join(f'{v:.6f}' for v in row['color']) + ' RG',
                f'{width:.6f} w', '1 J 1 j']
    current = None
    for kind, data in segments([(x - x0, y - y0) for x, y in points]):
        if kind == 'Q':
            control, end = data
            c1 = tuple(current[i] + 2/3 * (control[i] - current[i]) for i in (0, 1))
            c2 = tuple(end[i] + 2/3 * (control[i] - end[i]) for i in (0, 1))
            commands.append(' '.join(f'{v:.6f}' for v in (*c1, *c2, *end)) + ' c')
            current = end
        else:
            commands.append(f'{data[0]:.6f} {data[1]:.6f} ' + ('m' if kind == 'M' else 'l'))
            current = data
    commands.extend(('S', 'Q'))
    gs = DictionaryObject({NameObject('/Type'): NameObject('/ExtGState'),
                           NameObject('/CA'): FloatObject(row['opacity'])})
    appearance = DecodedStreamObject()
    appearance.set_data('\n'.join(commands).encode('ascii'))
    appearance.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                       NameObject('/BBox'): array((0, 0, x1-x0, y1-y0)),
                       NameObject('/Resources'): DictionaryObject({NameObject('/ExtGState'): DictionaryObject({
                           NameObject('/InkOpacity'): gs})})})
    return DictionaryObject({NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Ink'),
        NameObject('/Rect'): array((x0, y0, x1, y1)), NameObject('/InkList'): ArrayObject([
            array([v for p in points for v in p])]), NameObject('/C'): array(row['color']),
        NameObject('/CA'): FloatObject(row['opacity']), NameObject('/F'): NumberObject(4),
        NameObject('/NM'): TextStringObject('ape-ink-' + uuid4().hex),
        NameObject('/BS'): DictionaryObject({NameObject('/W'): FloatObject(width), NameObject('/S'): NameObject('/S')}),
        NameObject('/AP'): DictionaryObject({NameObject('/N'): writer._add_object(appearance)})})


def apply_ink(writer, reader, strokes, deleted):
    validate(reader, strokes, deleted)
    writer.clone_document_from_reader(reader)
    if strokes:
        writer.pdf_header = max(writer.pdf_header, '%PDF-1.4')
    targets = {}
    for page_num, index in deleted:
        targets.setdefault(page_num, set()).add(index)
    for page_num, indices in targets.items():
        annots = writer.pages[page_num]['/Annots']
        popups = [annots[i].get_object().get('/Popup') for i in indices]
        annots[:] = [ref for i, ref in enumerate(annots) if i not in indices and ref not in popups]
    for row in strokes:
        page = writer.pages[row['page']]
        writer.add_annotation(row['page'], annotation(writer, page, row))
