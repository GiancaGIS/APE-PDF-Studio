"""Read explicit link annotations; never execute PDF actions."""
from __future__ import annotations

import math
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from .page_coordinates import from_pdf


def pdf_get(mapping, key, default=None):
    """Dictionary indexing resolves indirect PDF values; dict.get does not."""
    return mapping[key] if key in mapping else default


def allowed_url(value: str) -> str | None:
    """Allow only complete web URLs and email composers without attachments."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    decoded = value
    for _ in range(3):
        if any(unicodedata.category(char).startswith("C") or char == "\\" for char in decoded):
            return None
        decoded = unquote(decoded)
    if any(char.isspace() for char in value):
        return None
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if scheme in ("http", "https"):
            if not parts.hostname or parts.username is not None or parts.password is not None:
                return None
            host = parts.hostname.encode("idna").decode("ascii")
            if any(char in host for char in "% /?#@"):
                return None
            if ":" in host:
                host = "[" + host + "]"
            port = parts.port  # Validate even when absent from the normalized URL.
            netloc = host + (":" + str(port) if port is not None else "")
            return urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))
        if scheme == "mailto" and not parts.netloc and not parts.fragment:
            address = unquote(parts.path)
            if address.count("@") != 1 or any(char.isspace() or char in '<>";,/?#' for char in address):
                return None
            if not all(address.split("@")):
                return None
            if any(key.lower() not in ("subject", "body") for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
                return None
            return urlunsplit((scheme, "", parts.path, parts.query, ""))
    except (ValueError, UnicodeError):
        pass
    return None


def displayed_rect(page, value):
    coordinates = tuple(map(float, value))
    if len(coordinates) != 4 or not all(math.isfinite(x) and abs(x) <= 1e7 for x in coordinates):
        raise ValueError("invalid annotation rectangle")
    x0, y0, x1, y1 = coordinates
    points = [from_pdf(page, x, y) for x in (x0, x1) for y in (y0, y1)]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def read_links(reader):
    links = {}
    for number, page in enumerate(reader.pages):
        for reference in pdf_get(page, "/Annots", []):
            annotation = reference.get_object()
            if annotation.get("/Subtype") != "/Link" or int(annotation.get("/F", 0)) & 35:
                continue
            action = annotation.get("/A")
            if action is None:
                continue
            action = action.get_object()
            if action.get("/S") != "/URI" or "/Next" in action:
                continue
            url = allowed_url(pdf_get(action, "/URI"))
            if url is None or "/Rect" not in annotation:
                continue
            rect = displayed_rect(page, annotation["/Rect"])
            links.setdefault(number, []).append({"rect": rect, "url": url})
    return links
