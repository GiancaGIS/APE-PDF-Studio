"""Bounded, interoperable PDF Ink annotations. No Qt or native PDF engine."""
from uuid import uuid4

from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, TextStringObject, DecodedStreamObject, BooleanObject)

from .document_tools import number
from .errors import PDFOperationError
from .page_coordinates import to_pdf, from_pdf, displayed_size
from .ink_geometry import segments
from .localization import tr

MAX_STROKES = 256
MAX_POINTS = 50000
# Leave room for annotation/style records and document state in the bounded IPC
# decoder (200000 nodes). Very large existing drawings remain visible in PDF.
MAX_READ_POINTS = 20000


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
                                 width=width, opacity=opacity,
                                 smooth=bool(getattr(annot.get('/APEInkSmoothing'), 'value',
                                     str(annot.get('/NM', '')).startswith('ape-ink-')))))
            except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
                truncated = True
    return {'rows': rows, 'truncated': truncated}


def validate(reader, strokes, deleted, replaced=()):
    if (not isinstance(strokes, list) or len(strokes) > MAX_STROKES or
            not isinstance(deleted, list) or len(deleted) > 2000):
        raise PDFOperationError('Invalid ink batch')
    total = 0
    for row in strokes:
        if (not isinstance(row, dict) or set(row)-{'page', 'points', 'color', 'width', 'opacity', 'smooth'} or
                not {'page', 'points', 'color', 'width', 'opacity'} <= set(row) or
                ('smooth' in row and type(row['smooth']) is not bool)):
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
    if not isinstance(replaced, (list, tuple)) or len(replaced) > 2000:
        raise PDFOperationError(tr('Cancellazione parziale non valida.'))
    for row in replaced:
        if not isinstance(row, dict) or set(row) != {'page', 'index', 'paths', 'smooth'} or type(row['smooth']) is not bool:
            raise PDFOperationError(tr('Cancellazione parziale non valida.'))
        pair = row['page'], row['index']
        validate(reader, [], [pair])
        if pair in seen:
            raise PDFOperationError(tr('Cancellazione parziale non valida.'))
        seen.add(pair)
        paths = row['paths']
        if not isinstance(paths, list) or not 1 <= len(paths) <= 2000:
            raise PDFOperationError(tr('Cancellazione parziale non valida.'))
        for points in paths:
            if not isinstance(points, list) or not 1 <= len(points) <= 8000:
                raise PDFOperationError(tr('Cancellazione parziale non valida.'))
            total += len(points)
            if total > MAX_POINTS or any(not isinstance(p, (list, tuple)) or len(p) != 2 or
                    any(not number(v, -1e7, 1e7) for v in p) for p in points):
                raise PDFOperationError(tr('Cancellazione parziale non valida.'))


def array(values):
    return ArrayObject([FloatObject(float(v)) for v in values])


def annotation(writer, page, row):
    paths = [[to_pdf(page, *p) for p in path] for path in row.get('paths', [row.get('points')])]
    points = [p for path in paths for p in path]
    width = row['width'] / float(page.get('/UserUnit', 1))
    margin = width / 2 + 1
    x0, y0 = min(x for x, y in points) - margin, min(y for x, y in points) - margin
    x1, y1 = max(x for x, y in points) + margin, max(y for x, y in points) + margin
    commands = ['q', '/InkOpacity gs', ' '.join(f'{v:.6f}' for v in row['color']) + ' RG',
                f'{width:.6f} w', '1 J 1 j']
    for path in paths:
        current = None
        for kind, data in segments([(x - x0, y - y0) for x, y in path], row.get('smooth', True)):
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
            array([v for p in path for v in p]) for path in paths]), NameObject('/C'): array(row['color']),
        NameObject('/CA'): FloatObject(row['opacity']), NameObject('/F'): NumberObject(4),
        NameObject('/NM'): TextStringObject('ape-ink-' + uuid4().hex),
        NameObject('/APEInkSmoothing'): BooleanObject(row.get('smooth', True)),
        NameObject('/BS'): DictionaryObject({NameObject('/W'): FloatObject(width), NameObject('/S'): NameObject('/S')}),
        NameObject('/AP'): DictionaryObject({NameObject('/N'): writer._add_object(appearance)})})


def apply_ink(writer, reader, strokes, deleted, replaced=()):
    validate(reader, strokes, deleted, replaced)
    writer.clone_document_from_reader(reader)
    if strokes or replaced:
        writer.pdf_header = max(writer.pdf_header, '%PDF-1.4')
    styles = {(row['page'], row['index']): row for row in read_ink(reader)['rows']} if replaced else {}
    for row in replaced:
        key = row['page'], row['index']
        if key not in styles:
            raise PDFOperationError(tr('Disegno troppo complesso o non supportato per la modifica parziale.'))
        page = writer.pages[row['page']]
        original = page['/Annots'][row['index']].get_object()
        updated = annotation(writer, page, {**styles[key], 'paths': row['paths'], 'smooth': row['smooth']})
        for name in ('/Rect', '/InkList', '/AP', '/APEInkSmoothing'):
            original[NameObject(name)] = updated[name]
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
