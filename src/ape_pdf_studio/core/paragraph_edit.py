"""Recognize horizontal paragraphs and reflow them inside their own column.

PDF parsing, font metrics, redaction and layout run only in the document worker.
The UI receives immutable records and a bounded layout summary, never fonts.
"""
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import io
import math
import re
from statistics import median
import unicodedata

from .errors import PDFOperationError
from .localization import tr
from .text_edit import TextTarget, replacement_font, supported_paint, text_spans

MAX_TEXT = 16000
MAX_MEMBERS = 2000
MAX_LINES = 2000


@dataclass(frozen=True)
class ParagraphTarget(TextTarget):
    members: tuple
    layout_bbox: tuple
    display_layout_bbox: tuple
    leading: float
    alignment: int
    first_indent: float
    continuation_indent: float
    rotation: int


def _supported(line, span):
    return (bool(span['text'].strip()) and line['wmode'] == 0
            and abs(line['dir'][0] - 1) <= .001 and abs(line['dir'][1]) <= .001
            and supported_paint(span) and .5 <= span['size'] <= 300)


def _groups(page):
    """Conservative geometric grouping: never join separate native blocks/columns."""
    lines = []
    for address, line, span in text_spans(page):
        if not lines or lines[-1]['address'] != address[:2]:
            lines.append(dict(address=address[:2], spans=[], supported=True))
        lines[-1]['spans'].append((address, span))
        if not _supported(line, span) and span['text'].strip():
            lines[-1]['supported'] = False
    import pymupdf
    for line in lines:
        spans = line['spans']
        box = pymupdf.Rect()
        styles = Counter()
        for _, span in spans:
            box |= pymupdf.Rect(span['bbox'])
            styles[(span['font'], span['flags'] & 18, span['color'])] += len(span['text'])
        line.update(box=box, text=''.join(span['text'] for _, span in spans),
                    size=median(span['size'] for _, span in spans),
                    baseline=median(span['origin'][1] for _, span in spans), style=styles.most_common(1)[0][0])
    groups = []
    for line in lines:
        if not line['supported'] or not line['text'].strip():
            continue
        previous = groups[-1][-1] if groups else None
        join = False
        if previous and previous['address'][0] == line['address'][0] and line['address'][1] == previous['address'][1] + 1:
            size = max(previous['size'], line['size'])
            dy = line['baseline'] - previous['baseline']
            a, b = previous['box'], line['box']
            overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            join = (.6 * size <= dy <= 1.7 * size and abs(previous['size'] - line['size']) <= .5
                    and overlap >= .5 * min(a.width, b.width)
                    and previous['style'] == line['style']
                    and (abs(a.x0 - b.x0) <= 3 * size or abs(a.x1 - b.x1) <= size))
            if re.match(r'^\s*(?:[•●▪◦‣*]|\d+[.)])\s', line['text']):
                join = False
            if previous['text'].rstrip().endswith(('.', '!', '?', ':')) and a.width < .65 * b.width:
                join = False
        if join:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _plain(members):
    text, styles = '', []
    previous = None
    for index, member in enumerate(members):
        if previous:
            new_line = previous.address[:2] != member.address[:2]
            gap = member.bbox[0] - previous.bbox[2]
            if (new_line or gap > member.size * .15) and not text.endswith((' ', '-')) and not member.text.startswith(' '):
                text += ' '
                styles.append(index - 1)
        text += member.text
        styles.extend([index] * len(member.text))
        previous = member
    return text, styles


def _obstacles(page, members, original):
    import pymupdf
    selected = {member.address for member in members}
    obstacles = [pymupdf.Rect(span['bbox']) for address, _, span in text_spans(page)
                 if address not in selected and span['text'].strip()]
    # Filled backgrounds and containing frames may contain text, but bound growth.
    containers = []
    for kind, bbox in page.get_bboxlog():
        if kind not in ('fill-image', 'fill-path', 'stroke-path', 'fill-shade'):
            continue
        rect = pymupdf.Rect(bbox)
        if rect.is_empty or rect.is_infinite:
            continue
        if rect.contains(original):
            containers.append(rect)
        elif (rect & original).get_area() <= .1:
            obstacles.append(rect)
        else:
            # Existing art behind part of the paragraph: keep growth in its old box.
            containers.append(pymupdf.Rect(original))
    for annotation in page.annots() or ():
        if not (annotation.rect & original).get_area() > .1:
            obstacles.append(annotation.rect)
    for widget in page.widgets() or ():
        if not (widget.rect & original).get_area() > .1:
            obstacles.append(widget.rect)
    return obstacles, containers


