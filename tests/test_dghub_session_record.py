import json
from pathlib import Path
import threading
import time

import pytest

from dghub_plugin.sdk_config import core_config, defaults
from dghub_plugin.session_record import SessionRecord
from src.config_loader import load_config
from test_dghub_sdk import client, receive, ready
from test_plugin_offline import run_async


ROOT = Path(__file__).resolve().parents[1]


def applied(scale=100):
    values = defaults() | {"hud_scale_percent": scale}
    return values, core_config(load_config(ROOT / "config.example.ini"), values)


def test_unique_runs_preserve_complete_applied_hud_without_credentials(tmp_path):
    first, second = SessionRecord(tmp_path), SessionRecord(tmp_path)
    assert first.directory != second.directory
    for index, record in enumerate((first, second)):
        values, core = applied(100 if index == 0 else 125)
        core["sender"]["token"] = "secret-must-not-appear"
        core["dghub"] = {"token": "secret-must-not-appear"}
        record.configured(values, core)
        record.append({"type": "session_start"})
        record.append({"hp": .75})
        record.close()
        # Trigger closing does not close the writer before transport cleanup.
        assert not record._stop.is_set()
        assert record.finish("host_stop", timeout=1)
        snapshot = json.loads((record.directory / "config_snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["settings"] == values
        assert snapshot["recognition_config"]["hud"] == core["hud"]
        assert snapshot["recognition_config"]["ROI"] == core["ROI"]
        assert snapshot["recognition_config"]["trigger"]["low_health_hp_threshold"] == .75
        assert "ws_host" not in snapshot["sender_config"]
        assert "secret-must-not-appear" not in json.dumps(snapshot)
        terminal = json.loads((record.directory / "session.json").read_text(encoding="utf-8"))
        assert terminal["state"] == "complete" and terminal["reason"] == "host_stop"
        assert terminal["rows_enqueued"] == terminal["rows_written"] == 2
        assert terminal["rows_dropped"] == terminal["rows_invalid"] == 0
        assert len((record.directory / "trigger_log.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_snapshot_cannot_be_reconfigured_or_aliased_by_later_changes(tmp_path):
    record = SessionRecord(tmp_path)
    values, core = applied()
    record.configured(values, core)
    core["ROI"]["bar_min_x"] = -100
    with pytest.raises(RuntimeError):
        record.configured(*applied(125))
    assert record.finish("done", timeout=1)
    snapshot = json.loads((record.directory / "config_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["recognition_config"]["ROI"]["bar_min_x"] == 121


def test_blocked_disk_full_queue_and_close_never_hold_stop_owner(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionRecord._atomic

    def blocked(self, name, text):
        entered.set()
        assert release.wait(3)
        original(self, name, text)

    monkeypatch.setattr(SessionRecord, "_atomic", blocked)
    record = SessionRecord(tmp_path, queue_size=1)
    try:
        assert entered.wait(1)
        record.configured(*applied())
        record.append({"first": True})
        for _ in range(10):
            record.append({"dropped": True})
        record.append({"bad": float("nan")})
        record.append({"too_long": "x" * 17000})
        start = time.monotonic()
        record.close()
        assert time.monotonic() - start < .1
        start = time.monotonic()
        assert not record.finish("disk_blocked", timeout=.01)
        assert time.monotonic() - start < .15
        assert record.status()["rows_dropped"] == 10
        assert record.status()["rows_invalid"] == 2
    finally:
        release.set()
        assert record.done.wait(2)
    assert record.status()["state"] == "complete"


def test_disk_failure_is_visible_without_error_paths_or_secrets(tmp_path, monkeypatch):
    def failed(*args):
        raise OSError("a-secret-and-sensitive-path")
    monkeypatch.setattr(SessionRecord, "_atomic", failed)
    record = SessionRecord(tmp_path)
    assert record.done.wait(1)
    assert not record.finish("failure", timeout=.01)
    status = record.status()
    assert status["state"] == "failed" and "secret" not in json.dumps(status)


def test_sdk_freezes_config_snapshot_and_pending_hud_does_not_replace_it(client, tmp_path):
    record = SessionRecord(tmp_path)
    client.log_writer = record
    try:
        ready(client)
        receive(client, op="config_changed", key="hud_scale_percent", value=125)
        assert client.settings["hud_scale_percent"] == 100
        assert client.status_fields("test")["session_record"]["state"] == "recording"
        client.close()
        assert not record._stop.is_set()
        assert record.finish("host_stop", timeout=1)
        snapshot = json.loads((record.directory / "config_snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["settings"]["hud_scale_percent"] == 100
        rows = [json.loads(line) for line in (record.directory / "trigger_log.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [row["type"] for row in rows if "type" in row] == ["session_start", "session_end"]
    finally:
        record.finish("test_end", timeout=1)


@pytest.mark.parametrize("size", [True, 0, -1, 10001, 1.5])
def test_invalid_queue_size_rejected_before_directory_or_thread(tmp_path, size):
    with pytest.raises(ValueError):
        SessionRecord(tmp_path, queue_size=size)
    assert list(tmp_path.iterdir()) == []


def test_runtime_finishes_record_only_after_transport_zero_requests(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.dghub_live_entry import run_host
    events = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            pass

        def append(self, row):
            pass

        def configured(self, settings, core):
            events.append("configured")

        def close(self):
            events.append("nonblocking_trigger_close")

        def finish(self, reason, **kwargs):
            assert events[-2:] == ["zero_a", "zero_b"]
            events.append("record_finish")

    class Transport:
        reason = "session_stopped"

        def __init__(self, session, environment, **kwargs):
            self.session = session

        async def run(self):
            # This fake transport bypasses the real startup catalog loader.
            self.session.available_presets = set(defaults()[key] for key in defaults() if key.endswith("_preset"))
            ready(self.session, defaults())
            batch = self.session.close("host_stop")
            for raw in batch.messages:
                packet = json.loads(raw)
                if packet["op"] == "set_strength" and packet["pct"] == 0:
                    events.append("zero_" + packet["channel"])
            return 0

    monkeypatch.setattr("dghub_plugin.session_record.SessionRecord", Recorder)
    monkeypatch.setattr("dghub_plugin.sdk_transport.DGHubTransport", Transport)
    assert run_async(run_host(SimpleNamespace(plugin_root=tmp_path), capture_source="injected_test")) == 0
    assert events.index("nonblocking_trigger_close") < events.index("zero_a") < events.index("record_finish")


def test_runtime_record_is_finished_even_if_core_initialization_fails(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.dghub_live_entry import run_host
    records = []

    def make_record(*args, **kwargs):
        record = SessionRecord(*args, **kwargs)
        records.append(record)
        return record

    def fail(*args):
        raise OSError("injected private initialization failure")

    monkeypatch.setattr("dghub_plugin.session_record.SessionRecord", make_record)
    monkeypatch.setattr("src.config_loader.load_config", fail)
    with pytest.raises(OSError):
        run_async(run_host(SimpleNamespace(plugin_root=tmp_path), capture_source="injected_test"))
    assert records[0].done.wait(1)
    metadata = json.loads((records[0].directory / "session.json").read_text(encoding="utf-8"))
    assert metadata["reason"] == "initialization_failed" and metadata["runtime_error"]
    assert "private" not in json.dumps(metadata)


def test_runtime_failed_configuration_retains_sanitized_diagnostic(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.dghub_live_entry import run_host
    records = []

    def make_record(*args, **kwargs):
        record = SessionRecord(*args, **kwargs)
        records.append(record)
        return record

    class Transport:
        reason = "session_stopped"

        def __init__(self, session, environment, **kwargs):
            self.session = session

        async def run(self):
            self.session.hello("secret-not-in-log")
            receive(self.session, op="hello_ack", accepted=True, sdk_version="1")
            receive(self.session, op="config", data=defaults() | {"hud_scale_percent": "76"})
            assert self.session.closed
            return 1

    monkeypatch.setattr("dghub_plugin.session_record.SessionRecord", make_record)
    monkeypatch.setattr("dghub_plugin.sdk_transport.DGHubTransport", Transport)
    assert run_async(run_host(SimpleNamespace(plugin_root=tmp_path), capture_source="injected_test")) == 1
    assert records[0].done.wait(1)
    metadata = json.loads((records[0].directory / "session.json").read_text(encoding="utf-8"))
    assert metadata["runtime_diagnostic"] == "Host protocol/configuration validation failed"
    assert metadata["runtime_error"] and "secret" not in json.dumps(metadata)
