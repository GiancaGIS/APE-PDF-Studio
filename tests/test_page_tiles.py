"""Tile geometry must cover the visible page at the physical display resolution."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QRectF

from ape_pdf_studio.ui.page_tiles import visible_tiles, viewport_plan, VISIBLE_PIXEL_BUDGET, TILE_EDGE


@pytest.mark.parametrize("ratio", [1., 1.25, 1.5, 2.])
def test_poster_zoom_retains_full_resolution_for_only_the_visible_region(ratio):
    page = QRectF(-10000, -9000, 84480, 65280)
    viewport = QRectF(0, 0, 1000, 700)
    plan = viewport_plan({0: page}, viewport, ratio)
    assert plan
    assert all(tile.full_width == round(page.width() * ratio) for tile in plan)
    assert all(tile.full_height == round(page.height() * ratio) for tile in plan)
    assert all(tile.width <= TILE_EDGE and tile.height <= TILE_EDGE for tile in plan)
    assert len(plan) <= 12
    # Sample the viewport including borders and tile boundaries: no gaps.
    for y in range(0, 700, 10):
        for x in range(0, 1000, 10):
            assert any(rect.contains(x, y) for rect in plan.values())


def test_tiles_are_reused_when_scrolling_inside_the_same_grid_cells():
    viewport = QRectF(0, 0, 400, 300)
    first = visible_tiles(0, QRectF(-1400, -1300, 12000, 9000), viewport, 1.)
    second = visible_tiles(0, QRectF(-1450, -1350, 12000, 9000), viewport, 1.)
    assert first == second


def test_edge_tiles_cover_page_boundaries_without_leaving_the_page():
    page = QRectF(-4200, -3900, 5000, 4500)
    viewport = QRectF(0, 0, 1000, 700)
    plan = viewport_plan({2: page}, viewport, 1.)
    assert plan
    assert all(tile.left + tile.width <= 5000 and tile.top + tile.height <= 4500 for tile in plan)
    assert any(rect.right() == page.right() and rect.bottom() == page.bottom() for rect in plan.values())
    assert visible_tiles(0, QRectF(1200, 1200, 100, 100), viewport, 1.) == []


def test_extreme_viewport_still_fits_the_bounded_visible_cache():
    page = QRectF(-1000, -1000, 100000, 100000)
    viewport = QRectF(0, 0, 15000, 10000)
    plan = viewport_plan({0: page}, viewport, 3.)
    assert plan
    assert sum(tile.width * tile.height for tile in plan) <= VISIBLE_PIXEL_BUDGET


def test_render_priority_starts_at_viewport_center():
    viewport = QRectF(0, 0, 2200, 1500)
    plan = viewport_plan({0: QRectF(-200, -300, 12000, 9000)}, viewport, 1.)
    center = viewport.center()
    distances = [(rect.center().x() - center.x()) ** 2 + (rect.center().y() - center.y()) ** 2 for rect in plan.values()]
    assert distances == sorted(distances)