def _target(source, page, number, group):
    import pymupdf
    members = tuple(TextTarget(str(source), number, address, span['text'], tuple(span['bbox']),
                              tuple(pymupdf.Rect(span['bbox']) * page.rotation_matrix), tuple(span['origin']),
                              span['size'], span['color'], span['font'], span['flags'])
                    for line in group for address, span in line['spans'] if span['text'])
    text, _ = _plain(members)
    if len(members) > MAX_MEMBERS or len(text) > MAX_TEXT:
        raise PDFOperationError(tr('Il paragrafo è troppo lungo per la modifica diretta.'))
    box = pymupdf.Rect()
    for member in members:
        box |= pymupdf.Rect(member.bbox)
    leading = median(b['baseline'] - a['baseline'] for a, b in zip(group, group[1:])) if len(group) > 1 else members[0].size * 1.2
    profile = Counter((m.font, m.size, m.color, m.flags) for m in members for _ in range(max(1, min(len(m.text), 100))))
    font, size, color, flags = profile.most_common(1)[0][0]
    baseline = group[0]['baseline']
    bounds = pymupdf.Rect(page.rect) * page.derotation_matrix
    bottom = max(box.y1, bounds.y1 - min(12, size * .5))
    obstacles, containers = _obstacles(page, members, box)
    for obstacle in obstacles:
        if min(box.x1, obstacle.x1) - max(box.x0, obstacle.x0) > .5 and obstacle.y0 >= box.y1 - .5:
            bottom = min(bottom, max(box.y1, obstacle.y0 - 2))
    for container in containers:
        bottom = min(bottom, max(box.y1, container.y1 - 2))
    layout = pymupdf.Rect(box.x0, box.y0, box.x1, bottom)
    lefts = [line['box'].x0 for line in group]
    rights = [line['box'].x1 for line in group]
    centers = [(left + right) / 2 for left, right in zip(lefts, rights)]
    alignment = 0
    if len(group) > 1 and max(lefts) - min(lefts) > size * .4:
        if max(centers) - min(centers) < size * .25:
            alignment = 1
        elif max(rights) - min(rights) < size * .25:
            alignment = 2
    if len(group) >= 3 and max(lefts[:-1]) - min(lefts[:-1]) < 1 and max(rights[:-1]) - min(rights[:-1]) < 1 and rights[-1] < max(rights) - size:
        alignment = 3
    continuation = max(0, median(lefts[1:] or lefts) - box.x0) if alignment == 0 else 0
    first = max(0, lefts[0] - box.x0) if alignment == 0 else 0
    return ParagraphTarget(str(source), number, members[0].address, text, tuple(box),
                           tuple(box * page.rotation_matrix), (lefts[0], baseline), size, color, font, flags,
                           members, tuple(layout), tuple(layout * page.rotation_matrix), leading,
                           alignment, first, continuation, page.rotation)


def paragraph_at(source, number, position):
    import pymupdf
    with pymupdf.open(stream=source.read_bytes(), filetype='pdf') as document:
        page = document[number]
        point = pymupdf.Point(*position) * page.derotation_matrix
        candidates = [(group, span) for group in _groups(page) for line in group
                      for _, span in line['spans'] if pymupdf.Rect(span['bbox']).contains(point)]
        if not candidates:
            raise PDFOperationError(tr('Nessun paragrafo modificabile in questo punto. Per le scansioni usa OCR.'))
        group, _ = min(candidates, key=lambda item: pymupdf.Rect(item[1]['bbox']).get_area())
        return _target(source, page, number, group)


def paragraph_boxes(source, number):
    import pymupdf
    with pymupdf.open(stream=source.read_bytes(), filetype='pdf') as document:
        page = document[number]
        boxes = []
        # Geometry scanning avoids font extraction and obstacle analysis per group.
        for group in _groups(page):
            box = pymupdf.Rect()
            for line in group:
                box |= line['box']
            if not box.is_empty:
                boxes.append(tuple(box * page.rotation_matrix))
        return boxes


