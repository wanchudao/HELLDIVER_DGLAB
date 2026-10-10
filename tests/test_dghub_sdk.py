"""SDK v1 adapter tests; transport injection only, never register a real host."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dghub_plugin.sdk_config import EVENTS, core_config, defaults, manifest, validate
from dghub_plugin.sdk_session import SDKSession, SerializedWriter
from dghub_plugin.preset_catalog import PresetInfo
from src.config_loader import load_config
from src.state_machine import DetectionResult
from test_plugin_offline import run_async


ROOT = Path(__file__).resolve().parents[1]


def settings(mode="both"):
    values = defaults()
    values["channel_mode"] = mode
    for event, *_ in EVENTS:
        values[event + "_preset"] = "测试-" + event
    return values


def messages(batch):
    return [json.loads(raw) for raw in batch.messages]


def receive(session, **data):
    return session.receive(json.dumps(data))


def observation(hp, valid=True):
    return DetectionResult(health_raw=hp, health_stable=hp, confidence=1,
                           anchor_score=1, valid=valid, state="injured" if valid else "unknown")


def catalog_fixture(prefix="测试-"):
    counts = dict(injury_pulse=8, low_health=11, heal_pulse=11,
                  healing_sustain=16, death_pulse=6)
    return {prefix + event: PresetInfo(prefix + event, count, 3, False)
            for event, count in counts.items()}


@pytest.fixture
def client():
    now = [0.0]
    session = SDKSession(load_config(ROOT / "config.example.ini"), clock=lambda: now[0],
                         available_presets={"测试-" + name for name, *_ in EVENTS},
                         preset_catalog=catalog_fixture(),
                         log_writer=SimpleNamespace(append=lambda row: None, close=lambda: None))
    session.test_clock = now
    yield session
    session.close()


def ready(client, values=None):
    hello = messages(client.hello("test-only-token"))
    assert hello[0]["op"] == "hello" and hello[0]["manifest"] == manifest()
    receive(client, op="hello_ack", accepted=True, sdk_version="1.0")
    receive(client, op="config", data=values or settings())
    receive(client, op="device_info", connected=True, device_type="v3",
            max_strength_a=200, max_strength_b=200)
    assert not client.armed and client.phase == "ready"
    client.set_running(True)


def frame(client, timestamp, hp, valid=True, ticket=None):
    client.test_clock[0] = timestamp
    batch = client.observe(observation(hp, valid), client.frame_ticket() if ticket is None else ticket)
    packets = messages(batch)
    for delivery in batch.deliveries:
        client.delivered(batch, delivery)
    return packets


def output(client):
    return client.vision.mock_output()["channels"]


def test_manifest_uses_only_documented_schema_and_independent_presets():
    value = manifest()
    assert value["sdk"] == "1" and value["id"] == "helldiver_dglab"
    fields = [field for section in value["config_schema"] for field in section["fields"]]
    assert len({field["key"] for field in fields}) == len(fields)
    assert sum(field["type"] == "preset" for field in fields) == 5
    assert sum(field["type"] == "percent" for field in fields) == 12


@pytest.mark.parametrize("key,value", [
    ("running", "true"), ("running", 1), ("channel_mode", "c"),
    ("max_strength", True), ("max_strength", 101), ("max_strength_b", -1),
    ("injury_pulse_strength_b", 2.5), ("monitor_index", 0),
    ("monitor_index", 33), ("low_health_preset", ""),
    ("low_health_preset", "\nwave"), ("death_pulse_preset", " wave"),
    ("idle_strength", 100), ("idle_strength", False), ("unknown", 1),
])
def test_bad_config_is_rejected(key, value):
    values = settings()
    values[key] = value
    with pytest.raises(ValueError):
        validate(values)


def test_unknown_catalog_preset_is_rejected_and_defaults_are_usable():
    with pytest.raises(ValueError, match="Unknown"):
        validate(settings(), available_presets=set())
    selected = validate(defaults())
    assert len({selected[event + "_preset"] for event, *_ in EVENTS}) == 5


def test_mapping_never_mutates_main_config_or_uses_host_names_as_wave_ids():
    base = load_config(ROOT / "config.example.ini")
    old = deepcopy(base)
    mapped = core_config(base, validate(settings()))
    assert base == old
    assert mapped["trigger"]["sender_mode"] == "mock"
    assert len({mapped["sender"][event + "_waveform"] for event, *_ in EVENTS}) == 5


def test_persisted_running_true_does_not_start(client):
    client.hello("test")
    receive(client, op="hello_ack", accepted=True, sdk_version="1")
    data = settings()
    data["running"] = True
    packets = messages(receive(client, op="config", data=data))
    assert not client.armed
    assert not any(packet["op"] == "set_config" for packet in packets)
    client.set_running(True)
    assert client.armed and not client.device_output_enabled  # recognition only


@pytest.mark.parametrize("message", [
    {"op": "config", "data": settings()},
    {"op": "hello_ack", "accepted": False, "sdk_version": "1"},
    {"op": "hello_ack", "accepted": True, "sdk_version": "2.0"},
])
def test_rejected_or_out_of_order_handshake_terminates(client, message):
    client.hello("test")
    receive(client, **message)
    assert client.closed and not client.armed


@pytest.mark.parametrize("raw", ["[]", "null", "{", '{"op":"ping","t":NaN}', " " * 65537],
                         ids=["array", "null", "broken", "nonfinite", "oversized"])
def test_bad_json_fails_closed_without_echoing_message(client, raw):
    ready(client)
    client.receive(raw)
    assert client.closed and client.last_error == "Host protocol/configuration validation failed"


@pytest.mark.parametrize("at_config", [False, True])
def test_handshake_and_config_timeout(client, at_config):
    client.hello("test")
    if at_config:
        receive(client, op="hello_ack", accepted=True, sdk_version="1")
    client.test_clock[0] = 5
    client.tick()
    assert client.closed


def test_ping_is_consumed_without_unsupported_196_json_pong(client):
    ready(client)
    assert messages(receive(client, op="ping", t=123.5)) == []


def test_server_pong_is_consumed_without_a_reply_or_session_restart(client):
    ready(client)
    ticket = client.frame_ticket()
    assert not receive(client, op="pong", t=123.5).messages
    assert not client.closed and client.frame_ticket() == ticket


@pytest.mark.parametrize("mode,active", [("a", {"a"}), ("b", {"b"}), ("both", {"a", "b"})])
def test_three_modes_and_native_preset_names(client, mode, active):
    data = settings(mode)
    data["low_health_strength_b"] = 7
    ready(client, data)
    frame(client, 0, 1)
    packets = frame(client, .1, .5)
    positives = {p["channel"]: p["delta_pct"] for p in packets if p.get("action") == "strength"}
    assert set(positives) == active
    assert positives == {channel: 10 if channel == "a" else 7 for channel in active}
    waves = [p for p in packets if p["op"] == "pulse"]
    assert {p["channel"] for p in waves} == ({"both"} if len(active) == 2 else active)
    assert all(p["preset"] == "测试-low_health" for p in waves)
    assert all(set(p) == {"op", "channel", "preset"} for p in waves)
    assert all(p["strength_mode"] == "rollback" and p["duration_s"] == .30
               for p in packets if p.get("action") == "strength")


def test_shared_rules_damage_heal_death_and_sustain(client):
    ready(client)
    frame(client, 0, 1)
    frame(client, .1, .7)
    frame(client, .15, .6)
    damaged = frame(client, .31, .6)
    assert output(client)["A"]["strength"] == 18
    assert any(p.get("preset") == "测试-injury_pulse" for p in damaged)
    healed = frame(client, .4, .8)
    assert output(client)["A"]["strength"] == 28  # pending injury 8 + heal 15 + sustain 5
    assert any(p.get("preset") == "测试-heal_pulse" for p in healed)
    dead = frame(client, .5, 0)
    assert output(client)["A"]["strength"] == 40
    assert any(p.get("preset") == "测试-death_pulse" for p in dead)
    frame(client, .6, 0)
    assert client.vision.trigger._death_latched
    frame(client, .7, .3)
    assert output(client)["A"]["waveform"] == "official_tide"


def test_75_boundary_and_invalid_baseline_never_fake_events(client):
    ready(client)
    frame(client, 0, .75)
    assert output(client)["A"]["strength"] == 0
    frame(client, .1, .749)
    assert output(client)["A"]["strength"] == 10
    invalid = frame(client, .2, 0, valid=False)
    assert output(client)["A"]["strength"] == 0
    assert any(p.get("fields", {}).get("display_status") == "NO HUD" for p in invalid)
    frame(client, .3, 1)
    assert output(client)["A"]["strength"] == 0


def test_sustain_after_confirmation_and_event_expiry(client):
    ready(client)
    frame(client, 0, .8)
    frame(client, .1, .85)
    for t in (.4, .7, 1, 1.3, 1.6, 1.9):
        frame(client, t, .85 if t < 1 else .9)
    packets = frame(client, 2.11, .9)
    assert output(client)["A"]["strength"] == 5
    assert any(p.get("preset") == "测试-healing_sustain" for p in packets)
    frame(client, 2.8, .9)
    frame(client, 3.1, .9)
    assert output(client)["A"]["strength"] == 0


def test_zero_contribution_and_independent_limits(client):
    data = settings()
    data.update(injury_pulse_strength=0, injury_pulse_strength_b=0,
                low_health_strength=0, low_health_strength_b=80, max_strength_b=25)
    ready(client, data)
    frame(client, 0, 1)
    frame(client, .1, .4)
    packets = frame(client, .31, .4)
    assert output(client)["A"]["strength"] == 0
    assert output(client)["B"]["strength"] == 25
    assert not any(p.get("preset") == "测试-injury_pulse" for p in packets)


def test_config_changes_wait_for_restart_even_while_paused(client):
    ready(client)
    receive(client, op="config_changed", key="low_health_strength", value=99)
    assert client.pending["low_health_strength"] == 99 and client.settings["low_health_strength"] == 10
    client.pause()
    receive(client, op="config_changed", key="channel_mode", value="b")
    assert client.pending["low_health_strength"] == 99 and client.settings["channel_mode"] == "both"


@pytest.mark.parametrize("key,value", [("target_id", "device-new"), ("enabled", False)])
@pytest.mark.parametrize("full_config", [False, True])
def test_host_configuration_change_invalidates_output_without_changing_hud(client, key, value, full_config):
    data = settings()
    data.update(target_id="device-original", enabled=True, idle_strength=0)
    ready(client, data)
    ticket = client.frame_ticket()
    old = client.observe(observation(.5), ticket)
    if full_config:
        data[key] = value
        batch = receive(client, op="config", data=data)
    else:
        batch = receive(client, op="config_changed", key=key, value=value)
    assert not client.armed and not client.current(old)
    assert client.host_configuration[key] == value
    assert client.settings["hud_scale_percent"] == 100
    assert {p["channel"] for p in messages(batch)
            if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}
    assert not client.observe(observation(.5), ticket).messages


def test_echoed_running_state_does_not_loop_or_reset_current_frame(client):
    ready(client)
    ticket = client.frame_ticket()
    assert not receive(client, op="config_changed", key="running", value=True).messages
    assert client.frame_ticket() == ticket
    packets = messages(receive(client, op="config_changed", key="running", value=False))
    assert packets == [] and client.armed and client.frame_ticket() == ticket
    packets = messages(receive(client, op="config_changed", key="running", value=False))
    assert not any(p["op"] == "set_config" for p in packets)


@pytest.mark.parametrize("cause", ["pause", "frame_timeout", "stop", "device", "capture"])
def test_stops_invalidate_old_frame_and_output_batches(client, cause):
    ready(client)
    frame(client, 0, 1)
    ticket = client.frame_ticket()
    client.test_clock[0] = .1
    old = client.observe(observation(.5), ticket)
    if cause == "pause":
        zeros = client.pause()
    elif cause == "stop":
        zeros = receive(client, op="stop", reason="manager.stop_plugin")
    elif cause == "device":
        zeros = receive(client, op="device_info", connected=False, device_type="", max_strength_a=0, max_strength_b=0)
    elif cause == "capture":
        zeros = client.process_frame(None, ticket)
    else:
        client.test_clock[0] = 1.11
        zeros = client.tick()
    assert not client.current(old) and not client.armed
    assert {p["channel"] for p in messages(zeros) if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}
    assert frame(client, 1.2, .2, ticket=ticket) == []


def test_terminal_close_cannot_be_rearmed(client):
    ready(client)
    client.close()
    receive(client, op="config", data=settings())
    receive(client, op="config_changed", key="running", value=True)
    client.set_running(True)
    assert client.closed and not client.armed and client.frame_ticket() is None


def test_real_image_core_is_reused_and_blank_frame_does_not_output(client):
    ready(client)
    packets = messages(client.process_frame(np.zeros((1080, 1920, 3), np.uint8), client.frame_ticket()))
    assert not any(p["op"] == "trigger" or (p["op"] == "set_strength" and p["pct"]) for p in packets)


@pytest.mark.parametrize("fail_on", ["a", "b"])
def test_single_channel_failure_attempts_both_zeros_and_terminates(client, fail_on):
    ready(client)
    frame(client, 0, 1)
    batch = client.observe(observation(.5), client.frame_ticket())
    sent = []
    async def send(raw):
        packet = json.loads(raw)
        sent.append(packet)
        if packet.get("action") == "strength" and packet["channel"] == fail_on:
            raise OSError("test failure")
    with pytest.raises(OSError):
        run_async(SerializedWriter(client, send).write(batch))
    assert client.closed
    assert {p["channel"] for p in sent if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}


def test_stop_during_inflight_send_discards_rest_then_zeroes(client):
    ready(client)
    frame(client, 0, 1)
    batch = client.observe(observation(.5), client.frame_ticket())
    sent = []
    async def exercise():
        first = asyncio.Event()
        release = asyncio.Event()
        async def send(raw):
            sent.append(json.loads(raw))
            if len(sent) == 1:
                first.set()
                await release.wait()
        writer = SerializedWriter(client, send)
        pending = asyncio.create_task(writer.write(batch))
        await first.wait()
        stop = asyncio.create_task(writer.write(client.pause()))
        release.set()
        assert not await pending
        assert await stop
    run_async(exercise())
    assert len([p for p in sent if p.get("action") == "strength"]) == 1
    assert not any(p["op"] == "pulse" or p.get("action") == "waveform" for p in sent)
    assert {p["channel"] for p in sent if p["op"] == "set_strength" and not p["pct"]} == {"a", "b"}


def test_expired_batch_fails_closed_instead_of_losing_target_state(client):
    ready(client)
    frame(client, 0, 1)
    batch = client.observe(observation(.5), client.frame_ticket())
    client.test_clock[0] = .2
    sent = []
    async def send(raw):
        sent.append(json.loads(raw))
    with pytest.raises(TimeoutError):
        run_async(SerializedWriter(client, send).write(batch))
    assert client.closed and all(p.get("pct") == 0 for p in sent)


def test_invalid_hud_supersedes_queued_positive_output(client):
    ready(client)
    frame(client, 0, 1)
    old = client.observe(observation(.5), client.frame_ticket())
    zero = client.observe(observation(0, False), client.frame_ticket())
    assert not client.current(old) and client.current(zero)
    assert {p["channel"] for p in messages(zero) if p["op"] == "set_strength" and not p["pct"]} == {"a", "b"}


def test_superseding_batch_contains_complete_both_channel_targets(client):
    ready(client)
    frame(client, 0, 1)
    client.test_clock[0] = .1
    old = client.observe(observation(.6), client.frame_ticket())
    client.test_clock[0] = .31
    latest = client.observe(observation(.6), client.frame_ticket())
    assert not client.current(old)
    assert {p["channel"]: p["delta_pct"] for p in messages(latest)
            if p.get("action") == "strength"} == {"a": 18, "b": 18}


def test_sdk_executable_selfcheck_and_default_entry_never_allocate_socket(monkeypatch):
    import socket
    from tools import dghub_sdk_entry
    def forbidden(*args, **kwargs):
        pytest.fail("SDK development preview must not open any socket")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "socketpair", forbidden)
    assert dghub_sdk_entry.main([]) == 2
    report = dghub_sdk_entry.self_check()
    assert report["status"] == "ok" and len(report["checks"]) == 36
    assert not report["installed_host_registration_enabled"]


def test_device_limit_change_pauses_and_old_frame_is_rejected(client):
    ready(client)
    ticket = client.frame_ticket()
    receive(client, op="device_info", connected=True, device_type="v3", max_strength_a=100, max_strength_b=200)
    assert not client.armed and not client.observe(observation(.3), ticket).messages


def test_wave_renewal_and_frame_timeout_do_not_need_another_capture(client):
    ready(client)
    frame(client, 0, 1)
    frame(client, .1, .5)
    client.test_clock[0] = .31
    assert any(p["op"] == "trigger" for p in messages(client.tick()))
    client.test_clock[0] = 1.11
    assert all(p.get("pct", 0) == 0 for p in messages(client.tick()))
    assert not client.armed


def test_sender_timeout_and_cancellation_attempt_both_zeros(client):
    ready(client)
    frame(client, 0, 1)
    batch = client.observe(observation(.5), client.frame_ticket())
    sent = []
    async def send(raw):
        data = json.loads(raw)
        sent.append(data)
        if data.get("delta_pct"):
            await asyncio.sleep(10)
    with pytest.raises(TimeoutError):
        run_async(SerializedWriter(client, send).write(batch))
    assert client.closed
    assert {p["channel"] for p in sent if p.get("pct") == 0} == {"a", "b"}


def test_resume_at_unchanged_full_health_refreshes_host_status(client):
    ready(client)
    frame(client, 0, 1)
    client.pause()
    client.set_running(True)
    packets = frame(client, .1, 1)
    assert any(p.get("fields", {}).get("display_status") == "运行中" for p in packets)
