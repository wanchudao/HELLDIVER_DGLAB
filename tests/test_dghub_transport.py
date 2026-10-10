"""Injected protocol tests: no host registration, sockets or hardware."""
import asyncio
from contextlib import asynccontextmanager
import json

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from dghub_plugin.sdk_config import PLUGIN_ID, defaults
from dghub_plugin.sdk_transport import DGHubTransport, HostEnvironment, default_connector, _WIRE_LOGGER
from test_dghub_sdk import client, messages, observation, settings
from test_plugin_offline import run_async


def launch_environment(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    return dict(DGHUB_HOST="127.0.0.1", DGHUB_PORT="56789", DGHUB_TOKEN="fixture-only:a?&",
                DGHUB_PLUGIN_ID=PLUGIN_ID, DGHUB_PLUGIN_ROOT=str(tmp_path))


@pytest.mark.parametrize("key,value", [
    ("DGHUB_HOST", "192.168.2.122"), ("DGHUB_HOST", "example.com"),
    ("DGHUB_HOST", "127.0.0.1:9999"), ("DGHUB_HOST", "0.0.0.0"),
    ("DGHUB_PORT", "0"), ("DGHUB_PORT", "65536"), ("DGHUB_PORT", "+123"),
    ("DGHUB_PORT", "12.0"), ("DGHUB_PORT", "１２３"), ("DGHUB_TOKEN", ""),
    ("DGHUB_TOKEN", "bad\nsecret"), ("DGHUB_PLUGIN_ID", "other_plugin"),
    ("DGHUB_PLUGIN_ROOT", "relative/plugin"),
])
def test_launch_environment_fails_without_echoing_secrets(tmp_path, key, value):
    data = launch_environment(tmp_path)
    data[key] = value
    with pytest.raises(ValueError) as caught:
        HostEnvironment.from_environ(data)
    assert "fixture-only" not in str(caught.value)
    if value:
        assert value not in str(caught.value)


@pytest.mark.parametrize("host,uri_host", [("127.0.0.1", "127.0.0.1"),
                                         ("localhost", "127.0.0.1"), ("::1", "[::1]")])
def test_host_uri_is_loopback_and_token_is_encoded_not_represented(tmp_path, host, uri_host):
    data = launch_environment(tmp_path)
    data["DGHUB_HOST"] = host
    env = HostEnvironment.from_environ(data)
    assert env.uri() == f"ws://{uri_host}:56789/ws/plugin?token=fixture-only%3Aa%3F%26"
    assert env.token not in repr(env)


def test_incomplete_environment_or_missing_manifest_is_rejected(tmp_path):
    data = launch_environment(tmp_path)
    data.pop("DGHUB_TOKEN")
    with pytest.raises(ValueError):
        HostEnvironment.from_environ(data)
    data = launch_environment(tmp_path)
    (tmp_path / "manifest.json").unlink()
    with pytest.raises(ValueError):
        HostEnvironment.from_environ(data)


def test_default_connector_never_follows_redirect_or_emits_wire_debug():
    connector = default_connector()
    exception = RuntimeError("redirect with sensitive URI")
    assert connector.process_redirect(None, exception) is exception
    assert not _WIRE_LOGGER.isEnabledFor(10) and not _WIRE_LOGGER.propagate


class WireFixture:
    def __init__(self, *, reject=False, fault_on=None):
        self.reject = reject
        self.fault_on = fault_on
        self.sent = []
        self.incoming = asyncio.Queue()
        self.open = False

    async def push(self, **packet):
        await self.incoming.put(json.dumps(packet))

    async def send(self, raw):
        assert self.open
        packet = json.loads(raw)
        self.sent.append(packet)
        if self.fault_on and packet.get("channel") == self.fault_on and packet.get("delta_pct"):
            raise OSError("send fixture failure")
        if packet["op"] == "hello":
            await self.push(op="hello_ack", accepted=not self.reject, sdk_version="1.0")
            if not self.reject:
                await self.push(op="config", data=settings())
                await self.push(op="device_info", connected=True, device_type="v3",
                                max_strength_a=200, max_strength_b=200)
                await self.push(op="ping", t=123.5)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    @asynccontextmanager
    async def connect(self, uri, **options):
        assert uri.startswith("ws://127.0.0.1:")
        assert options["proxy"] is None and options["max_size"] == 65536
        self.open = True
        try:
            yield self
        finally:
            self.open = False


class FrameFixtureDriver:
    def __init__(self, wire, clock):
        self.wire = wire
        self.clock = clock
        self.step = 0
        self.closed = False

    def poll(self, session):
        if session.phase != "ready" or not session.device:
            return []
        if self.step == 0:
            self.step += 1
            return [session.set_running(True), session.observe(observation(1), session.frame_ticket())]
        if self.step == 1:
            self.step += 1
            self.clock[0] = .1
            return [session.observe(observation(.5), session.frame_ticket())]
        if self.step == 2:
            self.step += 1
            self.wire.incoming.put_nowait(json.dumps({"op": "stop", "reason": "fixture"}))
        return []

    def close(self):
        self.closed = True


class ClosingWireFixture(WireFixture):
    """Host sends an explicit stop, then closes before the zero/status replies."""
    def __init__(self, *, close_error=None, explicit_stop=True):
        super().__init__()
        self.close_error = close_error or ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)
        self.explicit_stop = explicit_stop
        self.peer_closed = False

    async def __anext__(self):
        raw = await super().__anext__()
        if json.loads(raw).get("op") == "stop":
            self.peer_closed = True
            if not self.explicit_stop:
                raise self.close_error
        return raw

    async def send(self, raw):
        if self.peer_closed:
            self.sent.append(json.loads(raw))
            await asyncio.sleep(0)
            raise self.close_error
        await super().send(raw)


