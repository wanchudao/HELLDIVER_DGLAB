import configparser
import json
from pathlib import Path

import pytest

from src import feedback_settings as settings
from src.desktop_runtime import make_config, guide_text
from src.config_loader import load_config
from src.feedback_mixer import FeedbackMixer, MockFeedbackSender
from src.waveforms import WAVEFORM_V3, WAVEFORM_NAMES, validate_waveforms


def test_desktop_defaults_are_five_distinct_waves_but_legacy_is_unchanged():
    values = settings.default_settings()
    assert len({values[f"{event}_waveform"] for event, *_ in settings.EVENTS}) == 5
    assert values["channel_mode"] == "a"
    assert settings.validate_settings({})["injury_pulse_waveform"] == "weak_pulse"
    assert settings.validate_settings({})["heal_pulse_waveform"] == "heal_pulse"


def test_all_18_waveforms_are_valid_and_unique():
    validate_waveforms()
    assert len(WAVEFORM_V3) == len(WAVEFORM_NAMES) == 18
    assert len({tuple(w) for w in WAVEFORM_V3.values()}) == 18


@pytest.mark.parametrize("mode,expected", [("a", {"A": 10, "B": 0}), ("b", {"A": 0, "B": 6}),
                                           ("both", {"A": 10, "B": 6})])
def test_routing_and_independent_strengths(mode, expected):
    values = settings.default_settings() | {"channel_mode": mode, "low_health_strength_b": 6}
    mock = MockFeedbackSender({"sender": values}, lambda: 1)
    out = mock.update_continuous(low_health=True, healing=False, hp=.5)["channels"]
    assert {ch: v["strength"] for ch, v in out.items()} == expected


def test_shared_wave_with_zero_individual_event_and_independent_caps():
    values = settings.default_settings() | {"channel_mode": "both", "injury_pulse_strength_b": 0,
                                            "max_strength": 12, "max_strength_b": 7}
    mock = MockFeedbackSender({"sender": values}, lambda: 1)
    mock.update_continuous(low_health=True, healing=False, hp=.5)
    out = mock.send_action("injury_pulse", {"damage_pct": .5})["channels"]
    assert out["A"] == {"strength": 12, "waveform": "official_combo"}
    assert out["B"] == {"strength": 7, "waveform": "official_combo"}


def test_zero_layers_do_not_steal_wave_and_one_zero_channel_does_not_stop_other():
    values = settings.default_settings() | {"channel_mode": "both", "low_health_strength": 0,
                                            "healing_sustain_strength": 0, "healing_sustain_strength_b": 0,
                                            "heal_pulse_strength": 0, "heal_pulse_strength_b": 0}
    mock = MockFeedbackSender({"sender": values}, lambda: 1)
    mock.update_continuous(low_health=True, healing=True, hp=.5)
    out = mock.send_action("heal_pulse")["channels"]
    assert out["A"]["strength"] == 0 and out["A"]["waveform"] == ""
    assert out["B"] == {"strength": 10, "waveform": "weak_pulse"}


@pytest.mark.parametrize("event", [event for event, *_ in settings.EVENTS])
@pytest.mark.parametrize("wave", list(WAVEFORM_V3))
def test_every_event_can_choose_every_bundled_wave(event, wave):
    values = settings.default_settings() | {"channel_mode": "both", f"{event}_waveform": wave}
    mock = MockFeedbackSender({"sender": values}, lambda: 1)
    if event in ("low_health", "healing_sustain"):
        out = mock.update_continuous(low_health=event == "low_health", healing=event == "healing_sustain", hp=.5)
    else:
        out = mock.send_action(event)
    assert all(value["waveform"] == wave for value in out["channels"].values())


@pytest.mark.parametrize("bad", [-1, 101, True, "", "nan", "Infinity", 1.5, "20.0", "２０", None])
def test_invalid_ui_strength_rejected_without_modifying_file(tmp_path, bad):
    path = tmp_path / "feedback_settings.ini"
    original = settings.default_settings()
    settings.save_settings(path, original)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        settings.save_settings(path, original | {"death_pulse_strength_b": bad})
    assert path.read_bytes() == before


def test_settings_survive_restart_preserve_disabled_channel_and_snapshot(tmp_path):
    path = tmp_path / "feedback_settings.ini"
    store = settings.SettingsStore(path)
    assert not path.exists()
    values = settings.default_settings() | {"channel_mode": "b", "death_pulse_strength": 71}
    store.save(values)
    restored = settings.SettingsStore(path)
    assert restored.snapshot() == values
    snapshot = restored.snapshot()
    snapshot["death_pulse_strength"] = 1
    assert restored.snapshot()["death_pulse_strength"] == 71
    with pytest.raises(ValueError, match="停止全部"):
        restored.save(settings.default_settings(), running=True)
    assert settings.load_settings(path) == values


@pytest.mark.parametrize("text", ["bad data", "[other]\nx=1", "[sender]\nchannel_mode=z",
                                  "[sender]\nheal_pulse_waveform=missing", "[DEFAULT]\nx=1\n[sender]"])
def test_corrupt_settings_block_until_explicit_save(tmp_path, text):
    path = tmp_path / "feedback_settings.ini"
    path.write_text(text, encoding="utf-8")
    store = settings.SettingsStore(path)
    with pytest.raises(ValueError):
        store.snapshot()
    assert path.read_text(encoding="utf-8") == text
    store.save(settings.default_settings())
    assert store.snapshot() == settings.default_settings()


def test_disk_failure_keeps_previous_file_and_store_state(tmp_path, monkeypatch):
    path = tmp_path / "feedback_settings.ini"
    store = settings.SettingsStore(path)
    store.save(settings.default_settings())
    before = path.read_bytes()
    def fail(*args):
        raise OSError("read-only disk")
    monkeypatch.setattr(settings.os, "replace", fail)
    with pytest.raises(OSError):
        store.save(store.values | {"channel_mode": "both"})
    assert path.read_bytes() == before
    assert store.snapshot()["channel_mode"] == "a"
    assert not list(tmp_path.glob(".feedback-*.tmp"))


def test_session_receives_exact_settings_without_network_override(tmp_path):
    values = settings.default_settings() | {"channel_mode": "both", "death_pulse_strength_b": 52}
    path = make_config(tmp_path / "session.ini", 1, (1920,1080), "mock", feedback_settings=values)
    cfg = load_config(path)
    assert all(cfg["sender"][key] == value for key, value in values.items())
    with pytest.raises(ValueError):
        make_config(tmp_path / "bad.ini", 1, (1920,1080), "mock", feedback_settings={"ws_host": "evil"})
    assert not (tmp_path / "bad.ini").exists()


def test_offline_guide_has_runtime_path_and_all_event_rules():
    guide = guide_text()
    assert "node.exe" in guide and "0＜血量＜75%" in guide
    assert all(word in guide for word in ("死亡", "治疗确认", "持续舒缓", "累计", "A+B", "防火墙", "F8"))
