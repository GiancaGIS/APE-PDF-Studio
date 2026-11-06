"""Exact segment cuts, swept erasers and conservative curve flattening."""
import math
import pytest
from ape_pdf_studio.core.ink_geometry import cut_paths, flatten


def test_circle_cuts_between_sampled_vertices_and_keeps_both_sides():
    result = cut_paths([[(0, 50), (100, 50)]], (50, 50), (50, 50), 5, 2, False)
    assert len(result) == 2
    assert result[0][0] == pytest.approx((0, 50))
    assert result[0][-1] == pytest.approx((44, 50))
    assert result[1][0] == pytest.approx((56, 50))
    assert result[1][-1] == pytest.approx((100, 50))


@pytest.mark.parametrize('reverse', (False, True))
def test_fast_swept_eraser_does_not_skip_a_crossing(reverse):
    start, end = ((50, 0), (50, 100)) if not reverse else ((50, 100), (50, 0))
    result = cut_paths([[(0, 50), (100, 50)]], start, end, 3, 2, False)
    assert result[0][-1][0] == pytest.approx(46)
    assert result[1][0][0] == pytest.approx(54)


def test_diagonal_capsule_cuts_only_its_footprint():
    result = cut_paths([[(0, 50), (100, 50)]], (25, 0), (75, 100), 2, 2, False)
    offset = 3 * math.sqrt(1.25)
    assert result[0][-1][0] == pytest.approx(50-offset)
    assert result[1][0][0] == pytest.approx(50+offset)


def test_missed_and_tangent_strokes_are_unchanged():
    paths = [[(0, 50), (100, 50)]]
    assert cut_paths(paths, (50, 80), (50, 80), 5, 2, False) is None
    assert cut_paths(paths, (50, 56), (50, 56), 5, 2, False) is None


def test_dot_and_whole_line_can_be_removed_without_affecting_other_paths():
    assert cut_paths([[(50, 50)]], (50, 50), (50, 50), 5, 2) == []
    result = cut_paths([[(45, 50), (55, 50)], [(0, 90), (100, 90)]], (50, 50), (50, 50), 10, 2, False)
    assert result == [[(0, 90), (100, 90)]]


def test_flattening_retains_a_collinear_curve_that_doubles_back():
    path = flatten([(0, 0), (200, 0), (0, 0)])
    assert max(point[0] for point in path) > 130
    assert path[0] == path[-1] == (0, 0)


def test_curved_fragments_do_not_bridge_the_erased_gap():
    points = [(0, 50), (25, 10), (50, 50), (75, 90), (100, 50)]
    result = cut_paths([points], (50, 0), (50, 100), 5, 2)
    assert result and len(result) >= 2
    assert all(not 44.001 < x < 55.999 for path in result for x, y in path)
    assert all(not (min(a[0], b[0]) < 44 and max(a[0], b[0]) > 56)
               for path in result for a, b in zip(path, path[1:]))


@pytest.mark.parametrize('radius,width', ((math.nan, 2), (math.inf, 2), (-1, 2), (2, math.inf), (2, 0)))
def test_invalid_eraser_numbers_are_rejected(radius, width):
    with pytest.raises(ValueError):
        cut_paths([[(0, 0), (100, 0)]], (50, 0), (50, 0), radius, width)
