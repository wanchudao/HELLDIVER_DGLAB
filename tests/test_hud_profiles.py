"""1080p HUD tests: recorded full frames and separately labelled synthetic bars."""
import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.config_loader import load_config
from src.health_detector import HealthDetector
from src.hud_profiles import (MEASURED_SCALES, PROFILE_VERSION, SCALES,
                              get_profile, source_label, validate_hud_section)
from src.roi_locator import RoiLocator
from src.trigger import TriggerDispatch
from src.vision_pipeline import VisionPipeline

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "screenshots" / "hud_1080p_20261002"


def hud_config(scale):
    config = load_config(ROOT / "config.example.ini")
    profile = get_profile(scale)
    config["ROI"], config["hud"] = profile["roi"], profile["hud"]
    return config


def synthetic_hud_frame(scale, hp=1.0, *, shift=(0, 0), cap_color=(240, 240, 240)):
    """Controlled original-pixel bracket fixture, never a recorded gameplay claim."""
    config = hud_config(scale)
    roi, hud = config["ROI"], config["hud"]
    dx, dy = shift
    frame = np.full((1080, 1920, 3), 20, np.uint8)
    top, bottom = hud["cap_top"] + dy, hud["cap_bottom"] + dy
    thickness = hud["cap_stem_width"]
    for x, inward in ((hud["cap_left_x"] + dx, 1), (hud["cap_right_x"] + dx, -1)):
        left, right = x - thickness // 2, x + (thickness + 1) // 2
        frame[top:bottom, left:right] = cap_color
        tip_left, tip_right = ((left, right + thickness) if inward == 1
                               else (left - thickness, right))
        frame[top:top + 1, tip_left:tip_right] = cap_color
        frame[bottom - 1:bottom, tip_left:tip_right] = cap_color
    width = roi["bar_max_x"] - roi["bar_min_x"]
    start, stop = roi["bar_min_x"] + dx, roi["bar_max_x"] + dx
    fill_top, fill_bottom = hud["slot_y1"] + dy, hud["slot_y2"] + dy
    frame[fill_top:fill_bottom, start:stop] = 30
    frame[fill_top:fill_bottom, start:start + round(width * hp)] = 230
    return frame


def test_exact_integer_scales_and_half_up_interpolation():
    assert SCALES == tuple(range(75, 126, 5)) and len(SCALES) == 11
    first, last = get_profile(100), get_profile(125)
    for scale in (105, 110, 115, 120):
        profile = get_profile(scale)
        for key in ("cap_left_x", "cap_right_x", "cap_top", "cap_bottom", "slot_y1", "slot_y2"):
            value = Fraction(first["hud"][key]) + Fraction(scale - 100, 25) * (
                last["hud"][key] - first["hud"][key])
            assert profile["hud"][key] == (value.numerator * 2 + value.denominator) // (2 * value.denominator)
        assert profile["hud"]["source"] == "interpolated"
        assert source_label(scale) == "插值推算"
    assert PROFILE_VERSION == "hud1080_v1"
    first["hud"]["cap_left_x"] = 999
    assert get_profile(100)["hud"]["cap_left_x"] == 118


@pytest.mark.parametrize("scale", [None, True, False, 1.0, 75.0, 76, 74, 130, "100"])
def test_illegal_scale_rejected(scale):
    with pytest.raises(ValueError):
        get_profile(scale)


@pytest.mark.parametrize("scale", MEASURED_SCALES)
def test_recorded_full_health_accepts_correct_profile(scale):
    config = hud_config(scale)
    frame = cv2.imread(str(MATERIALS / f"hud_{scale:03d}_full.jpg"))
    assert frame.shape == (1080, 1920, 3)
    roi = RoiLocator(config).locate(frame)
    assert roi.valid and not roi.reject_reason
    health = HealthDetector(config).detect(roi.inner_img)
    assert health.health_raw == 1 and health.confidence >= config["unknown"]["min_confidence"]
    assert len(roi.cap_positions) == 2


@pytest.mark.parametrize("actual,selected", [(actual, selected)
                          for actual in MEASURED_SCALES for selected in SCALES if actual != selected])
def test_recorded_full_health_rejects_every_wrong_profile(actual, selected):
    frame = cv2.imread(str(MATERIALS / f"hud_{actual:03d}_full.jpg"))
    roi = RoiLocator(hud_config(selected)).locate(frame)
    assert not roi.valid and roi.reject_reason


