"""Bounded stroke geometry shared with the canvas; no PDF parsing or Qt."""
import math

MAX_FLAT_POINTS = 50000


def segments(points, smooth=True):
    yield 'M', points[0]
    if len(points) == 1:
        yield 'L', (points[0][0] + .01, points[0][1])
    elif not smooth or len(points) == 2:
        for point in points[1:]:
            yield 'L', point
    else:
        for index in range(1, len(points) - 1):
            yield 'Q', (points[index], ((points[index][0] + points[index+1][0]) / 2,
                                      (points[index][1] + points[index+1][1]) / 2))
        yield 'L', points[-1]


def flatten(points, smooth=True, tolerance=.12):
    result = []
    for kind, value in segments(points, smooth):
        if kind != 'Q':
            result.append(tuple(value))
            continue
        control, end = value
        stack = [(result[-1], control, end, 0)]
        while stack:
            start, control, end, depth = stack.pop()
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = dx*dx + dy*dy
            projection = (control[0]-start[0])*dx + (control[1]-start[1])*dy
            deviation = ((control[0]-start[0])*dy - (control[1]-start[1])*dx)**2
            if depth >= 12 or (length and 0 <= projection <= length and deviation <= tolerance*tolerance*length) or (
                    not length and math.dist(start, control) <= tolerance):
                result.append(tuple(end))
                if len(result) > MAX_FLAT_POINTS:
                    raise ValueError('Ink geometry exceeds budget')
            else:
                left = ((start[0]+control[0])/2, (start[1]+control[1])/2)
                right = ((control[0]+end[0])/2, (control[1]+end[1])/2)
                middle = ((left[0]+right[0])/2, (left[1]+right[1])/2)
                stack.append((middle, right, end, depth+1))
                stack.append((start, left, middle, depth+1))
    return result


def _circle(a, b, center, radius):
    dx, dy = b[0]-a[0], b[1]-a[1]
    x, y = a[0]-center[0], a[1]-center[1]
    aa = dx*dx + dy*dy
    if aa < 1e-20:
        return (0., 1.) if x*x+y*y <= radius*radius else None
    bb, cc = 2*(x*dx+y*dy), x*x+y*y-radius*radius
    discriminant = bb*bb-4*aa*cc
    if discriminant < 0:
        return None
    root = math.sqrt(max(0, discriminant))
    left, right = max(0., (-bb-root)/(2*aa)), min(1., (-bb+root)/(2*aa))
    return (left, right) if right >= left else None


def _capsule(a, b, start, end, radius):
    intervals = [hit for center in (start, end) if (hit := _circle(a, b, center, radius))]
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = math.hypot(dx, dy)
    if length > 1e-10:
        ux, uy = dx/length, dy/length
        origin = (a[0]-start[0], a[1]-start[1])
        delta = (b[0]-a[0], b[1]-a[1])
        left, right = 0., 1.
        for px, py, lower, upper in ((ux, uy, 0., length), (-uy, ux, -radius, radius)):
            base, slope = origin[0]*px+origin[1]*py, delta[0]*px+delta[1]*py
            if abs(slope) < 1e-12:
                if not lower <= base <= upper:
                    left, right = 1., 0.
                    break
            else:
                first, last = sorted(((lower-base)/slope, (upper-base)/slope))
                left, right = max(left, first), min(right, last)
        if right >= left:
            intervals.append((left, right))
    return (min(v[0] for v in intervals), max(v[1] for v in intervals)) if intervals else None


def cut_paths(paths, start, end, radius, width, smooth=True):
    """Subtract one swept eraser disk, returning None when nothing was hit.

    Cuts intersect segments, even when no sampled vertex lies inside the disk.
    Round stroke caps are included in the clearance. Curves are flattened to
    0.12 pt only when an annotation actually changes; fragments stay polylines.
    """
    if (not math.isfinite(radius) or not .1 <= radius <= 10000 or
            not math.isfinite(width) or not .01 <= width <= 10000 or
            any(not math.isfinite(v) or abs(v) > 1e7 for point in (start, end) for v in point)):
        raise ValueError('Invalid eraser geometry')
    radius += width/2
    bounds = (min(start[0], end[0])-radius, min(start[1], end[1])-radius,
              max(start[0], end[0])+radius, max(start[1], end[1])+radius)
    result, changed, total = [], False, 0
    for points in paths:
        flat = flatten(points, smooth)
        total += len(flat)
        if total > MAX_FLAT_POINTS:
            raise ValueError('Ink geometry exceeds budget')
        current = []
        for a, b in zip(flat, flat[1:]):
            hit = None
            if not (max(a[0], b[0]) < bounds[0] or min(a[0], b[0]) > bounds[2] or
                    max(a[1], b[1]) < bounds[1] or min(a[1], b[1]) > bounds[3]):
                hit = _capsule(a, b, start, end, radius)
            if hit is None or hit[1]-hit[0] < 1e-10:
                if not current:
                    current = [a]
                current.append(b)
                continue
            changed = True
            left, right = hit
            interpolate = lambda t: (a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1]))
            if left > 1e-10:
                if not current:
                    current = [a]
                current.append(interpolate(left))
            if current:
                result.append(current)
                current = []
            if right < 1-1e-10:
                current = [interpolate(right), b]
        if current:
            result.append(current)
    return result if changed else None
