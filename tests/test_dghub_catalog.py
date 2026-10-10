"""Bounded catalog HTTP parser and runtime gate; all I/O explicitly injected."""
import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from dghub_plugin.preset_catalog import (
    CATALOG_PATH, MAX_BYTES, MAX_HEADER_BYTES, PresetInfo, decode_catalog,
    decode_names, fetch_catalog, fetch_catalog_snapshot,
)
from dghub_plugin.sdk_transport import DGHubTransport, HostEnvironment
from test_dghub_sdk import client, settings
from test_dghub_transport import WireFixture, launch_environment
from test_plugin_offline import run_async


def payload(rows=None):
    return json.dumps(dict(status="success", presets=rows or [dict(name="呼吸", device_family="coyote")]),
                      ensure_ascii=False).encode("utf-8")


class Writer:
    def __init__(self):
        self.data = b""
        self.closed = False
        self.waited = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True


def response(body=None, headers=None, status="200 OK"):
    body = payload() if body is None else body
    headers = headers or ["Content-Type: application/json", f"Content-Length: {len(body)}"]
    return (f"HTTP/1.1 {status}\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


def fixture(raw, *, stalled=False):
    writer = Writer()
    addresses = []

    async def opener(host, port, **kwargs):
        addresses.append((host, port))
        reader = asyncio.StreamReader(**kwargs)
        if not stalled:
            reader.feed_data(raw)
            reader.feed_eof()
        return reader, writer

    return opener, writer, addresses


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
@pytest.mark.parametrize("length", [True, False])
def test_fetch_uses_only_fixed_readonly_route_without_token_proxy_or_redirect(host, length):
    body = payload()
    headers = ["Content-Type: application/json; charset=utf-8"]
    if length:
        headers.append(f"Content-Length: {len(body)}")
    opener, writer, addresses = fixture(response(body, headers))
    env = SimpleNamespace(host=host, port=10001, token="must-not-leak")
    names, digest = run_async(fetch_catalog(env, opener=opener))
    assert names == frozenset({"呼吸"}) and digest == hashlib.sha256(body).hexdigest()
    assert addresses == [(host, 10001)] and writer.closed and writer.waited
    assert writer.data.startswith(f"GET {CATALOG_PATH} HTTP/1.1\r\n".encode())
    assert b"must-not-leak" not in writer.data and b"token" not in writer.data
    assert (b"Host: [::1]:10001" if host == "::1" else b"Host: 127.0.0.1:10001") in writer.data


@pytest.mark.parametrize("row", [None, [], {}, dict(name="呼吸"), dict(name="呼吸", device_family="possum"),
    dict(name="", device_family="coyote"), dict(name=" padded", device_family="coyote"),
    dict(name="x\n", device_family="coyote"), dict(name="x\x7f", device_family="coyote"),
    dict(name="x" * 129, device_family="coyote"), dict(name=True, device_family="coyote")])
def test_invalid_names_or_device_family_are_not_usable_presets(row):
    with pytest.raises(ValueError):
        decode_names(payload([row]))


@pytest.mark.parametrize("body", [b"", b"[]", b"null", b"{}", b'{}{}', b'\xff',
    b'{"status":"success","presets":[]}', b'{"status":"success","presets":null}',
    b'{"status":"failure","presets":[{}]}', b'{"status":"success","status":"success","presets":[]}',
    b'{"status":"success","presets":[{"name":"x","device_family":"coyote","v":NaN}]}',
    payload([dict(name="duplicate", device_family="coyote")] * 2)])
def test_ambiguous_or_invalid_json_rejected(body):
    with pytest.raises(ValueError):
        decode_names(body)


def test_body_and_entry_limits_are_enforced_without_large_allocation(monkeypatch):
    import dghub_plugin.preset_catalog as catalog
    monkeypatch.setattr(catalog, "MAX_BYTES", 64)
    with pytest.raises(ValueError, match="size"):
        decode_names(b"x" * 65)
    monkeypatch.setattr(catalog, "MAX_BYTES", MAX_BYTES)
    with pytest.raises(ValueError, match="entries"):
        decode_names(payload([dict(name="x", device_family="coyote")] * 10001))
    opener, writer, _ = fixture(response(headers=["Content-Type: application/json"]))
    monkeypatch.setattr(catalog, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        run_async(fetch_catalog(SimpleNamespace(host="127.0.0.1", port=1), opener=opener))
    assert writer.closed


@pytest.mark.parametrize("raw", [response(status="302 Found"), response(status="500 Error"),
    response(headers=["Content-Type: text/html"]),
    response(headers=["Content-Type: application/json", "Content-Encoding: gzip"]),
    response(headers=["Content-Type: application/json", "Transfer-Encoding: chunked"]),
    response(headers=["Content-Type: application/json", "Content-Type: application/json"]),
    response(headers=["Content-Type: application/json", "Content-Length: -1"]),
    response(headers=["Content-Type: application/json", "Content-Length: 0"]),
    response(headers=["Content-Type: application/json", "Content-Length: +5"]),
    response(headers=["Content-Type: application/json", f"Content-Length: {MAX_BYTES + 1}"]),
    response(headers=["Content-Type: application/json", "Content-Length: 10000"]),
    response(headers=[" Content-Type: application/json"]),
    b"HTTP/1.1 200 OK\r\n" + b"X:" + b"a" * MAX_HEADER_BYTES + b"\r\n\r\n",
    b"HTTP/1.1 200 OK\r\n"])
def test_http_errors_are_secret_free_and_connection_closed(raw):
    opener, writer, _addresses = fixture(raw)
    with pytest.raises(ValueError) as error:
        run_async(fetch_catalog(SimpleNamespace(host="127.0.0.1", port=1), opener=opener))
    assert writer.closed and writer.waited and "10000" not in str(error.value)
    assert str(error.value) == "DGHub 1.9.6 波形目录不可用或格式不兼容；未启动输出"


@pytest.mark.parametrize("host,port", [("192.168.2.122", 1), ("localhost", 1), ("example.com", 1),
                                      ("127.0.0.1", True), ("127.0.0.1", 0), ("::1", 65536)])
def test_nonlocal_or_invalid_endpoint_never_opens_socket(host, port):
    opener, writer, addresses = fixture(response())
    with pytest.raises(ValueError):
        run_async(fetch_catalog(SimpleNamespace(host=host, port=port), opener=opener))
    assert not addresses and not writer.data


def test_timeout_and_explicit_cancellation_close_without_swallowing_cancellation():
    async def exercise():
        env = SimpleNamespace(host="127.0.0.1", port=1)
        opener, writer, _ = fixture(b"", stalled=True)
        with pytest.raises(ValueError):
            await fetch_catalog(env, opener=opener, timeout=.01)
        assert writer.closed
        opener, writer, _ = fixture(b"", stalled=True)
        task = asyncio.create_task(fetch_catalog(env, opener=opener))
        while not writer.data:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer.closed
    run_async(exercise())


def test_runtime_checks_catalog_before_hello_and_config_then_reports_snapshot(client, tmp_path):
    wire = WireFixture()
    names = frozenset(value for key, value in settings().items() if key.endswith("_preset"))
    events = []

    async def catalog(environment):
        assert wire.sent == [] and not wire.open
        events.append("catalog")
        return names, "f" * 64

    async def exercise():
        transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
            connector=wire.connect, catalog_loader=catalog, tick_interval=.005)
        task = asyncio.create_task(transport.run())
        while client.settings is None:
            assert not task.done()
            await asyncio.sleep(.001)
        assert client.available_presets == names
        assert client.status_fields("test")["preset_catalog"]["state"] == "verified_startup_snapshot"
        transport.request_stop()
        assert await task == 0
    run_async(asyncio.wait_for(exercise(), 2))
    assert events == ["catalog"] and wire.sent[0]["op"] == "hello"


@pytest.mark.parametrize("cause", ["failure", "stop", "cancel"])
def test_catalog_failure_or_shutdown_never_registers_or_starts_output(client, tmp_path, cause):
    wire = WireFixture()
    entered, cancelled = [], []

    async def catalog(environment):
        entered.append(True)
        if cause == "failure":
            raise OSError("must-not-leak-token-uri")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def exercise():
        transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
            connector=wire.connect, catalog_loader=catalog)
        task = asyncio.create_task(transport.run())
        while not entered:
            await asyncio.sleep(0)
        if cause == "stop":
            transport.request_stop()
        elif cause == "cancel":
            task.cancel()
        if cause == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == (1 if cause == "failure" else 0)
        assert client.closed and transport.finished and client.catalog_fingerprint is None
    run_async(asyncio.wait_for(exercise(), 2))
    assert not wire.sent and not wire.open and "must-not-leak" not in client.last_error
    if cause != "failure":
        assert cancelled