def test_archived_originals_match_provenance_and_auxiliary_is_not_calibration():
    manifest = json.loads((MATERIALS / "provenance.json").read_text(encoding="utf-8"))
    for entry in manifest["samples"]:
        path = MATERIALS / entry["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        frame = cv2.imread(str(path))
        assert frame.shape[1::-1] == (entry["width"], entry["height"])
    auxiliary = cv2.imread(str(MATERIALS / "hud_100_aux_1919x1079.png"))
    roi = RoiLocator(hud_config(100)).locate(auxiliary)
    assert not roi.valid and roi.reject_reason == "hud_frame_size_mismatch"


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("hp", [0, .01, .4, .74, .75, .76, 1])
def test_synthetic_geometry_and_slot_measurement(scale, hp):
    config = hud_config(scale)
    roi = RoiLocator(config).locate(synthetic_hud_frame(scale, hp))
    assert roi.valid, roi.reject_reason
    health = HealthDetector(config).detect(roi.inner_img)
    width = config["ROI"]["bar_max_x"] - config["ROI"]["bar_min_x"]
    assert health.health_raw == pytest.approx(round(width * hp) / width, abs=1 / width)
    assert health.confidence >= config["unknown"]["min_confidence"]
    if hp == 0:
        assert health.health_raw == 0
    if hp == .01:
        assert health.health_raw > 0


@pytest.mark.parametrize("shift,valid", [((2, 0), True), ((-2, 0), True),
                                       ((0, 2), True), ((0, -2), True),
                                       ((3, 0), False), ((-3, 0), False),
                                       ((0, 3), False), ((0, -3), False)])
@pytest.mark.parametrize("scale", SCALES)
def test_strict_position_tolerances(scale, shift, valid):
    result = RoiLocator(hud_config(scale)).locate(synthetic_hud_frame(scale, shift=shift))
    assert result.valid is valid, (shift, result.reject_reason, result.cap_positions)


@pytest.mark.parametrize("scale", SCALES)
def test_synthetic_dark_red_caps_remain_hud_evidence(scale):
    roi = RoiLocator(hud_config(scale)).locate(
        synthetic_hud_frame(scale, 0, cap_color=(20, 20, 100)))
    assert roi.valid, roi.reject_reason


def test_duplicate_stem_is_ambiguous_not_an_alternative_match():
    config = hud_config(110)
    frame = synthetic_hud_frame(110)
    hud = config["hud"]
    x = hud["cap_left_x"] - 6
    frame[hud["cap_top"]:hud["cap_bottom"], x - 1:x + 2] = 240
    roi = RoiLocator(config).locate(frame)
    assert not roi.valid


def test_independent_cap_offsets_cannot_exceed_spacing_tolerance():
    config = hud_config(110)
    frame = synthetic_hud_frame(110)
    hud = config["hud"]
    left, right = hud["cap_left_x"], hud["cap_right_x"]
    x1, x2 = left - 8, left + 9
    frame[:, x1 - 2:x2 - 2] = frame[:, x1:x2].copy()
    frame[:, x2 - 2:x2] = 20
    x1, x2 = right - 8, right + 9
    frame[:, x1 + 2:x2 + 2] = frame[:, x1:x2].copy()
    frame[:, x1:x1 + 2] = 20
    roi = RoiLocator(config).locate(frame)
    assert not roi.valid and roi.reject_reason == "hud_cap_spacing_mismatch"


@pytest.mark.parametrize("section,key,value", [("hud", "position_tolerance_px", 10),
                                             ("hud", "scale_percent", 76),
                                             ("hud", "scale_percent", True),
                                             ("hud", "version", "obsolete"),
                                             ("hud", "source", "unverified"),
                                             ("ROI", "inner_x1", 1)])
def test_edited_or_corrupt_session_geometry_fails_before_processing(section, key, value):
    config = hud_config(110)
    config[section][key] = value
    with pytest.raises(ValueError):
        RoiLocator(config)


@pytest.mark.parametrize("scale", (105, 110, 115, 120))
def test_synthetic_events_and_loss_rebaseline_without_new_thresholds(scale):
    config = hud_config(scale)
    pipeline = VisionPipeline(config)
    clock = [0.0]
    log = SimpleNamespace(append=lambda entry: None, close=lambda: None)
    trigger = TriggerDispatch(config, clock=lambda: clock[0], log_writer=log)
    records = []
    frames = ([synthetic_hud_frame(scale, 1)] * 4
              + [synthetic_hud_frame(scale, .74)] * 12
              + [synthetic_hud_frame(scale, .9)] * 8
              + [synthetic_hud_frame(scale, 0)] * 8
              + [np.zeros((1080, 1920, 3), np.uint8)] * 2
              + [synthetic_hud_frame(scale, .5)] * 6)
    try:
        for i, frame in enumerate(frames):
            clock[0] = i / 30
            result, roi, health = pipeline.process(frame, clock[0])
            event = trigger.dispatch(result)
            records.append((result, event, trigger.mock_output()))
        actions = [action for _, event, _ in records for action in event.get("actions", [])]
        assert actions == ["injury_pulse", "heal_pulse", "death_pulse"]
        assert records[15][1]["low_health"]
        assert not records[23][1]["low_health"]
        assert records[23][1]["healing_sustain"]
        assert not records[32][0].valid and records[32][2]["strength"] == 0
        assert pipeline.health_detector._ema_value >= 0
        assert records[34][0].debug["reject_reason"] == "observation_warmup"
        assert not records[35][1].get("actions")
        assert records[-1][0].valid
        assert config["trigger"]["low_health_hp_threshold"] == .75
        assert config["trigger"]["injury_window_sec"] == .2
        assert config["trigger"]["death_hp_threshold"] == 0
    finally:
        trigger.safe_stop()


def test_rejection_reason_reaches_pipeline_and_resets_observations():
    config = hud_config(100)
    pipeline = VisionPipeline(config)
    for i in range(4):
        pipeline.process(synthetic_hud_frame(100), i / 30)
    result, _, _ = pipeline.process(synthetic_hud_frame(95), .2)
    assert not result.valid and result.debug["reject_reason"].startswith("hud_")
    assert pipeline.health_detector._ema_value == -1 and not pipeline._health_observations
