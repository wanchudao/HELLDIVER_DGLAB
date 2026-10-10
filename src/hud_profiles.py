"""Measured 1080p HUD geometry; unrecorded scales remain interpolation candidates.

Coordinates are half-open original-image pixels.  The measurement strip lies
inside the full health slot, while cap coordinates refer to the vertical stems,
not to the currently filled white rectangle.
"""
from copy import deepcopy
from fractions import Fraction


SCALES = tuple(range(75, 126, 5))
PROFILE_VERSION = "hud1080_v1"
RESOLUTION = (1920, 1080)
MEASURED_SCALES = (75, 80, 85, 90, 95, 100, 125)

# x1, y1, x2, y2 of the slot; cap stem centers; cap top/bottom;
# cap stem thickness.  Includes antialiased slot pixels at the 1.25 endpoint.
_MEASURED = {
    75: (91, 1009, 247, 1017, 88, 249, 1003, 1023, 3),
    80: (97, 1005, 264, 1013, 94, 266, 998, 1020, 3),
    85: (103, 1000, 280, 1009, 100, 283, 993, 1016, 3),
    90: (109, 995, 297, 1004, 106, 299, 988, 1012, 3),
    95: (115, 991, 313, 1000, 112, 316, 983, 1008, 3),
    100: (121, 986, 330, 996, 118, 333, 977, 1005, 4),
    125: (151, 962, 413, 975, 147, 416, 952, 986, 5),
}


def _round_pixel(value):
    """Round positive rational pixel coordinates halfway upwards, once."""
    value = Fraction(value)
    return (value.numerator * 2 + value.denominator) // (2 * value.denominator)


def _check_scale(percent):
    if type(percent) is not int or percent not in SCALES:
        raise ValueError("HUD 档位必须是 75 到 125 之间每隔 5 的整数")
    return percent


def source_label(percent):
    _check_scale(percent)
    return "已有满血实拍" if percent in MEASURED_SCALES else "插值推算"


def get_profile(percent):
    """Return a fresh ROI and strict HUD gate suitable for session INI storage."""
    _check_scale(percent)
    if percent in _MEASURED:
        geometry = _MEASURED[percent]
    else:
        step = Fraction(percent - 100, 25)
        geometry = tuple(_round_pixel(a + (b - a) * step)
                         for a, b in zip(_MEASURED[100], _MEASURED[125]))
    x1, y1, x2, y2, left, right, cap_top, cap_bottom, thickness = geometry
    # Exclude antialiasing rows while retaining the original-pixel slot width.
    sample_top, sample_bottom = y1 + 2, y2 - 2
    roi = {
        "outer_x1": left - thickness - 4,
        "outer_y1": cap_top - 3,
        "outer_x2": right + thickness + 5,
        "outer_y2": cap_bottom + 3,
        "inner_x1": x1, "inner_y1": sample_top,
        "inner_x2": x2, "inner_y2": sample_bottom,
        "bar_min_x": x1, "bar_max_x": x2,
        "cap_ignore_left": 0, "cap_ignore_right": 0,
    }
    hud = {
        "version": PROFILE_VERSION, "scale_percent": percent,
        "source": "measured_full_health" if percent in MEASURED_SCALES else "interpolated",
        "frame_width": 1920, "frame_height": 1080,
        "cap_left_x": left, "cap_right_x": right,
        "cap_center_y": _round_pixel(Fraction(cap_top + cap_bottom - 1, 2)),
        "cap_top": cap_top, "cap_bottom": cap_bottom,
        "cap_stem_width": thickness,
        "slot_y1": y1, "slot_y2": y2,
        "position_tolerance_px": 2, "center_tolerance_px": 2,
        "spacing_tolerance_px": 3,
    }
    return deepcopy({"roi": roi, "hud": hud})


def validate_hud_section(hud, roi):
    """Reject edited/corrupt geometry before capture or output can start."""
    if not isinstance(hud, dict) or not isinstance(roi, dict):
        raise ValueError("HUD 配置必须包含完整档位和 ROI")
    percent = _check_scale(hud.get("scale_percent"))
    expected = get_profile(percent)
    if set(hud) != set(expected["hud"]):
        raise ValueError("HUD 配置字段缺失或未知，请重新保存 HUD 设置")
    for key, value in expected["hud"].items():
        actual = hud[key]
        if type(actual) is not type(value) or actual != value:
            raise ValueError(f"HUD 配置 {key} 无效，请重新保存 HUD 设置")
    for key, value in expected["roi"].items():
        if type(roi.get(key)) is not int or roi[key] != value:
            raise ValueError(f"HUD ROI {key} 与档位不一致，请重新保存 HUD 设置")
    return expected
