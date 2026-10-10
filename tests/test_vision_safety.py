"""Regression tests using real NumPy/OpenCV, never a screen or a device."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.config_loader import load_config
from src.health_detector import HealthDetector
from src.roi_locator import RoiLocator
from src.state_machine import StateMachine
from src.vision_pipeline import VisionPipeline


@pytest.fixture
def config():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.ini")
    cfg["ROI"].update(outer_x1=10, outer_y1=10, outer_x2=210, outer_y2=24,
                      inner_x1=16, inner_y1=13, inner_x2=204, inner_y2=21,
                      bar_min_x=16, bar_max_x=204)
    return cfg


def bar_frame(hp):
    frame = np.full((40, 240, 3), 20, np.uint8)
    frame[10:24, 10:14] = 240
    frame[10:24, 206:210] = 240
    # A bordered horizontal HUD with a dark empty slot.
    frame[13:21, 16:204] = 30
    frame[13:21, 16:16 + round(188 * hp)] = 230
    return frame


@pytest.mark.parametrize("level", [0, 30, 60, 115, 180, 255])
def test_uniform_non_hud_is_never_valid(config, level):
    pipeline = VisionPipeline(config)
    for i in range(5):
        result, roi, _ = pipeline.process(np.full((40, 240, 3), level, np.uint8), i * .03)
        assert not roi.valid
        assert not result.valid and result.state == "unknown"
        assert pipeline.health_detector._ema_value == -1


def test_low_confidence_probe_cannot_change_ema(config):
    detector = HealthDetector(config)
    detector.commit_health(.5)
    for _ in range(5):
        result = detector.detect(np.full((8, 188, 3), 115, np.uint8))
        assert result.confidence < config["unknown"]["min_confidence"]
        assert detector._ema_value == .5


def test_no_ema_update_during_probe(config):
    detector = HealthDetector(config)
    detector.commit_health(.5)
    result = detector.detect(np.full((8, 188, 3), 255, np.uint8), update_ema=False)
    assert result.health_raw == 1
    assert detector._ema_value == .5


def test_disconnected_right_bright_region_is_not_a_health_bar(config):
    detector = HealthDetector(config)
    detector.commit_health(.5)
    patch = np.full((8, 188, 3), 20, np.uint8)
    patch[:, 90:] = 250
    result = detector.detect(patch)
    assert result.health_raw == 0
    assert result.confidence == 0
    assert detector._ema_value == .5


def test_roi_outside_frame_is_rejected_not_clamped_into_validity(config):
    roi = RoiLocator(config).locate(bar_frame(1)[:, :150])
    assert not roi.valid


def test_valid_bar_and_invalid_gap_rebaseline(config):
    pipeline = VisionPipeline(config)
    first, _, _ = pipeline.process(bar_frame(1), 0)
    second, _, _ = pipeline.process(bar_frame(1), .03)
    assert not first.valid  # debounce is a real gate, not only a display label
    assert second.valid and second.health_raw == 1
    rejected, _, _ = pipeline.process(np.zeros((40, 240, 3), np.uint8), .06)
    assert not rejected.valid
    resumed, _, _ = pipeline.process(bar_frame(.4), .09)
    assert not resumed.valid
    assert resumed.health_stable == resumed.health_raw


def test_capture_gap_discards_stale_baseline(config):
    pipeline = VisionPipeline(config)
    pipeline.process(bar_frame(1), 0)
    pipeline.process(bar_frame(1), .03)
    resumed, _, _ = pipeline.process(bar_frame(.3), 2)
    assert not resumed.valid
    assert resumed.health_stable == resumed.health_raw


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_state_machine_rejects_nonfinite_or_out_of_range(config, value):
    sm = StateMachine(config)
    result = sm.update(value, value, 0, 0, 0, 1, 1, True)
    assert not result.valid


def test_death_transition_survives_debounce_and_filter_fade(config):
    sm = StateMachine(config)
    for hp in [1, 1, 0, 0, 0, 0]:
        result = sm.update(hp, hp, 0, 0, 0, 1, 1, True)
    assert result.state == "death" and result.valid


def test_healing_includes_revival_and_full_health(config):
    sm = StateMachine(config)
    for hp in [0, 0, .5, 1]:
        result = sm.update(hp, hp, 0, 0, 0, 1, 1, True)
    assert result.state == "healing"


@pytest.mark.parametrize("alpha", [0, -1, float("nan"), float("inf"), 2])
def test_invalid_ema_config_fails_before_capture_or_output(config, alpha):
    config["health"]["ema_alpha"] = alpha
    with pytest.raises(ValueError):
        VisionPipeline(config)


def test_ambiguous_gray_is_not_confident_empty_health(config):
    detector = HealthDetector(config)
    detector.commit_health(.5)
    result = detector.detect(np.full((8, 188, 3), 87, np.uint8))
    assert result.confidence == 0
    assert detector._ema_value == .5


def test_ambiguous_columns_reduce_confidence_even_with_a_few_classified_columns(config):
    detector = HealthDetector(config)
    scores = np.full(188, .34, np.float32)
    scores[:3] = .9
    scores[-3:] = .1
    assert detector._compute_confidence(scores) < config["unknown"]["min_confidence"]


def test_separated_bright_blocks_are_not_confident_health(config):
    detector = HealthDetector(config)
    patch = np.full((8, 188, 3), 20, np.uint8)
    patch[:, :12] = 230
    patch[:, 40:] = 230
    result = detector.detect(patch)
    assert result.confidence == 0
    assert detector._ema_value == -1


def test_last_visible_pixels_are_not_trimmed_as_another_end_cap(config):
    detector = HealthDetector(config)
    patch = np.full((8, 188, 3), 30, np.uint8)
    patch[:, :2] = 230
    result = detector.detect(patch)
    assert result.health_raw == pytest.approx(2 / 188)
    assert result.confidence >= config["unknown"]["min_confidence"]
    assert not result.empty
    assert result.fill_boundary_x == config["ROI"]["bar_min_x"] + 1


def test_explicit_bar_bounds_select_only_the_measurement_region(config):
    config["ROI"].update(bar_min_x=20, bar_max_x=200)
    detector = HealthDetector(config)
    patch = np.full((8, 188, 3), 230, np.uint8)
    patch[:, 4:184] = 30
    assert detector.detect(patch).health_raw == 0


@pytest.mark.parametrize("level", [0, 87, 255])
def test_zero_anchor_threshold_cannot_disable_hud_structure_gate(config, level):
    config["unknown"].update(min_anchor_score=0, min_confidence=0)
    pipeline = VisionPipeline(config)
    for timestamp in (0, .1, .2):
        result, roi, _ = pipeline.process(np.full((40, 240, 3), level, np.uint8), timestamp)
        assert not roi.valid and not result.valid
        assert pipeline.health_detector._ema_value == -1


def test_zero_confidence_threshold_cannot_accept_entirely_ambiguous_bar(config):
    config["unknown"]["min_confidence"] = 0
    pipeline = VisionPipeline(config)
    frame = bar_frame(1)
    frame[13:21, 16:204] = 87
    for timestamp in (0, .1):
        result, _, health = pipeline.process(frame, timestamp)
        assert health.confidence == 0 and not result.valid
        assert pipeline.health_detector._ema_value == -1


@pytest.mark.parametrize("frame", [None, [], np.zeros((40, 240)),
                                   np.zeros((40, 240, 3), np.float32),
                                   np.zeros((40, 240, 3), np.uint16)])
def test_malformed_frame_clears_pipeline_history_before_raising(config, frame):
    pipeline = VisionPipeline(config)
    pipeline.process(bar_frame(1), 0)
    pipeline.process(bar_frame(1), .1)
    with pytest.raises(ValueError, match="uint8 BGR"):
        pipeline.process(frame, .2)
    assert pipeline.health_detector._ema_value == -1
    assert pipeline.state_machine.is_unknown
    assert pipeline._last_frame is None


def test_invalid_effects_do_not_commit_health_to_ema(config, monkeypatch):
    pipeline = VisionPipeline(config)
    pipeline.process(bar_frame(1), 0)
    pipeline.process(bar_frame(1), .1)
    monkeypatch.setattr(pipeline.effect_detector, "detect", lambda *args, **kwargs:
                        SimpleNamespace(red_score=float("nan"), cyan_score=0, death_score=0))
    result, _, _ = pipeline.process(bar_frame(.4), .2)
    assert not result.valid and pipeline.health_detector._ema_value == -1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "not-a-time"])
def test_bad_timestamp_clears_pipeline_history(config, value):
    pipeline = VisionPipeline(config)
    pipeline.process(bar_frame(1), 0)
    with pytest.raises((TypeError, ValueError)):
        pipeline.process(bar_frame(1), value)
    assert pipeline.health_detector._ema_value == -1
    assert pipeline.state_machine.is_unknown


@pytest.mark.parametrize("section,key,value", [
    ("unknown", "min_anchor_score", float("nan")),
    ("unknown", "min_anchor_score", -1),
    ("unknown", "min_anchor_score", 2),
    ("unknown", "min_structure_contrast", 0),
    ("unknown", "min_structure_contrast", float("inf")),
    ("state_machine", "debounce_frames", 0),
    ("state_machine", "debounce_frames", 1.5),
    ("state_machine", "unknown_alarm_frames", float("nan")),
    ("death", "health_near_zero", float("nan")),
])
def test_invalid_gate_configuration_fails_before_processing(config, section, key, value):
    config[section][key] = value
    with pytest.raises(ValueError):
        VisionPipeline(config)


@pytest.mark.parametrize("relative_path", [
    "injured/20260621135432_2.jpg", "injured/20260621135434_1.jpg",
    "injured/20260621135436_1.jpg", "injured/20260621135439_1.jpg",
    "injured/20260621135445_1.jpg", "healing/group_one/20260621135448_1.jpg",
])
def test_recorded_critical_health_is_not_mistaken_for_zero(relative_path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config.example.ini")
    roi = RoiLocator(config).locate(cv2.imread(str(root / "screenshots" / relative_path)))
    result = HealthDetector(config).detect(roi.inner_img)
    assert roi.valid and result.confidence >= config["unknown"]["min_confidence"]
    assert 0 < result.health_raw < .05


@pytest.mark.parametrize("name", ["20260621135532_3.jpg", "20260621135533_1.jpg",
                                  "20260621135533_2.jpg", "20260621135534_1.jpg",
                                  "20260621135535_1.jpg"])
def test_recorded_dead_bar_still_measures_zero(name):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config.example.ini")
    roi = RoiLocator(config).locate(cv2.imread(str(root / "screenshots/death" / name)))
    result = HealthDetector(config).detect(roi.inner_img)
    assert roi.valid and result.confidence >= config["unknown"]["min_confidence"]
    assert result.health_raw == 0


def _mock_replay(config, frames, *, timestamps=None, reset_at=None):
    """Exercise observation confirmation and production events without a log file."""
    from src.trigger import TriggerDispatch
    now = 0.0
    entries = []
    log = SimpleNamespace(append=entries.append, close=lambda: None)
    trigger = TriggerDispatch(config, clock=lambda: now, log_writer=log)
    pipeline = VisionPipeline(config)
    results, events = [], []
    try:
        for index, frame in enumerate(frames):
            now = index / 30 if timestamps is None else timestamps[index]
            if index == reset_at:
                pipeline.reset()
                trigger.reset("test_manual_pause")
            result, _, _ = pipeline.process(frame, now)
            results.append(result)
            event = trigger.dispatch(result)
            event["mock_strength"] = trigger.mock_output()["strength"]
            events.append(event)
    finally:
        trigger.safe_stop()
    return results, events


@pytest.mark.parametrize("debounce", [1, 2])
@pytest.mark.parametrize("spike", [0, .1, 1])
def test_startup_single_observation_cannot_establish_a_false_baseline(config, debounce, spike):
    config["state_machine"]["debounce_frames"] = debounce
    frames = [bar_frame(spike)] + [bar_frame(.8)] * 6
    results, events = _mock_replay(config, frames)
    assert not results[0].valid and not results[1].valid
    assert results[-1].valid
    assert not any(event.get("actions") for event in events)
    assert all(event["mock_strength"] == 0 for event in events)


@pytest.mark.parametrize("debounce", [1, 2])
@pytest.mark.parametrize("spike", [0, .2, .8, 1])
def test_single_frame_hp_spike_cannot_emit_healing_injury_or_death(config, debounce, spike):
    config["state_machine"]["debounce_frames"] = debounce
    frames = [bar_frame(.5)] * 4 + [bar_frame(spike)] + [bar_frame(.5)] * 10
    results, events = _mock_replay(config, frames)
    assert all(result.health_raw == pytest.approx(.5)
               for result in results if result.valid)
    assert not any(event.get("actions") for event in events)
    assert results[4].debug["health_observed"] == pytest.approx(round(188 * spike) / 188)
    assert results[4].debug["health_confirmed"] == pytest.approx(.5)
    assert not any(event["healing_sustain"] for event in events)


@pytest.mark.parametrize("debounce", [1, 2])
@pytest.mark.parametrize("pixels", [1, 94, 179])
def test_persistent_single_pixel_healing_and_damage_both_reach_trigger(config, debounce, pixels):
    config["state_machine"]["debounce_frames"] = debounce
    baseline = pixels / 188
    tiny_recovery = (pixels + 1) / 188
    assert tiny_recovery - baseline < .02
    frames = ([bar_frame(baseline)] * 4 + [bar_frame(tiny_recovery)] * 4
              + [bar_frame(baseline)] * 10)
    results, events = _mock_replay(config, frames)
    actions = [action for event in events for action in event.get("actions", [])]
    assert actions.count("heal_pulse") == 1
    assert actions.count("injury_pulse") == 1
    assert results[4].health_raw == pytest.approx(baseline)  # first observation is pending
    assert results[5].health_raw == pytest.approx(tiny_recovery)
    assert results[9].health_raw == pytest.approx(baseline)


@pytest.mark.parametrize("debounce", [1, 2])
@pytest.mark.parametrize("spike", [0, .1, 1])
@pytest.mark.parametrize("restart", ["invalid", "gap", "reset"])
def test_restart_discards_filter_history_and_rejects_isolated_first_observation(
        config, debounce, spike, restart):
    config["state_machine"]["debounce_frames"] = debounce
    frames = [bar_frame(.8)] * 4
    if restart == "invalid":
        frames.append(np.zeros((40, 240, 3), np.uint8))
    first_resumed = len(frames)
    frames += [bar_frame(spike)] + [bar_frame(.8)] * 10
    timestamps = [index / 30 for index in range(len(frames))]
    if restart == "gap":
        timestamps[first_resumed:] = [time + 2 for time in timestamps[first_resumed:]]
    results, events = _mock_replay(
        config, frames, timestamps=timestamps,
        reset_at=first_resumed if restart == "reset" else None)
    if restart == "invalid":
        assert not results[first_resumed - 1].valid
    assert not results[first_resumed].valid and not results[first_resumed + 1].valid
    assert results[-1].valid
    assert not any(event.get("actions") for event in events)
    assert all(event["mock_strength"] == 0 for event in events)


@pytest.mark.parametrize("debounce", [1, 2])
def test_single_zero_with_maximum_death_score_cannot_bypass_observation_gate(config, monkeypatch, debounce):
    from src.effect_detector import EffectDetector
    config["state_machine"]["debounce_frames"] = debounce
    monkeypatch.setattr(EffectDetector, "detect", lambda self, frame, health_raw:
                        SimpleNamespace(red_score=1.0, cyan_score=0.0,
                                        death_score=float(health_raw == 0)))
    results, events = _mock_replay(
        config, [bar_frame(.5)] * 4 + [bar_frame(0)] + [bar_frame(.5)] * 10)
    assert results[4].debug["health_observed"] == 0
    assert results[4].death_score == 0
    assert all(result.state != "death" for result in results)
    assert not any(event.get("actions") for event in events)


@pytest.mark.parametrize("debounce", [1, 2])
def test_confirmed_zero_still_emits_once_per_life_and_revival_heals(config, debounce):
    config["state_machine"]["debounce_frames"] = debounce
    frames = ([bar_frame(.5)] * 4 + [bar_frame(0)] * 12
              + [bar_frame(1)] * 12 + [bar_frame(0)] * 12)
    results, events = _mock_replay(config, frames)
    assert results[4].health_raw == pytest.approx(.5)
    assert results[5].health_raw == 0
    assert events[4]["actions"] == []
    assert events[5]["actions"] == ["death_pulse"]
    actions = [action for event in events for action in event.get("actions", [])]
    assert actions == ["death_pulse", "heal_pulse", "death_pulse"]