def test_explicit_host_stop_then_normal_socket_close_is_graceful(client, tmp_path):
    wire = ClosingWireFixture()
    driver = FrameFixtureDriver(wire, client.test_clock)
    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
                               connector=wire.connect, driver=driver, tick_interval=.005, activation_delay=0)
    assert run_async(asyncio.wait_for(transport.run(), 3)) == 0
    assert transport.reason == "session_stopped" and not client.last_error
    assert client.closed and not client.armed and driver.closed and not wire.open
    assert client._last_hp is None and client._last_frame_at is None
    assert {packet["channel"] for packet in wire.sent if packet["op"] == "set_strength"
            and packet["pct"] == 0} == {"a", "b"}


@pytest.mark.parametrize("wire_options", [
    {"explicit_stop": False},
    {"close_error": OSError("test send failure after stop")},
    {"close_error": TimeoutError("test timeout after stop")},
    {"close_error": ConnectionClosedError(Close(1011, ""), Close(1011, ""), True)},
])
def test_stop_close_handling_does_not_hide_unrequested_close_or_send_fault(client, tmp_path, wire_options):
    wire = ClosingWireFixture(**wire_options)
    driver = FrameFixtureDriver(wire, client.test_clock)
    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
                               connector=wire.connect, driver=driver, tick_interval=.005, activation_delay=0)
    assert run_async(asyncio.wait_for(transport.run(), 3)) == 1
    assert client.closed and not client.armed and driver.closed and not wire.open
    assert client._last_hp is None and client._last_frame_at is None
    assert client.last_error == "DGHub connection or send failed"


@pytest.mark.parametrize("fault", [OSError("concurrent send failure"),
                                   ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)])
def test_concurrent_nonterminal_fault_is_not_hidden_by_terminal_normal_close(client, tmp_path, fault):
    wire = ClosingWireFixture()
    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
                               connector=wire.connect, tick_interval=.005, activation_delay=0)

    async def faulting_producer():
        while not client.device:
            await asyncio.sleep(0)
        await transport._write(client.set_running(True))
        await transport._write(client.observe(observation(1), client.frame_ticket()))
        await wire.push(op="stop")
        while not wire.peer_closed:
            await asyncio.sleep(0)
        raise fault

    transport._tick = faulting_producer
    assert run_async(asyncio.wait_for(transport.run(), 3)) == 1
    assert client.closed and not client.armed and not wire.open
    assert client.last_error == "DGHub connection or send failed"