def test_unknown_saved_preset_rejected_against_actual_loaded_names(client, tmp_path):
    wire = WireFixture()

    async def catalog(environment):
        return frozenset({"呼吸"}), "e" * 64

    transport = DGHubTransport(client, HostEnvironment.from_environ(launch_environment(tmp_path)),
        connector=wire.connect, catalog_loader=catalog)
    assert run_async(asyncio.wait_for(transport.run(), 2)) == 1
    assert client.closed and client.settings is None
    assert not any(row["op"] == "trigger" or row.get("pct", 0) for row in wire.sent)


def preset_row(*, name="呼吸", count=11, **changes):
    row = dict(name=name, device_family="coyote", format_version=3,
               waveform=[[[10, 10, 10, 10], [0, 25, 75, 100]] for _ in range(count)])
    row.update(changes)
    return row


@pytest.mark.parametrize("name,count", [
    ("连击", 8), ("呼吸", 11), ("潮汐", 11), ("心跳节奏", 16), ("CS2-死亡", 6),
    ("长模板", 87), ("最短模板", 1),
])
def test_full_cycle_metadata_is_frame_count_not_short_trigger_prefix(name, count):
    entry = decode_catalog(payload([preset_row(name=name, count=count)]))[name]
    assert entry == PresetInfo(name, count, 3, False)
    assert entry.cycle_seconds == count / 10


