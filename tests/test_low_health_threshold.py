"""New threshold preferences and both edition mappings; never physical output."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src import feedback_settings as preferences
from src.config_loader import load_config
from src.desktop_runtime import make_config
from src.settings_dialog import FeedbackSettingsDialog
from src.trigger import TriggerDispatch
from dghub_plugin.sdk_config import core_config, defaults, manifest, validate
from test_dghub_sdk import client, frame, messages, output, ready, receive, settings


KEY = "low_health_threshold_percent"


def expected_trigger(percent):
    return {KEY: percent, "death_feedback_mode": "pulse"}


@pytest.mark.parametrize("percent", range(101))
def test_all_percentages_persist_and_reach_main_trigger(tmp_path, percent):
    store = preferences.SettingsStore(tmp_path / "feedback_settings.ini")
    store.save(store.values, trigger_settings={KEY: percent})
    restored = preferences.SettingsStore(store.path)
    assert restored.trigger_snapshot() == expected_trigger(percent)
    assert KEY not in restored.snapshot()
    path = make_config(tmp_path / "session.ini", 1, (1920, 1080), "mock",
                       feedback_settings=restored.snapshot(), trigger_settings=restored.trigger_snapshot())
    config = load_config(path)
    assert config["trigger"]["low_health_hp_threshold"] == percent / 100
    assert KEY not in config["sender"]


@pytest.mark.parametrize("percent", range(101))
def test_all_percentages_dghub_integer_and_wire_string(percent):
    for value in (percent, str(percent)):
        selected = validate({KEY: value})
        assert selected[KEY] == percent and type(selected[KEY]) is int
        base = {"trigger": {"low_health_hp_threshold": .75}}
        before = deepcopy(base)
        mapped = core_config(base, selected)
        assert mapped["trigger"]["low_health_hp_threshold"] == percent / 100
        assert KEY not in mapped["sender"] and base == before


@pytest.mark.parametrize("bad", [-1, 101, True, False, None, 30.0, 30.5, "", "30.0", "30%", " 30", "030", "３０", "nan"])
def test_invalid_threshold_keeps_file_and_memory(tmp_path, bad):
    store = preferences.SettingsStore(tmp_path / "feedback_settings.ini")
    store.save(store.values, trigger_settings={KEY: 30})
    previous = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save(store.values, trigger_settings={KEY: bad})
    assert store.path.read_bytes() == previous and store.trigger_snapshot() == expected_trigger(30)
    with pytest.raises(ValueError):
        validate({KEY: bad})


def test_old_sender_file_and_old_generator_keep_behavior(tmp_path):
    path = tmp_path / "feedback_settings.ini"
    path.write_text("[sender]\nchannel_mode=b\nlow_health_strength=42\n", encoding="utf-8")
    before = path.read_bytes()
    store = preferences.SettingsStore(path)
    assert store.trigger_snapshot() == expected_trigger(75)
    assert store.snapshot()["low_health_strength"] == 42 and path.read_bytes() == before
    session = make_config(tmp_path / "old.ini", 1, (1920, 1080), "mock")
    assert load_config(session)["trigger"]["low_health_hp_threshold"] == .75
    assert validate({})[KEY] == 75


def test_sender_only_save_retains_threshold_and_snapshot_is_detached(tmp_path):
    store = preferences.SettingsStore(tmp_path / "feedback_settings.ini")
    store.save(store.values, trigger_settings={KEY: 0})
    snapshot = store.trigger_snapshot()
    snapshot[KEY] = 75
    store.save(store.values | {"low_health_strength_b": 55})
    preferences.save_settings(store.path, store.values | {"channel_mode": "both"})
    restored = preferences.SettingsStore(store.path)
    assert restored.trigger_snapshot() == expected_trigger(0)
    assert restored.values["low_health_strength_b"] == 55
    assert preferences.default_trigger_settings() == expected_trigger(75)


def test_atomic_failure_and_running_lock_preserve_both_sections(tmp_path, monkeypatch):
    store = preferences.SettingsStore(tmp_path / "feedback_settings.ini")
    store.save(store.values, trigger_settings={KEY: 30})
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="停止全部"):
        store.save(store.values, trigger_settings={KEY: 75}, running=True)
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(preferences.os, "replace", fail)
    with pytest.raises(OSError):
        store.save(store.values | {"channel_mode": "both"}, trigger_settings={KEY: 75})
    assert store.path.read_bytes() == before and store.trigger_snapshot() == expected_trigger(30)
    assert store.snapshot()["channel_mode"] == "a"
    assert not list(tmp_path.glob(".feedback-*.tmp"))


@pytest.mark.parametrize("section", ["low_health_threshold_percent=101", "unknown=30", "low_health_threshold_percent=30.5"])
def test_corrupt_trigger_section_blocks_both_snapshots_and_recovers(tmp_path, section):
    path = tmp_path / "feedback_settings.ini"
    path.write_text("[sender]\nchannel_mode=a\n[trigger]\n" + section, encoding="utf-8")
    store = preferences.SettingsStore(path)
    for snapshot in (store.snapshot, store.trigger_snapshot):
        with pytest.raises(ValueError):
            snapshot()
    store.save(preferences.default_settings(), trigger_settings={KEY: 75})
    assert not store.error and store.trigger_snapshot() == expected_trigger(75)


@pytest.mark.parametrize("percent", [0, 1, 30, 75, 100])
@pytest.mark.parametrize("relative", [-.001, 0, .001])
def test_shared_trigger_strict_boundary_raw_not_ema(percent, relative):
    now = [0.0]
    hp = min(1., max(.0001, percent / 100 + relative))
    dispatch = TriggerDispatch({"trigger": {"sender_mode": "mock", "low_health_hp_threshold": percent / 100}},
                              clock=lambda: now[0], log_writer=SimpleNamespace(append=lambda row: None, close=lambda: None))
    observe = SimpleNamespace(health_raw=hp, health_stable=1-hp, valid=True, state="injured")
    try:
        dispatch.dispatch(observe)
        assert dispatch.mock_output()["strength"] == 0  # baseline only
        now[0] = .1
        dispatch.dispatch(observe)
        assert dispatch._continuous_state[0] is (0 < hp < percent / 100)
        assert dispatch.mock_output()["strength"] == (10 if 0 < hp < percent / 100 else 0)
    finally:
        dispatch.safe_stop()


def test_zero_threshold_keeps_damage_healing_and_death(client):
    ready(client, settings() | {KEY: 0})
    frame(client, 0, 1)
    frame(client, .1, .5)
    assert output(client)["A"]["strength"] == 0
    packets = frame(client, .31, .5)
    assert any(p.get("preset") == "测试-injury_pulse" for p in packets)
    assert output(client)["A"]["strength"] == 10
    packets = frame(client, .4, .8)
    assert any(p.get("preset") == "测试-heal_pulse" for p in packets)
    assert client.vision.trigger._continuous_state == (False, True)
    packets = frame(client, .5, 0)
    assert sum(p.get("preset") == "测试-death_pulse" for p in packets) == 1
    assert not any(p.get("preset") == "测试-death_pulse" for p in frame(client, .6, 0))


def test_dghub_pending_does_not_change_applied_threshold_or_f8(client):
    ready(client, settings() | {KEY: 75})
    receive(client, op="config_changed", key=KEY, value=30)
    assert client.pending[KEY] == 30 and client.settings[KEY] == 75
    assert client.status_fields("test")[KEY] == 75
    client.set_running(False)
    client.set_running(True)
    frame(client, 0, .5)
    frame(client, .1, .5)
    assert output(client)["A"]["strength"] == 10
    assert client.vision.trigger._low_health_thresh == .75


@pytest.mark.parametrize("percent", [1, 30, 75, 100])
def test_healing_to_threshold_exits_only_low_health_layer(client, percent):
    ready(client, settings() | {KEY: percent})
    low = percent / 200
    frame(client, 0, low)
    frame(client, .1, low)
    assert client.vision.trigger._continuous_state == (True, False)
    packets = frame(client, .2, percent / 100)
    assert client.vision.trigger._continuous_state == (False, True)
    assert any(p.get("preset") == "测试-heal_pulse" for p in packets)
    assert output(client)["A"]["strength"] > 0


@pytest.mark.parametrize("percent", [0, 1, 30, 75, 100])
def test_threshold_hud_loss_discards_baseline(client, percent):
    ready(client, settings() | {KEY: percent})
    frame(client, 0, .5)
    frame(client, .1, .5)
    frame(client, .2, .5, valid=False)
    assert all(v["strength"] == 0 for v in output(client).values())
    assert client.vision.trigger._last_hp < 0  # Existing cleared-baseline sentinel.
    packets = frame(client, .3, .8)
    assert not any(p.get("op") in {"pulse", "trigger"} for p in packets)
    assert all(v["strength"] == 0 for v in output(client).values())


@pytest.mark.parametrize("mode", ["a", "b", "both"])
@pytest.mark.parametrize("running", [False, True])
def test_threshold_control_depends_on_session_not_channel(mode, running):
    states = {}
    widget = lambda name: SimpleNamespace(configure=lambda **value: states.update({name: value}))
    dialog = object.__new__(FeedbackSettingsDialog)
    dialog.is_running = lambda: running
    dialog.mode_labels = {mode: mode}
    dialog.mode = SimpleNamespace(get=lambda: mode)
    dialog.mode_box = widget("mode")
    dialog.threshold_box = widget("threshold")
    dialog.wave_boxes = []
    dialog.strength_boxes = {"A": [], "B": []}
    dialog.save_button, dialog.default_button = widget("save"), widget("default")
    dialog.store = SimpleNamespace(error="")
    dialog.hint = SimpleNamespace(set=lambda value: None)
    dialog.death_mode_box = widget("death_mode")
    dialog.update_enabled()
    assert states["threshold"]["state"] == ("disabled" if running else "normal")


def test_dghub_schema_field_is_number_under_low_health():
    section = next(s for s in manifest()["config_schema"] if s["section"] == "残血／濒死")
    field = next(f for f in section["fields"] if f["key"] == KEY)
    assert {k: field[k] for k in ("type", "min", "max", "step", "default")} == {
        "type": "number", "min": 0, "max": 100, "step": 1, "default": 75}
    assert manifest()["version"] == "1.0.7"
