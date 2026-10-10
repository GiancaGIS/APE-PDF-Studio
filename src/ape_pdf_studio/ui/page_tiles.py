"""Viewport tiles retain zoom resolution without allocating whole poster pages."""
from dataclasses import dataclass
from math import ceil, floor, sqrt

from PySide6.QtCore import QRect, QRectF, QSize
from PySide6.QtPdf import QPdfDocumentRenderOptions

TILE_EDGE = 1024
CACHE_BYTES = 64 * 1024 * 1024
MAX_PENDING = 2
VISIBLE_PIXEL_BUDGET = 12 * 1024 * 1024


@dataclass(frozen=True)
class PageTile:
    page: int
    full_width: int
    full_height: int
    left: int
    top: int
    width: int
    height: int

    def target_rect(self, page_rect):
        return QRectF(page_rect.left() + self.left * page_rect.width() / self.full_width,
                      page_rect.top() + self.top * page_rect.height() / self.full_height,
                      self.width * page_rect.width() / self.full_width,
                      self.height * page_rect.height() / self.full_height)

    def render_options(self):
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        options.setScaledSize(QSize(self.full_width, self.full_height))
        # Qt's clip matrix uses QRect.right()/bottom(), which are inclusive.
        # Exclusive bounds preserve the full-page pixel scale and avoid seams.
        options.setScaledClipRect(QRect(self.left, self.top, self.width + 1, self.height + 1))
        return options


def visible_tiles(page, page_rect, viewport, pixel_ratio):
    intersection = page_rect.intersected(viewport)
    if intersection.isEmpty():
        return []
    full = (page_rect.size() * pixel_ratio).toSize()
    full_width, full_height = max(1, full.width()), max(1, full.height())
    sx, sy = full_width / page_rect.width(), full_height / page_rect.height()
    left = max(0, floor((intersection.left() - page_rect.left()) * sx))
    top = max(0, floor((intersection.top() - page_rect.top()) * sy))
    right = min(full_width, ceil((intersection.right() - page_rect.left()) * sx))
    bottom = min(full_height, ceil((intersection.bottom() - page_rect.top()) * sy))
    return [PageTile(page, full_width, full_height, x, y,
                     min(TILE_EDGE, full_width - x), min(TILE_EDGE, full_height - y))
            for y in range(top // TILE_EDGE * TILE_EDGE, bottom, TILE_EDGE)
            for x in range(left // TILE_EDGE * TILE_EDGE, right, TILE_EDGE)]


def viewport_plan(pages, viewport, pixel_ratio):
    """Reserve cache space for every visible tile, even on unusually large screens."""
    for _ in range(16):
        plan = {tile: tile.target_rect(rect) for page, rect in pages.items()
                for tile in visible_tiles(page, rect, viewport, pixel_ratio)}
        pixels = sum(tile.width * tile.height for tile in plan)
        if pixels <= VISIBLE_PIXEL_BUDGET:
            center = viewport.center()
            return dict(sorted(plan.items(), key=lambda item:
                (item[1].center().x() - center.x()) ** 2 + (item[1].center().y() - center.y()) ** 2))
        # Limit the viewport raster footprint, rather than the entire page.
        pixel_ratio *= min(.9, sqrt(VISIBLE_PIXEL_BUDGET / pixels))
    return {}
