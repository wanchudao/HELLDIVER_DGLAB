"""Session death memory is independent of renewable software output, never hardware."""
from types import SimpleNamespace
import json

import cv2
import pytest

from src.trigger import TriggerDispatch
from src.feedback_settings import SettingsStore, default_settings
from src.desktop_runtime import make_config
from src.config_loader import load_config
from dghub_plugin.sdk_config import validate, core_config
from dghub_plugin.panel import DGHubPanelController
from test_dghub_sdk import client, ready, settings, frame, output, receive


def rig(mode, **sender):
    now = [0.]
    rows = []
    dispatch = TriggerDispatch({"trigger": {"sender_mode": "mock", "death_feedback_mode": mode},
                                "sender": default_settings() | sender}, clock=lambda: now[0],
                               log_writer=SimpleNamespace(append=rows.append, close=lambda: None))
    def observe(at, hp, valid=True):
        now[0] = at
        return dispatch.dispatch(SimpleNamespace(health_raw=hp, health_stable=hp, valid=valid, state="injured"))
    return dispatch, observe, now, rows


@pytest.mark.parametrize("mode", ["pulse", "until_revive", "until_reset"])
def test_death_zero_revive_second_life(mode):
    dispatch, observe, now, rows = rig(mode)
    try:
        observe(0, 1)
        observe(.1, 0)
        assert dispatch.mock_output()["strength"] == 40
        for tick in range(1, 71):
            observe(.1 + tick / 10, 0)
        assert dispatch.mock_output()["strength"] == (0 if mode == "pulse" else 40)
        observe(7.2, 1)
        assert dispatch.death_marked is (mode == "until_reset")
        assert dispatch.mock_output()["strength"] == (60 if mode == "until_reset" else 20)
        observe(7.3, 0)
        assert dispatch.mock_output()["strength"] == 40
        assert sum("death_pulse" in row.get("actions", []) for row in rows) == 2
        json.dumps(rows, allow_nan=False)
    finally:
        dispatch.safe_stop()


@pytest.mark.parametrize("mode", ["until_revive", "until_reset"])
def test_first_zero_or_invalid_history_is_not_death(mode):
    dispatch, observe, _, _ = rig(mode)
    try:
        for at, hp, valid in [(0, 0, True), (.1, 0, True), (.2, 1, False), (.3, 0, True), (.4, 0, True)]:
            observe(at, hp, valid)
            assert not dispatch.death_marked
            assert dispatch.mock_output()["strength"] == 0
    finally:
        dispatch.safe_stop()


@pytest.mark.parametrize("mode", ["until_revive", "until_reset"])
def test_output_reset_retains_mark_but_requires_fresh_baseline(mode):
    dispatch, observe, now, _ = rig(mode)
    try:
        observe(0, 1)
        observe(.1, 0)
        observe(.2, 0, False)
        assert dispatch.death_marked and not dispatch.death_status()["death_layer_active"]
        assert dispatch.mock_output()["strength"] == 0
        observe(.3, 0)
        assert dispatch.mock_output()["strength"] == 0
        observe(.4, 0)
        assert dispatch.mock_output()["strength"] == 40
        dispatch.reset("paused")
        assert dispatch.death_marked and dispatch.mock_output()["strength"] == 0
        observe(.5, 0)
        observe(.6, 0)
        now[0] = 2
        assert dispatch.mock_output()["strength"] == 0  # no frame lease can survive
        assert dispatch.death_marked
        dispatch.reset_challenge()
        observe(2.1, 0)
        observe(2.2, 0)
        assert not dispatch.death_marked and dispatch.mock_output()["strength"] == 0
    finally:
        dispatch.safe_stop()


def test_challenge_stacks_both_channels_and_zero_death_yields_waveform():
    dispatch, observe, _, _ = rig("until_reset", channel_mode="both", max_strength=50, max_strength_b=70,
                                  death_pulse_strength_b=60)
    try:
        observe(0, 1)
        observe(.1, 0)
        observe(.2, .5)
        out = dispatch.mock_output()["channels"]
        assert out["A"] == {"strength": 50, "waveform": "death_pulse"}
        assert out["B"] == {"strength": 70, "waveform": "death_pulse"}
    finally:
        dispatch.safe_stop()
    dispatch, observe, _, _ = rig("until_reset", death_pulse_strength=0, death_pulse_strength_b=0)
    try:
        observe(0, 1)
        observe(.1, 0)
        observe(.2, 1)
        assert dispatch.challenge_failed
        assert dispatch.mock_output()["waveform"] == "official_tide"
    finally:
        dispatch.safe_stop()


