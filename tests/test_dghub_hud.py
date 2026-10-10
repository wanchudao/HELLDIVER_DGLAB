"""DGHub settings -> actual shared vision, not native host/game acceptance.

Recorded and synthetic inputs remain separately identified. No sockets,
device transport, installed host configuration or main-edition INIs are used.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from dghub_plugin.sdk_config import core_config, defaults, manifest, validate
from dghub_plugin.sdk_session import SDKSession
from dghub_plugin.preset_catalog import PresetInfo
from src.config_loader import load_config
from src.hud_profiles import MEASURED_SCALES, PROFILE_VERSION, SCALES, get_profile, source_label
from test_hud_profiles import synthetic_hud_frame


ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "screenshots" / "hud_1080p_20261002"


def packets(batch):
    return [json.loads(raw) for raw in batch.messages]


@pytest.fixture
def session_factory():
    clients = []

    def make(scale):
        now = [0.0]
        values = defaults()
        values.update(hud_scale_percent=scale, channel_mode="both")
        client = SDKSession(load_config(ROOT / "config.example.ini"), clock=lambda: now[0],
                            preset_catalog={values[event + "_preset"]:
                                            PresetInfo(values[event + "_preset"], 11, 3, False)
                                            for event in ("injury_pulse", "low_health", "heal_pulse",
                                                          "healing_sustain", "death_pulse")},
                            log_writer=SimpleNamespace(append=lambda row: None, close=lambda: None))
        client.test_clock = now
        clients.append(client)
        client.hello("offline-hud-test-only")
        client.receive(json.dumps({"op": "hello_ack", "accepted": True, "sdk_version": "1"}))
        client.receive(json.dumps({"op": "config", "data": values}))
        client.receive(json.dumps({"op": "device_info", "connected": True}))
        client.set_running(True)
        assert client.armed and not client.closed
        return client

    yield make
    for client in clients:
        client.close()


def observe_frame(client, frame, count=4):
    output = []
    for _ in range(count):
        client.test_clock[0] += 1 / 30
        output.extend(packets(client.process_frame(frame, client.frame_ticket())))
    return output


def test_manifest_has_exact_readonly_hud_select_and_source_labels():
    fields = {f["key"]: f for section in manifest()["config_schema"] for f in section["fields"]}
    field = fields["hud_scale_percent"]
    assert field["type"] == "select" and field["default"] == 100
    assert field["options"] == [{"value": value, "label": f"{value / 100:.2f}"}
                                for value in range(75, 126, 5)]
    assert len(field["options"]) == 11
    assert all(source_label(value) == ("已有满血实拍" if value in MEASURED_SCALES else "插值推算")
               for value in SCALES)


@pytest.mark.parametrize("bad", [True, False, None, 74, 76, 126, "76", "100.0", "1.00",
                               " 100", "100 ", "+100", "0100", "１００", "", 100.0, .75, 1.25])
def test_sdk_hud_config_rejects_noninteger_or_nonstep_values(bad):
    values = defaults()
    values["hud_scale_percent"] = bad
    with pytest.raises(ValueError, match="HUD"):
        validate(values)


@pytest.mark.parametrize("scale", SCALES)
def test_native_dropdown_string_is_normalized_without_mutating_host_data(scale):
    values = defaults() | {"hud_scale_percent": str(scale), "running": False,
                           "target_id": "", "enabled": True}
    original = deepcopy(values)
    selected = validate(values)
    assert type(selected["hud_scale_percent"]) is int
    assert selected["hud_scale_percent"] == scale
    assert values == original
    core = core_config(load_config(ROOT / "config.example.ini"), selected)
    assert core["ROI"] == get_profile(scale)["roi"]


def test_native_saved_string_hud_starts_paused_and_local_control_can_arm(session_factory):
    client = session_factory("100")
    assert client.settings["hud_scale_percent"] == 100
    assert client.vision.config["hud"]["scale_percent"] == 100
    client.pause()
    client.receive(json.dumps({"op": "config_changed", "key": "hud_scale_percent", "value": "125"}))
    assert not client.closed and not client.armed
    assert client.settings["hud_scale_percent"] == 100
    assert client.pending["hud_scale_percent"] == 125
    assert client.set_running(True).messages and client.armed


@pytest.mark.parametrize("scale", SCALES)
def test_selected_hud_fixes_fresh_core_geometry_without_mutating_base(scale):
    base = load_config(ROOT / "config.example.ini")
    original = deepcopy(base)
    values = defaults()
    values["hud_scale_percent"] = scale
    mapped = core_config(base, validate(values))
    profile = get_profile(scale)
    assert base == original
    assert mapped["ROI"] == profile["roi"] and mapped["hud"] == profile["hud"]
    assert mapped["calibration"]["resolution"] == "1920x1080"
    assert mapped["hud"]["version"] == PROFILE_VERSION
    assert mapped["trigger"]["low_health_hp_threshold"] == .75
    assert mapped["trigger"]["injury_window_sec"] == .2


@pytest.mark.parametrize("actual", MEASURED_SCALES)
@pytest.mark.parametrize("selected", SCALES)
def test_recorded_seven_profiles_crosschecked_through_sdk_session(session_factory, actual, selected):
    client = session_factory(selected)
    image = cv2.imread(str(MATERIALS / f"hud_{actual:03d}_full.jpg"))
    assert image is not None and image.shape == (1080, 1920, 3)
    output = observe_frame(client, image)
    if actual == selected:
        assert client._last_hp == 1
        assert client.vision.pipeline.health_detector._ema_value == 1
    else:
        assert client._last_hp is None
        assert not client.vision.pipeline._health_observations
        assert any(p.get("fields", {}).get("display_status") == "NO HUD" for p in output)
    assert not any(p["op"] == "trigger" or (p["op"] == "set_strength" and p["pct"])
                   for p in output)
    assert all(item["strength"] == 0 for item in client.vision.mock_output()["channels"].values())


@pytest.mark.parametrize("scale", (105, 110, 115, 120))
@pytest.mark.parametrize("hp", [0, .01, .4, .74, .75, .76, 1])
def test_interpolated_hud_synthetic_health_is_actual_sdk_vision(session_factory, scale, hp):
    client = session_factory(scale)
    output = observe_frame(client, synthetic_hud_frame(scale, hp))
    width = get_profile(scale)["roi"]["bar_max_x"] - get_profile(scale)["roi"]["bar_min_x"]
    assert client._last_hp == pytest.approx(round(width * hp) / width, abs=1 / width)
    status = client.status_fields("test")
    assert status["hud_source"] == "插值推算"
    assert status["hud_scale"] == f"{scale / 100:.2f}"
    # A new empty baseline is not a positive-to-zero death event.
    if hp == 0:
        assert not any(p["op"] == "trigger" for p in output)


def test_wrong_size_clears_sdk_feedback_and_rebuild_does_not_fake_heal(session_factory):
    client = session_factory(100)
    observe_frame(client, synthetic_hud_frame(100, .5))
    assert client.vision.mock_output()["channels"]["A"]["strength"] == 10
    auxiliary = cv2.imread(str(MATERIALS / "hud_100_aux_1919x1079.png"))
    output = observe_frame(client, auxiliary, 1)
    assert client._last_hp is None
    assert not client.vision.pipeline._health_observations
    assert {p["channel"] for p in output if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}
    output = observe_frame(client, synthetic_hud_frame(100, 1))
    assert client._last_hp == 1
    assert not any(p["op"] == "trigger" for p in output)


def test_hud_change_is_pending_until_new_plugin_session_even_after_f8_pause(session_factory):
    client = session_factory(100)
    client.pause()
    client.receive(json.dumps({"op": "config_changed", "key": "hud_scale_percent", "value": 125}))
    assert client.settings["hud_scale_percent"] == client.vision.config["hud"]["scale_percent"] == 100
    assert client.pending["hud_scale_percent"] == 125
    client.set_running(True)
    observe_frame(client, synthetic_hud_frame(125))
    assert client._last_hp is None
    status = client.status_fields("test")
    assert status["hud_scale"] == "1.00" and status["settings_pending"]
    restarted = session_factory(client.pending["hud_scale_percent"])
    observe_frame(restarted, synthetic_hud_frame(125))
    assert restarted._last_hp == 1 and restarted.status_fields("test")["hud_scale"] == "1.25"