def _validate_text(text, size, alignment):
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or any(
            (unicodedata.category(c).startswith('C') and c != '\n')
            or unicodedata.bidirectional(c) in ('R', 'AL', 'AN') or unicodedata.combining(c) for c in text)):
        raise PDFOperationError(tr('Inserisci un paragrafo non vuoto, fino a 16000 caratteri. Le scritture complesse non sono ancora supportate.'))
    if type(size) not in (int, float) or not math.isfinite(size) or not .5 <= size <= 300:
        raise PDFOperationError(tr('Dimensione del testo non valida.'))
    if type(alignment) is not int or alignment not in (0, 1, 2, 3):
        raise PDFOperationError(tr('Allineamento del paragrafo non valido.'))


def _layout(document, page, target, text, size, alignment):
    """Greedy wrapping with actual PDF font advances and retained style runs."""
    _validate_text(text, size, alignment)
    original, source_styles = _plain(target.members)
    styles = [0] * len(text)
    for tag, a, b, c, d in SequenceMatcher(None, original, text, autojunk=True).get_opcodes():
        if tag == 'equal':
            styles[c:d] = source_styles[a:b]
        else:
            styles[c:d] = [source_styles[min(a, len(source_styles) - 1)]] * (d - c)
    definitions, aliases = {}, {}
    for index in set(styles):
        member = target.members[index]
        signature = (member.font, member.size, member.color, member.flags)
        aliases[index] = signature
        if signature not in definitions:
            if len(definitions) >= 32:
                raise PDFOperationError(tr('Il paragrafo contiene troppi stili diversi per il riflusso.'))
            chars = ''.join(char for char, style in zip(text, styles) if aliases.get(style, (
                target.members[style].font, target.members[style].size, target.members[style].color,
                target.members[style].flags)) == signature and char != '\n')
            font, name, fallback = replacement_font(document, page, member, chars)
            definitions[signature] = (font, member.size * size / target.size, member.color, name, fallback)
    advances = {}
    items = []
    for char, style in zip(text, styles):
        signature = aliases[style]
        font, points, _, _, _ = definitions[signature]
        key = signature, char
        if key not in advances:
            advances[key] = 0 if char == '\n' else font.text_length(char, fontsize=points)
        items.append((char, signature, advances[key]))
    width = target.layout_bbox[2] - target.layout_bbox[0]
    lines, line, pending = [], [], []
    indent = target.first_indent if alignment == 0 else 0
    line_width = 0
    def emit(justify=False):
        nonlocal line, indent, line_width
        while line and line[-1][0].isspace():
            line.pop()
        lines.append((line, indent, justify))
        if len(lines) > MAX_LINES:
            raise PDFOperationError(tr('Il testo richiede troppe righe per il riflusso.'))
        line = []
        line_width = 0
        indent = target.continuation_indent if alignment == 0 else 0
    for match in re.finditer(r'\n|[^\S\n]+|[^\s]+', text):
        token = items[match.start():match.end()]
        if match.group() == '\n':
            emit()
            indent = target.first_indent if alignment == 0 else 0
            pending = []
            continue
        if match.group().isspace():
            pending = token if line else []
            continue
        token_width = sum(item[2] for item in token)
        if line and line_width + sum(item[2] for item in pending) + token_width > width - indent + .01:
            emit(True)
            pending = []
        line.extend(pending)
        line_width += sum(item[2] for item in pending)
        pending = []
        for item in token:
            if item[2] > width - indent + .01:
                raise PDFOperationError(tr('Il riquadro è troppo stretto per il carattere selezionato.'))
            if line and line_width + item[2] > width - indent + .01:
                emit(True)
            line.append(item)
            line_width += item[2]
    emit()
    leading = target.leading * size / target.size
    first_ascent = max(font.ascender * points for font, points, *_ in definitions.values())
    baseline = target.layout_bbox[1] + max(first_ascent, (target.origin[1] - target.bbox[1]) * size / target.size)
    runs, bottom = [], target.layout_bbox[1]
    left, top, right, limit = target.layout_bbox
    for index, (line, indent, justify) in enumerate(lines):
        used = sum(item[2] for item in line)
        slack = max(0, width - indent - used)
        x = left + indent + (slack / 2 if alignment == 1 else slack if alignment == 2 else 0)
        spaces = sum(char == ' ' for char, *_ in line)
        extra = slack / spaces if alignment == 3 and justify and spaces else 0
        cursor = 0
        while cursor < len(line):
            end = cursor + 1
            signature = line[cursor][1]
            while end < len(line) and line[end][1] == signature and not (extra and line[end - 1][0] == ' '):
                end += 1
            segment = line[cursor:end]
            font, points, color, *_ = definitions[signature]
            value = ''.join(item[0] for item in segment)
            runs.append((x, baseline, value, font, points, color))
            bottom = max(bottom, baseline - font.descender * points)
            x += sum(item[2] for item in segment) + extra * value.count(' ')
            cursor = end
        if not line:
            bottom = max(bottom, baseline)
        baseline += max(leading, size * .8)
    if bottom > limit + .25:
        raise PDFOperationError(tr('Il paragrafo supera lo spazio libero: accorcialo o riduci la dimensione. Nessun contenuto è stato sovrascritto.'))
    return runs, (left, top, right, max(top + size, bottom)), len(lines), definitions


