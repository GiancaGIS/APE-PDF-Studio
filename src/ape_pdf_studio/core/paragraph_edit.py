"""Recognize horizontal paragraphs and reflow them inside their own column."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
import re
from statistics import median
import unicodedata

from .errors import PDFOperationError
from .localization import tr
from .text_edit import TextFormat, TextTarget, layout_font, make_target, normalize_draft, supported_paint, validate_size
from .text_rewrite import TextLayout, TextRun, display_rotation

MAX_TEXT = 16000
MAX_MEMBERS = 2000
MAX_LINES = 2000
NEW_BLOCK = (1_000_000, 0, 0)


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
    column_limit: float


def _supported(line, span):
    return (bool(span['text'].strip()) and line['wmode'] == 0
            and abs(line['dir'][0] - 1) <= .001 and abs(line['dir'][1]) <= .001
            and supported_paint(span) and .5 <= span['size'] <= 300)


def _groups(index):
    """Conservative geometric grouping: never join separate native blocks/columns."""
    if index.groups is not None:
        return index.groups
    lines = []
    for address, line, span in index.spans:
        if not lines or lines[-1]['address'] != address[:2]:
            lines.append(dict(address=address[:2], spans=[], supported=True))
        lines[-1]['spans'].append((address, span))
        lines[-1]['direction'] = line
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
    index.groups = groups
    return groups


def _cjk(char):
    return unicodedata.east_asian_width(char) in ('W', 'F')


def _plain(members):
    text, styles = '', []
    previous = None
    for index, member in enumerate(members):
        if previous:
            new_line = previous.address[:2] != member.address[:2]
            gap = member.bbox[0] - previous.bbox[2]
            no_space = (_cjk(text[-1]) or _cjk(member.text[0])) if new_line else False
            if (new_line or gap > member.size * .15) and not no_space and not (
                    text[-1].isspace() or text.endswith('-') or member.text[0].isspace()):
                text += ' '
                styles.append(index - 1)
        text += member.text
        styles.extend([index] * len(member.text))
        previous = member
    return text, styles


def _obstacles(index, members, original):
    import pymupdf
    page = index.page
    selected = {member.address for member in members}
    obstacles = [pymupdf.Rect(span['bbox']) for address, _, span in index.spans
                 if address not in selected and span['text'].strip()]
    # Filled backgrounds and containing frames may contain text, but bound growth.
    containers = []
    if index.artwork is None:
        from .text_rewrite import _artwork
        index.artwork = _artwork(page)
    for kind, bbox, _ in index.artwork:
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
        obstacles.append(annotation.rect)
    for widget in page.widgets() or ():
        obstacles.append(widget.rect)
    return obstacles, containers


def _target(source, index, group):
    import pymupdf
    page = index.page
    members = tuple(make_target(source, page, address, line['direction'], span)
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
    transform = display_rotation(page)
    bounds = page.rect * ~transform
    bottom = max(box.y1, bounds.y1 - min(12, size * .5))
    obstacles, containers = _obstacles(index, members, box)
    for obstacle in obstacles:
        if min(box.x1, obstacle.x1) - max(box.x0, obstacle.x0) > .5 and obstacle.y0 >= box.y1 - .5:
            bottom = min(bottom, max(box.y1, obstacle.y0 - 2))
    for container in containers:
        bottom = min(bottom, max(box.y1, container.y1 - 2))
    layout = pymupdf.Rect(box.x0, box.y0, box.x1, bottom)
    right = bounds.x1 - 6
    for obstacle in obstacles:
        if obstacle.x0 >= box.x1 - .25 and obstacle.y0 < bottom and obstacle.y1 > box.y0:
            right = min(right, obstacle.x0 - 2)
    for container in containers:
        right = min(right, container.x1 - 2)
    column_limit = max(box.width, right - box.x0)
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
    identity = sha256(''.join(member.identity for member in members).encode('ascii')).hexdigest()
    return ParagraphTarget(str(source), page.number, members[0].address, text, tuple(box),
                           tuple(box * transform), (lefts[0], baseline), size, color, font, flags,
                           identity, members, tuple(layout), tuple(layout * transform), leading,
                           alignment, first, continuation, page.rotation, column_limit)


def paragraph_at(source, number, position, cache):
    import pymupdf
    index = cache.page(source, number)
    point = pymupdf.Point(*position) * ~display_rotation(index.page)
    candidates = [(group, span) for group in _groups(index) for line in group
                  for _, span in line['spans'] if pymupdf.Rect(span['bbox']).contains(point)]
    if not candidates:
        containing = []
        for group in _groups(index):
            box = pymupdf.Rect()
            for line in group:
                box |= line['box']
            if box.contains(point):
                containing.append(group)
        if len(containing) != 1:
            raise PDFOperationError(tr('Nessun paragrafo modificabile in questo punto. Per le scansioni usa OCR.'))
        return _target(source, index, containing[0])
    group, _ = min(candidates, key=lambda item: pymupdf.Rect(item[1]['bbox']).get_area())
    return _target(source, index, group)


def paragraph_boxes(source, number, cache):
    import pymupdf
    index = cache.page(source, number)
    boxes = []
    for group in _groups(index):
        box = pymupdf.Rect()
        for line in group:
            box |= line['box']
        if not box.is_empty:
            boxes.append(tuple(box * display_rotation(index.page)))
    return boxes


def _insertion_target(source, index, top_left):
    import pymupdf
    page = index.page
    rotation = display_rotation(page)
    bounds = page.rect * ~rotation
    left, top = top_left
    size = 12
    seed = pymupdf.Rect(left, top, left + 24, top + 18)
    error = tr('Scegli uno spazio libero più ampio per il nuovo testo.')
    if not bounds.contains(seed):
        raise PDFOperationError(error)
    obstacles, containers = _obstacles(index, (), seed)
    # New text belongs in empty space. A painted background or annotation
    # intersecting the start needs an explicit placement strategy.
    if containers or any((seed & obstacle).get_area() > .1 for obstacle in obstacles):
        raise PDFOperationError(error)
    right = bounds.x1 - 4
    for obstacle in obstacles:
        if obstacle.x0 >= seed.x1 and obstacle.y0 < seed.y1 and obstacle.y1 > top:
            right = min(right, obstacle.x0 - 2)
    width = min(right - left, 240)
    bottom = bounds.y1 - 4
    for obstacle in obstacles:
        if obstacle.y0 >= seed.y1 and obstacle.x1 > left and obstacle.x0 < right:
            bottom = min(bottom, obstacle.y0 - 2)
    if width < 24 or bottom - top < 18:
        raise PDFOperationError(error)
    bbox = pymupdf.Rect(left, top, left + width, seed.y1)
    layout = pymupdf.Rect(left, top, bbox.x1, bottom)
    identity = sha256(repr((page.number, tuple(bbox), tuple(layout), right)).encode('ascii')).hexdigest()
    return ParagraphTarget(str(source), page.number, NEW_BLOCK, '', tuple(bbox), tuple(bbox * rotation),
                           (left, top + size * 1.075), size, 0, 'Helvetica', 0, identity,
                           (), tuple(layout), tuple(layout * rotation), size * 1.2, 0, 0., 0.,
                           page.rotation, right - left)


def new_paragraph_at(source, number, position, cache):
    import pymupdf
    index = cache.page(source, number)
    point = pymupdf.Point(*position) * ~display_rotation(index.page)
    return _insertion_target(source, index, tuple(point))


def _boundaries(value, kind):
    from PySide6.QtCore import QTextBoundaryFinder
    finder = QTextBoundaryFinder(kind, value)
    utf16 = {}
    offset = 0
    for i, char in enumerate(value, 1):
        offset += 2 if ord(char) > 0xffff else 1
        utf16[offset] = i
    result = set()
    while (boundary := finder.toNextBoundary()) >= 0:
        if boundary in utf16:
            result.add(utf16[boundary])
    return result


def _wrapped_lines(text, items, width, first, continuation):
    from PySide6.QtCore import QTextBoundaryFinder
    kinds = QTextBoundaryFinder.BoundaryType
    graphemes = _boundaries(text, kinds.Grapheme)
    breaks = _boundaries(text, kinds.Line)
    nonbreaking = ('\u00a0', '\u202f', '\u2011')
    closing = '、。，．！？：；）］｝〉》」』】〕〗〙〛・'
    opening = '（［｛〈《「『【〔〖〘〚'
    start, indent = 0, first
    while start <= len(items):
        new_line = text.find('\n', start)
        end = len(items) if new_line == -1 else new_line
        last_break, emergency, used = start, start, 0
        cursor = start
        while cursor < end:
            used += items[cursor][2]
            boundary = cursor + 1
            if used > width - indent + .01 and text[cursor] != ' ':
                break
            if boundary in graphemes and (boundary == end or
                    text[cursor] not in nonbreaking and text[boundary] not in nonbreaking
                    and text[cursor] not in opening and text[boundary] not in closing):
                emergency = boundary
                if boundary in breaks:
                    last_break = boundary
            cursor = boundary
        wrap = cursor < end
        if wrap:
            cursor = last_break if last_break > start else emergency
            if cursor == start:
                raise PDFOperationError(tr('Il riquadro è troppo stretto per una sequenza di caratteri non separabile.'))
        finish = cursor
        while finish > start and text[finish - 1] == ' ':
            finish -= 1
        yield items[start:finish], indent, wrap
        if wrap:
            start, indent = cursor, continuation
            while start < end and text[start] == ' ':
                start += 1
        else:
            start, indent = end + 1, first


def _layout(index, target, text, size, alignment, style):
    """Wrap a plain draft at Unicode boundaries using actual PDF font advances."""
    raw = text
    text = normalize_draft(text, MAX_TEXT, multiline=True)
    validate_size(size)
    if type(alignment) is not int or alignment not in (0, 1, 2, 3):
        raise PDFOperationError(tr('Allineamento del paragrafo non valido.'))
    original, source_styles = _plain(target.members)
    styles = [0] * len(text)
    if source_styles:
        for tag, a, b, c, d in SequenceMatcher(None, original, text, autojunk=True).get_opcodes():
            if tag == 'equal':
                styles[c:d] = source_styles[a:b]
            else:
                styles[c:d] = [source_styles[min(a, len(source_styles) - 1)]] * (d - c)
    members = target.members or (target,)
    if style.selection is not None:
        from PySide6.QtCore import QTextBoundaryFinder
        first, end = style.selection
        boundaries = _boundaries(raw, QTextBoundaryFinder.BoundaryType.Grapheme) | {0}
        if first not in boundaries or end not in boundaries:
            raise PDFOperationError(tr('Selezione di testo non valida.'))
        first = len(unicodedata.normalize('NFC', raw[:first]))
        end = len(unicodedata.normalize('NFC', raw[:style.selection[1]]))
        styles = [(n, first <= i < end) for i, n in enumerate(styles)]
    else:
        styles = [(n, True) for n in styles]
    inherited = TextFormat()
    definitions, aliases, samples = {}, {}, {}
    for style_index, selected in set(styles):
        run_style = style if selected else inherited
        member = run_style.apply(members[style_index])
        points = member.size * size / target.size if selected else member.size
        signature = (member.font, points, member.color, member.flags, selected)
        aliases[style_index, selected] = signature
        samples[signature] = member, run_style
    if len(samples) > 32:
        raise PDFOperationError(tr('Il paragrafo contiene troppi stili diversi per il riflusso.'))
    coverage = defaultdict(set)
    for char, style_index in zip(text, styles):
        if char != '\n':
            coverage[aliases[style_index]].add(char)
    for signature, (member, run_style) in samples.items():
        font, name, fallback = layout_font(index, member, coverage[signature], run_style)
        definitions[signature] = (font, signature[1], member.color, name, fallback)
    advances = {}
    items = []
    for char, style_index in zip(text, styles):
        signature = aliases[style_index]
        font, points, _, _, _ = definitions[signature]
        key = signature, char
        if key not in advances:
            advances[key] = 0 if char == '\n' else font.text_length(char, fontsize=points)
        items.append((char, signature, advances[key]))
    width = target.layout_bbox[2] - target.layout_bbox[0]
    if style.width is not None:
        if style.width > target.column_limit + .001:
            raise PDFOperationError(tr('La larghezza supera lo spazio libero accanto al paragrafo.'))
        width = style.width
    lines = _wrapped_lines(text, items, width, target.first_indent if alignment == 0 else 0,
                           target.continuation_indent if alignment == 0 else 0)
    leading = target.leading * (max(size, target.size) if style.selection is not None else size) / target.size
    first_ascent = max(font.ascender * points for font, points, *_ in definitions.values())
    baseline = target.layout_bbox[1] + max(first_ascent, (target.origin[1] - target.bbox[1]) * size / target.size)
    runs, bottom = [], target.layout_bbox[1]
    left, top, _, limit = target.layout_bbox
    for count, (line, indent, justify) in enumerate(lines, 1):
        if count > MAX_LINES:
            raise PDFOperationError(tr('Il testo richiede troppe righe per il riflusso.'))
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
            font, points, color, name, fallback = definitions[signature]
            value = ''.join(item[0] for item in segment)
            advance = sum(item[2] for item in segment)
            runs.append(TextRun(x, baseline, value, font, points, color, name, fallback, advance))
            if len(runs) > 4000:
                raise PDFOperationError(tr('Il testo richiede troppi segmenti per il riflusso.'))
            bottom = max(bottom, baseline - font.descender * points)
            x += advance + extra * value.count(' ')
            cursor = end
        if not line:
            bottom = max(bottom, baseline)
        baseline += max(leading, size * .8)
    if bottom > limit + .25:
        raise PDFOperationError(tr('Il paragrafo supera lo spazio libero: accorcialo o riduci la dimensione. Nessun contenuto è stato sovrascritto.'))
    return TextLayout(tuple(runs), (left, top, left + width, max(top + size, bottom)), count)


def paragraph_layout(index, target, text, size, alignment, style, *, delete=False):
    if target.members:
        group = next((group for group in _groups(index) if any(address == target.address
                      for line in group for address, _ in line['spans'])), None)
        current = _target(target.revision, index, group) if group is not None else None
    else:
        current = None if delete else _insertion_target(target.revision, index, target.bbox[:2])
    if current != target:
        raise PDFOperationError(tr('Il testo è cambiato. Selezionalo nuovamente.'))
    return TextLayout((), target.bbox, 0) if delete else _layout(index, target, text, size, alignment, style)