@pytest.mark.parametrize("mode", ["pulse", "until_revive", "until_reset"])
def test_saved_mode_main_and_host_core_chain(tmp_path, mode):
    store = SettingsStore(tmp_path / "feedback_settings.ini")
    store.save(store.values, trigger_settings={"death_feedback_mode": mode, "low_health_threshold_percent": 30})
    store.save(store.values, trigger_settings={"low_health_threshold_percent": 75})
    saved = SettingsStore(store.path).trigger_snapshot()
    assert saved == {"death_feedback_mode": mode, "low_health_threshold_percent": 75}
    config = load_config(make_config(tmp_path / "session.ini", 1, (1920, 1080), "mock", trigger_settings=saved))
    assert config["trigger"]["death_feedback_mode"] == mode
    assert core_config({}, validate({"death_feedback_mode": mode}))["trigger"]["death_feedback_mode"] == mode


@pytest.mark.parametrize("bad", [None, True, 1, "", "forever", "PULSE", [], {}])
def test_bad_mode_is_rejected_without_overwrite(tmp_path, bad):
    store = SettingsStore(tmp_path / "feedback_settings.ini")
    store.save(store.values)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save(store.values, trigger_settings={"death_feedback_mode": bad})
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        validate({"death_feedback_mode": bad})


def test_sdk_mouse_pause_keeps_challenge_f8_resets_and_stays_paused(client):
    ready(client, settings() | {"death_feedback_mode": "until_reset"})
    frame(client, 0, 1)
    frame(client, .1, 0)
    frame(client, .2, 1)
    assert client.vision.trigger.challenge_failed
    client.local_toggle("button")
    assert not client.armed and client.vision.trigger.challenge_failed
    client.local_toggle("hotkey")
    assert not client.armed and not client.vision.trigger.challenge_failed
    assert all(value["strength"] == 0 for value in output(client).values())
    client.local_toggle("hotkey")
    assert client.armed
    frame(client, .3, 0)
    frame(client, .4, 0)
    assert not client.vision.trigger.challenge_failed


def test_sdk_complete_wave_cycles_continue_past_old_duration_and_pending_is_not_applied(client):
    ready(client, settings() | {"death_feedback_mode": "until_reset"})
    frame(client, 0, 1)
    first = frame(client, .1, 0)
    assert any(m.get("preset") == "测试-death_pulse" for m in first)
    receive(client, op="config_changed", key="death_feedback_mode", value="pulse")
    waves = 0
    for index in range(1, 201):
        packets = frame(client, .1 + index / 10, 0)
        waves += sum(p.get("preset") == "测试-death_pulse" for p in packets)
        assert output(client)["A"]["strength"] == 40
        assert not any(p.get("strength_mode") == "permanent" for p in packets)
        assert len(client.vision.trigger._mock_sender.mixer.pulses) == 0
    cycle = client.preset_catalog["测试-death_pulse"].cycle_seconds
    assert 1 <= waves <= int(20 / cycle) + 1 and waves < 200
    assert client.pending["death_feedback_mode"] == "pulse"
    assert client.status_fields("test")["death_feedback_mode"] == "until_reset"


@pytest.mark.parametrize("size", [32, 40, 48, 64])
def test_live_button_only_expands_drag_cancels_at_scaled_threshold(size):
    controller = DGHubPanelController()
    controller.live_mode = controller.live_small = True
    controller.live_size = size
    controller.live_drag_threshold = size // 8
    moves = []
    controller.move_live = lambda x, y: moves.append((x, y))
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, 10, 10)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 10, 10)
    assert not controller.consume_toggle()
    controller.on_live_pointer(cv2.EVENT_LBUTTONDOWN, -100, 20)
    controller.on_live_pointer(cv2.EVENT_LBUTTONUP, -100, 20)
    assert controller.consume_layout() and not controller.live_small
    assert not controller.consume_toggle()
    controller.live_small = True
    controller.on_live_pointer(cv2.EVENT_LBUTTONDOWN, -100, 20)
    controller.on_live_pointer(cv2.EVENT_MOUSEMOVE, -100 + size // 8, 20)
    controller.on_live_pointer(cv2.EVENT_LBUTTONUP, -100, 20)
    assert moves and not controller.consume_layout() and controller.live_small
