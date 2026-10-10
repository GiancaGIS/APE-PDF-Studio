"""Displayed (rotated, cropped, UserUnit-scaled) points and PDF user space."""
def to_pdf(page, x, y):
    x0, y0, x1, y1 = map(float, page.cropbox)
    unit = float(page.get("/UserUnit", 1))
    x, y = x / unit, y / unit
    rotation = page.rotation % 360
    if rotation == 90:
        return x0 + y, y0 + x
    if rotation == 180:
        return x1 - x, y0 + y
    if rotation == 270:
        return x1 - y, y1 - x
    return x0 + x, y1 - y


def from_pdf(page, x, y):
    x0, y0, x1, y1 = map(float, page.cropbox)
    rotation = page.rotation % 360
    if rotation == 90:
        result = y - y0, x - x0
    elif rotation == 180:
        result = x1 - x, y - y0
    elif rotation == 270:
        result = y1 - y, x1 - x
    else:
        result = x - x0, y1 - y
    unit = float(page.get("/UserUnit", 1))
    return result[0] * unit, result[1] * unit


def displayed_size(page):
    width, height = float(page.cropbox.width), float(page.cropbox.height)
    if page.rotation % 180:
        width, height = height, width
    unit = float(page.get("/UserUnit", 1))
    return width * unit, height * unit
