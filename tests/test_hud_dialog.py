from types import SimpleNamespace

import pytest
from PIL import Image

from src.hud_dialog import (HUD_CHOICES, LEGACY_LABEL, HudSettingsDialog,
                            diagnostic_crop, diagnostic_summary, selected_values)


def test_ui_offers_exact_steps_and_no_freeform_conversion():
    assert HUD_CHOICES == (LEGACY_LABEL, "0.75", "0.80", "0.85", "0.90", "0.95", "1.00",
                           "1.05", "1.10", "1.15", "1.20", "1.25")
    assert selected_values("1.25") == {"mode": "manual", "scale_percent": 125}
    for bad in ("0.76", "1", "100", "1.250", " 1.25"):
        with pytest.raises(ValueError):
            selected_values(bad)


def test_dialog_remains_readonly_for_active_f8_paused_session():
    states = {}
    hint = []
    dialog = object.__new__(HudSettingsDialog)
    dialog.is_running = lambda: True
    dialog.store = SimpleNamespace(error="")
    dialog.scale_box = SimpleNamespace(configure=lambda **value: states.update(scale=value))
    dialog.save_button = SimpleNamespace(configure=lambda **value: states.update(save=value))
    dialog.hint = SimpleNamespace(set=hint.append)
    dialog.update_enabled()
    assert states == {"scale": {"state": "disabled"}, "save": {"state": "disabled"}}
    assert "F8 暂停不会解锁" in hint[-1]


def test_invalid_hud_placeholder_zero_is_not_displayed_as_death_or_health_zero():
    summary = diagnostic_summary({"health_raw": 0.0, "hud_valid": False,
        "first_frame_valid": False, "reject_reason": "hud_missing", "resolution": "1920x1080"})
    assert "原始血量估计：--" in summary
    assert "定位无效" in summary and "未找到有效 HUD" in summary
    assert "0.0%" not in summary


def test_single_frame_warmup_preserves_measured_raw_hp_without_claiming_temporal_validity():
    summary = diagnostic_summary({"health_raw": 1.0, "hud_valid": True,
        "first_frame_valid": False, "first_frame_reason": "observation_warmup",
        "resolution": "1920x1080", "hud_scale": 100, "hud_source": "已有满血实拍"})
    assert "原始血量估计：100.0%" in summary
    assert "首帧有效：否" in summary and "正常预热" in summary
    assert "1.00" in summary and "已有满血实拍" in summary


def test_report_only_preview_reads_unicode_path_and_draws_given_roi(tmp_path):
    path = tmp_path / "中文原始截图.png"
    Image.new("RGB", (200, 100), (15, 25, 35)).save(path)
    record = {"outer_coords": [40, 60, 140, 90], "inner_coords": [45, 70, 135, 80],
              "bar_bounds": [47, 133], "cap_positions": [42, 137]}
    preview, bounds = diagnostic_crop(path, record)
    assert bounds == (16, 44, 164, 100)
    assert preview.size[0] > 148
    colors = {preview.getpixel((x, y)) for x in range(preview.width) for y in range(preview.height)}
    assert (0, 221, 255) in colors and (238, 102, 255) in colors
    assert (76, 255, 113) in colors


def test_mismatched_resolution_without_reported_roi_has_no_fabricated_preview(tmp_path):
    with pytest.raises(ValueError, match="报告未提供"):
        diagnostic_crop(tmp_path / "does-not-exist.jpg", {"resolution": "1280x720"})
