"""Bounded, static SVG display lists. Unsupported PDF features use PDFium instead."""
from __future__ import annotations
from .localization import tr

import copy
import math
import re
import xml.etree.ElementTree as ET

MAX_SVG_BYTES = 12 * 1024 * 1024
MAX_NODES = 100_000
MAX_LAYERS = 1024
SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
PATH_TOKEN = re.compile(r"[MLHVCZ]|" + NUMBER)
ALLOWED_TAGS = {"svg", "defs", "g", "path", "use", "clipPath"}
ALLOWED_ELEMENTS = {f"{{{SVG}}}{name}" for name in ALLOWED_TAGS}
ALLOWED_ATTRS = {"version", "width", "height", "viewBox", "id", "d", "transform",
                 "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width",
                 "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
                 "stroke-dasharray", "stroke-dashoffset", "stroke-opacity", "opacity",
                 "clip-rule", "clip-path", "data-text", f"{{{XLINK}}}href"}


class VectorUnsupported(ValueError):
    """The page needs the full PDF renderer to preserve its appearance."""


def printable_svg(data):
    """Equivalent SVG stroke defaults for Qt's PDF/native print paint engines.

    Qt SVG uses a SolidLine pen with NoBrush for absent strokes. Qt's PDF
    engine checks the pen style alone and fills the stroker path using the
    previous fill color. A transparent solid stroke avoids those black halos.
    This is applied only to the bounded SVG already validated by prepare_svg.
    """
    root = ET.fromstring(data)
    if root.get("stroke", "none") == "none":
        root.set("stroke", "transparent")
    for element in root.iter():
        if element.get("stroke") == "none":
            element.set("stroke", "transparent")
    result = ET.tostring(root, encoding="utf-8")
    if len(result) > MAX_SVG_BYTES:
        raise VectorUnsupported(tr("SVG di stampa fuori limite"))
    return result


def matrix(value):
    match = re.fullmatch(r"matrix\(([^()]*)\)", value.strip())
    if not match:
        raise VectorUnsupported(tr("Trasformazione SVG non supportata"))
    values = [float(x) for x in re.split(r"[\s,]+", match[1].strip())]
    if len(values) != 6 or not all(math.isfinite(x) and abs(x) <= 1e9 for x in values):
        raise VectorUnsupported("Trasformazione SVG fuori limite")
    return values


def clip_commands(value):
    """MuPDF emits absolute M/L/H/V/C/Z paths; reject any other grammar."""
    tokens = PATH_TOKEN.findall(value)
    if re.sub(PATH_TOKEN, "", value).strip(" ,\t\n\r"):
        raise VectorUnsupported(tr("Ritaglio SVG non supportato"))
    commands = []
    index = 0
    command = None
    counts = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Z": 0}
    while index < len(tokens):
        if tokens[index] in counts:
            command=tokens[index]
            index+=1
        count = counts.get(command)
        if count is None:
            raise VectorUnsupported(tr("Ritaglio SVG non supportato"))
        try:
            coordinates = [float(x) for x in tokens[index:index + count]]
        except ValueError as exc:
            raise VectorUnsupported(tr("Ritaglio SVG non valido")) from exc
        if len(coordinates) != count or not all(math.isfinite(x) and abs(x) <= 1e9 for x in coordinates):
            raise VectorUnsupported("Ritaglio SVG fuori limite")
        commands.append([command, coordinates])
        index += count
        if command=='M':command='L'
        elif command=='Z':command=None
    if not commands or commands[0][0] != "M":
        raise VectorUnsupported(tr("Ritaglio SVG non valido"))
    return commands