def test_snapshot_and_entries_cannot_be_mutated_after_validation():
    entries = decode_catalog(payload([preset_row()]))
    with pytest.raises(TypeError):
        entries["other"] = entries["呼吸"]
    with pytest.raises(FrozenInstanceError):
        entries["呼吸"].frame_count = 1


@pytest.mark.parametrize("version", [None, 3])
def test_native_random_empty_template_has_no_fixed_cycle(version):
    row = dict(name="🎲 随机波形", device_family="coyote", random=True, waveform=[])
    if version is not None:
        row["format_version"] = version
    entry = decode_catalog(payload([row]))[row["name"]]
    assert entry.random and entry.frame_count == 0 and entry.cycle_seconds == 0
    assert entry.format_version == version


@pytest.mark.parametrize("changes", [
    {"format_version": None}, {"format_version": 2}, {"format_version": True},
    {"format_version": "3"}, {"random": "true"}, {"random": 1}, {"random": None},
    {"waveform": []}, {"waveform": None}, {"waveform": {}},
    {"waveform": [None]}, {"waveform": [[]]},
    {"waveform": [[[10] * 4]]}, {"waveform": [[[10] * 4, [0] * 4, [0] * 4]]},
    {"waveform": [[[10] * 3, [0] * 4]]},
    {"waveform": [[[10] * 4, [0] * 5]]},
    {"waveform": [[[241] * 4, [0] * 4]]},
    {"waveform": [[[-1] * 4, [0] * 4]]},
    {"waveform": [[[10] * 4, [101] * 4]]},
    {"waveform": [[[10] * 4, [-1] * 4]]},
    {"waveform": [[[10.0] * 4, [0] * 4]]},
    {"waveform": [[[10] * 4, [True] * 4]]},
    {"waveform": [[["10"] * 4, [0] * 4]]},
    {"preset_kind": "other"}, {"payload": None}, {"payload": {}},
    {"payload": {"frames": []}},
])
def test_invalid_or_ambiguous_cycle_metadata_rejected(changes):
    with pytest.raises(ValueError):
        decode_catalog(payload([preset_row(**changes)]))


def test_fixed_template_aliases_agree_and_protocol_numeric_bounds_allowed():
    frames = [[[0, 240, 197, 10], [0, 100, 75, 25]]]
    row = preset_row(waveform=frames, payload={"frames": frames})
    assert decode_catalog(payload([row]))["呼吸"].frame_count == 1


@pytest.mark.parametrize("value", [True, 1.0])
def test_payload_alias_rejects_bool_and_float_even_when_python_equal_to_integer(value):
    frames = [[[1] * 4, [1] * 4]]
    alias = [[[value] * 4, [1] * 4]]
    with pytest.raises(ValueError):
        decode_catalog(payload([preset_row(waveform=frames, payload={"frames": alias})]))


@pytest.mark.parametrize("rows", [
    [dict(name="x", device_family="coyote", random=True)],
    [preset_row(name="")], [preset_row(name=" missing")],
    [preset_row(name="x"), preset_row(name="x")],
])
def test_full_snapshot_keeps_name_validation_and_requires_random_waveform_field(rows):
    with pytest.raises(ValueError):
        decode_catalog(payload(rows))


def test_name_only_compatibility_does_not_invent_cycle_metadata():
    body = payload([dict(name="legacy", device_family="coyote")])
    assert decode_names(body) == frozenset({"legacy"})
    with pytest.raises(ValueError):
        decode_catalog(body)


def test_strict_http_snapshot_uses_same_readonly_transport_and_exact_body_digest():
    body = payload([preset_row(), dict(name="🎲 随机波形", device_family="coyote",
                                     random=True, waveform=[])])
    opener, writer, addresses = fixture(response(body))
    entries, digest = run_async(fetch_catalog_snapshot(
        SimpleNamespace(host="127.0.0.1", port=10001), opener=opener))
    assert set(entries) == {"呼吸", "🎲 随机波形"}
    assert entries["呼吸"].cycle_seconds == 1.1
    assert entries["🎲 随机波形"].random
    assert digest == hashlib.sha256(body).hexdigest()
    assert addresses == [("127.0.0.1", 10001)] and writer.closed and writer.waited
    assert writer.data.startswith(f"GET {CATALOG_PATH} HTTP/1.1\r\n".encode())


def test_strict_snapshot_template_failure_is_secret_free_and_closes_socket():
    body = payload([preset_row(waveform=["token-must-not-leak"])])
    opener, writer, _addresses = fixture(response(body))
    with pytest.raises(ValueError) as error:
        run_async(fetch_catalog_snapshot(SimpleNamespace(host="127.0.0.1", port=1),
                                        opener=opener))
    assert str(error.value) == "DGHub 1.9.6 波形目录不可用或格式不兼容；未启动输出"
    assert writer.closed and writer.waited
