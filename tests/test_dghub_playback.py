"""Receipt-aware SDK scheduling, independently of capture or a device."""
import asyncio
import json
from dataclasses import replace

import pytest

from dghub_plugin.sdk_session import SerializedWriter
from test_dghub_sdk import client, frame, messages, observation, ready, settings
from test_plugin_offline import run_async


def submit(client, batch):
    sent = []
    async def send(raw):
        sent.append(json.loads(raw))
    assert run_async(SerializedWriter(client, send).write(batch))
    return sent


def waves(packets):
    return [p for p in packets if p["op"] == "pulse" or p.get("action") == "waveform"]


def leases(packets):
    return [p for p in packets if p.get("action") == "strength"]


def low(client, mode="both"):
    ready(client, settings(mode))
    frame(client, 0, 1)
    return frame(client, .1, .5)


def test_strength_renews_without_restarting_full_wave(client):
    data = settings()
    data.update(injury_pulse_strength=0, injury_pulse_strength_b=0)
    ready(client, data)
    frame(client, 0, 1)
    assert waves(frame(client, .1, .5)) == [{"op": "pulse", "preset": "测试-low_health", "channel": "both"}]
    client.test_clock[0] = .199
    assert not leases(submit(client, client.tick()))
    for instant in (.2, .3, .4, .5, .6, .7, .8, .9, 1):
        client.test_clock[0] = instant
        packets = submit(client, client.tick())
        assert len(leases(packets)) == 2
        assert not waves(packets)
        assert all(p["duration_s"] == .3 and p["strength_mode"] == "rollback" for p in leases(packets))
    frame(client, 1.05, .5)
    client.test_clock[0] = 1.2
    assert len(waves(submit(client, client.tick()))) == 1


def test_unsent_superseded_wave_remains_due(client):
    ready(client)
    frame(client, 0, 1)
    client.test_clock[0] = .1
    old = client.observe(observation(.5), client.frame_ticket())
    assert client._wave_until == 0
    client.test_clock[0] = .31
    newest = client.observe(observation(.5), client.frame_ticket())
    assert not client.current(old)
    assert waves(submit(client, newest))[0]["preset"] == "测试-injury_pulse"
    assert client._wave_until == pytest.approx(1.11)


def test_duplicate_queued_batches_submit_wave_only_once(client):
    ready(client)
    frame(client, 0, 1)
    first = client.observe(observation(.5), client.frame_ticket())
    second = client.tick()
    assert len(waves(submit(client, first))) == 1
    assert not waves(submit(client, second))


def test_strength_only_revision_during_wave_send_keeps_receipt(client):
    ready(client)
    frame(client, 0, 1)
    client.test_clock[0] = .1
    batch = client.observe(observation(.5), client.frame_ticket())
    pending = []
    sent = []
    async def send(raw):
        packet = json.loads(raw)
        sent.append(packet)
        if packet["op"] == "pulse":
            client.vision.trigger._mock_sender.mixer.low_strength = 17
            pending.append(client.tick())
    async def exercise():
        writer = SerializedWriter(client, send)
        await writer.write(batch)
        await writer.write(pending[0])
    run_async(exercise())
    assert len(waves(sent)) == 1
    assert client._sent_strengths["A"] == 17
    assert client._wave_until == pytest.approx(1.2)


def test_zero_channel_stops_only_it_and_rejoin_restarts_common_cycle(client):
    low(client)
    mixer = client.vision.trigger._mock_sender.mixer
    mixer.low_strength = 0
    client.test_clock[0] = .2
    stopped = submit(client, client.tick())
    assert {"op": "set_strength", "channel": "a", "pct": 0} in stopped
    assert not waves(stopped)
    assert {p["channel"] for p in leases(stopped)} == {"b"}
    mixer.low_strength = 10
    client.test_clock[0] = .25
    joined = submit(client, client.tick())
    assert waves(joined) == [{"op": "pulse", "preset": "测试-low_health", "channel": "both"}]


def test_finite_final_cycle_is_truncated(client):
    ready(client)
    frame(client, 0, .8)
    assert waves(frame(client, .1, .85))[0]["op"] == "pulse"
    frame(client, .7, .85)
    frame(client, 1, .85)
    client.test_clock[0] = 1.2
    packet = waves(submit(client, client.tick()))[0]
    assert packet["op"] == "trigger" and packet["action"] == "waveform"
    assert packet["duration_s"] == pytest.approx(.9)


def test_finite_window_rechecked_after_strength_write_latency(client):
    ready(client)
    frame(client, 0, .8)
    frame(client, .1, .85)
    frame(client, .7, .85)
    frame(client, 1.1, .85)
    frame(client, 1.7, .85)
    client.test_clock[0] = 1.95
    batch = client.tick()
    sent = []
    async def send(raw):
        packet = json.loads(raw)
        sent.append(packet)
        if packet.get("action") == "strength":
            client.test_clock[0] += .04
    run_async(SerializedWriter(client, send).write(batch))
    assert not waves(sent)  # less than one 100ms frame now remains


def test_random_uses_one_second_window_not_empty_pulse(client):
    name = "测试-low_health"
    client.preset_catalog[name] = replace(client.preset_catalog[name], random=True, frame_count=0)
    packets = low(client)
    assert waves(packets) == [{"op": "trigger", "action": "waveform", "preset": name,
                               "channel": "both", "duration_s": 1.0}]


def test_missing_verified_timing_metadata_stops_without_wave(client):
    ready(client)
    frame(client, 0, 1)
    client.preset_catalog = None
    packets = frame(client, .1, .5)
    assert client.closed and not waves(packets) and not leases(packets)
    assert {p["channel"] for p in packets if p["op"] == "set_strength"} == {"a", "b"}


def test_pause_during_wave_send_cannot_restore_cycle(client):
    ready(client)
    frame(client, 0, 1)
    batch = client.observe(observation(.5), client.frame_ticket())
    async def send(raw):
        if json.loads(raw)["op"] == "pulse":
            client.pause()
    assert not run_async(SerializedWriter(client, send).write(batch))
    assert client._wave_until == 0 and not client._sent_waveform
    assert client._sent_strengths == {"A": 0, "B": 0}


@pytest.mark.parametrize("mode", ["a", "b", "both"])
def test_no_positive_lease_after_invalid_hud_and_pause(client, mode):
    low(client, mode)
    frame(client, .2, 0, valid=False)
    client.test_clock[0] = .3
    assert not leases(submit(client, client.tick()))
    assert not waves(submit(client, client.tick()))
    client.pause()
    assert not leases(submit(client, client.tick()))