def paragraph_info(source, target, text, size, alignment):
    import pymupdf
    with pymupdf.open(stream=source.read_bytes(), filetype='pdf') as document:
        try:
            _, bbox, count, fonts = _layout(document, document[target.page], target, text, size, alignment)
            return dict(bbox=tuple(pymupdf.Rect(bbox) * document[target.page].rotation_matrix), lines=count,
                        font=next(iter(fonts.values()))[3], fallback=any(value[4] for value in fonts.values()),
                        fits=True, message='')
        except PDFOperationError as exc:
            return dict(bbox=target.display_layout_bbox, lines=0, font=target.font,
                        fallback=False, fits=False, message=str(exc))


def replace_paragraph(writer, reader, source, target, text, size, alignment):
    import pymupdf
    from pypdf import PdfReader
    from pypdf.generic import NameObject
    with pymupdf.open(stream=source.read_bytes(), filetype='pdf') as document:
        page = document[target.page]
        # Re-derive editable geometry, style and free space from the accepted PDF.
        groups = [group for group in _groups(page) if any(address == target.members[0].address
                  for line in group for address, _ in line['spans'])]
        if len(groups) != 1 or _target(source, page, target.page, groups[0]) != target:
            raise PDFOperationError(tr('Il testo è cambiato. Selezionalo nuovamente.'))
        runs, _, _, _ = _layout(document, page, target, text, size, alignment)
        selected = {member.address for member in target.members}
        for address, _, span in text_spans(page):
            if address not in selected and any((pymupdf.Rect(span['bbox']) & pymupdf.Rect(member.bbox)).get_area() > .1 for member in target.members):
                raise PDFOperationError(tr('Il testo si sovrappone ad altri contenuti testuali: modifica non applicata.'))
        for annotation in list(page.annots() or []):
            if annotation.type[0] == pymupdf.PDF_ANNOT_REDACT:
                page.delete_annot(annotation)
        for member in target.members:
            page.add_redact_annot(member.bbox, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        rotation = page.rotation
        page.set_rotation(0)
        rect = page.rect
        with pymupdf.open() as overlay:
            output_page = overlay.new_page(width=rect.width, height=rect.height)
            # Keep drawing order as well as inline styles so later extraction
            # and subsequent paragraph edits retain the logical word order.
            output, current_color = None, None
            def flush():
                if output is not None:
                    output.write_text(output_page, color=tuple(((current_color >> shift) & 255) / 255 for shift in (16, 8, 0)))
            for x, y, value, font, points, color in runs:
                if color != current_color:
                    flush()
                    output, current_color = pymupdf.TextWriter(rect), color
                output.append((x, y), value, font=font, fontsize=points)
            flush()
            page.show_pdf_page(rect, overlay, 0)
        page.set_rotation(rotation)
        modified = document.tobytes(deflate=True)
    writer.clone_document_from_reader(reader)
    with PdfReader(io.BytesIO(modified)) as changed:
        for key in ('/Contents', '/Resources'):
            writer.pages[target.page][NameObject(key)] = changed.pages[target.page][key].clone(writer)
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