def test_real_transport_path_handshake_config_frames_ping_and_host_stop(client, tmp_path):
    wire = WireFixture()
    driver = FrameFixtureDriver(wire, client.test_clock)
    env = HostEnvironment.from_environ(launch_environment(tmp_path))
    transport = DGHubTransport(client, env, connector=wire.connect, driver=driver, tick_interval=.005, activation_delay=0)
    assert run_async(asyncio.wait_for(transport.run(), 3)) == 0
    assert transport.finished and driver.closed and not transport.connected
    assert wire.sent[0]["op"] == "hello"
    assert not any(p["op"] == "pong" for p in wire.sent)
    assert {p["channel"] for p in wire.sent if p.get("delta_pct", 0) > 0} == {"a", "b"}
    assert {p["preset"] for p in wire.sent if p["op"] == "pulse"} == {"测试-low_health"}
    last_positive = max(i for i, p in enumerate(wire.sent) if p.get("delta_pct", 0) > 0)
    assert {p["channel"] for p in wire.sent[last_positive + 1:]
            if p["op"] == "set_strength" and not p["pct"]} == {"a", "b"}
    with pytest.raises(RuntimeError):
        run_async(transport.run())


@pytest.mark.parametrize("fault_on", ["a", "b"])
def test_transport_channel_failure_zeroes_both_before_socket_context_closes(client, tmp_path, fault_on):
    wire = WireFixture(fault_on=fault_on)
    driver = FrameFixtureDriver(wire, client.test_clock)
    env = HostEnvironment.from_environ(launch_environment(tmp_path))
    transport = DGHubTransport(client, env, connector=wire.connect, driver=driver, tick_interval=.005, activation_delay=0)
    assert run_async(asyncio.wait_for(transport.run(), 3)) == 1
    assert client.closed and not client.armed and driver.closed and not wire.open
    last_positive = max(i for i, p in enumerate(wire.sent) if p.get("delta_pct", 0) > 0)
    assert {p["channel"] for p in wire.sent[last_positive + 1:]
            if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}


def test_rejected_handshake_never_sends_positive_output(client, tmp_path):
    wire = WireFixture(reject=True)
    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
                               connector=wire.connect, tick_interval=.005)
    assert run_async(asyncio.wait_for(transport.run(), 3)) == 1
    assert not any(p["op"] == "trigger" or p.get("pct", 0) for p in wire.sent)


@pytest.mark.parametrize("cause", ["request_stop", "cancel", "disconnect"])
def test_all_transport_exit_paths_close_driver_and_clear_local_state(client, tmp_path, cause):
    wire = WireFixture()
    driver = FrameFixtureDriver(wire, client.test_clock)
    driver.step = 99
    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
                               connector=wire.connect, driver=driver, tick_interval=.005, activation_delay=0)

    async def exercise():
        task = asyncio.create_task(transport.run())
        while not client.device:
            assert not task.done(), "Fixture handshake ended before device state"
            await asyncio.sleep(.001)
        await transport._write(client.set_running(True))
        await transport._write(client.observe(observation(1), client.frame_ticket()))
        client.test_clock[0] = .1
        await transport._write(client.observe(observation(.5), client.frame_ticket()))
        if cause == "request_stop":
            transport.request_stop()
        elif cause == "cancel":
            task.cancel()
        else:
            wire.incoming.put_nowait(None)
        if cause == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == (1 if cause == "disconnect" else 0)

    run_async(asyncio.wait_for(exercise(), 3))
    assert driver.closed and client.closed and not client.armed and not wire.open
    last_positive = max(i for i, p in enumerate(wire.sent) if p.get("delta_pct", 0) > 0)
    assert {p["channel"] for p in wire.sent[last_positive + 1:]
            if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}


def test_startup_settling_gate_requires_fresh_user_start_after_expiry(client, tmp_path):
    wire = WireFixture()
    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
                               connector=wire.connect, activation_delay=.05, tick_interval=.005)
    async def exercise():
        task = asyncio.create_task(transport.run())
        while not client.device:
            await asyncio.sleep(.001)
        assert client.activation_pending and not client.status_fields("test")["output_ready"]
        await transport._write(client.set_running(True))
        assert not client.armed
        await asyncio.sleep(.07)
        assert not client.activation_pending and not client.armed
        assert client.status_fields("test")["output_ready"]
        await transport._write(client.set_running(True))
        assert client.armed
        transport.request_stop()
        assert await task == 0
    run_async(asyncio.wait_for(exercise(), 1))


@pytest.mark.parametrize("delay", [True, -1, 6, float("nan"), float("inf")])
def test_invalid_settling_interval_rejected(client, tmp_path, delay):
    with pytest.raises(ValueError):
        DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)), activation_delay=delay)