def prepare_svg(data: bytes):
    """Split the drawing at clip boundaries, retaining order and vector paths.

    Qt SVG does not implement clipPath. Each layer is painted with an explicit
    QPainterPath clip, rather than a raster mask or an ignored SVG attribute.
    No external resources, embedded images, scripts, CSS or animations are accepted.
    """
    if len(data) > MAX_SVG_BYTES or b"<!" in data:
        raise VectorUnsupported("SVG fuori limite")
    root = ET.fromstring(data)
    nodes = list(root.iter())
    if root.tag != f"{{{SVG}}}svg" or len(nodes) > MAX_NODES:
        raise VectorUnsupported("SVG fuori limite")
    ids = {}
    for element in nodes:
        if element.tag not in ALLOWED_ELEMENTS:
            raise VectorUnsupported(tr("La pagina contiene immagini o effetti complessi"))
        for key, value in element.attrib.items():
            if key not in ALLOWED_ATTRS:
                raise VectorUnsupported(tr("Proprietà SVG non supportata"))
            if key == "transform":
                matrix(value)
            if "url(" in value and key != "clip-path":
                raise VectorUnsupported(tr("Risorsa SVG non supportata"))
            if key == "clip-path" and not re.fullmatch(r"url\(#[\w.-]+\)", value):
                raise VectorUnsupported(tr("Risorsa SVG non supportata"))
            if key == f"{{{XLINK}}}href" and not re.fullmatch(r"#[\w.-]+", value):
                raise VectorUnsupported("Risorsa SVG esterna")
        if "id" in element.attrib:
            ident = element.attrib["id"]
            if ident in ids or ident.startswith("apex_layer_"):
                raise VectorUnsupported(tr("Identificatore SVG non valido"))
            ids[ident] = element
    for element in nodes:
        href = element.get(f"{{{XLINK}}}href")
        # Restrict use to leaf paths: no recursive references or hidden subtrees.
        if href and (href[1:] not in ids or ids[href[1:]].tag != f"{{{SVG}}}path"):
            raise VectorUnsupported(tr("Riferimento SVG non supportato"))

    output = ET.Element(root.tag, root.attrib)
    for child in root:
        if child.tag == f"{{{SVG}}}defs":
            defs = ET.SubElement(output, child.tag)
            defs.extend(copy.deepcopy(e) for e in child if e.tag != f"{{{SVG}}}clipPath")
    layers = []
    chunk = []
    chunk_ancestors = None
    chunk_clips = None

    def flush():
        nonlocal chunk
        if not chunk:
            return
        if len(layers) >= MAX_LAYERS:
            raise VectorUnsupported("Troppi ritagli SVG")
        ident = f"apex_layer_{len(layers)}"
        group = ET.SubElement(output, f"{{{SVG}}}g", {"id": ident})
        for attributes in chunk_ancestors:
            group = ET.SubElement(group, f"{{{SVG}}}g", attributes)
        group.extend(chunk)
        layers.append({"id": ident, "clips": chunk_clips})
        chunk = []

    def visit(element, ancestors, clips, depth=0):
        nonlocal chunk_ancestors, chunk_clips
        if depth > 64:
            raise VectorUnsupported("SVG troppo annidato")
        if element.tag == f"{{{SVG}}}defs":
            return
        if element.tag == f"{{{SVG}}}g":
            attributes = {k: v for k, v in element.attrib.items() if k not in ("id", "clip-path")}
            # Splitting opacity groups changes compositing, so retain PDFium.
            if "opacity" in attributes:
                raise VectorUnsupported("Trasparenza di gruppo")
            ancestors = [*ancestors, attributes]
            reference = element.get("clip-path")
            if reference:
                clip = ids.get(reference[5:-1])
                if clip is None or clip.tag != f"{{{SVG}}}clipPath" or len(clip) != 1:
                    raise VectorUnsupported(tr("Ritaglio SVG non supportato"))
                path = clip[0]
                if path.tag != f"{{{SVG}}}path" or set(clip.attrib) != {"id"}:
                    raise VectorUnsupported(tr("Ritaglio SVG non supportato"))
                transforms = [matrix(a["transform"]) for a in ancestors if "transform" in a]
                if path.get("transform"):
                    transforms.append(matrix(path.get("transform")))
                clips = [*clips, {"commands": clip_commands(path.get("d", "")),
                                 "transforms": transforms, "rule": path.get("clip-rule", "nonzero")}]
            for child in element:
                visit(child, ancestors, clips, depth + 1)
        elif element.tag in (f"{{{SVG}}}path", f"{{{SVG}}}use"):
            if element.get("clip-path"):
                raise VectorUnsupported("Ritaglio su elemento SVG")
            if ancestors != chunk_ancestors or clips != chunk_clips:
                flush()
                chunk_ancestors, chunk_clips = ancestors, clips
            chunk.append(copy.deepcopy(element))
        else:
            raise VectorUnsupported(tr("Elemento SVG non supportato"))

    for element in root:
        visit(element, [], [])
    flush()
    if sum(1 for _ in output.iter()) > MAX_NODES:
        raise VectorUnsupported("SVG fuori limite")
    ET.register_namespace("", SVG)
    ET.register_namespace("xlink", XLINK)
    svg = ET.tostring(output, encoding="utf-8")
    if len(svg) > MAX_SVG_BYTES:
        raise VectorUnsupported("SVG fuori limite")
    return svg, layers
