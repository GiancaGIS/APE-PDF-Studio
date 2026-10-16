"""Bounded split plans; no PDF parsing or file access in this module."""
from .editor import parse_page_range
from .errors import PDFOperationError
from .localization import tr


def validate_groups(groups, page_count):
    if (type(page_count) is not int or not 1 <= page_count <= 3000
            or not isinstance(groups, (list, tuple)) or not 2 <= len(groups) <= page_count):
        raise PDFOperationError(tr("La divisione deve produrre almeno due PDF."))
    seen = set()
    normalized = []
    for group in groups:
        if not isinstance(group, (list, tuple)) or not 1 <= len(group) <= page_count:
            raise PDFOperationError(tr("Ogni parte deve contenere almeno una pagina."))
        pages = []
        for page in group:
            if type(page) is not int or not 0 <= page < page_count or page in seen:
                raise PDFOperationError(tr("Ogni pagina deve comparire una sola volta nella divisione."))
            seen.add(page)
            pages.append(page)
        normalized.append(pages)
    if len(seen) != page_count:
        raise PDFOperationError(tr("Gli intervalli devono includere tutte le pagine del PDF."))
    return normalized


def split_groups(page_count, *, size=1, ranges=None):
    if type(page_count) is not int or not 2 <= page_count <= 3000:
        raise PDFOperationError(tr("Servono almeno due pagine per dividere il PDF."))
    if ranges is not None:
        if not isinstance(ranges, str) or len(ranges) > 20000:
            raise PDFOperationError(tr("Gli intervalli di divisione sono troppo lunghi."))
        groups = [parse_page_range(part, page_count) for part in ranges.split(";")]
    else:
        if type(size) is not int or not 1 <= size < page_count:
            raise PDFOperationError(tr("Il numero di pagine per file deve essere inferiore al totale."))
        groups = [list(range(start, min(start + size, page_count)))
                  for start in range(0, page_count, size)]
    return validate_groups(groups, page_count)


def part_name(index):
    return f"part_{index:04d}.pdf"
