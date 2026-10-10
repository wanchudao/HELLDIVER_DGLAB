"""Local recognition controls must not depend on a host/device start switch."""
import json

import pytest

from dghub_plugin.sdk_config import defaults, manifest, validate
from test_dghub_sdk import client, frame, messages, observation, ready, receive, settings


def configured_without_device(client):
    client.hello("test")
    receive(client, op="hello_ack", accepted=True, sdk_version="1")
    receive(client, op="config", data=settings() | {"running": True})
    receive(client, op="device_info", connected=False, device_type="v3")


def test_f8_equivalent_can_start_recognition_without_connected_device(client):
    configured_without_device(client)
    batch = client.set_running(True)
    assert client.armed and client.frame_ticket() is not None
    assert not any(p["op"] == "set_config" for p in messages(batch))
    frame(client, 0, 1)
    packets = frame(client, .1, .5) + frame(client, .3, .5)
    assert client._last_hp == .5 and client.armed
    assert not any(p["op"] in {"trigger", "pulse"} for p in packets)
    status = client.status_fields("test")
    assert status["recognition_running"] and status["recognition_only"]
    assert not status["device_connected"] and not status["device_output_enabled"]
    assert client.output_snapshot()["A"]["strength"] == 0


def test_host_schema_has_insurance_not_start_button():
    fields = {field["key"]: field for section in manifest()["config_schema"] for field in section["fields"]}
    assert "running" not in fields
    assert fields["output_allowed"]["default"] is True
    assert "保险" in fields["output_allowed"]["label"]
    assert validate(defaults() | {"running": True}) == defaults()


@pytest.mark.parametrize("legacy", [True, False])
def test_old_host_running_changes_never_start_or_pause_local_recognition(client, legacy):
    configured_without_device(client)
    receive(client, op="config_changed", key="running", value=legacy)
    assert not client.armed
    client.set_running(True)
    ticket = client.frame_ticket()
    assert not receive(client, op="config_changed", key="running", value=legacy).messages
    assert client.armed and client.frame_ticket() == ticket


def test_insurance_off_stops_both_invalidates_old_work_and_on_does_not_start(client):
    ready(client)
    frame(client, 0, 1)
    client.test_clock[0] = .1
    old = client.observe(observation(.5), client.frame_ticket())
    stopped = receive(client, op="config_changed", key="output_allowed", value=False)
    assert not client.armed and not client.current(old)
    assert {p["channel"] for p in messages(stopped) if p["op"] == "set_strength"} == {"a", "b"}
    client.set_running(True)
    assert not client.armed and client.last_pause_reason == "output_safety_locked"
    receive(client, op="config_changed", key="output_allowed", value=True)
    assert not client.armed and client._last_hp is None
    client.set_running(True)
    assert client.armed


def test_full_config_insurance_change_is_immediate_other_settings_stay_pending(client):
    ready(client)
    data = settings() | {"output_allowed": False, "hud_scale_percent": 125}
    receive(client, op="config", data=data)
    assert not client.armed and not client.settings["output_allowed"]
    assert client.settings["hud_scale_percent"] == 100
    assert client.pending["hud_scale_percent"] == 125


def test_device_connect_during_recognition_requires_new_f8_and_baseline(client):
    configured_without_device(client)
    client.set_running(True)
    frame(client, 0, .5)
    receive(client, op="device_info", connected=True, device_type="v3", max_strength_a=200, max_strength_b=200)
    assert not client.armed and client._last_hp is None
    receive(client, op="config_changed", key="running", value=True)
    assert not client.armed
    client.set_running(True)
    frame(client, .1, 1)
    assert not any(p["op"] == "trigger" for p in frame(client, .2, 1))


@pytest.mark.parametrize("key", ["output_allowed", "running"])
@pytest.mark.parametrize("value", [1, "true", None])
def test_invalid_insurance_or_legacy_boolean_is_not_accepted(key, value):
    with pytest.raises(ValueError):
        validate(defaults() | {key: value})


def test_disabled_host_plugin_cannot_be_rearmed_locally(client):
    ready(client)
    receive(client, op="config_changed", key="enabled", value=False)
    client.set_running(True)
    assert not client.armed
