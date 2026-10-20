"""Independent reference-pixel synthetic HUDs; never recorded high-res gameplay.

The reference table is pinned to the original measurement evidence, not read
from the production profile generator. Missing 1080p scales are explicitly
interpolated reference values; higher-resolution frames are synthetic only.
"""
from fractions import Fraction

import numpy as np

REFERENCE = {
    75: (91, 1009, 247, 1017, 88, 249, 1003, 1023, 3),
    80: (97, 1005, 264, 1013, 94, 266, 998, 1020, 3),
    85: (103, 1000, 280, 1009, 100, 283, 993, 1016, 3),
    90: (109, 995, 297, 1004, 106, 299, 988, 1012, 3),
    95: (115, 991, 313, 1000, 112, 316, 983, 1008, 3),
    100: (121, 986, 330, 996, 118, 333, 977, 1005, 4),
    105: (127, 981, 347, 992, 124, 350, 972, 1001, 4),
    110: (133, 976, 363, 988, 130, 366, 967, 997, 4),
    115: (139, 972, 380, 983, 135, 383, 962, 994, 5),
    120: (145, 967, 396, 979, 141, 399, 957, 990, 5),
    125: (151, 962, 413, 975, 147, 416, 952, 986, 5),
}


def reference_geometry(scale, resolution):
    width, height = resolution
    if (width, height) not in {(1920, 1080), (2560, 1440), (3840, 2160)}:
        raise ValueError("Unsupported synthetic fixture size")
    factor = Fraction(width, 1920)
    def rounded(value):
        product = value * factor
        return (2 * product.numerator + product.denominator) // (2 * product.denominator)
    return tuple(rounded(value) for value in REFERENCE[scale])


def synthetic_frame(scale, resolution, hp=1., *, shift=(0, 0), cap_color=(240, 240, 240)):
    x1, y1, x2, y2, left, right, top, bottom, thickness = reference_geometry(scale, resolution)
    dx, dy = shift
    frame = np.full((resolution[1], resolution[0], 3), 20, dtype=np.uint8)
    for x, inward in ((left, 1), (right, -1)):
        first, last = x + dx - thickness // 2, x + dx + (thickness + 1) // 2
        frame[top + dy:bottom + dy, first:last] = cap_color
        tip_first, tip_last = (first, last + thickness) if inward == 1 else (first - thickness, last)
        tip_height = max(1, round(resolution[0] / 1920))
        frame[top + dy:top + dy + tip_height, tip_first:tip_last] = cap_color
        frame[bottom + dy - tip_height:bottom + dy, tip_first:tip_last] = cap_color
    frame[y1 + dy:y2 + dy, x1 + dx:x2 + dx] = 30
    frame[y1 + dy:y2 + dy, x1 + dx:x1 + dx + round((x2 - x1) * hp)] = 230
    return frame
